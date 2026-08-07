import requests
import json
import time

BASE_URL = "http://127.0.0.1:8098"

def run_tests():
    print("===================================================")
    print("Dexter Super Assistant - Post-Fix Verification Suite")
    print("===================================================")

    # 1. Status endpoint
    try:
        res = requests.get(f"{BASE_URL}/api/dexter/status", timeout=5)
        assert res.status_code == 200
        data = res.json()
        print("[OK] Test 1: Status endpoint online:", data["status"])
    except Exception as e:
        print("[FAIL] Test 1:", e)

    # 2. Day Planner
    try:
        res = requests.get(f"{BASE_URL}/api/dexter/planner", timeout=5)
        assert res.status_code == 200
        print("[OK] Test 2: Planner returned", len(res.json().get("tasks", [])), "tasks, date:", res.json().get("date"))
    except Exception as e:
        print("[FAIL] Test 2:", e)

    # 3. Add task
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/planner/add", json={"title": "Fix verification run", "time": "09:00 PM"}, timeout=5)
        assert res.status_code == 200
        print("[OK] Test 3: Added task:", res.json()["title"])
    except Exception as e:
        print("[FAIL] Test 3:", e)

    # 4. Toggle task
    try:
        planner = requests.get(f"{BASE_URL}/api/dexter/planner", timeout=5).json()
        if planner["tasks"]:
            tid = planner["tasks"][0]["id"]
            res = requests.post(f"{BASE_URL}/api/dexter/planner/toggle/{tid}", timeout=5)
            assert res.status_code == 200
            print("[OK] Test 4: Toggled task", tid, "->", res.json())
        else:
            print("[SKIP] Test 4: No tasks to toggle")
    except Exception as e:
        print("[FAIL] Test 4:", e)

    # 5. FIX VERIFY: "start my day" should hit planner NOT app launcher
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/chat", json={"message": "start my day plan"}, timeout=10)
        assert res.status_code == 200
        data = res.json()
        # Should NOT be launch_app
        assert data["action"] != "launch_app", f"REGRESSION: 'start my day' incorrectly routed to {data['action']}"
        print("[OK] Test 5: 'start my day plan' -> action:", data["action"], "(not launch_app)")
    except Exception as e:
        print("[FAIL] Test 5:", e)

    # 6. FIX VERIFY: "play lofi" should hit YouTube
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/chat", json={"message": "play lofi beats"}, timeout=10)
        assert res.status_code == 200
        data = res.json()
        assert data["action"] == "play_youtube"
        print("[OK] Test 6: YouTube intent correct:", data["action"])
    except Exception as e:
        print("[FAIL] Test 6:", e)

    # 7. FIX VERIFY: "go to google.com" should hit browse
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/chat", json={"message": "go to google.com"}, timeout=10)
        assert res.status_code == 200
        data = res.json()
        assert data["action"] == "browse_url"
        print("[OK] Test 7: Browse intent correct:", data["action"], "url:", data["details"]["url"])
    except Exception as e:
        print("[FAIL] Test 7:", e)

    # 8. FIX VERIFY: "open notepad" should hit app launcher
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/chat", json={"message": "open notepad"}, timeout=10)
        assert res.status_code == 200
        data = res.json()
        assert data["action"] == "launch_app"
        print("[OK] Test 8: App launcher correct:", data["action"])
    except Exception as e:
        print("[FAIL] Test 8:", e)

    # 9. SpilledCoffeeAI curate
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/spilledcoffee/curate", json={"niche": "AI", "count": 2}, timeout=10)
        assert res.status_code == 200
        print("[OK] Test 9: Curated", res.json()["count"], "items")
    except Exception as e:
        print("[FAIL] Test 9:", e)

    # 10. Skills endpoint
    try:
        res = requests.get(f"{BASE_URL}/api/dexter/skills", timeout=5)
        assert res.status_code == 200
        print("[OK] Test 10: Skills count:", len(res.json()))
    except Exception as e:
        print("[FAIL] Test 10:", e)

    # 11. Browse endpoint directly
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/browser/navigate", json={"url": "example.com"}, timeout=5)
        assert res.status_code == 200
        print("[OK] Test 11: Direct browse endpoint:", res.json()["url"])
    except Exception as e:
        print("[FAIL] Test 11:", e)

    # 12. Hybrid LLM fallback
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/chat", json={"message": "What is machine learning?"}, timeout=15)
        assert res.status_code == 200
        data = res.json()
        assert data["action"] == "chat_response"
        print("[OK] Test 12: LLM fallback correct, reply length:", len(data["reply"]))
    except Exception as e:
        print("[FAIL] Test 12:", e)

    # 13. Desktop File System Search
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/filesystem/search", json={"query": "dexter"}, timeout=10)
        assert res.status_code == 200
        data = res.json()
        print("[OK] Test 13: FileSystem search returned", data["count"], "results for query 'dexter'.")
    except Exception as e:
        print("[FAIL] Test 13:", e)

    print("===================================================")
    print("All 12 Post-Fix Verification Tests Executed!")
    print("===================================================")

if __name__ == "__main__":
    run_tests()
