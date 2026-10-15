"""
Browsing the disk the way it is spoken about.

The complaint this module answers: "it's not able to open C drive or any such
drive or list folders or open a specific folder". Two things were missing, and
they are separate problems:

* **Permission** - `pathguard` refused anything outside a handful of user
  folders. That is now a scope (see pathguard.py).
* **Vocabulary** - nothing turned "the C drive", "my downloads", "the
  BuzzAnimate folder" or "d:\\films" into a path, and there was no tool that
  listed a folder at all. `search_files` could find a file by name; nobody could
  ask what was *in* a directory.

So: `spoken_path` resolves the words people actually use, `drives` answers "what
drives are there", and `list_folder` answers "what's in it" - folders first,
then files, newest-sized-and-dated so the reply can say something useful rather
than reciting 400 names.

Permission is still pathguard's answer alone; nothing here widens it.
"""

from __future__ import annotations

import os
import re
import string
import time
from typing import Any, Dict, List, Optional, Tuple

import pathguard

#: Spoken names for the user's own folders. "my downloads" / "the desktop".
USER_FOLDERS: Dict[str, str] = {
    "desktop": "Desktop",
    "documents": "Documents",
    "docs": "Documents",
    "my documents": "Documents",
    "downloads": "Downloads",
    "download": "Downloads",
    "downloads folder": "Downloads",
    "pictures": "Pictures",
    "photos": "Pictures",
    "images": "Pictures",
    "videos": "Videos",
    "movies": "Videos",
    "music": "Music",
    "home": "",
    "user folder": "",
    "my folder": "",
    "home folder": "",
}

#: "this pc" is not a path - it is the list of drives.
_THIS_PC = re.compile(
    r"^(?:this\s+pc|my\s+(?:pc|computer)|the\s+(?:pc|computer)|computer|pc|drives|"
    r"my\s+drives|all\s+drives|the\s+drives|drive\s+list)$",
    re.I,
)

#: "c drive", "drive c", "c:", "c:\", "the c: drive"
_DRIVE = re.compile(
    r"^(?:the\s+|my\s+)?(?:drive\s+)?([a-z])\s*:?\s*(?:\\|/)?\s*(?:drive|disk)?$",
    re.I,
)

_FOLDER_WORDS = re.compile(r"\s+(?:folder|directory|dir)$", re.I)
_LEAD_WORDS = re.compile(r"^(?:(?:please|the|my|a|an|that|this|up)\s+)+", re.I)

#: How deep `find_folder` is willing to walk, and how many entries it will read.
FIND_DEPTH = int(os.getenv("DEXTER_FOLDER_FIND_DEPTH", "3") or 3)
FIND_BUDGET_SECONDS = float(os.getenv("DEXTER_FOLDER_FIND_BUDGET", "4") or 4)
LIST_LIMIT = int(os.getenv("DEXTER_FOLDER_LIST_LIMIT", "200") or 200)

_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build",
    "target", ".next", ".cache", "$recycle.bin", "system volume information",
    "appdata", ".gradle", ".idea", "obj",
}


# ───────────────────────── drives ─────────────────────────


def _drive_letters() -> List[str]:
    """Every drive letter Windows currently has, without needing pywin32."""
    letters: List[str] = []
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for index, letter in enumerate(string.ascii_uppercase):
            if mask & (1 << index):
                letters.append(letter)
        if letters:
            return letters
    except Exception:
        pass
    # Not Windows, or no ctypes: probe the ones that answer.
    return [l for l in string.ascii_uppercase if os.path.isdir(f"{l}:\\")]


_DRIVE_KINDS = {0: "unknown", 1: "no root", 2: "removable", 3: "fixed",
                4: "network", 5: "cd-rom", 6: "ram disk"}


def _drive_kind(root: str) -> str:
    try:
        import ctypes

        return _DRIVE_KINDS.get(int(ctypes.windll.kernel32.GetDriveTypeW(root)), "unknown")
    except Exception:
        return "unknown"


def _volume_label(root: str) -> str:
    try:
        import ctypes

        name = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), name, 260, None, None, None, fs, 260
        )
        return name.value.strip() if ok else ""
    except Exception:
        return ""


def drives() -> List[Dict[str, Any]]:
    """Every drive, with its label and how full it is. Skips empty card readers."""
    rows: List[Dict[str, Any]] = []
    for letter in _drive_letters():
        root = f"{letter}:\\"
        kind = _drive_kind(root)
        row: Dict[str, Any] = {"letter": f"{letter}:", "path": root, "kind": kind,
                               "label": "", "totalGb": 0.0, "freeGb": 0.0, "ready": False}
        try:
            import shutil

            usage = shutil.disk_usage(root)
            row.update(
                ready=True,
                label=_volume_label(root),
                totalGb=round(usage.total / (1024 ** 3), 1),
                freeGb=round(usage.free / (1024 ** 3), 1),
                usedPercent=round(100.0 * usage.used / usage.total, 1) if usage.total else 0.0,
            )
        except Exception:
            # A card reader with no card, or a disconnected network drive.
            pass
        rows.append(row)
    return rows


def describe_drives(rows: Optional[List[Dict[str, Any]]] = None) -> str:
    rows = rows if rows is not None else drives()
    if not rows:
        return "I couldn't read the drive list."
    parts = []
    for row in rows:
        if not row["ready"]:
            parts.append(f"{row['letter']} ({row['kind']}, empty)")
            continue
        label = f" {row['label']}" if row["label"] else ""
        parts.append(f"{row['letter']}{label} {row['freeGb']:.0f} of {row['totalGb']:.0f} GB free")
    return "; ".join(parts)


# ───────────────────────── words to paths ─────────────────────────


def is_this_pc(text: str) -> bool:
    """True when the words mean "the drives", not a folder."""
    return bool(_THIS_PC.match(_clean(text)))


def _clean(text: str) -> str:
    value = (text or "").strip().strip("'\"")
    value = value.strip(" .!?,")
    value = _LEAD_WORDS.sub("", value)
    return value.strip()


def spoken_path(text: str) -> Optional[str]:
    """A path from the words for it, or None when they name nothing on disk.

    Tried in order of certainty: a real path as written, a drive letter, one of
    the user's own folders, one of the owner's projects, then a folder of that
    name found near the places they keep things.
    """
    raw = _clean(text)
    if not raw or is_this_pc(raw):
        return None

    # Already a path? ("D:\\films", "~/Desktop", "%USERPROFILE%\\Documents")
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if os.path.isabs(expanded) and os.path.exists(expanded):
        return os.path.realpath(expanded)
    if re.match(r"^\\\\[^\\]", expanded) and os.path.exists(expanded):  # UNC share
        return expanded

    drive = _DRIVE.match(raw)
    if drive:
        root = f"{drive.group(1).upper()}:\\"
        return root if os.path.isdir(root) else None

    bare = _FOLDER_WORDS.sub("", raw).strip().lower()
    if bare in USER_FOLDERS:
        return os.path.realpath(os.path.join(os.path.expanduser("~"), USER_FOLDERS[bare]))

    # One of the owner's own projects: "the BuzzAnimate folder".
    project = _project_folder(bare)
    if project:
        return project

    # An absolute path that does not exist yet is still a path (a destination).
    if os.path.isabs(expanded):
        return os.path.realpath(expanded)

    found = find_folder(bare)
    return found[0] if found else None


def _project_folder(name: str) -> Optional[str]:
    try:
        import apps_registry

        record = apps_registry.resolve(name)
        if not record:
            return None
        root = apps_registry.root_of(record)
        return os.path.realpath(root) if os.path.isdir(root) else None
    except Exception:
        return None


def _search_bases() -> List[str]:
    """Where a named folder is looked for: the places the owner keeps things."""
    home = os.path.expanduser("~")
    bases = [os.path.join(home, name) for name in
             ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")]
    bases.append(home)
    try:
        import apps_registry

        bases.append(apps_registry.load().get("root") or apps_registry.APPS_ROOT)
    except Exception:
        pass
    bases.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    seen, unique = set(), []
    for base in bases:
        key = os.path.normcase(base)
        if key not in seen and os.path.isdir(base):
            seen.add(key)
            unique.append(base)
    return unique


def find_folder(name: str, bases: Optional[List[str]] = None) -> List[str]:
    """Folders called `name`, nearest first. Bounded by depth and a clock."""
    wanted = _FOLDER_WORDS.sub("", _clean(name)).strip().lower()
    if not wanted or len(wanted) < 2:
        return []
    started = time.time()
    hits: List[str] = []
    for base in (bases if bases is not None else _search_bases()):
        base_depth = base.rstrip(os.sep).count(os.sep)
        for root, dirs, _files in os.walk(base):
            if time.time() - started > FIND_BUDGET_SECONDS:
                return hits
            if root.rstrip(os.sep).count(os.sep) - base_depth >= FIND_DEPTH:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS and not d.startswith(".")]
            for folder in dirs:
                if folder.lower() == wanted:
                    path = os.path.realpath(os.path.join(root, folder))
                    if path not in hits:
                        hits.append(path)
                        if len(hits) >= 5:
                            return hits
    return hits


# ───────────────────────── listing ─────────────────────────


def _entry(path: str, name: str) -> Dict[str, Any]:
    full = os.path.join(path, name)
    row: Dict[str, Any] = {"name": name, "path": full, "kind": "folder", "sizeBytes": 0,
                           "modified": "", "hidden": False}
    try:
        stat = os.stat(full)
        row["modified"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
        if not os.path.isdir(full):
            row["kind"] = "file"
            row["sizeBytes"] = int(stat.st_size)
        row["hidden"] = bool(getattr(stat, "st_file_attributes", 0) & 2)  # FILE_ATTRIBUTE_HIDDEN
    except OSError:
        row["kind"] = "file" if os.path.splitext(name)[1] else "folder"
    return row


def human_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def list_folder(
    path: str,
    limit: int = 60,
    show_hidden: bool = False,
    pattern: Optional[str] = None,
) -> Dict[str, Any]:
    """What is in a folder: folders first, then files, each with size and date.

    Returns `{"ok": False, "error": ...}` rather than raising, because every
    caller is a tool that has to *say* what went wrong.
    """
    target = pathguard.resolve(path)
    if not target:
        return {"ok": False, "error": pathguard.denial(path)}
    if not os.path.exists(target):
        return {"ok": False, "error": f"There's no folder at {target}."}
    if not os.path.isdir(target):
        return {"ok": False, "error": f"{os.path.basename(target)} is a file, not a folder.",
                "path": target, "isFile": True}

    try:
        names = os.listdir(target)
    except PermissionError:
        return {"ok": False, "error": f"Windows won't let me read {target} (permission denied)."}
    except OSError as exc:
        return {"ok": False, "error": f"I couldn't read {target}: {exc}"}

    needle = (pattern or "").strip().lower().strip("*")
    rows = [_entry(target, name) for name in names]
    if not show_hidden:
        rows = [r for r in rows if not r["hidden"] and not r["name"].startswith("$")]
    if needle:
        rows = [r for r in rows if needle in r["name"].lower()]

    folders = sorted([r for r in rows if r["kind"] == "folder"], key=lambda r: r["name"].lower())
    files = sorted([r for r in rows if r["kind"] == "file"], key=lambda r: r["name"].lower())
    budget = max(1, min(int(limit or 60), LIST_LIMIT))
    shown = (folders + files)[:budget]
    return {
        "ok": True,
        "path": target,
        "name": os.path.basename(target) or target,
        "folders": len(folders),
        "files": len(files),
        "totalBytes": sum(r["sizeBytes"] for r in files),
        "entries": shown,
        "truncated": len(folders) + len(files) > len(shown),
        "writable": pathguard.resolve_write(target) is not None,
    }


def describe_listing(result: Dict[str, Any], budget: int = 18) -> str:
    """One sentence a voice assistant can say about a folder."""
    if not result.get("ok"):
        return str(result.get("error") or "I couldn't read that folder.")
    entries = result["entries"]
    if not entries:
        return f"{result['path']} is empty."
    names = []
    for row in entries[:budget]:
        names.append(f"{row['name']}\\" if row["kind"] == "folder" else
                     f"{row['name']} ({human_size(row['sizeBytes'])})")
    head = (
        f"{result['path']}: {result['folders']} folder(s), {result['files']} file(s)"
        f" ({human_size(result['totalBytes'])})"
    )
    more = ""
    if len(entries) > budget or result.get("truncated"):
        more = f" ... and {result['folders'] + result['files'] - budget} more"
    return f"{head}. " + ", ".join(names) + more + "."


def open_in_explorer(path: str, select: bool = False) -> Dict[str, Any]:
    """Show a path to the user: the folder itself, or its parent with it selected."""
    target = pathguard.resolve(path)
    if not target:
        return {"ok": False, "error": pathguard.denial(path)}
    if not os.path.exists(target):
        return {"ok": False, "error": f"There's nothing at {target}."}
    try:
        if select and os.path.dirname(target) != target:
            import subprocess

            subprocess.Popen(["explorer", f"/select,{target}"])
        else:
            os.startfile(target)  # noqa: S606 - a path the guard already cleared
        return {"ok": True, "path": target,
                "message": f"Opened {os.path.basename(target) or target} in File Explorer."}
    except Exception as exc:
        return {"ok": False, "error": f"I couldn't open {target}: {exc}"}
