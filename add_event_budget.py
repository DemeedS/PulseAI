# Appends event budget functions to database.py
new_code = '''

# ── EVENT BUDGET ─────────────────────────────────────────────────────

def init_event_budget_table():
    """Create event budget and expenses tables."""
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
'''

with open("database.py", "a", encoding="utf-8") as f:
    f.write(new_code)

print("✅ Event budget functions added to database.py")

# Verify
from database import init_event_budget_table, get_event_budget_summary
init_event_budget_table()
s = get_event_budget_summary(999)
print("✅ Test passed:", s)