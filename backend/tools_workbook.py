"""
The coworker tools: goals, commitments, progress, and the honest review.

These are the voice surface over workbook.py. Everything Dexter needed to be a
planner, coach, employee, and second brain and could not do before, because
there was nowhere to put any of it:

    "my goal this quarter is to ship the channel relaunch"
    "I'm committing to scripting episode three by Friday"
    "log that I worked ninety minutes on the edit, blocked on b-roll"
    "what did I say I'd do today"
    "how am I doing on the relaunch"
    "what am I avoiding"
    "close out my day"
    "how was my week"

Phrasings in `examples` are the Tier-1 training corpus, so they are written the
way these things actually get said out loud.
"""

from datetime import date, datetime, timedelta
from typing import Optional

import timeparse
from tool_registry import ToolResult, tool
from workbook import workbook


def _due_from_phrase(when: Optional[str]) -> tuple:
    """('YYYY-MM-DD', 'H:MM AM') from natural language, or (today, None)."""
    if not when or not when.strip():
        return date.today().isoformat(), None
    # parse_date, not parse_when: "by Friday" is a complete due date, and
    # parse_when returns None for anything without a clock time.
    parsed = timeparse.parse_date(when)
    if parsed is None:
        return date.today().isoformat(), None
    has_clock = parsed.hour or parsed.minute
    return parsed.date().isoformat(), parsed.strftime("%I:%M %p").lstrip("0") if has_clock else None


def _describe_due(due_date: Optional[str], due_time: Optional[str]) -> str:
    """'today', 'tomorrow', 'Friday', or a plain date - the way a person says it."""
    if not due_date:
        return "with no date"
    try:
        target = date.fromisoformat(due_date)
    except ValueError:
        return f"on {due_date}"

    delta = (target - date.today()).days
    if delta == 0:
        phrase = "today"
    elif delta == 1:
        phrase = "tomorrow"
    elif delta == -1:
        phrase = "yesterday"
    elif 0 < delta < 7:
        phrase = target.strftime("%A")
    elif delta < 0:
        phrase = f"{abs(delta)} days ago"
    else:
        phrase = target.strftime("%B %d").replace(" 0", " ")
    return f"{phrase} at {due_time}" if due_time else phrase


def _listing(commitments: list, limit: int = 6) -> str:
    lines = [f"{c['text']} ({_describe_due(c.get('due_date'), c.get('due_time'))})"
             for c in commitments[:limit]]
    more = len(commitments) - len(lines)
    if more > 0:
        lines.append(f"and {more} more")
    return "; ".join(lines)


# ───────────────────────── goals ─────────────────────────


@tool(
    name="set_goal",
    description=(
        "Record something the user is working towards - an outcome, not a task. "
        "Commitments and projects hang off goals, and the weekly review reports "
        "against them."
    ),
    params={
        "title": {"type": "string", "description": "The outcome, in the user's words"},
        "why": {"type": "string", "description": "Why it matters", "required": False},
        "by": {"type": "string", "description": "Target date, e.g. 'end of March'", "required": False},
    },
    examples=[
        "my goal this quarter is to ship the channel relaunch",
        "set a goal to get to ten thousand subscribers",
        "I want to work towards launching the course",
        "new goal: finish the studio build",
        "remember that I'm trying to publish weekly",
    ],
    category="workbook",
)
def set_goal(title: str, why: str = "", by: str = "") -> ToolResult:
    target = None
    if by:
        parsed = timeparse.parse_date(by)
        target = parsed.date().isoformat() if parsed else None
    goal = workbook.add_goal(title, why=why, target_date=target)
    tail = f" by {_describe_due(target, None)}" if target else ""
    return ToolResult(
        ok=True,
        summary=f"Goal set: {goal['title']}{tail}. I'll track what you commit to against it.",
        data={"goal": goal},
    )


@tool(
    name="list_goals",
    description="List what the user is currently working towards, with progress.",
    params={},
    examples=[
        "what are my goals", "what am I working towards", "list my goals",
        "remind me what I'm trying to achieve", "what's the big picture",
    ],
    category="workbook",
)
def list_goals() -> ToolResult:
    goals = workbook.list_goals(status="active")
    if not goals:
        return ToolResult(
            ok=True,
            summary="No goals set yet. Tell me what you're working towards and I'll track it.",
            data={"goals": []},
        )

    card = workbook.scorecard()
    by_id = {g["id"]: g for g in card["goals"]}
    parts = []
    for goal in goals:
        stats = by_id.get(goal["id"], {})
        if stats.get("untouched"):
            parts.append(f"{goal['title']} - nothing committed this week")
        else:
            parts.append(f"{goal['title']} - {stats.get('done', 0)} of {stats.get('committed', 0)} done this week")
    return ToolResult(
        ok=True,
        summary=f"{len(goals)} active goal(s). " + ". ".join(parts) + ".",
        data={"goals": goals, "scorecard": card},
    )


@tool(
    name="complete_goal",
    description="Mark a goal as achieved, paused, or dropped.",
    params={
        "title": {"type": "string", "description": "Which goal, by name or part of it"},
        "status": {
            "type": "string", "description": "done, paused, or dropped",
            "required": False, "enum": ["done", "paused", "dropped"],
        },
    },
    examples=[
        "I hit my goal of shipping the relaunch", "mark the relaunch goal as done",
        "pause the course goal for now", "drop the colour grading goal",
    ],
    category="workbook",
)
def complete_goal(title: str, status: str = "done") -> ToolResult:
    goal = workbook.find_goal(title)
    if not goal:
        return ToolResult.fail(f"I don't have a goal matching '{title}'.")
    updated = workbook.set_goal_status(goal["id"], status)
    verb = {"done": "Achieved", "paused": "Paused", "dropped": "Dropped"}.get(status, "Updated")
    return ToolResult(ok=True, summary=f"{verb}: {updated['title']}.", data={"goal": updated})


# ───────────────────────── commitments ─────────────────────────


@tool(
    name="make_commitment",
    description=(
        "Record something the user says they will do, with a due date. This is "
        "the unit Dexter holds them to - unlike a reminder, it is tracked, "
        "rolled visibly when missed, and reported on in the weekly review."
    ),
    params={
        "text": {"type": "string", "description": "What they are committing to"},
        "when": {"type": "string", "description": "When, e.g. 'Friday', 'tomorrow at 3'", "required": False},
        "goal": {"type": "string", "description": "Which goal this serves", "required": False},
    },
    examples=[
        "I'm committing to scripting episode three by Friday",
        "put me down for editing the intro tomorrow",
        "I'll finish the thumbnail today",
        "commit me to a gym session on Monday",
        "add a commitment to call the accountant this week",
        "hold me to finishing the outline tonight",
    ],
    category="workbook",
)
def make_commitment(text: str, when: str = "", goal: str = "") -> ToolResult:
    due_date, due_time = _due_from_phrase(when)
    goal_id = None
    if goal:
        matched = workbook.find_goal(goal)
        goal_id = matched["id"] if matched else None

    commitment = workbook.add_commitment(
        text, due_date=due_date, due_time=due_time, goal_id=goal_id
    )
    tail = f" towards {workbook.get_goal(goal_id)['title']}" if goal_id else ""
    return ToolResult(
        ok=True,
        summary=f"Got it - {commitment['text']}, {_describe_due(due_date, due_time)}{tail}. "
                "I'll hold you to it.",
        data={"commitment": commitment},
    )


@tool(
    name="my_commitments",
    description=(
        "What the user said they would do - today by default, including "
        "anything overdue. This is the planner surface."
    ),
    params={
        "scope": {
            "type": "string", "description": "today, week, or all",
            "required": False, "enum": ["today", "week", "all"],
        },
    },
    examples=[
        "what did I say I'd do today", "what am I committed to",
        "what's on my plate", "what do I owe myself today",
        "what's due this week", "run me through my commitments",
    ],
    category="workbook",
)
def my_commitments(scope: str = "today") -> ToolResult:
    if scope == "week":
        # The next seven days, not the calendar week: asked on a Sunday evening
        # the ISO week has three hours left in it, and everything the user is
        # actually about to do falls outside it.
        window = workbook.upcoming(7)
        open_items = window["commitments"]
        if not open_items:
            return ToolResult(
                ok=True, summary="Nothing committed for the next seven days.", data=window
            )
        return ToolResult(
            ok=True,
            summary=f"{len(open_items)} in the next seven days: {_listing(open_items)}.",
            data=window,
        )

    if scope == "all":
        items = workbook.list_commitments(status="open")
        if not items:
            return ToolResult(ok=True, summary="Nothing outstanding.", data={"commitments": items})
        return ToolResult(
            ok=True,
            summary=f"{len(items)} open commitment(s): {_listing(items)}.",
            data={"commitments": items},
        )

    snapshot = workbook.today()
    due, overdue, done = snapshot["due"], snapshot["overdue"], snapshot["done"]
    if not due and not overdue:
        if done:
            return ToolResult(
                ok=True,
                summary=f"Nothing left today - you've already closed out {len(done)}.",
                data=snapshot,
            )
        return ToolResult(
            ok=True,
            summary="Nothing committed for today. Want to put something down?",
            data=snapshot,
        )

    parts = []
    if due:
        parts.append(f"Today: {_listing(due)}")
    if overdue:
        # Named separately and first-class: silently folding overdue work into
        # "today" is how the old planner hid two-week-old tasks.
        parts.append(f"Still owed from before: {_listing(overdue)}")
    if done:
        parts.append(f"Done so far: {len(done)}")
    return ToolResult(ok=True, summary=". ".join(parts) + ".", data=snapshot)


@tool(
    name="keep_commitment",
    description="Mark a commitment as done, matched by what it says.",
    params={
        "text": {"type": "string", "description": "The commitment, or part of it"},
        "note": {"type": "string", "description": "How it went", "required": False},
    },
    examples=[
        "I finished scripting episode three", "done with the thumbnail",
        "mark the gym session as done", "I did the edit",
        "check off calling the accountant", "that's finished",
    ],
    category="workbook",
)
def keep_commitment(text: str, note: str = "") -> ToolResult:
    commitment = workbook.find_commitment(text)
    if not commitment:
        return ToolResult.fail(f"I don't have an open commitment matching '{text}'.")

    workbook.complete_commitment(commitment["id"], note=note)
    remaining = len(workbook.today()["due"])
    tail = f" {remaining} left today." if remaining else " That's everything for today."
    praise = "Nice." if commitment.get("roll_count", 0) == 0 else "Good - that one had been sitting."
    return ToolResult(
        ok=True,
        summary=f"{praise} Marked done: {commitment['text']}.{tail}",
        data={"commitment": commitment},
    )


@tool(
    name="reschedule_commitment",
    description=(
        "Move a commitment to another day. Dexter counts how many times each "
        "one has moved, and will say so when it becomes a pattern."
    ),
    params={
        "text": {"type": "string", "description": "The commitment, or part of it"},
        "when": {"type": "string", "description": "The new date, e.g. 'tomorrow'", "required": False},
    },
    examples=[
        "push the edit to tomorrow", "move the accountant call to Friday",
        "reschedule the gym session", "I'll do the outline next week instead",
        "bump the thumbnail to Monday",
    ],
    category="workbook",
)
def reschedule_commitment(text: str, when: str = "tomorrow") -> ToolResult:
    commitment = workbook.find_commitment(text)
    if not commitment:
        return ToolResult.fail(f"I don't have an open commitment matching '{text}'.")

    due_date, _ = _due_from_phrase(when)
    moved = workbook.roll_commitment(commitment["id"], due_date)

    # The coaching moment. Not a refusal - a question, once it has earned one.
    challenge = ""
    if moved["roll_count"] >= 3:
        challenge = (
            f" That's the {moved['roll_count']}th time this has moved - "
            "is it still something you actually want to do?"
        )
    return ToolResult(
        ok=True,
        summary=f"Moved to {_describe_due(due_date, None)}: {moved['text']}.{challenge}",
        data={"commitment": moved},
    )


@tool(
    name="drop_commitment",
    description="Let go of a commitment deliberately, rather than letting it rot.",
    params={
        "text": {"type": "string", "description": "The commitment, or part of it"},
        "why": {"type": "string", "description": "Why you're dropping it", "required": False},
    },
    examples=[
        "drop the colour grading commitment", "I'm not going to do the podcast one",
        "let go of the accountant call", "cancel that commitment",
    ],
    category="workbook",
)
def drop_commitment(text: str, why: str = "") -> ToolResult:
    commitment = workbook.find_commitment(text)
    if not commitment:
        return ToolResult.fail(f"I don't have an open commitment matching '{text}'.")
    workbook.drop_commitment(commitment["id"], why=why)
    return ToolResult(
        ok=True,
        summary=f"Dropped: {commitment['text']}. Better than carrying it.",
        data={"commitment": commitment},
    )


# ───────────────────────── the log ─────────────────────────


@tool(
    name="log_progress",
    description=(
        "Record what actually happened - work done, time spent, or a blocker. "
        "This is what makes the weekly review and 'what was I doing' real."
    ),
    params={
        "text": {"type": "string", "description": "What happened"},
        "minutes": {"type": "integer", "description": "Time spent", "required": False},
        "about": {"type": "string", "description": "Which commitment or project", "required": False},
    },
    examples=[
        "log that I worked ninety minutes on the edit",
        "note that I'm blocked on b-roll",
        "I spent two hours scripting today",
        "record that the render finished",
        "log some progress on the relaunch",
    ],
    category="workbook",
)
def log_progress(text: str, minutes: Optional[int] = None, about: str = "") -> ToolResult:
    commitment = workbook.find_commitment(about) if about else None
    kind = "blocker" if any(w in text.lower() for w in ("blocked", "stuck", "waiting on")) else "progress"

    entry = workbook.log_entry(
        text,
        commitment_id=commitment["id"] if commitment else None,
        kind=kind,
        minutes=minutes,
    )
    context = f" against {commitment['text']}" if commitment else ""
    time_note = f" ({minutes} minutes)" if minutes else ""
    return ToolResult(
        ok=True,
        summary=f"Logged{context}{time_note}.",
        data={"entry": entry},
    )


@tool(
    name="what_am_i_avoiding",
    description=(
        "The coaching question: what has gone overdue or been rescheduled so "
        "often that it needs a decision rather than another date."
    ),
    params={},
    examples=[
        "what am I avoiding", "what's slipping", "what have I been putting off",
        "what keeps moving", "am I behind on anything", "be honest with me",
    ],
    category="workbook",
)
def what_am_i_avoiding() -> ToolResult:
    flagged = workbook.slipping()
    if not flagged:
        return ToolResult(
            ok=True,
            summary="Nothing is slipping. Everything open is still on its original date.",
            data={"slipping": []},
        )

    lines = []
    for item in flagged[:4]:
        if item["stale"] and item["roll_count"]:
            lines.append(f"{item['text']} has moved {item['roll_count']} times")
        elif item["daysLate"]:
            lines.append(f"{item['text']} is {item['daysLate']} day(s) overdue")
        else:
            lines.append(item["text"])

    closing = (
        " Worth deciding whether to do them, shrink them, or drop them - "
        "carrying them costs something either way."
    )
    return ToolResult(
        ok=True,
        summary=f"{len(flagged)} thing(s) slipping. " + ". ".join(lines) + "." + closing,
        data={"slipping": flagged},
    )


# ───────────────────────── the loop ─────────────────────────


@tool(
    name="close_out_day",
    description=(
        "End the day deliberately: record what got done, move what did not, and "
        "count the moves. Replaces the silent midnight roll-over."
    ),
    params={
        "note": {"type": "string", "description": "How the day went", "required": False},
    },
    examples=[
        "close out my day", "let's wrap up the day", "end of day review",
        "I'm done for today", "shut it down for today", "day's over",
    ],
    category="workbook",
)
def close_out_day(note: str = "") -> ToolResult:
    result = workbook.close_out(note=note)
    done, rolled = result["done"], result["rolled"]

    if not done and not rolled:
        return ToolResult(ok=True, summary="Quiet day - nothing was committed.", data=result)

    parts = [f"{done} kept"] if done else []
    if rolled:
        parts.append(f"{rolled} moved to tomorrow")
    summary = f"Day closed: {', '.join(parts)}."

    repeat_offenders = [c for c in result["rolledItems"] if c.get("roll_count", 0) >= 2]
    if repeat_offenders:
        names = ", ".join(c["text"] for c in repeat_offenders[:2])
        summary += f" Worth a look: {names} keeps moving."
    if done and not rolled:
        summary += " Clean sheet - good day."
    return ToolResult(ok=True, summary=summary, data=result)


@tool(
    name="weekly_review",
    description=(
        "The honest weekly scorecard: what was committed, what was kept, where "
        "the time went, and which goals got no attention at all."
    ),
    params={},
    examples=[
        "how was my week", "weekly review", "run my week back",
        "how did I do this week", "give me the scorecard", "review the week",
    ],
    category="workbook",
)
def weekly_review() -> ToolResult:
    card = workbook.scorecard()

    if not card["committed"]:
        return ToolResult(
            ok=True,
            summary="Nothing was committed this week, so there's nothing to score. "
                    "Want to set something for next week?",
            data=card,
        )

    ratio = card["keptRatio"]
    verdict = "Strong week." if ratio and ratio >= 0.8 else (
        "Solid." if ratio and ratio >= 0.5 else "Tough week."
    )
    parts = [f"{verdict} You kept {card['done']} of {card['committed']} commitments"]
    if card["minutesLogged"]:
        parts.append(f"and logged {round(card['minutesLogged'] / 60, 1)} hours of work")

    summary = " ".join(parts) + "."
    untouched = [g["title"] for g in card["goals"] if g["untouched"]]
    if untouched:
        # The most useful sentence in a weekly review is usually this one.
        summary += f" Nothing moved on: {', '.join(untouched[:3])}."
    if card["slipping"]:
        summary += f" {len(card['slipping'])} item(s) are slipping."
    return ToolResult(ok=True, summary=summary, data=card)
