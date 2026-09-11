"""
buzzcode in its own terminal window.

Dexter launches this script in a fresh console (CREATE_NEW_CONSOLE) for every
code task. It runs buzzcode in one-shot mode with JSON-lines events, renders
those events live in the window (turns, tool calls, verify results, the
model's text) and mirrors every raw line into the job log that Dexter tails.
When buzzcode exits it has already stopped the llama-server it started, so the
big model is out of memory; the window then waits for a key so the result can
be read.

Standalone on purpose: no Dexter imports, so it runs under any python.

    python code_console.py <job-spec.json>

The spec carries: exe, cwd, mode, task, log, project, jobId.
"""

import json
import os
import subprocess
import sys
import time

DIM, RESET, RED, GREEN, YELLOW, CYAN, MAGENTA, GREY, BOLD = (
    "\x1b[2m", "\x1b[0m", "\x1b[31m", "\x1b[32m", "\x1b[33m", "\x1b[36m", "\x1b[35m", "\x1b[90m", "\x1b[1m",
)


def _console_setup(title: str) -> None:
    """UTF-8 output, ANSI colours, and a window title on Windows."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleTitleW(title)
        handle = k32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def _compact(value) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= 160 else text[:160] + "…"


class Renderer:
    def __init__(self) -> None:
        self.in_reasoning = False
        self.out = sys.stdout

    def _w(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()

    def _end_reasoning(self) -> None:
        if self.in_reasoning:
            self._w(RESET + "\n")
            self.in_reasoning = False

    def event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "reasoning_delta":
            if not self.in_reasoning:
                self._w(DIM)
                self.in_reasoning = True
            self._w(ev.get("text") or "")
        elif kind == "content_delta":
            self._end_reasoning()
            self._w(ev.get("text") or "")
        elif kind == "assistant_message":
            self._end_reasoning()
            self._w("\n")
        elif kind == "turn_start":
            self._end_reasoning()
            self._w(f"{GREY}── turn {ev.get('turn')} · {ev.get('task')} · effort {ev.get('effort')}{RESET}\n")
        elif kind == "tool_call_start":
            self._end_reasoning()
            self._w(f"{CYAN}▸ {ev.get('name')} {_compact(ev.get('arguments') or {})}{RESET}\n")
        elif kind == "tool_call_result":
            output = str(ev.get("output") or "")
            lines = output.splitlines()
            first = "\n  ".join(lines[:8])
            more = max(0, len(lines) - 8)
            colour = RED if ev.get("is_error") else GREY
            tail = f"\n  … {more} more lines" if more else ""
            self._w(f"  {colour}{ev.get('name')} → {first}{tail}{RESET}\n")
        elif kind == "permission_requested":
            self._w(f"{YELLOW}? permission needed for {ev.get('name')} (denied in this mode){RESET}\n")
        elif kind == "verify":
            state = f"{GREEN}ok" if ev.get("ok") else f"{RED}FAILED"
            self._w(f"{MAGENTA}⚙ verify `{ev.get('command')}` → {state}{RESET}\n")
        elif kind == "metrics":
            ratio = float(ev.get("cache_ratio") or 0) * 100
            self._w(
                f"{GREY}[ctx {ev.get('ctx_used')}/{ev.get('ctx_total')} · {float(ev.get('decode_tps') or 0):.1f} tok/s"
                f" · cache {ratio:.0f}%]{RESET}\n"
            )
        elif kind == "warning":
            self._w(f"{YELLOW}! {ev.get('text')}{RESET}\n")
        elif kind == "error":
            self._end_reasoning()
            self._w(f"{RED}✗ {ev.get('text')}{RESET}\n")
        elif kind == "compaction":
            self._w(f"{YELLOW}↻ compacted {ev.get('messages_before')}→{ev.get('messages_after')} messages{RESET}\n")
        elif kind == "finished":
            self._end_reasoning()
            self._w(f"{GREY}— finished ({ev.get('reason')}, {ev.get('turns')} turns){RESET}\n")
        elif kind == "agent_spawned":
            task = str(ev.get("task") or "").splitlines()[:1]
            self._w(f"{MAGENTA}▸ worker #{ev.get('id')} ({ev.get('kind')}) started: {(task[0] if task else '')[:120]}{RESET}\n")
        elif kind == "agent_finished":
            self._w(f"{MAGENTA}▸ worker #{ev.get('id')} finished ({ev.get('outcome')}, {ev.get('turns')} turns){RESET}\n")


def _kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def main(spec_path: str) -> int:
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    project = spec.get("project") or os.path.basename(spec["cwd"])
    _console_setup(f"buzzcode · {project} · {spec['task'][:60]}")

    print(f"{BOLD}buzzcode{RESET} in {CYAN}{project}{RESET}  ({spec.get('mode', 'edits')} mode)  job {spec.get('jobId', '')}")
    print(f"{GREY}{spec['cwd']}{RESET}")
    print(f"\n{BOLD}Task:{RESET} {spec['task']}\n")
    print(f"{GREY}Dexter follows this window through {os.path.basename(spec['log'])}. Ctrl-C cancels.{RESET}\n")

    argv = [spec["exe"], "-C", spec["cwd"], "--mode", spec.get("mode", "edits"), "--json", "-p", spec["task"]]
    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")
    started = time.time()
    renderer = Renderer()
    code = 1
    with open(spec["log"], "a", encoding="utf-8") as log:
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, cwd=spec["cwd"], env=env)
        except OSError as exc:
            print(f"{RED}Could not start buzzcode: {exc}{RESET}")
            log.write(json.dumps({"type": "error", "text": f"could not start buzzcode: {exc}"}) + "\n")
            log.write(json.dumps({"type": "launcher_exit", "code": 127}) + "\n")
            log.flush()
            _hold()
            return 127
        try:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                log.write(line)
                log.flush()
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    sys.stdout.write(line)
                    continue
                try:
                    renderer.event(ev)
                except Exception:
                    pass
            code = proc.wait()
        except KeyboardInterrupt:
            print(f"\n{YELLOW}Cancelling…{RESET}")
            _kill_tree(proc.pid)
            code = 130
            log.write(json.dumps({"type": "error", "text": "cancelled from the window"}) + "\n")
        log.write(json.dumps({"type": "launcher_exit", "code": code, "elapsed": round(time.time() - started, 1)}) + "\n")
        log.flush()

    elapsed = round(time.time() - started)
    if code == 0:
        print(f"\n{GREEN}buzzcode finished in {elapsed}s; its model server is stopped and the memory released.{RESET}")
    else:
        print(f"\n{RED}buzzcode exited with code {code} after {elapsed}s.{RESET}")
    _hold()
    return code


def _hold() -> None:
    try:
        input(f"{GREY}Press Enter to close this window.{RESET}")
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: code_console.py <job-spec.json>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
