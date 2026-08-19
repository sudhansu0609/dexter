"""
Native Windows notifications.

Reminders reach the user on three channels: spoken (frontend TTS), an in-app
banner, and this - a real Action Center toast. The toast is the only one that
still works when the Dexter window is closed, which is exactly when a reminder
matters most.

winotify is optional; without it the other two channels still fire.
"""

import os
import subprocess
import threading
from typing import Optional

APP_ID = "Dexter Assistant"
ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "public", "icon.png"
)

try:
    from winotify import Notification, audio  # type: ignore

    _HAS_WINOTIFY = True
except ImportError:
    Notification = None  # type: ignore
    audio = None  # type: ignore
    _HAS_WINOTIFY = False


def _powershell_toast(title: str, message: str) -> bool:
    """
    Fallback via the WinRT toast API through PowerShell.

    Slower than winotify (spawns a shell) but needs no third-party package, so
    reminders still surface on a bare install.
    """
    safe_title = title.replace("'", "''")
    safe_message = message.replace("'", "''")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n = $t.GetElementsByTagName('text');"
        f"$n.Item(0).AppendChild($t.CreateTextNode('{safe_title}')) > $null;"
        f"$n.Item(1).AppendChild($t.CreateTextNode('{safe_message}')) > $null;"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($t);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'Microsoft.WindowsShell').Show($toast);"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=10,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as exc:
        print(f"[Notify] PowerShell toast failed: {exc}")
        return False


def toast(title: str, message: str, urgent: bool = False) -> bool:
    """Show a Windows toast. Returns False if no channel was available."""
    if _HAS_WINOTIFY:
        try:
            notification = Notification(
                app_id=APP_ID,
                title=title,
                msg=message,
                icon=ICON_PATH if os.path.exists(ICON_PATH) else "",
            )
            notification.set_audio(
                audio.LoopingAlarm2 if urgent else audio.Default, loop=False
            )
            notification.show()
            return True
        except Exception as exc:
            print(f"[Notify] winotify failed ({exc}); falling back to PowerShell.")

    return _powershell_toast(title, message)


def toast_async(title: str, message: str, urgent: bool = False) -> None:
    """Fire and forget - never block the scheduler thread on a UI call."""
    threading.Thread(
        target=toast, args=(title, message, urgent), daemon=True
    ).start()


def available() -> str:
    return "winotify" if _HAS_WINOTIFY else "powershell"
