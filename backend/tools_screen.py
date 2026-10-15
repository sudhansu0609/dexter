"""
Clicking words, and reading the screen (v13.8).

"It shall be able to click on a text on the browser or screen without the precise
browser as well."

`click_text` (v11) clicks by accessible name and is the right tool wherever Windows
publishes one - Notepad, Explorer, Premiere, any ordinary app. Inside Chrome's web
content there is nothing to publish: measured every way round, modern Chrome does not
put page content into the accessibility tree unless it was started with
`--force-renderer-accessibility`. Chrome's own profile picker is a WebUI page, which is
why "select the first profile" failed too.

The text is on the screen though, so these tools read the screen and click the words.
Any browser, any profile, any app, nothing to enable. The first look costs two or three
seconds, which is why it is the second thing tried rather than the first. Picking a
second word off the same screen is nearly free: the reading is remembered against a
fingerprint of the pixels, word by word, so a word that has not moved is clicked
without reading anything again (v13.9.1, measured at 2.9 ms against a 9.8 s read).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import screen_text
from tool_registry import ToolResult, tool


def _unavailable(state: Dict[str, Any]) -> ToolResult:
    return ToolResult.fail(
        f"I can't read the screen: {state['why']}. Until then I can only click things "
        "Windows names (`click_text`) or use exact browser control."
    )


def _section_words(ordered, section: str, state: Dict[str, Any], band_px: int = 360):
    """The words that sit under a heading/label the user named.

    "read the ingredients section" is the whole screen read plus a find: locate the
    heading, then take the words in a band beneath it. Falls back to the fuzzy
    `screen_text.find` when the word is not an exact substring on any run.
    """
    needle = section.strip().lower()
    top = None
    for word in ordered:
        if needle in word.text.lower():
            top = word.top
            break
    if top is None:
        try:
            hit = screen_text.find(section, state=state)
        except Exception:
            hit = {}
        if hit.get("found") and hit.get("match"):
            top = hit["match"]["box"][1]
    if top is None:
        return None
    band = [w for w in ordered if top - 8 <= w.top <= top + band_px]
    return band or None


@tool(
    name="click_on_screen",
    description=(
        "Click a piece of text the user can see, by reading the screen. Works anywhere "
        "- inside Chrome's pages, a profile picker, a browser that has no debug port, "
        "any application - because it matches the words on screen rather than asking "
        "Windows what things are called. Slower than click_text, so use it when "
        "click_text has found nothing or for web page content."
    ),
    params={
        "text": {"type": "string", "required": True,
                 "description": "The words to click, as they appear on screen."},
        "window": {"type": "string", "required": False,
                   "description": "Part of a window title to look in; omit for the whole screen."},
        "nth": {"type": "integer", "required": False,
                "description": "Which match, when the same text appears more than once (default 1)."},
        "double": {"type": "boolean", "required": False,
                   "description": "Double click instead of single."},
        "button": {"type": "string", "required": False,
                   "description": "left (default) or right."},
    },
    examples=[
        "click on Learn more",
        "click the text that says Sign in",
        "click on my profile picture name in chrome",
        "press the button that says Continue",
        "click on the second Download link",
    ],
    category="desktop",
    confirm=False,
)
def click_on_screen(text: str, window: Optional[str] = None, nth: int = 1,
                    double: bool = False, button: str = "left") -> ToolResult:
    state = screen_text.availability()
    if not state["ready"]:
        return _unavailable(state)
    try:
        result = screen_text.click(text, window=window, nth=int(nth or 1),
                                   button=(button or "left").lower(),
                                   double=bool(double))
    except screen_text.ScreenTextError as exc:
        return ToolResult.fail(str(exc))
    if not result["found"]:
        seen = "; ".join(t for t in result.get("seen", [])[:6] if t)
        return ToolResult(
            ok=False,
            summary=(f"I couldn't find \"{text}\" on the screen. I read "
                     f"{result['count']} piece(s) of text"
                     + (f", including: {seen}" if seen else "") + "."),
            data=result,
        )
    where = result["match"]["centre"]
    how = "Double clicked" if double else ("Right clicked" if button == "right" else "Clicked")
    # Saying which route it took keeps the speed honest: a fast answer is fast because
    # something was already known, and that is worth knowing.
    if result.get("recalled"):
        read_how = (f"still exactly where I last saw it, so I didn't read the screen "
                    f"again - {result['took']}s")
    elif result.get("cached"):
        read_how = (f"the screen hadn't changed, so I used the reading I already had - "
                    f"{result['count']} pieces of text, {result['took']}s")
    else:
        read_how = f"read {result['count']} pieces of text in {result['took']}s"
    return ToolResult(
        ok=True,
        summary=(f"{how} \"{result['match']['text'][:60]}\" at {where[0]},{where[1]} "
                 f"({read_how})."),
        data=result,
    )


@tool(
    name="read_screen",
    description=(
        "Read the text on the screen, or in one window, by looking at the pixels. Use "
        "when the user asks what something says, or to check what is in front of them, "
        "in an app or page that Windows does not describe. Give 'section' to read just "
        "the part near a heading or label the user names."
    ),
    params={
        "window": {"type": "string", "required": False,
                   "description": "Part of a window title; omit for the whole screen."},
        "section": {"type": "string", "required": False,
                    "description": "A heading/label to read around, e.g. 'ingredients', 'comments', 'total'."},
    },
    examples=[
        "what does the screen say",
        "read this page to me",
        "what's on my screen right now",
        "read what's in the chrome window",
        "read the comments section",
        "what does the total section say",
        "read me the error message on screen",
    ],
    category="desktop",
)
def read_screen(window: Optional[str] = None, section: Optional[str] = None) -> ToolResult:
    state = screen_text.availability()
    if not state["ready"]:
        return _unavailable(state)
    try:
        found = screen_text.read(window=window)
    except screen_text.ScreenTextError as exc:
        return ToolResult.fail(str(exc))
    words = found["words"]
    if not words:
        return ToolResult(ok=False, summary="I couldn't read any text on the screen.",
                          data={"count": 0, "took": found["took"]})
    # Reading order, so the reply reads the way the screen looks.
    ordered = sorted(words, key=lambda w: (w.top // 24, w.left))

    if section and section.strip():
        band = _section_words(ordered, section, found)
        if band is None:
            return ToolResult(
                ok=False,
                summary=f"I couldn't find a '{section}' section on the screen.",
                data={"window": found["window"], "wanted": section,
                      "seen": [w.text for w in ordered[:40]]},
            )
        text = " · ".join(w.text for w in band)
        return ToolResult(
            ok=True,
            summary=(f"{found['window']} — '{section}': {text[:700]}"
                     + ("…" if len(text) > 700 else "")),
            data={"count": len(band), "took": found["took"], "window": found["window"],
                  "section": section, "cached": bool(found.get("cached")),
                  "words": [w.as_dict() for w in band[:80]]},
        )

    text = " · ".join(w.text for w in ordered)
    return ToolResult(
        ok=True,
        summary=(f"{found['window']}: {text[:700]}"
                 + ("…" if len(text) > 700 else "")),
        data={"count": found["count"], "took": found["took"],
              "window": found["window"], "cached": bool(found.get("cached")),
              "words": [w.as_dict() for w in ordered[:80]]},
    )
