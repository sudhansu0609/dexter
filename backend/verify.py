"""
Checking, rather than assuming (v13.8).

"Dexter is not able to check if Chrome is open or not, neither can it confirm if a
tab or a website is opened or not, it won't be able to verify if a video is present
with a certain title in an opened YouTube."

Every piece needed for this already existed - `desktop_control` can enumerate windows
and processes, `browser_cdp` can list a real Chrome's tabs with their URLs,
`page_control.results()` can read the things on a page. What was missing is that
**nothing answered a question.** There were tools that *listed* (`browser_tabs`,
`list_windows`, `page_results`) and tools that *acted* (`open_result`,
`youtube_play`), and in between a gap the model had to cross by inference - which is
exactly where a confident wrong answer comes from. "Is Chrome open?" became "here are
some windows", and the model decided.

So these functions answer yes or no, and each one carries **how it knows**:

    verdict  True / False / None  - None means "I cannot tell from here", which is a
                                    real answer and much better than a guess
    how      "cdp" / "windows" / "processes" / "accessibility" - the route that
                                    produced it, so a vague answer can be improved by
                                    turning the exact route on
    evidence the windows, tabs or titles it actually saw

The exact route (Playwright over CDP) is used when it is up, because only it can see
*every* tab's URL; Windows can only show the front tab's title, which is the honest
limit and is said out loud rather than papered over.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import desktop_control as dc
import page_control as page

#: Sites people name, and how to recognise them in a URL. Only the ones worth
#: spelling out - everything else is matched on the words the owner used.
SITE_HOSTS: Dict[str, tuple] = {
    "youtube": ("youtube.com", "youtu.be"),
    "google": ("google.com/search", "www.google.com"),
    "gmail": ("mail.google.com",),
    "gemini": ("gemini.google.com",),
    "chatgpt": ("chat.openai.com", "chatgpt.com"),
    "github": ("github.com",),
    "reddit": ("reddit.com",),
    "drive": ("drive.google.com",),
    "calendar": ("calendar.google.com",),
    "whatsapp": ("web.whatsapp.com",),
    "notion": ("notion.so",),
    "studio": ("studio.youtube.com",),
}

#: Browsers, so "is my browser open" is answerable without naming one.
BROWSERS = ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi")

#: `desktop_control.find_processes` grades its matches: 100 an exact image name, 70 the
#: file description, 60 a near image name, **50 the name appearing anywhere in a
#: command line**. That last one is right for "stop the thing I named" and wrong for
#: answering a question, so a verdict needs better than it.
MIN_PROCESS_SCORE = 60


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _words(text: str) -> List[str]:
    return [w for w in re.split(r"[^a-z0-9]+", _norm(text)) if len(w) > 2]


def looks_like(haystack: str, needle: str) -> bool:
    """Does `haystack` plausibly contain what somebody meant by `needle`?

    Whole phrase first, then every significant word - "the lofi hip hop one" should
    match "lofi hip hop radio 📚 beats to relax/study to", and a title people
    half-remember should not need to be typed exactly.
    """
    hay, need = _norm(haystack), _norm(needle)
    if not need:
        return False
    if need in hay:
        return True
    words = _words(need)
    return bool(words) and all(word in hay for word in words)


# ───────────────────────── is an application open ─────────────────────────


def app_open(name: str) -> Dict[str, Any]:
    """Is this application running, and where can it be seen?

    Windows first, because a window is what "open" means to a person; then the
    process table, which catches an app that is running without a visible window
    (minimised to the tray, or still starting).
    """
    wanted = _norm(name)
    aliases = [wanted] + list(BROWSERS if wanted in ("browser", "a browser", "my browser")
                              else ())
    windows: List[Dict[str, Any]] = []
    try:
        for alias in aliases:
            for win in dc.find_windows(alias):
                row = {"title": win.title, "process": win.process, "pid": win.pid}
                if row not in windows:
                    windows.append(row)
    except Exception:
        pass
    if windows:
        return {"verdict": True, "how": "windows", "name": name,
                "windows": windows, "processes": []}

    processes: List[Dict[str, Any]] = []
    try:
        for alias in aliases:
            for proc in dc.find_processes(alias):
                # Only evidence worth saying "yes" on. `find_processes` also matches
                # the app's name anywhere in a process's **command line** (score 50),
                # which is right for "stop this thing" and far too loose for a
                # question: asking "is notepad open?" from a shell whose own command
                # line contained the word "notepad" answered yes, three times over.
                # For a verdict, the name has to be the program, not a mention of it.
                if int(proc.get("score") or 0) < MIN_PROCESS_SCORE:
                    continue
                row = {"process": proc.get("process") or proc.get("name"),
                       "pid": proc.get("pid"), "why": proc.get("why")}
                if row not in processes:
                    processes.append(row)
    except Exception:
        pass
    if processes:
        # Running but nothing on screen: that is a different answer from both "open"
        # and "not running", and saying which is the whole point of this module.
        return {"verdict": True, "how": "processes", "name": name,
                "windows": [], "processes": processes}
    return {"verdict": False, "how": "windows", "name": name,
            "windows": [], "processes": []}


# ───────────────────────── is a site open in a tab ─────────────────────────


def _hosts_for(site: str) -> tuple:
    """The host fragments that would prove this site is open.

    A name the owner typed as a **host** is used as the host: "mail.google.com" used
    to fall through to the `google` entry (because "google" is in it) and then fail to
    match its own tab, which is a uniquely annoying way to be wrong.
    """
    key = _norm(site).replace("www.", "").split("/")[0]
    if not key:
        return ()
    if "." in key:
        return (key,)
    for name, hosts in SITE_HOSTS.items():
        if name == key:
            return hosts
    for name, hosts in SITE_HOSTS.items():
        if name in key.split() or key in name:
            return hosts
    return (key,)


def site_open(site: str) -> Dict[str, Any]:
    """Is this site open in a tab? Exactly, when the exact route is available."""
    hosts = _hosts_for(site)
    if page.precise():
        try:
            session = page._cdp().session
            tabs = session.tabs()
            matches = [
                tab for tab in tabs
                if any(host in _norm(tab.get("url", "")) for host in hosts)
                or looks_like(tab.get("title", ""), site)
            ]
            return {"verdict": bool(matches), "how": "cdp", "site": site,
                    "tabs": matches, "checked": len(tabs),
                    "all": [{"position": t.get("position"), "title": t.get("title"),
                             "url": t.get("url")} for t in tabs]}
        except Exception:
            pass

    # Without it, Windows shows only the **front** tab's title per browser window -
    # so a "no" here is genuinely "I could not see it", not "it is not there". That
    # distinction is the difference between an answer and a guess.
    seen: List[Dict[str, Any]] = []
    try:
        for browser in BROWSERS:
            for win in dc.find_windows(browser):
                seen.append({"title": win.title, "process": win.process})
    except Exception:
        pass
    for row in seen:
        if looks_like(row["title"], site) or any(host.split(".")[0] in _norm(row["title"])
                                                for host in hosts):
            return {"verdict": True, "how": "windows", "site": site,
                    "tabs": [{"title": row["title"], "url": ""}], "checked": len(seen),
                    "all": seen}
    return {"verdict": None if seen else False, "how": "windows", "site": site,
            "tabs": [], "checked": len(seen), "all": seen,
            "why": ("Windows only shows the front tab of each browser window; the "
                    "other tabs are invisible without exact browser control")
            if seen else "no browser window is open"}


# ───────────────────────── is it on the page in front ─────────────────────────


def on_page(text: str, kind: str = "", limit: int = 40) -> Dict[str, Any]:
    """Is there a video, link or result on this page whose title matches?

    Returns the position as well as the verdict, so "yes, the third one" can be
    followed by "open it" without looking twice.
    """
    try:
        rows = page.results(limit=limit, kind=kind) if kind else page.results(limit=limit)
    except TypeError:
        rows = page.results(limit=limit)
    except Exception as exc:
        return {"verdict": None, "how": "none", "text": text, "matches": [],
                "checked": 0, "why": str(exc)}

    if not rows:
        # Nothing published to accessibility and no debug port: look at the pixels
        # (v13.8). Chrome keeps page content out of the accessibility tree entirely, so
        # for "is there a video called X on this page" this is the only honest route
        # left - and it is a real answer rather than a shrug.
        seen = _on_screen(text)
        if seen is not None:
            return seen
    matches = [row for row in rows if looks_like(row.get("title", ""), text)]
    how = (rows[0].get("route") if rows and rows[0].get("route") else
           ("cdp" if page.precise() else "accessibility"))
    return {"verdict": bool(matches), "how": how, "text": text,
            "matches": [{"position": row.get("position"), "title": row.get("title"),
                         "url": row.get("url", "")} for row in matches[:5]],
            "checked": len(rows),
            "titles": [row.get("title", "") for row in rows[:12]]}


def _on_screen(text: str) -> Optional[Dict[str, Any]]:
    """Is it *visible*? The last route, and the only one Chrome cannot hide from."""
    try:
        import screen_text
    except Exception:
        return None
    if not screen_text.availability()["ready"]:
        return None
    try:
        state = screen_text.read()
        hit = screen_text.find(text, state=state)
    except Exception:
        return None
    return {
        "verdict": bool(hit["found"]), "how": "screen",
        "text": text,
        "matches": ([{"position": None, "title": hit["match"]["text"],
                      "url": "", "at": hit["match"]["centre"]}] if hit["found"] else []),
        "checked": hit["count"],
        "titles": hit.get("seen", [])[:12],
        "why": "" if hit["found"] else "read the screen; nothing matched",
    }


def where() -> Dict[str, Any]:
    """What page is in front, as exactly as can be managed."""
    if page.precise():
        try:
            spot = page._cdp().session.where()
            return {"how": "cdp", "title": spot.get("title", ""),
                    "url": spot.get("url", ""), "site": page.site_of(spot.get("url", ""))}
        except Exception:
            pass
    try:
        here = page.here()
        return {"how": "accessibility", "title": here.get("title", ""),
                "url": here.get("url", ""), "site": here.get("site", "")}
    except Exception as exc:
        return {"how": "none", "title": "", "url": "", "site": "", "why": str(exc)}
