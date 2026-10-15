"""
buzzcode as a Dexter tool (v6, C1): event folding, repository resolution,
routing, and refusals when nothing sensible can run.
"""

import json

import code_agent
import tools_code
import tools_browser  # noqa: F401
from router import fast_path


def test_job_folds_buzzcode_events_into_a_summary():
    job = code_agent.CodeJob("add a retry", {"name": "dexter", "path": "x"}, "edits")
    events = [
        {"type": "turn_start", "turn": 1, "task": "edit", "effort": "medium"},
        {"type": "tool_call_start", "id": "1", "name": "read", "arguments": {"path": "backend/a.py"}},
        {"type": "tool_call_start", "id": "2", "name": "edit", "arguments": {"path": "backend/a.py", "old": "x", "new": "y"}},
        {"type": "verify", "command": "pytest -q", "ok": True, "output": ""},
        {"type": "warning", "text": "context 80% full"},
        {"type": "assistant_message", "content": "Added a retry around the request.", "tool_calls": []},
        {"type": "finished", "reason": "done", "turns": 3},
    ]
    notable = [job.ingest(json.dumps(e)) for e in events]
    assert job.turns == 3 and job.tool_calls == 2
    assert job.files_changed == ["backend/a.py"]
    assert job.final_message.startswith("Added a retry")
    assert job.warnings == ["context 80% full"]
    assert [n["kind"] for n in notable if n] == ["tool", "tool", "verify"]
    assert job.ingest("not json") is None
    assert "1 file(s) touched: a.py" in job.summary()


def test_projects_resolve_by_name_or_mention(monkeypatch, tmp_path):
    for name in ("dexter", "BuzzcafAI", "BuzzBrain"):
        (tmp_path / name / ".git").mkdir(parents=True)
    monkeypatch.setattr(code_agent, "CODE_ROOTS", [str(tmp_path)])
    assert code_agent.resolve_project("buzzcafai")["name"] == "BuzzcafAI"
    assert code_agent.resolve_project("buzz brain")["name"] == "BuzzBrain"
    assert code_agent.resolve_project(str(tmp_path / "dexter"))["name"] == "dexter"
    assert code_agent.resolve_project(str(tmp_path.parent)) is None      # outside the code roots
    assert code_agent.project_from_text("fix the failing test in dexter please")["name"] == "dexter"
    assert code_agent.project_from_text("fix the failing test") is None


def test_code_task_refuses_without_a_repository_or_binary(monkeypatch, tmp_path):
    (tmp_path / "dexter" / ".git").mkdir(parents=True)
    monkeypatch.setattr(code_agent, "CODE_ROOTS", [str(tmp_path)])
    result = tools_code.code_task("fix the failing test")
    assert result.ok is False and "Which repository" in result.summary and "dexter" in result.summary
    monkeypatch.setattr(code_agent, "buzzcode_path", lambda: None)
    result = tools_code.code_task("fix the failing test in dexter")
    assert result.ok is False and "buzzcode is not installed" in result.summary


def test_code_task_launches_buzzcode_headless(monkeypatch, tmp_path):
    (tmp_path / "dexter" / ".git").mkdir(parents=True)
    monkeypatch.setattr(code_agent, "CODE_ROOTS", [str(tmp_path)])
    monkeypatch.setattr(code_agent, "WINDOWED", False)   # the hidden-pipe path (DEXTER_CODE_WINDOW=0)
    monkeypatch.setattr(code_agent, "buzzcode_path", lambda: "buzzcode.exe")
    monkeypatch.setattr(code_agent, "JOB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(code_agent, "JOBS_FILE", str(tmp_path / "jobs.json"))
    launched = {}

    class FakeProc:
        def __init__(self, argv, **kwargs):
            # subprocess is shared: the completion toast also spawns a process, so keep the first launch only.
            launched.setdefault("argv", argv)
            launched.setdefault("cwd", kwargs.get("cwd"))
            import io

            self.stdout = io.BytesIO(b'{"type":"assistant_message","content":"done","tool_calls":[]}\n{"type":"finished","reason":"done","turns":1}\n')
            self.stderr = io.BytesIO(b"")
            self.returncode = 0

        def wait(self):
            return 0

        def poll(self):
            return 0

        def terminate(self):
            pass

    monkeypatch.setattr(code_agent.subprocess, "Popen", FakeProc)
    import notify

    monkeypatch.setattr(notify, "toast_async", lambda *a, **k: None)
    import companion

    monkeypatch.setattr(companion, "on_event", lambda kind, payload: True)
    monkeypatch.setattr(code_agent.code_agent, "_diff_stat", lambda path: "")
    monkeypatch.setattr(code_agent.code_agent, "engine_status", lambda: {"online": True})
    agent = code_agent.CodeAgent()
    monkeypatch.setattr(code_agent, "code_agent", agent)
    monkeypatch.setattr(tools_code, "code_agent", agent)
    result = tools_code.code_task("fix the failing test in dexter and let it run the tests")
    assert result.ok, result.summary
    assert launched["argv"][1:5] == ["-C", str(tmp_path / "dexter"), "--mode", "yolo"]
    assert launched["argv"][-2:] == ["-p", "fix the failing test in dexter and let it run the tests"]
    job = agent.jobs[result.data["id"]]
    for _ in range(50):
        if job.status != "running":
            break
        import time

        time.sleep(0.05)
    assert job.status == "done" and job.final_message == "done"
    assert tools_code.code_status().summary.startswith(f"Job {job.id} on dexter done")


def test_fast_rules_reach_code_and_engine_tools():
    tool, args = fast_path("buzzcode: fix the failing test in dexter")[:2]
    assert tool == "code_task" and args["task"] == "fix the failing test in dexter"
    assert fast_path("ask the coder to add type hints in dexter")[0] == "code_task"
    assert fast_path("start the local model")[0] == "start_local_model"
    assert fast_path("is the local model running")[0] == "local_model_status"
    assert fast_path("code status")[0] == "code_status"
    tool, args = fast_path("analyse this video https://www.youtube.com/watch?v=abc for beyond3baje")[:2]
    assert tool == "studio_analyze_video" and args == {"url": "https://www.youtube.com/watch?v=abc", "channel": "beyond3baje"}


def test_code_task_opens_buzzcode_in_its_own_window(monkeypatch, tmp_path):
    """Default mode: a console launcher runs buzzcode; Dexter tails the job log
    it writes and finishes the job at the launcher's exit record - even though
    the window (the launcher process) stays open afterwards."""
    import threading
    import time

    (tmp_path / "dexter" / ".git").mkdir(parents=True)
    monkeypatch.setattr(code_agent, "CODE_ROOTS", [str(tmp_path)])
    monkeypatch.setattr(code_agent, "buzzcode_path", lambda: "buzzcode.exe")
    monkeypatch.setattr(code_agent, "JOB_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(code_agent, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(code_agent, "WINDOWED", True)
    monkeypatch.setattr(code_agent, "console_python", lambda: "python.exe")
    launched = {}

    class FakeWindow:
        """Stands in for code_console.py: writes events to the log, then stays open."""

        def __init__(self, argv, **kwargs):
            launched.setdefault("argv", argv)
            launched.setdefault("flags", kwargs.get("creationflags"))
            self.returncode = None
            self.pid = 4242
            if argv[0] == "python.exe":
                spec = json.load(open(argv[4], encoding="utf-8"))
                launched["spec"] = spec

                def write():
                    time.sleep(0.05)
                    with open(spec["log"], "a", encoding="utf-8") as log:
                        for ev in (
                            {"type": "tool_call_start", "id": "1", "name": "edit", "arguments": {"path": "a.py"}},
                            {"type": "assistant_message", "content": "Edited a.py.", "tool_calls": []},
                            {"type": "finished", "reason": "done", "turns": 2},
                            {"type": "launcher_exit", "code": 0, "elapsed": 1.0},
                        ):
                            log.write(json.dumps(ev) + "\n")
                            log.flush()

                threading.Thread(target=write, daemon=True).start()

        def poll(self):
            return None   # the window is still open

        def wait(self):
            return 0

        def terminate(self):
            pass

    monkeypatch.setattr(code_agent.subprocess, "Popen", FakeWindow)
    import notify

    monkeypatch.setattr(notify, "toast_async", lambda *a, **k: None)
    import companion

    monkeypatch.setattr(companion, "on_event", lambda kind, payload: True)
    agent = code_agent.CodeAgent()
    monkeypatch.setattr(agent, "_diff_stat", lambda path: " 1 file changed")
    monkeypatch.setattr(agent, "engine_status", lambda: {"online": True})
    monkeypatch.setattr(code_agent, "code_agent", agent)
    monkeypatch.setattr(tools_code, "code_agent", agent)

    result = tools_code.code_task("rename the helper in dexter")
    assert result.ok, result.summary
    assert "in its own buzzcode window" in result.summary
    assert launched["argv"][0] == "python.exe" and launched["argv"][3].endswith("code_console.py")
    assert launched["flags"] == code_agent._new_console()
    assert launched["spec"]["exe"] == "buzzcode.exe" and launched["spec"]["mode"] == "edits"
    assert launched["spec"]["task"] == "rename the helper in dexter"
    job = agent.jobs[result.data["id"]]
    for _ in range(100):
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "done" and job.exit_code == 0 and job.windowed
    assert job.files_changed == ["a.py"] and job.final_message == "Edited a.py."
    assert "done after" in tools_code.code_status().summary


def test_finished_job_releases_the_engine_dexter_started(monkeypatch, tmp_path):
    monkeypatch.setattr(code_agent, "JOBS_FILE", str(tmp_path / "jobs.json"))
    agent = code_agent.CodeAgent()
    stopped = []
    monkeypatch.setattr(agent, "stop_engine", lambda: stopped.append(True) or True)
    monkeypatch.setattr(agent, "_diff_stat", lambda path: "")
    monkeypatch.setattr(agent, "_announce", lambda job: None)
    agent._engine_proc = object()   # Dexter started the llama-server earlier
    job = code_agent.CodeJob("task", {"name": "dexter", "path": str(tmp_path)}, "edits")
    job.exit_code = 0
    job.final_message = "ok"
    agent._finish(job)
    assert job.status == "done" and stopped == [True]
    assert any("stopped the local model server" in w for w in job.warnings)


# ── v12, P2.3: the engine needs 12.5 GB, so Dexter steps aside for it too ──


def test_starting_the_engine_gives_dexters_own_model_back_first(monkeypatch):
    """Same rule as an app the owner asked for: our own models, nobody else's."""
    import model_steward

    blocker = {"label": "Ollama runner (Dexter)", "pid": 4711, "vram_mib": 5000}
    shed = []
    monkeypatch.setattr(model_steward, "query", lambda purpose, vram, ram=None: (
        model_steward.Decision(granted=False, purpose=purpose, vram_mib=vram,
                               ram_mib=ram or 0, reason="not enough VRAM",
                               blockers=[blocker], details={"free_mib": 1200,
                                                            "blockers": [blocker]})
    ))
    monkeypatch.setattr(model_steward, "shed", lambda level="orange", reason="", want_mib=0, move_fast=True: (
        shed.append((level, reason))
        or {"level": level, "steps": ["unloaded qwen2.5:7b"], "freedMib": 5017}
    ))
    monkeypatch.setattr(model_steward, "release", lambda why="": None)
    monkeypatch.setattr(model_steward, "_resident_mib", lambda: 0)
    # The second query is the one that decides; make_room asks it itself.
    monkeypatch.setattr(model_steward, "make_room", model_steward.make_room)
    monkeypatch.setattr(code_agent.code_agent, "start_engine", lambda: {
        "online": True, "started": True, "model": "qwen3.8-27b",
        "url": "http://127.0.0.1:8089/v1",
    })

    result = tools_code.start_local_model()

    assert shed == [("preempt", "making room for the coding engine")]
    assert result.ok
    assert result.summary.startswith("Gave back qwen2.5:7b (4.9 GB) so the coding engine could start.")


def test_the_engine_does_not_shed_for_somebody_elses_render(monkeypatch):
    import model_steward

    vrdeep = {"process": "VrDeep", "pid": 33228, "vram_mib": 10240, "class": "batch"}
    shed = []
    monkeypatch.setattr(model_steward, "query", lambda purpose, vram, ram=None: (
        model_steward.Decision(granted=False, purpose=purpose, vram_mib=vram,
                               ram_mib=ram or 0, reason="not enough VRAM",
                               blockers=[vrdeep], details={"free_mib": 1200})
    ))
    monkeypatch.setattr(model_steward, "shed", lambda *a, **k: shed.append(a) or {})
    monkeypatch.setattr(code_agent.code_agent, "start_engine", lambda: (_ for _ in ()).throw(
        RuntimeError("Sentinel won't give me 12.2 GB of video memory right now.")
    ))

    result = tools_code.start_local_model()

    assert shed == []
    assert result.ok is False and "12.2 GB" in result.summary
