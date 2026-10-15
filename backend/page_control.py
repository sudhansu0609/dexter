"""
Doing things *on the page* (v13.2).

What the owner found missing, in their words: "in chrome I ask it to select the
first profile it fails", "in opened youtube I ask it to select the first video it
fails", "I ask it to search a video by its name in current tab it fails", "it shall
be able to open comment, like, subscribe any video", "it shall be able to search
anything on youtube or on google and click the links", "it shall be able to copy a
section of text as I say and paste it in any other tab at any other field I say".

Every one of those is the same shape: *the thing I mean is on the screen, pick it*.
So this module is about the page rather than the browser - the four verbs that were
missing:

  search_here   search in the tab you are already in, rather than opening a new one
  results       what this page is offering, in the order it is offering it
  open_result   the nth one, or the one whose title I said
  click_nth     the nth thing of a kind - a profile card, an option, a row

plus the two that move text between places:

  copy_here     the selection, everything, or a named field's contents
  paste_there   optionally switch tab, optionally click a field, then paste

Three rules:

* **Say which route was taken.** "Clicked 'lofi hip hop radio' (3rd link on the
  page)" and "searched Google and opened the first result" are different claims.
* **Name what was seen on a miss.** A page that does not expose what was asked for
  is not a failure to report as "it didn't work": the controls it *does* expose are
  the useful half of the answer.
* **Never guess a target for a paste.** Typing into the wrong field is worse than
  not typing: with no field named and nothing focused that takes text, it refuses.
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import chrome_control
import desktop_control as dc

#: Names in a page's accessibility tree that are the site's own furniture rather
#: than a result. Matched on the whole name, case-insensitively.
_FURNITURE = re.compile(
    r"^(?:home|shorts|subscriptions|you|history|library|trending|explore|"
    r"sign in|sign up|log in|login|register|search|skip navigation|skip to content|"
    r"settings|help|send feedback|report|privacy|terms|about|advertising|"
    r"business|how (?:search|youtube) works|cookies|consent|accept all|reject all|"
    r"all|images|videos|news|shopping|maps|books|flights|finance|more|tools|filters|"
    r"next|previous|back|forward|page \d+|\d+|feedback|menu|navigation|"
    r"google apps|google account|clear|clear search|voice search|search by image|"
    r"share|save|like|dislike|subscribe|subscribed|join|download|report history|"
    r"create|notifications|watch later|your videos|playlists|"
    r"copyright|contact us|developers|press|careers|blog|status)\b",
    re.I,
)

#: Below this a name is a badge, a duration or an icon label, not a result.
MIN_RESULT_CHARS = 8
#: Control types that hold "results", in the order they are most likely to.
RESULT_TYPES: Tuple[str, ...] = ("Hyperlink", "ListItem", "Text")

#: What "the first <thing>" means, as control types to look through.
KINDS: Dict[str, Tuple[str, ...]] = {
    "link": ("Hyperlink",),
    "result": ("Hyperlink", "ListItem"),
    "video": ("Hyperlink", "ListItem"),
    "item": ("ListItem", "Hyperlink", "Button"),
    "row": ("ListItem", "DataItem", "TreeItem"),
    "option": ("ListItem", "MenuItem", "Button", "RadioButton"),
    "profile": ("ListItem", "Button", "Hyperlink"),
    "account": ("ListItem", "Button", "Hyperlink"),
    "button": ("Button",),
    "tab": ("TabItem",),
    "suggestion": ("ListItem", "MenuItem"),
    "field": ("Edit", "ComboBox"),
    "thumbnail": ("Hyperlink", "ListItem"),
    "card": ("ListItem", "Button", "Group"),
}

SEARCH_URLS: Dict[str, str] = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "bing": "https://www.bing.com/search?q={}",
    "duckduckgo": "https://duckduckgo.com/?q={}",
    "github": "https://github.com/search?q={}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={}",
    "amazon": "https://www.amazon.in/s?k={}",
    "maps": "https://www.google.com/maps/search/{}",
    "images": "https://www.google.com/search?tbm=isch&q={}",
    "gemini": "https://gemini.google.com/app",
}


class PageError(RuntimeError):
    """Something Dexter should say out loud, not a traceback."""


# ───────────────────────── two routes, best first (v13.4) ─────────────────────────
#
# When a debuggable Chrome is up, Playwright over CDP is strictly more precise than
# the accessibility tree: a result is an <a href> in document order rather than a
# name that looked like a title, a field is filled by its label, and a navigation can
# be *waited* on. When it is not up, everything below falls back to UIA exactly as in
# v13.1-v13.3 - so this is an upgrade that is never a dependency.


def precise() -> bool:
    """Is the exact route usable **on the browser in play** right now?

    Two conditions, and the second was missing until v13.8. A debuggable Chrome
    answering on the port is not enough: it is a *different browser*, on its own
    profile, and if the owner is working in their own Chrome then acting exactly on
    Dexter's is worse than acting approximately on theirs. It produced exactly the
    report it deserved - "when I say to open a website I want it to open in the Chrome
    normal browser which I open, not a different one".

    So the exact route applies when Dexter's Chrome is the one being used, and the
    accessibility route carries everything else. Nothing to configure: whichever
    browser is in front decides.
    """
    try:
        import browser_cdp

        if not browser_cdp.available():
            return False
    except Exception:
        return False
    try:
        import chrome_control

        ours = chrome_control.dexter_chrome_pids()
        if not ours:
            # No debug Chrome of Dexter's is running, so there is nothing for the
            # owner's browser to be confused with: whatever answers the port is the
            # browser in play. (This is also what keeps the question out of the test
            # suite, which must not scan the process table.)
            return True
        front = chrome_control.active_chrome(include_dexter=True)
        if front is None:
            return True          # no window either way; the port is all there is
        return int(getattr(front, "pid", 0) or 0) in ours
    except Exception:
        return True


def _cdp():
    import browser_cdp

    return browser_cdp


# ───────────────────────── the tab you are in ─────────────────────────


def _window() -> dc.Win:
    win = chrome_control.active_chrome()
    if win is None:
        raise PageError("Chrome isn't open. Say \"open chrome\" (or name a site) first.")
    return win


def site_of(url: str) -> str:
    """'youtube' / 'google' / the host, from a URL."""
    try:
        host = urllib.parse.urlparse(url or "").netloc.lower()
    except Exception:
        return ""
    host = host.removeprefix("www.")
    for name in ("youtube", "google", "bing", "duckduckgo", "github", "wikipedia"):
        if host.startswith(name) or f".{name}." in f".{host}.":
            return name
    return host


def here() -> Dict[str, Any]:
    """Where we are: the window, the URL, the site, the title."""
    win = _window()
    url = ""
    try:
        url = chrome_control.active_url(win)
    except Exception:
        url = ""
    return {"window": win, "url": url, "site": site_of(url),
            "title": chrome_control.tab_title(win)}


def search_here(query: str, site: Optional[str] = None) -> Dict[str, Any]:
    """Search **in the tab you are in**, not in a new one.

    "Search a video by its name in the current tab" had no implementation at all:
    `browser_open` always opens a new tab, because `chrome.exe <url>` is the only
    command line Chrome offers and it has no "replace the current tab" form. The
    address bar does: Ctrl+L, the URL, Enter - which is what a person would do.
    """
    text = (query or "").strip().strip(" .!?,'\"")
    if not text:
        raise PageError("What should I search for?")
    where = (site or "").strip().lower().removeprefix("on ").strip()
    if not where:
        # No site named: stay on the one we are on when it has a search, else Google.
        where = here()["site"] or "google"
    template = SEARCH_URLS.get(where)
    if not template:
        # An arbitrary site: let Google scope it, which is what people mean by
        # "search X on <some site>".
        template = SEARCH_URLS["google"].replace("{}", f"site:{where}+{{}}")
    url = template.format(urllib.parse.quote_plus(text))
    if precise():
        # `page.goto` in the tab that is in front: no keystrokes, no focus stealing,
        # and it waits for the page instead of sleeping.
        info = _cdp().session.goto(url, same_tab=True)
        return {"searched": text, "site": where, "url": info.get("url", url),
                "sameTab": True, "title": info.get("title", ""), "route": "cdp"}
    info = chrome_control.open_in_current_tab(url)
    return {"searched": text, "site": where, "url": url, "sameTab": True,
            "title": info.get("title", ""), "window": info.get("window", ""),
            "route": "accessibility"}


# ───────────────────────── what is on the page ─────────────────────────


def _looks_like_a_result(label: str) -> bool:
    text = (label or "").strip()
    if len(text) < MIN_RESULT_CHARS:
        return False
    if _FURNITURE.match(text):
        return False
    # A duration, a view count, a date, a price on its own.
    if re.fullmatch(r"[\d:,.₹$€%\s]+(?:views?|ago|sec|min|minutes?|hours?|days?|weeks?|"
                    r"months?|years?|k|m|b)?", text, re.I):
        return False
    return True


def results(
    limit: int = 12,
    kind: str = "result",
    window: Optional[str] = None,
    win: Optional[dc.Win] = None,
) -> List[Dict[str, Any]]:
    """What this page is offering, in the order it offers it.

    Deliberately the accessibility tree rather than the HTML: it is what the owner
    can *see*, it needs no page scripting, and it works the same on YouTube, a
    Google results page, a profile picker and an app's list.

    `win` is for callers that already have the window; passing a *title* back in
    means resolving it again, which is both wasted work and a second chance to
    resolve to a different window.
    """
    if win is None and window is None and kind in ("result", "link", "video", "thumbnail")             and precise():
        rows = _cdp().session.results(limit=max(1, int(limit or 12)))
        return [{"title": row["title"], "kind": "link", "element": None,
                 "position": row["position"], "url": row.get("url", ""),
                 "route": "cdp"} for row in rows]
    if win is None:
        win = _window() if window is None else dc.resolve_window(window)
    types = KINDS.get((kind or "result").strip().lower(), RESULT_TYPES)
    rows = dc.find_clickables("", window=win.title, types=types, budget=2.5, total=7.0)
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows:
        if row.get("element") is None:
            continue
        label = (row.get("label") or "").strip()
        if not _looks_like_a_result(label):
            continue
        key = label.lower()[:90]
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": label, "kind": row["kind"], "element": row["element"],
                    "position": len(out) + 1})
        if len(out) >= max(1, int(limit or 12)):
            break
    return out


def _press(element) -> str:
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


def _focus(win: dc.Win) -> None:
    try:
        dc._focused_target(win.title)
    except dc.DesktopError as exc:
        raise PageError(str(exc)) from exc


def open_result(
    position: Optional[int] = None,
    title: Optional[str] = None,
    kind: str = "result",
) -> Dict[str, Any]:
    """Click the nth result, or the one whose title was said.

    On YouTube this is the video list, which `youtube_control` already knows how to
    read; everywhere else it is the page's links. Both end in a click on something
    the owner can see.
    """
    if precise() and kind in ("result", "link"):
        # Exact: the nth <a href> in document order, navigated to and waited for.
        info = (_cdp().session.open_text(title) if title
                else _cdp().session.open_nth(position or 1))
        moved = bool(info.get("changed"))
        return {
            "opened": info.get("opened", ""), "position": info.get("position", position or 1),
            "kind": "link", "how": "navigated", "title": info.get("title", ""),
            "url": info.get("url", ""), "changed": moved, "route": (
                ("opened the link called " + str(title)) if title
                else f"opened the {_ordinal(position or 1)} link on the page"
            ) + " exactly (Playwright over CDP)",
        }

    state = here()
    if state["site"] == "youtube" and kind in ("result", "video", "link", "thumbnail"):
        import youtube_control as yt

        return yt.click_on_page(title=title, index=position)

    found = results(limit=max(12, int(position or 1)), kind=kind, win=state["window"])
    if not found:
        seen = _seen_names(state["window"])
        raise PageError(
            f"I can't see any {kind}s on this page."
            + (f" I can see: {seen}" if seen else
               " Some pages publish nothing to Windows; tell me the exact text to click.")
        )
    if title:
        needle = title.strip().lower()
        ranked = sorted((row for row in found if needle in row["title"].lower()),
                        key=lambda row: (0 if row["title"].lower().startswith(needle) else 1,
                                         row["position"]))
        if not ranked:
            offered = "; ".join(row["title"][:60] for row in found[:6])
            raise PageError(f"Nothing here is called '{title}'. I can see: {offered}")
        chosen = ranked[0]
    else:
        index = max(1, int(position or 1))
        if index > len(found):
            raise PageError(f"There are only {len(found)} {kind}s I can see on this page.")
        chosen = found[index - 1]

    _focus(state["window"])
    before = state.get("title") or ""
    how = _press(chosen["element"])
    if not how:
        raise PageError(f"I found '{chosen['title'][:60]}' but it would not take a click.")
    time.sleep(0.6)
    # Did anything actually happen? "It says it is doing it but it does nothing" is
    # the complaint this whole module answers, so the claim is checked: the tab's
    # title after the click is evidence, and its absence is worth saying too.
    after = chrome_control.tab_title()
    moved = bool(after and after != before)
    return {"opened": chosen["title"], "position": chosen["position"], "kind": chosen["kind"],
            "how": how, "title": after, "wasTitle": before, "changed": moved,
            "route": (f"clicked the {_ordinal(chosen['position'])} {kind} on the page"
                      + ("" if moved else " - the page has not changed yet, so it may still be loading"))}


def click_nth(what: str = "item", position: int = 1, window: Optional[str] = None) -> Dict[str, Any]:
    """"Select the first profile" - the nth thing of a kind, in any window.

    Chrome's profile picker is the case that prompted this: its cards are list
    items with the profile's name on them, so there is nothing to *type* and no
    fixed label to click - only "the first one".
    """
    kind = (what or "item").strip().lower().rstrip("s")
    win = dc.resolve_window(window) if window else _window()
    found = results(limit=max(12, int(position or 1)), kind=kind, win=win)
    if not found:
        # Nothing in the accessibility tree. In Chrome that is not a surprise and not a
        # failure: page content is simply not published there, and the profile picker is
        # a page. So read the pixels instead (v13.8) - it is slower, and it is the
        # difference between "select the first profile" working and not.
        by_sight = _nth_by_sight(kind, int(position or 1), win)
        if by_sight is not None:
            return by_sight
        seen = _seen_names(win)
        raise PageError(
            f"I can't see any {kind}s in {win.describe()}."
            + (f" I can see: {seen}" if seen else "")
        )
    index = max(1, int(position or 1))
    if index > len(found):
        names = "; ".join(row["title"][:40] for row in found)
        raise PageError(f"There are only {len(found)} {kind}s there: {names}")
    chosen = found[index - 1]
    _focus(win)
    how = _press(chosen["element"])
    if not how:
        raise PageError(f"'{chosen['title'][:60]}' would not take a click.")
    time.sleep(0.3)
    return {"clicked": chosen["title"], "position": index, "kind": chosen["kind"],
            "how": how, "window": win.describe()}


def _nth_by_sight(kind: str, position: int, win: dc.Win) -> Optional[Dict[str, Any]]:
    """The nth thing on screen, read with OCR, when accessibility has nothing.

    Reading order - down the page, then across - because that is the order a person
    means by "the first one". Runs of text that are obviously furniture (the address
    bar, the window buttons) are skipped, for the same reason the CDP extractor skips
    Google's own navigation.
    """
    try:
        import screen_text
    except Exception:
        return None
    if not screen_text.availability()["ready"]:
        return None
    try:
        state = screen_text.read(win=win)
    except Exception as exc:
        print(f"[Page] Could not read the screen: {exc}")
        return None
    rows = [w for w in state["words"]
            if len(w.text) > 1 and not _FURNITURE.match(w.text.strip())]
    if not rows:
        return None
    rows.sort(key=lambda w: (w.top // 28, w.left))
    index = max(1, int(position or 1))
    if index > len(rows):
        return None
    chosen = rows[index - 1]
    x, y = chosen.centre
    import mouse_input

    if not mouse_input.available():
        return None
    mouse_input.move_to(x, y)
    time.sleep(0.05)
    mouse_input.click("left")
    return {"clicked": chosen.text, "position": index, "kind": kind,
            "how": f"read the screen and clicked at {x},{y}",
            "window": win.describe(), "route": "screen text",
            "seen": [w.text for w in rows[:10]]}


def _seen_names(win: dc.Win, limit: int = 12) -> str:
    try:
        rows = dc.find_clickables("", window=win.title, budget=1.5, total=4.0)
    except Exception:
        return ""
    names = [row["label"] for row in rows if row.get("label")]
    return ", ".join(dict.fromkeys(names))[:300]


def _ordinal(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(
        int(n), f"{int(n)}th")


# ───────────────────────── text between places ─────────────────────────


def copy_here(what: str = "selection", window: Optional[str] = None) -> Dict[str, Any]:
    """Put something on the clipboard and say what it was.

    'selection' (what is highlighted), 'all' (the whole field or page), or the name
    of a field whose contents should be taken.
    """
    wanted = (what or "selection").strip().lower()
    if wanted in ("", "selection", "this", "that", "it", "the selection", "highlighted",
                  "this paragraph", "that paragraph", "this text", "that text"):
        if window is None and precise():
            # The actual selection string out of the page, rather than Ctrl+C and a
            # hopeful look at the clipboard.
            text = _cdp().session.selection()
            if text.strip():
                dc._set_clipboard(text)
                return {"text": text, "length": len(text), "window": "Chrome",
                        "app": "Chrome", "selectedAll": False, "route": "cdp"}
        info = dc.copy_from(window=window, select_all=False)
    elif wanted in ("all", "everything", "the page", "page", "the whole page", "the document"):
        info = dc.copy_from(window=window, select_all=True)
    else:
        # A named field: read its value through UIA rather than moving the caret.
        win = dc.resolve_window(window) if window else _window()
        rows = [row for row in dc.find_clickables(wanted, window=win.title,
                                                  types=("Edit", "ComboBox", "Document", "Text"))
                if row.get("element") is not None]
        if not rows:
            raise PageError(f"I can't find anything called '{what}' to copy from.")
        text = ""
        for attempt in ("get_value", "window_text", "legacy_properties"):
            getter = getattr(rows[0]["element"], attempt, None)
            if not callable(getter):
                continue
            try:
                value = getter()
                text = str(value.get("Value") or value.get("Name") or "") \
                    if isinstance(value, dict) else str(value or "")
                if text:
                    break
            except Exception:
                continue
        if not text.strip():
            raise PageError(f"'{rows[0]['label']}' has no text I can read.")
        if not dc._set_clipboard(text):
            raise PageError("I couldn't write to the clipboard (pyperclip missing?).")
        info = {"text": text, "window": win.describe(), "app": win.app, "length": len(text),
                "from": rows[0]["label"]}
    return info


def paste_there(
    field: Optional[str] = None,
    tab: Optional[str] = None,
    text: Optional[str] = None,
    submit: bool = False,
) -> Dict[str, Any]:
    """Put the clipboard (or `text`) into a field, optionally in another tab.

    The order is the order a person would use: change tab, click the field, paste.
    Nothing is typed into a window that did not take focus, and nothing is typed at
    all when no field was named and whatever has focus is not a text box - a paste
    into the wrong place is worse than a refusal.
    """
    # This whole function is browser-scoped (it switches *tabs*), so whenever the
    # exact route is up it is the right one. Pasting into another application goes
    # through `type_text(window=...)`, which is the keyboard route by design.
    if precise():
        payload = text if text else dc._clipboard_text()
        if not (payload or "").strip():
            raise PageError("There is nothing on the clipboard to paste.")
        moved = ""
        if tab:
            switched = _cdp().session.switch(tab)
            moved = str(switched.get("switched") or tab)
        info = _cdp().session.fill(field, payload, submit=bool(submit))
        return {"pasted": True, "field": info.get("filled", field or "the focused field"),
                "tab": moved, "submitted": bool(submit), "window": info.get("title", ""),
                "route": "cdp"}

    moved = ""
    if tab:
        try:
            switched = chrome_control.switch_tab(tab)
            moved = str(switched.get("title") or tab)
            time.sleep(0.5)
        except Exception as exc:
            raise PageError(f"I couldn't switch to the '{tab}' tab ({exc}).") from exc

    win = _window()
    _focus(win)
    target = ""
    if field:
        wanted = field.strip()
        rows = [row for row in dc.find_clickables(wanted, window=win.title,
                                                 types=("Edit", "ComboBox", "Document", "Button"))
                if row.get("element") is not None]
        if not rows:
            seen = _seen_names(win)
            raise PageError(
                f"I can't find a '{wanted}' field on this page."
                + (f" I can see: {seen}" if seen else "")
            )
        target = rows[0]["label"]
        for attempt in ("set_focus", "click_input", "invoke"):
            action = getattr(rows[0]["element"], attempt, None)
            if callable(action):
                try:
                    action()
                    break
                except Exception:
                    continue
        time.sleep(0.2)

    if text:
        dc._write_text(str(text))
    else:
        clip = dc._clipboard_text()
        if not clip.strip():
            raise PageError("There is nothing on the clipboard to paste.")
        dc._send_keys("ctrl+v")
    if submit:
        time.sleep(0.15)
        dc._send_keys("enter")
    return {"pasted": True, "field": target or "whatever had focus", "tab": moved,
            "submitted": bool(submit), "window": win.describe()}


def availability() -> Dict[str, Any]:
    state: Dict[str, Any] = {"chrome": False, "url": "", "site": ""}
    try:
        info = here()
        state.update(chrome=True, url=info["url"], site=info["site"],
                     title=info["title"])
    except Exception as exc:
        state["why"] = str(exc)
    return state
