"""
Knowing where Dexter stopped (v13.7).

Dexter froze twice in one evening. Both times the evidence was a log that simply
**ended** mid-turn: no exception, no traceback, nothing from any other thread. Three
plausible explanations survived a full round of investigation, one was tested and
disproved, and none could be confirmed - because the only question that matters had
no answer: *which thread was stuck, and in what call?*

So the watchdog's job is to answer that without being asked, and these tests are
about the properties that make it trustworthy when everything else has failed:

* the heartbeat is written **outside the logging system**, because a jammed logging
  handler silences every thread and is itself a suspect;
* a turn that overruns produces **every thread's stack**, naming the blocked one;
* it writes the stacks **once** per stuck turn, not once per tick;
* it says when the last HTTP request arrived, which is what separates a frozen front
  end (the microphone lives in the page) from a frozen backend.
"""

import os
import threading
import time

import pytest

import watchdog


@pytest.fixture
def wd(tmp_path, monkeypatch):
    """A watchdog writing into a temp directory, with a short fuse."""
    monkeypatch.setattr(watchdog, "HEARTBEAT_FILE", str(tmp_path / "heartbeat.txt"))
    monkeypatch.setattr(watchdog, "STACKS_FILE", str(tmp_path / "stacks.log"))
    monkeypatch.setattr(watchdog, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(watchdog, "INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(watchdog, "STUCK_AFTER", 0.3)
    monkeypatch.setattr(watchdog, "_stacks_handle", None, raising=False)
    monkeypatch.setattr(watchdog, "_turns", {}, raising=False)
    monkeypatch.setattr(watchdog, "_turns_done", 0, raising=False)
    monkeypatch.setattr(watchdog, "_dumped", set(), raising=False)
    monkeypatch.setattr(watchdog, "_thread", None, raising=False)
    yield watchdog
    watchdog._turns = {}
    watchdog._stacks_handle = None


def _wait_for(path, timeout=5.0, contains=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            text = open(path, encoding="utf-8", errors="replace").read()
            if not contains or contains in text:
                return text
        time.sleep(0.05)
    return open(path, encoding="utf-8", errors="replace").read() if os.path.exists(path) else ""


# ───────────────────────── the heartbeat ─────────────────────────


def test_a_turn_in_flight_is_visible_with_its_age(wd):
    wd.start()
    wd.note_request("/api/dexter/chat/stream")
    wd.turn_started("can I set up my hand tracking?", source="voice")
    text = _wait_for(wd.HEARTBEAT_FILE, contains="IN_TURN")
    assert "IN_TURN" in text
    assert "hand tracking" in text, "which turn, not just that there was one"
    assert "last_request=" in text, "what separates a frozen UI from a frozen backend"


def test_the_heartbeat_does_not_go_through_logging(wd, monkeypatch):
    """The scenario this was written for is every thread going quiet at once, and a
    deadlocked logging handler does exactly that. So the heartbeat must still be
    written when `logging` is the thing that is broken."""
    import logging

    def explode(*args, **kwargs):
        raise AssertionError("the watchdog must not use the logging system")

    monkeypatch.setattr(logging.Logger, "handle", explode)
    monkeypatch.setattr(logging.Logger, "_log", explode)
    wd.start()
    assert _wait_for(wd.HEARTBEAT_FILE, contains="threads="), "nothing was written"


def test_an_idle_dexter_says_so(wd):
    wd.start()
    assert "idle" in _wait_for(wd.HEARTBEAT_FILE, contains="idle")


# ───────────────────────── the stacks ─────────────────────────


def test_a_stuck_turn_produces_every_threads_stack(wd):
    """The whole point: the blocked thread has to be named, with its frames."""
    release = threading.Event()

    def a_thread_that_never_finishes():
        release.wait(10)

    worker = threading.Thread(target=a_thread_that_never_finishes, name="dexter-stream-tts")
    worker.start()
    try:
        wd.start()
        wd.turn_started("can I set up my hand tracking?")
        stacks = _wait_for(wd.STACKS_FILE, contains="a turn has been running")
        assert "a turn has been running" in stacks
        assert "hand tracking" in stacks, "the dump says which turn"
        assert "dexter-stream-tts" in stacks, "and names the thread that is stuck"
        assert "a_thread_that_never_finishes" in stacks, "with the frame it is stuck in"
    finally:
        release.set()
        worker.join(5)


def test_the_stacks_are_written_once_per_stuck_turn_not_once_per_tick(wd):
    wd.start()
    wd.turn_started("a turn that never ends")
    _wait_for(wd.STACKS_FILE, contains="a turn has been running")
    time.sleep(0.6)   # several more ticks
    dumps = open(wd.STACKS_FILE, encoding="utf-8").read().count("a turn has been running")
    assert dumps == 1, f"{dumps} dumps - a hang would fill the disk"


def test_a_turn_that_finishes_is_never_reported(wd):
    wd.start()
    token = wd.turn_started("a turn that finishes")
    wd.turn_finished(token)
    time.sleep(0.6)
    assert not os.path.exists(wd.STACKS_FILE) or "running" not in open(
        wd.STACKS_FILE, encoding="utf-8").read()


def test_the_stacks_can_be_asked_for_at_any_time(wd):
    """For the case where Dexter has gone quiet but HTTP still answers - which is
    itself half the diagnosis."""
    assert wd.dump_stacks("asked for") is True
    assert "asked for" in open(wd.STACKS_FILE, encoding="utf-8").read()


# ───────────────────────── the bookkeeping ─────────────────────────


def test_turns_are_counted_and_the_current_one_reported(wd):
    first = wd.turn_started("one")
    wd.turn_finished(first)
    second = wd.turn_started("two")
    state = wd.status()
    assert state["turns"] == 1
    assert state["inTurn"]["text"] == "two"
    assert state["inFlight"] == 1
    wd.turn_finished(second)
    assert wd.status()["inTurn"] is None


def test_overlapping_turns_are_tracked_separately(wd):
    """Turns do overlap - the pop-up and the full window both route - and the first
    version kept a single slot, so two of them shared a token and finishing the
    older one hid the live one from the watchdog entirely."""
    older = wd.turn_started("the turn that will hang")
    newer = wd.turn_started("a quick one alongside it")
    assert older != newer
    wd.turn_finished(newer)
    state = wd.status()
    assert state["inFlight"] == 1
    assert state["inTurn"]["text"] == "the turn that will hang", "the oldest is the suspect"
    wd.turn_finished(older)
    assert wd.status()["inTurn"] is None


def test_it_can_be_switched_off(wd, monkeypatch):
    monkeypatch.setattr(wd, "ENABLED", False)
    assert wd.start() is False


def test_starting_twice_is_one_watchdog(wd):
    assert wd.start() is True
    first = wd._thread
    assert wd.start() is True
    assert wd._thread is first


def test_the_turn_is_recorded_around_every_route(monkeypatch):
    """The wiring, not the watchdog: a turn that bypasses this is a turn that can
    hang without anybody finding out."""
    import main

    seen = {}
    monkeypatch.setattr(main.watchdog, "turn_started",
                        lambda text, source="chat": seen.setdefault("started", text) and 7 or 7)
    monkeypatch.setattr(main.watchdog, "turn_finished",
                        lambda token: seen.setdefault("finished", token))
    monkeypatch.setattr(main, "_route_turn", lambda req, on_event=None: {"reply": "ok"})

    req = main.ChatRequest(message="hello there")
    assert main._run_route(req)["reply"] == "ok"
    assert seen["started"] == "hello there"
    assert seen["finished"] == 7


def test_a_failing_turn_still_clears_the_watchdog(monkeypatch):
    import main

    monkeypatch.setattr(main, "_route_turn",
                        lambda req, on_event=None: (_ for _ in ()).throw(RuntimeError("boom")))
    before = main.watchdog.status()["inTurn"]
    with pytest.raises(RuntimeError):
        main._run_route(main.ChatRequest(message="hello"))
    assert main.watchdog.status()["inTurn"] == before, "a crashed turn must not look stuck forever"
