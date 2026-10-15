import pytest
import router


def test_open_tab_in_desktop_app():
    session = "test_desktop_tab"
    router.forget_focus(session)
    router.remember_app_focus(session, "notepad")

    for phrase in [
        "open a tab in it",
        "open a new tab in it",
        "open new tab in it",
        "new tab in it",
        "in it open a tab",
        "in it, open a new tab",
        "in this app open a tab",
        "open a tab in this app",
        "open a tab in the window",
    ]:
        hit = router.fast_path(phrase, session=session)
        assert hit is not None, f"Failed on: {phrase}"
        tool, args = hit
        assert tool == "app_shortcut", f"Expected app_shortcut for {phrase}, got {tool}"
        assert args.get("action") == "new tab"
        assert args.get("window") == "notepad"


def test_open_tab_in_browser():
    session = "test_browser_tab"
    router.forget_focus(session)
    router.remember_app_focus(session, "chrome")

    for phrase in [
        "open a tab in it",
        "open a new tab in it",
        "new tab in it",
        "in it open a tab",
    ]:
        hit = router.fast_path(phrase, session=session)
        assert hit == ("browser_new_tab", {}), f"Expected browser_new_tab for {phrase}, got {hit}"


def test_go_to_specific_tab_in_desktop_app():
    session = "test_specific_desktop_tab"
    router.forget_focus(session)
    router.remember_app_focus(session, "buzzcafai")

    hit = router.fast_path("go to the projects tab in it", session=session)
    assert hit == ("click_in_app", {"text": "projects", "app": "buzzcafai"})

    hit = router.fast_path("switch to settings tab in it", session=session)
    assert hit == ("click_in_app", {"text": "settings", "app": "buzzcafai"})

    hit = router.fast_path("open assets tab in it", session=session)
    assert hit == ("click_in_app", {"text": "assets", "app": "buzzcafai"})

    hit = router.fast_path("in it, go to the timeline tab", session=session)
    assert hit == ("click_in_app", {"text": "timeline", "app": "buzzcafai"})

    # Ordinals / relative navigation
    hit = router.fast_path("go to the next tab in it", session=session)
    assert hit == ("app_shortcut", {"action": "next tab", "window": "buzzcafai"})


def test_go_to_specific_tab_in_browser():
    session = "test_specific_browser_tab"
    router.forget_focus(session)
    router.remember_app_focus(session, "chrome")

    hit = router.fast_path("go to the youtube tab in it", session=session)
    assert hit == ("browser_switch_tab", {"which": "youtube"})

    hit = router.fast_path("switch to gmail tab in it", session=session)
    assert hit == ("browser_switch_tab", {"which": "gmail"})


def test_tab_in_it_never_routes_to_browser_open_search():
    """Verify that commands referring to tabs in apps/windows never trigger browser_open search."""
    for session in ["empty_sess", "desktop_sess"]:
        if session == "desktop_sess":
            router.remember_app_focus(session, "buzzcafai")
        else:
            router.forget_focus(session)

        for phrase in [
            "go to the projects tab in it",
            "go to projects tab in it",
            "go to tab in it",
            "go to settings tab in it",
            "open a tab in it",
        ]:
            hit = router.fast_path(phrase, session=session)
            if hit is not None:
                tool, _ = hit
                assert tool != "browser_open", f"Phrase '{phrase}' incorrectly routed to browser_open"


def test_list_tabs_in_it():
    session_desk = "test_list_desk"
    router.forget_focus(session_desk)
    router.remember_app_focus(session_desk, "buzzcafai")

    for phrase in [
        "what tabs are in it",
        "what tabs does it have",
        "list tabs in it",
        "show tabs in it",
        "in it, what tabs are there",
    ]:
        hit = router.fast_path(phrase, session=session_desk)
        assert hit == ("list_controls", {"window": "buzzcafai", "kind": "tab"}), f"Failed on: {phrase}"

    session_brow = "test_list_brow"
    router.forget_focus(session_brow)
    router.remember_app_focus(session_brow, "chrome")

    for phrase in [
        "what tabs are in it",
        "what tabs does it have",
        "list tabs in it",
    ]:
        hit = router.fast_path(phrase, session=session_brow)
        assert hit == ("browser_tabs", {}), f"Failed on: {phrase}"


def test_split_app_prefix_pronoun():
    session = "test_pronoun_prefix"
    router.forget_focus(session)
    router.remember_app_focus(session, "buzzcafai")

    assert router.split_app_prefix("in it, click settings", session=session) == ("buzzcafai", "click settings")
    assert router.split_app_prefix("in this app, find audio bitrate", session=session) == ("buzzcafai", "find audio bitrate")
    assert router.split_app_prefix("in that window: save all", session=session) == ("buzzcafai", "save all")


def test_split_tab_prefix():
    session = "test_tab_prefix"
    router.forget_focus(session)
    router.remember_app_focus(session, "buzzcafai")
    router.remember_tab_focus(session, "projects", app_name="buzzcafai", kind="desktop")

    kind, target, rest = router.split_tab_prefix("in this tab, click new project", session=session)
    assert kind == "desktop"
    assert target == "buzzcafai"
    assert rest == "click new project"


def test_focus_context_note_and_sequence():
    session = "test_seq"
    router.forget_focus(session)

    # 1. Launch desktop app
    router.note_focus(session, "launch_app", {"app_name": "buzzcafai"}, True)
    assert router.recall_focused_app(session) == "buzzcafai"

    # 2. Click tab inside app
    router.note_focus(session, "click_in_app", {"text": "Assets", "app": "buzzcafai"}, True)
    assert router.recall_focused_app(session) == "buzzcafai"
    tab_info = router.recall_focused_tab(session)
    assert tab_info is not None
    assert tab_info["name"] == "Assets"
    assert tab_info["app"] == "buzzcafai"

    # 3. Context note mentions both
    note = router.focus_context_note(session)
    assert "buzzcafai" in note
    assert "Assets" in note


def test_mission_reminder_tab_routing():
    session = "test_mr_routing"
    router.forget_focus(session)

    assert router.fast_path("open goals tab in mission reminder", session=session) == (
        "mission_go_to_tab",
        {"tab": "goals"},
    )
    assert router.fast_path("go to goals tab in mission reminder", session=session) == (
        "mission_go_to_tab",
        {"tab": "goals"},
    )
    assert router.fast_path("open goals tab in machine reminder", session=session) == (
        "mission_go_to_tab",
        {"tab": "goals"},
    )
    assert router.fast_path("in mission reminder, open goals tab", session=session) == (
        "mission_go_to_tab",
        {"tab": "goals"},
    )
    assert router.fast_path("switch to review tab in mission reminder", session=session) == (
        "mission_go_to_tab",
        {"tab": "review"},
    )
    assert router.fast_path("open blocks tab in mission reminder", session=session) == (
        "mission_go_to_tab",
        {"tab": "blocks"},
    )

    # When Mission Reminder is already focused, bare "open goals tab" works
    router.remember_app_focus(session, "mission reminder")
    assert router.fast_path("open goals tab", session=session) == (
        "mission_go_to_tab",
        {"tab": "goals"},
    )
    assert router.fast_path("switch to forest tab", session=session) == (
        "mission_go_to_tab",
        {"tab": "forest"},
    )


def test_app_specific_named_tab_routing():
    session = "test_named_app_tabs"
    router.forget_focus(session)

    assert router.fast_path("go to projects tab in buzzcafai", session=session) == (
        "click_in_app",
        {"text": "projects", "app": "buzzcafai"},
    )
    assert router.fast_path("open settings tab in notepad", session=session) == (
        "click_in_app",
        {"text": "settings", "app": "notepad"},
    )


def test_click_in_app_delegates_to_mission_bridge(monkeypatch):
    from tools_desktop import click_in_app
    import mission_bridge

    called_tab = None

    def fake_go_to(tab):
        nonlocal called_tab
        called_tab = tab
        return {"tab": tab, "shortcut": "ctrl+3", "window": "Mission Reminder"}

    monkeypatch.setattr(mission_bridge, "go_to", fake_go_to)

    result = click_in_app(text="Goals", app="Mission Reminder")
    assert result.ok
    assert called_tab == "goals"
    assert "Goals" in result.summary
