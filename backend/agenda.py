"""
One agenda (roadmap v5, 5.2).

Dexter kept four overlapping views of "what am I doing today": the flat
day_planner.json, reminders.json, scheduled tasks, and workbook commitments.
They never agreed. The agenda is now a *composition* over the stores that
remain: commitments from the workbook (the spine), pending reminders, and
today's calendar events when Google is connected. Adding a task creates a
commitment; completing one completes the commitment.
"""

import os
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from scheduler import scheduler
from timeparse import format_display, parse_when
from workbook import workbook


def _label_time(due_time: Optional[str]) -> str:
    if not due_time:
        return ""
    try:
        return datetime.strptime(due_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return str(due_time)


def _commitment_item(c: Dict[str, Any], completed: bool, overdue: bool) -> Dict[str, Any]:
    return {
        "id": f"c-{c['id']}",
        "kind": "commitment",
        "time": _label_time(c.get("due_time")),
        "sortKey": c.get("due_time") or "99:99",
        "title": c.get("text", ""),
        "completed": completed,
        "overdue": overdue,
        "dueDate": c.get("due_date"),
        "rollCount": int(c.get("roll_count") or 0),
        "goalId": c.get("goal_id"),
    }


def _reminder_items(on_date: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        pending = scheduler.list_reminders(include_fired=False)
    except Exception:
        return items
    for reminder in pending:
        stamp = reminder.get("at")
        try:
            when = datetime.fromisoformat(stamp) if stamp else None
        except Exception:
            when = None
        if when is None or when.date().isoformat() != on_date:
            continue
        items.append({
            "id": f"r-{reminder.get('id')}",
            "kind": "reminder",
            "time": format_display(when),
            "sortKey": when.strftime("%H:%M"),
            "title": reminder.get("text", "Reminder"),
            "completed": False,
            "overdue": False,
        })
    return items


def _calendar_items(on_date: str) -> List[Dict[str, Any]]:
    """Today's calendar events, only when Google is already connected (never prompts)."""
    try:
        import google_tools

        if not google_tools.connected():
            return []
        result = google_tools.list_calendar_events(days=1)
        events = (result.data or {}).get("events") or [] if result.ok else []
    except Exception:
        return []
    items: List[Dict[str, Any]] = []
    for event in events:
        start = str(event.get("start") or event.get("when") or "")
        try:
            when = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
        except Exception:
            when = None
        if when is not None and when.date().isoformat() != on_date:
            continue
        items.append({
            "id": f"e-{event.get('id') or start}",
            "kind": "event",
            "time": format_display(when.replace(tzinfo=None)) if when else "",
            "sortKey": when.strftime("%H:%M") if when else "99:98",
            "title": event.get("summary") or event.get("title") or "Calendar event",
            "completed": False,
            "overdue": False,
        })
    return items


def compose(on_date: Optional[str] = None) -> Dict[str, Any]:
    on_date = on_date or date.today().isoformat()
    snapshot = workbook.today(on_date)
    items: List[Dict[str, Any]] = []
    items += [_commitment_item(c, False, True) for c in snapshot.get("overdue", [])]
    items += [_commitment_item(c, False, False) for c in snapshot.get("due", [])]
    items += _reminder_items(on_date)
    items += _calendar_items(on_date)
    items.sort(key=lambda i: (not i["overdue"], i["sortKey"]))
    done = [_commitment_item(c, True, False) for c in snapshot.get("done", [])]
    for item in items + done:
        item.pop("sortKey", None)
    return {
        "date": on_date,
        "tasks": items + done,
        "counts": {
            "open": len(items),
            "overdue": sum(1 for i in items if i["overdue"]),
            "done": len(done),
            "reminders": sum(1 for i in items if i["kind"] == "reminder"),
            "events": sum(1 for i in items if i["kind"] == "event"),
        },
        "goals": snapshot.get("goals", []),
    }


def add(title: str, when: Optional[str] = None) -> Dict[str, Any]:
    """A new commitment for today (or the parsed day), with a reminder when a time was given."""
    title = (title or "").strip()
    if not title:
        raise ValueError("A task needs a title.")
    parsed = parse_when(when) if when else None
    due_date = parsed.date().isoformat() if parsed else date.today().isoformat()
    due_time = parsed.strftime("%H:%M") if parsed and (parsed.hour or parsed.minute) else None
    commitment = workbook.add_commitment(text=title, due_date=due_date, due_time=due_time)
    reminder = None
    if parsed and parsed > datetime.now() and when:
        try:
            reminder = scheduler.create_reminder(title, when)
        except Exception:
            reminder = None
    item = _commitment_item(commitment, False, False)
    item.pop("sortKey", None)
    item["reminder"] = bool(reminder and reminder.get("scheduled"))
    item["display"] = format_display(parsed) if parsed else "today"
    return item


def find(needle: str) -> Optional[Dict[str, Any]]:
    needle = (needle or "").strip()
    if needle.startswith("c-") and needle[2:].isdigit():
        found = workbook.get_commitment(int(needle[2:]))
        return found or None
    return workbook.find_commitment(needle, status="open")


def complete(needle: str, note: str = "") -> Optional[Dict[str, Any]]:
    commitment = find(needle)
    if not commitment:
        return None
    if commitment.get("status") == "done":
        return commitment
    return workbook.complete_commitment(int(commitment["id"]), note=note)


def toggle(item_id: str) -> Dict[str, Any]:
    """The planner UI's checkbox: commitments complete; reminders and events are read-only."""
    if item_id.startswith("c-") and item_id[2:].isdigit():
        commitment = workbook.get_commitment(int(item_id[2:]))
        if not commitment:
            return {"success": False, "reason": "not found"}
        if commitment.get("status") == "done":
            return {"success": False, "reason": "already done"}
        workbook.complete_commitment(int(commitment["id"]))
        return {"success": True}
    return {"success": False, "reason": "only commitments can be ticked off"}
