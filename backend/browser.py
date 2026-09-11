"""
Dexter's browser (roadmap v6, B1).

One Chrome window that Dexter drives through Playwright: open a page or a
search, read what is on it, click things by their visible text, type into
fields, scroll, switch tabs, take a screenshot. It uses the installed Chrome
(channel "chrome") with its own profile under data/browser_profile, so logins
you make in that window persist and your everyday Chrome is left alone.

Playwright's sync API is bound to the thread that started it, so a single
worker thread owns the browser and every tool call is queued to it. When you
close the window, the next command opens a fresh one.

Set DEXTER_BROWSER_CDP=http://127.0.0.1:9222 to attach to a Chrome you started
with --remote-debugging-port instead of launching a separate window.
"""

import os
import queue
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PROFILE_DIR = os.environ.get("DEXTER_BROWSER_PROFILE") or os.path.join(DATA_DIR, "browser_profile")
SHOTS_DIR = os.path.join(DATA_DIR, "screenshots")
CDP_URL = (os.environ.get("DEXTER_BROWSER_CDP") or "").strip()
COMMAND_TIMEOUT = float(os.environ.get("DEXTER_BROWSER_TIMEOUT", "45"))
NAV_TIMEOUT_MS = 25_000

_URL_LIKE = re.compile(r"^(?:https?://|[\w-]+(?:\.[\w-]+)+(?:[/:?#].*)?$)", re.I)
# v11 P1.3: this table is now also the answer to "is 'youtube' an app or a
# site?", asked by the router *before* the app launcher gets a say. Only names
# that are web-first belong here - "spotify" and "discord" are applications on
# this machine and must keep launching.
_SITE_ALIASES = {
    "youtube": "https://www.youtube.com/",
    "youtube studio": "https://studio.youtube.com/",
    "yt studio": "https://studio.youtube.com/",
    "yt": "https://www.youtube.com/",
    "gmail": "https://mail.google.com/",
    "google mail": "https://mail.google.com/",
    "inbox": "https://mail.google.com/",
    "google calendar": "https://calendar.google.com/",
    "calendar": "https://calendar.google.com/",
    "google drive": "https://drive.google.com/",
    "drive": "https://drive.google.com/",
    "google docs": "https://docs.google.com/",
    "docs": "https://docs.google.com/",
    "google sheets": "https://sheets.google.com/",
    "sheets": "https://sheets.google.com/",
    "google maps": "https://www.google.com/maps",
    "maps": "https://www.google.com/maps",
    "google photos": "https://photos.google.com/",
    "github": "https://github.com/",
    "google": "https://www.google.com/",
    "notion": "https://www.notion.so/",
    "chatgpt": "https://chatgpt.com/",
    "claude": "https://claude.ai/",
    "gemini": "https://gemini.google.com/",
    "reddit": "https://www.reddit.com/",
    "twitter": "https://x.com/",
    "x": "https://x.com/",
    "instagram": "https://www.instagram.com/",
    "facebook": "https://www.facebook.com/",
    "linkedin": "https://www.linkedin.com/",
    "whatsapp": "https://web.whatsapp.com/",
    "netflix": "https://www.netflix.com/",
    "amazon": "https://www.amazon.in/",
    "flipkart": "https://www.flipkart.com/",
    "wikipedia": "https://en.wikipedia.org/",
    "stack overflow": "https://stackoverflow.com/",
    "stackoverflow": "https://stackoverflow.com/",
    "hacker news": "https://news.ycombinator.com/",
    "canva": "https://www.canva.com/",
    "analytics": "https://analytics.google.com/",
    "google analytics": "https://analytics.google.com/",
}

_SITE_LEAD = re.compile(r"^(?:the|my|a|an)\s+", re.I)
_SITE_TAIL = re.compile(r"\s+(?:website|site|page|web\s*site|dot\s*com|in\s+the\s+browser)$", re.I)


def known_site(text: str) -> Optional[str]:
    """The URL for a spoken site name, or None when this is not a site.

    This is the line between "open youtube" (a tab) and "open chrome" (an app),
    so it is deliberately strict: an exact alias after stripping "the/my" and a
    trailing "website", nothing fuzzy.
    """
    name = (text or "").strip().strip("\"'.,!?").lower()
    if not name:
        return None
    name = _SITE_TAIL.sub("", _SITE_LEAD.sub("", name)).strip()
    name = re.sub(r"\s+", " ", name).rstrip("/")
    if name in _SITE_ALIASES:
        return _SITE_ALIASES[name]
    # "youtube.com" and "reddit.com" name the same sites; let the URL rule own
    # them so the caller gets one answer for both phrasings.
    stem = re.sub(r"^(?:https?://)?(?:www\.)?", "", name)
    stem = re.sub(r"\.(?:com|in|org|net|io|co|ai|so|dev)$", "", stem)
    if stem != name and stem in _SITE_ALIASES:
        return _SITE_ALIASES[stem]
    return None


def looks_like_a_site(text: str) -> bool:
    """A URL or a site Dexter knows - i.e. not a thing to launch from disk."""
    raw = (text or "").strip().strip("\"'")
    if not raw:
        return False
    return bool(known_site(raw)) or bool(_URL_LIKE.match(raw))


def normalize_target(text: str) -> str:
    """A URL, a known site name, a 'youtube ...' search, or a Google search."""
    raw = (text or "").strip().strip("\"'")
    if not raw:
        return "about:blank"
    lower = raw.lower().rstrip("/")
    if lower in _SITE_ALIASES:
        return _SITE_ALIASES[lower]
    # A typed-out domain is taken literally - "github.com" must stay
    # github.com and not be rewritten to the alias table's canonical URL.
    if _URL_LIKE.match(raw):
        return raw if raw.lower().startswith(("http://", "https://")) else f"https://{raw}"
    site = known_site(raw)
    if site:
        return site
    m = re.match(r"^(?:youtube|yt)\s+(?:for\s+)?(.+)$", raw, re.I)
    if m:
        return f"https://www.youtube.com/results?search_query={quote_plus(m.group(1).strip())}"
    return f"https://www.google.com/search?q={quote_plus(raw)}"


def _collapse(text: str, limit: int) -> str:
    text = re.sub(r"[ \t ]+", " ", text or "")
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class BrowserError(RuntimeError):
    pass


class BrowserSession:
    def __init__(self) -> None:
        self._queue: "queue.Queue[tuple[Callable[[], Any], Dict[str, Any]]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self.attached_over_cdp = False
        self.last_error: Optional[str] = None

    # ───────────────────────── worker plumbing ─────────────────────────

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="dexter-browser", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            fn, box = self._queue.get()
            try:
                box["result"] = fn()
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                box["error"] = exc
            finally:
                box["done"].set()

    def _run(self, fn: Callable[[], Any], timeout: float = COMMAND_TIMEOUT) -> Any:
        self._ensure_thread()
        box: Dict[str, Any] = {"done": threading.Event()}
        self._queue.put((fn, box))
        if not box["done"].wait(timeout):
            raise BrowserError(f"The browser did not answer within {int(timeout)}s.")
        if "error" in box:
            err = box["error"]
            self.last_error = str(err)
            raise BrowserError(_friendly(err))
        return box.get("result")

    # ───────────────────────── lifecycle (worker thread only) ─────────────────────────

    def _alive(self) -> bool:
        try:
            return self._ctx is not None and (self._browser is None or self._browser.is_connected())
        except Exception:
            return False

    def _ensure(self):
        if self._alive():
            try:
                _ = self._ctx.pages  # raises once the window was closed
                return
            except Exception:
                pass
        self._reset()
        from playwright.sync_api import sync_playwright

        if self._pw is None:
            self._pw = sync_playwright().start()
        if CDP_URL:
            try:
                self._browser = self._pw.chromium.connect_over_cdp(CDP_URL, timeout=5000)
                self._ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
                self.attached_over_cdp = True
                self._browser.on("disconnected", lambda *_: self._reset())
                return
            except Exception as exc:
                print(f"[Browser] CDP attach to {CDP_URL} failed ({exc}); launching a window instead.")
        os.makedirs(PROFILE_DIR, exist_ok=True)
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(
                PROFILE_DIR,
                channel="chrome",
                headless=False,
                args=["--window-size=1280,860", "--disable-features=TranslateUI"],
                ignore_default_args=["--enable-automation"],
                viewport=None,
            )
        except Exception as exc:
            raise BrowserError(f"Chrome could not be started: {exc}") from exc
        self.attached_over_cdp = False
        self._ctx.on("close", lambda *_: self._reset())

    def _reset(self) -> None:
        self._ctx = None
        self._browser = None
        self._page = None

    def _current_page(self):
        self._ensure()
        pages = self._ctx.pages
        if self._page is not None and not self._page.is_closed():
            return self._page
        self._page = pages[-1] if pages else self._ctx.new_page()
        return self._page

    # ───────────────────────── commands ─────────────────────────

    def open(self, target: str, new_tab: bool = False) -> Dict[str, Any]:
        url = normalize_target(target)

        def _do():
            page = self._ctx.new_page() if new_tab and self._alive() else self._current_page()
            if new_tab:
                self._page = page
            page.bring_to_front()
            page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            try:
                page.wait_for_load_state("load", timeout=4000)
            except Exception:
                pass
            return {"url": page.url, "title": page.title()}

        return self._run(_do)

    def read(self, max_chars: int = 6000) -> Dict[str, Any]:
        def _do():
            page = self._current_page()
            page.bring_to_front()
            info = page.evaluate(
                """() => {
                    const pick = (sel) => { const el = document.querySelector(sel); return el ? el.innerText : ''; };
                    const main = pick('article') || pick('main') || pick('[role=main]') || document.body.innerText || '';
                    const heads = Array.from(document.querySelectorAll('h1, h2, h3')).slice(0, 25)
                        .map(h => h.innerText.trim()).filter(Boolean);
                    const links = Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({ text: (a.innerText || a.getAttribute('aria-label') || '').trim(), href: a.href }))
                        .filter(l => l.text && l.href.startsWith('http')).slice(0, 40);
                    const fields = Array.from(document.querySelectorAll('input, textarea, select'))
                        .filter(el => el.type !== 'hidden')
                        .map(el => ({ label: el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '', type: el.type || el.tagName.toLowerCase() }))
                        .filter(f => f.label).slice(0, 20);
                    return { main, heads, links, fields, selection: String(window.getSelection() || '') };
                }"""
            )
            return {
                "url": page.url,
                "title": page.title(),
                "text": _collapse(info.get("main", ""), max_chars),
                "headings": info.get("heads", []),
                "links": info.get("links", []),
                "fields": info.get("fields", []),
            }

        return self._run(_do)

    def click(self, target: str) -> Dict[str, Any]:
        needle = (target or "").strip().strip("\"'")
        if not needle:
            raise BrowserError("What should I click?")

        def _do():
            page = self._current_page()
            page.bring_to_front()
            pattern = re.compile(re.escape(needle), re.I)
            candidates = [
                ("button", page.get_by_role("button", name=pattern)),
                ("link", page.get_by_role("link", name=pattern)),
                ("tab", page.get_by_role("tab", name=pattern)),
                ("menu item", page.get_by_role("menuitem", name=pattern)),
                ("label", page.get_by_label(pattern)),
                ("text", page.get_by_text(pattern)),
                ("placeholder", page.get_by_placeholder(pattern)),
                ("title", page.locator(f"[title*='{needle}' i], [aria-label*='{needle}' i]")),
            ]
            for kind, locator in candidates:
                try:
                    count = locator.count()
                except Exception:
                    continue
                for index in range(min(count, 6)):
                    item = locator.nth(index)
                    try:
                        if item.is_visible():
                            item.click(timeout=5000)
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=4000)
                            except Exception:
                                pass
                            return {"clicked": needle, "kind": kind, "url": page.url, "title": page.title()}
                    except Exception:
                        continue
            visible = page.evaluate(
                """() => Array.from(document.querySelectorAll('a, button, [role=button]'))
                    .map(e => (e.innerText || e.getAttribute('aria-label') || '').trim())
                    .filter(t => t && t.length < 60).slice(0, 30)"""
            )
            raise BrowserError(f"Nothing on the page matches '{needle}'. Visible options: " + ", ".join(visible[:15]))

        return self._run(_do)

    def type_text(self, text: str, into: Optional[str] = None, submit: bool = False) -> Dict[str, Any]:
        if text is None:
            raise BrowserError("What should I type?")

        def _do():
            page = self._current_page()
            page.bring_to_front()
            target_desc = into or "the focused field"
            field = None
            if into:
                pattern = re.compile(re.escape(into), re.I)
                for locator in (
                    page.get_by_label(pattern),
                    page.get_by_placeholder(pattern),
                    page.get_by_role("textbox", name=pattern),
                    page.get_by_role("searchbox", name=pattern),
                    page.get_by_role("combobox", name=pattern),
                    page.locator(f"input[name*='{into}' i], textarea[name*='{into}' i], input[id*='{into}' i]"),
                ):
                    try:
                        if locator.count() and locator.first.is_visible():
                            field = locator.first
                            break
                    except Exception:
                        continue
                if field is None:
                    raise BrowserError(f"I could not find a field called '{into}'.")
            else:
                for selector in (
                    "input[type=search]:visible", "textarea:visible",
                    "input[type=text]:visible", "[contenteditable=true]:visible",
                ):
                    try:
                        locator = page.locator(selector)
                        if locator.count():
                            field = locator.first
                            target_desc = "the first text field"
                            break
                    except Exception:
                        continue
            if field is None:
                page.keyboard.type(text)
            else:
                field.click(timeout=5000)
                try:
                    field.fill(text, timeout=5000)
                except Exception:
                    page.keyboard.type(text)
            if submit:
                page.keyboard.press("Enter")
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=6000)
                except Exception:
                    pass
            return {"typed": text, "into": target_desc, "submitted": submit, "url": page.url, "title": page.title()}

        return self._run(_do)

    def scroll(self, direction: str = "down") -> Dict[str, Any]:
        where = (direction or "down").lower()

        def _do():
            page = self._current_page()
            page.bring_to_front()
            if "top" in where:
                page.evaluate("window.scrollTo(0, 0)")
            elif "bottom" in where or "end" in where:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            else:
                delta = -700 if "up" in where else 700
                page.mouse.wheel(0, delta)
            time.sleep(0.3)
            return {"scrolled": where, "title": page.title()}

        return self._run(_do)

    def back(self) -> Dict[str, Any]:
        def _do():
            page = self._current_page()
            page.go_back(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            return {"url": page.url, "title": page.title()}

        return self._run(_do)

    def tabs(self) -> List[Dict[str, Any]]:
        def _do():
            self._ensure()
            current = self._current_page()
            out = []
            for index, page in enumerate(self._ctx.pages):
                try:
                    out.append({"index": index + 1, "title": page.title(), "url": page.url, "current": page is current})
                except Exception:
                    continue
            return out

        return self._run(_do)

    def switch_tab(self, which: str) -> Dict[str, Any]:
        needle = (which or "").strip().lower()

        def _do():
            self._ensure()
            pages = self._ctx.pages
            if not pages:
                raise BrowserError("There are no tabs open.")
            current = self._current_page()
            idx = pages.index(current) if current in pages else 0
            if needle in ("next", "right"):
                target = pages[(idx + 1) % len(pages)]
            elif needle in ("previous", "prev", "left", "last"):
                target = pages[(idx - 1) % len(pages)]
            elif needle.isdigit() and 1 <= int(needle) <= len(pages):
                target = pages[int(needle) - 1]
            else:
                target = next((p for p in pages if needle and (needle in (p.title() or "").lower() or needle in p.url.lower())), None)
                if target is None:
                    raise BrowserError(f"No tab matches '{which}'.")
            self._page = target
            target.bring_to_front()
            return {"title": target.title(), "url": target.url, "index": pages.index(target) + 1}

        return self._run(_do)

    def close_tab(self) -> Dict[str, Any]:
        def _do():
            self._ensure()
            page = self._current_page()
            title = page.title()
            page.close()
            self._page = None
            remaining = len(self._ctx.pages) if self._alive() else 0
            return {"closed": title, "remaining": remaining}

        return self._run(_do)

    def screenshot(self, full_page: bool = False) -> Dict[str, Any]:
        def _do():
            page = self._current_page()
            page.bring_to_front()
            os.makedirs(SHOTS_DIR, exist_ok=True)
            path = os.path.join(SHOTS_DIR, f"browser-{time.strftime('%Y%m%d-%H%M%S')}.png")
            page.screenshot(path=path, full_page=full_page)
            return {"path": path, "title": page.title(), "url": page.url}

        return self._run(_do)

    def close(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            return

        def _do():
            try:
                if self._ctx is not None:
                    self._ctx.close()
            except Exception:
                pass
            self._reset()
            return True

        try:
            self._run(_do, timeout=10)
        except Exception:
            pass


def _friendly(err: Exception) -> str:
    text = str(err)
    if isinstance(err, BrowserError):
        return text
    if "Timeout" in text or "timeout" in text:
        return "The page did not respond in time."
    if "net::ERR" in text:
        code = re.search(r"net::(ERR_[A-Z_]+)", text)
        return f"The page could not be loaded ({code.group(1) if code else 'network error'})."
    if "Target page, context or browser has been closed" in text or "has been closed" in text:
        return "The browser window was closed; say the command again to open a new one."
    if "Executable doesn't exist" in text or "channel" in text and "not found" in text:
        return "Chrome was not found. Install Google Chrome or set DEXTER_BROWSER_CDP."
    return text.splitlines()[0][:200]


browser = BrowserSession()
