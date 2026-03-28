import os
import eventlet
eventlet.monkey_patch()  # MUST be first line before any other imports

from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv

from database import (
    init_db, seed_demo_members, seed_demo_rsvp_data,
    get_all_events, get_all_members,
    save_rsvp, get_rsvps_for_event, get_event
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
seed_demo_rsvp_data()

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

# ── MAIN ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 PulseAI starting on http://localhost:{port}")
    print(f"📅 Today: {datetime.now().strftime('%A, %B %d, %Y')}")
    # MUST use socketio.run — never app.run()
    socketio.run(app, host="0.0.0.0", port=port, debug=False)