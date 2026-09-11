"""
The apps, as Dexter tools (roadmap v9, D3/D4).

"Open buzzedit", "is vrdeep still going", "what's running", "stop caliber",
"clean up space" — the sentences the owner actually says about their own
machine. Before v9 every one of those either missed (`launch_app` looks for
installed programs, not project launchers) or reached the model and came back
with a promise.

Two things make these tools different from `launch_app`:

* **They ask Sentinel first.** Anything with a VRAM appetite goes through
  `Query` before it is started. Starting BuzzEdit's ComfyUI beside a render is
  how a 16 GB card ends up paging, and "I started it, sorry" is not a repair.
* **They say no properly.** A refusal names who has the memory and the move
  Dexter is allowed to make. Stopping a batch app mid-job asks first, with the
  job named.

The Studio keeps its specialist (`buzzcaf_client`); the registry hands that one
record straight over rather than reimplementing the port walk here.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import apps_registry
import model_steward
import sentinel_client
from tool_registry import ToolResult, tool


def _gb(mib: Any) -> float:
    return int(mib or 0) / 1024.0


def _unknown(hint: str) -> ToolResult:
    known = ", ".join(sorted(a.get("label") or a.get("id") for a in apps_registry.all_apps()))
    return ToolResult(
        ok=False,
        summary=f"I don't know an app called '{hint}'. The ones I run are: {known}.",
        data={"known": known},
    )


def _row_line(row: Dict[str, Any]) -> str:
    bits = [row["label"]]
    if row["running"]:
        # Only a port we have actually identified. `port` falls back to the
        # preferred one for the UI, and saying "running on 8099" about an app
        # that merely wishes it were there is how the owner ends up debugging
        # the wrong process.
        where = f" on {row['livePort']}" if row.get("livePort") else ""
        extra = []
        if row.get("rssMib"):
            # "0.0 GB RAM" for a 300 MB Electron reads as broken; below a
            # gigabyte, say megabytes.
            rss = int(row["rssMib"])
            extra.append(f"{_gb(rss):.1f} GB RAM" if rss >= 1024 else f"{rss} MB RAM")
        if row.get("uptimeSeconds"):
            mins = int(row["uptimeSeconds"] // 60)
            extra.append(f"up {mins // 60}h{mins % 60:02d}m" if mins >= 60 else f"up {mins} min")
        bits.append(f"running{where}" + (f" ({', '.join(extra)})" if extra else ""))
    else:
        bits.append("stopped")
    return ": ".join(bits)


# ───────────────────────── status ─────────────────────────


@tool(
    name="app_status",
    description=(
        "Check whether one of the owner's own projects is running (VrDeep, BuzzEdit, "
        "the Studio, BuzzCode's engine, CaliberAI, BuzzFin, MidnightBuzz, MastGaane, "
        "BuzzAnimate, BuzzViolin, Mission Reminder, SmartCleaner), on which port, and "
        "for how long."
    ),
    params={"app": {"type": "string", "description": "The app's name, e.g. 'vrdeep', 'buzzedit', 'the studio'"}},
    examples=[
        "is buzzfin running", "is vrdeep running", "is the studio up",
        "check if buzzedit is running", "is caliberai on", "status of mastgaane",
        "is the coding engine up", "is midnightbuzz running",
    ],
    category="apps",
)
def app_status(app: str) -> ToolResult:
    record = apps_registry.resolve(app)
    if not record:
        return _unknown(app)
    row = apps_registry.status(record)
    if not row["running"]:
        tail = f" ({row['notes']})" if row.get("notes") and not row.get("canLaunch") else ""
        return ToolResult(
            ok=True,
            summary=f"{row['label']} is not running." + tail,
            data=row,
        )
    detail = f" — {row['detail']}" if row.get("detail") else ""
    return ToolResult(ok=True, summary=_row_line(row) + detail + ".", data=row)


@tool(
    name="apps_overview",
    description=(
        "List every project of the owner's that is running right now, with what each "
        "one is holding. Use for 'what's running' or 'what's on this machine'."
    ),
    params={},
    examples=[
        "what's running", "what is running right now", "what apps are running",
        "what's on my machine", "show me what's running", "which of my apps are up",
        "what have I got open", "list running projects",
    ],
    category="apps",
)
def apps_overview() -> ToolResult:
    rows = apps_registry.overview()
    live = [r for r in rows if r["running"]]
    snapshot = model_steward.snapshot()
    lines: List[str] = []
    if not live:
        lines.append("None of your projects are running.")
    else:
        lines.append(f"{len(live)} running: " + "; ".join(_row_line(r) for r in live) + ".")
    vram = snapshot.get("vram")
    if vram:
        lines.append(
            f"GPU: {_gb(vram['usedMib']):.1f} of {_gb(vram['totalMib']):.1f} GB used, "
            f"{_gb(vram['freeMib']):.1f} GB free ({vram.get('zone', '?')})."
        )
    ram = snapshot.get("ram")
    if ram and ram.get("availMib"):
        lines.append(f"RAM: {_gb(ram['availMib']):.1f} GB free ({ram.get('zone', '?')}).")
    frozen = snapshot.get("frozen") or []
    if frozen:
        lines.append(f"Paused by Sentinel: {', '.join(f.get('name', '?') for f in frozen)}.")
    return ToolResult(ok=True, summary=" ".join(lines), data={"apps": rows, "resources": snapshot})


# ───────────────────────── launching ─────────────────────────


@tool(
    name="open_app",
    description=(
        "Start one of the owner's own projects (VrDeep, BuzzEdit, the Studio, "
        "MidnightBuzz, MastGaane, CaliberAI, BuzzFin, the coffee ops, BuzzAnimate, "
        "BuzzViolin, SmartCleaner, Mission Reminder). Checks with Sentinel that there "
        "is memory for it first, then waits for it to come up."
    ),
    params={"app": {"type": "string", "description": "The project's name, e.g. 'buzzedit', 'vrdeep', 'the studio'"}},
    examples=[
        "open buzzedit", "start vrdeep", "launch the studio", "open buzzfin",
        "start midnightbuzz", "open caliberai", "start mastgaane", "open mission reminder",
        "fire up buzzanimate", "start the coffee ops", "open smartcleaner",
    ],
    category="apps",
)
def open_app(app: str) -> ToolResult:
    record = apps_registry.resolve(app)
    if not record:
        return _unknown(app)
    row = apps_registry.status(record)

    if row["running"]:
        return ToolResult(ok=True, summary=f"{row['label']} is already running.", data=row)
    if not row["canLaunch"]:
        why = (record.get("launch") or {}).get("why") or "there is no launch script for it"
        return ToolResult(ok=False, summary=f"I can't start {row['label']}: {why}.", data=row)

    # ── Something already has the card? Say so before doing anything. ──
    clash = apps_registry.conflicts(record)
    if clash:
        names = ", ".join(c.get("label") or c.get("id") for c in clash)
        return ToolResult(
            ok=False,
            summary=(
                f"{names} is running, and it can't share this 16 GB card with "
                f"{row['label']}. Stop it first (\"stop {clash[0].get('id')}\"), or wait for it."
            ),
            data={**row, "conflicts": [c.get("id") for c in clash]},
        )

    # ── D4: Sentinel decides, Dexter asks. `Query` books nothing. ──
    vram, ram = row["vramMib"], row["ramMib"]
    if row["class"] == "batch" or vram > 0:
        decision = model_steward.query(f"open:{row['id']}", vram, ram)
        if not decision.granted:
            return ToolResult(
                ok=False,
                summary=(
                    f"Not enough room for {row['label']} right now ({_gb(vram):.1f} GB "
                    f"of video memory, {_gb(ram):.1f} GB of RAM). " + decision.message()
                ),
                data={**row, "sentinel": decision.to_dict()},
            )
        zone = str(((model_steward.snapshot().get("ram") or {}).get("zone") or "")).lower()
        if zone == "red":
            return ToolResult(
                ok=False,
                summary=(
                    f"RAM is in the red — starting {row['label']} ({_gb(ram):.1f} GB) now "
                    "would push the machine into swapping. Close something first, or say "
                    "\"pause batch jobs\"."
                ),
                data={**row, "ramZone": zone},
            )
        warn_prefix = ""
        if zone == "yellow":
            warn_prefix = "RAM is already tight, so this may get slow. "
    else:
        warn_prefix = ""

    result = apps_registry.launch(record)
    if not result["ok"]:
        return ToolResult(ok=False, summary=f"I couldn't start {row['label']}: {result['message']}.",
                          data={**row, "error": result["message"]})

    up = apps_registry.wait_until_up(record)
    note = f" {record['warn']}" if record.get("warn") else ""
    if up:
        return ToolResult(
            ok=True,
            summary=f"{warn_prefix}{row['label']} is up.{note}",
            data={**apps_registry.status(record), "launched": True},
        )
    return ToolResult(
        ok=True,
        summary=(
            f"{warn_prefix}Started {row['label']}, but it hasn't answered its health check "
            f"in {apps_registry.LAUNCH_WAIT_SECONDS}s yet — it may still be warming up.{note}"
        ),
        data={**apps_registry.status(record), "launched": True, "confirmed": False},
    )


# ───────────────────────── stopping ─────────────────────────


def _stop_needs_confirmation(args: Dict[str, Any]) -> bool:
    """Ask only when there is something to lose."""
    record = apps_registry.resolve(str(args.get("app") or ""))
    if not record:
        return False
    return bool(apps_registry.mid_job(record))


def _stop_question(args: Dict[str, Any]) -> str:
    record = apps_registry.resolve(str(args.get("app") or ""))
    if not record:
        return ""
    busy = apps_registry.mid_job(record)
    label = record.get("label") or record.get("id")
    return f"{label} looks busy — {busy}. Stopping it loses that work. Stop it anyway?"


@tool(
    name="stop_app",
    description=(
        "Stop one of the owner's own projects. Uses each project's own shutdown route "
        "where it has one, and asks first when a batch job (a render, an encode, a "
        "code task) is in the middle of something."
    ),
    params={"app": {"type": "string", "description": "The project's name, e.g. 'caliberai', 'buzzedit'"}},
    examples=[
        "stop caliber", "stop buzzedit", "shut down vrdeep", "close the studio",
        "stop the coding engine", "kill midnightbuzz", "stop buzzfin", "stop mastgaane",
    ],
    category="apps",
    confirm_if=_stop_needs_confirmation,
)
def stop_app(app: str) -> ToolResult:
    record = apps_registry.resolve(app)
    if not record:
        return _unknown(app)
    row = apps_registry.status(record)
    if not row["running"]:
        return ToolResult(ok=True, summary=f"{row['label']} is not running.", data=row)
    if not row["canStop"]:
        why = (record.get("stop") or {}).get("why") or "it has no stop route I know of"
        return ToolResult(ok=False, summary=f"I can't stop {row['label']}: {why}.", data=row)

    result = apps_registry.stop(record)
    if not result["ok"]:
        return ToolResult(
            ok=False,
            summary=f"I couldn't stop {row['label']}: {result.get('message') or 'nothing responded'}.",
            data={**row, "stop": result},
        )
    still = apps_registry.probe(record).get("running")
    if still:
        return ToolResult(
            ok=True,
            summary=f"Asked {row['label']} to stop ({result.get('how')}); it is still shutting down.",
            data={**row, "stop": result},
        )
    return ToolResult(ok=True, summary=f"{row['label']} is stopped.", data={**row, "stop": result})


stop_app._dexter_confirm_prompt = _stop_question  # type: ignore[attr-defined]


@tool(
    name="restart_app",
    description="Stop one of the owner's projects and start it again.",
    params={"app": {"type": "string", "description": "The project's name"}},
    examples=[
        "restart buzzedit", "restart the studio", "restart caliberai",
        "bounce buzzfin", "restart mastgaane",
    ],
    category="apps",
)
def restart_app(app: str) -> ToolResult:
    record = apps_registry.resolve(app)
    if not record:
        return _unknown(app)
    label = record.get("label") or record.get("id")
    if apps_registry.probe(record).get("running"):
        stopped = stop_app(app)
        if not stopped.ok:
            return ToolResult(ok=False, summary=f"Couldn't restart {label}: {stopped.summary}",
                              data=stopped.data)
    started = open_app(app)
    return ToolResult(ok=started.ok, summary=f"Restarted {label}. {started.summary}".strip(),
                      data=started.data)


@tool(
    name="app_logs",
    description="Show the last lines of one of the owner's projects' own log file.",
    params={
        "app": {"type": "string", "description": "The project's name"},
        "lines": {"type": "integer", "description": "How many lines", "required": False, "default": 20},
    },
    examples=[
        "show me buzzedit's log", "what does the caliberai log say", "vrdeep logs",
        "last lines of the studio log", "show the mastgaane log",
    ],
    category="apps",
)
def app_logs(app: str, lines: int = 20) -> ToolResult:
    record = apps_registry.resolve(app)
    if not record:
        return _unknown(app)
    text = apps_registry.last_log_line(record, lines=max(1, min(200, int(lines or 20))))
    label = record.get("label") or record.get("id")
    if not text:
        return ToolResult(ok=False, summary=f"{label} has no log file I can read.",
                          data={"id": record.get("id")})
    return ToolResult(ok=True, summary=f"{label}, last {lines} line(s):\n{text}",
                      data={"id": record.get("id"), "log": text})


# ───────────────────────── disk ─────────────────────────


@tool(
    name="request_cleanup",
    description=(
        "Ask Sentinel to free disk space by running SmartCleaner (safe categories: "
        "temp files, caches, logs, crash dumps). Never touches model stores, "
        "node_modules, target or .venv."
    ),
    params={
        "mode": {"type": "string", "description": "'safe' (default) or 'deep'",
                 "required": False, "default": "safe", "enum": ["safe", "deep"]},
        "drive": {"type": "string", "description": "Which drive, e.g. 'C:'", "required": False},
    },
    examples=[
        "clean up space", "free up some disk space", "run a safe clean",
        "clear out the temp files", "we're low on disk", "clean up the drive",
        "how much space is free", "disk cleanup",
    ],
    category="apps",
)
def request_cleanup(mode: str = "safe", drive: Optional[str] = None) -> ToolResult:
    if os.getenv("DEXTER_AUTO_CLEAN", "safe").strip().lower() == "off":
        return ToolResult(
            ok=False,
            summary="Disk cleanup is switched off in your settings (DEXTER_AUTO_CLEAN=off).",
            data={"enabled": False},
        )
    client = sentinel_client.client()
    disk = None
    try:
        disk = client.disk_status()
    except Exception:
        disk = None
    if disk is None:
        disk = model_steward._local_disk()

    where = ""
    if disk and disk.get("drives"):
        where = " " + ", ".join(
            f"{d.get('letter')} {int(d.get('free') or 0) / (1024 ** 3):.0f} GB free"
            for d in disk["drives"]
        ) + "."

    if not client.supports("cleanup"):
        # The guardian installed today cannot run the cleaner. Say so, and say
        # what to do instead — not "done" over a no-op.
        return ToolResult(
            ok=False,
            summary=(
                "This Sentinel build can't run the cleaner yet (no cleanup support), so I "
                "haven't freed anything." + where +
                " Open SmartCleaner yourself with \"open smartcleaner\"."
            ),
            data={"connected": True, "disk": disk, "queued": False},
        )
    queued = client.request_cleanup(mode=str(mode or "safe").lower(), drive=drive)
    if not queued:
        return ToolResult(
            ok=False,
            summary="Sentinel isn't answering, so I couldn't queue a cleanup." + where,
            data={"connected": False, "disk": disk},
        )
    return ToolResult(
        ok=True,
        summary=(
            f"Queued a {mode} clean with Sentinel; its tray runs SmartCleaner and reports "
            f"back what it freed.{where}"
        ),
        data={"connected": True, "queued": queued, "disk": disk, "mode": mode},
    )


# ───────────────────────── thin readers ─────────────────────────
#
# Where an app already publishes its own state, read that rather than guessing
# from process lists: it is both cheaper and true.


@tool(
    name="vrdeep_queue",
    description="Read VrDeep's render queue and current session (its own output/queue.json).",
    params={},
    examples=[
        "what's in the vrdeep queue", "how many renders are queued", "vrdeep queue",
        "what is vrdeep working on", "is vrdeep still rendering", "render queue status",
    ],
    category="apps",
)
def vrdeep_queue() -> ToolResult:
    data = apps_registry.read_queue("vrdeep")
    if not data.get("found"):
        return ToolResult(ok=False, summary="VrDeep isn't in the registry.", data={})
    record = apps_registry.get("vrdeep")
    running = apps_registry.probe(record).get("running") if record else False
    pending = int(data.get("pending") or 0)
    if data.get("queue") is None and data.get("session") is None:
        return ToolResult(
            ok=True,
            summary=("VrDeep is running but hasn't written a queue file yet."
                     if running else "VrDeep isn't running and its queue file is empty."),
            data={**data, "running": running},
        )
    state = "rendering" if running else "not running"
    line = f"VrDeep is {state}; {pending} job(s) in the queue"
    if data.get("queueAge"):
        line += f" ({data['queueAge']})"
    session = data.get("session")
    if isinstance(session, dict):
        current = session.get("current") or session.get("job") or session.get("name")
        if current:
            line += f". Current: {str(current)[:80]}"
    return ToolResult(ok=True, summary=line + ".", data={**data, "running": running})


@tool(
    name="buzzedit_status",
    description="Ask BuzzEdit's backend how it is doing (its /api/health), including ComfyUI.",
    params={},
    examples=[
        "buzzedit status", "how is buzzedit doing", "is comfyui up",
        "what is buzzedit rendering", "check buzzedit health",
    ],
    category="apps",
)
def buzzedit_status() -> ToolResult:
    return _reader("buzzedit", "/api/health", "BuzzEdit")


@tool(
    name="finance_status",
    description="Ask BuzzFin whether it is up, unlocked and initialised (its /api/v1/health).",
    params={},
    examples=[
        "finance status", "is buzzfin unlocked", "how are the books",
        "check the finance app", "buzzfin health",
    ],
    category="apps",
)
def finance_status() -> ToolResult:
    # Not `/health`: BuzzFin's security gate answers 404 there. Only the
    # versioned route is outside the lock.
    return _reader("buzzfin", "/api/v1/health", "BuzzFin")


@tool(
    name="coffee_brief",
    description="Read the BuzzcafCoffee ops dashboard (its /api/dashboard).",
    params={},
    examples=[
        "coffee brief", "how is the coffee shop doing", "ops dashboard",
        "buzzcaf coffee status", "what's the coffee ops saying",
    ],
    category="apps",
)
def coffee_brief() -> ToolResult:
    # `/ops/api/dashboard` was a 404: the ops app only mounts a `root_path` when
    # it is run behind the Next front end, and `run.py` does not.
    return _reader("buzzcafcoffee", "/api/dashboard", "The coffee ops")


def _reader(app_id: str, path: str, label: str) -> ToolResult:
    """Read one route off an app, on the port that app is *actually* on.

    `path` is a route, never a URL. These three readers used to carry a literal
    loopback address apiece - `:8099/api/health`, `:8756/health`,
    `:8010/ops/api/dashboard` - which is a hardcoded port with a different hat
    on (GUARDIAN_PLAN.md section 11 rule 1)
    and was already wrong on this machine: with the Studio on 8099 BuzzEdit had
    stepped to 8100, so `buzzedit_status` was interrogating the Studio. The port
    comes from the registry's `live_port()` - the same discovery the probes and
    "what's running" use - so the reader follows the app when it steps forward.
    """
    record = apps_registry.get(app_id)
    if not record:
        return ToolResult(ok=False, summary=f"{label} isn't in the registry.", data={"id": app_id})
    if not apps_registry.probe(record).get("running"):
        return ToolResult(ok=False, summary=f"{label} isn't running. Say \"open {app_id}\" to start it.",
                          data={"running": False, "id": app_id})
    port = apps_registry.live_port(record)
    if not port:
        return ToolResult(
            ok=False,
            summary=f"{label} is running but did not identify itself on a port I could find.",
            data={"running": True, "id": app_id},
        )
    url = f"http://127.0.0.1:{port}/{str(path).lstrip('/')}"
    try:
        import requests

        res = requests.get(url, timeout=4)
        body = res.json() if res.headers.get("content-type", "").startswith("application/json") else res.text
    except Exception as exc:
        return ToolResult(ok=False, summary=f"{label} did not answer ({exc}).",
                          data={"running": True, "id": app_id})
    if isinstance(body, dict):
        pairs = ", ".join(f"{k}: {v}" for k, v in list(body.items())[:8]
                          if not isinstance(v, (dict, list)))
        return ToolResult(ok=True, summary=f"{label}: {pairs or 'up'}.",
                          data={"running": True, "id": app_id, "body": body})
    return ToolResult(ok=True, summary=f"{label} answered: {str(body)[:300]}",
                      data={"running": True, "id": app_id})


@tool(
    name="mastgaane_report",
    description="Read MastGaane's most recent report from its reports folder.",
    params={},
    examples=[
        "mastgaane report", "what did mastgaane find", "latest music report",
        "read the mastgaane report", "how did last night's mastgaane run go",
    ],
    category="apps",
)
def mastgaane_report() -> ToolResult:
    record = apps_registry.get("mastgaane")
    if not record:
        return ToolResult(ok=False, summary="MastGaane isn't in the registry.", data={})
    folder = os.path.join(apps_registry.root_of(record), str(record.get("reports_dir") or "reports"))
    if not os.path.isdir(folder):
        return ToolResult(ok=False, summary="MastGaane has no reports folder yet.", data={"dir": folder})
    files = [
        os.path.join(folder, name) for name in os.listdir(folder)
        if name.lower().endswith(".md")
    ]
    if not files:
        return ToolResult(ok=False, summary="MastGaane hasn't written a report yet.", data={"dir": folder})
    newest = max(files, key=lambda p: os.path.getmtime(p))
    try:
        with open(newest, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read(3000)
    except Exception as exc:
        return ToolResult(ok=False, summary=f"Couldn't read {os.path.basename(newest)}: {exc}",
                          data={"path": newest})
    return ToolResult(
        ok=True,
        summary=f"{os.path.basename(newest)} ({apps_registry._age(newest)}):\n{text[:1200]}",
        data={"path": newest, "text": text},
    )
