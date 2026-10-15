"""
Router regression tests.

The table below is the contract for intent handling: which tier claims a
phrasing, which tool it resolves to, and which arguments get extracted. Intent
accuracy is the thing most likely to silently drift as tools are added - a new
tool's example phrasings can steal a phrase another tool used to own - so this
table is the guard against that.

Run:  pytest backend/test_router.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Every tool module must be imported here: the registry is populated by import
# side effect, and a missing module silently changes which tool wins a phrase.
import google_tools  # noqa: F401  - registers gmail/calendar tools
import media_tools  # noqa: F401  - registers media tools
import tools_assistant  # noqa: F401  - registers reminder/memory tools
import tools_core  # noqa: F401  - registers core tools
import tools_browser  # noqa: F401  - registers the browser tools
import tools_code  # noqa: F401  - registers the code tools
import tools_studio  # noqa: F401  - registers the Studio tools
import windows_tools  # noqa: F401  - registers system tools
from router import _extract_slots, chitchat, fast_path, semantic_match
from timeparse import parse_recurrence, parse_when, strip_time_phrases
from tool_registry import REGISTRY, ToolResult, validate_args


def resolve(message):
    """Mirror route()'s tier order without executing anything."""
    if chitchat(message):
        return 0, None, {}
    hit = fast_path(message)
    if hit:
        return 0, hit[0], hit[1]
    match = semantic_match(message)
    if match:
        args = _extract_slots(match[0], message)
        if args is not None:
            return 1, match[0], args
    return 2, None, {}


# (utterance, expected tool or None for "must reach the agent/chat tier")
INTENT_CASES = [
    # ── apps and websites ──
    ("open chrome", "launch_app"),
    ("launch photoshop", "launch_app"),
    ("start spotify", "launch_app"),
    ("fire up vscode", "launch_app"),
    ("can you open notepad please", "launch_app"),
    ("open github.com", "browser_open"),
    ("go to youtube.com", "browser_open"),
    ("navigate to reddit.com", "browser_open"),
    # ── files ──
    ("find my invoice file", "search_files"),
    ("search for a file called report", "search_files"),
    ("create a folder called invoices", "create_folder"),
    ("make a new folder named assets", "create_folder"),
    # ── planner ──
    ("what is on my agenda", "get_agenda"),
    ("plan my day", "get_agenda"),
    ("my tasks", "get_agenda"),
    ("add a task to review the script at 3pm", "add_task"),
    ("mark the gym task as done", "complete_task"),
    ("I finished reviewing the script", "complete_task"),
    # ── reminders ──
    ("remind me to check the render in 30 minutes", "create_reminder"),
    ("remind me to call mom at 5pm", "create_reminder"),
    ("set a reminder for the meeting tomorrow at 9", "create_reminder"),
    ("wake me up in 45 minutes", "create_reminder"),
    ("what reminders do I have", "list_reminders"),
    ("cancel the reminder about the render", "cancel_reminder"),
    # ── memory ──
    ("remember that my main channel is Beyond3Baje", "remember_fact"),
    ("remember my editor is vscode", "remember_fact"),
    ("what do you remember about my channel", "recall_fact"),
    # ── notes ──
    ("show my notes", "list_notes"),
    ("take a note about the client meeting", "create_note"),
    # ── media and content ──
    ("play some lofi music", "play_on_youtube"),
    ("put on some jazz", "play_on_youtube"),
    # ── buzzcaf ──
    ("run the next step", "buzzcaf_execute_step"),
    ("show my video projects", "buzzcaf_list_projects"),
    ("start a video about AI agents", "buzzcaf_create_project"),
    ("open the studio", "buzzcaf_start_studio"),
    ("anything waiting on me in the studio", "studio_pending_approvals"),
    ("approve that step", "studio_approve_step"),
    ("ask the studio for title ideas for khayal3baje", "studio_chat"),
    ("show saved topics for khayal3baje", "studio_saved_topics"),
    # ── system control ──
    ("what am I looking at", "get_active_window"),
    ("what apps are open", "list_open_windows"),
    ("what's on my clipboard", "read_clipboard"),
    ("take a screenshot", "take_screenshot"),
    ("lock my pc", "lock_screen"),
    # v11.1: power. "restart my pc" used to reach nothing at all (the v9
    # "restart <app>" rule matched and yielded None; no tool existed).
    ("restart my pc", "restart_pc"),
    ("I want you to restart my PC.", "restart_pc"),
    ("can you reboot the computer", "restart_pc"),
    ("shut down the pc", "shutdown_pc"),
    ("turn off my computer", "shutdown_pc"),
    ("sign me out", "sign_out"),
    ("log off windows", "sign_out"),
    ("cancel the restart", "cancel_shutdown"),
    ("empty the recycle bin", "empty_recycle_bin"),
    # ... and none of these are the PC.
    ("restart vrdeep", "restart_app"),
    ("shut down discord", "close_app"),
    ("set volume to 25", "set_volume"),
    # The literal number in set_brightness's own example used to steal this one.
    ("set volume to 40", "set_volume"),
    ("turn the volume up", "set_volume"),
    ("mute the sound", "set_volume"),
    ("dim the screen", "set_brightness"),
    ("set brightness to 40", "set_brightness"),
    # ── media ──
    ("pause the music", "media_control"),
    ("skip this song", "media_control"),
    ("what's playing", "get_now_playing"),
    # ── google ──
    ("what's on my calendar", "list_calendar_events"),
    ("check my email", "check_email"),
    # ── time ──
    ("what time is it", "get_current_time"),
    # ── must NOT be captured by a tool ──
    ("what is the capital of France", None),
    ("why is the sky blue", None),
    ("explain quantum computing to me", None),
    ("tell me a joke", None),
    ("write a haiku about rain", None),
]


@pytest.mark.parametrize("message,expected_tool", INTENT_CASES)
def test_intent_resolves_to_expected_tool(message, expected_tool):
    tier, tool_name, _args = resolve(message)
    if expected_tool is None:
        assert tool_name is None, (
            f"{message!r} should have reached the agent tier but was captured "
            f"by {tool_name} at tier {tier}"
        )
    else:
        assert tool_name == expected_tool, (
            f"{message!r} resolved to {tool_name} (tier {tier}), expected {expected_tool}"
        )


GREETINGS = ["hi", "hello", "hey there", "good morning", "how are you", "thanks", "dexter"]


@pytest.mark.parametrize("message", GREETINGS)
def test_greetings_answer_instantly(message):
    assert chitchat(message), f"{message!r} should get a canned reply, not hit the LLM"


SLOT_CASES = [
    ("open chrome", "launch_app", "app_name", "chrome"),
    ("create a folder called invoices", "create_folder", "path", "invoices"),
    ("remind me to check the render in 30 minutes", "create_reminder", "text", "check the render"),
    ("remind me to call mom at 5pm", "create_reminder", "text", "call mom"),
    ("cancel the reminder about the render", "cancel_reminder", "text", "the render"),
    ("remember that my main channel is Beyond3Baje", "remember_fact", "value", "Beyond3Baje"),
    ("mark the gym task as done", "complete_task", "title", "gym"),
    ("add a task to review the script at 3pm", "add_task", "title", "review the script"),
    # Enum and optional-only arguments both used to be mishandled: the whole
    # utterance was passed as the enum value, and volume was called with nothing.
    ("pause the music", "media_control", "action", "pause"),
    ("skip this song", "media_control", "action", "next"),
    ("go back a song", "media_control", "action", "previous"),
    ("resume playback", "media_control", "action", "play"),
    ("set volume to 25", "set_volume", "level", 25),
    ("turn the volume up", "set_volume", "change", 10),
    ("turn the volume down", "set_volume", "change", -10),
    ("mute the sound", "set_volume", "mute", True),
    ("unmute", "set_volume", "mute", False),
    ("max volume", "set_volume", "level", 100),
]


@pytest.mark.parametrize("message,tool_name,arg,expected", SLOT_CASES)
def test_slot_extraction(message, tool_name, arg, expected):
    _tier, resolved, args = resolve(message)
    assert resolved == tool_name, f"{message!r} resolved to {resolved}"
    assert args.get(arg) == expected, f"{arg}={args.get(arg)!r}, expected {expected!r}"


# ───────────────────────── time parsing ─────────────────────────


def test_relative_times():
    from datetime import datetime

    base = datetime(2026, 8, 12, 10, 30)
    assert parse_when("in 20 minutes", base) == datetime(2026, 8, 12, 10, 50)
    assert parse_when("in 2 hours", base) == datetime(2026, 8, 12, 12, 30)
    assert parse_when("tomorrow at 5", base) == datetime(2026, 8, 13, 17, 0)
    assert parse_when("at 9am", base) == datetime(2026, 8, 13, 9, 0)


def test_non_times_are_rejected():
    """Bare numbers in ordinary sentences must not be read as clock times."""
    assert parse_when("remind me about 3 things") is None
    assert parse_when("buy 2 apples") is None


def test_recurrence():
    assert parse_recurrence("every weekday at 9am") == {
        "day_of_week": "mon-fri", "hour": 9, "minute": 0
    }
    assert parse_recurrence("in 20 minutes") is None


def test_strip_time_phrases():
    assert strip_time_phrases("remind me to call mom in 20 minutes") == "call mom"
    assert strip_time_phrases("add task review the script tomorrow at 5") == "review the script"


# ───────────────────────── registry ─────────────────────────


def test_every_tool_has_examples_and_description():
    for spec in REGISTRY.values():
        assert spec.description, f"{spec.name} has no description"
        assert spec.examples, f"{spec.name} has no example phrasings (Tier 1 needs them)"


def test_openai_schema_shape():
    for spec in REGISTRY.values():
        schema = spec.openai_schema()
        assert schema["type"] == "function"
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        for required in params["required"]:
            assert required in params["properties"]


def test_validate_args_coerces_and_rejects():
    spec = REGISTRY["set_volume"]
    ok, cleaned, _ = validate_args(spec, {"level": "30"})
    assert ok and cleaned["level"] == 30

    spec = REGISTRY["launch_app"]
    ok, _cleaned, err = validate_args(spec, {})
    assert not ok and "app_name" in err


def test_tool_result_legacy_adapter():
    result = ToolResult.from_legacy({"success": False, "error": "nope"})
    assert not result.ok and result.summary == "nope"


# ───────────────────────── tiered fast/smart models ─────────────────────────


def _stub_earlier_tiers(monkeypatch, router):
    """Force a message past chitchat/fast-path/semantic so it reaches Tier 1.5."""
    monkeypatch.setattr(router, "chitchat", lambda t: None)
    monkeypatch.setattr(router, "fast_path", lambda t, session="default": None)
    monkeypatch.setattr(router, "semantic_match", lambda t: None)
    monkeypatch.setattr(router.llm_router, "resolve_provider", lambda p="auto": "ollama")
    monkeypatch.setattr(router.llm_router, "provider_for_model", lambda m, preferred="": "ollama")
    monkeypatch.setattr(router, "_keep_fast_model_warm", lambda provider: None)
    monkeypatch.setattr(router, "_release_smart_model_soon", lambda provider, model: None)
    monkeypatch.setattr(router, "FAST_PROVIDER", "")
    monkeypatch.setattr(router, "SMART_PROVIDER", "")
    # v13: the ladder has a third rung that *loads* (in RAM). Nothing in this
    # file may reach it by accident — a test that wants the CPU rung says so.
    monkeypatch.setattr(router.model_steward, "fits_on_cpu",
                        lambda model, provider="ollama": False)
    monkeypatch.setattr(
        router.model_steward, "ensure",
        lambda *a, **kw: pytest.fail("no test may load a model; patch ensure to opt in"),
    )


def test_fast_tier_answers_simple_turn(monkeypatch):
    import router
    from hybrid_llm import LLMResponse

    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    _stub_earlier_tiers(monkeypatch, router)

    captured = {}

    def fake_chat(messages, **kw):
        captured["model"] = kw.get("model")
        captured["tools"] = kw.get("tools")
        return LLMResponse(provider="ollama", content="The moon is ~384,400 km away.")

    monkeypatch.setattr(router.llm_router, "chat", fake_chat)
    monkeypatch.setattr(
        router, "agent_loop",
        lambda *a, **k: pytest.fail("simple turn must not escalate to agent_loop"),
    )

    res = router.route("tell me a fun fact about the moon")
    assert res.action == "fast_chat"
    assert "moon" in res.reply.lower()
    assert captured["model"] == "fast-model"   # answered by the small model
    assert captured["tools"] is None           # fast pass carries no tools


def test_fast_tier_escalates_to_smart_model(monkeypatch):
    import router
    from hybrid_llm import LLMResponse

    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "SMART_MODEL", "smart-model")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(
        router.llm_router, "chat",
        lambda messages, **kw: LLMResponse(provider="ollama", content="<ESCALATE>"),
    )

    seen = {}

    def fake_agent(message, **kw):
        seen["provider"] = kw.get("provider")
        seen["model"] = kw.get("model")
        return router.RouteResult(reply="done", action="agent")

    monkeypatch.setattr(router, "agent_loop", fake_agent)

    res = router.route("open my email and summarise the latest three")
    assert res.action == "agent"
    assert seen["provider"] == "ollama"       # pinned to the resolved backend
    assert seen["model"] == "smart-model"     # escalated to the bigger model


def test_fast_tier_keeps_running_while_the_big_llama_server_is_up(monkeypatch):
    """The 27B on 8089 used to switch the fast tier off entirely (every message
    waited on the big model). Now the small model runs on its own server and
    only the escalated pass goes to the shared llama-server."""
    import router
    from hybrid_llm import LLMResponse

    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "SMART_MODEL", "smart-model")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router.llm_router, "resolve_provider", lambda p="auto": "llamacpp")

    calls = []

    def fake_chat(messages, **kw):
        calls.append((kw.get("provider"), kw.get("model")))
        return LLMResponse(provider="ollama", content="Paris.")

    monkeypatch.setattr(router.llm_router, "chat", fake_chat)
    res = router.route("capital of france?")
    assert res.action == "fast_chat" and calls == [("ollama", "fast-model")]

    # Escalation: the smart pass lands on the llama-server with its own (single) model.
    monkeypatch.setattr(router.llm_router, "chat", lambda messages, **kw: LLMResponse(provider="ollama", content="<ESCALATE>"))
    seen = {}
    monkeypatch.setattr(router, "agent_loop", lambda message, **kw: seen.update(kw) or router.RouteResult(reply="ok", action="agent"))
    router.route("open my calendar")
    assert seen["provider"] == "llamacpp" and seen["model"] is None


def test_fast_tier_is_skipped_when_nothing_serves_the_small_model(monkeypatch):
    import router

    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router.llm_router, "provider_for_model", lambda m, preferred="": None)
    monkeypatch.setattr(router.llm_router, "chat", lambda *a, **k: pytest.fail("fast model must not be called"))
    seen = {}
    monkeypatch.setattr(router, "agent_loop", lambda message, **kw: seen.update(kw) or router.RouteResult(reply="ok", action="agent"))
    router.route("tell me something")
    assert seen["provider"] == "auto" and seen["model"] is None


def test_fast_reply_streams_tokens_but_never_leaks_the_sentinel(monkeypatch):
    import router
    from hybrid_llm import LLMResponse

    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    _stub_earlier_tiers(monkeypatch, router)

    def streaming_chat(pieces):
        def fake(messages, **kw):
            # The fast *act* pass carries no on_token: a tool call streamed to
            # the UI would be leaked protocol (v13).
            for piece in pieces:
                if kw.get("on_token"):
                    kw["on_token"](piece)
            return LLMResponse(provider="ollama", content="".join(pieces))
        return fake

    events = []
    pieces = ["The moon", " is about", " 384,400 km away", " on average.", " Light", " takes 1.3 s."]
    monkeypatch.setattr(router.llm_router, "chat", streaming_chat(pieces))
    res = router.route("how far is the moon", on_event=lambda kind, data: events.append((kind, data)))
    assert res.action == "fast_chat"
    tokens = [d["text"] for k, d in events if k == "token"]
    # The opening is held until the first sentence can be judged, then the rest streams live.
    assert tokens == ["The moon is about 384,400 km away on average.", " Light", " takes 1.3 s."]
    assert "".join(tokens) == "".join(pieces)

    events.clear()
    monkeypatch.setattr(router.llm_router, "chat", streaming_chat(["<ESC", "ALATE>"]))
    monkeypatch.setattr(router, "agent_loop", lambda message, **kw: router.RouteResult(reply="did it", action="agent"))
    res = router.route("open spotify please", on_event=lambda kind, data: events.append((kind, data)))
    assert res.action == "agent"
    assert not [e for e in events if e[0] == "token"]          # nothing of the sentinel reached the UI
    assert [e for e in events if e[0] == "escalated"][0][1]["provider"] == "ollama"


def test_sentinel_gate_holds_only_while_a_sentinel_is_still_possible():
    import router

    out = []
    gate = router._SentinelGate(lambda kind, data: out.append(data["text"]))
    gate.feed("<")            # could be "<ESCALATE>": hold
    assert out == []
    gate.feed("b>Bold</b> is html, not a sentinel.")   # prose after all: release everything held
    assert out == ["<b>Bold</b> is html, not a sentinel."]
    gate.feed(" hi")
    assert out[-1] == " hi"

    held = []
    gate = router._SentinelGate(lambda kind, data: held.append(data["text"]))
    gate.feed("<ESCALATE>")
    gate.feed(" more")
    assert held == []          # a real sentinel never reaches the UI


@pytest.mark.parametrize("message", [
    "drop the Verification probe commitment, it was a test",
    "open spotify",
    "can you please remind me to call mum at 6",
    "hey dexter, set the volume to 40",
    "I need you to add a task for tomorrow",
    "let's create a note about the shoot",
    "go to youtube.com",
])
def test_instructions_never_reach_the_chat_only_fast_pass(monkeypatch, message):
    """The fast model may act (v13), but it is never asked to *talk* about an
    instruction: a small model with no tools answers "I've set that for you" and
    nothing has happened. With `_fast_act` off, every instruction escalates
    exactly as it did before v13."""
    import router

    assert router.looks_like_action(message)
    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    monkeypatch.setattr(router, "FAST_ACT_ENABLED", False)
    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router.llm_router, "chat", lambda *a, **k: pytest.fail("the fast model must not see an instruction"))
    seen = {}
    monkeypatch.setattr(router, "agent_loop", lambda m, **kw: seen.update(kw) or router.RouteResult(reply="ok", action="agent"))
    assert router.route(message).action == "agent"
    assert seen["provider"] == "ollama"


@pytest.mark.parametrize("message", [
    "what's the capital of australia?",
    "tell me a joke",
    "how are you today",
    "explain what a mutex does in one line",
    "thanks, that was helpful",
    "who wrote hamlet",
])
def test_conversation_stays_on_the_fast_model(message):
    import router

    assert not router.looks_like_action(message)


def test_fast_reply_that_pretends_to_act_is_escalated(monkeypatch):
    import router
    from hybrid_llm import LLMResponse

    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router, "looks_like_action", lambda text: False)   # the guard the model slipped past

    def fake(messages, **kw):
        if kw.get("tools"):  # v13: the acting round that follows; it declines
            return LLMResponse(provider="ollama", content="")
        for piece in ("Understood, the ", "Verification probe commitment ", "will be dropped."):
            kw["on_token"](piece)
        return LLMResponse(provider="ollama", content="Understood, the Verification probe commitment will be dropped.")

    monkeypatch.setattr(router.llm_router, "chat", fake)
    monkeypatch.setattr(router, "agent_loop", lambda m, **kw: router.RouteResult(reply="Dropped it.", action="agent"))
    events = []
    res = router.route("the verification probe thing is a test, it can go", on_event=lambda k, d: events.append((k, d)))
    assert res.action == "agent" and res.reply == "Dropped it."
    assert not [e for e in events if e[0] == "token"]     # the false claim never reached the UI


def test_claims_action_examples():
    import router

    assert router.claims_action("Understood, the commitment will be dropped.")
    assert router.claims_action("I'll remind you at 6.")
    assert router.claims_action("Reminder set for 6 PM.")
    assert router.claims_action("Done.")
    assert router.claims_action("I've added that to your list.")
    assert router.claims_action("I'm opening Spotify now.")
    assert not router.claims_action("The capital of Australia is Canberra.")
    assert not router.claims_action("A mutex ensures only one thread holds a resource at a time.")
    assert not router.claims_action("You're welcome! Anything else?")
    assert not router.claims_action("I'm doing well, thanks for asking!")
    assert not router.claims_action("The city was called Byzantium before it was renamed.")
    assert not router.claims_action("I have heard that Canberra was chosen as a compromise.")


def test_smart_pass_goes_where_the_smart_model_lives(monkeypatch):
    """LM Studio up (no qwen there) + Ollama up (has it): the smart pass must
    run on Ollama, not send an Ollama tag to LM Studio."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "smart-model")
    monkeypatch.setattr(router, "SMART_PROVIDER", "")
    monkeypatch.setattr(router.llm_router, "resolve_provider", lambda p="auto": "lmstudio")
    monkeypatch.setattr(router.llm_router, "provider_for_model", lambda m, preferred="": "ollama")
    assert router.smart_backend() == ("ollama", "smart-model")
    # Nobody serves it: stay on the resolved provider with its own default model.
    monkeypatch.setattr(router.llm_router, "provider_for_model", lambda m, preferred="": None)
    assert router.smart_backend() == ("lmstudio", None)
    # The shared llama-server serves one model of its own; SMART_MODEL does not apply.
    monkeypatch.setattr(router.llm_router, "resolve_provider", lambda p="auto": "llamacpp")
    assert router.smart_backend() == ("llamacpp", None)


# ── v8: day-to-day phrases that used to reach the model and get a promise instead of an action ──
@pytest.mark.parametrize("message, tool, args", [
    ("note: thumbnail idea - red arrow pointing at the fort gate", "create_note", {"content": "thumbnail idea - red arrow pointing at the fort gate"}),
    ("jot this down: call the printer about the banners", "create_note", {"content": "call the printer about the banners"}),
    ("remember that my editor is davinci resolve", "remember_fact", {"key": "editor", "value": "davinci resolve"}),
    ("remember that I edit in davinci resolve", "remember_fact", {"key": "edit in", "value": "I edit in davinci resolve"}),
    ("keep in mind the channel intro is 4 seconds", "remember_fact", {"key": "channel intro", "value": "4 seconds"}),
    ("remember to call mum at 6pm", "create_reminder", {"text": "call mum", "when": "remember to call mum at 6pm"}),
    ("remind me at 2:30pm to prep the studio lights", "create_reminder", {"text": "prep the studio lights", "when": "remind me at 2:30pm to prep the studio lights"}),
    ("mark the voiceover commitment done", "complete_task", {"title": "voiceover"}),
    ("tick off the thumbnail task", "complete_task", {"title": "thumbnail"}),
    ("what time is it in tokyo", "get_current_time", {"place": "tokyo"}),
    ("what's the time in new york right now", "get_current_time", {"place": "new york"}),
    # v11: closing an app means every window of it, gracefully (close_app),
    # not the one window whose title matched best (close_window).
    ("close notepad", "close_app", {"name": "notepad"}),
    ("quit spotify", "close_app", {"name": "spotify"}),
    ("what's on my screen right now", "get_active_window", {}),
    ("what did we get done today", "mission_status", {}),
    ("cancel the studio lights reminder", "cancel_reminder", {"text": "studio lights"}),
    ("drop the Verification probe commitment, it was a test", "drop_commitment", {"text": "Verification probe", "why": "it was a test"}),
    ("scrap the podcast task", "drop_commitment", {"text": "podcast"}),
    ("cancel the reminder about the studio lights", "cancel_reminder", {"text": "the studio lights"}),
    ("click File in notepad", "click_in_app", {"text": "File", "app": "notepad"}),
    ("click save in word", "click_in_app", {"text": "save", "app": "word"}),
    ("type hello in notepad", "type_text", {"text": "hello", "window": "notepad"}),
    ("type hello world into search box in spotify", "type_text", {"text": "hello world", "into": "search", "window": "spotify"}),
    ("click the first result", "open_result", {"position": 1}),
    ("click the second video", "youtube_play", {"position": 2}),
])
def test_day_to_day_phrases_are_deterministic(message, tool, args):
    hit = fast_path(message)
    assert hit is not None, f"{message!r} fell through to the model"
    assert hit[0] == tool and hit[1] == args


def test_desktop_vs_browser_disambiguation(monkeypatch):
    import desktop_control as dc
    import router

    # 1. When Notepad is the active window
    notepad_win = dc.Win(hwnd=10, title="Untitled - Notepad", pid=100, process="notepad.exe")
    monkeypatch.setattr(dc, "active_window", lambda: notepad_win)

    hit_click = router.fast_path("click Save")
    assert hit_click == ("click_text", {"text": "Save"})

    hit_type = router.fast_path("type Hello into search bar")
    assert hit_type == ("type_text", {"text": "Hello", "into": "search", "submit": False})

    # 2. When Chrome is the active window
    chrome_win = dc.Win(hwnd=20, title="Google - Google Chrome", pid=200, process="chrome.exe")
    monkeypatch.setattr(dc, "active_window", lambda: chrome_win)

    hit_click_chrome = router.fast_path("click Save")
    assert hit_click_chrome == ("browser_click", {"target": "Save"})

    hit_type_chrome = router.fast_path("type Hello into search bar")
    assert hit_type_chrome == ("browser_type", {"text": "Hello", "into": "search", "submit": False})


@pytest.mark.parametrize("message", ["close it", "shut down the pc", "close this tab", "close out my day"])
def test_close_needs_a_real_target(message):
    hit = fast_path(message)
    assert hit is None or hit[0] != "close_window"


@pytest.mark.parametrize("message", [
    "thanks dexter, that's all for today", "ok that's it", "I'm done for today", "that'll be all, thanks", "signing off",
])
def test_sign_offs_are_instant(message):
    assert chitchat(message) and "talk tomorrow" in chitchat(message)


def test_live_data_questions_skip_the_fast_model(monkeypatch):
    import router

    assert router.needs_live_data("what's on my screen right now")
    assert router.needs_live_data("what's on my calendar tomorrow")
    assert router.needs_live_data("what's the weather like")
    assert router.needs_live_data("what editor do I use")
    assert router.needs_live_data("what's my channel called")
    assert not router.needs_live_data("what's the capital of australia")
    assert not router.needs_live_data("in one sentence, what is a mutex")
    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    _stub_earlier_tiers(monkeypatch, router)
    from hybrid_llm import LLMResponse

    def chat(*a, **k):
        # v13: the small model may be asked - but only with tools in hand (the
        # acting round). Answering about the screen from memory is the failure.
        if not k.get("tools"):
            pytest.fail("the small model must not answer about the user's screen without tools")
        return LLMResponse(provider="ollama", content="")  # no call: escalate

    monkeypatch.setattr(router.llm_router, "chat", chat)
    monkeypatch.setattr(router, "agent_loop", lambda m, **kw: router.RouteResult(reply="You're in Notepad.", action="agent"))
    assert router.route("what is on my screen").action == "agent"


def test_fast_model_refusal_is_escalated(monkeypatch):
    import router
    from hybrid_llm import LLMResponse

    assert router.is_refusal("I don't have direct access to your screen right now.")
    assert router.is_refusal("I'm unable to check that for you.")
    assert not router.is_refusal("The capital of Australia is Canberra.")
    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router, "needs_live_data", lambda text: False)   # slipped past the pre-check

    def fake(messages, **kw):
        if kw.get("tools"):  # v13: the acting round that follows; it declines
            return LLMResponse(provider="ollama", content="")
        kw["on_token"]("I don't have direct access to your screen right now, sorry.")
        return LLMResponse(provider="ollama", content="I don't have direct access to your screen right now, sorry.")

    monkeypatch.setattr(router.llm_router, "chat", fake)
    monkeypatch.setattr(router, "agent_loop", lambda m, **kw: router.RouteResult(reply="You're in Notepad.", action="agent"))
    events = []
    res = router.route("tell me what you see", on_event=lambda k, d: events.append((k, d)))
    assert res.action == "agent" and not [e for e in events if e[0] == "token"]


def test_time_elsewhere():
    from tools_assistant import get_current_time

    assert "Tokyo" in get_current_time("tokyo").summary and "ahead" in get_current_time("tokyo").summary
    assert get_current_time("narnia").ok is False
    assert get_current_time().ok and "It's" in get_current_time().summary


def test_agent_loop_nudges_a_model_that_only_claims(monkeypatch):
    """The 7B answered 'the commitment is dropped' without calling anything.
    One nudge sends it back; the second pass must call the tool."""
    import router
    from hybrid_llm import LLMResponse

    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router, "FAST_MODEL", "")
    monkeypatch.setattr(router, "TIERED_ENABLED", False)
    replies = [
        LLMResponse(provider="ollama", content="Got it, the voiceover commitment is dropped."),
        LLMResponse(provider="ollama", content="", tool_calls=[{"id": "1", "name": "drop_commitment", "arguments": {"text": "voiceover"}}]),
        LLMResponse(provider="ollama", content="Dropped the voiceover commitment."),
    ]
    seen = []

    def fake_chat(messages, **kw):
        seen.append([m["content"][:40] for m in messages if m["role"] == "user"])
        return replies.pop(0)

    monkeypatch.setattr(router.llm_router, "chat", fake_chat)
    monkeypatch.setattr(
        router, "_run_tool",
        lambda name, args, on_event=None, session="default": (ToolResult(ok=True, summary=f"Dropped '{args['text']}'.", data={}), False),
    )
    res = router.route("drop the voiceover one")
    assert [c["name"] for c in res.tool_calls] == ["drop_commitment"]
    assert res.action == "agent"
    assert "Nothing happened" in str(seen)   # the nudge went into the conversation


# ── Sentinel: asking before loading, and letting go when told ────────────────
#
# The point of these two is the same: the GPU is shared, and Dexter is the only
# claimant that can choose *not* to take it. Both paths have to work with
# Sentinel absent as well, because most machines will not have it.


def test_a_denied_reservation_answers_honestly_and_loads_nothing(monkeypatch):
    """Sentinel says the card is full: Dexter must say so, not load anyway.

    Loading a 5 GB model into 2 GB of free VRAM is the exact failure this whole
    feature exists to stop — Ollama silently falls back to the CPU and every
    turn takes ten seconds.
    """
    import router

    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    # The fast pass escalates.
    monkeypatch.setattr(router, "_fast_reply", lambda *a, **kw: None)
    monkeypatch.setattr(
        router, "sentinel_reserve_for_smart",
        lambda model: (False, {"free_mib": 2048, "reason": "not enough VRAM"}),
    )

    loaded = []
    monkeypatch.setattr(router, "agent_loop", lambda *a, **kw: loaded.append(kw) or None)
    # Nothing resident to fall back to, so the refusal stands on its own.
    monkeypatch.setattr(router.model_steward, "resident_fallback", lambda provider: None)

    events = []
    res = router.route("write me a limerick about a frozen render",
                       on_event=lambda name, data: events.append((name, data)))

    assert loaded == [], "the smart model must not be loaded after a refusal"
    assert res.action == "gpu_busy"
    assert "2.0 GB" in res.reply, res.reply
    assert "pause batch jobs" in res.reply.lower()
    names = [name for name, _ in events]
    assert "gpu_busy" in names
    payload = next(data for name, data in events if name == "gpu_busy")
    assert payload["free_mib"] == 2048


def test_a_refusal_still_answers_on_whatever_is_already_loaded(monkeypatch):
    """Sentinel saying no must not make Dexter go quiet.

    The one thing it must not do is load: the reply runs on the model that is
    already in memory, and leads with who has the card.
    """
    import router

    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b")
    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    monkeypatch.setattr(router, "_fast_reply", lambda *a, **kw: None)
    monkeypatch.setattr(
        router, "sentinel_reserve_for_smart",
        lambda model: (False, {
            "free_mib": 1200, "reason": "not enough VRAM",
            "blockers": [{"process": "VrDeep", "pid": 33228, "vram_mib": 10240, "class": "batch"}],
        }),
    )
    monkeypatch.setattr(router.model_steward, "resident_fallback", lambda provider: "qwen2.5:3b")

    ran = []
    monkeypatch.setattr(
        router, "agent_loop",
        lambda *a, **kw: ran.append(kw.get("model"))
        or router.RouteResult(reply="Here you go.", action="agent"),
    )
    res = router.route("what did we get done yesterday")

    assert ran == ["qwen2.5:3b"], "it must run on what is loaded, never load the 7B"
    assert res.action == "gpu_busy_degraded"
    assert "VrDeep" in res.reply
    assert "Here you go." in res.reply


def test_a_granted_reservation_lets_the_escalation_through(monkeypatch):
    import router

    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    monkeypatch.setattr(router, "_fast_reply", lambda *a, **kw: None)
    monkeypatch.setattr(router, "sentinel_reserve_for_smart", lambda model: (True, {}))

    ran = []
    monkeypatch.setattr(
        router, "agent_loop",
        lambda *a, **kw: ran.append(kw.get("model")) or router.RouteResult(reply="done", action="agent"),
    )
    res = router.route("rename every file in the downloads folder")
    assert ran == ["qwen2.5:7b"]
    assert res.action == "agent"


def test_an_escalated_turn_hands_the_booking_back(monkeypatch):
    """A booking outlives its purpose the moment the model is resident.

    Found on the live run against Sentinel v2: the escalation reserved 5490 MiB
    and never released it, so for the next 90 seconds Sentinel counted those
    gigabytes *twice* — once as a Dexter booking and once as the llama-server's
    real usage — and refused the next claimant on memory that was not actually
    spoken for. It has to go back even when the turn raises.
    """
    import model_steward
    import router

    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router, "FAST_MODEL", "fast-model")
    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    monkeypatch.setattr(router, "_fast_reply", lambda *a, **kw: None)
    monkeypatch.setattr(router, "sentinel_reserve_for_smart", lambda model: (True, {}))
    monkeypatch.setattr(router, "_release_smart_model_soon", lambda *a, **kw: None)

    released = []
    monkeypatch.setattr(model_steward, "release", lambda why="": released.append(why))

    monkeypatch.setattr(
        router, "agent_loop",
        lambda *a, **kw: router.RouteResult(reply="done", action="agent"),
    )
    router.route("rename every file in the downloads folder")
    assert released == ["smart-model"], "the escalation kept the booking"

    # …and a turn that blows up must not leak it either.
    released.clear()

    def _boom(*a, **kw):
        raise RuntimeError("the model fell over")

    monkeypatch.setattr(router, "agent_loop", _boom)
    with pytest.raises(RuntimeError):
        router.route("rename every file in the downloads folder")
    assert released == ["smart-model"], "a failed turn leaked the booking"


def test_with_sentinel_absent_a_reservation_is_granted(monkeypatch):
    """No guardian must mean no change in behaviour, not a refusal to think."""
    import router

    monkeypatch.setattr(router, "SENTINEL_ENABLED", False)
    granted, details = router.sentinel_reserve_for_smart("qwen2.5:7b")
    assert granted is True
    assert details.get("absent") is True


def test_a_smart_model_already_on_the_card_books_nothing(monkeypatch):
    """Booking a resident model counts it twice and gets refused — then the turn
    stepped down to the fallback, which unloaded the 12B that was ready."""
    import model_steward
    import router

    monkeypatch.setattr(router, "SENTINEL_ENABLED", True)
    monkeypatch.setattr(model_steward, "_resident_place", lambda model: "gpu")
    monkeypatch.setattr(model_steward, "reserve",
                        lambda *a, **k: pytest.fail("a resident model must not be booked"))
    granted, details = router.sentinel_reserve_for_smart("gemma-4-12b")
    assert granted is True and details.get("resident") is True


# ── v12: the smart ladder ────────────────────────────────────────────────────
#
# One fixed smart model means an empty card buys nothing and a busy one costs
# the whole turn. The ladder asks for the better model first and takes the
# smaller one when Sentinel says no — the guardian still decides, the ladder
# only decides what is asked for, and in what order.


def _ladder_world(monkeypatch, top="gemma-4-12b", fallback="qwen2.5:7b"):
    """Tier 1.5 reached, both rungs served by Ollama, nothing real in reach."""
    import router

    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router, "FAST_MODEL", "qwen2.5:3b")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    monkeypatch.setattr(router, "SMART_MODEL", top)
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", fallback)
    monkeypatch.setattr(router, "_fast_reply", lambda *a, **kw: None)
    monkeypatch.setattr(router.model_steward, "release", lambda why="": None)
    monkeypatch.setattr(router.model_steward, "note_ladder", lambda info: None)
    return router


def test_the_ladder_asks_for_the_better_model_first(monkeypatch):
    router = _ladder_world(monkeypatch)
    asked, ran = [], []
    monkeypatch.setattr(router, "sentinel_reserve_for_smart",
                        lambda model: asked.append(model) or (True, {}))
    monkeypatch.setattr(
        router, "agent_loop",
        lambda *a, **kw: ran.append(kw.get("model")) or router.RouteResult(reply="done", action="agent"),
    )
    res = router.route("rename every file in the downloads folder")

    assert asked == ["gemma-4-12b"], "the fallback must not even be asked for"
    assert ran == ["gemma-4-12b"]
    assert "ladder" not in res.data, "a turn on the top rung has nothing to apologise for"


def test_a_refused_top_rung_steps_down_instead_of_giving_up(monkeypatch):
    """The whole point of v12: BuzzEdit has the card, the answer still arrives."""
    router = _ladder_world(monkeypatch)
    blockers = [{"process": "ComfyUI", "pid": 4242, "vram_mib": 9216, "class": "batch"}]
    asked, ran, released = [], [], []

    def reserve(model):
        asked.append(model)
        if model == "gemma-4-12b":
            return False, {"free_mib": 5000, "reason": "not enough VRAM", "blockers": blockers}
        return True, {}

    monkeypatch.setattr(router, "sentinel_reserve_for_smart", reserve)
    monkeypatch.setattr(router, "_release_smart_model_soon",
                        lambda provider, model: released.append(model))
    monkeypatch.setattr(
        router, "agent_loop",
        lambda *a, **kw: ran.append(kw.get("model")) or router.RouteResult(reply="done", action="agent"),
    )
    res = router.route("rename every file in the downloads folder")

    assert asked == ["gemma-4-12b", "qwen2.5:7b"]
    assert ran == ["qwen2.5:7b"], "the refused rung must never be loaded"
    assert res.action == "agent" and res.reply == "done"
    assert res.data["ladder"] == {
        "model": "qwen2.5:7b", "provider": "ollama",
        "steppedDownFrom": "gemma-4-12b", "place": "gpu", "blockers": blockers,
    }
    assert released == ["qwen2.5:7b"], "the rung actually used is the one released"


def test_every_rung_refused_is_the_gpu_busy_path_exactly_as_before(monkeypatch):
    router = _ladder_world(monkeypatch)
    asked, loaded = [], []
    monkeypatch.setattr(
        router, "sentinel_reserve_for_smart",
        lambda model: asked.append(model) or (
            False, {"free_mib": 2048, "reason": "not enough VRAM"}),
    )
    monkeypatch.setattr(router, "agent_loop", lambda *a, **kw: loaded.append(kw) or None)
    monkeypatch.setattr(router.model_steward, "resident_fallback", lambda provider: None)

    res = router.route("rename every file in the downloads folder")

    assert asked == ["gemma-4-12b", "qwen2.5:7b"]
    assert loaded == [], "a refusal on every rung must still load nothing"
    assert res.action == "gpu_busy"
    assert "2.0 GB" in res.reply


def test_a_rung_nobody_serves_is_skipped_not_asked_for(monkeypatch):
    """A model that was never pulled must not become a reservation for memory
    nothing can use — and must not swallow the rung below it either."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "gemma-4-12b")
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "qwen2.5:7b")
    monkeypatch.setattr(router, "SMART_PROVIDER", "")
    monkeypatch.setattr(router.llm_router, "resolve_provider", lambda p="auto": "ollama")
    monkeypatch.setattr(router.llm_router, "provider_for_model",
                        lambda m, preferred="": "ollama" if m == "qwen2.5:7b" else None)
    assert router.smart_ladder() == [("ollama", "qwen2.5:7b")]

    # Nobody serves either: the provider's own default model, as before v12.
    monkeypatch.setattr(router.llm_router, "provider_for_model", lambda m, preferred="": None)
    assert router.smart_ladder() == [("ollama", None)]


def test_the_same_tag_on_both_rungs_is_one_rung(monkeypatch):
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "qwen2.5:7b")
    monkeypatch.setattr(router, "SMART_PROVIDER", "")
    monkeypatch.setattr(router.llm_router, "resolve_provider", lambda p="auto": "ollama")
    monkeypatch.setattr(router.llm_router, "provider_for_model", lambda m, preferred="": "ollama")
    assert router.smart_ladder() == [("ollama", "qwen2.5:7b")]


def test_with_no_top_rung_configured_there_is_no_ladder(monkeypatch):
    """Blank smart model means the deployment has no smart tier."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "")
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "qwen2.5:7b")
    assert router.smart_ladder() == []


def _shed_recorder(monkeypatch, fast="", smart="qwen2.5:7b", fallback=""):
    """Watch what the steward unloads, with nothing real in reach."""
    import model_steward
    import router

    monkeypatch.setattr(router, "SMART_MODEL", smart)
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", fallback)
    monkeypatch.setattr(router, "FAST_MODEL", fast)
    monkeypatch.setattr(router, "SMART_PROVIDER", "ollama")
    released = []
    monkeypatch.setattr(
        model_steward.llm_router, "release_model",
        lambda provider, model, after="0": released.append((provider, model, after)) or True,
    )
    monkeypatch.setattr(model_steward.llm_router, "loaded_models", lambda: [])
    return released


def test_vram_tight_drops_the_smart_model(monkeypatch):
    """A red VramTight event unloads the big model immediately."""
    import router

    released = _shed_recorder(monkeypatch)
    router._on_sentinel_event({
        "VramTight": {"level": "red", "used": 1, "total": 2, "free": 700 * 1024 * 1024,
                      "over_budget": [], "deadline_secs": 10}
    })
    assert released == [("ollama", "qwen2.5:7b", "0")]


def test_a_red_also_gives_up_the_small_model(monkeypatch):
    """On red the instant tier goes too: a second of latency beats the whole
    machine paging, and VramRelief brings it back."""
    import router

    released = _shed_recorder(monkeypatch, fast="qwen2.5:3b")
    router._on_sentinel_event({
        "VramTight": {"level": "red", "used": 1, "total": 2, "free": 1,
                      "over_budget": [], "deadline_secs": 10}
    })
    assert [m for _, m, _ in released] == ["qwen2.5:7b", "qwen2.5:3b"]


def test_ram_tight_asking_to_shed_sheds(monkeypatch):
    """RAM pressure is handled the same way as VRAM: give memory back."""
    import router

    released = _shed_recorder(monkeypatch)
    router._on_sentinel_event({
        "RamTight": {"zone": "Yellow", "phys_avail": 1024 * 1024 * 1024,
                     "commit_fraction": 0.9, "over_budget": [], "deadline_secs": 20,
                     "ask": "shed"},
    })
    assert [m for _, m, _ in released] == ["qwen2.5:7b"]


def test_a_preempt_sheds_and_hands_the_booking_back(monkeypatch):
    """Being preempted is Sentinel giving us the chance to leave with dignity."""
    import model_steward
    import router

    released = _shed_recorder(monkeypatch)
    handed_back = []
    monkeypatch.setattr(model_steward, "release", lambda why="": handed_back.append(why))
    router._on_sentinel_event({
        "Preempt": {"client": "dexter", "by": "buzzcode", "mib": 12500,
                     "ram_mib": 6500, "deadline_secs": 20},
    })
    assert [m for _, m, _ in released] == ["qwen2.5:7b"]
    assert handed_back == ["preempted"]


def test_being_named_over_budget_also_drops_the_smart_model(monkeypatch):
    """Orange plus 'you are over your budget' is Sentinel asking politely."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(router, "SMART_MODEL_FALLBACK", "")
    monkeypatch.setattr(router, "SMART_PROVIDER", "ollama")
    released = []
    monkeypatch.setattr(
        router.llm_router, "release_model",
        lambda provider, model, after="0": released.append(model) or True,
    )
    router._on_sentinel_event({
        "VramTight": {"level": "orange", "used": 1, "total": 2, "free": 3,
                      "over_budget": ["Dexter"], "deadline_secs": 0}
    })
    assert released == ["qwen2.5:7b"]


def test_an_orange_from_another_app_drops_the_smart_model(monkeypatch):
    """Dexter is last in line for the card (2026-10-15): someone else's orange
    costs it the smart model, without waiting for red."""
    import model_steward
    import router

    released = _shed_recorder(monkeypatch, fast="qwen2.5:3b")
    monkeypatch.setattr(model_steward, "RESTORE_AFTER_PRESSURE", False)
    model_steward._ours.clear()
    router._on_sentinel_event({
        "VramTight": {"level": "orange", "used": 1, "total": 2, "free": 3,
                      "over_budget": ["buzzcode"], "deadline_secs": 0}
    })
    assert [m for _, m, _ in released] == ["qwen2.5:7b"]


def test_other_events_change_nothing(monkeypatch):
    """Dropping 5 GB of model is not free — it must not happen on every twitch."""
    import router

    monkeypatch.setattr(router, "SMART_MODEL", "qwen2.5:7b")
    monkeypatch.setattr(router, "SMART_PROVIDER", "ollama")
    released = []
    monkeypatch.setattr(
        router.llm_router, "release_model",
        lambda provider, model, after="0": released.append(model) or True,
    )
    for event in (
        {"VramTight": {"level": "yellow", "used": 1, "total": 2, "free": 3,
                       "over_budget": ["buzzcode"], "deadline_secs": 0}},
        {"VramRelief": {"used": 1, "total": 2, "free": 3}},
        {"Heartbeat": {"every_secs": 30}},
        {"Frozen": {"pid": 1, "name": "x.exe", "class": "batch", "by": "gpu", "reason": ""}},
        "not even a dict",
    ):
        router._on_sentinel_event(event)
    assert released == []


def test_pause_and_resume_batch_are_fast_path_phrases():
    """Said when the machine already feels slow — no model in the way."""
    import router

    for phrase in ("pause batch jobs", "pause the background work",
                   "freeze the batch jobs", "park the background jobs"):
        assert router.fast_path(phrase) == ("pause_batch_jobs", {}), phrase
    for phrase in ("resume batch jobs", "resume the background jobs",
                   "unfreeze the batch work", "unpause background processing"):
        assert router.fast_path(phrase) == ("resume_batch_jobs", {}), phrase


def test_shutdown_hands_back_the_booking_and_the_registration(monkeypatch):
    """A dead client still listed as registered holds a budget nobody is using.

    Observed live: killing the process with Stop-Process leaves the row behind
    in Sentinel's table, because the lifespan shutdown never ran. A *clean*
    shutdown must release, unregister and stop the event thread, in that order.
    """
    import model_steward
    import router
    import sentinel_client

    order = []

    class FakeClient:
        def unregister(self):
            order.append("unregister")
            return True

        def stop(self):
            order.append("stop")

    monkeypatch.setattr(sentinel_client, "client", lambda: FakeClient())
    monkeypatch.setattr(model_steward, "release", lambda why="": order.append(f"release:{why}"))
    monkeypatch.setattr(router, "_sentinel_started", True)

    router.sentinel_stop()

    assert order == ["release:shutdown", "unregister", "stop"]
    assert router._sentinel_started is False


def test_shutdown_is_a_no_op_when_we_never_started(monkeypatch):
    import router
    import sentinel_client

    monkeypatch.setattr(router, "_sentinel_started", False)
    monkeypatch.setattr(sentinel_client, "client",
                        lambda: (_ for _ in ()).throw(AssertionError("must not touch the pipe")))
    router.sentinel_stop()  # must not raise


def test_shutdown_survives_a_sentinel_that_is_already_gone(monkeypatch):
    """The service can be stopped before Dexter is; that is not an error path
    worth crashing a shutdown over."""
    import router
    import sentinel_client

    monkeypatch.setattr(router, "_sentinel_started", True)
    monkeypatch.setattr(sentinel_client, "client",
                        lambda: (_ for _ in ()).throw(OSError("pipe is gone")))
    router.sentinel_stop()
    assert router._sentinel_started is False


def test_registration_declares_ram_and_priority_only_to_a_v2_service(monkeypatch):
    """A v1 service refuses a Register carrying keys it does not know, so the
    new fields are held back until Hello says they are understood."""
    import router
    import sentinel_client

    calls = []

    class FakeClient:
        def __init__(self, features):
            self._features = features

        def features(self):
            return list(self._features)

        def version(self):
            return "2.0.0" if self._features else None

        def register(self, pid, cls, budget, ram_budget_mib=None, priority=None, label=None):
            calls.append({"cls": cls, "budget": budget, "ram": ram_budget_mib,
                          "priority": priority, "label": label})
            return True

        def subscribe(self, handler):
            return None

    monkeypatch.setattr(sentinel_client, "client", lambda: FakeClient(["ram_reserve", "query"]))
    monkeypatch.setattr(router, "_sentinel_started", False)
    monkeypatch.setattr(router, "SENTINEL_ENABLED", True)
    router.sentinel_start()
    assert calls[-1]["ram"] == router.DEXTER_RAM_BUDGET_MIB
    assert calls[-1]["label"] == "Dexter"

    monkeypatch.setattr(sentinel_client, "client", lambda: FakeClient([]))
    monkeypatch.setattr(router, "_sentinel_started", False)
    router.sentinel_start()
    assert calls[-1]["ram"] is None, "a v1 service must not be sent ram_budget_mib"
    assert calls[-1]["priority"] is None
    assert calls[-1]["label"] is None


# ── v13: the fast model acts ─────────────────────────────────────────────────
#
# Until now the small model was only ever allowed to decline: every instruction
# went to the big one, so "close notepad" paid for a 7 GB load. qwen3.5:4b picks
# the right tool in 27 of 30 measured cases with the same eight candidates the
# big model gets, so it gets exactly one round with them. One — a wrong action
# is far worse than a slow one, and everything below is about that boundary.

_FAST_ACT_TOOLS = ["launch_app", "list_open_windows", "forget_fact"]


def _fast_act_world(monkeypatch, tools=None):
    """Tier 1.6 reachable, a fixed candidate set, and no real tool ever runs."""
    import router

    monkeypatch.setattr(router, "FAST_MODEL", "qwen3.5:4b")
    monkeypatch.setattr(router, "TIERED_ENABLED", True)
    monkeypatch.setattr(router, "FAST_ACT_ENABLED", True)
    _stub_earlier_tiers(monkeypatch, router)
    monkeypatch.setattr(router, "candidate_tools", lambda message: list(tools or _FAST_ACT_TOOLS))
    return router


def _fast_chat(call=None, content="", voiced="Done."):
    """A fast model that answers the tool round with `call` and then voices it.

    The two rounds are told apart by `tools`: the acting round carries them, the
    one-line report never does.
    """
    from hybrid_llm import LLMResponse

    def chat(messages, **kw):
        if kw.get("tools"):
            assert kw.get("model") == "qwen3.5:4b", "the acting round is the small model's"
            assert kw.get("on_token") is None, "a tool call must never be streamed to the UI"
            assert kw.get("think") is False, "the fast tier does not deliberate"
            return LLMResponse(
                provider="ollama", content=content,
                tool_calls=[{"id": "c0", "name": call[0], "arguments": call[1]}] if call else [],
            )
        return LLMResponse(provider="ollama", content=voiced)

    return chat


def test_the_fast_model_takes_one_action_without_waking_the_big_one(monkeypatch):
    """The point of v13: "open spotify" costs a second, not a 7 GB load."""
    router = _fast_act_world(monkeypatch)
    ran = []
    monkeypatch.setattr(router, "execute",
                        lambda name, args: ran.append((name, args)) or ToolResult(
                            ok=True, summary="Opened Spotify."))
    monkeypatch.setattr(router.llm_router, "chat",
                        _fast_chat(("launch_app", {"app_name": "spotify"}), voiced="Spotify is open."))
    monkeypatch.setattr(router, "agent_loop",
                        lambda *a, **kw: pytest.fail("a settled action must not escalate"))

    res = router.route("open spotify")

    assert ran == [("launch_app", {"app_name": "spotify"})]
    assert res.action == "fast_act" and res.tier == 1
    assert res.reply == "Spotify is open."
    assert res.data["fastAct"] is True and res.data["model"] == "qwen3.5:4b"
    assert res.tool_calls[0]["name"] == "launch_app"


def test_no_call_from_the_fast_model_escalates_exactly_as_before(monkeypatch):
    router = _fast_act_world(monkeypatch)
    monkeypatch.setattr(router, "execute", lambda name, args: pytest.fail("nothing to run"))
    monkeypatch.setattr(router.llm_router, "chat", _fast_chat(None, content="<ESCALATE>"))
    seen = {}
    monkeypatch.setattr(router, "agent_loop",
                        lambda m, **kw: seen.update(kw) or router.RouteResult(reply="done", action="agent"))
    monkeypatch.setattr(router, "sentinel_reserve_for_smart", lambda model: (True, {}))

    assert router.route("open spotify").action == "agent"
    assert seen["provider"] == "ollama"


def test_invalid_arguments_escalate_rather_than_being_guessed_at(monkeypatch):
    """A 4B that calls the right tool with the wrong argument shape is exactly
    the case the big model exists for. Nothing runs in the meantime."""
    router = _fast_act_world(monkeypatch)
    monkeypatch.setattr(router, "execute",
                        lambda name, args: pytest.fail("an invalid call must never run"))
    monkeypatch.setattr(router.llm_router, "chat", _fast_chat(("launch_app", {"nope": 1})))
    monkeypatch.setattr(router, "agent_loop",
                        lambda m, **kw: router.RouteResult(reply="done", action="agent"))
    monkeypatch.setattr(router, "sentinel_reserve_for_smart", lambda model: (True, {}))

    assert router.route("open spotify").action == "agent"


def test_a_tool_it_was_never_offered_is_a_hallucination_not_a_command(monkeypatch):
    router = _fast_act_world(monkeypatch)
    monkeypatch.setattr(router, "execute",
                        lambda name, args: pytest.fail("a tool that was not on offer must never run"))
    monkeypatch.setattr(router.llm_router, "chat",
                        _fast_chat(("format_the_disk", {"drive": "C:"})))
    monkeypatch.setattr(router, "agent_loop",
                        lambda m, **kw: router.RouteResult(reply="done", action="agent"))
    monkeypatch.setattr(router, "sentinel_reserve_for_smart", lambda model: (True, {}))

    assert router.route("open spotify").action == "agent"


def test_a_chain_sentence_never_reaches_the_fast_model(monkeypatch):
    """Two acts in one breath. The small model gets one round, so it would do
    the first half and silently drop the second; the agent loop does both."""
    router = _fast_act_world(monkeypatch)
    monkeypatch.setattr(router.llm_router, "chat",
                        lambda *a, **k: pytest.fail("a chain must not be handed to the fast model"))
    monkeypatch.setattr(router, "agent_loop",
                        lambda m, **kw: router.RouteResult(reply="done", action="agent"))
    monkeypatch.setattr(router, "sentinel_reserve_for_smart", lambda model: (True, {}))

    message = "open youtube and search for lo-fi beats"
    assert router.is_desktop_chain(message)
    assert router.route(message).action == "agent"


def test_a_confirm_tool_still_asks_before_anything_happens(monkeypatch):
    """`forget_fact` deletes something. Being fast changes nothing about that."""
    router = _fast_act_world(monkeypatch)
    monkeypatch.setattr(router, "execute",
                        lambda name, args: pytest.fail("a confirm tool must not run unasked"))
    monkeypatch.setattr(router.llm_router, "chat", _fast_chat(("forget_fact", {"key": "my editor"})))
    monkeypatch.setattr(router, "agent_loop",
                        lambda m, **kw: pytest.fail("asking is not escalating"))

    res = router.route("forget what I told you about my editor", session="confirm-test")

    assert res.needs_confirmation is True and res.action == "confirm"
    assert res.reply.strip() != ""


def test_a_tool_that_failed_hands_the_turn_to_the_big_model(monkeypatch):
    """One round only. If it did not work, the small model does not get to try
    again - the model with the whole registry does."""
    router = _fast_act_world(monkeypatch)
    monkeypatch.setattr(router, "execute", lambda name, args: ToolResult(
        ok=False, summary="I could not find that app."))
    monkeypatch.setattr(router.llm_router, "chat", _fast_chat(("launch_app", {"app_name": ""})))
    seen = {}
    monkeypatch.setattr(router, "agent_loop",
                        lambda m, **kw: seen.update(kw) or router.RouteResult(reply="done", action="agent"))
    monkeypatch.setattr(router, "sentinel_reserve_for_smart", lambda model: (True, {}))

    assert router.route("open spotify").action == "agent"
    assert seen["provider"] == "ollama"


def test_the_voiced_line_falls_back_to_the_tools_own_summary(monkeypatch):
    """The summary was always a correct answer; the voicing is a courtesy. A
    model that pads, empties or leaks the sentinel loses the privilege."""
    router = _fast_act_world(monkeypatch)
    monkeypatch.setattr(router, "execute",
                        lambda name, args: ToolResult(ok=True, summary="Opened Spotify."))
    for voiced in ("", "  ", "<ESCALATE>", "Sure! " + "I have started Spotify for you. " * 12):
        monkeypatch.setattr(router.llm_router, "chat",
                            _fast_chat(("launch_app", {"app_name": "spotify"}), voiced=voiced))
        res = router.route("open spotify")
        assert res.reply == "Opened Spotify.", voiced[:30]


def test_with_fast_act_off_an_instruction_goes_straight_up(monkeypatch):
    router = _fast_act_world(monkeypatch)
    monkeypatch.setattr(router, "FAST_ACT_ENABLED", False)
    monkeypatch.setattr(router.llm_router, "chat",
                        lambda *a, **k: pytest.fail("the switch is off"))
    monkeypatch.setattr(router, "agent_loop",
                        lambda m, **kw: router.RouteResult(reply="done", action="agent"))
    monkeypatch.setattr(router, "sentinel_reserve_for_smart", lambda model: (True, {}))
    assert router.route("open spotify").action == "agent"


# -- v13: rung 3, the ladder in RAM ------------------------------------------


def test_a_refused_card_runs_the_fallback_rung_in_ram(monkeypatch):
    """Sentinel will not give the card to either rung. A 7B in system memory is
    a slower answer; silence is not an answer at all."""
    import model_steward

    router = _ladder_world(monkeypatch)
    blockers = [{"process": "VrDeep", "pid": 33228, "vram_mib": 10240, "class": "batch"}]
    monkeypatch.setattr(
        router, "sentinel_reserve_for_smart",
        lambda model: (False, {"free_mib": 1200, "reason": "not enough VRAM", "blockers": blockers}),
    )
    monkeypatch.setattr(router.model_steward, "fits_on_cpu",
                        lambda model, provider="ollama": True)
    asked = []

    def ensure(model, purpose, provider="ollama", keep_alive="30m", place="gpu", **kw):
        asked.append((model, purpose, place))
        return model_steward.Decision(True, purpose, model=model, provider=provider, loaded=True)

    monkeypatch.setattr(router.model_steward, "ensure", ensure)
    ran = []
    monkeypatch.setattr(
        router, "agent_loop",
        lambda *a, **kw: ran.append(kw.get("model")) or router.RouteResult(reply="done", action="agent"),
    )

    res = router.route("rename every file in the downloads folder")

    assert asked == [("qwen2.5:7b", "smart-model", "cpu")]
    assert ran == ["qwen2.5:7b"], "the rung that was granted RAM is the one that answers"
    assert res.action == "agent" and res.reply == "done"
    assert res.data["ladder"]["place"] == "cpu"
    assert res.data["ladder"]["steppedDownFrom"] == "gemma-4-12b"


def test_a_rung_too_big_for_ram_is_never_put_there(monkeypatch):
    """The 12B manages 8 tok/s in system memory. Waiting for the card beats that,
    and saying so beats both."""
    router = _ladder_world(monkeypatch, top="gemma-4-12b", fallback="gemma-4-12b")
    monkeypatch.setattr(
        router, "sentinel_reserve_for_smart",
        lambda model: (False, {"free_mib": 1200, "reason": "not enough VRAM"}),
    )
    monkeypatch.setattr(router.model_steward, "fits_on_cpu",
                        lambda model, provider="ollama": False)
    monkeypatch.setattr(router.model_steward, "ensure",
                        lambda *a, **kw: pytest.fail("the big rung must never be loaded into RAM"))
    monkeypatch.setattr(router.model_steward, "resident_fallback", lambda provider: None)

    res = router.route("rename every file in the downloads folder")
    assert res.action == "gpu_busy"


def test_ram_refused_too_is_still_the_gpu_busy_path(monkeypatch):
    """Sentinel is the judge of RAM as well. Refused everywhere means refused."""
    import model_steward

    router = _ladder_world(monkeypatch)
    monkeypatch.setattr(
        router, "sentinel_reserve_for_smart",
        lambda model: (False, {"free_mib": 800, "reason": "not enough VRAM"}),
    )
    monkeypatch.setattr(router.model_steward, "fits_on_cpu",
                        lambda model, provider="ollama": True)
    monkeypatch.setattr(
        router.model_steward, "ensure",
        lambda model, purpose, **kw: model_steward.Decision(
            False, purpose, model=model, reason="RAM zone is red"),
    )
    monkeypatch.setattr(router.model_steward, "resident_fallback", lambda provider: None)
    monkeypatch.setattr(router, "agent_loop", lambda *a, **kw: pytest.fail("nothing was granted"))

    res = router.route("rename every file in the downloads folder")
    assert res.action == "gpu_busy" and "0.8 GB" in res.reply


def test_only_the_fallback_rung_is_offered_to_the_cpu(monkeypatch):
    """The top rung is the one worth waiting for; the bottom one is the one
    worth running slowly."""
    import router

    monkeypatch.setattr(router.model_steward, "fits_on_cpu", lambda model, provider="ollama": True)
    rungs = [("ollama", "gemma-4-12b"), ("ollama", "qwen2.5:7b")]
    assert router.cpu_rung(rungs) == ("ollama", "qwen2.5:7b")
    # Nothing Ollama serves: there is no lever, so there is no CPU rung.
    assert router.cpu_rung([("lmstudio", "some-id")]) is None
    assert router.cpu_rung([("ollama", None)]) is None


def test_a_question_about_the_present_moment_is_the_fast_models_too(monkeypatch):
    """Found live: "what track is playing right now" is `needs_live_data`, not
    `looks_like_action`, and went to the 12B (7.8 s). One tool call is one
    tool call whichever way the sentence is phrased."""
    router = _fast_act_world(monkeypatch, tools=["get_now_playing", "get_weather"])
    ran = []
    monkeypatch.setattr(router, "execute",
                        lambda name, args: ran.append((name, args)) or ToolResult(
                            ok=True, summary="Nothing is playing."))
    # The chat round (no tools) declines with an empty answer, as the real
    # model did live; the acting round then calls the tool.
    monkeypatch.setattr(router.llm_router, "chat", _fast_chat(("get_now_playing", {}), voiced=""))
    monkeypatch.setattr(router, "agent_loop",
                        lambda *a, **kw: pytest.fail("a live-data question the fast model can answer must not escalate"))

    # Neither predicate matches this sentence; the fast chat pass declines it,
    # and that alone must be enough to give the small model its tool round.
    assert not router.looks_like_action("what track is playing right now")
    res = router.route("what track is playing right now")

    assert ran == [("get_now_playing", {})]
    assert res.action == "fast_act" and res.tier == 1
    assert res.reply == "Nothing is playing."  # the tool's own summary when the voicing round is empty
