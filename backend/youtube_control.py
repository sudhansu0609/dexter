"""
YouTube, in the Chrome the owner is actually looking at (v13).

The complaint: "it can't open a specific video from an opened YouTube". Dexter
could open youtube.com and it could search - `browser_open("youtube lofi")` -
but the moment a page of results was on screen there was no way to say "play the
second one" or "open the interview with the drummer". `browser_click` went
through a UIA walk that spent its whole budget on the hundreds of Buttons
YouTube renders before it ever reached a Hyperlink, so clicking a video by its
title could not work.

Two routes to a video, and the order matters:

1. **Click what the user can see.** If a YouTube page is in front, the video
   titles are Hyperlink/ListItem names in the accessibility tree; "the third
   one" and "the one about X" are answered from that list, and clicking it is
   exactly what the owner would have done with the mouse. Nothing is guessed.
2. **Resolve the title to a video id** over HTTP (the same search page the
   browser would fetch) and open `watch?v=...`. This is the fallback, and the
   only route when nothing is on screen yet - "play lofi hip hop on YouTube"
   from a cold start.

Playback control prefers the player's own buttons by name ("Play (k)", "Mute
(m)") over keystrokes, because `k` typed while the search box has focus is the
letter k, not a pause.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import chrome_control
import desktop_control as dc

SEARCH_URL = "https://www.youtube.com/results?search_query={}"
WATCH_URL = "https://www.youtube.com/watch?v={}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = float(os.getenv("DEXTER_YOUTUBE_TIMEOUT", "12") or 12)

#: Names in a YouTube page's accessibility tree that are navigation, not videos.
_CHROME_NAMES = re.compile(
    r"^(?:home|shorts|subscriptions|you|history|library|trending|music|gaming|"
    r"news|sports|learning|fashion|settings|report history|help|send feedback|"
    r"sign in|search|skip navigation|guide|create|notifications|filters|"
    r"watch later|liked videos|your videos|playlists|explore|browse channels|"
    r"premium|live|movies|more|less|show more|show less|next|previous|mute|"
    r"play|pause|full screen|theater mode|miniplayer|autoplay|subtitles|"
    r"captions|share|save|download|like|dislike|subscribe|subscribed|join|"
    r"comment|reply|sort comments|about|terms|privacy|policy & safety)\b",
    re.I,
)

#: A video's accessible name is its title, usually with duration and channel
#: appended ("... by Lofi Girl 3 hours ago 2 hours, 50 minutes").
_MIN_TITLE_CHARS = 8

#: How much of the top of the Chrome window is browser furniture, not the page,
#: measured from the window's top edge. A maximised Windows 11 Chrome stacks the
#: tab strip (~40), the omnibox (~40) and - the one that was being read as videos -
#: the **bookmarks bar** (~34), so the first pixel of the page proper is ~114 down.
#: The old value of 90 cut off mid-omnibox and let every bookmark through as a
#: "video title" (the reported bug). 130 clears the bookmarks bar with margin;
#: video thumbnails begin well below it. Configurable for a window that is not
#: maximised or a bookmarks bar that is hidden.
_HEADER_PX = int(os.getenv("DEXTER_YT_HEADER_PX", "130") or 130)


class YouTubeError(RuntimeError):
    """Something Dexter should say, not a traceback."""


# ───────────────────────── what is on screen ─────────────────────────


def _require_youtube_tab() -> dc.Win:
    """The Chrome window showing YouTube, or a refusal that says what to do."""
    try:
        win = chrome_control.active_chrome()
    except Exception:
        win = None
    if win is None:
        raise YouTubeError("Chrome isn't open. Say \"open youtube\" and I'll start there.")
    return win


def on_youtube() -> bool:
    """Is the tab in front a YouTube page?"""
    try:
        win = chrome_control.active_chrome()
        if win is None:
            return False
        if "youtube" in (win.title or "").lower():
            return True
        return "youtube.com" in (chrome_control.active_url(win) or "").lower()
    except Exception:
        return False


def page_query() -> str:
    """The search terms of the results page in front, if that is what it is."""
    try:
        url = chrome_control.active_url()
    except Exception:
        return ""
    if not url or "youtube.com/results" not in url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        return urllib.parse.parse_qs(parsed.query).get("search_query", [""])[0]
    except Exception:
        return ""


def _looks_like_a_video(label: str) -> bool:
    text = (label or "").strip()
    if len(text) < _MIN_TITLE_CHARS:
        return False
    if _CHROME_NAMES.match(text):
        return False
    # A bare duration, a view count, a date: part of a card, not its title.
    if re.fullmatch(r"[\d:,.]+(?:\s*(?:views?|ago|seconds?|minutes?|hours?|days?|weeks?|months?|years?))?",
                    text, re.I):
        return False
    return True


def _cluster_grid(cards: List[Dict[str, Any]], row_threshold: int = 65) -> List[Dict[str, Any]]:
    """Group cards into visual rows and columns (left-to-right, top-to-bottom)."""
    if not cards:
        return []
    cards.sort(key=lambda c: (c.get("top", 0), c.get("left", 0)))
    rows: List[List[Dict[str, Any]]] = []
    for card in cards:
        top = card.get("top", 0)
        if rows and abs(top - rows[-1][0].get("top", 0)) <= row_threshold:
            rows[-1].append(card)
        else:
            rows.append([card])

    out: List[Dict[str, Any]] = []
    for r_idx, row in enumerate(rows, 1):
        row.sort(key=lambda c: c.get("left", 0))
        for c_idx, card in enumerate(row, 1):
            card["row"] = r_idx
            card["col"] = c_idx
            card["position"] = len(out) + 1
            out.append(card)
    return out


_CDP_VIDEOS_JS = """
(limit) => {
  const selectors = [
    'ytd-rich-item-renderer',
    'ytd-video-renderer',
    'ytd-compact-video-renderer',
    'ytd-grid-video-renderer'
  ];
  const elements = Array.from(document.querySelectorAll(selectors.join(',')));
  const cards = [];
  for (const el of elements) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 50 || rect.height < 50) continue;
    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
    if (rect.right < 0 || rect.left > window.innerWidth) continue;
    const titleEl = el.querySelector('#video-title, #video-title-link, .yt-lockup-metadata-view-model__title');
    if (!titleEl) continue;
    const title = (titleEl.textContent || titleEl.getAttribute('title') || '').trim();
    if (!title || title.length < 4) continue;
    let url = titleEl.href || '';
    if (!url) {
      const a = el.querySelector('a#thumbnail, a[href*="watch?v="]');
      if (a) url = a.href;
    }
    const chanEl = el.querySelector('#channel-name, ytd-channel-name, .ytd-channel-name, #byline');
    const channel = chanEl ? chanEl.textContent.trim().replace(/\\s+/g, ' ') : '';
    const timeEl = el.querySelector('ytd-thumbnail-overlay-time-status-renderer, badge-shape, span.ytd-thumbnail-overlay-time-status-renderer');
    const length = timeEl ? timeEl.textContent.trim().replace(/\\s+/g, ' ') : '';
    cards.push({
      title,
      url,
      channel,
      length,
      top: Math.round(rect.top),
      left: Math.round(rect.left),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      cx: Math.round(rect.left + rect.width / 2),
      cy: Math.round(rect.top + rect.height / 2),
      kind: 'link',
      element: null,
      route: 'cdp'
    });
    if (cards.length >= limit * 2) break;
  }
  return cards;
}
"""


def _cdp_available() -> bool:
    try:
        import browser_cdp
        return browser_cdp.available()
    except Exception:
        return False


def _cdp_route_enabled() -> bool:
    """CDP is only trusted for YouTube once the owner has explicitly turned on

    precise browser control (`precise_browser` "start"). A debuggable Chrome
    merely being up (`_cdp_available()`) is not enough - that is the stray
    debug-Chrome hijack: it is a separate browser/profile from the one the
    owner is looking at, and reading/acting in it by surprise is worse than
    falling back to OCR/UIA.
    """
    try:
        import browser_cdp
        return browser_cdp.active_mode()
    except Exception:
        return False


def _detect_videos_from_cdp(limit: int = 25) -> List[Dict[str, Any]]:
    if not _cdp_route_enabled():
        return []
    try:
        import browser_cdp
        raw = browser_cdp.session._page().evaluate(_CDP_VIDEOS_JS, limit) or []
        cards = [c for c in raw if _looks_like_a_video(c.get("title", ""))]
        return _cluster_grid(cards)[:limit]
    except Exception:
        return []


def _detect_videos_from_screen(win: dc.Win, limit: int = 25) -> List[Dict[str, Any]]:
    """Detect videos and grid positions using screen OCR."""
    try:
        import screen_text
    except Exception:
        return []
    if not screen_text.availability().get("ready"):
        return []
    hwnd = int(getattr(win, "hwnd", 0) or 0)
    if not hwnd:
        return []
    try:
        state = screen_text.read(win=win)
    except Exception:
        return []
    words = state.get("words") or []
    if not words:
        return []
    try:
        box = screen_text.window_box(hwnd)
        win_top = box.get("top", 0)
    except Exception:
        win_top = 0
    # Everything above this line is the browser's own tabs/omnibox/bookmarks bar,
    # not the YouTube page. Reading it was what turned the owner's bookmarks into
    # "videos" they could not open.
    header_cutoff = win_top + _HEADER_PX

    candidates = []
    for w in words:
        if w.top < header_cutoff:
            continue
        text = w.text.strip()
        if not text:
            continue
        if _CHROME_NAMES.match(text) and len(text.split()) <= 3:
            continue
        candidates.append(w)
    if not candidates:
        return []

    candidates.sort(key=lambda item: (item.top, item.left))
    clusters: List[List[Any]] = []
    for w in candidates:
        matched = False
        for cluster in clusters:
            c_left = min(item.left for item in cluster)
            c_right = max(item.right for item in cluster)
            c_top = min(item.top for item in cluster)
            c_bottom = max(item.bottom for item in cluster)
            dx = max(0, max(c_left, w.left) - min(c_right, w.right))
            dy = w.top - c_bottom
            if dx <= 60 and -15 <= dy <= 38:
                cluster.append(w)
                matched = True
                break
        if not matched:
            clusters.append([w])

    cards: List[Dict[str, Any]] = []
    for cluster in clusters:
        cluster.sort(key=lambda item: (item.top, item.left))
        lines = []
        length = ""
        for item in cluster:
            t = item.text.strip()
            if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", t) or t.upper() == "LIVE":
                length = t
            elif re.search(r"\b(?:\d+[\d,.]*[KMB]?\s+views|views|ago|streamed)\b", t, re.I):
                continue
            else:
                lines.append(t)
        if not lines:
            continue
        title = " ".join(lines[:2]).strip()
        if len(title) < _MIN_TITLE_CHARS or not _looks_like_a_video(title):
            continue
        channel = lines[2] if len(lines) > 2 else ""
        c_left = min(item.left for item in cluster)
        c_right = max(item.right for item in cluster)
        c_top = min(item.top for item in cluster)
        c_bottom = max(item.bottom for item in cluster)
        cards.append({
            "title": title,
            "channel": channel,
            "length": length,
            "top": c_top,
            "left": c_left,
            "right": c_right,
            "bottom": c_bottom,
            "cx": (c_left + c_right) // 2,
            "cy": (c_top + min(c_top + 30, c_bottom)) // 2,
            "kind": "video",
            "element": None,
            "route": "screen_ocr",
        })

    grid_cards = _cluster_grid(cards, row_threshold=65)
    return grid_cards[:limit]


def _detect_videos_from_uia(win: dc.Win, limit: int = 25) -> List[Dict[str, Any]]:
    try:
        rows = dc.find_clickables("", window=win.title, types=("Hyperlink", "ListItem"))
    except Exception:
        rows = []
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows:
        if row.get("element") is None:
            continue
        label = (row.get("label") or "").strip()
        if not _looks_like_a_video(label):
            continue
        key = label.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": label,
            "kind": row.get("kind", "hyperlink"),
            "element": row["element"],
            "position": len(out) + 1,
            "row": len(out) + 1,
            "col": 1,
            "route": "uia",
        })
        if len(out) >= max(1, int(limit or 25)):
            break
    return out


def page_videos(limit: int = 25) -> List[Dict[str, Any]]:
    """The videos offered on the page in front, in the order they are shown.

    Checks CDP DOM first, then Screen OCR card/grid detection, and falls back to UIA.
    """
    win = _require_youtube_tab()
    # Tier 1: CDP - only when the owner has explicitly turned it on
    if _cdp_route_enabled():
        cdp_vids = _detect_videos_from_cdp(limit=limit)
        if cdp_vids:
            return cdp_vids
    # Tier 2: Screen Vision / OCR
    screen_vids = _detect_videos_from_screen(win, limit=limit)
    if screen_vids:
        return screen_vids
    # Tier 3: UIA
    return _detect_videos_from_uia(win, limit=limit)


def _click(element) -> str:
    for attempt in ("invoke", "select", "click_input"):
        action = getattr(element, attempt, None)
        if not callable(action):
            continue
        try:
            action()
            return attempt
        except Exception:
            continue
    return ""


def click_on_page(
    title: Optional[str] = None,
    index: Optional[int] = None,
    row: Optional[int] = None,
    col: Optional[int] = None,
) -> Dict[str, Any]:
    """Click a video on the page in front, by title words, position, or grid (row & col)."""
    videos = page_videos()
    if not videos:
        raise YouTubeError(
            "I can't see any video links on this page. If it just loaded, give it a "
            "second; otherwise tell me the title and I'll search for it."
        )
    chosen = None
    if row is not None and col is not None:
        r_val, c_val = int(row), int(col)
        chosen = next((v for v in videos if v.get("row") == r_val and v.get("col") == c_val), None)
        if chosen is None:
            grid_info = "; ".join(f"#{v['position']}: R{v.get('row')}C{v.get('col')}" for v in videos[:6])
            raise YouTubeError(f"I couldn't find a video at Row {row}, Column {col}. I can see: {grid_info}")
    elif title:
        needle = title.strip().lower()
        ranked = sorted(
            (v for v in videos if needle in v["title"].lower()),
            key=lambda v: (0 if v["title"].lower().startswith(needle) else 1, v["position"]),
        )
        chosen = ranked[0] if ranked else None
        if chosen is None:
            offered = "; ".join(v["title"][:60] for v in videos[:6])
            raise YouTubeError(
                f"Nothing on this page is called '{title}'. I can see: {offered}"
            )
    else:
        position = max(1, int(index or 1))
        if position > len(videos):
            raise YouTubeError(
                f"There are only {len(videos)} videos I can see on this page."
            )
        chosen = videos[position - 1]

    # Focus the window we are clicking in, and refuse rather than click blind.
    try:
        dc._focused_target(_require_youtube_tab().title)
    except dc.DesktopError as exc:
        raise YouTubeError(str(exc)) from exc

    how = ""
    route_type = chosen.get("route", "uia")
    if route_type == "cdp" and chosen.get("url"):
        try:
            import browser_cdp
            browser_cdp.session.goto(chosen["url"])
            how = "navigated via CDP"
        except Exception:
            how = ""
    if not how and (route_type == "screen_ocr" or chosen.get("cx") or chosen.get("point")):
        try:
            import mouse_input
            cx = chosen.get("cx") or (chosen["point"][0] if chosen.get("point") else 0)
            cy = chosen.get("cy") or (chosen["point"][1] if chosen.get("point") else 0)
            if cx and cy and mouse_input.available():
                mouse_input.move_to(cx, cy)
                time.sleep(0.05)
                mouse_input.click("left")
                how = f"clicked at ({cx}, {cy})"
        except Exception:
            how = ""
    if not how and chosen.get("element"):
        how = _click(chosen["element"])

    if not how:
        raise YouTubeError(f"I found '{chosen['title'][:60]}' but it would not take a click.")
    time.sleep(0.4)
    return {
        "opened": chosen["title"],
        "position": chosen["position"],
        "row": chosen.get("row", 1),
        "col": chosen.get("col", 1),
        "channel": chosen.get("channel", ""),
        "length": chosen.get("length", ""),
        "how": how,
        "route": "clicked on the page",
        "method": route_type,
        "title": chrome_control.tab_title(),
    }


def search_on_page(query: str) -> Dict[str, Any]:
    """Search YouTube on the page that is currently open without opening a new tab."""
    text = (query or "").strip()
    if not text:
        raise YouTubeError("What should I search YouTube for?")
    win = _require_youtube_tab()
    url = SEARCH_URL.format(urllib.parse.quote_plus(text))
    if _cdp_route_enabled():
        try:
            import browser_cdp
            info = browser_cdp.session.goto(url, same_tab=True)
            return {
                "searched": text,
                "url": info.get("url", url),
                "sameTab": True,
                "title": info.get("title", ""),
                "route": "cdp",
            }
        except Exception:
            pass
    info = chrome_control.open_in_current_tab(url)
    return {
        "searched": text,
        "url": url,
        "sameTab": True,
        "title": info.get("title", ""),
        "window": info.get("window", ""),
        "route": "current_tab",
    }


# ───────────────────────── resolving a title to a video ─────────────────────────


def _walk_renderers(node: Any, out: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if isinstance(node, dict):
        renderer = node.get("videoRenderer") or node.get("compactVideoRenderer")
        if isinstance(renderer, dict) and renderer.get("videoId"):
            title = renderer.get("title") or {}
            text = ""
            if isinstance(title, dict):
                if "runs" in title:
                    text = "".join(str(r.get("text", "")) for r in title["runs"])
                else:
                    text = str(title.get("simpleText") or "")
            channel = ""
            try:
                channel = str(renderer["ownerText"]["runs"][0]["text"])
            except Exception:
                channel = ""
            out.append({
                "videoId": str(renderer["videoId"]),
                "title": text.strip(),
                "channel": channel,
                "length": str((renderer.get("lengthText") or {}).get("simpleText") or ""),
                "url": WATCH_URL.format(renderer["videoId"]),
            })
        for value in node.values():
            _walk_renderers(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_renderers(value, out)
    return out


def search(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Search YouTube over HTTP and return the videos, in YouTube's own order.

    No API key and no login: this is the public results page, parsed. It is used
    to turn words into a `watch?v=` link; it never plays anything by itself.
    """
    text = (query or "").strip()
    if not text:
        raise YouTubeError("What should I search YouTube for?")
    try:
        import requests
    except Exception as exc:  # pragma: no cover - requests is a hard dependency
        raise YouTubeError("Searching YouTube needs requests installed.") from exc
    url = SEARCH_URL.format(urllib.parse.quote(text))
    try:
        res = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        raise YouTubeError(f"I couldn't reach YouTube ({exc}).") from exc
    if res.status_code >= 400:
        raise YouTubeError(f"YouTube answered {res.status_code} for that search.")

    rows: List[Dict[str, Any]] = []
    match = re.search(r"ytInitialData\s*=\s*(\{.*?\})\s*;\s*</script>", res.text, re.S)
    if match:
        try:
            rows = _walk_renderers(json.loads(match.group(1)), [])
        except Exception:
            rows = []
    if not rows:
        # The page shape changed; the ids are still in it, in order.
        ids = list(dict.fromkeys(re.findall(r'"videoId":"([\w-]{11})"', res.text)))
        rows = [{"videoId": vid, "title": "", "channel": "", "length": "",
                 "url": WATCH_URL.format(vid)} for vid in ids]
    if not rows:
        raise YouTubeError(f"YouTube returned nothing I could read for '{text}'.")
    # De-duplicate (a renderer can appear in several shelves) and keep order.
    unique: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows:
        if row["videoId"] in seen:
            continue
        seen.add(row["videoId"])
        unique.append(row)
    return unique[: max(1, int(limit or 8))]


def _best_match(rows: List[Dict[str, Any]], title: str) -> Dict[str, Any]:
    needle = (title or "").strip().lower()
    if not needle:
        return rows[0]
    for row in rows:
        if row["title"].strip().lower() == needle:
            return row
    for row in rows:
        if needle in row["title"].lower():
            return row
    return rows[0]


def open_video(
    title: Optional[str] = None,
    index: Optional[int] = None,
    row: Optional[int] = None,
    col: Optional[int] = None,
    query: Optional[str] = None,
    prefer_page: bool = True,
) -> Dict[str, Any]:
    """Open one video: from the page in front when it is there, else by search.

    * `title` - words from the video's title ("play the one about wgpu").
    * `index` - "the third one" / "play 3", meaning the third on the page in front.
    * `row`, `col` - grid coordinates ("row 1 column 2").
    * `query` - search terms when nothing is on screen yet.
    """
    if not (title or index or row or col or query):
        raise YouTubeError("Which video? Give me a title, say 'the first one', or specify a row and column.")

    page_first = prefer_page and on_youtube()
    if page_first:
        try:
            return click_on_page(title=title, index=index, row=row, col=col)
        except YouTubeError as exc:
            if (index or row or col) and not (title or query):
                # "the third one" or "row 1 col 2" only means something about the page in front.
                raise
            page_note = str(exc)
    else:
        page_note = ""

    terms = (query or title or "").strip()
    if not terms and index:
        terms = page_query()
    if not terms:
        raise YouTubeError(page_note or "I need a title or some search terms to find a video.")

    rows = search(terms, limit=max(8, int(index or 1)))
    if index and not title:
        position = max(1, int(index))
        if position > len(rows):
            raise YouTubeError(f"YouTube only gave me {len(rows)} results for '{terms}'.")
        chosen = rows[position - 1]
    else:
        chosen = _best_match(rows, title or "")

    info = chrome_control.open_url(chosen["url"])
    return {
        "opened": chosen["title"] or chosen["url"],
        "videoId": chosen["videoId"],
        "url": chosen["url"],
        "channel": chosen["channel"],
        "length": chosen["length"],
        "route": "searched YouTube and opened the link",
        "searched": terms,
        "note": page_note,
        "window": info.get("window", ""),
        "title": info.get("title", ""),
    }


# ───────────────────────── playback ─────────────────────────

#: The player's own buttons, by the accessible name YouTube gives them, with the
#: keyboard shortcut as the fallback. Buttons first: a keystroke sent while the
#: search box has focus types a letter instead of pausing the video.
CONTROLS: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "play": (("Play (k)", "Play"), "k"),
    "pause": (("Pause (k)", "Pause"), "k"),
    "toggle": (("Pause (k)", "Play (k)"), "k"),
    "mute": (("Mute (m)", "Mute"), "m"),
    "unmute": (("Unmute (m)", "Unmute"), "m"),
    "fullscreen": (("Full screen (f)", "Full screen"), "f"),
    "exit fullscreen": (("Exit full screen (f)", "Exit full screen"), "f"),
    "theater": (("Theater mode (t)",), "t"),
    "miniplayer": (("Miniplayer (i)",), "i"),
    "captions": (("Subtitles/closed captions (c)", "Subtitles"), "c"),
    "next": (("Next (SHIFT+n)", "Next"), "shift+n"),
    "previous": (("Previous (SHIFT+p)", "Previous"), "shift+p"),
    "like": (("Like this video along with", "Like"), ""),
    "forward": ((), "l"),
    "back": ((), "j"),
    "faster": ((), "shift+."),
    "slower": ((), "shift+,"),
    "skip ad": (("Skip Ads", "Skip Ad", "Skip"), ""),
}

_ACTION_WORDS = {
    "play": "play", "resume": "play", "continue": "play", "unpause": "play",
    "pause": "pause", "stop": "pause", "hold": "pause",
    "play pause": "toggle", "toggle": "toggle",
    "mute": "mute", "silence": "mute", "unmute": "unmute",
    "full screen": "fullscreen", "fullscreen": "fullscreen", "maximise": "fullscreen",
    "exit full screen": "exit fullscreen", "leave full screen": "exit fullscreen",
    "theater": "theater", "theatre": "theater", "cinema": "theater",
    "miniplayer": "miniplayer", "mini player": "miniplayer",
    "captions": "captions", "subtitles": "captions", "cc": "captions",
    "next": "next", "next video": "next", "skip": "next", "skip video": "next",
    "previous": "previous", "previous video": "previous", "back a video": "previous",
    "forward": "forward", "skip ahead": "forward", "fast forward": "forward",
    "back": "back", "rewind": "back", "go back": "back",
    "faster": "faster", "speed up": "faster", "slower": "slower", "slow down": "slower",
    "like": "like", "skip ad": "skip ad", "skip the ad": "skip ad", "skip ads": "skip ad",
}


def normalize_action(action: str) -> str:
    """"make it full screen", "turn on captions", "pause the video" -> one word."""
    text = re.sub(r"\s+", " ", (action or "").strip().lower()).strip(" .!?")
    # The verbs people put in front of a player action. Stripped first, because
    # "turn on captions" and "captions" are the same request.
    text = re.sub(
        r"^(?:please\s+)?(?:can\s+you\s+)?"
        r"(?:turn\s+(?:on|off)|switch\s+(?:on|off)|enable|disable|show|hide|put\s+on|"
        r"make(?:\s+(?:it|this|the\s+video))?|set|go)\s+",
        "", text,
    )
    text = re.sub(r"^(?:the\s+|this\s+)?(?:video|music|song|player)\s+", "", text)
    text = re.sub(r"\s+(?:the\s+)?(?:video|music|song|player|it|this)$", "", text).strip()
    text = re.sub(r"^(?:the|a)\s+", "", text)
    return _ACTION_WORDS.get(text, text)


def control(action: str) -> Dict[str, Any]:
    """Do one thing to the player in the YouTube tab in front."""
    wanted = normalize_action(action)
    spec = CONTROLS.get(wanted)
    if not spec:
        known = ", ".join(sorted(set(_ACTION_WORDS.values())))
        raise YouTubeError(f"I don't know how to '{action}' a video. I can: {known}.")
    win = _require_youtube_tab()
    names, keys = spec

    for name in names:
        try:
            rows = [r for r in dc.find_clickables(name, window=win.title, types=("Button",),
                                                  budget=2.0, total=4.0)
                    if r.get("element") is not None]
        except dc.DesktopError:
            rows = []
        if rows:
            try:
                dc._focused_target(win.title)
            except dc.DesktopError as exc:
                raise YouTubeError(str(exc)) from exc
            how = _click(rows[0]["element"])
            if how:
                return {"action": wanted, "how": f"clicked '{rows[0]['label']}'",
                        "window": win.describe()}
    if not keys:
        raise YouTubeError(
            f"I couldn't find the {wanted} button on this page, and there is no "
            "keyboard shortcut for it."
        )
    try:
        target = dc._focused_target(win.title)
    except dc.DesktopError as exc:
        raise YouTubeError(str(exc)) from exc
    dc._send_keys(keys)
    return {"action": wanted, "how": f"pressed {keys}", "window": target.describe()}


# ───────────────────────── the things around a video (v13.2) ─────────────────────────
#
# "It shall be able to open comment, like, subscribe any video I want." These are
# page buttons rather than player controls, and YouTube labels them for screen
# readers - which is exactly what makes them findable by name. Each entry is the
# names to try, longest-lived first; YouTube's like button has carried a count in
# its label for years ("like this video along with 12,345 other people"), so the
# match is a *contains* on a stable prefix.

PAGE_ACTIONS: Dict[str, Tuple[str, ...]] = {
    "like": ("Like this video along with", "like this video", "Like"),
    "unlike": ("Unlike", "Remove like"),
    "dislike": ("Dislike this video", "Dislike"),
    "subscribe": ("Subscribe to", "Subscribe"),
    "unsubscribe": ("Unsubscribe from", "Unsubscribe", "Subscribed to", "Subscribed"),
    "comments": ("Comments", "View comments", "Top comments"),
    "share": ("Share",),
    "save": ("Save to playlist", "Save"),
    "description": ("Show more", "...more", "Description"),
    "notifications": ("Notifications", "Notification preferences"),
    "join": ("Join this channel", "Join"),
}

_PAGE_WORDS = {
    "like": "like", "like it": "like", "like this": "like", "thumbs up": "like",
    "unlike": "unlike", "remove my like": "unlike",
    "dislike": "dislike", "thumbs down": "dislike",
    "subscribe": "subscribe", "sub": "subscribe", "follow": "subscribe",
    "unsubscribe": "unsubscribe", "unfollow": "unsubscribe",
    "comments": "comments", "comment": "comments", "open comments": "comments",
    "show comments": "comments", "read the comments": "comments",
    "share": "share", "save": "save", "add to playlist": "save",
    "save to a playlist": "save", "save to playlist": "save", "add to a playlist": "save",
    "save it": "save", "share it": "share", "share this": "share",
    "description": "description", "show more": "description", "more": "description",
    "notifications": "notifications", "bell": "notifications",
    "join": "join", "become a member": "join",
}


def normalize_page_action(action: str) -> str:
    text = re.sub(r"\s+", " ", (action or "").strip().lower()).strip(" .!?")
    text = re.sub(r"^(?:please\s+)?(?:can\s+you\s+)?(?:go\s+and\s+)?", "", text)
    text = re.sub(r"^(?:open|show|give\s+me|press|click|hit|tap|do)\s+(?:the\s+)?", "", text)
    text = re.sub(r"\s+(?:this|that|the)\s+(?:video|channel|one)$", "", text)
    text = re.sub(r"^(?:to\s+)?(?:this|that|the)\s+(?:video|channel)\s+", "", text)
    # "subscribe to this channel" -> "subscribe": the preposition survives the
    # noun being stripped, and "subscribe to" is not a key.
    text = re.sub(r"\s+(?:to|on|for|of|with|from|in|at)$", "", text)
    text = re.sub(r"\s+(?:section|button|link)$", "", text)
    return _PAGE_WORDS.get(text, text)


def page_action(action: str) -> Dict[str, Any]:
    """Press one of the buttons around the video: like, subscribe, comments…

    Comments are a scroll as well as a click: YouTube only builds the comment
    section when it is in view, so jumping to it is what "open the comments"
    actually means on that page.
    """
    wanted = normalize_page_action(action)
    names = PAGE_ACTIONS.get(wanted)
    if not names:
        known = ", ".join(sorted(set(_PAGE_WORDS.values())))
        raise YouTubeError(f"I don't know how to '{action}' on YouTube. I can: {known}.")
    win = _require_youtube_tab()

    if wanted == "comments":
        # Scroll the comments into view first; the section is lazy-loaded, so the
        # heading does not exist in the tree until the page has been moved.
        try:
            dc.scroll("down", amount=3, window=win.title)
            time.sleep(0.8)
        except dc.DesktopError as exc:
            raise YouTubeError(str(exc)) from exc
        found = _find_named(names, win)
        if found:
            return {"action": wanted, "how": f"scrolled to '{found[1]}'",
                    "window": win.describe()}
        return {"action": wanted, "how": "scrolled down to the comments",
                "window": win.describe()}

    found = _find_named(names, win)
    if not found:
        offered = _visible_buttons(win)
        raise YouTubeError(
            f"I can't find the {wanted} button on this page."
            + (f" I can see: {offered}" if offered else
               " Is the video page actually open (rather than a search or a shorts feed)?")
        )
    target_obj, label, method = found
    try:
        dc._focused_target(win.title)
    except dc.DesktopError as exc:
        raise YouTubeError(str(exc)) from exc
    if method == "screen_ocr":
        import mouse_input
        cx, cy = target_obj
        if mouse_input.available():
            mouse_input.move_to(cx, cy)
            time.sleep(0.05)
            mouse_input.click("left")
            time.sleep(0.3)
            return {"action": wanted, "how": f"clicked '{label}' on screen", "window": win.describe()}
    how = _click(target_obj)
    if not how:
        raise YouTubeError(f"I found '{label}' but it would not take a click.")
    time.sleep(0.3)
    return {"action": wanted, "how": f"clicked '{label}'", "window": win.describe()}


def _find_named(names: Tuple[str, ...], win: dc.Win):
    """The first control whose name starts with or contains one of `names`."""
    for name in names:
        try:
            rows = [row for row in dc.find_clickables(name, window=win.title,
                                                      types=("Button", "ToggleButton",
                                                             "Hyperlink", "Text"),
                                                      budget=2.0, total=5.0)
                    if row.get("element") is not None]
        except dc.DesktopError:
            rows = []
        if rows:
            return rows[0]["element"], rows[0]["label"], "uia"
    try:
        import screen_text
        if screen_text.availability().get("ready"):
            for name in names:
                hit = screen_text.find(name, win=win)
                if hit.get("found") and hit.get("match"):
                    return hit["match"]["centre"], hit["match"]["text"], "screen_ocr"
    except Exception:
        pass
    return None


def _visible_buttons(win: dc.Win, limit: int = 14) -> str:
    try:
        rows = dc.find_clickables("", window=win.title, types=("Button",),
                                 budget=1.5, total=3.0)
    except Exception:
        return ""
    names = [row["label"] for row in rows if row.get("label") and len(row["label"]) < 44]
    return ", ".join(list(dict.fromkeys(names))[:limit])[:300]


# ───────────────────────── writing a comment (v13.10) ─────────────────────────
#
# "on same page play and comment and like on video." Like and subscribe are
# labelled buttons - findable by name. *Writing* a comment is a text box plus a
# submit, and a public post is not something to fire off unseen. So this is two
# steps: type the comment into the box and hand it back for a look; only a second
# call with post=True presses Comment. The owner says "post it" in between, so
# nothing is published without them seeing it first.

#: The comment box's placeholder/label, as YouTube names it for screen readers.
_COMMENT_BOX_NAMES: Tuple[str, ...] = (
    "Add a comment…", "Add a comment...", "Add a comment", "Start the conversation",
)
#: The blue submit button that appears once the box has focus and text.
_COMMENT_SUBMIT_NAMES: Tuple[str, ...] = ("Comment",)


def _click_found(found, win: dc.Win) -> str:
    """Click a `_find_named` hit (UIA element or an OCR centre), report how."""
    target_obj, label, method = found
    try:
        dc._focused_target(win.title)
    except dc.DesktopError as exc:
        raise YouTubeError(str(exc)) from exc
    if method == "screen_ocr":
        import mouse_input
        cx, cy = target_obj
        if mouse_input.available():
            mouse_input.move_to(cx, cy)
            time.sleep(0.05)
            mouse_input.click("left")
            time.sleep(0.3)
            return f"clicked '{label}' on screen"
        raise YouTubeError(f"I found '{label}' but can't drive the mouse to click it.")
    how = _click(target_obj)
    if not how:
        raise YouTubeError(f"I found '{label}' but it would not take a click.")
    time.sleep(0.3)
    return f"clicked '{label}'"


def post_comment(text: Optional[str] = None, post: bool = False) -> Dict[str, Any]:
    """Write a comment on the video in front, in two steps.

    * First call (``text`` given, ``post`` False): scroll to the comments, click the
      box, type the comment, and hand it back **unsent**.
    * Second call (``post`` True): press the Comment button to publish what was
      typed.

    Never posts in one shot - a public comment gets a look first.
    """
    win = _require_youtube_tab()
    if not on_youtube():
        raise YouTubeError("Open the video on YouTube first, then tell me what to comment.")

    if post:
        found = _find_named(_COMMENT_SUBMIT_NAMES, win)
        if not found:
            raise YouTubeError(
                "I can't find the Comment button to publish it. If you've typed the "
                "comment, the button is next to the box - it appears once there's text."
            )
        how = _click_found(found, win)
        return {"posted": True, "how": how, "window": win.describe()}

    body = (text or "").strip()
    if not body:
        raise YouTubeError("What should the comment say?")

    # YouTube builds the comment section only when it is in view, so move the page
    # first - the box does not exist in the tree until then.
    try:
        dc.scroll("down", amount=3, window=win.title)
        time.sleep(0.8)
    except dc.DesktopError as exc:
        raise YouTubeError(str(exc)) from exc

    found = _find_named(_COMMENT_BOX_NAMES, win)
    if not found:
        raise YouTubeError(
            "I can't find the comment box on this page. Is the video page open "
            "(rather than a shorts feed), and are comments turned on for it?"
        )
    how_box = _click_found(found, win)

    # Type where focus now sits - the box we just clicked.
    try:
        chrome_control.type_text(body, submit=False)
    except Exception as exc:  # noqa: BLE001 - report, never raise
        raise YouTubeError(f"I clicked the comment box but couldn't type into it: "
                           f"{str(exc).splitlines()[0][:120]}") from exc

    return {
        "typed": body,
        "pending": True,
        "how": how_box,
        "window": win.describe(),
    }


def availability() -> Dict[str, Any]:
    return {
        "onYouTube": on_youtube(),
        "query": page_query(),
        "uia": dc.availability().get("uia"),
    }
