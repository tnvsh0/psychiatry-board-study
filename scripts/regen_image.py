#!/usr/bin/env python3
"""Regenerate ONE sketchy image (+ caption) for a chapter, e.g. after a transient
Gemini failure during a bulk run, and optionally re-upload it to the chapter's
GitHub Release.

Reuses the EXACT same director + coverage-retry path as the main pipeline
(build_image_jobs -> build_director_instruction -> generate_sketchy_directed), so
the regenerated image matches current quality settings (e.g. criteria images get
the coverage-guaranteed retry loop). Reads facts from the already-generated
_generated/day-NNN/facts.json.

Usage:
    python scripts/regen_image.py --day 16 --label dsm_criteria_inattention --upload
    python scripts/regen_image.py --day 16 --list        # show valid labels
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daily_study import (  # noqa: E402
    load_schedule, load_symbols, build_image_jobs,
    build_director_instruction, slugify, GITHUB_REPO,
)
from gemini_image import generate_sketchy_directed  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _jobs_for_day(day, facts):
    density = day.get("estimated_density", "medium")
    n_info = {"low": 1, "medium": 2, "high": 3}.get(density, 2)
    aspects = day.get("aspects", [])[:n_info]
    return build_image_jobs(aspects, facts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--label", help="e.g. dsm_criteria_inattention, epidemiology, etiology")
    ap.add_argument("--list", action="store_true", help="list valid labels for this day and exit")
    ap.add_argument("--upload", action="store_true", help="re-upload the image + caption to the chapter's GitHub Release")
    args = ap.parse_args()

    sched = load_schedule()
    symbols = load_symbols()
    day = next((d for d in sched["days"] if d["index"] == args.day), None)
    if not day:
        print(f"[!] day {args.day} not in schedule")
        sys.exit(1)

    day_dir = REPO_ROOT / "_generated" / f"day-{args.day:03d}"
    facts_path = day_dir / "facts.json"
    if not facts_path.exists():
        print(f"[!] {facts_path} not found — run the full chapter first so facts.json exists.")
        sys.exit(1)
    facts = json.loads(facts_path.read_text(encoding="utf-8"))

    jobs = _jobs_for_day(day, facts)
    if args.list or not args.label:
        print("Valid labels for day", args.day, ":")
        for label, aspect, _ in jobs:
            print(f"  {label}  (aspect={aspect})")
        sys.exit(0)

    job = next((j for j in jobs if j[0] == args.label), None)
    if not job:
        print(f"[!] label '{args.label}' not found. Valid: {[j[0] for j in jobs]}")
        sys.exit(1)
    label, aspect, job_facts = job

    required_items, max_tries = None, 1
    if aspect in ("dsm_criteria", "diagnostic_criteria") and isinstance(job_facts, dict):
        items = (job_facts.get("criteria") or {}).get("items")
        if items:
            required_items, max_tries = [str(i) for i in items], 3

    director = build_director_instruction(
        day, symbols, aspect, job_facts,
        anchor_world=day.get("anchor_world"),
        anchor_zone=day.get("anchor_zones", {}).get(aspect),
        half=label.split("epidemiology_")[-1] if label.startswith("epidemiology_") else None,
    )
    png = day_dir / f"sketchy_{label}.png"
    txt = day_dir / f"sketchy_{label}.txt"
    print(f"Regenerating sketchy_{label} "
          f"(criteria-completeness loop: {'yes, ' + str(len(required_items)) + ' items' if required_items else 'no'})...")
    ok, cap = generate_sketchy_directed(
        director_instruction=director, output_path=png, caption_path=txt,
        phase=day.get("phase"), required_items=required_items, max_image_tries=max_tries,
    )
    if not ok:
        print(f"[FAIL] regeneration failed for sketchy_{label} (transient — try again).")
        sys.exit(1)
    size_mb = png.stat().st_size / (1024 * 1024)
    print(f"[OK] sketchy_{label}.png ({size_mb:.1f} MB) + caption ({len(cap)} chars)")

    if args.upload:
        tag = f"study-{args.day:03d}-{slugify(day.get('title_en', ''))}"
        for asset in (png, txt):
            r = subprocess.run(
                ["gh", "release", "upload", tag, str(asset), "--repo", GITHUB_REPO, "--clobber"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                print(f"[OK] uploaded {asset.name} to {tag}")
            else:
                print(f"[FAIL] upload {asset.name}: {(r.stderr or r.stdout)[:200]}")


if __name__ == "__main__":
    main()
