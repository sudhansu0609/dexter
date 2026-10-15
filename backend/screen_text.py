"""
Reading the screen, and clicking words on it (v13.8).

"It shall be able to click on a text on the browser or screen without the precise
browser as well."

Dexter could already click by *accessible name* (`desktop_control.click_text`), which
works beautifully in Notepad, Explorer and every ordinary Windows app - and not at all
inside Chrome's web content. That was measured rather than assumed:

    UIA on a Chrome window     35 controls: Minimize, Back, Reload, Bookmark,
                               the profile button... and nothing from the page
    after WM_GETOBJECT poke     unchanged (Chrome answered, tree still empty)
    walking the render widget   0 named nodes
    MSAA via oleacc             0 named nodes
    system screen-reader flag   already on; still nothing

Modern Chrome does not publish page content to Windows accessibility at all unless it
is started with `--force-renderer-accessibility`. So the page is invisible to every
route except Chrome's own debug port - which is exactly the thing the owner does not
want to have to use.

What is always true, though, is that the text is **on the screen**. So: capture the
window, read it with OCR, and click the words. That works in any browser, any profile,
any app, with nothing enabled and nothing restarted - including Chrome's profile
picker, which is a WebUI page and therefore invisible the same way.

Measured on the owner's machine: 2.0-2.9 s for a full 1920x1080 window, 26 text boxes
including every word of the page. Full resolution is both faster *and* more accurate
than downscaling (the detector resizes internally anyway, and shrinking first turns
"BuzzAnimate" into "BursAnirute").

v13.9.1 - the same screen is not read twice. Two or three seconds is a fair price for
looking at a screen; it is not a fair price for looking at the *same* screen, which is
what "read this, now click that word on it" used to cost twice over. There are two
ways out of paying again, and both of them prove what they claim:

    the frame        Every reading is filed under a fingerprint of the pixels it read
                     (blake2b over the whole frame: 5.5 ms at 1080p, 22 ms at 4K). An
                     identical frame gives its words back in about 10 ms. Exact, so a
                     remembered position can never be one something has moved away
                     from - which matters most to `verify._on_screen`, where a stale
                     reading would confirm a change that never happened.

    the word         A real desktop is never identical twice. Measured on the owner's
                     two monitors, captures 1.5 s apart differ in 0.03% of their pixels
                     - a clock, a caret, an FPS counter - so whole-frame matching would
                     almost never fire on the case it was built for. But 206 of those
                     210 words were on pixels that had not changed at all. So a click
                     proves the smaller thing: the pixels *under the word asked for*.
                     Unchanged there means the word is still drawn there, since
                     scrolling it away or covering it would have changed them.

Measured live: first read of both monitors 9.8 s, then "Example Domains" recalled and
located in 2.9 ms on a frame that was *not* identical. The FPS counter, asked for in
the same breath, refused to be recalled and bought a fresh read - which is the whole
point of proving it word by word.

`read` (the whole screen, for "what does this say") still pays in full unless the frame
is identical; there is nothing to prove a word by when every word is wanted.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import math
import os
import re
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import desktop_control as dc
import mouse_input

#: Below this the match is a guess, and a guess that clicks is worse than a question.
MIN_SCORE = float(os.environ.get("DEXTER_SCREEN_MIN_SCORE", "0.55") or 0.55)
#: The OCR engine's own confidence floor. Text below this is usually an icon.
MIN_CONFIDENCE = float(os.environ.get("DEXTER_SCREEN_MIN_CONFIDENCE", "0.45") or 0.45)


def _flag(name: str, default: str = "1") -> bool:
    return (os.environ.get(name, default) or default).strip().lower() not in (
        "0", "false", "no", "off", "")


#: Reading the screen costs 2-3 s, and reading the *same* screen again cost the same
#: again - which is the part that felt slow. Pick a word, think about it, pick another,
#: and the second look was as dear as the first although nothing on screen had moved.
#:
#: So every reading is filed under a fingerprint of the pixels it read. Capturing and
#: fingerprinting a 1920x1080 window is a few milliseconds; the OCR is the whole of the
#: 2-3 s. An identical frame therefore answers immediately.
#:
#: The fingerprint covers the entire frame, with no tolerance: a hit means the picture
#: is byte-for-byte the one that was read, so a cached answer can never point at where
#: something *used* to be. The price of that honesty is that a blinking caret or a
#: ticking clock inside the captured region changes the pixels, and correctly buys a
#: fresh read.
CACHE_ON = _flag("DEXTER_SCREEN_CACHE", "1")
#: A frame that has sat around this long is re-read even if it still looks the same.
#: Identical pixels are already proof enough; this is only so a machine left alone for
#: an hour does not act on an hour-old reading.
CACHE_TTL = float(os.environ.get("DEXTER_SCREEN_CACHE_TTL", "120") or 120)
#: How many distinct frames to keep. More than one so that alternating between two
#: windows keeps both.
CACHE_MAX = max(1, int(os.environ.get("DEXTER_SCREEN_CACHE_MAX", "6") or 6))

_engine: Any = None
_engine_error = ""
_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_cache_stats: Dict[str, Any] = {"hits": 0, "misses": 0, "recalls": 0, "saved": 0.0}


class ScreenTextError(RuntimeError):
    """Something Dexter should say out loud."""


@dataclass
class Word:
    """One run of text the screen showed, and where it is."""

    text: str
    left: int
    top: int
    right: int
    bottom: int
    confidence: float = 0.0

    @property
    def centre(self) -> Tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    def as_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "centre": list(self.centre),
                "box": [self.left, self.top, self.right, self.bottom],
                "confidence": round(self.confidence, 2)}


# ───────────────────────── is this possible at all ─────────────────────────


def availability() -> Dict[str, Any]:
    """What is installed, and what to install when it is not."""
    have = {}
    for name in ("rapidocr_onnxruntime", "mss", "cv2", "numpy"):
        try:
            __import__(name)
            have[name] = True
        except Exception:
            have[name] = False
    ready = have["rapidocr_onnxruntime"] and have["mss"] and have["numpy"]
    why = ""
    if not ready:
        missing = [n for n, ok in have.items() if not ok]
        why = ("reading the screen needs " + " and ".join(missing)
               + " (pip install " + " ".join(missing) + ")")
    return {"ready": ready, "packages": have, "why": why, "engineError": _engine_error,
            "cache": cache_stats()}


def _ocr() -> Any:
    """The OCR engine, loaded once (about 0.4 s) and kept."""
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    state = availability()
    if not state["ready"]:
        raise ScreenTextError(state["why"])
    try:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
        return _engine
    except Exception as exc:
        _engine_error = str(exc)
        raise ScreenTextError(f"the OCR engine would not load: {exc}") from exc


# ───────────────────────── getting the pixels ─────────────────────────

SW_MINIMIZE, SW_RESTORE = 6, 9


def raise_window(hwnd: int, timeout: float = 1.5) -> bool:
    """Bring a window to the front, and **confirm** that it came.

    Politely first. When that fails - and it does fail, because Windows refuses the
    foreground to a process that is not already the foreground one - minimize and
    restore, which Windows does grant. Measured on this machine: plain
    `SetForegroundWindow` returned False and changed nothing; `ShowWindow(SW_RESTORE)`
    plus `SetForegroundWindow` also changed nothing; the minimize/restore dance worked
    first time.

    It matters here because what is not in front cannot be photographed: a capture of
    the region would be a picture of whatever is covering it, and Dexter would then
    click on the desktop by mistake.
    """
    if not hwnd or sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    if user32.GetForegroundWindow() == hwnd:
        return True
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    except Exception:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    try:
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        time.sleep(0.25)
        user32.ShowWindow(hwnd, SW_RESTORE)
    except Exception:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return False


def window_box(hwnd: int) -> Dict[str, int]:
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top = max(int(rect.left), 0), max(int(rect.top), 0)
    return {"left": left, "top": top,
            "width": max(1, int(rect.right) - left), "height": max(1, int(rect.bottom) - top)}


def capture(window: Optional[str] = None, win: Optional[Any] = None
            ) -> Tuple[Any, Dict[str, int], Optional[Any]]:
    """A picture of one window (raised first) or of the whole screen.

    Returns `(image, origin, window)`. The origin is what turns a position in the
    picture into a position on the screen, which is what the click needs.
    """
    import mss
    import numpy as np

    target = win
    if target is None and window:
        target = dc.find_window(window) if hasattr(dc, "find_window") else None
        if target is None:
            matches = dc.find_windows(window)
            target = matches[0] if matches else None
            if target is None:
                raise ScreenTextError(f"I can't find a window called '{window}'.")
    if target is not None:
        # PrintWindow would avoid raising it, but Chrome composites on the GPU and
        # returns a blank bitmap - tried, measured, empty. So the window has to be in
        # front to be read, which is fair: clicking what you can see implies seeing it.
        raise_window(int(getattr(target, "hwnd", 0) or 0))
        time.sleep(0.35)
        box = window_box(int(getattr(target, "hwnd", 0) or 0))
    else:
        with mss.MSS() as sct:
            monitor = sct.monitors[0]
        box = {"left": monitor["left"], "top": monitor["top"],
               "width": monitor["width"], "height": monitor["height"]}
    with mss.MSS() as sct:
        raw = np.asarray(sct.grab(box))
    image = raw[:, :, :3][:, :, ::-1].copy()      # BGRA -> RGB
    return image, box, target


# ───────────────────────── remembering a frame ─────────────────────────


def frame_hash(image: Any) -> str:
    """A fingerprint of exactly these pixels.

    blake2b over the whole buffer: about 6 MB for a 1080p window, a few milliseconds,
    against the two to three seconds it saves. Hashing the whole thing rather than a
    thumbnail is deliberate - a thumbnail would survive small changes on the screen,
    and "small change on the screen" is precisely the case where clicking a remembered
    position goes wrong.
    """
    try:
        import numpy as np

        data = np.ascontiguousarray(image)
        digest = hashlib.blake2b(digest_size=16)
        digest.update(str(data.shape).encode("ascii"))
        digest.update(memoryview(data).cast("B"))
        return digest.hexdigest()
    except Exception:
        return ""


def _frame_key(image: Any, origin: Dict[str, int]) -> str:
    """The fingerprint, plus where on the screen it was taken.

    The same pixels captured at a different offset describe different places, and it is
    the place that gets clicked.
    """
    stamp = frame_hash(image)
    if not stamp:
        return ""
    return f"{stamp}@{origin.get('left', 0)},{origin.get('top', 0)}"


def _box_hash(image: Any, word: "Word", origin: Dict[str, int]) -> str:
    """A fingerprint of just the pixels this word is drawn on.

    The whole-frame fingerprint answers "is this the same screen"; this one answers the
    smaller and far more useful question, "is this word still there". A clock ticking in
    the corner changes the frame and not the word.
    """
    try:
        import numpy as np

        left = max(0, word.left - origin.get("left", 0))
        top = max(0, word.top - origin.get("top", 0))
        right = min(image.shape[1], word.right - origin.get("left", 0))
        bottom = min(image.shape[0], word.bottom - origin.get("top", 0))
        if right <= left or bottom <= top:
            return ""
        crop = np.ascontiguousarray(image[top:bottom, left:right])
        digest = hashlib.blake2b(digest_size=12)
        digest.update(f"{left},{top},{right},{bottom}".encode("ascii"))
        digest.update(memoryview(crop).cast("B"))
        return digest.hexdigest()
    except Exception:
        return ""


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    entry = _cache.get(key)
    if entry is None:
        return None
    if CACHE_TTL > 0 and time.time() - entry["at"] > CACHE_TTL:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)          # keep what is being used
    return entry


def _cache_put(key: str, words: List[Word], ocr_took: float,
               image: Any = None, origin: Optional[Dict[str, int]] = None) -> None:
    origin = origin or {"left": 0, "top": 0}
    boxes = ([_box_hash(image, word, origin) for word in words]
             if image is not None else ["" for _ in words])
    _cache[key] = {"words": list(words), "boxes": boxes, "ocrTook": ocr_took,
                   "at": time.time(), "origin": dict(origin),
                   "shape": tuple(getattr(image, "shape", ()) or ())}
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def _last_reading(image: Any, origin: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """The most recent reading of a screen this size, in this place.

    Not necessarily of *this* frame - that is what `recall` then has to prove, word by
    word.
    """
    shape = tuple(getattr(image, "shape", ()) or ())
    now = time.time()
    for key in reversed(_cache):
        entry = _cache[key]
        if entry["shape"] != shape:
            continue
        if (entry["origin"].get("left") != origin.get("left")
                or entry["origin"].get("top") != origin.get("top")):
            continue
        if CACHE_TTL > 0 and now - entry["at"] > CACHE_TTL:
            continue
        return entry
    return None


def cache_clear() -> int:
    """Forget every remembered frame. Returns how many there were."""
    count = len(_cache)
    _cache.clear()
    return count


def cache_stats() -> Dict[str, Any]:
    """Hits, misses, recalls, and the seconds of OCR none of them had to spend."""
    return {"on": CACHE_ON, "frames": len(_cache), "ttl": CACHE_TTL,
            "hits": _cache_stats["hits"], "misses": _cache_stats["misses"],
            "recalls": _cache_stats["recalls"],
            "savedSeconds": round(float(_cache_stats["saved"]), 2)}


# ───────────────────────── reading it ─────────────────────────


def read(window: Optional[str] = None, win: Optional[Any] = None,
         image: Optional[Any] = None, origin: Optional[Dict[str, int]] = None
         ) -> Dict[str, Any]:
    """Every run of text on a window or the screen, with screen coordinates.

    A second look at an unchanged screen is answered from the last reading: the pixels
    are captured and fingerprinted either way, so an unchanged frame costs the capture
    only (a few hundred milliseconds, most of it waiting for the window to come
    forward) instead of the whole 2-3 s.
    """
    started = time.time()
    target = win
    if image is None:
        image, origin, target = capture(window=window, win=win)
    origin = origin or {"left": 0, "top": 0}
    here = getattr(target, "title", "") if target else "the screen"
    key = _frame_key(image, origin) if CACHE_ON else ""
    remembered = _cache_get(key) if key else None
    if remembered is not None:
        _cache_stats["hits"] += 1
        _cache_stats["saved"] = round(float(_cache_stats["saved"]) + remembered["ocrTook"], 2)
        words = list(remembered["words"])
        return {"words": words, "took": round(time.time() - started, 2),
                "window": here, "origin": origin, "count": len(words),
                "cached": True, "frame": key[:12]}
    if key:
        _cache_stats["misses"] += 1
    engine = _ocr()
    ocr_started = time.time()
    result, _elapsed = engine(image)
    ocr_took = round(time.time() - ocr_started, 2)
    words: List[Word] = []
    for row in result or []:
        try:
            box, text, score = row[0], str(row[1]), float(row[2])
        except Exception:
            continue
        if not text.strip() or score < MIN_CONFIDENCE:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        words.append(Word(
            text=text.strip(),
            left=int(min(xs)) + origin["left"], top=int(min(ys)) + origin["top"],
            right=int(max(xs)) + origin["left"], bottom=int(max(ys)) + origin["top"],
            confidence=score,
        ))
    if key:
        _cache_put(key, words, ocr_took, image=image, origin=origin)
    return {"words": words, "took": round(time.time() - started, 2),
            "window": here, "origin": origin, "count": len(words),
            "cached": False, "frame": key[:12]}


# ───────────────────────── finding the right words ─────────────────────────


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def score_match(candidate: str, wanted: str) -> float:
    """How well a run of screen text answers what was asked for, 0 to 1.

    Ordered so that the obvious answer wins: the same words, then the words inside a
    longer run, then every significant word present, then a character-level
    resemblance for OCR's own mistakes ("Leam more" for "Learn more").
    """
    have, want = _norm(candidate), _norm(wanted)
    if not have or not want:
        return 0.0
    if have == want:
        return 1.0
    if want in have:
        # A short hit inside a long line is a weaker answer than a tight one.
        return 0.9 * (len(want) / len(have)) ** 0.25
    words = [w for w in re.split(r"[^a-z0-9]+", want) if len(w) > 2]
    if words and all(word in have for word in words):
        return 0.75
    import difflib

    ratio = difflib.SequenceMatcher(None, have, want).ratio()
    return ratio * 0.8


def find(wanted: str, window: Optional[str] = None, win: Optional[Any] = None,
         nth: int = 1, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Where on the screen that text is, best match first."""
    state = state or read(window=window, win=win)
    scored = []
    for word in state["words"]:
        score = score_match(word.text, wanted)
        if score >= MIN_SCORE:
            scored.append((score, word))
    # Best first, and among equals the one nearest the top-left, which is reading order.
    scored.sort(key=lambda pair: (-pair[0], pair[1].top, pair[1].left))
    position = max(1, int(nth or 1))
    chosen = scored[position - 1][1] if len(scored) >= position else None
    return {
        "found": chosen is not None,
        "match": chosen.as_dict() if chosen else None,
        "score": round(scored[position - 1][0], 2) if len(scored) >= position else 0.0,
        "candidates": [w.as_dict() for _s, w in scored[:5]],
        "seen": [w.text for w in state["words"][:40]],
        "count": state["count"],
        "took": state["took"],
        "cached": bool(state.get("cached")),
        "window": state["window"],
        "wanted": wanted,
    }


def recall(wanted: str, image: Any, origin: Dict[str, int], nth: int = 1
           ) -> Optional[Dict[str, Any]]:
    """The word you asked for, *proved* still where it was, without reading again.

    The whole-frame fingerprint only fires on a screen that has not moved by a single
    pixel, and a real desktop always moves: the clock ticks, a caret blinks, a
    notification fades. Measured on the owner's two monitors, two captures a second
    apart differ in 0.03% of their pixels - never identical, and yet the words are all
    exactly where they were. An exact whole-frame match would therefore almost never
    fire on the case it was built for.

    So the frame is not the unit of proof; the word is. The last reading knows the
    pixels each word was drawn on, and if the pixels under the word you asked for are
    unchanged, that word is still rendered there - nothing has scrolled it away, and
    nothing is covering it, because either would have changed those pixels. That is a
    click that can be justified, and it costs a hash of a few hundred pixels.

    Returns None whenever it cannot prove it, and then the screen is read properly.
    """
    if not CACHE_ON:
        return None
    entry = _last_reading(image, origin)
    if entry is None:
        return None
    scored = []
    for index, word in enumerate(entry["words"]):
        score = score_match(word.text, wanted)
        if score >= MIN_SCORE:
            scored.append((score, index, word))
    scored.sort(key=lambda row: (-row[0], row[2].top, row[2].left))
    position = max(1, int(nth or 1))
    if len(scored) < position:
        # Not finding it in a stale reading proves nothing at all - it may have arrived
        # since. That is a real read, not a no.
        return None
    score, index, word = scored[position - 1]
    if not entry["boxes"][index] or _box_hash(image, word, origin) != entry["boxes"][index]:
        return None
    _cache_stats["recalls"] += 1
    _cache_stats["saved"] = round(float(_cache_stats["saved"]) + entry["ocrTook"], 2)
    return {
        "found": True,
        "match": word.as_dict(),
        "score": round(score, 2),
        "candidates": [w.as_dict() for _s, _i, w in scored[:5]],
        "seen": [w.text for w in entry["words"][:40]],
        "count": len(entry["words"]),
        "took": 0.0,
        "cached": True,
        "recalled": True,
        "window": "",
        "wanted": wanted,
    }


# ───────────────────────── clicking it ─────────────────────────


def click(wanted: str, window: Optional[str] = None, win: Optional[Any] = None,
          nth: int = 1, button: str = "left", double: bool = False,
          move_only: bool = False) -> Dict[str, Any]:
    """Click the words on the screen. Never on a weak match."""
    started = time.time()
    image, origin, target = capture(window=window, win=win)
    here = getattr(target, "title", "") if target else "the screen"
    hit = recall(wanted, image, origin, nth=nth)
    if hit is not None:
        hit["window"] = here
        hit["took"] = round(time.time() - started, 2)
    else:
        state = read(win=target, image=image, origin=origin)
        hit = find(wanted, state=state, nth=nth)
    if not hit["found"]:
        hit["clicked"] = False
        return hit
    x, y = hit["match"]["centre"]
    if not mouse_input.available():
        hit["clicked"] = False
        hit["why"] = "moving the pointer needs Windows"
        return hit
    mouse_input.move_to(x, y)
    if not move_only:
        time.sleep(0.05)
        mouse_input.click(button, double=double)
    hit["clicked"] = not move_only
    hit["at"] = [x, y]
    hit["button"] = button
    return hit
