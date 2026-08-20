#!/usr/bin/env python3
"""Produce every chapter, in study order, cleaning up as it goes.

Design notes that are load-bearing:

STUDY ORDER, NOT TRACK ORDER. The owner studies chapter 1 forward and is
already behind the calendar, so the queue is the schedule's own order: the
material always arrives ahead of where they are, and a run cut short leaves a
contiguous block of finished chapters rather than a scattering.

CLEAN UP AFTER EACH CHAPTER. The old pipeline wrote every artifact to
_generated/day-NNN/ and never deleted anything. Three chapters filled 670MB and
the shared VM disk hit 98% mid-run. Once a chapter's release exists on GitHub,
the local copy is a duplicate — so it goes, and the run needs room for one
chapter at a time instead of 141.

PACE THE GENERATIONS. The podcast project measured NotebookLM failing silently
at roughly 19-21 back-to-back generations: the call returns, the artifact never
appears, and the gap is only discovered later. We produce ~7 per chapter, so
without pacing the wall is hit on chapter three.

RESUMABLE. State is a JSON file keyed by chapter, so a usage limit, a dropped
connection or a reboot costs the current chapter and nothing else.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "_bulk" / "state.json"
GENERATED = ROOT / "_generated"

# Seconds between chapters. The generations inside one chapter are already
# spaced by daily_study; this is the gap between chapters, which is what keeps
# the hourly generation count under the ceiling the podcast project hit.
CHAPTER_GAP_S = 45


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"done": [], "failed": {}, "started_at": None}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def released(tag: str) -> bool:
    r = subprocess.run(["gh", "release", "view", tag, "--json", "tagName"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    return r.returncode == 0


def sweep_local(day_index: int) -> int:
    """Delete this chapter's local artifacts once its release exists."""
    d = GENERATED / f"day-{day_index:03d}"
    if not d.exists():
        return 0
    mb = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024)
    shutil.rmtree(d, ignore_errors=True)
    return int(mb)


def run_chapter(day_index: int, log_dir: Path) -> tuple[bool, str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"ch{day_index:03d}.log"
    started = time.time()
    with log.open("w", encoding="utf-8") as fh:
        p = subprocess.run(
            [sys.executable, "-u", str(ROOT / "scripts" / "daily_study.py"),
             "--day", str(day_index)],
            stdout=fh, stderr=subprocess.STDOUT, cwd=str(ROOT),
        )
    mins = (time.time() - started) / 60
    tail = log.read_text(encoding="utf-8", errors="replace")[-400:]
    return p.returncode == 0, f"{mins:.1f}min rc={p.returncode} :: {tail[-160:].strip()}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=144)
    ap.add_argument("--limit", type=int, help="stop after N chapters this session")
    ap.add_argument("--retry-failed", action="store_true",
                    help="second-chance pass over chapters that failed earlier")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    sched = json.loads((ROOT / "schedule" / "schedule.json").read_text(encoding="utf-8"))
    days = {d["index"]: d for d in sched["days"]}
    st = load_state()
    st["started_at"] = st.get("started_at") or datetime.now(timezone.utc).isoformat()

    queue = [i for i in range(a.start, a.end + 1) if i in days]
    if a.retry_failed:
        queue = [i for i in queue if str(i) in st["failed"]]
    else:
        queue = [i for i in queue if i not in st["done"]]

    print(f"queue: {len(queue)} chapter(s){' (retry pass)' if a.retry_failed else ''}")
    if a.dry_run:
        for i in queue[:20]:
            print(f"  {i:3} {days[i]['title_he']}")
        return 0

    n = 0
    for idx in queue:
        if a.limit and n >= a.limit:
            print(f"stopping: reached --limit {a.limit}")
            break
        day = days[idx]
        tag = f"study-{idx:03d}-" + "".join(
            c if c.isalnum() else "-" for c in day.get("title_en", "").lower()
        ).strip("-")[:60]

        if released(tag):
            print(f"[{idx:3}] already released — sweeping local copy")
            freed = sweep_local(idx)
            if idx not in st["done"]:
                st["done"].append(idx)
            st["failed"].pop(str(idx), None)
            save_state(st)
            continue

        print(f"[{idx:3}] {day['title_he'][:40]} — generating...", flush=True)
        ok, note = run_chapter(idx, ROOT / "_bulk" / "logs")
        if ok and released(tag):
            freed = sweep_local(idx)
            st["done"].append(idx)
            st["failed"].pop(str(idx), None)
            print(f"[{idx:3}] DONE  {note.split('::')[0].strip()} — freed {freed}MB")
        else:
            # Keep the local artifacts of a failed chapter: they are the only
            # evidence of what went wrong, and sweeping them would erase it.
            st["failed"][str(idx)] = note
            print(f"[{idx:3}] FAIL  {note}")
        save_state(st)
        n += 1
        time.sleep(CHAPTER_GAP_S)

    print(f"\ndone={len(st['done'])}  failed={len(st['failed'])}")
    if st["failed"]:
        print("failed chapters:", ", ".join(sorted(st["failed"], key=int)))
        print("re-run them with:  python scripts/bulk_run.py --retry-failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
