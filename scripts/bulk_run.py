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
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daily_study import slugify  # noqa: E402  one source of truth for tags

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "_bulk" / "state.json"
GENERATED = ROOT / "_generated"

# The same profile daily_study.py will use, so the preflight check and the work
# cannot disagree about which account is being tested.
PROFILE = os.environ.get("NOTEBOOKLM_PROFILE", "").strip()

# Must be the same repo daily_study.py publishes to.
GH_REPO = os.environ.get("GH_REPO", "tnvsh0/psychiatry-board-study")

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
    """Does this chapter's release exist? Two things ride on the answer.

    A false negative marks a chapter that actually succeeded as failed AND
    skips its sweep, so the disk keeps filling. Without --repo, gh resolves the
    repository from the working directory, while daily_study.py publishes to
    GH_REPO - so an override, or a run started from elsewhere, would have the
    two halves asking about different repositories and never agreeing.
    """
    r = subprocess.run(["gh", "release", "view", tag, "--repo", GH_REPO,
                        "--json", "tagName"],
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


# A failure that is really "the session died" looks exactly like a chapter
# failure from out here, and there are 141 chapters to fail.
AUTH_MARKERS = ("Authentication expired or invalid",
                "Run 'notebooklm login'",
                "accounts.google.com")
MAX_CONSECUTIVE_FAILURES = 3


def auth_ok() -> tuple[bool, str]:
    """One cheap read before committing to hours of work.

    Google revoked this project's sessions mid-session once, server-side, while
    the stored cookies were still valid locally for another 399 days - so
    checking the cookie file proves nothing and only a real call does. Without
    this the runner would spend two hours marking all 141 chapters failed at
    45-second intervals and leave a retry list with no information in it.
    """
    p = subprocess.run(["notebooklm"] + (["-p", PROFILE] if PROFILE else []) + ["list"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode == 0 and not any(m in out for m in AUTH_MARKERS):
        return True, ""
    if any(m in out for m in AUTH_MARKERS):
        return False, ("NotebookLM rejected the stored session. Re-authenticate:\n"
                       f"    notebooklm{' -p ' + PROFILE if PROFILE else ''} login --fresh")
    return False, f"notebooklm list failed rc={p.returncode}: {out.strip()[:200]}"


def run_chapter(day_index: int, log_dir: Path) -> tuple[bool, str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"ch{day_index:03d}.log"
    started = time.time()
    # The child writes Hebrew chapter titles into this log. On Windows a
    # redirected stdout gets the locale codec (cp1255), which cannot encode
    # them, and the chapter dies on UnicodeEncodeError partway through. Each
    # script also reconfigures its own streams; this covers anything they call.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8:replace"
    with log.open("w", encoding="utf-8") as fh:
        p = subprocess.run(
            [sys.executable, "-u", str(ROOT / "scripts" / "daily_study.py"),
             "--day", str(day_index)],
            stdout=fh, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env,
        )
    mins = (time.time() - started) / 60
    body = log.read_text(encoding="utf-8", errors="replace")
    tail = body[-400:]
    note = f"{mins:.1f}min rc={p.returncode} :: {tail[-160:].strip()}"
    if p.returncode != 0 and any(m in body for m in AUTH_MARKERS):
        note = "AUTH :: " + note
    return p.returncode == 0, note


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

    ok, why = auth_ok()
    if not ok:
        print(f"\n[STOP] {why}", file=sys.stderr)
        return 2
    print("auth OK\n")

    n, consecutive = 0, 0
    for idx in queue:
        if a.limit and n >= a.limit:
            print(f"stopping: reached --limit {a.limit}")
            break
        day = days[idx]
        # Import the real one rather than reimplementing it. These two had
        # drifted apart and disagreed on 92 of the 144 chapters: slugify
        # collapses a run of non-alphanumerics to a single dash, the copy here
        # emitted one dash per character, so "GABA / glutamate balance" became
        # study-007-gaba---glutamate-balance and released() looked up a tag
        # that does not exist. Every affected chapter would have been marked
        # FAILED after succeeding, and never swept.
        tag = f"study-{idx:03d}-{slugify(day.get('title_en', ''))}"

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
            consecutive = 0
            print(f"[{idx:3}] DONE  {note.split('::')[0].strip()} — freed {freed}MB")
        else:
            # Keep the local artifacts of a failed chapter: they are the only
            # evidence of what went wrong, and sweeping them would erase it.
            st["failed"][str(idx)] = note
            consecutive += 1
            print(f"[{idx:3}] FAIL  {note}")
        save_state(st)
        n += 1

        # A revoked session, an exhausted quota or a broken build fails every
        # chapter identically. Marking 141 of them failed at 45s apart destroys
        # the retry list and teaches nobody anything, so stop and say why.
        if note.startswith("AUTH ::"):
            print(f"\n[STOP] chapter {idx} failed on authentication. "
                  f"Re-authenticate and resume:\n"
                  f"    notebooklm{' -p ' + PROFILE if PROFILE else ''} login --fresh\n"
                  f"    python scripts/bulk_run.py", file=sys.stderr)
            return 2
        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            print(f"\n[STOP] {consecutive} chapters failed in a row — this is "
                  f"systemic, not per-chapter. Read "
                  f"_bulk/logs/ch{idx:03d}.log before resuming.", file=sys.stderr)
            return 2
        time.sleep(CHAPTER_GAP_S)

    print(f"\ndone={len(st['done'])}  failed={len(st['failed'])}")
    if st["failed"]:
        print("failed chapters:", ", ".join(sorted(st["failed"], key=int)))
        print("re-run them with:  python scripts/bulk_run.py --retry-failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
