from flask_apscheduler import APScheduler

# Single global scheduler instance
# app.py imports this and wires it into Flask
scheduler = APScheduler()

def init_scheduler(app):
    """
    Initialize APScheduler with Flask.
    MUST use APScheduler v3 — v4 has breaking changes.
    Pinned in requirements.txt as APScheduler>=3.0,<4.0
    """
    app.config["SCHEDULER_API_ENABLED"] = False  # don't expose scheduler endpoints publicly
    app.config["SCHEDULER_TIMEZONE"] = "America/New_York"

    scheduler.init_app(app)
    scheduler.start()

    print("✅ Scheduler started (APScheduler v3)")
    return scheduler