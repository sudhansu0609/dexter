"""
Starting any of the owner's own projects by name (v13.8).

"Make sure it is able to launch all the projects of mine in the Buzzcaf_Media folder...
I shall just tell the name or a matching name and it shall launch it using the
corresponding batch or ps1 files."

There are twenty-one folders next to Dexter and sixteen have a launcher. Twelve of those
are in `apps_registry`, which knows more than a folder scan ever could - a VrDeep render
books 10 GB of VRAM from Sentinel before it starts, and its own launcher is a .vbs that
starts it hidden. So the registry is asked first and its route is used where it exists.

This tool is for the rest, and for the case the registry cannot cover: a project the
owner adds tomorrow, with a `start.bat` in it and no entry anywhere.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import project_launcher
from tool_registry import ToolResult, tool


def _registered(name: str) -> Optional[str]:
    """The registry id for this name, if the registry knows it."""
    try:
        import apps_registry

        record = apps_registry.resolve(name) if hasattr(apps_registry, "resolve") else None
        if record:
            return record.get("id")
        for app in apps_registry.load().get("apps", []):
            haystack = " ".join(str(app.get(key, "")) for key in ("id", "label", "name"))
            squashed = project_launcher._squash(haystack)
            if project_launcher._squash(name) and project_launcher._squash(name) in squashed:
                return app.get("id")
    except Exception:
        return None
    return None


@tool(
    name="launch_project",
    description=(
        "Start one of the owner's own projects from the Buzzcaf_Media folder by name, "
        "using that project's own .bat, .cmd or .ps1 launcher. Handles a partial or "
        "mis-said name ('mission reminder' finds the missionRemider folder). Use for any "
        "of the owner's projects; registered ones still go through their proper start "
        "route so Sentinel is asked for memory first."
    ),
    params={
        "project": {"type": "string", "required": True,
                    "description": "The project name, or part of it."},
    },
    examples=[
        "launch vrdeep",
        "start mission reminder",
        "open buzzviolin",
        "run the buzzscan project",
        "start up midnight buzz",
    ],
    category="apps",
)
def launch_project(project: str) -> ToolResult:
    # The registry first: it knows ports, memory and each project's real entry point.
    app_id = _registered(project)
    if app_id:
        try:
            from tool_registry import execute

            result = execute("open_app", {"app": app_id})
            if result.ok:
                return result
            # Registered but its own route failed - fall through to the folder, which is
            # better than stopping at "I couldn't".
        except Exception:
            pass

    found = project_launcher.launch(project)
    if not found.get("ok"):
        names = ", ".join(found.get("projects", [])[:12])
        return ToolResult.fail(
            found.get("error", "I couldn't start that.")
            + (f" I can see: {names}." if names else "")
        )
    others = found.get("alternatives") or []
    tail = f" (it also has {', '.join(others)})" if others else ""
    return ToolResult(
        ok=True,
        summary=(f"Started {found['project']} with {found['launcherName']}{tail}."),
        data=found,
    )


@tool(
    name="list_projects",
    description=(
        "List the owner's own projects in the Buzzcaf_Media folder and the launcher each "
        "one would run. Use when asked what projects exist or what Dexter can start."
    ),
    params={},
    examples=[
        "what projects do I have",
        "list my projects",
        "what can you launch",
        "which of my apps can you start",
    ],
    category="apps",
)
def list_projects() -> ToolResult:
    found = project_launcher.projects()
    if not found:
        return ToolResult.fail(
            f"I can't see any projects with launchers in {project_launcher.ROOT}."
        )
    runnable = [p for p in found if p.best and p.best.score > 0]
    unclear = [p for p in found if p.best and p.best.score <= 0]
    lines = [f"{p.name} ({p.best.name})" for p in runnable]
    tail = ""
    if unclear:
        # Worth saying: these have scripts, but only build or teardown ones, and Dexter
        # will not run those on "open".
        tail = (f" {len(unclear)} more have only setup or build scripts, so I won't "
                f"start them on my own: {', '.join(p.name for p in unclear)}.")
    return ToolResult(
        ok=True,
        summary=f"{len(runnable)} project(s) I can start: " + "; ".join(lines) + "." + tail,
        data={"projects": [p.as_dict() for p in found],
              "root": project_launcher.ROOT,
              "runnable": [p.name for p in runnable],
              "needsNaming": [p.name for p in unclear]},
    )
