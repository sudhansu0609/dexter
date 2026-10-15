"""
The hand mouse (v13): gestures in, mouse events out.

Everything here runs on synthetic landmarks and a recording mouse. That is the
whole point of splitting `HandMouse.step()` from the camera loop: the part that
decides whether a pinch is a click can be tested exhaustively, on every machine,
with no webcam, no models and no pointer moving.

The landmark layout is MediaPipe's, and `hand()` below builds poses in the same
normalised 0..1 frame coordinates the real tracker produces.
"""

import numpy as np
import pytest

import hand_tracker as ht
import handmouse
from handmouse import PARK, PINCH, POINT, RIGHT_PINCH, SCROLL, HandMouse, Settings
from mouse_input import Recorder

# A neutral open hand: wrist low, fingers up, thumb out to the left.
BASE = {
    ht.WRIST: (0.50, 0.80),
    ht.THUMB_CMC: (0.44, 0.76), ht.THUMB_MCP: (0.41, 0.72),
    ht.THUMB_IP: (0.38, 0.68), ht.THUMB_TIP: (0.36, 0.64),
    ht.INDEX_MCP: (0.46, 0.66), ht.INDEX_PIP: (0.45, 0.60),
    ht.INDEX_DIP: (0.445, 0.56), ht.INDEX_TIP: (0.44, 0.52),
    ht.MIDDLE_MCP: (0.50, 0.65), ht.MIDDLE_PIP: (0.50, 0.58),
    ht.MIDDLE_DIP: (0.50, 0.54), ht.MIDDLE_TIP: (0.50, 0.50),
    ht.RING_MCP: (0.54, 0.66), ht.RING_PIP: (0.55, 0.60),
    ht.RING_DIP: (0.555, 0.56), ht.RING_TIP: (0.56, 0.53),
    ht.PINKY_MCP: (0.57, 0.68), ht.PINKY_PIP: (0.58, 0.63),
    ht.PINKY_DIP: (0.585, 0.60), ht.PINKY_TIP: (0.59, 0.57),
}

CURLS = {
    "index": (ht.INDEX_PIP, ht.INDEX_DIP, ht.INDEX_TIP),
    "middle": (ht.MIDDLE_PIP, ht.MIDDLE_DIP, ht.MIDDLE_TIP),
    "ring": (ht.RING_PIP, ht.RING_DIP, ht.RING_TIP),
    "pinky": (ht.PINKY_PIP, ht.PINKY_DIP, ht.PINKY_TIP),
}
MCPS = {"index": ht.INDEX_MCP, "middle": ht.MIDDLE_MCP,
        "ring": ht.RING_MCP, "pinky": ht.PINKY_MCP}


def hand(out=("index", "middle", "ring", "pinky"), pinch=None, offset=(0.0, 0.0),
         score=0.95):
    """One pose. `out` lists the fingers that are extended; `pinch` is 'index' or
    'middle' to bring the thumb tip onto that fingertip."""
    points = np.zeros((21, 3), dtype=np.float32)
    for index, (x, y) in BASE.items():
        points[index] = (x + offset[0], y + offset[1], 0.0)
    for finger, (pip, dip, tip) in CURLS.items():
        if finger in out:
            continue
        # Curled: the tip comes back towards the knuckle, so it is nearer the
        # wrist than the middle joint is.
        knuckle = points[MCPS[finger]]
        points[dip] = knuckle + np.array([0.0, 0.015, 0.0], dtype=np.float32)
        points[tip] = knuckle + np.array([0.0, 0.03, 0.0], dtype=np.float32)
        points[pip] = knuckle + np.array([0.0, -0.02, 0.0], dtype=np.float32)
    if pinch == "index":
        points[ht.THUMB_TIP] = points[ht.INDEX_TIP] + np.array([0.004, 0.004, 0.0], dtype=np.float32)
    elif pinch == "middle":
        points[ht.THUMB_TIP] = points[ht.MIDDLE_TIP] + np.array([0.004, 0.004, 0.0], dtype=np.float32)
    return ht.Hand(points=points, score=score, handedness=1.0, source="test")

def hold(hm, pose, now=1.0, frames=2, gap=0.05):
    """Hold a pose for several frames and return the last one.

    v13.8: a button is pressed only once the pose has been seen `confirm_frames`
    times in a row, and released only once it has been absent that many - so a
    single noisy frame can neither fire a click nor drop one. Tests that are about
    *what a click does* therefore have to hold the pose like a person would; the one
    test that is about the debounce itself steps frame by frame on purpose.
    """
    frame = None
    for index in range(frames):
        frame = hm.step(pose, now=now + index * gap)
    return frame



@pytest.fixture
def mouse():
    return Recorder()


@pytest.fixture
def hm(mouse):
    return HandMouse(mouse=mouse, settings=Settings(), screen=(0, 0, 1920, 1080))


# ───────────────────────── the poses ─────────────────────────


def test_an_open_palm_steers_the_pointer(hm, mouse):
    """v13.2: the first thing anyone does is hold a flat palm up and move it.
    Making that pose do nothing was the whole of "I can't move the cursor"."""
    frame = hm.step(hand(), now=1.0)
    assert frame.gesture == POINT
    assert mouse.kinds() == ["move_to"]


def test_a_fist_is_the_park_and_moves_nothing(hm, mouse):
    frame = hm.step(hand(out=()), now=1.0)
    assert frame.gesture == PARK
    assert mouse.events == [], "park must be completely still"


def test_parking_releases_a_held_button(hm, mouse):
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    # One frame of park is enough to let go: it is the emergency stop, and is
    # deliberately exempt from the v13.8 confirmation frames.
    released = hm.step(hand(out=()), now=1.2)
    assert ("up", "left") in released.actions


def test_pointing_moves_the_pointer(hm, mouse):
    frame = hm.step(hand(out=("index",)), now=1.0)
    assert frame.gesture == POINT
    assert mouse.kinds() == ["move_to"]
    x, y = mouse.cursor
    assert 0 <= x < 1920 and 0 <= y < 1080


def test_the_pointer_is_mirrored_so_moving_right_goes_right(hm, mouse):
    """The camera faces you, so its image is a mirror: your hand moving to *your*
    right moves to the left of the frame. Without the flip the pointer goes the
    wrong way, which is the first thing anyone notices."""
    hm.settings.smoothing = 0.0
    hm.step(hand(out=("index",)), now=1.0)
    start_x = mouse.cursor[0]
    # Towards the user's right = smaller x in the camera image.
    hm.step(hand(out=("index",), offset=(-0.15, 0.0)), now=1.1)
    assert mouse.cursor[0] > start_x
    # ...and the other way.
    hm.step(hand(out=("index",), offset=(0.15, 0.0)), now=1.2)
    assert mouse.cursor[0] < start_x


def test_a_pinch_presses_the_left_button_and_releasing_lets_go(hm, mouse):
    hm.step(hand(out=("index",)), now=1.0)
    pinched = hold(hm, hand(out=("index",), pinch="index"), now=1.1)
    assert pinched.gesture == PINCH
    assert ("down", "left") in pinched.actions
    released = hold(hm, hand(out=("index",)), now=1.4)
    assert ("up", "left") in released.actions
    # down then up, with the pointer where the hand was: that is a click.
    assert [k for k in mouse.kinds() if k in ("down", "up")] == ["down", "up"]


def test_a_held_pinch_that_moves_is_a_drag_not_a_second_click(hm, mouse):
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    # Far enough to be unmistakably a drag rather than the wobble of pinching
    # (v13.8 holds the pointer still until it passes drag_threshold_px).
    for step, dx in enumerate((0.06, 0.12, 0.18), start=1):
        hm.step(hand(out=("index",), pinch="index", offset=(dx, 0.0)), now=1.0 + step * 0.1)
    hold(hm, hand(out=("index",)), now=1.5)
    downs = [k for k in mouse.kinds() if k == "down"]
    ups = [k for k in mouse.kinds() if k == "up"]
    assert len(downs) == 1 and len(ups) == 1, "a drag is one press, not several"
    assert "move_to" in mouse.kinds()


def test_a_thumb_to_middle_pinch_is_the_right_button(hm, mouse):
    frame = hold(hm, hand(out=("index", "middle"), pinch="middle"), now=1.0)
    assert frame.gesture == RIGHT_PINCH
    assert ("down", "right") in frame.actions
    assert ("down", "left") not in frame.actions
    released = hold(hm, hand(out=("index",)), now=1.3)
    assert ("up", "right") in released.actions


def test_hysteresis_keeps_a_trembling_pinch_closed(hm, mouse):
    """Between pinch_on and pinch_off the pose does not flip: a hand shaking at
    the boundary must not machine-gun clicks."""
    hm.step(hand(out=("index",), pinch="index"), now=1.0)
    # Open the gap to 0.5 hand-widths: past `on` (0.45), inside `off` (0.62).
    wobbling = hand(out=("index",))
    wobbling.points[ht.THUMB_TIP] = (
        wobbling.points[ht.INDEX_TIP] + np.array([0.0, 0.075, 0.0], dtype=np.float32)
    )
    assert 0.45 < wobbling.distance(ht.THUMB_TIP, ht.INDEX_TIP) < 0.62
    frame = hm.step(wobbling, now=1.1)
    assert frame.gesture == PINCH
    assert ("up", "left") not in frame.actions


def test_two_fingers_scroll_and_do_not_move_the_pointer(hm, mouse):
    hm.step(hand(out=("index", "middle")), now=1.0)
    assert hm.gesture == SCROLL
    # Move the hand up by a third of a hand-width: that is a scroll up.
    hm.step(hand(out=("index", "middle"), offset=(0.0, -0.05)), now=1.1)
    scrolls = [value[0] for kind, value in mouse.events if kind == "scroll"]
    assert scrolls and scrolls[0] > 0, f"expected an upward scroll, got {mouse.events}"
    assert "move_to" not in mouse.kinds(), "scrolling must not drag the pointer"


def test_scrolling_down_is_the_other_direction(hm, mouse):
    hm.step(hand(out=("index", "middle")), now=1.0)
    hm.step(hand(out=("index", "middle"), offset=(0.0, 0.05)), now=1.1)
    scrolls = [value[0] for kind, value in mouse.events if kind == "scroll"]
    assert scrolls and scrolls[0] < 0


def test_a_tiny_movement_is_not_sent(hm, mouse):
    hm.settings.smoothing = 0.0
    hm.step(hand(out=("index",)), now=1.0)
    before = len(mouse.events)
    hm.step(hand(out=("index",), offset=(0.0002, 0.0)), now=1.1)
    assert len(mouse.events) == before, "camera noise must not produce events"


# ───────────────────────── safety ─────────────────────────


def test_a_hand_leaving_the_frame_releases_every_button(hm, mouse):
    """The worst bug this code could have is a button left down."""
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    assert hm._pinched is True
    frame = hm.step(None, now=1.0 + hm.settings.idle_release_s + 0.1)
    assert ("up", "left") in frame.actions
    assert hm._pinched is False


def test_a_brief_dropout_does_not_let_go_mid_drag(hm):
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    frame = hm.step(None, now=1.15)
    assert frame.actions == [], "one missed frame is not the end of a drag"
    assert hm._pinched is True


def test_clicks_are_rate_limited(hm, mouse):
    hm.settings.max_clicks_per_second = 2.0
    # Pinch, release, pinch again within the same half-second.
    hold(hm, hand(out=("index",), pinch="index"), now=1.00)
    hold(hm, hand(out=("index",)), now=1.10)
    # Counted on the recorder: with confirmation frames a press can land on any
    # frame of the hold, so "not in this one frame" no longer means "not at all".
    before = mouse.kinds().count("down")
    hold(hm, hand(out=("index",), pinch="index"), now=1.20)
    assert mouse.kinds().count("down") == before, "too soon - the limiter holds it"
    hold(hm, hand(out=("index",), pinch="index"), now=2.00)
    assert mouse.kinds().count("down") == before + 1, "and lets the next one through"


def test_stop_releases_everything_even_if_it_was_never_started(hm, mouse):
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    hm.stop()
    assert hm._pinched is False
    assert ("up", "left") in [(k, v) for k, v in mouse.events]


def test_status_says_what_is_missing_when_the_models_are_absent(monkeypatch):
    monkeypatch.setattr(ht, "models_present", lambda: False)
    monkeypatch.setattr(ht, "packages", lambda: {"cv2": True, "onnxruntime": True,
                                                 "mediapipe": False, "numpy": True})
    state = handmouse.HandMouse(mouse=Recorder(), screen=(0, 0, 1920, 1080)).status()
    assert state["ready"] is False
    assert "download" in state["why"] or "models" in state["why"]


def test_starting_without_the_models_refuses_instead_of_opening_the_camera(monkeypatch):
    monkeypatch.setattr(ht, "available", lambda: (False, "the hand models aren't downloaded yet"))
    opened = []
    monkeypatch.setattr(ht, "open_camera", lambda *a, **k: opened.append(a) or (None, 0))
    result = handmouse.HandMouse(mouse=Recorder(), screen=(0, 0, 1920, 1080)).start()
    assert result["ok"] is False
    assert "models" in result["error"]
    assert opened == [], "no camera may be opened when tracking cannot work"


# ───────────────────────── geometry ─────────────────────────


def test_finger_extension_reads_a_fist_and_an_open_palm_correctly():
    open_palm = handmouse.fingers_out(hand())
    assert all(open_palm[name] for name in ("index", "middle", "ring", "pinky"))
    fist = handmouse.fingers_out(hand(out=()))
    assert not any(fist[name] for name in ("index", "middle", "ring", "pinky"))


def test_hand_size_normalises_distance_so_depth_does_not_change_a_pinch():
    near = hand(out=("index",), pinch="index")
    far = ht.Hand(points=near.points * 0.5, score=0.9)
    assert near.distance(ht.THUMB_TIP, ht.INDEX_TIP) == pytest.approx(
        far.distance(ht.THUMB_TIP, ht.INDEX_TIP), abs=1e-5
    )


def test_the_region_maps_its_corners_to_the_whole_screen(hm):
    x0, y0, x1, y1 = hm.settings.region
    assert hm._to_screen(x1, y0) == (0, 0)
    assert hm._to_screen(x0, y1) == (1919, 1079)


def test_the_palm_anchors_match_the_models_output_shape():
    """2016 anchors, because that is how many rows the palm model returns. The
    numbers themselves are checked against the reference table in the zoo."""
    anchors = ht._palm_anchors()
    assert anchors.shape == (2016, 2)
    assert anchors.min() > 0 and anchors.max() < 1


# ───────────────────────── a fist is not a click (v13.2) ─────────────────────────


def fist_with_thumb_across():
    """A real closed hand: fingertips back at their knuckles, thumb lying across
    them. Measured on a photograph of a fist - the thumb tip sits 0.35 hand-widths
    from the middle fingertip, which is *inside* the pinch threshold."""
    pose = hand(out=())
    pose.points[ht.THUMB_TIP] = (
        pose.points[ht.MIDDLE_TIP] + np.array([0.01, 0.01, 0.0], dtype=np.float32)
    )
    return pose


def test_a_fist_is_never_a_right_click(hm, mouse):
    pose = fist_with_thumb_across()
    assert pose.distance(ht.THUMB_TIP, ht.MIDDLE_TIP) < hm.settings.pinch_on, (
        "the thumb really is inside the pinch distance - that is the trap"
    )
    frame = hm.step(pose, now=1.0)
    assert frame.gesture == PARK
    assert mouse.events == [], "a closed hand must not press anything"


def test_reach_separates_a_curled_finger_from_an_extended_one():
    open_hand = hand()
    assert handmouse.reach(open_hand, ht.INDEX_TIP, ht.INDEX_MCP) > 1.5
    assert handmouse.reach(hand(out=()), ht.INDEX_TIP, ht.INDEX_MCP) < 1.25


def test_a_pinch_still_works_when_the_finger_is_reaching(hm, mouse):
    frame = hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    assert frame.gesture == PINCH
    assert ("down", "left") in frame.actions


def test_the_preview_is_on_by_default_and_can_be_turned_off(monkeypatch):
    assert handmouse.HandMouse(mouse=Recorder(), screen=(0, 0, 1920, 1080)).preview_wanted
    monkeypatch.setenv("DEXTER_HAND_PREVIEW", "0")
    assert not handmouse.HandMouse(mouse=Recorder(), screen=(0, 0, 1920, 1080)).preview_wanted


def test_the_status_says_how_close_the_camera_came(hm):
    hm.palm_score, hm.best_score = 0.31, 0.42
    state = hm.status()
    assert state["palmScore"] == 0.31 and state["bestScore"] == 0.42
    assert state["palmNeeded"] == ht.PALM_SCORE_MIN
    assert "enabled" in state["preview"]
