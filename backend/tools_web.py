"""
Web search, page fetch and weather - the assistant's window onto the world.

Search and fetching are delegated to `web_research` (the same privacy-first design
BuzzcafAI uses): every query is sanitised on-device, sent to a self-hosted SearXNG
first (default http://localhost:8080), and falls back to Wikipedia and DuckDuckGo.
See web_research.py and searxng/README.md.

Weather stays here: keyless wttr.in, geolocated by IP when no place is given.
Results carry their sources so the agent can cite them; spoken summaries stay short.
"""

import re
from typing import Any, Dict, List, Optional

import requests

import web_research
from tool_registry import ToolResult, tool

_TIMEOUT = 12
_USER_AGENT = "Dexter/2.0 (local assistant)"


def _clean(text: str) -> str:
    """Weather strings arrive with markup and runs of whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


@tool(
    name="web_search",
    description=(
        "Search the web for current information the local model cannot know - "
        "news, facts, prices, documentation, anything after its training cutoff. "
        "Private: the query is scrubbed on-device and sent to your self-hosted "
        "SearXNG first, then Wikipedia/DuckDuckGo. Use this instead of guessing."
    ),
    params={
        "query": {"type": "string", "description": "What to search for"},
        "count": {"type": "integer", "description": "How many results (1-8)", "required": False},
    },
    examples=[
        "search the web for the new react release notes",
        "look up what happened with the exchange rate today",
        "google how to fix a stuck windows update",
        "what's the latest news on ai regulation",
        "find out who won the match last night",
        "search for the best microphone for voiceover",
    ],
    category="web",
)
def web_search(query: str, count: int = 4) -> ToolResult:
    raw = (query or "").strip()
    if not raw:
        return ToolResult.fail("What should I search for?")

    # Scrub before anything leaves the machine - the privacy guarantee.
    clean_q = web_research.sanitize_search_query(raw)
    if not clean_q:
        return ToolResult.fail("There was nothing safe to search for in that.")

    count = max(1, min(int(count or 4), 8))
    try:
        results = web_research.search_web(clean_q, max_results=count)
    except Exception as exc:
        return ToolResult.fail(f"I couldn't reach a search service: {exc}")

    if not results:
        cfg = web_research._config()
        if cfg["search_provider"] == "searxng":
            return ToolResult.fail(
                f"SearXNG didn't answer at {web_research.searxng_url()}. Start it with "
                "`docker compose up -d` in the searxng/ folder, or set DEXTER_SEARXNG_URL."
            )
        return ToolResult(
            ok=True,
            summary=f"Nothing came back for '{clean_q}'.",
            data={"query": clean_q, "results": []},
        )

    # Spoken: the top answer only. The rest are in `data` for the UI and the agent.
    top = results[0]
    spoken = top.get("snippet") or top.get("title") or ""
    if len(spoken) > 320:
        spoken = spoken[:317].rsplit(" ", 1)[0] + "..."

    url = top.get("url", "")
    source = top.get("source") or re.sub(r"^www\.", "", (url.split("/")[2] if "//" in url else ""))
    summary = spoken
    if source:
        summary += f" (from {source})"
    if len(results) > 1:
        summary += f" I have {len(results) - 1} more result(s) if you want them."
    if any(str(r.get("source", "")).lower().startswith("searxng") for r in results):
        summary += " (private search)"

    return ToolResult(
        ok=True,
        summary=summary,
        data={"query": clean_q, "results": results},
    )


@tool(
    name="web_fetch",
    description=(
        "Fetch a public web page and read its clean text (no scripts, cookies or "
        "tracking). Use to read an article, docs page or a result you found, without "
        "opening the browser."
    ),
    params={
        "url": {"type": "string", "description": "The http(s) URL to read"},
        "max_chars": {"type": "integer", "description": "How much text (default 3500)", "required": False},
    },
    examples=[
        "read that article for me",
        "fetch https://en.wikipedia.org/wiki/Bhangarh_Fort",
        "open that link and summarize it",
        "what does this page say: https://example.com/post",
    ],
    category="web",
)
def web_fetch(url: str, max_chars: int = 3500) -> ToolResult:
    text = web_research.fetch_page_content(url, max_chars=max(200, int(max_chars or 3500)))
    if text.startswith(("Error:", "Could not fetch")):
        return ToolResult.fail(text)
    preview = text[:600].replace("\n", " ")
    return ToolResult(
        ok=True,
        summary=f"{preview}" + ("…" if len(text) > 600 else ""),
        data={"url": url, "text": text, "length": len(text)},
    )


@tool(
    name="search_status",
    description=(
        "Check whether private web search (self-hosted SearXNG) is running, and which "
        "provider Dexter will use. Use for 'is private search on', 'test searxng'."
    ),
    params={},
    examples=[
        "is private search on",
        "test searxng",
        "is my private search working",
        "check the search provider",
    ],
    category="web",
)
def search_status() -> ToolResult:
    cfg = web_research._config()
    state = web_research.searxng_status()
    provider = cfg["search_provider"]
    if state["ok"]:
        summary = (f"Private search is on: SearXNG answered at {state['url']} "
                   f"(provider mode: {provider}).")
    else:
        summary = (f"SearXNG isn't reachable at {state['url']} ({state['detail']}). "
                   f"Provider mode is '{provider}'; "
                   + ("searches will fall back to Wikipedia/DuckDuckGo. "
                      if provider == "auto" else "")
                   + "Start it with `docker compose up -d` in the searxng/ folder.")
    return ToolResult(ok=True, summary=summary,
                      data={"provider": provider, "searxng": state})


@tool(
    name="get_weather",
    description="Current weather and a short forecast for a place, or here by default.",
    params={
        "location": {"type": "string", "description": "City or place", "required": False},
    },
    examples=[
        "what's the weather", "what's the weather like today",
        "is it going to rain", "weather in london",
        "how hot is it outside", "do I need a jacket",
        "what's the forecast",
    ],
    category="web",
)
def get_weather(location: str = "") -> ToolResult:
    location = (location or "").strip()

    try:
        # wttr.in geolocates by IP when the location is blank, which is the
        # right default for a desktop assistant.
        response = requests.get(
            f"https://wttr.in/{requests.utils.quote(location)}",
            params={"format": "j1"},
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        payload: Dict[str, Any] = response.json()
    except Exception as exc:
        return ToolResult.fail(f"I couldn't get the weather: {exc}")

    try:
        current = payload["current_condition"][0]
        area = payload.get("nearest_area", [{}])[0]
        place = location or _clean(
            (area.get("areaName", [{}])[0].get("value", "")) if area else ""
        ) or "here"

        temp_c = current["temp_C"]
        feels_c = current["FeelsLikeC"]
        description = _clean(current["weatherDesc"][0]["value"])
        today = payload["weather"][0]
        low, high = today["mintempC"], today["maxtempC"]
        rain_chance = max(
            (int(hour.get("chanceofrain", 0)) for hour in today.get("hourly", [])), default=0
        )
    except (KeyError, IndexError, ValueError) as exc:
        return ToolResult.fail(f"The weather service returned something unexpected: {exc}")

    summary = f"{description}, {temp_c} degrees in {place}"
    if feels_c != temp_c:
        summary += f", feels like {feels_c}"
    summary += f". Today: {low} to {high}."
    if rain_chance >= 40:
        summary += f" {rain_chance} percent chance of rain."

    return ToolResult(
        ok=True,
        summary=summary,
        data={
            "location": place,
            "tempC": temp_c,
            "feelsLikeC": feels_c,
            "description": description,
            "lowC": low,
            "highC": high,
            "chanceOfRain": rain_chance,
        },
    )
