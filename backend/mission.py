"""
The mission (roadmap v7, coworker).

What Dexter and the owner are working towards, kept in data/mission.json:
a north star and why it matters, this week's focus, today's win (the one
thing that makes today count), blockers, and a log of wins. The workbook
still holds goals and commitments; the mission is the layer above them that
gives every conversation its "why", and the record of which check-ins went
out today so nobody is nagged twice.
"""

import os
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from jsonstore import load_json, save_json

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MISSION_FILE = os.path.join(DATA_DIR, "mission.json")
MAX_WINS = 60

_DEFAULT: Dict[str, Any] = {
    "north_star": "",
    "why": "",
    "focus": "",
    "focus_set_on": "",
    "today_win": "",
    "today_win_date": "",
    "blockers": [],
    "wins": [],
    "snoozed_until": 0,
    "sent": {},
    "last_checkin": {},
    # "dexter": Dexter takes initiative (check-ins, nudges, proposals); "me": it waits to be asked.
    "initiative": "dexter",
}


def _today() -> str:
    return date.today().isoformat()


def load() -> Dict[str, Any]:
    data = load_json(MISSION_FILE, {})
    merged = dict(_DEFAULT)
    if isinstance(data, dict):
        merged.update(data)
    return merged


def save(data: Dict[str, Any]) -> bool:
    return save_json(MISSION_FILE, data)


def update(**fields: Any) -> Dict[str, Any]:
    data = load()
    for key, value in fields.items():
        if key in _DEFAULT:
            data[key] = value
    save(data)
    return data


def set_mission(north_star: str, why: str = "") -> Dict[str, Any]:
    return update(north_star=(north_star or "").strip(), why=(why or "").strip() or load().get("why", ""))


def set_focus(text: str) -> Dict[str, Any]:
    return update(focus=(text or "").strip(), focus_set_on=_today())


def set_today_win(text: str) -> Dict[str, Any]:
    return update(today_win=(text or "").strip(), today_win_date=_today())


def today_win() -> str:
    data = load()
    return data["today_win"] if data.get("today_win_date") == _today() else ""


def add_win(text: str) -> Dict[str, Any]:
    data = load()
    text = (text or "").strip()
    if text:
        data["wins"] = ([{"date": _today(), "text": text}] + list(data.get("wins") or []))[:MAX_WINS]
        save(data)
    return data


def recent_wins(limit: int = 5, days: int = 14) -> List[Dict[str, Any]]:
    cutoff = date.fromordinal(date.today().toordinal() - days).isoformat()
    return [w for w in load().get("wins") or [] if w.get("date", "") >= cutoff][:limit]


def add_blocker(text: str) -> Dict[str, Any]:
    data = load()
    text = (text or "").strip()
    if text and text.lower() not in [b.lower() for b in data.get("blockers") or []]:
        data["blockers"] = list(data.get("blockers") or []) + [text]
        save(data)
    return data


def clear_blocker(needle: str) -> Optional[str]:
    data = load()
    needle = (needle or "").strip().lower()
    for blocker in list(data.get("blockers") or []):
        if needle and (needle in blocker.lower() or blocker.lower() in needle):
            data["blockers"].remove(blocker)
            save(data)
            return blocker
    return None


# ───────────────────────── who leads ─────────────────────────


def initiative_mode() -> str:
    return "me" if str(load().get("initiative") or "dexter").lower() == "me" else "dexter"


def set_initiative(mode: str) -> str:
    mode = "me" if str(mode or "").lower() in ("me", "user", "off", "mine", "i") else "dexter"
    update(initiative=mode)
    return mode


# ───────────────────────── check-in bookkeeping ─────────────────────────


def snooze(hours: float = 2.0) -> float:
    until = time.time() + hours * 3600
    update(snoozed_until=until)
    return until


def snoozed() -> bool:
    return time.time() < float(load().get("snoozed_until") or 0)


def mark_sent(kind: str, text: str = "") -> None:
    data = load()
    sent = data.get("sent") or {}
    today = _today()
    sent = {today: list(sent.get(today) or [])}  # only today's list is worth keeping
    if kind not in sent[today]:
        sent[today].append(kind)
    data["sent"] = sent
    data["last_checkin"] = {"kind": kind, "ts": time.time(), "text": text[:300]}
    save(data)


def was_sent(kind: str) -> bool:
    return kind in ((load().get("sent") or {}).get(_today()) or [])


def last_checkin(max_age_hours: float = 6.0) -> Optional[Dict[str, Any]]:
    last = load().get("last_checkin") or {}
    if not last or time.time() - float(last.get("ts") or 0) > max_age_hours * 3600:
        return None
    return last


# ───────────────────────── what the model sees ─────────────────────────


def open_loops() -> List[str]:
    """Things hanging over us, cheap to compute: overdue and slipping commitments, jobs, approvals."""
    loops: List[str] = []
    try:
        from workbook import workbook

        snapshot = workbook.today()
        overdue = snapshot.get("overdue") or []
        if overdue:
            loops.append(f"{len(overdue)} overdue commitment(s), oldest: '{overdue[-1]['text']}' (due {overdue[-1].get('due_date')})")
        slipping = [c for c in workbook.slipping() if int(c.get("roll_count") or 0) >= 2]
        if slipping:
            loops.append(f"'{slipping[0]['text']}' has been moved {slipping[0].get('roll_count')} times")
    except Exception:
        pass
    try:
        from code_agent import code_agent

        running = code_agent.running()
        if running:
            loops.append(f"buzzcode is working on '{running[0].task[:60]}' in {running[0].project['name']}")
    except Exception:
        pass
    try:
        from buzzcaf_client import buzzcaf_client

        if buzzcaf_client.health_check():
            state = buzzcaf_client.state()
            pending = (state or {}).get("pending_approvals") or [] if isinstance(state, dict) else []
            if pending:
                loops.append(f"the Studio is waiting for approval on '{pending[0].get('step')}' ({pending[0].get('name')})")
    except Exception:
        pass
    return loops


def render_block() -> str:
    data = load()
    lines = ["## Our mission"]
    if data.get("north_star"):
        lines.append(f"North star: {data['north_star']}" + (f" - because {data['why']}" if data.get("why") else ""))
    else:
        lines.append("North star: not set yet. If it comes up naturally, ask what we are working towards (once, not every turn).")
    if data.get("focus"):
        lines.append(f"This week's focus: {data['focus']}")
    win = today_win()
    if win:
        lines.append(f"Today's win (the one thing that makes today count): {win}")
    else:
        lines.append("Today's win: not chosen yet.")
    blockers = data.get("blockers") or []
    if blockers:
        lines.append("Blockers: " + "; ".join(blockers[:5]))
    wins = recent_wins()
    if wins:
        lines.append("Recent wins: " + "; ".join(f"{w['text']} ({w['date']})" for w in wins))
    return "\n".join(lines)


def today_block(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    lines = [f"## Today: {now.strftime('%A %d %B %Y, %H:%M')}"]
    try:
        from workbook import workbook

        snapshot = workbook.today()
        due = snapshot.get("due") or []
        done = snapshot.get("done") or []
        overdue = snapshot.get("overdue") or []
        if due:
            lines.append("Commitments today: " + "; ".join(
                f"{c['text']}" + (f" at {c['due_time']}" if c.get("due_time") else "") for c in due[:6]))
        if done:
            lines.append("Done today: " + "; ".join(c["text"] for c in done[:6]))
        if overdue:
            lines.append("Overdue: " + "; ".join(f"{c['text']} (since {c.get('due_date')})" for c in overdue[:4]))
        if not (due or done or overdue):
            lines.append("Nothing committed today.")
        goals = snapshot.get("goals") or []
        if goals:
            lines.append("Active goals: " + "; ".join(g.get("title", "") for g in goals[:4]))
    except Exception:
        lines.append("(workbook unavailable)")
    loops = open_loops()
    if loops:
        lines.append("Open loops: " + "; ".join(loops))
    return "\n".join(lines)


def _now_line() -> str:
    try:
        import activity

        return activity.now_line() if activity.ENABLED else ""
    except Exception:
        return ""


def status() -> Dict[str, Any]:
    data = load()
    return {
        "northStar": data.get("north_star", ""),
        "why": data.get("why", ""),
        "focus": data.get("focus", ""),
        "focusSetOn": data.get("focus_set_on", ""),
        "todayWin": today_win(),
        "blockers": data.get("blockers") or [],
        "wins": (data.get("wins") or [])[:10],
        "snoozedUntil": data.get("snoozed_until") or 0,
        "initiative": initiative_mode(),
        "now": _now_line(),
        "openLoops": open_loops(),
        "sentToday": (data.get("sent") or {}).get(_today()) or [],
    }
