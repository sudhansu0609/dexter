"""
Exact control of the owner's own Chrome, over CDP (v13.4).

Asked as: "can Playwright be implemented to make Dexter's actions more precise?"

Playwright was already here (`browser.py`, v6) and was *demoted* in v11 for a good
reason: it launched a **second** Chrome on its own profile, so "open YouTube" opened
a browser that knew none of the owner's logins and "close this tab" acted in a
window they were not looking at. Since then everything goes through Windows
accessibility (`chrome_control`, `page_control`), which drives the real browser but
can only see *names*: clicking "the third result" means reading the accessibility
tree, filtering out the site's furniture by heuristics, and hoping.

This module is the third option, and it is strictly better than both when it is
available: **Playwright attached over CDP to the Chrome the owner is actually
using.** Then a result is an `<a href>` in document order rather than a name that
looked like a title, a field is filled by its label rather than by typing at
whatever has focus, "copy that paragraph" is the real selection string, and a click
can be *waited on* instead of followed by a sleep and a hopeful title check.

The catch, stated plainly because it decides whether this is on:

* Chrome 136 and later **refuse** `--remote-debugging-port` on the default profile
  directory. So a debuggable Chrome is a *separate* `--user-data-dir`. Dexter keeps
  one at `data/chrome_debug_profile`: sign in there once and it is thereafter a
  fully logged-in Chrome that Dexter can drive exactly.
* A debug port is a local privilege: anything running as the owner can drive that
  browser. It is bound to 127.0.0.1, and it is off until asked for.

Nothing here is required. `available()` is False until a debuggable Chrome is up,
and every caller falls back to the accessibility route, which is what v13.1-v13.3
already do.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PORT = int(os.environ.get("DEXTER_CHROME_DEBUG_PORT", "9222") or 9222)
PROFILE_DIR = os.environ.get("DEXTER_CHROME_DEBUG_PROFILE") or os.path.join(
    DATA_DIR, "chrome_debug_profile"
)
#: On by default *when a debuggable Chrome is there*; this only turns the whole
#: route off, for anyone who would rather Dexter never touched CDP.
ENABLED = (os.environ.get("DEXTER_CHROME_CDP", "1") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)
COMMAND_TIMEOUT = float(os.environ.get("DEXTER_CDP_TIMEOUT", "20") or 20)
LAUNCH_WAIT = float(os.environ.get("DEXTER_CDP_LAUNCH_WAIT", "15") or 15)


class CdpError(RuntimeError):
    """Something Dexter should say out loud."""


def endpoint() -> str:
    return f"http://127.0.0.1:{PORT}"


def version(timeout: float = 0.5) -> Optional[Dict[str, Any]]:
    """What is listening on the debug port, or None."""
    if not ENABLED:
        return None
    try:
        with urllib.request.urlopen(f"{endpoint()}/json/version", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return None


def available(timeout: float = 0.5) -> bool:
    return version(timeout) is not None


#: Off until the owner explicitly turns it on (`precise_browser` "start"). A
#: debuggable Chrome merely being *up* is not consent to drive YouTube/browser
#: actions through it - that was the hijack: Dexter would read/act in a debug
#: Chrome the owner was not even looking at. `active_mode()` is the one gate
#: every YouTube/browser caller should use instead of `available()`.
_ACTIVE_MODE = False


def active_mode() -> bool:
    return _ACTIVE_MODE and available()


def set_mode(on: bool) -> None:
    global _ACTIVE_MODE
    _ACTIVE_MODE = bool(on)


# ───────────────────────── starting a debuggable Chrome ─────────────────────────


def launch(wait: float = LAUNCH_WAIT) -> Dict[str, Any]:
    """Start Chrome with the debug port on Dexter's own profile directory.

    Deliberately a *separate* profile: Chrome refuses remote debugging on the
    default one, and silently running the owner's main profile under a debug port
    would be a surprise worth avoiding anyway. The first launch is a fresh Chrome -
    signing into Google there once makes it theirs, and it persists.
    """
    if not ENABLED:
        raise CdpError("The CDP route is switched off (DEXTER_CHROME_CDP=0).")
    if available():
        return {"ok": True, "already": True, **(version() or {}), "endpoint": endpoint()}

    import chrome_control

    exe = chrome_control.find_chrome()
    if not exe:
        raise CdpError(
            "I couldn't find chrome.exe. Install Chrome, or set DEXTER_CHROME_EXE."
        )
    os.makedirs(PROFILE_DIR, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=TranslateUI",
        "--restore-last-session",
    ]
    try:
        subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         close_fds=True)
    except Exception as exc:
        raise CdpError(f"Chrome would not start for debugging: {exc}") from exc

    deadline = time.time() + max(2.0, wait)
    while time.time() < deadline:
        info = version(0.4)
        if info:
            return {"ok": True, "already": False, **info, "endpoint": endpoint(),
                    "profile": PROFILE_DIR}
        time.sleep(0.4)
    raise CdpError(
        f"Chrome started but nothing answered on {endpoint()} within {int(wait)}s. "
        "Another Chrome may be holding that profile - close it and try again."
    )


# ───────────────────────── the session ─────────────────────────
#
# Playwright's sync API is bound to the thread that created it, so one worker
# thread owns the connection and every call is queued to it - the same shape
# `browser.py` uses, for the same reason.


class Session:
    def __init__(self) -> None:
        self._queue: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._pw = None
        self._browser = None
        self._ctx = None
        self.last_error = ""

    # ── plumbing ──

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="dexter-cdp",
                                                daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            fn, box = self._queue.get()
            try:
                box["result"] = fn()
            except Exception as exc:  # noqa: BLE001 - handed back to the caller
                box["error"] = exc
            finally:
                box["done"].set()

    def _run(self, fn: Callable[[], Any], timeout: float = COMMAND_TIMEOUT) -> Any:
        self._ensure_thread()
        box: Dict[str, Any] = {"done": threading.Event()}
        self._queue.put((fn, box))
        if not box["done"].wait(timeout):
            raise CdpError(f"Chrome did not answer within {int(timeout)}s.")
        if "error" in box:
            self.last_error = str(box["error"])
            raise CdpError(_friendly(box["error"]))
        return box.get("result")

    # ── connection (worker thread only) ──

    def _connected(self) -> bool:
        try:
            return self._browser is not None and self._browser.is_connected()
        except Exception:
            return False

    def _attach(self):
        if self._connected():
            return self._ctx
        self._browser = self._ctx = None
        from playwright.sync_api import sync_playwright

        if self._pw is None:
            self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(endpoint(), timeout=5000)
        self._ctx = (self._browser.contexts[0] if self._browser.contexts
                     else self._browser.new_context())
        return self._ctx

    def _page(self):
        ctx = self._attach()
        pages = [p for p in ctx.pages if not p.is_closed()]
        if not pages:
            return ctx.new_page()
        # The tab the owner is looking at: Chrome reports the foreground tab first
        # in CDP's target list only some of the time, so the one that is *visible*
        # is asked for explicitly.
        for page in pages:
            try:
                if page.evaluate("document.visibilityState") == "visible":
                    return page
            except Exception:
                continue
        return pages[-1]

    def close(self) -> None:
        def _do():
            try:
                if self._browser is not None:
                    self._browser.close()  # detaches; does not close their Chrome
            except Exception:
                pass
            self._browser = self._ctx = None
            return True

        try:
            self._run(_do, timeout=5)
        except Exception:
            self._browser = self._ctx = None

    # ── reading ──

    def where(self) -> Dict[str, Any]:
        def _do():
            page = self._page()
            return {"url": page.url, "title": page.title()}

        return self._run(_do)

    def tabs(self) -> List[Dict[str, Any]]:
        def _do():
            ctx = self._attach()
            out = []
            for index, page in enumerate([p for p in ctx.pages if not p.is_closed()], 1):
                try:
                    out.append({"position": index, "title": page.title(), "url": page.url})
                except Exception:
                    continue
            return out

        return self._run(_do)

    def read(self, max_chars: int = 6000) -> Dict[str, Any]:
        def _do():
            page = self._page()
            text = page.evaluate("document.body ? document.body.innerText : ''") or ""
            return {"url": page.url, "title": page.title(),
                    "text": text[: max(200, int(max_chars))],
                    "truncated": len(text) > max_chars}

        return self._run(_do)

    def selection(self) -> str:
        """The text the owner has actually highlighted - the real string, not a
        clipboard round-trip."""
        def _do():
            return self._page().evaluate("window.getSelection().toString()") or ""

        return self._run(_do)

    #: What counts as a *result* on a page, in three tiers of generality.
    #:
    #: The first cut of this was a plain `a[href]` scan, and on a Google results page
    #: it returned "Accessibility help", "Google apps", "AI Mode", "Shopping" - the
    #: site's own furniture, which comes first in document order. Precision is the
    #: whole point of this route, so:
    #:
    #:   1. a per-site selector where the site has a stable one (the search engines
    #:      and YouTube are where "open the third result" is actually said);
    #:   2. otherwise links inside the main content region, skipping nav/header/
    #:      footer/aside, preferring the ones that carry a heading;
    #:   3. otherwise the loose scan, so an unknown page still answers.
    #:
    #: Each tier is tried in order and the first that finds anything wins.
    _RESULTS_JS = """
    (limit) => {
      const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
      const nav = new RegExp('^(home|shorts|sign in|sign up|settings|images|videos|news'
        + '|maps|more|tools|filters|next|previous|privacy|terms|about|help|feedback'
        + '|accessibility help|google apps|ai mode|shopping|short videos|forums'
        + '|now playing|mix|live|new|watch later|watched'
        + '|share|save|like|dislike|subscribe|sign out|all)$', 'i');
      const junkHost = /(support|accounts|policies|myactivity|adssettings)\\.google\\./i;

      const visible = (el) => {
        const rect = el.getBoundingClientRect();
        if (rect.width < 8 || rect.height < 8) return false;
        const style = window.getComputedStyle(el);
        return style.visibility !== 'hidden' && style.display !== 'none'
          && Number(style.opacity || '1') > 0.05;
      };
      const inFurniture = (el) =>
        !!el.closest('nav, header, footer, aside, [role=navigation], [role=banner], '
          + '[role=contentinfo], [role=complementary], #searchform, #appbar, #hdtb');
      const textOf = (a) =>
        clean(a.innerText) || clean(a.getAttribute('aria-label')) || clean(a.title);

      const collect = (nodes, {needHeading = false, minChars = 8} = {}) => {
        const seen = new Set();
        const out = [];
        for (const a of nodes) {
          if (!a || !a.href) continue;
          if (!/^https?:/i.test(a.href)) continue;
          if (junkHost.test(a.href)) continue;
          if (!visible(a) || inFurniture(a)) continue;
          let text = textOf(a);
          // A thumbnail's own text is a duration and a badge ("1:01:14 Now
          // playing"), not a title: strip a leading timestamp and judge again.
          text = text.replace(/^\\d{1,2}(:\\d{2}){1,2}\\s+/, '').trim();
          if (text.length < minChars || nav.test(text)) continue;
          if (needHeading && !a.querySelector('h1,h2,h3,h4') && text.length < 20) continue;
          const key = text.slice(0, 90).toLowerCase();
          if (seen.has(key)) continue;
          seen.add(key);
          out.push({title: text.slice(0, 200), url: a.href});
          if (out.length >= limit) break;
        }
        return out;
      };

      // 1. the sites where "the third result" is actually said
      const host = location.host;
      const SITES = [
        [/youtube\\./i, 'a#video-title, a#video-title-link, '
          + 'a.yt-lockup-metadata-view-model__title, ytd-video-renderer h3 a'],
        [/(^|\\.)google\\./i, '#rso a:has(h3), #search a:has(h3), #center_col a:has(h3)'],
        [/bing\\./i, '#b_results h2 a'],
        [/duckduckgo\\./i, 'a[data-testid=result-title-a]'],
        [/github\\./i, '[data-testid=results-list] a, .search-title a'],
        [/reddit\\./i, 'a[slot=full-post-link], a[data-testid=post-title]'],
        [/stackoverflow\\./i, '.s-post-summary--content-title a'],
      ];
      for (const [pattern, selector] of SITES) {
        if (!pattern.test(host)) continue;
        let nodes = [];
        try { nodes = Array.from(document.querySelectorAll(selector)); } catch (e) { nodes = []; }
        const found = collect(nodes, {minChars: 4});
        if (found.length) return found;
      }

      // 2. the main content region, headings preferred
      const root = document.querySelector('#rso, #search, main, [role=main], #content')
        || document.body;
      const inMain = collect(Array.from(root.querySelectorAll('a[href]')),
                             {needHeading: true});
      if (inMain.length) return inMain;

      // 3. anything that looks like a link to somewhere
      return collect(Array.from(document.querySelectorAll('a[href]')), {minChars: 10});
    }
    """

    def results(self, limit: int = 12) -> List[Dict[str, Any]]:
        def _do():
            rows = self._page().evaluate(self._RESULTS_JS, max(1, int(limit))) or []
            return [{**row, "position": i} for i, row in enumerate(rows, 1)]

        return self._run(_do)

    # ── acting ──

    def goto(self, url: str, same_tab: bool = True) -> Dict[str, Any]:
        def _do():
            page = self._page() if same_tab else self._attach().new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            return {"url": page.url, "title": page.title(), "sameTab": same_tab}

        return self._run(_do)

    @staticmethod
    def _settled_title(page, tries: int = 3) -> str:
        """A title read the instant a page commits is often still empty; the reply
        quotes it, so it is worth three short looks before giving up on it."""
        for _ in range(max(1, tries)):
            try:
                title = (page.title() or "").strip()
            except Exception:
                return ""
            if title:
                return title
            try:
                page.wait_for_timeout(300)
            except Exception:
                break
        return ""

    def open_nth(self, position: int = 1, limit: int = 20) -> Dict[str, Any]:
        """Open the nth result by *navigating to its href*.

        More reliable than clicking it: a click can be intercepted by an overlay, and
        a result that opens in a new tab leaves the owner looking at the old one.
        """
        def _do():
            page = self._page()
            rows = page.evaluate(self._RESULTS_JS, max(1, int(limit))) or []
            index = max(1, int(position or 1))
            if index > len(rows):
                raise CdpError(f"There are only {len(rows)} results on this page.")
            chosen = rows[index - 1]
            before = page.url
            page.goto(chosen["url"], wait_until="domcontentloaded", timeout=25_000)
            return {"opened": chosen["title"], "url": page.url, "position": index,
                    "was": before, "changed": page.url != before,
                    "title": self._settled_title(page)}

        return self._run(_do)

    def open_text(self, text: str, limit: int = 40) -> Dict[str, Any]:
        def _do():
            page = self._page()
            rows = page.evaluate(self._RESULTS_JS, max(1, int(limit))) or []
            needle = (text or "").strip().lower()
            ranked = sorted(
                (row for row in rows if needle in row["title"].lower()),
                key=lambda row: 0 if row["title"].lower().startswith(needle) else 1,
            )
            if not ranked:
                offered = "; ".join(row["title"][:60] for row in rows[:6])
                raise CdpError(f"Nothing on this page is called '{text}'."
                               + (f" I can see: {offered}" if offered else ""))
            chosen = ranked[0]
            before = page.url
            page.goto(chosen["url"], wait_until="domcontentloaded", timeout=25_000)
            return {"opened": chosen["title"], "url": page.url, "was": before,
                    "changed": page.url != before, "title": self._settled_title(page)}

        return self._run(_do)

    def click_text(self, text: str) -> Dict[str, Any]:
        """Click something by its visible text, the way a person would point at it."""
        def _do():
            page = self._page()
            needle = (text or "").strip()
            for build in (
                lambda: page.get_by_role("button", name=needle, exact=False).first,
                lambda: page.get_by_role("link", name=needle, exact=False).first,
                lambda: page.get_by_label(needle).first,
                lambda: page.get_by_text(needle, exact=False).first,
            ):
                try:
                    locator = build()
                    locator.wait_for(state="visible", timeout=1500)
                    locator.scroll_into_view_if_needed(timeout=1500)
                    locator.click(timeout=3000)
                    page.wait_for_timeout(250)
                    return {"clicked": needle, "url": page.url, "title": page.title()}
                except Exception:
                    continue
            raise CdpError(f"I couldn't find anything clickable called '{needle}' on this page.")

        return self._run(_do)

    def fill(self, field: Optional[str], text: str, submit: bool = False) -> Dict[str, Any]:
        """Type into the field named - by label, placeholder, aria-label or role.

        With no name, the page's focused element; with no focus, its search box. A
        field that cannot be found is an error rather than a guess: text typed into
        the wrong box is worse than text not typed.
        """
        def _do():
            page = self._page()
            name = (field or "").strip()
            candidates = []
            if name:
                candidates = [
                    lambda: page.get_by_label(name).first,
                    lambda: page.get_by_placeholder(name).first,
                    lambda: page.get_by_role("textbox", name=name, exact=False).first,
                    lambda: page.get_by_role("combobox", name=name, exact=False).first,
                    lambda: page.locator(f"[aria-label*='{name}' i]").first,
                ]
            else:
                candidates = [
                    lambda: page.locator(":focus").first,
                    lambda: page.get_by_role("searchbox").first,
                    lambda: page.get_by_role("textbox").first,
                    lambda: page.locator("textarea, input[type=text], input[type=search], "
                                         "[contenteditable=true]").first,
                ]
            for build in candidates:
                try:
                    locator = build()
                    locator.wait_for(state="visible", timeout=1500)
                    locator.click(timeout=2000)
                    # `fill` is the exact one and works on inputs and textareas; a
                    # contenteditable (Gemini's prompt, Notion, Docs) needs keys.
                    try:
                        locator.fill(text, timeout=2000)
                    except Exception:
                        for typer in ("press_sequentially", "type"):
                            method = getattr(locator, typer, None)
                            if callable(method):
                                method(text, delay=8)
                                break
                    if submit:
                        locator.press("Enter")
                        page.wait_for_timeout(400)
                    return {"filled": name or "the focused field", "typed": text,
                            "submitted": bool(submit), "url": page.url,
                            "title": page.title()}
                except Exception:
                    continue
            raise CdpError(
                f"I couldn't find a field called '{name}' on this page."
                if name else "Nothing on this page looked like a text field."
            )

        return self._run(_do)

    def switch(self, which: str) -> Dict[str, Any]:
        def _do():
            ctx = self._attach()
            pages = [p for p in ctx.pages if not p.is_closed()]
            wanted = (which or "").strip().lower()
            for page in pages:
                try:
                    if wanted in (page.title() or "").lower() or wanted in (page.url or "").lower():
                        page.bring_to_front()
                        return {"switched": page.title(), "url": page.url}
                except Exception:
                    continue
            titles = "; ".join((p.title() or p.url)[:40] for p in pages)
            raise CdpError(f"No tab matching '{which}'." + (f" Open tabs: {titles}" if titles else ""))

        return self._run(_do)


def _friendly(exc: Exception) -> str:
    text = str(exc).splitlines()[0][:200]
    if "ECONNREFUSED" in text or "connect_over_cdp" in text:
        return ("The debuggable Chrome isn't running. Say \"turn on precise browser "
                "control\" and I'll start it.")
    return text


#: One session per process: one browser, one worker thread.
session = Session()


def status() -> Dict[str, Any]:
    info = version()
    state: Dict[str, Any] = {
        "enabled": ENABLED,
        "endpoint": endpoint(),
        "available": info is not None,
        "browser": (info or {}).get("Browser", ""),
        "profile": PROFILE_DIR,
        "error": session.last_error,
    }
    if info is not None:
        try:
            state["tabs"] = len(session.tabs())
            state.update(session.where())
        except Exception as exc:
            state["error"] = str(exc)
    return state
