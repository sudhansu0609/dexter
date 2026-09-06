"""
Natural-language time parsing for Dexter.

Deliberately dependency-optional: `dateparser` handles the long tail if it is
installed, but the regex fast paths below cover the phrasings that actually come
out of a voice command ("in 20 minutes", "tomorrow at 5", "every weekday at 9")
and run in microseconds without it. This module must keep working on a bare
stdlib install, because the scheduler and the day planner both depend on it.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

try:
    import dateparser  # type: ignore

    _HAS_DATEPARSER = True
except ImportError:
    dateparser = None  # type: ignore
    _HAS_DATEPARSER = False


_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_UNIT_SECONDS = {
    "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
}

_NAMED_TIMES = {
    "noon": (12, 0),
    "midnight": (0, 0),
    "morning": (9, 0),
    "afternoon": (14, 0),
    "evening": (18, 0),
    "tonight": (20, 0),
    "night": (21, 0),
}

# "in 20 minutes", "in an hour", "in 2 hrs"
_RE_RELATIVE = re.compile(
    r"\bin\s+(?:(?P<num>\d+(?:\.\d+)?)|(?P<article>an?|a\s+couple\s+of|a\s+few))\s*"
    r"(?P<unit>sec(?:s|ond|onds)?|min(?:s|ute|utes)?|h(?:r|rs|our|ours)|day|days|week|weeks)\b",
    re.IGNORECASE,
)

# "at 5", "at 5pm", "5:30 pm", "17:45", "9 am"
_RE_CLOCK = re.compile(
    r"\b(?P<at>at\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm|a\.m\.|p\.m\.)?\b",
    re.IGNORECASE,
)


def _apply_clock(base: datetime, hour: int, minute: int, ampm: Optional[str]) -> datetime:
    if ampm:
        ampm = ampm.replace(".", "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    elif hour < 8:
        # "remind me at 5" almost always means the evening, not 5am.
        hour += 12

    if hour > 23 or minute > 59:
        raise ValueError("hour/minute out of range")
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_when(text: str, base: Optional[datetime] = None) -> Optional[datetime]:
    """
    Resolve a spoken time expression to a concrete future datetime.

    Returns None if nothing time-like is found, so callers can distinguish
    "no time given" from "time given but in the past".
    """
    if not text:
        return None

    now = base or datetime.now()
    lowered = text.strip().lower()

    # ── relative offsets: "in 20 minutes" ──
    m = _RE_RELATIVE.search(lowered)
    if m:
        unit_key = m.group("unit").lower()
        seconds = None
        for key, mult in _UNIT_SECONDS.items():
            if unit_key.startswith(key[:3]) and unit_key in _UNIT_SECONDS:
                seconds = _UNIT_SECONDS[unit_key]
                break
        if seconds is None:
            seconds = _UNIT_SECONDS.get(unit_key, 60)

        if m.group("num"):
            qty = float(m.group("num"))
        else:
            article = (m.group("article") or "").strip()
            qty = 3.0 if "few" in article else (2.0 if "couple" in article else 1.0)
        return now + timedelta(seconds=seconds * qty)

    # ── day offset ──
    day_base = now
    has_day_hint = False
    if "day after tomorrow" in lowered:
        day_base, has_day_hint = now + timedelta(days=2), True
    elif "tomorrow" in lowered:
        day_base, has_day_hint = now + timedelta(days=1), True
    elif "today" in lowered or "tonight" in lowered:
        has_day_hint = True
    else:
        for name, idx in _WEEKDAYS.items():
            if re.search(rf"\b(?:next\s+)?{name}\b", lowered):
                delta = (idx - now.weekday()) % 7
                if delta == 0 or "next" in lowered:
                    delta = delta or 7
                day_base, has_day_hint = now + timedelta(days=delta), True
                break

    # ── named times: noon, tonight, evening ──
    for name, (hh, mm) in _NAMED_TIMES.items():
        if re.search(rf"\b{name}\b", lowered):
            candidate = day_base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= now and not has_day_hint:
                candidate += timedelta(days=1)
            return candidate

    # ── explicit clock time ──
    # Skip numbers that belong to a duration phrase we already ruled out.
    for match in _RE_CLOCK.finditer(lowered):
        hour_str = match.group("hour")
        # Ignore bare years / oversized numbers
        if len(hour_str) > 2:
            continue
        hour = int(hour_str)
        if hour > 23:
            continue
        minute = int(match.group("minute") or 0)
        ampm = match.group("ampm")
        # A bare number with no "at", no colon and no am/pm is probably not a time
        # ("remind me about 3 things"). Require some signal.
        if not ampm and not match.group("minute") and not match.group("at"):
            if not has_day_hint:
                continue
        try:
            candidate = _apply_clock(day_base, hour, minute, ampm)
        except ValueError:
            continue
        if candidate <= now and not has_day_hint:
            candidate += timedelta(days=1)
        return candidate

    # ── long tail via dateparser ──
    if _HAS_DATEPARSER:
        parsed = dateparser.parse(
            text,
            settings={
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": now,
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )
        if parsed:
            # dateparser resolves a bare day like "friday" to midnight on that
            # day. Returning it would turn "remind me to call mom friday" into an
            # alarm at 00:00, while the caller believed a real time had been
            # given. This function's contract is "a moment a reminder can fire";
            # None means "no time given" and lets the caller ask. Use parse_date
            # when a day on its own is a complete answer.
            midnight_with_no_clock = (
                parsed.hour == 0
                and parsed.minute == 0
                and not _RE_CLOCK_SIGNAL.search(lowered)
            )
            if not midnight_with_no_clock:
                return parsed

    return None


# Does this phrase name a time of day at all? Used by parse_date to decide
# whether the timed parser should have first refusal.
_RE_CLOCK_SIGNAL = re.compile(
    r"\b\d{1,2}:\d{2}"
    r"|\d\s*(?:am|pm|a\.m\.|p\.m\.)\b"
    r"|\b(?:am|pm)\b"
    r"|\bat\s+\d{1,2}"
    r"|\b(?:noon|midnight|tonight|morning|afternoon|evening|night)\b"
    r"|\bin\s+\d+\s*(?:sec|min|hour|hr)",
    re.IGNORECASE,
)


def parse_date(text: str, base: Optional[datetime] = None) -> Optional[datetime]:
    """
    Resolve a spoken expression to a *day*, with no clock time required.

    parse_when() deliberately returns None for "friday" or "tomorrow", because a
    reminder without a time cannot fire. A due date is the opposite case: "I'll
    have it done by Friday" is a complete thought, and the commitments in the
    workbook are dated, not timed. Midnight is returned as the day marker.

    Falls back to parse_when() first, so "friday at 3pm" keeps its time.
    """
    if not text:
        return None

    now = base or datetime.now()
    lowered = text.strip().lower()

    # Only defer to parse_when when the phrase actually names a time. Otherwise
    # "in 3 days" would come back stamped with the current clock, and a
    # commitment would read as due "Wednesday at 11:16 PM".
    if _RE_CLOCK_SIGNAL.search(lowered):
        timed = parse_when(text, base=now)
        if timed is not None:
            return timed

    def day(offset: int) -> datetime:
        return (now + timedelta(days=offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    if "day after tomorrow" in lowered:
        return day(2)
    if "tomorrow" in lowered:
        return day(1)
    if "today" in lowered or "tonight" in lowered:
        return day(0)
    if "next week" in lowered:
        # The start of next week, not seven days from now: "next week" is a
        # week, and Monday is where people mean it starts.
        return day(7 - now.weekday())
    if "end of the week" in lowered or "end of week" in lowered:
        return day((4 - now.weekday()) % 7)  # Friday
    if "this week" in lowered:
        return day((4 - now.weekday()) % 7)

    m = re.search(r"\bin\s+(\d+)\s+days?\b", lowered)
    if m:
        return day(int(m.group(1)))

    for name, idx in _WEEKDAYS.items():
        if re.search(rf"\b(?:next\s+|by\s+|on\s+)?{name}\b", lowered):
            delta = (idx - now.weekday()) % 7
            if delta == 0 or "next" in lowered:
                delta = delta or 7
            return day(delta)

    if _HAS_DATEPARSER:
        parsed = dateparser.parse(
            text,
            settings={
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": now,
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )
        if parsed:
            return parsed

    return None


def parse_recurrence(text: str) -> Optional[Dict[str, Any]]:
    """
    Detect a repeating schedule and return APScheduler cron kwargs.

    e.g. "every weekday at 9am" -> {"day_of_week": "mon-fri", "hour": 9, "minute": 0}
    Returns None for one-off reminders.
    """
    if not text:
        return None
    lowered = text.strip().lower()
    if not re.search(r"\b(every|each|daily|weekly|hourly)\b", lowered):
        return None

    when = parse_when(lowered)
    hour = when.hour if when else 9
    minute = when.minute if when else 0

    if re.search(r"\b(hourly|every hour)\b", lowered):
        return {"minute": minute}
    if re.search(r"\b(weekday|week days|working days|mon(day)?\s*(-|to|through)\s*fri(day)?)\b", lowered):
        return {"day_of_week": "mon-fri", "hour": hour, "minute": minute}
    if re.search(r"\b(weekend|sat(urday)?\s*(and|&)\s*sun(day)?)\b", lowered):
        return {"day_of_week": "sat,sun", "hour": hour, "minute": minute}

    for name, idx in _WEEKDAYS.items():
        if re.search(rf"\bevery\s+{name}\b", lowered):
            return {"day_of_week": idx, "hour": hour, "minute": minute}

    if re.search(r"\b(daily|every day|everyday)\b", lowered):
        return {"hour": hour, "minute": minute}
    if re.search(r"\b(weekly|every week)\b", lowered):
        return {"day_of_week": "mon", "hour": hour, "minute": minute}

    return {"hour": hour, "minute": minute}


def format_display(dt: datetime) -> str:
    """Match the existing day_planner.json display format: '09:00 AM'."""
    return dt.strftime("%I:%M %p") if dt else ""


def humanize(dt: datetime, base: Optional[datetime] = None) -> str:
    """Short spoken-friendly description: 'in 20 minutes', 'tomorrow at 5:00 PM'."""
    if not dt:
        return "an unspecified time"
    now = base or datetime.now()
    delta = dt - now
    secs = delta.total_seconds()

    if 0 < secs < 90:
        return f"in {int(secs)} seconds"
    if 0 < secs < 3600:
        mins = int(round(secs / 60))
        return f"in {mins} minute{'s' if mins != 1 else ''}"
    if 0 < secs < 6 * 3600 and dt.date() == now.date():
        hrs = round(secs / 3600, 1)
        hrs_str = str(int(hrs)) if hrs == int(hrs) else str(hrs)
        return f"in {hrs_str} hour{'s' if hrs != 1 else ''}"

    clock = dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == now.date():
        return f"today at {clock}"
    if dt.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {clock}"
    if 0 < delta.days < 7:
        return f"{dt.strftime('%A')} at {clock}"
    return f"{dt.strftime('%b %d')} at {clock}"


def strip_time_phrases(text: str) -> str:
    """
    Remove the time expression from a command so the remainder reads as the task.
    "remind me to call mom in 20 minutes" -> "call mom"
    """
    if not text:
        return ""
    cleaned = text
    patterns = [
        _RE_RELATIVE.pattern,
        r"\b(?:on\s+)?(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(?:day after tomorrow|tomorrow|today|tonight|this evening|this afternoon|this morning)\b",
        r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\b",
        r"\b\d{1,2}:\d{2}\s*(?:am|pm)?\b",
        r"\b\d{1,2}\s*(?:am|pm)\b",
        r"\b(?:every|each)\s+\w+\b",
        r"\b(?:noon|midnight|daily|weekly|hourly)\b",
    ]
    for pat in patterns:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(
        r"^\s*(?:please\s+)?(?:remind me to|remind me|set a reminder to|"
        r"set a reminder|reminder to|schedule|add a task to|add task|add)\s+",
        "",
        cleaned.strip(),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:-")
    return cleaned
