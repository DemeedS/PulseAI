import os
import anthropic
from datetime import datetime
from tools import TOOLS, dispatch_tool, emit_agent_action

# ── SYSTEM PROMPT ────────────────────────────────────────────────────
# Pattern from customer_service_agent.ipynb and courses/tool_use:
# Be explicit about ORDER of tool calls, never ask questions, always execute.

SYSTEM_PROMPT = """
You are PulseAI, an autonomous agent for school club management at Fordham University.

When given an instruction from a club president you MUST follow these steps IN ORDER:

1. Call create_event FIRST — extract title, date, time, location from the instruction.
   Always calculate exact dates from the "today" context provided.
   "next Thursday" means find the next Thursday from today's date.

2. Call check_conflicts immediately after — use the same date, time, location.

3. Call notify_members — use the event_id returned by create_event.
   Write a clear, friendly notification message for club members.

4. Call schedule_reminder for EACH reminder mentioned.
   "24 hours before" = minutes_before: 1440
   "1 hour before"   = minutes_before: 60
   "night before"    = minutes_before: 720
   Multiple reminders = multiple separate schedule_reminder calls.

5. Call open_rsvp if the president mentions RSVPs, "let us know", or attendance.

6. After ALL tools complete, write a short 2-sentence summary of everything done.

STRICT RULES:
- Never ask clarifying questions. Extract what you can and proceed.
- Never explain what you are about to do. Just do it.
- Never stop early. All steps must complete.
- If a detail is missing, make a reasonable assumption and continue.
- You are an AGENT, not a chatbot. Execute everything autonomously.
"""

# ── HOOK PATTERN from hooks.py ───────────────────────────────────────
# hooks.py uses PreToolUse and PostToolUse hooks to inspect/log every
# tool call. We replicate this with emit_agent_action before and after
# each tool execution, giving the same visibility in our SocketIO UI.

def pre_tool_hook(tool_name, tool_input):
    """
    Mirrors PreToolUse hook from hooks.py.
    Fires before every tool call — logs to UI and console.
    """
    print(f"🔧 [PRE]  {tool_name}({tool_input})")
    emit_agent_action("tool_called", {
        "tool": tool_name,
        "input": tool_input
    })

def post_tool_hook(tool_name, result):
    """
    Mirrors PostToolUse hook from hooks.py.
    Fires after every tool call — reports success or error to UI.
    """
    if result.get("success"):
        print(f"✅ [POST] {tool_name} → success")
        emit_agent_action("tool_success", {
            "tool": tool_name,
            "message": result.get("message", "")[:120]
        })
    else:
        print(f"❌ [POST] {tool_name} → error: {result.get('error', 'unknown')}")
        emit_agent_action("tool_error", {
            "tool": tool_name,
            "error": result.get("error", "Unknown error")
        })

# ── AGENT LOOP ───────────────────────────────────────────────────────
# Built directly from 04_complete_workflow.ipynb pattern:
#
#   messages = [{"role": "user", "content": ...}]
#   while True:
#       response = client.messages.create(messages=messages, tools=tools)
#       messages.append({"role": "assistant", "content": response.content})
#       if stop_reason == "end_turn": break
#       if stop_reason == "tool_use":
#           build tool_results with matching tool_use_id
#           messages.append({"role": "user", "content": tool_results})

def run_agent(user_input, today_context=""):
    """
    Full autonomous agent loop.
    Claude calls tools → gets results → decides next step → loops until done.
    Every step is broadcast to the UI via SocketIO.
    """

    # Error handling from sdk/_errors.py — catch specific anthropic errors
    try:
        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY")
        )
    except Exception as e:
        emit_agent_action("agent_error", {"message": f"Failed to init client: {e}"})
        return "Agent failed to start — check API key."

    # Inject today's date so Claude can resolve "next Thursday" correctly
    full_input = f"Today is {today_context}.\n\nPresident's instruction: {user_input}"

    # Initial message — pattern from 04_complete_workflow.ipynb
    messages = [{"role": "user", "content": full_input}]

    emit_agent_action("agent_started", {
        "input": user_input,
        "message": "Agent started. Planning actions..."
    })

    tools_called_count = 0
    max_iterations = 15  # safety limit — prevents runaway loops

    for iteration in range(max_iterations):
        print(f"\n── Iteration {iteration + 1} ──────────────────────")

        # ── Call Claude ──────────────────────────────────────────────
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOLS
            )
        except anthropic.APIConnectionError as e:
            print(f"Connection error: {e}")
            emit_agent_action("agent_error", {"message": "Connection lost. Check internet."})
            return "Connection error — agent stopped."
        except anthropic.RateLimitError as e:
            print(f"Rate limit: {e}")
            emit_agent_action("agent_error", {"message": "Rate limit hit. Wait 60 seconds and try again."})
            return "Rate limit — agent stopped."
        except anthropic.APIStatusError as e:
            print(f"API error {e.status_code}: {e}")
            emit_agent_action("agent_error", {"message": f"API error {e.status_code}"})
            return f"API error — agent stopped."

        print(f"Stop reason: {response.stop_reason}")

        # ── Claude is done ───────────────────────────────────────────
        # Pattern from 04_complete_workflow: check stop_reason first
        if response.stop_reason == "end_turn":
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text

            emit_agent_action("agent_complete", {
                "summary": final_text,
                "iterations": iteration + 1,
                "tools_called": tools_called_count
            })

            print(f"\n🎉 Agent complete. {tools_called_count} tools called in {iteration + 1} iterations.")
            return final_text

        # ── Claude wants to use tools ────────────────────────────────
        # Exact pattern from 04_complete_workflow.ipynb:
        # Step 1 — append Claude's full response to messages
        if response.stop_reason == "tool_use":
            messages.append({
                "role": "assistant",
                "content": response.content  # full content block, not just text
            })

            # Step 2 — process each tool call Claude requested
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    tool_name  = block.name
                    tool_input = block.input

                    # PreToolUse hook — log before executing
                    pre_tool_hook(tool_name, tool_input)
                    tools_called_count += 1

                    # Execute the tool
                    result = dispatch_tool(tool_name, tool_input)

                    # PostToolUse hook — log after executing
                    post_tool_hook(tool_name, result)

                    # Step 3 — build tool_result with matching tool_use_id
                    # This is the exact format from 04_complete_workflow.ipynb
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,   # must match block.id exactly
                        "content": str(result)
                    })

            # Step 4 — send tool results back to Claude as a user message
            messages.append({
                "role": "user",
                "content": tool_results
            })

        else:
            # Unexpected stop reason — log and exit cleanly
            print(f"Unexpected stop reason: {response.stop_reason}")
            emit_agent_action("agent_error", {
                "message": f"Unexpected stop: {response.stop_reason}"
            })
            break

    # Reached max iterations without end_turn
    emit_agent_action("agent_complete", {
        "summary": "Agent reached max iterations.",
        "iterations": max_iterations,
        "tools_called": tools_called_count
    })
    return "Agent completed (max iterations reached)."