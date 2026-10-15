"""
Clicking words on the screen (v13.8).

"It shall be able to click on a text on the browser or screen without the precise
browser as well."

The reason this module exists is a measurement, not a preference. On the owner's Chrome:

    UIA on the window            35 controls - Minimize, Back, Reload, Bookmark, the
                                 profile button - and nothing from the page
    after the WM_GETOBJECT poke  unchanged (Chrome answered; the tree stayed empty)
    walking the render widget    0 named nodes
    MSAA through oleacc          0 named nodes
    system screen-reader flag    already on, and still nothing

Modern Chrome keeps web content out of Windows accessibility unless it was launched with
`--force-renderer-accessibility`. So for page content - and for Chrome's profile picker,
which is a page - the pixels are the only route that does not need the debug port.

Verified live before these tests were written: read 27 pieces of text from a Chrome
window in 2.2 s, found "Learn more" at score 1.0, clicked it, and the tab's title
changed. No fake can prove that, so what is tested here is everything around it: what
counts as a match, that a weak match never gets clicked, and that the answer always says
what it saw.
"""

import sys
import time
import types

import numpy as np
import pytest

import screen_text


class FakeEngine:
    """Stands in for rapidocr: a fixed reading of a screen."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        return [[box, text, str(score)] for box, text, score in self.rows], 0.1


def boxed(left, top, right, bottom):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


SCREEN = [
    (boxed(40, 10, 190, 34), "Example Domain", 0.98),
    (boxed(300, 340, 480, 372), "Learn more", 0.95),
    (boxed(60, 200, 900, 230), "This domain is for use in documentation examples", 0.92),
    (boxed(1500, 8, 1560, 28), "x", 0.30),            # below the confidence floor
    (boxed(700, 500, 800, 524), "Learn more", 0.88),   # the same words again, lower down
]


def frame(value=0, size=(600, 800)):
    """A picture of a screen. Different `value`, different screen."""
    return np.full((size[0], size[1], 3), value, dtype=np.uint8)


@pytest.fixture
def screen(monkeypatch):
    """A screen that reads as SCREEN, with nothing real touched."""
    engine = FakeEngine(SCREEN)
    # The remembered frames are module-wide, so each test starts having seen nothing.
    screen_text.cache_clear()
    screen_text._cache_stats.update({"hits": 0, "misses": 0, "saved": 0.0})
    monkeypatch.setattr(screen_text, "_engine", engine, raising=False)
    monkeypatch.setattr(screen_text, "availability",
                        lambda: {"ready": True, "packages": {}, "why": "", "engineError": ""})
    monkeypatch.setattr(
        screen_text, "capture",
        lambda window=None, win=None: (np.zeros((600, 800, 3), dtype=np.uint8),
                                       {"left": 0, "top": 0, "width": 800, "height": 600},
                                       types.SimpleNamespace(title="A Window", hwnd=1)),
        raising=False)
    return engine


@pytest.fixture
def clicks(monkeypatch):
    """Where the pointer went, instead of going there."""
    done = {"moves": [], "clicks": []}
    import mouse_input

    monkeypatch.setattr(mouse_input, "available", lambda: True, raising=False)
    monkeypatch.setattr(mouse_input, "move_to",
                        lambda x, y: done["moves"].append((x, y)), raising=False)
    monkeypatch.setattr(mouse_input, "click",
                        lambda button="left", double=False:
                        done["clicks"].append((button, double)), raising=False)
    return done


# ───────────────────────── reading ─────────────────────────


def test_the_words_come_back_with_screen_coordinates(screen):
    state = screen_text.read()
    assert state["count"] == 4, "the one below the confidence floor is dropped"
    texts = [w.text for w in state["words"]]
    assert "Learn more" in texts and "Example Domain" in texts
    first = next(w for w in state["words"] if w.text == "Example Domain")
    assert first.centre == (115, 22)


def test_the_origin_is_added_so_a_window_is_not_the_screen(screen, monkeypatch):
    """A position in the picture is not a position on the screen, and the click needs
    the second one."""
    monkeypatch.setattr(
        screen_text, "capture",
        lambda window=None, win=None: (np.zeros((600, 800, 3), dtype=np.uint8),
                                       {"left": 1000, "top": 500, "width": 800, "height": 600},
                                       None),
        raising=False)
    word = next(w for w in screen_text.read()["words"] if w.text == "Learn more")
    assert word.centre == (1390, 856)


# ───────────────────────── remembering a frame ─────────────────────────
#
# Reading the screen costs two or three seconds, every time, and that is what made
# picking text deliberate rather than instant. A reading is now filed under a
# fingerprint of the pixels it read, so a second pick at a screen that has not moved is
# answered from the first. What these tests protect is the safety half of that bargain:
# the remembered answer is only ever used for a frame that is byte-for-byte identical,
# because a stale position is a click in the wrong place. (A click has a narrower proof
# available too - the pixels under the one word asked for - and that is the section
# after this one.)


def test_the_same_screen_is_not_read_twice(screen):
    first = screen_text.read()
    second = screen_text.read()
    assert screen.calls == 1, "the second look cost no OCR at all"
    assert first["cached"] is False and second["cached"] is True
    assert [w.as_dict() for w in second["words"]] == [w.as_dict() for w in first["words"]]


def test_a_screen_that_changed_is_read_again(screen, monkeypatch):
    """One different pixel is enough. Anything looser would let Dexter click where a
    button used to be."""
    screen_text.read()
    changed = frame()
    changed[300, 400] = 255
    monkeypatch.setattr(
        screen_text, "capture",
        lambda window=None, win=None: (changed, {"left": 0, "top": 0,
                                                 "width": 800, "height": 600}, None),
        raising=False)
    again = screen_text.read()
    assert screen.calls == 2
    assert again["cached"] is False


def test_the_same_pixels_somewhere_else_are_a_different_screen(screen, monkeypatch):
    """Two windows can look alike; they are not in the same place, and it is the place
    that gets clicked."""
    screen_text.read()
    monkeypatch.setattr(
        screen_text, "capture",
        lambda window=None, win=None: (frame(), {"left": 1000, "top": 500,
                                                 "width": 800, "height": 600}, None),
        raising=False)
    moved = screen_text.read()
    assert moved["cached"] is False and screen.calls == 2
    assert next(w for w in moved["words"] if w.text == "Learn more").centre == (1390, 856)


def test_a_frame_left_too_long_is_read_again(screen, monkeypatch):
    """Identical pixels are proof enough on any ordinary timescale. The clock is only
    so that a machine left alone all afternoon does not act on the morning."""
    monkeypatch.setattr(screen_text, "CACHE_TTL", 0.01)
    screen_text.read()
    time.sleep(0.03)
    assert screen_text.read()["cached"] is False
    assert screen.calls == 2


def test_the_cache_can_be_turned_off(screen, monkeypatch):
    monkeypatch.setattr(screen_text, "CACHE_ON", False)
    screen_text.read()
    assert screen_text.read()["cached"] is False
    assert screen.calls == 2


def test_only_so_many_frames_are_kept(screen, monkeypatch):
    """Screens are megabytes of words; the memory is a convenience, not an archive."""
    monkeypatch.setattr(screen_text, "CACHE_MAX", 2)
    for value in (1, 2, 3):
        monkeypatch.setattr(
            screen_text, "capture",
            lambda window=None, win=None, v=value: (frame(v), {"left": 0, "top": 0,
                                                               "width": 800, "height": 600},
                                                    None),
            raising=False)
        screen_text.read()
    assert len(screen_text._cache) == 2


def test_a_click_after_a_look_does_not_pay_for_a_second_look(screen, clicks):
    """The shape this was built for: read the screen, decide, then click a word on it."""
    screen_text.read()
    result = screen_text.click("Learn more")
    assert result["clicked"] is True and result["cached"] is True
    assert screen.calls == 1
    assert clicks["moves"] == [(390, 356)]


# ── the word, not the frame ──
#
# A whole desktop is never byte-for-byte the same twice: measured on the owner's two
# monitors, captures a second apart differ in 0.03% of their pixels - a clock, a caret,
# a fading notification - while every word stays exactly where it was. A cache that
# only fired on an identical frame would therefore almost never fire. So a click also
# has a narrower proof available: the pixels *under the word asked for*. Unchanged
# pixels there mean the word is still drawn there, because scrolling it away or
# covering it would have changed them.


def test_a_word_whose_pixels_have_not_moved_is_clicked_without_reading_again(
        screen, clicks, monkeypatch):
    screen_text.read()
    ticking = frame()
    ticking[560:590, 600:790] = 255     # a clock in the corner, nowhere near the link
    monkeypatch.setattr(
        screen_text, "capture",
        lambda window=None, win=None: (ticking, {"left": 0, "top": 0,
                                                 "width": 800, "height": 600}, None),
        raising=False)
    result = screen_text.click("Learn more")
    assert result["recalled"] is True and result["clicked"] is True
    assert screen.calls == 1, "the screen changed, the word did not, and OCR was not paid for"
    assert clicks["moves"] == [(390, 356)]


def test_a_word_whose_own_pixels_changed_is_read_again(screen, clicks, monkeypatch):
    """The one case that must never be recalled: something moved where the click goes."""
    screen_text.read()
    moved = frame()
    moved[340:372, 300:480] = 255       # straight over "Learn more"
    monkeypatch.setattr(
        screen_text, "capture",
        lambda window=None, win=None: (moved, {"left": 0, "top": 0,
                                               "width": 800, "height": 600}, None),
        raising=False)
    result = screen_text.click("Learn more")
    assert result.get("recalled") is not True
    assert screen.calls == 2, "it read the screen again rather than click on trust"


def test_a_word_that_was_never_there_is_not_a_no(screen, clicks, monkeypatch):
    """A stale reading can only prove a yes. Absence from it proves nothing - the word
    may have appeared since - so it buys a real read of the screen as it is now."""
    screen_text.read()
    ticking = frame()
    ticking[560:590, 600:790] = 255     # the clock again; the link is untouched
    monkeypatch.setattr(
        screen_text, "capture",
        lambda window=None, win=None: (ticking, {"left": 0, "top": 0,
                                                 "width": 800, "height": 600}, None),
        raising=False)
    result = screen_text.click("quantum tractors")
    assert result["found"] is False and result["clicked"] is False
    assert screen.calls == 2


def test_recall_is_off_with_the_cache(screen, clicks, monkeypatch):
    screen_text.read()
    monkeypatch.setattr(screen_text, "CACHE_ON", False)
    screen_text.click("Learn more")
    assert screen.calls == 2


def test_the_reply_says_which_shortcut_it_took(screen, clicks):
    import tools_screen

    first = tools_screen.click_on_screen("Learn more")
    again = tools_screen.click_on_screen("Example Domain")
    assert "pieces of text" in first.summary, "the first look says what it read"
    assert "still exactly where I last saw it" in again.summary
    assert again.data["cached"] is True and again.data["recalled"] is True


def test_the_books_are_kept_on_what_the_cache_saved(screen):
    screen_text.read()
    screen_text.read()
    stats = screen_text.cache_stats()
    assert stats["hits"] == 1 and stats["misses"] == 1
    assert stats["savedSeconds"] >= 0.0
    assert screen_text.cache_clear() == 1
    assert screen_text.cache_stats()["frames"] == 0


def test_a_fingerprint_notices_one_pixel(screen):
    a, b = frame(), frame()
    assert screen_text.frame_hash(a) == screen_text.frame_hash(b)
    b[0, 0, 0] = 1
    assert screen_text.frame_hash(a) != screen_text.frame_hash(b)


# ───────────────────────── matching ─────────────────────────


@pytest.mark.parametrize("asked,expected", [
    ("Learn more", 1.0),
    ("learn more", 1.0),
    ("LEARN MORE", 1.0),
])
def test_the_same_words_score_full_marks(asked, expected):
    assert screen_text.score_match("Learn more", asked) == expected


def test_words_inside_a_longer_line_still_match():
    line = "This domain is for use in documentation examples"
    assert screen_text.score_match(line, "documentation examples") > screen_text.MIN_SCORE
    assert screen_text.score_match(line, "for use in") > screen_text.MIN_SCORE


def test_a_tight_match_beats_a_loose_one():
    """So "Learn more" picks the link and not a paragraph that mentions it."""
    tight = screen_text.score_match("Learn more", "learn more")
    loose = screen_text.score_match("Click Learn more to learn more about this", "learn more")
    assert tight > loose


def test_ocr_s_own_mistakes_are_forgiven():
    """Screen text comes back imperfect - "Leam" for "Learn" is the classic."""
    assert screen_text.score_match("Leam more", "Learn more") > screen_text.MIN_SCORE


def test_nothing_like_it_scores_nothing():
    assert screen_text.score_match("Learn more", "quantum tractors") < screen_text.MIN_SCORE


def test_the_nth_match_is_reachable(screen):
    """The same words can appear twice; reading order decides which is first."""
    first = screen_text.find("Learn more")
    second = screen_text.find("Learn more", nth=2)
    assert first["match"]["centre"][1] < second["match"]["centre"][1]
    assert second["found"] is True


def test_what_it_saw_comes_back_even_on_a_miss(screen):
    miss = screen_text.find("quantum tractors")
    assert miss["found"] is False
    assert "Example Domain" in miss["seen"], "a no is more useful with the list behind it"


# ───────────────────────── clicking ─────────────────────────


def test_it_clicks_the_centre_of_the_words(screen, clicks):
    result = screen_text.click("Learn more")
    assert result["clicked"] is True
    assert clicks["moves"] == [(390, 356)]
    assert clicks["clicks"] == [("left", False)]


def test_a_weak_match_is_never_clicked(screen, clicks):
    """The rule this shares with the verification tools: a guess that clicks is worse
    than a question. Clicking the wrong thing on somebody's screen is not recoverable by
    asking again."""
    result = screen_text.click("quantum tractors")
    assert result["clicked"] is False
    assert clicks["moves"] == [] and clicks["clicks"] == []


def test_right_click_and_double_click_are_passed_through(screen, clicks):
    screen_text.click("Learn more", button="right")
    screen_text.click("Learn more", double=True)
    assert clicks["clicks"] == [("right", False), ("left", True)]


def test_move_only_aims_without_pressing(screen, clicks):
    """For "put the pointer there" and for proving the coordinates without consequences."""
    result = screen_text.click("Learn more", move_only=True)
    assert result["clicked"] is False
    assert clicks["moves"] == [(390, 356)]
    assert clicks["clicks"] == []


# ───────────────────────── the tools on top ─────────────────────────


def test_the_reply_says_what_it_clicked_and_how_long_it_took(screen, clicks):
    import tools_screen

    result = tools_screen.click_on_screen("Learn more")
    assert result.ok is True
    assert "Learn more" in result.summary
    assert "pieces of text" in result.summary


def test_a_miss_lists_what_was_on_the_screen(screen, clicks):
    import tools_screen

    result = tools_screen.click_on_screen("quantum tractors")
    assert result.ok is False
    assert "Example Domain" in result.summary


def test_reading_the_screen_comes_back_in_reading_order(screen):
    import tools_screen

    result = tools_screen.read_screen()
    assert result.ok is True
    assert result.summary.index("Example Domain") < result.summary.index("Learn more")


def test_without_the_ocr_package_it_says_what_to_install(monkeypatch):
    import tools_screen

    monkeypatch.setattr(screen_text, "availability",
                        lambda: {"ready": False, "packages": {},
                                 "why": "reading the screen needs rapidocr_onnxruntime",
                                 "engineError": ""})
    result = tools_screen.click_on_screen("anything")
    assert result.ok is False
    assert "rapidocr_onnxruntime" in result.summary


# ───────────────────────── the routing ─────────────────────────


def test_the_phrases_reach_it_and_the_ordinals_do_not():
    import tools_page  # noqa: F401
    import tools_screen  # noqa: F401
    import tools_youtube  # noqa: F401
    from router import fast_path

    assert fast_path("click the text that says Sign in") == (
        "click_on_screen", {"text": "Sign in"})
    assert fast_path("click on the text Learn more") == (
        "click_on_screen", {"text": "Learn more"})
    # Plain "click X" stays with `browser_click`: it tries the accessible name first,
    # which is instant and right for the browser's own furniture, and falls back to
    # reading the screen on its own (v13.8). Going straight to OCR would be slower and
    # no more capable.
    assert fast_path("click sign in")[0] == "browser_click"
    assert fast_path("what does the screen say") == ("read_screen", {})
    assert fast_path("read me the screen") == ("read_screen", {})
    # And the tools that read a *document* keep the phrases that are about documents:
    # a photograph of a page is a worse read than the page itself.
    assert fast_path("read this page")[0] == "browser_read_page"
    assert fast_path("what's on my screen")[0] == "get_active_window"
    # These belong to tools that know more than "some words".
    assert fast_path("click the first video")[0] == "youtube_play"
    assert fast_path("click the second result")[0] == "open_result"
    assert fast_path("select the first profile")[0] == "select_nth"
