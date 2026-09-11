"""
The desktop hands (roadmap v11, P2): keys, typing, scrolling, clicking by name,
window arrangement, closing apps, and the per-app hotkey recipes.

Everything runs on fakes. No test here may send a keystroke, move a mouse, focus,
close or minimize a real window, or terminate a process - conftest refuses all of
those outright, so a test that forgets to fake one *fails* instead of quietly
acting on the owner's machine.
"""

import os

import desktop_control as dc
import pytest
import tools_desktop
from tool_registry import get_tool


def W(hwnd, title, pid=0, process=""):
    return dc.Win(hwnd=hwnd, title=title, pid=pid, process=process)


CHROME = W(1, "GitHub - Google Chrome", 11, "chrome.exe")
CHROME2 = W(2, "Gmail - Google Chrome", 11, "chrome.exe")
CODE = W(3, "router.py - Visual Studio Code", 12, "code.exe")
NOTEPAD = W(4, "notes.txt - Notepad", 13, "notepad.exe")
DEXTER = W(5, "Dexter", os.getpid(), "pythonw.exe")


@pytest.fixture
def desk(monkeypatch):
    """A fake desktop: four windows plus Dexter, Chrome in front."""
    state = {"windows": [CHROME, CHROME2, CODE, NOTEPAD, DEXTER], "front": 1,
             "sent": [], "typed": [], "closed": [], "shown": [], "killed": []}
    monkeypatch.setattr(dc, "_enum_windows", lambda: list(state["windows"]))
    monkeypatch.setattr(dc, "_foreground_hwnd", lambda: state["front"])
    monkeypatch.setattr(dc, "_set_foreground", lambda hwnd: state.__setitem__("front", hwnd))
    monkeypatch.setattr(dc, "_send_keys", lambda combo: state["sent"].append(combo))
    monkeypatch.setattr(dc, "_write_text", lambda text: state["typed"].append(text))
    monkeypatch.setattr(dc, "_post_close", lambda hwnd: state["closed"].append(hwnd))
    monkeypatch.setattr(dc, "_show_window", lambda hwnd, cmd: state["shown"].append((hwnd, cmd)))
    monkeypatch.setattr(dc, "_terminate", lambda pid: state["killed"].append(pid) or True)
    return state


# ───────────────────────── windows ─────────────────────────


def test_a_window_is_named_by_its_app():
    assert CHROME.app == "Chrome"
    assert CODE.app == "VS Code"
    assert W(9, "report.md - Notepad++").app == "Notepad++", "no process info: read the title"
    assert "Google Chrome" in CHROME.describe()


def test_dexters_own_window_is_never_a_target(desk):
    """You are looking at Dexter when you talk to Dexter, so "close this" must
    not mean "close me"."""
    assert dc.is_self(DEXTER) is True
    assert dc.is_self(CHROME) is False
    assert DEXTER not in dc.windows()
    assert DEXTER in dc.windows(include_self=True)


def test_the_active_window_skips_dexter_and_takes_the_one_behind(desk):
    desk["front"] = DEXTER.hwnd
    assert dc.active_window().hwnd == CHROME.hwnd


def test_finding_a_window_prefers_the_process_over_the_title(desk):
    """"close chrome" is the browser, not a file called chrome.png."""
    paint = W(7, "chrome logo.png - Paint", 14, "mspaint.exe")
    desk["windows"] = [paint, CHROME, CHROME2, DEXTER]
    found = dc.find_windows("chrome")
    assert [w.hwnd for w in found] == [CHROME.hwnd, CHROME2.hwnd]


def test_spoken_aliases_resolve(desk):
    assert [w.hwnd for w in dc.find_windows("the browser")] == [CHROME.hwnd, CHROME2.hwnd]
    assert [w.hwnd for w in dc.find_windows("code")] == [CODE.hwnd]
    assert dc.find_windows("photoshop") == []


def test_a_window_that_is_not_open_says_so(desk):
    with pytest.raises(dc.DesktopError) as err:
        dc.resolve_window("photoshop")
    assert "photoshop" in str(err.value)


# ───────────────────────── focus is verified, not assumed ─────────────────────────


def test_keys_go_to_the_window_that_took_focus(desk):
    desk["front"] = NOTEPAD.hwnd
    info = dc.press_keys("ctrl+s", window="code")
    assert info["window"] == CODE.describe()
    assert desk["sent"] == ["ctrl+s"]


def test_nothing_is_sent_when_focus_cannot_be_taken(desk, monkeypatch):
    """Windows refuses SetForegroundWindow in plenty of situations. Believing it
    worked is how a Ctrl+W lands in the wrong application."""
    monkeypatch.setattr(dc, "_set_foreground", lambda hwnd: None)  # the call is ignored
    monkeypatch.setattr(dc, "FOCUS_TIMEOUT", 0.01)
    desk["front"] = NOTEPAD.hwnd
    with pytest.raises(dc.DesktopError) as err:
        dc.press_keys("ctrl+w", window="chrome")
    assert "couldn't put" in str(err.value)
    assert "Notepad" in str(err.value), "it must name who has the focus"
    assert desk["sent"] == []


def test_dexters_own_window_cannot_be_named_as_a_target(desk):
    """There is no phrasing that sends a keystroke to Dexter itself: its window is
    not in the list a name is matched against."""
    assert dc.find_windows("dexter") == []
    with pytest.raises(dc.DesktopError):
        dc.press_keys("ctrl+w", window="dexter")
    assert desk["sent"] == []


def test_with_dexter_in_front_the_keys_go_to_the_window_behind(desk):
    desk["front"] = DEXTER.hwnd
    info = dc.press_keys("ctrl+s")
    assert info["app"] == "Chrome"
    assert desk["sent"] == ["ctrl+s"]


# ───────────────────────── keys ─────────────────────────


@pytest.mark.parametrize("spoken,combo", [
    ("control s", "ctrl+s"),
    ("ctrl+s", "ctrl+s"),
    ("control shift p", "ctrl+shift+p"),
    ("the f5 key", "f5"),
    ("escape", "esc"),
    ("win left", "windows+left"),
    ("command z", "ctrl+z"),
    ("page down", "page+down"),
])
def test_spoken_keys_become_real_combinations(spoken, combo):
    assert dc.normalize_combo(spoken) == combo


def test_pressing_nothing_asks_what_to_press(desk):
    with pytest.raises(dc.DesktopError):
        dc.press_keys("")


# ───────────────────────── typing, and the secret refusal ─────────────────────────


def test_typing_reports_the_window_and_the_enter(desk):
    info = dc.type_text("hello world", submit=True, window="notepad")
    assert desk["typed"] == ["hello world"]
    assert desk["sent"] == ["enter"]
    assert info["window"] == NOTEPAD.describe()


@pytest.mark.parametrize("text", [
    "my password is hunter2",
    "sk-abc123def456ghi789jkl",
    "Tr0ub4dor&3xKq9mZp",
    "the api key for the studio",
])
def test_secrets_are_refused_into_a_browser(desk, text):
    """Anything typed into the omnibox goes into history, into sync and into the
    suggestion list for whoever borrows the machine."""
    assert dc.looks_like_a_secret(text) is True
    with pytest.raises(dc.DesktopError) as err:
        dc.type_text(text, window="chrome")
    assert "password" in str(err.value).lower()
    assert desk["typed"] == []


@pytest.mark.parametrize("text", [
    "bhangarh fort documentary",
    "https://github.com/anthropics/claude-code",
    "multisonu@gmail.com",
    "thanks for watching",
])
def test_ordinary_text_is_not_treated_as_a_secret(desk, text):
    assert dc.looks_like_a_secret(text) is False
    assert dc.type_text(text, window="chrome")["typed"] == text


def test_a_secret_is_allowed_into_an_editor_and_when_insisted_on(desk):
    assert dc.type_text("sk-abc123def456ghi789", window="notepad")["app"] == "Notepad"
    assert dc.type_text("sk-abc123def456ghi789", window="chrome", allow_secret=True)["app"] == "Chrome"


# ───────────────────────── scrolling ─────────────────────────


def test_scrolling_uses_page_keys_and_never_the_mouse(desk):
    info = dc.scroll("down", amount=3, window="code")
    assert desk["sent"] == ["page down"] * 3
    assert info["times"] == 3 and info["app"] == "VS Code"


def test_scroll_to_the_top_is_one_keystroke(desk):
    dc.scroll("to the top", amount=5, window="code")
    assert desk["sent"] == ["ctrl+home"]


def test_an_unknown_scroll_direction_is_refused(desk):
    with pytest.raises(dc.DesktopError):
        dc.scroll("sideways", window="code")


# ───────────────────────── clicking and reading by name ─────────────────────────


class FakeElement:
    def __init__(self, name, works=True):
        self._name = name
        self.works = works
        self.invoked = False

    def window_text(self):
        return self._name

    def invoke(self):
        if not self.works:
            raise RuntimeError("not invokable")
        self.invoked = True


def test_clicking_by_name_invokes_without_moving_the_mouse(desk, monkeypatch):
    save = FakeElement("Save")
    monkeypatch.setattr(dc, "_uia_window", lambda hwnd: object())
    monkeypatch.setattr(dc, "_descendants",
                        lambda spec, control_type=None: [save] if control_type == "Button" else [])
    info = dc.click_text("save", window="notepad")
    assert save.invoked and info["clicked"] == "Save" and info["app"] == "Notepad"


def test_a_missing_control_lists_what_is_clickable(desk, monkeypatch):
    monkeypatch.setattr(dc, "_uia_window", lambda hwnd: object())
    monkeypatch.setattr(
        dc, "_descendants",
        lambda spec, control_type=None: [FakeElement("Save"), FakeElement("Cancel")]
        if control_type == "Button" else [],
    )
    with pytest.raises(dc.DesktopError) as err:
        dc.click_text("export", window="notepad")
    assert "Save" in str(err.value) and "Cancel" in str(err.value)


def test_without_pywinauto_clicking_says_what_to_install(desk):
    with pytest.raises(dc.DesktopError) as err:
        dc.click_text("save", window="notepad")
    assert "pywinauto" in str(err.value)


def test_reading_a_window_is_budgeted(desk, monkeypatch):
    monkeypatch.setattr(dc, "_uia_window", lambda hwnd: object())
    monkeypatch.setattr(
        dc, "_descendants",
        lambda spec, control_type=None: [FakeElement("line " + str(i)) for i in range(500)],
    )
    info = dc.read_window_text(max_chars=200, window="notepad")
    assert len(info["text"]) <= 200
    assert info["truncated"] is True
    assert info["app"] == "Notepad"


# ───────────────────────── arranging ─────────────────────────


def test_minimize_maximize_and_restore_use_show_window(desk):
    dc.arrange("minimize", window="chrome")
    dc.arrange("maximize", window="chrome")
    dc.arrange("restore", window="chrome")
    assert desk["shown"] == [(1, dc.SW_MINIMIZE), (1, dc.SW_MAXIMIZE), (1, dc.SW_RESTORE)]


def test_snapping_is_the_windows_gesture_on_a_focused_window(desk):
    info = dc.arrange("snap left", window="code")
    assert desk["sent"] == ["windows+left"]
    assert info["app"] == "VS Code"


def test_closing_one_window_is_wm_close(desk):
    dc.arrange("close", window="notepad")
    assert desk["closed"] == [NOTEPAD.hwnd]


def test_arranging_dexters_own_window_is_refused(desk):
    with pytest.raises(dc.DesktopError):
        dc.arrange("close", window="Dexter")
    assert desk["closed"] == []


def test_an_unknown_arrangement_lists_the_real_ones(desk):
    with pytest.raises(dc.DesktopError) as err:
        dc.arrange("rotate", window="chrome")
    assert "minimize" in str(err.value)


# ───────────────────────── closing and killing apps ─────────────────────────


def test_closing_an_app_asks_every_window_politely(desk):
    def _gone():
        # Chrome honours the close; the list is read again by the wait loop.
        return [CODE, NOTEPAD, DEXTER]

    info = dc.close_app("chrome", wait=0)
    assert sorted(desk["closed"]) == [CHROME.hwnd, CHROME2.hwnd]
    assert info["asked"] == 2 and info["app"] == "Chrome"


def test_a_window_that_stays_open_is_reported_not_forced(desk, monkeypatch):
    """An editor with unsaved work shows its own prompt. Dexter's job is to say
    so, not to kill it."""
    info = dc.close_app("code", wait=0)
    assert info["remaining"] == 1 and info["closed"] == 0
    assert desk["killed"] == []
    result = tools_desktop.close_app("code")
    assert result.ok and "still open" in result.summary


def test_closing_an_app_that_is_not_running_says_so(desk):
    with pytest.raises(dc.DesktopError) as err:
        dc.close_app("photoshop")
    assert "photoshop" in str(err.value)


def test_killing_needs_confirmation_and_then_terminates(desk):
    spec = get_tool("kill_app")
    assert spec.needs_confirmation({"name": "chrome"}) is True, "kill must always ask"
    assert "unsaved" in spec.confirmation_prompt({"name": "chrome"})
    info = dc.kill_app("chrome")
    assert info["killed"] == 1 and desk["killed"] == [11]


def test_closing_is_never_a_kill(desk):
    assert get_tool("close_app").needs_confirmation({"name": "chrome"}) is False
    dc.close_app("chrome", wait=0)
    assert desk["killed"] == []


def test_windows_are_listed_grouped_by_app(desk):
    rows = dc.grouped_windows()
    assert [row["app"] for row in rows] == ["Chrome", "VS Code", "Notepad"]
    assert rows[0]["count"] == 2
    result = tools_desktop.list_windows()
    assert "Chrome (2)" in result.summary


# ───────────────────────── the recipe table (P2.2) ─────────────────────────


@pytest.mark.parametrize("action,app,keys", [
    ("new tab", "chrome.exe", "ctrl+t"),
    ("new tab", "notepad.exe", "ctrl+t"),
    ("new file", "code.exe", "ctrl+n"),
    ("save all", "code.exe", "ctrl+k s"),
    ("toggle sidebar", "code.exe", "ctrl+b"),
    ("toggle sidebar", "chrome.exe", "ctrl+shift+b"),
    ("find", "chrome.exe", "ctrl+f"),
    ("find", "spotify.exe", "ctrl+l"),
    ("next track", "spotify.exe", "ctrl+right"),
    ("new folder", "explorer.exe", "ctrl+shift+n"),
    ("export", "adobe premiere pro.exe", "ctrl+m"),
    ("quick switcher", "discord.exe", "ctrl+k"),
    ("command palette", "code.exe", "ctrl+shift+p"),
])
def test_each_app_gets_its_own_keys(action, app, keys):
    found, source = dc.recipe(action, app)
    assert found == keys and source == "app"


def test_an_unknown_app_falls_back_to_the_windows_shortcut():
    keys, source = dc.recipe("save", "someeditor.exe")
    assert keys == "ctrl+s" and source == "generic"


def test_an_action_nobody_knows_is_admitted_to(desk):
    keys, source = dc.recipe("toggle sidebar", "someeditor.exe")
    assert keys is None and source == ""
    with pytest.raises(dc.DesktopError) as err:
        dc.run_recipe("toggle sidebar", window="notepad")
    assert "Notepad" in str(err.value) and "don't know how" in str(err.value)


def test_synonyms_land_on_the_same_recipe():
    assert dc.recipe("open a new tab", "chrome.exe")[0] == "ctrl+t"
    assert dc.recipe("search", "chrome.exe")[0] == "ctrl+f"
    assert dc.recipe("reopen the last tab", "chrome.exe")[0] == "ctrl+shift+t"


def test_a_recipe_runs_in_the_app_in_front(desk):
    result = tools_desktop.app_shortcut("new tab")
    assert result.ok and desk["sent"] == ["ctrl+t"]
    assert "Google Chrome" in result.summary


def test_the_same_words_pick_different_keys_per_app(desk):
    desk["front"] = CODE.hwnd
    tools_desktop.app_shortcut("new file")
    desk["front"] = NOTEPAD.hwnd
    tools_desktop.app_shortcut("new tab")
    assert desk["sent"] == ["ctrl+n", "ctrl+t"]


# ───────────────────────── the tools say what they did ─────────────────────────


def test_every_tool_names_the_window_it_acted_on(desk):
    for result in (
        tools_desktop.press_keys("ctrl+s"),
        tools_desktop.type_text("hello"),
        tools_desktop.scroll_window("down"),
        tools_desktop.switch_to("notepad"),
        tools_desktop.arrange_window("minimize", window="chrome"),
    ):
        assert result.ok, result.summary
        assert any(name in result.summary for name in ("Chrome", "Notepad", "Google Chrome")), result.summary


def test_a_tool_reports_a_refusal_instead_of_raising(desk, monkeypatch):
    monkeypatch.setattr(dc, "_set_foreground", lambda hwnd: None)
    monkeypatch.setattr(dc, "FOCUS_TIMEOUT", 0.01)
    desk["front"] = NOTEPAD.hwnd
    result = tools_desktop.press_keys("ctrl+w", window="chrome")
    assert result.ok is False and "sent nothing" in result.summary


def test_switch_to_refuses_when_the_window_will_not_come_forward(desk, monkeypatch):
    monkeypatch.setattr(dc, "_set_foreground", lambda hwnd: None)
    monkeypatch.setattr(dc, "FOCUS_TIMEOUT", 0.01)
    desk["front"] = NOTEPAD.hwnd
    result = tools_desktop.switch_to("chrome")
    assert result.ok is False and "not sent anything" in result.summary


def test_desktop_status_is_honest_about_what_is_missing(desk, monkeypatch):
    monkeypatch.setattr(dc, "_uia_desktop", lambda: None)
    monkeypatch.setattr(dc, "_keyboard", None)
    result = tools_desktop.desktop_status()
    assert result.ok
    assert "keyboard" in result.summary and "uia" in result.summary


def test_a_known_app_never_matches_a_lookalike_title(desk):
    """"close notepad" with only Notepad++ open closed Notepad++ (v11.1, live):
    a known app with no window is closed, not "the nearest title"."""
    desk["windows"] = [CHROME, W(7, "settings.yaml - Notepad++", 14, "notepad++.exe"), DEXTER]
    assert dc.find_windows("notepad") == []
    with pytest.raises(dc.DesktopError):
        dc.close_app("notepad")
    assert desk["closed"] == []
    # ...while the lookalike is still reachable by its own name, and an unknown
    # name still falls back to the title.
    assert [w.hwnd for w in dc.find_windows("notepad++")] == [7]
    assert [w.hwnd for w in dc.find_windows("settings.yaml")] == [7]
