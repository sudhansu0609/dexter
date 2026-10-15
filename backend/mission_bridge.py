"""
Mission Reminder, driven and read (v13).

"I want it to schedule anything I want and use the Mission Reminder app
automatically to save and review my missions."

Reading is easy and already sanctioned: Mission Reminder writes a runtime mirror
(`%LOCALAPPDATA%\\MissionReminder\\runtime.json`, GUARDIAN_PLAN §6/MR1) with the
mission, today's blocks, the running session and the next nudge. `review()` is
that file, said out loud.

Writing is the interesting half, because Mission Reminder deliberately has no
inbound API: its state lives in the app's own storage and the bridge to the rest
of the machine is one-way. Rather than reach into another app's database - which
would corrupt it the moment both sides wrote at once - Dexter uses the app the
way a person does: its own keyboard shortcuts (Ctrl+1..7 for the tabs, Ctrl+Enter
to start the next block) and its own named controls ("New block", the "Name"
field, "Save block") through Windows accessibility.

That makes two promises worth being explicit about:

* **Nothing is silently lost.** Every write is recorded in
  `data/mission_outbox.json` first. If the UI has moved and a control cannot be
  found, the request is still there, with what was attempted, and the reply says
  so rather than claiming a save.
* **The labels are data, not code.** `data/mission_ui.json` overrides any of
  them, so a renamed button is a one-line fix and not a code change.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
OUTBOX_FILE = os.path.join(DATA_DIR, "mission_outbox.json")
UI_FILE = os.path.join(DATA_DIR, "mission_ui.json")

WINDOW_TITLE = "Mission Reminder"
APP_ID = "missionreminder"

#: The app's own labels, as of its current build. Overridable in data/mission_ui.json.
UI: Dict[str, Any] = {
    "window": WINDOW_TITLE,
    "tabs": {"today": 1, "mission": 2, "goals": 3, "blocks": 4,
             "forest": 5, "review": 6, "settings": 7},
    "newBlock": "New block",
    "saveBlock": "Save block",
    "blockName": "Name",
    "blockStart": "Starts at",
    "blockMinutes": "Minutes",
    "newGoal": "New goal",
    "saveGoal": "Save goal",
    "goalTitle": "Goal",
    "goalWhy": "Why this serves the mission",
    "missionField": "The one thing your days are supposed to add up to",
    "saveMission": "Save mission",
    "startNext": "ctrl+enter",
    "togglePause": "ctrl+p",
}

LAUNCH_WAIT_SECONDS = float(os.getenv("DEXTER_MISSION_WAIT", "25") or 25)


class MissionError(RuntimeError):
    """Something Dexter should say, not a traceback."""


def ui() -> Dict[str, Any]:
    """The labels, with the owner's overrides on top."""
    merged = dict(UI)
    try:
        with open(UI_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            for key, value in data.items():
                if key == "tabs" and isinstance(value, dict):
                    merged["tabs"] = {**merged["tabs"], **value}
                else:
                    merged[key] = value
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"[Mission] Ignoring {os.path.basename(UI_FILE)}: {exc}")
    return merged


# ───────────────────────── reading (the mirror) ─────────────────────────


def runtime() -> Optional[Dict[str, Any]]:
    """The app's runtime mirror, or None when it is not running."""
    try:
        import companion

        return companion.mission_reminder_runtime()
    except Exception:
        return None


def _clock(iso: str) -> str:
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except Exception:
        return str(iso or "")[:16]


def review() -> Dict[str, Any]:
    """Everything Mission Reminder is willing to say about today."""
    data = runtime()
    if not data:
        return {"running": False}
    mission = data.get("mission") or {}
    blocks = [b for b in (data.get("today_blocks") or []) if isinstance(b, dict)]
    session = data.get("session") if isinstance(data.get("session"), dict) else None
    nudge = data.get("next_nudge") if isinstance(data.get("next_nudge"), dict) else None
    return {
        "running": True,
        "statement": str(mission.get("statement") or ""),
        "whys": [str(w) for w in (mission.get("whys") or [])],
        "blocks": [
            {
                "name": str(b.get("name") or ""),
                "start": _clock(b.get("start") or ""),
                "end": _clock(b.get("end") or ""),
                "status": str(b.get("status") or ""),
            }
            for b in blocks
        ],
        "session": (
            {
                "name": str(session.get("name") or ""),
                "since": _clock(session.get("started_at") or ""),
                "paused": bool(session.get("paused")),
            }
            if session and session.get("started_at")
            else None
        ),
        "nudge": ({"at": _clock(nudge.get("at") or ""), "text": str(nudge.get("text") or ""),
                   "kind": str(nudge.get("kind") or "")} if nudge else None),
        "updated": _clock(data.get("updated_at") or ""),
    }


def describe(state: Dict[str, Any]) -> str:
    """The review as one spoken paragraph."""
    if not state.get("running"):
        return (
            "Mission Reminder isn't running, so I can't see your blocks. "
            "Say \"open mission reminder\" and I'll start it."
        )
    lines: List[str] = []
    if state.get("statement"):
        lines.append(f"Mission: {state['statement']}")
    blocks = state.get("blocks") or []
    if blocks:
        done = sum(1 for b in blocks if b["status"] == "done")
        missed = sum(1 for b in blocks if b["status"] == "missed")
        upcoming = [b for b in blocks if b["status"] == "upcoming"]
        lines.append(
            f"{len(blocks)} block(s) today: {done} kept, {missed} missed, "
            f"{len(upcoming)} still to come"
            + (f" (next: {upcoming[0]['name']} at {upcoming[0]['start']})" if upcoming else "")
        )
    else:
        lines.append("No blocks scheduled today")
    session = state.get("session")
    if session:
        state_word = "paused" if session["paused"] else "running"
        lines.append(f"'{session['name']}' is {state_word} since {session['since']}")
    nudge = state.get("nudge")
    if nudge and nudge.get("text"):
        lines.append(f"Next nudge {nudge['at']}: {nudge['text']}")
    return ". ".join(lines) + "."


# ───────────────────────── the outbox ─────────────────────────


def _outbox() -> List[Dict[str, Any]]:
    try:
        with open(OUTBOX_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _remember(kind: str, payload: Dict[str, Any]) -> str:
    """Record the intent *before* trying to type it into the app."""
    entry = {
        "id": f"{int(time.time() * 1000):x}",
        "kind": kind,
        "payload": payload,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "state": "pending",
        "note": "",
    }
    items = _outbox()
    items.append(entry)
    _save_outbox(items)
    return entry["id"]


def _settle(entry_id: str, state: str, note: str = "") -> None:
    items = _outbox()
    for item in items:
        if item.get("id") == entry_id:
            item["state"] = state
            item["note"] = note
    _save_outbox(items)


def _save_outbox(items: List[Dict[str, Any]]) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        # Keep the last 200; this is a record of intent, not an archive.
        with open(OUTBOX_FILE, "w", encoding="utf-8") as handle:
            json.dump(items[-200:], handle, indent=2)
    except OSError as exc:
        print(f"[Mission] Could not write the outbox: {exc}")


def pending() -> List[Dict[str, Any]]:
    return [item for item in _outbox() if item.get("state") == "pending"]


def history(limit: int = 10) -> List[Dict[str, Any]]:
    return _outbox()[-max(1, limit):]


# ───────────────────────── driving the app ─────────────────────────


def _window():
    import desktop_control as dc

    found = [w for w in dc.find_windows(ui()["window"]) if not dc.is_self(w)]
    return found[0] if found else None


def ensure_running(launch: bool = True) -> Any:
    """The app's window, starting it if need be. Raises with something sayable."""
    win = _window()
    if win is not None:
        return win
    if not launch:
        raise MissionError("Mission Reminder isn't open.")
    try:
        import apps_registry

        record = apps_registry.get(APP_ID)
        if record is None:
            raise MissionError("Mission Reminder isn't in my apps registry.")
        result = apps_registry.launch(record)
        if not result.get("ok"):
            raise MissionError(f"I couldn't start Mission Reminder: {result.get('message')}")
    except MissionError:
        raise
    except Exception as exc:
        raise MissionError(f"I couldn't start Mission Reminder ({exc}).") from exc
    deadline = time.time() + LAUNCH_WAIT_SECONDS
    while time.time() < deadline:
        win = _window()
        if win is not None:
            time.sleep(1.0)  # let the renderer paint before typing into it
            return win
        time.sleep(0.5)
    raise MissionError(
        "I started Mission Reminder but its window hasn't appeared yet - it builds "
        "itself on launch. Try again in a moment."
    )


def _focus():
    import desktop_control as dc

    win = ensure_running()
    try:
        return dc._focused_target(win.title)
    except dc.DesktopError as exc:
        raise MissionError(str(exc)) from exc


TAB_ALIASES: Dict[str, str] = {
    "goal": "goals",
    "goals": "goals",
    "block": "blocks",
    "blocks": "blocks",
    "today": "today",
    "home": "today",
    "mission": "mission",
    "missions": "mission",
    "forest": "forest",
    "tree": "forest",
    "trees": "forest",
    "review": "review",
    "reviews": "review",
    "setting": "settings",
    "settings": "settings",
    "config": "settings",
}


def normalize_tab(tab: str) -> str:
    raw = str(tab or "").strip().lower()
    raw = re.sub(r"\s+tab$", "", raw).strip()
    return TAB_ALIASES.get(raw, raw)


def go_to(tab: str) -> Dict[str, Any]:
    """Switch tabs with the app's own Ctrl+1..7."""
    import desktop_control as dc

    canonical = normalize_tab(tab)
    number = ui()["tabs"].get(canonical)
    if not number:
        valid = ", ".join(ui()["tabs"].keys())
        raise MissionError(f"Mission Reminder has no '{tab}' tab. Known tabs are: {valid}.")
    win = _focus()
    # Its shortcuts are deliberately dead while the caret is in a text box, so
    # leave any open editor first. Escape there means "close what is ended".
    dc._send_keys("esc")
    time.sleep(0.1)
    dc._send_keys(f"ctrl+{number}")
    time.sleep(0.4)
    return {"tab": canonical, "shortcut": f"ctrl+{number}", "window": win.describe()}


def _click(label: str) -> Dict[str, Any]:
    import desktop_control as dc

    try:
        return dc.click_text(label, window=ui()["window"])
    except dc.DesktopError as exc:
        raise MissionError(
            f"I couldn't find '{label}' in Mission Reminder ({exc}). If the app has "
            f"been redesigned, put the new label in data/mission_ui.json."
        ) from exc


def _set_field(label: str, value: str) -> None:
    """Put `value` in the field called `label` - select-all first, so a field that
    already has something in it is replaced, not appended to."""
    import desktop_control as dc

    if value is None:
        return
    rows = [r for r in dc.find_clickables(label, window=ui()["window"], types=("Edit",))
            if r.get("element") is not None]
    if not rows:
        raise MissionError(
            f"I couldn't find the '{label}' box in Mission Reminder. Nothing was saved."
        )
    element = rows[0]["element"]
    for attempt in ("set_focus", "click_input"):
        action = getattr(element, attempt, None)
        if callable(action):
            try:
                action()
                break
            except Exception:
                continue
    time.sleep(0.1)
    dc._send_keys("ctrl+a")
    dc._write_text(str(value))
    time.sleep(0.1)


def add_block(
    name: str,
    start: Optional[str] = None,
    minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """Add a time block: the promise that you will show up at a time."""
    title = (name or "").strip()
    if not title:
        raise MissionError("What should the block be called?")
    entry = _remember("block", {"name": title, "start": start, "minutes": minutes})
    labels = ui()
    try:
        go_to("blocks")
        _click(labels["newBlock"])
        time.sleep(0.3)
        _set_field(labels["blockName"], title)
        if start:
            _set_field(labels["blockStart"], _time_value(start))
        if minutes:
            _set_field(labels["blockMinutes"], str(int(minutes)))
        _click(labels["saveBlock"])
    except MissionError as exc:
        _settle(entry, "failed", str(exc))
        raise
    _settle(entry, "saved")
    return {"saved": True, "name": title, "start": start, "minutes": minutes, "outbox": entry}


def _time_value(spoken: str) -> str:
    """'9', '9am', '9:30 pm', '21:30' -> 'HH:MM' for the app's time input."""
    import re

    text = (spoken or "").strip().lower().replace(".", "")
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", text)
    if not match:
        return text
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def add_goal(title: str, why: Optional[str] = None) -> Dict[str, Any]:
    """Add a goal: an outcome the blocks are for."""
    text = (title or "").strip()
    if not text:
        raise MissionError("What is the goal?")
    entry = _remember("goal", {"title": text, "why": why})
    labels = ui()
    try:
        go_to("goals")
        _click(labels["newGoal"])
        time.sleep(0.3)
        _set_field(labels["goalTitle"], text)
        if why:
            _set_field(labels["goalWhy"], why)
        _click(labels["saveGoal"])
    except MissionError as exc:
        _settle(entry, "failed", str(exc))
        raise
    _settle(entry, "saved")
    return {"saved": True, "title": text, "why": why or "", "outbox": entry}


def save_mission(statement: str) -> Dict[str, Any]:
    """Write the mission statement itself - the thing the days are meant to add to."""
    text = (statement or "").strip()
    if not text:
        raise MissionError("What is the mission?")
    entry = _remember("mission", {"statement": text})
    labels = ui()
    try:
        go_to("mission")
        _set_field(labels["missionField"], text)
        _click(labels["saveMission"])
    except MissionError as exc:
        _settle(entry, "failed", str(exc))
        raise
    _settle(entry, "saved")
    return {"saved": True, "statement": text, "outbox": entry}


def start_next_block() -> Dict[str, Any]:
    """Start today's next block, through the app's own Ctrl+Enter."""
    import desktop_control as dc

    go_to("today")
    dc._send_keys(ui()["startNext"])
    time.sleep(0.6)
    state = review()
    session = state.get("session")
    return {"started": bool(session), "session": session, "blocks": state.get("blocks", [])}


def toggle_pause() -> Dict[str, Any]:
    import desktop_control as dc

    go_to("today")
    dc._send_keys(ui()["togglePause"])
    time.sleep(0.5)
    state = review()
    return {"session": state.get("session")}


def availability() -> Dict[str, Any]:
    return {
        "running": bool(_window()),
        "mirror": bool(runtime()),
        "pending": len(pending()),
        "uiFile": UI_FILE if os.path.exists(UI_FILE) else "",
    }
