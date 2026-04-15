import uuid
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urlparse
from database import (
    create_event, get_all_members, get_all_events,
    log_notification, save_scheduled_job, get_rsvps_for_event,
    get_event, mark_job_fired, save_draft_review_data
)
from email_service import send_bulk_emails

# ── GLOBALS ──────────────────────────────────────────────────────────
socketio  = None
scheduler = None

def set_socketio(sio):
    global socketio
    socketio = sio

def set_scheduler(sch):
    global scheduler
    scheduler = sch


def emit_agent_action(action_type, data):
    if socketio:
        socketio.emit("agent_action", {
            "type": action_type,
            "data": data,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        })


def _validate_event_inputs(title, date, time, location):
    if not title:   return False, "title is required"
    if not date:    return False, "date is required"
    if not time:    return False, "time is required"
    if not location: return False, "location is required"
    return True, "ok"


# ── SABC RULES ───────────────────────────────────────────────────────

SABC_RULES = {
    "tip_table": [
        (0,10,3),(11,15,4),(16,25,5),(26,35,6),(36,45,7),
        (46,50,8),(51,60,9),(61,99,10),(100,125,12),
        (126,150,14),(151,175,15),(176,200,20),
    ],
    "pizza_table": [
        (1,8,1),(9,16,2),(17,24,3),(25,32,4),(33,40,5),
        (41,48,6),(49,56,7),(57,64,8),(65,72,9),(73,80,10),
    ],
    "security_rate": 39.91,
    "security_min_hours": 4,
    "contract_lead_weeks": 3,
    "speaker_form_lead_weeks": 4,
}

def _calculate_tip(order_total):
    for low, high, tip in SABC_RULES["tip_table"]:
        if low <= order_total <= high:
            return float(tip)
    return 20.0 if order_total > 200 else 3.0

def _calculate_pizza_pies(attendance):
    for low, high, pies in SABC_RULES["pizza_table"]:
        if low <= attendance <= high:
            return pies
    return round(attendance / 8) if attendance > 80 else 1

def _calculate_security(attendance, has_alcohol):
    if attendance < 50:
        return {"required": False}
    ratio = 50 if has_alcohol else 100
    guards = max(1, -(-attendance // ratio))
    hours = SABC_RULES["security_min_hours"] + 1
    total = round(guards * hours * SABC_RULES["security_rate"], 2)
    return {
        "required": True, "guards": guards, "hours": hours,
        "rate_per_hour": SABC_RULES["security_rate"], "total_cost": total,
        "note": f"Must start 30 min before event, end 30 min after. ${SABC_RULES['security_rate']}/guard/hr, min 4 hrs."
    }


# ── TOOL IMPLEMENTATIONS ─────────────────────────────────────────────

def impl_create_event(title, date, time, location, description=""):
    ok, reason = _validate_event_inputs(title, date, time, location)
    if not ok:
        emit_agent_action("tool_error", {"tool": "create_event", "error": reason})
        return {"success": False, "error": reason}

    event_id = create_event(title, date, time, location, description, status="draft")
    emit_agent_action("event_drafted", {
        "event_id": event_id, "title": title, "date": date,
        "time": time, "location": location, "description": description
    })
    return {"success": True, "event_id": event_id,
            "message": f"Draft created: '{title}' on {date} at {time}"}


def impl_check_conflicts(date, time, location):
    events    = get_all_events()
    conflicts = []
    for event in events:
        if event["status"] == "active" and event["date"] == date:
            if event["location"].lower() == location.lower():
                conflicts.append({"event_id": event["id"], "title": event["title"],
                                   "time": event["time"], "conflict_type": "same_location_same_day"})
            elif event["time"] == time:
                conflicts.append({"event_id": event["id"], "title": event["title"],
                                   "location": event["location"], "conflict_type": "same_time"})
    campus_events = [
        {"title": "Fordham Career Fair", "date": date, "time": "18:00", "location": "McGinley Center"},
        {"title": "Finals Study Week",   "date": date, "time": "all-day", "location": "Duane Library"},
    ]
    emit_agent_action("conflicts_checked", {
        "date": date, "time": time, "location": location,
        "conflicts_found": len(conflicts), "conflicts": conflicts,
    })
    return {"success": True, "conflicts": conflicts, "conflict_count": len(conflicts),
            "message": f"Found {len(conflicts)} conflicts."}


def impl_save_draft_review(event_id, notification_message, reminders):
    """
    Store the agent's suggested notification message and reminder list on the draft.
    Emits 'draft_ready' so the frontend shows the review panel.
    reminders = [{"minutes_before": 1440, "message": "..."}, ...]
    """
    event = get_event(event_id)
    if not event:
        return {"success": False, "error": f"Event {event_id} not found"}

    reminders_json = json.dumps(reminders)
    save_draft_review_data(event_id, notification_message, reminders_json)

    # Emit draft_ready — triggers the review panel in the frontend
    emit_agent_action("draft_ready", {
        "event_id":            event_id,
        "title":               event["title"],
        "date":                event["date"],
        "time":                event["time"],
        "location":            event["location"],
        "description":         event.get("description", ""),
        "notification_message": notification_message,
        "reminders":           reminders,
    })

    return {
        "success": True,
        "message": f"Draft ready for review. Admin can now edit and approve event {event_id}."
    }


def impl_notify_members(event_id, message):
    """Direct notification — used by approve endpoint, not agent auto-flow."""
    members = get_all_members()
    event   = get_event(event_id)
    if not event:
        return {"success": False, "error": f"Event {event_id} not found"}

    for member in members:
        emit_agent_action("member_notified", {
            "member_name": member["name"], "member_email": member["email"],
            "message": message, "event_id": event_id
        })

    log_notification(event_id, message, "initial")
    subject = f"[PulseAI] {event['title']} — Club Update"
    body = (
        f"Hi {{name}},\n\n{message}\n\n"
        f"Event:    {event['title']}\n"
        f"Date:     {event['date']} at {event['time']}\n"
        f"Location: {event['location']}\n\n"
        f"View event & RSVP: https://pulseai-qk01.onrender.com/event/{event['id']}\n\n"
        f"— PulseAI Club Management"
    )
    email_results = send_bulk_emails(members, subject, body)
    emit_agent_action("notify_complete", {
        "event_id": event_id, "members_notified": len(members),
        "emails_sent": email_results["sent"]
    })
    return {"success": True, "members_notified": len(members),
            "emails_sent": email_results["sent"],
            "message": f"Notified {len(members)} members. {email_results['sent']} emails sent."}


def impl_schedule_reminder(event_id, reminder_message, minutes_before):
    """Called by approve endpoint with real fire times."""
    event = get_event(event_id)
    if not event:
        return {"success": False, "error": f"Event {event_id} not found"}

    job_id = f"reminder_{event_id}_{uuid.uuid4().hex[:8]}"

    # Production: calculate real fire time from event date/time
    try:
        event_dt = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")
        fire_at  = event_dt - timedelta(minutes=minutes_before)
        if fire_at <= datetime.now():
            # Event is in the past or reminder would fire immediately — skip
            return {"success": False, "error": "Reminder time is in the past — skipped"}
    except ValueError:
        fire_at = datetime.now() + timedelta(seconds=20)  # fallback demo

    if scheduler:
        scheduler.add_job(
            func=fire_reminder, trigger="date", run_date=fire_at,
            args=[event_id, reminder_message, job_id],
            id=job_id, replace_existing=True
        )

    save_scheduled_job(event_id, job_id, fire_at.isoformat(), reminder_message)
    emit_agent_action("reminder_scheduled", {
        "event_id": event_id, "job_id": job_id, "message": reminder_message,
        "fires_at": fire_at.strftime("%Y-%m-%d %H:%M"), "minutes_before": minutes_before,
    })
    return {"success": True, "job_id": job_id, "fires_at": fire_at.isoformat(),
            "message": f"Reminder scheduled for {fire_at.strftime('%b %d at %H:%M')}"}


def fire_reminder(event_id, message, job_id):
    members = get_all_members()
    print(f"🔔 Reminder firing: {job_id}")
    if socketio:
        socketio.emit("reminder_fired", {
            "event_id": event_id, "message": message, "job_id": job_id,
            "member_count": len(members), "timestamp": datetime.now().strftime("%H:%M:%S")
        })
    log_notification(event_id, f"[REMINDER FIRED] {message}", "reminder")
    mark_job_fired(job_id)
    event = get_event(event_id)
    if event:
        subject = f"[PulseAI REMINDER] {event['title']}"
        body = (
            f"Hi {{name}},\n\n{message}\n\n"
            f"Event:    {event['title']}\n"
            f"Date:     {event['date']} at {event['time']}\n"
            f"Location: {event['location']}\n\n"
            f"View event & RSVP: https://pulseai-qk01.onrender.com/event/{event['id']}\n\n"
            f"— PulseAI Club Management"
        )
        send_bulk_emails(members, subject, body)


def impl_open_rsvp(event_id):
    event   = get_event(event_id)
    members = get_all_members()
    if not event:
        return {"success": False, "error": f"Event {event_id} not found"}
    emit_agent_action("rsvp_opened", {"event_id": event_id, "event_title": event["title"],
                                       "member_count": len(members)})
    return {"success": True, "event_id": event_id,
            "message": f"RSVP collection open for {len(members)} members."}


def impl_get_rsvp_summary(event_id):
    rsvps = get_rsvps_for_event(event_id)
    event = get_event(event_id)
    attending     = [r for r in rsvps if r["status"] == "attending"]
    not_attending = [r for r in rsvps if r["status"] == "not_attending"]
    summary = {
        "event_id": event_id, "event_title": event["title"] if event else "Unknown",
        "total_responses": len(rsvps), "attending": len(attending),
        "not_attending": len(not_attending),
        "attending_names": [r["name"] for r in attending],
        "not_attending_names": [r["name"] for r in not_attending]
    }
    emit_agent_action("rsvp_summary", summary)
    return {"success": True, **summary}


# ── BUDGET TOOLS ─────────────────────────────────────────────────────

def impl_scrape_budget_item(url: str, delivery_to_fordham: bool = True) -> dict:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        product_name = None
        price        = None
        vendor       = None

        if "amazon.com" in url:
            vendor = "Amazon"
            title_el = soup.find(id="productTitle")
            if title_el:
                product_name = title_el.get_text(strip=True)
            for selector in ["#priceblock_ourprice","#priceblock_dealprice",
                              ".a-price .a-offscreen","#price_inside_buybox"]:
                el = soup.select_one(selector)
                if el:
                    raw = el.get_text(strip=True).replace("$","").replace(",","").strip()
                    try:
                        price = float(raw.split()[0])
                        break
                    except ValueError:
                        continue

        if not product_name:
            og = soup.find("meta", property="og:title")
            product_name = og.get("content","").strip() if og else None
        if not product_name:
            h1 = soup.find("h1")
            product_name = h1.get_text(strip=True) if h1 else (soup.title.string.strip() if soup.title else "Unknown Product")
        if not vendor:
            vendor = urlparse(url).netloc.replace("www.","").split(".")[0].capitalize()
        if not price:
            og_price = soup.find("meta", property="product:price:amount")
            if og_price:
                try:
                    price = float(og_price.get("content","0"))
                except ValueError:
                    pass
        if not price:
            for cls in ["price","product-price","offer-price","sale-price"]:
                el = soup.find(class_=cls)
                if el:
                    raw = el.get_text(strip=True).replace("$","").replace(",","")
                    try:
                        price = float(raw.split()[0])
                        break
                    except ValueError:
                        continue

        if not price:
            return {"success": False, "error": "Could not extract price. Try entering it manually."}

        NYC_TAX   = 0.08875
        tax_amount = round(price * NYC_TAX, 2) if not delivery_to_fordham else 0.0
        line_item  = {
            "product_name": product_name[:100], "vendor": vendor,
            "unit_price": round(price, 2), "quantity": 1,
            "line_total": round(price + tax_amount, 2), "url": url,
            "delivery_to_fordham": delivery_to_fordham,
            "tax_exempt": delivery_to_fordham, "tax_line": None
        }
        if not delivery_to_fordham:
            line_item["tax_line"] = {
                "description": f"Tax for {product_name[:40]} (NYC 8.875%)",
                "amount": tax_amount, "note": "Pickup — tax itemized per SABC rules"
            }
        emit_agent_action("budget_item_scraped", {
            "product": line_item["product_name"], "price": line_item["unit_price"],
            "vendor": vendor, "tax_exempt": delivery_to_fordham,
        })
        return {"success": True, "line_item": line_item,
                "message": f"Scraped: {line_item['product_name']} — ${line_item['unit_price']} from {vendor}."}

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Page timed out."}
    except Exception as e:
        return {"success": False, "error": f"Scraping failed: {str(e)}"}


def impl_check_sabc_compliance(event_type, attendance, has_food_delivery=False,
                                food_order_total=0.0, has_performer=False,
                                has_alcohol=False, event_date=None, is_pizza_order=False):
    flags = []
    auto_items = []
    required_docs = []

    sec = _calculate_security(attendance, has_alcohol)
    if sec["required"]:
        auto_item = {
            "description": f"Security — {sec['guards']} guard(s) × {sec['hours']} hrs",
            "quantity": sec["guards"] * sec["hours"],
            "unit_price": SABC_RULES["security_rate"],
            "vendor": "Fordham Public Safety", "total": sec["total_cost"],
            "tax_exempt": True, "note": sec["note"]
        }
        flags.append({"type": "SECURITY_REQUIRED", "severity": "required",
                       "message": f"{attendance} attendees → security required: ${sec['total_cost']}.",
                       "auto_item": auto_item})
        auto_items.append(auto_item)

    if has_food_delivery and food_order_total > 0:
        tip = _calculate_tip(food_order_total)
        auto_item = {
            "description": f"Delivery tip (SABC-approved, order ${food_order_total})",
            "quantity": 1, "unit_price": tip, "vendor": "Delivery",
            "total": tip, "tax_exempt": True,
            "note": "Must match SABC tip table exactly."
        }
        flags.append({"type": "TIP_REQUIRED", "severity": "required",
                       "message": f"Order ${food_order_total} → SABC tip: ${tip}.",
                       "auto_item": auto_item})
        auto_items.append(auto_item)

    if is_pizza_order and attendance > 0:
        pies = _calculate_pizza_pies(attendance)
        flags.append({"type": "PIZZA_ALLOCATION", "severity": "info",
                       "message": f"SABC pizza chart: {attendance} people → {pies} pie(s).",
                       "recommended_pies": pies})

    if has_performer:
        required_docs.extend([
            "Speaker-Performer-Service Provider Form (due 4 weeks before)",
            "W-9 Form (from Office Manager)",
            "Fordham University Contract (due 3 weeks before)"
        ])
        flags.append({"type": "PERFORMER_DOCS", "severity": "required",
                       "message": "Performer detected — Speaker-Performer Form required even if $0 honorarium."})
        if event_date:
            try:
                ev = datetime.strptime(event_date, "%Y-%m-%d")
                flags.append({"type": "CONTRACT_DEADLINES", "severity": "warning",
                               "message": f"Form due: {(ev - timedelta(weeks=4)).strftime('%B %d')}. Contract due: {(ev - timedelta(weeks=3)).strftime('%B %d')}."})
            except ValueError:
                pass

    flags.append({"type": "SHIPPING_REMINDER", "severity": "info",
                   "message": "Itemize shipping costs as a separate line item."})

    emit_agent_action("sabc_compliance_checked", {
        "event_type": event_type, "attendance": attendance,
        "flags": len(flags), "auto_items": len(auto_items)
    })
    return {"success": True, "flags": flags, "auto_items": auto_items,
            "required_docs": required_docs,
            "summary": f"{len(flags)} compliance items. {len(auto_items)} auto-calculated items."}


def impl_build_budget_packet(event_name, event_date, expected_attendance,
                              mission, community_benefit, items, priority=1):
    if not items:
        return {"success": False, "error": "No items provided."}

    subtotal = 0.0
    formatted_lines = []
    for i, item in enumerate(items, 1):
        qty   = int(item.get("quantity", 1))
        price = float(item.get("unit_price", 0))
        lt    = round(qty * price, 2)
        subtotal += lt
        formatted_lines.append({
            "line_number": i,
            "item_description": item.get("description", item.get("product_name", "Item")),
            "quantity": qty, "unit_price": f"${price:.2f}", "line_total": f"${lt:.2f}",
            "vendor": item.get("vendor",""), "product_url": item.get("url",""),
            "tax_status": "Tax Exempt" if item.get("tax_exempt", True) else "Taxable (pickup)",
            "notes": item.get("note",""),
        })

    packet = {
        "RAMS_INVOLVED_FORM_FIELDS": {
            "Event Name": event_name,
            "Amount Requested": f"${subtotal:.2f}",
            "Budget Period": "— Select current semester —",
            "Mission": mission, "Community Benefit": community_benefit,
            "Event Date": event_date, "Expected Attendance": str(expected_attendance),
            "Event Description": f"Budget for {event_name}. {expected_attendance} expected attendees.",
            "Priority": str(priority),
        },
        "ITEMIZED_LINE_ITEMS": formatted_lines,
        "BUDGET_SUMMARY": {"Total Items": len(formatted_lines), "Total": f"${subtotal:.2f}"},
        "COPY_PASTE_READY": True,
    }
    emit_agent_action("budget_packet_built", {
        "event_name": event_name, "total_items": len(formatted_lines),
        "total_amount": f"${subtotal:.2f}"
    })
    return {"success": True, "budget_packet": packet, "total_amount": subtotal,
            "message": f"Budget packet ready — {len(formatted_lines)} items, ${subtotal:.2f}."}


# ── TOOL DEFINITIONS ─────────────────────────────────────────────────

TOOLS = [
    {
        "name": "create_event",
        "description": (
            "Create a new club event as a DRAFT. "
            "Call this FIRST — it returns an event_id needed by all other tools. "
            "Event is saved as draft — admin must approve before members are notified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title":       {"type": "string", "description": "Event title"},
                "date":        {"type": "string", "description": "Date YYYY-MM-DD. Calculate from 'next Thursday' etc."},
                "time":        {"type": "string", "description": "Time HH:MM 24h format"},
                "location":    {"type": "string", "description": "Room or building"},
                "description": {"type": "string", "description": "Optional agenda or details"}
            },
            "required": ["title", "date", "time", "location"]
        }
    },
    {
        "name": "check_conflicts",
        "description": "Check for scheduling conflicts with existing published events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date":     {"type": "string", "description": "Date YYYY-MM-DD"},
                "time":     {"type": "string", "description": "Time HH:MM"},
                "location": {"type": "string", "description": "Location to check"}
            },
            "required": ["date", "time", "location"]
        }
    },
    {
        "name": "save_draft_review",
        "description": (
            "Save the agent's suggested notification message and reminder schedule to the draft. "
            "Call this LAST — after create_event and check_conflicts. "
            "This triggers the admin review panel in the UI. "
            "Do NOT call notify_members or schedule_reminder — those fire only after admin approves."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "integer",
                    "description": "Event ID from create_event"
                },
                "notification_message": {
                    "type": "string",
                    "description": "Pre-written notification message for members. Make it clear and friendly with all event details."
                },
                "reminders": {
                    "type": "array",
                    "description": "Array of reminder objects. Each has minutes_before (int) and message (string).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "minutes_before": {"type": "integer"},
                            "message": {"type": "string"}
                        }
                    }
                }
            },
            "required": ["event_id", "notification_message", "reminders"]
        }
    },
    {
        "name": "get_rsvp_summary",
        "description": "Get RSVP counts and attendee names for a published event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "integer", "description": "Event ID"}
            },
            "required": ["event_id"]
        }
    },
    {
        "name": "scrape_budget_item",
        "description": (
            "Scrape a product URL to get name, price, vendor. Applies SABC tax rules automatically. "
            "Delivered to Fordham = tax exempt. Picked up = 8.875% NYC tax line added."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full product URL"},
                "delivery_to_fordham": {"type": "boolean", "description": "True if delivered (tax exempt), False if pickup (add tax)"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "check_sabc_compliance",
        "description": "Run SABC compliance check. Auto-calculates security, tips, flags performer paperwork.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type":        {"type": "string"},
                "attendance":        {"type": "integer"},
                "has_food_delivery": {"type": "boolean"},
                "food_order_total":  {"type": "number"},
                "has_performer":     {"type": "boolean"},
                "has_alcohol":       {"type": "boolean"},
                "event_date":        {"type": "string"},
                "is_pizza_order":    {"type": "boolean"}
            },
            "required": ["event_type", "attendance"]
        }
    },
    {
        "name": "build_budget_packet",
        "description": "Build a complete Rams Involved budget packet from all line items. Copy-paste ready.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_name":          {"type": "string"},
                "event_date":          {"type": "string"},
                "expected_attendance": {"type": "integer"},
                "mission":             {"type": "string"},
                "community_benefit":   {"type": "string"},
                "items":               {"type": "array", "items": {"type": "object"}},
                "priority":            {"type": "integer"}
            },
            "required": ["event_name", "event_date", "expected_attendance", "mission", "community_benefit", "items"]
        }
    }
]


# ── TOOL DISPATCHER ──────────────────────────────────────────────────

def dispatch_tool(tool_name, tool_input):
    dispatch_map = {
        "create_event":          lambda i: impl_create_event(**i),
        "check_conflicts":       lambda i: impl_check_conflicts(**i),
        "save_draft_review":     lambda i: impl_save_draft_review(**i),
        "get_rsvp_summary":      lambda i: impl_get_rsvp_summary(**i),
        "scrape_budget_item":    lambda i: impl_scrape_budget_item(**i),
        "check_sabc_compliance": lambda i: impl_check_sabc_compliance(**i),
        "build_budget_packet":   lambda i: impl_build_budget_packet(**i),
    }

    handler = dispatch_map.get(tool_name)
    if not handler:
        emit_agent_action("tool_error", {"tool": tool_name, "error": "Unknown tool"})
        return {"success": False, "error": f"Unknown tool: {tool_name}"}

    try:
        result = handler(tool_input)
        if result.get("success"):
            emit_agent_action("tool_success", {"tool": tool_name, "summary": result.get("message","")[:120]})
        else:
            emit_agent_action("tool_error", {"tool": tool_name, "error": result.get("error","Unknown error")})
        return result
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Tool {tool_name} raised exception: {error_msg}")
        emit_agent_action("tool_error", {"tool": tool_name, "error": error_msg})
        return {"success": False, "error": error_msg, "tool": tool_name}