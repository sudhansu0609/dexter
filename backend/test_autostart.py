import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desktop_app


def test_get_start_mode_defaults_to_popup(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["desktop_app.py"])
    monkeypatch.delenv("DEXTER_START_MODE", raising=False)
    assert desktop_app.get_start_mode() == "popup"


def test_get_start_mode_tray_cli_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["desktop_app.py", "--tray"])
    monkeypatch.delenv("DEXTER_START_MODE", raising=False)
    assert desktop_app.get_start_mode() == "tray"


def test_get_start_mode_hidden_cli_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["desktop_app.py", "--hidden"])
    monkeypatch.delenv("DEXTER_START_MODE", raising=False)
    assert desktop_app.get_start_mode() == "tray"


def test_get_start_mode_full_cli_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["desktop_app.py", "--full"])
    assert desktop_app.get_start_mode() == "full"


def test_get_start_mode_env_var(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["desktop_app.py"])
    monkeypatch.setenv("DEXTER_START_MODE", "tray")
    assert desktop_app.get_start_mode() == "tray"


def test_autostart_registry_toggle(monkeypatch):
    """Test setting and unsetting autostart via Windows registry."""
    if sys.platform != "win32":
        pytest.skip("Windows only")

    # Verify initial state can be read without error
    initial = desktop_app.is_autostart_enabled()

    try:
        # Enable autostart
        assert desktop_app.set_autostart(True) is True
        assert desktop_app.is_autostart_enabled() is True

        # Disable autostart
        assert desktop_app.set_autostart(False) is True
        assert desktop_app.is_autostart_enabled() is False
    finally:
        # Restore initial state
        desktop_app.set_autostart(initial)


def test_autostart_prefers_dexter_exe(monkeypatch):
    """Verify that when Dexter.exe exists, it is preferred in registry command."""
    if sys.platform != "win32":
        pytest.skip("Windows only")

    import winreg
    initial = desktop_app.is_autostart_enabled()
    try:
        assert desktop_app.set_autostart(True) is True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, desktop_app.RUN_REG_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, desktop_app.AUTOSTART_NAME)
            exe_path = os.path.join(desktop_app.ROOT_DIR, "Dexter.exe")
            if os.path.exists(exe_path):
                assert "Dexter.exe" in val
                assert "--tray" in val
    finally:
        desktop_app.set_autostart(initial)


def test_single_instance_mutex(monkeypatch):
    """Test single-instance detection."""
    if sys.platform != "win32":
        pytest.skip("Windows only")

    # Initial check should succeed (primary instance)
    desktop_app._instance_mutex = None
    assert desktop_app._is_already_running() is False

    # Second check while mutex is held should detect existing instance
    assert desktop_app._is_already_running() is True


def test_main_exits_early_if_already_running(monkeypatch):
    """Verify that launching another instance exits early with 0 and signals existing window."""
    monkeypatch.setattr(sys, "argv", ["desktop_app.py"])
    monkeypatch.setattr(desktop_app, "_is_already_running", lambda: True)

    activated = []
    monkeypatch.setattr(desktop_app, "_activate_existing_instance", lambda: activated.append(True))

    exit_code = desktop_app.main()
    assert exit_code == 0
    assert activated == [True]


