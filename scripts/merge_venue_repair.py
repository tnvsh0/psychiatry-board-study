#!/usr/bin/env python3
"""Merge re-authored venues into schedule/venues.json, resolving collisions.

The venues are authored in parallel batches, and parallel authors cannot see
each other's choices. That is exactly how the first authoring run produced six
launderettes, three bowling alleys and four garden centres: the place-category
uniqueness rule cannot be enforced by the agents, only by whoever sees all 144
at once. That is this script.

Each proposal is admitted only if its category is free. A loser keeps its old
venue and is written to a re-do list with the category that beat it, so the
next round can be told what not to pick. Ties are broken by chapter number so
the result does not depend on which agent happened to answer first.

    python scripts/merge_venue_repair.py _bulk/venue_result.json
    python scripts/merge_venue_repair.py _bulk/venue_result.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
VENUES = ROOT / "schedule" / "venues.json"

# Words that make two categories the same place with different paint.
_NOISE = re.compile(r"\b(a|an|the|small|large|old|new|local|village|town|city|"
                    r"high street|independent|family|traditional)\b")
_SYNONYM = {
    "launderette": "laundromat", "laundrette": "laundromat",
    "chemist": "pharmacy", "drugstore": "pharmacy",
    "greengrocer": "grocer", "greengrocers": "grocer",
    "opticians": "optician", "optometrist": "optician",
}


# Words that name no particular kind of place. "travel agency" and "travel
# agent" are one category; so are "pharmacy" and "chemist shop". Stripping
# these leaves the head noun that actually distinguishes one venue from another.
_GENERIC = {"shop", "store", "practice", "premise", "business", "place",
            "outlet", "service", "company", "centre", "center", "agency",
            "agent", "room", "unit", "establishment", "venue", "parlour",
            "parlor", "bureau", "office"}


def category_words(c: str) -> frozenset:
    t = _NOISE.sub(" ", (c or "").lower())
    t = re.sub(r"[^a-z ]+", " ", t)
    words = [_SYNONYM.get(w, w) for w in t.split() if len(w) > 2]
    # crude singularisation: "bowling alleys" and "bowling alley" are one thing
    words = [w[:-1] if w.endswith("s") and not w.endswith("ss") else w for w in words]
    core = {w for w in words if w not in _GENERIC}
    return frozenset(core or words)      # never return nothing


def norm_category(c: str) -> str:
    return " ".join(sorted(category_words(c)))


def same_category(a: str, b: str) -> bool:
    """One category, written two ways.

    Subset rather than equality, so "bike repair shop" does not slip past
    "bike shop": if everything distinctive about one is present in the other,
    a reader looking for a unique world will not get one.
    """
    wa, wb = category_words(a), category_words(b)
    if not wa or not wb:
        return False
    return wa <= wb or wb <= wa


def load_venues() -> list[dict]:
    d = json.loads(VENUES.read_text(encoding="utf-8"))
    return d["venues"] if isinstance(d, dict) else d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result", help="the workflow's JSON result (accepted/flagged)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    res = json.loads(Path(a.result).read_text(encoding="utf-8"))
    proposals = res.get("accepted") or []
    flagged = res.get("flagged") or []
    if not proposals:
        print("[!] nothing accepted in that result file")
        return 1

    venues = load_venues()
    by_index = {v["index"]: v for v in venues}
    changing = {p["index"] for p in proposals}

    # Categories held by chapters this run is NOT touching are immovable.
    held = []          # [(category_string, chapter_index)]
    for v in venues:
        if v["index"] in changing:
            continue
        held.append((v.get("place_category", ""), v["index"]))

    applied, lost = [], []
    for p in sorted(proposals, key=lambda x: x["index"]):
        cat = p.get("place_category", "")
        if not category_words(cat):
            lost.append({**p, "beaten_by": "empty place_category"})
            continue
        clash = next(((c, i) for c, i in held if same_category(cat, c)), None)
        if clash:
            lost.append({**p, "beaten_by": f"chapter {clash[1]} already holds "
                                           f"'{clash[0]}'"})
            continue
        held.append((cat, p["index"]))
        applied.append(p)

    print(f"  {len(proposals)} proposals: {len(applied)} applied, "
          f"{len(lost)} lost a category collision, {len(flagged)} critic-flagged")
    for x in lost[:12]:
        print(f"    ch{x['index']:3} '{x.get('place_category')}' — {x['beaten_by']}")

    redo = sorted({x["index"] for x in lost} | {x["index"] for x in flagged})
    Path(ROOT / "_bulk" / "venue_redo.json").write_text(
        json.dumps({"redo": redo,
                    "taken_categories": sorted(c for c, _ in held),
                    "lost": lost, "flagged": flagged}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"  {len(redo)} chapter(s) need another round -> _bulk/venue_redo.json")

    if a.dry_run:
        print("  (dry run — venues.json untouched)")
        return 0

    for p in applied:
        old = by_index.get(p["index"], {})
        by_index[p["index"]] = {**old, **p, "_reauthored": True}
    out = sorted(by_index.values(), key=lambda v: v["index"])
    VENUES.write_text(json.dumps({"venues": out}, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    print(f"  wrote {len(out)} venues to {VENUES.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
