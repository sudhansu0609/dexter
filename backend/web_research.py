"""
Privacy-preserving web research for Dexter - the same design BuzzcafAI uses
(BuzzcafAI/backend/app/services/web_research.py, docs/LOCAL_PRIVACY_AND_SEARXNG.md).

Zero private data leaks:
- `sanitize_search_query()` strips filesystem paths, API keys, code blocks and prompt
  directives ON-DEVICE, so only generic public keywords ever leave the machine.
- Self-hosted SearXNG first (default http://localhost:8080), so a search provider
  never learns who you are; then keyless Wikipedia and DuckDuckGo as fallbacks.
- `fetch_page_content()` reads a public page and extracts clean text without running
  scripts or accepting cookies - no credentials, no tracking headers.

Configuration is by environment, matching the rest of Dexter:
    DEXTER_SEARXNG_URL       default http://localhost:8080 (also BuzzcafAI's instance)
    DEXTER_SEARCH_PROVIDER   auto (SearXNG->Wikipedia->DuckDuckGo) | searxng | wikipedia | duckduckgo
"""

from __future__ import annotations

import logging
import os
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger("dexter.web_research")

DEFAULT_TIMEOUT = 12
#: SearXNG is local; a short timeout keeps the fallback snappy when it isn't up.
SEARXNG_TIMEOUT = 5
DEFAULT_SEARXNG_URL = "http://localhost:8080"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
WIKI_USER_AGENT = "DexterLocalAssistant/1.0 (local privacy-safe research)"

try:
    from ddgs import DDGS

    _HAS_DDGS = True
except ImportError:  # pragma: no cover - depends on the install
    DDGS = None  # type: ignore
    _HAS_DDGS = False


def _config() -> Dict[str, str]:
    return {
        "search_provider": (os.getenv("DEXTER_SEARCH_PROVIDER", "") or "auto").strip().lower(),
        "searxng_url": (os.getenv("DEXTER_SEARXNG_URL", "") or DEFAULT_SEARXNG_URL).strip(),
    }


# ───────────────────────── query sanitisation ─────────────────────────


def sanitize_search_query(query: str) -> str:
    """Strip private paths, keys, code and directives so only generic keywords leave.

    The guarantee that "none of my data leaks the internet": no local paths, no
    tokens, no prompt plumbing is ever dispatched to a search engine (even a
    self-hosted one). Ported from BuzzcafAI.
    """
    if not query:
        return ""
    text = str(query).strip()
    text = re.sub(r"```[a-zA-Z]*", " ", text)
    text = re.sub(r"\[(?:INVOKE_AGENT|WEB_SEARCH|WEB_FETCH|SEARCH):\s*([^\]]*?)\]",
                  r" \1 ", text, flags=re.I)
    text = re.sub(r"\[/?(?:INVOKE_AGENT|WEB_SEARCH|WEB_FETCH|SEARCH)[^\]]*\]", " ", text, flags=re.I)
    text = re.sub(r"[{}\[\]\"']", " ", text)
    text = re.sub(r"[A-Za-z]:\\[^ \t\r\n]+", " ", text)
    text = re.sub(r"/(?:Users|home|tmp|etc|var|projects)/[^ \t\r\n]+", " ", text)
    text = re.sub(r"\b(?:sk-[a-zA-Z0-9_\-]{20,}|AIza[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9_\-]{20,})\b",
                  " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 120:
        text = text[:120].rsplit(" ", 1)[0].strip()
    return text


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


def searxng_url() -> str:
    return _config()["searxng_url"].rstrip("/")


# ───────────────────────── safe HTML text extractor ─────────────────────────


class CleanTextExtractor(HTMLParser):
    """Extract plain readable text from HTML without scripts, styles or chrome."""

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: List[str] = []
        self._ignore_stack = 0
        self._ignored_tags = {
            "script", "style", "noscript", "svg", "header",
            "footer", "nav", "aside", "form", "iframe",
        }

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag.lower() in self._ignored_tags:
            self._ignore_stack += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._ignored_tags and self._ignore_stack > 0:
            self._ignore_stack -= 1

    def handle_data(self, data: str) -> None:
        if self._ignore_stack == 0:
            cleaned = data.strip()
            if cleaned:
                self.text_parts.append(cleaned)


def fetch_page_content(url: str, max_chars: int = 3500) -> str:
    """Download a public webpage and extract clean plain-text for synthesis.

    Zero credentials, cookies or tracking headers are transmitted.
    """
    clean_url = (url or "").strip()
    if not clean_url.startswith(("http://", "https://")):
        return f"Error: Invalid URL '{clean_url}'"
    try:
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
        resp = requests.get(clean_url, headers=headers, timeout=DEFAULT_TIMEOUT)
        if resp.status_code != 200:
            return f"Error: HTTP {resp.status_code} fetching '{clean_url}'"
        parser = CleanTextExtractor()
        parser.feed(resp.text)
        cleaned = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()
        return cleaned[:max_chars] if cleaned else "No extractable text content found on the page."
    except Exception as exc:
        logger.warning("Error fetching page %s: %s", clean_url, exc)
        return f"Could not fetch {clean_url}: {exc}"


# ───────────────────────── search providers ─────────────────────────


def search_searxng(query: str, base_url: Optional[str] = None, max_results: int = 5) -> List[Dict[str, str]]:
    """Query a self-hosted SearXNG instance (JSON API) - private, keyless."""
    clean_q = sanitize_search_query(query)
    if not clean_q:
        return []
    base = (base_url or searxng_url()).rstrip("/")
    out: List[Dict[str, str]] = []
    try:
        resp = requests.get(
            f"{base}/search",
            params={"q": clean_q, "format": "json", "safesearch": 1, "language": "en"},
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
            timeout=SEARXNG_TIMEOUT,
        )
        if resp.status_code == 200:
            for r in resp.json().get("results", [])[:max_results]:
                title, link = _clean(r.get("title", "")), r.get("url", "")
                if title and link:
                    out.append({"title": title, "snippet": _clean(r.get("content", "")),
                                "url": link, "source": f"SearXNG ({r.get('engine', 'searxng')})"})
    except Exception as exc:
        logger.debug("SearXNG at %s failed: %s", base, exc)
    return out


def search_wikipedia(query: str, max_results: int = 4) -> List[Dict[str, str]]:
    """Wikipedia Opensearch + REST Summary - keyless, verified encyclopedic facts."""
    clean_q = sanitize_search_query(query)
    if not clean_q:
        return []
    out: List[Dict[str, str]] = []
    try:
        headers = {"User-Agent": WIKI_USER_AGENT}
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": clean_q, "limit": max_results,
                    "namespace": 0, "format": "json"},
            headers=headers, timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            titles = data[1] if len(data) > 1 else []
            descriptions = data[2] if len(data) > 2 else []
            urls = data[3] if len(data) > 3 else []
            for title, desc, link in zip(titles, descriptions, urls):
                snippet = desc
                if not snippet or len(snippet) < 40:
                    try:
                        s = requests.get(
                            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}",
                            headers=headers, timeout=4)
                        if s.status_code == 200:
                            snippet = s.json().get("extract", "") or snippet
                    except Exception:
                        pass
                out.append({"title": title, "snippet": snippet, "url": link, "source": "Wikipedia"})
    except Exception as exc:
        logger.warning("Wikipedia search failed for '%s': %s", clean_q, exc)
    return out


def search_duckduckgo_instant(query: str) -> List[Dict[str, str]]:
    """DuckDuckGo Instant Answer API - definitions and related topics, keyless."""
    clean_q = sanitize_search_query(query)
    if not clean_q:
        return []
    out: List[Dict[str, str]] = []
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": clean_q, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            heading = data.get("Heading") or clean_q
            if data.get("AbstractText") and data.get("AbstractURL"):
                out.append({"title": heading, "snippet": data["AbstractText"],
                            "url": data["AbstractURL"], "source": "DuckDuckGo"})
            for topic in data.get("RelatedTopics", [])[:3]:
                text, first_url = topic.get("Text"), topic.get("FirstURL")
                if text and first_url:
                    parts = text.split(" - ", 1)
                    out.append({"title": parts[0] if len(parts) > 1 else heading,
                                "snippet": parts[1] if len(parts) > 1 else text,
                                "url": first_url, "source": "DuckDuckGo"})
    except Exception as exc:
        logger.warning("DuckDuckGo instant answer failed for '%s': %s", clean_q, exc)
    return out


def search_duckduckgo_text(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Full-text DuckDuckGo results via the `ddgs` client - richer than instant
    answers for news/docs/prices, still keyless and account-free."""
    clean_q = sanitize_search_query(query)
    if not clean_q or not _HAS_DDGS:
        return []
    out: List[Dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for item in ddgs.text(clean_q, max_results=max_results):
                link = item.get("href", "")
                if link:
                    out.append({"title": _clean(item.get("title", "")),
                                "snippet": _clean(item.get("body", "")),
                                "url": link, "source": "DuckDuckGo"})
    except Exception as exc:
        logger.warning("DuckDuckGo text search failed for '%s': %s", clean_q, exc)
    return out


def search_web(query: str, max_results: int = 5, provider: Optional[str] = None,
               searxng_url_override: Optional[str] = None) -> List[Dict[str, str]]:
    """Unified private search: SearXNG -> Wikipedia -> DuckDuckGo, de-duplicated.

    Matches BuzzcafAI's `search_web`: SearXNG first (self-hosted, private), then
    Wikipedia for dense facts, then DuckDuckGo (full-text via ddgs, plus instant
    answers). `provider` forces a single source.
    """
    clean_q = sanitize_search_query(query)
    if not clean_q:
        return []

    cfg = _config()
    chosen = (provider or cfg["search_provider"] or "auto").lower()
    sx_url = searxng_url_override or cfg["searxng_url"]

    combined: List[Dict[str, str]] = []
    seen: set = set()

    def _add(rows: List[Dict[str, str]]) -> None:
        for r in rows:
            url = r.get("url", "")
            if url and url not in seen:
                seen.add(url)
                combined.append(r)

    # 1. SearXNG
    if chosen in ("searxng", "auto"):
        _add(search_searxng(clean_q, base_url=sx_url, max_results=max_results))
        if chosen == "searxng":
            return combined[:max_results]

    # 2. Wikipedia
    if chosen in ("wikipedia", "auto") or not combined:
        _add(search_wikipedia(clean_q, max_results=max_results))
        if chosen == "wikipedia":
            return combined[:max_results]

    # 3. DuckDuckGo (full text first, then instant answers)
    if chosen in ("duckduckgo", "auto") or not combined:
        _add(search_duckduckgo_text(clean_q, max_results=max_results))
        _add(search_duckduckgo_instant(clean_q))

    return combined[:max_results]


def searxng_status(base_url: Optional[str] = None) -> Dict[str, Any]:
    """Is the SearXNG instance reachable? Used by the 'test private search' tool."""
    base = (base_url or searxng_url()).rstrip("/")
    try:
        rows = search_searxng("connection test", base_url=base, max_results=1)
        return {"ok": bool(rows), "url": base,
                "detail": "answered" if rows else "reachable but returned nothing"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "url": base, "detail": str(exc).splitlines()[0][:160]}


def build_research_brief(topic: str) -> str:
    """A structured, cited research dossier for the agent to reason over."""
    clean_topic = sanitize_search_query(topic)
    if not clean_topic:
        return "No topic provided for web research."
    results = search_web(clean_topic, max_results=4)
    if not results:
        return f"No live search results found for '{clean_topic}'."
    lines = [f"### Verified Live Web Research ({clean_topic})", ""]
    for idx, r in enumerate(results, 1):
        lines.append(f"{idx}. **[{r.get('title', 'Reference')}]** ({r.get('source', 'Web')})")
        if r.get("snippet"):
            lines.append(f"   - {r['snippet'].strip()}")
        if r.get("url"):
            lines.append(f"   - Source: {r['url']}")
        lines.append("")
    return "\n".join(lines)
