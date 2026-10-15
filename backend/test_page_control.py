"""
Acting on the page in front (v13.2).

Every case here came from the owner using it: "select the first profile" failed,
"select the first video" failed, "search a video by its name in current tab"
failed, "like/subscribe/comments" had no tool, and there was no way to carry text
from one tab to a field in another.

Nothing here touches a real Chrome: the accessibility layer, the keystrokes and the
clipboard are all faked, which is also how the *refusals* get tested - and the
refusals matter more than the happy paths, because "it said it did it and nothing
happened" is the failure being fixed.
"""

import pytest

import desktop_control as dc
import page_control as page
import youtube_control as yt


class FakeElement:
    def __init__(self, name):
        self.name = name
        self.invoked = 0
        self.focused = 0

    def invoke(self):
        self.invoked += 1

    def set_focus(self):
        self.focused += 1

    def get_value(self):
        return f"value of {self.name}"


def rows(*names, kind="hyperlink"):
    return [{"element": FakeElement(name), "label": name, "kind": kind, "rank": 1,
             "order": i, "seen": list(names)}
            for i, name in enumerate(names)]


@pytest.fixture
def tab(monkeypatch):
    """A Chrome window on a Google results page, with a controllable tree."""
    win = dc.Win(hwnd=5, title="best mic - Google Search - Google Chrome", pid=7,
                 process="chrome.exe")
    state = {
        "url": "https://www.google.com/search?q=best+mic",
        "keys": [], "typed": [], "clipboard": "", "switched": [],
        "tree": rows(
            "Images", "Videos", "News", "Sign in", "Settings",
            "The best USB microphones in 2026 - reviewed",
            "Podcast mic shootout: 12 mics compared",
            "Cheap mics that sound expensive",
            "3:21",
        ),
    }
    monkeypatch.setattr(yt.chrome_control, "active_chrome", lambda: win)
    monkeypatch.setattr(yt.chrome_control, "active_url", lambda *a, **k: state["url"])
    monkeypatch.setattr(yt.chrome_control, "tab_title", lambda *a, **k: win.title)
    monkeypatch.setattr(dc, "find_clickables",
                        lambda needle="", **kwargs: [r for r in state["tree"]
                                                    if needle.lower() in r["label"].lower()])
    monkeypatch.setattr(dc, "_focused_target", lambda name=None: win)
    monkeypatch.setattr(dc, "_send_keys", lambda combo: state["keys"].append(combo))
    monkeypatch.setattr(dc, "_write_text", lambda text: state["typed"].append(text))
    monkeypatch.setattr(dc, "_clipboard_text", lambda: state["clipboard"])
    def _set_clip(text):
        state["clipboard"] = text
        return True

    monkeypatch.setattr(dc, "_set_clipboard", _set_clip)
    def _switch(which):
        state["switched"].append(which)
        return {"title": f"{which} - tab", "which": which}

    monkeypatch.setattr(yt.chrome_control, "switch_tab", _switch)
    state["window"] = win
    return state


# ───────────────────────── searching in the tab you are in ─────────────────────────


def test_searching_here_uses_the_address_bar_not_a_new_tab(tab, monkeypatch):
    """`chrome.exe <url>` is always a new tab; Ctrl+L is the only way to replace
    the one you are in, which is what "in the current tab" asks for."""
    opened = {}

    def _go(url):
        opened["url"] = url
        return {"title": "x", "window": "y", "sameTab": True}

    monkeypatch.setattr(yt.chrome_control, "open_in_current_tab", _go)
    info = page.search_here("lofi beats", site="youtube")
    assert info["sameTab"] is True
    assert "youtube.com/results?search_query=lofi+beats" in opened["url"]


def test_with_no_site_named_it_searches_the_site_you_are_on(tab, monkeypatch):
    opened = {}

    def _go(url):
        opened["url"] = url
        return {}

    monkeypatch.setattr(yt.chrome_control, "open_in_current_tab", _go)
    page.search_here("best mic")
    assert "google.com/search" in opened["url"], "we were on Google"


def test_an_unknown_site_is_scoped_with_a_site_query(tab, monkeypatch):
    opened = {}

    def _go(url):
        opened["url"] = url
        return {}

    monkeypatch.setattr(yt.chrome_control, "open_in_current_tab", _go)
    page.search_here("pricing", site="notion.so")
    assert "site%3Anotion.so" in opened["url"] or "site:notion.so" in opened["url"]


def test_an_empty_query_is_a_question(tab):
    with pytest.raises(page.PageError):
        page.search_here("   ")


def test_the_address_bar_route_types_the_url_and_presses_enter(monkeypatch):
    win = dc.Win(hwnd=9, title="YouTube - Google Chrome", pid=3, process="chrome.exe")
    sent, typed = [], []
    monkeypatch.setattr(yt.chrome_control, "active_chrome", lambda: win)
    monkeypatch.setattr(yt.chrome_control, "tab_title", lambda *a, **k: win.title)
    monkeypatch.setattr(dc, "_focused_target", lambda name=None: win)
    monkeypatch.setattr(dc, "_send_keys", lambda combo: sent.append(combo))
    monkeypatch.setattr(dc, "_write_text", lambda text: typed.append(text))
    monkeypatch.setattr(yt.time, "sleep", lambda *_: None)
    info = yt.chrome_control.open_in_current_tab("youtube.com")
    assert sent[0] == "ctrl+l" and sent[-1] == "enter"
    assert typed == ["https://youtube.com"]
    assert info["sameTab"] is True


# ───────────────────────── picking the nth thing ─────────────────────────


def test_the_page_results_exclude_the_sites_own_furniture(tab):
    titles = [row["title"] for row in page.results()]
    assert len(titles) == 3
    assert all(word not in titles for word in ("Images", "Sign in", "3:21"))


def test_the_nth_result_is_the_nth_a_person_would_count(tab):
    info = page.open_result(position=2)
    assert info["position"] == 2
    assert info["opened"].startswith("Podcast mic shootout")
    assert "second result" in info["route"]


def test_a_result_can_be_picked_by_words_in_its_title(tab):
    assert "Cheap mics" in page.open_result(title="cheap mics")["opened"]


def test_asking_past_the_end_says_how_many_there_are(tab):
    with pytest.raises(page.PageError) as excinfo:
        page.open_result(position=9)
    assert "only 3" in str(excinfo.value)


def test_a_title_that_is_not_there_lists_what_is(tab):
    with pytest.raises(page.PageError) as excinfo:
        page.open_result(title="quantum kazoo")
    assert "Podcast mic shootout" in str(excinfo.value)


def test_on_youtube_the_video_list_is_used_instead(tab, monkeypatch):
    """YouTube's page is videos, and youtube_control already reads them - the two
    must not each have their own idea of what "the first one" means."""
    tab["url"] = "https://www.youtube.com/results?search_query=lofi"
    called = {}
    def _click(title=None, index=None):
        called["index"] = index
        return {"opened": "a video", "position": index or 1, "route": "clicked on the page"}

    monkeypatch.setattr(yt, "click_on_page", _click)
    page.open_result(position=3)
    assert called["index"] == 3


def test_select_nth_clicks_the_nth_profile_card(tab):
    """Chrome's profile picker: nothing to type, no fixed label - only "the first"."""
    tab["tree"] = rows("Add", "Settings", "Sudhansu's profile", "Work profile",
                       kind="listitem")
    info = page.click_nth("profile", position=2)
    assert info["clicked"] == "Work profile"
    assert info["position"] == 2


def test_select_nth_says_what_it_can_see_when_there_is_nothing(tab):
    tab["tree"] = rows("OK", "Cancel", kind="button")
    with pytest.raises(page.PageError) as excinfo:
        page.click_nth("profile", position=1)
    message = str(excinfo.value)
    assert "profile" in message


def test_select_nth_past_the_end_names_the_ones_there_are(tab):
    tab["tree"] = rows("Sudhansu's profile", "Work profile", kind="listitem")
    with pytest.raises(page.PageError) as excinfo:
        page.click_nth("profile", position=5)
    assert "only 2" in str(excinfo.value)
    assert "Work profile" in str(excinfo.value)


def test_an_element_that_will_not_click_is_reported_not_claimed(tab, monkeypatch):
    class Stubborn(FakeElement):
        def invoke(self):
            raise RuntimeError("no")

    tab["tree"] = [{"element": Stubborn("A long enough result title"),
                    "label": "A long enough result title", "kind": "hyperlink",
                    "rank": 1, "order": 0}]
    with pytest.raises(page.PageError) as excinfo:
        page.open_result(position=1)
    assert "would not take a click" in str(excinfo.value)


# ───────────────────────── YouTube's page buttons ─────────────────────────


@pytest.fixture
def watching(tab):
    tab["url"] = "https://www.youtube.com/watch?v=abc"
    tab["tree"] = rows("Like this video along with 12,345 other people",
                       "Dislike this video", "Subscribe to Lofi Girl", "Share", "Save",
                       "Comments", kind="button")
    return tab


@pytest.mark.parametrize("said,label", [
    ("like this video", "Like this video along with"),
    ("subscribe", "Subscribe to"),
    ("subscribe to this channel", "Subscribe to"),
    ("share it", "Share"),
    ("save to a playlist", "Save"),
    ("dislike", "Dislike this video"),
])
def test_the_buttons_around_a_video(watching, said, label):
    info = yt.page_action(said)
    assert label.split()[0].lower() in info["how"].lower()


def test_open_the_comments_scrolls_to_them(watching, monkeypatch):
    """YouTube builds the comment section only when it is in view, so "open the
    comments" is a scroll before it is a click."""
    scrolled = []
    def _scroll(direction, amount=1, window=None):
        scrolled.append((direction, amount))
        return {}

    monkeypatch.setattr(dc, "scroll", _scroll)
    info = yt.page_action("open the comments")
    assert scrolled and scrolled[0][0] == "down"
    assert info["action"] == "comments"


def test_a_button_that_is_not_there_lists_what_is(watching):
    watching["tree"] = rows("Share", "Save", kind="button")
    with pytest.raises(yt.YouTubeError) as excinfo:
        yt.page_action("like")
    assert "Share" in str(excinfo.value)


def test_an_action_youtube_does_not_have_is_admitted_to(watching):
    with pytest.raises(yt.YouTubeError) as excinfo:
        yt.page_action("send a postcard")
    assert "I can:" in str(excinfo.value)


# ───────────────────────── text between places ─────────────────────────


def test_copying_the_selection_goes_through_the_app(tab, monkeypatch):
    monkeypatch.setattr(dc, "copy_from",
                        lambda window=None, select_all=False: {"text": "some selected words",
                                                               "length": 18, "window": "Chrome",
                                                               "app": "Chrome",
                                                               "selectedAll": select_all})
    info = page.copy_here("selection")
    assert info["text"] == "some selected words"
    assert info["selectedAll"] is False


def test_copying_everything_selects_all_first(tab, monkeypatch):
    seen = {}
    def _copy(window=None, select_all=False):
        seen["all"] = select_all
        return {"text": "the whole page", "length": 14, "window": "Chrome"}

    monkeypatch.setattr(dc, "copy_from", _copy)
    page.copy_here("all")
    assert seen["all"] is True


def test_copying_a_named_field_reads_it_without_moving_the_caret(tab):
    tab["tree"] = rows("Ask Gemini", kind="edit")
    info = page.copy_here("Ask Gemini")
    assert info["text"] == "value of Ask Gemini"
    assert tab["clipboard"] == "value of Ask Gemini"


def test_copying_from_something_that_is_not_there_is_refused(tab):
    with pytest.raises(page.PageError):
        page.copy_here("the nonexistent box")


def test_pasting_switches_tab_clicks_the_field_and_pastes(tab):
    tab["clipboard"] = "carried across"
    tab["tree"] = rows("Ask Gemini", kind="edit")
    info = page.paste_there(field="Ask Gemini", tab="gemini", submit=True)
    assert tab["switched"] == ["gemini"]
    assert info["field"] == "Ask Gemini"
    assert tab["keys"] == ["ctrl+v", "enter"]


def test_pasting_with_nothing_on_the_clipboard_says_so(tab):
    tab["clipboard"] = "   "
    with pytest.raises(page.PageError) as excinfo:
        page.paste_there()
    assert "nothing on the clipboard" in str(excinfo.value)


def test_a_field_that_does_not_exist_is_refused_rather_than_typed_into(tab):
    """Typing into the wrong field is worse than not typing."""
    tab["clipboard"] = "something"
    with pytest.raises(page.PageError) as excinfo:
        page.paste_there(field="Prompt")
    assert "can't find a 'Prompt' field" in str(excinfo.value)
    assert tab["keys"] == []


def test_a_tab_that_cannot_be_switched_to_stops_the_turn(tab, monkeypatch):
    def _no(which):
        raise RuntimeError("no tab matching 'gemini'")

    monkeypatch.setattr(yt.chrome_control, "switch_tab", _no)
    with pytest.raises(page.PageError) as excinfo:
        page.paste_there(field="Ask Gemini", tab="gemini")
    assert "couldn't switch" in str(excinfo.value)
    assert tab["keys"] == []


def test_explicit_text_is_typed_instead_of_the_clipboard(tab):
    tab["tree"] = rows("Ask Gemini", kind="edit")
    page.paste_there(field="Ask Gemini", text="hello there")
    assert tab["typed"] == ["hello there"]
    assert "ctrl+v" not in tab["keys"]


# ───────────────────────── without Chrome ─────────────────────────


def test_every_page_tool_says_chrome_is_closed_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(yt.chrome_control, "active_chrome", lambda: None)
    for call in (lambda: page.search_here("x"), lambda: page.open_result(position=1),
                 lambda: page.results(), lambda: page.paste_there(field="x")):
        with pytest.raises(page.PageError) as excinfo:
            call()
        assert "Chrome isn't open" in str(excinfo.value)


def test_a_click_that_changed_nothing_says_so(tab):
    """The whole point of this module: a claim that can be checked. The tab title
    after the click is the evidence, and its absence is reported rather than
    dressed up as success."""
    info = page.open_result(position=1)
    assert info["changed"] is False, "the fake tab title never changes"
    assert "has not changed yet" in info["route"]


def test_a_click_that_moved_the_page_says_that_instead(tab, monkeypatch):
    titles = iter(["best mic - Google Search - Google Chrome",
                   "The best USB microphones in 2026 - Google Chrome"])
    monkeypatch.setattr(yt.chrome_control, "tab_title", lambda *a, **k: next(titles, "x"))
    info = page.open_result(position=1)
    assert info["changed"] is True
    assert "has not changed" not in info["route"]
