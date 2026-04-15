import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    """Create all tables and migrate new columns. Safe to call multiple times."""
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            location TEXT NOT NULL,
            description TEXT,
            created_at TEXT DEFAULT (NOW()::TEXT),
            status TEXT DEFAULT 'draft',
            notification_message TEXT,
            draft_reminders TEXT
        )
    """)

    # Migrate existing tables — add new columns if they don't exist yet
    migrations = [
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS notification_message TEXT",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS draft_reminders TEXT",
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'draft'",
    ]
    for sql in migrations:
        try:
            c.execute(sql)
        except Exception:
            conn.rollback()

    c.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            role TEXT DEFAULT 'member'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS rsvps (
            id SERIAL PRIMARY KEY,
            event_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            responded_at TEXT DEFAULT (NOW()::TEXT),
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (member_id) REFERENCES members(id),
            UNIQUE(event_id, member_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            event_id INTEGER,
            message TEXT NOT NULL,
            type TEXT NOT NULL,
            sent_at TEXT DEFAULT (NOW()::TEXT)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id SERIAL PRIMARY KEY,
            event_id INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            fire_at TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            club_name TEXT DEFAULT 'Finance Club',
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            event_id INTEGER,
            logged_at TEXT DEFAULT (NOW()::TEXT)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS club_settings (
            id SERIAL PRIMARY KEY,
            club_name TEXT UNIQUE NOT NULL,
            budget REAL DEFAULT 0.00
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS event_expenses (
            id SERIAL PRIMARY KEY,
            event_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            logged_at TEXT DEFAULT (NOW()::TEXT),
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS event_budgets (
            id SERIAL PRIMARY KEY,
            event_id INTEGER UNIQUE NOT NULL,
            budget REAL DEFAULT 0.00,
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Database initialized")


def seed_demo_members():
    conn = get_db()
    c = conn.cursor()
    for name, email, role in [("Demian Vial", "dsv@fordham.edu", "member")]:
        c.execute("INSERT INTO members (name, email, role) VALUES (%s, %s, %s) ON CONFLICT (email) DO NOTHING",
                  (name, email, role))
    conn.commit()
    conn.close()
    print("✅ Demo members seeded")


# ── EVENT FUNCTIONS ──────────────────────────────────────────────────

def create_event(title, date, time, location, description="", status="draft"):
    """Create event — defaults to 'draft' so agent never auto-publishes."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO events (title, date, time, location, description, status)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (title, date, time, location, description, status))
    event_id = c.fetchone()["id"]
    conn.commit()
    conn.close()
    return event_id


def get_all_events():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM events ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_event(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM events WHERE id = %s", (event_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_event(event_id):
    """Delete an event and all associated records."""
    conn = get_db()
    c = conn.cursor()
    for table in ["rsvps", "scheduled_jobs", "notifications", "event_expenses", "event_budgets"]:
        c.execute(f"DELETE FROM {table} WHERE event_id = %s", (event_id,))
    c.execute("DELETE FROM events WHERE id = %s", (event_id,))
    conn.commit()
    conn.close()


def publish_event(event_id):
    """Change event status to 'active' (published)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE events SET status = 'active' WHERE id = %s", (event_id,))
    conn.commit()
    conn.close()


def save_draft_review_data(event_id, notification_message, draft_reminders_json):
    """Store agent-suggested notification message and reminders on the draft."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE events SET notification_message = %s, draft_reminders = %s WHERE id = %s
    """, (notification_message, draft_reminders_json, event_id))
    conn.commit()
    conn.close()


def update_event_details(event_id, title, date, time, location, description):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE events SET title=%s, date=%s, time=%s, location=%s, description=%s WHERE id=%s
    """, (title, date, time, location, description, event_id))
    conn.commit()
    conn.close()


def update_event_flyer(event_id, flyer_path):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE events SET description = %s WHERE id = %s", (flyer_path, event_id))
    conn.commit()
    conn.close()


# ── MEMBER FUNCTIONS ─────────────────────────────────────────────────

def get_all_members():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM members ORDER BY role, name")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_member(name, email, role="member"):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO members (name, email, role) VALUES (%s, %s, %s) RETURNING id",
                  (name, email, role))
        member_id = c.fetchone()["id"]
        conn.commit()
        conn.close()
        return {"success": True, "member_id": member_id}
    except Exception as e:
        conn.rollback()
        conn.close()
        return {"success": False, "error": str(e)}


def remove_member(member_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM members WHERE id = %s", (member_id,))
    conn.commit()
    conn.close()


def get_member_by_email(email):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM members WHERE email = %s", (email,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ── RSVP FUNCTIONS ───────────────────────────────────────────────────

def save_rsvp(event_id, member_id, status):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO rsvps (event_id, member_id, status) VALUES (%s, %s, %s)
        ON CONFLICT (event_id, member_id) DO UPDATE SET status = EXCLUDED.status, responded_at = NOW()::TEXT
    """, (event_id, member_id, status))
    conn.commit()
    conn.close()


def get_rsvps_for_event(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT r.status, m.name, m.email FROM rsvps r
        JOIN members m ON r.member_id = m.id
        WHERE r.event_id = %s ORDER BY r.responded_at DESC
    """, (event_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_rsvp_counts(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT status, COUNT(*) as count FROM rsvps WHERE event_id = %s GROUP BY status", (event_id,))
    rows = c.fetchall()
    conn.close()
    counts = {"attending": 0, "not_attending": 0}
    for row in rows:
        counts[row["status"]] = row["count"]
    return counts


def get_attending_members(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT m.name, m.email FROM rsvps r JOIN members m ON r.member_id = m.id
        WHERE r.event_id = %s AND r.status = 'attending' ORDER BY r.responded_at ASC
    """, (event_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_member_rsvp(event_id, member_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT status FROM rsvps WHERE event_id = %s AND member_id = %s", (event_id, member_id))
    row = c.fetchone()
    conn.close()
    return row["status"] if row else None


# ── NOTIFICATION LOG ─────────────────────────────────────────────────

def log_notification(event_id, message, notif_type):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO notifications (event_id, message, type) VALUES (%s, %s, %s)",
              (event_id, message, notif_type))
    conn.commit()
    conn.close()


# ── SCHEDULED JOBS ───────────────────────────────────────────────────

def save_scheduled_job(event_id, job_id, fire_at, message):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO scheduled_jobs (event_id, job_id, fire_at, message) VALUES (%s, %s, %s, %s)",
              (event_id, job_id, fire_at, message))
    conn.commit()
    conn.close()


def mark_job_fired(job_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE scheduled_jobs SET status = 'fired' WHERE job_id = %s", (job_id,))
    conn.commit()
    conn.close()


def get_scheduled_jobs(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM scheduled_jobs WHERE event_id = %s", (event_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ── TREASURY ─────────────────────────────────────────────────────────

def init_treasury_table():
    pass


def log_expense(amount, category, description, event_id=None, club_name="Finance Club"):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO expenses (club_name, amount, category, description, event_id)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
    """, (club_name, amount, category, description, event_id))
    expense_id = c.fetchone()["id"]
    conn.commit()
    conn.close()
    return expense_id


def get_expenses(club_name="Finance Club"):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM expenses WHERE club_name = %s ORDER BY logged_at DESC", (club_name,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_treasury_summary(club_name="Finance Club"):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COALESCE(SUM(amount), 0) as total_spent FROM expenses WHERE club_name = %s", (club_name,))
    total_spent = c.fetchone()["total_spent"]
    c.execute("""
        SELECT category, SUM(amount) as total FROM expenses WHERE club_name = %s
        GROUP BY category ORDER BY total DESC
    """, (club_name,))
    by_category = [dict(row) for row in c.fetchall()]
    conn.close()
    budget = get_budget(club_name)
    return {
        "budget": budget, "spent": total_spent,
        "remaining": budget - total_spent,
        "percent_used": round((total_spent / budget) * 100) if budget > 0 else 0,
        "by_category": by_category
    }


def set_budget(amount, club_name="Finance Club"):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO club_settings (club_name, budget) VALUES (%s, %s)
        ON CONFLICT (club_name) DO UPDATE SET budget = EXCLUDED.budget
    """, (club_name, amount))
    conn.commit()
    conn.close()


def get_budget(club_name="Finance Club"):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("SELECT budget FROM club_settings WHERE club_name = %s", (club_name,))
        row = c.fetchone()
        conn.close()
        return row["budget"] if row else 0.00
    except Exception:
        conn.close()
        return 0.00


def init_event_budget_table():
    pass


def set_event_budget(event_id, amount):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO event_budgets (event_id, budget) VALUES (%s, %s)
        ON CONFLICT (event_id) DO UPDATE SET budget = EXCLUDED.budget
    """, (event_id, amount))
    conn.commit()
    conn.close()


def get_event_budget(event_id):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("SELECT budget FROM event_budgets WHERE event_id = %s", (event_id,))
        row = c.fetchone()
        conn.close()
        return row["budget"] if row else 0.00
    except Exception:
        conn.close()
        return 0.00


def log_event_expense(event_id, amount, category, description):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO event_expenses (event_id, amount, category, description)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (event_id, amount, category, description))
    expense_id = c.fetchone()["id"]
    conn.commit()
    conn.close()
    return expense_id


def get_event_expenses(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM event_expenses WHERE event_id = %s ORDER BY logged_at DESC", (event_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_event_budget_summary(event_id):
    conn = get_db()
    c = conn.cursor()
    budget = get_event_budget(event_id)
    c.execute("SELECT COALESCE(SUM(amount), 0) as total_spent FROM event_expenses WHERE event_id = %s", (event_id,))
    total_spent = c.fetchone()["total_spent"]
    c.execute("""
        SELECT category, SUM(amount) as total FROM event_expenses
        WHERE event_id = %s GROUP BY category ORDER BY total DESC
    """, (event_id,))
    by_category = [dict(row) for row in c.fetchall()]
    conn.close()
    return {
        "budget": budget, "spent": total_spent,
        "remaining": budget - total_spent,
        "percent_used": round((total_spent / budget) * 100) if budget > 0 else 0,
        "by_category": by_category
    }