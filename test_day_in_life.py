"""
A day through Dexter, as its owner would use it: mission, plan, reminders,
notes, memory, apps, files, screen, code, Studio. Every turn is timed and the
result table is written as markdown for the report.

    python test_day_in_life.py [--only 3,7,12]   # rerun a subset (server must be up)
"""
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))


def _dexter_base_url() -> str:
    """Where Dexter actually is, not where it wished to be.

    Dexter takes the first free port from `DEXTER_PORT` (8098) upwards and
    publishes it (GUARDIAN_PLAN.md section 11). Assuming 8098 here is how this
    suite would "fail" against a perfectly healthy Dexter that had to step
    forward because something else got to 8098 first.
    """
    try:
        import buzzcaf_ports

        found = buzzcaf_ports.discover(
            "dexter", int(os.getenv("DEXTER_PORT") or 8098), "/health", ttl=0,
        )
        if found:
            return found
    except Exception as exc:  # the helper is stdlib-only; this should not happen
        print(f"[suite] port discovery unavailable ({exc}); falling back")
    return f"http://127.0.0.1:{os.getenv('DEXTER_PORT') or 8098}"


BASE = _dexter_base_url()
OUT = __file__.replace("test_day_in_life.py", "reports/day_in_life_results.md")
s = requests.Session()
s.headers["X-Dexter-Token"] = s.get(f"{BASE}/api/dexter/auth/token", timeout=5).json()["token"]
SESSION = "sudhansu-day"

# (message, expectation) - expectation is a short human note on what a good outcome is;
# `tool:` prefix means a specific tool should have run, `no-tools` means it must not run one.
DAY = [
    # ── morning ──
    ("good morning dexter", "greeting with today's win / first open loop, instant"),
    ("what's on my plate today", "tool:get_agenda"),
    ("what's our mission", "mission read, instant"),
    ("this week's focus is shipping dexter v8 with instant replies", "focus stored, instant"),
    ("add a commitment: record the bhangarh fort voiceover by 3pm", "commitment on today's plan with time"),
    ("remind me at 2:30pm to prep the studio lights", "tool:create_reminder"),
    ("note: thumbnail idea - red arrow pointing at the fort gate", "note saved"),
    ("what did I work on yesterday", "activity narrative, no hallucination"),
    ("how are we doing", "mission status, instant"),
    # ── work block ──
    ("open notepad", "tool:launch_app"),
    ("search my files for the bhangarh script", "file search results"),
    ("what's on my screen right now", "screen/window description via tools"),
    ("take a screenshot", "tool:take_screenshot"),
    ("what's the capital of australia", "no-tools fast answer, under 2s"),
    ("in one sentence, what is a mutex", "no-tools fast answer"),
    ("remember that I edit in davinci resolve", "memory stored"),
    ("what editor do I use", "recalls davinci resolve"),
    ("what time is it in tokyo", "correct time, tool or fast"),
    ("set the volume to 35", "tool:set_volume"),
    ("what's the volume", "tool:set_volume"),
    # ── coding + studio ──
    ("which repos can you code in", "tool:list_code_projects"),
    ("is the local model running", "tool:local_model_status"),
    ("how is the code task going", "tool:code_status"),
    ("any claude code sessions today", "claude session summary or honest none"),
    ("summarize the dexter readme in two lines", "reads README, no invention"),
    # ── comms ──
    ("check my email", "honest: google not connected"),
    ("what's on my calendar tomorrow", "honest: google not connected"),
    # ── afternoon ──
    ("log a win: fixed dexter's instant replies", "win logged, instant"),
    ("mark the voiceover commitment done", "commitment completed"),
    ("cancel the studio lights reminder", "reminder cancelled"),
    ("what reminders do I have", "lists remaining reminders"),
    ("close notepad", "tool:close_app"),
    # ── evening ──
    ("what did we get done today", "today's wins / completed"),
    ("thanks dexter, that's all for today", "warm sign-off, instant, no tools"),
]


def run(idx, message, expectation):
    t0 = time.time()
    try:
        r = s.post(f"{BASE}/api/dexter/chat", json={"message": message, "session": SESSION}, timeout=240)
        dt = time.time() - t0
        d = r.json()
    except Exception as exc:
        return {"i": idx, "msg": message, "exp": expectation, "ms": int((time.time() - t0) * 1000), "action": "ERROR", "tier": "-", "tools": [], "ok": [], "reply": str(exc)}
    tools = [(c["name"], bool(c.get("ok"))) for c in d.get("toolCalls") or []]
    return {
        "i": idx, "msg": message, "exp": expectation, "ms": int(dt * 1000),
        "action": d.get("action"), "tier": d.get("tier"), "tools": [t for t, _ in tools], "ok": [o for _, o in tools],
        "reply": (d.get("reply") or "").replace("\n", " ").strip(),
        "model": (d.get("data") or {}).get("model", ""),
    }


def verdict(row):
    exp = row["exp"]
    if row["action"] == "ERROR":
        return "FAIL error"
    if exp.startswith("tool:"):
        want = exp[5:]
        if want not in row["tools"]:
            return f"FAIL ran {row['tools'] or 'nothing'}"
        if not all(row["ok"]):
            return "FAIL tool error"
    if exp.startswith("no-tools") and row["tools"]:
        return f"FAIL ran {row['tools']}"
    if "under 2s" in exp and row["ms"] > 2000:
        return f"SLOW {row['ms']}ms"
    if "instant" in exp and row["ms"] > 2500:
        return f"SLOW {row['ms']}ms"
    if "ESCALATE" in row["reply"].upper():
        return "FAIL sentinel leaked"
    return "check"


def main():
    only = None
    if "--only" in sys.argv:
        only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")}
    rows = []
    for idx, (message, expectation) in enumerate(DAY, 1):
        if only and idx not in only:
            continue
        row = run(idx, message, expectation)
        row["verdict"] = verdict(row)
        rows.append(row)
        print(f"{idx:2d} {row['ms']:6d}ms t{row['tier']} {str(row['action']):<13} {row['verdict']:<22} {message!r}\n     tools={row['tools']} -> {row['reply'][:170]!r}")
        sys.stdout.flush()
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(f"\n## Run at {time.strftime('%Y-%m-%d %H:%M')}\n\n| # | I said | ms | tier / action | tools | verdict | Dexter replied |\n|---|---|---|---|---|---|---|\n")
        for r in rows:
            fh.write(f"| {r['i']} | {r['msg']} | {r['ms']} | t{r['tier']} {r['action']} | {', '.join(r['tools']) or '-'} | {r['verdict']} | {r['reply'][:220].replace('|', '/')} |\n")
    print("\nwritten", OUT)


if __name__ == "__main__":
    main()
