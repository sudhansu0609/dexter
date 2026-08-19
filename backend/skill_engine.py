"""
Skill engine.

A "skill" is now a *recorded sequence of tool calls* that Dexter can replay, not
a paragraph of prose. The previous version had two compounding problems:

  1. main.py called learn_skill_from_task() on every chat message longer than
     eight words, so ordinary conversation minted new skill files constantly.
  2. Those skills were matched by naive substring before the LLM ran, and their
     "execution" only printed the step text back. A skill learned from a
     mis-transcribed voice note ended up with triggers like "Got some mic",
     which then hijacked any message containing that phrase.

Skills are now only created on explicit request, must contain executable tool
calls to be matched, and legacy prose-only skills are kept for display but never
allowed to intercept a request.
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from jsonstore import load_json, save_json

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")

SCHEMA_VERSION = 2
MIN_TRIGGER_LEN = 3
MAX_TRIGGER_WORDS = 6


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or f"skill_{int(time.time())}"


def _trigger_is_sane(trigger: str) -> bool:
    """
    Reject transcription noise.

    Real triggers are short imperative fragments ("play song", "start a video").
    Hallucinated ones tend to be full sentences, carry terminal punctuation, or
    contain mid-phrase capitalised proper nouns from a garbled transcript.
    """
    if not isinstance(trigger, str):
        return False
    text = trigger.strip()
    if len(text) < MIN_TRIGGER_LEN or len(text) > 60:
        return False
    if len(text.split()) > MAX_TRIGGER_WORDS:
        return False
    if re.search(r"[.!?]$", text):
        return False
    if re.search(r"\b(?:oh|um|uh|hmm|okay so|i think)\b", text, re.IGNORECASE):
        return False
    words = text.split()
    if len(words) > 1 and sum(1 for w in words[1:] if w[:1].isupper()) >= 2:
        return False
    return True


class DexterSkillEngine:
    def __init__(self, llm_router: Any = None):
        self.llm = llm_router
        os.makedirs(SKILLS_DIR, exist_ok=True)

    # ───────────────────────── read ─────────────────────────

    def list_skills(self) -> List[Dict[str, Any]]:
        skills: List[Dict[str, Any]] = []
        if not os.path.exists(SKILLS_DIR):
            return skills
        for filename in sorted(os.listdir(SKILLS_DIR)):
            if not filename.endswith(".json"):
                continue
            data = load_json(os.path.join(SKILLS_DIR, filename), None)
            if isinstance(data, dict) and data.get("name"):
                data.setdefault("version", 1)
                data["executable"] = bool(data.get("toolCalls"))
                skills.append(data)
        return skills

    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        return next((s for s in self.list_skills() if s.get("name") == name), None)

    def match_skill(self, user_input: str) -> Optional[Dict[str, Any]]:
        """
        Find an *executable* skill whose trigger matches.

        Legacy prose-only skills are deliberately excluded: they cannot be run,
        so matching them only blocked the request from reaching a tier that could
        actually handle it.
        """
        text = (user_input or "").lower()
        if not text:
            return None

        best: Optional[Dict[str, Any]] = None
        best_len = 0
        for skill in self.list_skills():
            if not skill.get("toolCalls"):
                continue
            for trigger in skill.get("triggers", []):
                if not _trigger_is_sane(trigger):
                    continue
                trigger_lower = trigger.lower()
                if trigger_lower in text and len(trigger_lower) > best_len:
                    best, best_len = skill, len(trigger_lower)
        return best

    # ───────────────────────── write ─────────────────────────

    def save_skill(
        self,
        name: str,
        description: str,
        triggers: List[str],
        tool_calls: List[Dict[str, Any]],
        steps: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Persist a skill. Only sane triggers survive."""
        clean_triggers = [t.strip() for t in (triggers or []) if _trigger_is_sane(t)]
        if not clean_triggers:
            clean_triggers = [_slugify(name).replace("_", " ")]

        skill = {
            "name": _slugify(name),
            "description": description or f"Learned routine: {name}",
            "triggers": clean_triggers,
            "toolCalls": tool_calls or [],
            "steps": steps or [
                f"{c.get('tool')}({', '.join(f'{k}={v}' for k, v in (c.get('args') or {}).items())})"
                for c in (tool_calls or [])
            ],
            "learnedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "version": SCHEMA_VERSION,
        }
        save_json(os.path.join(SKILLS_DIR, f"{skill['name']}.json"), skill)
        print(f"[SkillEngine] Saved skill '{skill['name']}' with {len(skill['toolCalls'])} step(s).")
        return skill

    def learn_from_tool_calls(
        self, name: str, description: str, tool_calls: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Record a successful multi-tool run as a replayable skill.

        Called only on explicit user request, never automatically.
        """
        executable = [
            {"tool": c["name"], "args": c.get("args", {})}
            for c in (tool_calls or [])
            if c.get("ok") and c.get("name")
        ]
        if not executable:
            return None

        triggers = [_slugify(name).replace("_", " ")]
        if self.llm:
            triggers.extend(self._suggest_triggers(name, description))

        seen, unique = set(), []
        for trigger in triggers:
            key = trigger.lower()
            if key not in seen:
                seen.add(key)
                unique.append(trigger)

        return self.save_skill(name, description, unique, executable)

    def _suggest_triggers(self, name: str, description: str) -> List[str]:
        prompt = (
            f"A user routine is called '{name}': {description}\n\n"
            "List 3 short phrases (2-5 words each) a person might say to trigger it.\n"
            'Respond with a JSON array of strings only, e.g. ["phrase one", "phrase two"].'
        )
        try:
            from hybrid_llm import extract_json

            raw = self.llm.generate(prompt, temperature=0.3, max_tokens=200)
            parsed = extract_json(raw)
            if isinstance(parsed, list):
                return [t for t in parsed if isinstance(t, str)][:5]
        except Exception as exc:
            print(f"[SkillEngine] Trigger suggestion failed: {exc}")
        return []

    def delete_skill(self, name: str) -> bool:
        path = os.path.join(SKILLS_DIR, f"{_slugify(name)}.json")
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    # ───────────────────────── maintenance ─────────────────────────

    def purge_invalid(self) -> List[str]:
        """
        Remove skill files left behind by the old auto-learn bug.

        A legacy (non-executable) skill is junk when either its name carries the
        auto-generated `task_<timestamp>` signature, or any of its triggers fails
        the sanity check - a genuine skill does not have garbled transcript
        fragments mixed into its trigger list.

        Executable skills are never touched.
        """
        removed: List[str] = []
        for skill in self.list_skills():
            if skill.get("toolCalls"):
                continue

            name = skill.get("name", "")
            triggers = skill.get("triggers", [])
            auto_named = bool(re.match(r"^task_\d+$", name))
            has_garbage = any(not _trigger_is_sane(t) for t in triggers)

            if not auto_named and not has_garbage:
                continue

            try:
                os.remove(os.path.join(SKILLS_DIR, f"{name}.json"))
                removed.append(name)
            except Exception as exc:
                print(f"[SkillEngine] Could not remove {name}: {exc}")

        if removed:
            print(f"[SkillEngine] Purged {len(removed)} invalid skill(s): {', '.join(removed)}")
        return removed
