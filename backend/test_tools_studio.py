"""
The Studio tools (roadmap v5, 3.2) against a fake client.

What these protect: a failed step is reported as a failure; a step paused for
approval is announced as such; approving with nothing waiting is refused
(409); the strategist's reply comes back with its channel; the default
workflow follows the channel; the offline Studio never yields invented data.
"""

import pytest

import tools_studio
from tool_registry import REGISTRY, execute


class FakeClient:
    def __init__(self):
        self.online = True
        self.projects = [
            {"id": "p1", "name": "Ghost of Bhangarh", "brand": "Beyond3Baje", "status": "active",
             "current_step": "Research", "steps_history": [
                 {"step_name": "Research", "status": "paused_for_approval", "agent_name": "ResearchAgent"}]},
            {"id": "p2", "name": "Last Lantern", "brand": "Khayal3Baje", "status": "active",
             "current_step": "Idea", "steps_history": []},
        ]
        self.active = None
        self.approved = []
        self.executed = []
        self.chats = []

    # health / state
    def health_check(self, force=False):
        return self.online

    def state(self):
        pending = [
            {"project_id": p["id"], "name": p["name"], "brand": p["brand"], "step": p["steps_history"][-1]["step_name"]}
            for p in self.projects if p["steps_history"] and p["steps_history"][-1]["status"] == "paused_for_approval"
        ]
        return {"projects": {"total": 2, "in_progress": 2, "paused_for_approval": len(pending), "done": 0},
                "pending_approvals": pending, "active_project": None}

    # projects
    def list_projects(self):
        return list(self.projects)

    def list_brands(self):
        return ["Beyond3Baje", "Khayal3Baje", "Spilled Coffee Studio", "Spilled Coffee After Dark", "Life3Baje"]

    def list_workflows(self):
        return [{"id": w} for w in ("after_dark_narration", "beyond3baje_documentary", "khayal3baje_horror",
                                    "life3baje_video", "spilled_coffee_story", "video_production_pipeline")]

    def create_project(self, name, brand, workflow_name):
        project = {"id": "p3", "name": name, "brand": brand, "workflow_name": workflow_name,
                   "status": "active", "current_step": "Research", "steps_history": []}
        self.projects.append(project)
        return project

    def get_active_project(self):
        return self.active

    def set_active_project(self, pid):
        self.active = pid

    def get_active_project_details(self):
        return next((p for p in self.projects if p["id"] == self.active), None)

    def execute_step(self, project_id, feedback=None):
        self.executed.append((project_id, feedback))
        if project_id == "p2":
            return {"error": "Execution error: model server down", "status_code": 500}
        return {"id": project_id, "name": "Ghost of Bhangarh", "status": "active", "current_step": "Script",
                "steps_history": [{"step_name": "Script", "status": "paused_for_approval"}]}

    def approve(self, project_id=None, feedback=None):
        self.approved.append((project_id, feedback))
        if project_id == "p2":
            return {"error": "Nothing is waiting for approval on 'Last Lantern' (current step: Idea).", "status_code": 409}
        return {"id": project_id, "name": "Ghost of Bhangarh", "status": "active", "current_step": "Script",
                "steps_history": [{"step_name": "Research", "status": "completed"}]}

    # chat / topics / agents
    def chat(self, message, channel=None, agent_name=None, history=None, context_topic=None):
        self.chats.append((message, channel))
        return {"status": "success", "simulated": False, "agent_name": "Khayal3BajeStrategist",
                "channel": channel, "reply": "Three hooks: ..."}

    def discover_topics(self, channel):
        return {"topics": [{"topic": f"Idea for {channel}"}], "source": "curated_seed"}

    def saved_topics(self, channel=None):
        return [{"topic": "The Last Lantern", "channel": "Khayal3Baje"}] if (channel or "").lower() != "life3baje" else []

    def save_topic(self, topic):
        return {"status": "success", "topic": topic}

    def list_agents(self):
        return [{"name": f"Agent{i}"} for i in range(115)]

    def start_studio(self, wait_for_ready=True, timeout=30):
        return {"ready": True}

    # buzzbrain
    def buzzbrain_channels(self):
        return {"channels": [
            {"key": "UCme", "channel_id": "UCme", "name": "Beyond3Baje", "mine": True, "subscribers": 12400,
             "videos_seen": 3, "best_vph": 320, "latest_video": {"video_id": "v9", "title": "The Vanishing Guard",
             "channel_name": "Beyond3Baje", "views": 15200, "vph": 320, "likes": 900, "captured_at": "2026-09-06T10:00:00"}},
            {"key": "other", "channel_id": "UCx", "name": "Someone Else", "mine": False, "subscribers": 1000,
             "videos_seen": 1, "best_vph": 5, "latest_video": None},
        ]}

    def buzzbrain_latest(self, n=5, mine=None):
        return {"videos": [
            {"video_id": "s1", "title": "Stranger video", "channel_name": "Someone Else", "mine": False, "views": 100},
            {"video_id": "v9", "title": "The Vanishing Guard", "channel_name": "Beyond3Baje", "mine": True,
             "views": 15200, "vph": 320, "likes": 900, "published_ago": "2 days ago", "captured_at": "2026-09-06T10:00:00"},
        ]}

    def buzzbrain_video(self, video_id):
        return {"video_id": video_id, "history": [{"views": 4000}, {"views": 9000}, {"views": 15200}]}


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(tools_studio, "buzzcaf_client", client)
    return client


def test_all_studio_tools_are_registered():
    for name in ("buzzcaf_status", "buzzcaf_start_studio", "buzzcaf_list_projects", "buzzcaf_create_project",
                 "buzzcaf_execute_step", "studio_pending_approvals", "studio_approve_step", "studio_chat",
                 "studio_discover_topics", "studio_saved_topics", "studio_save_topic", "studio_agents"):
        assert name in REGISTRY, name


def test_pending_approvals_are_listed(fake):
    result = execute("studio_pending_approvals", {})
    assert result.ok
    assert "Ghost of Bhangarh" in result.summary and "Research" in result.summary


def test_approve_picks_the_waiting_project_and_reports_the_move(fake):
    result = execute("studio_approve_step", {})
    assert result.ok, result.summary
    assert fake.approved == [("p1", None)]
    assert "Script" in result.summary


def test_approve_with_nothing_waiting_is_refused(fake):
    result = execute("studio_approve_step", {"project_name": "Last Lantern"})
    assert not result.ok
    assert "Nothing is waiting" in result.summary


def test_execute_step_reports_failures_and_pauses_honestly(fake):
    fake.active = "p2"
    failed = execute("buzzcaf_execute_step", {})
    assert not failed.ok and "model server down" in failed.summary

    fake.active = "p1"
    paused = execute("buzzcaf_execute_step", {})
    assert paused.ok and "waiting for your approval" in paused.summary


def test_chat_goes_to_the_channel_strategist(fake):
    result = execute("studio_chat", {"question": "three hooks about the last lantern for khayal3baje"})
    assert result.ok
    assert fake.chats[-1][1] == "Khayal3Baje"
    assert "Three hooks" in result.summary


def test_create_project_uses_the_channel_workflow(fake):
    result = execute("buzzcaf_create_project", {"name": "Bhangarh fort at 3am", "brand": "after dark"})
    assert result.ok, result.summary
    created = fake.projects[-1]
    assert created["brand"] == "Spilled Coffee After Dark"
    assert created["workflow_name"] == "after_dark_narration"
    assert fake.active == "p3"


def test_offline_studio_never_invents_data(fake):
    fake.online = False
    for name, args in (("buzzcaf_list_projects", {}), ("studio_saved_topics", {}), ("studio_agents", {}),
                       ("studio_chat", {"question": "hi"})):
        result = execute(name, args)
        assert not result.ok, name
        assert "offline" in result.summary.lower()


def test_status_summarises_counts_and_pending(fake):
    result = execute("buzzcaf_status", {})
    assert result.ok
    assert "2 project(s)" in result.summary and "1 waiting for your approval" in result.summary


def test_channel_status_reports_only_the_owners_channels(fake):
    result = execute("youtube_channel_status", {})
    assert result.ok
    assert result.summary.startswith("Your channels")
    assert "Beyond3Baje" in result.summary and "12.4K subscribers" in result.summary
    assert "Someone Else" not in result.summary


def test_video_stats_prefers_the_owners_latest_video_and_shows_the_trend(fake):
    result = execute("youtube_video_stats", {})
    assert result.ok
    assert "The Vanishing Guard" in result.summary
    assert "15.2K views" in result.summary
    assert "4.0K -> 15.2K" in result.summary
    named = execute("youtube_video_stats", {"video": "stranger"})
    assert named.ok and "not marked as one of your channels" in named.summary


# ───────── finding the Studio, wherever it stepped to (P1) ─────────


def test_the_studio_is_looked_for_in_the_ledger_before_its_runtime_file(monkeypatch):
    """Order matters (GUARDIAN_PLAN.md section 11, rule 5): the owner's env, then
    the shared ledger, then the Studio's own runtime file, then a scan."""
    import buzzcaf_client

    monkeypatch.setattr(buzzcaf_client, "_ledger_base_url", lambda: "http://127.0.0.1:8100")
    monkeypatch.setattr(buzzcaf_client, "_runtime_base_url", lambda: "http://127.0.0.1:8101")
    monkeypatch.setattr(buzzcaf_client.buzzcaf_ports, "discover", lambda *a, **k: "http://127.0.0.1:8102")

    client = buzzcaf_client.BuzzcafAIClient("http://127.0.0.1:8099")
    assert client._candidate_base_urls() == [
        "http://127.0.0.1:8099",   # BUZZCAF_API_URL, as configured
        "http://127.0.0.1:8100",   # the ledger
        "http://127.0.0.1:8101",   # studio.runtime.json
        "http://127.0.0.1:8102",   # a step-forward scan
    ]


def test_a_ledger_entry_left_by_a_dead_studio_is_not_offered(monkeypatch):
    import buzzcaf_client

    monkeypatch.setattr(buzzcaf_client.buzzcaf_ports, "entry",
                        lambda app, *a, **k: {"port": 8100, "pid": 4})
    monkeypatch.setattr(buzzcaf_client.buzzcaf_ports, "_pid_alive", lambda pid: False)
    assert buzzcaf_client._ledger_base_url() is None


def test_only_something_that_says_it_is_the_studio_is_believed(monkeypatch):
    """CaliberAI on 8000 was once mistaken for the Studio. Every candidate,
    ledger entry included, still has to answer /health with app: "buzzcaf"."""
    import buzzcaf_client

    monkeypatch.setattr(buzzcaf_client, "_ledger_base_url", lambda: "http://127.0.0.1:8100")
    monkeypatch.setattr(buzzcaf_client, "_runtime_base_url", lambda: None)
    monkeypatch.setattr(buzzcaf_client.buzzcaf_ports, "discover", lambda *a, **k: None)

    client = buzzcaf_client.BuzzcafAIClient("http://127.0.0.1:8099")
    asked = []
    monkeypatch.setattr(client, "_answers", lambda url: asked.append(url) or False)
    assert client.health_check(force=True) is False
    assert "http://127.0.0.1:8100" in asked, "the ledger entry was never tried"
