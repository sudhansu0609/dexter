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
from typing import Any, Dict, Iterator, List, Optional

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
    ) -> LLMResponse:
        """Full multi-turn chat with optional native tool calling."""
        resolved = self.resolve_provider(provider)
        model_name = self._model_for(resolved, model)

        if resolved == "gemini" and self.gemini_api_key:
            return self._chat_gemini(messages, model_name, temperature, max_tokens)

        if resolved == "openai" and self.openai_api_key:
            return self._chat_openai_compat(
                "https://api.openai.com/v1", messages, model_name, temperature,
                max_tokens, tools, timeout,
                headers={"Authorization": f"Bearer {self.openai_api_key}"},
                provider_label="openai",
            )

        return self._chat_openai_compat(
            self._base_url_for(resolved), messages, model_name, temperature,
            max_tokens, tools, timeout, provider_label=resolved,
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
    ) -> LLMResponse:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            res = self.session.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers or {},
                timeout=timeout,
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
                    None, timeout, headers, provider_label,
                )
            return LLMResponse(
                provider=provider_label, model=model,
                error=f"Model server returned {res.status_code}: {body}",
            )

        try:
            choice = res.json()["choices"][0]["message"]
        except Exception as exc:
            return LLMResponse(
                provider=provider_label, model=model,
                error=f"Malformed response from model server: {exc}",
            )

        content = choice.get("content") or ""
        calls: List[Dict[str, Any]] = []
        for tc in choice.get("tool_calls") or []:
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
                "id": tc.get("id", f"call_{len(calls)}"),
                "name": fn.get("name", ""),
                "arguments": parsed_args,
            })

        return LLMResponse(
            content=content, tool_calls=calls,
            provider=provider_label, model=model,
        )

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

    # ───────────────────────── streaming ─────────────────────────

    def stream(
        self,
        messages: List[Dict[str, Any]],
        provider: str = "auto",
        model: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 768,
        timeout: int = 90,
    ) -> Iterator[str]:
        """
        Yield content deltas as they arrive. Falls back to yielding the whole
        reply in one chunk for providers without a streaming path.
        """
        resolved = self.resolve_provider(provider)
        model_name = self._model_for(resolved, model)

        if resolved in ("gemini",) or (resolved == "openai" and not self.openai_api_key):
            response = self.chat(messages, provider, model, temperature, max_tokens)
            yield response.content if response.ok else (response.error or "")
            return

        base_url = (
            "https://api.openai.com/v1" if resolved == "openai"
            else self._base_url_for(resolved)
        )
        headers = (
            {"Authorization": f"Bearer {self.openai_api_key}"}
            if resolved == "openai" else {}
        )

        try:
            with self.session.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                },
                headers=headers,
                timeout=timeout,
                stream=True,
            ) as res:
                if res.status_code != 200:
                    yield f"[Model server error {res.status_code}]"
                    return
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
                        yield piece
        except Exception as exc:
            yield f"[Streaming failed: {exc}]"

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
