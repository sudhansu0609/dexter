"""
Filesystem permission for Dexter's file tools and endpoints.

Two scopes, because "don't serve C:\\Windows\\System32 over HTTP" and "I can't
open my own C drive" are both real complaints and they pull in opposite
directions:

* **machine** (the default) - Dexter may *look* anywhere the owner can look:
  every drive, every folder, `C:\\Windows` included. This is what makes "open
  the C drive", "what's in D:\\Projects" and "list that folder" work at all.
  Writing is still refused inside the system areas (`C:\\Windows`,
  `C:\\Program Files`, `ProgramData`, a drive root), because a misheard
  "delete that" there is not a recoverable mistake.
* **user** - the v5 behaviour: only the folders in `allowed_roots()`. Set
  `DEXTER_FS_ROOTS` (a `;`-separated list) and this scope turns on by itself, so
  an existing install that pinned its roots keeps exactly the reach it had.
  `DEXTER_FS_SCOPE=user|machine` overrides either way.

`resolve(path)` answers "may Dexter read/open this"; `resolve(path, write=True)`
(or `resolve_write`) answers "may Dexter change it". Both canonicalize first -
symlinks and `..` resolved - so no comparison can be fooled by spelling.

`DEXTER_FS_DENY` is the escape hatch in the other direction: folders Dexter must
not touch at all, reads included (a credential store, a client's folder).
"""

import os
from typing import List, Optional

_HOME = os.path.expanduser("~")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Where Windows keeps itself. Readable, never writable by Dexter.
_SYSTEM_DIRS = (
    os.environ.get("SystemRoot") or r"C:\Windows",
    os.environ.get("ProgramFiles") or r"C:\Program Files",
    os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
    os.environ.get("ProgramData") or r"C:\ProgramData",
    r"C:\Recovery",
    r"C:\System Volume Information",
    r"C:\$Recycle.Bin",
)


def _default_roots() -> List[str]:
    return [
        os.path.join(_HOME, "Desktop"),
        os.path.join(_HOME, "Documents"),
        os.path.join(_HOME, "Downloads"),
        os.path.join(_HOME, "Pictures"),
        os.path.join(_HOME, "Videos"),
        os.path.join(_HOME, "Music"),
        _PROJECT_ROOT,
    ]


def _listed(env: str) -> List[str]:
    raw = os.getenv(env, "")
    return [r.strip().strip("'\"") for r in raw.split(os.pathsep) if r.strip()]


def scope() -> str:
    """'machine' (look anywhere) or 'user' (only `allowed_roots()`)."""
    value = (os.getenv("DEXTER_FS_SCOPE") or "").strip().lower()
    if value in ("machine", "user"):
        return value
    # Someone who spelled out their roots meant them as a limit.
    return "user" if _listed("DEXTER_FS_ROOTS") else "machine"


def allowed_roots() -> List[str]:
    """The roots of the 'user' scope. Also what a denial message names."""
    roots = _listed("DEXTER_FS_ROOTS") or _default_roots()
    # The knowledge folders Dexter indexes are, by definition, folders it may read.
    try:
        import knowledge_ingest

        roots += knowledge_ingest.root_paths()
    except Exception:
        pass
    seen, unique = set(), []
    for root in (os.path.realpath(r) for r in roots):
        key = os.path.normcase(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def denied_roots() -> List[str]:
    """Folders Dexter may not touch at all, reads included (DEXTER_FS_DENY)."""
    return [os.path.realpath(r) for r in _listed("DEXTER_FS_DENY")]


def _inside(path: str, root: str) -> bool:
    """True when `path` is `root` or sits under it. Prefix-safe: /data is not /data-x."""
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:
        return False  # different drives cannot share a common path


def is_system_path(path: str) -> bool:
    """A Windows-owned folder: readable, never written to by Dexter."""
    real = os.path.realpath(path)
    if any(_inside(real, root) for root in _SYSTEM_DIRS if root):
        return True
    # A drive root itself ("C:\\", "D:\\"): creating or deleting here is never
    # what "save that file" meant.
    return os.path.dirname(real) == real


def is_denied(path: str) -> bool:
    real = os.path.realpath(path)
    return any(_inside(real, root) for root in denied_roots())


def canonical(path: str) -> Optional[str]:
    """The canonical form of a non-empty path, or None."""
    cleaned = (path or "").strip().strip("'\"")
    if not cleaned:
        return None
    return os.path.realpath(os.path.expanduser(os.path.expandvars(cleaned)))


def resolve(path: str, write: bool = False) -> Optional[str]:
    """The canonical path when Dexter may use it, else None.

    `write=True` is the stricter question: the same path can be readable and
    unwritable (anything under `C:\\Windows`).
    """
    real = canonical(path)
    if real is None:
        return None
    if is_denied(real):
        return None
    if write and is_system_path(real):
        return None
    if scope() == "machine":
        return real
    return real if any(_inside(real, root) for root in allowed_roots()) else None


def resolve_write(path: str) -> Optional[str]:
    """`resolve` for anything that creates, changes, moves or deletes."""
    return resolve(path, write=True)


def denial(path: str, write: bool = False) -> str:
    """A refusal the agent can relay: why, and what would change it."""
    real = canonical(path) or (path or "")
    if is_denied(real):
        return (
            f"'{path}' is in a folder you told me to stay out of (DEXTER_FS_DENY). "
            "I haven't read or changed anything there."
        )
    if write and is_system_path(real):
        return (
            f"'{path}' is part of Windows itself. I'll read it, but I won't write, "
            "move or delete anything in there - name a folder of yours instead."
        )
    folders = ", ".join(os.path.basename(r) or r for r in allowed_roots())
    return (
        f"'{path}' is outside the folders Dexter is allowed to touch ({folders}). "
        "Add more folders via DEXTER_FS_ROOTS in .env, or set DEXTER_FS_SCOPE=machine "
        "to let me reach the whole PC."
    )


def summary() -> dict:
    """What /status and `desktop_status` report about Dexter's filesystem reach."""
    return {
        "scope": scope(),
        "roots": allowed_roots() if scope() == "user" else [],
        "denied": denied_roots(),
    }
