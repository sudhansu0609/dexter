"""
Reflection: what Dexter does with a day's conversations after they happen.

Two jobs, both run by the scheduler and backfilled at startup (a desktop
assistant is usually off at midnight, so a purely nightly cron would never
actually fire for most users):

  summarize_day / summarize_week   condense conversations into dated episodic
                                   summaries - the memory behind "what did we
                                   talk about last week"; the morning brief
                                   opens with yesterday's.
  extract_facts                    small local-LLM pass over recent turns that
                                   promotes durable personal facts ("my editor
                                   prefers 4k") into the facts table, deduped
                                   by embedding similarity, with provenance.
"""

import json
import time
from datetime import date, datetime, timedelta
from typing import List, Optional

from hybrid_llm import extract_json, llm_router
from memory import memory

# Enough context to summarize a day without flooding a small model.
MAX_TURN_CHARS = 300
MAX_TRANSCRIPT_CHARS = 9000
MIN_TURNS_TO_SUMMARIZE = 4

SUMMARY_PROMPT = (
    "Below is a transcript of today's conversations between the user and their "
    "assistant Dexter. Write a compact summary in 3-5 sentences: what the user "
    "worked on, decided, asked for, and anything left unfinished. Plain prose, "
    "no headings, no bullet points."
)

WEEKLY_PROMPT = (
    "Below are daily summaries from the past week of a user's conversations "
    "with their assistant Dexter. Write a 3-5 sentence weekly recap: recurring "
    "themes, progress made, and open threads. Plain prose."
)

FACTS_PROMPT = (
    "Below is a transcript of conversations between a user and their assistant. "
    "Extract durable personal facts worth remembering long-term: preferences, "
    "people, projects, equipment, routines. Ignore one-off requests, questions, "
    "and anything about the assistant itself. Respond with ONLY a JSON array "
    'of short statements, e.g. ["the user\'s editor prefers 4k footage"]. '
    "Return [] if there is nothing durable."
)


def _day_bounds(day: date) -> tuple:
    start = datetime(day.year, day.month, day.day)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _transcript(turns: List[dict]) -> str:
    lines = []
    for turn in turns:
        content = (turn.get("content") or "")[:MAX_TURN_CHARS]
        lines.append(f"{turn.get('role', 'user')}: {content}")
    text = "\n".join(lines)
    # Keep the most recent part when the day was long.
    return text[-MAX_TRANSCRIPT_CHARS:]


def summarize_day(day: Optional[date] = None) -> Optional[str]:
    """Summarize one calendar day (default: today). Idempotent per day."""
    day = day or date.today()
    period = day.isoformat()
    start, end = _day_bounds(day)
    turns = memory.turns_between(start, end)
    if len(turns) < MIN_TURNS_TO_SUMMARIZE:
        return None

    summary = llm_router.generate(
        f"{SUMMARY_PROMPT}\n\n{_transcript(turns)}",
        system_prompt="You write terse, factual summaries.",
        max_tokens=1024,
    ).strip()
    if not summary or summary.startswith("My local reasoning engine is offline"):
        return None

    memory.upsert_summary("day", period, summary)
    print(f"[Reflection] Stored day summary for {period}.")
    return summary


def summarize_week(anchor: Optional[date] = None) -> Optional[str]:
    """Roll the last 7 day-summaries into one weekly recap."""
    anchor = anchor or date.today()
    parts = []
    for offset in range(7, 0, -1):
        day = anchor - timedelta(days=offset - 1)
        day_summary = memory.get_summary("day", day.isoformat())
        if day_summary:
            parts.append(f"{day.isoformat()}: {day_summary}")
    if not parts:
        return None

    iso_year, iso_week, _ = anchor.isocalendar()
    period = f"{iso_year}-W{iso_week:02d}"
    recap = llm_router.generate(
        f"{WEEKLY_PROMPT}\n\n" + "\n\n".join(parts),
        system_prompt="You write terse, factual summaries.",
        max_tokens=1024,
    ).strip()
    if not recap or recap.startswith("My local reasoning engine is offline"):
        return None

    memory.upsert_summary("week", period, recap)
    print(f"[Reflection] Stored weekly recap {period}.")
    return recap


def extract_facts(hours: int = 24) -> int:
    """Promote durable facts from recent turns. Returns how many were stored."""
    end = time.time()
    turns = memory.turns_between(end - hours * 3600, end)
    user_turns = [t for t in turns if t.get("role") == "user"]
    if len(user_turns) < 2:
        return 0

    raw = llm_router.generate(
        f"{FACTS_PROMPT}\n\n{_transcript(turns)}",
        system_prompt="You extract facts as JSON. No commentary.",
        max_tokens=1024,
        temperature=0.2,
    )
    parsed = extract_json(raw)
    if not isinstance(parsed, list):
        return 0

    stored = 0
    stamp = date.today().isoformat()
    for item in parsed[:15]:
        statement = str(item).strip() if not isinstance(item, dict) else str(
            item.get("fact", "")
        ).strip()
        if not 8 <= len(statement) <= 300:
            continue
        if memory.similar_fact(statement):
            continue  # already known, embedding-close duplicate
        key = " ".join(statement.lower().split()[:8]).rstrip(".,:;")
        if memory.remember(key, statement, source=f"auto:{stamp}"):
            stored += 1
    if stored:
        print(f"[Reflection] Extracted {stored} new fact(s) from the last {hours}h.")
    return stored


def catch_up() -> None:
    """
    Startup backfill: if yesterday had conversations but no summary, write it
    now, extract its facts, and roll the week on Mondays. This is what makes
    reflection actually run on a machine that sleeps at night.
    """
    try:
        yesterday = date.today() - timedelta(days=1)
        if not memory.get_summary("day", yesterday.isoformat()):
            if summarize_day(yesterday):
                extract_facts(hours=48)
        if date.today().weekday() == 0:  # Monday: recap the finished week
            iso_year, iso_week, _ = (date.today() - timedelta(days=1)).isocalendar()
            if not memory.get_summary("week", f"{iso_year}-W{iso_week:02d}"):
                summarize_week(date.today() - timedelta(days=1))
    except Exception as exc:
        print(f"[Reflection] Catch-up skipped: {exc}")
