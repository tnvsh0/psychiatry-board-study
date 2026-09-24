#!/usr/bin/env python3
"""The pages a judge call is allowed to judge from, and the PDF that carries them.

A judge that never received its source answers anyway. Measured 2026-09-18
(agy 1.2.5, one page of StahlKids): the call returned status SUCCESS, confident
JSON, an invented "verbatim" quote, and ruled a wrong flashcard *supported*.
agy_attach proves the file was ingested; this module proves the judge TURNED
THE PAGES, by stamping every page with a fresh random code that exists nowhere
else and asking for a few of them back (§5.8 canary).

WHAT GOES IN A BUNDLE, AND WHY EACH REASON EXISTS

  cited    every cited printed page +-1. The radius is not politeness: the
           Dulcan CAMS sentence that contradicts 038 Q9 is split across printed
           pages 330 and 331 and is on neither page alone.
  guide    the chapter's reading ranges from schedule/reading_guide.json. This
           is the material the chapter was written from, so a claim the judge
           cannot find on a cited page is often three pages away.
  synopsis the chapter's Synopsis slices. The Synopsis has no printed pages at
           all (qc_books), so it can only ever enter as whole slice PDFs.
  search   full-text hits. §5.7: "A bare value match is not enough (StahlKids
           repeats 'may take 2-8 weeks' on 12 pages)." Measured here: the token
           `2|8` really is on exactly 12 StahlKids pages, and only two of them
           (69, 83) also name something chapter 019's atomoxetine item names.
           That is the whole trick — the value must land inside a key-term
           window, or the bundle fills with coincidences.

The search window is English-only. The key terms are Latin words (Atomoxetine,
CAMS, GAD-7), which is what makes a cheap window possible at all: this material
writes drug, trial and scale names in English inside Hebrew sentences. A claim
whose only identifier is Hebrew ("חרדת נטישה") gets no search hits, and its
values are then checked only against its cited pages. Documented, not fixed:
Hebrew-to-English term mapping is a lexicon problem, not a bundling one.

NEVER DROP PAGES, AND NEVER DUPLICATE THE CONTEXT

Chapter 019's reading guide alone is 137 printed pages (Dulcan 695-728,
StahlNeuro 449-485, StahlKids 69-96/141-154/243-254, Kartisiot 133-144) — most
of a bundle before a single citation is added, and past the 120-page end of
§5.7's range on its own. So the reading ranges are chapter CONTEXT: distributed
across the chapter's calls, each page in exactly one bundle, never copied into
all three. What follows a unit into
its call, always, is the unit's OWN evidence — its cited pages and its search
hits — because §5.7's promise is that "a unit is judged in the call that holds
its cited pages and its search hits", and a unit judged without them comes back
`unsupported`, which costs an owner review.

The limit counts the pages the judge actually turns, i.e. PDF pages, not
printed ones. In DSM-5-TR and Dulcan one printed page is 2-4 PDF pages, so a
limit applied to printed pages would build a 300-page bundle and call it 120.

STAMPS

One code per page, all six characters fresh per call (§5.7 says the code set is
fresh per call; per PAGE is what makes the canary mean anything — a single
per-call code would be answerable from any one page, or from a lucky guess of
one in 32^6). The codes are in the stamp and in the manifest and nowhere else;
in particular they are NOT on the index page, or the judge could read them all
without opening a single source page.

Hebrew stamps go through insert_htmlbox, not insert_text. Measured: insert_text
with Arial writes סיכומי כרטיסיות שלב ב in visual order, and the extractor then
returns 'ב בלש תויסיטרכ ימוכיס' — a reversed string the app, the judge and this
module's own verification would all fail to match. insert_htmlbox runs the bidi
algorithm and extracts in logical order.

Saving matters too: 48 pages copied out of StahlKids, Dulcan and Kartisiot save
to 35MB with default options and 1.1MB with garbage=4 + clean + subset_fonts —
the same bytes, minus five copies of every embedded font. The whole bundle has
to travel to agy on every retry; at 180 pages it is 2-4MB.

    python scripts/qc_sources.py <chapter dir> --day 19          plan only
    python scripts/qc_sources.py <chapter dir> --day 19 --build  bundles too
    python scripts/qc_sources.py --chapter 19 --measure-limit 250   §6, no agy
    python scripts/qc_sources.py --check-answer reply.json --out <that dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qc_books  # noqa: E402  page model, printed -> PDF pages, book files
import qc_cite   # noqa: E402  citations -> (book, printed)
import qc_units  # noqa: E402  the judged atom
# key_terms, the value-token index and the citation blanker come from qc_lint
# rather than being rebuilt here. All four implement rules qc_lint's §5.4 pass
# already had to get right — the boundary-token form of a number (`2|8`, never
# `28`), the stop-word list that keeps "front" and "answer" from matching every
# page, and the coverage-table cell whose bare `35, 68, 74` are page numbers and
# not values. A second implementation would drift from that one, and the drift
# would show up as a bundle quietly missing the page a finding rests on.
# _book_numbers is also lru_cached per process: qc_run lints before it plans, so
# by the time we get here the indexes are already built.
import qc_lint   # noqa: E402
import qc_norm   # noqa: E402  locked_values

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
READING_GUIDE = REPO_ROOT / "schedule" / "reading_guide.json"

# §5.7's candidates are 120 / 180 / 250 and §6 calibrates the choice with the
# last-10% canary. Until it runs, the middle one, because the limit decides how
# much agy time the backlog costs. Planned here on three released chapters:
#
#     limit    019 (quiz+cards)   029 (guide+facts)   042 (guide+facts)
#      120       2 calls            8 calls             8 calls
#      180       2 calls            5 calls             4 calls
#      250       1 call             4 calls             2 calls
#
# At 25 minutes a call, 120 would cost the 68-chapter backlog roughly twice
# what 180 does, and a limit that is too HIGH fails closed — a missed canary
# discards the verdict and retries — while one that is too low silently costs
# the whole run.
#
# MEASURED 2026-09-24 (scripts/qc_measure_limits.py, chapter 019, agy 1.2.10,
# one call per size, three stamped codes each at 5%, 50% and 95% of the
# bundle): 250 -> a 253-page bundle, all three codes returned, including the
# one on bundle page 241 of 253. The judge reads to the end of a bundle that
# size. 120 and 180 both fill to the same 197 pages on this chapter — its
# source map alone pulls 195 — and both passed too, which is the other half of
# the answer: the limit was never what those chapters obeyed.
#
# So the limit is 250, and that is what §6 asked for: "the largest bundle
# whose last-10% canary passes". It is one chapter and one call per size; a
# later tail miss lowers it again, and costs nothing but a retry when it does.
DEFAULT_PAGE_LIMIT = 250

# The Dulcan p.330/331 reason, same radius as qc_norm.source_window: a quote
# verified across a page break has to be in the bundle across that break too.
CITE_RADIUS = 1

# Bundle order. Ascending printed page inside a book, books in this order, so
# that a bundle reads like a stack of photocopies and the index is scannable.
BOOK_ORDER = tuple(qc_books.SPECS) + ("Synopsis",)
_BOOK_RANK = {k: i for i, k in enumerate(BOOK_ORDER)}

# A value inside a key-term window can still hit more pages than a bundle can
# carry. Ranked by how many of the unit's terms the page names, so the page
# that is about the same claim wins; the cap is per value and per unit because
# one unit with a table of numbers could otherwise pull in 50 pages on its own.
# Every cap that bites is recorded on the call (`notes`) — it is a page the
# judge did not see, and §5.7's "never drop pages" is about the SPLIT, not
# about an unbounded search.
MAX_SEARCH_PAGES_PER_VALUE = 4
MAX_SEARCH_PAGES_PER_UNIT = 12

# ...and one key term is not a window. Measured on chapter 019 Q1 (a vignette
# about a 9-year-old on atomoxetine): its locked values include the bare `9`,
# which lands on 78 pages that name at least one of the item's twelve terms —
# every page of every book that says "ADHD". Requiring TWO distinct terms
# leaves the pages that are about this claim: StahlKids p.69 (atomoxetine AND
# ADHD, "may take 2-8 weeks", the source conflict with the item's cited p.74)
# survives; p.83 (duloxetine, ADHD only) does not. A unit that names only one
# term keeps that one — it is all the identity it has.
MIN_TERM_HITS = 2

# A reading-guide range longer than this is a typo, not a reading assignment:
# the longest real one in the guide is StahlNeuro 77-158 (82 pages).
MAX_GUIDE_RANGE = 150

# How far the unit budget is squeezed to make room for the chapter's context,
# and where it stops. Each squeeze costs one more agy call (25 minutes), so the
# floor is high enough that a chapter can never be shredded into a call per
# unit: below 24 pdf pages a call cannot hold one cited page +-1 from each of
# four books plus its search hits, and splitting stops helping.
SPLIT_ATTEMPTS = 8
MIN_UNIT_BUDGET = 24

# Reason precedence when a page arrives twice. `cited` outranks everything
# because it is the page a finding will name; `guide` is the weakest, being
# chapter context rather than evidence for any one unit.
REASON_RANK = {"cited": 0, "search": 1, "synopsis": 2, "guide": 3}
UNIT_REASONS = frozenset({"cited", "search"})   # these follow their unit

# Crockford-ish base32 without the letters that read as digits in a scan of a
# stamped margin: no O/0, no I/1, no L. 27^6 = 387 million, and the judge is
# asked for 4+ of them per call.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 6
NONCE_LEN = 12
# The nonce line the judge must return (§5.7). It is deliberately unlike any
# unit heading, so a judge that echoes it into a coverage row is obvious.
NONCE_MARKER = "QC-NONCE"

# §5.8: the canary names at least this many pages, at least one per book, and
# at least one in the last tenth of the bundle — the tail is where a model that
# skimmed the first pages stops having read anything.
CANARY_MIN = 4
CANARY_TAIL_FRACTION = 0.10

# The nonce sits in the last tenth of artifacts.md for the same reason.
NONCE_TAIL_FRACTION = 0.10

# Which judge call an artifact's units belong to (§5.8). report.md is
# study_guide.md's name in the chapters built by the oldest pipeline.
KIND_BY_ARTIFACT = {
    "quiz.json": "A", "flashcards.json": "A",
    "study_guide.md": "B", "report.md": "B", "facts.json": "B",
}
KIND_LEGEND = "C"
KIND_SYNOPSIS = "S"

# §5.8: "Synopsis-heavy chapters (>= 20% of book citations, or Synopsis as the
# only textbook — 013, 070) judge the Synopsis in its own call(s)."
SYNOPSIS_HEAVY_SHARE = 0.20


# §5.7's split is the only thing allowed to move a unit between calls;
# nothing is allowed to drop one, and no unit is exempt from being judged.
# An earlier version of this module could leave the guide's source-map
# table out of the judge calls, on the premise that it states no clinical
# fact and that qc_cite and qc_lint already check every page reference in
# it. That was refuted by the first chapter judged end to end: 9 of
# chapter 019's 20 active errata are on that one unit and 8 of them are
# severity high — pages that exist, are mappable and are in the right
# book, cited for the wrong topic — while qc_cite marks none of its 136
# citations impossible and qc_lint leaves the unit with zero flags, so
# nothing deterministic can see them. The switch, the classifier behind it
# and every path that could exclude a unit from judging were removed; the
# per-call page limit (measure_limit below) is where the call count is
# bought instead. Do not rebuild it.


class PlanError(ValueError):
    """The chapter cannot be turned into calls. Never swallowed: a chapter
    planned wrong is a chapter judged against the wrong pages, which is worse
    than a chapter that stops."""


# ---------------------------------------------------------------------------
# the plan


@dataclass
class CallPlan:
    """One agy call: its units, its pages, and (once built) its two files."""
    call_id: str
    kind: str                                     # A | B | C | S
    units: list[str] = field(default_factory=list)          # uids
    pages: list[dict] = field(default_factory=list)         # see _page()
    notes: list[str] = field(default_factory=list)
    bundle: Path | None = None
    artifacts: Path | None = None
    manifest: dict = field(default_factory=dict)
    nonce: str = ""

    @property
    def pdf_page_count(self) -> int:
        """What the page limit counts: the pages the judge turns."""
        return len({(_src(p), i) for p in self.pages for i in p["pdf_pages"]})

    def brief(self) -> str:
        books = sorted({p["book"] for p in self.pages}, key=_book_rank)
        return (f"{self.call_id} [{self.kind}] {len(self.units)} units · "
                f"{len(self.pages)} printed / {self.pdf_page_count} pdf pages · "
                + ", ".join(books))


@dataclass
class Manifest:
    """Everything the verdict needs to say which bytes were judged (§5.10)."""
    call_id: str
    kind: str
    units: list[str]
    books: dict                      # key -> {file, sha256}
    pages: list[dict]                # bundle_page -> book/printed/pdf/reason/code
    index_pages: int
    page_count: int
    bytes: int
    sha256: str
    path: str
    stamps_verified: bool
    nonce: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"call_id": self.call_id, "kind": self.kind, "units": list(self.units),
                "books": self.books, "pages": self.pages,
                "index_pages": self.index_pages, "page_count": self.page_count,
                "bytes": self.bytes, "sha256": self.sha256, "path": self.path,
                "stamps_verified": self.stamps_verified, "nonce": self.nonce,
                "notes": list(self.notes)}


def _book_rank(key: str) -> int:
    return _BOOK_RANK.get(key, len(_BOOK_RANK))


def _page(book: str, printed: int, pdf_pages, reason: str, **extra) -> dict:
    return {"book": book, "printed": int(printed),
            "pdf_pages": [int(i) for i in pdf_pages], "reason": reason,
            "reasons": [reason], **extra}


def _src(page: dict) -> str:
    """The FILE a page lives in, which is what a page number is relative to.

    For a book that is its key. The Synopsis is not one file: it is judged from
    per-chapter slices, each numbered from 1 and each its own PDF, so two
    slices of a two-slice chapter (14, 56, 139-141) both hold a "page 1" and a
    "pdf page 0". Keying those on the book alone collapsed the later slice onto
    the earlier one in _merge, in _cost and in the bundle's own row table, and
    the survivors carried the WRONG slice_file — so the judge's quote was
    checked against a different chapter's page. `page["book"]` is untouched:
    everything that DISPLAYS a page ("Synopsis ch.N p.M", the manifest, the
    stamp) still reads "Synopsis".
    """
    return str(page.get("slice_file") or page["book"])


def _key(page: dict) -> tuple:
    """The identity of a printed page: which file, and where in it."""
    return (_src(page), page["printed"])


def _merge(dest: dict[tuple, dict], page: dict) -> None:
    """One entry per (file, printed); the strongest reason wins, all are kept."""
    key = _key(page)
    old = dest.get(key)
    if old is None:
        # The reasons list is copied on the way in. Callers pass `dict(v)`, a
        # shallow copy that still shares this list, and the append below would
        # then write a second unit's reason onto the first unit's page — so a
        # unit judged alone would carry reasons for pages nobody asked for.
        dest[key] = {**page, "reasons": list(page["reasons"])}
        return
    for r in page["reasons"]:
        if r not in old["reasons"]:
            old["reasons"].append(r)
    old["reasons"].sort(key=lambda r: REASON_RANK.get(r, 9))
    old["reason"] = old["reasons"][0]


def _sorted_pages(pages) -> list[dict]:
    return sorted(pages, key=lambda p: (_book_rank(p["book"]), _src(p),
                                        p["printed"], p["pdf_pages"][:1]))


# ---------------------------------------------------------------------------
# reason: cited


def cited_pages(cites: list[dict]) -> dict[tuple, dict]:
    """Every cited printed page +-CITE_RADIUS that the book actually has.

    A page past the end of the book is qc_lint's finding, not a bundle entry;
    silently including it would crash the merge and loudly including it would
    duplicate a check the lint already reports.

    A citation qc_cite could not pin to one Stahl book ('Stahl?') contributes
    the page from BOTH Stahl books. The judge is the one who can tell from the
    content which book the material meant, and it can only tell if it sees both.
    """
    out: dict[tuple, dict] = {}
    for c in cites or []:
        printed = c.get("printed")
        if printed is None:
            continue                     # a section reference (Synopsis ch.9)
        keys = (("StahlNeuro", "StahlKids") if c.get("book") == "Stahl?"
                else (c.get("book"),))
        for key in keys:
            if key not in qc_books.SPECS:
                continue                 # Synopsis has no printed pages at all
            book = qc_books.book(key)
            for n in range(printed - CITE_RADIUS, printed + CITE_RADIUS + 1):
                if n >= 1 and book.has_page(n):
                    _merge(out, _page(key, n, book.pdf_pages(n), "cited"))
    return out


# ---------------------------------------------------------------------------
# reason: search


def search_pages(text: str, cites: list[dict], *,
                 per_value: int = MAX_SEARCH_PAGES_PER_VALUE,
                 per_unit: int = MAX_SEARCH_PAGES_PER_UNIT,
                 notes: list | None = None) -> dict[tuple, dict]:
    """Pages where this unit's locked values occur beside one of its key terms.

    The citations are blanked first. Without that, the coverage table at the
    top of every guide — a book name as the column header and `35, 68, 74` in
    the cell — hands three page numbers to the value extractor, and the bundle
    fills with whatever pages happen to carry the number 68.

    Ranked by how many distinct key terms the page names, then by distance from
    a cited page, then by page number: the page that is about the same claim
    wins, and the ordering is total, so two runs plan the same bundle.
    """
    terms = qc_lint.key_terms(text)
    if not terms:
        # Nothing on the unit names a drug, trial or scale, so a page carrying
        # the same number has not been shown to be about the same claim. §5.7
        # is explicit that a bare value match is not enough.
        return {}
    term_re = re.compile("|".join(re.escape(t) for t in terms), re.I)
    want = min(MIN_TERM_HITS, len(terms))
    cited = {(c.get("book"), c.get("printed")) for c in cites or []}
    near = {b: sorted(p for bb, p in cited if bb == b and p) for b in qc_books.SPECS}

    out: dict[tuple, dict] = {}
    for value in qc_norm.locked_values(qc_lint._blank_citations(text, cites or [])):
        token = qc_lint._value_token(value["raw"])
        if token is None:
            continue                     # a spelled-out count: no digits to find
        hits: list[tuple] = []
        for key in qc_books.SPECS:
            book = qc_books.book(key)
            _, inverted = qc_lint._book_numbers(key)
            pages = inverted.get(token, [])
            if len(pages) > qc_lint.MAX_VALUE_PAGES:
                # "3" is on nearly every page of the DSM. A value that common
                # cannot single out a page, and scanning 800 of them for a key
                # term costs seconds to return noise.
                if notes is not None:
                    notes.append(f"search: {value['raw'].strip()!r} skipped in "
                                 f"{key} ({len(pages)} pages carry it)")
                continue
            for n in pages:
                page_text = book.page_text(n) or ""
                score = len({m.group(0).lower()
                             for m in term_re.finditer(page_text)})
                if score < want:
                    continue
                gap = min((abs(n - p) for p in near.get(key, ())), default=10_000)
                hits.append((-score, gap, _book_rank(key), n, key))
        hits.sort()
        if len(hits) > per_value and notes is not None:
            notes.append(f"search: {value['raw'].strip()!r} kept {per_value} of "
                         f"{len(hits)} key-term pages")
        for _, _, _, n, key in hits[:per_value]:
            _merge(out, _page(key, n, qc_books.book(key).pdf_pages(n), "search"))
            if len(out) >= per_unit:
                break
        if len(out) >= per_unit:
            if notes is not None:
                notes.append(f"search: unit capped at {per_unit} pages")
            break
    return out


# ---------------------------------------------------------------------------
# reason: guide


# "עמ' 3-16, 59-90" and "(עמ' 69-96, 141-154, 243-254)". The list after the
# page word is comma-separated; the first item may follow it directly.
_PAGE_WORD = re.compile(r"עמ['׳’]?")
_FIRST_RANGE = re.compile(r"[ \t]*(\d{1,4})(?:[ \t]*[-–—־][ \t]*(\d{1,4}))?")
_NEXT_RANGE = re.compile(r"[ \t]*,[ \t]*(\d{1,4})(?:[ \t]*[-–—־][ \t]*(\d{1,4}))?")


def reading_ranges(text: str) -> list[tuple[int, int]]:
    """The (lo, hi) printed ranges in one reading-guide entry.

    The entries are Hebrew free text written for a human — "פרק 1+4+5, עמ' 3-16,
    59-90" — so only the run of numbers that follows a page word counts. The
    chapter numbers before it are not pages: chapter 4 of Dulcan is not page 4.
    """
    out: list[tuple[int, int]] = []
    for m in _PAGE_WORD.finditer(text or ""):
        i, rx = m.end(), _FIRST_RANGE
        while True:
            mm = rx.match(text, i)
            if not mm:
                break
            lo = int(mm.group(1))
            hi = int(mm.group(2)) if mm.group(2) else lo
            if hi < lo:
                lo, hi = hi, lo
            out.append((lo, hi))
            i, rx = mm.end(), _NEXT_RANGE
    return out


def guide_pages(day: int, *, path: Path | None = None,
                notes: list | None = None) -> dict[tuple, dict]:
    """The chapter's reading-guide ranges, as pages.

    Book names in reading_guide.json are the ones a person writes — 'Stahl
    Essential', 'Stahl C&A', 'כרטיסיות' — and qc_books.normalize_label is the
    one place that knows them. Entries that name no book ('note', 'חוקים') and
    the Synopsis (no printed pages) drop out here; the Synopsis comes back as
    slices.
    """
    src = Path(path) if path else READING_GUIDE
    if not src.exists():
        if notes is not None:
            notes.append(f"guide: {src.name} missing — no reading-guide pages")
        return {}
    data = json.loads(src.read_text(encoding="utf-8"))
    entry = (data.get("chapters") or {}).get(str(int(day))) or {}
    out: dict[tuple, dict] = {}
    for raw_name, text in (entry.get("reading") or {}).items():
        key = qc_books.normalize_label(raw_name)
        if key is None or key not in qc_books.SPECS:
            continue                      # 'note', 'חוקים', 'Synopsis', 'Stahl?'
        book = qc_books.book(key)
        for lo, hi in reading_ranges(text):
            if hi - lo + 1 > MAX_GUIDE_RANGE:
                if notes is not None:
                    notes.append(f"guide: {key} {lo}-{hi} ignored "
                                 f"({hi - lo + 1} pages — a typo, not a range)")
                continue
            for n in range(lo, hi + 1):
                if book.has_page(n):
                    _merge(out, _page(key, n, book.pdf_pages(n), "guide"))
    return out


# ---------------------------------------------------------------------------
# reason: synopsis


def synopsis_pages(day: int, *, notes: list | None = None) -> dict[tuple, dict]:
    """The chapter's Synopsis slices, one entry per slice page.

    The Synopsis has no printed page numbers in this edition, so `printed` here
    is the page's position IN ITS SLICE and the stamp says so. Every slice
    therefore numbers from 1, and a chapter with two of them (14, 56, 139-141)
    holds two page 1s — which is why the entry carries `slice_file` and why
    `_key`/`_src` key a page on the file it lives in and not on the book.
    daily_study owns the curriculum -> slice map (the examiners' P024-2026
    list); reading the manifest again here is how the notebook and the bundle
    would come to disagree about which chapter needs which slice.
    """
    from daily_study import synopsis_slices_for      # local: keeps plan() cheap

    out: dict[tuple, dict] = {}
    for path, ref, _pages in synopsis_slices_for(int(day)):
        try:
            import fitz
            with fitz.open(path) as doc:
                count = doc.page_count
        except Exception as exc:                     # noqa: BLE001
            if notes is not None:
                notes.append(f"synopsis: {Path(path).name} unreadable ({exc})")
            continue
        for i in range(count):
            _merge(out, _page("Synopsis", i + 1, [i], "synopsis",
                              slice_file=Path(path).name, slice_ref=str(ref),
                              slice_path=str(path)))
    return out


# ---------------------------------------------------------------------------
# splitting


def _kind_of(unit) -> str:
    kind = KIND_BY_ARTIFACT.get(unit.artifact)
    if kind:
        return kind
    if qc_units.LEGEND_RE.match(unit.artifact or ""):
        return KIND_LEGEND
    raise PlanError(f"{unit.uid}: no judge call for artifact {unit.artifact!r}. "
                    f"qc_units let it through, so KIND_BY_ARTIFACT is missing a "
                    f"name — fail closed rather than judge it as prose.")


def _primary_book(pages: dict) -> str:
    """The book a unit leans on, for the 'split first by book' pass."""
    counts: dict[str, int] = {}
    for (book, _), page in pages.items():
        if page["reason"] == "cited":
            counts[book] = counts.get(book, 0) + 1
    if not counts:
        return "~"                        # sorts after every real book key
    return sorted(counts.items(), key=lambda kv: (-kv[1], _book_rank(kv[0])))[0][0]


def _cost(pages: dict) -> int:
    return len({(_src(p), i) for p in pages.values() for i in p["pdf_pages"]})


def _pack(uids: list[str], need: dict[str, dict], page_limit: int,
          notes: list, base: dict | None = None) -> list[list[str]]:
    """Unit groups that each fit the limit, keeping every unit with its pages.

    §5.7 splits "first by book, then by unit ranges". Both happen here in one
    ordering: units are visited grouped by the book they mostly cite, and a
    group is closed when the next unit would push its OWN evidence past the
    limit. A unit whose pages alone exceed the limit gets a call to itself and
    is logged — the alternative is to drop one of its pages, and a unit judged
    without a page it cites comes back `unsupported`.

    `base` is what every group of this kind carries whatever its units are: the
    Synopsis slices of an S call, which have no page numbers to cite, so a unit
    judged against them needs all of them.
    """
    if not uids:
        return []
    base = base or {}

    def fresh() -> dict:
        out: dict[tuple, dict] = {}
        for v in base.values():
            _merge(out, dict(v))
        return out

    order = sorted(uids, key=lambda u: (_primary_book(need[u]), u))
    groups: list[list[str]] = []
    current: list[str] = []
    carried = fresh()
    for uid in order:
        alone = _cost({**base, **need[uid]})
        if alone > page_limit:
            if current:
                groups.append(current)
                current, carried = [], fresh()
            groups.append([uid])
            notes.append(f"{uid}: {alone} pdf pages of its own — over the "
                         f"{page_limit}-page limit, judged in a call of its own")
            continue
        merged = dict(carried)
        for k, v in need[uid].items():
            _merge(merged, dict(v))
        if current and _cost(merged) > page_limit:
            groups.append(current)
            current, carried = [uid], fresh()
            for k, v in need[uid].items():
                _merge(carried, dict(v))
        else:
            current, carried = current + [uid], merged
    if current:
        groups.append(current)
    return groups


def _distribute(calls: list[CallPlan], context: dict[tuple, dict],
                page_limit: int) -> None:
    """Spread the chapter's context pages over its calls, each page once.

    Preference goes to a call that already carries the same book, so a bundle
    stays a stack of photocopies from a handful of books rather than a shuffle.
    When nothing has room the page still goes in — §5.7's "never drop pages" is
    not negotiable, the limit is a calibration number — and the call records
    that it ran over.
    """
    if not calls or not context:
        return
    by_id = {c.call_id: c for c in calls}
    # The cost of a page is the PDF pages it would ADD, not the PDF pages it
    # spans. In DSM-5-TR and Dulcan two adjacent printed pages routinely share
    # a PDF page, so a reading range of 34 printed pages next to a cited one
    # costs less than 34 — counting the spans would fake an overflow and split
    # the chapter into a call more than it needs.
    have_pdf = {c.call_id: {(_src(p), i) for p in c.pages for i in p["pdf_pages"]}
                for c in calls}
    have_book = {c.call_id: {p["book"] for p in c.pages} for c in calls}
    over: set[str] = set()
    # Sorted on the PAGE, not on its key: the key's first element is the file a
    # Synopsis page lives in, and _book_rank knows book keys, not slice names.
    for key in sorted(context, key=lambda k: (_book_rank(context[k]["book"]),
                                              _src(context[k]),
                                              context[k]["printed"])):
        page = context[key]
        book = page["book"]
        costs = {c.call_id: len({(_src(page), i) for i in page["pdf_pages"]}
                                - have_pdf[c.call_id]) for c in calls}
        room = {c.call_id: page_limit - len(have_pdf[c.call_id]) for c in calls}
        fits = [c for c in calls if room[c.call_id] >= costs[c.call_id]]
        if fits:
            pick = min(fits, key=lambda c: (costs[c.call_id] > 0,
                                            book not in have_book[c.call_id],
                                            -room[c.call_id], c.call_id))
        else:
            pick = max(calls, key=lambda c: (room[c.call_id], c.call_id))
            if pick.call_id not in over:
                over.add(pick.call_id)
                pick.notes.append(
                    f"over the {page_limit}-page limit: context pages did not "
                    f"fit anywhere and are carried here rather than dropped")
        by_id[pick.call_id].pages.append({**page, "reasons": list(page["reasons"])})
        have_book[pick.call_id].add(book)
        have_pdf[pick.call_id] |= {(_src(page), i) for i in page["pdf_pages"]}
    for c in calls:
        c.pages = _sorted_pages(c.pages)


def _absorb_pageless(calls: list[CallPlan], need: dict[str, dict],
                     notes: list) -> list[CallPlan]:
    """No call reaches the judge without a source, and no unit is dropped.

    A call ends up with nothing when every unit in it cites no printed page and
    the full-text search found none either — and the chapter's context pages,
    which are what such a call would otherwise live on, all landed elsewhere.
    That is not a coincidence: `_distribute` spends a context page where it
    costs no extra PDF page, and a call holding nothing can never be that call.

    070 is the case §6 names. It is Synopsis-heavy, so 77 of its 89 units go to
    the S calls with the slices; what is left in call B is `G:_intro`, the study
    guide's title line, which cites nothing. Its two Kartisiot context pages go
    to a call that already carries them, B1 ends up empty, and `build_bundle`
    refuses the whole chapter with "B1: no pages" — so 070 could not be planned
    at all. §5.7 splits a chapter across calls; it never drops one.

    So those units move into a call that does have pages, and the empty call
    goes away. It costs nothing — a unit with no pages of its own adds no PDF
    page to its host — and it loses nothing: §5.7's promise is that "a unit is
    judged in the call that holds its cited pages and its search hits", and a
    unit with neither has nothing to be separated from. That is asserted here
    rather than assumed, because the one way this could go wrong is a unit
    arriving with evidence the call does not carry.

    The host is a call of the same kind if the chapter has one, then the
    Synopsis call — for a chapter whose only textbook is the Synopsis (013,
    070, §5.8) the slices ARE the source such a unit would be read against —
    then the smallest call. The judge's checklists key off the unit id, not the
    call kind (§5.8), so a guide section keeps being judged as a guide section
    wherever it is read.
    """
    empty = [c for c in calls if not c.pages]
    if not empty:
        return calls
    hosts = [c for c in calls if c.pages]
    if not hosts:
        # Nothing in this chapter has a source at all. build_bundle raises on
        # that, and it is right to: there is nothing to judge against, and a
        # judge with no bundle answers anyway (module docstring).
        return calls
    for c in empty:
        for uid in c.units:
            if need.get(uid):
                raise PlanError(
                    f"{c.call_id}: unit {uid} has {len(need[uid])} pages of "
                    f"its own evidence and the call carries none. The plan and "
                    f"the pages disagree, so the split is wrong — stop rather "
                    f"than judge {uid} against pages it never cited.")
        host = min(hosts, key=lambda h: (h.kind != c.kind,
                                         h.kind != KIND_SYNOPSIS,
                                         h.pdf_page_count, h.call_id))
        host.units.extend(c.units)
        shown = ", ".join(c.units[:3]) + ("..." if len(c.units) > 3 else "")
        host.notes.append(
            f"{len(c.units)} unit(s) from {c.call_id} ({shown}) are judged "
            f"here: they cite no page and the search found none, and "
            f"{c.call_id} would have gone to the judge with an empty bundle")
        notes.append(f"{c.call_id}: {len(c.units)} unit(s) with no source of "
                     f"their own moved into {host.call_id}")
    return [c for c in calls if c.pages]


def _synopsis_heavy(cites_by_unit: dict) -> bool:
    books, synopsis = 0, 0
    for rows in (cites_by_unit or {}).values():
        for c in rows:
            if not c.get("book"):
                continue
            books += 1
            synopsis += c["book"] == "Synopsis"
    if not books:
        return False
    return synopsis / books >= SYNOPSIS_HEAVY_SHARE or synopsis == books


def plan(tag: str, day: int, units, cites, *, page_limit: int = DEFAULT_PAGE_LIMIT,
         reading_guide: Path | None = None, search: bool = True,
         notes: list | None = None) -> list[CallPlan]:
    """The chapter's judge calls, with every page each one carries.

    `units` is a qc_units.Unit list, `cites` is qc_cite.cites_by_unit(units).
    Nothing is built here and nothing touches agy: a plan is cheap enough to
    print, diff and argue with before 25 minutes of judge time is spent on it.

    `notes` collects the chapter-level remarks — a value too common to search,
    a search cap that bit, a missing reading guide. They are the caller's to
    print ONCE; what lands on a CallPlan is only what is true of that call.
    """
    units = list(units)
    cites = dict(cites or {})
    notes = notes if notes is not None else []
    # qc_run's --page-limit defaults to None (calibration has not picked one of
    # §5.7's 120/180/250 yet), and `None` compared against a page count raises.
    page_limit = int(page_limit or DEFAULT_PAGE_LIMIT)

    need: dict[str, dict] = {}
    for u in units:
        rows = cites.get(u.uid, [])
        pages = cited_pages(rows)
        if search:
            for k, v in search_pages(u.text, rows, notes=notes).items():
                _merge(pages, v)
        need[u.uid] = pages

    context: dict[tuple, dict] = {}
    for k, v in guide_pages(day, path=reading_guide, notes=notes).items():
        _merge(context, v)
    synopsis = synopsis_pages(day, notes=notes)

    # §5.8: a Synopsis-heavy chapter judges the Synopsis in its own call, so the
    # units that cite it move there with their own evidence. Without slices on
    # disk there is nothing to judge against, so the chapter stays ordinary and
    # says so — a silent empty S call would fail its canary and cost a retry.
    heavy = bool(synopsis) and _synopsis_heavy(cites)
    syn_units = {u.uid for u in units
                 if any(c.get("book") == "Synopsis" for c in cites.get(u.uid, []))}
    if heavy and syn_units:
        notes.append(f"Synopsis-heavy chapter: {len(syn_units)} units judged in "
                     f"their own call against the slices")
    else:
        if heavy:
            notes.append("Synopsis-heavy by citations but no unit cites it — "
                         "slices carried as context")
        syn_units = set()
        for k, v in synopsis.items():
            _merge(context, v)

    by_kind: dict[str, list[str]] = {}
    for u in units:
        kind = KIND_SYNOPSIS if u.uid in syn_units else _kind_of(u)
        by_kind.setdefault(kind, []).append(u.uid)

    def assemble(budget: int) -> list[CallPlan]:
        calls: list[CallPlan] = []
        for kind in ("A", "B", KIND_LEGEND, KIND_SYNOPSIS):
            uids = by_kind.get(kind)
            if not uids:
                continue
            kind_notes: list[str] = []
            # The S call's slices are not evidence for one unit: the Synopsis is
            # cited by chapter, never by page, so every S call carries all of
            # them and they do not split.
            base = synopsis if kind == KIND_SYNOPSIS else {}
            total: dict[tuple, dict] = {}
            for uid in uids:
                for k, v in need[uid].items():
                    _merge(total, dict(v))
            for k, v in base.items():
                _merge(total, dict(v))
            # One call while everything fits: splitting reorders units by book,
            # and a judge reading Q1..Q10 in order is easier to argue with than
            # one reading them in citation order.
            if _cost(total) <= budget:
                groups = [list(uids)]
            elif _cost(base) >= budget:
                # synopsis-1_1-adult-patient.pdf is 215 pages on its own.
                # Splitting the units would produce a call per unit, each still
                # carrying all 215 — the same pages, N times, and N times the
                # agy time. One call, over budget, and say so.
                groups = [list(uids)]
                kind_notes.append(
                    f"{kind}: the Synopsis slices are {_cost(base)} pdf pages on "
                    f"their own, past the {budget}-page budget — one call, "
                    f"because splitting cannot make a slice smaller")
            else:
                groups = _pack(list(uids), need, budget, kind_notes, base=base)
            for i, group in enumerate(groups, 1):
                pages: dict[tuple, dict] = {}
                for uid in group:
                    for k, v in need[uid].items():
                        _merge(pages, dict(v))
                if kind == KIND_SYNOPSIS:
                    for k, v in synopsis.items():
                        _merge(pages, dict(v))
                calls.append(CallPlan(call_id=f"{kind}{i}", kind=kind,
                                      units=list(group),
                                      pages=_sorted_pages(pages.values()),
                                      notes=list(kind_notes)))
            if len(groups) > 1:
                kind_notes.append(f"{kind}: {len(uids)} units in {len(groups)} "
                                  f"calls at a {budget}-page unit budget")
                for c in calls[-len(groups):]:
                    c.notes = list(kind_notes)
        if calls:
            placed = {_key(p) for c in calls for p in c.pages}
            _distribute(calls,
                        {k: v for k, v in context.items() if k not in placed},
                        page_limit)
        return calls

    # The units of a chapter can fit the limit while the chapter does not: 019's
    # quiz and cards need 110 pdf pages of their own evidence and the reading
    # guide adds 137 printed pages of context on top. So the budget the unit
    # packer works to is squeezed until no call is over — each squeeze buys one
    # more call and therefore one more call's worth of room for the context.
    # It stops when the only calls still over are over on their OWN evidence,
    # because no amount of splitting shrinks a single unit's cited pages.
    budget = page_limit
    calls = assemble(budget)
    for _ in range(SPLIT_ATTEMPTS):
        over = [c for c in calls if c.pdf_page_count > page_limit]
        if not over or all(_unit_cost(c, need, synopsis) >= page_limit
                           for c in over):
            break
        nxt = max(MIN_UNIT_BUDGET, budget * 3 // 4)
        if nxt == budget:
            break
        budget = nxt
        calls = assemble(budget)

    calls = _absorb_pageless(calls, need, notes)

    for c in calls:
        c.notes = list(dict.fromkeys(c.notes))

    # §5.7, fail closed: a chapter is split across calls, never dropped. The
    # squeeze loop and the absorb pass both move units between calls, and a
    # unit that fell out of the plan is a unit that is never judged and never
    # reported as unjudged either — the one failure nothing downstream can see.
    planned = [u for c in calls for u in c.units]
    want = {u.uid for u in units}
    if len(planned) != len(set(planned)):
        dupes = sorted({u for u in planned if planned.count(u) > 1})
        raise PlanError(f"{tag}: {dupes[:6]} planned into more than one call; "
                        f"the judge would rule on them twice.")
    if set(planned) != want:
        missing = sorted(want - set(planned))
        unknown = sorted(set(planned) - want)
        raise PlanError(
            f"{tag}: the plan covers {len(set(planned))} of {len(want)} units. "
            f"missing={missing[:6]} unknown={unknown[:6]}")
    return calls


def _unit_cost(call: CallPlan, need: dict[str, dict], synopsis: dict) -> int:
    """What this call must carry however the chapter is split: its units' own
    evidence, plus the Synopsis slices when it is the Synopsis call. The squeeze
    loop stops when a call is over the limit on this alone."""
    pages: dict[tuple, dict] = {}
    for uid in call.units:
        for k, v in need.get(uid, {}).items():
            _merge(pages, dict(v))
    if call.kind == KIND_SYNOPSIS:
        for v in synopsis.values():
            _merge(pages, dict(v))
    return _cost(pages)


# ---------------------------------------------------------------------------
# artifacts.md


def _code(seen: set[str], rng) -> str:
    while True:
        code = "".join(rng.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        if code not in seen:
            seen.add(code)
            return code


def build_artifacts_md(units, workdir, *, rng=None) -> tuple[Path, str]:
    """`### <uid>` + the unit's text verbatim, plus one nonce line near the end.

    Verbatim is the contract: §5.8's coverage check searches `Unit.text` for the
    echo the judge returns, so a block that re-wraps, re-renders or trims the
    text fails every row of its own call. That is also why the nonce goes
    BETWEEN blocks and never inside one — a line dropped into the middle of Q7
    would break any echo that spans it, and the judge would be punished for the
    file we wrote.
    """
    rng = rng or secrets.SystemRandom()
    out = Path(workdir)
    out.mkdir(parents=True, exist_ok=True)
    nonce = "".join(rng.choice(CODE_ALPHABET) for _ in range(NONCE_LEN))

    # Verbatim means verbatim, down to a trailing space: §5.8 searches Unit.text
    # for the judge's echo, and an rstrip() here made `assertIn(u.text, body)`
    # fail on the first unit whose rendering ends in one. Only a missing final
    # newline is added, so the next `###` starts its own line.
    blocks = [f"### {u.uid}\n\n{u.text}" + ("" if u.text.endswith("\n") else "\n")
              for u in units]
    if not blocks:
        raise PlanError("artifacts.md would be empty: a call with no units is a "
                        "judge call with nothing to judge")
    line = f"{NONCE_MARKER} {nonce}"
    # Candidate slots are the block boundaries that land inside the last tenth
    # of the FINISHED file, so the threshold already counts the nonce line
    # itself. Slot len(blocks) — the very end — always qualifies, so there is
    # always somewhere to put it even in a one-unit call.
    joined = "\n".join(blocks)
    cut = (len(joined) + len(line) + 1) * (1 - NONCE_TAIL_FRACTION)
    slots, pos = [], 0
    for i, b in enumerate(blocks, 1):
        pos += len(b) + 1                 # the block plus its joining newline
        if pos >= cut:
            slots.append(i)
    slot = rng.choice(slots) if slots else len(blocks)
    body = "\n".join(blocks[:slot] + [line] + blocks[slot:])

    path = out / "artifacts.md"
    # write_bytes, never write_text: on Windows write_text turns every "\n"
    # into "\r\n", and the file the judge reads would not be the file whose
    # sha256 the verdict records.
    path.write_bytes(body.encode("utf-8"))
    return path, nonce


# ---------------------------------------------------------------------------
# bundle.pdf


def _stamp_text(page: dict, code: str) -> str:
    """'<book> p.<printed pages> · <code>' — §5.7's margin stamp.

    Kartisiot is stamped with its Hebrew name because §2's owner decision says
    anything grounded in it must say so by name, and the judge can only name it
    if the page says it. The Synopsis names its slice: it has no printed pages,
    so "p.12" alone would invite a citation that cannot exist.
    """
    book = page["book"]
    if book == "Synopsis":
        return (f"Synopsis ch.{page.get('slice_ref') or '?'} "
                f"p.{page['printed']} \u00b7 {code}")
    return f"{qc_books.HEBREW_NAME.get(book, book)} p.{page['printed']} \u00b7 {code}"


def _index_rows(pages: list[dict], first_content_page: int) -> list[str]:
    rows = []
    for i, p in enumerate(pages):
        where = (f"Synopsis ch.{p.get('slice_ref', '?')} p.{p['printed']}"
                 if p["book"] == "Synopsis"
                 else f"{p['book']} p.{p['printed']}")
        rows.append(f"{first_content_page + i}. {where} [{p['reason']}]")
    return rows


# Measured on the probe bundle: 3 columns of 7px Arial hold ~60 rows each on a
# letter page with room for the heading. 150 leaves slack for a long book name.
INDEX_ROWS_PER_PAGE = 150

_STAMP_CSS = "@font-face {font-family: stampfont; src: url(stampfont.ttf);}"


def _font_file() -> Path:
    """A font with Hebrew glyphs. Arial ships with Windows; this repo runs on
    Windows and the stamp is the one place a missing glyph is invisible until a
    canary fails, so a missing font is an error, not a fallback."""
    for candidate in (Path(r"C:\Windows\Fonts\arial.ttf"),
                      Path(r"C:\Windows\Fonts\tahoma.ttf"),
                      Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
        if candidate.exists():
            return candidate
    raise PlanError("no font with Hebrew glyphs found for the page stamps "
                    "(looked for Arial and Tahoma)")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _html(inner: str, size: int = 8, align: str = "left") -> str:
    """`inner` is already HTML: the callers escape their own text and then join
    the index rows with <br>. Escaping here instead printed a literal "<br>"
    between every index row in the first bundle built."""
    return (f'<div style="font-family:stampfont;font-size:{size}px;'
            f'text-align:{align}">{inner}</div>')


def build_bundle(plan: CallPlan, workdir, *, rng=None) -> Manifest:
    """One bundle.pdf for one call: index pages, then the stamped source pages.

    Written with fitz because the pages have to arrive as PAGES — a judge asked
    for "the code on the page carrying this quote" cannot answer from a text
    dump, and the whole canary rests on the stamp being where the page is.

    Everything is verified before the manifest is returned: the file is reopened
    and every stamp read back out of it. A bundle whose stamps did not render
    fails every canary, and it would fail them 25 minutes and one agy call
    later, on a chapter that would then be marked `unjudged` for no reason.
    """
    import fitz                                   # lazy: plan() needs no PDFs

    rng = rng or secrets.SystemRandom()
    out = Path(workdir)
    out.mkdir(parents=True, exist_ok=True)
    pages = _sorted_pages(plan.pages)
    if not pages:
        raise PlanError(f"{plan.call_id}: no pages. A bundle with no source is "
                        f"the failure this module exists to prevent.")

    # One row per (book, pdf page): a reflowed printed page spans several PDF
    # pages and two adjacent printed pages share one, so the same PDF page must
    # not be copied twice — and the row has to name every printed page it
    # carries, or the index would call p.331 p.330.
    rows: list[dict] = []
    seen: dict[tuple, dict] = {}
    for p in pages:
        for i in p["pdf_pages"]:
            key = (_src(p), i)
            row = seen.get(key)
            if row is None:
                row = {"book": p["book"], "pdf_page": i, "printed": [],
                       "reasons": [], "slice_ref": p.get("slice_ref"),
                       "slice_path": p.get("slice_path"),
                       "slice_file": p.get("slice_file")}
                seen[key] = row
                rows.append(row)
            if p["printed"] not in row["printed"]:
                row["printed"].append(p["printed"])
            for r in p["reasons"]:
                if r not in row["reasons"]:
                    row["reasons"].append(r)
    for row in rows:
        row["printed"].sort()
        row["reasons"].sort(key=lambda r: REASON_RANK.get(r, 9))
        row["reason"] = row["reasons"][0]

    n_index = max(1, -(-len(rows) // INDEX_ROWS_PER_PAGE))
    index_rows = _index_rows(
        [{"book": r["book"], "printed": r["printed"][0], "reason": r["reason"],
          "slice_ref": r["slice_ref"]} for r in rows], n_index + 1)

    doc = fitz.open()
    srcs: dict[str, object] = {}
    books: dict[str, dict] = {}
    try:
        for _ in range(n_index):
            doc.new_page()
        for row in rows:
            if row["book"] == "Synopsis":
                if not row.get("slice_path"):
                    raise PlanError(
                        f"{plan.call_id}: a Synopsis page with no slice file. "
                        f"The Synopsis has no printed pages, so a page of it "
                        f"exists only inside a slice PDF.")
                path = Path(row["slice_path"])
                # The slices are sources too, and §5.10 wants the sha256 of
                # every file the verdict rests on — a regenerated slice is a
                # different book as far as a page number is concerned.
                books.setdefault(f"Synopsis:{path.name}",
                                 {"file": path.name, "sha256": _sha256_file(path)})
            else:
                path = qc_books.book_file(row["book"])
                books.setdefault(row["book"],
                                 {"file": path.name, "sha256": _sha256_file(path)})
            src = srcs.get(str(path))
            if src is None:
                src = srcs[str(path)] = fitz.open(path)
            doc.insert_pdf(src, from_page=row["pdf_page"], to_page=row["pdf_page"])
        if len(doc) != n_index + len(rows):
            raise PlanError(f"{plan.call_id}: bundle has {len(doc)} pages, "
                            f"planned {n_index + len(rows)}")

        font = _font_file()
        archive = fitz.Archive()
        archive.add(str(font), "stampfont.ttf")
        for i in range(n_index):
            page = doc[i]
            chunk = index_rows[i * INDEX_ROWS_PER_PAGE:(i + 1) * INDEX_ROWS_PER_PAGE]
            head = (f"QC bundle {plan.call_id} ({plan.kind}) — "
                    f"{len(rows)} source pages, index {i + 1}/{n_index}")
            page.insert_htmlbox(fitz.Rect(36, 28, page.rect.width - 36, 60),
                                _html(_esc(head), size=11), css=_STAMP_CSS,
                                archive=archive)
            # Three columns, and NO codes: the codes live on the pages and in
            # the manifest only. An index that listed them would let a judge
            # answer the canary from page 1 without opening a source page.
            per_col = -(-len(chunk) // 3)
            for col in range(3):
                part = chunk[col * per_col:(col + 1) * per_col]
                if not part:
                    continue
                width = (page.rect.width - 72) / 3
                rect = fitz.Rect(36 + col * width, 66,
                                 36 + (col + 1) * width - 6, page.rect.height - 36)
                spare, _scale = page.insert_htmlbox(
                    rect, _html("<br>".join(_esc(r) for r in part), size=7),
                    css=_STAMP_CSS, archive=archive)
                # insert_htmlbox reports a negative spare height when the text
                # did not fit, and then silently drops the rest. An index that
                # is missing its last rows is an index the judge cannot use to
                # find the page a finding needs, so it is an error here rather
                # than a surprise in a verdict.
                if spare < 0:
                    raise PlanError(
                        f"{plan.call_id}: index page {i + 1} column {col + 1} "
                        f"overflowed with {len(part)} rows — lower "
                        f"INDEX_ROWS_PER_PAGE (now {INDEX_ROWS_PER_PAGE})")

        codes: set[str] = set()
        for n, row in enumerate(rows):
            page = doc[n_index + n]
            row["code"] = _code(codes, rng)
            row["bundle_page"] = n_index + n + 1
            stamp = _stamp_text(
                {"book": row["book"], "printed": "-".join(str(p) for p in row["printed"]),
                 "slice_ref": row["slice_ref"]}, row["code"])
            # Top margin, full width. A stamp in the body would cover text; a
            # stamp off the page would not be extractable at all.
            page.insert_htmlbox(fitz.Rect(8, 2, page.rect.width - 8, 24),
                                _html(_esc(stamp), size=8, align="right"),
                                css=_STAMP_CSS, archive=archive)

        path = out / "bundle.pdf"
        doc.subset_fonts()
        # garbage=4 + clean + subset_fonts: measured 35MB -> 1.1MB on a 48-page
        # probe. The same bytes, minus one embedded font copy per source page.
        doc.save(str(path), garbage=4, deflate=True, clean=True)
    finally:
        for src in srcs.values():
            src.close()
        doc.close()

    missing = _verify_stamps(path, rows, n_index)
    if missing:
        raise PlanError(
            f"{plan.call_id}: {len(missing)} stamps did not survive the save "
            f"(first: bundle page {missing[0]}). Every canary on this bundle "
            f"would fail, 25 minutes of agy time later.")

    data = path.read_bytes()
    return Manifest(
        call_id=plan.call_id, kind=plan.kind, units=list(plan.units),
        books=books,
        pages=[{"bundle_page": r["bundle_page"], "book": r["book"],
                "printed": r["printed"], "pdf_page": r["pdf_page"],
                "reason": r["reason"], "reasons": r["reasons"],
                "code": r["code"],
                **({"slice_file": r["slice_file"], "slice_ref": r["slice_ref"]}
                   if r["book"] == "Synopsis" else {})}
               for r in rows],
        index_pages=n_index, page_count=n_index + len(rows), bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(), path=str(path),
        stamps_verified=True, notes=list(plan.notes))


def _verify_stamps(path: Path, rows: list[dict], n_index: int) -> list[int]:
    import fitz
    missing = []
    with fitz.open(path) as doc:
        for n, row in enumerate(rows):
            if row["code"] not in doc[n_index + n].get_text():
                missing.append(n_index + n + 1)
    return missing


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# canary


def canary_request(manifest, k: int = CANARY_MIN, *, rng=None) -> list[tuple]:
    """(book, printed, expected_code) for the pages the judge must read back.

    §5.8: at least one page per book, and at least one in the last tenth of the
    bundle. The tail requirement is the one that matters — a model that skimmed
    the first pages and stopped can still answer a canary drawn from the front,
    and the whole point of the per-call page limit is that the tail is where
    reading breaks down.
    """
    rng = rng or secrets.SystemRandom()
    # Sorted, not trusted: "the last 10%" is a claim about where a page sits in
    # the bundle, and a manifest that came back through JSON in another order
    # would draw the tail canary from the middle.
    pages = sorted(_manifest_pages(manifest), key=lambda p: p["bundle_page"])
    if not pages:
        raise PlanError("canary on a bundle with no pages")

    tail_start = int(len(pages) * (1 - CANARY_TAIL_FRACTION))
    tail = pages[min(tail_start, len(pages) - 1):]
    by_book: dict[str, list] = {}
    for p in pages:
        by_book.setdefault(p["book"], []).append(p)

    picked: list[dict] = []
    seen: set[int] = set()

    def take(row):
        if row["bundle_page"] not in seen:
            seen.add(row["bundle_page"])
            picked.append(row)

    for book in sorted(by_book, key=_book_rank):
        take(rng.choice(by_book[book]))
    if not any(p["bundle_page"] >= tail[0]["bundle_page"] for p in picked):
        take(rng.choice(tail))
    rest = [p for p in pages if p["bundle_page"] not in seen]
    rng.shuffle(rest)
    for row in rest[:max(0, k - len(picked))]:
        take(row)

    picked.sort(key=lambda p: p["bundle_page"])
    return [(p["book"], p["printed"][0] if isinstance(p["printed"], list)
             else p["printed"], p["code"]) for p in picked]


def _manifest_pages(manifest):
    if isinstance(manifest, Manifest):
        return manifest.pages
    if isinstance(manifest, dict):
        return manifest.get("pages") or []
    return getattr(manifest, "pages", []) or []


# ---------------------------------------------------------------------------
# the entry point qc_run binds


_TAG_DAY = re.compile(r"study-(\d{1,3})-")


def day_of_tag(tag: str) -> int:
    """19 from `study-019-adhd-treatment-non-stimulants` (qc_store.day_tag)."""
    m = _TAG_DAY.match(tag or "")
    if not m:
        raise PlanError(f"cannot read a chapter number out of tag {tag!r}; "
                        f"pass day= explicitly")
    return int(m.group(1))


def build_calls(*, tag: str, units, cites=None, work_dir, day: int | None = None,
                page_limit: int | None = DEFAULT_PAGE_LIMIT, rng=None,
                search: bool = True, **_ignored) -> list[CallPlan]:
    """Plan the chapter and build every call's two files. qc_run's entry point.

    Keyword-only and tolerant of extra keywords on purpose: qc_run passes
    `assets` and `root` as well, and a signature that rejected them would be
    reported as a wiring bug in the middle of a 24-hour run.
    """
    units = list(units)
    cites = cites if cites is not None else qc_cite.cites_by_unit(units)
    by_uid = {u.uid: u for u in units}
    notes: list[str] = []
    calls = plan(tag, day if day is not None else day_of_tag(tag), units, cites,
                 page_limit=page_limit, search=search, notes=notes)
    for note in notes:
        print(f"  [i] {note}", flush=True)
    root = Path(work_dir)
    for call in calls:
        here = root / call.call_id
        here.mkdir(parents=True, exist_ok=True)
        artifacts, nonce = build_artifacts_md([by_uid[u] for u in call.units],
                                              here, rng=rng)
        manifest = build_bundle(call, here, rng=rng)
        manifest.nonce = nonce
        call.artifacts, call.bundle, call.nonce = artifacts, Path(manifest.path), nonce
        call.manifest = manifest.as_dict()
        (here / "manifest.json").write_bytes(
            json.dumps(call.manifest, ensure_ascii=False, indent=1).encode("utf-8"))
        print(f"  {call.brief()} · bundle {manifest.bytes / 1e6:.1f}MB "
              f"({manifest.page_count} pdf pages)", flush=True)
        for note in call.notes:
            print(f"    [!] {note}", flush=True)
    return calls


# ---------------------------------------------------------------------------
# §6's page-limit measurement: everything up to the agy call, and nothing more
#
# "The largest bundle whose last-10% canary passes", trying 120, 180 and 250
# (§6). The limit decides how many calls the backlog costs — measured on six
# released chapters, 250 instead of 180 takes 013 from 4 calls to 3, 019 from 8
# to 5, 029 from 6 to 3, 042 from 3 to 2, 047 from 4 to 3 and 070 from 7 to 5 —
# so it is worth an experiment, and it must not be worth a GUESS: a limit set too
# high means a judge that stopped reading a third of the way in and answered
# anyway, which is the 2026-09-18 failure with a bigger bundle.
#
# The experiment is one agy call per candidate limit, and the owner makes it.
# What is here is everything that costs nothing: the bundle at that size, three
# canary pages at 5%, 50% and 95% of it, the codes to expect, and the exact
# prompt that would be sent. `--check-answer` then takes the reply and says
# which of the three codes came back. The 95% one is the measurement; the other
# two are the control that tells "it did not read the tail" apart from "it did
# not read the bundle at all".
MEASURE_FRACTIONS = (0.05, 0.50, 0.95)
MEASURE_FILE = "measurement.json"


def _first_printed(page: dict) -> int:
    printed = page.get("printed")
    if isinstance(printed, (list, tuple)):
        return int(printed[0])
    return int(printed)


def measure_canary(manifest, *, fractions=MEASURE_FRACTIONS) -> list[dict]:
    """One page at each fraction of the way through the bundle, with its code.

    Distinct pages where the bundle has them: two fractions that round onto the
    same page would make a three-code canary a two-code one and the tail result
    would rest on a page the front canary had already proved.

    A fraction that has to move moves TOWARDS the middle — forward from the
    front, backward from the tail — so the 95% code can never end up on a page
    at the front of the bundle. That is the one direction that would turn this
    measurement into a lie: the whole question is whether the judge read the
    END of a bundle this size.
    """
    # `row`, not `p`: scripts/_check_scope.py walks a function together with the
    # lambdas inside it, and a lambda parameter that shadows a local assigned
    # further down reads there as the read-before-assignment bug it hunts.
    pages = sorted(_manifest_pages(manifest), key=lambda row: row["bundle_page"])
    if not pages:
        raise PlanError("measurement canary on a bundle with no pages")
    out: list[dict] = []
    used: set[int] = set()
    for frac in fractions:
        target = min(len(pages) - 1, max(0, round(frac * (len(pages) - 1))))
        step = 1 if frac < 0.5 else -1
        i = target
        while i in used and 0 <= i + step < len(pages):
            i += step
        if i in used:
            # A bundle with fewer pages than fractions. Keep the page the
            # fraction asked for rather than answer about a different position.
            i = target
        used.add(i)
        page = pages[i]
        out.append({"fraction": frac, "book": page["book"],
                    "printed": _first_printed(page), "code": page["code"],
                    "bundle_page": page["bundle_page"],
                    "position": round((i + 1) / len(pages), 3)})
    return out


def canary_tuples(rows) -> list[tuple]:
    """The (book, printed, code) shape qc_judge.prompt_for and
    qc_judge.canary_problems both take."""
    return [(r["book"], r["printed"], r["code"]) for r in rows]


def _topup(call: CallPlan, calls, target: int) -> int:
    """Grow one call's bundle to about `target` PDF pages from the chapter's own
    other pages, and say how many it added.

    §6 wants to measure a bundle OF a given size, and the biggest call a chapter
    happens to produce at that limit is usually smaller — 019 at 250 splits into
    five calls of 144-241. The pages come from the chapter's other calls, so
    every page is a real page of a real book this chapter cites; nothing is
    invented and no page is duplicated inside the bundle.
    """
    have = {_key(p) for p in call.pages}
    spare = [p for c in calls if c is not call for p in c.pages
             if _key(p) not in have]
    added = 0
    for page in _sorted_pages(spare):
        if call.pdf_page_count >= target:
            break
        if _key(page) in have:
            continue
        have.add(_key(page))
        call.pages.append({**page, "reasons": list(page["reasons"])})
        added += 1
    call.pages = _sorted_pages(call.pages)
    return added


def measure_limit(*, tag: str, day: int, units, cites=None, target: int,
                  work_dir, search: bool = True, rng=None,
                  reading_guide: Path | None = None) -> dict:
    """Build one ~`target`-page bundle and the prompt for it. No agy call.

    Returns the measurement record, which is also written to
    `<work_dir>/measurement.json` so that `check_answer` can be run later, from
    another process, against the reply the owner gets back.
    """
    import qc_judge          # lazy: plan() and the tests must not need it

    units = list(units)
    cites = cites if cites is not None else qc_cite.cites_by_unit(units)
    by_uid = {u.uid: u for u in units}
    notes: list[str] = []
    calls = plan(tag, day, units, cites, page_limit=target, search=search,
                 notes=notes, reading_guide=reading_guide)
    if not calls:
        raise PlanError(f"{tag}: nothing to measure — the plan has no calls")
    call = max(calls, key=lambda c: (c.pdf_page_count, c.call_id))
    before = call.pdf_page_count
    added = _topup(call, calls, target)

    here = Path(work_dir)
    here.mkdir(parents=True, exist_ok=True)
    artifacts, nonce = build_artifacts_md([by_uid[u] for u in call.units], here,
                                          rng=rng)
    manifest = build_bundle(call, here, rng=rng)
    manifest.nonce = nonce
    rows = measure_canary(manifest)
    prompt = qc_judge.prompt_for(call, canary_tuples(rows), call.units,
                                 call.kind)
    rec = {
        "tag": tag, "day": int(day), "target_pages": int(target),
        "call_id": call.call_id, "kind": call.kind,
        "units": list(call.units),
        "pdf_pages": call.pdf_page_count,
        "pages_from_the_plan": before, "pages_added_to_reach_target": added,
        "bundle_pages": manifest.page_count, "index_pages": manifest.index_pages,
        "bundle_bytes": manifest.bytes, "bundle": manifest.path,
        "bundle_sha256": manifest.sha256,
        "artifacts": str(artifacts), "nonce": nonce,
        "canary": rows,
        "prompt": prompt, "prompt_sha256": qc_judge.prompt_sha256(prompt),
        "manifest": manifest.as_dict(),
        "notes": list(notes) + list(call.notes),
        "how_to_run": (
            "the agy call is the owner's to make: attach bundle.pdf and "
            "artifacts.md from this directory, send `prompt` with "
            "gemini-3.1-pro-high, then\n"
            "    python scripts/qc_sources.py --check-answer <reply.json> "
            f"--out {here}"),
    }
    # The answer key does NOT go in the workspace. `rec` holds the three codes,
    # the nonce and the prompt, and the workspace is the directory whose files
    # are attached to the judge: agy is an agent that has been observed opening
    # files beside the ones it was handed, so a measurement.json sitting next to
    # bundle.pdf is a canary the judge can read instead of the pages. It goes to
    # a sibling directory, and the workspace keeps only what a real call has.
    key_dir = here.parent / f"{here.name}.key"
    key_dir.mkdir(parents=True, exist_ok=True)
    rec["measurement_file"] = str(key_dir / MEASURE_FILE)
    rec["how_to_run"] = rec["how_to_run"].replace(f"--out {here}",
                                                 f"--out {key_dir}")
    (key_dir / MEASURE_FILE).write_bytes(
        json.dumps(rec, ensure_ascii=False, indent=1).encode("utf-8"))
    return rec


def check_answer(measurement: dict, reply) -> dict:
    """Which of the three codes verified, and whether the tail one did.

    `reply` is the judge's JSON object (or the text of it). The tail code is the
    measurement: §6's rule is "the largest bundle whose last-10% canary passes",
    and a front code that verifies while the tail one does not is precisely a
    bundle that is too big — which is why all three are reported, never just a
    pass/fail.
    """
    import qc_judge          # lazy, as in measure_limit

    if isinstance(reply, (str, bytes)):
        reply = json.loads(reply)
    if not isinstance(reply, dict):
        raise PlanError("the reply is not a JSON object")
    rows = list(measurement.get("canary") or [])
    if not rows:
        raise PlanError("this measurement carries no canary to check")
    manifest = measurement.get("manifest") or {}
    returned: dict[str, list] = {}
    for row in (reply.get("canary") or []):
        if isinstance(row, dict):
            returned.setdefault(_code_token(row.get("code")), []).append(
                str(row.get("page") or ""))
        else:
            returned.setdefault(_code_token(row), []).append("")
    # The label cross-check and the reflowed-page equivalence (a printed page is
    # several stamped sheets) live in qc_judge; running the real validator here
    # is the point — a measurement scored by a second, friendlier rule would
    # pick a limit the real run then fails.
    problems = qc_judge.canary_problems(reply, canary_tuples(rows), manifest)
    nonce = qc_judge.nonce_problem(reply, measurement.get("nonce") or "")
    out = []
    # Per row, the SAME equivalence canary_problems uses: a printed page in a
    # reflowed book is several stamped sheets, and any of them is a correct
    # answer to "the code on Dulcan p.720". Measured 2026-09-24: p.720 sits on
    # bundle pages 98, 99 and 100, the judge answered with one of the other
    # two, and this row printed MISS while `problems` — the authoritative check
    # — was empty and the run passed. A report that cries wolf next to a pass
    # is how a real miss gets waved through later.
    for row in rows:
        sheets = {_code_token(row["code"])}
        for page in (manifest.get("pages") or []):
            if (str(page.get("book")) == str(row["book"])
                    and int(row["printed"]) in [int(n) for n in
                                                (page.get("printed") or [])]):
                sheets.add(_code_token(page.get("code")))
        hit = next((c for c in sheets if c in returned), None)
        out.append({**{k: row[k] for k in ("fraction", "book", "printed",
                                           "position", "bundle_page")},
                    "code": row["code"], "verified": hit is not None,
                    "verified_on_code": hit,
                    "sheets_of_this_page": sorted(sheets),
                    "returned_on": returned.get(hit or "", [])})
    tail = max(out, key=lambda r: r["fraction"])
    return {"tag": measurement.get("tag"), "target_pages":
            measurement.get("target_pages"),
            "pdf_pages": measurement.get("pdf_pages"),
            "codes": out,
            "verified": sum(1 for r in out if r["verified"]),
            "of": len(out),
            "tail_verified": bool(tail["verified"]),
            "tail_fraction": tail["fraction"],
            "nonce_ok": nonce is None, "nonce_problem": nonce,
            "problems": problems,
            "passes": bool(tail["verified"] and not problems and nonce is None)}


def _code_token(s) -> str:
    """The same comparison qc_judge makes: alphanumerics, upper case."""
    return "".join(ch for ch in str(s or "").upper() if ch.isalnum())


# ---------------------------------------------------------------------------


def _read_chapter(d: Path) -> dict:
    return {p.name: p.read_bytes().decode("utf-8")
            for p in sorted(d.iterdir())
            if p.is_file() and qc_units.is_judged_asset(p.name)}


# Where a chapter's released text sits on this machine, best first: the
# snapshot §5.5 takes before any change, then what a previous run downloaded,
# then the calibration cache. `--measure-limit` needs a REAL chapter and must
# not need the network to find one.
CHAPTER_DIRS = (
    ("_qc", "orig", "{tag}"),
    ("_qc", "work", "{tag}", "release"),
    ("_qc", "cache", "calibration", "{tag}"),
)


def find_chapter(which: str) -> tuple[Path, str, int]:
    """(directory, tag, day) for `19`, `study-019-…` or a path."""
    raw = str(which).strip()
    direct = Path(raw)
    if direct.is_dir():
        tag = direct.name if direct.name != "release" else direct.parent.name
        return direct, tag, day_of_tag(tag)
    if raw.isdigit():
        day = int(raw)
        globbed = sorted(REPO_ROOT.glob(f"_qc/orig/study-{day:03d}-*")) \
            + sorted(REPO_ROOT.glob(f"_qc/work/study-{day:03d}-*")) \
            + sorted(REPO_ROOT.glob(f"_qc/cache/calibration/study-{day:03d}-*"))
        tags = [p.name for p in globbed if p.is_dir()]
        if not tags:
            raise PlanError(
                f"no local copy of chapter {day}. Pass a directory of released "
                f"assets instead, or fetch it first "
                f"(python scripts/qc_run.py --dry-run --chapters {day}).")
        tag = tags[0]
    else:
        tag = raw
        day = day_of_tag(tag)
    for parts in CHAPTER_DIRS:
        d = REPO_ROOT.joinpath(*(p.format(tag=tag) for p in parts))
        if d.is_dir() and (d / "study_guide.md").exists():
            return d, tag, day_of_tag(tag)
    raise PlanError(f"{tag}: no directory with a study_guide.md under "
                    f"{', '.join('/'.join(p) for p in CHAPTER_DIRS)}")


def _print_measurement(rec: dict) -> None:
    print(f"\n=== {rec['tag']} — one bundle at a {rec['target_pages']}-page "
          f"limit")
    print(f"  call {rec['call_id']} [{rec['kind']}] · {len(rec['units'])} units "
          f"· {rec['pdf_pages']} pdf pages "
          f"({rec['pages_from_the_plan']} from the plan + "
          f"{rec['pages_added_to_reach_target']} to reach the target) · "
          f"{rec['bundle_pages']} bundle pages · "
          f"{rec['bundle_bytes'] / 1e6:.1f}MB")
    print(f"  bundle:    {rec['bundle']}")
    print(f"  artifacts: {rec['artifacts']}")
    print(f"  nonce:     {rec['nonce']}")
    print("  codes to expect back:")
    for row in rec["canary"]:
        print(f"    {row['fraction']:>5.0%} through the bundle "
              f"(bundle page {row['bundle_page']}, at {row['position']:.0%}): "
              f"{row['book']} p.{row['printed']} -> {row['code']}")
    for note in rec.get("notes") or []:
        print(f"  [i] {note}")
    print(f"\n--- the prompt that would be sent "
          f"(sha256 {rec['prompt_sha256'][:12]}, {len(rec['prompt'])} chars) "
          f"---")
    print(rec["prompt"])
    print("--- end of prompt ---")
    print("\nNO agy call was made. " + rec["how_to_run"])


def _print_check(res: dict) -> None:
    print(f"\n=== {res['tag']} — {res['pdf_pages']} pdf pages "
          f"(target {res['target_pages']})")
    for row in res["codes"]:
        mark = "OK  " if row["verified"] else "MISS"
        print(f"  {mark} {row['fraction']:>5.0%} {row['book']} "
              f"p.{row['printed']} {row['code']}"
              + (f"  returned on {row['returned_on']}" if row["verified"]
                 else ""))
    print(f"  {res['verified']}/{res['of']} codes verified; "
          f"nonce {'ok' if res['nonce_ok'] else res['nonce_problem']}")
    for p in res["problems"]:
        print(f"  [!] {p}")
    # §6's words are "the last-10% canary"; the page it rests on is the one at
    # 95% of the bundle, which is inside that last tenth.
    print(f"  §6's last-10% canary (the page at "
          f"{res['tail_fraction']:.0%} of the bundle): "
          + ("PASSED" if res["tail_verified"] else "FAILED"))
    print(f"  => a {res['target_pages']}-page limit "
          + ("passes" if res["passes"] else "does NOT pass")
          + " on this chapter")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("chapter", nargs="?", default=None,
                    help="a directory of released assets")
    ap.add_argument("--chapter", dest="chapter_ref", default=None,
                    help="chapter number, tag or directory, found locally "
                         "(_qc/orig, _qc/work/<tag>/release, the calibration "
                         "cache)")
    ap.add_argument("--day", type=int, default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--page-limit", type=int, default=DEFAULT_PAGE_LIMIT)
    ap.add_argument("--no-search", action="store_true",
                    help="skip the full-text hits (fast, for plan shape only)")
    ap.add_argument("--build", action="store_true", help="write the bundles too")
    ap.add_argument("--measure-limit", type=int, default=None, metavar="PAGES",
                    help="§6: build ONE bundle of about this many pages, pick "
                         "three canary pages at 5%%/50%%/95%% of it, print the "
                         "codes and the prompt that would be sent, and stop. "
                         "No agy call.")
    ap.add_argument("--check-answer", default=None, metavar="REPLY.JSON",
                    help="score a judge's reply against the measurement in "
                         "--out ('-' reads the reply from stdin)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.check_answer:
        out = Path(a.out or (REPO_ROOT / "_qc" / "work" / "measure"))
        path = out / MEASURE_FILE if out.is_dir() else Path(out)
        if not path.exists():
            print(f"no measurement at {path}; run --measure-limit first")
            return 2
        rec = json.loads(path.read_bytes().decode("utf-8"))
        raw = (sys.stdin.read() if a.check_answer == "-"
               else Path(a.check_answer).read_bytes().decode("utf-8"))
        res = check_answer(rec, raw)
        _print_check(res)
        (path.parent / "check.json").write_bytes(
            json.dumps(res, ensure_ascii=False, indent=1).encode("utf-8"))
        return 0 if res["passes"] else 1

    which = a.chapter_ref or a.chapter
    if not which:
        ap.error("give a chapter directory, or --chapter <number|tag>")
    if a.chapter_ref or not Path(which).is_dir():
        d, tag, day = find_chapter(which)
    else:
        d = Path(which)
        tag, day = d.name, None
    tag = a.tag or tag
    day = a.day if a.day is not None else (day if day is not None
                                           else day_of_tag(tag))
    units = qc_units.units(_read_chapter(d))
    cites = qc_cite.cites_by_unit(units)

    if a.measure_limit:
        out = Path(a.out or (REPO_ROOT / "_qc" / "work" / tag / "measure"
                             / str(a.measure_limit)))
        rec = measure_limit(tag=tag, day=day, units=units, cites=cites,
                            target=a.measure_limit, work_dir=out,
                            search=not a.no_search)
        _print_measurement(rec)
        return 0

    if a.build:
        # build_calls plans and builds in one pass; planning twice would print
        # one plan and build a different one, since every call gets fresh codes.
        out = Path(a.out or (REPO_ROOT / "_qc" / "work" / tag / "cli"))
        calls = build_calls(tag=tag, day=day, units=units, cites=cites,
                            work_dir=out, page_limit=a.page_limit,
                            search=not a.no_search)
        print(f"  -> {out}")
        return 0 if calls else 1

    notes: list[str] = []
    calls = plan(tag, day, units, cites, page_limit=a.page_limit,
                 search=not a.no_search, notes=notes)
    for c in calls:
        print(c.brief())
        for note in c.notes:
            print(f"    [!] {note}")
    for note in notes:
        print(f"  [i] {note}")
    print(f"-- {len(calls)} calls, "
          f"{sum(len(c.units) for c in calls)} units")
    return 0 if calls else 1


if __name__ == "__main__":
    raise SystemExit(main())
