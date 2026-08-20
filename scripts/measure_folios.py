#!/usr/bin/env python3
"""Measure each textbook's real printed page range from its indexed text.

Why this exists: the QC judge checks page citations against a per-book page
count, and those counts were guessed from general knowledge. Two of the five
guesses were wrong, one of them dangerously: Stahl Essential was recorded as
550 pages when its last printed page is 623, so a correct citation to page 600
would have been reported as invented.

It also settles which numbering the course speaks in. NotebookLM's index is a
FLAT TEXT STREAM with no page structure at all - the extracted text carries no
page markers, no form feeds, nothing. The only page numbers it can see are the
folios printed in each page's header or footer, which arrive inline in the text
like any other word. So the model cannot cite a PDF viewer's page index even in
principle; every citation it produces is a printed page number, and the ceiling
it is checked against must be in the same space.

The measurement reads those inline folios back out: a printed page number
appears as a line containing nothing but a number. Publication years look the
same, so the top of the range is found by window density rather than by
maximum - a textbook's folios cover their range almost completely, while a
bibliography's years cover 1990-2020 at under a third.

    python scripts/measure_folios.py            # from _bulk/ft/*.txt
    python scripts/measure_folios.py --write    # update schedule/drive_books.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FT_DIR = ROOT / "_bulk" / "ft"
BOOKS = ROOT / "schedule" / "drive_books.json"

WINDOW = 100          # look back this far when testing a candidate top page
MIN_DENSITY = 0.50    # a real folio range fills at least half its window
MIN_RUN = 40          # ignore documents too short to measure


def folios(text: str) -> set[int]:
    return {int(m) for m in re.findall(r"(?m)^[ \t]*(\d{1,4})[ \t]*$", text)
            if 1 <= int(m) <= 3000}


def max_printed_page(present: set[int]) -> int | None:
    """Largest page number whose neighbourhood is densely populated."""
    if len(present) < MIN_RUN:
        return None
    for n in sorted(present, reverse=True):
        lo = max(1, n - WINDOW + 1)
        density = sum(1 for i in range(lo, n + 1) if i in present) / (n - lo + 1)
        if density >= MIN_DENSITY:
            return n
    return None


def measure(ft_dir: Path = FT_DIR) -> dict:
    out = {}
    for f in sorted(ft_dir.glob("*.txt")):
        p = folios(f.read_text(encoding="utf-8", errors="replace"))
        out[f.stem] = {"max_printed_page": max_printed_page(p),
                       "distinct_folios": len(p)}
    return out


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", label)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="record the measurements in schedule/drive_books.json")
    a = ap.parse_args()
    m = measure()
    if not m:
        print(f"[!] no fulltext dumps in {FT_DIR}")
        return 1
    for k, v in m.items():
        print(f"  {k:22} last printed page = {v['max_printed_page']}"
              f"   ({v['distinct_folios']} distinct folios seen)")
    if a.write:
        books = json.loads(BOOKS.read_text(encoding="utf-8"))
        hit = 0
        for label, book in books.items():
            v = m.get(_slug(label))
            if v and v["max_printed_page"]:
                book["max_printed_page"] = v["max_printed_page"]
                book["page_space"] = "printed"
                hit += 1
        BOOKS.write_text(json.dumps(books, indent=1, ensure_ascii=False) + "\n",
                         encoding="utf-8")
        print(f"[OK] recorded {hit}/{len(books)} books in {BOOKS.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
