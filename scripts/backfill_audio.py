#!/usr/bin/env python3
"""Fill in the chapters' podcast audio, a few a day, against NotebookLM's cap.

Audio is the one artifact with a hard daily quota. Chapter 29's third run asked
for one and got `RPC CREATE_ARTIFACT failed: RateLimitError` - the fourth audio
request of the day on this account. Everything else in a chapter (video, quiz,
flashcards, infographics, the Sketchy plates, the study guide) generates freely.

So audio must not sit on the critical path. If the bulk run had to produce a
podcast per chapter it would not be limited by anything except that quota, and
141 chapters would take months. Instead the bulk run publishes each chapter
WITHOUT audio and this backfills, decoupled: run it once a day, it takes the
next few chapters in study order that have no audio yet, generates, uploads to
the existing release, and refreshes the feed.

Study order matters here more than anywhere else. The owner listens while
commuting and reads chapter 1 forward, so the podcast that matters is always
the earliest one still missing.

    python scripts/backfill_audio.py --limit 3      # today's quota, roughly
    python scripts/backfill_audio.py --dry-run      # what it would do
"""
from __future__ import annotations

import argparse
import json
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

import daily_study as ds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "_bulk" / "audio"
RATE_MARKERS = ("RateLimit", "RESOURCE_EXHAUSTED", "quota")


def releases_missing_audio() -> list[dict]:
    """Released chapters with no usable mp3, earliest chapter first."""
    r = subprocess.run(["gh", "release", "list", "--repo", ds.GITHUB_REPO,
                        "--limit", "300", "--json", "tagName"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"gh release list failed: {(r.stderr or '')[:200]}")
    sched = {d["index"]: d for d in ds.load_schedule()["days"]}
    out = []
    for rel in json.loads(r.stdout or "[]"):
        tag = rel["tagName"]
        if not tag.startswith("study-"):
            continue
        try:
            idx = int(tag.split("-")[1])
        except (IndexError, ValueError):
            continue
        day = sched.get(idx)
        if not day or tag.split("-", 2)[-1] != ds.slugify(day.get("title_en", "")):
            continue                       # stale tag; leave it alone
        a = subprocess.run(["gh", "release", "view", tag, "--repo", ds.GITHUB_REPO,
                            "--json", "assets"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if a.returncode != 0:
            continue
        assets = json.loads(a.stdout or "{}").get("assets", [])
        # size, not presence: a truncated upload is a silent 0-second episode
        if any(x["name"].lower().endswith(".mp3") and x["size"] > 100_000
               for x in assets):
            continue
        out.append({"index": idx, "tag": tag, "day": day})
    return sorted(out, key=lambda x: x["index"])


def make_audio(job: dict) -> tuple[bool, str]:
    """Generate, download and attach one chapter's podcast. (ok, note)"""
    idx, day, tag = job["index"], job["day"], job["tag"]
    try:
        nb_id, _ = ds.find_day_notebook(idx, day.get("title_he", day.get("title_en", "")))
    except RuntimeError as e:
        return False, f"could not look up the notebook: {e}"
    if not nb_id:
        return False, "no notebook for this chapter (re-run the chapter first)"
    ds.use_notebook(nb_id)

    # The same prompt the chapter itself would have used, so a backfilled
    # episode is not a different product from one made in-line.
    symbol_block = ds.collect_symbol_descriptions(
        day.get("symbols_needed", []), ds.load_symbols())
    task = ds.start_audio(ds.prompt_audio(day, symbol_block, ""))
    if not task:
        return False, "generation refused (likely the daily cap)"

    # {uuid: kind} — the orientation wait_for_artifacts documents and matches
    # against the artifact listing. Passing {"audio": task} instead matched
    # nothing, timed out after the full wait, and then returned a record that is
    # TRUTHY ("audio" -> (uuid, "timeout", None)), so this function went on to
    # call download_artifact with a tuple as the artifact id. No episode was
    # ever attached by this script; it reported a download failure every time.
    ok = ds.wait_for_artifacts({task: "audio"})
    done = [aid for aid, (_k, status, _a) in (ok or {}).items()
            if status == "completed"]
    if not done:
        state = ", ".join(sorted({s for _k, s, _a in (ok or {}).values()}))
        return False, f"generation never completed ({state or 'no status'})"
    task = done[0]

    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / f"day-{idx:03d}.mp3"
    if not ds.download_artifact("audio", task, out) or not out.exists():
        return False, "download failed"
    size = out.stat().st_size
    if size < 100_000:
        out.unlink(missing_ok=True)
        return False, f"downloaded only {size} bytes"

    u = subprocess.run(["gh", "release", "upload", tag, str(out),
                        "--repo", ds.GITHUB_REPO, "--clobber"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out.unlink(missing_ok=True)            # ~50-95MB each; do not hoard them
    if u.returncode != 0:
        return False, f"upload failed: {(u.stderr or '')[:120]}"
    return True, f"{size / 1048576:.0f}MB attached to {tag}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3,
                    help="how many to attempt this run (the daily cap is small)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--day", type=int, help="just this chapter")
    a = ap.parse_args()

    jobs = releases_missing_audio()
    if a.day is not None:
        jobs = [j for j in jobs if j["index"] == a.day]
    print(f"{len(jobs)} released chapter(s) have no audio yet")
    if not jobs:
        return 0
    if a.dry_run:
        for j in jobs[:20]:
            print(f"  {j['index']:3} {j['day'].get('title_he', '')[:44]}")
        return 0

    done = attempted = 0
    for j in jobs:
        if attempted >= a.limit:
            break
        print(f"[{j['index']:3}] {j['day'].get('title_he', '')[:40]} — generating...",
              flush=True)
        ok, note = make_audio(j)
        if not ok and note.startswith("no notebook"):
            # Chapters 1-22 lost their notebooks (measured 2026-09-26: 112 of
            # 144 remain). Counting them against --limit meant a daily run
            # spent its whole allowance failing on chapters 1-3 and never
            # reached one it could make — the first live test of Hebrew audio,
            # 2026-10-02, never got past chapter 2.
            print(f"[{j['index']:3}] SKIP {note}", flush=True)
            continue
        attempted += 1
        print(f"[{j['index']:3}] {'OK  ' if ok else 'FAIL'} {note}", flush=True)
        done += ok
        if not ok and any(m.lower() in note.lower() for m in RATE_MARKERS + ("cap",)):
            print("  daily cap reached — stopping, try again tomorrow",
                  file=sys.stderr)
            break
        time.sleep(30)

    if done:
        print(f"\n{done} new episode(s); refreshing the feed...")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_study_rss.py")],
                           text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("  [!] feed refresh failed; run generate_study_rss.py by hand",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
