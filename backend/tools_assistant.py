"""
Reminder and memory tools.

Kept separate from tools_core so the scheduler (which reads the day planner from
tools_core) never forms an import cycle with the tools that drive it.
"""

import os
import re
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
        said = f"Got it - I'll remember: {value}." if value.lower().startswith(("i ", "we ", "you ")) else f"Got it - I'll remember that {key} is {value}."
        return ToolResult(ok=True, summary=said, data={"key": key, "value": value})
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
    import knowledge_ingest

    if not allowed.lower().endswith(_DOC_EXTENSIONS + (".pdf", ".docx")):
        return ToolResult.fail(
            "I can ingest text-like files, PDFs and Word documents "
            f"({', '.join(_DOC_EXTENSIONS[:4])}, .pdf, .docx ...)."
        )
    if os.path.getsize(allowed) > max(_DOC_MAX_BYTES, knowledge_ingest.MAX_FILE_BYTES):
        return ToolResult.fail("That file is too large to ingest (10 MB limit).")

    try:
        title, text = knowledge_ingest.extract(allowed)
    except Exception as exc:
        return ToolResult.fail(f"Could not read the file: {exc}")

    stored = memory.add_document(allowed, knowledge_ingest.chunk_text(text), tag="manual", title=title)
    if not stored:
        return ToolResult.fail("The file had no readable content to remember.")
    name = os.path.basename(allowed)
    return ToolResult(
        ok=True,
        summary=f"Read {name} into memory - {stored} passage(s). Ask me about it any time.",
        data={"source": allowed, "chunks": stored},
    )


# ───────────────────────── time ─────────────────────────


_CITY_TZ = {
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "osaka": "Asia/Tokyo", "seoul": "Asia/Seoul", "korea": "Asia/Seoul",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "china": "Asia/Shanghai", "hong kong": "Asia/Hong_Kong",
    "taipei": "Asia/Taipei", "singapore": "Asia/Singapore", "kuala lumpur": "Asia/Kuala_Lumpur", "malaysia": "Asia/Kuala_Lumpur",
    "bangkok": "Asia/Bangkok", "thailand": "Asia/Bangkok", "jakarta": "Asia/Jakarta", "manila": "Asia/Manila", "hanoi": "Asia/Ho_Chi_Minh",
    "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata", "bengaluru": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata", "chennai": "Asia/Kolkata", "hyderabad": "Asia/Kolkata", "pune": "Asia/Kolkata", "india": "Asia/Kolkata",
    "karachi": "Asia/Karachi", "pakistan": "Asia/Karachi", "dhaka": "Asia/Dhaka", "kathmandu": "Asia/Kathmandu", "colombo": "Asia/Colombo",
    "dubai": "Asia/Dubai", "abu dhabi": "Asia/Dubai", "uae": "Asia/Dubai", "doha": "Asia/Qatar", "riyadh": "Asia/Riyadh", "tehran": "Asia/Tehran",
    "istanbul": "Europe/Istanbul", "turkey": "Europe/Istanbul", "moscow": "Europe/Moscow", "russia": "Europe/Moscow",
    "london": "Europe/London", "uk": "Europe/London", "england": "Europe/London", "dublin": "Europe/Dublin", "lisbon": "Europe/Lisbon",
    "paris": "Europe/Paris", "france": "Europe/Paris", "berlin": "Europe/Berlin", "germany": "Europe/Berlin", "munich": "Europe/Berlin",
    "amsterdam": "Europe/Amsterdam", "brussels": "Europe/Brussels", "madrid": "Europe/Madrid", "spain": "Europe/Madrid", "barcelona": "Europe/Madrid",
    "rome": "Europe/Rome", "italy": "Europe/Rome", "milan": "Europe/Rome", "zurich": "Europe/Zurich", "vienna": "Europe/Vienna",
    "stockholm": "Europe/Stockholm", "oslo": "Europe/Oslo", "copenhagen": "Europe/Copenhagen", "helsinki": "Europe/Helsinki",
    "warsaw": "Europe/Warsaw", "prague": "Europe/Prague", "athens": "Europe/Athens", "kyiv": "Europe/Kyiv", "kiev": "Europe/Kyiv",
    "cairo": "Africa/Cairo", "egypt": "Africa/Cairo", "lagos": "Africa/Lagos", "nairobi": "Africa/Nairobi", "johannesburg": "Africa/Johannesburg",
    "cape town": "Africa/Johannesburg", "south africa": "Africa/Johannesburg",
    "new york": "America/New_York", "nyc": "America/New_York", "boston": "America/New_York", "washington": "America/New_York",
    "miami": "America/New_York", "toronto": "America/Toronto", "montreal": "America/Toronto", "atlanta": "America/New_York",
    "chicago": "America/Chicago", "dallas": "America/Chicago", "houston": "America/Chicago", "austin": "America/Chicago",
    "denver": "America/Denver", "phoenix": "America/Phoenix", "las vegas": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles", "san francisco": "America/Los_Angeles", "sf": "America/Los_Angeles",
    "seattle": "America/Los_Angeles", "vancouver": "America/Vancouver", "california": "America/Los_Angeles",
    "mexico city": "America/Mexico_City", "mexico": "America/Mexico_City", "sao paulo": "America/Sao_Paulo", "brazil": "America/Sao_Paulo",
    "buenos aires": "America/Argentina/Buenos_Aires", "argentina": "America/Argentina/Buenos_Aires", "santiago": "America/Santiago",
    "bogota": "America/Bogota", "lima": "America/Lima",
    "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne", "brisbane": "Australia/Brisbane", "perth": "Australia/Perth",
    "canberra": "Australia/Sydney", "australia": "Australia/Sydney", "auckland": "Pacific/Auckland", "new zealand": "Pacific/Auckland",
    "wellington": "Pacific/Auckland", "honolulu": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu",
    "utc": "UTC", "gmt": "UTC",
}


def _zone_for(place: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    key = (place or "").strip().lower().strip(" .!?,")
    key = re.sub(r"^(?:the\s+)", "", key)
    name = _CITY_TZ.get(key)
    if not name:
        # "Asia/Tokyo" or "tokyo japan" style: try the text itself, then its words.
        candidates = [place.strip()] + [w for w in key.replace(",", " ").split() if len(w) > 2]
        for cand in candidates:
            try:
                return ZoneInfo(cand), cand
            except (ZoneInfoNotFoundError, ValueError, KeyError):
                pass
            if cand in _CITY_TZ:
                name = _CITY_TZ[cand]
                break
    if not name:
        return None, None
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, KeyError):
        return None, None


@tool(
    name="get_current_time",
    description="Get the current date and time on the user's PC, or in another city / time zone.",
    params={"place": {"type": "string", "description": "A city, country or IANA zone (e.g. Tokyo, Asia/Tokyo). Omit for local time.", "required": False}},
    examples=["what time is it", "what's today's date", "what day is it", "current time", "what time is it in tokyo", "time in new york"],
    category="general",
)
def get_current_time(place: Optional[str] = None) -> ToolResult:
    from datetime import datetime

    if place and place.strip():
        zone, name = _zone_for(place)
        if zone is None:
            return ToolResult.fail(f"I don't know the time zone for '{place.strip()}'. Try a major city or a zone like Asia/Tokyo.")
        there = datetime.now(zone)
        here = datetime.now().astimezone()
        offset_h = (there.utcoffset() - here.utcoffset()).total_seconds() / 3600
        rel = "the same as here" if abs(offset_h) < 0.01 else f"{abs(offset_h):g} hours {'ahead of' if offset_h > 0 else 'behind'} you"
        return ToolResult(
            ok=True,
            summary=there.strftime(f"It's %I:%M %p on %A in {place.strip().title()} ({rel}).").replace(" 0", " "),
            data={"iso": there.isoformat(timespec="seconds"), "zone": name, "offsetHours": round(offset_h, 2)},
        )
    now = datetime.now()
    return ToolResult(
        ok=True,
        summary=now.strftime("It's %I:%M %p on %A, %B %d.").replace(" 0", " "),
        data={"iso": now.isoformat(timespec="seconds")},
    )


# ───────────────────────── read aloud (voice v10) ─────────────────────────


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
            "description": "What to read: 'agenda', 'notes', 'clipboard', 'reminders', or a full file path",
        }
    },
    examples=[
        "read my agenda out loud", "read me my tasks", "read out my notes",
        "read the clipboard to me", "read this file aloud",
        "read me my schedule for today", "narrate my day", "what's on today, read it to me",
    ],
    category="general",
)
def read_aloud(source: str) -> ToolResult:
    """
    Produce long-form narration text.

    The `_speech_emotion` hint tells the speech layer to use the slower
    narrator register with longer pauses, rather than the clipped tone used
    for confirmations.
    """
    target = (source or "").strip().lower()
    body = ""

    if any(word in target for word in ("agenda", "task", "plan", "schedule", "day", "today")):
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
                f"I couldn't read '{source}'. Try 'agenda', 'notes', 'clipboard', or give me a full file path."
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
    """Conversational narration, not the terse summary the agenda tool returns."""
    import agenda

    tasks = agenda.compose().get("tasks", [])
    pending = [t for t in tasks if not t.get("completed")]
    done = len(tasks) - len(pending)

    if not tasks:
        return "Your day is completely clear. Nothing on the plan."
    if not pending:
        return f"Everything is done. All {len(tasks)} items are complete. Enjoy the rest of your day."

    opener = f"You have {len(pending)} thing{'s' if len(pending) != 1 else ''} left today"
    opener += f", and {done} already done." if done else "."

    ordinals = ["First", "Then", "After that", "Next", "Then", "Later"]
    lines = []
    for index, task in enumerate(pending):
        lead = ordinals[index] if index < len(ordinals) else "Then"
        when = task.get("time") or ""
        title = task.get("title", "")
        late = " That one is overdue." if task.get("overdue") else ""
        lines.append(f"{lead}, {'at ' + when + ', ' if when and when.lower() != 'anytime' else ''}{title}.{late}")

    closing = "" if len(pending) < 4 else " That is a full day."
    return f"{opener} {' '.join(lines)}{closing}"


def _narrate_notes() -> str:
    from tools_core import notes

    entries = notes.list_notes(limit=10)
    if not entries:
        return "You have no notes saved."
    lines = []
    for i, n in enumerate(entries):
        text = (n.get("content") or n.get("preview") or n.get("title") or "").strip()
        if text:
            lines.append(f"Note {i + 1}. {text}.")
    return f"You have {len(entries)} note{'s' if len(entries) != 1 else ''}. " + " ".join(lines)


# ───────────────────────── changelog / self-awareness ─────────────────────────


@tool(
    name="what_changed",
    description=(
        "Tell the user what changes, updates, or new features were added to Dexter "
        "since the last time it ran or in recent updates."
    ),
    params={},
    examples=[
        "what changes were done in you from last time",
        "what changes were done in you",
        "what changed in dexter",
        "what's new in dexter",
        "what's new with you",
        "tell me what updates were made to you recently",
        "what changed since last time",
        "what can you do now that is new",
        "what features were added to you",
    ],
    category="general",
)
def what_changed() -> ToolResult:
    """Report recent changes and features added to Dexter."""
    import changelog_tracker

    changes = changelog_tracker.get_changes_since_last_run()
    summary = changes.get("summary", "Dexter is up to date.")
    return ToolResult(
        ok=True,
        summary=summary,
        data=changes,
    )

