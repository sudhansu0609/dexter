"""Window tools (v8): matching that closes the window you meant."""


def test_close_prefers_the_whole_word_match():
    """'close notepad' used to close Notepad++ (a substring hit listed first)."""
    from windows_tools import _rank_windows

    class W:
        def __init__(self, title):
            self.title = title

    wins = [W("REPORT.md - Notepad++"), W("Untitled - Notepad"), W("Decode.png - Photos"), W("Visual Studio Code")]
    assert [w.title for w in _rank_windows(wins, "notepad")] == ["Untitled - Notepad"]
    assert [w.title for w in _rank_windows(wins, "notepad++")] == ["REPORT.md - Notepad++"]
    assert [w.title for w in _rank_windows(wins, "code")] == ["Visual Studio Code"]
    assert [w.title for w in _rank_windows(wins, "photos")] == ["Decode.png - Photos"]
    assert _rank_windows(wins, "spotify") == []
    # nothing whole-word: fall back to the substring hit rather than failing
    assert [w.title for w in _rank_windows(wins, "ecod")] == ["Decode.png - Photos"]


# ───────────────────────── v11.1: power ─────────────────────────
#
# "restart my pc" reached nothing (the only power tools were lock and sleep).
# All three destructive ones ask first; the countdown can be cancelled.


def test_power_tools_ask_first():
    from tool_registry import get_tool

    for name in ("restart_pc", "shutdown_pc", "sign_out", "empty_recycle_bin"):
        spec = get_tool(name)
        assert spec is not None, name
        assert spec.needs_confirmation({}), f"{name} must ask before it acts"
        prompt = spec.confirmation_prompt({})
        assert "yes" in prompt.lower(), (name, prompt)
    assert not get_tool("cancel_shutdown").needs_confirmation({})


def test_restart_and_shutdown_use_a_grace_period(monkeypatch):
    import subprocess

    import windows_tools

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: calls.append(args))
    monkeypatch.setattr(windows_tools, "POWER_GRACE_SECONDS", 10)

    r = windows_tools.restart_pc()
    assert r.ok and "10 seconds" in r.summary and "cancel" in r.summary.lower()
    assert calls[-1][:4] == ["shutdown.exe", "/r", "/t", "10"]

    r = windows_tools.shutdown_pc()
    assert r.ok and calls[-1][:4] == ["shutdown.exe", "/s", "/t", "10"]

    r = windows_tools.sign_out()
    assert r.ok and calls[-1] == ["shutdown.exe", "/l"]


def test_cancel_shutdown_reports_both_outcomes(monkeypatch):
    import subprocess

    import windows_tools

    class P:
        def __init__(self, rc):
            self.returncode, self.stderr, self.stdout = rc, "", ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: P(0))
    assert "stays on" in windows_tools.cancel_shutdown().summary
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: P(1116))
    assert "Nothing" in windows_tools.cancel_shutdown().summary
