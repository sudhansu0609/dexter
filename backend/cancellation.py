"""
Per-session generation cancellation.

Barge-in (Phase 4.5) needs to stop an in-flight LLM stream from a *different*
request than the one generating it: the voice WebSocket detects the user talking
over Dexter and must halt the chat/stream endpoint that is still producing tokens
and speech. The two are correlated only by the chat `session` id.

This is deliberately tiny and dependency-free so both router.py (which passes the
`should_cancel` check into the agent loop) and main.py (which arms/triggers it)
can import it without an import cycle. hybrid_llm.py never imports this - it just
takes an opaque `should_cancel` callable.
"""

import threading
from typing import Dict

_events: Dict[str, threading.Event] = {}
_lock = threading.Lock()


def _event_for(session: str) -> threading.Event:
    with _lock:
        event = _events.get(session)
        if event is None:
            event = threading.Event()
            _events[session] = event
        return event


def begin(session: str = "default") -> None:
    """Clear any stale cancel flag at the start of a new request for this session."""
    _event_for(session).clear()


def request_cancel(session: str = "default") -> None:
    """Signal that the session's in-flight generation should stop as soon as possible."""
    _event_for(session).set()


def is_cancelled(session: str = "default") -> bool:
    return _event_for(session).is_set()


def clear(session: str = "default") -> None:
    """Drop the flag once the request has finished reacting to it."""
    _event_for(session).clear()
