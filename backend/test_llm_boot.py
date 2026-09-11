"""
Starting the model server (v11.1): Ollama was installed, preferred, and not
running, and nothing in the stack started it. Fakes only - no real spawn.

Run:  pytest backend/test_llm_boot.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hybrid_llm
import llm_boot


class World:
    """A fake machine: which URLs answer, and what has been spawned."""

    def __init__(self, online=()):
        self.online = set(online)
        self.spawned = []
        self.comes_up_after_spawn = True

    def probe(self, url):
        return any(key in url for key in self.online)

    def spawn(self, args, env):
        self.spawned.append((args, env))
        if self.comes_up_after_spawn:
            self.online.add("11434" if "ollama" in args[0].lower() else "1234")
        return 4242


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(llm_boot, "_probe", w.probe)
    monkeypatch.setattr(llm_boot, "_spawn", w.spawn)
    monkeypatch.setattr(llm_boot, "ENABLED", True)
    monkeypatch.setattr(llm_boot, "ollama_exe", lambda: r"C:\Programs\Ollama\ollama.exe")
    monkeypatch.setattr(llm_boot, "lms_exe", lambda: r"C:\Users\x\.lmstudio\bin\lms.exe")
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:1234/v1")
    llm_boot._reset_for_tests()
    return w


def test_running_ollama_is_left_alone(world):
    world.online.add("11434")
    out = llm_boot.ensure_ollama(wait=0.1)
    assert out["online"] and not out["started"]
    assert world.spawned == []


def test_stopped_ollama_is_started_detached_and_waited_for(world):
    out = llm_boot.ensure_ollama(wait=2)
    assert out["online"] and out["started"]
    args, env = world.spawned[0]
    assert args[0].endswith("ollama.exe") and args[1] == "serve"
    assert env["OLLAMA_HOST"] == "127.0.0.1:11434"


def test_a_server_that_will_not_come_up_is_reported_not_retried_every_call(world):
    world.comes_up_after_spawn = False
    first = llm_boot.ensure_ollama(wait=0.2)
    assert not first["online"] and first["started"]
    second = llm_boot.ensure_ollama(wait=0.2)
    assert not second["online"] and not second["started"]
    assert "tried less than" in second["reason"]
    assert len(world.spawned) == 1


def test_autostart_can_be_turned_off(world, monkeypatch):
    monkeypatch.setattr(llm_boot, "ENABLED", False)
    out = llm_boot.ensure_ollama(wait=0.1)
    assert not out["online"] and "DEXTER_AUTOSTART_LLM" in out["reason"]
    assert world.spawned == []


def test_missing_install_is_a_reason_not_a_crash(world, monkeypatch):
    monkeypatch.setattr(llm_boot, "ollama_exe", lambda: None)
    out = llm_boot.ensure_ollama(wait=0.1)
    assert not out["online"] and "not installed" in out["reason"]


def test_lmstudio_is_started_with_lms(world):
    out = llm_boot.ensure_lmstudio(wait=2)
    assert out["online"] and out["started"]
    args, _ = world.spawned[0]
    assert args[1:3] == ["server", "start"] and args[-1] == "1234"


def test_preferred_ollama_starts_ollama_only(world, monkeypatch):
    monkeypatch.setenv("DEXTER_LLM_PROVIDER", "ollama")
    out = llm_boot.ensure_preferred(wait=2)
    assert out["provider"] == "ollama" and out["online"]
    assert [a[0][1] for a in world.spawned] == ["serve"]


def test_auto_does_nothing_when_any_local_server_is_up(world, monkeypatch):
    monkeypatch.setenv("DEXTER_LLM_PROVIDER", "auto")
    world.online.add("1234")
    out = llm_boot.ensure_preferred(wait=2)
    assert out["provider"] is None and out["online"]
    assert world.spawned == []


def test_auto_with_nothing_up_tries_ollama_then_lmstudio(world, monkeypatch):
    monkeypatch.setenv("DEXTER_LLM_PROVIDER", "auto")
    monkeypatch.setattr(llm_boot, "ollama_exe", lambda: None)
    out = llm_boot.ensure_preferred(wait=2)
    assert out["provider"] == "lmstudio" and out["online"]


# ───────────────────────── through the provider layer ─────────────────────────


def test_health_probe_revives_the_preferred_backend(monkeypatch):
    """Every path (request chain, tier pickers, preflight, /llm/models) goes
    through get_available_providers, so that is where the start happens."""
    router = hybrid_llm.HybridLLMRouter()
    router.default_provider = "ollama"
    up = {"ollama": False}
    calls = []

    def check(url):
        if "11434" in url:
            return up["ollama"]
        return "1234" in url  # LM Studio is up

    def ensure(provider):
        calls.append(provider)
        up["ollama"] = True
        return {"online": True, "started": True, "seconds": 1.0, "reason": "started"}

    monkeypatch.setattr(router, "_check_endpoint", check)
    monkeypatch.setattr(llm_boot, "ensure_provider", ensure)
    health = router.get_available_providers(force=True)
    assert calls == ["ollama"]
    assert health["ollama"]["online"] is True
    assert router.resolve_provider("auto") == "ollama"


def test_health_probe_falls_back_when_the_backend_will_not_start(monkeypatch):
    router = hybrid_llm.HybridLLMRouter()
    router.default_provider = "ollama"
    monkeypatch.setattr(router, "_check_endpoint", lambda url: "1234" in url)
    monkeypatch.setattr(llm_boot, "ensure_provider",
                        lambda provider: {"online": False, "started": False, "seconds": 0.0, "reason": "ollama.exe is not installed"})
    health = router.get_available_providers(force=True)
    assert health["ollama"]["online"] is False and health["lmStudio"]["online"] is True
    # The request still has somewhere to go.
    assert router.resolve_provider("auto") == "lmstudio"
    assert router._provider_chain("auto") == ["lmstudio"]
