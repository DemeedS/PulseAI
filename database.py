import sqlite3
from datetime import datetime

DB_PATH = "pulseai.db"

def get_db():
    """
    Get a database connection.
    Pattern from courses/tool_use - keep connections simple and explicit.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create all tables. Safe to call multiple times."""
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            location TEXT NOT NULL,
            description TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'active'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            role TEXT DEFAULT 'member'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS rsvps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            responded_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (member_id) REFERENCES members(id),
            UNIQUE(event_id, member_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER,
            message TEXT NOT NULL,
            type TEXT NOT NULL,
            sent_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            fire_at TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Database initialized")

def seed_demo_members():
    """Seed fake club members for demo."""
    conn = get_db()
    c = conn.cursor()

    members = [
        ("Demian Vial", "dsv@fordham.edu", "member"),  # Demo user — judges can RSVP with their own email to see it live
    ]

    for name, email, role in members:
        c.execute("""
            INSERT OR IGNORE INTO members (name, email, role)
            VALUES (?, ?, ?)
        """, (name, email, role))

    conn.commit()
    conn.close()
    print("✅ Demo members seeded")

def seed_demo_rsvp_data():
    """
    Pre-populate RSVPs so the panel looks alive before the demo runs.
    Judges see real data the moment they open the URL.
    """
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM rsvps")
    if c.fetchone()[0] > 0:
        conn.close()
        return

    c.execute("""
        INSERT OR IGNORE INTO events (id, title, date, time, location)
        VALUES (999, 'Kickoff Meeting (Demo)', '2026-03-20', '18:00', 'Keating 1st Floor')
    """)

    rsvp_data = [
        (999, 1, 'attending'),
        (999, 2, 'attending'),
        (999, 3, 'not_attending'),
        (999, 4, 'attending'),
        (999, 5, 'attending'),
        (999, 6, 'not_attending'),
    ]
    for event_id, member_id, status in rsvp_data:
        c.execute("""
            INSERT OR IGNORE INTO rsvps (event_id, member_id, status)
            VALUES (?, ?, ?)
        """, (event_id, member_id, status))

    conn.commit()
    conn.close()
    print("✅ Demo RSVP data seeded")

# ── EVENT FUNCTIONS ──────────────────────────────────────────────────

def create_event(title, date, time, location, description=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO events (title, date, time, location, description)
        VALUES (?, ?, ?, ?, ?)
    """, (title, date, time, location, description))
    event_id = c.lastrowid
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
    c.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

# ── MEMBER FUNCTIONS ─────────────────────────────────────────────────

def get_all_members():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM members ORDER BY role, name")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ── RSVP FUNCTIONS ───────────────────────────────────────────────────

def save_rsvp(event_id, member_id, status):
    """Save or UPDATE rsvp — member can change their response."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO rsvps (event_id, member_id, status)
        VALUES (?, ?, ?)
        ON CONFLICT(event_id, member_id) DO UPDATE SET
            status = excluded.status,
            responded_at = datetime('now')
    """, (event_id, member_id, status))
    conn.commit()
    conn.close()

def get_rsvps_for_event(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT r.status, m.name, m.email
        FROM rsvps r
        JOIN members m ON r.member_id = m.id
        WHERE r.event_id = ?
        ORDER BY r.responded_at DESC
    """, (event_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ── NOTIFICATION LOG ─────────────────────────────────────────────────

def log_notification(event_id, message, notif_type):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO notifications (event_id, message, type)
        VALUES (?, ?, ?)
    """, (event_id, message, notif_type))
    conn.commit()
    conn.close()

# ── SCHEDULED JOBS ───────────────────────────────────────────────────

def save_scheduled_job(event_id, job_id, fire_at, message):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO scheduled_jobs (event_id, job_id, fire_at, message)
        VALUES (?, ?, ?, ?)
    """, (event_id, job_id, fire_at, message))
    conn.commit()
    conn.close()

def mark_job_fired(job_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE scheduled_jobs SET status = 'fired' WHERE job_id = ?
    """, (job_id,))
    conn.commit()
    conn.close()

def get_scheduled_jobs(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM scheduled_jobs WHERE event_id = ?", (event_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ── EVENT DETAIL + FLYER ─────────────────────────────────────────────

def update_event_flyer(event_id, flyer_path):
    """Store path to uploaded or generated flyer."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE events SET description = ? WHERE id = ?
    """, (flyer_path, event_id))
    conn.commit()
    conn.close()

def update_event_details(event_id, title, date, time, location, description):
    """Admin edits event details."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE events 
        SET title=?, date=?, time=?, location=?, description=?
        WHERE id=?
    """, (title, date, time, location, description, event_id))
    conn.commit()
    conn.close()

def get_rsvp_counts(event_id):
    """Get attending/not attending counts for an event."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT status, COUNT(*) as count
        FROM rsvps
        WHERE event_id = ?
        GROUP BY status
    """, (event_id,))
    rows = c.fetchall()
    conn.close()
    counts = {"attending": 0, "not_attending": 0}
    for row in rows:
        counts[row["status"]] = row["count"]
    return counts

def get_attending_members(event_id):
    """Get list of members who said they are attending — public facing."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT m.name, m.email
        FROM rsvps r
        JOIN members m ON r.member_id = m.id
        WHERE r.event_id = ? AND r.status = 'attending'
        ORDER BY r.responded_at ASC
    """, (event_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_member_by_email(email):
    """Look up a member by email for RSVP submission."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM members WHERE email = ?", (email,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_member_rsvp(event_id, member_id):
    """Check if a specific member already RSVPed."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT status FROM rsvps 
        WHERE event_id = ? AND member_id = ?
    """, (event_id, member_id))
    row = c.fetchone()
    conn.close()
    return row["status"] if row else None

# ── TREASURY ─────────────────────────────────────────────────────────

def init_treasury_table():
    """Create treasury tables if not exists."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            club_name TEXT DEFAULT 'Finance Club',
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            event_id INTEGER,
            logged_at TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS club_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            club_name TEXT UNIQUE NOT NULL,
            budget REAL DEFAULT 0.00
        )
    """)
    conn.commit()
    conn.close()

def log_expense(amount, category, description, event_id=None, club_name="Finance Club"):
    """Log a club expense."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO expenses (club_name, amount, category, description, event_id)
        VALUES (?, ?, ?, ?, ?)
    """, (club_name, amount, category, description, event_id))
    expense_id = c.lastrowid
    conn.commit()
    conn.close()
    return expense_id

def get_expenses(club_name="Finance Club"):
    """Get all expenses for a club."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM expenses 
        WHERE club_name = ?
        ORDER BY logged_at DESC
    """, (club_name,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_treasury_summary(club_name="Finance Club"):
    """Get budget vs spending summary."""
    conn = get_db()
    c = conn.cursor()
    
    # Get total spent
    c.execute("""
        SELECT COALESCE(SUM(amount), 0) as total_spent
        FROM expenses WHERE club_name = ?
    """, (club_name,))
    total_spent = c.fetchone()["total_spent"]
    
    # Get by category
    c.execute("""
        SELECT category, SUM(amount) as total
        FROM expenses WHERE club_name = ?
        GROUP BY category
        ORDER BY total DESC
    """, (club_name,))
    by_category = [dict(row) for row in c.fetchall()]
    
    conn.close()
    
    budget = get_budget(club_name)  # demo budget
    return {
        "budget": budget,
        "spent": total_spent,
        "remaining": budget - total_spent,
        "percent_used": round((total_spent / budget) * 100) if budget > 0 else 0,
        "by_category": by_category
    }

def set_budget(amount, club_name="Finance Club"):
    """Set or update the club budget."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS club_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            club_name TEXT UNIQUE NOT NULL,
            budget REAL DEFAULT 500.00
        )
    """)
    c.execute("""
        INSERT INTO club_settings (club_name, budget)
        VALUES (?, ?)
        ON CONFLICT(club_name) DO UPDATE SET budget = excluded.budget
    """, (club_name, amount))
    conn.commit()
    conn.close()

def get_budget(club_name="Finance Club"):
    """Get the current budget for a club."""
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("SELECT budget FROM club_settings WHERE club_name = ?", (club_name,))
        row = c.fetchone()
        conn.close()
        return row["budget"] if row else 0.00
    except:
        conn.close()
        return 0.00

def seed_demo_expenses():
    """Seed demo expenses so treasury looks alive immediately."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM expenses")
    if c.fetchone()[0] > 0:
        conn.close()
        return
    
    expenses = [
        ("Finance Club", 85.00,  "Food & Drinks",  "Pizza for September kickoff meeting", None),
        ("Finance Club", 45.50,  "Supplies",       "Printed materials and folders",       None),
        ("Finance Club", 120.00, "Speaker",        "Guest speaker honorarium",            None),
        ("Finance Club", 32.00,  "Food & Drinks",  "Coffee and snacks for study session", None),
        ("Finance Club", 18.75,  "Supplies",       "Whiteboard markers and paper",        None),
    ]
    for exp in expenses:
        c.execute("""
            INSERT INTO expenses (club_name, amount, category, description, event_id)
            VALUES (?, ?, ?, ?, ?)
        """, exp)
    conn.commit()
    conn.close()
    print("✅ Demo expenses seeded")

    # ── EVENT BUDGET ─────────────────────────────────────────────────────

def init_event_budget_table():
    """Create event budget and expenses table."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS event_expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            logged_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS event_budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER UNIQUE NOT NULL,
            budget REAL DEFAULT 0.00,
            FOREIGN KEY (event_id) REFERENCES events(id)
        )
    """)
    conn.commit()
    conn.close()

def set_event_budget(event_id, amount):
    """Set or update budget for a specific event."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO event_budgets (event_id, budget)
        VALUES (?, ?)
        ON CONFLICT(event_id) DO UPDATE SET budget = excluded.budget
    """, (event_id, amount))
    conn.commit()
    conn.close()

def get_event_budget(event_id):
    """Get budget for a specific event."""
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("SELECT budget FROM event_budgets WHERE event_id = ?", (event_id,))
        row = c.fetchone()
        conn.close()
        return row["budget"] if row else 0.00
    except:
        conn.close()
        return 0.00

def log_event_expense(event_id, amount, category, description):
    """Log an expense against a specific event."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO event_expenses (event_id, amount, category, description)
        VALUES (?, ?, ?, ?)
    """, (event_id, amount, category, description))
    expense_id = c.lastrowid
    conn.commit()
    conn.close()
    return expense_id

def get_event_expenses(event_id):
    """Get all expenses for a specific event."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM event_expenses
        WHERE event_id = ?
        ORDER BY logged_at DESC
    """, (event_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_event_budget_summary(event_id):
    """Get full budget summary for an event."""
    conn = get_db()
    c = conn.cursor()

    budget = get_event_budget(event_id)

    c.execute("""
        SELECT COALESCE(SUM(amount), 0) as total_spent
        FROM event_expenses WHERE event_id = ?
    """, (event_id,))
    total_spent = c.fetchone()["total_spent"]

    c.execute("""
        SELECT category, SUM(amount) as total
        FROM event_expenses WHERE event_id = ?
        GROUP BY category ORDER BY total DESC
    """, (event_id,))
    by_category = [dict(row) for row in c.fetchall()]

    conn.close()
    return {
        "budget": budget,
        "spent": total_spent,
        "remaining": budget - total_spent,
        "percent_used": round((total_spent / budget) * 100) if budget > 0 else 0,
        "by_category": by_category
    }

def add_member(name, email, role="member"):
    """Add a new member to the club."""
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO members (name, email, role)
            VALUES (?, ?, ?)
        """, (name, email, role))
        member_id = c.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "member_id": member_id}
    except Exception as e:
        conn.close()
        return {"success": False, "error": str(e)}

def remove_member(member_id):
    """Remove a member from the club."""
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM members WHERE id = ?", (member_id,))
    conn.commit()
    conn.close()
