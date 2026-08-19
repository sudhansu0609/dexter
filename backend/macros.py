"""
Recorded macros - the skill engine, rebuilt on explicit recording.

The old engine tried to learn skills silently from long messages and minted
junk that hijacked real requests; it was dead code by v2. This replaces it
with something trustworthy because the user drives every part of it:

    "record a skill called publish routine"   -> capture begins
    ...user does the steps by voice as normal...
    "stop recording"                          -> saved to skills/<slug>.json
    "run publish routine"                     -> replays the exact tool calls

Parameterization: the macro's *subject* is the first recorded step's longest
string argument. Replaying with a parameter substitutes it everywhere it
appears - "run publish routine for the outro" swaps "intro" for "outro" in
every step that mentioned it. Deterministic, inspectable in the JSON.

Replay runs through run_skill, which is a confirm=True tool - one spoken
go-ahead covers the whole sequence, including any destructive steps the user
already performed once while recording.
"""

import os
import re
import time
from typing import Any, Dict, List, Optional

from jsonstore import load_json, save_json
from tool_registry import ToolResult, execute, get_tool

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")

MAX_STEPS_PER_MACRO = 12

# Tool calls that drive the recorder itself must never end up inside a macro.
_CONTROL_TOOLS = frozenset({"record_skill", "stop_recording", "run_skill"})

# session -> {"name": str, "steps": [{tool, args}]}
_recording: Dict[str, Dict[str, Any]] = {}


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "skill"


def _path_for(name: str) -> str:
    return os.path.join(SKILLS_DIR, f"{_slugify(name)}.json")


# ───────────────────────── recording ─────────────────────────


def observe_tool_call(session: str, tool_name: str, args: Dict[str, Any], ok: bool) -> None:
    """router.TOOL_OBSERVERS hook: capture calls for a session being recorded."""
    state = _recording.get(session)
    if not state or tool_name in _CONTROL_TOOLS or not ok:
        return
    if len(state["steps"]) >= MAX_STEPS_PER_MACRO:
        return
    state["steps"].append({"tool": tool_name, "args": dict(args or {})})


def start_recording(name: str, session: str = "default") -> ToolResult:
    name = (name or "").strip()
    if not name:
        return ToolResult.fail("The skill needs a name - say: record a skill called <name>.")
    if session in _recording:
        return ToolResult.fail(
            f"Already recording '{_recording[session]['name']}'. Say 'stop recording' first."
        )
    _recording[session] = {"name": name, "steps": []}
    return ToolResult(
        ok=True,
        summary=(
            f"Recording '{name}'. Do the steps as you normally would, "
            "then say 'stop recording'."
        ),
        data={"name": name},
    )


def _detect_slot(steps: List[Dict[str, Any]]) -> Optional[str]:
    """The first step's longest string argument is the macro's subject."""
    if not steps:
        return None
    candidates = [
        str(v) for v in (steps[0].get("args") or {}).values()
        if isinstance(v, str) and len(v.strip()) > 2
    ]
    return max(candidates, key=len) if candidates else None


def stop_recording(session: str = "default") -> ToolResult:
    state = _recording.pop(session, None)
    if not state:
        return ToolResult.fail("I wasn't recording anything.")
    if not state["steps"]:
        return ToolResult.fail(
            f"Nothing was captured for '{state['name']}' - no tools ran while recording."
        )
    os.makedirs(SKILLS_DIR, exist_ok=True)
    slot = _detect_slot(state["steps"])
    skill = {
        "name": state["name"],
        "kind": "macro",
        "description": f"Recorded macro - {len(state['steps'])} step(s)",
        "triggers": [state["name"].lower()],
        "steps": state["steps"],
        "slot": slot,
        "recordedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_json(_path_for(state["name"]), skill)
    step_names = ", ".join(s["tool"].replace("_", " ") for s in state["steps"])
    return ToolResult(
        ok=True,
        summary=f"Saved '{state['name']}' with {len(state['steps'])} step(s): {step_names}.",
        data={"skill": skill},
    )


def recording_status(session: str = "default") -> Optional[Dict[str, Any]]:
    return _recording.get(session)


# ───────────────────────── replay ─────────────────────────


def _substitute(args: Dict[str, Any], slot: str, parameter: str) -> Dict[str, Any]:
    replaced: Dict[str, Any] = {}
    pattern = re.compile(re.escape(slot), re.IGNORECASE)
    for key, value in args.items():
        replaced[key] = pattern.sub(parameter, value) if isinstance(value, str) else value
    return replaced


def replay(name: str, parameter: Optional[str] = None) -> ToolResult:
    skill = get_skill(name)
    if not skill:
        return ToolResult.fail(f"I don't have a skill called '{name}'.")
    steps = skill.get("steps") or []
    if not steps or not isinstance(steps[0], dict):
        return ToolResult.fail(
            f"'{skill.get('name', name)}' is a legacy note, not a replayable macro. "
            "Re-record it with 'record a skill'."
        )

    slot = skill.get("slot")
    outcomes: List[str] = []
    for index, step in enumerate(steps, 1):
        tool_name = step.get("tool", "")
        args = dict(step.get("args") or {})
        if parameter and slot:
            args = _substitute(args, slot, parameter)
        result = execute(tool_name, args)
        outcomes.append(f"{tool_name}: {result.summary}")
        if not result.ok:
            return ToolResult.fail(
                f"'{skill['name']}' stopped at step {index} ({tool_name}): {result.summary}"
            )
    return ToolResult(
        ok=True,
        summary=f"Ran '{skill['name']}' - {len(steps)} step(s) completed."
        + (f" (with '{parameter}')" if parameter and slot else ""),
        data={"steps": outcomes},
    )


# ───────────────────────── listing / maintenance ─────────────────────────


def get_skill(name: str) -> Optional[Dict[str, Any]]:
    exact = load_json(_path_for(name), None)
    if isinstance(exact, dict) and exact.get("name"):
        return exact
    wanted = (name or "").strip().lower()
    for skill in list_skills():
        if wanted and wanted in skill.get("name", "").lower():
            return skill
    return None


def list_skills() -> List[Dict[str, Any]]:
    skills: List[Dict[str, Any]] = []
    if not os.path.isdir(SKILLS_DIR):
        return skills
    for filename in sorted(os.listdir(SKILLS_DIR)):
        if not filename.endswith(".json"):
            continue
        data = load_json(os.path.join(SKILLS_DIR, filename), None)
        if not isinstance(data, dict) or not data.get("name"):
            continue
        steps = data.get("steps") or []
        executable = bool(steps) and all(isinstance(s, dict) and s.get("tool") for s in steps)
        data["executable"] = executable
        if not executable:
            data.setdefault("description", "Legacy note (not replayable)")
        skills.append(data)
    return skills


def delete_skill(name: str) -> bool:
    path = _path_for(name)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def purge_invalid() -> List[str]:
    """Remove leftovers of the old auto-learn bug (task_<timestamp> files)."""
    removed: List[str] = []
    for skill in list_skills():
        if skill.get("executable"):
            continue
        if re.match(r"^task_\d+$", skill.get("name", "")):
            if delete_skill(skill["name"]):
                removed.append(skill["name"])
    return removed
