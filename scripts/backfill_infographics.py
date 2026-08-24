#!/usr/bin/env python3
"""Fill in the per-aspect infographics, a few a day, off the critical path.

The owner's call, and the right one: the Sketchy plates matter more, so nothing
about infographics may slow them down. Worth being precise about what the two
actually compete for, because it is not the same pool:

  - Sketchy plates are rendered by the Gemini IMAGE API.
  - Infographics are built by NOTEBOOKLM, which caps artifact creation per day
    and starts refusing audio and infographics after about two chapters' worth
    while video, quiz and flashcards keep working.

So they never take each other's quota. What they take from each other is WALL
CLOCK inside a chapter — and since the NotebookLM cap would refuse them by the
third chapter of any run anyway, the only way to have them at all is like this:
a separate pass, a few a day, against released chapters.

Each infographic is scoped to ONE aspect's facts, uploaded as its own source,
so it summarises the chapter's DATA and not the mnemonic scene.

    python scripts/backfill_infographics.py --dry-run
    python scripts/backfill_infographics.py --limit 2
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
WORK = ROOT / "_bulk" / "infographics"
RATE = ("RateLimit", "RESOURCE_EXHAUSTED", "quota", "cap")


def chapters_missing_infographics() -> list[dict]:
    """Released chapters with no infographic asset, earliest first."""
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
        try:
            idx = int(tag.split("-")[1])
        except (IndexError, ValueError):
            continue
        day = sched.get(idx)
        if not day or tag.split("-", 2)[-1] != ds.slugify(day.get("title_en", "")):
            continue                      # stale tag
        a = subprocess.run(["gh", "release", "view", tag, "--repo", ds.GITHUB_REPO,
                            "--json", "assets"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if a.returncode != 0:
            continue
        names = [x["name"] for x in json.loads(a.stdout or "{}").get("assets", [])]
        if any(n.startswith("infographic_") for n in names):
            continue
        if "facts.json" not in names:
            continue                      # nothing to scope an infographic to
        out.append({"index": idx, "tag": tag, "day": day})
    return sorted(out, key=lambda x: x["index"])


def make_for_chapter(job: dict, per_chapter: int) -> tuple[int, str]:
    idx, day, tag = job["index"], job["day"], job["tag"]
    try:
        nb_id, _ = ds.find_day_notebook(idx, day.get("title_he", day.get("title_en", "")))
    except Exception as e:
        return 0, f"notebook lookup failed: {str(e)[:90]}"
    if not nb_id:
        return 0, "no notebook for this chapter"

    WORK.mkdir(parents=True, exist_ok=True)
    facts_f = WORK / f"facts-{idx:03d}.json"
    d = subprocess.run(["gh", "release", "download", tag, "--repo", ds.GITHUB_REPO,
                        "--pattern", "facts.json", "--output", str(facts_f),
                        "--clobber"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if d.returncode != 0 or not facts_f.exists():
        return 0, "could not fetch facts.json"
    facts = json.loads(facts_f.read_text(encoding="utf-8"))
    facts_f.unlink(missing_ok=True)

    ds.use_notebook(nb_id)
    made = 0
    for aspect in day.get("aspects", [])[:per_chapter]:
        slice_ = ds.facts_for_aspect(aspect, facts)
        if not slice_:
            continue
        sid = ds.add_text_source(nb_id, ds._render_scope_md(day, aspect, slice_),
                                 f"[SCOPE] {aspect}")
        if not sid:
            return made, "scope source upload refused (likely the daily cap)"
        time.sleep(ds.SCOPE_INDEX_WAIT_S if hasattr(ds, "SCOPE_INDEX_WAIT_S") else 60)
        task = ds.start_infographic_scoped(
            ds.prompt_infographic_scoped(day, aspect, slice_), sid)
        if not task:
            return made, "generation refused (likely the daily cap)"
        got = ds.wait_for_artifacts({task: "infographic"})
        aid = next((k for k, v in got.items() if v[1] == "completed"), None)
        if not aid:
            return made, f"{aspect}: never completed"
        out = WORK / f"infographic_{aspect}.png"
        if not ds.download_artifact("infographic", aid, out) or not out.exists():
            return made, f"{aspect}: download failed"
        u = subprocess.run(["gh", "release", "upload", tag, str(out),
                            "--repo", ds.GITHUB_REPO, "--clobber"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        out.unlink(missing_ok=True)
        if u.returncode != 0:
            return made, f"{aspect}: upload failed"
        made += 1
        time.sleep(20)
    return made, f"{made} infographic(s) attached"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2,
                    help="chapters to attempt this run (the daily cap is small)")
    ap.add_argument("--per-chapter", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    jobs = chapters_missing_infographics()
    print(f"{len(jobs)} released chapter(s) have no infographic")
    if not jobs:
        return 0
    if a.dry_run:
        for j in jobs[:20]:
            print(f"  {j['index']:3} {j['day'].get('title_he','')[:44]}")
        return 0

    total = 0
    for j in jobs[:a.limit]:
        print(f"[{j['index']:3}] {j['day'].get('title_he','')[:40]} — generating...",
              flush=True)
        n, note = make_for_chapter(j, a.per_chapter)
        total += n
        print(f"[{j['index']:3}] {'OK  ' if n else 'FAIL'} {note}", flush=True)
        if n == 0 and any(m.lower() in note.lower() for m in RATE):
            print("  daily cap reached — stopping, try again tomorrow",
                  file=sys.stderr)
            break
        time.sleep(30)
    print(f"\n{total} infographic(s) added")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
