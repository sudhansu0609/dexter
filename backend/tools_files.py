"""
File mutation: rename, move, copy, delete.

Dexter could read, write, search, and open files but could not rename, move,
copy, or delete one - the README's own Tier-2 example ("find the invoice, rename
it, and remind me at 5") could not actually run. These close that gap.

Two rules, both non-negotiable:

* **Every path goes through pathguard**, source and destination alike. A move
  whose destination is unchecked is an escape hatch out of the allowlist.
* **Delete means the Recycle Bin**, never `os.remove`. An assistant acting on a
  spoken command, occasionally mishearing it, must not be able to destroy
  anything irreversibly. Without send2trash installed, delete refuses outright
  rather than silently falling back to a permanent delete.
"""

import os
import shutil
from typing import Optional

import pathguard
from tool_registry import ToolResult, tool

try:
    from send2trash import send2trash

    _HAS_TRASH = True
except ImportError:  # pragma: no cover - depends on the install
    send2trash = None  # type: ignore
    _HAS_TRASH = False


def _resolve_existing(path: str) -> tuple:
    """(resolved, error) for a path that must already exist and be allowed."""
    allowed = pathguard.resolve(path)
    if not allowed:
        return None, pathguard.denial(path)
    if not os.path.exists(allowed):
        return None, f"I can't find {os.path.basename(path)}."
    return allowed, None


def _resolve_destination(path: str) -> tuple:
    """(resolved, error) for a destination, which need not exist yet."""
    allowed = pathguard.resolve(path)
    if not allowed:
        return None, pathguard.denial(path)
    return allowed, None


def _target_for(source: str, destination: str) -> str:
    """Moving onto a directory keeps the original filename, as `mv` does."""
    if os.path.isdir(destination):
        return os.path.join(destination, os.path.basename(source))
    return destination


def _unique(path: str) -> str:
    """Never silently overwrite: 'report.pdf' becomes 'report (2).pdf'."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    counter = 2
    while os.path.exists(f"{stem} ({counter}){ext}"):
        counter += 1
    return f"{stem} ({counter}){ext}"


@tool(
    name="rename_file",
    description=(
        "Rename a file or folder, keeping it where it is. Give the new name "
        "only, not a full path."
    ),
    params={
        "path": {"type": "string", "description": "The file to rename"},
        "new_name": {"type": "string", "description": "The new name, e.g. 'invoice-march.pdf'"},
    },
    examples=[
        "rename that file to invoice march",
        "rename screenshot.png to thumbnail.png",
        "call this file something else",
        "change the name of that document",
    ],
    confirm=True,
    category="files",
)
def rename_file(path: str, new_name: str) -> ToolResult:
    source, error = _resolve_existing(path)
    if error:
        return ToolResult.fail(error)

    new_name = os.path.basename((new_name or "").strip())
    if not new_name:
        return ToolResult.fail("I need a new name for it.")

    # Keep the extension when the user says a bare name - "rename it to invoice"
    # should not turn invoice.pdf into an extensionless file.
    if not os.path.splitext(new_name)[1]:
        new_name += os.path.splitext(source)[1]

    destination, error = _resolve_destination(os.path.join(os.path.dirname(source), new_name))
    if error:
        return ToolResult.fail(error)

    destination = _unique(destination)
    try:
        os.rename(source, destination)
    except OSError as exc:
        return ToolResult.fail(f"Could not rename it: {exc}")

    return ToolResult(
        ok=True,
        summary=f"Renamed to {os.path.basename(destination)}.",
        data={"from": source, "to": destination},
    )


@tool(
    name="move_file",
    description="Move a file or folder into another folder.",
    params={
        "path": {"type": "string", "description": "The file to move"},
        "destination": {"type": "string", "description": "The destination folder or full path"},
    },
    examples=[
        "move that file to my documents",
        "move the invoice into the receipts folder",
        "put this file on the desktop",
        "shift that screenshot to the pictures folder",
    ],
    confirm=True,
    category="files",
)
def move_file(path: str, destination: str) -> ToolResult:
    source, error = _resolve_existing(path)
    if error:
        return ToolResult.fail(error)

    target, error = _resolve_destination(destination)
    if error:
        return ToolResult.fail(error)

    target = _unique(_target_for(source, target))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(source, target)
    except OSError as exc:
        return ToolResult.fail(f"Could not move it: {exc}")

    return ToolResult(
        ok=True,
        summary=f"Moved {os.path.basename(source)} to {os.path.dirname(target)}.",
        data={"from": source, "to": target},
    )


@tool(
    name="copy_file",
    description="Copy a file or folder to another location, leaving the original.",
    params={
        "path": {"type": "string", "description": "The file to copy"},
        "destination": {"type": "string", "description": "The destination folder or full path"},
    },
    examples=[
        "copy that file to my desktop",
        "make a copy of the invoice in documents",
        "duplicate this file",
        "back that file up to the archive folder",
    ],
    confirm=True,
    category="files",
)
def copy_file(path: str, destination: str) -> ToolResult:
    source, error = _resolve_existing(path)
    if error:
        return ToolResult.fail(error)

    target, error = _resolve_destination(destination)
    if error:
        return ToolResult.fail(error)

    target = _unique(_target_for(source, target))
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isdir(source):
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    except OSError as exc:
        return ToolResult.fail(f"Could not copy it: {exc}")

    return ToolResult(
        ok=True,
        summary=f"Copied to {os.path.basename(target)}.",
        data={"from": source, "to": target},
    )


@tool(
    name="delete_file",
    description=(
        "Send a file or folder to the Recycle Bin. Never deletes permanently, "
        "so a misheard command is always recoverable."
    ),
    params={"path": {"type": "string", "description": "The file to delete"}},
    examples=[
        "delete that file",
        "move the old screenshot to the recycle bin",
        "get rid of that download",
        "bin this file",
        "throw away the temp file",
    ],
    confirm=True,
    category="files",
)
def delete_file(path: str) -> ToolResult:
    if not _HAS_TRASH:
        # Deliberately no os.remove fallback: an assistant that occasionally
        # mishears must not have an irreversible delete.
        return ToolResult.fail(
            "I can't delete safely without send2trash installed, and I won't "
            "delete permanently. Install it with: pip install send2trash"
        )

    source, error = _resolve_existing(path)
    if error:
        return ToolResult.fail(error)

    try:
        send2trash(source)
    except Exception as exc:
        return ToolResult.fail(f"Could not delete it: {exc}")

    return ToolResult(
        ok=True,
        summary=f"Sent {os.path.basename(source)} to the Recycle Bin. "
                "It's recoverable from there.",
        data={"deleted": source},
    )
