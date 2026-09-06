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
