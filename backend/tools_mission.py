"""
The mission as Dexter tools (roadmap v7): what we are working towards, this
week's focus, today's win, wins and blockers, and control over check-ins.
"""

from typing import Optional

import companion
import mission
from tool_registry import ToolResult, tool


@tool(
    name="set_mission",
    description="Set what the user and Dexter are working towards (the north star) and why it matters.",
    params={
        "north_star": {"type": "string", "description": "The mission in one sentence"},
        "why": {"type": "string", "description": "Why it matters", "required": False},
    },
    examples=[
        "our mission is to get beyond3baje to 100k subscribers this year",
        "north star: make a living from the channels by december",
        "we're working towards launching the after dark series",
        "the big goal is finishing the documentary season",
    ],
    category="mission",
)
def set_mission(north_star: str, why: Optional[str] = None) -> ToolResult:
    if not (north_star or "").strip():
        return ToolResult.fail("What's the mission?")
    data = mission.set_mission(north_star, why or "")
    return ToolResult(ok=True, summary=f"Locked in. We're going after: {data['north_star']}." + (f" Because {data['why']}." if data.get("why") else " What's this week's focus?"), data=mission.status())


@tool(
    name="set_focus",
    description="Set this week's focus: the thing most of the week should go to.",
    params={"text": {"type": "string", "description": "This week's focus"}},
    examples=["this week's focus is finishing the bhangarh script", "focus this week: edit episode two", "the focus for the week is thumbnails"],
    category="mission",
)
def set_focus(text: str) -> ToolResult:
    if not (text or "").strip():
        return ToolResult.fail("What's the focus?")
    data = mission.set_focus(text)
    return ToolResult(ok=True, summary=f"This week: {data['focus']}. What's today's win?", data=mission.status())


@tool(
    name="set_today_win",
    description="Set today's win: the one thing that makes today count.",
    params={"text": {"type": "string", "description": "Today's win"}},
    examples=[
        "today's win is recording the voiceover",
        "the one thing today is the edit",
        "if I do one thing today it's the script outline",
        "my win for today: publish the short",
    ],
    category="mission",
)
def set_today_win(text: str) -> ToolResult:
    if not (text or "").strip():
        return ToolResult.fail("What would make today a win?")
    mission.set_today_win(text)
    return ToolResult(ok=True, summary=f"Today's win: '{mission.today_win()}'. Let's get it.", data=mission.status())


@tool(
    name="log_win",
    description="Record a win (something that got done and mattered).",
    params={"text": {"type": "string", "description": "What got done"}},
    examples=["log a win: the episode is live", "small win: fixed the audio sync", "we shipped the thumbnail", "win: 10k views on the short"],
    category="mission",
)
def log_win(text: str) -> ToolResult:
    if not (text or "").strip():
        return ToolResult.fail("What's the win?")
    mission.add_win(text)
    wins = mission.recent_wins(limit=10)
    return ToolResult(ok=True, summary=f"Noted: {text.strip()}. That's {len(wins)} in the last two weeks.", data=mission.status())


@tool(
    name="add_blocker",
    description="Record something that is blocking progress so it is visible and can be worked around.",
    params={"text": {"type": "string", "description": "What is in the way"}},
    examples=["I'm blocked by the render crashing", "stuck on the voiceover quality", "blocker: waiting for the thumbnail from the designer"],
    category="mission",
)
def add_blocker(text: str) -> ToolResult:
    if not (text or "").strip():
        return ToolResult.fail("What's blocking you?")
    mission.add_blocker(text)
    return ToolResult(ok=True, summary=f"Got it: '{text.strip()}' is in the way. Can we work around it today, or should we move what depends on it?", data=mission.status())


@tool(
    name="clear_blocker",
    description="Mark a blocker as resolved.",
    params={"text": {"type": "string", "description": "Which blocker"}},
    examples=["the render issue is sorted", "unblocked: the thumbnail arrived", "clear the blocker about the voiceover"],
    category="mission",
)
def clear_blocker(text: str) -> ToolResult:
    cleared = mission.clear_blocker(text)
    if not cleared:
        return ToolResult.fail("I don't have a blocker matching that.")
    return ToolResult(ok=True, summary=f"'{cleared}' cleared. Back on it.", data=mission.status())


@tool(
    name="mission_status",
    description="Where we are: the mission, this week's focus, today's win, blockers, recent wins and open loops.",
    examples=["how are we doing", "where are we on the mission", "what's our plan", "what are we working towards", "status check on us", "what's the mission"],
    category="mission",
)
def mission_status() -> ToolResult:
    s = mission.status()
    parts = []
    parts.append(f"Mission: {s['northStar']}." if s["northStar"] else "We haven't set a mission yet; tell me what we're working towards.")
    if s["focus"]:
        parts.append(f"This week: {s['focus']}.")
    parts.append(f"Today's win: {s['todayWin']}." if s["todayWin"] else "No win picked for today yet.")
    if s["blockers"]:
        parts.append("Blockers: " + "; ".join(s["blockers"]) + ".")
    if s["openLoops"]:
        parts.append("Open loops: " + "; ".join(s["openLoops"]) + ".")
    if s["wins"]:
        parts.append(f"Last win: {s['wins'][0]['text']} ({s['wins'][0]['date']}).")
    return ToolResult(ok=True, summary=" ".join(parts), data=s)


@tool(
    name="snooze_checkins",
    description="Pause Dexter's check-ins for a while.",
    params={"hours": {"type": "number", "description": "How long, default 2", "required": False}},
    examples=["not now", "leave me alone for a bit", "pause the check-ins", "no check-ins for the next 3 hours", "I'm in deep work"],
    category="mission",
)
def snooze_checkins(hours: Optional[float] = 2) -> ToolResult:
    hours = float(hours or 2)
    mission.snooze(hours)
    return ToolResult(ok=True, summary=f"Okay, no check-ins for {hours:g} hour(s). I'm here if you need me.", data=mission.status())


@tool(
    name="check_in",
    description="Ask Dexter for a check-in right now: where today stands and what's next.",
    examples=["check in with me", "let's check in", "give me a check-in", "where do we stand today"],
    category="mission",
)
def check_in() -> ToolResult:
    from workbook import workbook

    snapshot = workbook.today()
    due, done, overdue = snapshot.get("due") or [], snapshot.get("done") or [], snapshot.get("overdue") or []
    win = mission.today_win()
    parts = []
    if win:
        parts.append(f"Today's win is '{win}'.")
    parts.append(f"{len(done)} done, {len(due)} open" + (f", {len(overdue)} overdue" if overdue else "") + ".")
    if due:
        parts.append("Next up: " + due[0]["text"] + ".")
    loops = mission.open_loops()
    if loops:
        parts.append(loops[0][0].upper() + loops[0][1:] + ".")
    parts.append("How's it going?" if win else "What's the one thing that makes today a win?")
    text = " ".join(parts)
    mission.mark_sent("midday", text)
    return ToolResult(ok=True, summary=text, data={"quickReplies": ["On it", "Done", "Stuck", "Later"] if win else []})


@tool(
    name="set_initiative",
    description="Who leads: 'dexter' means Dexter takes initiative (check-ins, nudges, proposals); 'me' means Dexter only answers when asked.",
    params={"mode": {"type": "string", "description": "dexter or me", "enum": ["dexter", "me"]}},
    examples=[
        "you take the lead",
        "take initiative",
        "I'll take it from here",
        "stop taking initiative",
        "let me lead for now",
        "you can lead again",
    ],
    category="mission",
)
def set_initiative(mode: str) -> ToolResult:
    mode = mission.set_initiative(mode)
    if mode == "me":
        return ToolResult(ok=True, summary="Understood. You lead; I'll wait to be asked. Say 'take the lead' when you want me proposing again.", data=mission.status())
    return ToolResult(ok=True, summary="On it. I'll check in, flag what slips and propose next moves; 'I'll take it from here' hands it back.", data=mission.status())
