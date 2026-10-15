"""
The quality of the tracking itself (v13.8).

"The hand gestures in hand mouse are not that good, improve the hand tracking."

Measured before changing anything, on the owner's own webcam:

    palm detection      5.7 ms per frame
    landmark model      3.1 ms per frame
    camera, DSHOW 640x480   **16.6 fps**
    camera, MSMF  640x360   **30.3 fps**  (three trials: 30.2 / 30.3 / 30.3)

So the models were never the problem - nine milliseconds of work was waiting sixty for
a frame. Three things came out of that:

1. **Twice the frames**, by asking Media Foundation for the camera's native 16:9 mode.
   Every frame the camera does not deliver is gesture quality thrown away: frames are
   the currency of smoothing, debouncing and motion blur alike.
2. **The landmarks are filtered before anything reads them.** Every pose decision -
   finger out, pinch width, thumb reach - was computed from coordinates that jitter a
   pixel or two per frame, so a hand held still could flicker between poses.
3. **A finger is "out" by the angle at its joint**, not by how far its tip is from the
   wrist. The old measure fails on a hand tilted towards the camera, where
   foreshortening shrinks every distance equally.

The numbers in these tests are the ones that justified the defaults, so a future change
to them has something to beat.
"""

import numpy as np
import pytest

import hand_tracker as ht
import handmouse as hm
from test_handmouse import hand

#: About 1.5 px of sensor noise on a 480-row frame, in normalised units. Real webcam
#: landmark noise at arm's length in room light is this order.
NOISE = 1.5 / 480.0
#: The control rectangle is 0.64 of the frame across 1920 px, so one unit of frame
#: width is about 3000 screen pixels. Every figure below is in screen pixels.
PX_PER_UNIT = 3000.0


def jitter_and_error(min_hz, beta, speed, frames=120, seed=3):
    """(mean error from the truth, mean frame-to-frame movement), in screen pixels."""
    rng = np.random.default_rng(seed)
    smooth = ht._OneEuro(min_hz=min_hz, beta=beta)
    base = hand(out=("index",))
    errors, jitters, previous = [], [], None
    for index in range(frames):
        truth = base.points.copy()
        truth[:, 0] += speed * index
        noisy = truth.copy()
        noisy[:, :2] += rng.normal(0, NOISE, size=(21, 2)).astype(np.float32)
        out = smooth(noisy, now=index / 30.0)
        errors.append(float(np.mean(np.abs(out[:, :2] - truth[:, :2]))) * PX_PER_UNIT)
        if previous is not None:
            jitters.append(
                float(np.mean(np.abs(out[:, :2] - previous[:, :2]))) * PX_PER_UNIT)
        previous = out.copy()
    return float(np.mean(errors[10:])), float(np.mean(jitters[10:]))


STILL, SLOW, FAST = 0.0, 0.0012, 0.0035


# ───────────────────────── the filter earns its place ─────────────────────────


def test_a_still_hand_stops_wobbling():
    """The measurement behind "clicking is unstable": an unfiltered pointer moves ten
    pixels a frame while the hand is not moving at all."""
    raw_error, raw_jitter = jitter_and_error(0.0, 0.0, STILL)
    error, jitter = jitter_and_error(ht.SMOOTH_MIN_HZ, ht.SMOOTH_BETA, STILL)
    assert raw_jitter > 9.0, f"the premise: unfiltered jitter was {raw_jitter:.1f} px"
    assert jitter < raw_jitter / 3, f"{jitter:.1f} px vs {raw_jitter:.1f} px"
    assert error < raw_error / 1.8, "and closer to where the hand actually is"


@pytest.mark.parametrize("speed,name", [(STILL, "still"), (SLOW, "slow"), (FAST, "fast")])
def test_the_filter_is_never_worse_than_no_filter(speed, name):
    """The point of a one-euro filter over a fixed exponential one: it gets out of the
    way of a moving hand. At these settings it is *more* accurate than raw landmarks at
    every speed, which is what makes it safe to put underneath every pose decision."""
    raw_error, raw_jitter = jitter_and_error(0.0, 0.0, speed)
    error, jitter = jitter_and_error(ht.SMOOTH_MIN_HZ, ht.SMOOTH_BETA, speed)
    assert error <= raw_error, f"{name}: {error:.1f} px vs {raw_error:.1f} px"
    assert jitter < raw_jitter, f"{name}: {jitter:.1f} px vs {raw_jitter:.1f} px"


def test_beta_is_what_lets_a_moving_hand_through():
    """Recording why the default is 30 and not the published 0.35: these coordinates
    are 0..1 of a frame, so a hand's speed is a hundredth of the number the literature
    assumes, and beta has to be that much larger to mean the same thing."""
    lagging, _ = jitter_and_error(ht.SMOOTH_MIN_HZ, 0.35, FAST)
    tuned, _ = jitter_and_error(ht.SMOOTH_MIN_HZ, ht.SMOOTH_BETA, FAST)
    assert tuned < lagging * 0.75, f"{tuned:.1f} px vs {lagging:.1f} px"


def test_switching_it_off_changes_nothing_else():
    """`min_hz=0` has to be a true bypass: the geometry tests depend on it."""
    points = hand().points
    assert ht._OneEuro(min_hz=0.0)(points) is points


def test_a_new_hand_does_not_inherit_the_old_one_s_position():
    """Between two hands there is a gap, and dragging the first frame of the new one
    towards where the last one was would throw the pointer across the screen."""
    smooth = ht._OneEuro()
    left = hand(offset=(-0.2, 0.0))
    smooth(left.points, now=1.0)
    smooth.reset()
    right = hand(offset=(0.2, 0.0))
    first = smooth(right.points, now=5.0)
    assert np.allclose(first, right.points), "a reset hand starts where it is"


def test_a_long_gap_is_treated_as_a_new_frame_not_a_fast_one():
    """The camera stalls; the next frame must not be read as enormous speed."""
    smooth = ht._OneEuro()
    smooth(hand().points, now=1.0)
    moved = hand(offset=(0.1, 0.0))
    out = smooth(moved.points, now=9.0)          # eight seconds later
    assert np.all(np.isfinite(out))
    assert float(np.max(np.abs(out[:, :2]))) < 2.0, "no wild extrapolation"


# ───────────────────── a finger is out by its angle ─────────────────────


def test_a_straight_finger_and_a_curled_one():
    straight = hand(out=("index",))
    assert hm.joint_angle(straight, ht.INDEX_MCP, ht.INDEX_PIP, ht.INDEX_TIP) > 150
    curled = hand(out=())
    assert hm.joint_angle(curled, ht.INDEX_MCP, ht.INDEX_PIP, ht.INDEX_TIP) < 100


def test_the_angle_survives_a_hand_tilted_towards_the_camera():
    """Why the measure changed. Tilting the hand foreshortens every distance from the
    wrist equally, so "tip further than the middle joint" weakens - while the angle at
    the joint is unchanged, because it is the same shape seen shorter."""
    flat = hand(out=("index", "middle", "ring", "pinky"))
    tilted = hand(out=("index", "middle", "ring", "pinky"))
    # Squash towards the wrist: a hand pointing at the lens, 60% foreshortened.
    wrist = tilted.points[ht.WRIST].copy()
    tilted.points[:, :2] = wrist[:2] + (tilted.points[:, :2] - wrist[:2]) * 0.6

    before = hm.joint_angle(flat, ht.INDEX_MCP, ht.INDEX_PIP, ht.INDEX_TIP)
    after = hm.joint_angle(tilted, ht.INDEX_MCP, ht.INDEX_PIP, ht.INDEX_TIP)
    assert abs(before - after) < 1.0, f"{before:.0f}deg became {after:.0f}deg"
    assert hm.fingers_out(tilted)["index"] is True, "still out, as anyone can see"


def test_a_finger_at_the_boundary_does_not_flicker():
    """The dead band. A finger bent to just inside the threshold stays as it was, which
    is what stops two fingers becoming three and back several times a second."""
    straight = hand(out=("index", "middle"))
    # Bend the index to 135 degrees: past "straight" (148) but short of "bent" (125).
    wrist = straight.points[ht.WRIST]
    pip = straight.points[ht.INDEX_PIP]
    mcp = straight.points[ht.INDEX_MCP]
    towards = (mcp - pip)
    straight.points[ht.INDEX_TIP] = pip - towards * 0.8 + np.array(
        [0.035, 0.0, 0.0], dtype=np.float32)
    angle = hm.joint_angle(straight, ht.INDEX_MCP, ht.INDEX_PIP, ht.INDEX_TIP)
    assert hm.BENT_DEGREES < angle < hm.STRAIGHT_DEGREES, f"{angle:.0f}deg is the grey zone"

    assert hm.fingers_out(straight, previous={"index": True})["index"] is True
    assert hm.fingers_out(straight, previous={"index": False})["index"] is False


def test_the_poses_still_come_out_right():
    """The whole point of the change is that these keep working while being measured a
    better way."""
    machine = hm.HandMouse(mouse=None, settings=hm.Settings(), screen=(0, 0, 1920, 1080))
    assert machine.detect_pose(hand(out=("index", "middle", "ring", "pinky"))) == hm.OPEN_PALM
    assert machine.detect_pose(hand(out=("index",))) == hm.INDEX_ONLY
    assert machine.detect_pose(hand(out=("index", "middle"))) == hm.TWO_FINGERS
    assert machine.detect_pose(hand(out=())) == hm.FIST


# ───────────────────────── the camera mode ─────────────────────────


def test_the_fast_backend_leads_where_it_is_reliable():
    """MSMF measured 30.3 fps against DirectShow's 16.6 at the same resolution, so
    it led the list - until OpenCV 5.0, whose MSMF backend hangs on open. So DSHOW
    leads on cv2 >= 5 (a camera that opens beats a faster one that never does) and
    MSMF leads on the 4.x line that measured well. Either way both are present."""
    names = [name for name, _flag in ht._backends()]
    assert names[0] == ("dshow" if ht._cv2_major() >= 5 else "msmf")
    assert "msmf" in names and "dshow" in names, "and it still falls back"


def test_the_mode_asked_for_is_the_fast_one():
    assert (ht.CAPTURE_WIDTH, ht.CAPTURE_HEIGHT) == (640, 360)
    assert ht.CAPTURE_FPS == 30


def test_the_backend_can_be_pinned(monkeypatch):
    monkeypatch.setenv("DEXTER_HAND_BACKEND", "dshow")
    assert ht._backends()[0][0] == "dshow"
