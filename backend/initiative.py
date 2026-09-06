"""
Initiative (roadmap v7, K6): Dexter proposes the next move on its own.

Every 15 minutes (the proactive cron) it asks "what would a good coworker
say right now?" and picks at most one thing worth saying, each once a day,
at most a few a day, and only while the initiative switch says Dexter leads.
Each proposal is a chat message with quick replies that are real phrases the
router understands, so one tap acts.

Candidates, in priority order:
  - a timed commitment is due within 30 minutes
  - it is mid-morning and no win has been picked (offers the first commitment)
  - a commitment has been overdue for three days or more (move / drop / keep)
  - it is evening and today's win is still open
  - one of the owner's own videos is gathering pace (BuzzBrain) → offer the analysis
  - saved topics are waiting on a channel with nothing in production → offer to start one
"""

import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import companion
import mission

MAX_PER_DAY = 4
SOON_MINUTES = 30
STALE_DAYS = 3
MORNING_NUDGE_HOUR = 10
EVENING_NUDGE_HOUR = 18


def _sent_today() -> List[str]:
    return [k for k in mission.status().get("sentToday", []) if k.startswith("init:")]


def _due_within(commitment: Dict[str, Any], minutes: int, now: datetime) -> Optional[int]:
    due_time = commitment.get("due_time")
    if not due_time or commitment.get("status") != "open":
        return None
    try:
        at = datetime.strptime(f"{commitment.get('due_date')} {due_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    delta = (at - now).total_seconds() / 60
    return int(delta) if 0 <= delta <= minutes else None


# ───────────────────────── candidates ─────────────────────────


def upcoming_commitment(now: datetime) -> Optional[Dict[str, Any]]:
    from workbook import workbook

    for c in workbook.today().get("due") or []:
        mins = _due_within(c, SOON_MINUTES, now)
        if mins is not None:
            return {
                "key": f"soon-{c['id']}",
                "text": f"'{c['text']}' is in {mins} minutes. Ready, or should we move it?",
                "quick": [f"Keep '{c['text'][:30]}'", f"Move '{c['text'][:30]}' to tomorrow"],
            }
    return None


def pick_today_win(now: datetime) -> Optional[Dict[str, Any]]:
    if now.hour < MORNING_NUDGE_HOUR or mission.today_win():
        return None
    from workbook import workbook

    due = workbook.today().get("due") or []
    if not due:
        return None
    first = due[0]["text"]
    return {
        "key": "pick-win",
        "text": f"We haven't picked today's win. '{first}' looks like the one; shall we make it that?",
        "quick": [f"Today's win is {first[:40]}", "Something else"],
    }


def stale_overdue(now: datetime) -> Optional[Dict[str, Any]]:
    from workbook import workbook

    cutoff = (now.date() - timedelta(days=STALE_DAYS)).isoformat()
    for c in workbook.today().get("overdue") or []:
        if (c.get("due_date") or "") <= cutoff:
            days = (now.date() - date.fromisoformat(c["due_date"])).days
            return {
                "key": f"stale-{c['id']}",
                "text": f"'{c['text']}' has been open for {days} days. Do we move it, break it into something smaller, or drop it?",
                "quick": [f"Move '{c['text'][:30]}' to tomorrow", f"Drop '{c['text'][:30]}'", "Keep it, I'm on it"],
            }
    return None


def win_before_close(now: datetime) -> Optional[Dict[str, Any]]:
    win = mission.today_win()
    if now.hour < EVENING_NUDGE_HOUR or not win:
        return None
    if any(w.get("text") == win and w.get("date") == now.date().isoformat() for w in mission.load().get("wins") or []):
        return None
    return {
        "key": "win-before-close",
        "text": f"Still time for '{win}' before we close the day. Where does it stand?",
        "quick": ["Done", "On it", "Move it to tomorrow"],
    }


def own_video_momentum(now: datetime) -> Optional[Dict[str, Any]]:
    from buzzcaf_client import buzzcaf_client

    if not buzzcaf_client.health_check():
        return None
    data = buzzcaf_client.buzzbrain_latest(5, mine=True)
    videos = (data.get("videos") or data.get("items") or []) if isinstance(data, dict) else []
    for video in videos:
        latest = video.get("latest") or video
        vph = latest.get("vph") or 0
        video_id = latest.get("video_id") or video.get("video_id")
        title = latest.get("title") or video.get("title") or "your latest video"
        if video_id and vph and float(vph) >= 50:
            url = f"https://www.youtube.com/watch?v={video_id}"
            return {
                "key": f"momentum-{video_id}",
                "text": f"'{title}' is doing {int(float(vph))} views an hour right now. Want the breakdown of why, so we can repeat it?",
                "quick": [f"Analyse this video {url}", "Later"],
            }
    return None


def studio_idle(now: datetime) -> Optional[Dict[str, Any]]:
    from buzzcaf_client import buzzcaf_client

    if not buzzcaf_client.health_check():
        return None
    state = buzzcaf_client.state()
    if not isinstance(state, dict) or state.get("error"):
        return None
    projects = state.get("projects") or {}
    if int(projects.get("in_progress") or 0) > 0 or int(projects.get("paused_for_approval") or 0) > 0:
        return None
    topics = buzzcaf_client.saved_topics()
    if not topics:
        return None
    top = topics[0]
    channel = top.get("channel") or "the channel"
    return {
        "key": "studio-idle",
        "text": f"Nothing is in production and {len(topics)} saved topic(s) are waiting, starting with '{top.get('topic')}' for {channel}. Start it?",
        "quick": [f"Start a video about {str(top.get('topic'))[:50]} on {channel}", "Not that one"],
    }


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 3}


def off_mission(now: datetime) -> Optional[Dict[str, Any]]:
    """Forty-five minutes in a work app on a project that is not today's win: plan changed, or on the way?"""
    import activity

    win = mission.today_win()
    if not win or not activity.ENABLED:
        return None
    current = activity.activity_log.current()
    if not current or current["minutes"] < 45 or current["app"] not in activity.WORK_APPS:
        return None
    seen = _words(current.get("project", "")) | _words(current.get("title", ""))
    if seen & _words(win):
        return None
    where = f"{current['app']}" + (f" on {current['project']}" if current["project"] else "")
    return {
        "key": f"offmission-{current['app']}-{now.hour // 3}",
        "text": f"You've been in {where} for {current['minutes']} minutes while today's win is '{win}'. Is this on the way there, or has the plan changed?",
        "quick": ["It's on the way", f"Today's win is {current['project'] or current['app']}"[:60], "Change today's win"],
    }


def long_stretch(now: datetime) -> Optional[Dict[str, Any]]:
    import activity

    if not activity.ENABLED:
        return None
    current = activity.activity_log.current()
    if not current or current["minutes"] < 110 or current["app"] not in activity.WORK_APPS:
        return None
    return {
        "key": f"stretch-{now.hour // 2}",
        "text": f"{current['minutes']} minutes straight in {current['app']}. Five minutes away from the screen, then back?",
        "quick": ["Taking five", "Almost done, then"],
    }


CANDIDATES: List[Callable[[datetime], Optional[Dict[str, Any]]]] = [
    upcoming_commitment, pick_today_win, stale_overdue, off_mission, win_before_close, long_stretch,
    own_video_momentum, studio_idle,
]


def scan(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Say at most one thing. Returns what was said, or None."""
    if not companion.enabled() or companion._quiet() or mission.snoozed():
        return None
    if len(_sent_today()) >= MAX_PER_DAY:
        return None
    now = now or datetime.now()
    for candidate in CANDIDATES:
        try:
            proposal = candidate(now)
        except Exception as exc:
            print(f"[Initiative] {candidate.__name__} failed: {exc}")
            continue
        if not proposal:
            continue
        key = f"init:{proposal['key']}"
        if mission.was_sent(key):
            continue
        if companion.say(key, proposal["text"], quick_replies=proposal.get("quick")):
            return proposal
    return None
