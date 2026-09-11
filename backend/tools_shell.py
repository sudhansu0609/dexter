"""
The long tail: one PowerShell command, shown first, run only on a yes (v11.1).

Dexter has a hundred-odd tools and still meets sentences none of them cover:
"what's my IP", "flush the DNS", "how much space is left on D", "restart
explorer", "turn off wifi". The owner's ask is that Dexter can do anything they
can do at this PC, and at this PC the honest general instrument is a shell.

So this is a shell with the safeguards a colleague would want:

  * It **always asks**, and the question is the exact command. Nothing runs on
    the model's say-so alone.
  * A short denylist of the things no assistant should run from a spoken
    sentence (formatting a disk, wiping a drive root, disabling the firewall,
    deleting shadow copies, the registry hives, BitLocker). Those are refused
    outright, before the question, with the reason.
  * Bounded: one command, a timeout, output trimmed to what a reply can carry.
  * Never reachable by the deterministic tiers. The router's slot filler would
    otherwise take "what's my ip" as the command text; only a model that has
    read the description composes the command (see `router._extract_slots`).
"""

import os
import re
import subprocess
from typing import Any, Dict, List

from tool_registry import ToolResult, tool

COMMAND_TIMEOUT_SECONDS = float(os.getenv("DEXTER_SHELL_TIMEOUT", "60") or 60)
OUTPUT_BUDGET = 1500

#: Patterns that are refused outright. Each is (regex, why).
DENYLIST: List[tuple] = [
    (r"\bformat(?:\.com)?\s+[a-z]:", "formatting a drive"),
    (r"\bformat-volume\b|\bclear-disk\b|\binitialize-disk\b|\bdiskpart\b", "rewriting a disk"),
    (r"\b(?:remove-item|rm|rd|rmdir|del|erase|ri)\b[^|;&]*\s['\"]?[a-z]:[\\/]?['\"]?(?:\s|$|-)", "deleting a drive root"),
    # Anything under Windows or Program Files; the Users folder itself or a
    # whole profile - but not a file or folder *inside* a profile, which is
    # exactly the kind of tidy-up this tool is for.
    (r"\b(?:remove-item|rm|rd|rmdir|del|erase|ri)\b[^|;&]*[\\/](?:windows|program files(?: \(x86\))?)(?:[\\/]|\b)", "deleting a system folder"),
    (r"\b(?:remove-item|rm|rd|rmdir|del|erase|ri)\b[^|;&]*[\\/]users(?:[\\/][^\\/\s'\"]+)?[\\/]?['\"]?(?:\s|$|-)", "deleting a user profile"),
    (r"\bcipher\s+/w\b", "wiping free space"),
    (r"\bvssadmin\b.*\bdelete\b|\bwbadmin\b.*\bdelete\b", "deleting backups or shadow copies"),
    (r"\bbcdedit\b|\bbootrec\b", "changing how Windows boots"),
    (r"\bmanage-bde\b|\bdisable-bitlocker\b|\benable-bitlocker\b", "changing BitLocker"),
    (r"\b(?:reg(?:\.exe)?\s+delete|remove-item)\b[^|;&]*\bhk(?:lm|ey_local_machine)\b", "deleting from the machine registry"),
    (r"\bset-mppreference\b.*\bdisablerealtimemonitoring\b|\bnetsh\s+advfirewall\s+set\s+\w+\s+state\s+off\b", "turning off protection"),
    (r"\bset-executionpolicy\b\s+unrestricted", "removing script protection"),
    (r"\binvoke-expression\b|\biex\b\s*\(|\bdownloadstring\b|\bcurl\b[^|]*\|\s*(?:iex|powershell|sh|bash)\b", "running code fetched from the internet"),
    (r"\bstop-computer\b|\brestart-computer\b|\bshutdown(?:\.exe)?\s+/", "power (use the restart / shutdown tools, which ask properly)"),
    (r"\btakeown\b.*[\\/]windows\b|\bicacls\b.*[\\/]windows\b", "changing ownership of Windows files"),
]

_DENY = [(re.compile(pat, re.I), why) for pat, why in DENYLIST]


def refused_because(command: str) -> str:
    """Why a command is refused, or '' when it may be asked about."""
    flat = " ".join((command or "").split())
    for pattern, why in _DENY:
        if pattern.search(flat):
            return why
    return ""


def _run(command: str) -> Dict[str, Any]:
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        encoding="utf-8",
        errors="replace",
    )
    return {"code": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}


def _trim(text: str, budget: int = OUTPUT_BUDGET) -> str:
    text = (text or "").strip()
    if len(text) <= budget:
        return text
    return text[: budget - 20].rstrip() + "\n… (trimmed)"


def _prompt(args: Dict[str, Any]) -> str:
    command = str(args.get("command") or "").strip()
    return f"I'd run this in PowerShell:\n\n    {command}\n\nSay yes and I'll run it."


@tool(
    name="run_command",
    description=(
        "Run one PowerShell command on this PC, for things no other tool covers: network "
        "settings, disk space, services, processes, Windows features. The user sees the exact "
        "command and must say yes before it runs. Compose the command yourself; do not pass "
        "the user's sentence. Prefer a specific tool when one exists."
    ),
    params={
        "command": {"type": "string", "description": "The PowerShell command to run (one line)."},
        "purpose": {"type": "string", "description": "Three to eight words on what it is for.", "required": False},
    },
    examples=[
        "what's my ip address",
        "flush the dns cache",
        "how much free space is on drive d",
        "turn off wifi",
        "restart windows explorer",
        "list the services that failed to start",
        "which process is using port 8099",
    ],
    confirm=True,
    category="system",
)
def run_command(command: str, purpose: str = "") -> ToolResult:
    command = " ".join((command or "").split())
    if not command:
        return ToolResult.fail("There's no command to run.")
    why = refused_because(command)
    if why:
        return ToolResult.fail(f"I won't run that: it means {why}. Do that one yourself if you're sure.")
    try:
        out = _run(command)
    except subprocess.TimeoutExpired:
        return ToolResult.fail(f"That took longer than {int(COMMAND_TIMEOUT_SECONDS)} s, so I stopped it.")
    except Exception as exc:
        return ToolResult.fail(f"Couldn't run it: {str(exc).splitlines()[0][:200]}")
    stdout, stderr = _trim(out["stdout"]), _trim(out["stderr"], 400)
    data = {"command": command, "purpose": purpose, **out}
    if out["code"] != 0:
        detail = stderr or stdout or f"exit code {out['code']}"
        return ToolResult(ok=False, summary=f"It failed: {detail}", data=data)
    if not stdout:
        return ToolResult(ok=True, summary="Done - no output.", data=data)
    return ToolResult(ok=True, summary=stdout, data=data)


run_command._dexter_confirm_prompt = _prompt
