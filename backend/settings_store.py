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
        "llmProvider", "DEXTER_LLM_PROVIDER", "choice", "lmstudio",
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
    # v11.1: which local models the tiers use, from the list the servers
    # actually have (pulled Ollama tags, LM Studio ids). The owner asked to be
    # able to change the Ollama model without editing .env.
    Setting(
        "fastModel", "DEXTER_FAST_MODEL", "model", "qwen3.5:4b",
        "Fast model (chat and quick actions)",
        "The small model that answers simple turns instantly and, since v13, "
        "takes single actions itself before anything escalates. Blank turns the "
        "fast tier off. Takes effect on the next message.",
        module="router", attr="FAST_MODEL", group="model",
    ),
    # v13, D1: the fast model is allowed one tool call of its own. Off restores
    # v12 behaviour, where every instruction went to the big model.
    Setting(
        "fastAct", "DEXTER_FAST_ACT", "bool", True,
        "Let the fast model act",
        "One tool call on the small model before escalating, so most commands "
        "finish in about a second without loading the big one. Off sends every "
        "instruction to the smart model, as before.",
        module="router", attr="FAST_ACT_ENABLED", group="model",
    ),
    Setting(
        "smartModel", "DEXTER_SMART_MODEL", "model", "gemma-4-12b",
        "Smart model (actions)",
        "The model that gets the tools. Blank means the backend's own default. "
        "Takes effect on the next message.",
        module="router", attr="SMART_MODEL", group="model",
    ),
    # v12: the second rung. Dexter asks Sentinel for the smart model first and
    # takes this one when the card is too full for it.
    Setting(
        "smartModelFallback", "DEXTER_SMART_MODEL_FALLBACK", "model", "gemma-4-12b",
        "Smart model (fallback)",
        "The smaller model an escalated turn falls back to when Sentinel won't "
        "give the smart model room — so a busy GPU costs you a rung, not the "
        "answer. Blank means no second rung.",
        module="router", attr="SMART_MODEL_FALLBACK", group="model",
    ),
    # v13.2: what happens to the model Dexter is no longer using.
    Setting(
        "offloadPrevious", "DEXTER_OFFLOAD_PREVIOUS", "choice", "rungs",
        "Offload the model you switched away from",
        "'rungs' (default): a model that has been replaced - a superseded ladder "
        "rung, or a tier you changed here - is unloaded at once, and the big model "
        "goes immediately when memory is tight. The small fast model stays, because "
        "every chat turn needs it. 'always': only the model that just answered is "
        "kept in memory, which saves the most and makes the next switch pay for a "
        "reload. 'off': models leave only when their keep-alive expires, as in v13.",
        module=None, attr=None, options=["rungs", "always", "off"], group="model",
    ),
    # ── voice (v10) ──
    Setting(
        "bargeIn", "DEXTER_VOICE_BARGE_IN", "bool", False,
        "Interrupt by talking over Dexter",
        "Off by default: through speakers the mic hears Dexter's own voice and it "
        "interrupts itself. Turn on with headphones. Pressing the mic or saying "
        "'stop' after it finishes always works.",
        module="voice_pipeline", attr="BARGE_IN_ENABLED", group="voice",
    ),
    Setting(
        "followUpWindow", "DEXTER_FOLLOWUP_WINDOW", "float", 6.0,
        "Follow-up window (seconds)",
        "How long after Dexter speaks you can reply without the wake word. Longer "
        "means more of the room's conversation lands in it; since v13 an "
        "unaddressed sentence in the window still has to look like a command.",
        module="voice_pipeline", attr="FOLLOWUP_WINDOW", group="voice",
    ),
    # ── v13: what it takes for an unaddressed sentence to count ──
    Setting(
        "voiceMinWords", "DEXTER_VOICE_MIN_WORDS", "int", 2,
        "Shortest unaddressed command (words)",
        "Inside the follow-up window (or open mic), a sentence that does not start "
        "with Dexter's name needs at least this many words. Answers like 'yes' and "
        "'go ahead' are always allowed.",
        module="voice_pipeline", attr="MIN_WORDS_UNADDRESSED", group="voice",
    ),
    Setting(
        "voiceMinConfidence", "DEXTER_VOICE_MIN_CONFIDENCE", "float", 0.45,
        "Confidence for an unaddressed command",
        "How sure the recogniser has to be about a sentence nobody addressed to "
        "Dexter. Raise it if the television keeps getting through.",
        module="voice_pipeline", attr="MIN_CONFIDENCE_UNADDRESSED", group="voice",
    ),
    Setting(
        "followupMisses", "DEXTER_VOICE_FOLLOWUP_MISSES", "int", 2,
        "Give up after this many empty windows",
        "After this many follow-up windows that caught nothing meant for Dexter, it "
        "stops re-opening them by itself until you say its name again.",
        module="voice_pipeline", attr="FOLLOWUP_MISSES_MAX", group="voice",
    ),
    Setting(
        "echoGuardMs", "DEXTER_ECHO_GUARD_MS", "float", 600.0,
        "Echo guard (ms)",
        "How long the mic stays deaf after Dexter finishes speaking, so the "
        "room's echo of its last words is not taken as your reply.",
        module="voice_pipeline", attr="ECHO_GUARD_MS", group="voice",
    ),
    Setting(
        "wakeMode", "DEXTER_WAKE_MODE", "choice", "name",
        "How to wake Dexter",
        "'open': everything you say is for Dexter until you mute - no name needed "
        "(best with 'Only answer to my voice'). 'name': start with 'Dexter'. "
        "'model': openWakeWord's 'hey jarvis' phrase - instant, but not 'Dexter'. "
        "'both': name or phrase.",
        module="voice_pipeline", attr="WAKE_MODE", options=["open", "name", "model", "both"], group="voice",
    ),
    Setting(
        "voiceKey", "DEXTER_VOICE_KEY", "choice", "michael",
        "Dexter's voice",
        "Kokoro voices. heart and bella are its most natural; michael, fenrir, "
        "adam are male; george and daniel are British. Use 'Try' to hear one.",
        module="voice_engine", attr="DEFAULT_VOICE_KEY",
        options=["michael", "fenrir", "adam", "puck", "onyx", "george", "daniel", "heart", "bella", "nova", "sarah", "emma"],
        group="voice",
    ),
    Setting(
        "speakerOnly", "DEXTER_SPEAKER_ONLY", "bool", True,
        "Only answer to my voice",
        "After 'Learn my voice', ignore anyone else - and Dexter's own voice "
        "coming back through the speakers.",
        module="speaker_id", attr="STRICT", group="voice",
    ),
    Setting(
        "speakerThreshold", "DEXTER_SPEAKER_THRESHOLD", "float", 0.60,
        "Voice match strictness",
        "Similarity a sentence needs to count as you (0.5 lenient - 0.85 strict). "
        "A little under the bar still counts when you start with Dexter's name, and "
        "every confident match teaches Dexter your voice on this mic.",
        module="speaker_id", attr="THRESHOLD", group="voice",
    ),
    Setting(
        "speakerHard", "DEXTER_SPEAKER_HARD", "bool", False,
        "Never answer a stranger who says my name",
        "Strict 'only me': a voice that is not a confident match for yours is "
        "ignored even when it says Dexter's name or trips the wake word. Turn "
        "this on when other people set Dexter off. The cost is that a borderline "
        "match of your own voice may be ignored until you start with the name "
        "again - raise it back by lowering 'Voice match strictness'. Needs "
        "'Only answer to my voice' on, and your voice learned.",
        module="voice_pipeline", attr="HARD_ONLY_ME", group="voice",
    ),
    Setting(
        "wakeName", "DEXTER_WAKE_NAME", "string", "dexter",
        "The name to answer to",
        "For the 'name' mode. One word, lower case; 'dexter' and 'jarvis' come "
        "with the spellings Whisper tends to use for them.",
        module="voice_pipeline", attr="WAKE_NAME", group="voice",
    ),
    Setting(
        "wakeWordThreshold", "DEXTER_WAKEWORD_THRESHOLD", "float", 0.5,
        "Wake phrase sensitivity (model mode)",
        "Lower triggers more easily; higher means fewer false wakes.",
        module="wakeword", attr="_THRESHOLD", group="voice",
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
        "activity", "DEXTER_ACTIVITY", "bool", True,
        "Activity awareness",
        "Dexter samples the window in focus and recently changed files so it knows what you are working on. Stays on this PC, seven days.",
        module="activity", attr="ENABLED", group="behaviour",
    ),
    Setting(
        "claudeLog", "DEXTER_CLAUDE_LOG", "bool", True,
        "Read Claude Code's logs",
        "Let Dexter read ~/.claude/projects to know what you and Claude did there (read-only; Dexter never runs Claude Code).",
        module="claude_sessions", attr="ENABLED", group="behaviour",
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
    # ── The machine (roadmap v9, D7) ──
    # These decide what Dexter asks Sentinel for. They live here rather than in
    # .env because the right number depends on what the owner has open, and the
    # only way to find it is to change it and watch the Resource panel.
    Setting(
        "vramBudget", "DEXTER_VRAM_BUDGET_MIB", "int", 5120,
        "My VRAM budget (MiB)",
        "How much video memory Dexter may hold. Sentinel enforces it; going over is "
        "what gets a model dropped when the card gets tight.",
        module="model_steward", attr="VRAM_BUDGET_MIB", group="machine",
    ),
    Setting(
        "ramBudget", "DEXTER_RAM_BUDGET_MIB", "int", 3072,
        "My RAM budget (MiB)",
        "How much system memory Dexter declares to Sentinel. Only used by Sentinel "
        "builds that admit on RAM as well as VRAM.",
        module="model_steward", attr="RAM_BUDGET_MIB", group="machine",
    ),
    Setting(
        "manageLmStudio", "DEXTER_MANAGE_LMSTUDIO", "bool", True,
        "Unload LM Studio when memory is tight",
        "Let Dexter run `lms unload --all` on a red warning. Turn this off if BuzzEdit "
        "(or you) owns LM Studio.",
        module="model_steward", attr="MANAGE_LMSTUDIO", group="machine",
    ),
    # ── v13: how far into the disk Dexter may reach, and where dictation goes ──
    Setting(
        "fsScope", "DEXTER_FS_SCOPE", "choice", "machine",
        "Folders Dexter may open",
        "'machine': every drive and folder you can open yourself - which is what "
        "makes \"open the C drive\" and \"list that folder\" work. 'user': only your "
        "Desktop, Documents, Downloads, Pictures, Videos, Music and the Dexter "
        "folder. Writing inside Windows itself is refused either way.",
        module=None, attr=None, options=["machine", "user"], group="machine",
    ),
    Setting(
        "dictationTarget", "DEXTER_DICTATION_TARGET", "choice", "notes",
        "Where \"take notes\" writes",
        "The default sink for continuous note-taking when you do not name one: a "
        "Markdown note in Dexter's notes folder, Notepad, Sticky Notes, Notion, or "
        "the clipboard. Saying \"take notes in Notion\" overrides it for that note.",
        module=None, attr=None,
        options=["notes", "notepad", "sticky", "notion", "clipboard"], group="machine",
    ),
    # ── the hand mouse (v13.6). Its own group: eight of these are the gesture
    # map, and they belong next to each other rather than scattered through
    # "Machine", where the first one lived alone since v13.
    Setting(
        "interruptWords", "DEXTER_VOICE_INTERRUPT_WORDS", "bool", True,
        "Let me interrupt mid-sentence",
        "Keeps the microphone open while Dexter is speaking, listening for one thing "
        "only: \"stop\", \"quiet\", \"never mind\". Anything else it hears in that "
        "moment is dropped, so its own voice coming back through the speakers can "
        "never be read as an instruction. Off makes it properly deaf while talking.",
        module="voice_pipeline", attr="INTERRUPT_WORDS_ENABLED", group="voice",
    ),
    Setting(
        "handSmoothing", "DEXTER_HAND_SMOOTHING", "float", 0.6,
        "Pointer steadiness",
        "How much the hand-tracked pointer is smoothed: 0 follows your fingertip "
        "exactly and shakes with it, 0.9 is slow and very steady. Takes effect the "
        "next time hand tracking starts.",
        module=None, attr=None, group="hand",
    ),
    Setting(
        "handSpeed", "DEXTER_HAND_SPEED", "float", 1.0,
        "Pointer speed",
        "How far the pointer travels for a given hand movement. The pointer is "
        "absolute - a rectangle in the camera image maps onto the whole screen - so "
        "this is the size of that rectangle: above 1 is faster and twitchier, below "
        "1 is slower and much easier to aim with. 0.5 to 2.5.",
        module=None, attr=None, group="hand",
    ),
    Setting(
        "handDragThreshold", "DEXTER_HAND_DRAG_THRESHOLD", "int", 120,
        "Drag distance (pixels)",
        "The pointer holds still while you make a click, so the press lands where "
        "you aimed. Move further than this - and hold for a moment - and it becomes "
        "a drag instead. Lower it if drags are hard to start, raise it if clicks "
        "slide.",
        module=None, attr=None, group="hand",
    ),
    Setting(
        "handConfirmFrames", "DEXTER_HAND_CONFIRM_FRAMES", "int", 2,
        "Frames a click must hold",
        "A button goes down only after the pose has been seen this many frames in a "
        "row, and comes up only once it has been gone that many - so one noisy frame "
        "cannot fire a click or drop one. 1 is instant and jittery; 3 is very "
        "deliberate.",
        module=None, attr=None, group="hand",
    ),
    Setting(
        "handPinchOn", "DEXTER_HAND_PINCH_ON", "float", 0.45,
        "How close a pinch has to be",
        "The gap between your thumb and fingertip that counts as a pinch, measured "
        "in hand-widths so your distance from the camera does not matter. Raise it "
        "if pinches are missed, lower it if they fire on their own.",
        module=None, attr=None, group="hand",
    ),
    Setting(
        "handPreview", "DEXTER_HAND_PREVIEW", "bool", True,
        "Show the tracking window",
        "The small camera window that draws what the tracker sees, the pose it read "
        "and the palm score. Worth keeping on: without it, \"the camera can't see "
        "my hand\" and \"my hand is in a pose bound to do nothing\" look identical.",
        module=None, attr=None, group="hand",
    ),
    Setting(
        "autoClean", "DEXTER_AUTO_CLEAN", "choice", "safe",
        "Disk cleanup",
        "Whether 'clean up space' forwards a cleanup request to Sentinel (its tray runs "
        "SmartCleaner). Dexter never deletes anything itself.",
        module=None, attr=None, options=["safe", "off"], group="machine",
    ),
    Setting(
        "engineVram", "DEXTER_ENGINE_VRAM_MIB", "int", 12500,
        "buzzcode engine VRAM (MiB)",
        "What Dexter books from Sentinel before starting buzzcode's 27B. If it is refused, "
        "the code task is refused rather than started on top of a render.",
        module="model_steward", attr="ENGINE_VRAM_MIB", group="machine",
    ),
    Setting(
        "engineRam", "DEXTER_ENGINE_RAM_MIB", "int", 6500,
        "buzzcode engine RAM (MiB)",
        "The host memory booked alongside it (the KV cache spill).",
        module="model_steward", attr="ENGINE_RAM_MIB", group="machine",
    ),
    Setting(
        "appsFile", "DEXTER_APPS_FILE", "string", "",
        "App registry overrides",
        "Path to your own apps.json (launch commands, ports, memory figures). Blank uses "
        "data/apps.json.",
        module="apps_registry", attr="OVERRIDE_FILE", group="machine",
    ),
    Setting(
        "missionReminderRuntime", "MISSION_REMINDER_RUNTIME", "string", "",
        "Mission Reminder runtime file",
        "Where Mission Reminder mirrors its state. Dexter reads it (never writes) and "
        "stays quiet while one of its focus blocks is running. Blank uses "
        "%LOCALAPPDATA%\\MissionReminder\\runtime.json.",
        module=None, attr=None, group="machine",
    ),
]

def _add_gesture_settings() -> None:
    """One choice per hand pose: what that shape of hand does (v13.6).

    Generated from `handmouse`'s own catalogue rather than typed out here, so a
    pose the detector knows about can never be missing from the settings - the
    failure mode would be a gesture nobody can change.
    """
    try:
        import handmouse
    except Exception as exc:  # pragma: no cover - numpy/opencv absent
        print(f"[Settings] Hand gesture settings unavailable: {exc}")
        return
    actions = list(handmouse.ACTIONS)
    for pose, description in handmouse.POSES.items():
        key = "handPose" + "".join(part.capitalize() for part in pose.split("-"))
        SETTINGS.append(Setting(
            key, handmouse.pose_env(pose), "choice", handmouse.POSE_DEFAULTS[pose],
            f"Gesture: {description}",
            "What this hand shape does. Changing it takes effect immediately, "
            "even while hand tracking is running.",
            module=None, attr=None, options=actions, group="hand",
        ))


_add_gesture_settings()

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
    if setting.kind == "model":
        # Free text on purpose: the model may not be pulled yet, or its server
        # may be down while the owner types the name. Never lower-cased -
        # LM Studio ids are case-sensitive.
        return str(value or "").strip()
    return str(value)


def _model_options(current: Any) -> List[str]:
    """Every model id the local servers list right now, current value first.

    Read live so a freshly pulled Ollama tag appears without a restart; the
    current value is always present so the picker never shows a blank.
    """
    seen: List[str] = []
    if current:
        seen.append(str(current))
    try:
        from hybrid_llm import llm_router

        listing = llm_router.list_models()
        for prov in ("ollama", "lmstudio", "llamacpp"):
            for name in listing.get("providers", {}).get(prov, {}).get("models", []) or []:
                if name and name not in seen:
                    seen.append(name)
    except Exception:
        pass
    return seen


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
            "options": _model_options(values[s.key]) if s.kind == "model" else s.options,
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


#: Changing one of these means a model is no longer wanted. The one being
#: replaced is offloaded rather than left holding memory until its keep-alive
#: expires - two hours, for the fast tier (v13.2, the owner's ask).
_MODEL_KEYS = ("fastModel", "smartModel", "smartModelFallback")


def _offload_replaced(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    """Unload the models the owner has just switched away from."""
    dropped: List[str] = []
    try:
        import model_steward
    except Exception:
        return dropped
    for key in _MODEL_KEYS:
        old_value, new_value = before.get(key), after.get(key)
        if not old_value or old_value == new_value:
            continue
        try:
            # The steward decides: it refuses when the old tag is still another
            # tier, and it never touches a model Dexter did not load.
            if model_steward.replaced(str(old_value), str(new_value or "")):
                dropped.append(str(old_value))
        except Exception as exc:
            print(f"[Settings] Could not offload {old_value}: {exc}")
    return dropped


#: Hand-mouse settings the tracker can pick up without being restarted. The
#: preview window is not one of them: it is opened once, by the camera thread.
_HAND_NUMBERS = ("handSmoothing", "handPinchOn", "handSpeed", "handDragThreshold",
                 "handConfirmFrames")


def _is_live_hand_key(key: str) -> bool:
    return key.startswith("handPose") or key in _HAND_NUMBERS


def _rebind_hand(keys: List[str]) -> bool:
    """Re-read the hand mouse's gesture map, now, while it is tracking.

    Changing a gesture is something you do *because* the current one is not
    working, so "takes effect the next time you start hand tracking" would be the
    wrong answer - you would have to stop the thing you are trying to fix.
    """
    if not any(_is_live_hand_key(key) for key in keys):
        return False
    try:
        import handmouse

        handmouse.hand_mouse.rebind()
        return True
    except Exception as exc:
        print(f"[Settings] Could not re-read the hand gestures: {exc}")
        return False


def update(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Persist and apply a set of changes. Unknown keys are ignored."""
    stored = _stored()
    before = current()
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
    # Applied first, then the tidy-up: `replaced` reads the router's *new* tiers
    # to decide whether the old tag is still wanted anywhere.
    offloaded = _offload_replaced(before, current())
    # The hand mouse has no module constant to patch - it reads the environment
    # when it builds its Settings - so these would be reported as
    # "needs a restart" when in fact they can be live. Tell the running tracker.
    if _rebind_hand(applied + needs_restart):
        moved = [key for key in needs_restart if _is_live_hand_key(key)]
        needs_restart = [key for key in needs_restart if key not in moved]
        applied += moved
    return {
        "ok": True,
        "applied": applied,
        "needsRestart": needs_restart,
        "rejected": rejected,
        "offloaded": offloaded,
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
