new_code = '''

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
'''

with open("database.py", "a", encoding="utf-8") as f:
    f.write(new_code)

print("✅ add_member and remove_member added to database.py")

from database import add_member, remove_member
r = add_member("Test User", "test@test.com")
print("✅ Test result:", r)