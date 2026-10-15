"""
Customisable hand gestures (v13.6).

v13 chose the poses and wired each one to a fixed job, which was the right place
to start and the wrong place to stay: whether a fist should park or right-click is
a question about a person's hand, not about the code. So a pose (the *shape*) and
an action (what it does) are now separate, with a map between them that is an
ordinary setting - persisted, editable in the panel, changeable out loud, and
applied while tracking is running.

Two things are worth more than the rest here:

* **the defaults are exactly v13.5's behaviour**, so nobody's hand mouse changes
  by upgrading;
* **a binding that is nonsense costs you the default, not the use of your hand.**

Synthetic landmarks and a recording mouse throughout, like `test_handmouse.py`,
whose `hand()` builder this reuses.
"""

import numpy as np
import pytest

import hand_tracker as ht
import handmouse
from handmouse import (
    DOUBLE, FIST, INDEX_ONLY, MIDDLE, OPEN_PALM, OTHER, PARK, PINCH, PINCH_INDEX,
    PINCH_MIDDLE, POINT, RIGHT_PINCH, SCROLL, THREE_FINGERS, TWO_FINGERS, HandMouse,
    Settings,
)
from mouse_input import Recorder
from test_handmouse import hand, hold


@pytest.fixture
def mouse():
    return Recorder()


def machine(mouse, **bindings):
    """A hand mouse with some poses rebound, everything else on its default."""
    settings = Settings()
    settings.bindings.update(bindings)
    return HandMouse(mouse=mouse, settings=settings, screen=(0, 0, 1920, 1080))


# ───────────────────────── the shapes, named ─────────────────────────


@pytest.mark.parametrize("fingers,expected", [
    (("index", "middle", "ring", "pinky"), OPEN_PALM),
    (("index",), INDEX_ONLY),
    (("index", "middle"), TWO_FINGERS),
    (("index", "middle", "ring"), THREE_FINGERS),
    ((), FIST),
    (("index", "pinky"), OTHER),
])
def test_each_shape_is_recognised_as_itself(mouse, fingers, expected):
    assert machine(mouse).detect_pose(hand(out=fingers)) == expected


def test_a_pinch_is_its_own_pose_not_a_finger_count(mouse):
    hm = machine(mouse)
    assert hm.detect_pose(hand(out=("index",), pinch="index")) == PINCH_INDEX
    assert hm.detect_pose(hand(out=("index", "middle"), pinch="middle")) == PINCH_MIDDLE


def test_detecting_a_shape_says_nothing_about_what_it_does(mouse):
    """The split that makes the rest of this possible: the detector has no opinion."""
    hm = machine(mouse, fist=RIGHT_PINCH)
    assert hm.detect_pose(hand(out=())) == FIST, "still a fist"
    assert hm.action_for(FIST) == RIGHT_PINCH, "bound to something else entirely"


# ───────────────────────── the defaults are v13.5 ─────────────────────────


def test_the_shipped_map_is_what_v135_did(mouse):
    hm = machine(mouse)
    assert hm.action_for(OPEN_PALM) == POINT, "v13.2: an open hand steers"
    assert hm.action_for(INDEX_ONLY) == POINT
    assert hm.action_for(TWO_FINGERS) == SCROLL
    assert hm.action_for(FIST) == PARK, "the one pose nobody makes by accident"
    assert hm.action_for(PINCH_INDEX) == PINCH
    assert hm.action_for(PINCH_MIDDLE) == RIGHT_PINCH


def test_an_upgrade_changes_nothing_about_an_open_palm(mouse):
    frame = machine(mouse).step(hand(out=("index", "middle", "ring", "pinky")), now=1.0)
    assert frame.gesture == POINT
    assert any(kind == "move_to" for kind, _ in frame.actions)


# ───────────────────────── rebinding ─────────────────────────


def test_a_fist_can_be_the_right_click(mouse):
    """The owner's example. A fist is also the easiest pose to hold deliberately,
    which is why somebody would want this."""
    hm = machine(mouse, fist=RIGHT_PINCH)
    frame = hold(hm, hand(out=()), now=1.0)
    assert frame.pose == FIST
    assert frame.gesture == RIGHT_PINCH
    assert ("down", "right") in frame.actions


def test_an_open_palm_can_be_the_scroll(mouse):
    hm = machine(mouse, **{"open-palm": SCROLL})
    hm.step(hand(out=("index", "middle", "ring", "pinky")), now=1.0)
    frame = hm.step(hand(out=("index", "middle", "ring", "pinky"), offset=(0.0, -0.05)),
                    now=1.1)
    assert frame.gesture == SCROLL
    assert any(kind == "scroll" for kind, _ in frame.actions)
    assert not any(kind == "move_to" for kind, _ in frame.actions), "scrolling, not steering"


def test_the_pinch_can_become_the_right_button_and_the_anchor_follows(mouse):
    """Rebinding the action must not move the point the pointer tracks: the hand
    is still holding something between thumb and finger."""
    hm = machine(mouse, **{"pinch-index": RIGHT_PINCH})
    frame = hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    assert frame.gesture == RIGHT_PINCH
    assert ("down", "right") in frame.actions
    assert ("down", "left") not in frame.actions
    assert frame.cursor is not None, "a rebound pinch still steers while held"


def test_a_pose_bound_to_nothing_releases_what_was_held(mouse):
    hm = machine(mouse, **{"two-fingers": PARK})
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    assert hm._pinched is True
    # One frame: a pose bound to "nothing" releases immediately, like the fist it
    # usually is. Waiting for confirmation frames to let go of a button would be the
    # wrong way round (v13.8).
    frame = hm.step(hand(out=("index", "middle")), now=1.2)
    assert frame.gesture == PARK
    assert ("up", "left") in frame.actions


def test_swapping_one_button_for_another_lets_go_of_the_first(mouse):
    """In one frame, or a drag carries on underneath a right click."""
    hm = machine(mouse, **{"pinch-middle": MIDDLE})
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    frame = hold(hm, hand(out=("index", "middle"), pinch="middle"), now=1.5)
    assert ("up", "left") in frame.actions
    assert ("down", "middle") in frame.actions
    assert frame.actions.index(("up", "left")) < frame.actions.index(("down", "middle"))


# ───────────────────────── the two new actions ─────────────────────────


def test_the_middle_button_is_held_like_the_others(mouse):
    hm = machine(mouse, fist=MIDDLE)
    assert ("down", "middle") in hold(hm, hand(out=()), now=1.0).actions
    assert ("up", "middle") in hold(hm, hand(out=("index",)), now=1.3).actions


def test_a_double_click_fires_once_however_long_the_pose_is_held(mouse):
    hm = machine(mouse, **{"three-fingers": DOUBLE})
    held = hand(out=("index", "middle", "ring"))
    first = hm.step(held, now=1.0)
    assert ("double", "left") in first.actions
    for tick in (1.05, 1.1, 1.2, 2.0):
        assert ("double", "left") not in hm.step(held, now=tick).actions, "once per pose"
    # Leave the pose and come back: that is a second deliberate double click.
    hm.step(hand(out=("index",)), now=2.2)
    assert ("double", "left") in hm.step(held, now=2.6).actions


def test_a_double_click_does_not_also_drag_the_pointer(mouse):
    hm = machine(mouse, **{"three-fingers": DOUBLE})
    frame = hm.step(hand(out=("index", "middle", "ring")), now=1.0)
    assert not any(kind == "move_to" for kind, _ in frame.actions)


def test_a_double_click_is_rate_limited_like_a_click(mouse):
    hm = machine(mouse, **{"three-fingers": DOUBLE, "index": DOUBLE})
    hm.settings.max_clicks_per_second = 1.0
    assert ("double", "left") in hm.step(hand(out=("index", "middle", "ring")), now=1.0).actions
    assert ("double", "left") not in hm.step(hand(out=("index",)), now=1.1).actions


# ───────────────────────── bad input ─────────────────────────


def test_an_unknown_action_falls_back_to_the_default_rather_than_doing_nothing(mouse):
    """A typo in settings.json costs you the default, not the use of your hand."""
    hm = machine(mouse, fist="teleport")
    assert hm.action_for(FIST) == PARK
    hm2 = machine(mouse, **{"open-palm": ""})
    assert hm2.action_for(OPEN_PALM) == POINT


def test_a_pose_that_does_not_exist_is_dropped_rather_than_stored(mouse):
    hm = machine(mouse)
    hm.rebind({"knuckles": PINCH, FIST: MIDDLE})
    assert "knuckles" not in hm.settings.bindings
    assert hm.settings.bindings[FIST] == MIDDLE, "the valid half still applies"


# ───────────────────────── read from the environment ─────────────────────────


def test_a_binding_in_the_environment_is_picked_up(monkeypatch):
    monkeypatch.setenv("DEXTER_HAND_POSE_FIST", RIGHT_PINCH)
    assert handmouse.default_bindings()[FIST] == RIGHT_PINCH


def test_a_nonsense_binding_in_the_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("DEXTER_HAND_POSE_FIST", "launch the rocket")
    assert handmouse.default_bindings()[FIST] == PARK


def test_every_pose_has_an_environment_variable_of_its_own():
    names = {handmouse.pose_env(pose) for pose in handmouse.POSES}
    assert len(names) == len(handmouse.POSES), "no two poses share a variable"
    assert handmouse.pose_env(PINCH_MIDDLE) == "DEXTER_HAND_POSE_PINCH_MIDDLE"


def test_rebinding_happens_without_stopping_the_tracking(mouse, monkeypatch):
    """The point of doing it live: you change a gesture *because* the current one
    is not working, and stopping to fix it is the thing you are trying to avoid."""
    hm = machine(mouse)
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    assert hm._pinched is True
    monkeypatch.setenv("DEXTER_HAND_POSE_PINCH_INDEX", RIGHT_PINCH)
    hm.rebind()
    assert hm.settings.bindings[PINCH_INDEX] == RIGHT_PINCH
    assert hm._pinched is False, "a button held under the old map must not stay down"
    frame = hold(hm, hand(out=("index",), pinch="index"), now=1.5)
    assert frame.gesture == RIGHT_PINCH


def test_rebinding_also_re_reads_the_numbers(mouse, monkeypatch):
    hm = machine(mouse)
    monkeypatch.setenv("DEXTER_HAND_SMOOTHING", "0.25")
    monkeypatch.setenv("DEXTER_HAND_PINCH_ON", "0.30")
    hm.rebind()
    assert hm.settings.smoothing == 0.25
    assert hm.settings.pinch_on == 0.30


def test_the_open_threshold_can_never_end_up_below_the_close_one(monkeypatch):
    """Both are settings now, and reversed hysteresis chatters."""
    monkeypatch.setenv("DEXTER_HAND_PINCH_ON", "0.9")
    settings = Settings()
    assert settings.pinch_off > settings.pinch_on


# ───────────────────────── saying it out loud ─────────────────────────


@pytest.mark.parametrize("said,pose", [
    ("a fist", FIST),
    ("closed fist", FIST),
    ("open palm", OPEN_PALM),
    ("my flat hand", OPEN_PALM),
    ("the index finger", INDEX_ONLY),
    ("two fingers", TWO_FINGERS),
    ("three fingers", THREE_FINGERS),
    ("a pinch", PINCH_INDEX),
    ("thumb and middle finger", PINCH_MIDDLE),
    ("middle pinch", PINCH_MIDDLE),
    ("anything else", OTHER),
])
def test_the_poses_people_actually_say(said, pose):
    assert handmouse.normalize_pose(said) == pose


@pytest.mark.parametrize("said,action", [
    ("right click", RIGHT_PINCH),
    ("the right button", RIGHT_PINCH),
    ("left click", PINCH),
    ("click", PINCH),
    ("drag", PINCH),
    ("middle click", MIDDLE),
    ("double click", DOUBLE),
    ("scroll", SCROLL),
    ("move the pointer", POINT),
    ("the cursor", POINT),
    ("do nothing", PARK),
    ("ignore it", PARK),
])
def test_the_actions_people_actually_say(said, action):
    assert handmouse.normalize_action(said) == action


def test_a_phrase_nobody_can_act_on_is_refused_not_guessed():
    assert handmouse.normalize_pose("sideways wiggle") is None
    assert handmouse.normalize_action("teleport") is None


# ───────────────────────── what the panel is shown ─────────────────────────


def test_the_status_carries_the_map_and_the_catalogues(mouse):
    state = machine(mouse, fist=RIGHT_PINCH).status()
    assert state["bindings"][FIST] == RIGHT_PINCH
    rows = {row["pose"]: row for row in state["poses"]}
    assert set(rows) == set(handmouse.POSES), "every pose is offered, or it cannot be changed"
    assert rows[FIST]["action"] == RIGHT_PINCH
    assert rows[FIST]["default"] == PARK, "so the panel can show what is customised"
    assert rows[FIST]["hint"], "how to make the shape, not just its name"
    assert {row["action"] for row in state["actions"]} == set(handmouse.ACTIONS)


def test_the_status_says_the_shape_and_what_it_meant(mouse):
    hm = machine(mouse, fist=MIDDLE)
    hold(hm, hand(out=()), now=1.0)
    state = hm.status()
    assert state["pose"] == FIST
    assert state["action"] == MIDDLE
    assert state["buttons"]["middle"] is True


def test_an_idle_tracker_reports_the_map_as_it_is_now(monkeypatch):
    """`hand_mouse` is built when the module is imported, which can be before the
    stored settings are applied over .env. A panel showing a stale gesture map
    would be worse than showing none, and the panel is what gets read while
    somebody is deciding which pose to change."""
    monkeypatch.setenv("DEXTER_HAND_POSE_FIST", DOUBLE)
    assert handmouse.live_status()["bindings"][FIST] == DOUBLE
    monkeypatch.delenv("DEXTER_HAND_POSE_FIST")
    assert handmouse.live_status()["bindings"][FIST] == PARK, "and back, with no restart"


def test_a_running_tracker_is_not_re_read_behind_its_own_back(monkeypatch):
    """Only an idle one is refreshed: doing it on every status poll would drop any
    button being held, forty times a minute, while somebody was dragging."""
    monkeypatch.setattr(type(handmouse.hand_mouse), "running",
                        property(lambda self: True))
    handmouse.hand_mouse.settings.bindings[FIST] = DOUBLE
    monkeypatch.setenv("DEXTER_HAND_POSE_FIST", PARK)
    assert handmouse.live_status()["bindings"][FIST] == DOUBLE


# ───────────────────────── as settings, and out loud ─────────────────────────


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway settings.json: these tests write real settings."""
    import settings_store

    monkeypatch.setattr(settings_store, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    return settings_store


def test_every_pose_is_a_setting_somebody_can_change(store):
    """Generated from the detector's own catalogue, so a pose can never ship
    without a way to rebind it."""
    keys = {row["key"] for row in store.describe()}
    for pose in handmouse.POSES:
        key = "handPose" + "".join(part.capitalize() for part in pose.split("-"))
        assert key in keys, f"{pose} has no setting"


def test_the_gesture_rows_offer_every_action(store):
    row = next(r for r in store.describe() if r["key"] == "handPoseFist")
    assert set(row["options"]) == set(handmouse.ACTIONS)
    assert row["value"] == PARK
    assert row["group"] == "hand"


def test_changing_a_gesture_setting_persists_and_applies_it(store):
    result = store.update({"handPoseFist": RIGHT_PINCH})
    assert store.current()["handPoseFist"] == RIGHT_PINCH
    assert handmouse.live_status()["bindings"][FIST] == RIGHT_PINCH
    # It has no module constant to patch, but it is live all the same - so it must
    # not be reported as needing a restart.
    assert "handPoseFist" in result["applied"]
    assert "handPoseFist" not in result["needsRestart"]


def test_a_nonsense_action_is_refused_and_the_default_kept(store):
    store.update({"handPoseFist": "teleport"})
    assert store.current()["handPoseFist"] == PARK


def test_the_running_tracker_is_told_when_a_gesture_changes(store, monkeypatch):
    told = {}
    monkeypatch.setattr(handmouse.hand_mouse, "rebind",
                        lambda *a, **k: told.setdefault("yes", True))
    store.update({"handPoseOpenPalm": SCROLL})
    assert told.get("yes") is True


def test_an_unrelated_setting_does_not_touch_the_hand_mouse(store, monkeypatch):
    told = {}
    monkeypatch.setattr(handmouse.hand_mouse, "rebind",
                        lambda *a, **k: told.setdefault("yes", True))
    store.update({"closeOutHour": 22})
    assert told == {}


def test_saying_it_out_loud_changes_the_same_setting(store):
    """The owner's phrasing. One channel: the tool writes the setting the panel
    edits, so the two can never disagree about what a pose does."""
    import tools_handmouse

    result = tools_handmouse.set_hand_gesture("a fist", "right click")
    assert result.ok is True
    assert "right" in result.summary.lower()
    assert store.current()["handPoseFist"] == RIGHT_PINCH
    assert handmouse.live_status()["bindings"][FIST] == RIGHT_PINCH


def test_a_pose_it_cannot_place_is_refused_with_the_list(store):
    import tools_handmouse

    result = tools_handmouse.set_hand_gesture("my elbow", "click")
    assert result.ok is False
    assert "Open palm" in result.summary
    assert store.current()["handPoseFist"] == PARK, "nothing was written"


def test_the_map_can_be_read_back_in_one_line(store):
    import tools_handmouse

    tools_handmouse.set_hand_gesture("fist", "double click")
    listed = tools_handmouse.hand_gestures()
    assert "Double click" in listed.summary
    assert listed.data["bindings"][FIST] == DOUBLE


def test_the_spoken_description_follows_the_map_rather_than_a_hardcoded_line():
    """The sentence in the start reply said "open palm to park" for two releases
    after an open palm started steering. It is generated now."""
    sentence = handmouse.gesture_sentence({**handmouse.POSE_DEFAULTS, FIST: RIGHT_PINCH})
    assert "fist" in sentence and "right click" in sentence
    assert "open palm" in sentence and "move" in sentence


# ───────────── speed, and making a click land (v13.8) ─────────────
#
# "Can we control the speed of the hand mouse pointer? Also clicking seems difficult
# as it is unstable." Three things were fighting the owner's hand:
#
#   1. the pointer kept moving *through* the click, and pinching is a movement;
#   2. one noisy frame could fire a click, or drop one halfway;
#   3. holding a pinch curls the pinching finger, which lowered the very measurement
#      that had allowed the pinch to start - so a held click could fail its own test.


def test_speed_is_the_size_of_the_control_rectangle(mouse):
    """The pointer is absolute, so "faster" means *less hand movement per pixel* -
    a smaller rectangle mapped onto the same screen."""
    slow = Settings(speed=0.6)
    fast = Settings(speed=2.0)
    width = lambda s: s.region[2] - s.region[0]
    assert width(fast) < width(Settings(speed=1.0)) < width(slow)


def test_the_same_hand_movement_goes_further_when_faster(mouse):
    """The measurable consequence, in pixels on the screen."""
    def travel(speed):
        hm = HandMouse(mouse=Recorder(), settings=Settings(speed=speed, smoothing=0.0),
                       screen=(0, 0, 1920, 1080))
        first = hm.step(hand(out=("index",)), now=1.0).cursor
        second = hm.step(hand(out=("index",), offset=(0.05, 0.0)), now=1.1).cursor
        return abs(second[0] - first[0])

    assert travel(2.0) > travel(1.0) > travel(0.6)


def test_the_speed_is_clamped_at_both_ends(mouse):
    """A rectangle of nothing makes the pointer unusable; one bigger than the frame
    puts the corners of the screen out of reach of a hand that stays in shot."""
    assert Settings(speed=99.0).speed == handmouse.MAX_SPEED
    assert Settings(speed=0.01).speed == handmouse.MIN_SPEED
    for speed in (handmouse.MIN_SPEED, 1.0, handmouse.MAX_SPEED):
        x0, y0, x1, y1 = Settings(speed=speed).region
        assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


def test_the_corners_of_the_screen_are_reachable_at_any_speed(mouse):
    """Whatever the rectangle, its own corners still map to the screen's corners."""
    for speed in (0.5, 1.0, 2.5):
        hm = HandMouse(mouse=Recorder(), settings=Settings(speed=speed, smoothing=0.0),
                       screen=(0, 0, 1920, 1080))
        x0, y0, x1, y1 = hm.settings.region
        assert hm._to_screen(x1, y0) == (0, 0), "mirrored: the far corner is the origin"
        assert hm._to_screen(x0, y1) == (1919, 1079)


def test_the_pointer_holds_still_while_a_click_is_made(mouse):
    """The fix for "clicking is unstable": the press lands where it was aimed."""
    hm = HandMouse(mouse=mouse, settings=Settings(smoothing=0.0), screen=(0, 0, 1920, 1080))
    aimed = hm.step(hand(out=("index",)), now=1.0).cursor
    # Pinching moves the hand a little, as it does in life.
    frames = [hm.step(hand(out=("index",), pinch="index", offset=(0.003 * n, 0.003 * n)),
                      now=1.0 + 0.05 * n) for n in range(1, 5)]
    assert any(("down", "left") == action for frame in frames for action in frame.actions)
    assert frames[-1].locked is True, "held still on purpose"
    assert frames[-1].cursor == aimed, "the click landed where it was aimed"
    assert not any(kind == "move_to" for kind, _ in mouse.events[1:]), "no drift mid-click"


def test_moving_far_enough_while_held_is_still_a_drag(mouse):
    """The lock must not cost you drag-and-drop."""
    hm = HandMouse(mouse=mouse, settings=Settings(smoothing=0.0), screen=(0, 0, 1920, 1080))
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    assert hm._pinched is True
    moved = hm.step(hand(out=("index",), pinch="index", offset=(0.25, 0.0)), now=1.5)
    assert moved.locked is False
    assert any(kind == "move_to" for kind, _ in moved.actions), "a drag follows the hand"
    assert hm._pinched is True, "and the button stays down through it"


def test_the_lock_is_released_when_the_pinch_is(mouse):
    hm = HandMouse(mouse=mouse, settings=Settings(smoothing=0.0), screen=(0, 0, 1920, 1080))
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    after = hold(hm, hand(out=("index",), offset=(0.005, 0.0)), now=1.4)
    assert after.locked is False
    assert any(kind == "move_to" for kind, _ in after.actions)


def test_one_noisy_frame_does_not_make_a_click(mouse):
    """A single frame of pinch is landmark noise, not an instruction."""
    hm = HandMouse(mouse=mouse, settings=Settings(), screen=(0, 0, 1920, 1080))
    hm.step(hand(out=("index",)), now=1.0)
    flicker = hm.step(hand(out=("index",), pinch="index"), now=1.05)
    assert ("down", "left") not in flicker.actions
    assert hm._pinched is False
    back = hm.step(hand(out=("index",)), now=1.10)
    assert ("down", "left") not in back.actions


def test_one_noisy_frame_does_not_drop_a_click_either(mouse):
    """The half that matters more: a click that lets go on its own is the report."""
    hm = HandMouse(mouse=mouse, settings=Settings(), screen=(0, 0, 1920, 1080))
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    assert hm._pinched is True
    glitch = hm.step(hand(out=("index",)), now=1.2)          # one frame, pinch lost
    assert ("up", "left") not in glitch.actions
    assert hm._pinched is True, "one frame is not letting go"
    gone = hm.step(hand(out=("index",)), now=1.25)
    assert ("up", "left") in gone.actions, "two frames is"


def test_a_held_pinch_survives_the_finger_curling(mouse):
    """Pinching curls the index finger, which lowers its reach past the bar that
    stopped a fist being read as a click. The held pinch gets a relaxed bar."""
    hm = HandMouse(mouse=mouse, settings=Settings(), screen=(0, 0, 1920, 1080))
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    assert hm._held_pinch == PINCH_INDEX

    curled = hand(out=("index",), pinch="index")
    # Pull the index tip back towards its knuckle: reach drops below pinch_reach.
    wrist = curled.points[ht.WRIST]
    knuckle = curled.points[ht.INDEX_MCP]
    # reach() measures the tip against the knuckle *as seen from the wrist*, so a
    # curl is the tip coming back towards the wrist, not towards the knuckle.
    curled.points[ht.INDEX_TIP] = wrist + (knuckle - wrist) * 1.12
    curled.points[ht.THUMB_TIP] = curled.points[ht.INDEX_TIP] + np.array(
        [0.004, 0.004, 0.0], dtype=np.float32)
    assert handmouse.reach(curled, ht.INDEX_TIP, ht.INDEX_MCP) < hm.settings.pinch_reach

    frame = hm.step(curled, now=1.3)
    assert frame.pose == PINCH_INDEX, "still the same pinch, just tighter"
    assert hm._pinched is True, "a held click must not fall out of its own test"


def test_a_fist_is_still_never_a_click(mouse):
    """The relaxed bar applies only to a pinch already held - the reason the bar
    exists is that a fist puts the thumb against the middle fingertip."""
    hm = HandMouse(mouse=mouse, settings=Settings(), screen=(0, 0, 1920, 1080))
    for tick in range(4):
        frame = hm.step(hand(out=()), now=1.0 + tick * 0.05)
        assert frame.pose == FIST
        assert frame.gesture == PARK
    assert hm._pinched is False and hm._right_pinched is False


def test_the_status_explains_a_pointer_that_is_not_moving(mouse):
    hm = HandMouse(mouse=mouse, settings=Settings(speed=1.4), screen=(0, 0, 1920, 1080))
    hold(hm, hand(out=("index",), pinch="index"), now=1.0)
    state = hm.status()
    assert state["locked"] is True
    assert state["dragging"] is False
    assert state["speed"] == 1.4
    assert len(state["region"]) == 4
