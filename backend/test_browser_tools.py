"""
The browser tools (v6 B1, rebuilt for v11 P1.4).

Two things are pinned here: target normalisation and fast-path routing (which
have to keep working whichever browser is in charge), and the mode switch -
`own` acts in the user's Chrome through chrome_control, `playwright` keeps the v6
behaviour. Both are driven with fakes: nothing in this file may start Chrome,
send a keystroke or close a window (conftest refuses anyway).
"""

import chrome_control
import desktop_control as dc
import pytest
import tools_browser  # noqa: F401  - registers the browser tools
import tools_code  # noqa: F401  - registers the code tools (routing test below)
import tools_desktop  # noqa: F401  - registers the desktop tools
import tools_studio  # noqa: F401
from browser import BrowserError, known_site, looks_like_a_site, normalize_target
from router import fast_path


def test_targets_become_urls_sites_or_searches():
    assert normalize_target("github.com") == "https://github.com"
    assert normalize_target("https://example.com/a?b=1") == "https://example.com/a?b=1"
    assert normalize_target("youtube") == "https://www.youtube.com/"
    assert normalize_target("YouTube Studio") == "https://studio.youtube.com/"
    assert normalize_target("youtube bhangarh fort").startswith("https://www.youtube.com/results?search_query=bhangarh")
    assert normalize_target("best mic for voiceover").startswith("https://www.google.com/search?q=best")


def test_a_site_name_is_a_site_and_an_app_name_is_not():
    """The line v11 turns on: 'youtube' is a tab, 'chrome' is an application."""
    assert known_site("youtube") == "https://www.youtube.com/"
    assert known_site("the gmail website") == "https://mail.google.com/"
    assert known_site("Reddit") == "https://www.reddit.com/"
    assert known_site("chrome") is None
    assert known_site("notepad") is None
    assert known_site("spotify") is None, "Spotify is installed here; it must still launch"
    assert looks_like_a_site("github.com") and looks_like_a_site("gmail")
    assert not looks_like_a_site("photoshop")


def _route(text):
    hit = fast_path(text)
    assert hit is not None, f"no fast rule for {text!r}"
    return hit


def test_fast_rules_reach_the_browser_tools():
    tool, args = _route("go to github.com")[:2]
    assert tool == "browser_open" and args == {"target": "github.com"}
    tool, args = _route("search youtube for bhangarh fort documentary")[:2]
    assert tool == "browser_open" and args["target"].startswith("youtube ")
    assert _route("read this page")[0] == "browser_read_page"
    tool, args = _route("click sign in")[:2]
    assert tool == "browser_click" and args == {"target": "sign in"}
    tool, args = _route("type bhangarh fort into the search box and press enter")[:2]
    assert tool == "browser_type" and args["text"] == "bhangarh fort" and args["into"] == "search" and args["submit"]
    assert _route("scroll down")[0] == "browser_scroll"
    assert _route("what tabs are open")[0] == "browser_tabs"
    tool, args = _route("switch to the youtube tab")[:2]
    assert tool == "browser_switch_tab" and args == {"which": "youtube"}
    assert _route("close this tab")[0] == "browser_close_tab"
    assert _route("screenshot this page")[0] == "browser_screenshot"
    assert _route("go back")[0] == "browser_back"


def test_reminders_and_apps_still_route_as_before():
    assert _route("remind me to stretch in 20 minutes")[0] == "create_reminder"
    assert _route("open the studio")[0] == "buzzcaf_start_studio"
    assert _route("open notepad")[0] == "launch_app"


# ───────────────────────── mode: playwright (v6) ─────────────────────────


@pytest.fixture
def playwright_mode(monkeypatch):
    monkeypatch.setenv("DEXTER_BROWSER_MODE", "playwright")


def test_playwright_mode_still_drives_dexters_own_window(playwright_mode, monkeypatch):
    calls = {}

    def _open(target, new_tab=False):
        calls["open"] = (target, new_tab)
        return {"url": "https://github.com", "title": "GitHub"}

    monkeypatch.setattr(tools_browser.browser, "open", _open)
    result = tools_browser.browser_open("github.com")
    assert result.ok and "GitHub" in result.summary
    assert calls["open"] == ("github.com", False)


def test_playwright_failures_are_reported_not_raised(playwright_mode, monkeypatch):
    def boom(*args, **kwargs):
        raise BrowserError("Chrome was not found.")

    monkeypatch.setattr(tools_browser.browser, "open", boom)
    result = tools_browser.browser_open("github.com")
    assert result.ok is False and "Chrome" in result.summary


def test_things_playwright_cannot_do_say_so(playwright_mode):
    for result in (tools_browser.browser_zoom("in"),
                   tools_browser.browser_fullscreen(),
                   tools_browser.browser_find_in_page("pricing"),
                   tools_browser.browser_reopen_tab()):
        assert result.ok is False
        assert "own Chrome" in result.summary


# ───────────────────────── mode: own (v11, the default) ─────────────────────────


@pytest.fixture
def own_chrome(monkeypatch):
    """One fake Chrome window, in focus, with nothing real behind it."""
    monkeypatch.delenv("DEXTER_BROWSER_MODE", raising=False)
    win = dc.Win(hwnd=101, title="GitHub - Google Chrome", pid=4242, process="chrome.exe")
    other = dc.Win(hwnd=202, title="notes.txt - Notepad", pid=99, process="notepad.exe")
    monkeypatch.setattr(dc, "_enum_windows", lambda: [win, other])
    monkeypatch.setattr(dc, "_foreground_hwnd", lambda: 101)
    sent = []
    typed = []
    monkeypatch.setattr(dc, "_send_keys", lambda combo: sent.append(combo))
    monkeypatch.setattr(dc, "_write_text", lambda text: typed.append(text))
    return {"window": win, "sent": sent, "typed": typed}


def test_own_mode_is_the_default():
    assert chrome_control.mode() == "own"
    assert chrome_control.own_mode() is True


def test_opening_a_site_hands_the_url_to_the_running_chrome(own_chrome, monkeypatch):
    spawned = []
    monkeypatch.setattr(chrome_control, "find_chrome", lambda: r"C:\chrome.exe")
    monkeypatch.setattr(chrome_control, "_spawn", lambda args: spawned.append(args))
    result = tools_browser.browser_open("youtube")
    assert result.ok, result.summary
    assert spawned == [[r"C:\chrome.exe", "https://www.youtube.com/"]]
    assert result.data["launched"] is False, "Chrome was already running"
    assert "GitHub" in result.summary


def test_opening_with_chrome_closed_launches_it(monkeypatch):
    monkeypatch.delenv("DEXTER_BROWSER_MODE", raising=False)
    monkeypatch.setattr(dc, "_enum_windows", lambda: [])
    monkeypatch.setattr(dc, "_foreground_hwnd", lambda: 0)
    monkeypatch.setattr(chrome_control, "is_running", lambda: False)
    monkeypatch.setattr(chrome_control, "find_chrome", lambda: r"C:\chrome.exe")
    monkeypatch.setattr(chrome_control, "LAUNCH_WAIT_SECONDS", 0.01)
    spawned = []
    monkeypatch.setattr(chrome_control, "_spawn", lambda args: spawned.append(args))
    result = tools_browser.browser_open("reddit")
    assert result.ok and result.data["launched"] is True
    assert spawned == [[r"C:\chrome.exe", "https://www.reddit.com/"]]
    assert result.summary.startswith("Opened Chrome on")


def test_no_chrome_on_disk_is_said_plainly(own_chrome, monkeypatch):
    monkeypatch.setattr(chrome_control, "find_chrome", lambda: None)
    result = tools_browser.browser_open("youtube")
    assert result.ok is False
    assert "chrome.exe" in result.summary and "DEXTER_CHROME_EXE" in result.summary


def test_tab_actions_are_keystrokes_to_the_focused_chrome(own_chrome):
    assert tools_browser.browser_close_tab().ok
    assert tools_browser.browser_new_tab().ok
    assert tools_browser.browser_back().ok
    assert tools_browser.browser_forward().ok
    assert tools_browser.browser_reload().ok
    assert tools_browser.browser_reload(hard=True).ok
    assert tools_browser.browser_reopen_tab().ok
    assert tools_browser.browser_zoom("out").ok
    assert tools_browser.browser_fullscreen().ok
    assert own_chrome["sent"] == [
        "ctrl+w", "ctrl+t", "alt+left", "alt+right", "f5", "ctrl+shift+r",
        "ctrl+shift+t", "ctrl+-", "f11",
    ]


def test_every_result_names_the_window_it_acted_on(own_chrome):
    result = tools_browser.browser_close_tab()
    assert "GitHub - Google Chrome" in result.summary


def test_find_in_page_opens_the_find_bar_then_types(own_chrome):
    result = tools_browser.browser_find_in_page("pricing")
    assert result.ok and "pricing" in result.summary
    assert own_chrome["sent"] == ["ctrl+f"]
    assert own_chrome["typed"] == ["pricing"]


def test_tab_actions_with_chrome_closed_say_chrome_is_closed(monkeypatch):
    monkeypatch.delenv("DEXTER_BROWSER_MODE", raising=False)
    monkeypatch.setattr(dc, "_enum_windows", lambda: [])
    monkeypatch.setattr(dc, "_foreground_hwnd", lambda: 0)
    result = tools_browser.browser_close_tab()
    assert result.ok is False and "isn't open" in result.summary


def test_reading_the_active_tab_uses_ui_automation(own_chrome, monkeypatch):
    monkeypatch.setattr(chrome_control, "active_url", lambda win=None: "https://github.com/anthropics")
    monkeypatch.setattr(chrome_control, "tab_names", lambda win=None: ["GitHub", "Gmail"])
    monkeypatch.setattr(chrome_control, "page_text", lambda max_chars=6000, win=None: "Sign in to GitHub")
    result = tools_browser.browser_read_page()
    assert result.ok
    assert result.data["url"] == "https://github.com/anthropics"
    assert result.data["tabs"] == ["GitHub", "Gmail"]
    assert "Sign in to GitHub" in result.summary


def test_a_page_that_exposes_no_text_says_so_instead_of_inventing(own_chrome, monkeypatch):
    monkeypatch.setattr(chrome_control, "active_url", lambda win=None: "https://example.com")
    monkeypatch.setattr(chrome_control, "tab_names", lambda win=None: [])
    monkeypatch.setattr(chrome_control, "page_text", lambda max_chars=6000, win=None: "")
    result = tools_browser.browser_read_page()
    assert result.ok and "couldn't get any text" in result.summary


def test_listing_tabs_reads_the_tab_strip(own_chrome, monkeypatch):
    monkeypatch.setattr(chrome_control, "tab_names", lambda win=None: ["GitHub", "Gmail", "YouTube"])
    result = tools_browser.browser_tabs()
    assert result.ok and result.data["tabs"] == ["GitHub", "Gmail", "YouTube"]
    assert "3 tab(s)" in result.summary


def test_switching_to_a_named_tab_uses_its_position(own_chrome, monkeypatch):
    monkeypatch.setattr(chrome_control, "tab_names", lambda win=None: ["GitHub", "Gmail", "YouTube"])
    result = tools_browser.browser_switch_tab("youtube")
    assert result.ok and result.data["index"] == 3
    assert own_chrome["sent"] == ["ctrl+3"]


def test_switching_to_a_tab_that_is_not_there_lists_what_is(own_chrome, monkeypatch):
    monkeypatch.setattr(chrome_control, "tab_names", lambda win=None: ["GitHub", "Gmail"])
    result = tools_browser.browser_switch_tab("figma")
    assert result.ok is False
    assert "figma" in result.summary and "Gmail" in result.summary


def test_next_and_previous_tab_are_plain_keystrokes(own_chrome):
    assert tools_browser.browser_switch_tab("next").ok
    assert tools_browser.browser_switch_tab("previous").ok
    assert own_chrome["sent"] == ["ctrl+tab", "ctrl+shift+tab"]


def test_clicking_in_the_page_goes_through_accessibility_names(own_chrome, monkeypatch):
    class Element:
        def __init__(self, name):
            self._name = name
            self.invoked = False

        def window_text(self):
            return self._name

        def invoke(self):
            self.invoked = True

    button = Element("Sign in")
    monkeypatch.setattr(dc, "_uia_window", lambda hwnd: object())
    monkeypatch.setattr(
        dc, "_descendants",
        lambda spec, control_type=None: [button] if control_type == "Button" else [],
    )
    result = tools_browser.browser_click("sign in")
    assert result.ok and button.invoked
    assert "Sign in" in result.summary and "Google Chrome" in result.summary


def test_clicking_something_absent_names_what_is_there(own_chrome, monkeypatch):
    class Element:
        def __init__(self, name):
            self._name = name

        def window_text(self):
            return self._name

    monkeypatch.setattr(dc, "_uia_window", lambda hwnd: object())
    monkeypatch.setattr(
        dc, "_descendants",
        lambda spec, control_type=None: [Element("Sign up"), Element("Pricing")]
        if control_type == "Button" else [],
    )
    result = tools_browser.browser_click("log out")
    assert result.ok is False
    assert "Sign up" in result.summary and "Pricing" in result.summary


def test_scrolling_in_own_mode_is_page_keys_not_a_mouse_wheel(own_chrome):
    result = tools_browser.browser_scroll("down", amount=2)
    assert result.ok and own_chrome["sent"] == ["page down", "page down"]
    assert "Google Chrome" in result.summary


def test_a_screenshot_captures_the_chrome_window(own_chrome, monkeypatch):
    monkeypatch.setattr(dc, "capture_window", lambda win, path=None: r"C:\shots\chrome.png")
    result = tools_browser.browser_screenshot()
    assert result.ok and "chrome.png" in result.summary
