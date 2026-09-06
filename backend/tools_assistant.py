"""
Reminder and memory tools.

Kept separate from tools_core so the scheduler (which reads the day planner from
tools_core) never forms an import cycle with the tools that drive it.
"""

import os
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


_DOC_EXTENSIONS = (".txt", ".md", ".markdown", ".log", ".csv", ".json", ".py", ".js", ".ts")
_DOC_MAX_BYTES = 400_000
_DOC_CHUNK_CHARS = 1000


def _chunk_text(text: str, size: int = _DOC_CHUNK_CHARS) -> list:
    """Split on paragraph boundaries, packing paragraphs up to ~size chars."""
    chunks, current = [], ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= size:
            current = f"{current}\n\n{para}" if current else para
            continue
        if current:
            chunks.append(current)
        # A single oversized paragraph is split hard.
        while len(para) > size:
            chunks.append(para[:size])
            para = para[size:]
        current = para
    if current:
        chunks.append(current)
    return chunks


@tool(
    name="remember_document",
    description=(
        "Read a text file (txt, md, code, csv...) into Dexter's long-term memory "
        "so its contents can be recalled later by meaning, with the file cited "
        "as the source."
    ),
    params={"path": {"type": "string", "description": "Path to the file to ingest"}},
    examples=[
        "remember this document",
        "ingest my notes file into your memory",
        "learn the contents of that readme",
        "add this file to your knowledge",
        "memorize the project notes",
    ],
    category="memory",
)
def remember_document(path: str) -> ToolResult:
    import pathguard

    allowed = pathguard.resolve(path)
    if not allowed:
        return ToolResult.fail(pathguard.denial(path))
    if not os.path.isfile(allowed):
        return ToolResult.fail(f"'{path}' is not a file I can read.")
    if not allowed.lower().endswith(_DOC_EXTENSIONS):
        return ToolResult.fail(
            "I can only ingest text-like files for now "
            f"({', '.join(_DOC_EXTENSIONS[:4])}...). PDFs come later."
        )
    if os.path.getsize(allowed) > _DOC_MAX_BYTES:
        return ToolResult.fail("That file is too large to ingest (400KB limit).")

    try:
        with open(allowed, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except Exception as exc:
        return ToolResult.fail(f"Could not read the file: {exc}")

    stored = memory.add_document(allowed, _chunk_text(text))
    if not stored:
        return ToolResult.fail("The file had no readable content to remember.")
    name = os.path.basename(allowed)
    return ToolResult(
        ok=True,
        summary=f"Read {name} into memory - {stored} passage(s). Ask me about it any time.",
        data={"source": allowed, "chunks": stored},
    )


# ───────────────────────── time ─────────────────────────


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
