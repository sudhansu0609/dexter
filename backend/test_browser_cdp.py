"""
The exact route: Playwright over CDP (v13.4).

Two things to pin down, and the second matters more than the first:

1. When a debuggable Chrome is up, the page tools go through it - exactly, in
   document order, waiting for navigation.
2. When it is **not** up, nothing changes: every one of those tools falls back to
   the accessibility route that v13.1-v13.3 built. An upgrade that becomes a
   dependency is a regression.

No real Chrome and no real Playwright here: the session is a recorder. The launch
command is checked as a command line, because the two flags that matter (the debug
port and a non-default profile) are the whole reason this works at all on Chrome 136+.
"""

import pytest

import browser_cdp
import page_control as page

#: conftest stubs `launch` so no test can start a real Chrome. The three tests that
#: are *about* launching put the real one back deliberately - captured here at import
#: time, which is before any fixture has replaced it.
REAL_LAUNCH = browser_cdp.launch


class FakeSession:
    """Records what the exact route was asked to do."""

    def __init__(self):
        self.calls = []
        self.last_error = ""
        self.selection_text = "the highlighted paragraph"
        self.rows = [
            {"title": "The best USB microphones in 2026", "url": "https://a.example/1"},
            {"title": "Podcast mic shootout", "url": "https://a.example/2"},
            {"title": "Cheap mics that sound expensive", "url": "https://a.example/3"},
        ]

    def goto(self, url, same_tab=True):
        self.calls.append(("goto", url, same_tab))
        return {"url": url, "title": "a page", "sameTab": same_tab}

    def results(self, limit=12):
        self.calls.append(("results", limit))
        return [{**row, "position": i} for i, row in enumerate(self.rows[:limit], 1)]

    def open_nth(self, position=1, limit=20):
        self.calls.append(("open_nth", position))
        row = self.rows[position - 1]
        return {"opened": row["title"], "url": row["url"], "position": position,
                "was": "https://a.example/search", "changed": True, "title": row["title"]}

    def open_text(self, text, limit=40):
        self.calls.append(("open_text", text))
        row = next(r for r in self.rows if text.lower() in r["title"].lower())
        return {"opened": row["title"], "url": row["url"], "was": "x", "changed": True,
                "title": row["title"]}

    def selection(self):
        self.calls.append(("selection",))
        return self.selection_text

    def fill(self, field, text, submit=False):
        self.calls.append(("fill", field, text, submit))
        return {"filled": field or "the focused field", "typed": text,
                "submitted": submit, "url": "https://gemini.example", "title": "Gemini"}

    def switch(self, which):
        self.calls.append(("switch", which))
        return {"switched": f"{which} - tab", "url": "https://x"}

    def tabs(self):
        return [{"position": 1, "title": "a tab", "url": "https://x"}]

    def where(self):
        return {"url": "https://a.example/search", "title": "search - a.example"}

    def close(self):
        self.calls.append(("close",))


@pytest.fixture
def exact(monkeypatch):
    """A live debuggable Chrome, faked."""
    fake = FakeSession()
    monkeypatch.setattr(browser_cdp, "launch", REAL_LAUNCH)
    monkeypatch.setattr(browser_cdp, "available", lambda timeout=0.5: True)
    monkeypatch.setattr(browser_cdp, "version",
                        lambda timeout=0.5: {"Browser": "Chrome/141.0"})
    monkeypatch.setattr(browser_cdp, "session", fake)
    monkeypatch.setattr(browser_cdp, "ENABLED", True)
    return fake


@pytest.fixture
def absent(monkeypatch):
    """No debuggable Chrome: the accessibility route must carry everything."""
    monkeypatch.setattr(browser_cdp, "available", lambda timeout=0.5: False)
    monkeypatch.setattr(browser_cdp, "version", lambda timeout=0.5: None)


# ───────────────────────── which route ─────────────────────────


def test_precise_is_true_only_when_a_debuggable_chrome_answers(exact):
    assert page.precise() is True


def test_precise_is_false_without_one(absent):
    assert page.precise() is False


def test_precise_is_false_when_the_route_is_switched_off(monkeypatch):
    monkeypatch.setenv("DEXTER_CHROME_CDP", "0")
    import importlib

    reloaded = importlib.reload(browser_cdp)
    try:
        assert reloaded.ENABLED is False
        assert reloaded.version() is None
    finally:
        monkeypatch.delenv("DEXTER_CHROME_CDP", raising=False)
        importlib.reload(browser_cdp)


# ───────────────────────── what the exact route does ─────────────────────────


def test_searching_here_navigates_the_tab_instead_of_typing(exact):
    info = page.search_here("lofi beats", site="youtube")
    assert info["route"] == "cdp"
    assert exact.calls[0][0] == "goto"
    assert "youtube.com/results?search_query=lofi+beats" in exact.calls[0][1]
    assert exact.calls[0][2] is True, "the tab you are in, not a new one"


def test_results_come_from_the_dom_in_document_order(exact):
    rows = page.results(limit=3)
    assert [row["position"] for row in rows] == [1, 2, 3]
    assert rows[0]["title"].startswith("The best USB")
    assert rows[0]["url"] == "https://a.example/1"
    assert rows[0]["route"] == "cdp"


def test_the_nth_result_is_navigated_to_by_href(exact):
    info = page.open_result(position=2)
    assert ("open_nth", 2) in exact.calls
    assert info["opened"] == "Podcast mic shootout"
    assert info["changed"] is True
    assert "exactly" in info["route"]


def test_a_result_by_title_goes_through_the_same_route(exact):
    info = page.open_result(title="cheap mics")
    assert ("open_text", "cheap mics") in exact.calls
    assert info["opened"].startswith("Cheap mics")


def test_copying_the_selection_reads_the_real_string(exact, monkeypatch):
    """Not a Ctrl+C and a hopeful look at the clipboard."""
    written = {}
    monkeypatch.setattr(page.dc, "_set_clipboard",
                        lambda text: written.setdefault("text", text) is None or True)
    info = page.copy_here("that paragraph")
    assert info["route"] == "cdp"
    assert info["text"] == "the highlighted paragraph"
    assert written["text"] == "the highlighted paragraph"


def test_an_empty_selection_falls_back_to_the_keyboard(exact, monkeypatch):
    exact.selection_text = "   "
    called = {}
    monkeypatch.setattr(page.dc, "copy_from",
                        lambda window=None, select_all=False: called.setdefault("used", True)
                        is None or {"text": "via keys", "length": 8, "window": "Chrome"})
    info = page.copy_here("selection")
    assert info["text"] == "via keys", "nothing highlighted: Ctrl+C is still worth a try"


def test_pasting_switches_the_tab_and_fills_the_named_field(exact, monkeypatch):
    monkeypatch.setattr(page.dc, "_clipboard_text", lambda: "carried across")
    info = page.paste_there(field="Ask Gemini", tab="gemini", submit=True)
    assert ("switch", "gemini") in exact.calls
    assert ("fill", "Ask Gemini", "carried across", True) in exact.calls
    assert info["route"] == "cdp"


def test_pasting_nothing_is_refused_before_any_tab_is_switched(exact, monkeypatch):
    monkeypatch.setattr(page.dc, "_clipboard_text", lambda: "")
    with pytest.raises(page.PageError):
        page.paste_there(field="Ask Gemini", tab="gemini")
    assert exact.calls == [], "refused before touching the browser"


# ───────────────────────── the fallback is intact ─────────────────────────


def test_without_cdp_searching_here_uses_the_address_bar(absent, monkeypatch):
    import chrome_control

    used = {}

    def _bar(url):
        used["url"] = url
        return {"title": "x", "window": "y"}

    monkeypatch.setattr(chrome_control, "open_in_current_tab", _bar)
    monkeypatch.setattr(page, "here", lambda: {"window": None, "url": "", "site": "google",
                                               "title": ""})
    info = page.search_here("best mic")
    assert info["route"] == "accessibility"
    assert "google.com/search" in used["url"]


def test_without_cdp_results_come_from_the_accessibility_tree(absent, monkeypatch):
    import desktop_control as dc

    win = dc.Win(hwnd=3, title="x - Google Chrome", pid=1, process="chrome.exe")
    monkeypatch.setattr(page.chrome_control, "active_chrome", lambda: win)
    monkeypatch.setattr(dc, "find_clickables",
                        lambda needle="", **kwargs: [
                            {"element": object(), "label": "A result from the tree",
                             "kind": "hyperlink", "rank": 1, "order": 0}])
    rows = page.results()
    assert rows and rows[0]["title"] == "A result from the tree"
    assert rows[0].get("route") != "cdp"


# ───────────────────────── starting it ─────────────────────────


def test_the_launch_command_carries_the_two_flags_that_matter(monkeypatch, tmp_path):
    """Chrome 136+ refuses remote debugging on the default profile, so a separate
    --user-data-dir is not a nicety - it is the reason this works at all."""
    started = {}
    monkeypatch.setattr(browser_cdp, "launch", REAL_LAUNCH)
    monkeypatch.setattr(browser_cdp, "PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(browser_cdp, "available", lambda timeout=0.5: False)
    monkeypatch.setattr(browser_cdp, "ENABLED", True)
    import chrome_control

    monkeypatch.setattr(chrome_control, "find_chrome", lambda: r"C:\chrome.exe")
    monkeypatch.setattr(browser_cdp.subprocess, "Popen",
                        lambda args, **kwargs: started.setdefault("args", args))
    # Nothing ever answers the port, so it reports that honestly rather than hanging.
    with pytest.raises(browser_cdp.CdpError) as excinfo:
        browser_cdp.launch(wait=0.1)
    args = started["args"]
    assert f"--remote-debugging-port={browser_cdp.PORT}" in args
    assert any(a.startswith("--user-data-dir=") for a in args)
    assert "nothing answered" in str(excinfo.value)


def test_launching_when_it_is_already_up_is_not_a_second_chrome(exact):
    info = browser_cdp.launch()
    assert info["already"] is True


def test_without_chrome_installed_it_says_so(monkeypatch):
    monkeypatch.setattr(browser_cdp, "launch", REAL_LAUNCH)
    monkeypatch.setattr(browser_cdp, "available", lambda timeout=0.5: False)
    monkeypatch.setattr(browser_cdp, "ENABLED", True)
    import chrome_control

    monkeypatch.setattr(chrome_control, "find_chrome", lambda: None)
    with pytest.raises(browser_cdp.CdpError) as excinfo:
        browser_cdp.launch()
    assert "chrome.exe" in str(excinfo.value)


def test_the_status_says_which_route_is_in_use(exact):
    state = browser_cdp.status()
    assert state["available"] is True
    assert state["browser"] == "Chrome/141.0"
    assert state["tabs"] == 1


def test_the_tool_explains_the_off_state_and_how_to_turn_it_on(absent, monkeypatch):
    """Enabled but not running: the reply has to say which route is in use *and* how
    to get the other one. (Switched off entirely is a different sentence.)"""
    import tools_page

    monkeypatch.setattr(browser_cdp, "ENABLED", True)
    result = tools_page.precise_browser("status")
    assert result.ok is True
    assert "accessibility" in result.summary
    assert "precise browser control" in result.summary
