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
        # Same reasoning for the v12 ladder note: the rung one test's turn ran
        # on must not be the rung another test's panel reads back.
        model_steward._last_ladder.clear()
        model_steward._relief.clear()
        # v13: and where each model was put.
        model_steward._places.clear()
    except Exception:
        pass

    # v13: the residency API itself. Until now every test that could reach it
    # patched it by hand, and the ladder growing a third rung (RAM) made that a
    # matter of remembering — `ensure` now loads a model on a path that used to
    # stop at a reservation. A test that wants a load asserts on its own stub;
    # one that forgets gets a loud failure instead of the owner's 12B being
    # dropped mid-sentence.
    try:
        import hybrid_llm

        def _no_residency(what):
            def _raise(*args, **kwargs):
                raise AssertionError(f"a test tried to {what} for real")

            return _raise

        for name, what in (
            ("warm_model", "load a model"),
            ("release_model", "unload a model"),
            ("set_keep_alive", "change a model's residency"),
        ):
            monkeypatch.setattr(hybrid_llm.llm_router, name, _no_residency(what), raising=False)
        # Reading is harmless in principle, but it is an HTTP call to whatever
        # Ollama is running on this machine; "nothing is loaded" is both safe
        # and the state most worth testing against.
        monkeypatch.setattr(hybrid_llm.llm_router, "loaded_models", lambda: [], raising=False)

        # And the model itself. Ollama *is* running on this machine, so an
        # unstubbed `chat()` does not fail — it answers, slowly, with whatever
        # the owner has loaded, and then the tool it picked runs for real.
        # Caught exactly that way while v13 was being built: a ladder test that
        # never mentions a model listed the owner's open windows. The default is
        # therefore "no provider is up", which is a real state Dexter handles and
        # a deterministic one; a test that wants an answer stubs `chat`.
        def _no_model(*args, **kwargs):
            return hybrid_llm.LLMResponse(
                provider="none", model="",
                error="no model server in tests (stub llm_router.chat to answer)",
            )

        monkeypatch.setattr(hybrid_llm.llm_router, "chat", _no_model, raising=False)
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
        # v13: the same for the process table. `find_processes` scans every
        # running process to match an app by its path or file description; in a
        # test that is both slow and dependent on whatever the owner has open.
        monkeypatch.setattr(desktop_control, "_process_table", lambda: [], raising=False)
        # The clipboard is shared with the owner's actual work. A test that
        # copies must not leave its marker sitting in it.
        monkeypatch.setattr(desktop_control, "_clipboard_text", lambda: "", raising=False)
        monkeypatch.setattr(desktop_control, "_set_clipboard", lambda text: True, raising=False)
    except Exception:
        pass

    try:
        import chrome_control

        # v13.8: no test may believe any Chrome on this machine is Dexter's own debug
        # one. `page_control.precise()` asks that question now, and the honest answer
        # depends on a process-table scan the suite must not do.
        monkeypatch.setattr(chrome_control, "dexter_chrome_pids", lambda: set(), raising=False)
        monkeypatch.setattr(
            chrome_control, "_spawn",
            lambda args: (_ for _ in ()).throw(
                AssertionError(f"a test tried to launch {args[:1]} for real")
            ),
            raising=False,
        )
    except Exception:
        pass

    # v13.4: the CDP route. `browser_cdp.available()` asks a real port whether a real
    # debuggable Chrome is there, so leaving it live makes the suite's behaviour
    # depend on whether the owner happens to have one open - and it did: nineteen
    # page tests took the exact route the moment one was running. Off by default;
    # test_browser_cdp.py turns it on against a recorder.
    try:
        import browser_cdp

        monkeypatch.setattr(browser_cdp, "ENABLED", False, raising=False)
        monkeypatch.setattr(browser_cdp, "available", lambda timeout=0.5: False, raising=False)
        monkeypatch.setattr(browser_cdp, "version", lambda timeout=0.5: None, raising=False)
        monkeypatch.setattr(
            browser_cdp, "launch",
            lambda wait=0: (_ for _ in ()).throw(
                AssertionError("a test tried to start a real debuggable Chrome")
            ),
            raising=False,
        )
    except Exception:
        pass

    # v13.8: reading the screen and clicking words on it. `screen_text.click` moves the
    # owner's real pointer and presses a real button, and `read` photographs whatever is
    # on their screen and runs OCR over it - neither belongs in a unit test. Tests that
    # are *about* this install their own fake engine.
    try:
        import screen_text

        monkeypatch.setattr(
            screen_text, "capture",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("a test tried to photograph the owner's screen")
            ),
            raising=False,
        )
        monkeypatch.setattr(
            screen_text, "raise_window",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("a test tried to rearrange the owner's windows")
            ),
            raising=False,
        )
        monkeypatch.setattr(screen_text, "_engine", None, raising=False)
    except Exception:
        pass

    try:
        import mouse_input

        # The same reasoning as the keystroke guards: a stray click lands in whatever
        # the owner happens to have open.
        for name in ("move_to", "click", "down", "up", "scroll"):
            monkeypatch.setattr(
                mouse_input, name,
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("a test tried to move or click the real mouse")
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
def _hand_settings_do_not_leak():
    """Put the hand-mouse environment back after every test.

    `settings_store.update` writes `os.environ` directly - that is how a setting
    takes effect without a restart - so monkeypatch cannot undo it. Once the
    gesture map became a setting (v13.6), one test saving "a fist is the right
    click" changed what a fist meant for every test that ran afterwards, in
    another file, with no clue as to why.
    """
    prefix = "DEXTER_HAND_"
    before = {key: value for key, value in os.environ.items() if key.startswith(prefix)}
    yield
    for key in [k for k in os.environ if k.startswith(prefix)]:
        if key not in before:
            del os.environ[key]
    os.environ.update(before)


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
