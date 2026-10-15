"""
Opening a specific video (v13).

Two routes, and which one was taken matters: clicking a link the owner can see is
a different claim from searching YouTube and opening whatever came back first. So
the tests check the route as well as the result, and nothing here touches the
network or a real Chrome - `search` is fed a canned results page and the UIA layer
is faked.
"""

import json

import pytest

import youtube_control as yt


class FakeElement:
    def __init__(self, name):
        self.name = name
        self.invoked = 0

    def invoke(self):
        self.invoked += 1


def rows(*names):
    """What `desktop_control.find_clickables` hands back."""
    return [{"element": FakeElement(name), "label": name, "kind": "hyperlink",
             "rank": 1, "order": i, "seen": list(names)}
            for i, name in enumerate(names)]


@pytest.fixture
def page(monkeypatch):
    """A YouTube results page in front, with three videos and the usual chrome."""
    import desktop_control as dc

    win = dc.Win(hwnd=7, title="lofi - YouTube - Google Chrome", pid=9, process="chrome.exe")
    monkeypatch.setattr(yt.chrome_control, "active_chrome", lambda: win)
    monkeypatch.setattr(yt.chrome_control, "active_url",
                        lambda *a, **k: "https://www.youtube.com/results?search_query=lofi+beats")
    monkeypatch.setattr(yt.chrome_control, "tab_title", lambda *a, **k: "lofi - YouTube")
    found = rows(
        "Home", "Shorts", "Subscriptions",
        "lofi hip hop radio - beats to relax to by Lofi Girl 2 hours ago",
        "Deep focus ambient mix by Ambient Co 45 minutes",
        "1 A.M Study Session by Lofi Girl 1:01:14",
        "3:21",
    )
    monkeypatch.setattr(dc, "find_clickables", lambda *a, **k: found)
    monkeypatch.setattr(dc, "_focused_target", lambda name=None: win)
    return found


# ───────────────────────── what is on the page ─────────────────────────


def test_navigation_and_durations_are_not_videos(page):
    titles = [video["title"] for video in yt.page_videos()]
    assert len(titles) == 3
    assert all("Home" != title for title in titles)
    assert not any(title == "3:21" for title in titles)


def test_the_nth_video_is_the_nth_one_a_person_would_count(page):
    result = yt.click_on_page(index=2)
    assert result["position"] == 2
    assert result["opened"].startswith("Deep focus")
    assert result["route"] == "clicked on the page"


def test_a_title_picks_the_matching_video_wherever_it_is(page):
    result = yt.click_on_page(title="study session")
    assert "1 A.M Study Session" in result["opened"]


def test_asking_for_a_title_that_is_not_there_lists_what_is(page):
    with pytest.raises(yt.YouTubeError) as excinfo:
        yt.click_on_page(title="wgpu rendering")
    message = str(excinfo.value)
    assert "lofi hip hop radio" in message, "say what is actually on the page"


def test_asking_past_the_end_says_how_many_there_are(page):
    with pytest.raises(yt.YouTubeError) as excinfo:
        yt.click_on_page(index=9)
    assert "only 3" in str(excinfo.value)


def test_the_search_terms_of_the_page_are_read_from_its_url(page):
    assert yt.page_query() == "lofi beats"


# ───────────────────────── the search fallback ─────────────────────────

SAMPLE = (
    '<html><script>var ytInitialData = '
    + json.dumps({
        "contents": {"items": [
            {"videoRenderer": {"videoId": "aaaaaaaaaaa",
                               "title": {"runs": [{"text": "First result"}]},
                               "ownerText": {"runs": [{"text": "Channel One"}]},
                               "lengthText": {"simpleText": "3:21"}}},
            {"videoRenderer": {"videoId": "bbbbbbbbbbb",
                               "title": {"simpleText": "Second result"},
                               "lengthText": {"simpleText": "1:00:00"}}},
            # The same video again, in another shelf: it must not be counted twice.
            {"videoRenderer": {"videoId": "aaaaaaaaaaa",
                               "title": {"runs": [{"text": "First result"}]}}},
        ]},
    })
    + ';</script></html>'
)


class FakeResponse:
    status_code = 200

    def __init__(self, text):
        self.text = text


@pytest.fixture
def searching(monkeypatch):
    import requests

    calls = []

    def _get(url, **kwargs):
        calls.append(url)
        return FakeResponse(SAMPLE)

    monkeypatch.setattr(requests, "get", _get)
    return calls


def test_search_reads_the_results_page_in_order(searching):
    found = yt.search("lofi")
    assert [row["videoId"] for row in found] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert found[0]["title"] == "First result"
    assert found[0]["channel"] == "Channel One"
    assert found[0]["url"].endswith("watch?v=aaaaaaaaaaa")
    assert "search_query=lofi" in searching[0]


def test_a_page_shape_it_cannot_parse_still_yields_the_video_ids(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: FakeResponse('{"videoId":"ccccccccccc"} junk'))
    found = yt.search("anything")
    assert found[0]["videoId"] == "ccccccccccc"


def test_with_nothing_on_screen_it_searches_and_opens_the_link(monkeypatch, searching):
    monkeypatch.setattr(yt, "on_youtube", lambda: False)
    opened = {}

    def _open(url, **kwargs):
        opened["url"] = url
        return {"url": url, "title": "First result", "window": "Chrome"}

    monkeypatch.setattr(yt.chrome_control, "open_url", _open)
    result = yt.open_video(query="lofi")
    assert opened["url"].endswith("watch?v=aaaaaaaaaaa")
    assert result["route"].startswith("searched")


def test_a_title_falls_back_to_search_when_the_page_does_not_have_it(
    page, searching, monkeypatch
):
    opened = {}

    def _open(url, **kwargs):
        opened["url"] = url
        return {"url": url, "title": "First result", "window": "Chrome"}

    monkeypatch.setattr(yt.chrome_control, "open_url", _open)
    result = yt.open_video(title="First result")
    assert opened["url"].endswith("watch?v=aaaaaaaaaaa")
    assert result["note"], "it should say why the page route did not work"


def test_an_ordinal_with_nothing_on_screen_is_refused_rather_than_guessed(monkeypatch):
    """"Play the third one" is about a page. With no page, saying so beats
    opening the third result of a search the user never asked for."""
    import desktop_control as dc

    monkeypatch.setattr(yt, "on_youtube", lambda: True)
    monkeypatch.setattr(yt, "page_videos", lambda *a, **k: [])
    with pytest.raises(yt.YouTubeError):
        yt.open_video(index=3)


def test_nothing_to_go_on_is_a_question_not_a_search():
    with pytest.raises(yt.YouTubeError) as excinfo:
        yt.open_video()
    assert "which video" in str(excinfo.value).lower()


# ───────────────────────── playback ─────────────────────────


@pytest.mark.parametrize("spoken,action", [
    ("pause the video", "pause"), ("play", "play"), ("skip the ad", "skip ad"),
    ("next video", "next"), ("turn on captions", "captions"),
    ("make it full screen", "fullscreen"), ("mute", "mute"), ("faster", "faster"),
])
def test_spoken_playback_actions_map_onto_the_player(spoken, action):
    assert yt.normalize_action(spoken) == action


def test_a_player_button_is_clicked_rather_than_a_key_being_guessed(page, monkeypatch):
    import desktop_control as dc

    button = rows("Pause (k)")
    monkeypatch.setattr(dc, "find_clickables", lambda *a, **k: button)
    sent = []
    monkeypatch.setattr(dc, "_send_keys", lambda combo: sent.append(combo))
    result = yt.control("pause")
    assert "clicked" in result["how"]
    assert sent == [], "a key typed into the search box is not a pause"


def test_without_the_button_the_keyboard_shortcut_is_used(page, monkeypatch):
    import desktop_control as dc

    monkeypatch.setattr(dc, "find_clickables", lambda *a, **k: [])
    sent = []
    monkeypatch.setattr(dc, "_send_keys", lambda combo: sent.append(combo))
    result = yt.control("pause")
    assert sent == ["k"]
    assert "pressed k" in result["how"]


def test_an_action_with_no_button_and_no_key_admits_it(page, monkeypatch):
    import desktop_control as dc

    monkeypatch.setattr(dc, "find_clickables", lambda *a, **k: [])
    with pytest.raises(yt.YouTubeError):
        yt.control("like")


def test_an_unknown_action_lists_the_real_ones(page):
    with pytest.raises(yt.YouTubeError) as excinfo:
        yt.control("do a barrel roll")
    assert "I can:" in str(excinfo.value)


# ───────────────────────── grid, OCR, and in-page search ─────────────────────────


def test_click_on_page_by_grid_row_and_col(monkeypatch):
    """Picking a video by grid row and column."""
    import desktop_control as dc

    win = dc.Win(hwnd=7, title="YouTube - Google Chrome", pid=9, process="chrome.exe")
    monkeypatch.setattr(yt.chrome_control, "active_chrome", lambda: win)
    monkeypatch.setattr(yt.chrome_control, "tab_title", lambda *a, **k: "YouTube")
    monkeypatch.setattr(yt, "on_youtube", lambda: True)
    monkeypatch.setattr(dc, "_focused_target", lambda name=None: win)

    grid_cards = [
        {"position": 1, "row": 1, "col": 1, "title": "Video R1C1", "cx": 200, "cy": 300, "route": "screen_ocr"},
        {"position": 2, "row": 1, "col": 2, "title": "Video R1C2", "cx": 600, "cy": 300, "route": "screen_ocr"},
        {"position": 3, "row": 2, "col": 1, "title": "Video R2C1", "cx": 200, "cy": 700, "route": "screen_ocr"},
    ]
    monkeypatch.setattr(yt, "page_videos", lambda *a, **k: grid_cards)

    clicked = []
    import mouse_input
    monkeypatch.setattr(mouse_input, "available", lambda: True)
    monkeypatch.setattr(mouse_input, "move_to", lambda x, y: clicked.append(("move", x, y)))
    monkeypatch.setattr(mouse_input, "click", lambda btn="left", **kw: clicked.append(("click", btn)))

    res = yt.click_on_page(row=1, col=2)
    assert res["opened"] == "Video R1C2"
    assert res["row"] == 1
    assert res["col"] == 2
    assert ("move", 600, 300) in clicked
    assert ("click", "left") in clicked


def test_screen_ocr_video_detection(monkeypatch):
    """Verify video cards and grid layout reconstructed from screen OCR."""
    import desktop_control as dc
    import screen_text

    win = dc.Win(hwnd=7, title="YouTube - Google Chrome", pid=9, process="chrome.exe")
    monkeypatch.setattr(yt.chrome_control, "active_chrome", lambda: win)
    monkeypatch.setattr(screen_text, "window_box", lambda hwnd: {"top": 0, "left": 0, "width": 1920, "height": 1080})
    monkeypatch.setattr(screen_text, "availability", lambda: {"ready": True})

    # Words simulating two video cards on Row 1 (top ~300) and one on Row 2 (top ~600)
    fake_words = [
        screen_text.Word(text="Home", left=20, top=120, right=60, bottom=140),
        # Card 1 (Row 1, Col 1)
        screen_text.Word(text="12:34", left=380, top=280, right=420, bottom=295),
        screen_text.Word(text="Lofi Hip Hop Radio", left=100, top=310, right=300, bottom=330),
        screen_text.Word(text="Lofi Girl", left=100, top=335, right=180, bottom=350),
        screen_text.Word(text="1M views 2 days ago", left=100, top=355, right=240, bottom=370),
        # Card 2 (Row 1, Col 2)
        screen_text.Word(text="08:15", left=880, top=280, right=920, bottom=295),
        screen_text.Word(text="Deep Focus Ambient Music", left=600, top=310, right=850, bottom=330),
        screen_text.Word(text="Ambient Co", left=600, top=335, right=700, bottom=350),
        # Card 3 (Row 2, Col 1)
        screen_text.Word(text="Rust WGPU Tutorial from Scratch", left=100, top=610, right=350, bottom=630),
    ]
    monkeypatch.setattr(screen_text, "read", lambda **kw: {"words": fake_words})

    vids = yt._detect_videos_from_screen(win)
    assert len(vids) >= 2
    assert vids[0]["row"] == 1
    assert vids[0]["col"] == 1
    assert "Lofi Hip Hop" in vids[0]["title"]
    assert vids[1]["row"] == 1
    assert vids[1]["col"] == 2
    assert "Deep Focus" in vids[1]["title"]
    assert vids[0]["position"] == 1
    assert vids[1]["position"] == 2


def test_search_on_page(monkeypatch):
    """In-page YouTube search navigates the current tab without opening a new window."""
    import desktop_control as dc

    win = dc.Win(hwnd=7, title="YouTube - Google Chrome", pid=9, process="chrome.exe")
    monkeypatch.setattr(yt.chrome_control, "active_chrome", lambda: win)

    opened = []
    monkeypatch.setattr(yt.chrome_control, "open_in_current_tab", lambda url: opened.append(url) or {"url": url, "title": "search"})
    monkeypatch.setattr(yt, "_cdp_available", lambda: False)

    res = yt.search_on_page("rust game engine")
    assert len(opened) == 1
    assert "search_query=rust+game+engine" in opened[0]
    assert res["searched"] == "rust game engine"


# ───────────────────────── the CDP route is opt-in ─────────────────────────


def test_cdp_route_is_off_by_default(monkeypatch):
    """The owner drives their own Chrome unless they explicitly switch on the
    debug browser; a stray debuggable Chrome must not hijack detection."""
    import browser_cdp

    monkeypatch.setattr(browser_cdp, "_ACTIVE_MODE", False, raising=False)
    assert yt._cdp_route_enabled() is False


def test_page_videos_skips_cdp_when_the_route_is_off(monkeypatch):
    import desktop_control as dc

    win = dc.Win(hwnd=7, title="YouTube - Google Chrome", pid=9, process="chrome.exe")
    monkeypatch.setattr(yt.chrome_control, "active_chrome", lambda *a, **k: win)
    monkeypatch.setattr(yt, "_cdp_route_enabled", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("CDP must not be consulted when the route is off")

    monkeypatch.setattr(yt, "_detect_videos_from_cdp", _boom)
    monkeypatch.setattr(
        yt, "_detect_videos_from_screen",
        lambda *a, **k: [{"position": 1, "row": 1, "col": 1, "title": "From OCR",
                          "route": "screen_ocr"}],
    )
    vids = yt.page_videos()
    assert vids and vids[0]["title"] == "From OCR"


# ───────────────────────── writing a comment ─────────────────────────


@pytest.fixture
def video_page(monkeypatch):
    """A YouTube *video* page in front, with the desktop layer faked."""
    import desktop_control as dc

    win = dc.Win(hwnd=7, title="Great Video - YouTube - Google Chrome", pid=9,
                 process="chrome.exe")
    monkeypatch.setattr(yt.chrome_control, "active_chrome", lambda *a, **k: win)
    monkeypatch.setattr(yt, "on_youtube", lambda: True)
    monkeypatch.setattr(dc, "scroll", lambda *a, **k: None)
    monkeypatch.setattr(dc, "_focused_target", lambda name=None: win)
    return win


def test_post_comment_types_but_does_not_post(video_page, monkeypatch):
    box = FakeElement("Add a comment…")
    monkeypatch.setattr(yt, "_find_named", lambda names, win: (box, "Add a comment…", "uia"))
    typed = []
    monkeypatch.setattr(
        yt.chrome_control, "type_text",
        lambda text, submit=False: typed.append((text, submit)) or {"typed": text},
    )

    res = yt.post_comment(text="great video", post=False)
    assert res["pending"] is True
    assert res["typed"] == "great video"
    assert typed == [("great video", False)], "the comment is typed, not submitted"
    assert box.invoked == 1, "the comment box is clicked to give it focus"
    assert "posted" not in res


def test_post_comment_publishes_only_on_post_true(video_page, monkeypatch):
    submit = FakeElement("Comment")
    monkeypatch.setattr(yt, "_find_named", lambda names, win: (submit, "Comment", "uia"))
    res = yt.post_comment(post=True)
    assert res["posted"] is True
    assert submit.invoked == 1


def test_post_comment_without_text_asks_what_to_say(video_page):
    with pytest.raises(yt.YouTubeError):
        yt.post_comment()


def test_post_comment_missing_box_reports_it(video_page, monkeypatch):
    monkeypatch.setattr(yt, "_find_named", lambda names, win: None)
    with pytest.raises(yt.YouTubeError) as excinfo:
        yt.post_comment(text="hello there")
    assert "comment box" in str(excinfo.value).lower()

