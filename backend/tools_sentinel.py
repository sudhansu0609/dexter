"""
Sentinel, as Dexter tools: what is on the GPU, and pausing the work that is not
urgent so the work that is gets the card.

Sentinel is the machine's memory and VRAM guardian (a separate Rust service on
a local named pipe). Dexter does not manage the GPU itself — it asks Sentinel,
and reports honestly when Sentinel is not there to ask.
"""

from typing import Any, Dict, List

import sentinel_client
from tool_registry import ToolResult, tool

_MIB = 1024 * 1024


def _absent() -> ToolResult:
    return ToolResult(
        ok=False,
        summary=(
            "Sentinel is not answering, so I cannot see or manage the GPU. "
            "It is the guardian service that watches memory and VRAM — check "
            "that it is running."
        ),
        data={"connected": False},
    )


def _gb(value: Any) -> float:
    return int(value or 0) / (1024.0 * _MIB)


@tool(
    name="gpu_status",
    description=(
        "Report what is using the GPU right now: how much video memory is in use "
        "and free, and which processes are holding it."
    ),
    params={},
    examples=[
        "what's using the gpu", "gpu status", "how much vram is free",
        "what is on the graphics card", "is the gpu busy", "check video memory",
        "who is using the gpu", "how much video memory is left",
    ],
    category="system",
)
def gpu_status() -> ToolResult:
    status = sentinel_client.client().gpu_status()
    if not status:
        return _absent()

    primary = next((a for a in status.get("adapters", []) if a.get("is_primary")), None)
    if not primary:
        return ToolResult(
            ok=True,
            summary="Sentinel reports no GPU with dedicated video memory on this machine.",
            data={"connected": True, "adapters": status.get("adapters", [])},
        )

    lines = [
        f"{primary.get('name', 'GPU')}: {_gb(primary.get('used')):.1f} GB of "
        f"{_gb(primary.get('total')):.1f} GB video memory in use, "
        f"{_gb(primary.get('free')):.1f} GB free ({status.get('zone', '?')})."
    ]
    top = [p for p in status.get("top", []) if p.get("valid")][:4]
    if top:
        holders = ", ".join(
            f"{p.get('name')} {_gb(p.get('vram_bytes')):.1f} GB"
            + (" (frozen)" if p.get("frozen") else "")
            for p in top
        )
        lines.append(f"Holding it: {holders}.")
    frozen: List[Dict[str, Any]] = status.get("frozen", [])
    if frozen:
        lines.append(
            f"{len(frozen)} background job(s) are paused: "
            + ", ".join(f["name"] for f in frozen)
            + "."
        )
    return ToolResult(
        ok=True,
        summary=" ".join(lines),
        data={"connected": True, "zone": status.get("zone"), "frozen": frozen,
              "free_gb": round(_gb(primary.get("free")), 2)},
    )


@tool(
    name="pause_batch_jobs",
    description=(
        "Ask Sentinel to freeze every background/batch job (renders, encodes, "
        "other model servers) so the GPU and RAM are free for what you are doing. "
        "They stay paused until resumed."
    ),
    params={},
    examples=[
        "pause batch jobs", "pause the background jobs", "freeze the batch work",
        "stop the background renders for now", "give me the whole gpu",
        "park the background work", "pause background processing",
    ],
    category="system",
)
def pause_batch_jobs() -> ToolResult:
    frozen = sentinel_client.client().pause_batch()
    if frozen is None:
        return _absent()
    if not frozen:
        return ToolResult(
            ok=True,
            summary="There were no background jobs to pause.",
            data={"connected": True, "frozen": []},
        )
    names = ", ".join(f["name"] for f in frozen)
    return ToolResult(
        ok=True,
        summary=(
            f"Paused {len(frozen)} background job(s): {names}. "
            "They stay paused until you ask me to resume them."
        ),
        data={"connected": True, "frozen": frozen},
    )


@tool(
    name="resume_batch_jobs",
    description="Ask Sentinel to resume every paused background/batch job.",
    params={},
    examples=[
        "resume batch jobs", "resume the background jobs", "unfreeze the batch work",
        "start the background renders again", "unpause background processing",
        "let the background jobs run again",
    ],
    category="system",
)
def resume_batch_jobs() -> ToolResult:
    remaining = sentinel_client.client().resume_batch()
    if remaining is None:
        return _absent()
    if remaining:
        names = ", ".join(f["name"] for f in remaining)
        return ToolResult(
            ok=True,
            summary=f"Background jobs resumed. Still frozen: {names}.",
            data={"connected": True, "frozen": remaining},
        )
    return ToolResult(
        ok=True,
        summary="Background jobs resumed — nothing is paused now.",
        data={"connected": True, "frozen": []},
    )
