"""
Tests for runtime settings.

The property that matters: a setting must take effect *now*, not on the next
launch. Every knob was a module-level constant read once at import, so a
settings screen that only wrote a file would look like it worked and change
nothing - worse than having no settings screen at all.
"""

import os

import pytest

import settings_store
import voice_pipeline


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    yield


def test_defaults_are_reported_when_nothing_is_set(monkeypatch):
    monkeypatch.delenv("DEXTER_VOICE_BARGE_IN", raising=False)
    monkeypatch.delenv("DEXTER_FOLLOWUP_WINDOW", raising=False)

    values = settings_store.current()
    assert values["bargeIn"] is False
    assert values["followUpWindow"] == 9.0


def test_env_is_used_before_the_default(monkeypatch):
    monkeypatch.setenv("DEXTER_FOLLOWUP_WINDOW", "4.5")
    assert settings_store.current()["followUpWindow"] == 4.5


def test_a_stored_value_overrides_env(monkeypatch):
    monkeypatch.setenv("DEXTER_FOLLOWUP_WINDOW", "4.5")
    settings_store.update({"followUpWindow": 12})
    assert settings_store.current()["followUpWindow"] == 12.0


def test_a_change_takes_effect_immediately(monkeypatch):
    """The whole point: patch the already-imported module constant."""
    monkeypatch.setattr(voice_pipeline, "BARGE_IN_ENABLED", False, raising=False)

    result = settings_store.update({"bargeIn": True})

    assert voice_pipeline.BARGE_IN_ENABLED is True
    assert "bargeIn" in result["applied"]
    assert "bargeIn" not in result["needsRestart"]


def test_the_environment_is_updated_too(monkeypatch):
    settings_store.update({"bargeIn": True})
    assert os.environ["DEXTER_VOICE_BARGE_IN"] == "1"

    settings_store.update({"bargeIn": False})
    assert os.environ["DEXTER_VOICE_BARGE_IN"] == "0"


def test_restart_only_settings_say_so():
    result = settings_store.update({"closeOutHour": 22})
    assert "closeOutHour" in result["needsRestart"]
    assert "closeOutHour" not in result["applied"]


def test_settings_survive_a_restart(monkeypatch):
    settings_store.update({"followUpWindow": 15})
    monkeypatch.setattr(voice_pipeline, "FOLLOWUP_WINDOW", 9.0, raising=False)

    assert settings_store.load_at_startup() >= 1
    assert voice_pipeline.FOLLOWUP_WINDOW == 15.0


def test_unknown_keys_are_rejected_not_stored():
    result = settings_store.update({"nonsense": 1})
    assert result["rejected"] == ["nonsense"]
    assert "nonsense" not in settings_store.current()


def test_a_bad_value_is_rejected_without_breaking_the_rest():
    result = settings_store.update({"followUpWindow": "not a number", "bargeIn": True})
    assert "followUpWindow" in result["rejected"]
    assert settings_store.current()["bargeIn"] is True


def test_choices_outside_the_list_fall_back_to_the_default():
    settings_store.update({"llmProvider": "openrouter"})
    assert settings_store.current()["llmProvider"] == "auto"


@pytest.mark.parametrize("truthy", [True, "1", "true", "on", "yes"])
def test_booleans_accept_what_a_ui_actually_sends(truthy):
    settings_store.update({"bargeIn": truthy})
    assert settings_store.current()["bargeIn"] is True


@pytest.mark.parametrize("falsy", [False, "0", "false", "off", "no", ""])
def test_booleans_reject_what_a_ui_actually_sends(falsy):
    settings_store.update({"bargeIn": falsy})
    assert settings_store.current()["bargeIn"] is False


def test_describe_exposes_a_renderable_schema():
    schema = {row["key"]: row for row in settings_store.describe()}

    assert schema["bargeIn"]["kind"] == "bool"
    assert schema["bargeIn"]["liveReload"] is True
    assert schema["closeOutHour"]["liveReload"] is False
    assert set(schema["llmProvider"]["options"]) == {"auto", "ollama", "lmstudio"}
    assert all(row["label"] for row in schema.values()), "every setting needs a label"
