"""
Test-suite guards (roadmap v9).

The v9 modules reach out to real things: Sentinel's named pipe, Ollama's
`/api/ps`, `lms unload --all`, `taskkill`. A test that forgets to stub one of
those does not fail — it *works*, on the owner's live machine, and unloads a
model somebody was using or stops a render. That happened once while D2 was
being built, which is why this file exists.

So the default for every test is: no live Sentinel, no LM Studio management,
and a temp apps registry. A test that genuinely wants one of them opts back in
explicitly.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _no_live_machine(monkeypatch, tmp_path):
    # A pipe name nothing can be listening on: every Sentinel call degrades to
    # "absent" in well under the 300 ms budget, which is also the path most
    # worth testing.
    monkeypatch.setenv("SENTINEL_PIPE", r"\\.\pipe\SentinelAbsentInTests")

    # The port ledger is shared with every other app on this machine. A test
    # that published into the real one would tell the Studio that Dexter is on
    # a port belonging to a pytest process that ended a second later.
    monkeypatch.setenv("BUZZCAF_PORTS_FILE", str(tmp_path / "ports.json"))
    try:
        import buzzcaf_ports

        # Discovery caches its answers for a few seconds; one test's "offline"
        # must not become another test's starting point.
        buzzcaf_ports._forget()
    except Exception:
        pass

    try:
        import model_steward

        # Never run `lms unload --all` from a test: LM Studio may be serving
        # BuzzEdit, and a unit test is not allowed to take that away.
        monkeypatch.setattr(model_steward, "MANAGE_LMSTUDIO", False, raising=False)
        # The steward remembers its last shed so `evicted_notice` can mention it.
        # That is module state, and one test's shed showing up in another test's
        # initiative scan is exactly the kind of order-dependent failure that
        # wastes an afternoon.
        model_steward._last_shed.clear()
        model_steward._ours.clear()
    except Exception:
        pass

    # v11: the desktop hands. These send real keystrokes, close real windows and
    # start real processes, and the default must be that a test cannot. Proven
    # necessary within a minute of writing them: one browser test handed
    # github.com to the owner's live Chrome because the tool is now mode-aware
    # and no longer goes through the Playwright fake.
    try:
        import desktop_control

        def _forbidden(what):
            def _raise(*args, **kwargs):
                raise AssertionError(f"a test tried to {what} for real")

            return _raise

        for name, what in (
            ("_send_keys", "send keystrokes"),
            ("_write_text", "type text"),
            ("_post_close", "close a window"),
            ("_set_foreground", "steal the foreground"),
            ("_show_window", "minimize or maximize a real window"),
            ("_terminate", "terminate a process"),
        ):
            monkeypatch.setattr(desktop_control, name, _forbidden(what), raising=False)
        # Reading is harmless, but a UIA walk of the owner's screen is slow and
        # non-deterministic, so it is absent unless a test fakes it.
        monkeypatch.setattr(desktop_control, "_uia_window", lambda hwnd: None, raising=False)
    except Exception:
        pass

    try:
        import chrome_control

        monkeypatch.setattr(
            chrome_control, "_spawn",
            lambda args: (_ for _ in ()).throw(
                AssertionError(f"a test tried to launch {args[:1]} for real")
            ),
            raising=False,
        )
    except Exception:
        pass

    try:
        import procs

        # Nothing in the suite may terminate a real process. VrDeep renders for
        # hours; there is no version of "oops" that gives those hours back.
        monkeypatch.setattr(
            procs, "kill_tree",
            lambda pid, timeout=5.0: (_ for _ in ()).throw(
                AssertionError("a test tried to kill a real process tree")
            ),
            raising=False,
        )
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _no_real_voice_profile(monkeypatch, tmp_path):
    """The owner's enrolled voice must never judge a test's synthetic audio.

    Found the day after enrolment (v11.1): `test_wake_word_alone_is_not_a_message`
    started failing because data/voice_profile.json now existed and the real
    encoder scored the fake clip 0.37 - "someone else". A test that wants a
    speaker verdict installs its own stand-in (see test_speaker_id.py).
    """
    try:
        import speaker_id as _sid
        import voice_pipeline as _vp

        blank = _sid.SpeakerId(path=str(tmp_path / "no_profile.json"))
        monkeypatch.setattr(_vp, "speaker_id", blank, raising=False)
        monkeypatch.setattr(_sid, "speaker_id", blank, raising=False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_model_server_autostart(monkeypatch):
    """A unit test must never start `ollama serve` on the owner's machine.

    get_available_providers() now revives the preferred backend when it is
    down (v11.1), and plenty of tests probe health. Off by default; the
    llm_boot tests install fakes and opt back in.
    """
    try:
        import llm_boot

        monkeypatch.setattr(llm_boot, "ENABLED", False, raising=False)
        llm_boot._reset_for_tests()
    except Exception:
        pass
