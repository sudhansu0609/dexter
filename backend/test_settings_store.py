"""
Tests for runtime settings.

The property that matters: a setting must take effect *now*, not on the next
launch. Every knob was a module-level constant read once at import, so a
settings screen that only wrote a file would look like it worked and change
nothing - worse than having no settings screen at all.

The live knob under test is the fast-model tier (router.TIERED_ENABLED); the
restart-only one is the close-out hour. Voice knobs were removed in v5.
"""

import os

import pytest

import router
import settings_store


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    yield


def test_defaults_are_reported_when_nothing_is_set(monkeypatch):
    monkeypatch.delenv("DEXTER_TIERED_LLM", raising=False)
    monkeypatch.delenv("DEXTER_CLOSEOUT_HOUR", raising=False)

    values = settings_store.current()
    assert values["tieredLlm"] is True
    assert values["closeOutHour"] == 21


def test_env_is_used_before_the_default(monkeypatch):
    monkeypatch.setenv("DEXTER_CLOSEOUT_HOUR", "20")
    assert settings_store.current()["closeOutHour"] == 20


def test_a_stored_value_overrides_env(monkeypatch):
    monkeypatch.setenv("DEXTER_CLOSEOUT_HOUR", "20")
    settings_store.update({"closeOutHour": 22})
    assert settings_store.current()["closeOutHour"] == 22


def test_a_change_takes_effect_immediately(monkeypatch):
    """The whole point: patch the already-imported module constant."""
    monkeypatch.setattr(router, "TIERED_ENABLED", True, raising=False)

    result = settings_store.update({"tieredLlm": False})

    assert router.TIERED_ENABLED is False
    assert "tieredLlm" in result["applied"]
    assert "tieredLlm" not in result["needsRestart"]


def test_the_environment_is_updated_too():
    settings_store.update({"tieredLlm": True})
    assert os.environ["DEXTER_TIERED_LLM"] == "1"

    settings_store.update({"tieredLlm": False})
    assert os.environ["DEXTER_TIERED_LLM"] == "0"


def test_restart_only_settings_say_so():
    result = settings_store.update({"closeOutHour": 22})
    assert "closeOutHour" in result["needsRestart"]
    assert "closeOutHour" not in result["applied"]


def test_settings_survive_a_restart(monkeypatch):
    settings_store.update({"tieredLlm": False})
    monkeypatch.setattr(router, "TIERED_ENABLED", True, raising=False)

    assert settings_store.load_at_startup() >= 1
    assert router.TIERED_ENABLED is False


def test_unknown_keys_are_rejected_not_stored():
    result = settings_store.update({"nonsense": 1})
    assert result["rejected"] == ["nonsense"]
    assert "nonsense" not in settings_store.current()


def test_a_bad_value_is_rejected_without_breaking_the_rest():
    result = settings_store.update({"closeOutHour": "not a number", "tieredLlm": False})
    assert "closeOutHour" in result["rejected"]
    assert settings_store.current()["tieredLlm"] is False


def test_choices_outside_the_list_fall_back_to_the_default():
    settings_store.update({"llmProvider": "openrouter"})
    assert settings_store.current()["llmProvider"] == "auto"


@pytest.mark.parametrize("truthy", [True, "1", "true", "on", "yes"])
def test_booleans_accept_what_a_ui_actually_sends(truthy):
    settings_store.update({"tieredLlm": truthy})
    assert settings_store.current()["tieredLlm"] is True


@pytest.mark.parametrize("falsy", [False, "0", "false", "off", "no", ""])
def test_booleans_reject_what_a_ui_actually_sends(falsy):
    settings_store.update({"tieredLlm": falsy})
    assert settings_store.current()["tieredLlm"] is False


def test_describe_exposes_a_renderable_schema():
    schema = {row["key"]: row for row in settings_store.describe()}

    assert schema["tieredLlm"]["kind"] == "bool"
    assert schema["tieredLlm"]["liveReload"] is True
    assert schema["closeOutHour"]["liveReload"] is False
    assert set(schema["llmProvider"]["options"]) == {"auto", "llamacpp", "ollama", "lmstudio"}
    assert all(row["label"] for row in schema.values()), "every setting needs a label"
