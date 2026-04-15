import os
import anthropic
from datetime import datetime
from tools import TOOLS, dispatch_tool, emit_agent_action

SYSTEM_PROMPT = """
You are PulseAI, an autonomous agent for student club management at Fordham University.

═══════════════════════════════════════════
EVENT CREATION — DRAFT FLOW
═══════════════════════════════════════════
When given an instruction to create an event, follow these EXACT steps IN ORDER:

1. Call create_event — extract title, date, time, location.
   Calculate exact dates from the "today" context provided.
   "next Thursday" = find the next Thursday from today's date.
   Event is saved as a DRAFT. Members are NOT notified yet.

2. Call check_conflicts — use the same date, time, location.

3. Call save_draft_review — this is the FINAL step. Pass:
   - event_id: from create_event
   - notification_message: write a complete, friendly notification for members
     (include event name, date, time, location, and any relevant details)
   - reminders: array of {minutes_before, message} based on what the president said
     "24 hours before" → {minutes_before: 1440, message: "..."}
     "1 hour before"   → {minutes_before: 60, message: "..."}
     "night before"    → {minutes_before: 720, message: "..."}
     If no reminders mentioned, suggest sensible defaults (24hr and 1hr before)

CRITICAL RULES:
- NEVER call notify_members or schedule_reminder — those only fire after admin approves
- NEVER ask clarifying questions — extract what you can and proceed
- NEVER stop after create_event — always complete all 3 steps
- After all 3 tools complete, write ONE sentence: "Draft ready — review and approve to notify members."

═══════════════════════════════════════════
BUDGET MODE
═══════════════════════════════════════════
When a treasurer asks to build a budget or prepare items for SABC:

1. For each product URL: call scrape_budget_item
2. Call check_sabc_compliance after all items are scraped
3. Call build_budget_packet with all items assembled
4. Present the Rams Involved fields clearly for copy-paste

SABC RULES:
- Delivered to Fordham = tax exempt. Picked up = 8.875% NYC tax line
- Tips: use exact SABC tip table (never estimate)
- Security: required if 50+ people — $39.91/guard/hr, min 4 hrs, +30min buffer each side
- Performers: Speaker-Performer Form due 4 weeks before. Contract due 3 weeks before.
- Late budgets = automatic 20% deduction
- Everything must be itemized — no lump sums ever
- Shipping is always its own separate line item
"""


def pre_tool_hook(tool_name, tool_input):
    print(f"🔧 [PRE]  {tool_name}({tool_input})")
    emit_agent_action("tool_called", {"tool": tool_name, "input": tool_input})

def post_tool_hook(tool_name, result):
    if result.get("success"):
        print(f"✅ [POST] {tool_name} → success")
        emit_agent_action("tool_success", {"tool": tool_name, "message": result.get("message","")[:120]})
    else:
        print(f"❌ [POST] {tool_name} → error: {result.get('error','unknown')}")
        emit_agent_action("tool_error", {"tool": tool_name, "error": result.get("error","Unknown error")})


def run_agent(user_input, today_context=""):
    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    except Exception as e:
        emit_agent_action("agent_error", {"message": f"Failed to init client: {e}"})
        return "Agent failed to start — check API key."

    full_input = f"Today is {today_context}.\n\nInstruction: {user_input}"
    messages   = [{"role": "user", "content": full_input}]

    emit_agent_action("agent_started", {"input": user_input, "message": "Agent started..."})

    tools_called_count = 0
    max_iterations     = 10

    for iteration in range(max_iterations):
        print(f"\n── Iteration {iteration + 1} ──────────────────────")

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOLS
            )
        except anthropic.APIConnectionError:
            emit_agent_action("agent_error", {"message": "Connection lost."})
            return "Connection error."
        except anthropic.RateLimitError:
            emit_agent_action("agent_error", {"message": "Rate limit. Wait 60 seconds."})
            return "Rate limit."
        except anthropic.APIStatusError as e:
            emit_agent_action("agent_error", {"message": f"API error {e.status_code}"})
            return "API error."

        print(f"Stop reason: {response.stop_reason}")

        if response.stop_reason == "end_turn":
            final_text = "".join(b.text for b in response.content if hasattr(b, "text"))
            emit_agent_action("agent_complete", {
                "summary": final_text, "iterations": iteration + 1,
                "tools_called": tools_called_count
            })
            print(f"\n🎉 Agent complete. {tools_called_count} tools in {iteration + 1} iterations.")
            return final_text

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    pre_tool_hook(block.name, block.input)
                    tools_called_count += 1
                    result = dispatch_tool(block.name, block.input)
                    post_tool_hook(block.name, result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result)
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            emit_agent_action("agent_error", {"message": f"Unexpected stop: {response.stop_reason}"})
            break

    emit_agent_action("agent_complete", {
        "summary": "Max iterations reached.", "iterations": max_iterations,
        "tools_called": tools_called_count
    })
    return "Agent completed (max iterations)."