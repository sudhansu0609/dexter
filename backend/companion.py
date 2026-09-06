"""
Dexter as a coworker (roadmap v7).

Everything Dexter used to say on its own initiative went out as a toast or a
banner, and everything it said in reply was a tool's summary. That is a
program with notifications. This module gives Dexter a stance and a voice:

- `context_block()` is injected into every model prompt: how to talk (a
  partner on one mission, not a help desk), what the mission is, what today
  looks like, what is hanging over us.
- `say()` is how Dexter starts a conversation: the message goes into the chat
  as a Dexter turn (and into memory, so the thread continues), with optional
  quick replies. Toasts are only a wake-up for the morning greeting.
- The check-ins (morning, midday, evening, weekly, and event-driven ones)
  are composed by the model from facts when a provider is up, otherwise from
  templates, so they never invent numbers.
- `handle_short_reply()` understands "done", "on it", "stuck", "later" as an
  answer to the last check-in instead of routing them as commands.
- `after_reply()` adds one coaching sentence to a tool's summary when it
  moves things forward, rate-limited so it never becomes noise.
"""

import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import mission
from events import bus

ENABLED = os.environ.get("DEXTER_CHECKINS", "1").strip().lower() not in ("0", "false", "no", "off")
MIDDAY_HOUR = int(os.environ.get("DEXTER_MIDDAY_HOUR", "13"))
TAIL_INTERVAL = 15 * 60
_last_tail = 0.0
_lock = threading.Lock()

STANCE = """## How you work with the user
You and the user are coworkers on one mission (below). You are not a command line and not a notification service: you are the colleague who knows the plan, keeps the thread, and coaches.
- Speak as a partner: "we", "our", plain words, warm and direct. No "How can I assist you today", no corporate filler, no lists of what you can do.
- Tie what you say to the mission and to today's win when it is relevant; do not mention them on every turn.
- Ask at most one question, and only when the answer changes what happens next.
- When something slipped, say so plainly and offer a way through (break it down, move it, drop it). Never scold.
- When something got done, acknowledge it in a few words. No confetti.
- Keep it short. Match the user's language and register (Hinglish is fine if they use it).
- You remember today's conversation; refer back to it instead of asking again."""


def enabled() -> bool:
    return ENABLED


def context_block() -> str:
    return STANCE + "\n\n" + mission.render_block() + "\n\n" + mission.today_block()


def fast_stance() -> str:
    star = mission.load().get("north_star") or "not set yet"
    win = mission.today_win() or "not chosen yet"
    return (
        "You are Dexter, the user's coworker on a shared mission "
        f"({star}); today's win: {win}. Talk like a partner: 'we', short, warm, direct, "
        "never like a help desk. Never list what you can do."
    )


def greeting_reply() -> str:
    """The tier-0 answer to 'hi' / 'good morning': a colleague, not a chatbot."""
    hour = datetime.now().hour
    opener = "Morning." if hour < 12 else "Hey." if hour < 18 else "Evening."
    win = mission.today_win()
    loops = mission.open_loops()
    if win:
        return f"{opener} Today's win is still '{win}'. Where do we start?"
    if loops:
        return f"{opener} Before anything else: {loops[0]}. What's the one thing that makes today a win?"
    if mission.load().get("focus"):
        return f"{opener} This week we're on '{mission.load()['focus']}'. What's today's win?"
    return f"{opener} What are we going after today?"


# ───────────────────────── speaking first ─────────────────────────


def _quiet() -> bool:
    try:
        import proactive

        return proactive.in_quiet_hours()
    except Exception:
        return False


def say(kind: str, text: str, quick_replies: Optional[List[str]] = None, toast: bool = False, force: bool = False) -> bool:
    """Start a conversation. Returns False when it was held back (disabled, quiet hours, snoozed, already sent)."""
    text = (text or "").strip()
    if not text:
        return False
    if not force:
        if not enabled() or _quiet() or mission.snoozed():
            return False
        if kind in ("morning", "midday", "evening", "weekly") and mission.was_sent(kind):
            return False
    try:
        from memory import memory

        memory.add_turn("assistant", text, tool_calls=f"checkin:{kind}")
    except Exception:
        pass
    mission.mark_sent(kind, text)
    bus.publish("companion", {"kind": kind, "text": text, "quickReplies": quick_replies or [], "ts": time.time()})
    if toast:
        try:
            import notify

            notify.toast_async("Dexter", text[:140])
        except Exception:
            pass
    print(f"[Companion] {kind}: {text[:120]}")
    return True


def compose(kind: str, facts: List[str], fallback: str, ask: str = "") -> str:
    """Let the model phrase the check-in from facts; fall back to the template when it cannot."""
    try:
        from hybrid_llm import llm_router
    except Exception:
        return fallback
    system = (
        context_block()
        + "\n\n## Task\nWrite Dexter's next message to the user, unprompted, as the coworker described above. "
        "Use ONLY the facts below; do not invent numbers, names or events. Two or three sentences, plain text, "
        "no headings, no bullet points, no emoji. "
        + (f"End with this question (rephrase freely, keep the meaning): {ask}" if ask else "No question needed.")
        + "\n\n## Facts\n- " + "\n- ".join(f for f in facts if f)
    )
    try:
        response = llm_router.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": f"Write the {kind} message."}],
            provider="auto", temperature=0.6, max_tokens=180, tools=None,
        )
    except Exception:
        return fallback
    text = (getattr(response, "content", "") or "").strip().strip('"')
    if not getattr(response, "error", None) and 20 <= len(text) <= 700 and "\n\n" not in text:
        return text
    return fallback


def _today_facts() -> Dict[str, Any]:
    facts: Dict[str, Any] = {"due": [], "done": [], "overdue": [], "slipping": [], "yesterday": ""}
    try:
        from workbook import workbook

        snapshot = workbook.today()
        facts["due"] = snapshot.get("due") or []
        facts["done"] = snapshot.get("done") or []
        facts["overdue"] = snapshot.get("overdue") or []
        facts["slipping"] = [c for c in workbook.slipping() if int(c.get("roll_count") or 0) >= 2]
    except Exception:
        pass
    try:
        from memory import memory

        yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
        summary = memory.get_summary("day", yesterday)
        if summary:
            facts["yesterday"] = summary.split(". ")[0].rstrip(".")
    except Exception:
        pass
    return facts


def morning(force: bool = False) -> bool:
    f = _today_facts()
    win = mission.today_win()
    data = mission.load()
    facts = []
    if f["yesterday"]:
        facts.append(f"Yesterday: {f['yesterday']}")
    if f["due"]:
        facts.append("Committed today: " + "; ".join(c["text"] for c in f["due"][:5]))
    if f["overdue"]:
        facts.append(f"Still open from before: {len(f['overdue'])} item(s), e.g. '{f['overdue'][-1]['text']}'")
    if f["slipping"]:
        facts.append(f"'{f['slipping'][0]['text']}' has been moved {f['slipping'][0].get('roll_count')} times")
    if data.get("focus"):
        facts.append(f"This week's focus: {data['focus']}")
    if win:
        facts.append(f"Today's win was already set: {win}")
        ask = f"Still going for '{win}'?"
        quick = ["Yes, that's the one", "Change it"]
    else:
        ask = "What's the one thing that makes today a win?"
        quick = [c["text"][:40] for c in f["due"][:3]]
    parts = ["Morning."]
    if f["yesterday"]:
        parts.append(f"Yesterday: {f['yesterday']}.")
    if f["due"]:
        parts.append(f"Today we've got {len(f['due'])}: " + "; ".join(c["text"] for c in f["due"][:3]) + ".")
    if f["overdue"]:
        parts.append(f"'{f['overdue'][-1]['text']}' is still open from {f['overdue'][-1].get('due_date')}.")
    if f["slipping"]:
        parts.append(f"And '{f['slipping'][0]['text']}' has moved {f['slipping'][0].get('roll_count')} times; we should break it down or drop it.")
    parts.append(ask)
    fallback = " ".join(parts)
    text = compose("morning", facts, fallback, ask=ask)
    return say("morning", text, quick_replies=quick, toast=True, force=force)


def midday(force: bool = False) -> bool:
    win = mission.today_win()
    f = _today_facts()
    if not win:
        if not f["due"]:
            return False
        first = f["due"][0]["text"]
        fallback = f"Quick one: we haven't picked today's win. Is it '{first}', or something else?"
        text = compose("midday", [f"No win chosen for today; first commitment is '{first}'"], fallback,
                       ask=f"Is today's win '{first}', or something else?")
        return say("midday", text, quick_replies=[f"It's {first[:30]}", "Something else"], force=force)
    fallback = f"How's '{win}' going?"
    text = compose("midday", [f"Today's win: {win}", f"Done so far: {len(f['done'])}, still open: {len(f['due'])}"],
                   fallback, ask=f"How is '{win}' going?")
    return say("midday", text, quick_replies=["On it", "Done", "Stuck", "Later"], force=force)


def evening(force: bool = False) -> bool:
    f = _today_facts()
    win = mission.today_win()
    if not (f["due"] or f["done"] or f["overdue"]):
        return False
    open_items = f["due"] + f["overdue"]
    facts = [f"Kept today: {len(f['done'])}", f"Still open: {len(open_items)}"]
    if f["done"]:
        facts.append("Done: " + "; ".join(c["text"] for c in f["done"][:4]))
    if open_items:
        facts.append("Open: " + "; ".join(c["text"] for c in open_items[:4]))
    if win:
        facts.append(f"Today's win was: {win}")
    ask = "What got done, and what rolls to tomorrow?"
    fallback = (f"That's the day. {len(f['done'])} kept, {len(open_items)} still open"
                + (f", including '{open_items[0]['text']}'" if open_items else "") + f". {ask}")
    text = compose("evening", facts, fallback, ask=ask)
    return say("evening", text, quick_replies=["Close out the day", "Roll the rest", "Later"], force=force)


def weekly(card: Dict[str, Any], force: bool = False) -> bool:
    facts = [f"This week: {card.get('done', 0)} of {card.get('committed', 0)} commitments kept"]
    untouched = [g["title"] for g in card.get("goals", []) if g.get("untouched")]
    if untouched:
        facts.append(f"Nothing moved on: {untouched[0]}")
    if card.get("slipping"):
        facts.append(f"{len(card['slipping'])} item(s) slipping")
    ask = "Want to run the full review together?"
    fallback = " ".join(facts) + f". {ask}"
    text = compose("weekly", facts, fallback, ask=ask)
    return say("weekly", text, quick_replies=["Run the weekly review", "Later"], toast=True, force=force)


def on_event(kind: str, payload: Dict[str, Any]) -> bool:
    """Something happened while we were both working; say it like a colleague would."""
    payload = payload or {}
    if kind == "code_done":
        project = payload.get("project") or "the repo"
        status = payload.get("status") or "finished"
        files = payload.get("filesChanged") or []
        msg = (payload.get("message") or "")[:200]
        if status == "done":
            text = f"buzzcode is done in {project}" + (f": {len(files)} file(s) touched" if files else "") + (f". It says: {msg}" if msg else ".") + " Want to look at it together?"
            return say("code", text, quick_replies=["Code status", "Looks good"], force=True)
        text = f"The code task in {project} {status}." + (f" {msg}" if msg else "") + " Want me to try a narrower task?"
        return say("code", text, quick_replies=["Code status", "Later"], force=True)
    if kind == "approval_needed":
        step = payload.get("step") or "a step"
        name = payload.get("name") or payload.get("project_id") or "the project"
        return say("studio", f"'{step}' on {name} is waiting on us. Approve, or want to check it first?",
                   quick_replies=["Approve", "Check it first"], force=True)
    if kind == "step_failed":
        return say("studio", f"A Studio step failed on {payload.get('project_id')}: {str(payload.get('error'))[:160]}. Want me to retry it or look at why?",
                   quick_replies=["Run the next step", "Later"], force=True)
    return False


def greet_if_needed() -> bool:
    """On launch: if it is daytime and we have not said good morning yet, do it now."""
    now = datetime.now()
    if now.hour < 6 or now.hour >= 20:
        return False
    if mission.was_sent("morning"):
        return False
    return morning()


# ───────────────────────── replies to a check-in ─────────────────────────

_DONE = re.compile(r"^(?:done|finished|did it|complete[d]?|ho gaya|hogaya|kar diya|yes done|it'?s done)\W*$", re.I)
_ON_IT = re.compile(r"^(?:on it|working(?: on it)?|still (?:going|on it|working)|in progress|chal raha(?: hai)?|going well|almost(?: there| done)?)\W*$", re.I)
_STUCK = re.compile(r"^(?:stuck|blocked|not moving|can'?t (?:get|make) (?:it|this) (?:work|move)|atka hua(?: hai)?)\W*$", re.I)
_LATER = re.compile(r"^(?:later|not now|busy|baad mein|leave me(?: alone)?(?: for a bit)?|in a bit)\W*$", re.I)
_YES = re.compile(r"^(?:yes|yeah|yep|yes,? that'?s the one|that'?s the one|haan|ha)\W*$", re.I)
_CHANGE = re.compile(r"^(?:change it|something else|no,? something else|different)\W*$", re.I)
_ITS = re.compile(r"^it'?s\s+(.+)$", re.I)


def handle_short_reply(message: str) -> Optional[str]:
    """An answer to the last check-in, if the message reads like one. None means: route normally."""
    text = (message or "").strip()
    last = mission.last_checkin()
    if not last or not text or len(text) > 80:
        return None
    kind = last.get("kind")
    win = mission.today_win()
    if _DONE.match(text):
        if not win:
            return None
        mission.add_win(win)
        completed = None
        try:
            import agenda

            completed = agenda.complete(win)
        except Exception:
            pass
        tail = " Anything next, or coast for a bit?"
        return f"Logged: '{win}' done." + (" Commitment closed too." if completed else "") + tail
    if _ON_IT.match(text) and kind in ("midday", "morning"):
        mission.snooze(2)
        return "Good. I'll check back in a couple of hours."
    if _STUCK.match(text):
        return "What's in the way? Say 'blocked by ...' and we'll work around it, or we break it into a smaller piece."
    if _LATER.match(text):
        mission.snooze(2)
        return "Okay, I'll leave you to it for a couple of hours."
    if kind == "morning" and _YES.match(text) and win:
        return f"Locked in: '{win}'. Go."
    if kind in ("morning", "midday") and _CHANGE.match(text):
        return "What's the new one? Say 'today's win is ...'."
    if kind == "midday" and not win:
        m = _ITS.match(text)
        if m:
            mission.set_today_win(m.group(1).strip(" .'\""))
            return f"Today's win: '{mission.today_win()}'. Let's get it."
    return None


# ───────────────────────── one coaching sentence after a tool ─────────────────────────


def after_reply(tool_name: Optional[str], ok: bool, reply: str) -> str:
    """A short tail on tier-0/1 tool replies so they read like a colleague, not a log line."""
    global _last_tail
    if not tool_name or not ok or not enabled():
        return reply
    try:
        from workbook import workbook

        snapshot = workbook.today()
    except Exception:
        return reply
    due = snapshot.get("due") or []
    done = snapshot.get("done") or []
    overdue = snapshot.get("overdue") or []
    tail = ""
    if tool_name in ("complete_task", "keep_commitment"):
        total = len(due) + len(done)
        if total and not due and not overdue:
            tail = f"That's all {total} for today cleared."
        elif total:
            tail = f"{len(done)} of {total} today."
        if mission.today_win() and mission.today_win().lower() in reply.lower():
            mission.add_win(mission.today_win())
            tail = (tail + " That was today's win.").strip()
    else:
        with _lock:
            if time.time() - _last_tail < TAIL_INTERVAL:
                return reply
        if tool_name in ("add_task", "make_commitment") and overdue:
            oldest = overdue[-1]
            tail = f"Also, '{oldest['text']}' is still open from {oldest.get('due_date')}; want it before this one?"
        elif tool_name in ("get_agenda", "my_commitments") and not mission.today_win() and due:
            tail = "Which one is today's win?"
        elif tool_name in ("create_reminder",) and len(overdue) >= 3:
            tail = f"Heads up: {len(overdue)} things are overdue; say 'what am I avoiding' when you want to sort them."
    if not tail:
        return reply
    if tool_name not in ("complete_task", "keep_commitment"):
        with _lock:
            _last_tail = time.time()
    return reply.rstrip() + " " + tail
