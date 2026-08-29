#!/usr/bin/env python3
"""Render the plates of chapters that were built without pictures.

The free image allowance runs out long before the day does — measured at about
fifteen a day, with a multi-day lockout after sustained use — while pulling a
chapter's facts out of NotebookLM has nothing to do with that quota. So chapters
are built with SKIP_PLATES during a lockout, which produces everything except
the PNGs, and this fills them in when the images come back.

It renders the table, the image, the legend and the quiz together, which is why
the deferred pass discards the director's earlier work rather than reusing it:
a legend written against one element table and an image drawn from another
would disagree, and the reader would have no way to tell which was wrong.

    python scripts/backfill_plates.py                 # every chapter, oldest first
    python scripts/backfill_plates.py --day 26        # just this one
    python scripts/backfill_plates.py --limit 6       # stop after six plates
    python scripts/backfill_plates.py --dry-run       # say what is missing
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plate_pipeline as pp  # noqa: E402
import sketchy_spec as spec  # noqa: E402
from agy_image import QuotaExhausted  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
GEN = ROOT / "_generated"


def missing_plates(day_dir: Path) -> tuple[dict, list]:
    """(schedule row, plates with no PNG) for one built chapter."""
    facts_file = day_dir / "facts.json"
    if not facts_file.exists():
        return {}, []
    index = int(day_dir.name.split("-")[-1])
    sched = json.loads((ROOT / "schedule/schedule.json").read_text(encoding="utf-8"))
    day = next((d for d in sched["days"] if d["index"] == index), None)
    if not day:
        return {}, []
    facts = json.loads(facts_file.read_text(encoding="utf-8"))
    plates = spec.plan_plates(spec.atomize(facts))
    return day, [p for p in plates
                 if not (day_dir / f"sketchy_{p.name}.png").exists()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, help="one chapter, by index")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many plates (0 = no limit)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    dirs = sorted(d for d in GEN.glob("day-*") if d.is_dir())
    if a.day:
        dirs = [d for d in dirs if d.name == f"day-{a.day:03d}"]

    todo = []
    for d in dirs:
        day, plates = missing_plates(d)
        for p in plates:
            todo.append((d, day, p))

    if not todo:
        print("every built chapter has all of its plates.")
        return 0

    print(f"{len(todo)} plate(s) missing across "
          f"{len({d.name for d, _, _ in todo})} chapter(s):")
    for d, day, p in todo:
        print(f"  {d.name}  {p.name:14} {len(p.facts):>3} facts   "
              f"{day.get('title_he', '')[:44]}")
    if a.dry_run:
        return 0

    done = 0
    for d, day, plate in todo:
        if a.limit and done >= a.limit:
            print(f"\nstopping at the {a.limit}-plate limit; "
                  f"{len(todo) - done} still missing.")
            break
        venue = pp.load_venue(day["index"])
        tokens = pp.load_tokens(day)
        print(f"\n=== {d.name} / {plate.name} ({len(plate.facts)} facts) ===")
        try:
            rep = pp.run_plate(day, plate, venue, tokens, d)
        except QuotaExhausted as e:
            # Not a failure of this chapter. Stop the whole pass rather than
            # grinding every remaining plate against the same wall, and say
            # when it is worth coming back.
            print(f"\n[i] image quota exhausted — {e}")
            print(f"[i] {len(todo) - done} plate(s) still missing. "
                  f"Re-run this command when the quota returns.")
            break
        except Exception as e:  # noqa: BLE001 — one bad plate must not stop the pass
            print(f"[FAIL] {e}")
            continue
        png = d / f"sketchy_{plate.name}.png"
        if not png.exists():
            print("[FAIL] no render produced")
            continue
        done += 1
        g = rep.get("gates", {}) or {}
        m = g.get("mnemonic") or {}
        print(f"[OK] {png.name}  score {g.get('score')}  "
              f"tries {rep.get('tries')}  "
              f"actions {m.get('actions_drawn')}/{m.get('actions_asked')}")

    print(f"\nrendered {done} plate(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
