"""
MastGaane as Dexter's music.

"play some ghazals", "play Hawa Mahal", "play songs by Jagjit Singh", "I like this song",
"never play this again" — MastGaane runs its FM jockey on the PC speakers (its `/api/player/*`
routes) and these tools are the remote. The wish is passed through as said; MastGaane's
`resolve_focus()` turns it into a genre, language, era, programme or singer.

MastGaane is found on the port it is actually on (`apps_registry.live_port`, the same discovery as
"what's running") and started through `open_app` when it is not running, so Sentinel still gets
its say. ffplay does not show up in Windows' media session, so `media_control` and
`get_now_playing` ask `take_over()` first: while MastGaane is playing, "pause the music",
"skip this song" and "what's playing" go to it instead of the media keys.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import apps_registry
from tool_registry import ToolResult, tool

APP_ID = "mastgaane"
START_WAIT_S = 30.0


def _port() -> Optional[int]:
    record = apps_registry.get(APP_ID)
    if not record:
        return None
    try:
        return apps_registry.live_port(record)
    except Exception:
        return None


def _request(method: str, route: str, body: Optional[Dict[str, Any]] = None,
             port: Optional[int] = None, timeout: float = 8.0) -> ToolResult:
    port = port or _port()
    if not port:
        return ToolResult(ok=False, summary="MastGaane isn't running. Say \"play some ghazals\" to start it.",
                          data={"running": False})
    import requests

    try:
        res = requests.request(method, f"http://127.0.0.1:{port}/api/player/{route}", json=body, timeout=timeout)
    except Exception as exc:
        return ToolResult(ok=False, summary=f"MastGaane did not answer ({exc}).", data={"port": port})
    try:
        payload = res.json()
    except ValueError:
        payload = {}
    if res.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        return ToolResult(ok=False, summary=f"MastGaane: {detail or res.reason}.",
                          data={"status": res.status_code, "port": port})
    return ToolResult(ok=True, summary=payload.get("summary") or "Done.", data=payload)


def _ensure_running() -> ToolResult | int:
    port = _port()
    if port:
        return port
    import tools_apps

    opened = tools_apps.open_app(APP_ID)
    if not opened.ok:
        return opened
    deadline = time.monotonic() + START_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(1.0)
        port = _port()
        if port:
            return port
    return ToolResult(ok=False, summary="I started MastGaane but it did not come up in time.", data={"running": False})


def active() -> Optional[Dict[str, Any]]:
    """MastGaane's player state when it is playing or paused, else None. Never starts anything."""
    port = _port()
    if not port:
        return None
    res = _request("GET", "now", port=port, timeout=2.0)
    if res.ok and res.data.get("state") in ("playing", "paused"):
        return res.data
    return None


def take_over(action: str) -> Optional[ToolResult]:
    """`media_control`'s action, done on MastGaane if it is the one playing; None = not ours."""
    now = active()
    if now is None:
        return None
    if action == "playpause":
        action = "resume" if now["state"] == "paused" else "pause"
    route = {"play": "resume", "resume": "resume", "pause": "pause", "next": "next", "stop": "stop"}.get(action)
    if route is None:
        return ToolResult(ok=False, summary="MastGaane can't go back a song — say \"next\" or name what you want.",
                          data=now)
    res = _request("POST", route)
    if res.ok and route == "next":
        return ToolResult(ok=True, summary="Skipped. MastGaane is lining up the next one.", data=res.data)
    if res.ok and route == "stop":
        return ToolResult(ok=True, summary="Stopped MastGaane.", data=res.data)
    return res


def now_playing() -> Optional[ToolResult]:
    """`get_now_playing`, answered by MastGaane when it is the one playing; None = not ours."""
    now = active()
    if now is None:
        return None
    return ToolResult(ok=True, summary=now.get("summary") or "MastGaane is playing.", data=now)


# ───────────────────────── tools ─────────────────────────


@tool(
    name="mastgaane_play",
    description=(
        "Play music on the PC through MastGaane, the owner's Hindi/Urdu music jockey (film songs, "
        "ghazals, Bhojpuri folk, old Vividh Bharati programmes). `focus` is what they asked for, as "
        "said: a genre (ghazals, folk, bhajans), a language (Bhojpuri, Urdu), an era (90s, purane "
        "gaane), a programme (Hawa Mahal, Chhaya Geet, Jaimala) or a singer. Empty for the usual mix."
    ),
    params={
        "focus": {"type": "string", "description": "What to play, in the user's words", "required": False},
    },
    examples=[
        "play some ghazals", "play hawa mahal", "play bhojpuri songs", "play old hindi songs",
        "play mastgaane", "start mastgaane fm", "play songs by jagjit singh", "put on some purane gaane",
        "play chhaya geet", "play some music", "play 90s songs", "play jaimala on mastgaane",
    ],
    category="media",
)
def mastgaane_play(focus: str = "") -> ToolResult:
    port = _ensure_running()
    if isinstance(port, ToolResult):
        return port
    res = _request("POST", "play", {"focus": (focus or "").strip()[:120]}, port=port, timeout=15.0)
    if res.ok:
        wish = res.data.get("focus") or ""
        return ToolResult(ok=True, summary=f"Starting MastGaane{' — ' + wish if wish else ''}.", data=res.data)
    return res


@tool(
    name="mastgaane_feedback",
    description=(
        "Tell MastGaane whether the owner likes what it is playing right now. Like = more of this "
        "(the song is kept); dislike = never again (the song is dropped and it skips)."
    ),
    params={
        "verdict": {"type": "string", "description": "like or dislike", "enum": ["like", "dislike"]},
    },
    examples=[
        "i like this song", "more like this", "i love this ghazal", "i don't like this song",
        "never play this song again", "i hate this song",
    ],
    category="media",
)
def mastgaane_feedback(verdict: str = "like") -> ToolResult:
    verdict = "dislike" if str(verdict).lower().startswith(("dis", "hate", "no")) else "like"
    if active() is None:
        return ToolResult(ok=False, summary="MastGaane isn't playing anything right now.", data={})
    res = _request("POST", verdict)
    if not res.ok:
        return res
    if verdict == "like":
        return ToolResult(ok=True, summary="Noted — more like this.", data=res.data)
    return ToolResult(ok=True, summary="Got it, never again. Skipping.", data=res.data)
