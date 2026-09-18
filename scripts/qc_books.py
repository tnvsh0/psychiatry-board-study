#!/usr/bin/env python3
"""The textbooks, addressed the way the study material cites them: by printed page.

A judge can only check "(Dulcan p.330)" if p.330 means the text a reader sees on
printed page 330. That is not a PDF page in the two most-cited books.

DSM-5-TR and Dulcan are REFLOWED ebooks. The printed page number is a
standalone number line in the middle of the text, where that printed page
begins; one printed page spans 2-4 PDF pages. Measured 2026-09-18 by the spec
review: a header/footer folio scan finds 124 folios in DSM's 1,377 PDF pages
and 170 in Dulcan's 1,677, and taking "the mapped PDF page" as the page's text
yields a median 32% (DSM) and 16% (Dulcan) of it — 0% for 255 DSM pages and
448 Dulcan pages. So for those two books a printed page here is the text
between the inline number N and the next inline number.

The Stahl books are laid out: a folio on the first or last line of each PDF
page, at a constant offset. The Syllabus and the flashcard summaries
(סיכומי כרטיסיות שלב ב) are laid out too. The Synopsis has no printed pages
at all; it is cited by chapter and read from the per-chapter slices.

Page text is cached under _qc/cache/books/ (gitignored: it is the books' text).

    python scripts/qc_books.py --check     coverage per book + known-page probes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "_qc" / "cache" / "books"
SLICES_DIR = REPO_ROOT / "schedule" / "synopsis_slices"

# Canonical keys. Every other name for a book goes through normalize_label().
# model: "inline" = printed page is the text between inline number lines;
#        "offset" = one PDF page per printed page at a constant offset.
SPECS = {
    "DSM-5-TR":   {"needle": "DSM 5 TR", "model": "inline"},
    "Dulcan":     {"needle": "Dulcans Textbook", "model": "inline"},
    "StahlNeuro": {"needle": "Stahls Essential Psychopharmacology. Neuroscientific",
                   "model": "offset"},
    "StahlKids":  {"needle": "Prescribers Guide - Children", "model": "offset"},
    "Syllabus":   {"needle": "cylabus", "model": "offset", "default_delta": -1},
    # printed == PDF page number (build_reading_guide.py:212)
    "Kartisiot":  {"needle": "סיכומי כרטיסיות", "model": "offset", "default_delta": -1},
}
HEBREW_NAME = {"Kartisiot": "סיכומי כרטיסיות שלב ב", "Syllabus": "סילבוס"}

_NUMLINE = re.compile(r"^\d{1,4}$")
# Consecutive inline page numbers never jump further than this; a bigger jump
# is a table value or a list number, not a page.
MAX_STEP = 10


def books_dir() -> Path:
    """Text BOOKS/ — in this checkout, or in the main checkout of a worktree."""
    env = os.environ.get("QC_BOOKS_DIR")
    if env:
        return Path(env)
    for base in (REPO_ROOT, *REPO_ROOT.parents):
        d = base / "Text BOOKS"
        if d.is_dir() and any("DSM" in p.name for p in d.glob("*.pdf")):
            return d
    raise FileNotFoundError("no 'Text BOOKS' directory with the DSM found")


def book_file(key: str) -> Path:
    needle = SPECS[key]["needle"].lower()
    for p in sorted(books_dir().glob("*.pdf")):
        if needle in p.name.lower():
            return p
    raise FileNotFoundError(f"{key}: no PDF matching {SPECS[key]['needle']!r} "
                            f"in {books_dir()}")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_texts(key: str) -> list[str]:
    """Text of every PDF page, cached by the PDF's sha256."""
    import fitz
    pdf = book_file(key)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{key}.json"
    sha = _sha(pdf)
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("sha256") == sha:
            return data["texts"]
    doc = fitz.open(pdf)
    texts = [doc[i].get_text() for i in range(doc.page_count)]
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps({"sha256": sha, "file": pdf.name, "texts": texts},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cache)
    return texts


def _inline_markers(texts: list[str]) -> dict[int, tuple[int, int]]:
    """printed page -> (pdf index, line index) where that page begins.

    Every standalone number line is a candidate. The page numbers are the
    longest chain that increases through the book in steps of 1..MAX_STEP —
    figure labels, table cells and list numbers fall out because they do not
    continue the sequence.
    """
    limit = len(texts)
    cands = []
    for i, t in enumerate(texts):
        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        for j, ln in enumerate(lines):
            if _NUMLINE.match(ln) and 0 < int(ln) <= limit:
                cands.append((i, j, int(ln)))
    # best[v] = (chain length, index into cands) for a chain ending at value v
    best: dict[int, tuple[int, int]] = {}
    prev = [-1] * len(cands)
    for pos, (_, _, v) in enumerate(cands):
        length, back = 1, -1
        for u in range(v - MAX_STEP, v):
            if u in best and best[u][0] + 1 > length:
                length, back = best[u][0] + 1, best[u][1]
        prev[pos] = back
        if v not in best or best[v][0] < length:
            best[v] = (length, pos)
    if not best:
        return {}
    pos = max(best.values())[1]
    chain = []
    while pos != -1:
        chain.append(cands[pos])
        pos = prev[pos]
    return {v: (i, j) for i, j, v in reversed(chain)}


def _edge_folios(texts: list[str]) -> dict[int, int]:
    """pdf index -> folio, from the first or last non-empty line of the page."""
    out = {}
    for i, t in enumerate(texts):
        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        for ln in (lines[:1] + lines[-1:]):
            if _NUMLINE.match(ln):
                out[i] = int(ln)
                break
    return out


class Book:
    def __init__(self, key: str):
        self.key = key
        self.spec = SPECS[key]
        self.model = self.spec["model"]
        self.texts = _page_texts(key)
        self.file = book_file(key).name
        self._pages: dict[int, tuple[str, list[int]]] = {}
        self.coverage: dict = {}
        (self._build_inline if self.model == "inline" else self._build_offset)()

    # -- construction ------------------------------------------------------
    def _build_inline(self):
        markers = _inline_markers(self.texts)
        lines = [[ln for ln in t.split("\n") if ln.strip()] for t in self.texts]
        order = sorted(markers.items(), key=lambda kv: kv[1])
        for k, (n, (i, j)) in enumerate(order):
            end = order[k + 1][1] if k + 1 < len(order) else (min(i + 3, len(lines) - 1), None)
            ei, ej = end
            chunk, pages = [], []
            for pi in range(i, ei + 1):
                lo = j + 1 if pi == i else 0
                hi = ej if (pi == ei and ej is not None) else len(lines[pi])
                seg = [ln for ln in lines[pi][lo:hi] if not _NUMLINE.match(ln.strip())]
                if seg or pi == i:
                    pages.append(pi)
                chunk.extend(seg)
            text = "\n".join(chunk)
            nxt = order[k + 1][0] if k + 1 < len(order) else n + 1
            # printed pages with no number of their own (chapter openers,
            # figures) belong to the text that runs across them
            for p in range(n, max(nxt, n + 1)):
                self._pages[p] = (text, pages)
        vals = sorted(markers)
        self.coverage = {"model": "inline", "pdf_pages": len(self.texts),
                         "marked": len(markers),
                         "range": [vals[0], vals[-1]] if vals else None,
                         "unmarked_in_range": (vals[-1] - vals[0] + 1 - len(vals))
                         if vals else None}

    def _build_offset(self):
        folios = _edge_folios(self.texts)
        deltas = Counter(i - n for i, n in folios.items())
        if deltas:
            delta, agree = deltas.most_common(1)[0]
        else:
            delta, agree = self.spec.get("default_delta", 0), 0
        if agree < 10 and "default_delta" in self.spec:
            delta = self.spec["default_delta"]
        for i, t in enumerate(self.texts):
            n = i - delta
            if n >= 1:
                self._pages[n] = (t, [i])
        self.delta = delta
        self.coverage = {"model": "offset", "pdf_pages": len(self.texts),
                         "folios_found": len(folios), "delta": delta,
                         "delta_agreement": round(agree / len(folios), 3)
                         if folios else None,
                         "range": [1, len(self.texts) - delta]}

    # -- lookups -------------------------------------------------------------
    @property
    def max_printed(self) -> int:
        return max(self._pages) if self._pages else 0

    def has_page(self, printed: int) -> bool:
        return printed in self._pages

    def page_text(self, printed: int) -> str | None:
        hit = self._pages.get(printed)
        return hit[0] if hit else None

    def pdf_pages(self, printed: int) -> list[int]:
        hit = self._pages.get(printed)
        return list(hit[1]) if hit else []

    def pages(self):
        """(printed, text) for every distinct printed page, ascending."""
        seen = set()
        for n in sorted(self._pages):
            text = self._pages[n][0]
            if id(text) in seen:
                continue
            seen.add(id(text))
            yield n, text


@lru_cache(maxsize=None)
def book(key: str) -> Book:
    return Book(key)


# ---------------------------------------------------------------------------
# Names the study material uses for the books.
_ALIASES = [
    (r"dsm[\s\-]*5[\s\-]*tr|dsm[\s\-]*5|dsm", "DSM-5-TR"),
    (r"dulcan", "Dulcan"),
    (r"stahl\s*kids|stahl\s*c\s*&\s*a|stahl'?s?\s*prescriber'?s?|prescriber'?s?\s*guide"
     r"|stahl\s*pediatric|stahl\s*children", "StahlKids"),
    (r"stahl\s*neuro|stahl\s*essential|essential\s*psychopharmacology", "StahlNeuro"),
    (r"synopsis|kaplan|k\s*&\s*s", "Synopsis"),
    (r"syllabus|סילבוס|cylabus", "Syllabus"),
    (r"סיכומי\s*כרטיסיות|כרטיסיות", "Kartisiot"),
]


def normalize_label(raw: str) -> str | None:
    """Canonical book key for a name as written in the material, or None.
    A bare 'Stahl' is ambiguous between the two Stahl books and returns
    'Stahl?' for the caller to resolve from the page number and context."""
    s = (raw or "").strip().lower()
    for pat, key in _ALIASES:
        if re.fullmatch(rf"\s*(?:{pat})\s*", s, re.I):
            return key
    if re.fullmatch(r"\s*stahl'?s?\s*", s, re.I):
        return "Stahl?"
    return None


# ---------------------------------------------------------------------------
def search(pattern: str, keys=None, flags=re.I, limit: int = 50):
    """Every printed page whose text matches `pattern`: [(key, printed, snippet)]."""
    rx = re.compile(pattern, flags)
    out = []
    for key in keys or SPECS:
        for n, text in book(key).pages():
            m = rx.search(text)
            if m:
                a, b = max(0, m.start() - 80), min(len(text), m.end() + 80)
                out.append((key, n, " ".join(text[a:b].split())))
                if len(out) >= limit:
                    return out
    return out


def synopsis_slices() -> list[dict]:
    """The per-chapter Synopsis extracts: [{ref, path, pages}] from the manifest."""
    man = SLICES_DIR / "manifest.json"
    if not man.exists():
        return []
    data = json.loads(man.read_text(encoding="utf-8"))
    out = []
    for s in data.get("slices", []):
        path = SLICES_DIR / s.get("file", "")
        out.append({"ref": s.get("synopsis_ch") or s.get("ref") or s.get("chapter"),
                    "path": path, "pages": s.get("pages")})
    return out


# ---------------------------------------------------------------------------
# Pages whose content is known, used as a regression check on the mapping.
# Each was confirmed by reading the page during the 2026-09-18 spec review.
PROBES = [
    ("StahlKids", 69, r"full\s+therapeutic\s+benefits\s+may\s+take\s+2\s*[–-]\s*8"),
    ("StahlKids", 74, r"continue\s+to\s+improve\s+for\s+8\s*[–-]\s*12\s+weeks"),
    ("DSM-5-TR", 135, r"catalepsy"),
    ("Dulcan", 330, r"sertraline"),
    ("Dulcan", 771, r"carbamazepine\s+also\s+decreases\s+lithium\s+clearance"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--books", nargs="*", default=list(SPECS))
    a = ap.parse_args()
    if not a.check:
        ap.print_help()
        return 0
    bad = 0
    for key in a.books:
        b = book(key)
        print(f"{key:<11} {json.dumps(b.coverage)}  max_printed={b.max_printed}")
    for key, n, pat in PROBES:
        if key not in a.books:
            continue
        text = book(key).page_text(n) or ""
        ok = bool(re.search(pat, text, re.I))
        near = [m for m in (n - 1, n + 1)
                if re.search(pat, book(key).page_text(m) or "", re.I)]
        print(f"probe {key} p.{n}: {'OK' if ok else 'MISS'}"
              + ("" if ok else f" (found on {near})" if near else ""))
        bad += not ok
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
