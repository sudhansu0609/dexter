"""
System-wide media control via the Windows System Media Transport Controls.

SMTC is what the keyboard media keys drive, so this controls whatever is
actually playing - Spotify, YouTube in a browser, VLC, Netflix - without any
per-service OAuth, API key or Premium subscription. It also reports the current
track, which the Spotify Web API could only do for Spotify.

Falls back to synthesising virtual media keypresses when winsdk is unavailable;
that still controls playback, it just cannot read what is playing.
"""

import asyncio
import ctypes
from typing import Any, Dict, Optional

from tool_registry import ToolResult, tool

try:
    from winsdk.windows.media.control import (  # type: ignore
        GlobalSystemMediaTransportControlsSessionManager as _SessionManager,
    )

    _HAS_WINSDK = True
except Exception:
    _SessionManager = None  # type: ignore
    _HAS_WINSDK = False


# Virtual key codes for the media keys, used by the fallback path.
_VK = {
    "playpause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
}
_KEYEVENTF_KEYUP = 0x0002


def _press_media_key(name: str) -> bool:
    code = _VK.get(name)
    if not code:
        return False
    try:
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, _KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False


async def _current_session():
    manager = await _SessionManager.request_async()
    return manager.get_current_session()


def _run(coro):
    """SMTC is async-only; each call gets its own short-lived loop."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


async def _now_playing_async() -> Optional[Dict[str, Any]]:
    session = await _current_session()
    if not session:
        return None
    props = await session.try_get_media_properties_async()
    playback = session.get_playback_info()
    status_map = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
    return {
        "title": props.title or "",
        "artist": props.artist or "",
        "album": props.album_title or "",
        "app": session.source_app_user_model_id or "",
        "status": status_map.get(int(playback.playback_status), "unknown"),
    }


def now_playing() -> Optional[Dict[str, Any]]:
    if not _HAS_WINSDK:
        return None
    try:
        return _run(_now_playing_async())
    except Exception:
        return None


async def _control_async(action: str) -> bool:
    session = await _current_session()
    if not session:
        return False
    if action == "play":
        return await session.try_play_async()
    if action == "pause":
        return await session.try_pause_async()
    if action == "playpause":
        return await session.try_toggle_play_pause_async()
    if action == "next":
        return await session.try_skip_next_async()
    if action == "previous":
        return await session.try_skip_previous_async()
    if action == "stop":
        return await session.try_stop_async()
    return False


def _control(action: str) -> bool:
    if _HAS_WINSDK:
        try:
            if _run(_control_async(action)):
                return True
        except Exception:
            pass
    # Media keys have no separate play/pause, only a toggle.
    return _press_media_key({"play": "playpause", "pause": "playpause"}.get(action, action))


# ───────────────────────── tools ─────────────────────────


@tool(
    name="media_control",
    description=(
        "Control whatever media is currently playing on the PC - Spotify, "
        "YouTube, VLC, any player. Pause, resume, skip or go back."
    ),
    params={
        "action": {
            "type": "string",
            "description": "What to do",
            "enum": ["play", "pause", "playpause", "next", "previous", "stop"],
        }
    },
    examples=[
        "pause the music", "resume playback", "skip this song", "next track",
        "go back a song", "play it again", "stop the music", "unpause",
        "skip this", "previous track",
    ],
    category="media",
)
def media_control(action: str) -> ToolResult:
    if not _control(action):
        return ToolResult.fail("Nothing seems to be playing right now.")

    verb = {
        "play": "Resumed", "pause": "Paused", "playpause": "Toggled",
        "next": "Skipped ahead", "previous": "Went back", "stop": "Stopped",
    }.get(action, "Done")

    track = now_playing()
    if track and track.get("title") and action in ("next", "previous", "play"):
        artist = f" by {track['artist']}" if track.get("artist") else ""
        return ToolResult(ok=True, summary=f"{verb}. Now playing {track['title']}{artist}.",
                          data=track)
    return ToolResult(ok=True, summary=f"{verb}.", data=track or {})


@tool(
    name="get_now_playing",
    description="Find out what music or video is currently playing on the PC.",
    params={},
    examples=[
        "what's playing", "what song is this", "who sings this",
        "what am I listening to", "now playing",
    ],
    category="media",
)
def get_now_playing() -> ToolResult:
    if not _HAS_WINSDK:
        return ToolResult.fail(
            "Reading the current track needs winsdk. Run: pip install winsdk"
        )
    track = now_playing()
    if not track or not track.get("title"):
        return ToolResult(ok=True, summary="Nothing is playing right now.", data={})

    artist = f" by {track['artist']}" if track.get("artist") else ""
    state = "Playing" if track.get("status") == "playing" else track.get("status", "").capitalize()
    return ToolResult(ok=True, summary=f"{state}: {track['title']}{artist}", data=track)


def availability() -> Dict[str, Any]:
    return {"smtc": _HAS_WINSDK, "mediaKeys": True}
