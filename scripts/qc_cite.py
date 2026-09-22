#!/usr/bin/env python3
"""Every citation in the material, as (book, printed page) pairs.

Two jobs, both of which the old check got wrong.

1. THE PAGE GATE. `qc_review.check_page_citations` reads only the FIRST number
   after a book name, so `(StahlNeuro p. 289, 902)` — chapter 029's guide —
   passes in a 624-page book. It also never looks inside the source-map tables
   that open every guide, where the book is the column header and the cell is
   bare numbers. Here every page in a list is a citation of its own.

2. THE BUNDLE. qc_sources builds one PDF per judge call from "every cited
   printed page ±1" (spec §5.7). A citation this parser misses is a page the
   judge never sees, and a unit judged without its own source page comes back
   `unsupported` — a false finding that costs an owner review.

RULES THAT ARE NOT OBVIOUS, EACH FROM A REAL STRING IN THE CORPUS

- A number is a page only after a page word (`p.` `pp.` `page` `עמ'`). This is
  the whole year defence: `DSM-5-TR (2022)` and `DSM-5 בשנת 2013` carry a
  four-digit number right after a book name and cite nothing.
- Synopsis / Kaplan & Sadock numbers are SECTIONS, never pages: the Synopsis
  has no printed pages here (qc_books: per-chapter slices only), and the
  material writes `Kaplan & Sadock p. 2-16` and `p.33.2` — a "range" that is a
  section label. Expanding it as pages would invent 15 citations.
- A bare "Stahl p. N" names neither book in the notebook. Resolving it to the
  smaller one flagged 117 of chapter 2's 134 citations as impossible. So it is
  resolved only when exactly ONE of the two Stahl books has that printed page,
  and is `ambiguous` otherwise — and an ambiguous page is impossible only when
  it is past the larger book.
- Ranges expand, but only to 10 pages. `pp. 448-449` is a range; `p. 2-16` on a
  paginated book is a typo or a chapter span, and expanding it would put 15
  unrelated pages in the bundle, so only the two endpoints are kept.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qc_books  # noqa: E402  canonical book keys, page mapping, normalize_label

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Ordered: the first alternative that matches at a position wins, so every
# specific Stahl name must precede the bare one, and DSM-5-TR must precede DSM.
_BOOK_PATTERNS: list[tuple[str, list[str]]] = [
    ("DSM-5-TR", [r"DSM[\s\-–]*5[\s\-–]*TR", r"DSM[\s\-–]*5", r"DSM"]),
    ("Dulcan", [r"Dulcan(?:['’]s)?(?:\s+Textbook)?"]),
    ("StahlNeuro", [r"StahlNeuro",
                    r"Stahl(?:['’]s)?\s*(?:Essential|Neuroscientific)"
                    r"(?:\s+Psychopharmacology)?",
                    r"Essential\s+Psychopharmacology"]),
    ("StahlKids", [r"StahlKids",
                   r"Stahl(?:['’]s)?\s*(?:Prescriber(?:['’]s)?(?:\s+Guide)?|PG"
                   r"|C\s*&\s*A|Pediatric|Kids|Children)",
                   r"Prescriber(?:['’]s)?\s+Guide"]),
    ("Synopsis", [r"Kaplan[\s\-–]*(?:&|and)?[\s\-–]*Sadock(?:['’]s)?",
                  r"Kaplan(?:['’]s)?", r"K\s*&\s*S", r"Synopsis"]),
    ("Syllabus", [r"Syllabus", r"cylabus", r"סילבוס"]),
    ("Kartisiot", [r"סיכומי\s*כרטיסיות(?:\s*שלב\s*ב)?", r"כרטיסיות"]),
    ("Stahl?", [r"Stahl(?:['’]s)?(?![A-Za-z])"]),
]
_BOOK_RE = re.compile(
    "|".join(f"(?P<b{i}>(?<![A-Za-z])(?:{'|'.join(pats)}))"
             for i, (_, pats) in enumerate(_BOOK_PATTERNS)), re.I)
_GROUP_KEY = {f"b{i}": key for i, (key, _) in enumerate(_BOOK_PATTERNS)}

# Books with no printed pages in this notebook: every number is a section.
_SECTION_ONLY = {"Synopsis"}

# "עמ" only when it stands alone — otherwise it eats the prefix of עמוד, עמית.
_PAGE_WORD = re.compile(r"(?:pp?\.|pages?\b|עמודים|עמוד|עמ['’׳]?(?!\w))", re.I)
_SECTION_WORD = re.compile(r"(?:chapters?\b|chap\.|ch\.|פרקים|פרק)", re.I)
# Bidi controls sit between a Hebrew word and its number all over this corpus
# (the legends are full of U+2066/2069), and a soft hyphen survives the PDFs.
_SEP = re.compile(r"[\s,;­​‎‏‪-‮⁦-⁩]+")
_DASHES = r"\-–—־"                               # hyphen, en, em, Hebrew maqaf
_PAGE_NUM = re.compile(rf"(\d{{1,4}})(?:\s*[{_DASHES}]\s*(\d{{1,4}}))?")
# "2.4", "2-16", "33.2" — a Synopsis section label, kept whole.
_SECTION_NUM = re.compile(rf"\d{{1,4}}(?:[.{_DASHES}]\d{{1,4}})*")
# `)` ends a parenthesised citation, `|` a table cell, backtick a code span.
_STOP = re.compile(r"[)\]}`|\n]")
MAX_RANGE = 10

CiteDict = dict


def resolve_label(raw: str) -> str | None:
    """Canonical book key for a name written anywhere in the material.

    qc_books.normalize_label first — it is the source of truth for the keys —
    then the compound forms only the study material uses ("Stahl PG",
    "Kaplan & Sadock", "Dulcan's Textbook"), which it does not know.
    """
    s = (raw or "").strip()
    if not s:
        return None
    key = qc_books.normalize_label(s)
    if key:
        return key
    for canonical, pats in _BOOK_PATTERNS:
        if any(re.fullmatch(p, s, re.I) for p in pats):
            return canonical
    return None


def _cite(book: str, printed, section, raw: str, unit: str, span, ambiguous):
    return {"book": book, "printed": printed, "section": section,
            "raw": raw, "unit": unit, "span": span, "ambiguous": ambiguous}


def _page_exists(key: str, printed: int) -> bool:
    """has_page, but a missing PDF answers False instead of raising.

    Only the bare-Stahl resolution uses this: with no books on the machine it
    must fall back to `ambiguous`, not crash a parse that is otherwise offline.
    impossible() deliberately does NOT go through here — see its docstring.
    """
    try:
        return qc_books.book(key).has_page(printed)
    except Exception:
        return False


def _resolve_stahl(printed: int | None) -> tuple[str, bool]:
    """(book, ambiguous) for a bare "Stahl p.N"."""
    if printed is None:
        return "Stahl?", True
    hits = [k for k in ("StahlKids", "StahlNeuro") if _page_exists(k, printed)]
    if len(hits) == 1:
        return hits[0], False
    return "Stahl?", True


def _expand(a: int, b: int | None) -> list[int]:
    if b is None:
        return [a]
    if b < a:
        return [a, b]
    if b - a + 1 > MAX_RANGE:
        return [a, b]
    return list(range(a, b + 1))


def _scan_tail(key: str, tail: str) -> tuple[list, list, int]:
    """Read the numbers that belong to one book mention.

    Returns (pages, sections, consumed) where pages is [(page, (s, e))] and
    sections is [(label, (s, e))], offsets relative to `tail`. Stops at the
    first thing that is not a separator, a page/section word or a number —
    which is what keeps "According to StahlNeuro, what is the precise..." from
    swallowing the first number of the next sentence.
    """
    section_only = key in _SECTION_ONLY
    pages: list = []
    sections: list = []
    mode = "section" if section_only else None
    i, consumed = 0, 0
    while i < len(tail):
        m = _SEP.match(tail, i)
        if m and m.end() > i:
            i = m.end()
            continue
        m = _PAGE_WORD.match(tail, i)
        if m:
            mode = "section" if section_only else "page"
            i = m.end()
            continue
        m = _SECTION_WORD.match(tail, i)
        if m:
            mode = "section"
            i = m.end()
            continue
        if mode == "section":
            m = _SECTION_NUM.match(tail, i)
            if m:
                sections.append((m.group(0), (m.start(), m.end())))
                i = consumed = m.end()
                continue
        elif mode == "page":
            m = _PAGE_NUM.match(tail, i)
            if m:
                span = (m.start(), m.end())
                for p in _expand(int(m.group(1)),
                                 int(m.group(2)) if m.group(2) else None):
                    pages.append((p, span))
                i = consumed = m.end()
                continue
        # A number before any page word is a year, an edition or a study date.
        # Skip it rather than stop, so "(DSM-5-TR, 2022, p. 135)" still cites.
        m = _PAGE_NUM.match(tail, i)
        if m:
            i = m.end()
            continue
        break
    return pages, sections, consumed


# ---------------------------------------------------------------------------
# The source-map table that opens every guide: the book is the column header.


def _row_cells(line: str, base: int) -> list[tuple[int, int]]:
    """(start, end) of each cell of a markdown row, absolute in the document."""
    bars = [k for k, ch in enumerate(line) if ch == "|"]
    return [(base + a + 1, base + b) for a, b in zip(bars, bars[1:])]


def _clean_header(cell: str) -> str:
    s = re.sub(r"\([^)]*\)", " ", cell)          # "עמ' (מתוך הסיכום)"
    s = re.sub(r"[*_`#]", " ", s)
    s = _PAGE_WORD.sub(" ", s)
    s = _SECTION_WORD.sub(" ", s)
    return " ".join(s.split())


_SEP_ROW = re.compile(r"^[\s|:\-–—]+$")


def _book_columns(text: str) -> list[tuple[int, int, str]]:
    """(cell start, cell end, book) for every data cell in a book column."""
    lines, pos = [], 0
    for line in text.splitlines(keepends=True):
        lines.append((pos, line.rstrip("\n")))
        pos += len(line)

    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(lines) - 1:
        base, line = lines[i]
        nxt = lines[i + 1][1]
        # A separator row is what makes this a table and not prose with pipes.
        if not (line.strip().startswith("|") and nxt.strip().startswith("|")
                and _SEP_ROW.match(nxt)):
            i += 1
            continue
        header = {}
        for col, (a, b) in enumerate(_row_cells(line, base)):
            key = resolve_label(_clean_header(text[a:b]))
            if key:
                header[col] = key
        j = i + 2
        while j < len(lines) and lines[j][1].strip().startswith("|"):
            rbase, rline = lines[j]
            for col, (a, b) in enumerate(_row_cells(rline, rbase)):
                if col in header:
                    out.append((a, b, header[col]))
            j += 1
        i = j
    return out


# ---------------------------------------------------------------------------


def parse(text: str, *, unit: str = "") -> list[CiteDict]:
    """Every citation in `text`.

    Each is {book, printed, section, raw, unit, span, ambiguous}: `book` is a
    canonical qc_books key (or 'Stahl?' when a bare "Stahl" cannot be resolved),
    `printed` is None for a section-only reference, `span` locates that one
    number in `text` (qc_fix rewrites through it) and `raw` is the citation it
    came from, for the report.
    """
    text = text or ""
    out: list[CiteDict] = []

    for m in _BOOK_RE.finditer(text):
        key = _GROUP_KEY[m.lastgroup]
        tail = text[m.end():]
        stop = _STOP.search(tail)
        if stop:
            tail = tail[:stop.start()]
        nxt = _BOOK_RE.search(tail)
        if nxt:                                  # "(Dulcan p.182, DSM-5-TR p.71)"
            tail = tail[:nxt.start()]
        pages, sections, consumed = _scan_tail(key, tail)
        if not pages and not sections:
            continue
        raw = text[m.start():m.end() + consumed].strip()
        section = sections[-1][0] if sections else None
        if pages:
            for p, (a, b) in pages:
                book, amb = _resolve_stahl(p) if key == "Stahl?" else (key, False)
                out.append(_cite(book, p, section, raw, unit,
                                 (m.end() + a, m.end() + b), amb))
        else:
            for label, (a, b) in sections:
                out.append(_cite(key, None, label, raw, unit,
                                 (m.end() + a, m.end() + b), key == "Stahl?"))

    for a, b, key in _book_columns(text):
        cell = text[a:b]
        if not any(a <= c["span"][0] < b for c in out):
            # An explicit "Synopsis ch.10" inside the cell was already parsed;
            # only a cell of bare numbers needs its column's book.
            raw = cell.strip()
            if key in _SECTION_ONLY:
                for m in _SECTION_NUM.finditer(cell):
                    out.append(_cite(key, None, m.group(0), raw, unit,
                                     (a + m.start(), a + m.end()), False))
            else:
                for m in _PAGE_NUM.finditer(cell):
                    span = (a + m.start(), a + m.end())
                    for p in _expand(int(m.group(1)),
                                     int(m.group(2)) if m.group(2) else None):
                        book, amb = (_resolve_stahl(p) if key == "Stahl?"
                                     else (key, False))
                        out.append(_cite(book, p, None, raw, unit, span, amb))

    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: (c["span"][0], c["printed"] or 0,
                                        c["section"] or "")):
        sig = (c["book"], c["printed"], c["section"], c["span"])
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(c)
    return uniq


def impossible(cites: list[CiteDict]) -> list[CiteDict]:
    """The citations whose page cannot exist: past the last printed page, or
    inside the range but with no text mapped to it.

    Sections are never impossible — the Synopsis has no pages to exceed.
    A bare "Stahl" is checked against the LARGER of the two Stahl books, the
    rule that took chapter 2 from 117 false flags to its real ones.

    This needs the PDFs. It raises if they are missing rather than reporting
    "nothing impossible", which would let an unchecked chapter look clean.
    """
    out = []
    for c in cites:
        if c["printed"] is None:
            continue
        keys = (["StahlKids", "StahlNeuro"] if c["book"] == "Stahl?"
                else [c["book"]])
        keys = [k for k in keys if k in qc_books.SPECS]
        if not keys:
            continue
        if not any(qc_books.book(k).has_page(c["printed"]) for k in keys):
            out.append(c)
    return out


def cites_by_unit(units) -> dict[str, list[CiteDict]]:
    """uid -> its citations, for the qc_units.Unit list of a chapter."""
    out: dict[str, list[CiteDict]] = {}
    for u in units:
        out.setdefault(u.uid, []).extend(parse(u.text, unit=u.uid))
    return out


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="a text/markdown/JSON asset to scan")
    ap.add_argument("--impossible", action="store_true",
                    help="only the citations that cannot exist (needs the PDFs)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cites = parse(Path(a.path).read_text(encoding="utf-8"))
    if a.impossible:
        cites = impossible(cites)
    if a.json:
        print(json.dumps(cites, ensure_ascii=False, indent=1))
        return 0
    for c in cites:
        where = c["printed"] if c["printed"] is not None else f"§{c['section']}"
        flag = " AMBIGUOUS" if c["ambiguous"] else ""
        print(f"{c['book']:<11} {str(where):<8} {c['raw'][:60]!r}{flag}")
    print(f"-- {len(cites)} citations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
