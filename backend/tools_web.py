"""
Web search and weather - the assistant's only window onto the world.

Dexter had 55 tools and not one of them could look anything up. Paired with a
local model whose knowledge is frozen at training time, that meant every
question about the actual world ("what's the weather", "who won", "what's the
latest on X") was answered from memory, confidently, and often wrongly. This is
the single largest capability gap in the product.

Both providers are keyless on purpose, matching the local-first stance: DuckDuckGo
for search, wttr.in for weather. Neither needs an account, and both degrade to a
plain "I couldn't reach it" rather than an exception.

Results are handed back with their sources so the agent can cite them, and the
spoken summary stays short - reading five search results aloud is unusable.
"""

import re
from typing import Any, Dict, List, Optional

import requests

from tool_registry import ToolResult, tool

_TIMEOUT = 12
_USER_AGENT = "Dexter/2.0 (local assistant)"

try:
    from ddgs import DDGS

    _HAS_SEARCH = True
except ImportError:  # pragma: no cover - depends on the install
    DDGS = None  # type: ignore
    _HAS_SEARCH = False


def _clean(text: str) -> str:
    """Search snippets arrive with markup and runs of whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


def _search(query: str, limit: int) -> List[Dict[str, str]]:
    with DDGS() as ddgs:
        return [
            {
                "title": _clean(item.get("title", "")),
                "snippet": _clean(item.get("body", "")),
                "url": item.get("href", ""),
            }
            for item in ddgs.text(query, max_results=limit)
        ]


@tool(
    name="web_search",
    description=(
        "Search the web for current information the local model cannot know - "
        "news, facts, prices, documentation, anything after its training cutoff. "
        "Use this instead of guessing."
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
    query = (query or "").strip()
    if not query:
        return ToolResult.fail("What should I search for?")
    if not _HAS_SEARCH:
        return ToolResult.fail(
            "Web search needs the ddgs package. Install it with: pip install ddgs"
        )

    count = max(1, min(int(count or 4), 8))
    try:
        results = _search(query, count)
    except Exception as exc:
        return ToolResult.fail(f"I couldn't reach the search service: {exc}")

    if not results:
        return ToolResult(
            ok=True,
            summary=f"Nothing came back for '{query}'.",
            data={"query": query, "results": []},
        )

    # Spoken: the top answer only. Reading four results aloud is unusable, and
    # the rest are in `data` for the UI and for the agent to reason over.
    top = results[0]
    spoken = top["snippet"] or top["title"]
    if len(spoken) > 320:
        spoken = spoken[:317].rsplit(" ", 1)[0] + "..."

    source = re.sub(r"^www\.", "", (top["url"].split("/")[2] if "//" in top["url"] else ""))
    summary = f"{spoken}"
    if source:
        summary += f" (from {source})"
    if len(results) > 1:
        summary += f" I have {len(results) - 1} more result(s) if you want them."

    return ToolResult(
        ok=True,
        summary=summary,
        data={"query": query, "results": results},
    )


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
