"""
The apps registry (roadmap v9, D3/D4) — Dexter's map of everything under
`B:\\youtubeProjects\\Buzzcaf_Media`.

Dexter could already open Chrome and start the Studio. It could not tell you
whether VrDeep was rendering, start BuzzEdit, or stop the coding engine — and
`launch_app` would happily fire up a 10 GB pipeline next to a 12 GB model
server, because nothing in that path knew what an app *costs*.

One record per app, from `data/apps.default.json` (shipped) with the owner's
overrides merged from `data/apps.json` (never written by Dexter). A record
says four things:

* **launch** — the script the owner actually uses (`launch_app.vbs`,
  `START_APP.vbs`, `uv run …`), so Dexter starts it the same way they would.
* **probes** — how to tell it is up *without* starting anything: a health
  route, a runtime file, an open port, or a process whose path contains the
  project. Path matters: half these apps are `python.exe`.
* **stop** — and how *not* to. `strategy: none` is a real answer for BuzzWorlds
  and for Dexter itself. Nothing is killed that a record did not name.
* **class / vram_mib / ram_mib / exclusive_with** — what to ask Sentinel for
  before launching, and what cannot share a 16 GB card with it.

The Studio keeps its specialist: `buzzcaf_client` knows about the port walk and
the runtime file, so `handler: buzzcaf_client` hands that record straight over
rather than reimplementing it badly here.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import buzzcaf_ports
import procs

log = logging.getLogger("dexter.apps")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
DEFAULT_FILE = os.path.join(DATA_DIR, "apps.default.json")
#: The owner's overrides. Dexter reads this and never writes it, so a hand-made
#: change survives every update to the shipped defaults.
OVERRIDE_FILE = os.environ.get("DEXTER_APPS_FILE") or os.path.join(DATA_DIR, "apps.json")
#: Where the sibling projects live. One place, so a moved checkout is one edit.
APPS_ROOT = os.environ.get("DEXTER_APPS_ROOT") or r"B:\youtubeProjects\Buzzcaf_Media"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_HTTP_TIMEOUT = 2.0
#: How long `open_app` waits for a probe to go green before reporting honestly
#: that it started something but cannot yet see it.
LAUNCH_WAIT_SECONDS = int(os.environ.get("DEXTER_APP_LAUNCH_WAIT", "30") or 30)

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_stamp: float = 0.0


# ───────────────────────── loading ─────────────────────────


def _read(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"[Apps] Could not read {os.path.basename(path)}: {exc}")
        return {}


def load(force: bool = False) -> Dict[str, Any]:
    """The merged registry: shipped defaults with the owner's overrides on top."""
    global _cache, _cache_stamp
    with _lock:
        stamp = _mtime(DEFAULT_FILE) + _mtime(OVERRIDE_FILE)
        if _cache is not None and not force and stamp == _cache_stamp:
            return _cache

        base = _read(DEFAULT_FILE)
        apps = {a["id"]: copy.deepcopy(a) for a in base.get("apps", []) if a.get("id")}

        override = _read(OVERRIDE_FILE)
        for row in override.get("apps", []):
            app_id = row.get("id")
            if not app_id:
                continue
            if app_id in apps:
                apps[app_id].update(copy.deepcopy(row))
            else:
                apps[app_id] = copy.deepcopy(row)

        registry = {
            "root": override.get("root") or base.get("root") or APPS_ROOT,
            "apps": list(apps.values()),
        }
        _cache, _cache_stamp = registry, stamp
        return registry


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def all_apps() -> List[Dict[str, Any]]:
    return list(load().get("apps", []))


def get(app_id: str) -> Optional[Dict[str, Any]]:
    for app in all_apps():
        if app.get("id") == app_id:
            return app
    return None


def resolve(hint: Optional[str]) -> Optional[Dict[str, Any]]:
    """A spoken name to a record: id, alias, label, then a loose contains match.

    Deliberately not fuzzy-by-edit-distance: "buzzedit" and "buzzfin" are three
    characters apart, and opening the wrong 9 GB app is not a small mistake.
    """
    text = (hint or "").strip().lower()
    if not text:
        return None
    text = text.strip(" .!?,'\"")
    apps = all_apps()

    for app in apps:
        if app.get("id") == text:
            return app
    for app in apps:
        names = [str(n).lower() for n in app.get("names", [])] + [str(app.get("label", "")).lower()]
        if text in names:
            return app
    # Collapse spaces: "vr deep" and "buzz edit" are how they are said.
    squashed = text.replace(" ", "").replace("-", "")
    for app in apps:
        candidates = [app.get("id", "")] + [str(n) for n in app.get("names", [])] + [str(app.get("label", ""))]
        if any(squashed == c.lower().replace(" ", "").replace("-", "") for c in candidates):
            return app
    # A substring match only below this length is a coin toss: "code" is inside
    # "buzzcode", and "open code" means VS Code. Short words must be exact.
    if len(squashed) < 5:
        return None
    # ...and only against the id and one-word aliases. Descriptive aliases like
    # "the chrome extension" exist to be *said*, not to be searched inside:
    # matching them turned "open chrome" into "open BuzzBrain".
    matches = [
        app for app in apps
        if any(squashed in c.lower().replace("-", "")
               for c in [str(app.get("id", ""))] + [str(n) for n in app.get("names", []) if " " not in str(n)])
    ]
    return matches[0] if len(matches) == 1 else None


def root_of(app: Dict[str, Any]) -> str:
    """The app's folder on disk."""
    root = app.get("root") or app.get("id") or ""
    if os.path.isabs(root):
        return root
    return os.path.join(load().get("root") or APPS_ROOT, root)


# ───────────────────────── which port is it really on? ─────────────────────────


def ledger_id(app: Dict[str, Any]) -> str:
    """The name this app publishes itself under.

    Usually the record id; the Studio is the exception — its record is
    `buzzcafai` and its health route has always said `app: "buzzcaf"`.
    """
    return str(app.get("ledger_id") or app.get("id") or "")


def preferred_port(app: Dict[str, Any]) -> Optional[int]:
    """The port the app *asks* for. Not where it is (GUARDIAN_PLAN §11, rule 1)."""
    value = app.get("preferred_port")
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def live_port(app: Dict[str, Any]) -> Optional[int]:
    """Where this app is listening right now, or None.

    Three answers, weakest last. An app with an identity route is *discovered*:
    env URL, then the ledger, then a step-forward scan, and at every step the
    health body has to name it — which is what stops BuzzEdit's backend and the
    Studio being mistaken for each other on their shared 8099. An app that has
    no identity route yet (a third-party llama-server, anything before P2/P3
    lands) is trusted from its own ledger entry while its pid is alive. Failing
    both, the preferred port counts only if something is actually listening on
    it, and even then it is a guess, not an identification.
    """
    name = ledger_id(app)
    wanted = preferred_port(app)
    if not name:
        return None

    health_path = app.get("health_path")
    if health_path and wanted:
        base = buzzcaf_ports.discover(name, wanted, str(health_path))
        if base:
            try:
                from urllib.parse import urlparse

                port = urlparse(base).port
                if port:
                    return int(port)
            except Exception:
                pass

    record = buzzcaf_ports.entry(name)
    if record and buzzcaf_ports._pid_alive(record.get("pid")):
        try:
            port = int(record.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        if port and procs.port_open(port):
            return port

    # Last resort, and only for an app that has no identity route yet: "the
    # port it asked for is open" is a guess, not an identification. It was
    # caught live claiming BuzzEdit was on 8099 while Dexter was sitting there -
    # BuzzEdit really was running, but not on that port, and a wrong port is
    # worse than no port because the next launch walks straight into it.
    if wanted and not health_path and procs.port_open(wanted):
        return wanted
    return None


# ───────────────────────── probing ─────────────────────────


def _probe_url(url: str, app: Dict[str, Any]) -> str:
    """`{base}` in a probe URL means "wherever this app actually is right now".

    The alternative is a literal `http://127.0.0.1:8099/...` in the registry,
    which is a hardcoded port by another name and goes stale the first time the
    app it names has to step forward.
    """
    if "{base}" not in url:
        return url
    port = live_port(app)
    return url.replace("{base}", f"http://127.0.0.1:{port}") if port else ""


def _http_probe(url: str, expect: Optional[Dict[str, Any]]) -> Optional[bool]:
    """True/False, or None when we could not tell (no requests, odd body)."""
    try:
        import requests
    except Exception:
        return None
    try:
        res = requests.get(url, timeout=_HTTP_TIMEOUT)
    except Exception:
        return False
    if res.status_code >= 400:
        return False
    if not expect:
        return True
    try:
        body = res.json()
    except Exception:
        return True
    if not isinstance(body, dict):
        return True
    # Every expected key must match. This is what keeps BuzzEdit's backend and
    # the Studio apart on their shared port 8099.
    return all(body.get(key) == value for key, value in expect.items())


def probe(app: Dict[str, Any]) -> Dict[str, Any]:
    """Is this app up? Returns `{running, evidence, pids, detail}`.

    Never launches anything, never blocks for more than a couple of seconds,
    and says *which* probe answered so a wrong answer is diagnosable.
    """
    if app.get("handler") == "buzzcaf_client":
        # The Studio has a specialist that already knows the port walk and the
        # runtime file; asking it twice, differently, is how they disagree.
        try:
            from buzzcaf_client import buzzcaf_client

            online = bool(buzzcaf_client.health_check(force=True))
            return {"running": online, "evidence": "buzzcaf_client", "pids": [], "detail": ""}
        except Exception as exc:
            log.debug("apps: studio probe failed: %s", exc)

    # An identity route is the cheapest and the most trustworthy evidence there
    # is: it says both "something is listening" and "it is this app". It also
    # follows the app when it stepped forward, which a fixed URL cannot.
    #
    # Only for a record that publishes under its *own* name. BuzzBrain lives
    # inside the Studio and shares its port, so the Studio answering there is
    # evidence about the Studio; BuzzBrain still has to be asked separately.
    if app.get("health_path") and preferred_port(app) and ledger_id(app) == app.get("id"):
        found = live_port(app)
        if found:
            owner = procs.port_owner(found)
            pids = [int(owner["pid"])] if owner and owner.get("pid") else []
            return {"running": True, "evidence": f"port {found}", "pids": pids, "detail": ""}

    root = root_of(app)
    pids: List[int] = []
    # Process, port and HTTP probes are evidence of a *process*. A file is only
    # evidence that something once wrote it — this was caught live reporting
    # VrDeep, MastGaane and CaliberAI as running off a two-hour-old session
    # file, a database and a log file that are simply always there. So file
    # probes go last, and only count while they are fresh.
    for spec in sorted(app.get("probes", []) or [],
                       key=lambda s: 1 if ("file" in s or "file_env" in s) else 0):
        if "http" in spec:
            url = _probe_url(str(spec["http"]), app)
            if not url:
                continue  # `{base}` and we do not know where the app is
            hit = _http_probe(url, spec.get("expect"))
            if hit:
                return {"running": True, "evidence": f"http {spec['http']}", "pids": pids, "detail": ""}
            continue
        if "port" in spec:
            if procs.port_open(int(spec["port"])):
                owner = procs.port_owner(int(spec["port"]))
                if owner and owner.get("pid"):
                    pids.append(int(owner["pid"]))
                return {"running": True, "evidence": f"port {spec['port']}", "pids": pids, "detail": ""}
            continue
        if "file" in spec:
            path = os.path.join(root, str(spec["file"]))
            if _fresh(path, spec):
                return {"running": True, "evidence": f"file {spec['file']}", "pids": pids,
                        "detail": _age(path)}
            continue
        if "file_env" in spec:
            path = os.environ.get(str(spec["file_env"]) or "")
            if path and _fresh(path, spec):
                return {"running": True, "evidence": f"file ${spec['file_env']}", "pids": pids,
                        "detail": _age(path)}
            continue
        if "process" in spec:
            found = procs.find([str(spec["process"])], path_contains=spec.get("path_contains"))
            if found:
                pids = [p["pid"] for p in found]
                return {"running": True, "evidence": f"process {spec['process']}", "pids": pids,
                        "detail": ""}
            continue
    return {"running": False, "evidence": "", "pids": [], "detail": ""}


#: A file probe only counts while the app is still writing it. Long enough to
#: cover a slow render step, short enough that yesterday's session file does
#: not report a render that finished last night.
DEFAULT_FILE_MAX_AGE = 900


def _fresh(path: str, spec: Dict[str, Any]) -> bool:
    """Does this file exist *and* look like something still being written?"""
    if not path or not os.path.exists(path):
        return False
    max_age = spec.get("max_age_secs", DEFAULT_FILE_MAX_AGE)
    if max_age in (None, 0):
        return True  # an explicit "existence is enough" (a runtime file, say)
    return (time.time() - _safe_mtime(path)) <= float(max_age)


def _age(path: str) -> str:
    try:
        mins = int((time.time() - os.path.getmtime(path)) / 60)
    except OSError:
        return ""
    if mins < 1:
        return "updated just now"
    if mins < 60:
        return f"updated {mins} min ago"
    return f"updated {mins // 60} h ago"


def status(app: Dict[str, Any]) -> Dict[str, Any]:
    """A registry row plus its live state, as the UI and the tools want it."""
    state = probe(app)
    pids = state.get("pids") or []
    here = live_port(app) if state.get("running") else None
    row = {
        "id": app.get("id"),
        "label": app.get("label") or app.get("id"),
        "class": app.get("class") or "interactive",
        "running": bool(state.get("running")),
        "evidence": state.get("evidence"),
        "detail": state.get("detail"),
        "pids": pids,
        # `port` is what the UI and the spoken summaries print, so it is the
        # live one whenever we know it. `preferredPort` is the wish, kept
        # separate so "it asked for 8099" and "it is on 8100" never merge.
        "port": here or preferred_port(app),
        "livePort": here,
        "preferredPort": preferred_port(app),
        "extraPreferred": app.get("extra_preferred") or {},
        "vramMib": int(app.get("vram_mib") or 0),
        "ramMib": int(app.get("ram_mib") or 0),
        "exclusiveWith": app.get("exclusive_with") or [],
        "canLaunch": bool((app.get("launch") or {}).get("cmd")),
        "canStop": (app.get("stop") or {}).get("strategy", "none") != "none",
        "batchJob": bool(app.get("batch_job")),
        "notes": app.get("notes") or "",
        "warn": app.get("warn") or "",
        "root": root_of(app),
    }
    if pids:
        row["rssMib"] = procs.rss_mib(pids[0])
        row["uptimeSeconds"] = procs.uptime_seconds(pids[0])
    return row


def overview() -> List[Dict[str, Any]]:
    """Every app with its live state. One pass, all probes, run in parallel.

    Sequentially this took **16 s** on the live machine: seventeen apps, most of
    them stopped, each paying a TCP connect and an HTTP timeout for every dead
    port it advertises. `/api/dexter/apps` timed out in the verification suite
    and the Apps panel sat blank for a quarter of a minute.

    The probes are read-only and independent of each other, so they run on a
    small pool — the wall time becomes the slowest single app (~3.5 s, BuzzEdit
    with three probes) instead of the sum. Order is preserved because the UI
    lists apps in registry order, and one app that raises must not lose the
    other sixteen rows.
    """
    apps = all_apps()
    if len(apps) < 2:
        return [status(app) for app in apps]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, len(apps)),
                            thread_name_prefix="dexter-probe") as pool:
        return list(pool.map(status, apps))


def running() -> List[Dict[str, Any]]:
    return [row for row in overview() if row["running"]]


def last_log_line(app: Dict[str, Any], lines: int = 1) -> str:
    """The tail of the app's own log, so 'why is it stuck' has an answer."""
    root = root_of(app)
    for rel in app.get("logs", []) or []:
        path = os.path.normpath(os.path.join(root, str(rel)))
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                tail = handle.readlines()[-max(1, lines):]
            text = "".join(tail).strip()
            if text:
                return text[-2000:]
        except Exception:
            continue
    return ""


# ───────────────────────── launching ─────────────────────────


def _resolve_command(app: Dict[str, Any]) -> Optional[List[str]]:
    """The argv to run, or None when this app has nothing to launch."""
    launch = app.get("launch") or {}
    cmd = launch.get("cmd")
    if not cmd:
        return None
    root = root_of(app)
    how = launch.get("resolve") or "file"
    args = [str(a) for a in (launch.get("args") or [])]

    if how == "python":
        return [_python()] + args
    if how == "path":
        found = shutil.which(str(cmd))
        if not found:
            return None
        return [found] + args
    if how == "shell":
        return [str(cmd)] + [root if a == "." else a for a in args]

    candidate = os.path.join(root, str(cmd))
    if not os.path.exists(candidate):
        return None
    lowered = str(cmd).lower()
    if lowered.endswith(".vbs"):
        # cscript, not wscript: a .vbs launcher that fails should say so on
        # stderr rather than popping a modal dialog behind everything.
        return ["cscript", "//nologo", candidate] + args
    return [candidate] + args


def _python() -> str:
    return os.environ.get("DEXTER_APPS_PYTHON") or sys.executable or "python"


def launch(app: Dict[str, Any]) -> Dict[str, Any]:
    """Start the app the way the owner starts it. No probing, no waiting."""
    if app.get("handler") == "buzzcaf_client":
        from buzzcaf_client import buzzcaf_client

        result = buzzcaf_client.start_studio(wait_for_ready=True, timeout=30)
        return {"ok": bool(result.get("ready") or result.get("backend", {}).get("success")),
                "message": result.get("message") or "", "pid": None}

    argv = _resolve_command(app)
    if not argv:
        why = (app.get("launch") or {}).get("why") or "there is no launch script for it"
        return {"ok": False, "message": why, "pid": None}

    cwd = root_of(app)
    launch = app.get("launch") or {}
    if launch.get("cwd") and launch["cwd"] != ".":
        cwd = os.path.join(cwd, str(launch["cwd"]))
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    flags = _NO_WINDOW if launch.get("hidden") else 0
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd if os.path.isdir(cwd) else None, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
    except Exception as exc:
        return {"ok": False, "message": f"could not run {argv[0]}: {exc}", "pid": None}
    print(f"[Apps] Launched {app.get('id')} -> {' '.join(argv[:2])} (pid {proc.pid}).")
    return {"ok": True, "message": "", "pid": proc.pid}


def wait_until_up(app: Dict[str, Any], seconds: int = LAUNCH_WAIT_SECONDS) -> bool:
    """Poll the probes. Returns False rather than pretending it came up."""
    deadline = time.time() + max(1, seconds)
    while time.time() < deadline:
        if probe(app).get("running"):
            return True
        time.sleep(1.0)
    return False


# ───────────────────────── stopping ─────────────────────────


def stop(app: Dict[str, Any]) -> Dict[str, Any]:
    """Stop the app by the strategy its record names, and only that one."""
    spec = app.get("stop") or {}
    strategy = spec.get("strategy") or "none"
    if strategy == "none":
        return {"ok": False, "message": spec.get("why") or "this one has no stop route", "how": "none"}

    if app.get("handler") == "buzzcaf_client":
        result = _stop_health_route(app, spec)
        if result["ok"]:
            return result

    result = _stop_by(app, spec, strategy)
    if not result["ok"] and isinstance(spec.get("fallback"), dict):
        alt = spec["fallback"]
        result = _stop_by(app, alt, alt.get("strategy") or "none")
    return result


def _stop_by(app: Dict[str, Any], spec: Dict[str, Any], strategy: str) -> Dict[str, Any]:
    if strategy == "health_route":
        return _stop_health_route(app, spec)
    if strategy == "runtime_pid":
        path = os.path.join(root_of(app), str(spec.get("file") or ""))
        pid = _pid_from_file(path, str(spec.get("key") or "pid"))
        if not pid:
            return {"ok": False, "message": "no runtime file to read a pid from", "how": "runtime_pid"}
        return {"ok": procs.kill_tree(pid), "message": "", "how": "runtime_pid", "pid": pid}
    if strategy == "port_owner":
        # Follow the app to the port it took. Killing the owner of the port it
        # merely *asked* for would stop whoever got there first.
        owner = procs.port_owner(int(live_port(app) or spec.get("port") or 0))
        if not owner:
            return {"ok": False, "message": "nothing is listening on that port", "how": "port_owner"}
        return {"ok": procs.kill_tree(owner.get("pid")), "message": "", "how": "port_owner",
                "pid": owner.get("pid")}
    if strategy == "process_tree":
        found = procs.find([str(spec.get("process") or "")], path_contains=spec.get("path_contains"))
        if not found:
            return {"ok": False, "message": "it does not look like it is running", "how": "process_tree"}
        killed = [p["pid"] for p in found if procs.kill_tree(p["pid"])]
        return {"ok": bool(killed), "message": "", "how": "process_tree", "pids": killed}
    if strategy == "script":
        argv = _resolve_command({**app, "launch": {"cmd": spec.get("cmd"), "resolve": "file"}})
        if not argv:
            return {"ok": False, "message": f"{spec.get('cmd')} is not there", "how": "script"}
        try:
            subprocess.run(argv, cwd=root_of(app), timeout=60, capture_output=True,
                           creationflags=_NO_WINDOW)
            return {"ok": True, "message": "", "how": "script"}
        except Exception as exc:
            return {"ok": False, "message": str(exc), "how": "script"}
    if strategy == "window":
        try:
            import windows_tools

            result = windows_tools.close_window(str(spec.get("title") or app.get("label") or ""))
            return {"ok": bool(result.ok), "message": result.summary, "how": "window"}
        except Exception as exc:
            return {"ok": False, "message": str(exc), "how": "window"}
    return {"ok": False, "message": f"unknown stop strategy '{strategy}'", "how": strategy}


def _stop_health_route(app: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    url = _probe_url(str(spec.get("route") or ""), app)
    if not url:
        return {"ok": False, "message": "no shutdown route", "how": "health_route"}
    try:
        import requests

        res = requests.post(url, timeout=6) if str(spec.get("method", "POST")).upper() == "POST" \
            else requests.get(url, timeout=6)
        return {"ok": res.status_code < 400, "message": "" if res.status_code < 400 else res.text[:200],
                "how": "health_route"}
    except Exception as exc:
        return {"ok": False, "message": str(exc), "how": "health_route"}


def _pid_from_file(path: str, key: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return int(data.get(key)) if data.get(key) else None
    except Exception:
        return None


# ───────────────────────── conflicts ─────────────────────────


def conflicts(app: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apps that cannot share the card with this one and are running now."""
    names = {str(x).lower() for x in (app.get("exclusive_with") or [])}
    if not names:
        return []
    out = []
    for other in all_apps():
        if other.get("id") == app.get("id"):
            continue
        if str(other.get("id", "")).lower() not in names:
            continue
        if probe(other).get("running"):
            out.append(other)
    return out


def mid_job(app: Dict[str, Any]) -> Optional[str]:
    """Is this batch app in the middle of something? A sentence, or None.

    This is what turns a stop into a confirmation: "VrDeep has 3 jobs in its
    queue" is the difference between a click and losing an evening's render.
    """
    if not app.get("batch_job"):
        return None
    root = root_of(app)
    queue_file = app.get("queue_file")
    if queue_file:
        path = os.path.join(root, str(queue_file))
        pending = _queue_pending(path)
        if pending:
            return f"{pending} job(s) in its queue"
    session_file = app.get("session_file")
    if session_file:
        path = os.path.join(root, str(session_file))
        if os.path.exists(path) and (time.time() - _safe_mtime(path)) < 600:
            return f"its session file was {_age(path)}"
    if app.get("id") == "buzzcode":
        try:
            from code_agent import code_agent

            if code_agent.running():
                return "a code task is running on it"
        except Exception:
            pass
    return None


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _queue_pending(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return 0
    items = data if isinstance(data, list) else (
        data.get("jobs") or data.get("queue") or data.get("items") or []
    )
    if not isinstance(items, list):
        return 0
    pending = [
        item for item in items
        if not isinstance(item, dict)
        or str(item.get("status", "pending")).lower() in ("pending", "queued", "waiting", "running")
    ]
    return len(pending)


def read_queue(app_id: str = "vrdeep") -> Dict[str, Any]:
    """The queue/session pair a batch app publishes, read-only."""
    app = get(app_id)
    if not app:
        return {"found": False}
    root = root_of(app)
    out: Dict[str, Any] = {"found": True, "id": app_id, "queue": None, "session": None}
    for key, rel in (("queue", app.get("queue_file")), ("session", app.get("session_file"))):
        if not rel:
            continue
        path = os.path.join(root, str(rel))
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                out[key] = json.load(handle)
            out[f"{key}Age"] = _age(path)
        except Exception:
            out[key] = None
    out["pending"] = _queue_pending(os.path.join(root, str(app.get("queue_file") or "")))
    return out
