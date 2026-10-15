"""
Launching any of the owner's projects by name (v13.8).

"Make sure it is able to launch all the projects of mine in the Buzzcaf_Media folder,
the root folder of Dexter. I shall just tell the name or a matching name and it shall
launch it using the corresponding batch or ps1 files."

`apps_registry` already knows a dozen of them properly - with ports, memory figures and
Sentinel's blessing before a 9 GB model starts - and that route is still preferred where
it exists. This is for everything else: twenty-one folders next to Dexter, each with its
own launcher, named however it was named on the day.

Two problems, and the second is the interesting one.

**Finding the project.** The spoken name never matches the folder exactly: "mission
reminder" is `missionRemider` (the typo is in the folder), "buzzcaf ai" is `BuzzcafAI`,
"the animator" is `BuzzAnimate`. Squash the punctuation, then exact, then contains, then
a character-level ratio.

**Finding the right launcher.** A project can hold a dozen scripts, and most of them are
not what "open VrDeep" means:

    VrDeep/     launch.bat  launch_app.bat  launch_app.ps1  launch_debug.bat
    CaliberAI/  start.bat  start_app.bat  stop_app.bat
    BuzzScan/   run.bat  setup.bat  start.bat
    Sentinel/   build.ps1  make-installer.bat  rebuild-installer.bat

So scripts are scored, not just found: the project's own name and the words start,
launch and run earn points; setup, install, build, stop, uninstall, debug and test lose
them heavily. A `stop_app.bat` must never be what "open CaliberAI" runs.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: Dexter lives inside the projects folder, so the root is its parent.
ROOT = os.environ.get("DEXTER_PROJECTS_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
#: How deep to look for a launcher inside a project.
MAX_DEPTH = 2
SCRIPT_SUFFIXES = (".bat", ".cmd", ".ps1")

#: Folders that are not projects.
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
             "data", "logs", "docs", ".idea", ".vscode"}

#: Words in a script's name that make it more likely to be *the* way in...
GOOD_WORDS = (("start", 40), ("launch", 40), ("run", 30), ("app", 15), ("open", 20),
              ("gui", 10), ("ui", 5), ("main", 10), ("dev", -5))
#: ...and the ones that make it something else entirely. Heavily negative, because
#: running `uninstall.bat` when somebody said "open" is not a small mistake.
BAD_WORDS = (("stop", -120), ("kill", -120), ("uninstall", -200), ("remove", -150),
             ("delete", -200), ("clean", -80), ("reset", -80), ("setup", -70),
             ("install", -70), ("build", -70), ("make", -60), ("rebuild", -80),
             ("debug", -50), ("test", -60), ("shortcut", -90), ("update", -40),
             ("upgrade", -40), ("migrate", -60), ("backup", -50), ("fix", -30))


@dataclass
class Launcher:
    path: str
    score: int = 0
    why: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class Project:
    name: str
    path: str
    launchers: List[Launcher] = field(default_factory=list)

    @property
    def best(self) -> Optional[Launcher]:
        return self.launchers[0] if self.launchers else None

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "path": self.path,
                "launcher": self.best.path if self.best else None,
                "launchers": [launcher.name for launcher in self.launchers[:6]]}


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def score_script(path: str, project: str) -> Tuple[int, str]:
    """How likely this script is the way a person means to start the project."""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    squashed = _squash(stem)
    score, reasons = 0, []
    if squashed == _squash(project):
        score += 90
        reasons.append("named after the project")
    elif _squash(project) in squashed:
        score += 45
        reasons.append("mentions the project")
    for word, points in GOOD_WORDS + BAD_WORDS:
        if re.search(rf"(?:^|[^a-z]){word}(?:$|[^a-z])", stem) or stem == word:
            score += points
            if points < 0:
                reasons.append(f"{word}: not a way in")
    # A shallower script is more likely to be the front door than one buried in a tools
    # folder, and .bat is more often the double-clickable one than .ps1.
    depth = path.replace("/", os.sep).count(os.sep)
    score -= depth
    if path.lower().endswith(".ps1"):
        score -= 5
    return score, "; ".join(reasons)


def _launchers_in(folder: str, project: str) -> List[Launcher]:
    found: List[Launcher] = []
    base_depth = folder.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs
                   if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        if root.rstrip(os.sep).count(os.sep) - base_depth >= MAX_DEPTH:
            dirs[:] = []
        for filename in files:
            if not filename.lower().endswith(SCRIPT_SUFFIXES):
                continue
            path = os.path.join(root, filename)
            score, why = score_script(path, project)
            found.append(Launcher(path=path, score=score, why=why))
    found.sort(key=lambda launcher: (-launcher.score, len(launcher.name)))
    return found


def projects(root: Optional[str] = None) -> List[Project]:
    """Every project folder next to Dexter that has a launcher of its own."""
    base = root or ROOT
    out: List[Project] = []
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name.lower())
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name.lower() in SKIP_DIRS or entry.name.startswith("."):
            continue
        launchers = _launchers_in(entry.path, entry.name)
        if launchers:
            out.append(Project(name=entry.name, path=entry.path, launchers=launchers))
    return out


def find(name: str, root: Optional[str] = None) -> Optional[Project]:
    """The project somebody meant, or None.

    Exact, then contained, then a character ratio - which is what gets "mission
    reminder" to a folder spelled `missionRemider`.
    """
    wanted = _squash(name)
    if not wanted:
        return None
    found = projects(root)
    by_key = {_squash(project.name): project for project in found}
    if wanted in by_key:
        return by_key[wanted]
    contained = [project for key, project in by_key.items()
                 if wanted in key or key in wanted]
    if contained:
        # The shortest name that contains it: "buzz" should not pick BuzzcafCoffee over
        # Buzzcaf when both match.
        return min(contained, key=lambda project: len(project.name))
    close = difflib.get_close_matches(wanted, list(by_key), n=1, cutoff=0.72)
    return by_key[close[0]] if close else None


def command_for(launcher: Launcher) -> List[str]:
    """How to run it. A .ps1 double-clicked opens an editor, so it is run explicitly."""
    path = launcher.path
    if path.lower().endswith(".ps1"):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    # `cmd /c start` so the batch file gets its own console and Dexter is not its parent
    # for the rest of the session.
    return ["cmd", "/c", "start", "", "/D", os.path.dirname(path), path]


def launch(name: str, root: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Start a project by name. Returns what it found and what it ran."""
    project = find(name, root)
    if project is None:
        available = [p.name for p in projects(root)]
        return {"ok": False, "asked": name, "projects": available,
                "error": f"I don't have a project called '{name}' in {ROOT}."}
    launcher = project.best
    if launcher is None:
        return {"ok": False, "asked": name, "project": project.name,
                "error": f"{project.name} has no .bat or .ps1 launcher I can see."}
    if launcher.score <= 0:
        # Everything in the folder looked like a setup or teardown script. Better to say
        # so than to run `uninstall.bat` because it was the only file there.
        return {"ok": False, "asked": name, "project": project.name,
                "launcher": launcher.path, "score": launcher.score,
                "error": (f"The only scripts in {project.name} look like setup or "
                          f"teardown ({launcher.name}), so I have not run anything. "
                          "Name the script if you do want it.")}
    command = command_for(launcher)
    if dry_run:
        return {"ok": True, "dryRun": True, "project": project.name,
                "launcher": launcher.path, "command": command, "score": launcher.score}
    try:
        subprocess.Popen(
            command, cwd=os.path.dirname(launcher.path),
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as exc:
        return {"ok": False, "asked": name, "project": project.name,
                "launcher": launcher.path, "error": f"{launcher.name} would not start: {exc}"}
    return {"ok": True, "project": project.name, "launcher": launcher.path,
            "launcherName": launcher.name, "command": command, "score": launcher.score,
            "alternatives": [l.name for l in project.launchers[1:4]]}
