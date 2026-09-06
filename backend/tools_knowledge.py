"""
Knowledge and profile tools (roadmap v5, 4.1 / 4.2): search the indexed
folders with citations, re-index on demand, and read/update who the owner is.
"""

import os
from typing import Any, Dict, List, Optional

import knowledge_ingest
import user_profile as profile
from memory import memory
from tool_registry import ToolResult, tool


def _cite(hit: Dict[str, Any]) -> str:
    source = str(hit.get("source") or "")
    tag = hit.get("tag") or ""
    page = ""
    text = hit.get("text") or ""
    if text.startswith("[p."):
        page = text[1:text.find("]")] if "]" in text[:8] else ""
    name = os.path.basename(source) if source else "memory"
    label = f"{tag}/{name}" if tag else name
    return f"{label}{f', {page}' if page else ''}"


@tool(
    name="search_knowledge",
    description=(
        "Search the owner's indexed notes, documents, YouTube project folders and Studio data "
        "by meaning and quote the matching passages with their file names."
    ),
    params={
        "query": {"type": "string", "description": "What to look for"},
        "tag": {
            "type": "string",
            "description": "Limit to one root: notes, docs, youtube, studio",
            "required": False,
        },
    },
    examples=[
        "search my notes for the relaunch plan", "what do my documents say about the invoice",
        "find the script draft about bhangarh in my youtube folders", "look in my notes for the camera settings",
        "what did I write about episode three", "search my knowledge for the channel voice",
    ],
    category="memory",
)
def search_knowledge(query: str, tag: Optional[str] = None) -> ToolResult:
    hits = memory.search_documents(query, top_k=8, tag=(tag or "").strip().lower() or None)
    if not hits:
        where = f" under '{tag}'" if tag else ""
        return ToolResult(
            ok=True,
            summary=f"I found nothing about '{query}' in the indexed folders{where}.",
            data={"hits": []},
        )
    lines = []
    for hit in hits[:5]:
        snippet = str(hit.get("text") or "").replace("\n", " ").strip()[:240]
        lines.append(f"- ({_cite(hit)}) {snippet}")
    return ToolResult(
        ok=True,
        summary=f"Found {len(hits)} passage(s) about '{query}':\n" + "\n".join(lines),
        data={"hits": hits},
    )


@tool(
    name="reindex_knowledge",
    description="Re-read the owner's knowledge folders (notes, documents, YouTube projects, Studio data) into memory.",
    params={
        "root": {"type": "string", "description": "Only this root (notes, docs, youtube, studio)", "required": False},
        "force": {"type": "boolean", "description": "Re-read files even if they look unchanged", "required": False},
    },
    examples=[
        "reindex knowledge", "re-read my notes folder", "refresh your knowledge of my documents",
        "rescan my youtube folders", "update your index of my files", "reindex the studio data",
    ],
    category="memory",
)
def reindex_knowledge(root: Optional[str] = None, force: bool = False) -> ToolResult:
    roots = knowledge_ingest.parse_roots()
    if root:
        wanted = root.strip().lower()
        roots = [(tag, path) for tag, path in roots if tag == wanted]
        if not roots:
            known = ", ".join(tag for tag, _ in knowledge_ingest.parse_roots()) or "none configured"
            return ToolResult.fail(f"No knowledge root called '{root}'. Configured: {known}.")
    if not roots:
        return ToolResult.fail(
            "No knowledge folders are configured. Set DEXTER_KNOWLEDGE_ROOTS "
            "(tag=path;tag=path) in .env or in Settings."
        )
    stats = knowledge_ingest.reindex(roots=roots, force=bool(force))
    summary = (
        f"Indexed {stats['scanned']} file(s) in {stats['seconds']}s: {stats['updated']} updated, "
        f"{stats['skipped']} unchanged, {stats['removed']} removed"
    )
    if stats["empty"] or stats["failed"]:
        summary += f", {stats['empty']} with no text, {stats['failed']} failed"
    return ToolResult(ok=True, summary=summary + ".", data=stats)


@tool(
    name="show_profile",
    description="Say what Dexter knows about the owner: name, channels, roles, preferences, working hours.",
    params={},
    examples=[
        "what do you know about me", "who am I", "what channels do I run",
        "show my profile", "what are my working hours", "what do you know about my channels",
    ],
    category="memory",
)
def show_profile() -> ToolResult:
    return ToolResult(ok=True, summary=profile.summary(), data=profile.load())


@tool(
    name="update_profile",
    description=(
        "Remember something about the owner: name, timezone, location, roles, preferences, "
        "working_hours, channels (Name=UCchannelId), notes."
    ),
    params={
        "field": {
            "type": "string",
            "description": "One of name, timezone, location, roles, preferences, working_hours, channels, notes",
        },
        "value": {"type": "string", "description": "The value (lists comma-separated)"},
    },
    examples=[
        "my name is Sudhansu", "I work from 10am to 7pm", "my timezone is Asia/Kolkata",
        "I run the channels Beyond3Baje and Khayal3Baje", "I prefer short replies",
        "add to my profile that I edit in DaVinci Resolve",
    ],
    category="memory",
)
def update_profile(field: str, value: str) -> ToolResult:
    try:
        updated = profile.update_field(field, value)
    except ValueError as exc:
        return ToolResult.fail(str(exc))
    key = field.strip().lower().replace(" ", "_")
    shown = updated.get(key)
    if isinstance(shown, list):
        shown = ", ".join(c.get("name", "?") if isinstance(c, dict) else str(c) for c in shown)
    return ToolResult(ok=True, summary=f"Noted. Your {key.replace('_', ' ')}: {shown}.", data=updated)
