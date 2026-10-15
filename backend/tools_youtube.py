"""
YouTube as tools (v13).

"Open a specific video from the YouTube that's already open" - the gap these
close. `browser_open` could put a results page on screen and `play_on_youtube`
could search; neither could then play anything, and "play the second one" had no
tool at all.

Every tool here says *how* it got there - clicked the link on your page, or
searched and opened the result - because those are different claims and only one
of them is about what the owner could see.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import youtube_control as yt
from tool_registry import ToolResult, tool


def _guard(fn) -> ToolResult:
    try:
        return fn()
    except yt.YouTubeError as exc:
        return ToolResult.fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool reports, never raises
        return ToolResult.fail(f"That didn't work: {str(exc).splitlines()[0][:200]}")


@tool(
    name="youtube_play",
    description=(
        "Play a specific video on YouTube. Give the title (or words from it) to "
        "pick a video by name, the position number (1, 2, 3...) to take the nth one from "
        "the page the user is looking at, or the grid row and column (e.g. row 1, col 2). "
        "Clicks the link on screen when YouTube is open, otherwise searches YouTube and opens the result."
    ),
    params={
        "title": {"type": "string", "description": "Words from the video's title", "required": False},
        "position": {"type": "integer", "description": "1 for the first video on the page, 2 for the second",
                     "required": False},
        "row": {"type": "integer", "description": "Grid row number (1 for top row)", "required": False},
        "col": {"type": "integer", "description": "Grid column number (1 for left column)", "required": False},
        "query": {"type": "string", "description": "Search terms, when nothing is open yet",
                  "required": False},
    },
    examples=[
        "play video 1",
        "play the first video",
        "open the second video on this page",
        "play 2",
        "play the third one",
        "play row 1 column 2",
        "open row 2 col 1",
        "open the video called lofi hip hop radio",
        "play the one about wgpu",
        "play that video",
        "open the top result on youtube",
        "play lofi hip hop radio on youtube",
    ],
    category="media",
)
def youtube_play(
    title: Optional[str] = None,
    position: Optional[int] = None,
    row: Optional[int] = None,
    col: Optional[int] = None,
    query: Optional[str] = None,
) -> ToolResult:
    def _do():
        info = yt.open_video(title=title, index=position, row=row, col=col, query=query)
        where = info.get("route", "")
        extra = ""
        if info.get("channel"):
            extra = f" ({info['channel']}{', ' + info['length'] if info.get('length') else ''})"
        grid_desc = f" [Row {info['row']}, Col {info['col']}]" if info.get("row") and info.get("col") else ""
        return ToolResult(
            ok=True,
            summary=f"Playing '{str(info['opened'])[:80]}'{extra}{grid_desc} - {where}.",
            data=info,
        )

    return _guard(_do)


@tool(
    name="youtube_results",
    description=(
        "List what YouTube offers for a search, or the videos visible on the page "
        "the user is looking at with their numbers and grid positions, so one can be chosen by number or row/column."
    ),
    params={
        "query": {"type": "string", "description": "Search terms; omit to read the current page",
                  "required": False},
        "limit": {"type": "integer", "description": "How many (default 8)", "required": False},
    },
    examples=[
        "what videos are on this page",
        "what's on youtube for lofi hip hop",
        "list the youtube results",
        "search youtube for rust wgpu tutorials and show me the list",
        "read me the video titles on screen",
        "show videos on this page",
    ],
    category="media",
)
def youtube_results(query: Optional[str] = None, limit: int = 8) -> ToolResult:
    def _do():
        budget = max(1, min(int(limit or 8), 25))
        if query:
            rows = yt.search(query, limit=budget)
            lines = [f"{i}. {r['title'] or r['url']}"
                     + (f" - {r['channel']}" if r.get("channel") else "")
                     + (f" ({r['length']})" if r.get("length") else "")
                     for i, r in enumerate(rows, 1)]
            return ToolResult(
                ok=True,
                summary=f"YouTube for '{query}': " + "; ".join(lines),
                data={"query": query, "results": [
                    {k: v for k, v in r.items() if k != "element"} for r in rows]},
            )
        videos = yt.page_videos(limit=budget)
        if not videos:
            return ToolResult.fail(
                "I can't see any videos on the page in front. Open YouTube, or tell "
                "me what to search for."
            )
        lines = [
            f"{v['position']}. {v['title'][:65]}"
            + (f" [Row {v['row']}, Col {v['col']}]" if v.get("row") and v.get("col") else "")
            + (f" - {v['channel']}" if v.get("channel") else "")
            + (f" ({v['length']})" if v.get("length") else "")
            for v in videos
        ]
        return ToolResult(
            ok=True,
            summary=f"{len(videos)} video(s) on this page: " + "; ".join(lines) + ". Say 'play 1' or 'play row 1 column 2' to open.",
            data={"videos": [
                {"position": v["position"], "row": v.get("row"), "col": v.get("col"),
                 "title": v["title"], "channel": v.get("channel"), "length": v.get("length")}
                for v in videos
            ]},
        )

    return _guard(_do)


@tool(
    name="youtube_search_page",
    description=(
        "Search YouTube directly on the page/tab currently open, without opening a "
        "new tab. Use for 'search a video on the same page' or 'search for lofi on this page'."
    ),
    params={
        "query": {"type": "string", "description": "Search terms to find on YouTube"},
    },
    examples=[
        "search lofi on this page",
        "search a video on the same page",
        "search for tutorials on this page",
        "search youtube in this tab for python",
    ],
    category="media",
)
def youtube_search_page(query: str) -> ToolResult:
    def _do():
        info = yt.search_on_page(query)
        return ToolResult(
            ok=True,
            summary=f"Searched YouTube for '{info['searched']}' on this page.",
            data=info,
        )

    return _guard(_do)


@tool(
    name="youtube_control",
    description=(
        "Control the YouTube video that is playing: pause, play, next, previous, "
        "mute, captions, full screen, faster, slower, skip ahead, skip an ad."
    ),
    params={
        "action": {
            "type": "string",
            "description": "pause, play, next, previous, mute, unmute, captions, "
                           "fullscreen, faster, slower, forward, back, skip ad",
        }
    },
    examples=[
        "pause the video",
        "resume the video",
        "next video",
        "mute this",
        "turn on captions",
        "make it full screen",
        "skip the ad",
        "skip ahead",
        "play it faster",
    ],
    category="media",
)
def youtube_control(action: str) -> ToolResult:
    def _do():
        info = yt.control(action)
        return ToolResult(
            ok=True,
            summary=f"{info['action'].capitalize()}: {info['how']} in {info['window']}.",
            data=info,
        )

    return _guard(_do)


@tool(
    name="youtube_comment",
    description=(
        "Write a comment on the YouTube video the user is looking at. Give the "
        "comment text to TYPE it into the box - it is shown to the user first and NOT "
        "posted. Call again with post=true (when the user says 'post it' / 'submit') "
        "to publish it. Never posts a comment without that second confirming step."
    ),
    params={
        "text": {"type": "string", "description": "The comment to write", "required": False},
        "post": {"type": "boolean",
                 "description": "True to publish the comment that was already typed",
                 "required": False},
    },
    examples=[
        "comment great video",
        "comment 'thanks for the tutorial'",
        "leave a comment saying this really helped",
        "write a comment: loved this",
        "reply saying nice work",
        "post it",
        "post the comment",
        "submit the comment",
    ],
    category="media",
)
def youtube_comment(text: Optional[str] = None, post: bool = False) -> ToolResult:
    def _do():
        info = yt.post_comment(text=text, post=bool(post))
        if info.get("posted"):
            return ToolResult(
                ok=True,
                summary=f"Posted your comment on the video ({info['how']}).",
                data=info,
            )
        typed = str(info.get("typed", ""))
        return ToolResult(
            ok=True,
            summary=(f"I've typed under the video: \"{typed[:160]}\". "
                     "Say \"post it\" to publish it, or tell me a different comment."),
            data=info,
        )

    return _guard(_do)
