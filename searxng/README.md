# Dexter private search (SearXNG)

Dexter's `web_search` tool searches the web **without leaking your data**, the same
way BuzzcafAI does it:

1. **On-device sanitisation** — `sanitize_search_query()` in `backend/tools_web.py`
   strips filesystem paths, API keys, code blocks and prompt directives *before*
   any query leaves the machine.
2. **Self-hosted SearXNG** — a metasearch instance you run locally. The query goes
   to your own box, which proxies the public engines, so no search provider sees
   who you are.
3. **Auto fallback** — if SearXNG is offline, Dexter falls back to keyless DuckDuckGo.

## Start SearXNG

```bash
cd searxng
docker compose up -d
```

Verify it at http://localhost:8080 (the JSON API is `http://localhost:8080/search?q=test&format=json`).

> Port **8080** is intentionally the same as BuzzcafAI's SearXNG. If BuzzcafAI's
> instance is already running, Dexter reuses it — you don't need this container.
> Don't run both at once (port clash); run one and point at 8080.

## Configuration (env vars, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `DEXTER_SEARXNG_URL` | `http://localhost:8080` | Your SearXNG instance |
| `DEXTER_SEARCH_PROVIDER` | `auto` | `auto` (SearXNG→DuckDuckGo), `searxng` (only), or `duckduckgo` (only) |

With defaults and SearXNG running, "search the web for X" is fully private.
