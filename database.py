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
            FOREIGN KEY (member_id) REFERENCES members(id)
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
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO rsvps (event_id, member_id, status)
        VALUES (?, ?, ?)
        ON CONFLICT DO NOTHING
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