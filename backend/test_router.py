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
    ("open github.com", "open_website"),
    ("go to youtube.com", "open_website"),
    ("navigate to reddit.com", "open_website"),
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
    ("generate video titles", "curate_video_ideas"),
    # ── buzzcaf ──
    ("run the next step", "buzzcaf_execute_step"),
    ("show my video projects", "buzzcaf_list_projects"),
    ("start a video about AI agents", "buzzcaf_create_project"),
    # ── system control ──
    ("what am I looking at", "get_active_window"),
    ("what apps are open", "list_open_windows"),
    ("what's on my clipboard", "read_clipboard"),
    ("take a screenshot", "take_screenshot"),
    ("lock my pc", "lock_screen"),
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
    spec = REGISTRY["curate_video_ideas"]
    ok, cleaned, _ = validate_args(spec, {"count": "3"})
    assert ok and cleaned["count"] == 3

    spec = REGISTRY["launch_app"]
    ok, _cleaned, err = validate_args(spec, {})
    assert not ok and "app_name" in err


def test_tool_result_legacy_adapter():
    result = ToolResult.from_legacy({"success": False, "error": "nope"})
    assert not result.ok and result.summary == "nope"


# ───────────────────────── speech delivery ─────────────────────────

from prosody import build_delivery, normalize_for_speech, say_number  # noqa: E402


@pytest.mark.parametrize("value,expected", [
    (0, "zero"), (7, "seven"), (13, "thirteen"), (40, "forty"),
    (25, "twenty five"), (100, "one hundred"), (342, "three hundred forty two"),
    (1500, "one thousand five hundred"),
])
def test_say_number(value, expected):
    assert say_number(value) == expected


NORMALIZE_CASES = [
    # Times must be spoken, not spelled out digit by digit.
    ("Meeting at 10:30 AM", "ten thirty"),
    ("Call at 3pm", "three"),
    ("Volume set to 40%", "forty percent"),
    # Opaque ids should never be read aloud at all.
    ("Added task t-1786555392658 today", "1786555392658"),
    # Paths collapse to a filename; the timestamp inside it is dropped.
    (r"Saved to C:\Users\singh\data\screenshot_1786554297.png.", "1786554297"),
    ("task(s) remaining", "tasks"),
]


@pytest.mark.parametrize("raw,fragment", NORMALIZE_CASES)
def test_normalize_for_speech(raw, fragment):
    spoken = normalize_for_speech(raw)
    if fragment.isdigit():
        assert fragment not in spoken, f"{fragment!r} should not be spoken: {spoken!r}"
    else:
        assert fragment in spoken, f"expected {fragment!r} in {spoken!r}"


def test_semicolon_list_becomes_sentences():
    """A run-on agenda is the main thing that made narration sound robotic."""
    spoken = build_delivery("2 tasks: 10:30 AM - Review; 02:00 PM - Ship").text
    assert ";" not in spoken
    assert spoken.count(".") >= 2


EMOTION_CASES = [
    ("You asked me to remind you: stand up", {"is_reminder": True}, "urgent"),
    ("Opening chrome.", {"action": "fast_path"}, "upbeat"),
    ("I couldn't find that file.", {"tool_ok": False}, "apologetic"),
    ("Hey. What do you need?", {"action": "chitchat"}, "warm"),
    ("Some long narration.", {"long_form": True}, "reading"),
]


@pytest.mark.parametrize("text,kwargs,expected", EMOTION_CASES)
def test_emotion_inference(text, kwargs, expected):
    assert build_delivery(text, **kwargs).emotion == expected


def test_inline_cue_sets_emotion_and_is_not_spoken():
    """*excited* used to be deleted outright; it should steer delivery instead."""
    delivery = build_delivery("*excited* The render finished!")
    assert delivery.emotion == "upbeat"
    assert "excited" not in delivery.text


def test_emotions_differ_in_speed():
    urgent = build_delivery("x", is_reminder=True)
    calm = build_delivery("y", long_form=True)
    assert urgent.speed > 1.0 > calm.speed
    assert urgent.blend_with != calm.blend_with
