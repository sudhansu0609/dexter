"""
Filesystem allowlist for Dexter's file tools and endpoints.

The file tools and /filesystem/* endpoints accepted any absolute path on the
machine, mitigated only by the loopback bind. Every path is now canonicalized
(symlinks and '..' resolved) and must land under an allowed root, so a request
for C:\\Windows\\System32\\... is refused with an explanation instead of served.

Roots are configurable: set DEXTER_FS_ROOTS in .env to a list of directories
separated by the OS path separator (';' on Windows). When unset, the common
user folders plus the Dexter project directory are allowed.
"""

import os
from typing import List, Optional

_HOME = os.path.expanduser("~")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def allowed_roots() -> List[str]:
    raw = os.getenv("DEXTER_FS_ROOTS", "")
    roots = [r.strip() for r in raw.split(os.pathsep) if r.strip()] or _default_roots()
    return [os.path.realpath(r) for r in roots]


def resolve(path: str) -> Optional[str]:
    """The canonical path when it is inside an allowed root, else None."""
    cleaned = (path or "").strip().strip("'\"")
    if not cleaned:
        return None
    real = os.path.realpath(os.path.expanduser(cleaned))
    real_cmp = os.path.normcase(real)
    for root in allowed_roots():
        root_cmp = os.path.normcase(root)
        try:
            if os.path.commonpath([real_cmp, root_cmp]) == root_cmp:
                return real
        except ValueError:
            # Different drives cannot share a common path.
            continue
    return None


def denial(path: str) -> str:
    """A refusal the agent can relay so the user knows why and what to change."""
    folders = ", ".join(os.path.basename(r) or r for r in allowed_roots())
    return (
        f"'{path}' is outside the folders Dexter is allowed to touch ({folders}). "
        "Add more folders via DEXTER_FS_ROOTS in .env if this was intentional."
    )
