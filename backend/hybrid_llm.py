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
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

DEFAULT_SYSTEM_PROMPT = (
    "You are Dexter, an intelligent personal AI assistant. "
    "Be concise, direct, and precise. Keep answers to 1-3 sentences "
    "unless detail is explicitly requested. Never use filler."
)

HEALTH_TTL_SECONDS = 30.0
_PROBE_TIMEOUT = 1.5


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    error: Optional[str] = None
    # Set when the answering provider is not the one first selected - the
    # request was completed by a fallback after the primary failed mid-flight.
    fallback_from: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class HybridLLMRouter:
    def __init__(self) -> None:
        self.lm_studio_url = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")

        # Explicit overrides; when unset the loaded model is auto-detected.
        self.lm_studio_model = os.getenv("LM_STUDIO_MODEL", "")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2")

        self.session = requests.Session()
        self._health_cache: Optional[Dict[str, Any]] = None
        self._health_checked_at = 0.0
        self._model_cache: Dict[str, str] = {}
        self._lock = threading.Lock()

    # ───────────────────────── health ─────────────────────────

    def _check_endpoint(self, url: str) -> bool:
        try:
            return self.session.get(url, timeout=_PROBE_TIMEOUT).status_code == 200
        except Exception:
            return False

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

        lm_online = self._check_endpoint(f"{self.lm_studio_url}/models")
        ollama_online = self._check_endpoint(f"{self.ollama_url}/models")
        result = {
            "lmStudio": {"online": lm_online, "url": self.lm_studio_url},
            "ollama": {"online": ollama_online, "url": self.ollama_url},
            "gemini": {"configured": bool(self.gemini_api_key)},
            "openai": {"configured": bool(self.openai_api_key)},
        }

        with self._lock:
            self._health_cache = result
            self._health_checked_at = time.time()
        return result

    def invalidate_health(self) -> None:
        with self._lock:
            self._health_cache = None

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

    def _model_for(self, provider: str, override: Optional[str]) -> str:
        if override:
            return override
        if provider == "lmstudio":
            return self.lm_studio_model or self._discover_model(self.lm_studio_url) or "local-model"
        if provider == "ollama":
            return self.ollama_model or self._discover_model(self.ollama_url) or "llama3.2"
        if provider == "gemini":
            return "gemini-1.5-pro"
        if provider == "openai":
            return "gpt-4o"
        return "local-model"

    def resolve_provider(self, provider: str = "auto") -> str:
        if provider and provider != "auto":
            return provider
        providers = self.get_available_providers()
        if providers["lmStudio"]["online"]:
            return "lmstudio"
        if providers["ollama"]["online"]:
            return "ollama"
        if providers["gemini"]["configured"]:
            return "gemini"
        if providers["openai"]["configured"]:
            return "openai"
        return "lmstudio"

    def _base_url_for(self, provider: str) -> str:
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
        if health["lmStudio"]["online"]:
            chain.append("lmstudio")
        if health["ollama"]["online"]:
            chain.append("ollama")
        if health["openai"]["configured"]:
            chain.append("openai")
        if health["gemini"]["configured"] and not needs_tools:
            chain.append("gemini")
        return chain or ["lmstudio"]

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
    ) -> LLMResponse:
        """
        Full multi-turn chat with optional native tool calling.

        `on_token` streams content deltas as they arrive (the request is made
        with stream=True); the returned LLMResponse still carries the complete
        content and parsed tool calls either way.

        `on_fallback(failed, next)` fires before each retry against the next
        provider in the chain, so callers can reset any tokens already shown.
        """
        chain = self._provider_chain(provider, needs_tools=bool(tools))
        last: Optional[LLMResponse] = None
        for idx, prov in enumerate(chain):
            if idx > 0:
                print(f"[HybridLLM] {chain[idx - 1]} failed mid-request; trying {prov}.")
                if on_fallback:
                    try:
                        on_fallback(chain[idx - 1], prov)
                    except Exception:
                        pass
            response = self._chat_one(
                prov, messages, model, temperature, max_tokens, tools, timeout, on_token
            )
            if response.ok:
                if idx > 0:
                    response.fallback_from = chain[0]
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
    ) -> LLMResponse:
        model_name = self._model_for(prov, model)

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
                provider_label="openai", on_token=on_token,
            )

        return self._chat_openai_compat(
            self._base_url_for(prov), messages, model_name, temperature,
            max_tokens, tools, timeout, provider_label=prov, on_token=on_token,
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
            # Some local servers reject the `tools` key outright; retry plain.
            if tools and res.status_code in (400, 422, 500):
                print(f"[HybridLLM] Server rejected native tools ({res.status_code}); retrying without.")
                return self._chat_openai_compat(
                    base_url, messages, model, temperature, max_tokens,
                    None, timeout, headers, provider_label, on_token,
                )
            return LLMResponse(
                provider=provider_label, model=model,
                error=f"Model server returned {res.status_code}: {body}",
            )

        if streaming:
            return self._consume_stream(res, provider_label, model, on_token)

        try:
            choice = res.json()["choices"][0]["message"]
        except Exception as exc:
            return LLMResponse(
                provider=provider_label, model=model,
                error=f"Malformed response from model server: {exc}",
            )
        return LLMResponse(
            content=choice.get("content") or "",
            tool_calls=self._parse_tool_calls(choice.get("tool_calls") or []),
            provider=provider_label, model=model,
        )

    def _consume_stream(
        self,
        res: requests.Response,
        provider_label: str,
        model: str,
        on_token: Callable[[str], None],
    ) -> LLMResponse:
        """
        Accumulate an SSE completion stream into a normal LLMResponse, calling
        on_token for each content delta. A connection dropped mid-stream is
        reported as an error so chat() can retry the whole request on the next
        provider - the partial text is discarded rather than stitched.
        """
        content_parts: List[str] = []
        # Tool-call fragments arrive keyed by index; the name lands once, the
        # arguments accumulate as string fragments.
        calls_acc: Dict[int, Dict[str, str]] = {}
        try:
            with res:
                for line in res.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get("delta", {})
                    except Exception:
                        continue
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        try:
                            on_token(piece)
                        except Exception:
                            pass
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
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=self._parse_tool_calls(raw_calls),
            provider=provider_label, model=model,
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
