"""
Coding and the local model engine as Dexter tools (roadmap v6, C1).

code_task hands a job to buzzcode in one of the owner's repositories and
returns at once; the result comes back as a banner and toast when the job
ends. The engine tools start and inspect the llama-server on 8089 that
buzzcode manages, which is also Dexter's first-choice model provider once it
is up.
"""

from typing import Optional

import model_steward
from code_agent import code_agent, known_projects, project_from_text, resolve_project
from tool_registry import ToolResult, tool


def _make_room_for_engine() -> str:
    """Step aside for buzzcode's 12.5 GB, when *we* are what is in the way.

    The engine is the most demanding thing on this machine after a render, and
    "buzzcode does not run every time" was usually Dexter's own smart model
    holding the last few gigabytes. Same rule as `open_app` (v12, P2.3): only
    our own models, never anyone else's work.
    """
    try:
        decision = model_steward.query(
            "buzzcode-engine", model_steward.ENGINE_VRAM_MIB, model_steward.ENGINE_RAM_MIB,
        )
        if decision.granted:
            return ""
        room = model_steward.make_room(
            "buzzcode-engine", "the coding engine",
            model_steward.ENGINE_VRAM_MIB, model_steward.ENGINE_RAM_MIB,
            blockers=decision.blockers,
        )
        return (room["note"] + " ") if room.get("note") else ""
    except Exception:
        # Making room is an optimisation; `start_engine` still asks Sentinel
        # properly and reports the holder if the answer is still no.
        return ""


@tool(
    name="code_task",
    description=(
        "Delegate a coding task to buzzcode, the local coding agent, inside one of the user's repositories "
        "(dexter, BuzzcafAI, BuzzBrain, BuzzCode, ...) - including dexter's own code. It opens in its own "
        "terminal window; returns immediately and "
        "the result arrives when the job ends. "
        "mode 'edits' lets it change files only; 'yolo' also lets it run commands and needs the user to ask for it."
    ),
    params={
        "task": {"type": "string", "description": "What to build, fix or change, in plain words"},
        "project": {"type": "string", "description": "Repository name; taken from the task text when omitted", "required": False},
        "mode": {"type": "string", "description": "edits (default) or yolo", "enum": ["edits", "yolo"], "required": False},
    },
    examples=[
        "fix the failing test in dexter",
        "have buzzcode add a dark mode toggle to buzzcafai",
        "write a python script in dexter that exports my reminders to csv",
        "code this: add input validation to the snapshot endpoint in buzzcafai",
        "refactor the popup in buzzbrain to show the last push time",
        "ask the coder to add type hints to knowledge_ingest.py in dexter",
        "implement a retry in the studio client, dexter repo, and let it run the tests",
    ],
    category="code",
)
def code_task(task: str, project: Optional[str] = None, mode: Optional[str] = "edits") -> ToolResult:
    task = (task or "").strip()
    if not task:
        return ToolResult.fail("What should be coded?")
    target = resolve_project(project) if project else project_from_text(task)
    if not target:
        names = ", ".join(p["name"] for p in known_projects()[:12])
        return ToolResult.fail(f"Which repository? I know: {names}.")
    lowered = task.lower()
    if mode != "yolo" and any(k in lowered for k in ("run the tests", "let it run", "run commands", "full access")):
        mode = "yolo"
    try:
        job = code_agent.start(task, target, mode or "edits")
    except (ValueError, RuntimeError) as exc:
        return ToolResult.fail(str(exc))
    engine = code_agent.engine_status()
    note = "" if engine["online"] else " The local model loads first, so the first minute or two is model load."
    where = " in its own buzzcode window" if job.windowed else ""
    return ToolResult(
        ok=True,
        summary=f"Started code task {job.id} in {target['name']}{where} ({job.mode} mode). I'll tell you when it's done.{note}",
        data=job.to_dict(),
    )


@tool(
    name="code_status",
    description="Progress or result of the current (or last) buzzcode task: turns, tool calls, files changed, its final message.",
    params={"job_id": {"type": "string", "description": "A specific job id", "required": False}},
    examples=[
        "how is the code task going",
        "code status",
        "is buzzcode done yet",
        "what did the coder change",
        "show me the result of the coding job",
    ],
    category="code",
)
def code_status(job_id: Optional[str] = None) -> ToolResult:
    info = code_agent.status(job_id)
    if not info:
        return ToolResult(ok=True, summary="No code tasks have run yet.", data={})
    files = ", ".join(info["filesChanged"][:6]) or "no files yet"
    if info["status"] == "running":
        summary = (
            f"Job {info['id']} on {info['project']['name']} is running: {info['turns']} turn(s), "
            f"{info['toolCalls']} tool call(s), {files}. {info['elapsed']}s so far."
        )
    else:
        summary = (
            f"Job {info['id']} on {info['project']['name']} {info['status']} after {info['elapsed']}s: "
            f"{len(info['filesChanged'])} file(s) changed ({files}). {info.get('message', '')[:400]}"
        )
        if info.get("diffStat"):
            summary += f" Diff: {info['diffStat'].splitlines()[-1].strip()}."
        if info.get("errors"):
            summary += " Errors: " + "; ".join(e[:120] for e in info["errors"][-2:])
    return ToolResult(ok=True, summary=summary, data=info)


@tool(
    name="code_cancel",
    description="Stop the running buzzcode task.",
    examples=["cancel the code task", "stop buzzcode", "abort the coding job", "kill the coder"],
    category="code",
)
def code_cancel() -> ToolResult:
    job = code_agent.cancel()
    if not job:
        return ToolResult(ok=True, summary="No code task is running.", data={})
    return ToolResult(ok=True, summary=f"Cancelled code task {job.id} on {job.project['name']}.", data=job.to_dict())


@tool(
    name="list_code_projects",
    description="List the repositories buzzcode can work in.",
    examples=["which repos can you code in", "list my projects for coding", "what repositories do you know"],
    category="code",
)
def list_code_projects() -> ToolResult:
    projects = known_projects()
    if not projects:
        return ToolResult(ok=True, summary="No repositories found under DEXTER_CODE_ROOTS.", data={"projects": []})
    return ToolResult(ok=True, summary="Repositories: " + ", ".join(p["name"] for p in projects), data={"projects": projects})


@tool(
    name="start_local_model",
    description="Start the local llama-server (Qwen3.8-27B on port 8089) through buzzcode's engine so Dexter and the coder run on the GPU.",
    examples=[
        "start the local model",
        "bring up the llama server",
        "start the buzzcode engine",
        "load the 27b model",
        "turn on the local llm",
    ],
    category="code",
)
def start_local_model() -> ToolResult:
    gave_back = _make_room_for_engine()
    try:
        info = code_agent.start_engine()
    except RuntimeError as exc:
        return ToolResult.fail(f"{gave_back}{exc}".strip())
    if info.get("online"):
        verb = "started" if info.get("started") else "was already running"
        return ToolResult(
            ok=True,
            summary=f"{gave_back}The local model {verb}: {info.get('model')} on {info['url']}.",
            data={**info, "gaveBack": gave_back.strip()},
        )
    return ToolResult(
        ok=True,
        summary=f"{gave_back}The engine is starting; the model is still loading (check again in a minute).",
        data={**info, "gaveBack": gave_back.strip()},
    )


@tool(
    name="local_model_status",
    description="Is the local llama-server on port 8089 up, and which model is loaded?",
    examples=["is the local model running", "llama server status", "which model is loaded", "is the engine up"],
    category="code",
)
def local_model_status() -> ToolResult:
    info = code_agent.engine_status()
    if info["online"]:
        return ToolResult(ok=True, summary=f"The local model is up: {info.get('model')} on {info['url']}.", data=info)
    return ToolResult(ok=True, summary="The local model server on 8089 is offline. Say 'start the local model' to bring it up.", data=info)


@tool(
    name="stop_local_model",
    description="Stop the llama-server that Dexter started (frees the GPU).",
    examples=["stop the local model", "shut down the llama server", "free the gpu"],
    category="code",
)
def stop_local_model() -> ToolResult:
    if code_agent.stop_engine():
        return ToolResult(ok=True, summary="Stopped the local model server.", data={})
    return ToolResult(ok=True, summary="Dexter did not start that server, so it was left alone.", data={})


@tool(
    name="reinstall_dexter",
    description=(
        "Rebuild and reinstall Dexter itself after its code has changed: reinstall the Python "
        "dependencies (requirements.txt) and rebuild the front end (npm run build), in a visible "
        "terminal window. Use this after buzzcode has edited the dexter repo (code_task with "
        "project 'dexter'), so the changes take effect. Set restart=true to relaunch Dexter when "
        "the build succeeds. Runs in its own console and returns immediately."
    ),
    params={
        "restart": {"type": "boolean", "description": "Relaunch Dexter after a clean build", "required": False},
        "deps_only": {"type": "boolean", "description": "Only reinstall Python deps, skip the front-end build", "required": False},
    },
    examples=[
        "reinstall dexter",
        "rebuild dexter and restart it",
        "reinstall dexter after the changes",
        "update dexter's install",
        "reinstall dexter dependencies only",
    ],
    category="code",
)
def reinstall_dexter(restart: Optional[bool] = False, deps_only: Optional[bool] = False) -> ToolResult:
    import self_update

    result = self_update.reinstall_dexter(restart=bool(restart), frontend=not bool(deps_only))
    if not result.get("ok"):
        return ToolResult.fail(result.get("error", "Couldn't start the reinstall."))
    steps = " and ".join(result.get("steps") or ["build"])
    tail = " I'll relaunch Dexter when it finishes cleanly." if result.get("restart") else \
        " Restart Dexter yourself once it's done, or say 'reinstall dexter and restart'."
    return ToolResult(
        ok=True,
        summary=f"Reinstalling Dexter in a terminal window ({steps}). Watch it there.{tail}",
        data=result,
    )
