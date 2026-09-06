"""
Routing contract for the workbook tools.

Two things are protected here:

1. **Which tool wins.** Twelve new tools with speakable examples were added to a
   corpus of 55; a new phrasing can silently steal a phrase an existing tool
   used to own. The README makes adding a case here mandatory for a reason.

2. **That these never reach the LLM.** Every one of these sentences has a
   completely predictable shape, and Tier 2 costs ~9 seconds on this machine.
   If slot extraction regresses, the tool still fires but the assistant becomes
   slow - which is exactly the failure that is hard to notice in a test suite
   and impossible to miss in daily use.
"""

import pytest

# Every tool module, for the same reason test_router.py imports them all: the
# registry is populated by import side effect, and a missing module silently
# changes which tool wins a phrase - which would make the "existing tools keep
# their phrases" half of this file pass for the wrong reason.
import google_tools  # noqa: F401  - registers gmail/calendar tools
import media_tools  # noqa: F401  - registers media tools
import tools_assistant  # noqa: F401  - registers reminder/memory tools
import tools_core  # noqa: F401  - registers core tools
import tools_files  # noqa: F401  - registers file mutation tools
import tools_web  # noqa: F401  - registers web search and weather
import tools_workbook  # noqa: F401  - registers the tools under test
import windows_tools  # noqa: F401  - registers system tools
import router


def route_tool(message: str):
    """The tool Tier 0/1 would pick, without executing anything."""
    if router.chitchat(message):
        return None
    hit = router.fast_path(message)
    if hit:
        return hit[0]
    match = router.semantic_match(message)
    return match[0] if match else None


# ───────────────────────── tool selection ─────────────────────────


@pytest.mark.parametrize(
    "message,expected",
    [
        ("my goal this quarter is to ship the channel relaunch", "set_goal"),
        ("what am I working towards", "list_goals"),
        ("I'm committing to scripting episode three by friday", "make_commitment"),
        ("what did I say I'd do today", "my_commitments"),
        ("I finished scripting episode three", "keep_commitment"),
        ("push the edit to tomorrow", "reschedule_commitment"),
        ("log that I worked ninety minutes on the edit", "log_progress"),
        ("what am I avoiding", "what_am_i_avoiding"),
        ("close out my day", "close_out_day"),
        ("how was my week", "weekly_review"),
    ],
)
def test_workbook_phrases_route_to_their_tool(message, expected):
    assert route_tool(message) == expected


@pytest.mark.parametrize(
    "message,expected",
    [
        # The tools most at risk of being stolen by the new corpus.
        ("remind me to check the render in 20 minutes", "create_reminder"),
        ("what's on my agenda", "get_agenda"),
        ("open chrome", "launch_app"),
        ("turn the volume down", "set_volume"),
        ("take a screenshot", "take_screenshot"),
    ],
)
def test_existing_tools_keep_their_phrases(message, expected):
    assert route_tool(message) == expected


# ───────────────────────── slot extraction ─────────────────────────


def slots(tool: str, message: str):
    return router._extract_slots(tool, message)


def test_scope_is_read_from_the_question():
    """Without this the tool answered about today no matter what was asked."""
    assert slots("my_commitments", "what did I say I would do this week") == {"scope": "week"}
    assert slots("my_commitments", "what's on my plate today") == {"scope": "today"}
    assert slots("my_commitments", "show me everything outstanding") == {"scope": "all"}


@pytest.mark.parametrize(
    "message,text,when",
    [
        ("I'm committing to scripting episode three by friday", "scripting episode three", "friday"),
        ("put me down for editing the intro tomorrow", "editing the intro", "tomorrow"),
        ("hold me to finishing the outline", "finishing the outline", ""),
    ],
)
def test_commitment_text_and_date_are_separated(message, text, when):
    extracted = slots("make_commitment", message)
    assert extracted["text"] == text
    assert extracted["when"] == when


@pytest.mark.parametrize(
    "message,expected",
    [
        ("I finished scripting episode three", "scripting episode three"),
        ("mark the gym session as done", "gym session"),
        ("check off calling the accountant", "calling the accountant"),
    ],
)
def test_completion_strips_the_framing(message, expected):
    assert slots("keep_commitment", message)["text"] == expected


def test_reschedule_splits_what_from_when():
    assert slots("reschedule_commitment", "push the edit to tomorrow") == {
        "text": "edit", "when": "tomorrow",
    }
    # No date given: tomorrow is the sane default for "push this".
    assert slots("reschedule_commitment", "postpone the accountant call")["when"] == "tomorrow"


@pytest.mark.parametrize(
    "message,minutes",
    [
        ("log that I worked 90 minutes on the edit", 90),
        ("log that I spent 2 hours scripting", 120),
        ("note that I am blocked on b-roll", None),
    ],
)
def test_logged_time_is_parsed(message, minutes):
    extracted = slots("log_progress", message)
    assert extracted.get("minutes") == minutes


def test_goal_title_drops_the_lead_in():
    assert slots("set_goal", "my goal this quarter is to ship the channel relaunch")["title"] == (
        "ship the channel relaunch"
    )


def test_argument_free_tools_need_no_reasoning():
    """These must not fall through to Tier 2 - they take no arguments at all."""
    for tool in ("what_am_i_avoiding", "weekly_review", "list_goals"):
        assert slots(tool, "anything") == {}
