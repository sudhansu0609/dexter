"""
Dexter's browser (v6, B1): target normalisation, fast-path routing, and
honest failures when the window cannot be driven.
"""

import tools_browser  # noqa: F401  - registers the browser tools
import tools_code  # noqa: F401  - registers the code tools (routing test below)
import tools_studio  # noqa: F401
from browser import BrowserError, normalize_target
from router import fast_path


def test_targets_become_urls_sites_or_searches():
    assert normalize_target("github.com") == "https://github.com"
    assert normalize_target("https://example.com/a?b=1") == "https://example.com/a?b=1"
    assert normalize_target("youtube") == "https://www.youtube.com/"
    assert normalize_target("YouTube Studio") == "https://studio.youtube.com/"
    assert normalize_target("youtube bhangarh fort").startswith("https://www.youtube.com/results?search_query=bhangarh")
    assert normalize_target("best mic for voiceover").startswith("https://www.google.com/search?q=best")


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


def test_browser_failures_are_reported_not_raised(monkeypatch):
    def boom(*args, **kwargs):
        raise BrowserError("Chrome was not found.")

    monkeypatch.setattr(tools_browser.browser, "open", boom)
    result = tools_browser.browser_open("github.com")
    assert result.ok is False and "Chrome" in result.summary
