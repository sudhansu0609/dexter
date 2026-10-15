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
    assert settings_store.current()["llmProvider"] == "lmstudio"


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


# ───────────────────────── v11.1: the tier models are settings ─────────────────────────


def test_the_ollama_model_can_be_changed_without_editing_env(monkeypatch):
    """Owner: "make sure I can change the models in the local instance like Ollama"."""
    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b", raising=False)
    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b", raising=False)

    result = settings_store.update({"smartModel": "gemma4:12b", "fastModel": ""})

    assert router.SMART_MODEL == "gemma4:12b"
    assert router.FAST_MODEL == ""  # blank = fast tier off
    assert {"smartModel", "fastModel"} <= set(result["applied"])
    assert os.environ["DEXTER_SMART_MODEL"] == "gemma4:12b"


def test_the_fallback_rung_is_a_setting_of_its_own(monkeypatch):
    """v12: the ladder is two rows in Settings → Model, not an .env edit."""
    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b", raising=False)
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "qwen2.5:7b", raising=False)

    result = settings_store.update({"smartModel": "gemma-4-12b",
                                    "smartModelFallback": "qwen2.5:7b"})

    assert router.SMART_MODEL == "gemma-4-12b"
    assert router.SMART_MODEL_FALLBACK == "qwen2.5:7b"
    assert {"smartModel", "smartModelFallback"} <= set(result["applied"])
    assert os.environ["DEXTER_SMART_MODEL_FALLBACK"] == "qwen2.5:7b"

    schema = {row["key"]: row for row in settings_store.describe()}
    assert schema["smartModelFallback"]["kind"] == "model"
    assert schema["smartModelFallback"]["liveReload"] is True

    # Blank is a real answer: one rung, as before v12.
    settings_store.update({"smartModelFallback": ""})
    assert router.SMART_MODEL_FALLBACK == ""


def test_model_ids_keep_their_case_and_need_not_be_in_the_list(monkeypatch):
    monkeypatch.setattr(router, "SMART_MODEL", "", raising=False)
    settings_store.update({"smartModel": "google/Gemma-4-12B"})
    assert settings_store.current()["smartModel"] == "google/Gemma-4-12B"


def test_model_settings_list_what_the_servers_have(monkeypatch):
    import hybrid_llm

    monkeypatch.setattr(hybrid_llm.llm_router, "list_models", lambda: {
        "providers": {
            "ollama": {"online": True, "models": ["qwen2.5:7b", "qwen2.5:3b", "gemma4:12b"]},
            "lmstudio": {"online": True, "models": ["google/gemma-4-12b"]},
            "llamacpp": {"online": False, "models": []},
        }
    })
    settings_store.update({"smartModel": "qwen2.5:7b"})
    row = next(r for r in settings_store.describe() if r["key"] == "smartModel")
    assert row["kind"] == "model"
    assert row["options"][0] == "qwen2.5:7b"  # the current value leads
    assert "gemma4:12b" in row["options"] and "google/gemma-4-12b" in row["options"]
    assert row["liveReload"] is True


# ── v13: the fast tier is a model that acts ─────────────────────────────────


def test_the_fast_tier_defaults_to_the_model_that_measured_best(monkeypatch):
    """qwen3.5:4b: 27 of 30 tool choices right, 111 tok/s, 3.0 GB on this card.

    The default matters because it is what a machine with nothing in its .env
    gets, and because the Settings row and the router must not disagree about
    what "unset" means - one of them showing qwen2.5:3b while the other ran
    something else is how a measured decision quietly stops applying.
    """
    monkeypatch.delenv("DEXTER_FAST_MODEL", raising=False)
    schema = {row["key"]: row for row in settings_store.describe()}
    assert schema["fastModel"]["default"] == "qwen3.5:4b"
    assert schema["fastModel"]["value"] == "qwen3.5:4b"

    source = open(os.path.join(os.path.dirname(os.path.abspath(router.__file__)),
                               "router.py"), encoding="utf-8").read()
    assert 'os.getenv("DEXTER_FAST_MODEL", "qwen3.5:4b")' in source
    # ...and blank is still "no fast tier at all" - the owner's off switch, not
    # a value to be helpfully replaced by a default.
    assert '.strip()' in source.split('os.getenv("DEXTER_FAST_MODEL"')[1][:40]


def test_letting_the_fast_model_act_is_a_switch_not_an_env_edit(monkeypatch):
    monkeypatch.setattr(router, "FAST_ACT_ENABLED", True, raising=False)

    result = settings_store.update({"fastAct": False})

    assert router.FAST_ACT_ENABLED is False
    assert "fastAct" in result["applied"]
    assert os.environ["DEXTER_FAST_ACT"] == "0"

    schema = {row["key"]: row for row in settings_store.describe()}
    assert schema["fastAct"]["kind"] == "bool"
    assert schema["fastAct"]["liveReload"] is True
    assert schema["fastAct"]["group"] == "model"

    settings_store.update({"fastAct": True})
    assert router.FAST_ACT_ENABLED is True
