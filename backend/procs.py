"""
Processes, without assuming psutil (roadmap v9, D3).

The apps registry has to answer three questions about a Windows process —
*is it mine*, *who owns this port*, *stop it and its children* — and it has to
answer them on a machine where psutil may or may not be importable under the
Python that happens to be running Dexter.

psutil is the fast path (one API, no console windows, exe paths for free).
Without it, `tasklist` and PowerShell's `Get-CimInstance` give the same
answers more slowly, which is fine: none of this is on a chat turn's critical
path.

Two rules the whole module is built around:

* **Match on the path, not just the name.** Half the apps under
  `Buzzcaf_Media` are `python.exe`. Killing "python.exe" because BuzzEdit was
  asked to stop would take VrDeep's render with it.
* **Never a bare `kill`.** Stops go to the process *tree*, and a tree is only
  ever taken down when a caller named it explicitly.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

try:
    import psutil  # type: ignore

    HAVE_PSUTIL = True
except Exception:  # pragma: no cover - depends on the interpreter Dexter runs under
    psutil = None  # type: ignore
    HAVE_PSUTIL = False

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _norm(text: Optional[str]) -> str:
    return (text or "").replace("/", "\\").lower()


# ───────────────────────── finding ─────────────────────────


def find(
    names: Optional[List[str]] = None,
    path_contains: Optional[str] = None,
    cmdline_contains: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Live processes matching an image name and/or a path fragment.

    `path_contains` is checked against both the executable path and the command
    line, because a launcher (`pythonw.exe -m foo`) has the project only in its
    arguments while a venv interpreter has it only in its path.
    """
    wanted = {n.lower() for n in (names or []) if n}
    needle = _norm(path_contains)
    arg_needle = _norm(cmdline_contains)
    out: List[Dict[str, Any]] = []

    for proc in _iter_processes():
        name = (proc.get("name") or "").lower()
        if wanted and name not in wanted:
            continue
        haystack = _norm(proc.get("exe")) + " " + _norm(proc.get("cmdline"))
        if needle and needle not in haystack:
            continue
        if arg_needle and arg_needle not in _norm(proc.get("cmdline")):
            continue
        out.append(proc)
    return out


def _iter_processes() -> List[Dict[str, Any]]:
    if HAVE_PSUTIL:
        rows = []
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline", "create_time"]):
            try:
                info = proc.info
                cmd = info.get("cmdline") or []
                rows.append({
                    "pid": info.get("pid"),
                    "name": info.get("name") or "",
                    "exe": info.get("exe") or "",
                    "cmdline": " ".join(cmd) if isinstance(cmd, list) else str(cmd),
                    "startedAt": info.get("create_time"),
                })
            except Exception:
                # A process can exit between the listing and the read; that is
                # normal, not an error worth surfacing.
                continue
        return rows
    return _iter_processes_powershell()


def _iter_processes_powershell() -> List[Dict[str, Any]]:
    """Fallback: one CIM query for name, pid, path and command line."""
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=25, creationflags=_NO_WINDOW,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return []
        import json

        data = json.loads(res.stdout)
        if isinstance(data, dict):
            data = [data]
        return [
            {
                "pid": int(row.get("ProcessId") or 0),
                "name": row.get("Name") or "",
                "exe": row.get("ExecutablePath") or "",
                "cmdline": row.get("CommandLine") or "",
                "startedAt": None,
            }
            for row in data
        ]
    except Exception:
        return []


def is_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    if HAVE_PSUTIL:
        try:
            return psutil.pid_exists(int(pid))
        except Exception:
            return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
            text=True, errors="ignore", creationflags=_NO_WINDOW,
        )
        return str(pid) in out
    except Exception:
        return False


# ───────────────────────── ports ─────────────────────────


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """A plain TCP knock. A closed local port *times out* on this Windows rather
    than refusing, so an HTTP probe without this costs seconds per miss."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def port_owner(port: int) -> Optional[Dict[str, Any]]:
    """The process listening on `port`, or None."""
    pid = None
    if HAVE_PSUTIL:
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == int(port) and conn.status == "LISTEN":
                    pid = conn.pid
                    break
        except Exception:
            pid = None
    if pid is None:
        pid = _port_owner_netstat(port)
    if not pid:
        return None
    for proc in _iter_processes():
        if proc.get("pid") == pid:
            return proc
    return {"pid": pid, "name": "", "exe": "", "cmdline": ""}


def _port_owner_netstat(port: int) -> Optional[int]:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "TCP"], text=True, errors="ignore",
            creationflags=_NO_WINDOW, timeout=15,
        )
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING":
            local = parts[1]
            if local.rsplit(":", 1)[-1] == str(port):
                try:
                    return int(parts[4])
                except ValueError:
                    return None
    return None


# ───────────────────────── stopping ─────────────────────────


def kill_tree(pid: Optional[int], timeout: float = 5.0) -> bool:
    """Terminate a process and its children, politely first.

    Used only where a registry record explicitly asks for `process_tree`, and
    (for anything mid-job) only after the owner has confirmed. A render that
    dies without notice is hours of work.
    """
    if not pid:
        return False
    pid = int(pid)
    if HAVE_PSUTIL:
        try:
            parent = psutil.Process(pid)
        except Exception:
            return False
        family = []
        try:
            family = parent.children(recursive=True)
        except Exception:
            family = []
        for proc in family + [parent]:
            try:
                proc.terminate()
            except Exception:
                pass
        gone, alive = psutil.wait_procs(family + [parent], timeout=timeout)
        for proc in alive:
            try:
                proc.kill()
            except Exception:
                pass
        return not is_alive(pid)
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=timeout + 5, creationflags=_NO_WINDOW,
        )
    except Exception:
        return False
    time.sleep(0.3)
    return not is_alive(pid)


def rss_mib(pid: Optional[int]) -> Optional[int]:
    """Resident memory of one process, for "what's running" lines."""
    if not (pid and HAVE_PSUTIL):
        return None
    try:
        return int(psutil.Process(int(pid)).memory_info().rss) // (1024 * 1024)
    except Exception:
        return None


def uptime_seconds(pid: Optional[int]) -> Optional[int]:
    if not (pid and HAVE_PSUTIL):
        return None
    try:
        return int(time.time() - psutil.Process(int(pid)).create_time())
    except Exception:
        return None


def availability() -> Dict[str, Any]:
    return {"psutil": HAVE_PSUTIL, "os": os.name}
