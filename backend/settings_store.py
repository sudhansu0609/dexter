"""
Runtime settings - change how Dexter behaves without editing .env and restarting.

Every knob lived in .env and was read once at import: `BARGE_IN_ENABLED`,
`FOLLOWUP_WINDOW`, `FAST_MODEL`, quiet hours, and so on are module-level
constants. Turning barge-in on meant editing a file and restarting the app,
which in practice meant nobody ever tuned anything, which meant the defaults
were the product.

Three things happen when a setting changes:

1. it is persisted to data/settings.json, so it survives a restart;
2. the environment variable is updated, so anything reading it later agrees;
3. **the already-imported module constant is patched**, so it takes effect now.

Step 3 is the point. Without it a setting would appear to save and change
nothing until the next launch - worse than having no settings screen at all.

Only settings that are genuinely safe to change live are listed here. Anything
requiring a reconnect or a model reload stays in .env on purpose.
"""

import os
from typing import Any, Dict, List, Optional

import env_boot  # noqa: F401  - loads .env for every entry point
import jsonstore

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")


class Setting:
    """One tunable knob: where it comes from, and where it has to land."""

    def __init__(
        self,
        key: str,
        env: str,
        kind: str,
        default: Any,
        label: str,
        help_text: str = "",
        module: Optional[str] = None,
        attr: Optional[str] = None,
        options: Optional[List[str]] = None,
        group: str = "general",
    ) -> None:
        self.key = key
        self.env = env
        self.kind = kind  # bool | int | float | string | choice
        self.default = default
        self.label = label
        self.help_text = help_text
        self.module = module
        self.attr = attr
        self.options = options or []
        self.group = group


SETTINGS: List[Setting] = [
    Setting(
        "llmProvider", "DEXTER_LLM_PROVIDER", "choice", "auto",
        "Preferred model backend",
        "Which local server to prefer for 'auto' requests.",
        module="hybrid_llm", attr=None, options=["auto", "llamacpp", "ollama", "lmstudio"], group="model",
    ),
    Setting(
        "tieredLlm", "DEXTER_TIERED_LLM", "bool", True,
        "Fast model for simple turns",
        "Answer chitchat with the small model and escalate to the big one for "
        "anything needing tools.",
        module="router", attr="TIERED_ENABLED", group="model",
    ),
    Setting(
        "proactive", "DEXTER_PROACTIVE", "bool", True,
        "Proactive suggestions",
        "Let Dexter speak up about upcoming events, unread email, and overdue work.",
        module="proactive", attr=None, group="behaviour",
    ),
    Setting(
        "checkins", "DEXTER_CHECKINS", "bool", True,
        "Check-ins",
        "Let Dexter start the conversation: the morning plan, midday progress, the evening close-out.",
        module="companion", attr="ENABLED", group="behaviour",
    ),
    Setting(
        "quietHours", "DEXTER_QUIET_HOURS", "string", "",
        "Quiet hours",
        "When Dexter stays silent, e.g. 22:00-07:00.",
        module="proactive", attr=None, group="behaviour",
    ),
    Setting(
        "knowledgeRoots", "DEXTER_KNOWLEDGE_ROOTS", "string", "",
        "Knowledge folders",
        "Folders Dexter indexes, as tag=path;tag=path (tags: notes, docs, youtube, studio).",
        module="knowledge_ingest", attr="ROOTS_SPEC", group="knowledge",
    ),
    Setting(
        "closeOutHour", "DEXTER_CLOSEOUT_HOUR", "int", 21,
        "End-of-day check-in",
        "The hour Dexter asks you to close out the day. Takes effect on next start.",
        module=None, attr=None, group="behaviour",
    ),
]

_BY_KEY = {setting.key: setting for setting in SETTINGS}


def _coerce(setting: Setting, value: Any) -> Any:
    if setting.kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in ("0", "false", "no", "off", "")
    if setting.kind == "int":
        return int(value)
    if setting.kind == "float":
        return float(value)
    if setting.kind == "choice":
        candidate = str(value).strip().lower()
        return candidate if candidate in setting.options else setting.default
    return str(value)


def _from_env(setting: Setting) -> Any:
    raw = os.getenv(setting.env)
    if raw is None or raw == "":
        return setting.default
    try:
        return _coerce(setting, raw)
    except (TypeError, ValueError):
        return setting.default


def _stored() -> Dict[str, Any]:
    return jsonstore.load_json(SETTINGS_PATH, default={}) or {}


def current() -> Dict[str, Any]:
    """Effective values: stored overrides on top of .env, on top of defaults."""
    stored = _stored()
    values = {}
    for setting in SETTINGS:
        if setting.key in stored:
            try:
                values[setting.key] = _coerce(setting, stored[setting.key])
                continue
            except (TypeError, ValueError):
                pass
        values[setting.key] = _from_env(setting)
    return values


def describe() -> List[Dict[str, Any]]:
    """The schema the settings UI renders, with the effective values."""
    values = current()
    return [
        {
            "key": s.key,
            "label": s.label,
            "help": s.help_text,
            "kind": s.kind,
            "options": s.options,
            "group": s.group,
            "value": values[s.key],
            "default": s.default,
            # A restart-only setting must say so rather than appearing to work.
            "liveReload": bool(s.module and s.attr),
        }
        for s in SETTINGS
    ]


def _apply(setting: Setting, value: Any) -> bool:
    """Push a value into the running process. True if it took effect live."""
    # The environment first: anything reading it later must agree, and modules
    # that read os.getenv on each use pick it up immediately.
    os.environ[setting.env] = (
        ("1" if value else "0") if setting.kind == "bool" else str(value)
    )

    if not (setting.module and setting.attr):
        return False

    try:
        import importlib

        module = importlib.import_module(setting.module)
        setattr(module, setting.attr, value)
        return True
    except Exception as exc:
        print(f"[Settings] Could not apply {setting.key} live: {exc}")
        return False


def update(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Persist and apply a set of changes. Unknown keys are ignored."""
    stored = _stored()
    applied, needs_restart, rejected = [], [], []

    for key, raw in (changes or {}).items():
        setting = _BY_KEY.get(key)
        if not setting:
            rejected.append(key)
            continue
        try:
            value = _coerce(setting, raw)
        except (TypeError, ValueError):
            rejected.append(key)
            continue

        stored[key] = value
        if _apply(setting, value):
            applied.append(key)
        else:
            needs_restart.append(key)

    jsonstore.save_json(SETTINGS_PATH, stored)
    return {
        "ok": True,
        "applied": applied,
        "needsRestart": needs_restart,
        "rejected": rejected,
        "settings": describe(),
    }


def load_at_startup() -> int:
    """Re-apply stored settings over .env when the process starts."""
    stored = _stored()
    count = 0
    for key, value in stored.items():
        setting = _BY_KEY.get(key)
        if not setting:
            continue
        try:
            _apply(setting, _coerce(setting, value))
            count += 1
        except (TypeError, ValueError):
            continue
    return count
