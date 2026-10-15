"""
Answering "is it open?" instead of listing things (v13.8).

"Dexter is not able to check if Chrome is open or not, neither can it confirm if a tab
or a website is opened or not, it won't be able to verify if a video is present with a
certain title in an opened YouTube."

Every piece existed - windows, processes, Chrome's own tab list over CDP, the page's
items. What was missing was a tool whose *answer* is yes or no: there were tools that
listed and tools that acted, and the model had to cross the gap by inference, which is
exactly where a confident wrong answer comes from.

So the properties worth testing are about the quality of the answer:

* a **yes** has evidence behind it, and weak evidence does not count as yes;
* a **no** is distinguished from **"I cannot tell from here"**, which is a real answer
  and the one that tells the owner how to get a better one;
* with the exact route on, every tab is read; without it, only the front tab is
  visible and that limit is stated rather than papered over.
"""

from typing import Any, Dict, List

import pytest

import desktop_control as dc
import verify


class FakeSession:
    """A debuggable Chrome, faked: the tabs it has open."""

    def __init__(self, tabs):
        self._tabs = tabs

    def tabs(self):
        return self._tabs

    def where(self):
        return self._tabs[0] if self._tabs else {"url": "", "title": ""}


@pytest.fixture
def exact(monkeypatch):
    """The exact route, up, with two tabs open."""
    import browser_cdp

    session = FakeSession([
        {"position": 1, "title": "lofi hip hop radio - YouTube",
         "url": "https://www.youtube.com/results?search_query=lofi"},
        {"position": 2, "title": "Inbox (3) - Gmail", "url": "https://mail.google.com/u/0"},
    ])
    monkeypatch.setattr(verify.page, "precise", lambda: True)
    monkeypatch.setattr(browser_cdp, "session", session)
    return session


@pytest.fixture
def no_cdp(monkeypatch):
    monkeypatch.setattr(verify.page, "precise", lambda: False)


def _win(title, process="chrome.exe", pid=100):
    return dc.Win(hwnd=pid, title=title, pid=pid, process=process)


# ───────────────────────── is an application open ─────────────────────────


def test_a_window_is_what_open_means(monkeypatch):
    monkeypatch.setattr(dc, "find_windows",
                        lambda name: [_win("Google Chrome")] if "chrome" in name else [])
    state = verify.app_open("chrome")
    assert state["verdict"] is True
    assert state["how"] == "windows"
    assert state["windows"][0]["title"] == "Google Chrome"


def test_running_with_no_window_is_its_own_answer(monkeypatch):
    """Minimised to the tray is neither "open" nor "not running", and saying which is
    the entire point of this module."""
    monkeypatch.setattr(dc, "find_windows", lambda name: [])
    monkeypatch.setattr(dc, "find_processes",
                        lambda name: [{"process": "spotify.exe", "pid": 7, "score": 100,
                                       "why": "image name"}])
    state = verify.app_open("spotify")
    assert state["verdict"] is True
    assert state["how"] == "processes"
    assert state["windows"] == []


def test_the_name_appearing_in_a_command_line_is_not_evidence(monkeypatch):
    """The bug this caught on the way in: `find_processes` also matches the app's name
    anywhere in a process's command line (score 50), which is right for "stop the thing
    I named" and far too loose for a question. Asking "is notepad open?" from a shell
    whose own command line contained the word answered **yes, three times over**."""
    monkeypatch.setattr(dc, "find_windows", lambda name: [])
    monkeypatch.setattr(dc, "find_processes",
                        lambda name: [{"process": "bash.exe", "pid": 9, "score": 50,
                                       "why": "command line"}])
    assert verify.app_open("notepad")["verdict"] is False


def test_nothing_open_is_a_plain_no(monkeypatch):
    monkeypatch.setattr(dc, "find_windows", lambda name: [])
    monkeypatch.setattr(dc, "find_processes", lambda name: [])
    assert verify.app_open("qwertyapp")["verdict"] is False


def test_a_browser_need_not_be_named(monkeypatch):
    monkeypatch.setattr(dc, "find_windows",
                        lambda name: [_win("Google Chrome")] if name == "chrome" else [])
    assert verify.app_open("a browser")["verdict"] is True


# ───────────────────── is a site open in a tab ─────────────────────


def test_every_tab_is_read_on_the_exact_route(exact):
    state = verify.site_open("youtube")
    assert state["verdict"] is True
    assert state["how"] == "cdp"
    assert state["tabs"][0]["position"] == 1
    assert state["checked"] == 2


def test_a_site_that_is_not_there_is_a_no_not_a_maybe(exact):
    state = verify.site_open("github")
    assert state["verdict"] is False
    assert state["checked"] == 2, "and it says how many tabs it looked at"


def test_a_url_is_matched_as_well_as_a_name(exact):
    assert verify.site_open("mail.google.com")["verdict"] is True
    assert verify.site_open("gmail")["verdict"] is True


def test_without_the_exact_route_the_front_tab_is_all_there_is(no_cdp, monkeypatch):
    """And that has to be said. Windows shows one title per browser window, so a "no"
    would be a guess - the honest answer is "I cannot tell", plus how to fix it."""
    monkeypatch.setattr(dc, "find_windows",
                        lambda name: [_win("Inbox (3) - Gmail - Google Chrome")]
                        if name == "chrome" else [])
    found = verify.site_open("gmail")
    assert found["verdict"] is True, "the front tab is visible, and that is enough"
    assert found["how"] == "windows"

    unknown = verify.site_open("github")
    assert unknown["verdict"] is None, "not False: it might be in another tab"
    assert "without exact browser control" in unknown["why"]


def test_with_no_browser_at_all_it_is_a_definite_no(no_cdp, monkeypatch):
    monkeypatch.setattr(dc, "find_windows", lambda name: [])
    state = verify.site_open("youtube")
    assert state["verdict"] is False, "no browser is a fact, not an uncertainty"
    assert "no browser window" in state["why"]


# ───────────────────── is it on the page in front ─────────────────────


PAGE = [
    {"position": 1, "title": "1 A.M Study Session 📚 [lofi hip hop]", "url": "https://a/1",
     "route": "cdp"},
    {"position": 2, "title": "lofi hip hop radio 📚 beats to relax/study to",
     "url": "https://a/2", "route": "cdp"},
    {"position": 3, "title": "Rust in 100 Seconds", "url": "https://a/3", "route": "cdp"},
]


@pytest.fixture
def a_page(monkeypatch):
    monkeypatch.setattr(verify.page, "results", lambda **kwargs: PAGE)
    monkeypatch.setattr(verify.page, "precise", lambda: True)


def test_a_title_that_is_there_comes_back_with_its_number(a_page):
    state = verify.on_page("lofi hip hop")
    assert state["verdict"] is True
    assert state["matches"][0]["position"] == 1
    assert state["checked"] == 3


def test_half_remembered_words_are_enough(a_page):
    """Nobody types a YouTube title exactly. Every significant word has to appear,
    which is strict enough to be useful and loose enough to be usable."""
    assert verify.on_page("beats to relax")["verdict"] is True
    assert verify.on_page("study session")["verdict"] is True
    assert verify.on_page("rust 100 seconds")["verdict"] is True


def test_something_that_is_not_there_is_a_no_with_what_was_seen(a_page):
    state = verify.on_page("quantum tractors")
    assert state["verdict"] is False
    assert state["titles"], "a no is more useful with the list it was drawn from"


def test_an_unreadable_page_says_so_rather_than_no(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("no Chrome window is in front")

    monkeypatch.setattr(verify.page, "results", boom)
    state = verify.on_page("anything")
    assert state["verdict"] is None
    assert "no Chrome window" in state["why"]


def test_the_route_is_reported_so_a_vague_answer_can_be_improved(a_page):
    assert verify.on_page("lofi")["how"] == "cdp"


# ───────────────────────── the matcher itself ─────────────────────────


@pytest.mark.parametrize("title,asked,expected", [
    ("lofi hip hop radio 📚 beats to relax/study to", "lofi hip hop", True),
    ("lofi hip hop radio", "the lofi hip hop one", False),   # "one" is not in the title
    ("Rust in 100 Seconds", "rust seconds", True),
    ("Rust in 100 Seconds", "python", False),
    # Part of a word counts: saying "usb mic" and finding "USB microphones" is the
    # point, not an accident. The risk is a short word inside a longer one, which is
    # why a word has to be at least three characters to count at all.
    ("Best USB microphones 2026", "usb mic", True),
    ("Best USB microphones 2026", "usb microphones", True),
    ("anything", "", False),
])
def test_what_counts_as_a_match(title, asked, expected):
    assert verify.looks_like(title, asked) is expected


# ───────────────────────── the tools on top ─────────────────────────


def test_the_reply_says_how_it_knows(exact, monkeypatch):
    import tools_verify

    monkeypatch.setattr(dc, "find_windows",
                        lambda name: [_win("Google Chrome")] if "chrome" in name else [])
    assert "from the open windows" in tools_verify.is_app_open("chrome").summary
    assert "reading Chrome's own tabs" in tools_verify.is_site_open("youtube").summary


def test_an_uncertain_answer_offers_the_way_to_be_certain(no_cdp, monkeypatch):
    import tools_verify

    monkeypatch.setattr(dc, "find_windows",
                        lambda name: [_win("Some page - Google Chrome")]
                        if name == "chrome" else [])
    summary = tools_verify.is_site_open("github").summary
    assert "can't tell" in summary
    assert "precise browser" in summary, "the answer includes how to get a real one"


def test_the_page_check_names_the_item_so_it_can_then_be_opened(a_page):
    import tools_verify

    result = tools_verify.is_on_page("lofi hip hop", kind="video")
    assert result.ok is True
    assert "number 1" in result.summary


def test_every_verification_tool_is_registered_and_routed():
    """A rule that names a tool which is not registered is a dead turn."""
    import tools_verify  # noqa: F401
    from router import fast_path
    from tool_registry import get_tool

    for phrase, tool in (
        ("is chrome open", "is_app_open"),
        ("is youtube open", "is_site_open"),
        ("is there a video called lofi hip hop on this page", "is_on_page"),
        ("what page am I on", "what_page_is_this"),
    ):
        routed = fast_path(phrase)
        assert routed is not None and routed[0] == tool, f"{phrase!r} -> {routed}"
        assert get_tool(tool) is not None
