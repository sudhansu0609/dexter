"""
A small window showing what the hand tracker sees (v13.2).

Asked for as: "in dexter initially a small window shall open to show my hand
mouse tracking" — and it is not a nicety. A gesture mouse without a preview is
unusable for two reasons: you cannot tell whether the camera can see your hand at
all, and you cannot tell which pose it thinks you are making. Both were exactly
the complaint ("I am not able to move the cursor"), and both are answered by
drawing the truth on screen: the skeleton it tracked, the pose it decided, the
palm score when it found nothing, and the rectangle your hand has to stay inside
for the pointer to reach every corner of the screen.

Deliberately an OpenCV HighGUI window rather than part of Dexter's UI: it lives in
the tracking thread that already owns the camera, costs one `imshow` per frame,
needs no streaming endpoint, and disappears with the tracker. A preview that
throws is disabled rather than allowed to take tracking down with it — the window
is the diagnosis, not the feature.

Esc or Q in the window stops the hand mouse, which makes it a visible kill switch
as well.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import hand_tracker as ht

WINDOW = "Dexter — hand mouse"
#: The preview is scaled to this width; the camera frame is usually 640.
WIDTH = int(os.environ.get("DEXTER_HAND_PREVIEW_WIDTH", "360") or 360)

#: MediaPipe's 21-point skeleton, as pairs to draw.
BONES: Tuple[Tuple[int, int], ...] = (
    (ht.WRIST, ht.THUMB_CMC), (ht.THUMB_CMC, ht.THUMB_MCP),
    (ht.THUMB_MCP, ht.THUMB_IP), (ht.THUMB_IP, ht.THUMB_TIP),
    (ht.WRIST, ht.INDEX_MCP), (ht.INDEX_MCP, ht.INDEX_PIP),
    (ht.INDEX_PIP, ht.INDEX_DIP), (ht.INDEX_DIP, ht.INDEX_TIP),
    (ht.INDEX_MCP, ht.MIDDLE_MCP), (ht.MIDDLE_MCP, ht.MIDDLE_PIP),
    (ht.MIDDLE_PIP, ht.MIDDLE_DIP), (ht.MIDDLE_DIP, ht.MIDDLE_TIP),
    (ht.MIDDLE_MCP, ht.RING_MCP), (ht.RING_MCP, ht.RING_PIP),
    (ht.RING_PIP, ht.RING_DIP), (ht.RING_DIP, ht.RING_TIP),
    (ht.RING_MCP, ht.PINKY_MCP), (ht.PINKY_MCP, ht.PINKY_PIP),
    (ht.PINKY_PIP, ht.PINKY_DIP), (ht.PINKY_DIP, ht.PINKY_TIP),
    (ht.WRIST, ht.PINKY_MCP),
)

TIPS = (ht.THUMB_TIP, ht.INDEX_TIP, ht.MIDDLE_TIP, ht.RING_TIP, ht.PINKY_TIP)

# BGR, because this is OpenCV.
_GREEN = (120, 220, 120)
_WHITE = (245, 245, 245)
_GREY = (150, 150, 150)
_AMBER = (60, 180, 240)
_RED = (80, 80, 235)
_INK = (30, 30, 30)


class Preview:
    """One window, owned by the thread that owns the camera."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = bool(enabled)
        self.opened = False
        self.failed = ""
        self.quit_requested = False

    # ── lifecycle ──

    def _cv2(self):
        try:
            import cv2

            return cv2
        except Exception as exc:  # pragma: no cover - opencv is checked before this
            self.failed = str(exc)
            self.enabled = False
            return None

    def open(self) -> bool:
        if not self.enabled or self.opened:
            return self.opened
        cv2 = self._cv2()
        if cv2 is None:
            return False
        try:
            cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(WINDOW, WIDTH, int(WIDTH * 0.78))
            # Bottom-right-ish, out of the way of whatever is being driven.
            cv2.moveWindow(WINDOW, 40, 40)
            try:
                cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
            except Exception:
                pass  # older builds have no topmost property; not worth failing over
            self.opened = True
        except Exception as exc:
            self.failed = f"the preview window would not open ({exc})"
            self.enabled = False
        return self.opened

    def close(self) -> None:
        if not self.opened:
            return
        cv2 = self._cv2()
        self.opened = False
        if cv2 is None:
            return
        try:
            cv2.destroyWindow(WINDOW)
            # HighGUI needs a pump for the destroy to actually happen.
            cv2.waitKey(1)
        except Exception:
            pass

    # ── drawing ──

    def draw(
        self,
        frame: np.ndarray,
        hand: Optional[ht.Hand],
        gesture: str,
        fps: float,
        region: Tuple[float, float, float, float],
        buttons: Tuple[bool, bool],
        palm_score: float = 0.0,
        cursor: Optional[Tuple[int, int]] = None,
        anchor: Optional[Tuple[float, float]] = None,
        pose: str = "",
    ) -> None:
        """One frame of preview. Never raises: a broken preview disables itself."""
        if not self.enabled:
            return
        cv2 = self._cv2()
        if cv2 is None or not (self.opened or self.open()):
            return
        try:
            self._render(cv2, frame, hand, gesture, fps, region, buttons,
                         palm_score, cursor, anchor, pose)
        except Exception as exc:
            self.failed = f"preview drawing failed ({exc})"
            self.enabled = False
            self.close()

    def _render(self, cv2, frame, hand, gesture, fps, region, buttons,
                palm_score, cursor, anchor, pose="") -> None:
        height, width = frame.shape[:2]
        scale = WIDTH / float(width or 1)
        view = cv2.resize(frame, (WIDTH, max(1, int(height * scale))))
        # Mirrored, so it reads like a mirror and your hand moves the way you do.
        view = cv2.flip(view, 1)
        vh, vw = view.shape[:2]

        def at(point) -> Tuple[int, int]:
            """A normalised landmark to a pixel in the mirrored view."""
            return (int((1.0 - float(point[0])) * vw), int(float(point[1]) * vh))

        # The control rectangle: inside it, your hand reaches the whole screen.
        x0, y0, x1, y1 = region
        cv2.rectangle(view, (int((1 - x1) * vw), int(y0 * vh)),
                      (int((1 - x0) * vw), int(y1 * vh)), _GREY, 1)

        if hand is not None:
            for a, b in BONES:
                cv2.line(view, at(hand.point(a)), at(hand.point(b)), _GREEN, 2)
            for index in range(21):
                cv2.circle(view, at(hand.point(index)), 2, _WHITE, -1)
            for tip in TIPS:
                cv2.circle(view, at(hand.point(tip)), 4, _AMBER, -1)
            if anchor is not None:
                cv2.circle(view, at(anchor), 7, _RED, 2)

        # A third button arrived with the bindings (v13.6); older callers pass two.
        left_down, right_down, middle_down = tuple(buttons) + (False,) * (3 - len(buttons))
        held = " · LEFT" if left_down else ""
        held += " · RIGHT" if right_down else ""
        held += " · MIDDLE" if middle_down else ""
        if hand is not None:
            # The shape *and* what it is bound to: with customisable gestures, "it
            # saw a fist and did nothing" and "it saw nothing" have to look
            # different, and so do "a fist parks" and "a fist is your right click".
            line = f"{pose} -> {gesture}{held}" if pose and pose != gesture else f"{gesture}{held}"
        elif palm_score:
            line = f"no hand (best {palm_score:.2f} / {ht.PALM_SCORE_MIN:.2f} needed)"
        else:
            line = "no hand"
        hint = {
            "point": "moving the pointer",
            "pinch": "left button held",
            "right-pinch": "right button held",
            "middle": "middle button held",
            "double-click": "double clicked",
            "scroll": "scrolling",
            "park": "parked - this pose is bound to do nothing",
            "none": "hold your hand up, palm to the camera",
        }.get(gesture, "")

        # A strip at the bottom, so the text never sits on top of the hand.
        cv2.rectangle(view, (0, vh - 38), (vw, vh), _INK, -1)
        cv2.putText(view, line, (8, vh - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.45, _WHITE, 1,
                    cv2.LINE_AA)
        tail = f"{fps:.0f} fps" + (f" · {cursor[0]},{cursor[1]}" if cursor else "")
        cv2.putText(view, tail, (vw - 110, vh - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    _GREY, 1, cv2.LINE_AA)
        if hint:
            cv2.putText(view, hint, (8, vh - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.36, _GREY, 1,
                        cv2.LINE_AA)

        cv2.imshow(WINDOW, view)
        # The pump. Also the kill switch: Esc or Q here stops the hand mouse.
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q"), ord("Q")):
            self.quit_requested = True

    def state(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "open": self.opened, "error": self.failed}
