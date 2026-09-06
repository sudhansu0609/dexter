"""
Tests for parse_date - due dates, which are days rather than moments.

parse_when() requires a clock time and returns None without one, because a
reminder with no time cannot fire. Commitments are the opposite: "I'll have it
done by Friday" is a complete thought. Getting this wrong silently dated every
commitment to today, which is exactly the kind of quiet wrongness that made the
planner untrustworthy.
"""

from datetime import datetime

import pytest

import timeparse

# A fixed Sunday, so "friday" and "next week" have unambiguous answers.
BASE = datetime(2026, 8, 23, 14, 30)  # Sunday


def parse(text: str):
    return timeparse.parse_date(text, base=BASE)


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("today", "2026-08-23"),
        ("tomorrow", "2026-08-24"),
        ("day after tomorrow", "2026-08-25"),
        ("friday", "2026-08-28"),
        ("by friday", "2026-08-28"),
        ("on friday", "2026-08-28"),
        ("next monday", "2026-08-24"),
        ("next week", "2026-08-24"),
        ("end of the week", "2026-08-28"),
        ("in 3 days", "2026-08-26"),
    ],
)
def test_bare_day_phrases_resolve_to_a_date(phrase, expected):
    result = parse(phrase)
    assert result is not None, f"{phrase!r} should resolve to a day"
    assert result.date().isoformat() == expected


@pytest.mark.parametrize("phrase", ["today", "tomorrow", "friday", "in 3 days", "next week"])
def test_a_bare_day_carries_no_clock_time(phrase):
    """Otherwise a commitment reads as due 'Wednesday at 11:16 PM'."""
    result = parse(phrase)
    assert (result.hour, result.minute) == (0, 0)


@pytest.mark.parametrize(
    "phrase,expected_hour,expected_minute",
    [
        ("friday at 3pm", 15, 0),
        ("tomorrow at 9am", 9, 0),
        ("monday 5:30 pm", 17, 30),
        ("tonight", 20, 0),
    ],
)
def test_an_explicit_time_is_preserved(phrase, expected_hour, expected_minute):
    result = parse(phrase)
    assert (result.hour, result.minute) == (expected_hour, expected_minute)


def test_nothing_date_like_returns_none():
    for phrase in ("", "   ", "the thumbnail", "programme"):
        assert parse(phrase) is None


def test_parse_when_still_refuses_a_bare_day():
    """The two functions answer different questions; keep them distinct."""
    assert timeparse.parse_when("friday", base=BASE) is None
    assert timeparse.parse_when("friday at 3pm", base=BASE) is not None


@pytest.mark.skipif(not timeparse._HAS_DATEPARSER, reason="dateparser not installed")
def test_dateparser_cannot_smuggle_in_a_midnight_reminder():
    """
    Installing dateparser changed this behaviour and nearly slipped through.

    dateparser resolves "friday" to Friday 00:00, and parse_when returned it -
    so "remind me to call mom friday" would have quietly become an alarm at
    midnight instead of Dexter asking what time. A bare day is still "no time
    given" here; parse_date is the function for which a day is a full answer.
    """
    for phrase in ("friday", "next tuesday", "on the 3rd of september"):
        assert timeparse.parse_when(phrase, base=BASE) is None, phrase

    # An explicitly requested midnight is a real time and must survive.
    assert timeparse.parse_when("at midnight", base=BASE) is not None
    assert timeparse.parse_when("tomorrow at midnight", base=BASE) is not None
