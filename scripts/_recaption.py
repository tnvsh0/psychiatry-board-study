#!/usr/bin/env python3
"""Re-caption an EXISTING sketchy image WITHOUT regenerating the image — a cheap
way to test caption-prompt changes (e.g. after de-priming the number pegs) and to
see what the image ACTUALLY contains, free of stale priming.

Usage: python scripts/_recaption.py --day 16 --label epidemiology [--save]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daily_study import facts_for_aspect  # noqa: E402
from gemini_image import generate_caption  # noqa: E402

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--label", required=True, help="e.g. epidemiology, dsm_criteria_inattention")
    ap.add_argument("--save", action="store_true", help="overwrite the .txt caption file")
    args = ap.parse_args()

    day_dir = REPO / "_generated" / f"day-{args.day:03d}"
    png = day_dir / f"sketchy_{args.label}.png"
    if not png.exists():
        print(f"[!] {png} not found")
        sys.exit(1)
    facts_path = day_dir / "facts.json"
    facts = json.loads(facts_path.read_text(encoding="utf-8")) if facts_path.exists() else {}
    aspect = args.label.split("_")[0]
    if aspect == "dsm":
        aspect = "dsm_criteria"
    slice_ = facts_for_aspect(aspect, facts) or facts

    cap = generate_caption(
        image_prompt=f"A {aspect} mnemonic scene.",
        symbol_legend="Facts for context: " + json.dumps(slice_, ensure_ascii=False),
        image_path=png,
    )
    print("===== CAPTION OF EXISTING IMAGE (no peg priming) =====")
    print(cap)
    if args.save and cap:
        (day_dir / f"sketchy_{args.label}.txt").write_text(cap, encoding="utf-8")
        print(f"\n[saved] sketchy_{args.label}.txt")


if __name__ == "__main__":
    main()
