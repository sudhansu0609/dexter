"""
Model residency (v8): the fast model finds its own server and stays loaded;
the smart model is released after its task. Ollama's native API is stubbed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hybrid_llm import HybridLLMRouter


class _Res:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def _router(monkeypatch, ollama_models, lm_online=False):
    r = HybridLLMRouter()
    r.default_provider = "auto"
    monkeypatch.setattr(r, "get_available_providers", lambda force=False: {
        "llamacpp": {"online": True}, "lmStudio": {"online": lm_online}, "ollama": {"online": True},
        "gemini": {"configured": False}, "openai": {"configured": False},
    })
    monkeypatch.setattr(r, "_list_models_at", lambda url: ollama_models if "11434" in url else ["lm-model"])
    return r


def test_provider_for_model_finds_the_server_that_lists_it(monkeypatch):
    r = _router(monkeypatch, ["qwen2.5:3b", "qwen2.5:7b"], lm_online=True)
    assert r.provider_for_model("qwen2.5:3b") == "ollama"
    assert r.provider_for_model("lm-model") == "lmstudio"
    assert r.provider_for_model("nope:1b") is None
    assert r.provider_for_model("") is None


def test_keep_alive_calls_ollama_native_api(monkeypatch):
    r = HybridLLMRouter()
    r.ollama_url = "http://localhost:11434/v1"
    posted = []
    monkeypatch.setattr(r.session, "post", lambda url, json=None, timeout=0: posted.append((url, json)) or _Res(200))
    assert r.warm_model("ollama", "qwen2.5:3b", "2h")
    assert r.release_model("ollama", "qwen2.5:7b", "3m")
    assert r.release_model("ollama", "qwen2.5:7b")
    assert posted == [
        ("http://localhost:11434/api/generate", {"model": "qwen2.5:3b", "keep_alive": "2h", "stream": False}),
        ("http://localhost:11434/api/generate", {"model": "qwen2.5:7b", "keep_alive": "3m", "stream": False}),
        ("http://localhost:11434/api/generate", {"model": "qwen2.5:7b", "keep_alive": "0", "stream": False}),
    ]
    # Only Ollama exposes residency control; other providers are a no-op.
    assert r.warm_model("lmstudio", "x") is False and posted[-1][1]["model"] == "qwen2.5:7b"


def test_loaded_models_reports_vram_share(monkeypatch):
    r = HybridLLMRouter()
    monkeypatch.setattr(r.session, "get", lambda url, timeout=0: _Res(200, {"models": [
        {"name": "qwen2.5:3b", "size": 2 * 1024 ** 3, "size_vram": 2 * 1024 ** 3, "expires_at": "later"},
        {"name": "qwen2.5:7b", "size": 5 * 1024 ** 3, "size_vram": 1 * 1024 ** 3},
    ]}))
    loaded = r.loaded_models()
    assert [m["name"] for m in loaded] == ["qwen2.5:3b", "qwen2.5:7b"]
    assert loaded[0]["onGpu"] is True and loaded[1]["onGpu"] is False and loaded[1]["vramMb"] == 1024


def test_stream_decodes_utf8_without_a_charset_header():
    """An SSE body with a curly apostrophe must reach the UI intact, even though
    the server declares text/event-stream with no charset."""
    import io

    import requests

    res = requests.Response()
    res.status_code = 200
    res.headers["Content-Type"] = "text/event-stream"
    apostrophe = "’"
    body = (
        'data: {"choices":[{"delta":{"content":"How' + apostrophe + 's"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" it going?"}}]}\n\n'
        "data: [DONE]\n\n"
    ).encode("utf-8")
    res.raw = io.BytesIO(body)
    got = []
    out = HybridLLMRouter()._consume_stream(res, "ollama", "m", got.append)
    assert out.ok and out.content == f"How{apostrophe}s it going?"
    assert got == [f"How{apostrophe}s", " it going?"]


def test_local_urls_never_use_localhost(monkeypatch):
    """Windows resolves localhost to ::1 first and burns ~2 s per call before
    falling back to IPv4; every local endpoint must be 127.0.0.1."""
    from hybrid_llm import local_url

    assert local_url("http://localhost:11434/v1") == "http://127.0.0.1:11434/v1"
    assert local_url("http://LOCALHOST:1234") == "http://127.0.0.1:1234"
    assert local_url("http://127.0.0.1:8089/v1") == "http://127.0.0.1:8089/v1"
    assert local_url("https://api.example.com/v1") == "https://api.example.com/v1"
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LM_STUDIO_URL", "http://localhost:1234/v1")
    r = HybridLLMRouter()
    assert r.ollama_url == "http://127.0.0.1:11434/v1" and r.lm_studio_url == "http://127.0.0.1:1234/v1"


# ───────── the engine's port is discovered, not assumed (P1) ─────────


def test_llamacpp_url_still_wins_when_the_owner_set_it(monkeypatch):
    """An address typed by hand outranks anything the ledger says."""
    from hybrid_llm import engine_url

    monkeypatch.setenv("LLAMACPP_URL", "http://localhost:9099/v1")
    assert engine_url() == "http://127.0.0.1:9099/v1"


def test_the_engine_is_found_where_the_ledger_says_it_landed(monkeypatch):
    """buzzcode steps forward from 8089 like everything else, and 12 GB of model
    on a port Dexter is not asking reads exactly like no model at all."""
    import buzzcaf_ports
    from hybrid_llm import engine_url

    monkeypatch.delenv("LLAMACPP_URL", raising=False)
    monkeypatch.setattr(
        buzzcaf_ports, "entry",
        lambda app, *a, **k: {"port": 8091, "pid": os.getpid()} if app == "buzzcode" else None,
    )
    assert engine_url() == "http://127.0.0.1:8091/v1"


def test_a_dead_engine_entry_falls_back_to_the_default(monkeypatch):
    import buzzcaf_ports
    from hybrid_llm import DEFAULT_LLAMACPP_URL, engine_url

    monkeypatch.delenv("LLAMACPP_URL", raising=False)
    monkeypatch.setattr(buzzcaf_ports, "entry", lambda app, *a, **k: {"port": 8091, "pid": 4})
    monkeypatch.setattr(buzzcaf_ports, "_pid_alive", lambda pid: False)
    assert engine_url() == DEFAULT_LLAMACPP_URL


def test_an_empty_ledger_falls_back_to_the_default(monkeypatch):
    from hybrid_llm import DEFAULT_LLAMACPP_URL, engine_url

    monkeypatch.delenv("LLAMACPP_URL", raising=False)
    assert engine_url() == DEFAULT_LLAMACPP_URL == "http://127.0.0.1:8089/v1"
