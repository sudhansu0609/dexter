"""
Private web research, the BuzzcafAI way - no network in tests.

Every query is sanitised on-device, then SearXNG (self-hosted, default localhost:8080)
is tried first, with Wikipedia and DuckDuckGo as fallbacks. These tests monkeypatch
the provider functions and `requests`, so nothing ever touches the network.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import web_research
import tools_web


# ───────────────────────── sanitisation (the privacy guarantee) ─────────────────────────


def test_sanitize_strips_paths_keys_and_directives():
    dirty = r'[WEB_SEARCH: bhangarh fort history] C:\Users\me\secret\notes.txt sk-ABCDEFGHIJKLMNOPQRSTUVWX ```json```'
    clean = web_research.sanitize_search_query(dirty)
    assert "C:\\" not in clean and "secret" not in clean
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in clean
    assert "WEB_SEARCH" not in clean
    assert "bhangarh fort history" in clean


def test_sanitize_truncates_to_120_chars():
    assert len(web_research.sanitize_search_query("word " * 60)) <= 120


# ───────────────────────── the SearXNG client ─────────────────────────


def test_search_searxng_maps_fields_and_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("DEXTER_SEARXNG_URL", raising=False)
    payload = {"results": [
        {"title": "<b>Title</b>", "content": "Some   content", "url": "https://ex.com/a", "engine": "google"},
        {"title": "Second", "content": "More", "url": "https://ex.com/b"},
    ]}

    class Resp:
        status_code = 200

        def json(self):
            return payload

    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update(url=url, params=params, headers=headers)
        return Resp()

    monkeypatch.setattr(web_research.requests, "get", fake_get)
    rows = web_research.search_searxng("some query", max_results=2)

    assert rows == [
        {"title": "Title", "snippet": "Some content", "url": "https://ex.com/a", "source": "SearXNG (google)"},
        {"title": "Second", "snippet": "More", "url": "https://ex.com/b", "source": "SearXNG (searxng)"},
    ]
    assert captured["url"] == "http://localhost:8080/search"
    assert captured["params"]["format"] == "json"


# ───────────────────────── the unified auto chain ─────────────────────────


def test_auto_combines_all_sources_searxng_first(monkeypatch):
    # BuzzcafAI's auto mode queries SearXNG + Wikipedia + DuckDuckGo and merges
    # them (deduped), with SearXNG's results first.
    monkeypatch.delenv("DEXTER_SEARCH_PROVIDER", raising=False)
    sx = [{"title": "SX", "snippet": "s", "url": "https://ex.com/1", "source": "SearXNG (google)"}]
    wiki = [{"title": "W", "snippet": "w", "url": "https://ex.com/2", "source": "Wikipedia"}]
    ddg = [{"title": "D", "snippet": "d", "url": "https://ex.com/3", "source": "DuckDuckGo"}]
    monkeypatch.setattr(web_research, "search_searxng", lambda q, base_url=None, max_results=5: sx)
    monkeypatch.setattr(web_research, "search_wikipedia", lambda q, max_results=4: wiki)
    monkeypatch.setattr(web_research, "search_duckduckgo_text", lambda q, max_results=5: ddg)
    monkeypatch.setattr(web_research, "search_duckduckgo_instant", lambda q: [])
    rows = web_research.search_web("x", max_results=5)
    assert rows[0]["source"].startswith("SearXNG")
    assert {r["url"] for r in rows} == {"https://ex.com/1", "https://ex.com/2", "https://ex.com/3"}


def test_auto_falls_through_to_wikipedia_then_ddg(monkeypatch):
    monkeypatch.delenv("DEXTER_SEARCH_PROVIDER", raising=False)
    monkeypatch.setattr(web_research, "search_searxng", lambda q, base_url=None, max_results=5: [])
    monkeypatch.setattr(web_research, "search_wikipedia", lambda q, max_results=4: [])
    ddg = [{"title": "DDG", "snippet": "d", "url": "https://duckduckgo.com/x", "source": "DuckDuckGo"}]
    monkeypatch.setattr(web_research, "search_duckduckgo_text", lambda q, max_results=5: ddg)
    monkeypatch.setattr(web_research, "search_duckduckgo_instant", lambda q: [])
    assert web_research.search_web("x") == ddg


def test_provider_searxng_only_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("DEXTER_SEARCH_PROVIDER", "searxng")
    monkeypatch.setattr(web_research, "search_searxng", lambda q, base_url=None, max_results=5: [])
    monkeypatch.setattr(web_research, "search_wikipedia",
                        lambda q, max_results=4: (_ for _ in ()).throw(AssertionError("no fallback")))
    assert web_research.search_web("x") == []


def test_search_web_deduplicates_by_url(monkeypatch):
    monkeypatch.delenv("DEXTER_SEARCH_PROVIDER", raising=False)
    dup = {"title": "A", "snippet": "s", "url": "https://ex.com/same", "source": "SearXNG (google)"}
    monkeypatch.setattr(web_research, "search_searxng", lambda q, base_url=None, max_results=5: [dup])
    monkeypatch.setattr(web_research, "search_wikipedia",
                        lambda q, max_results=4: [{"title": "A2", "snippet": "s2",
                                                   "url": "https://ex.com/same", "source": "Wikipedia"}])
    monkeypatch.setattr(web_research, "search_duckduckgo_text", lambda q, max_results=5: [])
    monkeypatch.setattr(web_research, "search_duckduckgo_instant", lambda q: [])
    rows = web_research.search_web("x")
    assert len(rows) == 1 and rows[0]["source"] == "SearXNG (google)"


# ───────────────────────── safe page fetch ─────────────────────────


def test_fetch_page_content_strips_scripts(monkeypatch):
    html = "<html><head><style>x{}</style></head><body><script>evil()</script><p>Hello there</p></body></html>"

    class Resp:
        status_code = 200
        text = html

    monkeypatch.setattr(web_research.requests, "get", lambda *a, **k: Resp())
    out = web_research.fetch_page_content("https://ex.com/p")
    assert "Hello there" in out
    assert "evil" not in out and "x{}" not in out


def test_fetch_rejects_non_http():
    assert web_research.fetch_page_content("file:///etc/passwd").startswith("Error")


# ───────────────────────── tool wrappers ─────────────────────────


def test_web_search_tool_notes_private_when_searxng(monkeypatch):
    monkeypatch.setattr(web_research, "search_web",
                        lambda q, max_results=5: [{"title": "T", "snippet": "body",
                                                   "url": "https://ex.com/a", "source": "SearXNG (google)"}])
    res = tools_web.web_search("news today")
    assert res.ok and "private search" in res.summary


def test_web_fetch_tool(monkeypatch):
    monkeypatch.setattr(web_research, "fetch_page_content", lambda url, max_chars=3500: "Clean article text")
    res = tools_web.web_fetch("https://ex.com/a")
    assert res.ok and res.data["text"] == "Clean article text"


def test_search_status_tool_reports_offline(monkeypatch):
    monkeypatch.setattr(web_research, "searxng_status",
                        lambda base_url=None: {"ok": False, "url": "http://localhost:8080", "detail": "refused"})
    monkeypatch.setenv("DEXTER_SEARCH_PROVIDER", "auto")
    res = tools_web.search_status()
    assert res.ok and "isn't reachable" in res.summary
