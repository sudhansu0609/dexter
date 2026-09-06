"""
Who the owner is (roadmap v5, 4.2).

Nothing in Dexter represented the person it works for: no name, no channels,
no preferences, no working hours. `data/profile.json` holds that, and
`render_block()` turns it into a few lines at the top of every system prompt
so "what channels do I run" and "plan around my hours" have something to
stand on. Channel ids also feed the Studio's BuzzBrain "mine" matching.
"""

import os
from typing import Any, Dict, List, Optional

import jsonstore

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PROFILE_PATH = os.path.join(DATA_DIR, "profile.json")

FIELDS = ("name", "timezone", "location", "roles", "preferences", "working_hours", "channels", "notes")
LIST_FIELDS = ("roles", "preferences")

_DEFAULT: Dict[str, Any] = {
    "name": "",
    "timezone": "",
    "location": "",
    "roles": [],
    "preferences": [],
    "working_hours": "",
    "channels": [],   # [{"name": "Beyond3Baje", "channel_id": "UC...", "brand": "Beyond3Baje"}]
    "notes": "",
}


def load() -> Dict[str, Any]:
    stored = jsonstore.load_json(PROFILE_PATH, default={}) or {}
    profile = dict(_DEFAULT)
    for key in FIELDS:
        if key in stored:
            profile[key] = stored[key]
    for key in LIST_FIELDS:
        if isinstance(profile[key], str):
            profile[key] = [p.strip() for p in profile[key].split(",") if p.strip()]
    if not isinstance(profile["channels"], list):
        profile["channels"] = []
    return profile


def save(profile: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = load()
    for key in FIELDS:
        if key in profile and profile[key] is not None:
            cleaned[key] = profile[key]
    for key in LIST_FIELDS:
        if isinstance(cleaned[key], str):
            cleaned[key] = [p.strip() for p in cleaned[key].split(",") if p.strip()]
    channels = []
    for entry in cleaned.get("channels") or []:
        if isinstance(entry, str):
            entry = {"name": entry}
        if isinstance(entry, dict) and (entry.get("name") or entry.get("channel_id")):
            channels.append({
                "name": str(entry.get("name") or "").strip(),
                "channel_id": str(entry.get("channel_id") or "").strip(),
                "brand": str(entry.get("brand") or entry.get("name") or "").strip(),
            })
    cleaned["channels"] = channels
    jsonstore.save_json(PROFILE_PATH, cleaned)
    return cleaned


def update_field(field: str, value: Any) -> Dict[str, Any]:
    """Set one field. Lists accept comma-separated text; channels accept 'Name' or 'Name=UCid'."""
    field = (field or "").strip().lower().replace(" ", "_")
    if field not in FIELDS:
        raise ValueError(f"Unknown profile field '{field}'. Fields: {', '.join(FIELDS)}")
    profile = load()
    if field == "channels":
        entries = value if isinstance(value, list) else [v.strip() for v in str(value).split(",") if v.strip()]
        channels = []
        for entry in entries:
            if isinstance(entry, dict):
                channels.append(entry)
                continue
            name, _, cid = str(entry).partition("=")
            channels.append({"name": name.strip(), "channel_id": cid.strip(), "brand": name.strip()})
        profile["channels"] = channels
    elif field in LIST_FIELDS:
        profile[field] = value if isinstance(value, list) else [v.strip() for v in str(value).split(",") if v.strip()]
    else:
        profile[field] = str(value).strip()
    return save(profile)


def owner_channel_ids() -> List[str]:
    return [c["channel_id"] for c in load().get("channels", []) if c.get("channel_id")]


def is_empty(profile: Optional[Dict[str, Any]] = None) -> bool:
    p = profile or load()
    return not any(p.get(k) for k in FIELDS)


def render_block(profile: Optional[Dict[str, Any]] = None) -> str:
    """A compact 'About the user' block for a system prompt, or '' when nothing is known."""
    p = profile or load()
    if is_empty(p):
        return ""
    lines = ["About the user you work for:"]
    if p.get("name"):
        lines.append(f"- Name: {p['name']}")
    where = ", ".join(x for x in (p.get("location"), p.get("timezone")) if x)
    if where:
        lines.append(f"- Location / timezone: {where}")
    if p.get("roles"):
        lines.append(f"- Roles: {', '.join(p['roles'])}")
    if p.get("channels"):
        names = ", ".join(c.get("name") or c.get("brand") or "?" for c in p["channels"])
        lines.append(f"- YouTube channels they run: {names}")
    if p.get("working_hours"):
        lines.append(f"- Working hours: {p['working_hours']}")
    if p.get("preferences"):
        lines.append(f"- Preferences: {'; '.join(p['preferences'])}")
    if p.get("notes"):
        lines.append(f"- Notes: {str(p['notes'])[:400]}")
    return "\n".join(lines)


def summary() -> str:
    """One sentence for status/tools."""
    p = load()
    if is_empty(p):
        return "I don't know anything about you yet. Tell me your name, channels or preferences and I'll remember."
    parts = []
    if p.get("name"):
        parts.append(f"You are {p['name']}")
    if p.get("channels"):
        parts.append("you run " + ", ".join(c.get("name") or "?" for c in p["channels"]))
    if p.get("roles"):
        parts.append("roles: " + ", ".join(p["roles"]))
    if p.get("working_hours"):
        parts.append(f"working hours {p['working_hours']}")
    if p.get("preferences"):
        parts.append("preferences: " + "; ".join(p["preferences"]))
    return "; ".join(parts) + "."
