"""
Check that Dexter can actually work before starting it.

Starting Dexter used to be an act of faith: the launcher killed two ports and
ran the app. If Ollama was down, the configured model was not pulled, the UI was
never built, or an optional dependency was missing, you found out later as
mysterious misbehaviour - or, in the case of the GPU being occupied, as replies
that took nine seconds and no explanation at all.

This prints one readable report and says what to do about anything wrong.

    python backend/preflight.py           report and exit 0 unless something is fatal
    python backend/preflight.py --quiet   only print problems

Exit code is 1 only for FAIL rows - things that genuinely stop Dexter working.
A WARN degrades an optional capability and never blocks startup.
"""

import argparse
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import env_boot  # noqa: F401,E402  - loads .env for every entry point

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OK, WARN, FAIL = "OK", "WARN", "FAIL"

_SYMBOL = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}

# Optional dependencies and the capability each one buys. Absent means degraded,
# never broken - Dexter reports what is missing rather than crashing.
_OPTIONAL = [
    ("sentence_transformers", "semantic intent matching and memory recall"),
]

_REQUIRED = [
    ("fastapi", "the API server"),
    ("uvicorn", "the API server"),
    ("requests", "talking to the local model"),
]


class Report:
    def __init__(self, quiet: bool = False) -> None:
        self.rows = []
        self.quiet = quiet

    def add(self, status: str, title: str, detail: str = "", action: str = "") -> None:
        self.rows.append((status, title, detail, action))
        if self.quiet and status == OK:
            return
        line = f"{_SYMBOL[status]} {title}"
        if detail:
            line += f" - {detail}"
        print(line)
        if action and status != OK:
            print(f"       -> {action}")

    @property
    def failed(self) -> bool:
        return any(row[0] == FAIL for row in self.rows)

    def problem_lines(self):
        """WARN/FAIL rows as plain text - what a dialog or log should show."""
        lines = []
        for status, title, detail, action in self.rows:
            if status == OK:
                continue
            line = f"{_SYMBOL[status]} {title}"
            if detail:
                line += f" - {detail}"
            if action:
                line += f"  -> {action}"
            lines.append(line)
        return lines

    def summary(self) -> str:
        counts = {OK: 0, WARN: 0, FAIL: 0}
        for status, *_ in self.rows:
            counts[status] += 1
        return f"{counts[OK]} ok, {counts[WARN]} warning(s), {counts[FAIL]} failure(s)"


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def check_python_deps(report: Report) -> None:
    missing_required = [(m, why) for m, why in _REQUIRED if not _installed(m)]
    if missing_required:
        names = ", ".join(m for m, _ in missing_required)
        report.add(FAIL, "Python dependencies", f"missing {names}",
                   "pip install -r requirements.txt")
    else:
        report.add(OK, "Python dependencies", "core packages present")

    for module, capability in _OPTIONAL:
        if not _installed(module):
            report.add(WARN, f"Optional: {module}", f"{capability} unavailable",
                       f"pip install {module.replace('_', '-')}")


def check_env(report: Report) -> None:
    if os.path.exists(os.path.join(ROOT, ".env")):
        report.add(OK, "Configuration", ".env loaded")
    else:
        report.add(WARN, "Configuration", "no .env file",
                   "copy .env.example to .env (defaults work, but this is where you tune it)")


def check_frontend(report: Report) -> None:
    index = os.path.join(ROOT, "dist", "index.html")
    if os.path.exists(index):
        report.add(OK, "Frontend build", "dist/index.html present")
    else:
        report.add(FAIL, "Frontend build", "dist/index.html missing",
                   "npm install && npm run build")


def check_data_dir(report: Report) -> None:
    data = os.path.join(ROOT, "data")
    try:
        os.makedirs(data, exist_ok=True)
        probe = os.path.join(data, ".preflight")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        report.add(OK, "Data directory", "writable")
    except Exception as exc:
        report.add(FAIL, "Data directory", str(exc),
                   "Dexter cannot persist reminders, tasks, or memory without it")


def check_desktop_shell(report: Report) -> None:
    """
    The window itself.

    This check exists because preflight once reported "0 failures" and then
    start_dexter.bat died on `ModuleNotFoundError: No module named 'webview'`.
    A preflight that passes and then cannot launch the app is worse than none,
    so the shell it actually starts is checked like anything else.
    """
    if not _installed("webview"):
        report.add(FAIL, "Desktop shell", "pywebview missing - the window cannot open",
                   "pip install pywebview")
        return

    report.add(OK, "Desktop shell", "pywebview present")


def check_embeddings_cache(report: Report) -> None:
    """
    MiniLM powers Tier-1 routing and memory recall. When its snapshot is cached
    Dexter loads it offline; when it is not, the first launch downloads ~90 MB
    and every launch before that stalls on the network (roadmap v5, 1.3).
    """
    try:
        import embeddings
    except Exception as exc:
        report.add(WARN, "Embeddings", f"could not check: {exc}")
        return
    if not _installed("sentence_transformers"):
        return
    if embeddings.cached_locally():
        report.add(OK, "Embeddings", f"{embeddings.MODEL_NAME} cached; loads offline")
    else:
        report.add(WARN, "Embeddings", f"{embeddings.MODEL_NAME} not cached yet",
                   "The first launch needs internet once to download it (~90 MB)")


def check_system_integrations(report: Report) -> None:
    """
    Volume, brightness, clipboard, screenshots, windows, and now-playing.

    Each of these degrades to "that tool is unavailable" when its package is
    missing - individually polite, collectively the assistant quietly losing
    thirteen tools. All nine of these packages were absent on this machine while
    everything else looked fine, so the capability matrix is reported directly
    rather than inferred from imports.
    """
    try:
        import media_tools
        import windows_tools

        capabilities = dict(windows_tools.availability())
        capabilities.update(media_tools.availability())
    except Exception as exc:
        report.add(WARN, "System control", f"could not check: {exc}")
        return

    dead = [name for name, live in capabilities.items() if not live]
    if not dead:
        report.add(OK, "System control", f"{len(capabilities)} capabilities live")
        return

    hints = {
        "volume": "pycaw", "brightness": "screen-brightness-control",
        "clipboard": "pyperclip", "screenshot": "mss", "windows": "pygetwindow",
        "smtc": "winsdk", "mediaKeys": "winsdk",
    }
    packages = sorted({hints.get(name, name) for name in dead})
    report.add(WARN, "System control", f"unavailable: {', '.join(dead)}",
               f"pip install {' '.join(packages)}")


def check_reminders(report: Report) -> None:
    """The one degradation that silently guts the product."""
    if _installed("apscheduler"):
        report.add(OK, "Reminders", "APScheduler present, reminders will fire")
    else:
        report.add(FAIL, "Reminders", "APScheduler missing - reminders are stored but NEVER FIRE",
                   "pip install apscheduler")


def check_llm(report: Report) -> None:
    try:
        from hybrid_llm import llm_router
    except Exception as exc:
        report.add(FAIL, "Local model", f"could not load the provider layer: {exc}")
        return

    providers = llm_router.get_available_providers(force=True)
    online = [
        name for name, key in (
            ("llama-server", "llamacpp"), ("LM Studio", "lmStudio"), ("Ollama", "ollama"),
        )
        if providers.get(key, {}).get("online")
    ]
    if not online:
        report.add(FAIL, "Local model", "no local provider is reachable",
                   "Start Ollama (`ollama serve`) or LM Studio, then retry")
        return

    provider = llm_router.resolve_provider("auto")
    model = llm_router._model_for(provider, None)
    report.add(OK, "Local model", f"{model} via {provider} ({', '.join(online)} up)")

    # Is the configured model actually pulled? A model named in .env but never
    # pulled fails at request time with nothing useful in the UI.
    try:
        listing = llm_router.list_models()
        available = {
            m.get("id") or m.get("name")
            for entry in listing.get("providers", {}).values() if isinstance(entry, dict)
            for m in (entry.get("models") or [])
            if isinstance(m, dict)
        }
        if available and model not in available:
            report.add(WARN, "Configured model", f"{model} is not in the provider's model list",
                       f"ollama pull {model}, or change OLLAMA_MODEL in .env")
    except Exception:
        pass


def check_runtime(report: Report) -> None:
    """The GPU question - the single biggest cause of Dexter feeling broken."""
    try:
        import runtime_health
        from hybrid_llm import llm_router

        provider = llm_router.resolve_provider("auto")
        diagnosis = runtime_health.diagnose(
            provider, llm_router._model_for(provider, None), llm_router.ollama_url, force=True
        )
    except Exception as exc:
        report.add(WARN, "Runtime", f"could not check: {exc}")
        return

    if diagnosis["ok"]:
        gpu = diagnosis.get("gpu")
        detail = "no obvious bottleneck"
        if diagnosis.get("device") == "gpu":
            detail = "model resident on the GPU"
        elif gpu:
            detail = f"{gpu['freeMb']} MB VRAM free on {gpu['name']}"
        report.add(OK, "Runtime", detail)
        return

    for warning in diagnosis["warnings"]:
        report.add(WARN, "Runtime", warning["message"], warning["action"])


def run_checks(quiet: bool = True) -> Report:
    """Every check, in order. desktop_app.py calls this before opening a window."""
    report = Report(quiet=quiet)
    check_python_deps(report)
    check_embeddings_cache(report)
    check_env(report)
    check_data_dir(report)
    check_frontend(report)
    check_llm(report)
    check_runtime(report)
    check_reminders(report)
    check_system_integrations(report)
    check_desktop_shell(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that Dexter can run.")
    parser.add_argument("--quiet", action="store_true", help="only print problems")
    args = parser.parse_args()

    if not args.quiet:
        print("=== Dexter preflight ===")
    report = run_checks(quiet=args.quiet)

    print(f"--- {report.summary()} ---")
    if report.failed:
        print("Dexter will not start correctly until the failures above are fixed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
