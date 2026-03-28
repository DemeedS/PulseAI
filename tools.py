import uuid
from datetime import datetime, timedelta
from database import (
    create_event, get_all_members, get_all_events,
    log_notification, save_scheduled_job, get_rsvps_for_event,
    get_event, mark_job_fired
)
from email_service import send_bulk_emails

# ── GLOBALS set by app.py after init ────────────────────────────────
# Pattern from agents.py — options/config injected at runtime,
# not hardcoded inside the module
socketio  = None
scheduler = None

def set_socketio(sio):
    global socketio
    socketio = sio

def set_scheduler(sch):
    global scheduler
    scheduler = sch


# ── EMIT HELPER ──────────────────────────────────────────────────────
# Pattern from hooks.py PostToolUse — after every tool action,
# broadcast what happened so the UI stays live

def emit_agent_action(action_type, data):
    """Push real-time update to every connected browser."""
    if socketio:
        socketio.emit("agent_action", {
            "type": action_type,
            "data": data,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        })


# ── PRE-TOOL VALIDATION ──────────────────────────────────────────────
# Pattern from hooks.py check_bash_command (PreToolUse hook) —
# validate inputs before executing, return error dict if invalid
# so the agent loop can recover instead of crashing

def _validate_event_inputs(title, date, time, location):
    if not title:
        return False, "title is required"
    if not date:
        return False, "date is required"
    if not time:
        return False, "time is required"
    if not location:
        return False, "location is required"
    return True, "ok"


# ── TOOL IMPLEMENTATIONS ─────────────────────────────────────────────

def impl_create_event(title, date, time, location, description=""):
    """
    Create event in DB and broadcast to UI.
    PreToolUse validation → execute → PostToolUse broadcast.
    Mirrors the hook lifecycle from hooks.py.
    """
    # PreToolUse — validate before touching DB
    ok, reason = _validate_event_inputs(title, date, time, location)
    if not ok:
        emit_agent_action("tool_error", {
            "tool": "create_event",
            "error": reason
        })
        return {"success": False, "error": reason}

    # Execute
    event_id = create_event(title, date, time, location, description)

    # PostToolUse — broadcast result to UI
    emit_agent_action("event_created", {
        "event_id":    event_id,
        "title":       title,
        "date":        date,
        "time":        time,
        "location":    location,
        "description": description
    })

    return {
        "success":  True,
        "event_id": event_id,
        "message":  f"Event '{title}' created with ID {event_id}"
    }


def impl_notify_members(event_id, message):
    """
    Send in-app + email notifications to all members.
    Iterates members independently — one failure never stops the rest.
    Pattern from agents.py multiple_agents_example.
    """
    members = get_all_members()
    event   = get_event(event_id)

    if not event:
        return {"success": False, "error": f"Event {event_id} not found"}

    # Broadcast each member notification live to UI
    for member in members:
        emit_agent_action("member_notified", {
            "member_name":  member["name"],
            "member_email": member["email"],
            "message":      message,
            "event_id":     event_id
        })

    # Log to DB
    log_notification(event_id, message, "initial")

    # Send emails — returns counts dict
    subject = f"[PulseAI] {event['title']} — Club Update"
    body    = (
        f"Hi {{name}},\n\n"
        f"{message}\n\n"
        f"Event:    {event['title']}\n"
        f"Date:     {event['date']} at {event['time']}\n"
        f"Location: {event['location']}\n\n"
        f"— PulseAI Club Management"
    )
    email_results = send_bulk_emails(members, subject, body)

    # PostToolUse broadcast
    emit_agent_action("notify_complete", {
        "event_id":         event_id,
        "members_notified": len(members),
        "emails_sent":      email_results["sent"]
    })

    return {
        "success":          True,
        "members_notified": len(members),
        "emails_sent":      email_results["sent"],
        "message":          f"Notified {len(members)} members. {email_results['sent']} emails sent."
    }


def impl_schedule_reminder(event_id, reminder_message, minutes_before):
    """
    Schedule an automatic reminder via APScheduler.
    DEMO MODE: always fires in 20 seconds so judges watch it live.
    Production: swap fire_at line to use real event datetime.
    """
    event = get_event(event_id)
    if not event:
        return {"success": False, "error": f"Event {event_id} not found"}

    job_id = f"reminder_{event_id}_{uuid.uuid4().hex[:8]}"

    # DEMO: fires in 20 seconds regardless of minutes_before
    # Production: datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M") - timedelta(minutes=minutes_before)
    fire_at = datetime.now() + timedelta(seconds=20)

    if scheduler:
        scheduler.add_job(
            func=fire_reminder,
            trigger="date",
            run_date=fire_at,
            args=[event_id, reminder_message, job_id],
            id=job_id,
            replace_existing=True
        )

    # Save to DB
    save_scheduled_job(event_id, job_id, fire_at.isoformat(), reminder_message)

    # PostToolUse broadcast
    emit_agent_action("reminder_scheduled", {
        "event_id":     event_id,
        "job_id":       job_id,
        "message":      reminder_message,
        "fires_at":     fire_at.strftime("%H:%M:%S"),
        "minutes_before": minutes_before,
        "demo_note":    "Demo mode: fires in 20 seconds"
    })

    return {
        "success":  True,
        "job_id":   job_id,
        "fires_at": fire_at.isoformat(),
        "message":  f"Reminder scheduled. Fires at {fire_at.strftime('%H:%M:%S')} (20s demo mode)"
    }


def fire_reminder(event_id, message, job_id):
    """
    Called automatically by APScheduler — zero human trigger.
    This is what makes PulseAI a real agent, not a wrapper.
    Pattern: PostToolUse broadcast after autonomous execution.
    """
    members = get_all_members()

    print(f"🔔 Reminder firing automatically: {job_id}")

    # Push live notification to every connected browser
    if socketio:
        socketio.emit("reminder_fired", {
            "event_id":     event_id,
            "message":      message,
            "job_id":       job_id,
            "member_count": len(members),
            "timestamp":    datetime.now().strftime("%H:%M:%S")
        })

    # Log to DB
    log_notification(event_id, f"[REMINDER FIRED] {message}", "reminder")
    mark_job_fired(job_id)

    # Send reminder emails
    event = get_event(event_id)
    if event:
        subject = f"[PulseAI REMINDER] {event['title']}"
        body = (
            f"Hi {{name}},\n\n"
            f"Reminder: {message}\n\n"
            f"Event:    {event['title']}\n"
            f"Date:     {event['date']} at {event['time']}\n"
            f"Location: {event['location']}\n\n"
            f"— PulseAI"
        )
        send_bulk_emails(members, subject, body)


def impl_check_conflicts(date, time, location):
    """
    Check existing events for date/location/time conflicts.
    Also checks a hardcoded campus calendar for demo realism.
    """
    events    = get_all_events()
    conflicts = []

    for event in events:
        if event["date"] == date:
            if event["location"].lower() == location.lower():
                conflicts.append({
                    "event_id":      event["id"],
                    "title":         event["title"],
                    "time":          event["time"],
                    "conflict_type": "same_location_same_day"
                })
            elif event["time"] == time:
                conflicts.append({
                    "event_id":      event["id"],
                    "title":         event["title"],
                    "location":      event["location"],
                    "conflict_type": "same_time"
                })

    # Demo campus calendar — makes conflict check feel real
    campus_events = [
        {"title": "Fordham Career Fair",   "date": date, "time": "18:00", "location": "McGinley Center"},
        {"title": "Finals Study Week",     "date": date, "time": "all-day", "location": "Duane Library"},
    ]

    # PostToolUse broadcast
    emit_agent_action("conflicts_checked", {
        "date":                  date,
        "time":                  time,
        "location":              location,
        "conflicts_found":       len(conflicts),
        "conflicts":             conflicts,
        "campus_events_checked": campus_events
    })

    return {
        "success":               True,
        "conflicts":             conflicts,
        "conflict_count":        len(conflicts),
        "campus_events_on_date": campus_events,
        "message":               f"Found {len(conflicts)} conflicts. Campus calendar checked."
    }


def impl_open_rsvp(event_id):
    """Open RSVP collection for an event."""
    event   = get_event(event_id)
    members = get_all_members()

    if not event:
        return {"success": False, "error": f"Event {event_id} not found"}

    emit_agent_action("rsvp_opened", {
        "event_id":    event_id,
        "event_title": event["title"],
        "member_count": len(members),
        "rsvp_url":    f"/rsvp/{event_id}"
    })

    return {
        "success":  True,
        "event_id": event_id,
        "message":  f"RSVP collection open for {len(members)} members. URL: /rsvp/{event_id}"
    }


def impl_get_rsvp_summary(event_id):
    """Get current RSVP counts and names for an event."""
    rsvps = get_rsvps_for_event(event_id)
    event = get_event(event_id)

    attending     = [r for r in rsvps if r["status"] == "attending"]
    not_attending = [r for r in rsvps if r["status"] == "not_attending"]

    summary = {
        "event_id":           event_id,
        "event_title":        event["title"] if event else "Unknown",
        "total_responses":    len(rsvps),
        "attending":          len(attending),
        "not_attending":      len(not_attending),
        "attending_names":    [r["name"] for r in attending],
        "not_attending_names":[r["name"] for r in not_attending]
    }

    emit_agent_action("rsvp_summary", summary)
    return {"success": True, **summary}


# ── TOOL DEFINITIONS ─────────────────────────────────────────────────
# Exact input_schema format confirmed from:
# courses/tool_use/02_your_first_simple_tool.ipynb
# Rules:
#   - name must match ^[a-zA-Z0-9_-]{1,64}$
#   - description must be detailed — Claude uses it to decide WHEN to call
#   - all required params listed in "required" array
#   - type always "object" at top level

TOOLS = [
    {
        "name": "create_event",
        "description": (
            "Create a new club event in the database with full details. "
            "ALWAYS call this first before any other tool — every other tool needs the event_id this returns. "
            "Extract date, time, location, and title from the president's natural language instruction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Event title, e.g. 'Finance Club Weekly Meeting'"
                },
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format. Calculate from relative terms like 'next Thursday'."
                },
                "time": {
                    "type": "string",
                    "description": "Time in HH:MM 24-hour format, e.g. '19:00' for 7pm"
                },
                "location": {
                    "type": "string",
                    "description": "Room or building, e.g. 'Hughes Hall Room 302'"
                },
                "description": {
                    "type": "string",
                    "description": "Optional agenda or extra details"
                }
            },
            "required": ["title", "date", "time", "location"]
        }
    },
    {
        "name": "notify_members",
        "description": (
            "Send real-time in-app notifications AND emails to ALL club members about an event. "
            "Call this after create_event. Use the event_id returned by create_event."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "The event ID returned by create_event"
                },
                "message": {
                    "type": "string",
                    "description": "The notification message to send. Be specific about date, time, location."
                }
            },
            "required": ["event_id", "message"]
        }
    },
    {
        "name": "schedule_reminder",
        "description": (
            "Schedule an automatic reminder that fires WITHOUT any human trigger. "
            "Call this ONCE per reminder mentioned. If the president says '24 hours before AND 1 hour before', "
            "call this tool TWICE — once with minutes_before=1440, once with minutes_before=60. "
            "The reminder fires automatically at the scheduled time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "The event ID"
                },
                "reminder_message": {
                    "type": "string",
                    "description": "The reminder message to send when it fires"
                },
                "minutes_before": {
                    "type": "integer",
                    "description": "Minutes before event to send reminder. 1440=24hrs, 60=1hr, 30=30min"
                }
            },
            "required": ["event_id", "reminder_message", "minutes_before"]
        }
    },
    {
        "name": "check_conflicts",
        "description": (
            "Check if the proposed event conflicts with existing events or campus bookings. "
            "Call this after create_event to detect scheduling issues."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format"
                },
                "time": {
                    "type": "string",
                    "description": "Time in HH:MM format"
                },
                "location": {
                    "type": "string",
                    "description": "Room or location to check"
                }
            },
            "required": ["date", "time", "location"]
        }
    },
    {
        "name": "open_rsvp",
        "description": (
            "Open RSVP collection so members can indicate attendance. "
            "Call this when the president asks to collect RSVPs or wants to know who can make it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "The event ID to collect RSVPs for"
                }
            },
            "required": ["event_id"]
        }
    },
    {
        "name": "get_rsvp_summary",
        "description": (
            "Get a summary of who has RSVPed for an event — attending vs not attending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "The event ID to summarize RSVPs for"
                }
            },
            "required": ["event_id"]
        }
    }
]


# ── TOOL DISPATCHER ──────────────────────────────────────────────────
# Pattern from 04_complete_workflow.ipynb:
#   tool_name = tool_use.name
#   tool_input = tool_use.input
#   result = dispatch(tool_name, tool_input)
# 
# Pattern from hooks.py stop_on_error_hook —
#   if tool returns error, agent reads it and decides what to do next
#   we never raise exceptions here, always return a dict

def dispatch_tool(tool_name, tool_input):
    """
    Route Claude's tool call to the correct implementation.
    Always returns a dict — never raises.
    Claude reads the returned dict and decides next steps.
    """
    dispatch_map = {
        "create_event":     lambda i: impl_create_event(**i),
        "notify_members":   lambda i: impl_notify_members(**i),
        "schedule_reminder":lambda i: impl_schedule_reminder(**i),
        "check_conflicts":  lambda i: impl_check_conflicts(**i),
        "open_rsvp":        lambda i: impl_open_rsvp(**i),
        "get_rsvp_summary": lambda i: impl_get_rsvp_summary(**i),
    }

    handler = dispatch_map.get(tool_name)

    if not handler:
        error = {"success": False, "error": f"Unknown tool: {tool_name}"}
        emit_agent_action("tool_error", {"tool": tool_name, "error": f"Unknown tool"})
        return error

    try:
        result = handler(tool_input)
        # PostToolUse — broadcast success or failure
        if result.get("success"):
            emit_agent_action("tool_success", {
                "tool":    tool_name,
                "summary": result.get("message", "")[:120]
            })
        else:
            emit_agent_action("tool_error", {
                "tool":  tool_name,
                "error": result.get("error", "Unknown error")
            })
        return result

    except Exception as e:
        # Never crash the agent loop — return error dict so Claude can recover
        error_msg = str(e)
        print(f"❌ Tool {tool_name} raised exception: {error_msg}")
        emit_agent_action("tool_error", {"tool": tool_name, "error": error_msg})
        return {"success": False, "error": error_msg, "tool": tool_name}