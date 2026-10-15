"""
Measure candidate models on THIS machine, with Dexter's own tools (v13).

For each Ollama model: cold-load time, VRAM as Ollama reports it, chat speed
(tokens/s and wall time on three short questions), and tool choice on the
router's eval cases - does the model pick the right tool with the right
arguments when handed the same 8 candidates Dexter would hand it?

Nothing is executed: tool calls are captured, never run. Thinking is turned
off (`think: false`) because an assistant that deliberates for ten seconds
before "close notepad" is not a fast tier.

    python backend/bench_models.py qwen2.5:3b qwen3.5:4b gemma4:e2b-it-qat
    python backend/bench_models.py --json out.json <models...>

`--fast-act` answers a different question (v13, P2.3): of the 30 cases, how many
would the configured fast model *settle on its own*, without the smart model
ever loading? That is the router's own rule, not a judgement of the model - a
case is settled when the fast tier would reach it (an instruction, not a chain),
the model calls a tool it was offered, and the arguments validate. Nothing runs.

    python backend/bench_models.py --fast-act qwen3.5:4b qwen2.5:3b
"""

import json
import os
import sys
import time
from typing import Any, Dict, List

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import router  # noqa: E402
import tools_browser  # noqa: E402,F401  - the hands, so the hard cases have their tools
import tools_desktop  # noqa: E402,F401
import tools_files  # noqa: E402,F401
import tools_shell  # noqa: E402,F401
import tools_web  # noqa: E402,F401
from test_agent_eval import EVAL_CASES as EASY_CASES  # noqa: E402
from tool_registry import get_tool, validate_args  # noqa: E402

# The easy set is what a 7B already gets 15/15 on. These are the sentences a
# desktop assistant actually meets: two acts in one breath, an argument that
# must be composed rather than copied, a window that must be named, and two
# that must NOT become a tool call. Only the FIRST call is judged.
HARD_CASES = [
    ("open youtube and search for lo-fi beats", "browser_open", {"target": "youtube"}),
    ("check the weather in Delhi and remind me at 8am to take an umbrella if it rains", "get_weather", {"location": "delhi"}),
    ("type hello world into notepad", "type_text", {"text": "hello world"}),
    ("move the invoice pdf from downloads to documents", "move_file", None),
    ("remind me every weekday at 9 to check email", "create_reminder", {"text": "email"}),
    ("what's my ip address", "run_command", None),
    ("snap chrome to the left and code to the right", "arrange_window", {"action": "snap_left"}),
    ("press control shift p in code", "press_keys", {"keys": "shift"}),  # 'control' or 'ctrl' both normalise
    ("close every chrome window", "close_app", {"name": "chrome"}),
    ("scroll to the bottom of this page", "browser_scroll", None),
    ("what does the dialog on screen say", "read_screen_text", None),
    ("kill premiere, it's frozen", "kill_app", {"name": "premiere"}),
    ("why is the sky blue", None, None),
    ("write me a haiku about rain", None, None),
    ("tell me a joke", None, None),
]
EVAL_CASES = EASY_CASES + HARD_CASES

OLLAMA = (os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/v1").rstrip("/"))
OLLAMA = OLLAMA[:-3] if OLLAMA.endswith("/v1") else OLLAMA

CHAT_PROMPTS = [
    "What is the capital of Italy? One sentence.",
    "Give me three words that rhyme with light.",
    "In one sentence, why is the sky blue?",
]

SYSTEM = (
    "You are Dexter, a desktop assistant. When the user asks for an action, call exactly one "
    "of the provided tools with correct arguments. Do not explain, do not ask; just call the tool."
)


LMSTUDIO = os.getenv("LM_STUDIO_URL", "http://127.0.0.1:1234/v1").rstrip("/")


def _chat_lms(model: str, messages: List[Dict[str, Any]], tools=None, timeout=300) -> Dict[str, Any]:
    """`lms:<id>` - the same question to LM Studio, answered in Ollama's shape.

    LM Studio reports no generation time, so tok/s is completion tokens over
    the whole wall time (prompt included): slightly pessimistic, never flattering.
    """
    body: Dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": 200}
    if tools:
        body["tools"] = tools
    t0 = time.time()
    r = requests.post(f"{LMSTUDIO}/chat/completions", json=body, timeout=timeout)
    wall = time.time() - t0
    if r.status_code != 200:
        return {"error": f"{r.status_code}: {r.text[:200]}", "wall": wall}
    data = r.json()
    msg = ((data.get("choices") or [{}])[0].get("message")) or {}
    return {"message": msg, "wall": wall,
            "eval_count": (data.get("usage") or {}).get("completion_tokens") or 0,
            "eval_duration": wall * 1e9}


def _chat(model: str, messages: List[Dict[str, Any]], tools=None, timeout=300) -> Dict[str, Any]:
    if model.startswith("lms:"):
        return _chat_lms(model[4:], messages, tools, timeout)
    body: Dict[str, Any] = {"model": model, "messages": messages, "stream": False, "think": False,
                            "keep_alive": "5m", "options": {"temperature": 0.1, "num_predict": 200}}
    if tools:
        body["tools"] = tools
    t0 = time.time()
    r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
    wall = time.time() - t0
    if r.status_code != 200:
        return {"error": f"{r.status_code}: {r.text[:200]}", "wall": wall}
    data = r.json()
    data["wall"] = wall
    return data


def _unload(model: str) -> None:
    if model.startswith("lms:"):
        return  # LM Studio models are measured as they are loaded (warm)
    try:
        requests.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
    except Exception:
        pass


def _vram(model: str) -> int:
    try:
        for m in requests.get(f"{OLLAMA}/api/ps", timeout=10).json().get("models", []):
            if m.get("name", "").split(":")[0] == model.split(":")[0] and (m.get("name") == model or m.get("model") == model):
                return int(m.get("size_vram", 0) // (1024 * 1024))
        for m in requests.get(f"{OLLAMA}/api/ps", timeout=10).json().get("models", []):
            if model in (m.get("name"), m.get("model")):
                return int(m.get("size_vram", 0) // (1024 * 1024))
    except Exception:
        pass
    return 0


def _matches(expected_args, got: Dict[str, Any]) -> bool:
    if not expected_args:
        return True
    for k, v in expected_args.items():
        val = got.get(k)
        if val is None:
            return False
        if isinstance(v, str) and v.lower() not in str(val).lower():
            return False
    return True


def bench(model: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"model": model}
    _unload(model)
    time.sleep(1.0)

    # Cold load + first answer.
    first = _chat(model, [{"role": "user", "content": CHAT_PROMPTS[0]}])
    if "error" in first:
        out["error"] = first["error"]
        return out
    out["coldSeconds"] = round(first["wall"], 1)
    out["vramMib"] = _vram(model)

    # Warm chat speed.
    walls, tps = [], []
    for p in CHAT_PROMPTS:
        d = _chat(model, [{"role": "user", "content": p}])
        if "error" in d:
            continue
        walls.append(d["wall"])
        ec, ed = d.get("eval_count") or 0, d.get("eval_duration") or 0
        if ec and ed:
            tps.append(ec / (ed / 1e9))
    out["chatSeconds"] = round(sum(walls) / max(1, len(walls)), 2)
    out["tokensPerSec"] = round(sum(tps) / max(1, len(tps)), 1)

    # Tool choice on Dexter's eval cases with Dexter's candidate set.
    right, wrong_tool, no_call, bad_args, walls = 0, 0, 0, 0, []
    details = []
    for prompt, expected, expected_args in EVAL_CASES:
        names = router.candidate_tools(prompt)
        if expected not in names:
            names = [expected] + names[:7]
        schemas = [get_tool(n).openai_schema() for n in names if get_tool(n)]
        d = _chat(model, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}], tools=schemas)
        if "error" in d:
            no_call += 1
            details.append((prompt, "error", d["error"][:80]))
            continue
        walls.append(d["wall"])
        calls = (d.get("message") or {}).get("tool_calls") or []
        if expected is None:
            # A question is a question: calling a tool here is the failure.
            if calls:
                wrong_tool += 1
                details.append((prompt, "called a tool for chat", (calls[0].get("function") or {}).get("name")))
            else:
                right += 1
            continue
        if not calls:
            no_call += 1
            details.append((prompt, "no call", ((d.get("message") or {}).get("content") or "")[:60]))
            continue
        fn = calls[0].get("function") or {}
        name, args = fn.get("name"), fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if name != expected:
            wrong_tool += 1
            details.append((prompt, "wrong tool", name))
            continue
        spec = get_tool(name)
        ok, cleaned, err = validate_args(spec, args) if spec else (False, {}, "no spec")
        if not ok or not _matches(expected_args, cleaned):
            bad_args += 1
            details.append((prompt, "bad args", json.dumps(args)[:60]))
            continue
        right += 1
    total = len(EVAL_CASES)
    out.update({
        "toolRight": right, "toolWrong": wrong_tool, "toolNoCall": no_call, "toolBadArgs": bad_args,
        "toolTotal": total, "toolAccuracy": round(right / total, 2),
        "toolSeconds": round(sum(walls) / max(1, len(walls)), 2),
        "misses": details,
    })
    _unload(model)
    return out


def fast_act(model: str) -> Dict[str, Any]:
    """How many of the 30 cases this model would settle without escalating.

    The rules are the router's, verbatim (`router._fast_act`): a question never
    reaches the fast-act pass at all, nor does a chain sentence; a call to a tool
    that was not offered is a hallucination; arguments that do not validate are
    an escalation. A case that escalates is not a failure - it is the design
    working - so the number that matters is "settled correctly", not "answered".
    """
    out: Dict[str, Any] = {"model": model, "mode": "fast-act"}
    settled, escalated, wrong = 0, 0, 0
    skipped = 0
    walls: List[float] = []
    misses = []
    for prompt, expected, expected_args in EVAL_CASES:
        # The two gates in front of the fast-act pass.
        if not router.looks_like_action(prompt) or router.is_desktop_chain(prompt):
            skipped += 1
            continue
        names = router.candidate_tools(prompt)
        schemas = [get_tool(n).openai_schema() for n in names if get_tool(n)]
        d = _chat(model, [{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": prompt}], tools=schemas)
        if "error" in d:
            escalated += 1
            continue
        walls.append(d["wall"])
        calls = (d.get("message") or {}).get("tool_calls") or []
        if not calls:
            escalated += 1
            misses.append((prompt, "no call", "escalated"))
            continue
        fn = calls[0].get("function") or {}
        name, args = fn.get("name"), fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        spec = get_tool(name) if name in names else None
        if spec is None:
            escalated += 1
            misses.append((prompt, "tool not on offer", name))
            continue
        ok, cleaned, _err = validate_args(spec, args)
        if not ok:
            escalated += 1
            misses.append((prompt, "invalid args", json.dumps(args)[:60]))
            continue
        # It would have acted. Was it the right act?
        if expected is None or name != expected or not _matches(expected_args, cleaned):
            wrong += 1
            misses.append((prompt, "WOULD HAVE ACTED WRONGLY", f"{name} {json.dumps(cleaned)[:50]}"))
            continue
        settled += 1
    total = len(EVAL_CASES) - skipped
    out.update({
        "cases": total, "notAnInstruction": skipped,
        "settled": settled, "escalated": escalated, "wrongAction": wrong,
        "settledShare": round(settled / max(1, total), 2),
        "seconds": round(sum(walls) / max(1, len(walls)), 2),
        "misses": misses,
    })
    _unload(model)
    return out


def main(argv: List[str]) -> None:
    json_path = None
    if "--json" in argv:
        i = argv.index("--json")
        json_path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if "--fast-act" in argv:
        argv = [a for a in argv if a != "--fast-act"]
        results = []
        for m in argv or ["qwen3.5:4b"]:
            print(f"\n=== {m} (fast-act) ===", flush=True)
            r = fast_act(m)
            results.append(r)
            print(f"  settled {r['settled']}/{r['cases']} ({r['settledShare']}) | "
                  f"escalated {r['escalated']} | would have acted wrongly {r['wrongAction']} | "
                  f"{r['seconds']}s avg ({r['notAnInstruction']} cases are not instructions)")
            for miss in r["misses"]:
                print("   -", miss)
        if json_path:
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2)
        return
    models = argv or ["qwen2.5:3b", "qwen2.5:7b"]
    results = []
    for m in models:
        print(f"\n=== {m} ===", flush=True)
        r = bench(m)
        results.append(r)
        if "error" in r:
            print("  error:", r["error"])
            continue
        print(f"  cold {r['coldSeconds']}s | vram {r['vramMib']} MiB | chat {r['chatSeconds']}s avg, {r['tokensPerSec']} tok/s")
        print(f"  tools {r['toolRight']}/{r['toolTotal']} right ({r['toolAccuracy']}) | wrong {r['toolWrong']} | no call {r['toolNoCall']} | bad args {r['toolBadArgs']} | {r['toolSeconds']}s avg")
        for miss in r["misses"]:
            print("   -", miss)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
    print("\n| model | cold s | VRAM MiB | chat s | tok/s | tools right | tool s |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        if "error" in r:
            print(f"| {r['model']} | error | | | | | |")
        else:
            print(f"| {r['model']} | {r['coldSeconds']} | {r['vramMib']} | {r['chatSeconds']} | {r['tokensPerSec']} | {r['toolRight']}/{r['toolTotal']} | {r['toolSeconds']} |")


if __name__ == "__main__":
    main(sys.argv[1:])
