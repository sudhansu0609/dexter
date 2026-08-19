"""
Dexter end-to-end verification suite.

Run the backend first:  python backend/main.py

These are live HTTP checks against a running server. Pure routing logic is
covered much more thoroughly by backend/test_router.py, which needs no server.

Note on assertions: intent used to be asserted via the `action` string
("launch_app", "browse_url", ...). Those were branch labels from the old keyword
chain. `action` now reports which tier answered (fast_path / semantic / agent),
and the tool that actually ran is in `toolCalls` - so that is what these assert.
It is a stricter check than before, since it verifies execution rather than a
routing label.
"""

import json
import sys
import time

import requests

BASE_URL = "http://127.0.0.1:8098"

# Tier 2 falls through to a local model, which can take a minute on consumer
# hardware. Short timeouts here were reporting slowness as failure.
FAST_TIMEOUT = 15
LLM_TIMEOUT = 150

_passed = 0
_failed = 0


def check(name, fn):
    global _passed, _failed
    try:
        detail = fn()
        _passed += 1
        print(f"[OK]   {name}: {detail}")
    except Exception as exc:
        _failed += 1
        print(f"[FAIL] {name}: {exc}")


def chat(message, timeout=FAST_TIMEOUT):
    res = requests.post(
        f"{BASE_URL}/api/dexter/chat", json={"message": message}, timeout=timeout
    )
    res.raise_for_status()
    return res.json()


def tools_used(data):
    return [c["name"] for c in data.get("toolCalls", [])]


def assert_tool(message, expected, timeout=FAST_TIMEOUT):
    data = chat(message, timeout)
    used = tools_used(data)
    assert expected in used, f"{message!r} ran {used}, expected {expected} (tier {data.get('tier')})"
    return f"{message!r} -> {expected} (tier {data.get('tier')}, {len(data['reply'])} char reply)"


def run_tests():
    print("=" * 60)
    print("Dexter Super Assistant - Verification Suite")
    print("=" * 60)

    # ── core endpoints ──
    def status():
        data = requests.get(f"{BASE_URL}/api/dexter/status", timeout=FAST_TIMEOUT).json()
        assert data["status"] == "online"
        assert data["toolCount"] > 0, "no tools registered"
        return (
            f"v{data['version']}, {data['toolCount']} tools, "
            f"{data['appsIndexed']} apps indexed, intent={data['intentMode']}, "
            f"scheduler={'up' if data['scheduler']['running'] else 'DOWN'}"
        )

    check("Status endpoint", status)

    check("Tool registry", lambda: (
        f"{len(requests.get(f'{BASE_URL}/api/dexter/tools', timeout=FAST_TIMEOUT).json())} tools exposed"
    ))

    # ── planner ──
    check("Planner read", lambda: (
        f"{len(requests.get(f'{BASE_URL}/api/dexter/planner', timeout=FAST_TIMEOUT).json().get('tasks', []))} tasks"
    ))

    def add_task():
        res = requests.post(
            f"{BASE_URL}/api/dexter/planner/add",
            json={"title": "Verification run", "time": "09:00 PM"}, timeout=FAST_TIMEOUT,
        )
        body = res.json()
        assert body["title"] == "Verification run"
        # The ISO timestamp is what makes a task schedulable at all.
        assert body.get("at"), "task has no ISO 'at' timestamp - scheduler cannot fire it"
        return f"added, at={body['at']}"

    check("Planner add (ISO timestamp)", add_task)

    def toggle_task():
        planner = requests.get(f"{BASE_URL}/api/dexter/planner", timeout=FAST_TIMEOUT).json()
        tasks = planner.get("tasks", [])
        if not tasks:
            return "skipped - no tasks"
        tid = tasks[-1]["id"]
        res = requests.post(f"{BASE_URL}/api/dexter/planner/toggle/{tid}", timeout=FAST_TIMEOUT)
        assert res.json()["success"]
        return f"toggled {tid}"

    check("Planner toggle", toggle_task)

    # ── intent routing (the old tests 5-8, rewritten against toolCalls) ──
    check("Intent: agenda not app launcher",
          lambda: assert_tool("what is on my agenda", "get_agenda"))
    check("Intent: media", lambda: assert_tool("play lofi beats", "play_on_youtube"))
    check("Intent: browse", lambda: assert_tool("go to google.com", "open_website"))
    check("Intent: app launch", lambda: assert_tool("open notepad", "launch_app"))
    check("Intent: time", lambda: assert_tool("what time is it", "get_current_time"))
    check("Intent: volume", lambda: assert_tool("what is the volume", "set_volume"))

    def general_question_reaches_llm():
        data = chat("What is machine learning?", timeout=LLM_TIMEOUT)
        assert not tools_used(data), f"a general question wrongly ran tools: {tools_used(data)}"
        assert len(data["reply"]) > 20, "reply suspiciously short"
        return f"tier {data.get('tier')}, {len(data['reply'])} char answer"

    check("General question reaches the model, runs no tools", general_question_reaches_llm)

    # ── reminders ──
    def reminder_lifecycle():
        data = chat("remind me to run the verification in 3 hours")
        assert "create_reminder" in tools_used(data), f"ran {tools_used(data)}"
        pending = requests.get(f"{BASE_URL}/api/dexter/reminders", timeout=FAST_TIMEOUT).json()
        match = next((r for r in pending if "verification" in r["text"].lower()), None)
        assert match, "reminder was not persisted"
        assert match["at"], "reminder has no fire time"
        requests.delete(f"{BASE_URL}/api/dexter/reminders/{match['id']}", timeout=FAST_TIMEOUT)
        return f"created and cancelled, fire at {match['at']}"

    check("Reminder create / list / cancel", reminder_lifecycle)

    # ── memory ──
    def memory_roundtrip():
        chat("remember that my test project is dexter-rebuild")
        data = chat("what do you remember about my test project")
        assert "dexter-rebuild" in data["reply"], f"recall failed: {data['reply']}"
        requests.delete(f"{BASE_URL}/api/dexter/memory/my test project", timeout=FAST_TIMEOUT)
        return "stored and recalled"

    check("Memory store / recall", memory_roundtrip)

    def conversation_history():
        stats = requests.get(f"{BASE_URL}/api/dexter/memory", timeout=FAST_TIMEOUT).json()
        assert stats["stats"]["turns"] > 0, "no conversation turns recorded"
        return f"{stats['stats']['turns']} turns stored"

    check("Conversation history persists", conversation_history)

    # ── streaming + sentence-chunked TTS ──
    def streaming():
        started = time.time()
        first_audio = None
        chunks = 0
        reply = ""
        res = requests.post(
            f"{BASE_URL}/api/dexter/chat/stream",
            json={"message": "what is on my agenda", "voiceEnabled": True},
            stream=True, timeout=LLM_TIMEOUT,
        )
        for line in res.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            evt = json.loads(line[5:])
            if evt["type"] == "reply":
                reply = evt["payload"]["reply"]
            elif evt["type"] == "audio":
                chunks += 1
                if first_audio is None:
                    first_audio = int((time.time() - started) * 1000)
        assert reply, "no reply event received"
        assert chunks > 0, "no audio chunks received"
        return f"first audio {first_audio}ms, {chunks} chunk(s)"

    check("Streaming chat with chunked TTS", streaming)

    # ── remaining endpoints ──
    check("Skills", lambda: (
        f"{len(requests.get(f'{BASE_URL}/api/dexter/skills', timeout=FAST_TIMEOUT).json())} skills"
    ))
    check("Direct browse endpoint", lambda: requests.post(
        f"{BASE_URL}/api/dexter/browser/navigate", json={"url": "example.com"},
        timeout=FAST_TIMEOUT).json()["url"])
    check("Filesystem search", lambda: (
        f"{requests.post(f'{BASE_URL}/api/dexter/filesystem/search', json={'query': 'dexter'}, timeout=FAST_TIMEOUT).json()['count']} results"
    ))
    check("SpilledCoffee curate", lambda: (
        f"{len(requests.post(f'{BASE_URL}/api/dexter/spilledcoffee/curate', json={'niche': 'AI', 'count': 2}, timeout=LLM_TIMEOUT).json())} ideas"
    ))

    print("=" * 60)
    print(f"{_passed} passed, {_failed} failed")
    print("=" * 60)
    return _failed


if __name__ == "__main__":
    try:
        requests.get(f"{BASE_URL}/api/dexter/status", timeout=FAST_TIMEOUT)
    except Exception:
        print(f"Dexter is not running at {BASE_URL}. Start it with: python backend/main.py")
        sys.exit(2)
    sys.exit(1 if run_tests() else 0)
