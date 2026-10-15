"""
Dexter Tool Registry
====================

Single source of truth for every capability Dexter has.

One `@tool(...)` declaration produces all three things the router needs:

  1. An OpenAI-compatible function schema  -> Tier 2 (LLM tool calling)
  2. A corpus of example phrasings         -> Tier 1 (semantic intent match)
  3. A validated, uniformly-wrapped callable -> execution

Tools return a `ToolResult`. The `summary` field is what gets fed back to the
language model, deliberately kept short: a small local model drowns if you hand
it a raw JSON blob, so the tool itself decides what is worth saying.
"""

import contextvars
import json
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ───────────────────────── Result type ─────────────────────────


@dataclass
class ToolResult:
    """Uniform return value for every Dexter tool."""

    ok: bool
    summary: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "summary": self.summary, "data": self.data}

    @classmethod
    def fail(cls, message: str, **data: Any) -> "ToolResult":
        return cls(ok=False, summary=message, data=data)

    @classmethod
    def success(cls, message: str, **data: Any) -> "ToolResult":
        return cls(ok=True, summary=message, data=data)

    @classmethod
    def from_legacy(cls, raw: Dict[str, Any], fallback: str = "Done.") -> "ToolResult":
        """Adapt the older {"success": bool, "message"/"error": str, ...} dicts."""
        if not isinstance(raw, dict):
            return cls(ok=True, summary=str(raw)[:300], data={"raw": raw})
        ok = bool(raw.get("success", True))
        summary = raw.get("message") or raw.get("error") or raw.get("status") or fallback
        return cls(ok=ok, summary=str(summary), data=raw)


# ───────────────────────── Tool spec ─────────────────────────

_JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object"}


@dataclass
class ToolSpec:
    name: str
    description: str
    params: Dict[str, Dict[str, Any]]
    examples: List[str]
    confirm: bool
    category: str
    fn: Callable[..., Any]
    #: Confirmation that depends on the *state*, not the tool. "Stop BuzzFin"
    #: should just happen; "stop VrDeep" while it is three hours into a render
    #: must be asked about. A flat `confirm=True` cannot tell those apart, and a
    #: tool that asks every time is a tool people learn to say yes to blindly.
    confirm_if: Optional[Callable[[Dict[str, Any]], bool]] = None

    def needs_confirmation(self, args: Dict[str, Any]) -> bool:
        if self.confirm:
            return True
        if not self.confirm_if:
            return False
        try:
            return bool(self.confirm_if(args or {}))
        except Exception as exc:
            # A broken predicate must fail *safe*: ask.
            print(f"[ToolRegistry] confirm_if for '{self.name}' raised: {exc}")
            return True

    def confirmation_prompt(self, args: Dict[str, Any]) -> str:
        """Let a tool word its own question ("VrDeep has 3 jobs queued — stop it?")."""
        builder = getattr(self.fn, "_dexter_confirm_prompt", None)
        if builder:
            try:
                text = builder(args or {})
                if text:
                    return str(text)
            except Exception:
                pass
        return ""

    @property
    def required(self) -> List[str]:
        return [k for k, v in self.params.items() if v.get("required", True)]

    def openai_schema(self) -> Dict[str, Any]:
        """The function schema sent to LM Studio / Ollama / any OpenAI-compatible API."""
        properties: Dict[str, Any] = {}
        for pname, pmeta in self.params.items():
            prop = {
                "type": pmeta.get("type", "string"),
                "description": pmeta.get("description", ""),
            }
            if "enum" in pmeta:
                prop["enum"] = pmeta["enum"]
            if pmeta.get("type") == "array":
                prop["items"] = pmeta.get("items", {"type": "string"})
            properties[pname] = prop

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": self.required,
                },
            },
        }

    def signature_hint(self) -> str:
        """Compact human/LLM readable signature, used in prompts and error repair."""
        parts = []
        for pname, pmeta in self.params.items():
            suffix = "" if pmeta.get("required", True) else "?"
            parts.append(f"{pname}{suffix}: {pmeta.get('type', 'string')}")
        return f"{self.name}({', '.join(parts)})"


# ───────────────────────── Registry ─────────────────────────

REGISTRY: Dict[str, ToolSpec] = {}


def tool(
    name: str,
    description: str,
    params: Optional[Dict[str, Dict[str, Any]]] = None,
    examples: Optional[List[str]] = None,
    confirm: bool = False,
    category: str = "general",
    confirm_if: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Callable:
    """
    Register a function as a Dexter tool.

    `params`   JSON-schema-ish per argument: {"type", "description", "required", "enum"}
    `examples` natural phrasings a user might say. These are embedded at startup and
               are what makes Tier 1 work without touching the LLM, so write them the
               way you would actually speak, not the way you would write a doc.
    `confirm`  True for destructive or outward-facing actions (send_email, delete_file).
    """

    def decorator(fn: Callable) -> Callable:
        spec = ToolSpec(
            name=name,
            description=description,
            params=params or {},
            examples=examples or [],
            confirm=confirm,
            category=category,
            fn=fn,
            confirm_if=confirm_if,
        )
        for pname, pmeta in spec.params.items():
            ptype = pmeta.get("type", "string")
            if ptype not in _JSON_TYPES:
                raise ValueError(
                    f"Tool '{name}' param '{pname}' has invalid JSON type '{ptype}'"
                )
        if name in REGISTRY:
            raise ValueError(f"Duplicate tool name registered: '{name}'")
        REGISTRY[name] = spec
        fn._dexter_tool = spec  # type: ignore[attr-defined]
        return fn

    return decorator


def get_tool(name: str) -> Optional[ToolSpec]:
    return REGISTRY.get(name)


def all_tools() -> List[ToolSpec]:
    return list(REGISTRY.values())


def tool_names() -> List[str]:
    return list(REGISTRY.keys())


def schemas_for(names: List[str]) -> List[Dict[str, Any]]:
    """OpenAI tool schemas for a subset of tools (the router sends only the top-N)."""
    return [REGISTRY[n].openai_schema() for n in names if n in REGISTRY]


# ───────────────────────── Validation ─────────────────────────


def _coerce(value: Any, target: str) -> Any:
    """Small local models emit "3" for integers and "true" for booleans. Be forgiving."""
    if target == "string":
        return value if isinstance(value, str) else str(value)
    if target == "integer":
        return int(float(value)) if not isinstance(value, bool) else int(value)
    if target == "number":
        return float(value)
    if target == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "yes", "1", "on")
    if target == "array":
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
            return [v.strip() for v in value.split(",") if v.strip()]
        return [value]
    if target == "object":
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return json.loads(value)
    return value


def validate_args(
    spec: ToolSpec, args: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], str]:
    """
    Returns (ok, cleaned_args, error_message).

    The error message is written to be fed straight back to the model as a repair
    prompt, so it names the offending argument and the expected signature.
    """
    if not isinstance(args, dict):
        return False, {}, f"Arguments must be a JSON object. Expected {spec.signature_hint()}"

    cleaned: Dict[str, Any] = {}
    for pname, pmeta in spec.params.items():
        required = pmeta.get("required", True)
        if pname not in args or args[pname] is None:
            if required:
                return (
                    False,
                    {},
                    f"Missing required argument '{pname}'. Expected {spec.signature_hint()}",
                )
            if "default" in pmeta:
                cleaned[pname] = pmeta["default"]
            continue

        try:
            value = _coerce(args[pname], pmeta.get("type", "string"))
        except Exception:
            return (
                False,
                {},
                f"Argument '{pname}' must be a {pmeta.get('type', 'string')}. "
                f"Got {args[pname]!r}. Expected {spec.signature_hint()}",
            )

        if "enum" in pmeta and value not in pmeta["enum"]:
            return (
                False,
                {},
                f"Argument '{pname}' must be one of {pmeta['enum']}. Got {value!r}.",
            )
        cleaned[pname] = value

    unknown = set(args) - set(spec.params)
    if unknown:
        # Drop rather than reject: hallucinated extra keys are common and harmless.
        print(f"[ToolRegistry] Dropping unknown args for {spec.name}: {sorted(unknown)}")

    return True, cleaned, ""


# ───────────────────────── Execution ─────────────────────────


# ───────────────────────── which conversation is this? ─────────────────────────
#
# A few tools belong to a conversation rather than to the machine: dictation is
# per session (a note dictated in the pop-up is not the one being dictated in the
# full window), and so is anything that has to be stopped by the same person who
# started it. Passing a session id as a tool *parameter* would put it in the
# schema the model sees and invite it to invent one, so it travels beside the
# call instead. `_run_tool` sets it; a tool asks for it with `session_now()`.
_session_now = contextvars.ContextVar("dexter_tool_session", default="default")


def set_session(session: str) -> Any:
    """Bind the session for the tool about to run. Returns a reset token."""
    return _session_now.set(str(session or "default"))


def reset_session(token: Any) -> None:
    try:
        _session_now.reset(token)
    except Exception:
        pass


def session_now() -> str:
    return _session_now.get()


def execute(name: str, args: Optional[Dict[str, Any]] = None) -> ToolResult:
    """
    Validate and run a tool. Never raises - a failed tool is a result the agent
    loop can reason about and recover from, not an exception that kills the turn.
    """
    args = args or {}
    spec = REGISTRY.get(name)
    if not spec:
        close = [n for n in REGISTRY if name.lower() in n or n in name.lower()]
        hint = f" Did you mean: {', '.join(close[:3])}?" if close else ""
        return ToolResult.fail(f"No such tool '{name}'.{hint}")

    ok, cleaned, err = validate_args(spec, args)
    if not ok:
        return ToolResult.fail(err)

    started = time.perf_counter()
    try:
        raw = spec.fn(**cleaned)
    except Exception as exc:
        print(f"[ToolRegistry] '{name}' raised: {exc}")
        traceback.print_exc()
        return ToolResult.fail(f"Tool '{name}' failed: {exc}")

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if isinstance(raw, ToolResult):
        result = raw
    elif isinstance(raw, dict):
        result = ToolResult.from_legacy(raw)
    else:
        result = ToolResult.success(str(raw)[:500] if raw is not None else "Done.")

    result.data.setdefault("_tool", name)
    result.data.setdefault("_elapsed_ms", elapsed_ms)
    return result
