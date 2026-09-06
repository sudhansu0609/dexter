"""
BuzzcafAI Client - HTTP interface to the 115-agent content production studio.

BuzzcafAI API runs on http://127.0.0.1:8000 (BUZZCAF_API_URL) and provides:
- Project management (create, execute workflows, track status)
- Workflow engine (multi-step content pipelines)
- Asset management (media files, thumbnails, etc.)
- Brand management (Beyond3Baje, Khayal3Baje, etc.)
"""

import os

import env_boot  # noqa: F401  - loads .env for every entry point
import shutil
import sys
import time
import subprocess
import requests
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

BUZZCAF_BASE_URL = os.getenv("BUZZCAF_API_URL", "http://127.0.0.1:8000")
# Sibling-project locations default to directories next to the Dexter checkout
# but can be pointed anywhere via .env.
_SIBLING_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUZZCAF_DIR = os.getenv("BUZZCAF_DIR", os.path.join(_SIBLING_ROOT, "BuzzcafAI"))


def _studio_port(base_url: str) -> str:
    """Port the Studio should listen on, derived from the URL Dexter will poll."""
    try:
        from urllib.parse import urlparse

        return str(urlparse(base_url).port or 8000)
    except Exception:
        return "8000"


def _studio_python() -> str:
    """
    Interpreter used to launch the Studio: an explicit BUZZCAF_PYTHON, then a
    .venv inside the Studio checkout, then a console-less pythonw next to our
    own interpreter, then ourselves. pythonw matters: the Studio is a desktop
    app and must not open a console window.
    """
    explicit = os.getenv("BUZZCAF_PYTHON", "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    venv = os.path.join(BUZZCAF_DIR, ".venv", "Scripts", "pythonw.exe")
    if os.path.exists(venv):
        return venv
    sibling = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if sys.platform == "win32" and os.path.exists(sibling):
        return sibling
    return sys.executable


@dataclass
class BuzzcafProject:
    id: str
    name: str
    brand: str
    workflow_name: str
    current_step: str
    status: str
    created_at: str
    assets: Dict[str, str]

    @classmethod
    def from_dict(cls, data: Dict) -> "BuzzcafProject":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            brand=data.get("brand", ""),
            workflow_name=data.get("workflow_name", ""),
            current_step=data.get("current_step", ""),
            status=data.get("status", ""),
            created_at=data.get("created_at", ""),
            assets=data.get("assets", {})
        )


class BuzzcafAIClient:
    """HTTP client for BuzzcafAI Studio API."""

    def __init__(self, base_url: str = BUZZCAF_BASE_URL):
        # "localhost" resolves to both ::1 and 127.0.0.1. When BuzzcafAI is not
        # running, urllib3 tries IPv6 first, waits out the timeout, then retries
        # IPv4 - so a 2s timeout cost 4s per call. Pinning to 127.0.0.1 halves
        # that; the cache below removes it from the hot path entirely.
        self.base_url = base_url.rstrip("/").replace("//localhost:", "//127.0.0.1:")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._active_project_id: Optional[str] = None
        self._health: Optional[bool] = None
        self._health_at: float = 0.0

    # ─────────────────────────────────────────────────────────────
    # Health & Status
    # ─────────────────────────────────────────────────────────────

    HEALTH_TTL_SECONDS = 15.0

    def health_check(self, force: bool = False) -> bool:
        """
        Check if BuzzcafAI server is online.

        Cached: /api/dexter/status calls this on every poll, and an offline
        BuzzcafAI made that a multi-second wait each time.
        """
        now = time.time()
        if (
            not force
            and self._health is not None
            and (now - self._health_at) < self.HEALTH_TTL_SECONDS
        ):
            return self._health

        try:
            res = self.session.get(f"{self.base_url}/health", timeout=1.5)
            # Port 8000 is popular. Only the Studio answers with app=buzzcaf;
            # anything else there is "offline" as far as Dexter is concerned.
            online = res.status_code == 200 and (res.json() or {}).get("app") == "buzzcaf"
        except Exception:
            online = False

        self._health = online
        self._health_at = time.time()
        return online

    def invalidate_health(self) -> None:
        """Force the next health_check to hit the network (after start/stop)."""
        self._health = None

    # ─────────────────────────────────────────────────────────────
    # Startup & Process Management
    # ─────────────────────────────────────────────────────────────

    def start_backend(self) -> Dict[str, Any]:
        """
        Start the BuzzcafAI backend server.
        Returns status dict with success flag and message.
        """
        if self.health_check(force=True):
            return {"success": True, "message": "BuzzcafAI is already running.", "already_running": True}

        backend_dir = os.path.join(BUZZCAF_DIR, "backend")
        if not os.path.exists(backend_dir):
            return {"success": False, "error": f"Studio backend directory not found at {backend_dir}"}

        python_exe = _studio_python()
        port = _studio_port(self.base_url)
        desktop_entry = os.path.join(backend_dir, "desktop_app.py")
        if os.path.exists(desktop_entry):
            # The Studio's own desktop shell: FastAPI in-process plus its window.
            cmd = [python_exe, desktop_entry]
        else:
            # Older checkout without the shell: API only, loopback, no reload.
            cmd = [
                python_exe, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", port,
            ]

        try:
            env = dict(os.environ)
            env["BUZZCAF_PORT"] = port
            env.setdefault("PYTHONIOENCODING", "utf-8")
            # No console window: the Studio is a desktop app, and a stray
            # cmd.exe behind it was one of the owner's explicit complaints.
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            process = subprocess.Popen(
                cmd,
                cwd=backend_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
                env=env,
            )

            return {
                "success": True,
                "message": "BuzzcafAI backend starting...",
                "pid": process.pid,
                "backend_dir": backend_dir
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def start_frontend(self) -> Dict[str, Any]:
        """
        Kept for API compatibility. The Studio's UI is a built bundle served by
        its own backend (and shown in its desktop window), so there is no
        separate dev server to start any more.
        """
        return {
            "success": True,
            "message": "The Studio UI is served by its backend; nothing separate to start.",
            "already_running": True,
        }

    def _retired_start_frontend(self) -> Dict[str, Any]:  # pragma: no cover
        frontend_dir = os.path.join(BUZZCAF_DIR, "frontend")
        try:
            npm = shutil.which("npm") or "npm"
            process = subprocess.Popen(
                [npm, "run", "dev"],
                cwd=frontend_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            return {
                "success": True,
                "message": "Studio dev server starting...",
                "pid": process.pid
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def start_studio(self, wait_for_ready: bool = True, timeout: int = 30) -> Dict[str, Any]:
        """
        Start both BuzzcafAI backend and frontend.

        Args:
            wait_for_ready: If True, wait until backend is responsive
            timeout: Maximum seconds to wait for backend to be ready

        Returns:
            Status dict with startup results
        """
        results = {
            "backend": None,
            "frontend": None,
            "ready": False,
            "message": ""
        }

        # Start backend
        backend_result = self.start_backend()
        results["backend"] = backend_result

        if not backend_result.get("success"):
            results["message"] = f"Failed to start backend: {backend_result.get('error')}"
            return results

        # Start frontend
        frontend_result = self.start_frontend()
        results["frontend"] = frontend_result

        # Wait for backend to be ready
        if wait_for_ready and not backend_result.get("already_running"):
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self.health_check(force=True):
                    results["ready"] = True
                    results["message"] = "BuzzcafAI Studio is now online!"
                    return results
                time.sleep(1)

            results["message"] = "Backend started but not yet responding. It may still be initializing."
            return results

        results["ready"] = self.health_check(force=True)
        results["message"] = "BuzzcafAI Studio started successfully!" if results["ready"] else "Starting BuzzcafAI Studio..."
        return results

    def ensure_running(self, auto_start: bool = True, timeout: int = 15) -> bool:
        """
        Ensure BuzzcafAI is running, optionally starting it if not.

        Args:
            auto_start: If True, start BuzzcafAI if it's not running
            timeout: Seconds to wait for startup

        Returns:
            True if BuzzcafAI is running (or was successfully started)
        """
        if self.health_check():
            return True

        if not auto_start:
            return False

        result = self.start_backend()
        if not result.get("success"):
            return False

        # Wait for it to come up
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.health_check(force=True):
                return True
            time.sleep(1)

        return False

    def get_diagnostics(self) -> Dict[str, Any]:
        """Get system diagnostics from BuzzcafAI."""
        try:
            res = self.session.get(f"{self.base_url}/api/diagnostics", timeout=5)
            return res.json() if res.ok else {"error": "Failed to get diagnostics"}
        except Exception as e:
            return {"error": str(e)}

    def get_status(self) -> Dict[str, Any]:
        """Get overall BuzzcafAI status summary."""
        online = self.health_check()
        if not online:
            return {
                "online": False,
                "message": "BuzzcafAI studio is offline. Start it with 'open buzzcaf studio'."
            }

        projects = self.list_projects()
        workflows = self.list_workflows()
        brands = self.list_brands()

        in_progress = [p for p in projects if p.get("status") not in ["completed", "archived"]]

        return {
            "online": True,
            "total_projects": len(projects),
            "in_progress": len(in_progress),
            "workflows_available": len(workflows),
            "brands": brands
        }

    # ─────────────────────────────────────────────────────────────
    # Brands
    # ─────────────────────────────────────────────────────────────

    def list_brands(self) -> List[str]:
        """Get all available channel brands."""
        try:
            res = self.session.get(f"{self.base_url}/api/brands", timeout=5)
            return res.json() if res.ok else []
        except Exception:
            # Never invent channels: an empty list tells the caller the Studio
            # is unreachable, which is the truth.
            return []

    # ─────────────────────────────────────────────────────────────
    # Workflows
    # ─────────────────────────────────────────────────────────────

    def list_workflows(self) -> List[Dict[str, Any]]:
        """Get all available workflow templates."""
        try:
            res = self.session.get(f"{self.base_url}/api/workflows", timeout=5)
            return res.json() if res.ok else []
        except Exception:
            return []

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific workflow by ID."""
        workflows = self.list_workflows()
        for wf in workflows:
            if wf.get("id") == workflow_id or wf.get("name") == workflow_id:
                return wf
        return None

    # ─────────────────────────────────────────────────────────────
    # Projects
    # ─────────────────────────────────────────────────────────────

    def list_projects(self) -> List[Dict[str, Any]]:
        """Get all projects."""
        try:
            res = self.session.get(f"{self.base_url}/api/projects", timeout=5)
            return res.json() if res.ok else []
        except Exception:
            return []

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific project by ID."""
        try:
            res = self.session.get(f"{self.base_url}/api/projects/{project_id}", timeout=5)
            if res.ok:
                return res.json()
            return None
        except Exception:
            return None

    def create_project(self, name: str, brand: str, workflow_name: str) -> Dict[str, Any]:
        """
        Create a new content production project.

        Args:
            name: Project name (e.g., "Sleep Science Deep Dive")
            brand: Channel brand (e.g., "Beyond3Baje")
            workflow_name: Workflow template to use (e.g., "full_video_workflow")

        Returns:
            Created project data or error dict
        """
        try:
            res = self.session.post(
                f"{self.base_url}/api/projects",
                json={
                    "name": name,
                    "brand": brand,
                    "workflow_name": workflow_name
                },
                timeout=10
            )
            if res.ok:
                project = res.json()
                self._active_project_id = project.get("id")
                return project
            return {"error": res.text, "status_code": res.status_code}
        except Exception as e:
            return {"error": str(e)}

    def execute_step(self, project_id: Optional[str] = None, feedback: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute the next workflow step for a project.

        Args:
            project_id: Project ID (uses active project if None)
            feedback: Optional user feedback for revision

        Returns:
            Updated project data or error dict
        """
        pid = project_id or self._active_project_id
        if not pid:
            return {"error": "No active project. Create or select a project first."}

        try:
            payload = {}
            if feedback:
                payload["feedback"] = feedback

            res = self.session.post(
                f"{self.base_url}/api/projects/{pid}/execute",
                json=payload,
                timeout=120  # Workflow steps can take time
            )
            if res.ok:
                return res.json()
            return {"error": res.text, "status_code": res.status_code}
        except Exception as e:
            return {"error": str(e)}

    def get_project_logs(self, project_id: Optional[str] = None) -> List[str]:
        """Get execution logs for a project."""
        pid = project_id or self._active_project_id
        if not pid:
            return ["No active project selected."]

        try:
            res = self.session.get(f"{self.base_url}/api/projects/{pid}/logs", timeout=5)
            return res.json() if res.ok else []
        except Exception:
            return []

    def get_project_asset(self, asset_name: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Get a specific asset from a project (e.g., script, research)."""
        pid = project_id or self._active_project_id
        if not pid:
            return {"error": "No active project selected."}

        try:
            res = self.session.get(
                f"{self.base_url}/api/projects/{pid}/asset/{asset_name}",
                timeout=5
            )
            if res.ok:
                return res.json()
            return {"error": f"Asset '{asset_name}' not found"}
        except Exception as e:
            return {"error": str(e)}

    # ─────────────────────────────────────────────────────────────
    # Assets Library
    # ─────────────────────────────────────────────────────────────

    def query_assets(
        self,
        asset_type: Optional[str] = None,
        tag: Optional[str] = None,
        query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Query the media asset library.

        Args:
            asset_type: Filter by type (image, video, audio, etc.)
            tag: Filter by tag
            query: Free-text search
        """
        try:
            params = {}
            if asset_type:
                params["type"] = asset_type
            if tag:
                params["tag"] = tag
            if query:
                params["query"] = query

            res = self.session.get(f"{self.base_url}/api/assets", params=params, timeout=5)
            return res.json() if res.ok else []
        except Exception:
            return []

    def register_asset(
        self,
        title: str,
        asset_type: str,
        tags: List[str],
        file_path: str,
        project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Register a new asset in the library."""
        try:
            res = self.session.post(
                f"{self.base_url}/api/assets",
                json={
                    "title": title,
                    "type": asset_type,
                    "tags": tags,
                    "file_path": file_path,
                    "project_id": project_id or self._active_project_id
                },
                timeout=5
            )
            return res.json() if res.ok else {"error": res.text}
        except Exception as e:
            return {"error": str(e)}

    # ─────────────────────────────────────────────────────────────
    # Settings
    # ─────────────────────────────────────────────────────────────

    def get_settings(self) -> Dict[str, Any]:
        """Get BuzzcafAI LLM settings."""
        try:
            res = self.session.get(f"{self.base_url}/api/settings", timeout=5)
            return res.json() if res.ok else {}
        except Exception:
            return {}

    def update_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Update BuzzcafAI settings."""
        try:
            res = self.session.post(f"{self.base_url}/api/settings", json=settings, timeout=5)
            return res.json() if res.ok else {"error": res.text}
        except Exception as e:
            return {"error": str(e)}

    # ─────────────────────────────────────────────────────────────
    # Active Project Context
    # ─────────────────────────────────────────────────────────────

    def set_active_project(self, project_id: str):
        """Set the active project for follow-up commands."""
        self._active_project_id = project_id

    def get_active_project(self) -> Optional[str]:
        """Get the active project ID."""
        return self._active_project_id

    def get_active_project_details(self) -> Optional[Dict[str, Any]]:
        """Get full details of the active project."""
        if self._active_project_id:
            return self.get_project(self._active_project_id)
        return None

    def clear_active_project(self):
        """Clear the active project context."""
        self._active_project_id = None

    # ─────────────────────────────────────────────────────────────
    # v5 control surface (see ECOSYSTEM.md)
    # ─────────────────────────────────────────────────────────────

    def _get(self, path: str, timeout: float = 8, **params) -> Any:
        try:
            res = self.session.get(f"{self.base_url}{path}", params=params or None, timeout=timeout)
            if res.ok:
                return res.json()
            return {"error": _detail(res), "status_code": res.status_code}
        except Exception as e:
            return {"error": str(e)}

    # ───────────── video intelligence (v6) ─────────────

    def analyze_video(self, url: str, channel: Optional[str] = None, force: bool = False) -> Any:
        """Fetching captions and running the model takes a while; wait up to five minutes."""
        return self._post("/api/video-intel/analyze", {"url": url, "channel": channel, "force": force}, timeout=300)

    def recent_analyses(self) -> Any:
        return self._get("/api/video-intel/recent")

    def _post(self, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 30) -> Any:
        try:
            res = self.session.post(f"{self.base_url}{path}", json=body or {}, timeout=timeout)
            if res.ok:
                return res.json()
            return {"error": _detail(res), "status_code": res.status_code}
        except Exception as e:
            return {"error": str(e)}

    def state(self) -> Dict[str, Any]:
        """GET /api/studio/state - counts, pending approvals, active project."""
        data = self._get("/api/studio/state")
        return data if isinstance(data, dict) else {"error": "unexpected reply"}

    def chat(
        self,
        message: str,
        channel: Optional[str] = None,
        agent_name: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        context_topic: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """POST /api/studio/chat - one Studio Assistant turn. Slow: a real model call."""
        data = self._post(
            "/api/studio/chat",
            {"message": message, "channel": channel, "agent_name": agent_name,
             "history": history or [], "context_topic": context_topic},
            timeout=240,
        )
        return data if isinstance(data, dict) else {"error": "unexpected reply"}

    def discover_topics(self, channel: str) -> Dict[str, Any]:
        data = self._post("/api/topics/discover", {"channel": channel}, timeout=20)
        return data if isinstance(data, dict) else {"error": "unexpected reply"}

    def saved_topics(self, channel: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._get("/api/topics/saved")
        topics = data.get("topics", []) if isinstance(data, dict) else []
        if channel:
            needle = channel.lower()
            topics = [t for t in topics if needle in str(t.get("channel", "")).lower()]
        return topics

    def save_topic(self, topic: Dict[str, Any]) -> Dict[str, Any]:
        data = self._post("/api/topics/save", topic, timeout=10)
        return data if isinstance(data, dict) else {"error": "unexpected reply"}

    def list_agents(self) -> List[Dict[str, Any]]:
        data = self._get("/api/agents")
        return data.get("agents", []) if isinstance(data, dict) else []

    def approve(self, project_id: Optional[str] = None, feedback: Optional[str] = None) -> Dict[str, Any]:
        """POST /api/projects/{id}/approve - approve the paused step, or send it back with notes."""
        pid = project_id or self._active_project_id
        if not pid:
            return {"error": "No project selected."}
        body = {"feedback": feedback} if feedback else {}
        data = self._post(f"/api/projects/{pid}/approve", body, timeout=240)
        return data if isinstance(data, dict) else {"error": "unexpected reply"}

    # ── BuzzBrain snapshots the Studio keeps (roadmap v5, 5.1) ──

    def buzzbrain_channels(self) -> Dict[str, Any]:
        data = self._get("/api/buzzbrain/channels")
        return data if isinstance(data, dict) else {"error": "unexpected reply"}

    def buzzbrain_latest(self, n: int = 5, mine: Optional[bool] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"n": n}
        if mine is not None:
            params["mine"] = "true" if mine else "false"
        data = self._get("/api/buzzbrain/latest", **params)
        return data if isinstance(data, dict) else {"error": "unexpected reply"}

    def buzzbrain_video(self, video_id: str) -> Dict[str, Any]:
        data = self._get(f"/api/buzzbrain/videos/{video_id}")
        return data if isinstance(data, dict) else {"error": "unexpected reply"}

    # ── events: the Studio pushes, Dexter listens ──

    def start_event_listener(self, on_event) -> None:
        """
        Follow GET /api/studio/events in a daemon thread and call
        on_event(type, payload) for each frame. Reconnects when the Studio
        restarts; idles cheaply while it is offline. One listener per process.
        """
        if getattr(self, "_listener", None) is not None:
            return
        import json as _json
        import threading

        def _run() -> None:
            backoff = 5
            while True:
                if not self.health_check(force=True):
                    time.sleep(15)
                    continue
                try:
                    with self.session.get(
                        f"{self.base_url}/api/studio/events", stream=True, timeout=(5, None)
                    ) as res:
                        if not res.ok:
                            time.sleep(backoff)
                            continue
                        backoff = 5
                        for line in res.iter_lines(decode_unicode=True):
                            if not line or not line.startswith("data:"):
                                continue
                            try:
                                message = _json.loads(line[5:].strip())
                            except Exception:
                                continue
                            kind = message.get("type")
                            if kind and kind != "connected":
                                try:
                                    on_event(kind, message.get("payload"))
                                except Exception as exc:
                                    print(f"[Studio events] handler failed for {kind}: {exc}")
                except Exception as exc:
                    print(f"[Studio events] stream dropped: {exc}")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 60)

        self._listener = threading.Thread(target=_run, name="dexter-studio-events", daemon=True)
        self._listener.start()


def _detail(res) -> str:
    try:
        body = res.json()
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("error") or body.get("message") or res.text[:300])
    except Exception:
        pass
    return res.text[:300]


# Singleton instance
buzzcaf_client = BuzzcafAIClient()
