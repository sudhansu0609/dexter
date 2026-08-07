import os
import sys
import time
import threading
import webview

# Ensure backend directory is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def start_backend():
    import uvicorn
    from main import app
    uvicorn.run(app, host="127.0.0.1", port=8098, log_level="warning")

def launch_desktop():
    # 1. Start FastAPI backend in background thread
    t = threading.Thread(target=start_backend, daemon=True)
    t.start()
    time.sleep(1)

    # 2. Determine target URL / html file
    # We serve the frontend dist folder from main.py on port 8098
    # Using http:// instead of file:/// fixes persistent microphone permission prompts in WebView2
    dist_index = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "index.html")
    if os.path.exists(dist_index):
        target_url = "http://localhost:8098/"
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
