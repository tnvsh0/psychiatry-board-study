#!/usr/bin/env python3
"""Extract a chapter map for each textbook into schedule/book_map.json.

Run this ONCE (or whenever a source book changes). It needs the PDFs locally;
audit_coverage.py then runs off the resulting JSON alone, so the audit does not
need 500 MB of textbooks every time.

The hard part is that the books' embedded tables of contents give PDF page
indices, while NotebookLM cites PRINTED page numbers — the two differ by a
front-matter offset, and in Dulcan the offset is not even constant. So rather
than assuming a fixed offset, this scans every page for a printed folio and
builds a real printed->pdf mapping, then converts each chapter boundary through
it.

Kaplan & Sadock's Synopsis is a reflowed edition with no printed page numbers at
all. It is recorded with pages=None: cite it by chapter, never by page.

Usage:
    python scripts/build_book_map.py [--books-dir "Text BOOKS"]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = REPO_ROOT / "schedule" / "book_map.json"

# key -> (filename substring, TOC depth that corresponds to "chapter")
BOOKS = {
    "DSM-5-TR": ("DSM 5 TR", 2),
    "Dulcan": ("Dulcans Textbook", 2),
    "StahlNeuro": ("Stahls Essential Psychopharmacology. Neuroscientific", 1),
    "StahlKids": ("Prescribers Guide - Children", 1),
    "Synopsis": ("Synopsis of Psychiatry", 2),
}

FOLIO = re.compile(r"^\d{1,4}$")


def find_pdf(books_dir: Path, needle: str) -> Path | None:
    for p in sorted(books_dir.glob("*.pdf")):
        if needle.lower() in p.name.lower():
            return p
    return None


def scan_folios(doc) -> dict[int, int]:
    """Map pdf page index (0-based) -> printed page number.

    Only the first and last text lines are considered — a page number is a
    header or a footer, and digits anywhere on the page would pick up figure
    numbers and years. That alone is not enough: a chapter-opening page reading
    "1." looks exactly like folio 1. Synopsis is entirely chapter-opening
    numbers, and taking them at face value produced chapter ranges of [1,1],
    [2,2], [3,3].

    So candidates are then filtered:
      1. a folio never exceeds its own pdf index (front matter only adds pages);
      2. printed numbers must rise monotonically with pdf position;
      3. the surviving run must advance at a page-like SLOPE. This is the real
         discriminator. Do not filter on a constant offset instead — Dulcan's
         offset drifts from 46 early to 113 by the ADHD chapter because the
         scan carries extra plates, so an offset window silently amputates both
         ends of the book. Chapter numbers are what a slope test catches:
         Synopsis's "1., 2., 3." crawl at slope 0.01 while real folios run
         near 1.0.
    """
    candidates: list[tuple[int, int]] = []
    for i in range(doc.page_count):
        lines = [x.strip() for x in doc[i].get_text().split("\n") if x.strip()]
        if not lines:
            continue
        for cand in (lines[0], lines[-1]):
            if FOLIO.fullmatch(cand):
                n = int(cand)
                if 0 < n <= doc.page_count:
                    candidates.append((i, n))
                    break

    if len(candidates) < 10:
        return {}

    # A folio can never exceed its own pdf index: front matter only ever adds
    # pages ahead of printed page 1, never removes them.
    kept = [(i, n) for i, n in candidates if n <= i]

    # Printed numbers only ever go up, so keep the largest subset that rises
    # monotonically. This must be a longest-increasing-subsequence and not a
    # greedy forward pass: greedy anchors on whatever comes first, so a single
    # bogus high value early in the book discards everything after it (that
    # alone cut Dulcan from 181 usable folios to 56 and got it misclassified
    # as unpaginated).
    import bisect

    kept.sort()
    tails: list[int] = []          # tails[k] = smallest end value of an len-(k+1) run
    tail_idx: list[int] = []       # position in `kept` of that end value
    prev: list[int] = [-1] * len(kept)
    for pos, (_i, n) in enumerate(kept):
        k = bisect.bisect_left(tails, n)
        if k == len(tails):
            tails.append(n)
            tail_idx.append(pos)
        else:
            tails[k] = n
            tail_idx[k] = pos
        prev[pos] = tail_idx[k - 1] if k else -1

    folios: dict[int, int] = {}
    pos = tail_idx[-1] if tail_idx else -1
    while pos != -1:
        i, n = kept[pos]
        folios[i] = n
        pos = prev[pos]

    # Slope test: real pages advance at roughly the rate of pdf pages, so the
    # per-step slope Δn/Δi clusters near 1.0. Chapter numbers advance by 1 over
    # a whole chapter (~0.01). Use the MEDIAN of consecutive slopes, not the
    # endpoints: the endpoints are fooled when the run is 35 chapter numbers
    # (slope 0.01) that happens to end on two real index-page folios (2000,
    # 2013), which lifts the overall endpoint slope past any fixed bar while
    # 34 of 36 steps are still chapter crawl. The median ignores those two
    # outliers and correctly reads ~0.01, rejecting the whole polluted run.
    if len(folios) >= 3:
        idx = sorted(folios)
        slopes = sorted(
            (folios[b] - folios[a]) / (b - a)
            for a, b in zip(idx, idx[1:]) if b != a
        )
        if not slopes or slopes[len(slopes) // 2] < 0.5:
            return {}
    return folios


def printed_for(pdf_idx: int, folios: dict[int, int]) -> int | None:
    """Printed page for a pdf index, via the nearest folio in either direction."""
    if not folios:
        return None
    if pdf_idx in folios:
        return folios[pdf_idx]
    nearest = min(folios, key=lambda k: abs(k - pdf_idx))
    if abs(nearest - pdf_idx) > 25:  # too far to extrapolate honestly
        return None
    return folios[nearest] + (pdf_idx - nearest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--books-dir", default=str(REPO_ROOT / "Text BOOKS"))
    args = ap.parse_args()

    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("ERROR: PyMuPDF required.  pip install pymupdf", file=sys.stderr)
        return 2

    books_dir = Path(args.books_dir)
    if not books_dir.is_dir():
        print(f"ERROR: books dir not found: {books_dir}", file=sys.stderr)
        return 2

    out: dict[str, object] = {
        "_note": (
            "Generated by scripts/build_book_map.py. 'pages' is the PRINTED page "
            "range (what NotebookLM cites); null means the edition carries no "
            "printed page numbers and must be cited by chapter."
        ),
        "books": {},
    }

    for key, (needle, depth) in BOOKS.items():
        pdf = find_pdf(books_dir, needle)
        if pdf is None:
            print(f"[!] {key}: no PDF matching {needle!r} — skipped")
            continue

        doc = fitz.open(pdf)
        toc = [(lvl, title, pg) for lvl, title, pg in doc.get_toc() if lvl == depth]
        if not toc:
            print(f"[!] {key}: no TOC entries at depth {depth} — skipped")
            continue

        print(f"[*] {key}: {doc.page_count} pdf pages, {len(toc)} chapters — scanning folios...")
        folios = scan_folios(doc)

        # scan_folios returns {} when the page numbers it found do not behave
        # like pagination — a reflowed edition with none at all. Synopsis is
        # exactly this, and any range we invented for it would be a lie.
        if not folios:
            print("    no usable pagination — treating as UNPAGINATED (cite by chapter)")

        chapters = []
        for i, (_lvl, title, pg) in enumerate(toc):
            start_pdf = pg - 1  # get_toc is 1-based
            end_pdf = (toc[i + 1][2] - 2) if i + 1 < len(toc) else doc.page_count - 1
            chapters.append({
                "title": title.strip(),
                "pdf_pages": [start_pdf + 1, end_pdf + 1],
                "pages": None if not folios else [
                    printed_for(start_pdf, folios), printed_for(end_pdf, folios)
                ],
            })

        # Drop the page range if either endpoint could not be resolved honestly.
        for c in chapters:
            if c["pages"] and (c["pages"][0] is None or c["pages"][1] is None):
                c["pages"] = None

        resolved = sum(1 for c in chapters if c["pages"])
        out["books"][key] = {
            "file": pdf.name,
            "pdf_page_count": doc.page_count,
            "has_printed_pages": bool(folios),
            "folios_found": len(folios),
            "chapters": chapters,
        }
        print(f"    folios on {len(folios)}/{doc.page_count} pages; "
              f"{resolved}/{len(chapters)} chapters got a printed range")
        doc.close()

    # Self-check. These printed page numbers were verified by hand against the
    # physical books; if the folio logic regresses, the map silently starts
    # pointing at the wrong chapters, which is worse than having no map.
    anchors = [
        ("DSM-5-TR", "Neurodevelopmental Disorders", 35, 3),
        ("Dulcan", "10 Attention-Deficit/Hyperactivity Disorder", 173, 4),
        ("StahlNeuro", "11 Attention Deficit Hyperactivity Disorder", 449, 3),
        ("StahlKids", "1 amphetamine-d", 1, 3),
    ]
    failures = []
    for book_key, title_prefix, expect, tol in anchors:
        book = out["books"].get(book_key)
        if not book:
            continue
        match = next((c for c in book["chapters"]
                      if c["title"].startswith(title_prefix)), None)
        if match is None:
            failures.append(f"{book_key}: no chapter starting {title_prefix!r}")
        elif not match["pages"]:
            failures.append(f"{book_key}/{title_prefix}: no printed range resolved")
        elif abs(match["pages"][0] - expect) > tol:
            failures.append(
                f"{book_key}/{title_prefix}: starts at p.{match['pages'][0]}, "
                f"expected ~{expect}")

    print()
    if failures:
        print("SELF-CHECK FAILED — not writing the map:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"Self-check passed ({len(anchors)} known anchors).")

    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
