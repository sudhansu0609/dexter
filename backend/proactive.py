"""
Proactive suggestions - Dexter speaking up without being asked.

Scans three sources on a 15-minute cadence: calendar events in the next two
hours, unread email (at most hourly), and overdue planner tasks. Anything
noteworthy goes out as a banner + toast; only a genuinely imminent calendar
event (under 20 minutes) is spoken aloud.

Proactivity that nags gets disabled, so this is conservative by design:

  * Quiet hours (default 22:00-08:00, DEXTER_QUIET_HOURS="22-8") silence it.
  * Each category has a cooldown (default 45 min, DEXTER_SUGGEST_COOLDOWN_MIN).
  * A suggestion is never repeated the same day (content-hash dedup).
  * DEXTER_PROACTIVE=0 turns the whole thing off.
"""

import hashlib
import os

import env_boot  # noqa: F401  - loads .env for every entry point
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from events import bus

import notify

_COOLDOWN_SECONDS = int(os.getenv("DEXTER_SUGGEST_COOLDOWN_MIN", "45")) * 60
_EMAIL_COOLDOWN_SECONDS = 3600
IMMINENT_MINUTES = 20

_last_sent: Dict[str, float] = {}
_sent_today: set = set()
_sent_today_date: str = ""


def enabled() -> bool:
    if os.getenv("DEXTER_PROACTIVE", "1") == "0":
        return False
    try:
        import mission

        return mission.initiative_mode() == "dexter"
    except Exception:
        return True


def _quiet_hours() -> tuple:
    raw = os.getenv("DEXTER_QUIET_HOURS", "22-8")
    try:
        start, end = raw.split("-")
        return int(start) % 24, int(end) % 24
    except Exception:
        return 22, 8


def in_quiet_hours(now: Optional[datetime] = None) -> bool:
    start, end = _quiet_hours()
    hour = (now or datetime.now()).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps midnight


def _dedup_key(category: str, text: str) -> str:
    return f"{category}:{hashlib.sha1(text.encode('utf-8', 'ignore')).hexdigest()[:12]}"


def _should_send(category: str, text: str) -> bool:
    global _sent_today_date
    today = date.today().isoformat()
    if _sent_today_date != today:
        _sent_today.clear()
        _sent_today_date = today
    if _dedup_key(category, text) in _sent_today:
        return False
    cooldown = _EMAIL_COOLDOWN_SECONDS if category == "email" else _COOLDOWN_SECONDS
    return (time.time() - _last_sent.get(category, 0.0)) >= cooldown


def _send(category: str, text: str, speak: bool = False) -> None:
    _last_sent[category] = time.time()
    _sent_today.add(_dedup_key(category, text))
    notify.toast_async("Dexter", text)
    bus.publish("reminder", {
        "id": f"suggest-{category}-{int(time.time())}",
        "text": text,
        "spoken": text,
        "speak": speak,
        "banner": True,
        "missed": not speak,
        "category": category,
    })
    print(f"[Proactive] {category}: {text}")


# ───────────────────────── sources ─────────────────────────


def _calendar_suggestions() -> List[Dict[str, Any]]:
    from tool_registry import execute

    result = execute("list_calendar_events", {"days": 1})
    if not result.ok or not isinstance(result.data, dict):
        return []
    suggestions = []
    now = datetime.now()
    for event in result.data.get("events", [])[:10]:
        start_raw = (event.get("start") or {}).get("dateTime")
        if not start_raw:
            continue
        try:
            start = datetime.fromisoformat(start_raw).replace(tzinfo=None)
        except Exception:
            continue
        minutes_away = (start - now).total_seconds() / 60
        if 0 < minutes_away <= 120:
            title = event.get("summary", "an event")
            text = f"'{title}' starts in {int(minutes_away)} minutes."
            suggestions.append({
                "category": "calendar",
                "text": text,
                "speak": minutes_away <= IMMINENT_MINUTES,
            })
    return suggestions


def _email_suggestions() -> List[Dict[str, Any]]:
    from tool_registry import execute

    result = execute("check_email", {"query": "is:unread"})
    if not result.ok or not isinstance(result.data, dict):
        return []
    messages = result.data.get("messages") or result.data.get("emails") or []
    if len(messages) >= 3:
        return [{
            "category": "email",
            "text": f"You have {len(messages)} unread emails waiting.",
            "speak": False,
        }]
    return []


def _planner_suggestions() -> List[Dict[str, Any]]:
    from workbook import workbook

    overdue = []
    now = datetime.now()
    try:
        snapshot = workbook.today()
    except Exception:
        return []
    overdue += [c.get("text", "a commitment") for c in snapshot.get("overdue", [])]
    for commitment in snapshot.get("due", []):
        due_time = commitment.get("due_time")
        if not due_time:
            continue
        try:
            at = datetime.strptime(f"{commitment.get('due_date')} {due_time}", "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if (now - at).total_seconds() > 30 * 60:
            overdue.append(commitment.get("text", "a commitment"))
    if overdue:
        preview = "', '".join(overdue[:2])
        return [{
            "category": "planner",
            "text": f"Still open from earlier today: '{preview}'"
                    + (f" and {len(overdue) - 2} more." if len(overdue) > 2 else "."),
            "speak": False,
        }]
    return []


# ───────────────────────── scan ─────────────────────────


def run_scan() -> int:
    """One pass over all sources. Returns how many suggestions went out."""
    if not enabled() or in_quiet_hours():
        return 0
    sent = 0
    for source in (_calendar_suggestions, _planner_suggestions, _email_suggestions):
        try:
            for suggestion in source():
                if _should_send(suggestion["category"], suggestion["text"]):
                    _send(suggestion["category"], suggestion["text"], suggestion["speak"])
                    sent += 1
        except Exception as exc:
            print(f"[Proactive] {source.__name__} failed: {exc}")
    return sent
