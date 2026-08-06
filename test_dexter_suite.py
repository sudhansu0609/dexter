import requests
import json
import time

BASE_URL = "http://127.0.0.1:8098"

def run_tests():
    print("===================================================")
    print("Running Dexter Super Assistant End-to-End Test Suite")
    print("===================================================")

    # 1. Test Status Endpoint
    try:
        res = requests.get(f"{BASE_URL}/api/dexter/status", timeout=5)
        assert res.status_code == 200
        data = res.json()
        print("[OK] Test 1 Passed: /api/dexter/status returned:", data["status"], "| LLMs:", data["providers"])
    except Exception as e:
        print("[FAIL] Test 1 Failed:", e)

    # 2. Test Day Planner Agenda
    try:
        res = requests.get(f"{BASE_URL}/api/dexter/planner", timeout=5)
        assert res.status_code == 200
        planner_data = res.json()
        print("[OK] Test 2 Passed: /api/dexter/planner returned", len(planner_data.get("tasks", [])), "tasks.")
    except Exception as e:
        print("[FAIL] Test 2 Failed:", e)

    # 3. Test Add Day Planner Task
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/planner/add", json={"title": "Test AI Automation Run", "time": "03:00 PM"}, timeout=5)
        assert res.status_code == 200
        print("[OK] Test 3 Passed: Added task:", res.json()["title"])
    except Exception as e:
        print("[FAIL] Test 3 Failed:", e)

    # 4. Test SpilledCoffeeAI Overnight Video Curation
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/spilledcoffee/curate", json={"niche": "AI Tech Trends", "count": 2}, timeout=10)
        assert res.status_code == 200
        curate_data = res.json()
        print("[OK] Test 4 Passed: SpilledCoffeeAI Curated", curate_data["count"], "viral titles & scripts!")
        print("   Sample Title:", curate_data["items"][0]["title"])
    except Exception as e:
        print("[FAIL] Test 4 Failed:", e)

    # 5. Test Learned Skills Endpoint
    try:
        res = requests.get(f"{BASE_URL}/api/dexter/skills", timeout=5)
        assert res.status_code == 200
        skills_data = res.json()
        print("[OK] Test 5 Passed: Dexter Skills Engine returned", len(skills_data), "skills.")
    except Exception as e:
        print("[FAIL] Test 5 Failed:", e)

    # 6. Test App Launcher Intent
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/chat", json={"message": "open notepad app"}, timeout=10)
        assert res.status_code == 200
        chat_data = res.json()
        print("[OK] Test 6 Passed: App Launcher Intent Action:", chat_data["action"], "| Reply:", chat_data["reply"])
    except Exception as e:
        print("[FAIL] Test 6 Failed:", e)

    # 7. Test YouTube Intent
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/chat", json={"message": "play lofi beats on youtube"}, timeout=10)
        assert res.status_code == 200
        yt_data = res.json()
        print("[OK] Test 7 Passed: YouTube Play Action:", yt_data["action"], "| Details:", yt_data["details"]["status"])
    except Exception as e:
        print("[FAIL] Test 7 Failed:", e)

    # 8. Test Hybrid LLM Chat Generation
    try:
        res = requests.post(f"{BASE_URL}/api/dexter/chat", json={"message": "Hello Dexter, tell me your core mission."}, timeout=15)
        assert res.status_code == 200
        llm_data = res.json()
        print("[OK] Test 8 Passed: Hybrid LLM Reasoning Reply:", llm_data["reply"][:90], "...")
    except Exception as e:
        print("[FAIL] Test 8 Failed:", e)

    print("===================================================")
    print("All 8 End-to-End Verification Tests Executed!")
    print("===================================================")

if __name__ == "__main__":
    run_tests()
