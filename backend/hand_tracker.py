"""
Where the hand is, 21 points at a time (v13).

The webcam half of "hand tracking as a mouse". Two models, both MediaPipe's, both
run through onnxruntime:

  palm detection    192x192 -> 2016 anchored boxes + 7 palm keypoints
  hand landmarks    224x224 -> 21 (x, y, z) points, a confidence, a handedness

MediaPipe itself is not importable under this machine's Python (3.14; the wheels
stop at 3.12), which is why the models are driven directly. The `mediapipe`
backend is still here and preferred when it *is* importable, so a 3.12 venv gets
the faster, better-tuned pipeline for free and the rest of Dexter does not change.

The models are not in the repository - they are 4 MB each and they are not ours.
`ensure_models(download=True)` fetches them from the OpenCV Zoo into
`data/models/hand/`, which is what `hand_mouse_setup` calls. Without them,
`available()` is False and the tools say exactly what to run.

Detection is the expensive step, so it runs only when there is no hand; after
that, the landmark crop follows the hand from the previous frame and detection is
re-run when tracking confidence drops. That is MediaPipe's own strategy, and it
is the difference between 8 fps and 25 on a CPU.
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.environ.get("DEXTER_HAND_MODELS") or os.path.join(
    ROOT_DIR, "data", "models", "hand"
)

#: The OpenCV Zoo keeps these in Git LFS, so the download URL is the media host;
#: raw.githubusercontent.com serves a 132-byte pointer file instead (found the
#: hard way).
MODEL_FILES: Dict[str, Dict[str, str]] = {
    "palm": {
        "file": "palm_detection_mediapipe_2023feb.onnx",
        "url": ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
                "models/palm_detection_mediapipe/palm_detection_mediapipe_2023feb.onnx"),
    },
    "landmarks": {
        "file": "handpose_estimation_mediapipe_2023feb.onnx",
        "url": ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
                "models/handpose_estimation_mediapipe/handpose_estimation_mediapipe_2023feb.onnx"),
    },
}

PALM_INPUT = 192
LANDMARK_INPUT = 224
#: Below this the palm detector has not found a hand. 0.5 is the reference
#: implementation's own default (opencv_zoo) and MediaPipe's: 0.6 was a guess,
#: and on a webcam at arm's length in ordinary room light a real hand scores
#: between 0.4 and 0.8, so the stricter bar showed "no hand" to a hand that was
#: plainly there.
PALM_SCORE_MIN = float(os.environ.get("DEXTER_HAND_PALM_SCORE", "0.5") or 0.5)
#: Below this the landmark model is no longer tracking and detection re-runs.
TRACK_SCORE_MIN = float(os.environ.get("DEXTER_HAND_TRACK_SCORE", "0.5") or 0.5)

#: The 21 landmarks, by the names the gesture code uses.
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20


@dataclass
class Hand:
    """One hand in one frame. Coordinates are 0..1 of the frame, origin top-left."""

    points: np.ndarray          # (21, 3) float32
    score: float = 0.0
    handedness: float = 0.5     # >0.5 right hand, as the model reports it
    source: str = "onnx"

    def point(self, index: int) -> np.ndarray:
        return self.points[index][:2]

    @property
    def size(self) -> float:
        """Hand scale: wrist to middle-finger knuckle. Every distance below is
        divided by this, so a hand near the camera and one further away pinch at
        the same threshold."""
        span = float(np.linalg.norm(self.point(MIDDLE_MCP) - self.point(WRIST)))
        return max(span, 1e-3)

    def distance(self, a: int, b: int) -> float:
        """Distance between two landmarks, in hand-widths."""
        return float(np.linalg.norm(self.point(a) - self.point(b))) / self.size


class TrackerError(RuntimeError):
    """Something Dexter should say out loud."""


# ───────────────────────── models on disk ─────────────────────────


def model_path(kind: str) -> str:
    return os.path.join(MODELS_DIR, MODEL_FILES[kind]["file"])


def models_present() -> bool:
    return all(os.path.exists(model_path(kind)) and os.path.getsize(model_path(kind)) > 100_000
               for kind in MODEL_FILES)


def ensure_models(download: bool = False, timeout: float = 120.0) -> Dict[str, Any]:
    """Report (and optionally fetch) the two model files."""
    state: Dict[str, Any] = {"dir": MODELS_DIR, "models": {}, "downloaded": []}
    for kind, spec in MODEL_FILES.items():
        path = model_path(kind)
        if os.path.exists(path) and os.path.getsize(path) > 100_000:
            state["models"][kind] = {"path": path, "bytes": os.path.getsize(path)}
            continue
        if not download:
            state["models"][kind] = {"path": path, "bytes": 0, "missing": True}
            continue
        try:
            import requests

            os.makedirs(MODELS_DIR, exist_ok=True)
            res = requests.get(spec["url"], timeout=timeout, stream=True)
            res.raise_for_status()
            temp = path + ".part"
            with open(temp, "wb") as handle:
                for chunk in res.iter_content(chunk_size=1 << 16):
                    handle.write(chunk)
            if os.path.getsize(temp) < 100_000:
                os.remove(temp)
                raise TrackerError(
                    "the download came back too small to be the model (the host "
                    "served a Git LFS pointer, not the file)"
                )
            os.replace(temp, path)
            state["downloaded"].append(kind)
            state["models"][kind] = {"path": path, "bytes": os.path.getsize(path)}
        except Exception as exc:
            state["models"][kind] = {"path": path, "bytes": 0, "missing": True,
                                     "error": str(exc)}
    state["ready"] = all(not m.get("missing") for m in state["models"].values())
    return state


def packages() -> Dict[str, bool]:
    """Which optional pieces are importable, for an honest "can you do this"."""
    def _has(name: str) -> bool:
        try:
            __import__(name)
            return True
        except Exception:
            return False

    return {"cv2": _has("cv2"), "onnxruntime": _has("onnxruntime"),
            "mediapipe": _has("mediapipe"), "numpy": _has("numpy")}


def available() -> Tuple[bool, str]:
    """(can we track, why not)."""
    have = packages()
    if have["mediapipe"] and have["cv2"]:
        return True, ""
    missing = [name for name in ("cv2", "onnxruntime") if not have[name]]
    if missing:
        names = {"cv2": "opencv-python", "onnxruntime": "onnxruntime"}
        return False, ("hand tracking needs " + " and ".join(names[m] for m in missing)
                       + " (pip install " + " ".join(names[m] for m in missing) + ")")
    if not models_present():
        return False, ("the hand models aren't downloaded yet - say \"set up hand "
                       "tracking\" and I'll fetch them (8 MB)")
    return True, ""


# ───────────────────────── the ONNX pipeline ─────────────────────────


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def _palm_anchors() -> np.ndarray:
    """The 2016 anchor centres the palm model was trained against.

    MediaPipe's palm detector for a 192x192 input uses four layers: one at
    stride 8 with two anchors per cell (24x24x2 = 1152) and three at stride 16
    with two each (12x12x2 x3 = 864). 1152 + 864 = 2016, which is exactly the
    row count the model outputs - that is the check that this is right.
    """
    anchors: List[Tuple[float, float]] = []
    for stride, repeats in ((8, 2), (16, 6)):
        grid = PALM_INPUT // stride
        for y in range(grid):
            for x in range(grid):
                cx = (x + 0.5) / grid
                cy = (y + 0.5) / grid
                for _ in range(repeats):
                    anchors.append((cx, cy))
    return np.asarray(anchors, dtype=np.float32)


#: Landmark smoothing (v13.8). The single biggest thing that was missing: **every
#: pose decision read raw landmarks.** Whether a finger counts as out, how wide a
#: pinch is, whether a thumb is reaching - all of it was computed from coordinates
#: that jitter by a pixel or two per frame from sensor noise alone, so a hand held
#: perfectly still could flicker between poses, and a gesture could fire or drop on
#: nobody's intention.
#:
#: A fixed exponential filter cannot fix it: enough smoothing to kill the jitter adds
#: visible lag to a moving hand. The one-euro filter is the standard answer - it
#: smooths hard when the hand is still and barely at all when it moves, by deriving
#: its cutoff from the measured speed of the signal.
#: Tuned by measurement, not by the published defaults - those assume coordinates in
#: metres, and these are 0..1 of a frame, so the same hand speed is a hundredth of the
#: number and `beta` has to be correspondingly larger. Against a synthetic hand with
#: 1.5 px of sensor noise, error from the truth and frame-to-frame jitter in screen
#: pixels:
#:
#:                    still          slow move      fast move
#:     unfiltered     7.6 / 10.8     7.6 / 11.0     7.6 / 12.4
#:     1.2 Hz, b=8    2.8 /  1.8     5.3 /  2.9     8.9 /  6.2
#:     **1.2 Hz, b=30**  3.3 / 2.3    4.4 /  3.5     5.7 /  6.5
#:     1.2 Hz, b=60   3.9 /  2.8     4.5 /  4.1     5.3 /  7.0
#:
#: beta=30 is the knee: **more accurate than no filter at every speed** while halving
#: the jitter, where the lower betas buy stillness with lag on a moving hand and the
#: higher ones give the stillness back.
SMOOTH_MIN_HZ = float(os.environ.get("DEXTER_HAND_SMOOTH_HZ", "1.2") or 1.2)
SMOOTH_BETA = float(os.environ.get("DEXTER_HAND_SMOOTH_BETA", "30") or 30)
SMOOTH_DERIVATIVE_HZ = 1.0


class _OneEuro:
    """One-euro filter over an array of coordinates.

    `min_hz` sets how still a still hand is; `beta` how quickly it gets out of the way
    of a moving one. Zero `min_hz` disables it entirely, which is what the tests that
    are about geometry rather than filtering use.
    """

    def __init__(self, min_hz: float = SMOOTH_MIN_HZ, beta: float = SMOOTH_BETA) -> None:
        self.min_hz = float(min_hz)
        self.beta = float(beta)
        self._value: Optional[np.ndarray] = None
        self._speed: Optional[np.ndarray] = None
        self._at = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / max(dt, 1e-6))

    def reset(self) -> None:
        self._value = None
        self._speed = None

    def __call__(self, points: np.ndarray, now: Optional[float] = None) -> np.ndarray:
        if self.min_hz <= 0:
            return points
        now = time.time() if now is None else float(now)
        if self._value is None:
            self._value = points.astype(np.float32).copy()
            self._speed = np.zeros_like(self._value)
            self._at = now
            return points
        dt = now - self._at
        if not 0.0 < dt < 1.0:          # a long gap is a new hand, not a fast one
            dt = 1.0 / 30.0
        self._at = now
        speed = (points - self._value) / dt
        self._speed = (self._alpha(SMOOTH_DERIVATIVE_HZ, dt) * speed
                       + (1 - self._alpha(SMOOTH_DERIVATIVE_HZ, dt)) * self._speed)
        cutoff = self.min_hz + self.beta * np.abs(self._speed)
        # Per-coordinate alpha: a still finger is smoothed hard while a moving one in
        # the same hand is not.
        tau = 1.0 / (2.0 * math.pi * np.maximum(cutoff, 1e-6))
        alpha = 1.0 / (1.0 + tau / max(dt, 1e-6))
        self._value = alpha * points + (1 - alpha) * self._value
        return self._value.copy()


@dataclass
class _Crop:
    """Where a square crop came from, so landmarks can be mapped back."""

    centre: Tuple[float, float]   # in frame pixels
    side: float                   # square side in frame pixels
    angle: float                  # radians, clockwise


class OnnxHandTracker:
    """MediaPipe's two models, run directly. One hand, the nearest one."""

    def __init__(self, max_hands: int = 1) -> None:
        import onnxruntime as ort

        ready, why = available()
        if not ready:
            raise TrackerError(why)
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(os.environ.get("DEXTER_HAND_THREADS", "2") or 2)
        options.log_severity_level = 3
        self._palm = ort.InferenceSession(model_path("palm"), options,
                                          providers=["CPUExecutionProvider"])
        self._marks = ort.InferenceSession(model_path("landmarks"), options,
                                           providers=["CPUExecutionProvider"])
        self._anchors = _palm_anchors()
        self._crop: Optional[_Crop] = None
        self.max_hands = max(1, int(max_hands))
        self.source = "onnx"
        #: The best palm score of the most recent detection attempt, kept so
        #: "I can't see your hand" can say *how close* it came. A live webcam in
        #: a dim room scores 0.2-0.4 where a photograph scores 0.9, and without
        #: this the only available answer was "no".
        self.last_palm_score = 0.0
        self.best_palm_score = 0.0
        #: One filter for all 21 landmarks (v13.8).
        self._smooth = _OneEuro()

    # ── palm detection ──

    def _detect_palm(self, frame: np.ndarray) -> Optional[_Crop]:
        import cv2

        height, width = frame.shape[:2]
        side = max(height, width)
        padded = np.zeros((side, side, 3), dtype=frame.dtype)
        padded[:height, :width] = frame
        # RGB, like every MediaPipe model. Feeding it BGR halves the scores - and
        # the reference implementation (opencv_zoo mp_palmdet.py) converts too.
        blob = cv2.cvtColor(cv2.resize(padded, (PALM_INPUT, PALM_INPUT)),
                            cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        boxes, scores = self._palm.run(None, {self._palm.get_inputs()[0].name: blob[None]})
        scores = _sigmoid(np.asarray(scores, dtype=np.float32)).reshape(-1)
        best = int(np.argmax(scores))
        self.last_palm_score = float(scores[best])
        self.best_palm_score = max(self.best_palm_score, self.last_palm_score)
        if self.last_palm_score < PALM_SCORE_MIN:
            return None
        row = np.asarray(boxes, dtype=np.float32).reshape(-1, 18)[best]
        anchor = self._anchors[best]
        # The first four numbers are the box, in input pixels, relative to the anchor.
        cx = anchor[0] + row[0] / PALM_INPUT
        cy = anchor[1] + row[1] / PALM_INPUT
        box_w = row[2] / PALM_INPUT
        box_h = row[3] / PALM_INPUT
        # Then seven palm keypoints, the same way. 0 is the wrist, 2 is the
        # middle-finger knuckle: the line between them is the hand's rotation.
        keypoints = row[4:18].reshape(7, 2) / PALM_INPUT + anchor
        wrist, middle = keypoints[0], keypoints[2]
        angle = math.atan2(middle[1] - wrist[1], middle[0] - wrist[0]) + math.pi / 2
        # A palm box does not contain the fingers: the landmark model wants the
        # whole hand, so the box is enlarged *and* moved towards the fingers. The
        # reference does the same with PALM_BOX_SHIFT_VECTOR [0, -0.4] and a 3x
        # enlargement; here the shift is along the wrist-to-knuckles direction,
        # which is the same thing without rotating first.
        crop_side = float(max(box_w, box_h) * side * 2.8)
        reach = middle - wrist
        length = float(np.linalg.norm(reach)) or 1.0
        shift = (reach / length) * (crop_side * 0.22)
        return _Crop(centre=(float(cx * side + shift[0]), float(cy * side + shift[1])),
                     side=max(crop_side, 32.0), angle=float(angle))

    # ── landmarks ──

    def _rotated_crop(self, frame: np.ndarray, crop: _Crop) -> Tuple[np.ndarray, np.ndarray]:
        import cv2

        matrix = cv2.getRotationMatrix2D(crop.centre, math.degrees(crop.angle), 1.0)
        # Put the crop's centre in the middle of a LANDMARK_INPUT square.
        matrix[0, 2] += LANDMARK_INPUT / 2 - crop.centre[0]
        matrix[1, 2] += LANDMARK_INPUT / 2 - crop.centre[1]
        scale = LANDMARK_INPUT / crop.side
        scaling = np.array([[scale, 0, LANDMARK_INPUT * (1 - scale) / 2],
                            [0, scale, LANDMARK_INPUT * (1 - scale) / 2],
                            [0, 0, 1]], dtype=np.float32)
        full = scaling @ np.vstack([matrix, [0, 0, 1]]).astype(np.float32)
        patch = cv2.warpAffine(frame, full[:2], (LANDMARK_INPUT, LANDMARK_INPUT))
        return patch, full

    def _landmarks(self, frame: np.ndarray, crop: _Crop) -> Optional[Hand]:
        import cv2

        patch, full = self._rotated_crop(frame, crop)
        blob = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        outputs = self._marks.run(None, {self._marks.get_inputs()[0].name: blob[None]})
        points = np.asarray(outputs[0], dtype=np.float32).reshape(-1, 3)
        if points.shape[0] < 21:
            return None
        score = float(np.asarray(outputs[1]).reshape(-1)[0])
        handedness = float(np.asarray(outputs[2]).reshape(-1)[0]) if len(outputs) > 2 else 0.5
        if score < TRACK_SCORE_MIN:
            return None
        # Back out of the crop: invert the affine that produced the patch.
        inverse = np.linalg.inv(full)
        xy = np.hstack([points[:, :2], np.ones((points.shape[0], 1), dtype=np.float32)])
        mapped = (inverse @ xy.T).T[:, :2]
        height, width = frame.shape[:2]
        normalised = np.empty((points.shape[0], 3), dtype=np.float32)
        normalised[:, 0] = mapped[:, 0] / max(1, width)
        normalised[:, 1] = mapped[:, 1] / max(1, height)
        normalised[:, 2] = points[:, 2] / max(1.0, crop.side)
        return Hand(points=normalised, score=score, handedness=handedness, source="onnx")

    # ── the frame loop's one call ──

    def find(self, frame: np.ndarray) -> List[Hand]:
        """The hands in one BGR frame. Detection only when tracking has lost it."""
        crop = self._crop
        hand = self._landmarks(frame, crop) if crop is not None else None
        if hand is None:
            crop = self._detect_palm(frame)
            self._crop = crop
            if crop is None:
                # Nothing there: forget the filter's history, or the first frame of
                # the next hand would be dragged towards where the last one was.
                self._smooth.reset()
                return []
            hand = self._landmarks(frame, crop)
            if hand is None:
                self._crop = None
                self._smooth.reset()
                return []
            self._smooth.reset()
        # Smoothed *before* anything reads it (v13.8): every pose decision used to be
        # made on raw, jittering coordinates. The crop for the next frame is taken
        # from the smoothed hand too, so tracking does not chase noise either.
        hand.points = self._smooth(hand.points)
        # Follow the hand: next frame's crop is this frame's landmarks.
        self._crop = self._crop_from(hand, frame.shape[:2])
        return [hand]

    def _crop_from(self, hand: Hand, shape: Tuple[int, int]) -> _Crop:
        height, width = shape
        pixels = hand.points[:, :2] * np.array([width, height], dtype=np.float32)
        wrist = pixels[WRIST]
        middle = pixels[MIDDLE_MCP]
        angle = math.atan2(middle[1] - wrist[1], middle[0] - wrist[0]) + math.pi / 2
        centre = pixels.mean(axis=0)
        span = float(np.max(np.linalg.norm(pixels - centre, axis=1)))
        # 3x the hand's radius: the hand fills about two thirds of the crop, which
        # is the margin the landmark model was trained with.
        return _Crop(centre=(float(centre[0]), float(centre[1])),
                     side=max(span * 3.0, 32.0), angle=float(angle))


class MediaPipeHandTracker:
    """The same interface over mediapipe, when it is installed."""

    def __init__(self, max_hands: int = 1) -> None:
        import mediapipe as mp  # type: ignore

        self._hands = mp.solutions.hands.Hands(
            static_image_mode=False, max_num_hands=max(1, int(max_hands)),
            min_detection_confidence=PALM_SCORE_MIN, min_tracking_confidence=TRACK_SCORE_MIN,
        )
        self.source = "mediapipe"
        self.last_palm_score = 0.0
        self.best_palm_score = 0.0
        #: One filter for all 21 landmarks (v13.8).
        self._smooth = _OneEuro()

    def find(self, frame: np.ndarray) -> List[Hand]:
        import cv2

        result = self._hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        hands: List[Hand] = []
        for index, marks in enumerate(result.multi_hand_landmarks or []):
            points = np.asarray([[p.x, p.y, p.z] for p in marks.landmark], dtype=np.float32)
            score, handedness = 1.0, 0.5
            try:
                label = result.multi_handedness[index].classification[0]
                score = float(label.score)
                handedness = 1.0 if label.label.lower().startswith("r") else 0.0
            except Exception:
                pass
            hands.append(Hand(points=points, score=score, handedness=handedness,
                              source="mediapipe"))
        if hands:
            # Same filter as the ONNX path, so a 3.12 venv behaves identically
            # (mediapipe smooths its own landmarks, but not for the decisions this
            # code makes on top of them).
            hands[0].points = self._smooth(hands[0].points)
            self.last_palm_score = float(hands[0].score)
            self.best_palm_score = max(self.best_palm_score, self.last_palm_score)
        else:
            self._smooth.reset()
        return hands


def tracker(max_hands: int = 1) -> Any:
    """The best tracker this machine can run."""
    if packages()["mediapipe"]:
        try:
            return MediaPipeHandTracker(max_hands=max_hands)
        except Exception as exc:
            print(f"[Hand] mediapipe is installed but would not start ({exc}); using ONNX.")
    return OnnxHandTracker(max_hands=max_hands)


# ───────────────────────── the camera ─────────────────────────

_camera_lock = threading.Lock()


#: The capture mode to ask for, and why (v13.8, measured on the owner's webcam).
#:
#:     backend  size      delivered
#:     DSHOW    640x480   16.6 fps
#:     DSHOW    320x240   16.6 fps
#:     MSMF     640x480   15.9 fps
#:     MSMF     640x360   **30.3 fps**  (three trials, 30.2 / 30.3 / 30.3)
#:
#: Media Foundation at 640x360 is the camera's native 16:9 mode, and it is worth
#: having for its own sake: the models cost 9 ms a frame together, so every frame the
#: camera does not deliver was gesture quality thrown away. Doubling the frame rate
#: halves the motion blur in each frame, halves the lag of any smoothing, and gives
#: the click confirmation twice the evidence in the same tenth of a second.
#:
#: 16:9 also matches the screen better than 4:3 did, so the control rectangle stops
#: stretching vertically.
CAPTURE_WIDTH = int(os.environ.get("DEXTER_HAND_WIDTH", "640") or 640)
CAPTURE_HEIGHT = int(os.environ.get("DEXTER_HAND_HEIGHT", "360") or 360)
CAPTURE_FPS = int(os.environ.get("DEXTER_HAND_FPS", "30") or 30)
#: Longest a single backend/index is allowed to spend opening before it is
#: abandoned for the next one. OpenCV's `VideoCapture` can block *forever* in
#: native code on a bad backend (measured: cv2 5.0's MSMF hangs indefinitely on
#: this machine, which read from the outside as "the hand mouse does nothing when
#: I start it"). It cannot be interrupted, so the attempt is run on a throwaway
#: thread and left behind if it overruns; the next backend still gets its turn.
OPEN_TIMEOUT = float(os.environ.get("DEXTER_HAND_OPEN_TIMEOUT", "6") or 6)


def _cv2_major() -> int:
    try:
        import cv2
        return int(str(cv2.__version__).split(".", 1)[0])
    except Exception:
        return 0


def _backends() -> List[Tuple[str, int]]:
    """Capture backends to try, most reliable first on this platform.

    MSMF at 640x360 is the fastest here (30 fps vs DSHOW's 16), and it led the
    list for that reason - until OpenCV 5.0, whose MSMF backend hangs on open on
    this machine and took the whole hand mouse down with it. So DSHOW leads on
    cv2 >= 5 (correct beats fast: a 16 fps camera that opens is infinitely better
    than a 30 fps one that never does), MSMF leads on the 4.x line that measured
    well, and `DEXTER_HAND_BACKEND` overrides either way.
    """
    import cv2

    wanted = (os.environ.get("DEXTER_HAND_BACKEND") or "").strip().lower()
    msmf = ("msmf", getattr(cv2, "CAP_MSMF", 0))
    dshow = ("dshow", getattr(cv2, "CAP_DSHOW", 0))
    any_be = ("any", getattr(cv2, "CAP_ANY", 0))
    options = [dshow, msmf, any_be] if _cv2_major() >= 5 else [msmf, dshow, any_be]
    if wanted:
        options.sort(key=lambda pair: 0 if pair[0] == wanted else 1)
    return [(name, flag) for name, flag in options if flag or name == "any"]


def _open_capture(idx: int, flag: int, width: int, height: int,
                  box: Dict[str, Any]) -> None:
    """Open one camera and pull one frame, on a thread that may be abandoned."""
    import cv2

    try:
        capture = cv2.VideoCapture(idx, flag)
        if not capture.isOpened():
            capture.release()
            return
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if CAPTURE_FPS:
            capture.set(cv2.CAP_PROP_FPS, CAPTURE_FPS)
        ok, _frame = capture.read()
        if not ok:
            capture.release()
            return
        if box.get("abandoned"):
            # We already gave up on this attempt; do not leak the device.
            capture.release()
            return
        box["capture"] = capture
    except Exception as exc:  # noqa: BLE001 - reported, next backend tried
        box["error"] = exc


def open_camera(index: Optional[int] = None, width: int = CAPTURE_WIDTH,
                height: int = CAPTURE_HEIGHT):
    """The webcam, opened on the backend and index that actually deliver frames.

    Returns `(capture, index)`; `capture.dexter_mode` carries what was negotiated, so
    the status line can say 30 fps at 640x360 rather than leaving the owner to wonder
    why it feels different.
    """
    import cv2

    wanted = [int(index)] if index is not None else [
        int(os.environ.get("DEXTER_HAND_CAMERA", "0") or 0), 0, 1, 2
    ]
    tried: List[str] = []
    timed_out: List[str] = []
    for name, flag in _backends():
        for idx in dict.fromkeys(wanted):
            label = f"{name}:{idx}"
            if label in tried:
                continue
            tried.append(label)
            # Open on a worker thread and wait at most OPEN_TIMEOUT: a backend
            # that hangs in native code cannot be killed, but it can be left
            # behind so the next one still gets a turn.
            box: Dict[str, Any] = {}
            worker = threading.Thread(
                target=_open_capture, args=(idx, flag, width, height, box),
                name=f"dexter-cam-open-{label}", daemon=True,
            )
            worker.start()
            worker.join(OPEN_TIMEOUT)
            if worker.is_alive():
                box["abandoned"] = True      # if it ever returns, it self-releases
                timed_out.append(label)
                print(f"[Hand] Camera {label} did not open within {OPEN_TIMEOUT:.0f}s; "
                      "moving on.")
                continue
            capture = box.get("capture")
            if capture is None:
                continue
            got_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or width)
            got_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or height)
            try:
                capture.dexter_mode = {
                    "backend": name, "width": got_w, "height": got_h,
                    "fps": float(capture.get(cv2.CAP_PROP_FPS) or 0.0),
                }
            except Exception:
                pass
            print(f"[Hand] Camera {idx} on {name}: {got_w}x{got_h}")
            return capture, idx
    hint = ("Close anything else using the camera, or set DEXTER_HAND_CAMERA to its "
            "index.")
    if timed_out:
        hint = (f"{', '.join(timed_out)} hung on open (a known OpenCV "
                f"{_cv2_major()}.x MSMF problem). Try DEXTER_HAND_BACKEND=dshow, or "
                "grant camera access under Windows privacy settings.")
    raise TrackerError(f"No webcam answered (tried {', '.join(tried)}). {hint}")


def check_camera(seconds: float = 3.0, index: Optional[int] = None) -> Dict[str, Any]:
    """Look for a hand for a few seconds and report honestly what was seen.

    This is what "can you see my hand" runs: no pointer is moved, nothing is
    saved, and the answer names the confidence so a dim room is diagnosable.
    """
    import time as _time

    ready, why = available()
    if not ready:
        return {"ok": False, "error": why}
    capture, index_used = open_camera(index)
    engine = tracker()
    frames, seen, best = 0, 0, 0.0
    try:
        deadline = _time.time() + max(0.5, seconds)
        while _time.time() < deadline:
            ok, frame = capture.read()
            if not ok:
                break
            frames += 1
            hands = engine.find(frame)
            if hands:
                seen += 1
                best = max(best, float(hands[0].score))
    finally:
        capture.release()
    return {"ok": True, "camera": index_used, "frames": frames, "handFrames": seen,
            "bestScore": round(best, 3), "engine": engine.source,
            "sawHand": seen > 0}
