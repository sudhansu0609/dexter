"""
Function-calling eval harness for the local model behind Tier 2.

Where test_router.py verifies Dexter's own deterministic routing, this measures
the MODEL: given a request, does the agent loop pick the right tool with valid
arguments, or does it hallucinate a tool, drop the arguments, or just chat?
Use it to compare candidates (Qwen2.5-7B vs Llama-3.1-8B vs Hermes...) with a
number instead of a feeling.

Needs a live LLM (LM Studio / Ollama), so it is skipped by default:

    set DEXTER_RUN_EVALS=1 && pytest backend/test_agent_eval.py -v -s
or simply:
    python backend/test_agent_eval.py

No tool is ever executed - the first tool call is captured and the loop is
stopped, so running this cannot launch apps or write files.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import google_tools  # noqa: F401  - registers gmail/calendar tools
import media_tools  # noqa: F401  - registers media tools
import tools_assistant  # noqa: F401  - registers reminder/memory tools
import tools_core  # noqa: F401  - registers core tools
import windows_tools  # noqa: F401  - registers system tools

import router
from hybrid_llm import llm_router
from tool_registry import get_tool, validate_args

pytestmark = pytest.mark.skipif(
    os.getenv("DEXTER_RUN_EVALS") != "1",
    reason="live-LLM eval; set DEXTER_RUN_EVALS=1 to run",
)

# (prompt, expected tool, args that must appear (subset match, case-insensitive
# substring for string values), or None when any args are acceptable)
EVAL_CASES = [
    ("please get chrome up on the screen for me", "launch_app", {"app_name": "chrome"}),
    ("I need to see what is on my schedule for the day", "get_agenda", None),
    ("jot this down: buy a new SSD for the render rig", "create_note", None),
    ("could you make the computer louder", "set_volume", None),
    ("whatever song this is, skip to the next one", "media_control", None),
    ("set something up to ping me about the oven in 25 minutes", "create_reminder", None),
    ("I want you to keep in mind that my editor prefers 4k footage", "remember_fact", None),
    ("pull up the site github.com in a browser", "open_website", {"url": "github"}),
    ("locate that invoice pdf somewhere in my documents", "search_files", {"query": "invoice"}),
    ("what does my inbox look like, anything unread?", "check_email", None),
    ("capture what is on my screen right now", "take_screenshot", None),
    ("get tomorrow blocked out at 2pm for a call with the editor", "create_calendar_event", None),
    ("darken the display a little", "set_brightness", None),
    ("what track is playing right now", "get_now_playing", None),
    ("copy the words hello world onto the clipboard", "write_clipboard", {"text": "hello world"}),
]

# Prompts that must NOT produce a tool call - the model should just answer.
CHAT_CASES = [
    "what is the tallest mountain on earth",
    "explain the difference between RAM and VRAM in one sentence",
]


class _ToolCaptured(Exception):
    def __init__(self, name, args):
        self.name = name
        self.args = args


def _capture_run_tool(tool_name, args, confirmed=False, on_event=None, session="default"):
    raise _ToolCaptured(tool_name, args)


def _llm_online() -> bool:
    health = llm_router.get_available_providers(force=True)
    return health["lmStudio"]["online"] or health["ollama"]["online"]


def _args_match(expected, actual) -> bool:
    if not expected:
        return True
    for key, want in expected.items():
        got = actual.get(key)
        if got is None:
            return False
        if isinstance(want, str):
            if want.lower() not in str(got).lower():
                return False
        elif got != want:
            return False
    return True


def run_eval(verbose: bool = True):
    """Returns the scoreboard dict; prints it when verbose."""
    if not _llm_online():
        raise RuntimeError("No local LLM online (LM Studio / Ollama). Start one first.")

    original = router._run_tool
    router._run_tool = _capture_run_tool
    rows = []
    try:
        for prompt, expected_tool, expected_args in EVAL_CASES:
            started = time.time()
            try:
                router.agent_loop(prompt)
                # No exception: the model answered without calling any tool.
                rows.append({
                    "prompt": prompt, "expected": expected_tool, "got": "(no tool)",
                    "tool_ok": False, "args_ok": False,
                    "secs": time.time() - started,
                })
            except _ToolCaptured as call:
                spec = get_tool(call.name)
                if spec:
                    args_valid, cleaned, _err = validate_args(spec, call.args)
                else:
                    args_valid, cleaned = False, {}
                rows.append({
                    "prompt": prompt, "expected": expected_tool, "got": call.name,
                    "tool_ok": call.name == expected_tool,
                    "args_ok": args_valid and _args_match(expected_args, cleaned),
                    "secs": time.time() - started,
                })

        chat_rows = []
        for prompt in CHAT_CASES:
            started = time.time()
            try:
                router.agent_loop(prompt)
                chat_rows.append({"prompt": prompt, "clean": True, "secs": time.time() - started})
            except _ToolCaptured as call:
                chat_rows.append({
                    "prompt": prompt, "clean": False, "got": call.name,
                    "secs": time.time() - started,
                })
    finally:
        router._run_tool = original

    tool_hits = sum(1 for r in rows if r["tool_ok"])
    args_hits = sum(1 for r in rows if r["tool_ok"] and r["args_ok"])
    chat_clean = sum(1 for r in chat_rows if r["clean"])
    health = llm_router.get_available_providers()
    provider = "lmstudio" if health["lmStudio"]["online"] else "ollama"
    model = llm_router._model_for(provider, None)

    board = {
        "model": model,
        "provider": provider,
        "tool_accuracy": f"{tool_hits}/{len(rows)}",
        "args_accuracy": f"{args_hits}/{len(rows)}",
        "chat_restraint": f"{chat_clean}/{len(chat_rows)}",
        "avg_secs": round(sum(r["secs"] for r in rows) / max(len(rows), 1), 2),
    }

    if verbose:
        print("\n" + "=" * 64)
        print(f"Agent eval - {model} via {provider}")
        print("=" * 64)
        for r in rows:
            mark = "OK " if (r["tool_ok"] and r["args_ok"]) else ("~  " if r["tool_ok"] else "X  ")
            print(f"[{mark}] {r['prompt'][:52]:<52} -> {r['got']} ({r['secs']:.1f}s)")
        for r in chat_rows:
            mark = "OK " if r["clean"] else "X  "
            extra = "" if r["clean"] else f" (called {r.get('got')})"
            print(f"[{mark}] {r['prompt'][:52]:<52} -> chat{extra}")
        print("-" * 64)
        print(f"tool accuracy   {board['tool_accuracy']}")
        print(f"args accuracy   {board['args_accuracy']}")
        print(f"chat restraint  {board['chat_restraint']}")
        print(f"avg latency     {board['avg_secs']}s")
    return board


def test_agent_eval():
    board = run_eval()
    # Deliberately loose floor: this is a scoreboard, not a gate. A model
    # under 60% tool accuracy is not usable as Dexter's Tier 2.
    hits, total = board["tool_accuracy"].split("/")
    assert int(hits) / int(total) >= 0.6, f"tool accuracy too low: {board}"


if __name__ == "__main__":
    run_eval()
