import os
import eventlet
eventlet.monkey_patch()

import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, abort
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

from database import (
    init_db, seed_demo_members, seed_demo_rsvp_data,
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
    add_member, remove_member
)
from scheduler import init_scheduler
import tools

# ── LOAD ENV ─────────────────────────────────────────────────────────
load_dotenv()

# ── APP SETUP ─────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "pulseai_dev_secret")

# SocketIO MUST use eventlet — never use app.run()
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ── DATABASE ──────────────────────────────────────────────────────────
init_db()
seed_demo_members()
# seed_demo_rsvp_data()
init_treasury_table()
init_event_budget_table()
# seed_demo_expenses()


# ── SCHEDULER ─────────────────────────────────────────────────────────
scheduler = init_scheduler(app)

# ── WIRE DEPENDENCIES INTO TOOLS ──────────────────────────────────────
# tools.py needs socketio to push real-time events to browser
# tools.py needs scheduler to register APScheduler jobs
tools.set_socketio(socketio)
tools.set_scheduler(scheduler)

# ── ROUTES ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/run-agent", methods=["POST"])
def run_agent_route():
    """
    Main endpoint. President types one sentence, agent runs everything.
    Runs in background thread so WebSocket events stream live to browser
    while Claude is still working — judges watch it happen in real time.
    """
    data = request.get_json()
    user_input = data.get("instruction", "").strip()

    if not user_input:
        return jsonify({"error": "No instruction provided"}), 400

    # Give Claude today's date so it can resolve "next Thursday" etc.
    today = datetime.now().strftime("%A, %B %d, %Y at %H:%M")

    # Run agent in background — never block the HTTP response
    # SocketIO events stream to browser while this runs
    def run_in_background():
        from agent import run_agent
        run_agent(user_input, today_context=today)

    socketio.start_background_task(run_in_background)

    return jsonify({
        "status": "Agent started",
        "instruction": user_input,
        "today": today
    })

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
    """Member submits their RSVP — president sees it live via SocketIO."""
    data = request.get_json()
    event_id  = data.get("event_id")
    member_id = data.get("member_id")
    status    = data.get("status")  # "attending" or "not_attending"

    if not all([event_id, member_id, status]):
        return jsonify({"error": "Missing fields"}), 400

    save_rsvp(event_id, member_id, status)

    # Push live update to president's screen
    rsvps = get_rsvps_for_event(event_id)
    attending = [r for r in rsvps if r["status"] == "attending"]

    socketio.emit("rsvp_update", {
        "event_id": event_id,
        "total": len(rsvps),
        "attending": len(attending),
        "not_attending": len(rsvps) - len(attending),
        "latest_name": data.get("member_name", "A member")
    })

    return jsonify({"success": True})

@app.route("/api/rsvp/<int:event_id>", methods=["GET"])
def get_rsvp_summary(event_id):
    rsvps = get_rsvps_for_event(event_id)
    attending     = [r for r in rsvps if r["status"] == "attending"]
    not_attending = [r for r in rsvps if r["status"] == "not_attending"]
    return jsonify({
        "total": len(rsvps),
        "attending": attending,
        "not_attending": not_attending
    })

# ── WEBSOCKET EVENTS ──────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    print(f"✅ Client connected: {request.sid}")
    emit("connected", {"message": "Connected to PulseAI"})

@socketio.on("disconnect")
def on_disconnect():
    print(f"❌ Client disconnected: {request.sid}")

# ── EVENT DETAIL PAGE ────────────────────────────────────────────────

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
        event=event,
        counts=counts,
        attending=attending,
        members=members,
        budget=budget_summary,
        expenses=event_expenses
    )

@app.route("/api/event/<int:event_id>", methods=["GET"])
def get_event_api(event_id):
    event = get_event(event_id)
    if not event:
        return jsonify({"error": "Not found"}), 404
    counts = get_rsvp_counts(event_id)
    attending = get_attending_members(event_id)
    return jsonify({
        "event": event,
        "counts": counts,
        "attending": attending
    })

@app.route("/api/event/<int:event_id>", methods=["POST"])
def update_event_api(event_id):
    """Admin updates event details."""
    data = request.get_json()
    update_event_details(
        event_id,
        data.get("title"),
        data.get("date"),
        data.get("time"),
        data.get("location"),
        data.get("description", "")
    )
    socketio.emit("event_updated", {"event_id": event_id})
    return jsonify({"success": True})

@app.route("/api/rsvp/event/<int:event_id>", methods=["POST"])
def rsvp_for_event(event_id):
    """Member RSVPs from event detail page."""
    data = request.get_json()
    member_email = data.get("email", "").strip().lower()
    status = data.get("status")

    if not member_email or not status:
        return jsonify({"error": "Missing email or status"}), 400

    member = get_member_by_email(member_email)
    if not member:
        return jsonify({"error": "Email not found in club members"}), 404

    # Check if already RSVPed
    # Allow changing RSVP — always overwrite previous response
    save_rsvp(event_id, member["id"], status)

    counts = get_rsvp_counts(event_id)
    attending = get_attending_members(event_id)

    socketio.emit("rsvp_update", {
        "event_id": event_id,
        "total": counts["attending"] + counts["not_attending"],
        "attending": counts["attending"],
        "not_attending": counts["not_attending"],
        "latest_name": member["name"]
    })

    return jsonify({
        "success": True,
        "member_name": member["name"],
        "status": status,
        "counts": counts
    })

@app.route("/api/upload-flyer/<int:event_id>", methods=["POST"])
def upload_flyer(event_id):
    """Upload custom flyer image."""
    if "flyer" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["flyer"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    # Save to static folder
    os.makedirs("static/flyers", exist_ok=True)
    ext = file.filename.rsplit(".", 1)[-1].lower()
    filename = f"flyer_{event_id}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join("static/flyers", filename)
    file.save(filepath)

    return jsonify({
        "success": True,
        "flyer_url": f"/static/flyers/{filename}"
    })

# ── TREASURY ROUTES ──────────────────────────────────────────────────

@app.route("/treasury")
def treasury_page():
    """Treasury dashboard page."""
    summary = get_treasury_summary()
    expenses = get_expenses()
    return render_template(
        "treasury.html",
        summary=summary,
        expenses=expenses
    )

@app.route("/api/treasury/summary", methods=["GET"])
def treasury_summary_api():
    summary = get_treasury_summary()
    return jsonify(summary)

@app.route("/api/treasury/expense", methods=["POST"])
def log_expense_api():
    """Log a new expense — can be called by agent or manually."""
    data = request.get_json()
    amount = data.get("amount")
    category = data.get("category", "General")
    description = data.get("description", "")
    event_id = data.get("event_id")

    if not amount or not description:
        return jsonify({"error": "Amount and description required"}), 400

    expense_id = log_expense(
        amount=float(amount),
        category=category,
        description=description,
        event_id=event_id
    )

    summary = get_treasury_summary()
    socketio.emit("treasury_updated", summary)

    return jsonify({
        "success": True,
        "expense_id": expense_id,
        "summary": summary
    })

@app.route("/api/treasury/expenses", methods=["GET"])
def get_expenses_api():
    expenses = get_expenses()
    return jsonify(expenses)

@app.route("/api/treasury/budget", methods=["POST"])
def update_budget():
    data = request.get_json()
    amount = data.get("amount")
    if not amount or float(amount) <= 0:
        return jsonify({"error": "Invalid amount"}), 400
    set_budget(float(amount))
    summary = get_treasury_summary()
    socketio.emit("treasury_updated", summary)
    return jsonify({"success": True, "summary": summary})

# ── EVENT BUDGET ROUTES ──────────────────────────────────────────────

@app.route("/api/event/<int:event_id>/budget", methods=["GET"])
def get_event_budget_api(event_id):
    summary = get_event_budget_summary(event_id)
    expenses = get_event_expenses(event_id)
    return jsonify({"summary": summary, "expenses": expenses})

@app.route("/api/event/<int:event_id>/budget", methods=["POST"])
def set_event_budget_api(event_id):
    data = request.get_json()
    amount = data.get("amount")
    if amount is None or float(amount) < 0:
        return jsonify({"error": "Invalid amount"}), 400
    set_event_budget(event_id, float(amount))
    summary = get_event_budget_summary(event_id)
    socketio.emit("event_budget_updated", {
        "event_id": event_id,
        "summary": summary
    })
    return jsonify({"success": True, "summary": summary})

@app.route("/api/event/<int:event_id>/expense", methods=["POST"])
def log_event_expense_api(event_id):
    data = request.get_json()
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

    socketio.emit("event_budget_updated", {
        "event_id": event_id,
        "summary": summary
    })

    return jsonify({
        "success": True,
        "summary": summary,
        "expenses": expenses
    })

# ── MAIN ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 PulseAI starting on http://localhost:{port}")
    print(f"📅 Today: {datetime.now().strftime('%A, %B %d, %Y')}")
    # MUST use socketio.run — never app.run()
    socketio.run(app, host="0.0.0.0", port=port, debug=False)