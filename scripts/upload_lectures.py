#!/usr/bin/env python3
"""Put the recorded academic lectures online and attach them to their chapters.

28 recordings from the owner's child-and-adolescent psychiatry course, 14GB of
MP4. They go in the same media bucket as the podcast feed, under the same
unguessable prefix: anonymous read of an object whose exact name you already
know, no bucket listing. Same privacy model, no new infrastructure.

Object names are deliberately NOT the Hebrew filenames. A URL built from
"טיפול במשחק ובכלים השלכתיים שונים - דר מרים פסקין - חלק ב.mp4" is a wall of
percent-escapes that breaks in half the places a link gets pasted. Each lecture
gets `lectures/chNNN-slug-partX.mp4` instead, which is stable, readable, and
sorts by chapter.

    python scripts/upload_lectures.py --dry-run
    python scripts/upload_lectures.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
LECTURES = ROOT.parent.parent.parent / "הקלטות לימודי המשך"
MAP_FILE = ROOT / "_bulk" / "lectures_map.json"
OUT_FILE = ROOT / "schedule" / "lectures.json"
BUCKET = os.environ.get("MEDIA_BUCKET", "psych-study-media")

# On Windows `gcloud` is a .cmd shim, and subprocess.run without shell=True
# cannot find it by bare name — it raises FileNotFoundError before any upload
# starts. Resolve it once, properly.
GCLOUD = shutil.which("gcloud") or shutil.which("gcloud.cmd") or "gcloud"
PROJECT = os.environ.get("GCP_PROJECT", "psych-research-agent")

# Lecturer names stay out of the object name: these are course recordings and
# the URL is effectively public to anyone holding it.
_PART = re.compile(r"חלק\s*([אב])'?")


def slug_for(row: dict, sched: dict) -> str:
    """chNNN-<what the talk is>[-part-x].

    Named after the LECTURE, not the chapter it is filed under. Using the
    chapter's title put the ethics lecture at ch102-forensic-psychiatry and the
    mindfulness one at ch132-cbt-theory-practice, which is wrong in the URL and
    wrong in the sidebar. The chapter number still leads, so the list sorts by
    where it belongs.
    """
    ch = row["primary"]
    base = row.get("name_en") or re.sub(
        r"[^a-z0-9]+", "-", sched[ch].get("title_en", "").lower()).strip("-")[:44]
    m = _PART.search(row["file"])
    part = {"א": "-part-a", "ב": "-part-b"}.get(m.group(1), "") if m else ""
    return f"ch{ch:03d}-{base}{part}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    secret = os.environ.get("FEED_SECRET", "").strip()
    if not secret:
        raise SystemExit("FEED_SECRET is not set (it is in .env)")
    rows = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    sched = {d["index"]: d for d in json.loads(
        (ROOT / "schedule" / "schedule.json").read_text(encoding="utf-8"))["days"]}

    seen, plan = {}, []
    for r in sorted(rows, key=lambda x: (x["primary"], x["file"])):
        s = slug_for(r, sched)
        if s in seen:                      # same chapter, different talk
            seen[s] += 1
            s = f"{s}-{seen[s]}"
        else:
            seen[s] = 1
        src = LECTURES / r["file"]
        if not src.exists():
            print(f"  [!] missing on disk: {r['file']}", file=sys.stderr)
            continue
        plan.append({**r, "object": f"lectures/{s}.mp4", "src": str(src),
                     "url": f"https://storage.googleapis.com/{BUCKET}/{secret}/lectures/{s}.mp4"})

    print(f"{len(plan)} lecture(s), {sum(p['size_mb'] for p in plan)/1024:.1f} GB")
    for p in plan:
        print(f"  ch{p['primary']:3}  {p['object']:52} {p['size_mb']:>5}MB")
    if a.dry_run:
        return 0

    done, failed = 0, []
    for i, p in enumerate(plan, 1):
        t0 = time.time()
        r = subprocess.run(
            [GCLOUD, "storage", "cp", p["src"],
             f"gs://{BUCKET}/{secret}/{p['object']}",
             "--project", PROJECT, "--content-type=video/mp4"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            done += 1
            print(f"  [{i}/{len(plan)}] OK   {p['object']}  "
                  f"{p['size_mb']}MB in {time.time()-t0:.0f}s", flush=True)
        else:
            failed.append(p["object"])
            print(f"  [{i}/{len(plan)}] FAIL {p['object']}: "
                  f"{(r.stderr or '')[:160]}", file=sys.stderr, flush=True)

    # Write the index the site reads, keyed by chapter, only for what uploaded.
    ok = [p for p in plan if p["object"] not in failed]
    by_ch: dict[int, list] = {}
    for p in ok:
        for ch in p["chapters"]:
            by_ch.setdefault(ch, []).append(
                {"title": Path(p["file"]).stem, "url": p["url"],
                 "size_mb": p["size_mb"], "primary": ch == p["primary"]})
    OUT_FILE.write_text(json.dumps(
        {"lectures_by_chapter": {str(k): v for k, v in sorted(by_ch.items())}},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n{done} uploaded, {len(failed)} failed; "
          f"{len(by_ch)} chapter(s) now have a lecture -> {OUT_FILE.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
