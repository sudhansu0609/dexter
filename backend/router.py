"""
Three-tier intent router.

Dexter runs on a local model, which cannot be trusted to pick correctly from
thirty tools or to chain four calls reliably. So the language model is the last
resort, not the first:

    Tier 0  regex fast path      ~1ms    the handful of verbs said constantly
    Tier 1  semantic match       ~15ms   embedding vs each tool's example phrasings
    Tier 2  constrained agent    LLM     one tool per iteration, validated, repairable

Most commands never reach Tier 2. That is what makes this both faster and more
accurate than sending everything to the model.
"""

import math
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from hybrid_llm import extract_json, llm_router
from tool_registry import ToolResult, all_tools, execute, get_tool, schemas_for

# ───────────────────────── result type ─────────────────────────


@dataclass
class RouteResult:
    reply: str
    action: str = "chat"
    tier: int = 3
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    needs_confirmation: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "action": self.action,
            "tier": self.tier,
            "toolCalls": self.tool_calls,
            "details": self.data,
            "needsConfirmation": self.needs_confirmation,
        }


# ───────────────────────── Tier 0: fast path ─────────────────────────

_URLISH = re.compile(
    r"^(?:https?://|www\.)|(?:\.(?:com|org|net|io|dev|ai|co|gov|edu|tv|me|app)\b)",
    re.IGNORECASE,
)

_FILLER = re.compile(
    r"^\s*(?:please|hey|ok|okay|can you|could you|would you|i want you to|"
    r"i need you to|for me|go ahead and)\s+",
    re.IGNORECASE,
)

_TRAILING = re.compile(
    r"\s*(?:please|for me|right now|now|on my pc|on my computer|thanks|thank you)\s*[.!?]*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    out = text.strip()
    prev = None
    while prev != out:
        prev = out
        out = _FILLER.sub("", out)
        out = _TRAILING.sub("", out)
    return out.strip(" .!?,")


_REMINDER_LEAD = re.compile(
    r"^(?:remind me to|remind me|set a reminder to|set a reminder for|set a reminder|"
    r"ping me to|ping me about|ping me|wake me up|nudge me to|nudge me|"
    r"don'?t let me forget to|don'?t let me forget)\s*",
    re.IGNORECASE,
)


def _reminder_args(text: str) -> Optional[Dict[str, Any]]:
    """
    Split "remind me to check the render in 30 minutes" into text + when.

    Done deterministically rather than semantically because the reminder body is
    arbitrary user content - it dilutes any embedding, so Tier 1 scored these
    below threshold and they fell through to a very slow Tier 2.
    """
    from timeparse import parse_when, strip_time_phrases

    if not parse_when(text):
        return None
    without_time = strip_time_phrases(text)
    body = _REMINDER_LEAD.sub("", without_time).strip(" .!?,")
    if not body:
        # The whole phrase was the lead-in, e.g. "wake me up in 45 minutes".
        # Keep the verb and drop the pronoun so the reminder still reads right.
        body = re.sub(r"\bme\b", "", without_time, flags=re.I).strip(" .!?,")
        body = re.sub(r"\s+", " ", body)
    return {"text": body, "when": text} if body else None


# Ordered: first match wins. Each entry is (pattern, tool_name, arg_builder).
# An arg_builder returning None means "not actually this intent" - fall through.
_FAST_RULES: List[Tuple[re.Pattern, str, Callable[[re.Match], Optional[Dict[str, Any]]]]] = [
    (
        re.compile(
            r"^(?:remind me|set a reminder|ping me|nudge me|wake me up|don'?t let me forget)\b",
            re.I,
        ),
        "create_reminder",
        lambda m: _reminder_args(m.string),
    ),
    (
        re.compile(
            r"^(?:cancel|delete|remove|forget|drop)\s+(?:the\s+|my\s+|that\s+)?reminder"
            r"(?:\s+(?:about|for|to|regarding))?\s*(.*)$",
            re.I,
        ),
        "cancel_reminder",
        lambda m: {"text": m.group(1).strip(" .!?,")} if m.group(1).strip() else None,
    ),
    (
        re.compile(r"^(?:go to|navigate to|browse to|visit|open (?:the )?(?:website|url|site))\s+(.+)$", re.I),
        "open_website",
        lambda m: {"url": m.group(1).strip()},
    ),
    (
        re.compile(r"^(?:create|make|new)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory)\s+(?:called\s+|named\s+)?(.+)$", re.I),
        "create_folder",
        lambda m: {"path": m.group(1).strip().strip("'\"")},
    ),
    (
        re.compile(r"^(?:what(?:'s| is)(?: on)?(?: my)?(?: the)?\s+)?(?:my\s+)?(?:agenda|schedule|day plan|daily plan|today'?s? plan|plan for today)\s*$", re.I),
        "get_agenda",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:plan my day|what do i have (?:on )?today|what am i doing today|show (?:me )?my tasks|my tasks|what'?s? on today)\s*$", re.I),
        "get_agenda",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:show|list|read)\s+(?:me\s+)?my\s+notes\s*$", re.I),
        "list_notes",
        lambda m: {},
    ),
    (
        re.compile(r"^(?:run|execute|do|continue(?: with)?)\s+(?:the\s+)?next\s+step\s*$", re.I),
        "buzzcaf_execute_step",
        lambda m: {},
    ),
    # "remember X is Y" and "what do you remember about X" are close enough in
    # embedding space that Tier 1 confused storing with recalling. Split them
    # deterministically on the phrasing instead.
    (
        re.compile(r"^(?:what do you |do you )?remember\s+(?:about|what i told you about)\s+(.+)$", re.I),
        "recall_fact",
        lambda m: {"key": m.group(1).strip(" ?.")},
    ),
    (
        re.compile(
            r"^(?:remember|keep in mind|note|don'?t forget)\s+(?:that\s+)?(.+?)\s+(?:is|are|=|lives? (?:at|in)|will be)\s+(.+)$",
            re.I,
        ),
        "remember_fact",
        lambda m: {"key": m.group(1).strip(), "value": m.group(2).strip(" .")},
    ),
    (
        re.compile(r"^(?:open|launch|start|run|fire up|boot up|bring up)\s+(.+)$", re.I),
        "launch_app",
        lambda m: {"app_name": m.group(1).strip()},
    ),
]

# Words that follow "open/start/run" but never name an application.
_NOT_AN_APP = re.compile(
    r"^(?:a |an |the |my |new |another )?"
    r"(?:video|project|task|note|reminder|folder|file|step|workflow|meeting|"
    r"song|playlist|conversation|chat|over|again)\b",
    re.IGNORECASE,
)


# Greetings and pleasantries. Handled here rather than by the model for two
# reasons: they are said constantly and deserve an instant reply, and "how are
# you doing today" otherwise collides with the agenda tool on "doing/today".
_CHITCHAT: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:hi|hey|hello|yo|sup)\b.{0,15}$", re.I), "Hey. What do you need?"),
    (re.compile(r"^good (?:morning|afternoon|evening)\b.{0,15}$", re.I), "Good to see you. What's first?"),
    (re.compile(r"^how (?:are|r) (?:you|u)\b.{0,20}$", re.I), "Running fine and ready. What do you need?"),
    (re.compile(r"^(?:what'?s up|how'?s it going|you there|are you there)\b.{0,15}$", re.I), "I'm here. What do you need?"),
    (re.compile(r"^(?:thanks|thank you|cheers|nice one|perfect|great)\b.{0,15}$", re.I), "Anytime."),
    (re.compile(r"^(?:bye|goodbye|good night|see you|later)\b.{0,15}$", re.I), "Talk soon."),
    (re.compile(r"^(?:dexter|hey dexter)\s*[?!.]*$", re.I), "Yes? I'm listening."),
]


def chitchat(message: str) -> Optional[str]:
    text = message.strip()
    for pattern, reply in _CHITCHAT:
        if pattern.match(text):
            return reply
    return None


def fast_path(message: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Deterministic matches for the verbs said many times a day. No LLM, no embeddings."""
    text = _clean(message)
    if not text:
        return None

    for pattern, tool_name, build_args in _FAST_RULES:
        match = pattern.match(text)
        if not match:
            continue
        args = build_args(match)
        if args is None:
            # This rule matched but could not build usable args - a later rule
            # or tier may still handle the phrase, so keep going.
            continue

        # "open github.com" is a website, not an application.
        if tool_name == "launch_app":
            target = args.get("app_name", "")
            if _URLISH.search(target):
                return "open_website", {"url": target}
            # "start a video about X" is a Buzzcaf project, "run the next step"
            # is a workflow action - neither is an app. Let the later tiers decide.
            if _NOT_AN_APP.match(target):
                return None
            if len(target.split()) > 4:
                return None

        return tool_name, args

    return None


# ───────────────────────── Tier 1: semantic match ─────────────────────────


class _Embedder:
    """
    sentence-transformers when available, lexical fallback when not.

    The fallback matters: it keeps intent matching working on a bare install
    before `pip install sentence-transformers`, just with lower recall.
    """

    def __init__(self) -> None:
        self._model = None
        self._mode = "pending"
        self._lock = threading.Lock()
        self._idf: Dict[str, float] = {}

    def _ensure(self) -> None:
        if self._mode != "pending":
            return
        with self._lock:
            if self._mode != "pending":
                return
            try:
                # Shared instance: memory.py embeds against the same model, so
                # loading here must not create a second copy.
                import embeddings as shared_embeddings

                model = shared_embeddings.get_model()
                if model is None:
                    raise RuntimeError("shared embedding model unavailable")
                self._model = model
                self._mode = "neural"
                print(f"[Router] Semantic matching: {shared_embeddings.MODEL_NAME}")
            except Exception as exc:
                self._mode = "lexical"
                print(f"[Router] sentence-transformers unavailable ({exc}); using lexical matching.")

    @property
    def mode(self) -> str:
        self._ensure()
        return self._mode

    # Function words carry no intent signal, but they are the tokens most likely
    # to be shared between an unrelated question and a tool description. Left in,
    # "what is the capital of France" scored 0.49 against buzzcaf_status purely on
    # "what/is/the/of". Dropping them makes an unrelated query score ~0.
    _STOPWORDS = frozenset("""
        a an the this that these those my your our their its his her it they them
        i me we us you he she is are was were be been being am do does did doing
        have has had having will would shall should can could may might must
        of in on at to for with from by about into over under again further
        and or but if then else when while as so than too very just also
        what which who whom whose where why how there here
        please thanks thank ok okay hey yeah yep
    """.split())

    @classmethod
    def _tokens(cls, text: str) -> List[str]:
        # Bare numbers are argument values, never intent. Left in, the literal
        # "40" inside the example "set brightness to 40" is a rare, high-IDF
        # token that pulled "set volume to 40" over to set_brightness.
        return [
            tok for tok in re.findall(r"[a-z0-9]+", text.lower())
            if tok not in cls._STOPWORDS and len(tok) > 1 and not tok.isdigit()
        ]

    def fit_lexical(self, corpus: List[str]) -> None:
        doc_count = len(corpus) or 1
        seen = Counter()
        for phrase in corpus:
            for token in set(self._tokens(phrase)):
                seen[token] += 1
        self._idf = {
            token: math.log((doc_count + 1) / (count + 1)) + 1.0
            for token, count in seen.items()
        }

    def encode(self, texts: List[str]):
        self._ensure()
        if self._mode == "neural":
            return self._model.encode(texts, normalize_embeddings=True)  # type: ignore
        return [self._lexical_vector(t) for t in texts]

    def _lexical_vector(self, text: str) -> Dict[str, float]:
        tokens = self._tokens(text)
        if not tokens:
            return {}
        vec: Dict[str, float] = {}
        for token in tokens:
            vec[token] = vec.get(token, 0.0) + self._idf.get(token, 1.0)
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {k: v / norm for k, v in vec.items()}

    def similarity(self, query_vec, doc_vec) -> float:
        if self._mode == "neural":
            return float(sum(a * b for a, b in zip(query_vec, doc_vec)))
        if not query_vec or not doc_vec:
            return 0.0
        smaller, larger = (
            (query_vec, doc_vec) if len(query_vec) < len(doc_vec) else (doc_vec, query_vec)
        )
        return float(sum(weight * larger.get(token, 0.0) for token, weight in smaller.items()))


_embedder = _Embedder()

# example phrase -> tool name, plus the encoded matrix
_example_tools: List[str] = []
_example_vectors: List[Any] = []
_tool_vocab: Dict[str, set] = {}
_index_ready = False
_index_lock = threading.Lock()

# A short query shares one incidental word with a tool and scores high: "tell me
# a joke" hit recall_fact purely on "tell" (from "what did I tell you about..."),
# and "write a haiku" hit create_note on "write". Demanding two overlapping
# content words costs nothing and removes that whole class of false positive.
MIN_TOKEN_OVERLAP = 2

SEMANTIC_THRESHOLD = 0.62
LEXICAL_THRESHOLD = 0.55


def build_intent_index(force: bool = False) -> None:
    """Encode every tool's example phrasings once. Called at startup."""
    global _example_tools, _example_vectors, _index_ready

    with _index_lock:
        if _index_ready and not force:
            return

        phrases: List[str] = []
        owners: List[str] = []
        for spec in all_tools():
            for example in spec.examples:
                phrases.append(example)
                owners.append(spec.name)
            # The description carries signal the examples may miss.
            phrases.append(spec.description)
            owners.append(spec.name)

        if not phrases:
            _index_ready = True
            return

        # Vocabulary per tool, used to demand real overlap rather than a single
        # incidental shared word (see semantic_match).
        _tool_vocab.clear()
        for phrase, owner in zip(phrases, owners):
            _tool_vocab.setdefault(owner, set()).update(_Embedder._tokens(phrase))

        if _embedder.mode == "lexical":
            _embedder.fit_lexical(phrases)

        _example_vectors = list(_embedder.encode(phrases))
        _example_tools = owners
        _index_ready = True
        print(f"[Router] Intent index built: {len(phrases)} phrases over {len(all_tools())} tools.")


def rank_tools(message: str, top_k: int = 8) -> List[Tuple[str, float]]:
    """Best-scoring tools for a message, highest first. Used for Tier 1 and to
    shrink the tool list handed to the model in Tier 2."""
    build_intent_index()
    if not _example_vectors:
        return []

    query = _embedder.encode([message])[0]
    best: Dict[str, float] = {}
    for tool_name, vector in zip(_example_tools, _example_vectors):
        score = _embedder.similarity(query, vector)
        if score > best.get(tool_name, -1.0):
            best[tool_name] = score

    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:top_k]


def semantic_match(message: str) -> Optional[Tuple[str, float]]:
    ranked = rank_tools(message, top_k=3)
    if not ranked:
        return None
    threshold = SEMANTIC_THRESHOLD if _embedder.mode == "neural" else LEXICAL_THRESHOLD
    name, score = ranked[0]
    if score < threshold:
        return None
    # Require a margin over the runner-up, otherwise it is a coin flip and the
    # agent loop should decide instead.
    if len(ranked) > 1 and (score - ranked[1][1]) < 0.04:
        return None

    query_tokens = set(_Embedder._tokens(message))
    if len(query_tokens) >= MIN_TOKEN_OVERLAP:
        overlap = len(query_tokens & _tool_vocab.get(name, set()))
        if overlap < MIN_TOKEN_OVERLAP:
            return None

    return name, score


# ───────────────────────── slot extraction ─────────────────────────

_VERB_STRIP = re.compile(
    r"^(?:can you|could you|please|hey|ok|okay)?\s*"
    r"(?:go and |just )?"
    r"(?:open|launch|start|run|play|put on|find|search for|search|look for|locate|"
    r"create|make|add|write|take|jot|note|show|list|read|get|give me|tell me)\s+"
    r"(?:me\s+)?(?:a\s+|an\s+|the\s+|some\s+|my\s+)?",
    re.IGNORECASE,
)


# Spoken phrasings mapped onto media_control's enum. Without this, Tier 1 passed
# the whole utterance ("pause the music") as the enum value and validation
# rejected it.
_MEDIA_ACTIONS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:skip|next|forward|advance)\b", re.I), "next"),
    (re.compile(r"\b(?:previous|last|back|go back|rewind|replay)\b", re.I), "previous"),
    (re.compile(r"\b(?:pause|hold on|freeze)\b", re.I), "pause"),
    (re.compile(r"\b(?:resume|unpause|continue|carry on|keep playing)\b", re.I), "play"),
    (re.compile(r"\bstop\b", re.I), "stop"),
    (re.compile(r"\bplay\b", re.I), "play"),
]


_STEP = 10
_RE_LEVEL = re.compile(r"\b(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?\b|\b(\d{1,3})\s*(?:%|percent)\b", re.I)
_RE_UP = re.compile(r"\b(up|louder|higher|brighter|increase|raise|more)\b", re.I)
_RE_DOWN = re.compile(r"\b(down|quieter|lower|softer|dimmer|dim|decrease|reduce|less)\b", re.I)


def _level_slots(tool_name: str, text: str) -> Optional[Dict[str, Any]]:
    """Parse "set volume to 25", "turn it up", "mute" into the right arguments."""
    lowered = text.lower()

    if tool_name == "set_volume":
        if re.search(r"\bunmute\b|\bsound (?:back )?on\b", lowered):
            return {"mute": False}
        if re.search(r"\bmute\b|\bsilence\b|\bsound off\b|\bshut the sound\b", lowered):
            return {"mute": True}

    if re.search(r"\bmax(?:imum)?\b|\bfull\b|\ball the way up\b", lowered):
        return {"level": 100}
    if re.search(r"\bmin(?:imum)?\b|\ball the way down\b", lowered):
        return {"level": 0}

    match = _RE_LEVEL.search(lowered)
    if match:
        value = int(match.group(1) or match.group(2))
        if 0 <= value <= 100:
            return {"level": value}

    if _RE_UP.search(lowered):
        return {"change": _STEP}
    if _RE_DOWN.search(lowered):
        return {"change": -_STEP}

    # A bare "volume?" is a query - the tool reports the current level.
    return {}


def _enum_slot(spec, param: str, text: str) -> Optional[str]:
    """Pick an enum value that the utterance actually names."""
    options = spec.params[param].get("enum") or []
    lowered = text.lower()
    if param == "action" and spec.name == "media_control":
        for pattern, value in _MEDIA_ACTIONS:
            if pattern.search(lowered) and value in options:
                return value
    for option in options:
        if re.search(rf"\b{re.escape(option)}\b", lowered):
            return option
    return None


def _primary_param(spec) -> Optional[str]:
    required = spec.required
    if len(required) == 1:
        name = required[0]
        if spec.params[name].get("type") == "string":
            return name
    return None


def _extract_slots(tool_name: str, message: str) -> Optional[Dict[str, Any]]:
    """
    Fill a tool's arguments from the raw utterance without calling the model.
    Returns None when the arguments need real reasoning.
    """
    spec = get_tool(tool_name)
    if not spec:
        return None

    text = _clean(message)

    # Tool-specific handlers run before the "no required args" shortcut below,
    # because set_volume and set_brightness take only optional arguments yet
    # still need them parsed - otherwise "set volume to 25" would call the tool
    # with nothing and merely report the current level.
    if tool_name in ("set_volume", "set_brightness"):
        return _level_slots(tool_name, text)

    if not spec.required:
        # Nothing required means the tool is callable as-is; optional args can
        # stay at their defaults rather than forcing an LLM round trip.
        return {}

    if tool_name == "complete_task":
        title = re.sub(
            r"^(?:mark|check|tick)\s+(?:off\s+)?(?:the\s+|my\s+)?", "", text, flags=re.I
        )
        title = re.sub(
            r"\s+(?:as\s+)?(?:done|complete|completed|finished|off)\s*$", "", title, flags=re.I
        )
        title = re.sub(
            r"^(?:i\s+)?(?:have\s+|just\s+)?(?:finished|completed|did|done with)\s+(?:the\s+|my\s+)?",
            "", title, flags=re.I,
        )
        title = re.sub(r"\s+task\s*$", "", title, flags=re.I).strip(" .!?,")
        return {"title": title} if title else None

    if tool_name == "create_reminder":
        return _reminder_args(text)

    if tool_name == "cancel_reminder":
        body = re.sub(
            r"^(?:cancel|delete|remove|forget|drop)\s+(?:the\s+|my\s+|that\s+)?"
            r"reminder(?:\s+(?:about|for|to|regarding))?\s*",
            "", text, flags=re.I,
        ).strip(" .!?,")
        return {"text": body} if body else None

    if tool_name == "add_task":
        from timeparse import parse_when, strip_time_phrases

        title = strip_time_phrases(text)
        title = _VERB_STRIP.sub("", title).strip()
        if not title:
            return None
        args: Dict[str, Any] = {"title": title}
        if parse_when(text):
            args["when"] = text
        return args

    if tool_name == "create_note":
        body = re.sub(
            r"^(?:i\s+)?(?:need|want)\s+to\s+", "", text, flags=re.IGNORECASE
        )
        body = re.sub(
            r"^(?:take a note|make a (?:sticky )?note|write (?:this )?down|jot (?:this )?down|"
            r"capture this|note that|note|remember that)\s*(?:about|that|:)?\s*",
            "", body, flags=re.IGNORECASE,
        ).strip(" .!?,")
        return {"content": body} if body else None

    primary = _primary_param(spec)
    if not primary:
        return None

    if "enum" in spec.params[primary]:
        value = _enum_slot(spec, primary, text)
        return {primary: value} if value else None

    stripped = _VERB_STRIP.sub("", text).strip(" .!?,")
    if not stripped or len(stripped) > 120:
        return None
    return {primary: stripped}


# ───────────────────────── confirmation for risky tools ─────────────────────────

# Keyed by session so one user's stale "yes" can never fire another session's
# forgotten destructive call; entries expire so an answer given minutes later
# does not trigger an action the user no longer remembers being asked about.
_PENDING_TTL_SECONDS = 120
_pending: Dict[str, Dict[str, Any]] = {}
_AFFIRMATIVE = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|confirm|please do)\b", re.I)
_NEGATIVE = re.compile(r"^\s*(no|nope|cancel|stop|don'?t|nevermind|never mind)\b", re.I)


def _remember_pending(tool_name: str, args: Dict[str, Any], session: str = "default") -> None:
    _pending[session] = {"tool": tool_name, "args": args, "ts": time.time()}


def _has_pending(session: str = "default") -> bool:
    entry = _pending.get(session)
    if not entry:
        return False
    if time.time() - entry["ts"] > _PENDING_TTL_SECONDS:
        _pending.pop(session, None)
        return False
    return True


def _take_pending(session: str = "default") -> Optional[Tuple[str, Dict[str, Any]]]:
    entry = _pending.pop(session, None)
    if not entry or time.time() - entry["ts"] > _PENDING_TTL_SECONDS:
        return None
    return entry["tool"], entry.get("args", {})


def _describe_call(tool_name: str, args: Dict[str, Any]) -> str:
    if not args:
        return tool_name.replace("_", " ")
    rendered = ", ".join(f"{k}={str(v)[:60]}" for k, v in args.items())
    return f"{tool_name.replace('_', ' ')} ({rendered})"


# Observers notified after each executed tool call: (session, tool, args, ok).
# Macro recording hooks in here; observers must never raise into the turn.
TOOL_OBSERVERS: List[Callable[[str, str, Dict[str, Any], bool], None]] = []


def _run_tool(
    tool_name: str,
    args: Dict[str, Any],
    confirmed: bool = False,
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
) -> Tuple[ToolResult, bool]:
    """Execute, unless the tool is marked confirm and the user has not agreed yet."""
    spec = get_tool(tool_name)
    if spec and spec.confirm and not confirmed:
        _remember_pending(tool_name, args, session=session)
        return (
            ToolResult(
                ok=True,
                summary=f"Just to confirm - you want me to {_describe_call(tool_name, args)}?",
                data={"pending": True},
            ),
            True,
        )

    if on_event:
        on_event("tool_start", {"name": tool_name, "args": args})
    result = execute(tool_name, args)
    if on_event:
        on_event("tool_result", {"name": tool_name, "ok": result.ok, "summary": result.summary})
    for observer in TOOL_OBSERVERS:
        try:
            observer(session, tool_name, args, result.ok)
        except Exception:
            pass
    return result, False


def _speech_hint(result: ToolResult) -> Dict[str, Any]:
    """Let a tool ask for a particular delivery, e.g. read_aloud wants narration."""
    emotion = result.data.get("_speech_emotion") if isinstance(result.data, dict) else None
    return {"speechEmotion": emotion} if emotion else {}


# ───────────────────────── Tier 2: constrained agent loop ─────────────────────────

MAX_ITERATIONS = 5
TOOLS_PER_CALL = 8

AGENT_SYSTEM_PROMPT = """You are Dexter, a personal AI assistant running on the user's Windows PC.

You can act on the computer by calling tools. Rules:
- Call ONE tool at a time. After you see its result, decide the next single action.
- Use a tool only when the request needs real action or live information.
- For general questions, conversation or knowledge, just answer directly. Do not call a tool.
- When the task is done, reply in 1-2 short sentences describing what happened.
- Never invent file paths or results. If a tool fails, say so plainly.

Your replies are spoken aloud, so keep them short and natural."""

JSON_PROTOCOL_HINT = """If you cannot use the native tool-calling format, respond with a single JSON object instead:
{"tool": "tool_name", "args": {...}}
or, if no tool is needed:
{"answer": "your reply"}
Respond with JSON only, no other text."""


def _parse_text_tool_call(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Recover a tool call that the model wrote as JSON text instead of using
    the native protocol. Common with small local models."""
    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        return None
    name = parsed.get("tool") or parsed.get("name") or parsed.get("function")
    if not name or not isinstance(name, str):
        return None
    args = parsed.get("args") or parsed.get("arguments") or parsed.get("parameters") or {}
    if not isinstance(args, dict):
        args = {}
    return name, args


def _parse_text_answer(text: str) -> Optional[str]:
    parsed = extract_json(text)
    if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
        return parsed["answer"]
    return None


class _TokenGate:
    """
    Forward LLM content deltas to the event stream - unless the reply turns out
    to be a text-protocol tool call. A small model may answer with raw JSON
    ({"tool": ...} or {"answer": ...}); streaming that to the UI and voice
    would leak protocol, so emission is deferred until the first non-space
    character shows the reply is prose.
    """

    def __init__(self, on_event: Callable[[str, Dict[str, Any]], None]) -> None:
        self._on_event = on_event
        self.reset()

    def reset(self) -> None:
        self._buffer = ""
        self._decided: Optional[bool] = None  # None undecided, True prose, False JSON

    def feed(self, piece: str) -> None:
        if self._decided is False:
            return
        if self._decided is True:
            self._on_event("token", {"text": piece})
            return
        self._buffer += piece
        head = self._buffer.lstrip()
        if not head:
            return
        if head[0] in "{[`":
            self._decided = False
            return
        self._decided = True
        self._on_event("token", {"text": self._buffer})


def agent_loop(
    message: str,
    history: Optional[List[Dict[str, str]]] = None,
    provider: str = "auto",
    model: Optional[str] = None,
    context_note: str = "",
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
) -> RouteResult:
    """
    ReAct-style loop, deliberately constrained for small local models:
    one tool per iteration, only the most relevant tools in context, and a
    single repair retry on invalid arguments.
    """
    candidate_names = [name for name, _ in rank_tools(message, top_k=TOOLS_PER_CALL)]
    if not candidate_names:
        candidate_names = [spec.name for spec in all_tools()][:TOOLS_PER_CALL]
    tool_schemas = schemas_for(candidate_names)

    system = AGENT_SYSTEM_PROMPT
    if context_note:
        system += f"\n\nContext you may use:\n{context_note}"

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in (history or [])[-8:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    executed: List[Dict[str, Any]] = []
    last_result: Optional[ToolResult] = None
    repair_used = False

    gate = _TokenGate(on_event) if on_event else None

    def _on_fallback(failed: str, next_provider: str) -> None:
        # Tokens already shown came from a stream that died; the retry starts
        # the reply over, so the UI must clear what it has.
        if gate:
            gate.reset()
        if on_event:
            on_event("provider_switched", {"from": failed, "to": next_provider})

    for iteration in range(MAX_ITERATIONS):
        if gate:
            gate.reset()
        response = llm_router.chat(
            messages,
            provider=provider,
            model=model,
            temperature=0.1 if tool_schemas else 0.5,
            max_tokens=1024,
            tools=tool_schemas or None,
            on_token=gate.feed if gate else None,
            on_fallback=_on_fallback,
        )

        if not response.ok:
            if executed:
                return RouteResult(
                    reply=last_result.summary if last_result else "Done.",
                    action="agent", tier=2, tool_calls=executed,
                    data={"warning": response.error},
                )
            return RouteResult(
                reply=(
                    "My local reasoning engine is offline. "
                    "Please make sure LM Studio or Ollama is running."
                ),
                action="llm_offline", tier=2,
                data={"error": response.error},
            )

        # Native tool call, or a JSON one recovered from the text body.
        call: Optional[Tuple[str, Dict[str, Any]]] = None
        if response.tool_calls:
            first = response.tool_calls[0]
            call = (first["name"], first["arguments"])
        elif response.content:
            call = _parse_text_tool_call(response.content)

        if not call:
            answer = _parse_text_answer(response.content) or response.content
            answer = (answer or "").strip()
            if not answer and last_result:
                answer = last_result.summary
            return RouteResult(
                reply=answer or "Done.",
                action="agent" if executed else "chat",
                tier=2,
                tool_calls=executed,
                data={"iterations": iteration + 1},
            )

        tool_name, tool_args = call
        result, awaiting = _run_tool(tool_name, tool_args, on_event=on_event, session=session)
        last_result = result

        if awaiting:
            return RouteResult(
                reply=result.summary, action="confirm", tier=2,
                tool_calls=executed, data=result.data, needs_confirmation=True,
            )

        executed.append({
            "name": tool_name,
            "args": tool_args,
            "ok": result.ok,
            "summary": result.summary,
            **_speech_hint(result),
        })

        # One repair attempt on a validation failure, then move on.
        if not result.ok and not repair_used and "argument" in result.summary.lower():
            repair_used = True
            messages.append({"role": "assistant", "content": f"I called {tool_name}."})
            messages.append({
                "role": "user",
                "content": f"That call was invalid: {result.summary} Try again with correct arguments.",
            })
            continue

        messages.append({
            "role": "assistant",
            "content": f"[called {tool_name}]",
        })
        messages.append({
            "role": "user",
            "content": (
                f"Result of {tool_name}: {result.summary}\n\n"
                "If the user's request is now complete, reply to them directly in "
                "1-2 short sentences. Otherwise call the next tool."
            ),
        })

    # Hit the iteration cap - report honest partial progress.
    summary = last_result.summary if last_result else "I couldn't complete that."
    return RouteResult(
        reply=f"{summary} (I stopped after {MAX_ITERATIONS} steps.)",
        action="agent_max_steps", tier=2, tool_calls=executed,
        data={"iterations": MAX_ITERATIONS},
    )


# ───────────────────────── entry point ─────────────────────────


def route(
    message: str,
    history: Optional[List[Dict[str, str]]] = None,
    provider: str = "auto",
    model: Optional[str] = None,
    context_note: str = "",
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    session: str = "default",
) -> RouteResult:
    """Route a user message through the three tiers and return a reply."""
    text = (message or "").strip()
    if not text:
        return RouteResult(reply="I didn't catch that.", action="empty", tier=0)

    # Resolve an outstanding confirmation before anything else.
    if _has_pending(session):
        if _AFFIRMATIVE.match(text):
            pair = _take_pending(session)
            if pair:
                result = execute(pair[0], pair[1])
                return RouteResult(
                    reply=result.summary, action="confirmed", tier=0,
                    tool_calls=[{"name": pair[0], "args": pair[1], "ok": result.ok,
                                 "summary": result.summary}],
                    data=result.data,
                )
        elif _NEGATIVE.match(text):
            _take_pending(session)
            return RouteResult(reply="Cancelled.", action="cancelled", tier=0)

    # ── Tier 0 ──
    small_talk = chitchat(text)
    if small_talk:
        return RouteResult(reply=small_talk, action="chitchat", tier=0)

    hit = fast_path(text)
    if hit:
        tool_name, args = hit
        result, awaiting = _run_tool(tool_name, args, on_event=on_event, session=session)
        return RouteResult(
            reply=result.summary,
            action="confirm" if awaiting else "fast_path",
            tier=0,
            tool_calls=[] if awaiting else [
                {"name": tool_name, "args": args, "ok": result.ok, "summary": result.summary}
            ],
            data={**result.data, **_speech_hint(result)},
            needs_confirmation=awaiting,
        )

    # ── Tier 1 ──
    match = semantic_match(text)
    if match:
        tool_name, score = match
        args = _extract_slots(tool_name, text)
        if args is not None:
            result, awaiting = _run_tool(tool_name, args, on_event=on_event, session=session)
            return RouteResult(
                reply=result.summary,
                action="confirm" if awaiting else "semantic",
                tier=1,
                tool_calls=[] if awaiting else [
                    {"name": tool_name, "args": args, "ok": result.ok, "summary": result.summary}
                ],
                data={**result.data, "score": round(score, 3), **_speech_hint(result)},
                needs_confirmation=awaiting,
            )

    # ── Tier 2 ──
    return agent_loop(text, history=history, provider=provider, model=model,
                      context_note=context_note, on_event=on_event, session=session)
