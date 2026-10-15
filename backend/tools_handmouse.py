"""
The hand mouse as tools (v13).

Four tools, and the order they are meant to be used in: set it up (download the
models once), check the camera can see your hand, turn it on, turn it off. The
check exists because "it doesn't work" about a webcam is almost always the room,
the camera index or another app holding the device - and each of those has a
different answer.

Two more in v13.6, because the gesture map became a setting: ask what each pose
does, and change one out loud ("make a fist the right click"). The change goes
through `settings_store`, which is what persists it and what the panel uses, so
the spoken route and the panel can never disagree about what a pose means.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

import hand_tracker as ht
import handmouse
from handmouse import hand_mouse
from tool_registry import ToolResult, tool


@tool(
    name="hand_mouse_setup",
    description=(
        "Download the hand-tracking models (about 8 MB, once) so the webcam can be "
        "used as a mouse. Also reports which packages are present."
    ),
    params={},
    examples=[
        "set up hand tracking",
        "download the hand tracking models",
        "get hand mouse ready",
        "install the hand gesture models",
    ],
    category="desktop",
)
def hand_mouse_setup() -> ToolResult:
    packages = ht.packages()
    missing = [name for name in ("cv2", "onnxruntime") if not packages[name]]
    if missing and not packages["mediapipe"]:
        names = {"cv2": "opencv-python", "onnxruntime": "onnxruntime"}
        return ToolResult.fail(
            "Hand tracking needs " + " and ".join(names[m] for m in missing)
            + f". Run: pip install {' '.join(names[m] for m in missing)}"
        )
    state = ht.ensure_models(download=True)
    if not state.get("ready"):
        errors = "; ".join(
            f"{kind}: {info.get('error', 'missing')}"
            for kind, info in state["models"].items() if info.get("missing")
        )
        return ToolResult.fail(
            f"I couldn't get the hand models into {state['dir']} ({errors}). "
            "Check the connection, or copy the two .onnx files in by hand."
        )
    fetched = ", ".join(state["downloaded"]) or "already there"
    size = sum(int(info.get("bytes") or 0) for info in state["models"].values())
    return ToolResult(
        ok=True,
        summary=(
            f"Hand tracking is ready ({fetched}; {size / (1024 * 1024):.1f} MB in "
            f"{state['dir']}). Say \"can you see my hand\" to check the camera."
        ),
        data={**state, "packages": packages},
    )


@tool(
    name="hand_mouse_check",
    description=(
        "Look through the webcam for a few seconds and say whether a hand is "
        "visible, and how confidently. Moves nothing."
    ),
    params={
        "seconds": {"type": "number", "description": "How long to look (default 3)",
                    "required": False},
    },
    examples=[
        "can you see my hand",
        "check the webcam for hand tracking",
        "test hand tracking",
        "is the camera seeing my hand",
    ],
    category="desktop",
)
def hand_mouse_check(seconds: float = 3.0) -> ToolResult:
    try:
        result = ht.check_camera(seconds=float(seconds or 3.0))
    except ht.TrackerError as exc:
        return ToolResult.fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"The camera check failed: {str(exc).splitlines()[0][:160]}")
    if not result.get("ok"):
        return ToolResult.fail(str(result.get("error")))
    if not result["frames"]:
        return ToolResult.fail(
            f"Camera {result['camera']} opened but delivered no frames. Close whatever "
            "else is using it and try again."
        )
    if not result["sawHand"]:
        return ToolResult(
            ok=False,
            summary=(
                f"I looked at {result['frames']} frames from camera {result['camera']} "
                f"({result['engine']}) and saw no hand. Hold your palm up, about an arm's "
                "length away, with some light on it, and ask again."
            ),
            data=result,
        )
    share = 100.0 * result["handFrames"] / max(1, result["frames"])
    return ToolResult(
        ok=True,
        summary=(
            f"I can see your hand: {result['handFrames']} of {result['frames']} frames "
            f"({share:.0f}%), best confidence {result['bestScore']:.2f}, camera "
            f"{result['camera']} via {result['engine']}. Say \"start hand mouse\" to drive "
            "the pointer with it."
        ),
        data=result,
    )


@tool(
    name="start_hand_mouse",
    description=(
        "Drive the mouse pointer with your hand through the webcam: an open palm or a "
        "pointing finger moves it, a pinch clicks and drags, and the rest of the map is "
        "whatever the gesture settings say. Ctrl+Alt+H stops it."
    ),
    params={
        "dry_run": {"type": "boolean",
                    "description": "Recognise the gestures but do not move the real pointer",
                    "required": False},
        "camera": {"type": "integer", "description": "Which webcam index", "required": False},
    },
    examples=[
        "start hand mouse",
        "control the mouse with my hand",
        "let me use my hand as a mouse",
        "turn on hand tracking",
        "use the webcam as a mouse",
        "start gesture control",
    ],
    category="desktop",
)
def start_hand_mouse(dry_run: bool = False, camera: Optional[int] = None) -> ToolResult:
    result = hand_mouse.start(camera=camera, dry_run=bool(dry_run))
    if not result.get("ok"):
        return ToolResult.fail(str(result.get("error") or "I couldn't start hand tracking."))
    if result.get("already"):
        return ToolResult(ok=True, summary="Hand tracking is already running.", data=result)
    note = " Nothing will actually move - this is a rehearsal." if dry_run else ""
    return ToolResult(
        ok=True,
        summary=(
            f"Hand mouse on (camera {result.get('camera')}, {result.get('engine')}). "
            # Read from the live map, not written out here: the sentence that used to
            # be hardcoded was two releases out of date, and with the gestures now
            # customisable it would have been wrong for anybody who changed one.
            + handmouse.gesture_sentence(result.get("bindings"))
            + f". Ctrl+Alt+H or \"stop hand mouse\" stops it.{note}"
        ),
        data=result,
    )


@tool(
    name="stop_hand_mouse",
    description="Stop driving the mouse with your hand and release the webcam.",
    params={},
    examples=[
        "stop hand mouse",
        "turn off hand tracking",
        "stop controlling the mouse with my hand",
        "release the webcam",
    ],
    category="desktop",
)
def stop_hand_mouse() -> ToolResult:
    if not hand_mouse.running:
        return ToolResult(ok=True, summary="Hand tracking wasn't running.",
                          data=hand_mouse.status())
    result = hand_mouse.stop()
    return ToolResult(
        ok=True,
        summary=(
            f"Hand mouse off after {result['minutes']} min "
            f"({result['handFrames']} of {result['frames']} frames had a hand in them)."
        ),
        data={**result, **hand_mouse.status()},
    )


@tool(
    name="hand_mouse_status",
    description="Whether the hand mouse is running, what pose it is seeing, and at what frame rate.",
    params={},
    examples=[
        "is hand tracking on",
        "hand mouse status",
        "what gesture do you see",
        "how fast is the hand tracking running",
    ],
    category="desktop",
)
def hand_mouse_status() -> ToolResult:
    state = handmouse.live_status()
    if not state["running"]:
        tail = f" ({state['error']})" if state.get("error") else ""
        if not state["ready"]:
            return ToolResult(ok=True, summary=f"Hand tracking is off and not ready: {state['why']}",
                              data=state)
        return ToolResult(ok=True, summary=f"Hand tracking is off.{tail}", data=state)
    buttons = []
    if state["leftDown"]:
        buttons.append("left held")
    if state["rightDown"]:
        buttons.append("right held")
    return ToolResult(
        ok=True,
        summary=(
            f"Hand mouse on: {state['gesture']} at {state['fps']} fps on camera "
            f"{state['camera']} ({state['engine']})"
            + (f", {', '.join(buttons)}" if buttons else "")
            + f". {state['minutes']} min so far."
        ),
        data=state,
    )


@tool(
    name="hand_gestures",
    description=(
        "List which hand pose does what on the hand mouse - move the pointer, left "
        "click, right click, middle click, double click, scroll or nothing."
    ),
    params={},
    examples=[
        "what do the hand gestures do",
        "list the hand mouse gestures",
        "which gesture is the right click",
        "show me the hand gesture map",
    ],
    category="desktop",
)
def hand_gestures() -> ToolResult:
    state = handmouse.live_status()
    lines = [f"{row['label']} ({row['hint']}): {handmouse.ACTIONS[row['action']]}"
             for row in state["poses"]]
    return ToolResult(
        ok=True,
        summary="Hand gestures right now — " + "; ".join(lines),
        data={"poses": state["poses"], "actions": state["actions"],
              "bindings": state["bindings"]},
    )


@tool(
    name="set_hand_gesture",
    description=(
        "Change what a hand pose does on the hand mouse. The pose is one of: open "
        "palm, index finger, two fingers, three fingers, fist, pinch (thumb and "
        "index), middle pinch (thumb and middle), or anything else. The action is "
        "one of: move, left click, right click, middle click, double click, scroll, "
        "nothing. Takes effect at once, even while tracking is running."
    ),
    params={
        "pose": {"type": "string", "required": True,
                 "description": "The hand shape, e.g. 'fist' or 'open palm'."},
        "action": {"type": "string", "required": True,
                   "description": "What it should do, e.g. 'right click'."},
    },
    examples=[
        "make a fist the right click",
        "set the open palm to scroll",
        "change the pinch to a double click",
        "make two fingers do nothing",
        "bind the index finger to move the pointer",
    ],
    category="desktop",
)
def set_hand_gesture(pose: str, action: str) -> ToolResult:
    which = handmouse.normalize_pose(pose)
    what = handmouse.normalize_action(action)
    if not which:
        return ToolResult.fail(
            f"I don't know the hand pose '{pose}'. The poses are: "
            + ", ".join(handmouse.POSES.values()) + "."
        )
    if not what:
        return ToolResult.fail(
            f"I don't know what '{action}' should mean. The actions are: move the "
            "pointer, left click, right click, middle click, double click, scroll, "
            "nothing."
        )
    # Through the settings store rather than straight at the tracker: that is what
    # persists it, and it is the same channel the panel uses, so the two can never
    # disagree about what a pose does.
    import settings_store

    key = "handPose" + "".join(part.capitalize() for part in which.split("-"))
    result = settings_store.update({key: what})
    if key in (result.get("rejected") or []):
        return ToolResult.fail(f"Could not save that gesture ({key} was rejected).")
    state = handmouse.live_status()
    live = " It's live now." if state["running"] else ""
    return ToolResult(
        ok=True,
        summary=(f"{handmouse.POSES[which]} ({handmouse.POSE_HINTS[which]}) now does: "
                 f"{handmouse.ACTIONS[what]}.{live}"),
        data={"pose": which, "action": what, "bindings": state["bindings"]},
    )


@tool(
    name="set_hand_speed",
    description=(
        "Change how fast the hand-mouse pointer moves - how far it travels for a "
        "given hand movement. Accepts 'faster'/'slower', or a number from 0.5 (slow "
        "and precise) to 2.5 (quick and twitchy). Takes effect at once."
    ),
    params={
        "speed": {"type": "string", "required": True,
                  "description": "A number 0.5-2.5, or 'faster' / 'slower'."},
    },
    examples=[
        "make the hand mouse slower",
        "the hand pointer is too fast",
        "speed up the hand mouse",
        "set the hand mouse speed to 1.5",
        "make the hand cursor less sensitive",
    ],
    category="desktop",
)
def set_hand_speed(speed: str) -> ToolResult:
    # Being able to say this matters more than it looks: when the pointer is too
    # fast, clicking the slider that fixes it is exactly the thing you cannot do.
    import settings_store

    current = float(handmouse.live_status().get("speed") or 1.0)
    word = str(speed or "").strip().lower()
    if word in ("faster", "quicker", "more sensitive", "speed up", "up", "more"):
        wanted = current + 0.3
    elif word in ("slower", "slow", "less sensitive", "slow down", "down", "less",
                  "more precise", "precise"):
        wanted = current - 0.3
    else:
        try:
            wanted = float(re.sub(r"[^0-9.]", "", word) or "nan")
        except ValueError:
            wanted = float("nan")
    if wanted != wanted:      # NaN
        return ToolResult.fail(
            f"I don't know what speed '{speed}' means. Say faster, slower, or a "
            f"number between {handmouse.MIN_SPEED} and {handmouse.MAX_SPEED}."
        )
    wanted = round(min(handmouse.MAX_SPEED, max(handmouse.MIN_SPEED, wanted)), 2)
    settings_store.update({"handSpeed": wanted})
    state = handmouse.live_status()
    at_edge = ""
    if wanted in (handmouse.MIN_SPEED, handmouse.MAX_SPEED):
        at_edge = " That's as far as it goes."
    return ToolResult(
        ok=True,
        summary=(f"Hand pointer speed {current:g} to {wanted:g}.{at_edge}"
                 + (" It's live now." if state["running"] else "")),
        data={"speed": wanted, "was": current, "region": state["region"]},
    )
