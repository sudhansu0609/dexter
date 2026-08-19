"""
Reminder and memory tools.

Kept separate from tools_core so the scheduler (which reads the day planner from
tools_core) never forms an import cycle with the tools that drive it.
"""

from typing import Optional

from memory import memory
from scheduler import scheduler
from timeparse import humanize, parse_when, strip_time_phrases
from tool_registry import ToolResult, tool

# ───────────────────────── reminders ─────────────────────────


@tool(
    name="create_reminder",
    description=(
        "Set a reminder that will alert the user at a specific time. "
        "Use for anything the user wants to be told about later, including "
        "recurring reminders like 'every weekday at 9am'."
    ),
    params={
        "text": {"type": "string", "description": "What to remind the user about"},
        "when": {
            "type": "string",
            "description": "When to fire, e.g. 'in 20 minutes', 'tomorrow at 5pm', 'every weekday at 9'",
        },
    },
    examples=[
        "remind me to call mom in 20 minutes",
        "set a reminder for the meeting at 3pm",
        "remind me to take a break every hour",
        "wake me up in 45 minutes",
        "ping me tomorrow at 9 about the invoice",
        "don't let me forget to submit the report at 5",
    ],
    category="reminders",
)
def create_reminder(text: str, when: str) -> ToolResult:
    clean_text = strip_time_phrases(text) or text
    result = scheduler.create_reminder(clean_text, when)

    if not result.get("ok"):
        return ToolResult.fail(
            result.get("error", "I couldn't understand that time.")
            + " Try something like 'in 20 minutes' or 'tomorrow at 5pm'."
        )

    if not result.get("scheduled") and not scheduler.running:
        return ToolResult(
            ok=True,
            summary=(
                f"Saved a reminder to {clean_text}, but the scheduler isn't running "
                "so it won't alert you. Install apscheduler to enable it."
            ),
            data=result,
        )

    return ToolResult(
        ok=True,
        summary=f"I'll remind you to {clean_text} {result.get('humanWhen', '')}.".replace("  ", " "),
        data=result,
    )


@tool(
    name="list_reminders",
    description="List the reminders that are currently pending.",
    params={},
    examples=[
        "what reminders do I have", "show my reminders",
        "what am I being reminded about", "list my alerts",
    ],
    category="reminders",
)
def list_reminders() -> ToolResult:
    pending = scheduler.list_reminders(include_fired=False)
    if not pending:
        return ToolResult(ok=True, summary="You have no pending reminders.", data={"reminders": []})

    lines = []
    for reminder in pending[:6]:
        when = reminder.get("whenText") or ""
        if reminder.get("at"):
            try:
                from datetime import datetime

                when = humanize(datetime.fromisoformat(reminder["at"]))
            except Exception:
                pass
        lines.append(f"{reminder.get('text', '')} ({when})")

    return ToolResult(
        ok=True,
        summary=f"{len(pending)} pending reminder(s): " + "; ".join(lines),
        data={"reminders": pending},
    )


@tool(
    name="cancel_reminder",
    description="Cancel a pending reminder, matched by what it is about.",
    params={"text": {"type": "string", "description": "Part of the reminder text"}},
    examples=["cancel the reminder about the meeting", "forget the reminder to call mom",
              "delete that reminder", "remove the invoice reminder"],
    category="reminders",
)
def cancel_reminder(text: str) -> ToolResult:
    cancelled = scheduler.cancel_by_text(text)
    if not cancelled:
        return ToolResult.fail(f"I couldn't find a pending reminder about '{text}'.")
    return ToolResult(
        ok=True,
        summary=f"Cancelled the reminder to {cancelled.get('text', '')}.",
        data=cancelled,
    )


# ───────────────────────── memory ─────────────────────────


@tool(
    name="remember_fact",
    description=(
        "Store a durable fact about the user so it can be recalled in future "
        "conversations - preferences, names, project details, file locations."
    ),
    params={
        "key": {"type": "string", "description": "Short label for the fact, e.g. 'favourite editor'"},
        "value": {"type": "string", "description": "The fact itself"},
    },
    examples=[
        "remember that my main project folder is on the D drive",
        "remember I prefer vscode over sublime",
        "keep in mind that my channel is called Beyond3Baje",
        "store this for later",
    ],
    category="memory",
)
def remember_fact(key: str, value: str) -> ToolResult:
    if memory.remember(key, value):
        return ToolResult(ok=True, summary=f"Got it - I'll remember that {key} is {value}.",
                          data={"key": key, "value": value})
    return ToolResult.fail("I couldn't store that.")


@tool(
    name="recall_fact",
    description="Look up something the user previously asked Dexter to remember.",
    params={"key": {"type": "string", "description": "What to look up"}},
    examples=[
        "what did I tell you about my project folder",
        "what do you remember about my channel",
        "do you remember my preferred editor",
    ],
    category="memory",
)
def recall_fact(key: str) -> ToolResult:
    value = memory.recall(key)
    if value is None:
        return ToolResult(ok=True, summary=f"I don't have anything stored about '{key}'.",
                          data={"key": key})
    return ToolResult(ok=True, summary=f"{key}: {value}", data={"key": key, "value": value})


@tool(
    name="list_memories",
    description="List everything Dexter has been asked to remember about the user.",
    params={},
    examples=["what do you remember", "list what you know about me", "show your memory"],
    category="memory",
)
def list_memories() -> ToolResult:
    facts = memory.all_facts(limit=25)
    if not facts:
        return ToolResult(ok=True, summary="I haven't been asked to remember anything yet.",
                          data={"facts": []})
    preview = "; ".join(f"{f['key']}: {f['value']}" for f in facts[:6])
    return ToolResult(
        ok=True,
        summary=f"I'm holding {len(facts)} fact(s): {preview}",
        data={"facts": facts},
    )


@tool(
    name="forget_fact",
    description="Delete a stored fact from Dexter's memory.",
    params={"key": {"type": "string", "description": "The fact label to remove"}},
    examples=["forget what I told you about my editor", "delete that memory"],
    confirm=True,
    category="memory",
)
def forget_fact(key: str) -> ToolResult:
    if memory.forget(key):
        return ToolResult(ok=True, summary=f"Forgotten: {key}.", data={"key": key})
    return ToolResult.fail(f"I had nothing stored under '{key}'.")


# ───────────────────────── time ─────────────────────────


@tool(
    name="read_aloud",
    description=(
        "Read something out loud to the user in a narrator voice - their agenda, "
        "notes, the clipboard, or a text file. Use when they want to listen to "
        "content rather than just be told a one-line summary."
    ),
    params={
        "source": {
            "type": "string",
            "description": (
                "What to read: 'agenda', 'tasks', 'notes', 'clipboard', "
                "'reminders', or a full file path"
            ),
        }
    },
    examples=[
        "read my agenda out loud", "read me my tasks", "read out my notes",
        "read the clipboard to me", "read this file aloud",
        "read me my schedule for today", "narrate my day",
    ],
    category="general",
)
def read_aloud(source: str) -> ToolResult:
    """
    Produce long-form narration text.

    The `_speech_emotion` hint tells the speech layer to use the slower narrator
    register with longer pauses, rather than the clipped tone used for
    confirmations.
    """
    target = (source or "").strip().lower()
    body = ""

    if any(word in target for word in ("agenda", "task", "plan", "schedule", "day")):
        body = _narrate_agenda()
    elif "note" in target:
        body = _narrate_notes()
    elif "reminder" in target:
        pending = scheduler.list_reminders(include_fired=False)
        if not pending:
            body = "You have no reminders set."
        else:
            lines = [f"{r.get('text', '')}, {r.get('whenText', '')}" for r in pending]
            body = f"You have {len(pending)} reminders. " + ". ".join(lines) + "."
    elif "clip" in target:
        from windows_tools import read_clipboard

        clip = read_clipboard()
        content = clip.data.get("content", "") if clip.ok else ""
        body = content.strip() or "There is nothing on your clipboard."
    else:
        from filesystem_tool import FileSystemTool

        raw = FileSystemTool.read_file(source, max_chars=6000)
        if not raw.get("success"):
            return ToolResult.fail(
                f"I couldn't read '{source}'. Try 'agenda', 'notes', 'clipboard', "
                "or give me a full file path."
            )
        body = raw.get("content", "").strip()
        if raw.get("truncated"):
            body += " That is as far as I will read for now."

    if not body:
        return ToolResult.fail(f"There is nothing to read for '{source}'.")

    return ToolResult(
        ok=True,
        summary=body,
        data={"_speech_emotion": "reading", "length": len(body), "source": source},
    )


def _narrate_agenda() -> str:
    """Conversational narration, not the terse summary get_agenda returns."""
    from tools_core import day_planner

    tasks = day_planner.get_agenda().get("tasks", [])
    pending = [t for t in tasks if not t.get("completed")]
    done = len(tasks) - len(pending)

    if not tasks:
        return "Your day is completely clear. Nothing on the plan."
    if not pending:
        return f"Everything is done. All {len(tasks)} tasks are complete. Enjoy the rest of your day."

    opener = f"You have {len(pending)} thing{'s' if len(pending) != 1 else ''} left today"
    opener += f", and {done} already done." if done else "."

    ordinals = ["First", "Then", "After that", "Next", "Then", "Later"]
    lines = []
    for index, task in enumerate(pending):
        lead = ordinals[index] if index < len(ordinals) else "Then"
        lines.append(f"{lead}, at {task.get('time', '')}, {task.get('title', '')}.")

    closing = "" if len(pending) < 4 else " That is a full day."
    return f"{opener} {' '.join(lines)}{closing}"


def _narrate_notes() -> str:
    from tools_core import sticky_notes

    notes = sticky_notes.list_notes()
    if not notes:
        return "You have no notes saved."
    lines = [f"Note {i + 1}. {n.get('content', '')}." for i, n in enumerate(notes[:10])]
    return f"You have {len(notes)} note{'s' if len(notes) != 1 else ''}. " + " ".join(lines)


@tool(
    name="get_current_time",
    description="Get the current date and time on the user's PC.",
    params={},
    examples=["what time is it", "what's today's date", "what day is it", "current time"],
    category="general",
)
def get_current_time() -> ToolResult:
    from datetime import datetime

    now = datetime.now()
    return ToolResult(
        ok=True,
        summary=now.strftime("It's %I:%M %p on %A, %B %d.").replace(" 0", " "),
        data={"iso": now.isoformat(timespec="seconds")},
    )
