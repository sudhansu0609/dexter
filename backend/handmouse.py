"""
The hand as a mouse (v13).

"Can we have hand tracking work as a mouse using the webcam - click, right click,
scroll?" Yes, and the whole design question is *which* hand poses, because a
gesture mouse that clicks when you did not mean it is worse than no mouse at all.

The poses, chosen so that each one is a distinct shape rather than a threshold on
the same shape:

  point           index finger out, the rest curled      -> the pointer follows
                                                            your fingertip
  pinch           thumb tip meets index tip              -> left button, held
                                                            while you hold it, so
                                                            a tap is a click and a
                                                            hold-and-move is a drag
  middle pinch    thumb tip meets middle fingertip       -> right button
  two fingers     index and middle out together          -> scroll with the hand's
                                                            vertical movement
  open palm       all five out                           -> also moves the pointer:
                                                            it is what a person
                                                            does first, and making
                                                            it do nothing read as
                                                            "the hand mouse is
                                                            broken" (v13.2)
  fist            nothing out                            -> park: nothing moves,
                                                            any button is released

Three rules make it usable rather than a novelty:

* **Hysteresis on every pinch.** One threshold to close, a looser one to open, so
  a hand trembling at the boundary does not machine-gun clicks.
* **The pointer is absolute, not relative.** A rectangle in the middle of the
  camera image maps onto the screen, so your hand has a *place* - you can look at
  where your hand is and know where the pointer is, and drift cannot accumulate.
* **It can always be stopped without the mouse.** Ctrl+Alt+H is registered as a
  kill switch the moment tracking starts, Esc in the preview window does the same,
  the buttons are released when a hand leaves the frame, and `dry_run` drives the
  whole thing into a recorder so the poses can be rehearsed with the real pointer
  untouched.
* **It shows its work.** A small preview window (`hand_preview.py`, on by default)
  draws the skeleton it tracked, the pose it decided and the palm score when it
  found nothing. Without it, "the camera cannot see my hand" and "my hand is in a
  pose that deliberately does nothing" are indistinguishable from the outside -
  which is exactly how the first cut of this module was reported as broken.
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

import hand_preview
import hand_tracker as ht
import mouse_input

#: What a hand can be asked to *do*. These names are also what the status line,
#: the preview window and the tests have shown since v13 - which is why the first
#: three are named after the pose that used to be the only way to get them
#: ("pinch" is the left button). The ones added in v13.6 are named for the effect.
POINT = "point"
PINCH = "pinch"
RIGHT_PINCH = "right-pinch"
MIDDLE = "middle"
DOUBLE = "double-click"
SCROLL = "scroll"
PARK = "park"
#: Not an action: "there is no hand in the frame".
NONE = "none"

#: The action catalogue, in the order the settings panel offers them.
ACTIONS: Dict[str, str] = {
    POINT: "Move the pointer",
    PINCH: "Left button, held (click and drag)",
    RIGHT_PINCH: "Right button, held",
    MIDDLE: "Middle button, held",
    DOUBLE: "Double click, once per pose",
    SCROLL: "Scroll with the hand's movement",
    PARK: "Nothing — release every button",
}

#: The hand *shapes* the tracker can tell apart. v13.6 split these from the
#: actions above: until then the pose and what it did were one string, so "make a
#: fist the right click" was a code change rather than a setting. Each one is a
#: distinct shape rather than a threshold on the same shape, which is what keeps
#: them from being confused for each other at 20 fps.
OPEN_PALM = "open-palm"
INDEX_ONLY = "index"
TWO_FINGERS = "two-fingers"
THREE_FINGERS = "three-fingers"
FIST = "fist"
PINCH_INDEX = "pinch-index"
PINCH_MIDDLE = "pinch-middle"
#: Any shape that is none of the above (an index and a pinky, say). It is bindable
#: rather than hidden, because a hand makes shapes nobody planned for and
#: "sometimes it does nothing" is the worst thing a gesture mouse can do.
OTHER = "other"

#: The short name, for a settings row or a spoken reply...
POSES: Dict[str, str] = {
    OPEN_PALM: "Open palm",
    INDEX_ONLY: "Index finger",
    TWO_FINGERS: "Two fingers",
    THREE_FINGERS: "Three fingers",
    FIST: "Fist",
    PINCH_INDEX: "Pinch",
    PINCH_MIDDLE: "Middle pinch",
    OTHER: "Any other shape",
}

#: ...and how to make it, which is the half that stops a gesture map from being a
#: guessing game.
POSE_HINTS: Dict[str, str] = {
    OPEN_PALM: "all four fingers out",
    INDEX_ONLY: "index out, the rest curled",
    TWO_FINGERS: "index and middle out together",
    THREE_FINGERS: "index, middle and ring out",
    FIST: "nothing out",
    PINCH_INDEX: "thumb touching the index fingertip",
    PINCH_MIDDLE: "thumb touching the middle fingertip",
    OTHER: "anything the list above does not describe",
}

#: The defaults, which are exactly what v13.2-v13.5 did, so nobody's hand mouse
#: changes behaviour by upgrading. An open palm steers (v13.2: making it do
#: nothing was the whole of "I can't move the cursor"), a fist parks because it is
#: the one pose nobody makes by accident.
POSE_DEFAULTS: Dict[str, str] = {
    OPEN_PALM: POINT,
    INDEX_ONLY: POINT,
    TWO_FINGERS: SCROLL,
    THREE_FINGERS: POINT,
    FIST: PARK,
    PINCH_INDEX: PINCH,
    PINCH_MIDDLE: RIGHT_PINCH,
    OTHER: POINT,
}


#: What a person calls these things out loud. "Make a fist the right click" has to
#: work, and nobody says "pinch-middle". Longest match first, so "open palm" is not
#: read as "palm" on its way to something else.
POSE_WORDS: Tuple[Tuple[str, str], ...] = (
    ("open palm", OPEN_PALM), ("open hand", OPEN_PALM), ("flat hand", OPEN_PALM),
    ("palm", OPEN_PALM), ("all five", OPEN_PALM), ("all fingers", OPEN_PALM),
    ("index finger", INDEX_ONLY), ("one finger", INDEX_ONLY), ("pointing", INDEX_ONLY),
    ("point", INDEX_ONLY), ("index", INDEX_ONLY),
    ("two fingers", TWO_FINGERS), ("peace", TWO_FINGERS), ("victory", TWO_FINGERS),
    ("three fingers", THREE_FINGERS), ("three", THREE_FINGERS),
    ("fist", FIST), ("closed hand", FIST), ("closed fist", FIST),
    ("middle pinch", PINCH_MIDDLE), ("thumb and middle", PINCH_MIDDLE),
    ("middle finger pinch", PINCH_MIDDLE),
    ("pinch", PINCH_INDEX), ("thumb and index", PINCH_INDEX),
    ("anything else", OTHER), ("any other", OTHER), ("other", OTHER),
)

ACTION_WORDS: Tuple[Tuple[str, str], ...] = (
    ("double click", DOUBLE), ("double", DOUBLE),
    ("right click", RIGHT_PINCH), ("right button", RIGHT_PINCH), ("right", RIGHT_PINCH),
    ("middle click", MIDDLE), ("middle button", MIDDLE), ("wheel click", MIDDLE),
    ("left click", PINCH), ("left button", PINCH), ("click", PINCH), ("drag", PINCH),
    ("scroll", SCROLL), ("scrolling", SCROLL),
    ("move", POINT), ("pointer", POINT), ("cursor", POINT), ("steer", POINT),
    ("nothing", PARK), ("park", PARK), ("ignore", PARK), ("stop", PARK),
    ("disable", PARK), ("off", PARK),
)


def _match(words: Tuple[Tuple[str, str], ...], text: str) -> Optional[str]:
    lowered = f" {(text or '').strip().lower()} "
    for phrase, value in sorted(words, key=lambda pair: -len(pair[0])):
        if phrase in lowered:
            return value
    return None


def normalize_pose(text: str) -> Optional[str]:
    """"a fist" -> "fist"; "thumb and middle finger" -> "pinch-middle"."""
    if (text or "").strip().lower() in POSES:
        return text.strip().lower()
    return _match(POSE_WORDS, text)


def normalize_action(text: str) -> Optional[str]:
    """"right click" -> "right-pinch"; "do nothing" -> "park"."""
    if (text or "").strip().lower() in ACTIONS:
        return text.strip().lower()
    return _match(ACTION_WORDS, text)


#: Short, spoken forms of each action, for describing the map out loud.
ACTION_SPOKEN: Dict[str, str] = {
    POINT: "move", PINCH: "click", RIGHT_PINCH: "right click",
    MIDDLE: "middle click", DOUBLE: "double click", SCROLL: "scroll",
    PARK: "do nothing",
}


def gesture_sentence(bindings: Optional[Dict[str, str]] = None) -> str:
    """"Point or open your palm to move, pinch to click, fist to do nothing."

    Built from the live map rather than written out, because the moment the
    bindings became a setting, any sentence naming the poses could be wrong - and
    the one in the start tool's reply *was*, still saying "open palm to park" two
    releases after an open palm started steering.
    """
    bound = bindings or default_bindings()
    by_action: Dict[str, List[str]] = {}
    for pose in POSES:
        if pose == OTHER:
            continue
        by_action.setdefault(bound.get(pose) or POSE_DEFAULTS[pose], []).append(POSES[pose].lower())
    parts = [f"{' or '.join(names)} to {ACTION_SPOKEN.get(action, action)}"
             for action, names in by_action.items()]
    return "; ".join(parts)


def pose_env(pose: str) -> str:
    """`DEXTER_HAND_POSE_PINCH_INDEX` for `pinch-index`.

    One variable per pose rather than one JSON blob, so each binding is an
    ordinary setting: it persists, it shows up in the settings panel, and it can
    be changed while tracking is running.
    """
    return "DEXTER_HAND_POSE_" + pose.upper().replace("-", "_")


def default_bindings() -> Dict[str, str]:
    """Pose -> action, read from the environment *now* (see `_env_float`)."""
    bound: Dict[str, str] = {}
    for pose, fallback in POSE_DEFAULTS.items():
        wanted = (os.environ.get(pose_env(pose)) or "").strip().lower()
        bound[pose] = wanted if wanted in ACTIONS else fallback
    return bound


#: Slowest and fastest the pointer can be set to. At 0.5 the control rectangle is
#: twice the size - the whole frame, near enough - which is as precise as a webcam
#: allows. At 2.5 a few centimetres of hand crosses the screen, which is as twitchy
#: as is usable.
MIN_SPEED, MAX_SPEED = 0.5, 2.5


def scaled_region(region: Tuple[float, float, float, float],
                  speed: float) -> Tuple[float, float, float, float]:
    """Shrink or grow the control rectangle about its centre.

    Faster means a *smaller* rectangle: the same hand movement covers more screen.
    Kept inside the frame, because a rectangle hanging off the edge would make the
    far side of the screen unreachable - the hand would have to leave the picture.
    """
    x0, y0, x1, y1 = region
    speed = min(MAX_SPEED, max(MIN_SPEED, float(speed or 1.0)))
    if abs(speed - 1.0) < 1e-6:
        return region
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half_w, half_h = (x1 - x0) / 2.0 / speed, (y1 - y0) / 2.0 / speed
    # Never wider than the frame, and never so small that the pointer is unusable.
    half_w = min(half_w, min(cx, 1.0 - cx))
    half_h = min(half_h, min(cy, 1.0 - cy))
    half_w, half_h = max(half_w, 0.04), max(half_h, 0.04)
    return (cx - half_w, cy - half_h, cx + half_w, cy + half_h)


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _env_float(name: str, default: float) -> float:
    """Read the knob *now*, not at import: Settings is built when tracking starts,
    so changing it in the Settings panel takes effect on the next start rather
    than on the next launch of Dexter."""
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """Everything tunable, and where each number comes from."""

    #: The part of the camera frame that maps onto the whole screen. Smaller than
    #: the frame because the edges of a webcam image are where a hand is
    #: half-visible and the landmarks are worst.
    region: Tuple[float, float, float, float] = (0.18, 0.14, 0.82, 0.78)
    #: How far the pointer travels for a given hand movement (v13.8).
    #:
    #: The pointer is **absolute**, not relative - a rectangle in the camera image
    #: maps onto the whole screen - so "speed" is the size of that rectangle, and
    #: this scales it about its centre. Above 1 the rectangle shrinks: less hand
    #: movement crosses the screen, which feels faster and twitchier. Below 1 it
    #: grows: more movement per pixel, which is slower and much easier to aim with.
    #: Clamped, because a rectangle of nothing makes the pointer unusable and one
    #: bigger than the frame puts the corners of the screen out of reach.
    speed: float = field(default_factory=lambda: _env_float("DEXTER_HAND_SPEED", 1.0))
    #: Exponential smoothing on the pointer. 0 = no smoothing (jittery),
    #: 0.9 = syrup. 0.6 is steady without feeling laggy at 20-ish fps.
    smoothing: float = field(default_factory=lambda: _env_float("DEXTER_HAND_SMOOTHING", 0.6))
    #: Pinch closes below this many hand-widths, opens above the second.
    pinch_on: float = field(default_factory=lambda: _env_float("DEXTER_HAND_PINCH_ON", 0.45))
    pinch_off: float = field(default_factory=lambda: _env_float("DEXTER_HAND_PINCH_OFF", 0.62))
    #: A finger counts as out when its tip is this much further from the wrist
    #: than its middle joint.
    extend_ratio: float = 1.08
    #: How far past its own knuckle a fingertip has to be for a pinch with that
    #: finger to count. Measured on real hands: an open hand reaches 2.0-2.4, a
    #: **fist** 0.87-0.96 - and in a fist the thumb tip sits right next to the
    #: middle fingertip, so without this a closed hand was read as a right click
    #: (found the first time a fist was put in front of the camera, v13.2).
    pinch_reach: float = field(default_factory=lambda: _env_float("DEXTER_HAND_PINCH_REACH", 1.25))
    #: Wheel notches per hand-width of vertical movement in scroll pose.
    scroll_gain: float = field(default_factory=lambda: _env_float("DEXTER_HAND_SCROLL_GAIN", 9.0))
    #: Movement below this many pixels is not sent: it is camera noise.
    dead_zone_px: float = 2.0
    #: ── making a click land where you aimed (v13.8) ──
    #:
    #: "Clicking seems difficult as it is unstable." Three things were fighting the
    #: owner's hand, and this is the first: **the pointer kept moving while the
    #: click was being made.** Pinching is a movement - the thumb and finger come
    #: together and the whole hand shifts - so the press landed a few pixels from
    #: wherever it had been aimed. The pointer is now held still from the moment a
    #: click pose appears; move further than this and it is obviously a drag, so the
    #: lock is dropped and the pointer follows again.
    #: Sized from the geometry rather than guessed: at speed 1 the control rectangle
    #: is 0.64 of the frame mapped onto the screen, so one centimetre of hand at
    #: arm's length is about 60 screen pixels, and the incidental movement of making
    #: a pinch is half to one and a half of those. A deliberate drag is five
    #: centimetres - three hundred. 120 sits cleanly between them, and scales with
    #: `speed`, because the same hand movement covers more screen when faster.
    drag_threshold_px: float = field(
        default_factory=lambda: _env_float("DEXTER_HAND_DRAG_THRESHOLD", 120.0)
    )
    #: And never a drag before this long, however far the hand wobbles: a click has
    #: to be allowed to land first. The same "tap or drag?" rule a touchscreen uses -
    #: time, then distance.
    drag_after_s: float = 0.3
    #: The second: **one noisy frame should not be a click, or end one.** A button
    #: goes down only after the pose has been seen this many frames in a row, and
    #: comes up only after it has been gone that many. At ~20 fps two frames is
    #: about a tenth of a second - far below a deliberate tap, far above the single
    #: frame of landmark noise that was making clicks fire and drop on their own.
    confirm_frames: int = field(
        default_factory=lambda: max(1, int(_env_float("DEXTER_HAND_CONFIRM_FRAMES", 2)))
    )
    #: The third: **a pinch curls the finger it pinches with.** `pinch_reach` exists
    #: to stop a fist being read as a right click, but holding a pinch lowers that
    #: same measurement, so a held click could fail the test that let it start and
    #: be dropped mid-gesture. While a pinch is held the bar is this much of its
    #: usual value.
    held_reach_factor: float = 0.72
    #: No hand for this long: release everything. A button left down because a
    #: hand left the frame is the worst failure this code has.
    idle_release_s: float = 0.4
    #: The most clicks a second this will ever produce, whatever the hand does.
    max_clicks_per_second: float = 8.0
    #: Stop by itself after this long (minutes). 0 = never.
    max_minutes: float = field(
        default_factory=lambda: _env_float("DEXTER_HAND_MAX_MINUTES", 120.0)
    )
    #: Which pose does what (v13.6). Read from the environment when tracking
    #: starts, and re-readable live through `HandMouse.rebind()`.
    bindings: Dict[str, str] = field(default_factory=default_bindings)

    def __post_init__(self) -> None:
        # The open threshold has to be the looser of the two or the hysteresis
        # runs backwards and a pinch chatters - easy to get wrong now that
        # `pinch_on` is a setting somebody can type into.
        if self.pinch_off <= self.pinch_on:
            self.pinch_off = self.pinch_on + 0.15
        self.speed = min(MAX_SPEED, max(MIN_SPEED, float(self.speed)))
        self.region = scaled_region(self.region, self.speed)


#: A finger is out when its middle joint is this straight, in degrees (v13.8).
#: Two thresholds, not one: a finger already counted as out stays out until it bends
#: past the lower figure. Without that dead band a finger resting near the boundary
#: flickers, and a flickering finger is a flickering *pose* - two fingers becoming
#: three and back several times a second, which is what "the gestures are not that
#: good" feels like from the outside.
STRAIGHT_DEGREES = 148.0
BENT_DEGREES = 125.0


def joint_angle(hand: ht.Hand, mcp: int, pip: int, tip: int) -> float:
    """How straight a finger is: the angle at its middle joint, in degrees.

    This replaces "is the tip further from the wrist than the middle joint is"
    (v13-v13.7), which is a reasonable proxy and wrong in two ordinary situations: a
    hand tilted towards the camera foreshortens every distance equally, and a finger
    can be bent at the knuckle while perfectly straight along its length. An angle at
    the joint is scale-free, rotation-free and measures the thing itself.
    """
    a = hand.point(mcp) - hand.point(pip)
    b = hand.point(tip) - hand.point(pip)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-6 or nb < 1e-6:
        return 180.0
    cosine = float(np.dot(a, b) / (na * nb))
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _extended(hand: ht.Hand, tip: int, pip: int, ratio: float) -> bool:
    """Kept for anything still asking the old question, now answered the new way."""
    mcp = {ht.INDEX_PIP: ht.INDEX_MCP, ht.MIDDLE_PIP: ht.MIDDLE_MCP,
           ht.RING_PIP: ht.RING_MCP, ht.PINKY_PIP: ht.PINKY_MCP}.get(pip, ht.WRIST)
    return joint_angle(hand, mcp, pip, tip) >= STRAIGHT_DEGREES


FINGERS = (
    ("index", ht.INDEX_TIP, ht.INDEX_PIP),
    ("middle", ht.MIDDLE_TIP, ht.MIDDLE_PIP),
    ("ring", ht.RING_TIP, ht.RING_PIP),
    ("pinky", ht.PINKY_TIP, ht.PINKY_PIP),
)

#: mcp, pip, tip per finger, for the joint angle.
FINGER_JOINTS = (
    ("index", ht.INDEX_MCP, ht.INDEX_PIP, ht.INDEX_TIP),
    ("middle", ht.MIDDLE_MCP, ht.MIDDLE_PIP, ht.MIDDLE_TIP),
    ("ring", ht.RING_MCP, ht.RING_PIP, ht.RING_TIP),
    ("pinky", ht.PINKY_MCP, ht.PINKY_PIP, ht.PINKY_TIP),
)


def reach(hand: ht.Hand, tip: int, mcp: int) -> float:
    """How far a fingertip is from the wrist, in units of its own knuckle.

    About 1.0 for a curled finger (the tip comes back to the knuckle) and 2.0+
    for an extended one, whatever the hand's size or distance from the camera.
    """
    wrist = hand.point(ht.WRIST)
    tip_d = float(np.linalg.norm(hand.point(tip) - wrist))
    mcp_d = float(np.linalg.norm(hand.point(mcp) - wrist))
    return tip_d / max(mcp_d, 1e-6)


def fingers_out(hand: ht.Hand, ratio: float = 1.08,
                previous: Optional[Dict[str, bool]] = None) -> Dict[str, bool]:
    """Which fingers are out, from the angle at each middle joint (v13.8).

    `previous` is last frame's answer, and passing it engages the dead band: a finger
    counted as out stays out until it bends past `BENT_DEGREES`. That is what stops a
    finger hovering at the threshold from turning two fingers into three and back
    several times a second. `ratio` is accepted for the callers that still pass it;
    the angle is what decides now.
    """
    state: Dict[str, bool] = {}
    for name, mcp, pip, tip in FINGER_JOINTS:
        angle = joint_angle(hand, mcp, pip, tip)
        was = bool((previous or {}).get(name, False))
        state[name] = angle >= (BENT_DEGREES if was else STRAIGHT_DEGREES)
    # The thumb folds sideways, so no joint angle describes it well; its tip's
    # distance from its own knuckle does.
    thumb_was = bool((previous or {}).get("thumb", False))
    reach_thumb = hand.distance(ht.THUMB_TIP, ht.THUMB_MCP)
    state["thumb"] = reach_thumb > (0.48 if thumb_was else 0.55)
    return state


@dataclass
class Frame:
    """What one processed frame did, for the status line and the tests."""

    gesture: str = NONE
    #: The shape the hand was in, before the bindings decided what it meant. Both
    #: are reported because "it saw my hand but did nothing" and "it never saw my
    #: hand" are different problems with the same symptom.
    pose: str = NONE
    actions: List[Tuple[str, Any]] = field(default_factory=list)
    cursor: Optional[Tuple[int, int]] = None
    pinched: bool = False
    right_pinched: bool = False
    #: The pointer is being held still so a click lands where it was aimed (v13.8).
    locked: bool = False


class HandMouse:
    """The gesture state machine, and the thread that feeds it camera frames.

    `step()` is pure enough to test: hand in, mouse calls out, no camera and no
    clock of its own.
    """

    def __init__(
        self,
        mouse: Any = None,
        settings: Optional[Settings] = None,
        screen: Optional[Tuple[int, int, int, int]] = None,
    ) -> None:
        self.settings = settings or Settings()
        self.mouse = mouse if mouse is not None else mouse_input.System()
        self._screen = screen or self.mouse.virtual_screen()
        self._smoothed: Optional[Tuple[float, float]] = None
        #: Which buttons this code is holding down. One dict rather than a flag
        #: each, because v13.6 added a third (middle) and a pose change has to be
        #: able to release whatever was held without naming it.
        self._down: Dict[str, bool] = {"left": False, "right": False, "middle": False}
        #: The pinch pose currently being held open by hysteresis, if any. This
        #: used to be inferred from the button state, which stopped being the same
        #: thing the moment a pinch could be bound to something other than a click.
        self._held_pinch: Optional[str] = None
        #: The pose a one-shot action (a double click) has already fired for, so
        #: holding the pose does not fire it forty times a second.
        self._fired_pose = ""
        #: Where the pointer was when a click pose appeared, and whether the lock
        #: has been broken by a deliberate drag (v13.8).
        self._locked_at: Optional[Tuple[int, int]] = None
        self._locked_since = 0.0
        self._dragging = False
        #: Consecutive frames each button has been wanted, or not wanted. Nothing is
        #: pressed or released on the strength of a single frame.
        self._wanted_for: Dict[str, int] = {"left": 0, "right": 0, "middle": 0}
        #: Last frame's answer to "which fingers are out", for the dead band.
        self._fingers: Dict[str, bool] = {}
        self._scroll_anchor: Optional[float] = None
        self._last_seen = 0.0
        self._last_click = 0.0
        self._last_cursor: Optional[Tuple[int, int]] = None
        self.gesture = NONE
        self.pose = NONE
        self.frames = 0
        self.hand_frames = 0
        self.started_at = 0.0
        self.last_error = ""
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.camera_index: Optional[int] = None
        self.engine = ""
        self.dry_run = False
        self.fps = 0.0
        #: The little window showing what the tracker sees (v13.2). On by default:
        #: without it, "it can't see my hand" and "it sees my hand but I am making
        #: the wrong pose" look identical from the outside.
        self.preview_wanted = _env_flag("DEXTER_HAND_PREVIEW", True)
        self.preview_state: Dict[str, Any] = {}
        #: How close the camera came to seeing a hand, for an honest status line.
        self.palm_score = 0.0
        self.best_score = 0.0

    # ───────────────────────── buttons, as read by everyone else ─────────────

    @property
    def _pinched(self) -> bool:
        return self._down["left"]

    @property
    def _right_pinched(self) -> bool:
        return self._down["right"]

    # ───────────────────────── the state machine ─────────────────────────

    def _to_screen(self, x: float, y: float) -> Tuple[int, int]:
        left, top, width, height = self._screen
        x0, y0, x1, y1 = self.settings.region
        # Mirror x: the camera sees you face-on, so moving your hand right moves
        # your image left. Without this the pointer goes the wrong way.
        u = 1.0 - (x - x0) / max(1e-6, x1 - x0)
        v = (y - y0) / max(1e-6, y1 - y0)
        u = min(1.0, max(0.0, u))
        v = min(1.0, max(0.0, v))
        return int(left + u * (width - 1)), int(top + v * (height - 1))

    def _smooth(self, point: Tuple[float, float]) -> Tuple[float, float]:
        alpha = min(0.95, max(0.0, self.settings.smoothing))
        if self._smoothed is None:
            self._smoothed = point
        else:
            self._smoothed = (
                self._smoothed[0] * alpha + point[0] * (1 - alpha),
                self._smoothed[1] * alpha + point[1] * (1 - alpha),
            )
        return self._smoothed

    def detect_pose(self, hand: ht.Hand) -> str:
        """Which *shape* the hand is in. One shape per pose, never a tie.

        Deliberately says nothing about what it means: that is the bindings' job
        (v13.6). A pose detector that also decided the action could not be
        customised without editing it.
        """
        out = fingers_out(hand, self.settings.extend_ratio, previous=self._fingers)
        self._fingers = out
        pinch = hand.distance(ht.THUMB_TIP, ht.INDEX_TIP)
        middle_pinch = hand.distance(ht.THUMB_TIP, ht.MIDDLE_TIP)
        on, off = self.settings.pinch_on, self.settings.pinch_off
        # A pinch is the thumb meeting a finger that is *reaching out* to it. In a
        # fist the thumb lies against the middle fingertip at 0.35 hand-widths -
        # well inside any pinch threshold - so the reach test is what keeps a
        # closed hand from being a right click.
        bar = self.settings.pinch_reach
        # A pinch you are *holding* curls the finger doing the pinching, which
        # lowers this very measurement - so the test that allowed the click to start
        # could fail while it was held, drop the button, and read as a click that
        # "didn't take" (v13.8). The bar is relaxed for the pinch already in
        # progress, and only for that one.
        held_bar = bar * max(0.1, min(1.0, self.settings.held_reach_factor))
        index_bar = held_bar if self._held_pinch == PINCH_INDEX else bar
        middle_bar = held_bar if self._held_pinch == PINCH_MIDDLE else bar
        index_ready = reach(hand, ht.INDEX_TIP, ht.INDEX_MCP) >= index_bar
        middle_ready = reach(hand, ht.MIDDLE_TIP, ht.MIDDLE_MCP) >= middle_bar

        gap = {PINCH_INDEX: pinch, PINCH_MIDDLE: middle_pinch}
        ready = {PINCH_INDEX: index_ready, PINCH_MIDDLE: middle_ready}
        # Both pinches can be inside the threshold at once - the index and middle
        # tips are close together on a real hand - so the *nearer* one is the one
        # meant.
        closed = sorted((gap[p], p) for p in (PINCH_INDEX, PINCH_MIDDLE)
                        if gap[p] < on and ready[p])
        held = self._held_pinch
        # Hysteresis: what is already closed stays closed until it opens wide. The
        # exception is the other pinch being plainly nearer - moving from an index
        # pinch to a middle one leaves the index gap well inside the *open*
        # threshold, and sticking on the old one would make the swap impossible
        # without opening the hand in between.
        if held and ready[held] and gap[held] < off:
            if not (closed and closed[0][0] < gap[held]):
                return held
        if closed:
            self._held_pinch = closed[0][1]
            return self._held_pinch
        self._held_pinch = None

        fingers = {name for name in ("index", "middle", "ring", "pinky") if out[name]}
        if not fingers:
            return FIST
        if fingers == {"index"}:
            return INDEX_ONLY
        if fingers == {"index", "middle"}:
            return TWO_FINGERS
        if fingers == {"index", "middle", "ring"}:
            return THREE_FINGERS
        if len(fingers) == 4:
            return OPEN_PALM
        return OTHER

    def action_for(self, pose: str) -> str:
        """What this pose is bound to, falling back to the shipped default.

        An unknown action name never silently becomes "do nothing": a typo in
        settings.json costs you the default, not the use of your hand.
        """
        wanted = (self.settings.bindings or {}).get(pose)
        if wanted in ACTIONS:
            return wanted
        return POSE_DEFAULTS.get(pose, POINT)

    def rebind(self, bindings: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Re-read every knob, live, while tracking is running.

        Changing a gesture is the kind of thing you do *because* the current one
        is not working, so waiting for the next start would be the wrong answer -
        it would mean stopping the thing you are trying to fix. `Settings` reads
        the environment, and the settings panel writes it, so rebuilding it is the
        whole of "apply now"; the smoothing and the pinch threshold come along for
        the ride.
        """
        fresh = Settings()
        for pose, action in (bindings or {}).items():
            if pose in POSE_DEFAULTS and action in ACTIONS:
                fresh.bindings[pose] = action
        self.settings = fresh
        # Whatever was held under the old map is not held under the new one: a
        # button left down by a binding that no longer exists would be unkillable
        # without the mouse this is replacing.
        self._release_all()
        self._fired_pose = ""
        return dict(fresh.bindings)

    def classify(self, hand: ht.Hand) -> str:
        """The pose this hand is in, as the action it is bound to."""
        self.pose = self.detect_pose(hand)
        return self.action_for(self.pose)

    def step(self, hand: Optional[ht.Hand], now: Optional[float] = None) -> Frame:
        """One frame. Returns what was done; calls the mouse as a side effect."""
        now = time.monotonic() if now is None else float(now)
        self.frames += 1
        frame = Frame()
        if hand is None:
            self.gesture = NONE
            self.pose = NONE
            if now - self._last_seen > self.settings.idle_release_s:
                frame.actions += self._release_all()
            frame.gesture = NONE
            frame.pose = NONE
            return frame

        self.hand_frames += 1
        self._last_seen = now
        gesture = self.classify(hand)
        self.gesture = gesture
        frame.gesture = gesture
        frame.pose = self.pose

        # ── buttons ──
        # Let go first, then press: a pose that swaps one button for another has
        # to release the old one in the same frame, or a drag continues under a
        # right click. Releases are never rate limited; presses always are.
        wants = {"left": gesture == PINCH, "right": gesture == RIGHT_PINCH,
                 "middle": gesture == MIDDLE}
        # Neither pressed nor released on one frame's evidence (v13.8): a single
        # noisy landmark was enough to fire a click nobody made, or to drop one
        # halfway through. `confirm_frames` consecutive frames, either way.
        # Park is the emergency stop, so it is exempt: "any button is released" has
        # to mean *now*, not two frames from now. Same reasoning as a hand leaving
        # the frame, which has always bypassed every filter in here.
        needed = 1 if gesture == PARK else max(1, int(self.settings.confirm_frames))
        settled = {}
        for button, wanted in wants.items():
            # Positive: consecutive frames this button has been wanted. Negative:
            # consecutive frames it has not. Either way it takes `needed` of them.
            streak = self._wanted_for[button]
            if wanted:
                self._wanted_for[button] = streak + 1 if streak > 0 else 1
            else:
                self._wanted_for[button] = streak - 1 if streak < 0 else -1
            settled[button] = abs(self._wanted_for[button]) >= needed

        for button, wanted in wants.items():
            if self._down[button] and not wanted and settled[button]:
                frame.actions += self._set_button(button, False)
        for button, wanted in wants.items():
            if (wanted and not self._down[button] and settled[button]
                    and self._allow_click(now)):
                frame.actions += self._set_button(button, True)
        frame.pinched = self._pinched
        frame.right_pinched = self._right_pinched

        # ── a double click: one per pose, not one per frame ──
        if gesture == DOUBLE:
            if self._fired_pose != self.pose:
                if self._allow_click(now):
                    self.mouse.click("left", double=True)
                    frame.actions.append(("double", "left"))
                    self._fired_pose = self.pose
            self._smoothed = None  # the pointer stays where you aimed it
            return frame
        self._fired_pose = ""

        # ── scrolling ──
        if gesture == SCROLL:
            middle_y = float(hand.point(ht.MIDDLE_TIP)[1])
            if self._scroll_anchor is None:
                self._scroll_anchor = middle_y
            travel = (self._scroll_anchor - middle_y) / hand.size
            notches = travel * self.settings.scroll_gain
            if abs(notches) >= 1.0:
                whole = float(int(notches))
                self.mouse.scroll(whole)
                frame.actions.append(("scroll", whole))
                self._scroll_anchor = middle_y
            self._smoothed = None  # the pointer is parked; do not jump on return
            return frame
        self._scroll_anchor = None

        if gesture == PARK:
            self._smoothed = None
            return frame

        # ── the pointer ──
        # Anchored on the *pose*, not the action: a pinch that has been rebound to
        # the middle button is still held between thumb and finger, and that is the
        # point that moves least.
        anchor = self._anchor_point(hand, self.pose)
        smoothed = self._smooth((float(anchor[0]), float(anchor[1])))
        target = self._to_screen(*smoothed)

        # ── hold still while a click is being made (v13.8) ──
        #
        # The pointer used to keep following the hand through the whole gesture, and
        # pinching *is* a hand movement: the press landed a little away from wherever
        # it had been aimed, which is what "clicking is unstable" feels like. So from
        # the moment a click pose appears - before the button is even confirmed - the
        # pointer stops where it is. Moving well past that point is unmistakably a
        # drag, and then it follows again for the rest of the gesture.
        clicking = gesture in (PINCH, RIGHT_PINCH, MIDDLE) or any(self._down.values())
        if clicking:
            if self._locked_at is None:
                self._locked_at = self._last_cursor or target
                self._locked_since = now
                self._dragging = False
            if not self._dragging:
                reach_px = self.settings.drag_threshold_px * max(0.5, self.settings.speed)
                far = math.dist(target, self._locked_at) >= reach_px
                waited = (now - self._locked_since) >= self.settings.drag_after_s
                if far and waited:
                    self._dragging = True          # a real drag: let go of the lock
                else:
                    frame.cursor = self._last_cursor or self._locked_at
                    frame.locked = True
                    return frame
        else:
            self._locked_at = None
            self._dragging = False

        if self._last_cursor is None or math.dist(target, self._last_cursor) >= self.settings.dead_zone_px:
            self.mouse.move_to(*target)
            self._last_cursor = target
            frame.actions.append(("move_to", target))
        frame.cursor = self._last_cursor
        return frame

    def _anchor_point(self, hand: ht.Hand, what: str):
        """The point on the hand the pointer follows.

        The fingertip while pointing; the middle of the pinch while pinching,
        because that is what the hand is "holding" and it moves less. Shared with
        the preview so the red circle is drawn on the same point the pointer is
        actually using - a preview that shows something else is worse than none.

        Takes either a pose or an action, since the preview has one and `step` the
        other, and for the two pinches they mean the same thing.
        """
        if what in (PINCH, PINCH_INDEX):
            return (hand.point(ht.THUMB_TIP) + hand.point(ht.INDEX_TIP)) / 2.0
        if what in (RIGHT_PINCH, PINCH_MIDDLE):
            return (hand.point(ht.THUMB_TIP) + hand.point(ht.MIDDLE_TIP)) / 2.0
        return hand.point(ht.INDEX_TIP)

    def _allow_click(self, now: float) -> bool:
        """Rate limit, so no hand tremor can produce a click storm."""
        gap = 1.0 / max(0.5, self.settings.max_clicks_per_second)
        if now - self._last_click < gap:
            return False
        self._last_click = now
        return True

    def _set_button(self, button: str, down: bool) -> List[Tuple[str, Any]]:
        if self._down.get(button) == down:
            return []
        self._down[button] = down
        if down:
            self.mouse.down(button)
        else:
            self.mouse.up(button)
        return [("down" if down else "up", button)]

    def _release_all(self) -> List[Tuple[str, Any]]:
        actions: List[Tuple[str, Any]] = []
        for button in tuple(self._down):
            actions += self._set_button(button, False)
        self._smoothed = None
        self._scroll_anchor = None
        self._held_pinch = None
        self._locked_at = None
        self._dragging = False
        for button in self._wanted_for:
            self._wanted_for[button] = 0
        return actions

    # ───────────────────────── the camera thread ─────────────────────────

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self, camera: Optional[int] = None, dry_run: bool = False,
              preview: Optional[bool] = None) -> Dict[str, Any]:
        with self._lock:
            if self.running:
                return {"ok": True, "already": True, **self.status()}
            ready, why = ht.available()
            if not ready:
                return {"ok": False, "error": why}
            # Re-read the knobs: the Settings panel writes them into the
            # environment, and "it takes effect next time you start it" is a
            # promise this keeps.
            self.settings = Settings()
            self.dry_run = bool(dry_run)
            self.preview_wanted = (_env_flag("DEXTER_HAND_PREVIEW", True)
                                   if preview is None else bool(preview))
            self.palm_score = self.best_score = 0.0
            if self.dry_run:
                self.mouse = mouse_input.Recorder()
                self._screen = self.mouse.virtual_screen()
            elif not mouse_input.available():
                return {"ok": False, "error": "Moving the pointer needs Windows."}
            else:
                self.mouse = mouse_input.System()
                self._screen = self.mouse.virtual_screen()
            self._stop.clear()
            self.last_error = ""
            self.frames = self.hand_frames = 0
            self.started_at = time.time()
            self._install_kill_switch()
            ready_event = threading.Event()
            self._thread = threading.Thread(
                target=self._loop, args=(camera, ready_event), name="dexter-handmouse",
                daemon=True,
            )
            self._thread.start()
        # Wait for the camera to open (or fail) so the reply is about reality.
        ready_event.wait(timeout=12)
        if self.last_error:
            return {"ok": False, "error": self.last_error}
        return {"ok": True, **self.status()}

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._release_all()
        self._remove_kill_switch()
        ran = time.time() - self.started_at if self.started_at else 0.0
        return {"ok": True, "minutes": round(ran / 60.0, 1), "frames": self.frames,
                "handFrames": self.hand_frames}

    def _loop(self, camera: Optional[int], ready_event: threading.Event) -> None:
        capture = None
        try:
            capture, self.camera_index = ht.open_camera(camera)
            engine = ht.tracker()
            self.engine = engine.source
        except Exception as exc:
            self.last_error = str(exc)
            ready_event.set()
            return
        ready_event.set()
        preview = hand_preview.Preview(enabled=self.preview_wanted)
        if preview.enabled and not preview.open():
            print(f"[Hand] {preview.failed or 'the preview window is unavailable'}; "
                  "tracking without it.")
        self.preview_state = preview.state()
        last = time.monotonic()
        smoothed_fps = 0.0
        deadline = (self.started_at + self.settings.max_minutes * 60.0
                    if self.settings.max_minutes else 0.0)
        try:
            while not self._stop.is_set():
                if deadline and time.time() > deadline:
                    self.last_error = (
                        f"stopped itself after {self.settings.max_minutes:.0f} minutes "
                        "(DEXTER_HAND_MAX_MINUTES)"
                    )
                    break
                ok, frame = capture.read()
                if not ok:
                    self.last_error = "the webcam stopped delivering frames"
                    break
                try:
                    hands = engine.find(frame)
                except Exception as exc:
                    self.last_error = f"tracking failed: {exc}"
                    break
                hand = hands[0] if hands else None
                result = self.step(hand)
                self.best_score = max(self.best_score, float(hand.score) if hand else 0.0)
                self.palm_score = float(getattr(engine, "last_palm_score", 0.0) or 0.0)
                now = time.monotonic()
                gap = now - last
                last = now
                if gap > 0:
                    smoothed_fps = smoothed_fps * 0.8 + (1.0 / gap) * 0.2
                    self.fps = round(smoothed_fps, 1)
                # The preview is last: it reports what was decided, and it must
                # never be the reason tracking stops.
                preview.draw(
                    frame, hand, result.gesture, self.fps, self.settings.region,
                    (self._pinched, self._right_pinched, self._down["middle"]),
                    palm_score=self.palm_score, cursor=result.cursor,
                    anchor=self._anchor_point(hand, result.pose) if hand else None,
                    pose=result.pose,
                )
                if preview.quit_requested:
                    self.last_error = ""
                    print("[Hand] Preview closed with Esc; stopping.")
                    break
        finally:
            # Closed *then* recorded, or the status would keep claiming a window
            # that is gone.
            preview.close()
            self.preview_state = preview.state()
            try:
                if capture is not None:
                    capture.release()
            except Exception:
                pass
            self._release_all()
            self._remove_kill_switch()

    # ───────────────────────── the kill switch ─────────────────────────

    _HOTKEY = os.environ.get("DEXTER_HAND_HOTKEY", "ctrl+alt+h")

    def _install_kill_switch(self) -> None:
        """Ctrl+Alt+H stops tracking, because a pointer that has run away cannot
        be used to click a Stop button."""
        try:
            import keyboard  # type: ignore

            self._hotkey_handle = keyboard.add_hotkey(self._HOTKEY, self.stop)
        except Exception:
            self._hotkey_handle = None

    def _remove_kill_switch(self) -> None:
        handle = getattr(self, "_hotkey_handle", None)
        if handle is None:
            return
        try:
            import keyboard  # type: ignore

            keyboard.remove_hotkey(handle)
        except Exception:
            pass
        self._hotkey_handle = None

    # ───────────────────────── reporting ─────────────────────────

    def status(self) -> Dict[str, Any]:
        ready, why = ht.available()
        return {
            "running": self.running,
            "gesture": self.gesture,
            #: The shape, and what it is bound to, so the panel can say
            #: "open palm -> move the pointer" rather than one or the other.
            "pose": self.pose,
            "action": self.action_for(self.pose) if self.pose != NONE else NONE,
            "bindings": dict(self.settings.bindings or {}),
            "poses": [{"pose": pose, "label": POSES[pose], "hint": POSE_HINTS[pose],
                       "action": self.action_for(pose),
                       "default": POSE_DEFAULTS[pose]} for pose in POSES],
            "actions": [{"action": key, "label": label} for key, label in ACTIONS.items()],
            "buttons": dict(self._down),
            #: Why the pointer is not moving, when it is not (v13.8).
            "locked": self._locked_at is not None and not self._dragging,
            "dragging": self._dragging,
            "speed": round(float(self.settings.speed), 2),
            "region": [round(v, 3) for v in self.settings.region],
            "dragThresholdPx": self.settings.drag_threshold_px,
            "confirmFrames": self.settings.confirm_frames,
            "fps": self.fps,
            "camera": self.camera_index,
            "engine": self.engine,
            "dryRun": self.dry_run,
            "frames": self.frames,
            "handFrames": self.hand_frames,
            "seeingHand": bool(self.gesture != NONE),
            "leftDown": self._pinched,
            "rightDown": self._right_pinched,
            "minutes": round((time.time() - self.started_at) / 60.0, 1) if self.started_at else 0.0,
            "hotkey": self._HOTKEY,
            "ready": ready,
            "why": why,
            "error": self.last_error,
            # How close the camera came to seeing a hand, so "it doesn't work" has
            # a number attached: a palm under PALM_SCORE_MIN means the room, the
            # distance or the pose, not the code.
            "palmScore": round(self.palm_score, 3),
            "bestScore": round(self.best_score, 3),
            "palmNeeded": ht.PALM_SCORE_MIN,
            "preview": self.preview_state or {"enabled": self.preview_wanted,
                                              "open": False, "error": ""},
            "recorded": getattr(self.mouse, "events", None) and len(self.mouse.events) or 0,
        }


#: One per process: there is one webcam and one pointer.
hand_mouse = HandMouse()


def live_status() -> Dict[str, Any]:
    """The status, with the settings re-read first if nothing is tracking.

    `hand_mouse` is built when this module is imported, which can be before the
    stored settings are applied over .env - so an idle tracker's `Settings` is
    whatever .env said at import. A panel showing a gesture map that is not the one
    in force would be worse than showing none, and the panel is exactly what gets
    read when somebody is deciding which pose to change.
    """
    if not hand_mouse.running:
        hand_mouse.rebind()
        hand_mouse.preview_wanted = _env_flag("DEXTER_HAND_PREVIEW", True)
    return hand_mouse.status()
