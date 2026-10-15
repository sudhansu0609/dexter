"""
The v13 sentences, routed (Tier 0).

One table of "what the owner says" -> "which tool runs", plus the cases that must
*not* be caught, because every rule added to the fast path is a chance to steal a
sentence from another one. The near-misses below are the ones that actually went
wrong while this was being written: "open the studio" reaching the folder opener,
"write this down in Notion" being saved as a two-word note, "every time I open
chrome" being scheduled for 9am.
"""

import pytest

# Importing these registers the tools the rules name, which `fast_path` needs
# for nothing but keeps the suite honest about them existing.
import tools_dictation  # noqa: F401
import tools_files  # noqa: F401 - list_folder / open_folder / list_drives
import tools_handmouse  # noqa: F401
import tools_mission_app  # noqa: F401
import tools_page  # noqa: F401 - search_here / open_result / copy_text / paste_text
import tools_tasks  # noqa: F401 - schedule_task
import tools_youtube  # noqa: F401
from router import fast_path
from tool_registry import get_tool

CASES = [
    # drives and folders
    ("open the c drive", "open_folder"),
    ("open c drive", "open_folder"),
    ("open my downloads folder", "open_folder"),
    ("what's in my downloads", "list_folder"),
    ("what files are in my documents", "list_folder"),
    ("show me what's on the d drive", "list_folder"),
    ("what drives do I have", "list_drives"),
    ("list my drives", "list_drives"),
    ("show me this pc", "open_folder"),
    ("where is my films folder", "find_folder"),
    ("play the first video", "youtube_play"),
    ("play video 1", "youtube_play"),
    ("play 2", "youtube_play"),
    ("play row 1 column 2", "youtube_play"),
    ("first row second video", "youtube_play"),
    ("open the second video on this page", "youtube_play"),
    ("play the third one", "youtube_play"),
    ("open the video called lofi hip hop radio", "youtube_play"),
    ("what videos are on this page", "youtube_results"),
    ("show videos on this page", "youtube_results"),
    ("skip the ad", "youtube_control"),
    ("turn on captions", "youtube_control"),
    ("next video", "youtube_control"),
    # dictation
    ("take notes", "start_dictation"),
    ("take notes in notepad", "start_dictation"),
    ("write this down in notion", "start_dictation"),
    ("take notes from me continuously", "start_dictation"),
    ("stop taking notes", "stop_dictation"),
    ("are you still taking notes", "dictation_status"),
    # scheduling
    ("every morning at 8 review my missions", "schedule_task"),
    ("every friday at 5 curate video ideas", "schedule_task"),
    ("review my missions every evening", "schedule_task"),
    ("schedule a backup for tomorrow at 9", "schedule_task"),
    # the Mission Reminder app
    ("review my missions", "mission_review"),
    ("what are my blocks today", "mission_review"),
    ("what's my next block", "mission_review"),
    ("start my next block", "mission_start_block"),
    ("pause my block", "mission_start_block"),
    ("add a block called deep work at 9am for 50 minutes", "mission_save_block"),
    # ── v13.2: what the owner tried and found missing ──
    ("select the first video", "youtube_play"),
    ("select the second video", "youtube_play"),
    ("select the first profile", "select_nth"),
    ("pick the first profile", "select_nth"),
    ("choose the second option", "select_nth"),
    ("click the first link", "open_result"),
    ("open the first result", "open_result"),
    ("select the third link", "open_result"),
    ("search for lofi beats in the current tab", "search_here"),
    ("search lofi beats on this page", "search_here"),
    ("search this tab for rust tutorials", "search_here"),
    ("search youtube for rust tutorials in this tab", "search_here"),
    ("google best mic for voiceover and click the first link", "search_and_open"),
    ("search youtube for lofi and play the first one", "search_and_open"),
    ("like this video", "youtube_page_action"),
    ("subscribe to this channel", "youtube_page_action"),
    ("open the comments", "youtube_page_action"),
    ("share it", "youtube_page_action"),
    ("copy this paragraph", "copy_text"),
    ("copy everything on this page", "copy_text"),
    ("paste it in the gemini tab", "paste_text"),
    ("paste that into the search box and send it", "paste_text"),
    ("list the results", "page_results"),
]


@pytest.mark.parametrize("phrase,tool", CASES)
def test_the_sentence_reaches_the_right_tool(phrase, tool):
    result = fast_path(phrase)
    assert result is not None, f"{phrase!r} fell through the fast path"
    assert result[0] == tool, f"{phrase!r} -> {result[0]}, expected {tool}"


@pytest.mark.parametrize("phrase,tool", [
    ("open chrome", "launch_app"),
    ("open buzzedit", "open_app"),
    ("open the studio", "buzzcaf_start_studio"),
    ("open youtube", "browser_open"),
    ("open explorer", "launch_app"),
    ("open a new tab", "app_shortcut"),
    ("what's on my screen", "get_active_window"),
    ("what's running", "apps_overview"),
    ("close notepad", "close_app"),
    ("close buzzanimate", "stop_app"),
    ("write this down: buy milk", "create_note"),
    ("note that the render finished", "create_note"),
    ("remind me to call mum at 6", "create_reminder"),
])
def test_the_sentences_that_already_worked_still_do(phrase, tool):
    result = fast_path(phrase)
    assert result is not None and result[0] == tool, f"{phrase!r} -> {result}"


@pytest.mark.parametrize("phrase", [
    "in the downloads folder find the invoice",
    "in chrome, close the tab",
    "every time I open chrome do something",
    "make a note",
])
def test_these_are_left_to_the_tiers_below(phrase):
    assert fast_path(phrase) is None, f"{phrase!r} was caught by a fast rule"


@pytest.mark.parametrize("phrase,expected", [
    # The sentence that started this round: spoken politeness with a comma, which
    # fell past the whole fast path and reached the model as an app name.
    ("okay, open chrome for me please", ("launch_app", {"app_name": "chrome"})),
    ("ok open chrome", ("launch_app", {"app_name": "chrome"})),
    ("please open notepad for me", ("launch_app", {"app_name": "notepad"})),
])
def test_spoken_politeness_does_not_become_part_of_the_command(phrase, expected):
    assert fast_path(phrase) == expected


@pytest.mark.parametrize("phrase,app,rest", [
    ("in chrome select the first profile", "chrome", "select the first profile"),
    ("in chrome, close the tab", "chrome", "close the tab"),
])
def test_an_app_prefix_still_focuses_a_window(phrase, app, rest):
    from router import split_app_prefix

    assert split_app_prefix(phrase) == (app, rest)


@pytest.mark.parametrize("phrase,site,rest", [
    ("in youtube select the first video", "youtube", "select the first video"),
    ("in youtube, like this video", "youtube", "like this video"),
    ("in gmail open the first email", "gmail", "open the first email"),
])
def test_a_site_prefix_is_a_tab_not_a_window(phrase, site, rest):
    """"In YouTube, …" has no window to focus - it means "switch to that tab"."""
    from router import split_app_prefix, split_site_prefix

    assert split_site_prefix(phrase) == (site, rest)
    assert split_app_prefix(phrase) is None


def test_the_arguments_are_filled_in_not_just_the_tool():
    assert fast_path("play the third one") == ("youtube_play", {"position": 3})
    assert fast_path("play video 1") == ("youtube_play", {"position": 1})
    assert fast_path("play 2") == ("youtube_play", {"position": 2})
    assert fast_path("play row 1 column 2") == ("youtube_play", {"row": 1, "col": 2})
    assert fast_path("first row second video") == ("youtube_play", {"row": 1, "col": 2})
    assert fast_path("show videos on this page") == ("youtube_results", {})
    assert fast_path("take notes in notepad") == ("start_dictation", {"into": "notepad"})
    assert fast_path("skip the ad") == ("youtube_control", {"action": "skip ad"})
    assert fast_path("every morning at 8 review my missions") == (
        "schedule_task", {"goal": "review my missions", "when": "every morning at 8"}
    )
    assert fast_path("add a block called deep work at 9am for 50 minutes") == (
        "mission_save_block", {"name": "deep work", "start": "9am", "minutes": 50}
    )
    assert fast_path("select the first profile") == (
        "select_nth", {"what": "profile", "position": 1}
    )
    assert fast_path("search youtube for rust tutorials in this tab") == (
        "search_here", {"query": "rust tutorials", "site": "youtube"}
    )
    assert fast_path("paste it in the gemini tab") == ("paste_text", {"tab": "gemini"})
    assert fast_path("copy everything on this page") == ("copy_text", {"what": "all"})


@pytest.mark.parametrize("phrase,tool", CASES)
def test_every_tool_a_rule_names_actually_exists(phrase, tool):
    assert get_tool(tool) is not None, f"{tool} is routed to but not registered"


def test_the_new_tools_validate_the_arguments_the_rules_send():
    """A fast-path rule that builds arguments the tool rejects is worse than no
    rule: the turn dies in validation instead of falling through."""
    from tool_registry import validate_args

    for phrase, tool in CASES:
        routed = fast_path(phrase)
        assert routed is not None
        spec = get_tool(routed[0])
        ok, _cleaned, error = validate_args(spec, routed[1])
        assert ok, f"{phrase!r} -> {routed[0]}({routed[1]}): {error}"


# ───────────────────── v13.7: the hand mouse is not an app ─────────────────────
#
# "can you open the hand tracking?" went to the app launcher, which searched the
# installed-app index for "hand tracking" and started GNU `tr` from Git's usr\bin -
# a program that then sat waiting on stdin forever with its console in front of
# everything. Two bugs met: the index matched `tr` as a substring of "tracking"
# (see test_app_index.py), and *this* - Dexter's own features were being looked for
# out on the disk. The hand mouse now has rules of its own, above the launcher.

HAND_CASES = [
    ("can you open the hand tracking?", "start_hand_mouse"),
    ("open the hand tracking", "start_hand_mouse"),
    ("show me the hand tracking", "start_hand_mouse"),
    ("open the hand mouse window", "start_hand_mouse"),
    ("start hand mouse", "start_hand_mouse"),
    ("turn on hand tracking", "start_hand_mouse"),
    ("use the gesture control", "start_hand_mouse"),
    ("stop hand tracking", "stop_hand_mouse"),
    ("turn off the hand mouse", "stop_hand_mouse"),
    ("close the hand tracker", "stop_hand_mouse"),
    ("is hand tracking on", "hand_mouse_status"),
    ("hand mouse status", "hand_mouse_status"),
    ("what gesture do you see", "hand_mouse_status"),
    ("what do the hand gestures do", "hand_gestures"),
    ("list the hand mouse gestures", "hand_gestures"),
    ("make a fist the right click", "set_hand_gesture"),
    ("set the open palm to scroll", "set_hand_gesture"),
    ("change the pinch to a double click", "set_hand_gesture"),
    ("make two fingers do nothing", "set_hand_gesture"),
    ("bind the index finger to move the pointer", "set_hand_gesture"),
    # v13.8: the pointer's speed, out loud - the one control you cannot reach with
    # the pointer when the pointer is the problem.
    ("make the hand mouse slower", "set_hand_speed"),
    ("the hand pointer is too fast", "set_hand_speed"),
    ("set the hand mouse speed to 1.5", "set_hand_speed"),
    ("make the hand cursor more precise", "set_hand_speed"),
    ("the hand mouse is jumpy", "set_hand_speed"),
]


@pytest.mark.parametrize("phrase,tool", HAND_CASES)
def test_the_hand_mouse_is_never_looked_for_on_the_disk(phrase, tool):
    result = fast_path(phrase)
    assert result is not None, f"{phrase!r} fell through to the app launcher"
    assert result[0] == tool, f"{phrase!r} -> {result[0]}, expected {tool}"


def test_too_fast_means_slower_and_too_slow_means_faster():
    """The complaint names the symptom; the fix is the opposite direction."""
    assert fast_path("the hand pointer is too fast") == ("set_hand_speed", {"speed": "slower"})
    assert fast_path("the hand mouse is sluggish") == ("set_hand_speed", {"speed": "faster"})
    assert fast_path("set the hand mouse speed to 1.5") == ("set_hand_speed", {"speed": "1.5"})


def test_the_gesture_it_names_is_the_gesture_it_sends():
    assert fast_path("make a fist the right click") == (
        "set_hand_gesture", {"pose": "fist", "action": "right-pinch"}
    )
    assert fast_path("set the open palm to scroll") == (
        "set_hand_gesture", {"pose": "open-palm", "action": "scroll"}
    )


@pytest.mark.parametrize("phrase", [
    # "make X the Y" is far too common a shape to claim on a partial match, so the
    # rule yields unless both halves name a real pose and a real action.
    "make the window bigger",
    "make a note",
    "set a reminder to call mum at 6",
    "change the fast model to qwen",
    "make the pop-up go away",
])
def test_the_gesture_rule_does_not_steal_other_sentences(phrase):
    routed = fast_path(phrase)
    assert routed is None or routed[0] != "set_hand_gesture", f"{phrase!r} -> {routed}"
