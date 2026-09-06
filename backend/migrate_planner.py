"""
One-time migration (roadmap v5, 5.2): open day_planner.json tasks become
workbook commitments, then the planner file is renamed so it is never read
again. Safe to run twice.

    python backend/migrate_planner.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import env_boot  # noqa: E402,F401

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PLANNER_FILE = os.path.join(DATA_DIR, "day_planner.json")
# Demo rows the old planner seeded on first run; never the owner's own tasks.
SEED_TITLES = {
    "Morning Routine & Coffee", "Review SpilledCoffeeAI overnight videos",
    "Content Strategy & Scripting Session", "Evening Walk & Music Playlist",
}


def migrate() -> int:
    if not os.path.exists(PLANNER_FILE):
        return 0
    try:
        with open(PLANNER_FILE, "r", encoding="utf-8") as handle:
            planner = json.load(handle) or {}
    except Exception as exc:
        print(f"[Migrate] Could not read {PLANNER_FILE}: {exc}")
        return 0

    from workbook import workbook

    moved = 0
    skipped_seed = 0
    date_str = planner.get("date")
    for task in planner.get("tasks", []):
        if task.get("completed"):
            continue
        title = (task.get("title") or "").strip()
        if not title or title in SEED_TITLES:
            skipped_seed += title in SEED_TITLES
            continue
        due_time = None
        stamp = task.get("at")
        if stamp:
            try:
                from datetime import datetime

                due_time = datetime.fromisoformat(stamp).strftime("%H:%M")
            except Exception:
                due_time = None
        if workbook.find_commitment(title, status="open"):
            continue
        workbook.add_commitment(text=title, due_date=date_str or None, due_time=due_time)
        moved += 1

    os.replace(PLANNER_FILE, PLANNER_FILE.replace(".json", ".migrated.json"))
    print(f"[Migrate] Moved {moved} open planner task(s) into the workbook, dropped {skipped_seed} demo seed(s); planner file retired.")
    return moved


if __name__ == "__main__":
    migrate()
