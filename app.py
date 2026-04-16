import os
import uuid
import eventlet
eventlet.monkey_patch()

from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, abort
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

from database import (
    init_db, seed_demo_members,
    get_all_events, get_all_members,
    save_rsvp, get_rsvps_for_event, get_event,
    update_event_details,
    get_rsvp_counts, get_attending_members,
    get_member_by_email, get_member_rsvp,
    init_treasury_table, log_expense,
    get_expenses, get_treasury_summary,
    set_budget, get_budget,
    init_event_budget_table, set_event_budget,
    get_event_budget, log_event_expense,
    get_event_expenses, get_event_budget_summary,
    add_member, remove_member,
    delete_event, publish_event, save_draft_review_data,
    save_scheduled_job, log_notification
)
from scheduler import init_scheduler
import tools

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "pulseai_dev_secret")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ── DATABASE ──────────────────────────────────────────────────────────
init_db()
seed_demo_members()
init_treasury_table()
init_event_budget_table()

# ── SCHEDULER ─────────────────────────────────────────────────────────
scheduler = init_scheduler(app)

# ── WIRE DEPENDENCIES ─────────────────────────────────────────────────
tools.set_socketio(socketio)
tools.set_scheduler(scheduler)

# ── ROUTES ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run-agent", methods=["POST"])
def run_agent_route():
    data       = request.get_json()
    user_input = data.get("instruction", "").strip()
    if not user_input:
        return jsonify({"error": "No instruction provided"}), 400

    today = datetime.now().strftime("%A, %B %d, %Y at %H:%M")

    def run_in_background():
        from agent import run_agent
        run_agent(user_input, today_context=today)

    socketio.start_background_task(run_in_background)
    return jsonify({"status": "Agent started", "instruction": user_input, "today": today})


@app.route("/api/events", methods=["GET"])
def get_events():
    events = get_all_events()
    return jsonify(events)


@app.route("/api/members", methods=["GET"])
def get_members():
    members = get_all_members()
    return jsonify(members)


@app.route("/api/members", methods=["POST"])
def add_member_route():
    data  = request.get_json()
    name  = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    role  = data.get("role", "member")

    if not name or not email:
        return jsonify({"error": "Name and email required"}), 400
    if "@" not in email:
        return jsonify({"error": "Invalid email"}), 400

    result = add_member(name, email, role)
    if result["success"]:
        members = get_all_members()
        socketio.emit("members_updated", {"members": members})
        return jsonify({"success": True, "members": members})
    else:
        return jsonify({"error": "Email already exists"}), 400


@app.route("/api/members/<int:member_id>", methods=["DELETE"])
def remove_member_route(member_id):
    remove_member(member_id)
    members = get_all_members()
    socketio.emit("members_updated", {"members": members})
    return jsonify({"success": True, "members": members})


@app.route("/api/rsvp", methods=["POST"])
def submit_rsvp():
    data      = request.get_json()
    event_id  = data.get("event_id")
    member_id = data.get("member_id")
    status    = data.get("status")

    if not all([event_id, member_id, status]):
        return jsonify({"error": "Missing fields"}), 400

    save_rsvp(event_id, member_id, status)

    rsvps     = get_rsvps_for_event(event_id)
    attending = [r for r in rsvps if r["status"] == "attending"]

    socketio.emit("rsvp_update", {
        "event_id":     event_id,
        "total":        len(rsvps),
        "attending":    len(attending),
        "not_attending": len(rsvps) - len(attending),
        "latest_name":  data.get("member_name", "A member")
    })
    return jsonify({"success": True})


@app.route("/api/rsvp/<int:event_id>", methods=["GET"])
def get_rsvp_summary(event_id):
    rsvps         = get_rsvps_for_event(event_id)
    attending     = [r for r in rsvps if r["status"] == "attending"]
    not_attending = [r for r in rsvps if r["status"] == "not_attending"]
    return jsonify({"total": len(rsvps), "attending": attending, "not_attending": not_attending})


# ── WEBSOCKET ─────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    print(f"✅ Client connected: {request.sid}")
    emit("connected", {"message": "Connected to PulseAI"})

@socketio.on("disconnect")
def on_disconnect():
    print(f"❌ Client disconnected: {request.sid}")


# ── EVENT DETAIL PAGE ─────────────────────────────────────────────────

@app.route("/event/<int:event_id>")
def event_detail(event_id):
    event = get_event(event_id)
    if not event:
        abort(404)
    counts         = get_rsvp_counts(event_id)
    attending      = get_attending_members(event_id)
    members        = get_all_members()
    budget_summary = get_event_budget_summary(event_id)
    event_expenses = get_event_expenses(event_id)
    return render_template(
        "event.html",
        event=event, counts=counts, attending=attending,
        members=members, budget=budget_summary, expenses=event_expenses
    )


@app.route("/api/event/<int:event_id>", methods=["GET"])
def get_event_api(event_id):
    event = get_event(event_id)
    if not event:
        return jsonify({"error": "Not found"}), 404
    counts   = get_rsvp_counts(event_id)
    attending = get_attending_members(event_id)
    return jsonify({"event": event, "counts": counts, "attending": attending})


@app.route("/api/event/<int:event_id>", methods=["POST"])
def update_event_api(event_id):
    data = request.get_json()
    update_event_details(
        event_id, data.get("title"), data.get("date"),
        data.get("time"), data.get("location"), data.get("description", "")
    )
    socketio.emit("event_updated", {"event_id": event_id})
    return jsonify({"success": True})


# ── DELETE EVENT ──────────────────────────────────────────────────────

@app.route("/api/event/<int:event_id>", methods=["DELETE"])
def delete_event_route(event_id):
    delete_event(event_id)
    socketio.emit("event_deleted", {"event_id": event_id})
    return jsonify({"success": True})


# ── SAVE DRAFT (admin edits before approving) ─────────────────────────

@app.route("/api/event/<int:event_id>/draft", methods=["POST"])
def save_draft_route(event_id):
    import json as jsonlib
    data = request.get_json() or {}

    update_event_details(
        event_id,
        data.get("title"),
        data.get("date"),
        data.get("time"),
        data.get("location"),
        data.get("description", "")
    )
    save_draft_review_data(
        event_id,
        data.get("notification_message", ""),
        jsonlib.dumps(data.get("reminders", []))
    )
    event = get_event(event_id)
    socketio.emit("draft_updated", {"event_id": event_id, "event": dict(event)})
    return jsonify({"success": True, "event": dict(event)})


# ── APPROVE EVENT → send notifications + schedule reminders ──────────

@app.route("/api/event/<int:event_id>/approve", methods=["POST"])
def approve_event_route(event_id):
    import json as jsonlib
    data = request.get_json() or {}

    # 1 — Update event details if admin changed anything in the review panel
    if data.get("title"):
        update_event_details(
            event_id,
            data.get("title"),
            data.get("date"),
            data.get("time"),
            data.get("location"),
            data.get("description", "")
        )

    # 2 — Publish the event (status → active)
    publish_event(event_id)
    event   = get_event(event_id)
    members = get_all_members()

    # 3 — Send notification emails to all members
    notification_message = data.get("notification_message", "")
    if notification_message and members:
        from email_service import send_bulk_emails
        subject = f"[PulseAI] {event['title']} — Club Update"
        body = (
            f"Hi {{name}},\n\n{notification_message}\n\n"
            f"Event:    {event['title']}\n"
            f"Date:     {event['date']} at {event['time']}\n"
            f"Location: {event['location']}\n\n"
            f"View event & RSVP: https://pulseai-qk01.onrender.com/event/{event['id']}\n\n"
            f"— PulseAI Club Management"
        )
        send_bulk_emails(members, subject, body)
        log_notification(event_id, notification_message, "initial")
        socketio.emit("members_notified", {
            "event_id": event_id,
            "count":    len(members),
            "message":  notification_message
        })

    # 4 — Schedule all reminders with real fire times
    reminders = data.get("reminders", [])
    scheduled_count = 0
    for reminder in reminders:
        try:
            event_dt       = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")
            minutes_before = int(reminder.get("minutes_before", 60))
            fire_at        = event_dt - timedelta(minutes=minutes_before)

            if fire_at > datetime.now():
                job_id       = f"reminder_{event_id}_{uuid.uuid4().hex[:8]}"
                reminder_msg = reminder.get("message", f"Reminder: {event['title']} is coming up!")

                scheduler.add_job(
                    func=tools.fire_reminder,
                    trigger="date",
                    run_date=fire_at,
                    args=[event_id, reminder_msg, job_id],
                    id=job_id,
                    replace_existing=True
                )
                save_scheduled_job(event_id, job_id, fire_at.isoformat(), reminder_msg)
                scheduled_count += 1
                socketio.emit("reminder_scheduled_confirmed", {
                    "event_id":  event_id,
                    "job_id":    job_id,
                    "fires_at":  fire_at.strftime("%B %d at %H:%M"),
                    "message":   reminder_msg
                })
        except Exception as e:
            print(f"Error scheduling reminder: {e}")

    # 5 — Broadcast event published to all connected clients
    socketio.emit("event_published", {
        "event_id":        event_id,
        "event":           dict(event),
        "members_notified": len(members),
        "reminders_scheduled": scheduled_count
    })

    return jsonify({
        "success":             True,
        "event":               dict(event),
        "members_notified":    len(members),
        "reminders_scheduled": scheduled_count
    })


# ── RSVP FROM EVENT PAGE ──────────────────────────────────────────────

@app.route("/api/rsvp/event/<int:event_id>", methods=["POST"])
def rsvp_for_event(event_id):
    data         = request.get_json()
    member_email = data.get("email", "").strip().lower()
    status       = data.get("status")

    if not member_email or not status:
        return jsonify({"error": "Missing email or status"}), 400

    member = get_member_by_email(member_email)
    if not member:
        return jsonify({"error": "Email not found in club members"}), 404

    save_rsvp(event_id, member["id"], status)
    counts   = get_rsvp_counts(event_id)
    attending = get_attending_members(event_id)

    socketio.emit("rsvp_update", {
        "event_id":     event_id,
        "total":        counts["attending"] + counts["not_attending"],
        "attending":    counts["attending"],
        "not_attending": counts["not_attending"],
        "latest_name":  member["name"]
    })
    return jsonify({
        "success":     True,
        "member_name": member["name"],
        "status":      status,
        "counts":      counts
    })


@app.route("/api/upload-flyer/<int:event_id>", methods=["POST"])
def upload_flyer(event_id):
    if "flyer" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["flyer"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    os.makedirs("static/flyers", exist_ok=True)
    ext      = file.filename.rsplit(".", 1)[-1].lower()
    filename = f"flyer_{event_id}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join("static/flyers", filename)
    file.save(filepath)
    return jsonify({"success": True, "flyer_url": f"/static/flyers/{filename}"})


# ── TREASURY ROUTES ───────────────────────────────────────────────────

@app.route("/treasury")
def treasury_page():
    summary  = get_treasury_summary()
    expenses = get_expenses()
    return render_template("treasury.html", summary=summary, expenses=expenses)

@app.route("/api/treasury/summary", methods=["GET"])
def treasury_summary_api():
    return jsonify(get_treasury_summary())

@app.route("/api/treasury/expense", methods=["POST"])
def log_expense_api():
    data        = request.get_json()
    amount      = data.get("amount")
    category    = data.get("category", "General")
    description = data.get("description", "")
    event_id    = data.get("event_id")
    if not amount or not description:
        return jsonify({"error": "Amount and description required"}), 400
    expense_id = log_expense(float(amount), category, description, event_id)
    summary = get_treasury_summary()
    socketio.emit("treasury_updated", summary)
    return jsonify({"success": True, "expense_id": expense_id, "summary": summary})

@app.route("/api/treasury/expenses", methods=["GET"])
def get_expenses_api():
    return jsonify(get_expenses())

@app.route("/api/treasury/budget", methods=["POST"])
def update_budget():
    data   = request.get_json()
    amount = data.get("amount")
    if not amount or float(amount) <= 0:
        return jsonify({"error": "Invalid amount"}), 400
    set_budget(float(amount))
    summary = get_treasury_summary()
    socketio.emit("treasury_updated", summary)
    return jsonify({"success": True, "summary": summary})


# ── EVENT BUDGET ROUTES ───────────────────────────────────────────────

@app.route("/api/event/<int:event_id>/budget", methods=["GET"])
def get_event_budget_api(event_id):
    summary  = get_event_budget_summary(event_id)
    expenses = get_event_expenses(event_id)
    return jsonify({"summary": summary, "expenses": expenses})

@app.route("/api/event/<int:event_id>/budget", methods=["POST"])
def set_event_budget_api(event_id):
    data   = request.get_json()
    amount = data.get("amount")
    if amount is None or float(amount) < 0:
        return jsonify({"error": "Invalid amount"}), 400
    set_event_budget(event_id, float(amount))
    summary = get_event_budget_summary(event_id)
    socketio.emit("event_budget_updated", {"event_id": event_id, "summary": summary})
    return jsonify({"success": True, "summary": summary})

@app.route("/api/event/<int:event_id>/expense", methods=["POST"])
def log_event_expense_api(event_id):
    data        = request.get_json()
    amount      = data.get("amount")
    category    = data.get("category", "Other")
    description = data.get("description", "")
    if not amount or float(amount) <= 0:
        return jsonify({"error": "Invalid amount"}), 400
    if not description:
        return jsonify({"error": "Description required"}), 400
    log_event_expense(event_id, float(amount), category, description)
    summary  = get_event_budget_summary(event_id)
    expenses = get_event_expenses(event_id)
    socketio.emit("event_budget_updated", {"event_id": event_id, "summary": summary})
    return jsonify({"success": True, "summary": summary, "expenses": expenses})


# ── MAIN ──────────────────────────────────────────────────────────────
@app.route("/api/budget/scrape", methods=["POST"])
def scrape_budget_item_route():
    data     = request.get_json() or {}
    url      = data.get("url", "").strip()
    delivery = data.get("delivery_to_fordham", True)
    if not url:
        return jsonify({"success": False, "error": "URL required"}), 400
    result = tools.impl_scrape_budget_item(url, delivery)
    return jsonify(result)

@app.route("/api/budget/compliance", methods=["POST"])
def check_sabc_compliance_route():
    data = request.get_json() or {}
    result = tools.impl_check_sabc_compliance(
        event_type       = data.get("event_type", "social"),
        attendance       = int(data.get("attendance", 0)),
        has_food_delivery= bool(data.get("has_food_delivery", False)),
        food_order_total = float(data.get("food_order_total", 0)),
        has_performer    = bool(data.get("has_performer", False)),
        has_alcohol      = bool(data.get("has_alcohol", False)),
        event_date       = data.get("event_date"),
        is_pizza_order   = bool(data.get("is_pizza_order", False))
    )
    return jsonify(result)

# ── MAIN ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 PulseAI starting on http://localhost:{port}")
    print(f"📅 Today: {datetime.now().strftime('%A, %B %d, %Y')}")
    socketio.run(app, host="0.0.0.0", port=port, debug=False)