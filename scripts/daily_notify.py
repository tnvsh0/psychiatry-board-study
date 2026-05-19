#!/usr/bin/env python3
"""
daily_notify.py — sticky daily reminder via ntfy.

Rule: ONE ntfy per day-becoming-current. If user hasn't marked it complete,
silent until they do. (No daily spam.)

Run via cron daily at 06:00 UTC (09:00 Israel):
    0 6 * * * /opt/psychiatry-board-study/scripts/daily_notify.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SCHED = ROOT / "schedule" / "schedule.json"
PROG = ROOT / "schedule" / "progress.json"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "psychiatry-study-tnvsh")
COURSE_URL = os.environ.get(
    "COURSE_URL",
    "https://psychiatry-board-study.run.app",  # Cloud Run URL once deployed
)
GITHUB_REPO = "tnvsh0/psychiatry-board-study"


def load_progress() -> dict:
    if PROG.exists():
        return json.loads(PROG.read_text(encoding="utf-8"))
    return {"completed_days": [], "current_day_index": 1, "last_notified": None}


def save_progress(p: dict):
    PROG.parent.mkdir(parents=True, exist_ok=True)
    PROG.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def release_exists(day_index: int, schedule: dict, gh_token: str) -> bool:
    """Check if this day's GitHub Release exists yet (i.e. content is generated)."""
    import re
    day = next((d for d in schedule["days"] if d["index"] == day_index), None)
    if not day:
        return False
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", day.get("title_en", "")).strip("-").lower()[:60]
    tag = f"study-{day_index:03d}-{slug}"
    headers = {"Accept": "application/vnd.github+json"}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}",
        headers=headers, timeout=15,
    )
    return r.status_code == 200


def send_ntfy(day: dict, day_index: int):
    title_he = day.get("title_he", day.get("title_en", ""))
    phase = day.get("phase", 0)
    course_link = f"{COURSE_URL}/?day={day_index}"

    payload = {
        "topic": NTFY_TOPIC,
        "title": f"📚 יום {day_index}: {title_he}",
        "message": f"השלב {phase} • {len(day.get('aspects', []))} היבטים. החומר מוכן.",
        "priority": 3,
        "tags": ["books"],
        "actions": [
            {"action": "view", "label": "פתח בקורס", "url": course_link},
        ],
    }
    try:
        r = requests.post("https://ntfy.sh", json=payload, timeout=15)
        print(f"ntfy sent (status {r.status_code}): day {day_index}")
        return r.status_code == 200
    except Exception as e:
        print(f"ntfy failed: {e}")
        return False


def main():
    # Pull latest (so we see any Mark Done commits from the Streamlit UI)
    import subprocess
    try:
        subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only"],
                       capture_output=True, timeout=30)
    except Exception:
        pass

    if not SCHED.exists():
        print(f"schedule.json not found at {SCHED}")
        sys.exit(1)

    schedule = json.loads(SCHED.read_text(encoding="utf-8"))
    progress = load_progress()
    gh_token = os.environ.get("GH_TOKEN", "")

    current = progress.get("current_day_index", 1)
    completed = set(progress.get("completed_days", []))

    # Skip completed days
    while current in completed:
        current += 1
        if current > len(schedule["days"]):
            print("Course complete!")
            return

    # Check if this day's content is ready (Release exists)
    if not release_exists(current, schedule, gh_token):
        print(f"Day {current} content not generated yet — staying silent.")
        return

    # Check if we already notified for this day
    last_notified = progress.get("last_notified_day")
    if last_notified == current:
        print(f"Day {current} already notified — skipping (sticky rule).")
        return

    # Find the day, send ntfy
    day = next((d for d in schedule["days"] if d["index"] == current), None)
    if not day:
        print(f"Day {current} not in schedule")
        return

    if send_ntfy(day, current):
        progress["last_notified_day"] = current
        progress["last_notified_at"] = datetime.now(timezone.utc).isoformat()
        progress["current_day_index"] = current
        save_progress(progress)


if __name__ == "__main__":
    main()
