import os
import sys
import time
import threading
import webview

# Ensure backend directory is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import urllib.request

def enable_webview2_mic():
    """
    Grant microphone access to the WebView2 (edgechromium) desktop shell.

    pywebview registers a media-permission handler only in its Qt backend, not in
    the WebView2 backend this app runs on, so getUserMedia is never granted and
    the voice mic stays dead (the pipeline then falls back to a browser recognizer
    that WebView2 also lacks). We wrap pywebview's own CoreWebView2-ready hook to
    auto-allow microphone permission requests. Fully guarded: any failure here
    just leaves the shell as-is and never blocks startup, and it no-ops on
    non-WebView2 platforms.
    """
    try:
        from webview.platforms import edgechromium as ec
        from Microsoft.Web.WebView2.Core import (
            CoreWebView2PermissionKind,
            CoreWebView2PermissionState,
        )
    except Exception as exc:
        print(f"[Desktop] Mic-permission patch unavailable (non-WebView2?): {exc}")
        return

    original_ready = ec.EdgeChrome.on_webview_ready

    def patched_ready(self, sender, args):
        original_ready(self, sender, args)
        try:
            core = self.webview.CoreWebView2

            def on_permission(_s, evt):
                if evt.PermissionKind == CoreWebView2PermissionKind.Microphone:
                    evt.State = CoreWebView2PermissionState.Allow

            core.PermissionRequested += on_permission
            print("[Desktop] WebView2 microphone permission handler attached.")
        except Exception as exc:
            print(f"[Desktop] Could not attach mic-permission handler: {exc}")

    ec.EdgeChrome.on_webview_ready = patched_ready


def start_backend():
    import uvicorn
    from main import app
    uvicorn.run(app, host="127.0.0.1", port=8098, log_level="warning")

def wait_for_server(url, timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as res:
                if res.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False

def launch_desktop():
    # 0. Let the WebView2 shell grant microphone access (voice input needs it).
    enable_webview2_mic()

    # 1. Start FastAPI backend in background thread
    t = threading.Thread(target=start_backend, daemon=True)
    t.start()

    # 2. Determine target URL / html file
    dist_index = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "index.html")
    if os.path.exists(dist_index):
        target_url = "http://127.0.0.1:8098/"
        # Wait for FastAPI server to become ready
        wait_for_server(target_url, timeout=10)
    else:
        target_url = "http://localhost:3006"

    # 3. Create Native Windows Desktop WebView Window
    window = webview.create_window(
        title="Dexter AI Super Assistant",
        url=target_url,
        width=460,
        height=680,
        resizable=True,
        frameless=False,
        easy_drag=True,
        on_top=False,
        background_color="#090d16"
    )

    webview.start(debug=False)

if __name__ == "__main__":
    launch_desktop()
