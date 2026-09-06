"""
Tests for the calendar/email completions: reschedule, cancel, reply, search.

These cannot be exercised against a real account here - Google is not connected
on this machine - so the API is driven through a fake service. That still pins
the behaviours that are easy to get wrong and expensive when wrong: preserving a
meeting's duration when moving it, threading a reply instead of starting a new
conversation, and resolving "my 3pm" to the right event.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest

import google_tools


class FakeRequest:
    def __init__(self, result: Any, recorder: List = None, call: tuple = None) -> None:
        self._result = result
        self._recorder = recorder
        self._call = call

    def execute(self) -> Any:
        if self._recorder is not None and self._call is not None:
            self._recorder.append(self._call)
        return self._result


class FakeEvents:
    def __init__(self, events: List[Dict], recorder: List) -> None:
        self._events = events
        self._recorder = recorder

    def list(self, **kwargs):
        return FakeRequest({"items": self._events})

    def patch(self, **kwargs):
        return FakeRequest({}, self._recorder, ("patch", kwargs))

    def delete(self, **kwargs):
        return FakeRequest({}, self._recorder, ("delete", kwargs))


class FakeCalendar:
    def __init__(self, events: List[Dict], recorder: List) -> None:
        self._events = events
        self._recorder = recorder

    def events(self):
        return FakeEvents(self._events, self._recorder)


def at(hour: int, day_offset: int = 0) -> str:
    moment = (datetime.now() + timedelta(days=day_offset)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return moment.astimezone().isoformat()


@pytest.fixture()
def calendar(monkeypatch):
    recorder: List = []
    events = [
        {
            "id": "evt-standup",
            "summary": "Team standup",
            "start": {"dateTime": at(10, 1)},
            "end": {"dateTime": at(11, 1)},
        },
        {
            "id": "evt-client",
            "summary": "Client call",
            "start": {"dateTime": at(15, 1)},
            # A 30-minute meeting, to prove the duration is preserved.
            "end": {"dateTime": (
                datetime.fromisoformat(at(15, 1)) + timedelta(minutes=30)
            ).isoformat()},
        },
    ]
    monkeypatch.setattr(
        google_tools, "_service", lambda *a, **k: FakeCalendar(events, recorder)
    )
    return recorder


# ───────────────────────── reschedule ─────────────────────────


def test_reschedule_moves_the_matching_event(calendar):
    result = google_tools.reschedule_event("client call", "tomorrow at 4pm")
    assert result.ok

    verb, kwargs = calendar[0]
    assert verb == "patch"
    assert kwargs["eventId"] == "evt-client"
    assert "16:00" in kwargs["body"]["start"]["dateTime"]


def test_reschedule_preserves_the_meeting_length(calendar):
    """A 30-minute call moved to 4pm must end at 4:30, not 5."""
    google_tools.reschedule_event("client call", "tomorrow at 4pm")

    _, kwargs = calendar[0]
    start = datetime.fromisoformat(kwargs["body"]["start"]["dateTime"])
    end = datetime.fromisoformat(kwargs["body"]["end"]["dateTime"])
    assert end - start == timedelta(minutes=30)


def test_an_event_can_be_found_by_its_time(calendar):
    """'move my 3pm' refers to the clock, not the title."""
    result = google_tools.reschedule_event("my 3pm", "tomorrow at 5pm")
    assert result.ok
    assert calendar[0][1]["eventId"] == "evt-client"


def test_an_unmatched_event_is_reported_not_guessed(calendar):
    result = google_tools.reschedule_event("dentist appointment", "tomorrow at 4pm")
    assert not result.ok
    assert calendar == [], "nothing should have been modified"


def test_an_unparseable_time_is_refused(calendar):
    result = google_tools.reschedule_event("client call", "sometime whenever")
    assert not result.ok
    assert calendar == []


# ───────────────────────── cancel ─────────────────────────


def test_cancel_deletes_the_matching_event(calendar):
    result = google_tools.cancel_event("standup")
    assert result.ok

    verb, kwargs = calendar[0]
    assert verb == "delete"
    assert kwargs["eventId"] == "evt-standup"


def test_cancel_and_reschedule_both_require_confirmation():
    from tool_registry import REGISTRY

    for name in ("cancel_event", "reschedule_event", "reply_to_email"):
        assert REGISTRY[name].confirm is True, f"{name} must ask before acting"


# ───────────────────────── reply ─────────────────────────


class FakeMessages:
    def __init__(self, recorder: List) -> None:
        self._recorder = recorder

    def list(self, **kwargs):
        return FakeRequest({"messages": [{"id": "msg-1"}]})

    def get(self, **kwargs):
        return FakeRequest({
            "threadId": "thread-9",
            "snippet": "about the invoice",
            "payload": {
                "headers": [
                    {"name": "From", "value": '"The Accountant" <acc@example.com>'},
                    {"name": "Subject", "value": "March invoice"},
                    {"name": "Message-ID", "value": "<abc@mail>"},
                ]
            },
        })

    def send(self, **kwargs):
        return FakeRequest({}, self._recorder, ("send", kwargs))


class FakeGmail:
    def __init__(self, recorder: List) -> None:
        self._recorder = recorder

    def users(self):
        return self

    def messages(self):
        return FakeMessages(self._recorder)


@pytest.fixture()
def gmail(monkeypatch):
    recorder: List = []
    monkeypatch.setattr(google_tools, "_service", lambda *a, **k: FakeGmail(recorder))
    return recorder


def test_reply_stays_in_the_same_thread(gmail):
    """Without threadId and In-Reply-To this starts a new conversation."""
    import base64

    result = google_tools.reply_to_email("accountant", "Sending it tomorrow.")
    assert result.ok

    _, kwargs = gmail[0]
    assert kwargs["body"]["threadId"] == "thread-9"

    raw = base64.urlsafe_b64decode(kwargs["body"]["raw"]).decode()
    assert "In-Reply-To: <abc@mail>" in raw
    assert "Subject: Re: March invoice" in raw
    assert "acc@example.com" in raw


def test_reply_does_not_double_prefix_an_existing_re(gmail, monkeypatch):
    def already_replied(**kwargs):
        return FakeRequest({
            "threadId": "t",
            "payload": {"headers": [
                {"name": "From", "value": "x@example.com"},
                {"name": "Subject", "value": "Re: March invoice"},
            ]},
        })

    monkeypatch.setattr(FakeMessages, "get", lambda self, **k: already_replied(**k))
    google_tools.reply_to_email("anyone", "ok")

    import base64
    raw = base64.urlsafe_b64decode(gmail[0][1]["body"]["raw"]).decode()
    assert "Re: Re:" not in raw


def test_search_email_passes_the_query_through(gmail, monkeypatch):
    seen = {}

    def capture_list(self, **kwargs):
        seen.update(kwargs)
        return FakeRequest({"messages": []})

    monkeypatch.setattr(FakeMessages, "list", capture_list)
    result = google_tools.search_email("invoice from the accountant")

    assert result.ok
    assert seen["q"] == "invoice from the accountant"


def test_search_needs_something_to_look_for(gmail):
    assert not google_tools.search_email("   ").ok
