#!/usr/bin/env python3
"""Update schedule.json days 1, 8-13 (foundations) to include developmental symbols
   and force Sketchy-style visuals even when symbols_needed was empty."""
import json
from pathlib import Path

import sys
# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SCHED = ROOT / "schedule" / "schedule.json"

# Maps day_index -> symbols to ADD
DAY_SYMBOL_UPDATES = {
    1: ["developmental_ladder", "developmental_red_flags"],  # examination
    8: ["developmental_ladder", "brazelton_still_face", "developmental_red_flags"],  # 0-2
    9: ["developmental_ladder", "piaget_4_stages"],  # 2-5
    10: ["developmental_ladder", "piaget_4_stages", "erikson_8_stages"],  # 6-12
    11: ["developmental_ladder", "piaget_4_stages", "erikson_8_stages", "prefrontal_cortex", "nucleus_accumbens"],  # 13-18
    12: ["attachment_4_styles", "fonagy_mentalization", "brazelton_still_face"],  # attachment
    13: ["thomas_chess_temperament"],  # temperament
    # Day 14 already has DSM_personality_disorders symbol
    # Day 15 (mood/affect/temperament clinical assessment) — minimal
    119: ["winnicott_holding", "klein_positions", "kohut_self_psychology", "anna_freud_defenses", "fonagy_mentalization"],  # psychodynamic
}


def main():
    d = json.loads(SCHED.read_text(encoding="utf-8"))
    updated = 0
    for day in d["days"]:
        idx = day["index"]
        if idx in DAY_SYMBOL_UPDATES:
            existing = set(day.get("symbols_needed", []))
            new = DAY_SYMBOL_UPDATES[idx]
            merged = list(existing | set(new))
            day["symbols_needed"] = merged
            updated += 1
            print(f"  Day {idx}: +{len(new)} symbols ({day['title_he']})")

    SCHED.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nUpdated {updated} days")


if __name__ == "__main__":
    main()
