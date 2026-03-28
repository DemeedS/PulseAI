import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ── PRE-TOOL HOOK PATTERN from hooks.py ─────────────────────────────
# Before sending, we check inputs are valid (like check_bash_command checks
# tool_name before allowing execution). If invalid, fail gracefully, never crash.

def _check_email_inputs(to_email, subject, body):
    """
    Validate before sending — mirrors PreToolUse hook pattern from hooks.py.
    Returns (ok, reason) tuple so caller decides what to do.
    """
    if not to_email or "@" not in to_email:
        return False, f"Invalid email address: {to_email}"
    if not subject:
        return False, "Subject is empty"
    if not body:
        return False, "Body is empty"
    return True, "ok"

def send_email(to_email, to_name, subject, body):
    """
    Send one email via SendGrid.
    
    Error handling pattern from sdk/_errors.py:
    - Catch specific error types, not bare Exception where possible
    - Always return a value, never raise — agent loop must continue
    - Log what happened so we can debug during demo
    """
    # Pre-send validation (PreToolUse hook pattern)
    ok, reason = _check_email_inputs(to_email, subject, body)
    if not ok:
        print(f"[EMAIL BLOCKED] {reason}")
        return False

    api_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("FROM_EMAIL", "pulseai@fordham.edu")

    # No API key = demo mode, log and continue (never crash the agent)
    if not api_key or api_key == "your_key_here":
        print(f"[EMAIL DEMO MODE] Would send to {to_email} | Subject: {subject}")
        return True  # return True so agent counts it as success in demo

    try:
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            plain_text_content=body
        )
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"✅ Email sent → {to_email} (status {response.status_code})")
        return True

    except Exception as e:
        # PostToolUse hook pattern from hooks.py — on error, add context
        # and return False so agent knows but keeps running
        print(f"❌ Email failed → {to_email} | Error: {e}")
        return False


def send_bulk_emails(members, subject, body_template):
    """
    Send to all members. body_template supports {name} placeholder.
    
    Mirrors the multiple_agents_example pattern from agents.py —
    iterate over each target, handle each independently so one
    failure does not stop the rest.
    
    Returns dict with counts so agent can report accurately.
    """
    results = {"sent": 0, "failed": 0, "total": len(members)}

    for member in members:
        # Personalize the body for each member
        body = body_template.replace("{name}", member.get("name", "Member"))

        success = send_email(
            to_email=member["email"],
            to_name=member["name"],
            subject=subject,
            body=body
        )

        if success:
            results["sent"] += 1
        else:
            results["failed"] += 1

    print(f"📧 Bulk email complete: {results['sent']}/{results['total']} sent")
    return results