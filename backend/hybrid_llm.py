"""
Hybrid LLM router.

Dexter runs local-first (LM Studio / Ollama). That constraint drives three
design choices here:

  * Provider health is cached. The previous version fired two blocking 1.5s
    probes on *every* generate() call and every /status poll, which alone cost
    up to 3 seconds per message.
  * The loaded model name is discovered from /models rather than hardcoded, so
    swapping the model in LM Studio needs no code change.
  * Tool calling supports both the native OpenAI `tools` protocol and a plain
    JSON-in-text fallback, because small local models advertise native tool
    support far more often than they actually honour it.
"""

import json
import os

import env_boot  # noqa: F401  - loads .env for every entry point
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

import token_ledger

DEFAULT_SYSTEM_PROMPT = (
    "You are Dexter, an intelligent personal AI assistant. "
    "Be concise, direct, and precise. Keep answers to 1-3 sentences "
    "unless detail is explicitly requested. Never use filler."
)

HEALTH_TTL_SECONDS = 30.0
_PROBE_TIMEOUT = 1.5


def port_open(url: str, timeout: float = 0.3) -> bool:
    """A raw TCP connect. On this machine a connect to a closed local port
    times out instead of being refused, so an HTTP probe of a server that is
    simply not running cost the full probe timeout every time."""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def local_url(url: str) -> str:
    """'http://localhost:11434/v1' -> 'http://127.0.0.1:11434/v1' (see __init__)."""
    return re.sub(r"^(https?://)localhost(?=[:/]|$)", r"\g<1>127.0.0.1", (url or "").strip(), flags=re.IGNORECASE)


#: Where buzzcode's llama-server lives when nobody has said otherwise. A
#: last-resort default, not an assumption: see `engine_url`.
DEFAULT_LLAMACPP_URL = "http://127.0.0.1:8089/v1"


def engine_url() -> str:
    """buzzcode's OpenAI-compatible base URL.

    `LLAMACPP_URL` wins - an address the owner typed is the whole point of the
    variable. Failing that, the shared port ledger says where buzzcode's engine
    actually landed: it steps forward from 8089 like everything else since
    GUARDIAN_PLAN.md section 11, and 12 GB of model on the wrong port reads
    exactly like no model at all.

    The ledger entry is trusted on a live pid alone, without the identity check
    the rest of discovery uses: the thing on that port is llama.cpp, and no
    amount of asking will make its /health say `app: "buzzcode"`.
    """
    explicit = (os.getenv("LLAMACPP_URL") or "").strip()
    if explicit:
        return local_url(explicit).rstrip("/")
    try:
        import buzzcaf_ports

        record = buzzcaf_ports.entry("buzzcode")
        if record and buzzcaf_ports._pid_alive(record.get("pid")):
            port = int(record.get("port") or 0)
            if 0 < port < 65536:
                return f"http://127.0.0.1:{port}/v1"
    except Exception:
        pass
    return DEFAULT_LLAMACPP_URL


#: Ollama's own `/api/chat` instead of its OpenAI-compatible shim (v13, D3).
#: Measured on this machine: a thinking model asked through `/v1/chat/completions`
#: spends its whole token budget reasoning and returns empty content, because
#: `think:false` has nowhere to go in the OpenAI schema. The native endpoint
#: takes `think`, `keep_alive` and `options` per request, which is also how a
#: model gets loaded onto the CPU (`options.num_gpu: 0`, v13 D4). Off with
#: DEXTER_OLLAMA_NATIVE=0 if it ever needs to be.
OLLAMA_NATIVE = os.getenv("DEXTER_OLLAMA_NATIVE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


def _ollama_usage(body: Dict[str, Any]) -> Dict[str, Any]:
    """The generation counters Ollama reports on a finished response."""
    return {
        key: body[key]
        for key in ("eval_count", "eval_duration", "load_duration",
                    "prompt_eval_count", "total_duration")
        if body.get(key) is not None
    }


def _openai_usage(body: Dict[str, Any]) -> Dict[str, Any]:
    """The `usage` block of an OpenAI-compatible reply, plus which backend answered
    when the server says (claude-local-api reports `backend`)."""
    usage = body.get("usage") if isinstance(body, dict) else None
    out: Dict[str, Any] = dict(usage) if isinstance(usage, dict) else {}
    if isinstance(body, dict) and body.get("backend"):
        out["backend"] = body["backend"]
    return out


#: A reply that is *only* reasoning (v13, P1.3). Through the OpenAI-compatible
#: shim `think:false` has nowhere to go, so a reasoning model can burn its whole
#: budget deliberating and hand back empty content with the thoughts in
#: `reasoning` / `reasoning_content`. Showing nothing is the worst possible
#: outcome, so it is reported as an error and the router escalates.
ONLY_THOUGHT = "model only thought"

_REASONING_KEYS = ("reasoning", "reasoning_content")


def _thought_without_an_answer(
    content: Optional[str], payload: Dict[str, Any], calls: List[Dict[str, Any]],
) -> int:
    """Characters of reasoning behind an empty reply, or 0 when there is an answer.

    A tool call *is* an answer, so a model that thought and then called a tool
    is doing exactly what was asked of it.
    """
    if (content or "").strip() or calls:
        return 0
    thought = "".join(str(payload.get(key) or "") for key in _REASONING_KEYS)
    return len(thought.strip())


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    error: Optional[str] = None
    #: What the backend reported about the generation itself: `eval_count`,
    #: `eval_duration`, `load_duration` (Ollama's native path). Empty elsewhere.
    usage: Dict[str, Any] = field(default_factory=dict)
    # Set when the answering provider is not the one first selected - the
    # request was completed by a fallback after the primary failed mid-flight.
    fallback_from: Optional[str] = None
    # Set when generation was deliberately stopped mid-stream (barge-in, Phase
    # 4.5). Unlike an error this must NOT trigger provider fallback - the user
    # asked us to stop, not to try harder elsewhere.
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


class HybridLLMRouter:
    def __init__(self) -> None:
        # codeBuzz's llama-server (Qwen3.8-27B on 8089). It is only up while
        # codeBuzz runs, but when it is, it already owns the GPU - using it
        # beats loading a second model onto the CPU (roadmap v5, 1.5).
        # Always 127.0.0.1, never "localhost": on Windows, Python resolves
        # localhost to ::1 first and waits ~2 s for the IPv6 connect to fail
        # before trying IPv4 - which put a fixed two seconds on every model
        # call and every health probe, and made a 0.3 s reply feel like 2.5 s.
        self.llamacpp_url = engine_url()
        self.lm_studio_url = local_url(os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234/v1"))
        self.ollama_url = local_url(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/v1"))
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        # claude-local-api (B:\youtubeProjects\Buzzcaf_Media\claudeAPI): an
        # OpenAI-compatible server over the Claude Code login, with ChatGPT via
        # Codex as its own fallback. Needs its key; the model is optional (the
        # server's default is Sonnet with escalation to Opus).
        self.claude_url = local_url(os.getenv("CLAUDE_API_URL", "http://127.0.0.1:8787/v1")).rstrip("/")
        self.claude_api_key = os.getenv("CLAUDE_API_KEY", "").strip()
        self.claude_model = os.getenv("CLAUDE_API_MODEL", "").strip()

        # Explicit overrides; when unset the loaded model is auto-detected.
        self.llamacpp_model = os.getenv("LLAMACPP_MODEL", "")
        self.lm_studio_model = os.getenv("LM_STUDIO_MODEL", "gemma-4-12b")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2")

        # Preferred local backend for "auto" requests. Set to "ollama" to run
        # Dexter on Ollama and keep LM Studio free for other work (it stays a
        # fallback if Ollama is down); "lmstudio" forces the reverse.
        self.default_provider = (os.getenv("DEXTER_LLM_PROVIDER", "lmstudio").strip().lower() or "lmstudio")

        self.session = requests.Session()
        self._health_cache: Optional[Dict[str, Any]] = None
        self._health_checked_at = 0.0
        self._model_cache: Dict[str, str] = {}
        # (provider:model) -> (served?, checked_at); see provider_for_model.
        self._served_cache: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # ───────────────────────── health ─────────────────────────

    def _check_endpoint(self, url: str, headers: Optional[Dict[str, str]] = None) -> bool:
        if not port_open(url):
            return False
        try:
            return self.session.get(url, timeout=_PROBE_TIMEOUT, headers=headers or {}).status_code == 200
        except Exception:
            return False

    def _claude_online(self) -> bool:
        """Up *and* the key works: the Claude server answers /models only when authenticated.
        Kept apart from `_check_endpoint`, which tests replace with a one-argument fake."""
        if not self.claude_api_key:
            return False
        # Reachability goes through the shared probe (its unauthenticated
        # /health), so a test that fakes `_check_endpoint` decides here too.
        root = re.sub(r"/v1$", "", self.claude_url)
        if not self._check_endpoint(f"{root}/health"):
            return False
        try:
            res = self.session.get(f"{self.claude_url}/models", timeout=_PROBE_TIMEOUT,
                                   headers=self._headers_for("claude"))
            return res.status_code == 200
        except Exception:
            return False

    def _headers_for(self, provider: str) -> Dict[str, str]:
        if provider == "claude":
            return {"Authorization": f"Bearer {self.claude_api_key}"} if self.claude_api_key else {}
        if provider == "openai":
            return {"Authorization": f"Bearer {self.openai_api_key}"} if self.openai_api_key else {}
        return {}

    def get_available_providers(self, force: bool = False) -> Dict[str, Any]:
        """Cached for HEALTH_TTL_SECONDS. This is on the hot path of every message."""
        now = time.time()
        with self._lock:
            fresh = (
                self._health_cache is not None
                and (now - self._health_checked_at) < HEALTH_TTL_SECONDS
            )
            if fresh and not force:
                return self._health_cache  # type: ignore[return-value]

        # buzzcode usually starts *after* Dexter, so re-ask the ledger on every
        # real health check rather than holding whatever was true at import.
        if not (os.getenv("LLAMACPP_URL") or "").strip():
            self.llamacpp_url = engine_url()
        llamacpp_online = self._check_endpoint(f"{self.llamacpp_url}/models")
        lm_online = self._check_endpoint(f"{self.lm_studio_url}/models")
        ollama_online = self._check_endpoint(f"{self.ollama_url}/models")
        # v11.1: the preferred backend is a program, and nobody was starting it.
        # If it is down, start it (llm_boot: detached, rate-limited, off with
        # DEXTER_AUTOSTART_LLM=0) and probe once more. This is the one place
        # every path goes through - the request chain, the tier pickers,
        # preflight, /llm/models - so it is the one place to do it.
        wanted = self.default_provider
        down = (wanted == "ollama" and not ollama_online) or (wanted == "lmstudio" and not lm_online)
        if down and self._revive(wanted):
            if wanted == "ollama":
                ollama_online = self._check_endpoint(f"{self.ollama_url}/models")
            else:
                lm_online = self._check_endpoint(f"{self.lm_studio_url}/models")

        # The Claude server answers /models only with a valid key, so one probe
        # checks both that it is up and that the key works.
        claude_online = self._claude_online()

        result = {
            "llamacpp": {"online": llamacpp_online, "url": self.llamacpp_url},
            "lmStudio": {"online": lm_online, "url": self.lm_studio_url},
            "ollama": {"online": ollama_online, "url": self.ollama_url},
            "claude": {"online": claude_online, "url": self.claude_url, "configured": bool(self.claude_api_key)},
            "gemini": {"configured": bool(self.gemini_api_key)},
            "openai": {"configured": bool(self.openai_api_key)},
        }

        with self._lock:
            self._health_cache = result
            self._health_checked_at = time.time()
        return result

    def _revive(self, provider: str) -> bool:
        """Start the preferred local backend when it is not answering.

        True when it answers now (started here, or came up meanwhile). Never
        raises: a backend that will not start is a health result, not a crash.
        """
        try:
            import llm_boot

            outcome = llm_boot.ensure_provider(provider)
            if not outcome.get("online") and not outcome.get("reason", "").startswith("tried less than"):
                print(f"[HybridLLM] {provider} is down and could not be started: {outcome.get('reason')}")
            return bool(outcome.get("online"))
        except Exception as exc:
            print(f"[HybridLLM] autostart of {provider} failed: {exc}")
            return False

    def invalidate_health(self) -> None:
        with self._lock:
            self._health_cache = None
            # The shared llama-server swaps models when codeBuzz does; do not
            # keep naming a model that is no longer loaded.
            self._model_cache.pop(self.llamacpp_url, None)
            self._served_cache.clear()

    def _list_models_at(self, base_url: str, headers: Optional[Dict[str, str]] = None) -> List[str]:
        """All model ids a local OpenAI-compatible server currently exposes."""
        try:
            res = self.session.get(f"{base_url}/models", timeout=_PROBE_TIMEOUT, headers=headers or {})
            if res.status_code == 200:
                data = res.json().get("data") or []
                return [d.get("id", "") for d in data if d.get("id")]
        except Exception:
            pass
        return []

    def list_models(self) -> Dict[str, Any]:
        """
        Per-provider online state + available model ids, plus the current
        selection - everything the UI needs to render a provider/model picker.
        """
        health = self.get_available_providers(force=True)
        providers = {}
        for key, prov, url in (
            ("llamacpp", "llamacpp", self.llamacpp_url),
            ("lmStudio", "lmstudio", self.lm_studio_url),
            ("ollama", "ollama", self.ollama_url),
        ):
            online = bool(health[key]["online"])
            providers[prov] = {
                "online": online,
                "url": url,
                "models": self._list_models_at(url) if online else [],
            }
        claude_online = bool(health.get("claude", {}).get("online"))
        providers["claude"] = {
            "online": claude_online,
            "url": self.claude_url,
            "configured": bool(self.claude_api_key),
            "models": self._list_models_at(self.claude_url, self._headers_for("claude")) if claude_online else [],
        }
        return {
            "providers": providers,
            "preferred": self.default_provider,
            "defaultModel": {
                "llamacpp": self.llamacpp_model or None,
                "lmstudio": self.lm_studio_model or "gemma-4-12b",
                "ollama": self.ollama_model or None,
                "claude": self.claude_model or None,
            },
        }

    # ───────────────────────── model discovery ─────────────────────────

    def _discover_model(self, base_url: str) -> str:
        """Ask the server what is actually loaded instead of guessing a name."""
        if base_url in self._model_cache:
            return self._model_cache[base_url]
        try:
            res = self.session.get(f"{base_url}/models", timeout=_PROBE_TIMEOUT)
            if res.status_code == 200:
                data = res.json().get("data") or []
                if data:
                    model_id = data[0].get("id", "")
                    if model_id:
                        self._model_cache[base_url] = model_id
                        print(f"[HybridLLM] Detected model at {base_url}: {model_id}")
                        return model_id
        except Exception:
            pass
        return ""

    @staticmethod
    def _model_matches(candidate: str, requested: str) -> bool:
        if not candidate or not requested:
            return False
        if candidate == requested or candidate.split(":")[0] == requested:
            return True
        c_clean = candidate.split("/")[-1].lower().replace(" ", "-").replace("_", "-")
        r_clean = requested.split("/")[-1].lower().replace(" ", "-").replace("_", "-")
        if c_clean == r_clean or r_clean in c_clean or c_clean in r_clean:
            return True
        c_norm = c_clean.replace("gemma4", "gemma-4")
        r_norm = r_clean.replace("gemma4", "gemma-4")
        return c_norm == r_norm or r_norm in c_norm or c_norm in r_norm

    def _model_for(self, provider: str, override: Optional[str]) -> str:
        if override:
            return override
        if provider == "llamacpp":
            return self.llamacpp_model or self._discover_model(self.llamacpp_url) or "local-model"
        if provider == "lmstudio":
            discovered = self._discover_model(self.lm_studio_url)
            target = self.lm_studio_model or "gemma-4-12b"
            if discovered and self._model_matches(discovered, target):
                return discovered
            return self.lm_studio_model or discovered or "gemma-4-12b"
        if provider == "ollama":
            return self.ollama_model or self._discover_model(self.ollama_url) or "llama3.2"
        if provider == "gemini":
            return "gemini-1.5-pro"
        if provider == "openai":
            return "gpt-4o"
        if provider == "claude":
            # "default" lets the server apply its own tiering (Sonnet, escalating to Opus).
            return self.claude_model or "default"
        return "local-model"

    def _is_available(self, provider: str, providers: Dict[str, Any]) -> bool:
        if provider == "llamacpp":
            return bool(providers.get("llamacpp", {}).get("online"))
        if provider == "lmstudio":
            return bool(providers["lmStudio"]["online"])
        if provider == "ollama":
            return bool(providers["ollama"]["online"])
        if provider == "gemini":
            return bool(providers["gemini"]["configured"])
        if provider == "openai":
            return bool(providers["openai"]["configured"])
        if provider == "claude":
            return bool(providers.get("claude", {}).get("online"))
        return False

    def resolve_provider(self, provider: str = "auto") -> str:
        if provider and provider != "auto":
            return provider
        providers = self.get_available_providers()
        # Configured preference wins when it is actually up; otherwise fall back
        # to the original local-first order.
        order = ["llamacpp", "lmstudio", "ollama", "claude", "gemini", "openai"]
        if self.default_provider != "auto":
            order = [self.default_provider] + [p for p in order if p != self.default_provider]
        for prov in order:
            if self._is_available(prov, providers):
                return prov
        return "lmstudio"

    def _base_url_for(self, provider: str) -> str:
        if provider == "llamacpp":
            return self.llamacpp_url
        if provider == "claude":
            return self.claude_url
        return self.ollama_url if provider == "ollama" else self.lm_studio_url

    def _provider_chain(self, provider: str, needs_tools: bool = False) -> List[str]:
        """
        Ordered providers to try for one request. An explicit choice is honoured
        exactly; "auto" is local-first with cloud fallback so a local server
        dying mid-request degrades to a configured key instead of an apology.
        Gemini is chat-only here (its path has no tool calling), so it is
        skipped whenever tools are required.
        """
        if provider and provider != "auto":
            return [provider]
        health = self.get_available_providers()
        chain: List[str] = []
        if health.get("llamacpp", {}).get("online"):
            chain.append("llamacpp")
        if health["lmStudio"]["online"]:
            chain.append("lmstudio")
        if health["ollama"]["online"]:
            chain.append("ollama")
        if health.get("claude", {}).get("online"):
            chain.append("claude")
        if health["openai"]["configured"]:
            chain.append("openai")
        if health["gemini"]["configured"] and not needs_tools:
            chain.append("gemini")
        # Honour the configured default: move it to the front so it is tried
        # first, while the others remain as graceful fallbacks. Frees the
        # non-preferred local backend (e.g. LM Studio) for other work.
        if self.default_provider != "auto" and self.default_provider in chain:
            chain = [self.default_provider] + [p for p in chain if p != self.default_provider]
        return chain or ["lmstudio"]

    def provider_for_model(self, model: str, preferred: str = "") -> Optional[str]:
        """
        The online local backend that actually serves `model` (an Ollama tag
        like "qwen2.5:3b" or an LM Studio id). Used to run the small fast model
        on its own server even while the shared llama-server holds the big one.
        Returns None when no online backend lists it.
        """
        if not model:
            return None
        health = self.get_available_providers()
        order = ["ollama", "lmstudio"]
        if preferred in order:
            order = [preferred] + [p for p in order if p != preferred]
        elif self.default_provider in order:
            order = [self.default_provider] + [p for p in order if p != self.default_provider]
        for prov in order:
            if not self._is_available(prov, health):
                continue
            key = f"{prov}:{model}"
            now = time.time()
            with self._lock:
                cached = self._served_cache.get(key)
            if cached and now - cached[1] < HEALTH_TTL_SECONDS:
                if cached[0]:
                    return prov
                continue
            ids = self._list_models_at(self._base_url_for(prov))
            served = any(self._model_matches(m, model) for m in ids)
            with self._lock:
                self._served_cache[key] = (served, now)
            if served:
                return prov
        return None

    # ───────────────────────── model residency (Ollama) ─────────────────────────
    #
    # Ollama keeps a model in memory for 5 minutes after each request, whatever
    # the request was. The instant-reply tier wants the small model resident
    # for hours (a cold load is what makes the first answer slow), and the big
    # model gone soon after its task so the GPU is free again. Both are one
    # native /api/generate call with `keep_alive` and no prompt: it (re)sets
    # the expiry without generating anything.

    def _ollama_api(self) -> str:
        return self.ollama_url[:-3] if self.ollama_url.endswith("/v1") else self.ollama_url

    def set_keep_alive(
        self, provider: str, model: str, keep_alive: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if provider != "ollama" or not model:
            return False
        body: Dict[str, Any] = {"model": model, "keep_alive": keep_alive, "stream": False}
        if options:
            # `options.num_gpu` is how a model is put somewhere other than the
            # card: 0 loads it entirely into RAM, N splits it (v13, D4). Ollama
            # keeps a model where it was put, so the placement only changes on
            # the next *load* — which is exactly the lever the steward wants.
            body["options"] = dict(options)
        try:
            res = self.session.post(
                f"{self._ollama_api()}/api/generate",
                json=body,
                timeout=120,
            )
            return res.status_code == 200
        except Exception as exc:
            print(f"[HybridLLM] keep_alive({model}={keep_alive}) failed: {exc}")
            return False

    def warm_model(
        self, provider: str, model: str, keep_alive: str = "2h",
        options: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Load `model` now (if needed) and keep it resident for `keep_alive`."""
        return self.set_keep_alive(provider, model, keep_alive, options=options)

    def model_layers(self, model: str) -> int:
        """How many transformer blocks `model` has, per Ollama's `/api/show`.

        Needed to turn "this much VRAM is free" into a `num_gpu` layer count
        (v13, P3.4). 0 when Ollama cannot say, which the caller reads as "do not
        try to split this one".
        """
        if not model:
            return 0
        try:
            res = self.session.post(
                f"{self._ollama_api()}/api/show", json={"model": model}, timeout=10,
            )
            if res.status_code != 200:
                return 0
            info = res.json().get("model_info") or {}
        except Exception:
            return 0
        for key, value in info.items():
            if str(key).endswith(".block_count"):
                try:
                    return int(value)
                except Exception:
                    return 0
        return 0

    def release_model(self, provider: str, model: str, after: str = "0") -> bool:
        """Unload `model` now ("0") or once idle for `after` (e.g. "3m")."""
        return self.set_keep_alive(provider, model, after)

    def loaded_models(self) -> List[Dict[str, Any]]:
        """What Ollama currently holds in memory (name, size, VRAM share, expiry)."""
        try:
            res = self.session.get(f"{self._ollama_api()}/api/ps", timeout=_PROBE_TIMEOUT)
            if res.status_code == 200:
                out = []
                for m in res.json().get("models") or []:
                    size = int(m.get("size") or 0)
                    vram = int(m.get("size_vram") or 0)
                    out.append({
                        "name": m.get("name") or m.get("model"),
                        "sizeMb": size // (1024 * 1024),
                        "vramMb": vram // (1024 * 1024),
                        "onGpu": size > 0 and vram >= size,
                        # Where it actually is (v13): all of it on the card, all
                        # of it in RAM, or Ollama's own partial offload. Read off
                        # the numbers rather than remembered, so a model someone
                        # else loaded is described just as honestly as ours.
                        "place": "gpu" if size > 0 and vram >= size else ("cpu" if vram <= 0 else "split"),
                        "expiresAt": m.get("expires_at"),
                    })
                return out
        except Exception:
            pass
        return []

    # ───────────────────────── chat ─────────────────────────

    def chat(
        self,
        messages: List[Dict[str, Any]],
        provider: str = "auto",
        model: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 768,
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: int = 90,
        on_token: Optional[Callable[[str], None]] = None,
        on_fallback: Optional[Callable[[str, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        think: Optional[Any] = False,
        options: Optional[Dict[str, Any]] = None,
        keep_alive: Optional[str] = None,
    ) -> LLMResponse:
        """
        Full multi-turn chat with optional native tool calling.

        `on_token` streams content deltas as they arrive (the request is made
        with stream=True); the returned LLMResponse still carries the complete
        content and parsed tool calls either way.

        `on_fallback(failed, next)` fires before each retry against the next
        provider in the chain, so callers can reset any tokens already shown.

        `should_cancel()` is polled while a stream is consumed; when it returns
        True generation stops and the response is flagged `cancelled` - returned
        as-is without trying the next provider (a barge-in is not a failure).

        `think` / `options` / `keep_alive` are Ollama's own knobs (v13, D3) and
        only reach the native `/api/chat` path: `think=False` (the default) stops
        a reasoning model from spending its whole budget deliberating, `options`
        carries `num_gpu` and friends, `keep_alive` pins the model for this
        request. Every other provider ignores them, so a caller never has to ask
        which backend answered.
        """
        chain = self._provider_chain(provider, needs_tools=bool(tools))
        last: Optional[LLMResponse] = None
        for idx, prov in enumerate(chain):
            if should_cancel and should_cancel():
                return LLMResponse(provider=prov, model=model or "", cancelled=True)
            if idx > 0:
                print(f"[HybridLLM] {chain[idx - 1]} failed mid-request; trying {prov}.")
                if on_fallback:
                    try:
                        on_fallback(chain[idx - 1], prov)
                    except Exception:
                        pass
            response = self._chat_one(
                prov, messages, model, temperature, max_tokens, tools, timeout,
                on_token, should_cancel, think=think, options=options,
                keep_alive=keep_alive,
            )
            if response.cancelled:
                return response
            if response.ok:
                if idx > 0:
                    response.fallback_from = chain[0]
                token_ledger.record(response.provider or prov, response.model or model or "", response.usage)
                return response
            last = response
            # The failed probe result is stale now; let the next request re-probe.
            self.invalidate_health()
        return last or LLMResponse(
            provider=chain[0] if chain else "none",
            error="No LLM provider is available.",
        )

    def _chat_one(
        self,
        prov: str,
        messages: List[Dict[str, Any]],
        model: Optional[str],
        temperature: float,
        max_tokens: int,
        tools: Optional[List[Dict[str, Any]]],
        timeout: int,
        on_token: Optional[Callable[[str], None]],
        should_cancel: Optional[Callable[[], bool]] = None,
        think: Optional[Any] = False,
        options: Optional[Dict[str, Any]] = None,
        keep_alive: Optional[str] = None,
    ) -> LLMResponse:
        model_name = self._model_for(prov, model)

        if prov == "ollama" and OLLAMA_NATIVE:
            return self._chat_ollama_native(
                messages, model_name, temperature, max_tokens, tools, timeout,
                on_token=on_token, should_cancel=should_cancel, think=think,
                options=options, keep_alive=keep_alive,
            )

        if prov == "gemini":
            if not self.gemini_api_key:
                return LLMResponse(provider=prov, model=model_name,
                                   error="Gemini API key not configured")
            return self._chat_gemini(messages, model_name, temperature, max_tokens)

        if prov == "openai":
            if not self.openai_api_key:
                return LLMResponse(provider=prov, model=model_name,
                                   error="OpenAI API key not configured")
            return self._chat_openai_compat(
                "https://api.openai.com/v1", messages, model_name, temperature,
                max_tokens, tools, timeout,
                headers={"Authorization": f"Bearer {self.openai_api_key}"},
                provider_label="openai", on_token=on_token, should_cancel=should_cancel,
            )

        if prov == "claude":
            if not self.claude_api_key:
                return LLMResponse(provider=prov, model=model_name,
                                   error="CLAUDE_API_KEY not configured (see claudeAPI/README.md)")
            return self._chat_openai_compat(
                self.claude_url, messages, model_name, temperature,
                max_tokens, tools, timeout, headers=self._headers_for("claude"),
                provider_label="claude", on_token=on_token, should_cancel=should_cancel,
            )

        return self._chat_openai_compat(
            self._base_url_for(prov), messages, model_name, temperature,
            max_tokens, tools, timeout, provider_label=prov, on_token=on_token,
            should_cancel=should_cancel,
        )

    # ───────────────────────── Ollama's own /api/chat (v13, D3) ─────────────────────────

    def _chat_ollama_native(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: Optional[List[Dict[str, Any]]],
        timeout: int,
        on_token: Optional[Callable[[str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        think: Optional[Any] = False,
        options: Optional[Dict[str, Any]] = None,
        keep_alive: Optional[str] = None,
        _no_think_key: bool = False,
    ) -> LLMResponse:
        """Ollama's native chat endpoint — same LLMResponse, three more levers.

        Measured before this existed: qwen3.5 asked through `/v1/chat/completions`
        spent all 200 tokens reasoning and returned empty content, because the
        OpenAI schema has no way to say "do not think". The same question through
        here with `think:false` answers in 0.6 s.
        """
        streaming = on_token is not None
        base = self._ollama_api()
        opts: Dict[str, Any] = {"temperature": temperature, "num_predict": max_tokens}
        if options:
            opts.update(options)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": streaming,
            "options": opts,
        }
        if not _no_think_key:
            payload["think"] = think
        if tools:
            payload["tools"] = tools
        if keep_alive:
            payload["keep_alive"] = keep_alive

        try:
            res = self.session.post(
                f"{base}/api/chat", json=payload, timeout=timeout, stream=streaming,
            )
        except Exception as exc:
            return LLMResponse(
                provider="ollama", model=model,
                error=f"Could not reach the local model at {base}: {exc}",
            )

        if res.status_code != 200:
            body = res.text[:300]
            # Ollama refuses `think` outright for a model that has no reasoning
            # mode ("... does not support thinking"). Asking a plain model not to
            # think must not be the reason a turn fails, so drop the key and ask
            # again — once.
            if not _no_think_key and "think" in body.lower():
                return self._chat_ollama_native(
                    messages, model, temperature, max_tokens, tools, timeout,
                    on_token, should_cancel, think, options, keep_alive,
                    _no_think_key=True,
                )
            if tools and res.status_code in (400, 422, 500):
                print(f"[HybridLLM] Ollama rejected native tools ({res.status_code}); retrying without.")
                return self._chat_ollama_native(
                    messages, model, temperature, max_tokens, None, timeout,
                    on_token, should_cancel, think, options, keep_alive,
                    _no_think_key=_no_think_key,
                )
            return LLMResponse(
                provider="ollama", model=model,
                error=f"Model server returned {res.status_code}: {body}",
            )

        if streaming:
            return self._consume_ollama_stream(res, model, on_token, should_cancel)

        try:
            body_json = res.json()
        except Exception as exc:
            return LLMResponse(
                provider="ollama", model=model,
                error=f"Malformed response from model server: {exc}",
            )
        message = body_json.get("message") or {}
        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=self._parse_tool_calls(message.get("tool_calls") or []),
            provider="ollama", model=model,
            usage=_ollama_usage(body_json),
        )

    def _consume_ollama_stream(
        self,
        res: requests.Response,
        model: str,
        on_token: Callable[[str], None],
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> LLMResponse:
        """Ollama streams newline-delimited JSON objects, not SSE frames.

        Otherwise identical to `_consume_stream`: content deltas go straight to
        `on_token`, a cancellation abandons the stream without an error, and a
        connection that dies mid-reply is an error so the caller can retry it
        somewhere else rather than showing half an answer.
        """
        content_parts: List[str] = []
        raw_calls: List[Dict[str, Any]] = []
        usage: Dict[str, Any] = {}
        res.encoding = "utf-8"
        try:
            with res:
                for line in res.iter_lines(decode_unicode=True):
                    if should_cancel and should_cancel():
                        res.close()
                        return LLMResponse(
                            content="".join(content_parts),
                            provider="ollama", model=model, cancelled=True,
                        )
                    if not line or not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue
                    if chunk.get("error"):
                        return LLMResponse(
                            provider="ollama", model=model,
                            error=f"Ollama: {chunk['error']}",
                        )
                    message = chunk.get("message") or {}
                    piece = message.get("content")
                    if piece:
                        content_parts.append(piece)
                        try:
                            on_token(piece)
                        except Exception:
                            pass
                    # Native tool calls arrive whole, not as argument fragments.
                    for call in message.get("tool_calls") or []:
                        raw_calls.append(call)
                    if chunk.get("done"):
                        usage = _ollama_usage(chunk)
                        break
        except Exception as exc:
            return LLMResponse(
                provider="ollama", model=model,
                error=f"Stream dropped mid-response: {exc}",
            )

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=self._parse_tool_calls(raw_calls),
            provider="ollama", model=model, usage=usage,
        )

    def _chat_openai_compat(
        self,
        base_url: str,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: Optional[List[Dict[str, Any]]],
        timeout: int,
        headers: Optional[Dict[str, str]] = None,
        provider_label: str = "lmstudio",
        on_token: Optional[Callable[[str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        usage_ok: bool = True,
    ) -> LLMResponse:
        streaming = on_token is not None
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if streaming:
            payload["stream"] = True
            # Ask for the token counts on the last chunk (token_ledger). Servers
            # that reject the key get one retry without it, below.
            if usage_ok:
                payload["stream_options"] = {"include_usage": True}

        try:
            res = self.session.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers or {},
                timeout=timeout,
                stream=streaming,
            )
        except Exception as exc:
            return LLMResponse(
                provider=provider_label, model=model,
                error=f"Could not reach the local model at {base_url}: {exc}",
            )

        if res.status_code != 200:
            body = res.text[:300]
            if streaming and usage_ok and res.status_code in (400, 422) and "stream_options" in body:
                return self._chat_openai_compat(
                    base_url, messages, model, temperature, max_tokens,
                    tools, timeout, headers, provider_label, on_token, should_cancel,
                    usage_ok=False,
                )
            # Some local servers reject the `tools` key outright; retry plain.
            if tools and res.status_code in (400, 422, 500):
                print(f"[HybridLLM] Server rejected native tools ({res.status_code}); retrying without.")
                return self._chat_openai_compat(
                    base_url, messages, model, temperature, max_tokens,
                    None, timeout, headers, provider_label, on_token, should_cancel,
                    usage_ok=usage_ok,
                )
            return LLMResponse(
                provider=provider_label, model=model,
                error=f"Model server returned {res.status_code}: {body}",
            )

        if streaming:
            return self._consume_stream(res, provider_label, model, on_token, should_cancel)

        try:
            body_json = res.json()
            choice = body_json["choices"][0]["message"]
        except Exception as exc:
            return LLMResponse(
                provider=provider_label, model=model,
                error=f"Malformed response from model server: {exc}",
            )
        calls = self._parse_tool_calls(choice.get("tool_calls") or [])
        only_thought = _thought_without_an_answer(choice.get("content"), choice, calls)
        if only_thought:
            print(f"[HybridLLM] {model} returned {only_thought} characters of reasoning "
                  "and no answer; escalating rather than showing nothing.")
            return LLMResponse(provider=provider_label, model=model, error=ONLY_THOUGHT)
        return LLMResponse(
            content=choice.get("content") or "",
            tool_calls=calls,
            provider=provider_label, model=body_json.get("model") or model,
            usage=_openai_usage(body_json),
        )

    def _consume_stream(
        self,
        res: requests.Response,
        provider_label: str,
        model: str,
        on_token: Callable[[str], None],
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> LLMResponse:
        """
        Accumulate an SSE completion stream into a normal LLMResponse, calling
        on_token for each content delta. A connection dropped mid-stream is
        reported as an error so chat() can retry the whole request on the next
        provider - the partial text is discarded rather than stitched.

        If should_cancel() turns True the stream is abandoned and a `cancelled`
        response is returned (barge-in) - not retried on another provider.
        """
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        usage: Dict[str, Any] = {}
        served_model = model
        # Tool-call fragments arrive keyed by index; the name lands once, the
        # arguments accumulate as string fragments.
        calls_acc: Dict[int, Dict[str, str]] = {}
        # Servers send SSE as UTF-8 without a charset; requests would otherwise
        # guess ISO-8859-1 for text/event-stream and turn every curly quote or
        # accent into mojibake on its way to the UI and the voice.
        res.encoding = "utf-8"
        try:
            with res:
                for line in res.iter_lines(decode_unicode=True):
                    if should_cancel and should_cancel():
                        res.close()
                        return LLMResponse(
                            content="".join(content_parts),
                            provider=provider_label, model=model, cancelled=True,
                        )
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except Exception:
                        continue
                    if isinstance(chunk, dict):
                        # The usage chunk (stream_options.include_usage) has no
                        # choices; claude-local-api also names the backend on it.
                        found = _openai_usage(chunk)
                        if found:
                            usage.update(found)
                        if chunk.get("model"):
                            served_model = chunk["model"]
                    try:
                        delta = chunk["choices"][0].get("delta", {})
                    except Exception:
                        continue
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        try:
                            on_token(piece)
                        except Exception:
                            pass
                    for key in _REASONING_KEYS:
                        # Never streamed to the UI: the user asked a question,
                        # not for the model's inner monologue.
                        if delta.get(key):
                            reasoning_parts.append(str(delta[key]))
                    for tc in delta.get("tool_calls") or []:
                        acc = calls_acc.setdefault(
                            tc.get("index", 0), {"id": "", "name": "", "args": ""}
                        )
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            acc["name"] = acc["name"] or fn["name"]
                        if fn.get("arguments"):
                            acc["args"] += fn["arguments"]
        except Exception as exc:
            return LLMResponse(
                provider=provider_label, model=model,
                error=f"Stream dropped mid-response: {exc}",
            )

        raw_calls = [
            {"id": acc["id"], "function": {"name": acc["name"], "arguments": acc["args"]}}
            for _, acc in sorted(calls_acc.items())
            if acc["name"]
        ]
        calls = self._parse_tool_calls(raw_calls)
        only_thought = _thought_without_an_answer(
            "".join(content_parts), {"reasoning": "".join(reasoning_parts)}, calls,
        )
        if only_thought:
            print(f"[HybridLLM] {model} streamed {only_thought} characters of reasoning "
                  "and no answer; escalating rather than showing nothing.")
            return LLMResponse(provider=provider_label, model=model, error=ONLY_THOUGHT)
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=calls,
            provider=provider_label, model=served_model or model,
            usage=usage,
        )

    @staticmethod
    def _parse_tool_calls(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []
        for tc in raw:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            if isinstance(raw_args, str):
                try:
                    parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                except Exception:
                    parsed_args = {}
            else:
                parsed_args = raw_args or {}
            calls.append({
                "id": tc.get("id") or f"call_{len(calls)}",
                "name": fn.get("name", ""),
                "arguments": parsed_args,
            })
        return calls

    def _chat_gemini(
        self, messages: List[Dict[str, Any]], model: str,
        temperature: float, max_tokens: int,
    ) -> LLMResponse:
        try:
            import google.generativeai as genai

            genai.configure(api_key=self.gemini_api_key)
            system = next((m["content"] for m in messages if m.get("role") == "system"), None)
            history = [m for m in messages if m.get("role") != "system"]
            prompt = "\n\n".join(
                f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in history
            )
            g_model = genai.GenerativeModel(model_name=model, system_instruction=system)
            response = g_model.generate_content(prompt)
            return LLMResponse(content=response.text, provider="gemini", model=model)
        except Exception as exc:
            return LLMResponse(provider="gemini", model=model, error=f"Gemini error: {exc}")

    # ───────────────────────── back-compat ─────────────────────────

    def generate(
        self,
        prompt: str,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        provider: str = "auto",
        model: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 512,
    ) -> str:
        """Single-shot text completion. Returns a friendly string on failure."""
        response = self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            provider=provider, model=model,
            temperature=temperature, max_tokens=max_tokens,
        )
        if not response.ok:
            print(f"[HybridLLM] {response.error}")
            return (
                "My local reasoning engine is offline. "
                "Please make sure LM Studio or Ollama is running."
            )
        return response.content


# ───────────────────────── JSON extraction helper ─────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Optional[Any]:
    """
    Pull the first JSON value out of a model reply.

    Small models wrap JSON in prose, in ``` fences, or emit it with trailing
    commentary. This tries fenced blocks first, then brace matching.
    """
    if not text:
        return None
    candidates: List[str] = []

    for match in _FENCE_RE.finditer(text):
        candidates.append(match.group(1).strip())

    stripped = text.strip()
    if stripped and stripped[0] in "{[":
        candidates.append(stripped)

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    candidates.append(text[start: idx + 1])
                    break

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


# Shared singleton - importing this avoids paying for duplicate health probes.
llm_router = HybridLLMRouter()
