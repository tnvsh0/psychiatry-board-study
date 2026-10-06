#!/usr/bin/env python3
"""§6 calibration — the measurement that decides whether the judge may be
trusted on 68 released chapters, and whether Tier-2 rewriting may be switched
on.

WHY THIS FILE EXISTS AND WHAT IT IS NOT. Everything else in the QC mechanism
answers "is this chapter right?". This one answers "is the answer worth
anything?", and it is the only place in the pipeline whose output is allowed to
say NO-GO. §6 names four numbers — recall >= 89% on seeded errors and on the
contradicted gold, verified false positives <= 2% on >= 200 source-confirmed
units, negative controls rejected 100%, first-attempt canary >= 90% — and
§5.9's Tier 2 is gated on the second of them. So every number here is built the
conservative way on purpose: where a metric could be read two ways, the
headline is the reading that makes the judge look WORSE, and the friendlier
reading is printed beside it as a bound. A calibration that can flatter the
judge is worth less than no calibration, because it would be believed.

THE FOUR PARTS, AND WHY EACH IS SHAPED THE WAY IT IS.

1. GOLD (§6, §1). The nine items the 2026-09-18 review adjudicated. Their
   verdicts are NOT written down here. What is written down is where to look
   and how to decide, and `gold()` then searches the full text of every book
   and derives the label. §1's first count of "9 errors" came from a reviewer
   using its own knowledge and three of the nine survived contact with the
   books; a gold set that hardcoded "042 Q2: contradicted" would repeat exactly
   that mistake one layer down, and the suite would pass while measuring
   nothing. If a book stops saying what §1 says it says, `gold()` returns a
   different label and the test that compares it to §1 fails — loudly, which is
   the point.

2. MUTANTS (§6, >= 60). Copies of units the books CONFIRM, with exactly one
   locked value changed. Before a mutant is used, code confirms it now
   contradicts its own cited page (qc_norm.values_dispute, plus the stricter
   check that the new value is nowhere in the page window). A mutant that does
   not contradict is DISCARDED, never quietly kept: it would turn a correct "no
   finding" into a recall miss and understate the judge for free — the mirror
   image of flattery, and just as useless.

3. NEGATIVES (§6, >= 200). Source-confirmed units left untouched. The seeding
   code asserts that every unit of a seeded chapter except the mutated one
   renders byte-for-byte as it did before, so "untouched" is a property the
   code proves rather than a comment.

4. NEGATIVE CONTROLS (§6). A call whose bundle.pdf is missing and one whose
   artifacts.md is truncated, both driven through the REAL qc_judge
   verification path with the agy call faked. They cost no quota and they are
   the only test of §2.1 that reproduces the 2026-09-18 incident itself: an agy
   run that never received its PDF returned SUCCESS with confident JSON, an
   invented "verbatim" quote, and ruled a card supported.

WHAT COSTS QUOTA. Only `--run`. `--build` and `--report` never construct an agy
call, and nothing in this module uploads anything: the calibration chapters are
judged through qc_run with `calibration=True` (which makes `uploads_allowed`
false and Tier 2 unavailable) against a LocalGh that serves the seeded bytes
from disk and raises on every write.

    python scripts/qc_calibrate.py --build
    python scripts/qc_calibrate.py --run          # the only part that costs quota
    python scripts/qc_calibrate.py --report
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qc_books                                           # noqa: E402
import qc_cite                                            # noqa: E402
import qc_lint                                            # noqa: E402
import qc_norm                                            # noqa: E402
import qc_sources                                         # noqa: E402
import qc_store                                           # noqa: E402
import qc_units                                           # noqa: E402

# Windows gives a redirected stdout the locale codec (cp1255 here) and the
# first Hebrew character then raises UnicodeEncodeError. Every report line this
# module prints is Hebrew and a calibration run is always piped into a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                         # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# §6's bar, and nothing else. These four decide GO / NO-GO.
BAR_RECALL = 0.89           # "recall on mutants + contradicted gold >= 89%"
BAR_FALSE_POSITIVES = 0.02  # "verified false positives <= 2%" — the Tier-2 gate
BAR_CONTROLS = 1.0          # "must be rejected 100%"
BAR_CANARY_FIRST = 0.90     # "first-attempt canary pass rate ... target >= 90%"

MIN_MUTANTS = 60            # §6: ">= 60 seeded mutants"
MIN_NEGATIVES = 200         # §6: ">= 200 negatives"

# §1's three contradicted items, by id. The requirement is PINNED HERE and
# never derived from the rows `gold()` happened to produce. `gold()` is written
# to degrade quietly — no books on the machine, no cached material, a page
# whose text moved all come back as `undecided` rather than as a crash — so a
# bar counted off its own output is a bar that deletes itself exactly when the
# gold set stopped working. These three are the only part of the calibration
# made of errors a human found in the owner's own released material, and §6's
# recall is "on mutants + contradicted gold".
REQUIRED_GOLD = ("042-Q2", "038-Q9", "043-Q1")

# The statuses qc_run returns for a chapter it actually measured. `unjudged` is
# not among them, by §2.3: "No verified verdict → `unjudged`, never approved."
# `needs_regeneration` is, because qc_run only reaches it after `judged` is
# true (qc_run.chapter_status).
MEASURED_STATUSES = ("approved", "fixed", "needs_owner", "needs_regeneration")

# The only two gates that may count as a negative control being REJECTED: the
# real verification path returning ok=false after it spent an attempt, and
# qc_judge's own refusal type. Anything else — a signature drift, an import
# error, a bad path — is the harness breaking, and a broken harness must not
# print "negative controls rejected 100%" on §6's one bar that exists to prove
# the mechanism still works.
CONTROL_GATES = ("verify", "JudgeError")

# The controls are deterministic, so one run would prove them. They are run
# five times with a fresh bundle, fresh page codes and a fresh nonce each time
# because "rejected 100% of the time" is a claim about the mechanism, not about
# one lucky nonce, and the whole thing is local and free.
CONTROL_REPEATS = 5

ALPHA = 0.05                # §6: Clopper-Pearson intervals, 95%

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_NOGO = 3               # the set was measured and it does not clear §6
EXIT_QUOTA = 4              # paused on quota; start again after `resume_after`

# The chapters the calibration set is drawn from. §6 requires mutants "across
# every artifact type ... and every book, including 005, 056, 070"; the rest
# are here because between them they hold the quiz types that exist at all
# (multiple_select, fill_in_the_blank and short_answer are 33 items in the
# whole backlog) and enough source-confirmed units to reach 200 negatives.
# Measured 2026-09-23 over these 16: 462 units whose locked value is on a page
# they cite.
DEFAULT_CHAPTERS = (
    "study-005-serotonin-system",
    "study-014-general-personality-disorder-criteria",
    "study-019-adhd-treatment-non-stimulants",
    "study-020-autism-spectrum-disorder",
    "study-024-tic-disorders-tourette-s",
    "study-029-ssris-deep-dive",
    "study-033-bipolar-i-disorder",
    "study-035-lithium",
    "study-038-generalized-anxiety-disorder",
    "study-042-benzodiazepines-buspirone-sleep-aids-overview",
    "study-047-ptsd-core",
    "study-055-schizophrenia-core",
    "study-056-schizophrenia-neurobiology-da-nmda-5-ht-hypotheses",
    "study-060-clozapine-the-gold-standard-for-treatment-resistant",
    "study-066-tardive-dyskinesia-vmat2-inhibitors",
    "study-070-ect-and-other-somatic-therapies",
)

# The gold chapters, spelled out once. §6 freezes 017-019 and 035-044 until the
# calibration is done, and these are the tags inside that freeze that the 2026-
# 09-18 review adjudicated.
T018 = "study-018-adhd-treatment-stimulants"
T019 = "study-019-adhd-treatment-non-stimulants"
T035 = "study-035-lithium"
T036 = "study-036-mood-stabilizers-valproate-lamotrigine-carbamazepine"
T038 = "study-038-generalized-anxiety-disorder"
T042 = "study-042-benzodiazepines-buspirone-sleep-aids-overview"
T043 = "study-043-ocd-core"

# Every label §6 allows a gold item to carry.
LABELS = ("contradicted", "supported", "not_in_source", "item_logic", "lint",
          "source_conflict", "undecided")

# The seeding writes into a tree of its own. `_qc/calibration/qc/orig/` will
# hold the SEEDED bytes of a chapter — deliberately not `_qc/orig/`, which is
# §2.5's one pristine copy of what was published and what qc_revert rebuilds
# from. Pointing a revert at a calibration snapshot would restore mutated
# material onto a release, so the two trees never touch and the calibration one
# carries a marker saying so.
SEEDED_MARKER = "SEEDED-DO-NOT-REVERT.txt"


class CalibrationError(RuntimeError):
    """The set cannot be built or scored. Never swallowed: a calibration that
    half ran is a number nobody may quote."""


# ---------------------------------------------------------------------------
# paths


def qc_root(root=None) -> Path:
    return qc_store.qc_root(root)


def cal_root(root=None) -> Path:
    """Where the calibration's own output lives."""
    d = qc_root(root) / "calibration"
    d.mkdir(parents=True, exist_ok=True)
    return d


def material_root(root=None) -> Path:
    """The released bytes, cached. Under `_qc/cache/` because it is other
    people's published material re-downloaded, and §5.10 gitignores that."""
    d = qc_root(root) / "cache" / "calibration"
    d.mkdir(parents=True, exist_ok=True)
    return d


def seeded_root(root=None) -> Path:
    d = cal_root(root) / "seeded"
    d.mkdir(parents=True, exist_ok=True)
    return d


def judge_root(root=None) -> Path:
    """The `_qc/` tree qc_run writes its calibration verdicts into."""
    d = cal_root(root) / "qc"
    d.mkdir(parents=True, exist_ok=True)
    marker = d / SEEDED_MARKER
    if not marker.exists():
        marker.write_text(
            "This tree holds QC verdicts for SEEDED calibration chapters.\n"
            "Its orig/ directory is NOT the pristine release bytes: one value\n"
            "in one unit of each chapter was changed on purpose (spec §6).\n"
            "Never point qc_revert.py at this root.\n", encoding="utf-8")
    return d


FIXTURES = REPO_ROOT / "tests" / "qc" / "fixtures"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _print(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# the released material


def load_chapter(tag: str, *, root=None, gh=None, fetch: bool = True
                 ) -> dict[str, str]:
    """`{asset name: text}` for one released chapter.

    Cache first, then the test fixtures, then GitHub — and the download is a
    plain `gh release download` of the text assets, the only network this
    module ever touches. §6 says to take the gold items "read-only from the
    releases ... or from tests/qc/fixtures where they already exist", and the
    order matters for the tests: a machine with no network still builds the
    part of the set the fixtures cover.
    """
    cached = material_root(root) / tag
    got = _read_dir(cached)
    if got:
        return got
    # The release BEFORE the fixtures whenever fetching is allowed. Fixtures-
    # first was right for tests and wrong for a real build: on 2026-10-06 the
    # study-scope set took chapter 001 from tests/qc/fixtures/quiz — an old
    # quiz and card set with no study guide — instead of the chapter as
    # published, which had been regenerated on 2026-10-01 with a new guide. It
    # seeded zero items there and would have spent two agy calls measuring
    # nothing. Fixtures stay the offline path (fetch=False), and the fallback
    # when GitHub cannot be reached, so a machine with no network still builds
    # the part of the set they cover.
    fetch_error = None
    if fetch:
        try:
            cached.mkdir(parents=True, exist_ok=True)
            qc_store.fetch(tag, cached, gh=gh or qc_store.Gh(), text_only=True)
            got = _read_dir(cached)
            if got:
                return got
        except Exception as exc:                                # noqa: BLE001
            fetch_error = exc
    for sub in ("quiz", "text"):
        got = _read_dir(FIXTURES / sub / tag)
        if got:
            if fetch:
                _print(f"  [!] {tag}: the release could not be read "
                       f"({fetch_error}); using the test fixture instead")
            return got
    if not fetch:
        raise CalibrationError(
            f"{tag}: no cached material under {cached} and fetching is off")
    if fetch_error is not None:
        raise CalibrationError(f"{tag}: could not fetch the release: {fetch_error}")
    raise CalibrationError(f"{tag}: the release served no text assets")


def _read_dir(d: Path) -> dict[str, str]:
    if not d.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in sorted(d.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if not qc_units.is_judged_asset(p.name):
            continue
        try:
            out[p.name] = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return out


def chapter_units(assets: dict[str, str]) -> list:
    """qc_units over one chapter, skipping an asset that will not parse.

    A chapter whose quiz.json is malformed is qc_lint's finding, not a reason
    for the calibration set to be one chapter smaller without saying so — the
    skip is recorded by the caller.
    """
    out = []
    for name in sorted(assets):
        try:
            out.extend(qc_units.units_from_asset(name, assets[name]))
        except qc_units.UnitError:
            continue
    return out


# ---------------------------------------------------------------------------
# the books, read once


DASH = r"[-‐-―−]"


def phrase(s: str) -> str:
    """A regex for `s` as the PDF text actually holds it.

    Every book here is reflowed text: a sentence wraps mid-phrase, so
    "decreases lithium clearance" is three tokens separated by a newline, and a
    literal search finds nothing. Measured 2026-09-23: five of the ten §1
    probes returned zero hits as literals and all ten resolve with `\\s+`
    between the words. Hyphens become a dash class for the same reason — the
    books write "2–8" with an en dash and the material writes "2-8".
    """
    out = []
    for ch in s:
        if ch == " ":
            out.append(r"\s+")
        elif ch in "-–—":
            out.append(DASH + r"\s*")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def sentence_at(text: str, a: int, b: int, *, span: int = 320) -> str:
    """The verbatim sentence around `text[a:b]`, whitespace collapsed.

    Collapsing the whitespace is the one liberty taken with a "verbatim" quote,
    and it is the same one qc_norm takes before it compares anything: the raw
    slice would carry the PDF's line breaks and hyphenation and be unreadable
    in the report the owner reads.
    """
    starts = [text.rfind(x, 0, a) for x in (". ", ".\n", "\n\n", "•", "•")]
    lo = max(starts)
    lo = 0 if lo < 0 else lo + 1
    ends = [x for x in (text.find(". ", b), text.find(".\n", b),
                        text.find("\n•", b), text.find("\n•", b))
            if x >= 0]
    hi = min(ends) + 1 if ends else len(text)
    return " ".join(text[max(lo, a - span):min(hi, b + span)].split())


@dataclass
class Hit:
    """One place in one book where a probe matched."""
    book: str
    printed: int
    quote: str

    def as_dict(self) -> dict:
        return {"book": self.book, "printed": self.printed, "quote": self.quote}


def search_books(pattern: str, *, limit: int = 40) -> list[Hit]:
    """Every printed page of EVERY book whose text matches `pattern`.

    Never narrowed to the book the material cites. §1's whole lesson is that
    the citation is the claim under test, not the answer: 036 Q10 cites
    "StahlKids p.772" for a sentence that is in Dulcan p.771, and a search
    restricted to the cited book would have called a supported claim
    unsourced.
    """
    hits: list[Hit] = []
    for key, printed, _snippet in qc_books.search(pattern, limit=limit):
        text = qc_books.book(key).page_text(printed) or ""
        m = re.search(pattern, text, re.I)
        if not m:                       # the snippet spanned a page boundary
            continue
        hits.append(Hit(key, printed, sentence_at(text, m.start(), m.end())))
    return hits


def _bucket(v: dict) -> tuple:
    """(kind, unit) — the only pair qc_norm will compare two values inside.

    Reaching into qc_norm for its private helper on purpose. The alternative is
    calling values_dispute() once per value against a three-page window, which
    re-parses ~9,000 characters of book text for each of ~1,900 units; a local
    copy of the rule would be worse still, because it would drift from the rule
    the judge's own verification uses and then a "contradicting" mutant would
    not be one. qc_fix borrows qc_lint's tables the same way.
    """
    return qc_norm._bucket(v)                             # noqa: SLF001


class Pages:
    """The printed pages of the books, parsed once per page.

    One object per build. Without it the confirmation pass re-parses the same
    three-page window for every unit that cites it — measured at 14s for 1,886
    units WITH the cache, and the books' own text cache makes the first read
    of a page cheap.
    """

    def __init__(self):
        self._win: dict[tuple, str] = {}
        self._val: dict[tuple, dict] = {}

    def window(self, book: str, printed: int) -> str:
        key = (book, printed)
        if key not in self._win:
            try:
                self._win[key] = qc_norm.source_window(book, printed, 1)
            except Exception:                             # noqa: BLE001
                # A book with no printed pages (the Synopsis is read through
                # per-chapter slices, §5.1) or a page past the end: not an
                # error, just nothing to confirm against.
                self._win[key] = ""
        return self._win[key]

    def values(self, book: str, printed: int) -> dict:
        """`{(kind, unit, value): raw}` for everything stated on the window."""
        key = (book, printed)
        if key not in self._val:
            out: dict[tuple, str] = {}
            for v in qc_norm.locked_values(self.window(book, printed)):
                out.setdefault((*_bucket(v), str(v["value"])), v["raw"])
            self._val[key] = out
        return self._val[key]

    def quote_for(self, book: str, printed: int, raw: str) -> str:
        """The verbatim sentence on the page that states `raw`."""
        text = self.window(book, printed)
        m = re.search(phrase(raw), text, re.I)
        if not m:
            i = text.find(raw)
            if i < 0:
                return ""
            m_span = (i, i + len(raw))
        else:
            m_span = (m.start(), m.end())
        return sentence_at(text, *m_span)


# ---------------------------------------------------------------------------
# source confirmation — the pool both the mutants and the negatives come from


@dataclass
class Confirmed:
    """One locked value of one unit that a cited page of a book states."""
    tag: str
    uid: str
    artifact: str
    unit_type: str               # quiz type, or the unit's kind
    book: str
    printed: int
    kind: str                    # qc_norm's value kind
    raw: str                     # the value as the material writes it
    value: Any
    quote: str

    def as_dict(self) -> dict:
        return {"tag": self.tag, "unit": self.uid, "artifact": self.artifact,
                "unit_type": self.unit_type, "book": self.book,
                "printed": self.printed, "kind": self.kind, "raw": self.raw,
                "value": self.value, "quote": self.quote}


def _unit_type(u) -> str:
    meta = getattr(u, "meta", {}) or {}
    if meta.get("kind") == "quiz":
        return str(meta.get("type") or "untyped")
    return str(meta.get("kind") or "unit")


def artifact_type(artifact: str) -> str:
    """The five artifact families §6 wants recall reported per."""
    if qc_units.LEGEND_RE.match(artifact):
        return "legend"
    return {"study_guide.md": "guide", "report.md": "guide",
            "facts.json": "facts", "quiz.json": "quiz",
            "flashcards.json": "cards"}.get(artifact, artifact)


def confirm_unit(unit, pages: Pages, tag: str) -> list[Confirmed]:
    """Every locked value of `unit` that one of its OWN cited pages states.

    "Its own cited pages" and not the whole library, because this is the pool a
    seeded mutant is drawn from and the mutant has to contradict *the page the
    material points the learner at*. A value confirmed only somewhere else in
    the same book would produce a mutant whose contradiction depends on which
    page the bundle happens to carry.
    """
    text = getattr(unit, "text", "")
    values = qc_norm.locked_values(text)
    if not values:
        return []
    out: list[Confirmed] = []
    seen: set[tuple] = set()
    for cite in qc_cite.parse(text, unit=getattr(unit, "uid", "")):
        book, printed = cite.get("book"), cite.get("printed")
        if not book or printed is None or book not in qc_books.SPECS:
            continue
        stated = pages.values(book, int(printed))
        if not stated:
            continue
        for v in values:
            key = (*_bucket(v), str(v["value"]))
            if key not in stated or (book, printed, key) in seen:
                continue
            seen.add((book, printed, key))
            quote = pages.quote_for(book, int(printed), stated[key])
            if not quote:
                continue
            out.append(Confirmed(
                tag=tag, uid=unit.uid, artifact=unit.artifact,
                unit_type=_unit_type(unit), book=book, printed=int(printed),
                kind=v["kind"], raw=v["raw"], value=v["value"], quote=quote))
    return out


def confirmed_pool(tags: Sequence[str], *, root=None, gh=None,
                   fetch: bool = True, pages: Pages | None = None
                   ) -> tuple[dict[str, dict[str, str]], list[Confirmed]]:
    """`({tag: assets}, [Confirmed])` over every requested chapter."""
    pages = pages or Pages()
    assets_by_tag: dict[str, dict[str, str]] = {}
    pool: list[Confirmed] = []
    for tag in tags:
        assets = load_chapter(tag, root=root, gh=gh, fetch=fetch)
        assets_by_tag[tag] = assets
        for unit in chapter_units(assets):
            pool.extend(confirm_unit(unit, pages, tag))
    return assets_by_tag, pool


# ---------------------------------------------------------------------------
# 1. the gold set
#
# What each record carries is WHERE TO LOOK and HOW TO DECIDE. The label is
# never in the data. `probe` is a phrase to find in the full text of every
# book; `claim` is the span of the released unit under test, and the code
# refuses the item if that span is not in the unit any more.

GOLD: tuple[dict, ...] = (
    {
        "gid": "042-Q2",
        "tag": T042, "unit": "Q2", "rule": "threshold_of_list",
        "claim": "Maintenance of a rigid posture against gravity (Catalepsy).",
        "probe": "The clinical picture is dominated by three (or more) of the "
                 "following symptoms",
        "element": "Catalepsy",
        "expect": ("DSM-5-TR", 135),
        "why": "The item marks three named features as REQUIRED for the "
               "catatonia specifier. The criterion is a threshold over a list: "
               "if the source asks for N of M alternatives and N < M, no single "
               "alternative is required, so marking one as required is "
               "contradicted by the page.",
    },
    {
        "gid": "038-Q9",
        "tag": T038, "unit": "Q9", "rule": "trial_arms",
        "claim": "Child/Adolescent Anxiety Multimodal Study (CAMS)",
        "probe": "CAMS, the largest trial of SSRIs in pediatric patients with "
                 "anxiety, compared sertraline",
        "claimed_arm": "SNRIs",
        "arm_terms": ("snri", "snris", "venlafaxine", "duloxetine"),
        "expect": ("Dulcan", 330),
        "why": "The stem attributes an SSRI-vs-SNRI comparison to CAMS. The "
               "page lists the trial's arms; an arm the item names that the "
               "source does not list is a contradicted trial arm (§5.8 locks "
               "trial names and arms).",
    },
    {
        "gid": "043-Q1",
        "tag": T043, "unit": "Q1", "rule": "definition_map",
        "claim": "With good or fair insight",
        "probe": "With poor insight: The individual thinks obsessive-compulsive "
                 "disorder beliefs are probably true",
        "label_re": r"With\s+(.+?)\s+insight",
        "stem_cue": "ככל הנראה",
        "expect": ("DSM-5-TR", 266),
        "why": "The stem gives the patient's belief strength verbatim "
               "(\"probably\"). The source maps that strength onto one "
               "specifier; the item keys a different one.",
    },
    {
        "gid": "018-Q3",
        "tag": T018, "unit": "Q3", "rule": "terms",
        "claim": "Children may grow more slowly during treatment, but they "
                 "eventually reach their expected adult height",
        "probe": "eventually reached their expected adult height",
        "claim_terms": ("expected adult height", "more slowly"),
        "quote_terms": ("expected adult height", "more slowly"),
        "expect": ("StahlKids", 3),
        "owner_review": "StahlKids p.3 states the MTA result the item states. "
                        "The owner list carries it because the book may be "
                        "outdated against Swanson 2017 — §6: supported but "
                        "possibly outdated goes to him, never to a fixer.",
    },
    {
        "gid": "036-Q10",
        "tag": T036, "unit": "Q10", "rule": "terms",
        "claim": "Carbamazepine עלולה להפחית את פינוי ה-Lithium ולהעלות את "
                 "הסיכון לרעילות",
        "probe": "Carbamazepine also decreases lithium clearance, increasing "
                 "the risk of lithium toxicity",
        "claim_terms": ("Carbamazepine", "Lithium"),
        "quote_terms": ("carbamazepine", "lithium clearance", "toxicity"),
        "expect": ("Dulcan", 771),
        "why": "The claim is Hebrew and the source English, so the terms are "
               "checked on the side that can carry them; a locked-value "
               "dispute would still overrule a term match.",
    },
    {
        "gid": "019-Q1+C5",
        "tag": T019, "units": ("Q1", "C5"), "rule": "pair",
        "claims": ("השפעה מלאה עשויה להימשך 2-8 שבועות",
                   "בין 8 ל-12 שבועות"),
        "probes": ("full therapeutic benefits may take 2-8",
                   "continue to improve for 8-12 weeks"),
        "unit_terms": (("שבועות",), ("שבועות",)),
        "expect": (("StahlKids", 69), ("StahlKids", 74)),
        "why": "§1's source conflict. The label comes from the two SOURCE "
               "quotes disputing each other, not from either unit's own text: "
               "the book is what disagrees with itself, and both units cite "
               "the same page.",
    },
    {
        "gid": "042-Q5+C5",
        "tag": T042, "units": ("Q5", "C5"), "rule": "pair",
        "claims": ("is much preferred over Benzodiazepines",
                   "Buspirone לא הראה עדיפות סטטיסטית על פני Placebo"),
        "probes": ("is much preferred over benzodiazepines",
                   "Buspirone did not separate from placebo"),
        "unit_terms": (("buspirone", "benzodiazepines"),
                       ("buspirone", "placebo")),
        "expect": (("Dulcan", 235), ("Dulcan", 332)),
        "why": "Two pages of one book about one drug. Whether that is a "
               "conflict or two supported claims about different questions is "
               "decided by values_dispute between the quotes, not asserted.",
    },
    {
        "gid": "035-Q1",
        "tag": T035, "unit": "Q1", "rule": "item_logic",
        "claim": "ביצוע בדיקת דם לרמת הסידן פעם בשנה לזיהוי Hyperparathyroidism",
        "probe": "serum calcium levels should be checked once a year",
        "measurement": ("סידן", "calcium"),
        "given_markers": ("נמצאה", "נמצא"),
        "action_markers": ("ביצוע בדיקת", "בדיקת דם"),
        "expect": ("Dulcan", 765),
        "why": "The key's CONTENT is on the page, so this is not a content "
               "error. The defect is that the stem reports the calcium as "
               "already measured and asks for the next step, and the key "
               "answers by ordering that same measurement.",
    },
    {
        "gid": "042-Q3",
        "tag": T042, "unit": "Q3", "rule": "lint",
        "checks": ("hygiene_leaked_label", "hygiene_signoff",
                   "hygiene_latex", "hygiene_marker", "hygiene_arabic"),
        "why": "No book decides this one: a rationale cites "
               "'General Instruction Filter' as its source. §5.4 names that "
               "string, and the label therefore comes from qc_lint on the "
               "released bytes.",
    },
)


def _norm_in(needle: str, hay: str) -> bool:
    """Normalized containment — the only comparison this module makes on prose.

    qc_norm.norm folds the LaTeX, the markdown, the geresh and the dashes the
    material is full of; a raw `in` would miss "2–8" inside "$2\\text{-}8$",
    which is how the guides actually write it.
    """
    n = qc_norm.norm(needle)
    return bool(n) and n in qc_norm.norm(hay)


def _units_of(tag: str, assets: dict[str, str]) -> dict[str, Any]:
    return {u.uid: u for u in chapter_units(assets)}


def _enumerated(text: str, after: int, *, limit: int = 30) -> list[str]:
    """The `1. … 2. … 3. …` list that follows a threshold sentence.

    Read off the page rather than counted from the sentence, because the
    sentence says "three (or more) of the following" and never says how many
    "the following" are. DSM p.135 lists 12 and the item says 12; what decides
    the item is that 3 < 12.
    """
    tail = text[after:after + 4000]
    out: list[str] = []
    for m in re.finditer(r"(?m)^\s*(\d{1,2})\.\s+(.+)$", tail):
        n = int(m.group(1))
        if n != len(out) + 1:
            break
        out.append(m.group(2).strip())
        if len(out) >= limit:
            break
    return out


def _gold_record(spec: dict, **kw) -> dict:
    rec = {"gid": spec["gid"], "tag": spec["tag"], "rule": spec["rule"],
           "unit": spec.get("unit") or "", "label": "undecided",
           "book": "", "printed": None, "quote": "", "said": "",
           "detail": "", "other_hits": [], "owner_review": False}
    rec.update(kw)
    # §6 lists the labels a gold item may carry. A rule that invented a sixth
    # would put a value in the report that nothing downstream knows how to
    # read, and `undecided` would stop meaning "the books did not settle it".
    if rec["label"] not in LABELS:
        raise CalibrationError(
            f"{spec['gid']}: {rec['label']!r} is not one of {LABELS}")
    if spec.get("owner_review"):
        rec["owner_review"] = True
        rec["owner_note"] = spec["owner_review"]
    return rec


def gold(*, root=None, gh=None, fetch: bool = True,
         specs: Sequence[dict] = GOLD) -> list[dict]:
    """§1's suspected items, each adjudicated against the full text of the
    books. One record per UNIT (a pair rule yields two).

    Every record carries `label`, `book`, `printed` and the verbatim `quote`
    the label rests on, plus every other page in every other book where the
    same probe matched — that multiplicity is evidence in its own right (the
    019 sentence is on twelve StahlKids pages, which is why the chapter's
    citation of one of them settles nothing).
    """
    pages = Pages()
    cache: dict[str, dict[str, str]] = {}

    def assets_of(tag: str) -> dict[str, str]:
        if tag not in cache:
            cache[tag] = load_chapter(tag, root=root, gh=gh, fetch=fetch)
        return cache[tag]

    out: list[dict] = []
    for spec in specs:
        try:
            out.extend(_adjudicate(spec, assets_of(spec["tag"]), pages))
        except CalibrationError as exc:
            out.append(_gold_record(spec, label="undecided", detail=str(exc)))
    return out


def _adjudicate(spec: dict, assets: dict[str, str], pages: Pages) -> list[dict]:
    rule = spec["rule"]
    units = _units_of(spec["tag"], assets)
    fn = _RULES.get(rule)
    if fn is None:
        raise CalibrationError(f"{spec['gid']}: no rule {rule!r}")
    return fn(spec, units, assets, pages)


def _claim_span(spec: dict, units: dict, uid: str, claim: str) -> str:
    unit = units.get(uid)
    if unit is None:
        raise CalibrationError(f"{spec['gid']}: {uid} is not a unit of "
                               f"{spec['tag']} any more")
    if not _norm_in(claim, unit.text):
        raise CalibrationError(
            f"{spec['gid']}: the span this item was adjudicated on is no "
            f"longer in {uid} — the released material changed, and the 2026-"
            f"09-18 verdict does not carry over to text nobody measured")
    return claim


def _one_hit(spec: dict, probe: str) -> tuple[Hit, list[dict]]:
    hits = search_books(phrase(probe))
    if not hits:
        raise CalibrationError(
            f"{spec['gid']}: no book states {probe!r} any more; the item "
            f"cannot be adjudicated from the sources on this machine")
    return hits[0], [h.as_dict() for h in hits[1:]]


def _rule_value_label(said: str, quote: str) -> tuple[str, str]:
    """contradicted / supported from the locked values alone."""
    d = qc_norm.values_dispute(said, quote)
    if d["disputed"] and not d["same"]:
        return "contradicted", "the page states a different value of the same kind"
    if d["same"] and not d["disputed"]:
        return "supported", "the page states the same value"
    if not d["said"] or not d["source"]:
        return "", "no comparable locked value on one of the two sides"
    return "", "the page states both the same value and a different one"


def _rule_terms(spec, units, assets, pages) -> list[dict]:
    uid = spec["unit"]
    said = _claim_span(spec, units, uid, spec["claim"])
    hit, others = _one_hit(spec, spec["probe"])
    missing = [t for t in spec.get("quote_terms", ()) if not _norm_in(t, hit.quote)]
    if missing:
        return [_gold_record(spec, label="not_in_source", book=hit.book,
                             printed=hit.printed, quote=hit.quote, said=said,
                             other_hits=others,
                             detail=f"the page does not state {missing}")]
    missing = [t for t in spec.get("claim_terms", ()) if not _norm_in(t, said)]
    if missing:
        return [_gold_record(spec, label="undecided", book=hit.book,
                             printed=hit.printed, quote=hit.quote, said=said,
                             other_hits=others,
                             detail=f"the item no longer says {missing}")]
    # A locked-value dispute overrules a term match: the words can line up
    # while the number does not, and the number is what a learner carries into
    # the exam.
    label, why = _rule_value_label(said, hit.quote)
    if label == "contradicted":
        return [_gold_record(spec, label="contradicted", book=hit.book,
                             printed=hit.printed, quote=hit.quote, said=said,
                             other_hits=others, detail=why)]
    return [_gold_record(spec, label="supported", book=hit.book,
                         printed=hit.printed, quote=hit.quote, said=said,
                         other_hits=others,
                         detail=why or "every term of the claim is on the page")]


def _rule_threshold_of_list(spec, units, assets, pages) -> list[dict]:
    uid = spec["unit"]
    said = _claim_span(spec, units, uid, spec["claim"])
    hit, others = _one_hit(spec, spec["probe"])
    text = qc_books.book(hit.book).page_text(hit.printed) or ""
    m = re.search(phrase(spec["probe"]), text, re.I)
    listed = _enumerated(text, m.end()) if m else []
    counts = [v for v in qc_norm.locked_values(hit.quote) if v["kind"] == "count"]
    if not counts or not listed:
        return [_gold_record(spec, label="undecided", book=hit.book,
                             printed=hit.printed, quote=hit.quote, said=said,
                             other_hits=others,
                             detail=f"threshold={bool(counts)} "
                                    f"alternatives={len(listed)}")]
    need = int(counts[0]["value"])
    element = spec["element"]
    in_list = [row for row in listed if _norm_in(element, row)]
    detail = (f"the page asks for {need} of {len(listed)} listed alternatives; "
              f"{element!r} is alternative "
              f"{listed.index(in_list[0]) + 1 if in_list else '?'}")
    if in_list and need < len(listed):
        return [_gold_record(spec, label="contradicted", book=hit.book,
                             printed=hit.printed, quote=hit.quote, said=said,
                             other_hits=others,
                             detail=detail + " — so no single one is required, "
                                             "and the item marks this one as "
                                             "required")]
    return [_gold_record(spec, label="supported" if in_list else "not_in_source",
                         book=hit.book, printed=hit.printed, quote=hit.quote,
                         said=said, other_hits=others, detail=detail)]


def _rule_trial_arms(spec, units, assets, pages) -> list[dict]:
    uid = spec["unit"]
    said = _claim_span(spec, units, uid, spec["claim"])
    hit, others = _one_hit(spec, spec["probe"])
    text = qc_books.book(hit.book).page_text(hit.printed) or ""
    m = re.search(phrase(spec["probe"]), text, re.I)
    # The arm list is the sentence the probe opens: "compared A, B, C and
    # placebo". Read to its full stop rather than guessed at.
    arms_sentence = sentence_at(text, m.start(), m.end()) if m else hit.quote
    on_page = [t for t in spec["arm_terms"] if _norm_in(t, arms_sentence)]
    detail = (f"the page's arms for this trial: {arms_sentence[:220]!r}; "
              f"terms of the claimed arm found there: {on_page or 'none'}")
    if not on_page and _norm_in(spec["claimed_arm"], units[uid].text):
        return [_gold_record(spec, label="contradicted", book=hit.book,
                             printed=hit.printed, quote=arms_sentence,
                             said=said, other_hits=others, detail=detail)]
    return [_gold_record(spec, label="supported", book=hit.book,
                         printed=hit.printed, quote=arms_sentence, said=said,
                         other_hits=others, detail=detail)]


def _rule_definition_map(spec, units, assets, pages) -> list[dict]:
    uid = spec["unit"]
    said = _claim_span(spec, units, uid, spec["claim"])
    hit, others = _one_hit(spec, spec["probe"])
    m = re.search(spec["label_re"], hit.quote, re.I)
    if not m:
        return [_gold_record(spec, label="undecided", book=hit.book,
                             printed=hit.printed, quote=hit.quote, said=said,
                             other_hits=others,
                             detail="the source sentence carries no label")]
    source_label = m.group(1).strip()
    stem = units[uid].text
    if not _norm_in(spec["stem_cue"], stem):
        return [_gold_record(spec, label="undecided", book=hit.book,
                             printed=hit.printed, quote=hit.quote, said=said,
                             other_hits=others,
                             detail=f"the stem no longer states the cue "
                                    f"{spec['stem_cue']!r}")]
    detail = (f"the source assigns this belief strength to "
              f"{source_label!r}; the item keys {said!r}")
    label = "supported" if _norm_in(source_label, said) else "contradicted"
    return [_gold_record(spec, label=label, book=hit.book, printed=hit.printed,
                         quote=hit.quote, said=said, other_hits=others,
                         detail=detail)]


def _rule_pair(spec, units, assets, pages) -> list[dict]:
    uids = spec["units"]
    hits, extra = [], []
    for probe in spec["probes"]:
        hit, others = _one_hit(spec, probe)
        hits.append(hit)
        extra.append(others)
    between = qc_norm.values_dispute(hits[0].quote, hits[1].quote)
    conflict = bool(between["disputed"] and not between["same"])
    out = []
    for i, uid in enumerate(uids):
        said = _claim_span(spec, units, uid, spec["claims"][i])
        terms = spec.get("unit_terms", ((), ()))[i]
        missing = [t for t in terms if not _norm_in(t, hits[i].quote)]
        if conflict:
            label = "source_conflict"
            detail = (f"the two pages state different values of the same kind: "
                      f"{hits[0].book} p.{hits[0].printed} against "
                      f"{hits[1].book} p.{hits[1].printed}")
        elif missing:
            label, detail = "not_in_source", f"the page does not state {missing}"
        else:
            value_label, why = _rule_value_label(said, hits[i].quote)
            label = value_label or "supported"
            detail = why or "every term of the claim is on the page"
        rec = _gold_record(spec, unit=uid, label=label, book=hits[i].book,
                           printed=hits[i].printed, quote=hits[i].quote,
                           said=said, other_hits=extra[i], detail=detail)
        rec["second_source"] = hits[1 - i].as_dict()
        out.append(rec)
    return out


def _rule_item_logic(spec, units, assets, pages) -> list[dict]:
    uid = spec["unit"]
    said = _claim_span(spec, units, uid, spec["claim"])
    hit, others = _one_hit(spec, spec["probe"])
    text = units[uid].text
    stem = text.split("\n", 2)[1] if text.startswith("type:") else text
    measured = [t for t in spec["measurement"] if _norm_in(t, stem)]
    given = [t for t in spec["given_markers"] if _norm_in(t, stem)]
    ordered = [t for t in spec["action_markers"] if _norm_in(t, said)]
    also_measured = [t for t in spec["measurement"] if _norm_in(t, said)]
    detail = (f"the source states the key's content ({hit.book} "
              f"p.{hit.printed}); stem reports {measured} as {given}; the key "
              f"orders {ordered} of {also_measured}")
    if measured and given and ordered and also_measured:
        return [_gold_record(spec, label="item_logic", book=hit.book,
                             printed=hit.printed, quote=hit.quote, said=said,
                             other_hits=others,
                             detail=detail + " — the key repeats the "
                                             "measurement the stem already "
                                             "reports as taken")]
    return [_gold_record(spec, label="supported", book=hit.book,
                         printed=hit.printed, quote=hit.quote, said=said,
                         other_hits=others, detail=detail)]


def _rule_lint(spec, units, assets, pages) -> list[dict]:
    uid = spec["unit"]
    if uid not in units:
        raise CalibrationError(f"{spec['gid']}: {uid} is not a unit any more")
    # use_books=False on purpose: the label under test is a hygiene defect in
    # the released bytes, and running the book-backed checks here would make
    # the gold set depend on the PDFs for an item no book decides.
    res = qc_lint.lint_chapter(spec["tag"], assets, use_books=False)
    flags = [f for f in (res.get("flags") or {}).get(uid, [])
             if str(f.get("check")) in spec["checks"]]
    if not flags:
        return [_gold_record(spec, label="undecided",
                             detail="qc_lint reports none of "
                                    f"{list(spec['checks'])} on {uid}")]
    return [_gold_record(spec, label="lint", said=uid,
                         detail="; ".join(f"{f['check']}: {f.get('detail', '')}"
                                          for f in flags))]


_RULES: dict[str, Callable] = {
    "terms": _rule_terms,
    "threshold_of_list": _rule_threshold_of_list,
    "trial_arms": _rule_trial_arms,
    "definition_map": _rule_definition_map,
    "pair": _rule_pair,
    "item_logic": _rule_item_logic,
    "lint": _rule_lint,
}


# ---------------------------------------------------------------------------
# 2. mutants
#
# Eight kinds, §6's list. Each is a function from (text, Confirmed) to
# (new text, the value before, the value after) or None when it does not
# apply. Nothing here decides whether the result contradicts anything —
# `contradicts()` does that, once, for all of them.

_WORDS = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
          9: "nine", 10: "ten", 2: "two", 1: "one"}
_WORD_VALUE = {w: n for n, w in _WORDS.items()}
_HEB_WORDS = {2: "שני", 3: "שלושה", 4: "ארבעה", 5: "חמישה", 6: "שישה"}

# Same-class swaps. A mutant has to be WRONG, not nonsense: swapping
# methylphenidate for "aspirin" would be caught by a judge that never read a
# page, and would measure nothing.
DRUG_SWAPS = (
    ("fluoxetine", "sertraline"), ("sertraline", "fluoxetine"),
    ("methylphenidate", "amphetamine"), ("amphetamine", "methylphenidate"),
    ("lithium", "valproate"), ("valproate", "lithium"),
    ("clozapine", "olanzapine"), ("olanzapine", "clozapine"),
    ("lorazepam", "diazepam"), ("diazepam", "lorazepam"),
    ("atomoxetine", "guanfacine"), ("guanfacine", "atomoxetine"),
    ("carbamazepine", "lamotrigine"), ("lamotrigine", "carbamazepine"),
    ("haloperidol", "risperidone"), ("risperidone", "haloperidol"),
    ("buspirone", "hydroxyzine"), ("venlafaxine", "duloxetine"),
)

# Trial arms, for the 038-Q9 failure mode: the trial is real, the arm is not.
# Applied ONLY inside a unit that names a trial. Without that guard the swap is
# just a word substitution — "placebo" appears in dozens of units that describe
# no trial at all — and the mutation would not be the one §6 asks for.
ARM_SWAPS = (
    ("placebo", "waitlist"), ("CBT", "ERP"), ("sertraline", "fluvoxamine"),
    ("fluoxetine", "escitalopram"), ("combination therapy", "monotherapy"),
    ("methylphenidate", "atomoxetine"), ("citalopram", "mirtazapine"),
)
TRIALS = ("MTA", "CAMS", "CATIE", "STAR*D", "STARD", "TADS", "POTS", "RUPP",
          "TORDIA", "CUtLASS")

DIRECTION_SWAPS = (
    ("increase", "decrease"), ("decrease", "increase"),
    ("increases", "decreases"), ("decreases", "increases"),
    ("higher", "lower"), ("lower", "higher"),
    ("improve", "worsen"), ("improves", "worsens"),
    ("inhibits", "induces"), ("induces", "inhibits"),
    ("agonist", "antagonist"), ("antagonist", "agonist"),
    ("מעלה", "מוריד"), ("מוריד", "מעלה"),
    ("מגביר", "מפחית"), ("מפחית", "מגביר"),
    ("עלייה", "ירידה"), ("ירידה", "עלייה"),
)

# Which qc_norm value kind feeds which §6 mutation name. The names are §6's,
# because the report is read against §6.
KIND_TO_MUTATION = {"number": "number", "percent": "number", "range": "number",
                    "dose": "dose", "duration": "duration", "count": "criteria_count"}

MUTATION_NAMES = ("number", "dose", "duration", "criteria_count", "drug",
                  "trial_arm", "key_swap", "direction")

# The tie-break when two mutation kinds are equally under-represented, rarest
# in the material first. Measured on the second build: with a plain
# "value bump first" order the 60 mutants came out 39 numbers and durations,
# one direction, two drugs and no trial arm at all — because almost every
# confirmed unit carries a bumpable number and only 108 of them name a trial.
# Round-robin by current count, with this as the tie-break, spreads §6's eight
# kinds over the units that can carry them.
MUTATION_RANK = {"trial_arm": 0, "direction": 1, "drug": 2,
                 "criteria_count": 3, "key_swap": 4, "dose": 5,
                 "duration": 6, "number": 7}


def _bump_number(raw: str) -> str | None:
    """`raw` with its first number changed to a different one of the same shape.

    The FIRST number only, and the rest of the string untouched: "2-8 weeks"
    becomes "3-8 weeks" and stays one duration with one unit, which is what
    "change exactly one locked value" means. Changing both endpoints would be
    two edits and a mutant nobody could attribute.

    One exception, measured on the first build: "2-3 ימים" bumped to "3-3
    ימים", a range whose ends are equal. qc_norm reads that as the range (3,3)
    and it disputes the page perfectly well, but it is not a claim any material
    would make, so the judge would be answering about a typo rather than about
    a wrong number. When the bump would collide with the next number in the
    same value, the SECOND number moves instead.
    """
    nums = list(re.finditer(r"\d+(?:[.,]\d+)?", raw))
    if nums:
        m = nums[0]
        if len(nums) > 1 and _plus_one(m.group(0)) == nums[1].group(0):
            m = nums[1]
        new = _plus_one(m.group(0))
        return raw[:m.start()] + new + raw[m.end():]
    for word, n in _WORD_VALUE.items():
        m = re.search(rf"\b{word}\b", raw, re.I)
        if m and (n + 1) in _WORDS:
            return raw[:m.start()] + _WORDS[n + 1] + raw[m.end():]
    for n, word in _HEB_WORDS.items():
        if word in raw and (n + 1) in _HEB_WORDS:
            return raw.replace(word, _HEB_WORDS[n + 1], 1)
    return None


def _plus_one(txt: str) -> str:
    if "." in txt or "," in txt:
        sep = "." if "." in txt else ","
        whole, frac = txt.split(sep, 1)
        return f"{int(whole) + 1}{sep}{frac}"
    return str(int(txt) + 1)




@dataclass
class Mutant:
    """One seeded error, and everything needed to score it."""
    mid: str
    tag: str
    uid: str
    artifact: str
    artifact_type: str
    unit_type: str
    mutation: str
    book: str
    printed: int
    quote: str
    before: str
    after: str
    confirmed_by: str

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def contradicts(before: str, after: str, quote: str, stated: dict,
                window: str = "") -> tuple[bool, str]:
    """Does the mutated claim now contradict the page it cites?

    §6: "Code confirms each contradicts its page first." Two gates, and a
    mutant needs one of them:

      * a locked-value dispute — qc_norm.values_dispute says the page states a
        comparable value and none of them equals the seeded one. This is the
        same call qc_judge makes before it will believe a `contradicted`
        finding, so a mutant that passes it is one the verification path can
        convict;
      * a token swap — the page states the original token and states the new
        one nowhere on the page at all. There is no number to dispute in
        "swap methylphenidate for amphetamine", and refusing those kinds would
        leave §6's list of eight mutations with four members.

    The token gate reads the whole PAGE WINDOW and not the value's own
    sentence. Measured on the first build: 51 of 343 discards were drug and
    direction swaps whose original word is on the page but not inside the one
    sentence that carries the confirmed number, which is an accident of where
    the number sits, not evidence about the swap.

    Both gates end with the same veto: whatever the seeded value is, it must
    not be stated anywhere on the window. A "mutant" whose new value the page
    also states is not a mutant — a finding on it would be the judge being
    right — and it is discarded.
    """
    d = qc_norm.values_dispute(after, quote)
    for v in qc_norm.locked_values(after):
        if (*_bucket(v), str(v["value"])) in stated:
            return False, (f"the page also states {v['raw']!r}; a finding on "
                           f"this would be the judge being right")
    if d["disputed"] and not d["same"]:
        return True, "values_dispute: the page states a different value"
    changed = _changed_token(before, after)
    if changed:
        old, new = changed
        haystack = window or quote
        if _word_in(old, haystack) and not _word_in(new, haystack):
            return True, (f"the page states {old!r} and not {new!r}")
        return False, (f"the page does not state {old!r}, or states {new!r} "
                       f"too")
    return False, ("no locked-value dispute and no token swap: "
                   f"{d['said']=} {d['source']=}")


def _soft(s: str) -> str:
    """Case-folded text with every run of punctuation turned into one space.

    NOT qc_norm.norm, which DELETES every non-alphanumeric — including the
    spaces — so that "2-8" and "28" cannot be confused. That is the right rule
    for comparing values and the wrong one for comparing words: on a space-free
    string every word boundary is gone, and the first version of the token
    check found none of "atomoxetine", "CBT" or "increase" on pages that state
    all three. Measured: 155 of 367 discarded mutants were drug, arm and
    direction swaps rejected by that bug alone.
    """
    import unicodedata
    t = unicodedata.normalize("NFKC", s or "").casefold()
    return " " + re.sub(r"[^0-9a-z֐-׿]+", " ", t).strip() + " "


def _word_in(token: str, text: str) -> bool:
    """`token` as a whole word.

    Whole-word and not substring: "CBT" is inside "CBT + sertraline" (which is
    the point) but "one" must not match inside "prednisone", and a direction
    swap that matched a substring would call a page that says "increased"
    evidence about "increase".
    """
    t = _soft(token).strip()
    return bool(t) and f" {t} " in _soft(text)


def _changed_token(before: str, after: str) -> tuple[str, str] | None:
    """The one word that differs between two otherwise identical strings."""
    a, b = before.split(), after.split()
    if len(a) != len(b):
        return None
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    return diff[0] if len(diff) == 1 else None


def _replace_once(text: str, old: str, new: str) -> str | None:
    """`old` -> `new`, but only when `old` occurs exactly once in `text`.

    Uniqueness is the whole safety property of the seeding: a value that occurs
    twice in an asset cannot be edited "exactly once" without choosing which
    occurrence, and a mutant whose location is a guess is a mutant whose miss
    cannot be attributed.
    """
    if text.count(old) != 1:
        return None
    return text.replace(old, new, 1)


def _json_string_edit(data: Any, old: str, new: str) -> tuple[Any, int]:
    """Replace `old` with `new` inside JSON string leaves; returns the count."""
    hits = 0

    def walk(node):
        nonlocal hits
        if isinstance(node, str):
            if node.count(old) == 1:
                hits += 1
                return node.replace(old, new, 1)
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        return node

    return walk(data), hits


def _dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _seed_into_asset(asset: str, text: str, uid: str, old: str, new: str
                     ) -> str | None:
    """The asset with `old` -> `new`, or None when it cannot be done once."""
    if asset.endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        data, hits = _json_string_edit(data, old, new)
        return _dump_json(data) if hits == 1 else None
    return _replace_once(text, old, new)


def _key_swap(asset: str, text: str, unit) -> tuple[str, str, str] | None:
    """Move `isCorrect` onto another option of a quiz item.

    Only when exactly one option is keyed and the keyed one is the one whose
    value the page confirms: the point of the mutation is that the item now
    asserts an answer its own source does not support.
    """
    if asset != "quiz.json":
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    items = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    idx = (unit.meta or {}).get("index")
    if not isinstance(idx, int) or idx >= len(items):
        return None
    item = items[idx]
    opts = item.get("answerOptions") or item.get("options") or []
    keyed = [i for i, o in enumerate(opts)
             if isinstance(o, dict) and o.get("isCorrect")]
    if len(keyed) != 1 or len(opts) < 2:
        return None
    was, now = keyed[0], (keyed[0] + 1) % len(opts)
    opts[was]["isCorrect"] = False
    opts[now]["isCorrect"] = True
    # The FULL option texts. `_make_mutant` truncates them for the record; the
    # gates below it have to search the whole thing, because a confirmed value
    # or the entity that identifies an option can sit past character 160.
    return (_dump_json(data),
            str(opts[was].get("text", "")),
            str(opts[now].get("text", "")))


def key_swap_contradicts(confirmed: Confirmed, before_option: str,
                         after_option: str, window: str) -> tuple[bool, str]:
    """Does moving the key onto `after_option` leave an item the page refutes?

    The code this replaces confirmed a key swap with `not _norm_in(after,
    c.quote)` — the new key's text must not be inside the ONE confirmed
    sentence — so `contradicts()`'s central veto never ran on 7 of the 60
    mutants. Measured: on a page stating "Atomoxetine is approved as
    monotherapy for ADHD. Guanfacine ER is approved as monotherapy for ADHD",
    moving the key from the first to the second was kept as a mutant. The item
    is still correct, the judge's correct silence is scored as a recall miss,
    and §6's recall goes down for the judge doing its job.

    Two gates, both the ones `_key_swap`'s own docstring already claimed:

      1. the page must confirm the option the key is ON today, or the swap does
         not move the key off anything the source supports;
      2. the page must not state the new key's claim anywhere on the window —
         the same veto `contradicts()` ends with, for the same reason: a
         finding on it would be the judge being right.
    """
    if not _norm_in(confirmed.raw, before_option):
        return False, (f"the confirmed value {confirmed.raw!r} is not in the "
                       f"keyed option; moving the key would not move it off "
                       f"what the page supports")
    for needle in _key_claim(after_option):
        if _word_in(needle, window) or _norm_in(needle, window):
            return False, (f"the page also states {needle!r}; a finding on "
                           f"this would be the judge being right")
    if _norm_in(after_option, confirmed.quote):
        return False, "the page states the new key's text too"
    return True, "the key moved off the option the page confirms"


def _unit_texts(assets: dict[str, str]) -> dict[str, str]:
    return {f"{u.artifact}\u0000{u.uid}": u.text for u in chapter_units(assets)}


def gold_units(rows: Sequence[dict]) -> set[tuple[str, str]]:
    """`{(tag, uid)}` of every unit the gold set is adjudicated on.

    Seeding a mutant into one of these would measure the judge on a unit whose
    released text §1 already adjudicated — the gold hit and the mutant hit
    would be the same finding, counted twice, on material that is no longer
    the material the 2026-09-18 review read.
    """
    return {(str(r.get("tag")), str(r.get("unit")))
            for r in rows if r.get("unit")}


def seed_mutants(pool: Sequence[Confirmed], assets_by_tag: dict[str, dict],
                 *, n: int = MIN_MUTANTS, pages: Pages | None = None,
                 seed: int = 20260923,
                 protect: Sequence[tuple[str, str]] = ()
                 ) -> tuple[list[Mutant], dict[str, str], list[dict]]:
    """`(mutants, {tag: {asset: seeded text}}, discarded)`.

    One mutant per unit at most, and the selection walks the pool in a fixed
    shuffled order so a rebuild reproduces the same set: §6's numbers have to
    be quotable, and a set that changed between `--build` and `--report` would
    make the recall a number about two different experiments.

    Coverage first, then volume. The walk takes the rarest slot it can fill —
    artifact type, then quiz type, then book, then mutation kind — because a
    set of 60 mutants that are all `number` in `facts.json` measures one thing
    sixty times, and §6 asks for the recall per artifact type and per book.
    """
    pages = pages or Pages()
    rng = random.Random(seed)
    order = list(pool)
    rng.shuffle(order)

    seeded: dict[str, dict[str, str]] = {t: dict(a) for t, a in assets_by_tag.items()}
    before_units = {t: _unit_texts(a) for t, a in assets_by_tag.items()}
    # The Unit objects, once. _make_mutant used to re-derive every unit of a
    # chapter on every attempt, which is ~1,900 renders per mutant considered.
    index: dict[tuple[str, str, str], Any] = {}
    for tag, assets in assets_by_tag.items():
        for u in chapter_units(assets):
            index[(tag, u.artifact, u.uid)] = u
    mutants: list[Mutant] = []
    discarded: list[dict] = []
    used_units: set[tuple[str, str]] = set(protect)
    counts = {"artifact": {}, "unit_type": {}, "book": {}, "mutation": {}}
    # Which of §6's eight kinds each confirmed value could carry, computed on
    # the ORIGINAL text: a unit is mutated at most once, so this does not move.
    kinds: dict[int, tuple[str, ...]] = {}
    for i, c in enumerate(order):
        u = index.get((c.tag, c.artifact, c.uid))
        kinds[i] = tuple(e[0] for e in _candidate_edits(c, u)) if u else ()

    def rarity(item: tuple[int, Confirmed]) -> tuple:
        i, c = item
        at = artifact_type(c.artifact)
        # The scarcest mutation this unit could carry comes FIRST. Without it
        # the walk picks units by artifact and book alone, and the kinds that
        # only a few units can carry — a trial arm needs a unit that names a
        # trial, and 108 of 1,900 do — are never reached before the set is
        # full: the second build produced no trial-arm mutant at all.
        best = min((counts["mutation"].get(k, 0) for k in kinds[i]),
                   default=99)
        # Artifact family, then unit type, then mutation kind, then book.
        # unit_type sits second because §6 names the quiz types explicitly
        # ("every quiz type incl. multiple_select / fill_in_the_blank /
        # short_answer") and the whole backlog holds 33 such items: with the
        # mutation term second, fill_in_the_blank came out of the fourth build
        # with none at all.
        return (counts["artifact"].get(at, 0),
                counts["unit_type"].get(c.unit_type, 0), best,
                counts["book"].get(c.book, 0))

    while len(mutants) < n:
        candidates = [(i, c) for i, c in enumerate(order)
                      if (c.tag, c.uid) not in used_units]
        if not candidates:
            break
        for _i, c in sorted(candidates, key=rarity):
            made = _make_mutant(c, seeded, pages, discarded, counts["mutation"],
                                index.get((c.tag, c.artifact, c.uid)))
            if made is None:
                used_units.add((c.tag, c.uid))
                continue
            mutant, new_assets = made
            seeded[c.tag] = new_assets
            mutants.append(mutant)
            used_units.add((c.tag, c.uid))
            at = artifact_type(c.artifact)
            counts["artifact"][at] = counts["artifact"].get(at, 0) + 1
            counts["unit_type"][c.unit_type] = \
                counts["unit_type"].get(c.unit_type, 0) + 1
            counts["book"][c.book] = counts["book"].get(c.book, 0) + 1
            counts["mutation"][mutant.mutation] = \
                counts["mutation"].get(mutant.mutation, 0) + 1
            break
        else:
            break

    # §6's negatives are "units confirmed correct against the books,
    # UNTOUCHED". That is asserted here rather than promised: every unit of
    # every seeded chapter except the mutated ones must render exactly as it
    # did, or the whole set is thrown away. The JSON is re-serialized when a
    # mutant is written into it, and a re-serialization that moved a single
    # character of any other unit would silently turn 200 negatives into 200
    # units nobody checked.
    mutated = {(m.tag, f"{m.artifact}\u0000{m.uid}") for m in mutants}
    for tag, assets in seeded.items():
        after = _unit_texts(assets)
        for key, text in before_units[tag].items():
            if (tag, key) in mutated:
                continue
            if after.get(key) != text:
                raise CalibrationError(
                    f"{tag}: seeding changed {key.replace(chr(0), ' ')}, which "
                    f"is not a mutant — the negatives of this chapter are no "
                    f"longer untouched")
    return mutants, seeded, discarded


def _make_mutant(c: Confirmed, seeded: dict[str, dict[str, str]], pages: Pages,
                 discarded: list[dict], mutation_counts: dict[str, int],
                 unit: Any) -> tuple[Mutant, dict[str, str]] | None:
    """One mutant from one confirmed value, or None with a reason recorded."""
    assets = seeded[c.tag]
    text = assets.get(c.artifact)
    if text is None or unit is None:
        return None
    stated = pages.values(c.book, c.printed)
    window = pages.window(c.book, c.printed)

    # Rarest mutation kind first. §6 lists eight kinds and the value bump
    # applies to almost every confirmed unit, so a plain "try the value first"
    # order produced 41 of 60 mutants in `number` and `duration` and one
    # criteria count — a set that measures one thing sixty times.
    edits = sorted(_candidate_edits(c, unit),
                   key=lambda e: (mutation_counts.get(e[0], 0),
                                  MUTATION_RANK.get(e[0], 99)))
    for mutation, new_raw, extra in edits:
        if mutation == "key_swap":
            swapped = _key_swap(c.artifact, text, unit)
            if swapped is None:
                discarded.append({"tag": c.tag, "unit": c.uid,
                                  "artifact": c.artifact, "mutation": mutation,
                                  "before": "", "after": "",
                                  "reason": "no single-keyed quiz item with two "
                                            "or more options to move a key on",
                                  "book": c.book, "printed": c.printed})
                continue
            new_text, before, after = swapped
            ok, why = key_swap_contradicts(c, before, after, window)
            before, after = before[:160], after[:160]
        else:
            # `extra` carries the exact spelling the unit uses for a token
            # swap (the tables are lower case and the material is not); a value
            # bump edits the value's own raw slice.
            before = c.raw if extra is None else extra[0]
            after = new_raw
            ok, why = contradicts(before, after, c.quote, stated, window)
            new_text = None
            if ok:
                new_text = _seed_into_asset(c.artifact, text, c.uid, before,
                                            after)
                if new_text is None:
                    ok, why = False, (f"{before!r} is not unique in "
                                      f"{c.artifact}; one edit cannot be "
                                      f"placed")
        if not ok:
            discarded.append({"tag": c.tag, "unit": c.uid,
                              "artifact": c.artifact, "mutation": mutation,
                              "before": before, "after": after, "reason": why,
                              "book": c.book, "printed": c.printed})
            continue
        new_assets = dict(assets)
        new_assets[c.artifact] = new_text
        mid = f"{c.tag}:{c.artifact}:{c.uid}:{mutation}"
        # The evidence a scorer will read back is the sentence that states what
        # was changed, which for a token swap is not the sentence that carries
        # the confirmed number.
        quote = c.quote
        if mutation in ("drug", "trial_arm", "direction"):
            quote = pages.quote_for(c.book, c.printed, before) or c.quote
        return (Mutant(mid=mid, tag=c.tag, uid=c.uid, artifact=c.artifact,
                       artifact_type=artifact_type(c.artifact),
                       unit_type=c.unit_type, mutation=mutation, book=c.book,
                       printed=c.printed, quote=quote, before=before,
                       after=after, confirmed_by=why), new_assets)
    return None


def _candidate_edits(c: Confirmed, unit) -> list[tuple[str, str, Any]]:
    """Every edit worth trying on this unit, most specific first.

    Ordered so the value kinds §6 names come before the token swaps: a unit
    that carries a dose gets its dose changed, and only a unit whose value
    cannot be bumped falls through to a drug or a direction.
    """
    out: list[tuple[str, str, Any]] = []
    bumped = _bump_number(c.raw)
    if bumped and bumped != c.raw:
        out.append((KIND_TO_MUTATION.get(c.kind, "number"), bumped, None))
    text = unit.text
    names_trial = any(_word_in(t, text) for t in TRIALS)
    for name, table in (("drug", DRUG_SWAPS), ("trial_arm", ARM_SWAPS),
                        ("direction", DIRECTION_SWAPS)):
        if name == "trial_arm" and not names_trial:
            continue
        # EVERY pair the unit matches, not the first. Measured: all seven
        # trial-arm candidates of the third build were "CBT" -> "ERP" on OCD
        # pages, which state ERP — so the one pair a unit happened to match
        # first decided that the kind produced nothing at all.
        found = 0
        for a, b in table:
            m = re.search(rf"(?<![\w֐-׿]){re.escape(a)}"
                          rf"(?![\w֐-׿])", text, re.I)
            if m:
                out.append((name, b, (m.group(0),)))
                found += 1
                if found >= 4:
                    break
    if (unit.meta or {}).get("kind") == "quiz":
        out.append(("key_swap", "", None))
    return out


# ---------------------------------------------------------------------------
# 3. negatives


@dataclass
class Negative:
    nid: str
    tag: str
    uid: str
    artifact: str
    artifact_type: str
    unit_type: str
    book: str
    printed: int
    raw: str
    quote: str
    # How much of this unit the books actually confirm. §6 asks for
    # "source-confirmed-correct UNITS" and `confirm_unit` confirms one locked
    # VALUE against one cited page, so a unit enters the pool as soon as any
    # one of its values is stated there. The headline false-positive rate then
    # counts any verified finding anywhere in the unit — including a genuine
    # error the judge is right about, in a claim nobody confirmed. The gate
    # stays on that strict reading (making it looser is the flattering
    # direction), and these two numbers say how much of each unit it rests on.
    values_confirmed: int | None = None
    values_total: int | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def negatives(pool: Sequence[Confirmed], mutants: Sequence[Mutant], *,
              n: int = MIN_NEGATIVES, seed: int = 20260923,
              protect: Sequence[tuple[str, str]] = (),
              values_by_unit: dict[tuple[str, str], int] | None = None
              ) -> list[Negative]:
    """§6's >= 200 source-confirmed-correct units, untouched.

    A unit that carries a seeded mutant is excluded: its text is no longer the
    text the books confirmed, and a finding on it is the measurement, not a
    false positive. A gold unit is excluded for the opposite reason — §1
    already adjudicated it, and three of the nine are contradicted, so a
    finding there is the judge being right. Everything else that reached
    `confirmed_pool` qualifies, one entry per unit, taken in the same fixed
    shuffled order so the set is reproducible.

    `values_by_unit` is how many locked values each unit states in total; with
    it every row can say what fraction of itself the books confirm, which is
    the difference between "a source-confirmed unit" and "a unit with a
    source-confirmed value".
    """
    used = {(m.tag, m.uid) for m in mutants} | set(protect)
    confirmed_by_unit: dict[tuple[str, str], set] = {}
    for c in pool:
        confirmed_by_unit.setdefault((c.tag, c.uid), set()).add(
            (c.kind, str(c.value)))
    rng = random.Random(seed + 1)
    seen: set[tuple[str, str]] = set()
    rows: list[Negative] = []
    order = list(pool)
    rng.shuffle(order)
    # Rarity-first again, for the same reason as the mutants: the false-
    # positive rate is reported per artifact type, and 200 negatives that are
    # all facts leaves would say nothing about the quiz.
    counts: dict[str, int] = {}
    while len(rows) < n:
        rest = [c for c in order if (c.tag, c.uid) not in used | seen]
        if not rest:
            break
        c = min(rest, key=lambda x: counts.get(artifact_type(x.artifact), 0))
        seen.add((c.tag, c.uid))
        at = artifact_type(c.artifact)
        counts[at] = counts.get(at, 0) + 1
        rows.append(Negative(
            nid=f"{c.tag}:{c.artifact}:{c.uid}", tag=c.tag, uid=c.uid,
            artifact=c.artifact, artifact_type=at, unit_type=c.unit_type,
            book=c.book, printed=c.printed, raw=c.raw, quote=c.quote,
            values_confirmed=len(confirmed_by_unit.get((c.tag, c.uid)) or ()),
            values_total=(values_by_unit or {}).get((c.tag, c.uid))))
    return rows


# ---------------------------------------------------------------------------
# 4. negative controls
#
# Both run the REAL qc_judge path — the real prompt, the real canary request,
# the real make_validator, the real verify() — with only the agy subprocess
# replaced. The fake is written to behave like the 2026-09-18 incident rather
# than like a broken program: it answers, confidently, in schema.


class FakeAgy:
    """An agy that answers without having read what it was given.

    `mode` says which half of §2.1's proof it fails:
      blind    — it never saw the bundle, so its page codes are invented
      partial  — it saw the bundle (real codes) but not the whole artifacts.md,
                 so it cannot return the nonce and echoes text it was not shown

    It honours agy_attach's `validate` contract exactly: a rejected attempt
    costs one of the three and comes back with `data=None` and a diagnostic,
    which is what makes this a test of the retry path and not only of one call.
    """

    def __init__(self, mode: str, manifest: Any, nonce: str,
                 texts: dict[str, str], *, attempts_allowed: int = 3):
        self.mode = mode
        self.manifest = manifest
        self.nonce = nonce
        self.texts = texts
        self.attempts_allowed = attempts_allowed
        self.calls = 0

    def _answer(self) -> dict:
        rows = qc_judge_module().manifest_pages(self.manifest)
        if self.mode == "blind":
            canary = [{"page": f"{r.get('book')} p.{r.get('printed')}",
                       "code": "ZZZZZZ"} for r in rows[:4]]
            nonce = "QQQQQQQQQQQQ"
        else:
            canary = [{"page": f"{r.get('book')} p.{r.get('printed')}",
                       "code": r.get("code")} for r in rows[:4]]
            nonce = "QQQQQQQQQQQQ"
        coverage = [{"unit": uid, "status": "supported",
                     "echo": (text or "")[:120], "source_quote": "",
                     "evidence_code": (rows[0].get("code") if rows else "")}
                    for uid, text in self.texts.items()]
        return {"canary": canary, "nonce": nonce, "coverage": coverage,
                "findings": []}

    def __call__(self, prompt, files, *, model=None, attempts=3, validate=None,
                 tag="qc", call_id="A", **_kw):
        import agy_attach
        last = ""
        for _ in range(max(1, min(attempts, self.attempts_allowed))):
            self.calls += 1
            data = self._answer()
            last = validate(data) if validate else None
            if not last:
                return agy_attach.AgyResult(
                    data=data, text=json.dumps(data), attempts=self.calls,
                    conversation_id=None, agy_version="fake",
                    tempmedia_ok=(self.mode != "blind"), tool_audit=[])
        return agy_attach.AgyResult(
            data=None, text="", attempts=self.calls, diagnostic=last or "",
            conversation_id=None, agy_version="fake",
            tempmedia_ok=(self.mode != "blind"), tool_audit=[])


def qc_judge_module():
    """qc_judge, imported on use.

    Not at the top of the file: importing it pulls in agy_attach, and `--build`
    and `--report` must be able to run on a machine where agy does not exist.
    """
    import qc_judge
    return qc_judge


def _control_call(workdir: Path, *, book: str = "StahlKids",
                  printed: int = 69) -> tuple[Any, Any, Path, str, dict]:
    """A real, tiny call: three printed pages, one unit, a stamped bundle."""
    b = qc_books.book(book)
    pages = [qc_sources._page(book, p, b.pdf_pages(p), "cited")  # noqa: SLF001
             for p in (printed - 1, printed, printed + 1) if b.has_page(p)]
    if not pages:
        raise CalibrationError(f"{book} p.{printed} is not in the local books")
    unit = qc_units.Unit(
        uid="Q1", artifact="quiz.json", stable_id="control",
        text="type: multiple_choice\n"
             "stem: How long may the full therapeutic benefit of atomoxetine "
             "take to appear in a child?\n"
             "A. [KEY] 2-8 weeks\n"
             "    rationale: StahlKids p.69 states the onset window.")
    plan = qc_sources.CallPlan(call_id="ctl", kind="A", units=["Q1"],
                              pages=pages)
    manifest = qc_sources.build_bundle(plan, workdir)
    artifacts, nonce = qc_sources.build_artifacts_md([unit], workdir)
    plan.bundle = Path(manifest.path)
    plan.artifacts = Path(artifacts)
    plan.manifest = manifest.as_dict()
    plan.nonce = nonce
    return plan, manifest, Path(artifacts), nonce, {"Q1": unit.text}


def control_rejected(row: dict) -> bool:
    """Was this control rejected BY THE MECHANISM §6 is testing?

    The rule the code this replaces used was "anything raised counts", so a
    signature drift, an import error or a bad path out of `_control_call`
    printed "negative controls rejected 100%" — the one bar that exists to
    prove the verification path still works was the one bar a broken
    verification path passed. Measured: with qc_judge.judge replaced by one
    raising TypeError, controls() reported 4/4 rejected with 0 attempts.

    So a rejection counts only when it came from the mechanism: qc_judge's own
    refusal type, or the real path returning ok=false after it actually spent
    an attempt on the answer.
    """
    if not row.get("rejected"):
        return False
    gate = str(row.get("gate") or "")
    if gate == "verify":
        return int(row.get("attempts") or 0) >= 1
    return gate in CONTROL_GATES


def controls(*, repeats: int = CONTROL_REPEATS, workdir=None) -> list[dict]:
    """§6's two negative controls, `repeats` times each.

    Free: the only agy on the other end is FakeAgy. A control is `rejected`
    when the real path refuses the answer — whether that refusal comes from
    qc_judge raising (a truncated artifacts.md has no block for a unit the call
    names) or from `ok: false` after three attempts. Both are the mechanism
    working; the record says which gate caught it, because a change in that is
    a change in the mechanism — and anything caught by something that is NOT
    the mechanism is recorded as not rejected (`control_rejected`).
    """
    import shutil
    import tempfile
    qc_judge = qc_judge_module()
    base = Path(workdir) if workdir else Path(
        tempfile.mkdtemp(prefix="qc-controls-"))
    base.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for i in range(repeats):
        for mode, name in (("blind", "bundle_missing"),
                           ("partial", "artifacts_truncated")):
            d = base / f"{name}-{i}"
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
            plan, manifest, artifacts, nonce, texts = _control_call(d)
            if name == "bundle_missing":
                Path(manifest.path).unlink()
            else:
                raw = artifacts.read_text(encoding="utf-8")
                artifacts.write_text(raw[:int(len(raw) * 0.55)],
                                     encoding="utf-8")
            fake = FakeAgy(mode, manifest.as_dict(), nonce, texts)
            row = {"control": name, "run": i, "rejected": False, "gate": "",
                   "reason": "", "attempts": 0}
            try:
                verdict = qc_judge.judge(plan, manifest.as_dict(), artifacts,
                                         nonce, None, ask=fake, tag="calib",
                                         raw_dir=d)
            except Exception as exc:                      # noqa: BLE001
                # A refusal by qc_judge ITSELF before the call is the strongest
                # rejection there is: no quota was spent at all. Anything else
                # is the harness breaking, and it is recorded as such rather
                # than as §6's bar being cleared.
                judge_error = getattr(qc_judge, "JudgeError", None)
                mechanism = (isinstance(exc, judge_error)
                             if isinstance(judge_error, type) else False)
                row.update(rejected=mechanism,
                           gate=type(exc).__name__,
                           reason=str(exc)[:300], attempts=fake.calls)
            else:
                row.update(rejected=not verdict.get("ok"),
                           gate="verify" if not verdict.get("ok") else "",
                           reason=str(verdict.get("reason"))[:300],
                           attempts=int(verdict.get("attempts") or fake.calls))
            # One rule, in one place, for the row and for the metric.
            row["rejected"] = control_rejected(row)
            row["bundle_present"] = Path(manifest.path).exists()
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 5. Clopper-Pearson, because scipy is not installed


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (Lentz's method)."""
    tiny, eps, itmax = 1e-300, 3e-16, 400
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lb = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(lb)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def betainv(p: float, a: float, b: float) -> float:
    """The inverse of `betainc` in x, by bisection.

    Bisection rather than Newton on purpose: it needs no derivative, cannot
    leave [0, 1], and 200 halvings take a microsecond and reach 1e-60. The
    numbers this feeds are printed to four decimals in a go/no-go the owner
    reads once.
    """
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if betainc(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def clopper_pearson(k: int, n: int, alpha: float = ALPHA) -> tuple[float, float]:
    """The exact binomial confidence interval §6 asks every rate to carry.

    Exact and conservative: a normal approximation on 3 contradicted gold items
    would print an interval that runs past 1 and below 0, and the recall bar is
    89% on a denominator that is sometimes 4. The two closed forms at the edges
    are the same formula — I_x(0, ·) and I_x(·, 0) are degenerate — written out
    because they are the cases the calibration hits most often.
    """
    if n <= 0:
        return (0.0, 1.0)
    k = max(0, min(int(k), int(n)))
    lo = 0.0 if k == 0 else betainv(alpha / 2.0, k, n - k + 1)
    hi = 1.0 if k == n else betainv(1.0 - alpha / 2.0, k + 1, n - k)
    return (lo, hi)


def rate(k: int, n: int) -> dict:
    lo, hi = clopper_pearson(k, n)
    return {"k": int(k), "n": int(n),
            "rate": (k / n) if n else None,
            "lo": lo, "hi": hi}


# ---------------------------------------------------------------------------
# 6. build


def set_id(doc: dict) -> str:
    """A name for exactly this experiment.

    Everything a score is computed over: which units carry a seeded error,
    which are negatives, which gold items were adjudicated, and the seed that
    chose them. A rebuild walks a pool whose size changed, so a unit that
    carried a mutant can come back as a negative — and a report that mixed the
    new set with the old verdicts would print one recall and one
    false-positive rate about two different experiments.
    """
    import hashlib
    parts = ["|".join(sorted(str(m.get("mid")) for m in doc.get("mutants") or [])),
             "|".join(sorted(str(x.get("nid")) for x in doc.get("negatives") or [])),
             "|".join(sorted(f"{g.get('gid')}:{g.get('unit')}:{g.get('label')}"
                             for g in doc.get("gold") or [])),
             str(doc.get("seed"))]
    # Appended only when present, so every set built before scopes existed
    # keeps the id its judgments are bound to.
    if doc.get("scope"):
        parts.append("scope:" + ",".join(sorted(doc["scope"])))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _judged_anything(root=None) -> list[str]:
    """The chapters that already hold judgments under this calibration root."""
    base = judge_root(root)
    return sorted(d.name for d in base.iterdir()
                  if d.is_dir() and any((d / "judgments").glob("round0-*.json"))
                  ) if base.is_dir() else []


def supersede_judgments(root=None) -> list[str]:
    """Move every cached judgment aside, so a re-run really re-measures.

    qc_run caches one file per verified call and REUSES it on a resume without
    rewriting it (qc_run._judge_one) — which is what keeps a 25-minute call
    from being paid for twice, and which would also mean that after a rebuild
    the re-run silently re-adopted verdicts about the old seeded bytes. Scoring
    would keep refusing them as older than the set, and the calibration would
    be stuck at NO-GO with no way forward but deleting files by hand.

    So a forced rebuild that really changed the set renames them rather than
    trusting either side: nothing is deleted, and the next `--run` asks the
    judge again.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    moved = []
    base = judge_root(root)
    if not base.is_dir():
        return moved
    for d in sorted(base.iterdir()):
        src = d / "judgments"
        if d.is_dir() and any(src.glob("round0-*.json")):
            src.rename(d / f"judgments.superseded-{stamp}")
            moved.append(d.name)
    return moved


def build(*, tags: Sequence[str] = DEFAULT_CHAPTERS, root=None, gh=None,
          fetch: bool = True, n_mutants: int = MIN_MUTANTS,
          n_negatives: int = MIN_NEGATIVES, with_gold: bool = True,
          with_controls: bool = True, seed: int = 20260923,
          force: bool = False, scope: frozenset | None = None) -> dict:
    """Prepare the whole set. No agy call, no upload, no network beyond the
    read-only asset download.

    Refuses to overwrite a set that has already been judged unless `force`:
    the judgments on disk are about the OLD seeded bytes, and nothing in a
    qc_run judgment says which set it measured.
    """
    t0 = time.time()
    judged = _judged_anything(root)
    was = str((qc_store.read_json(cal_root(root) / "set.json") or {}
               ).get("set_id") or "")
    if judged and not force and (cal_root(root) / "set.json").exists():
        raise CalibrationError(
            f"{len(judged)} chapters have already been judged against the "
            f"current set ({', '.join(judged[:3])}…). Rebuilding reseeds from a "
            f"fresh walk, so a unit that carried a mutant can come back as a "
            f"negative while those verdicts still sit in "
            f"{judge_root(root)} — one recall about two experiments. Pass "
            f"--force to rebuild anyway; the old judgments will then be "
            f"ignored (they predate the new set) and the run starts again.")
    pages = Pages()
    gold_rows = gold(root=root, gh=gh, fetch=fetch) if with_gold else []
    # Every chapter the gold set is adjudicated on has to be judged too, or
    # §6's "recall on mutants + contradicted gold" has no gold in it.
    tags = list(dict.fromkeys(list(tags) + sorted({g["tag"] for g in gold_rows})))
    assets_by_tag, pool = confirmed_pool(tags, root=root, gh=gh, fetch=fetch,
                                         pages=pages)
    if scope is not None:
        # A set about the study scope seeds its errors only where the judge
        # will read, and draws its negatives from there too: a mutant in
        # facts.json under `--scope study` would never be judged, and §6's
        # recall would then be measured on a denominator that cannot score.
        pool = [c for c in pool if c.artifact in scope]
    mutants, seeded, discarded = seed_mutants(
        pool, assets_by_tag, n=n_mutants, pages=pages, seed=seed,
        protect=sorted(gold_units(gold_rows)))
    # How many locked values each unit states at all, so every negative can
    # report what fraction of itself the books confirm (§6's "units" against
    # confirm_unit's "one value of a unit").
    values_by_unit: dict[tuple[str, str], int] = {}
    for tag_, assets_ in assets_by_tag.items():
        for u in chapter_units(assets_):
            values_by_unit[(tag_, u.uid)] = len(
                {(v["kind"], str(v["value"]))
                 for v in qc_norm.locked_values(getattr(u, "text", ""))})
    negs = negatives(pool, mutants, n=n_negatives, seed=seed,
                     protect=sorted(gold_units(gold_rows)),
                     values_by_unit=values_by_unit)

    out_dir = seeded_root(root)
    for tag, assets in seeded.items():
        d = out_dir / tag
        d.mkdir(parents=True, exist_ok=True)
        for name, text in assets.items():
            (d / name).write_text(text, encoding="utf-8")

    doc = {
        "built_at": _now_iso(),
        "seed": seed,
        "chapters": list(tags),
        "bar": {"recall": BAR_RECALL, "false_positives": BAR_FALSE_POSITIVES,
                "controls": BAR_CONTROLS, "canary_first": BAR_CANARY_FIRST},
        "pool": len(pool),
        "gold": gold_rows,
        "mutants": [m.as_dict() for m in mutants],
        "negatives": [x.as_dict() for x in negs],
        "discarded": discarded,
        "controls": controls() if with_controls else [],
        "build_seconds": round(time.time() - t0, 1),
        # Recorded so `--run` judges the same scope the set was seeded for. A
        # set seeded for the study scope but run over every asset would spend
        # quota on facts and legends; the other way round, it could not score.
        "scope": sorted(scope) if scope is not None else None,
    }
    doc["set_id"] = set_id(doc)
    doc["counts"] = _set_counts(doc)
    # A forced rebuild that really produced a different set invalidates the
    # verdicts made against the old one. A rebuild that produced the SAME set
    # (the walk is seeded, so `--build` twice is idempotent) changes nothing
    # and must not throw away a measurement that cost a day of agy time.
    if judged and doc["set_id"] != was:
        doc["superseded_judgments"] = supersede_judgments(root)
        _print(f"  הסט השתנה — פסקי הדין של {len(doc['superseded_judgments'])} "
               f"פרקים הועברו ל-judgments.superseded-* ו-`--run` ישפוט אותם "
               f"מחדש")
    qc_store.write_json_atomic(cal_root(root) / "set.json", doc)
    return doc


def _set_counts(doc: dict) -> dict:
    def by(rows, field_name):
        out: dict[str, int] = {}
        for r in rows:
            out[str(r.get(field_name))] = out.get(str(r.get(field_name)), 0) + 1
        return dict(sorted(out.items()))

    gold_rows = doc.get("gold") or []
    return {
        "gold": by(gold_rows, "label"),
        "gold_total": len(gold_rows),
        "mutants": len(doc.get("mutants") or []),
        "mutants_by_artifact": by(doc.get("mutants") or [], "artifact_type"),
        "mutants_by_unit_type": by(doc.get("mutants") or [], "unit_type"),
        "mutants_by_book": by(doc.get("mutants") or [], "book"),
        # Every one of §6's eight kinds, including the ones that came out at
        # zero. A dict that simply omitted them would let a kind the material
        # cannot carry disappear from the report instead of being reported as
        # a gap — and a gap is a real finding about the calibration.
        "mutants_by_mutation": {
            k: by(doc.get("mutants") or [], "mutation").get(k, 0)
            for k in MUTATION_NAMES},
        "mutation_kinds_missing": sorted(
            k for k in MUTATION_NAMES
            if not by(doc.get("mutants") or [], "mutation").get(k)),
        "mutants_by_tag": by(doc.get("mutants") or [], "tag"),
        "negatives": len(doc.get("negatives") or []),
        # §6 asks for confirmed UNITS and the pool is built from confirmed
        # VALUES: this is how many of the chosen units have every locked value
        # they state confirmed on a page they cite.
        "negatives_fully_confirmed": sum(
            1 for x in (doc.get("negatives") or [])
            if x.get("values_total") is not None
            and int(x.get("values_confirmed") or 0) >= int(x["values_total"])),
        "negatives_by_artifact": by(doc.get("negatives") or [], "artifact_type"),
        "negatives_by_book": by(doc.get("negatives") or [], "book"),
        "discarded": len(doc.get("discarded") or []),
        "discarded_by_reason": by(doc.get("discarded") or [], "mutation"),
        "controls": len(doc.get("controls") or []),
    }


def load_set(root=None) -> dict:
    doc = qc_store.read_json(cal_root(root) / "set.json")
    if not doc:
        raise CalibrationError(
            f"no calibration set at {cal_root(root) / 'set.json'} — run "
            f"`python scripts/qc_calibrate.py --build` first")
    return doc


# ---------------------------------------------------------------------------
# 7. run — the only part that costs quota


class LocalGh:
    """qc_store.Gh's read half, served from the seeded directory.

    Two jobs, and the second is the important one. It lets `--run` judge the
    seeded bytes without asking GitHub for anything, and it makes uploading
    impossible rather than merely disabled: `opts.calibration` already turns
    `uploads_allowed` off, but the calibration set is 60 deliberately wrong
    values inside copies of the owner's published chapters and "impossible" is
    the only acceptable standard for that. Every write raises.
    """

    def __init__(self, directory, repo: str = "local/calibration"):
        self.dir = Path(directory)
        self.repo = repo
        self.uploads_attempted: list[tuple] = []

    def _files(self, tag: str) -> dict[str, Path]:
        d = self.dir / tag
        if not d.is_dir():
            raise qc_store.GhError(f"{tag}: no seeded material under {d}")
        return {p.name: p for p in sorted(d.iterdir()) if p.is_file()}

    def list_assets(self, tag: str) -> list:
        out = []
        for name, path in self._files(tag).items():
            data = path.read_bytes()
            out.append(qc_store.Asset(
                name=name, asset_id=abs(hash((tag, name))) % 10_000_000,
                size=len(data), sha256=qc_store.sha256_bytes(data)))
        return out

    def asset(self, tag: str, name: str):
        for a in self.list_assets(tag):
            if a.name == name:
                return a
        return None

    def download(self, tag: str, name: str, dest) -> Path:
        src = self._files(tag).get(name)
        if src is None:
            raise qc_store.GhError(f"{tag}: no asset {name}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return dest

    def list_releases(self) -> list[dict]:
        return [{"tagName": p.name, "isDraft": False}
                for p in sorted(self.dir.iterdir()) if p.is_dir()]

    def _refuse(self, what: str, *args):
        self.uploads_attempted.append((what, *args))
        raise CalibrationError(
            f"calibration tried to {what} on a release. The bytes it holds are "
            f"seeded with {MIN_MUTANTS}+ deliberate errors; nothing here may "
            f"ever reach GitHub.")

    def upload(self, tag, path, name=None):
        self._refuse("upload", tag, name or Path(path).name)

    def rename_asset(self, asset_id, new_name):
        self._refuse("rename", asset_id, new_name)

    def delete_asset(self, asset_id):
        self._refuse("delete", asset_id)


class RunJournal:
    """One entry per prepared item and per chapter, written atomically.

    §6's run is 16 chapters of three agy calls, and the lockout measured on
    this account was 13h33m. A resume that re-judged a chapter because the
    journal only learned about it at the end would pay for it twice; qc_run's
    own per-call cache stops that inside a chapter, and this stops it between
    them.
    """

    def __init__(self, root=None):
        self.path = cal_root(root) / "journal.json"
        self.data = qc_store.read_json(self.path) or {
            "started_at": _now_iso(), "chapters": {}, "items": {},
            "calls": [], "paused": None}

    def save(self) -> None:
        self.data["updated_at"] = _now_iso()
        qc_store.write_json_atomic(self.path, self.data)

    def bind(self, sid: str) -> None:
        """Record which set this run is about.

        A journal that was written against a different set is not this
        experiment's: its finished chapters were measured on other bytes, so
        `done()` stops being true for them and the run comes back to them.
        qc_run's per-call cache keeps that from repaying for calls whose
        bundle did not change.
        """
        if not sid:
            return
        prior = str(self.data.get("set_id") or "")
        if prior and prior != sid:
            self.data["previous_set_id"] = prior
        self.data["set_id"] = sid
        for rec in self.data.get("chapters", {}).values():
            rec.setdefault("set_id", prior)
        self.save()

    def bind_limit(self, limit: int) -> None:
        """Record the per-call page limit this run judges at.

        The limit decides how the chapter is CUT INTO CALLS, so a chapter
        judged at 180 and a chapter judged at 250 were read in different
        bundles. Measured on the six chapters judged before the limit moved,
        every one of them replans: 4->3, 5->4, 8->5, 6->4, 4->3, 6->3 calls.
        A recall of "61%" over both halves describes neither, and the number
        it would license is Tier 2 rewriting the owner's exam material.

        So the limit is stamped like the set id, and a chapter measured at
        another one stops being done: the run comes back to it.
        """
        limit = int(limit or 0)
        if not limit:
            return False
        prior = int(self.data.get("page_limit") or 0)
        had = bool(self.data.get("calls") or self.data.get("chapters"))
        changed = prior != limit and (bool(prior) or had)
        if changed:
            # The evidence stays in the file; it just stops being added up
            # with the new regime's. Same move `calls_of_set` documents for a
            # superseded set, one level down.
            self.data.setdefault("superseded_calls", []).extend(
                self.data.get("calls") or [])
            self.data["calls"] = []
            self.data["previous_page_limit"] = prior or None
        self.data["page_limit"] = limit
        self.save()
        return changed

    def done(self, tag: str) -> bool:
        rec = self.data["chapters"].get(tag) or {}
        if not rec.get("finished_at"):
            return False
        sid = str(self.data.get("set_id") or "")
        if sid and str(rec.get("set_id") or sid) != sid:
            return False
        limit = int(self.data.get("page_limit") or 0)
        if not limit:
            return True
        # Fail-closed (§2.3): a chapter finished before the stamping existed
        # carries no limit, and "it was probably the same one" is the guess
        # this whole mechanism exists not to make.
        return int(rec.get("page_limit") or 0) == limit

    def start(self, tag: str) -> None:
        self.data["chapters"].setdefault(tag, {})["started_at"] = _now_iso()
        self.data["paused"] = None
        self.save()

    def finish(self, tag: str, verdict: dict, items: Sequence[str]) -> None:
        self.data["chapters"][tag] = {
            **(self.data["chapters"].get(tag) or {}),
            "finished_at": _now_iso(),
            "set_id": self.data.get("set_id"),
            "page_limit": self.data.get("page_limit"),
            "status": verdict.get("status"),
            "duration_s": verdict.get("duration_s"),
            "judgments": verdict.get("judgments") or []}
        for item in items:
            self.data["items"][item] = {"chapter": tag, "judged_at": _now_iso()}
        self.save()

    def fail(self, tag: str, why: str) -> None:
        rec = {**(self.data["chapters"].get(tag) or {}),
               "failed_at": _now_iso(), "error": why[:500],
               "set_id": self.data.get("set_id")}
        # A chapter that failed is a chapter that was not measured, whatever a
        # previous run wrote about it. Leaving `finished_at` there would make
        # `done()` skip it for ever and `failed_chapters` never see it.
        rec.pop("finished_at", None)
        self.data["chapters"][tag] = rec
        self.save()

    def pause(self, tag: str, resume_after: float, raw: str, step: str) -> None:
        self.data["paused"] = {
            "tag": tag, "step": step, "raw": raw,
            "resume_after_epoch": resume_after,
            "resume_after": datetime.fromtimestamp(resume_after).isoformat(
                timespec="seconds"),
            "at": _now_iso()}
        self.save()

    def add_call(self, rec: dict) -> None:
        # Stamped with the set it was made against. The journal outlives a
        # --force rebuild, and its `calls` list used to keep every call of
        # every run: the verifier mixed a superseded run's 30 good calls with
        # this set's 3 bad ones and got 90.9%, which clears §6's 90%
        # first-attempt bar while the set being reported on scored 0 of 3. A
        # rate is about one experiment or it is about nothing.
        rec.setdefault("set_id", str(self.data.get("set_id") or ""))
        rec.setdefault("page_limit", int(self.data.get("page_limit") or 0))
        self.data["calls"].append(rec)
        self.save()

    def calls_of_set(self) -> list[dict]:
        """This set's calls, at this run's page limit.

        A call with no stamp predates the stamping and is counted only when
        the journal itself has never been re-bound — to another set, or to
        another page limit.
        """
        sid = str(self.data.get("set_id") or "")
        prior = str(self.data.get("previous_set_id") or "")
        limit = int(self.data.get("page_limit") or 0)
        out = []
        for rec in self.data.get("calls") or []:
            got = str(rec.get("set_id") or "")
            if not (got == sid or (not got and not prior)):
                continue
            if limit and int(rec.get("page_limit") or 0) != limit:
                continue
            out.append(rec)
        return out


def _timed_judge(journal: RunJournal, tag: str, inner: Callable) -> Callable:
    """qc_run's judge, wrapped so §6's per-call numbers survive the run.

    §6 asks for the median and p90 duration of a full A/B/C call, the attempts
    per accepted verdict, the first-attempt canary rate and how many pro-high
    calls happen before the first 429. None of those reach `verdict.json`:
    qc_run's Judgment carries no duration at all and the raw dict's own
    `duration_s` is dropped by its adapter. Wrapping the call is the only place
    the clock and the answer are both in scope.
    """
    def judge(**kw):
        t0 = time.time()
        plan = kw.get("plan")
        try:
            res = inner(**kw)
        except Exception as exc:                          # noqa: BLE001
            journal.add_call({
                "tag": tag, "at": _now_iso(),
                "call_id": str(getattr(plan, "call_id", "?")),
                "kind": str(getattr(plan, "kind", "?")),
                "duration_s": round(time.time() - t0, 1),
                "ok": False, "raised": type(exc).__name__})
            raise
        canary = (res or {}).get("canary") or {}
        sizes = plan.sizes() if hasattr(plan, "sizes") else {}
        journal.add_call({
            "tag": tag, "at": _now_iso(),
            "call_id": str(getattr(plan, "call_id", "?")),
            "kind": str(getattr(plan, "kind", "?")),
            "duration_s": float((res or {}).get("duration_s")
                                or round(time.time() - t0, 1)),
            "attempts": int((res or {}).get("attempts") or 0),
            "ok": bool((res or {}).get("ok")),
            "canary_verified": bool(canary.get("verified")),
            "pages": int(getattr(plan, "pages", 0) or 0),
            "bundle_bytes": int(sizes.get("bundle_bytes", 0) or 0),
            "artifacts_bytes": int(sizes.get("artifacts_bytes", 0) or 0),
            "units": len(getattr(plan, "units", []) or []),
            "model": (res or {}).get("model", ""),
            "agy_version": (res or {}).get("agy_version", ""),
            "reason": str((res or {}).get("reason", ""))[:200]})
        return res
    return judge


def run_prepared(*, root=None, tags: Sequence[str] | None = None,
                 model: str | None = None, page_limit: int | None = None,
                 judge: Callable | None = None, sources: Callable | None = None,
                 notify: Callable | None = None,
                 now: Callable[[], float] = time.time) -> int:
    """Judge every seeded chapter. Resumable, quota-safe, never uploads.

    `judge` and `sources` are injected for the same reason qc_run injects
    everything that talks to the world: the tests of this function must reach
    the resume, the quota pause and the journal without a bundle, a book or an
    agy call between them.
    """
    import qc_run
    from agy_attach import QuotaPaused

    doc = load_set(root)
    chapters = list(tags or doc.get("chapters") or [])
    journal = RunJournal(root)
    journal.bind(str(doc.get("set_id") or ""))
    if journal.bind_limit(int(page_limit or qc_sources.DEFAULT_PAGE_LIMIT)):
        # A cached judgment was produced by a call whose bundle no longer
        # exists at this limit. qc_run reuses it by call id, so leaving it in
        # place would let a 180-page measurement answer for a 250-page one.
        moved = supersede_judgments(root)
        _print(f"  תקרת העמודים השתנתה ל-"
               f"{int(page_limit or qc_sources.DEFAULT_PAGE_LIMIT)} — "
               f"פסקי הדין של {len(moved)} פרקים הועברו ל-judgments.superseded-*"
               f" והם יישפטו מחדש")
    items_by_tag: dict[str, list[str]] = {}
    for m in doc.get("mutants") or []:
        items_by_tag.setdefault(m["tag"], []).append(m["mid"])
    for x in doc.get("negatives") or []:
        items_by_tag.setdefault(x["tag"], []).append(x["nid"])

    gh = LocalGh(seeded_root(root))
    scope = doc.get("scope")
    opts = qc_run.Options(
        tags=list(chapters), calibration=True, no_upload=True,
        root=judge_root(root), model=model, page_limit=page_limit,
        scope=frozenset(scope) if scope else None)
    # qc_store.snapshot() commits `_qc/orig/` by explicit path (§2.5). Under
    # the calibration root that directory holds SEEDED bytes, and committing
    # those would put 60 deliberate errors into the history of the repository
    # that publishes the owner's material.
    prior_no_commit = os.environ.get(qc_store.NO_COMMIT_ENV)
    os.environ[qc_store.NO_COMMIT_ENV] = "1"
    exit_code = EXIT_OK
    try:
        with qc_store.keep_awake():
            for i, tag in enumerate(chapters, 1):
                if journal.done(tag):
                    _print(f"[{i}/{len(chapters)}] {tag} — כבר נשפט, מדלג")
                    continue
                _print(f"[{i}/{len(chapters)}] {tag}")
                deps = qc_run.Deps(gh=gh, now=now,
                                   notify=notify or qc_run.Ntfy(enabled=False))
                if sources is not None:
                    deps.sources = sources
                # The real judge is qc_run's own lazily bound one unless a
                # caller injected a double; either way it is wrapped, because
                # the clock and the answer are only both in scope here.
                deps.judge = _timed_judge(journal, tag, judge or deps.judge)
                journal.start(tag)
                try:
                    verdict = qc_run.ChapterRun(tag, deps=deps,
                                                opts=opts).run()
                except QuotaPaused as exc:
                    step = qc_store.Journal(
                        tag, root=judge_root(root)).resume() or "verdict"
                    journal.pause(tag, exc.resume_after_epoch, exc.raw, step)
                    when = datetime.fromtimestamp(exc.resume_after_epoch)
                    qc_store.set_run_state(
                        tag, "paused_quota", root=judge_root(root),
                        # qc_store refuses a paused_quota without an absolute
                        # wake-up time: a duration is useless in a file that
                        # outlives the process (§4).
                        resume_after=when.isoformat(timespec="seconds"),
                        resume_after_epoch=exc.resume_after_epoch, step=step,
                        raw=exc.raw)
                    _print(f"  [PAUSE] מכסת agy נגמרה בשלב {step}; "
                           f"אפשר להמשיך אחרי "
                           f"{datetime.fromtimestamp(exc.resume_after_epoch):%H:%M %d/%m}")
                    return EXIT_QUOTA
                except Exception as exc:                  # noqa: BLE001
                    journal.fail(tag, f"{type(exc).__name__}: {exc}")
                    traceback.print_exc()
                    exit_code = EXIT_ERROR
                    continue
                status = str((verdict or {}).get("status") or "")
                if status in MEASURED_STATUSES:
                    journal.finish(tag, verdict, items_by_tag.get(tag, []))
                    _print(f"  → {status} · "
                           f"{len(verdict.get('fixes') or [])} תיקונים")
                else:
                    # §2.3 is fail-closed, and a calibration has to treat it the
                    # same way: `unjudged` is what qc_run returns when a call
                    # failed its canary (the confident invented JSON of the
                    # 2026-09-18 incident), so the chapter was NOT measured and
                    # the run must come back to it. Marking it finished made
                    # every later run print "כבר נשפט, מדלג" for ever while the
                    # chapter kept contributing unmeasured units to §6's floors,
                    # and the only way out was deleting journal.json by hand.
                    # qc_run's own per-call cache means the resume does not pay
                    # again for the calls that did verify.
                    journal.fail(tag, f"status {status or '?'}: "
                                      f"{_trim(verdict.get('reason') or '', 200)}")
                    exit_code = EXIT_ERROR
                    _print(f"  → {status or '?'} — הפרק לא נמדד, ההרצה הבאה "
                           f"תחזור אליו")
    finally:
        if prior_no_commit is None:
            os.environ.pop(qc_store.NO_COMMIT_ENV, None)
        else:
            os.environ[qc_store.NO_COMMIT_ENV] = prior_no_commit
        if gh.uploads_attempted:                          # pragma: no cover
            raise CalibrationError(
                f"the run attempted {len(gh.uploads_attempted)} writes to a "
                f"release: {gh.uploads_attempted[:3]}")
    return exit_code


# ---------------------------------------------------------------------------
# 8. scoring


def _built_epoch(doc: dict | None) -> float | None:
    """`built_at` as an epoch, or None when the set does not date itself."""
    raw = str((doc or {}).get("built_at") or "")
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _judgments_of(tag: str, root=None, *, doc: dict | None = None
                  ) -> list[dict]:
    """Every judgment qc_run cached for `tag` THAT IS ABOUT THIS SET.

    Read from `_qc/<tag>/judgments/` rather than from verdict.json because the
    verdict keeps only the findings that reached a tier or the owner: a
    verified `contradicted` of severity `low` is counted there and not listed,
    and scoring recall without it would understate the judge for the same
    reason a discarded mutant would.

    A judgment is a measurement OF ONE SET. `--build` reseeds from a fresh walk
    over a pool whose size changed — the builder's own open_issues names
    `--build --mutants 70` as the operator workaround for chapter 070, i.e. a
    rebuild after a run — so a unit that carried a mutant can come back as a
    negative and vice versa while the old verdicts are still on disk. A
    judgment that names a different `set_id`, or that predates `built_at`, is
    about a different experiment and is not counted. Without `doc` nothing is
    filtered, which is what the callers that only want the raw files ask for.
    """
    d = judge_root(root) / tag / "judgments"
    out = []
    if not d.is_dir():
        return out
    sid = str((doc or {}).get("set_id") or "")
    cutoff = _built_epoch(doc) if doc is not None else None
    for p in sorted(d.glob("round0-*.json")):
        rec = qc_store.read_json(p) or {}
        if not rec:
            continue
        stamped = str(rec.get("set_id") or "")
        if sid and stamped and stamped != sid:
            continue
        if cutoff is not None and not stamped:
            # Nothing stamps qc_run's own files, so an unstamped judgment is
            # dated by the only thing that dates it: when it was written.
            try:
                if p.stat().st_mtime < cutoff:
                    continue
            except OSError:                               # pragma: no cover
                continue
        out.append(rec)
    return out


def _stale_judgments(tag: str, root=None, *, doc: dict | None = None) -> int:
    """How many cached judgments this set may not count."""
    d = judge_root(root) / tag / "judgments"
    if not d.is_dir():
        return 0
    all_files = len(list(d.glob("round0-*.json")))
    return max(0, all_files - len(_judgments_of(tag, root, doc=doc)))


def _judged_units(tag: str, root=None, *, doc: dict | None = None) -> set[str]:
    """The uids a VERIFIED call of this chapter actually covered.

    Judged-ness is per UNIT and never per chapter. A chapter is three or more
    calls (§5.8's A / B / C, plus the S calls a Synopsis-heavy chapter gets),
    qc_run caches one file per call and only for a call that verified
    (qc_run._judge_one), and it stops the chapter at the first call that did
    not. "This tag has a judgment on disk" therefore says nothing about whether
    the guide, the facts or the legends were ever read — and a negative nobody
    read contributes a zero to the numerator and a one to the denominator of
    the very rate §5.9 puts Tier-2 rewriting behind. Measured on the code this
    replaced: one cached A call over one unit gave false_positives 0/200 with a
    1.8% upper bound, clearing both §6's 200-negative floor and §5.9's 2% gate.

    Two sources, both of them proof the call covered the unit: the uids the
    call was PLANNED over, and the coverage rows §5.8.2 requires one of per
    unit — intersected, so a row naming a unit that was not in the call proves
    nothing either.

    NOT the units the findings name. A judge that writes a finding about a unit
    no call ever showed it would make that unit "judged and clean": the
    verification pass marks such a finding unverified ("unit X is not in this
    call") and drops it, and the unit then enters the negatives at 0 false
    positives. Measured by the verifier on the code this replaces — one A call
    over one unit refilled the 200-negative floor to 201 and cleared §5.9's 2%
    gate on units nobody read, which is the exact defect this function exists
    to close, one door further along.
    """
    out: set[str] = set()
    for j in _judgments_of(tag, root, doc=doc):
        if not j.get("ok"):
            continue     # §5.8: a failed call proves nothing about any unit
        out |= {str(u) for u in (j.get("units") or []) if u}
        out |= {str(r["unit"]) for r in (j.get("coverage") or [])
                if isinstance(r, dict) and r.get("unit")}
    return out


def _findings_by_unit(tag: str, root=None, *, doc: dict | None = None
                      ) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for j in _judgments_of(tag, root, doc=doc):
        if not j.get("ok"):
            continue                    # §5.8: a failed call's findings are noise
        for f in j.get("findings") or []:
            if not isinstance(f, dict):
                continue
            uid = str(f.get("unit") or "")
            if uid:
                out.setdefault(uid, []).append(f)
    return out


def _verified(f: dict) -> bool:
    v = f.get("verified")
    return bool(v) if not isinstance(v, str) else v.strip().lower() in (
        "true", "yes", "verified", "ok", "1")


def _value_keys(text: str) -> set[tuple]:
    """`{(kind, unit, value)}` for every locked value in `text`."""
    try:
        values = qc_norm.locked_values(text or "")
    except Exception:                                     # noqa: BLE001
        return set()
    return {(*_bucket(v), str(v["value"])) for v in values}


def _hits_value(f: dict, needle: str) -> bool:
    """Does this finding NAME `needle` — as a value, not as a substring?

    The test this replaces was `qc_norm.norm(needle) in qc_norm.norm(said)`,
    and qc_norm.norm deletes the spaces: on the one- and two-character needles
    `_bump_number` routinely produces (a criteria count 8 -> "9", a number 3 ->
    "4") it degenerated into "does the judge's sentence contain this digit
    anywhere". A finding about "19 mg" then scored as having found the seeded
    "9". That is the flattering direction, which this file exists not to take.

    So: when the needle is a VALUE, only a value of the same (kind, unit)
    bucket and the same magnitude counts — the same `_bucket` comparison
    `confirm_unit` and `contradicts` already make, so a hit here means the same
    thing a confirmation means. When it is a token (a drug, an arm, a
    direction, a moved key) there is no value to compare and the fallback is
    whole-WORD containment, never a substring.
    """
    if not needle:
        return False
    hay = (str(f.get("said") or ""), str(f.get("problem") or ""))
    want = _value_keys(needle)
    if want:
        return any(want & _value_keys(h) for h in hay)
    return any(_word_in(needle, h) for h in hay)


def _key_head(option: str) -> str:
    """The entity an option leads with — "Guanfacine ER, an alpha-2A …"."""
    text = str(option or "").strip()
    head = re.split(r"[,;:()–—]|\s-\s", text, maxsplit=1)[0].strip()
    return head if 3 <= len(head) <= 60 and head != text else ""


def _key_claim(option: str) -> list[str]:
    """The new key's CLAIM, for `contradicts()`'s veto: if the page states it,
    the item is still right and a finding on it would be the judge being
    right."""
    text = str(option or "").strip()
    if not text:
        return []
    out = [text]
    head = _key_head(text)
    if head:
        out.append(head)
    out += [v["raw"] for v in (qc_norm.locked_values(text) or [])]
    return out


def _key_needles(option: str, before: str = "") -> list[str]:
    """What a judge naming a moved key would actually write.

    `_key_swap` stores the new option's text (up to 160 characters) as the
    mutant's `after`, and §5.8's schema says `said` carries the disputed value,
    not a transcript of the option — so demanding the whole string back is a
    miss pre-set for every key swap, and with 7 of 60 mutants that is enough on
    its own to push recall under §6's 89% for a judge that did its job.

    What identifies the moved key is the entity it leads with, a locked value
    inside it, or the words that tell it apart from the option the key came
    OFF: half these options are one Hebrew sentence with no head at all
    ("חסימה סלקטיבית של קולטני $5-HT2A$ …" against "… $D_{2}$ …"), and the
    token that distinguishes them is the whole difference the judge has to
    report.
    """
    text = str(option or "").strip()
    if not text:
        return []
    out = [h for h in (_key_head(text),) if h]
    out += [v["raw"] for v in (qc_norm.locked_values(text) or [])]
    old = set(_soft(before).split())
    # Latin-or-digit tokens first: in this material they are the receptor, the
    # drug and the trial — "$5-HT2A$" against "$D_{2}$" is the entire
    # difference between the two options, and ranking by length alone buried it
    # under five longer Hebrew function words.
    distinct = sorted((t for t in set(_soft(text).split())
                       if t not in old and len(t) >= 4),
                      key=lambda t: (0 if re.search(r"[0-9a-z]", t) else 1,
                                     -len(t)))[:6]
    out += distinct
    return out or [text[:80]]


def _hits_mutant(f: dict, m: dict) -> bool:
    """Did this finding name the value that was seeded into this mutant?"""
    if str(m.get("mutation") or "") == "key_swap":
        return any(_hits_value(f, n) for n in
                   _key_needles(m.get("after"), str(m.get("before") or "")))
    return _hits_value(f, str(m.get("after") or ""))


def score(doc: dict, *, root=None) -> dict:
    """Every §6 number, from whatever judging has actually happened.

    "Has actually happened" is per UNIT and against THIS set: see
    `_judged_units` and `_judgments_of`. An item nobody read is neither a
    recall miss nor a clean negative — it is not a measurement at all, and §6's
    floors are the size of the measurement.
    """
    tags = set([m["tag"] for m in doc.get("mutants") or []]
               + [x["tag"] for x in doc.get("negatives") or []]
               + [str(g.get("tag")) for g in doc.get("gold") or [] if g.get("tag")]
               + list(doc.get("chapters") or []))
    per_tag = {t: _findings_by_unit(t, root, doc=doc) for t in tags}
    judged_units = {t: _judged_units(t, root, doc=doc) for t in tags}
    judged_tags = {t for t in tags if judged_units[t]}
    stale = {}
    for t in sorted(tags):
        skipped = _stale_judgments(t, root, doc=doc)
        if skipped:
            stale[t] = skipped

    def was_judged(tag: str, uid: str) -> bool:
        return str(uid) in judged_units.get(tag, set())

    mutants, negs = [], []
    for m in doc.get("mutants") or []:
        found = [f for f in per_tag.get(m["tag"], {}).get(m["uid"], [])
                 if _verified(f)]
        exact = [f for f in found if _hits_mutant(f, m)]
        mutants.append({**m, "judged": was_judged(m["tag"], m["uid"]),
                        "hit_any": bool(found), "hit_exact": bool(exact),
                        "findings": len(found),
                        "evidence": (exact or found or [{}])[0].get("said", "")})
    for x in doc.get("negatives") or []:
        found = [f for f in per_tag.get(x["tag"], {}).get(x["uid"], [])
                 if _verified(f)]
        onvalue = [f for f in found if _hits_value(f, x["raw"])]
        negs.append({**x, "judged": was_judged(x["tag"], x["uid"]),
                     "fp_any": bool(found), "fp_on_value": bool(onvalue),
                     "findings": len(found),
                     "evidence": (onvalue or found or [{}])[0].get("said", "")})

    gold_rows = [g for g in (doc.get("gold") or [])
                 if g.get("label") == "contradicted"]
    gold_scored = []
    for g in gold_rows:
        found = [f for f in per_tag.get(g["tag"], {}).get(g["unit"], [])
                 if _verified(f)]
        # §5.8 routes `unsupported` and `source_conflict` to the owner queue and
        # never lets either reach qc_fix; neither is the judge having found what
        # §1 found. The 60 mutants are held to the value test, so holding the
        # three real errors to "any verified finding in the same item" would
        # score the two halves of §6's one recall by different rules — and the
        # looser one on the half that is made of real errors.
        contra = [f for f in found
                  if str(f.get("kind") or "") == "contradicted"]
        want = str(g.get("said") or "")
        exact = ([f for f in contra if _hits_value(f, want)]
                 if _value_keys(want) else contra)
        gold_scored.append({**g, "judged": was_judged(g["tag"], g["unit"]),
                            "hit_any": bool(found),
                            "hit_exact": bool(exact),
                            "findings": len(found),
                            "evidence": (exact or found or [{}])[0].get(
                                "said", "")})

    judged_m = [m for m in mutants if m["judged"]]
    judged_n = [x for x in negs if x["judged"]]
    judged_g = [g for g in gold_scored if g["judged"]]
    recall_pool = judged_m + judged_g

    out = {
        # For the report's own line only. Nothing is counted off it: see
        # `_judged_units`.
        "judged_chapters": sorted(judged_tags),
        "judged_units": sum(len(v) for v in judged_units.values()),
        "stale_judgments": stale,
        # §6's headline recall is the STRICT one: a verified finding that names
        # the seeded value. `hit_any` is printed beside it as the friendly
        # bound, and the gap between them is how much of the recall rests on
        # the judge having found something else in the same unit.
        "recall": rate(sum(1 for r in recall_pool if r["hit_exact"]),
                       len(recall_pool)),
        "recall_any": rate(sum(1 for r in recall_pool if r["hit_any"]),
                           len(recall_pool)),
        "recall_mutants": rate(sum(1 for m in judged_m if m["hit_exact"]),
                               len(judged_m)),
        "recall_gold": rate(sum(1 for g in judged_g if g["hit_exact"]),
                            len(judged_g)),
        # And the headline false-positive rate is the LOOSE one: any verified
        # finding on a unit the books confirm. `fp_on_value` is the subset that
        # certainly disputes the confirmed value; the rest may be real errors
        # elsewhere in the same unit, and counting them out would be the judge
        # marking its own homework.
        "false_positives": rate(sum(1 for x in judged_n if x["fp_any"]),
                                len(judged_n)),
        "false_positives_on_value": rate(
            sum(1 for x in judged_n if x["fp_on_value"]), len(judged_n)),
        "by_artifact": _breakdown(recall_pool, "artifact_type"),
        "by_unit_type": _breakdown(judged_m, "unit_type"),
        "by_book": _breakdown(recall_pool, "book"),
        "by_mutation": _breakdown(judged_m, "mutation"),
        "mutants": mutants,
        "negatives": negs,
        "gold": gold_scored,
    }
    out.update(_call_metrics(doc, root=root))
    out["controls"] = _control_metrics(doc)
    return out


def _breakdown(rows: Sequence[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get(key) or "?"), []).append(r)
    return {k: rate(sum(1 for r in v if r.get("hit_exact")), len(v))
            for k, v in sorted(groups.items())}


def _control_metrics(doc: dict) -> dict:
    """§6's "rejected 100%", counted only on rejections the mechanism made.

    `control_rejected` is re-applied to every row rather than trusting the
    stored flag, so a set.json written by an older build — or by a run whose
    qc_judge has since drifted — cannot carry a 100% into the GO line on
    records whose gate is not a gate.
    """
    rows = doc.get("controls") or []
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r.get("control")), []).append(r)
    unexpected = [{"control": r.get("control"), "gate": r.get("gate"),
                   "reason": r.get("reason"), "attempts": r.get("attempts")}
                  for r in rows if not control_rejected(r)
                  and str(r.get("gate") or "") not in ("", "verify")]
    return {
        "overall": rate(sum(1 for r in rows if control_rejected(r)), len(rows)),
        "by_control": {k: rate(sum(1 for r in v if control_rejected(r)), len(v))
                       for k, v in sorted(by.items())},
        "gates": sorted({str(r.get("gate")) for r in rows if r.get("gate")}),
        "unexpected": unexpected,
        "error": str(doc.get("controls_error") or ""),
        "measured_at": doc.get("controls_at") or doc.get("built_at"),
    }


def _is_quota_abort(call: dict) -> bool:
    """§4: "A quota stop never consumes a content retry."

    It must not consume a canary statistic either. `_timed_judge` records an
    aborted call as `ok: false` because it raised, but the judge never
    answered and there was nothing to verify — counting it would write one
    phantom canary failure per paused chapter into a rate §6 asks to be
    reported.
    """
    return str(call.get("raised") or "") == "QuotaPaused"


def _call_metrics(doc: dict, *, root=None) -> dict:
    journal = RunJournal(root)
    calls = journal.calls_of_set()
    aborts = [c for c in calls if _is_quota_abort(c)]
    # Every call the judge actually answered or failed to answer. The canary
    # rates are computed on THIS and not on the calls that verified: a call
    # that burned all three content attempts and was discarded is precisely
    # the judge's worst behaviour, and dividing by the survivors printed 100%
    # for a judge whose true first-attempt rate was 5 of 10 — against §6's 90%
    # bar, on the one proof (§2.1) that the judge read its bundle at all.
    attempted = [c for c in calls if not _is_quota_abort(c)]
    ok = [c for c in calls if c.get("ok")]
    durations = [float(c.get("duration_s") or 0.0) for c in ok]
    attempts = [int(c.get("attempts") or 0) for c in ok if c.get("attempts")]
    first_try = [c for c in attempted
                 if c.get("ok") and int(c.get("attempts") or 0) == 1]
    unverified = 0
    findings = 0
    for tag in doc.get("chapters") or []:
        for j in _judgments_of(tag, root, doc=doc):
            for f in j.get("findings") or []:
                if isinstance(f, dict):
                    findings += 1
                    unverified += 0 if _verified(f) else 1
    # A chapter that never reached a verdict is the difference between the set
    # that was prepared and the set that was measured, and §6's floors are
    # counted on the second. Measured 2026-09-23 on 070, one of the three
    # chapters §6 names: it is Synopsis-heavy (278 of 615 citations), so
    # qc_sources routes 77 of its 89 units into S calls and leaves B1 holding
    # one unit and ZERO pages — and build_bundle then refuses the whole plan
    # with "B1: no pages". Nothing is wrong with the judge there, and a NO-GO
    # that did not name the chapter would read as though there were.
    failed = {t: str(rec.get("error") or "")
              for t, rec in (journal.data.get("chapters") or {}).items()
              if rec.get("error") and not rec.get("finished_at")}
    # Every chapter the set was built from has to have reached a verdict. A
    # chapter that was never attempted leaves no error behind, so it is not in
    # `failed` and nothing else would ever notice it — and §6 names three
    # chapters the set must include (005, 056, 070).
    finished = {t for t, rec in (journal.data.get("chapters") or {}).items()
                if rec.get("finished_at")}
    unfinished = [t for t in (doc.get("chapters") or [])
                  if t not in finished and t not in failed]
    paused = journal.data.get("paused")
    # §6: "pro-high calls before the first 429". The abort record itself is
    # where the first one happened; the pause record dates it only when the
    # journal has one at all, and `len(calls)` counts the calls of every run
    # that resumed afterwards.
    if aborts:
        before_429 = calls.index(aborts[0])
    elif paused:
        before_429 = sum(1 for c in calls
                         if str(c.get("at", "")) <= str(paused.get("at", "")))
    else:
        before_429 = len(calls)
    return {
        "calls": len(calls),
        "calls_ok": len(ok),
        "calls_attempted": len(attempted),
        "quota_aborts": len(aborts),
        "canary_first_attempt": rate(len(first_try), len(attempted)),
        "canary_after_retries": rate(sum(1 for c in attempted if c.get("ok")),
                                     len(attempted)),
        "attempts_per_accepted": (statistics.mean(attempts)
                                  if attempts else None),
        "duration_median_s": statistics.median(durations) if durations else None,
        "duration_p90_s": (_p90(durations) if durations else None),
        "pages_per_call": (statistics.median([int(c.get("pages") or 0)
                                              for c in calls])
                           if calls else None),
        "bundle_bytes_per_call": (statistics.median(
            [int(c.get("bundle_bytes") or 0) for c in calls]) if calls else None),
        "unverified_findings": rate(unverified, findings),
        "calls_before_first_quota": before_429,
        "paused": paused,
        "failed_chapters": failed,
        "unfinished_chapters": unfinished,
        # The journal was written by a run against ONE set. If it names a
        # different one, the verdicts on disk and the set being scored are two
        # experiments and nothing below may be added up.
        # Two ways the journal can span two experiments, and the owner has to
        # be told about either: it is bound to another set, or it still holds
        # calls made against one. The rates above count only this set's calls
        # (calls_of_set), which is what keeps a superseded run's 30 good calls
        # out of a 90% bar — but silently dropping them would hide that the
        # file describes two runs.
        "set_mismatch": bool(
            (journal.data.get("set_id") and doc.get("set_id")
             and journal.data.get("set_id") != doc.get("set_id"))
            or (len(journal.data.get("calls") or []) > len(calls))),
        "page_limit": int(journal.data.get("page_limit") or 0),
        # Same reasoning one limit down: the rates above count only the calls
        # made at this run's page limit, and the owner has to be told when the
        # file also holds calls made at another — otherwise a set that was
        # half-judged at 180 and half at 250 reports one number for two
        # experiments.
        "limit_mismatch": bool(
            len({int(c.get("page_limit") or 0)
                 for c in (journal.data.get("calls") or [])}) > 1),
    }


def _p90(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    i = 0.9 * (len(ordered) - 1)
    lo = math.floor(i)
    hi = math.ceil(i)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (i - lo)


def verdict_line(doc: dict, s: dict) -> tuple[bool, list[str]]:
    """GO / NO-GO against §6's four numbers, and every reason it is not GO.

    GO is not a report line. It is the owner's authority to run the judge over
    68 released chapters and, through §5.9, to let Tier 2 REWRITE the material
    he is revising for a board exam. So the only question asked here is "could
    this print GO while a bar §6 names was not actually MEASURED on the current
    set", and every answer is fail-closed:

      * a bar is never cleared by an empty denominator — `n = 0` means the
        thing was not measured (§2.3);
      * the sizes are checked on what was judged, per unit, never on what was
        prepared;
      * the contradicted-gold requirement is pinned to §1's three ids and not
        derived from the rows this call was handed;
      * a chapter that never reached a verdict, a run still parked on a quota
        pause, a journal about a different set and a negative control that was
        not caught by the verification path each block on their own.
    """
    why: list[str] = []
    rec, fp = s["recall"], s["false_positives"]
    ctl, can = s["controls"]["overall"], s["canary_first_attempt"]
    if not rec["n"]:
        why.append("אף מוטנט או פריט זהב סותר לא נשפט")
    elif (rec["rate"] or 0) < BAR_RECALL:
        why.append(f"recall {rec['rate']:.0%} < {BAR_RECALL:.0%}")
    if not fp["n"]:
        why.append("אף שלילי לא נשפט")
    elif (fp["rate"] or 0) > BAR_FALSE_POSITIVES:
        why.append(f"false positives {fp['rate']:.1%} > "
                   f"{BAR_FALSE_POSITIVES:.0%}")
    if not ctl["n"] or (ctl["rate"] or 0) < BAR_CONTROLS:
        why.append(f"בקרות שליליות נדחו {ctl['rate'] if ctl['n'] else 0:.0%} "
                   f"מהפעמים (נדרש 100%)")
    # §6's controls prove the VERIFICATION PATH refuses a call that never read
    # its bundle. A row whose gate is an exception from somewhere else proves
    # only that the harness is broken, and the 100% bar is the one bar a broken
    # harness would otherwise pass.
    for bad in s["controls"].get("unexpected") or []:
        why.append(f"בקרה שלילית ({bad.get('control')}) לא נתפסה על ידי מנגנון "
                   f"האימות אלא על ידי {bad.get('gate') or '?'}: "
                   f"{_trim(bad.get('reason') or '', 120)}")
    if s["controls"].get("error"):
        why.append(f"לא ניתן היה להריץ מחדש את הבקרות השליליות מול qc_judge "
                   f"הנוכחי: {_trim(s['controls']['error'], 120)}")
    if not can["n"]:
        why.append("אין קריאות שופט מאומתות למדוד עליהן canary")
    elif (can["rate"] or 0) < BAR_CANARY_FIRST:
        why.append(f"canary בניסיון ראשון {can['rate']:.0%} < "
                   f"{BAR_CANARY_FIRST:.0%}")
    # Counted on what was JUDGED, never on what was prepared. A run that
    # stopped after one chapter had 9 mutants at 100% recall and 12 negatives
    # at 0% false positives, and printed GO — §6's ">= 60 seeded mutants" and
    # ">= 200 negatives" are the size of the MEASUREMENT, and a partial run is
    # exactly the kind of flattering number this file exists not to produce.
    if s["recall_mutants"]["n"] < MIN_MUTANTS:
        why.append(f"רק {s['recall_mutants']['n']} מוטנטים נשפטו מתוך "
                   f"{len(doc.get('mutants') or [])} (נדרשים {MIN_MUTANTS})")
    if s["false_positives"]["n"] < MIN_NEGATIVES:
        why.append(f"רק {s['false_positives']['n']} שליליים נשפטו מתוך "
                   f"{len(doc.get('negatives') or [])} (נדרשים "
                   f"{MIN_NEGATIVES})")
    # §6's recall is "on mutants + contradicted gold", and §1 adjudicated
    # exactly three contradicted items. The requirement is REQUIRED_GOLD and
    # never `len([g for g in doc["gold"] if ...])`: gold() degrades to
    # `undecided` on every failure to adjudicate, so a bar counted off its own
    # rows disappears exactly when the gold set stopped working — GO with
    # nothing adjudicated, on the only part of the set made of real errors.
    scored_gold = {str(g.get("gid")): g for g in (s.get("gold") or [])}
    present_gold = {str(g.get("gid")): g for g in (doc.get("gold") or [])}
    for gid in REQUIRED_GOLD:
        row = present_gold.get(gid)
        if row is None:
            why.append(f"פריט הזהב {gid} (§1) חסר מהסט")
        elif str(row.get("label")) != "contradicted":
            why.append(f"פריט הזהב {gid} לא הוכרע כ-contradicted אלא כ-"
                       f"{row.get('label')}: {_trim(row.get('detail') or '', 120)}")
        elif not (scored_gold.get(gid) or {}).get("judged"):
            why.append(f"פריט הזהב {gid} לא נשפט")
    undecided = sorted({str(g.get("gid")) for g in (doc.get("gold") or [])
                        if str(g.get("label")) == "undecided"})
    if undecided:
        why.append(f"פריטי זהב שלא הוכרעו מול הספרים: {', '.join(undecided)}")
    if s["recall_gold"]["n"] < len([g for g in (doc.get("gold") or [])
                                    if g.get("label") == "contradicted"]):
        why.append(f"רק {s['recall_gold']['n']} פריטי זהב סותרים נשפטו")
    # §2.3 is fail-closed: a calibration that did not finish is not a GO. Both
    # of these are already computed and printed in the report; the GO line just
    # has to read what the report reads.
    for tag, err in sorted((s.get("failed_chapters") or {}).items()):
        why.append(f"{tag} לא הגיע לפסק דין: {_trim(err, 120)}")
    unfinished = list(s.get("unfinished_chapters") or [])
    if unfinished:
        why.append(f"{len(unfinished)} פרקים מהסט לא נשפטו כלל: "
                   f"{', '.join(unfinished[:4])}"
                   f"{'…' if len(unfinished) > 4 else ''}")
    if s.get("paused"):
        why.append(f"ההרצה עצורה על מכסה ({(s['paused'] or {}).get('tag')} "
                   f"בשלב {(s['paused'] or {}).get('step')}); אפשר להמשיך אחרי "
                   f"{(s['paused'] or {}).get('resume_after')}")
    if s.get("set_mismatch"):
        why.append("יומן ההרצה נכתב מול סט אחר (set_id שונה) — המספרים "
                   "מתארים שני ניסויים")
    if s.get("limit_mismatch"):
        why.append(f"היומן מערבב שתי תקרות עמודים (כעת {s.get('page_limit')}) "
                   f"— הפרק נחתך אחרת לכל קריאה, אז המספרים מתארים שני ניסויים")
    return (not why), why


# ---------------------------------------------------------------------------
# 9. the report


def _pct(r: dict) -> str:
    if not r.get("n"):
        return "—"
    return (f"{r['rate']:.1%} ({r['k']}/{r['n']}, "
            f"CI {r['lo']:.1%}–{r['hi']:.1%})")


def _table(title: str, rows: dict) -> list[str]:
    if not rows:
        return []
    out = [f"### {title}", "", "| | שיעור |", "|---|---|"]
    out += [f"| {k} | {_pct(v)} |" for k, v in rows.items()]
    out.append("")
    return out


def render_report(doc: dict, s: dict) -> str:
    go, why = verdict_line(doc, s)
    counts = doc.get("counts") or _set_counts(doc)
    L: list[str] = []
    L.append("# כיול בדיקת האיכות — §6")
    L.append("")
    L.append(f"**{'GO' if go else 'NO-GO'}**"
             + ("" if go else " — " + "; ".join(why)))
    L.append("")
    L.append(f"נבנה: {doc.get('built_at')} · set_id: "
             f"{doc.get('set_id') or '—'} · פרקים: "
             f"{len(doc.get('chapters') or [])} · פרקים עם קריאה מאומתת אחת "
             f"לפחות: {len(s.get('judged_chapters') or [])} · יחידות שנשפטו "
             f"בפועל: {s.get('judged_units')}")
    L.append("")
    L.append("כל מדד למטה נספר על יחידות שקריאה מאומתת באמת כיסתה אותן — לא "
             "על פרק שאחת מקריאותיו הצליחה. פרק הוא שלוש קריאות או יותר "
             "(§5.8), qc_run עוצר אותו בקריאה הראשונה שלא אומתה, והיחידות של "
             "הקריאות שלא רצו לא נמדדו.")
    L.append("")
    if s.get("stale_judgments"):
        L.append(f"**פסקי דין ישנים שלא נספרו** (נכתבו לפני הסט הנוכחי או מול "
                 f"set_id אחר): {s['stale_judgments']}. כדי למדוד אותם מחדש יש "
                 f"למחוק את `judgments/` של אותם פרקים ולהריץ `--run` שוב.")
        L.append("")
    L.append("## מול הסף של §6")
    L.append("")
    L.append("| מדד | נמדד | סף | עובר |")
    L.append("|---|---|---|---|")
    for name, got, bar, ok in (
            ("recall (מוטנטים + זהב סותר)", _pct(s["recall"]),
             f">= {BAR_RECALL:.0%}",
             bool(s["recall"]["n"]) and (s["recall"]["rate"] or 0) >= BAR_RECALL),
            ("false positives מאומתים", _pct(s["false_positives"]),
             f"<= {BAR_FALSE_POSITIVES:.0%}",
             bool(s["false_positives"]["n"])
             and (s["false_positives"]["rate"] or 1) <= BAR_FALSE_POSITIVES),
            ("דחיית בקרות שליליות", _pct(s["controls"]["overall"]), "100%",
             bool(s["controls"]["overall"]["n"])
             and (s["controls"]["overall"]["rate"] or 0) >= 1.0),
            ("canary בניסיון ראשון", _pct(s["canary_first_attempt"]),
             f">= {BAR_CANARY_FIRST:.0%}",
             bool(s["canary_first_attempt"]["n"])
             and (s["canary_first_attempt"]["rate"] or 0) >= BAR_CANARY_FIRST)):
        L.append(f"| {name} | {got} | {bar} | {'כן' if ok else 'לא'} |")
    L.append("")
    L.append(f"גבול עליון ל-recall (כל ממצא מאומת ביחידה): "
             f"{_pct(s['recall_any'])}. "
             f"תת-הקבוצה של ה-FP שבוודאות חולקת על הערך המאומת: "
             f"{_pct(s['false_positives_on_value'])}.")
    L.append("")
    # §6 sets a bar on the COMBINED recall only, and the three gold items are
    # 5% of that denominator — so a judge can miss every real error §1 found
    # and still clear 89% on the 60 errors this file wrote itself. The split is
    # printed here so that cannot pass unnoticed.
    L.append(f"פירוק ה-recall: על {s['recall_mutants']['n']} המוטנטים "
             f"{_pct(s['recall_mutants'])} · על פריטי הזהב הסותרים של §1 "
             f"({', '.join(REQUIRED_GOLD)}) {_pct(s['recall_gold'])}. "
             f"§6 קובע סף על המדד המשולב בלבד, אבל פריטי הזהב הם הטעויות "
             f"האמיתיות היחידות בסט — השאר נזרעו כאן.")
    L.append("")

    L.append("## מה נמדד על כל פריט זהב")
    L.append("")
    L.append("| פריט | יחידה | תווית | ספר | עמוד | ציטוט |")
    L.append("|---|---|---|---|---|---|")
    for g in doc.get("gold") or []:
        L.append(f"| {g['gid']} | {g.get('unit') or '—'} | {g['label']} | "
                 f"{g.get('book') or '—'} | {g.get('printed') or '—'} | "
                 f"{_trim(g.get('quote') or g.get('detail') or '', 150)} |")
    L.append("")
    owner = [g for g in (doc.get("gold") or []) if g.get("owner_review")]
    if owner:
        L.append("**לבעלים (נתמך אך ייתכן שאינו עדכני — לעולם לא לתיקון "
                 "אוטומטי):**")
        for g in owner:
            L.append(f"- {g['gid']} — {g.get('owner_note', '')}")
        L.append("")

    L.append("## הסט")
    L.append("")
    L.append(f"- מוטנטים: {counts.get('mutants')} נזרעו, "
             f"{s['recall_mutants']['n']} נשפטו (נדרשים {MIN_MUTANTS})")
    L.append(f"- שליליים: {counts.get('negatives')} נבחרו, "
             f"{s['false_positives']['n']} נשפטו (נדרשים {MIN_NEGATIVES})")
    # §6 says "source-confirmed-correct UNITS"; confirm_unit confirms one
    # locked VALUE against one cited page. The headline FP rate stays on the
    # strict reading (any verified finding on such a unit), and this line says
    # what that rate actually rests on instead of letting the claim stand.
    full = counts.get("negatives_fully_confirmed")
    L.append(f"  שליליים הם יחידות שלפחות ערך נעול אחד שלהן מאומת מול עמוד "
             f"שהן מצטטות; מתוכן {full if full is not None else '—'} יחידות "
             f"שכל ערכיהן הנעולים מאומתים. שיעור ה-FP הראשי סופר כל ממצא "
             f"מאומת ביחידה כזו, כולל בטענה שאיש לא אימת — הקריאה המחמירה, "
             f"וזו שהשער של Tier 2 יושב עליה.")
    L.append(f"- מוטנטים שנפסלו לפני שימוש: {counts.get('discarded')} — "
             f"כל אחד מהם לא סתר את העמוד שלו")
    L.append(f"- לפי סוג נכס: {counts.get('mutants_by_artifact')}")
    L.append(f"- לפי סוג יחידה: {counts.get('mutants_by_unit_type')}")
    L.append(f"- לפי ספר: {counts.get('mutants_by_book')}")
    L.append(f"- לפי סוג שינוי: {counts.get('mutants_by_mutation')}")
    missing = counts.get("mutation_kinds_missing") or []
    if missing:
        L.append(f"- **סוגי שינוי מ-§6 שלא ניתן היה לזרוע**: "
                 f"{', '.join(missing)} — לא נמצאה יחידה מאומתת שבה השינוי "
                 f"באמת סותר את העמוד שלה. כל מועמד נפסל ונרשם ב-set.json.")
    L.append("")

    L += _table("recall לפי סוג נכס", s.get("by_artifact") or {})
    L += _table("recall לפי ספר", s.get("by_book") or {})
    L += _table("recall לפי סוג שינוי", s.get("by_mutation") or {})
    L += _table("recall לפי סוג יחידה", s.get("by_unit_type") or {})

    L.append("## בקרות שליליות")
    L.append("")
    L.append(f"נמדדו: {s['controls'].get('measured_at') or '—'} (מורצות מחדש "
             f"בכל `--report`, כדי שהמספר יתאר את qc_judge שעליו מדווחים)")
    for name, r in (s["controls"].get("by_control") or {}).items():
        L.append(f"- {name}: נדחה {_pct(r)}")
    if s["controls"].get("gates"):
        L.append(f"- השערים שתפסו אותן: {', '.join(s['controls']['gates'])}")
    L.append(f"- נספרת רק דחייה שהגיעה ממנגנון האימות: "
             f"{', '.join(CONTROL_GATES)} (ול-verify נדרש ניסיון אחד לפחות). "
             f"חריגה מכך היא תקלה ברתמה, לא בקרה שעברה.")
    for bad in s["controls"].get("unexpected") or []:
        L.append(f"- **בקרה שלא נתפסה על ידי המנגנון**: {bad.get('control')} — "
                 f"{bad.get('gate')}: {_trim(bad.get('reason') or '', 160)}")
    if s["controls"].get("error"):
        L.append(f"- **לא ניתן היה להריץ אותן מחדש**: "
                 f"{_trim(s['controls']['error'], 200)}")
    L.append("")

    L.append("## מדדי הרצה")
    L.append("")
    L.append(f"- קריאות: {s.get('calls')} (מתוכן אומתו {s.get('calls_ok')}; "
             f"{s.get('quota_aborts')} נקטעו על מכסה ואינן נספרות ב-canary — "
             f"השופט לא ענה, לא היה מה לאמת)")
    L.append(f"- canary אחרי ניסיונות חוזרים: "
             f"{_pct(s['canary_after_retries'])} (המכנה הוא כל קריאה שנוסתה, "
             f"כולל אלה שנשרפו אחרי שלושה ניסיונות)")
    L.append(f"- ניסיונות לכל תשובה מאומתת: "
             f"{_num(s.get('attempts_per_accepted'))}")
    L.append(f"- משך קריאה: חציון {_num(s.get('duration_median_s'))}s, "
             f"p90 {_num(s.get('duration_p90_s'))}s")
    L.append(f"- עמודים לקריאה (חציון): {_num(s.get('pages_per_call'))}")
    L.append(f"- bundle לקריאה (חציון): "
             f"{_num((s.get('bundle_bytes_per_call') or 0) / 1e6)}MB")
    L.append(f"- קריאות pro-high עד ה-429 הראשון: "
             f"{s.get('calls_before_first_quota')}")
    L.append(f"- שיעור ממצאים לא מאומתים: {_pct(s['unverified_findings'])}")
    if s.get("paused"):
        L.append(f"- **עצירה על מכסה**: {s['paused'].get('tag')} בשלב "
                 f"{s['paused'].get('step')}, אפשר להמשיך אחרי "
                 f"{s['paused'].get('resume_after')}")
    L.append("")
    if s.get("failed_chapters"):
        L.append("## פרקים שלא הגיעו לפסק דין")
        L.append("")
        L.append("הפריטים שלהם לא נמדדו — זה ההפרש בין הסט שנבנה לסט שנמדד, "
                 "והרצפות של §6 נספרות על השני.")
        for tag, why in sorted(s["failed_chapters"].items()):
            L.append(f"- {tag}: {_trim(why, 200)}")
        L.append("")

    if not s.get("judged_chapters"):
        L.append("> עדיין לא רץ שופט על הסט. המספרים שלמעלה הם של הבנייה בלבד; "
                 "`--run` הוא החלק היחיד שצורך מכסה.")
        L.append("")
    L.append("## מה §6 מאפשר אם זה GO")
    L.append("")
    L.append(f"- שיעור ה-FP המאומת הוא השער ל-Tier 2 (§5.9): "
             f"{_pct(s['false_positives'])} מול סף "
             f"{BAR_FALSE_POSITIVES:.0%}.")
    L.append("- עד שהשער עובר, כל תיקון הוא Tier 1 בלבד: הערה עם הציטוט "
             "והעמוד, שום שכתוב.")
    return "\n".join(L) + "\n"


def _num(v) -> str:
    if v is None:
        return "—"
    return f"{v:.1f}" if isinstance(v, float) else str(v)


def _trim(s: str, n: int) -> str:
    s = " ".join(str(s).split()).replace("|", "/")
    return s if len(s) <= n else s[:n - 1] + "…"


def refresh_controls(doc: dict, *, root=None) -> dict:
    """Re-run §6's negative controls, so the number describes the qc_judge
    being reported on.

    The controls are run at `--build` time and frozen into set.json, and a
    `--report` days later would otherwise quote them against a verification
    path that has changed since. They cost nothing — the only agy on the other
    end is FakeAgy — so the only reason not to is that the books are not on
    this machine, and a set built without controls asks for none.
    """
    if not (doc.get("controls") or []):
        return doc
    try:
        rows = controls()
    except Exception as exc:                              # noqa: BLE001
        doc["controls_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return doc
    doc["controls"] = rows
    doc["controls_at"] = _now_iso()
    doc["controls_error"] = ""
    qc_store.write_json_atomic(cal_root(root) / "set.json", doc)
    return doc


def report(*, root=None, recheck_controls: bool = True) -> dict:
    doc = load_set(root)
    if recheck_controls:
        doc = refresh_controls(doc, root=root)
    s = score(doc, root=root)
    go, why = verdict_line(doc, s)
    out = {"built_at": doc.get("built_at"), "reported_at": _now_iso(),
           "go": go, "why_not": why, "bar": doc.get("bar"),
           "counts": doc.get("counts"), "metrics": {
               k: v for k, v in s.items()
               if k not in ("mutants", "negatives", "gold")},
           "gold": s["gold"], "mutants": s["mutants"],
           "negatives": s["negatives"]}
    d = cal_root(root)
    (d / "report.md").write_text(render_report(doc, s), encoding="utf-8")
    qc_store.write_json_atomic(d / "result.json", out)
    return out


# ---------------------------------------------------------------------------
# CLI


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true",
                    help="prepare the gold set, the mutants, the negatives "
                         "and the controls. No agy call.")
    ap.add_argument("--run", action="store_true",
                    help="judge the prepared set (the ONLY part that costs "
                         "quota); resumable, never uploads")
    ap.add_argument("--report", action="store_true",
                    help="write _qc/calibration/report.md and result.json and "
                         "print GO / NO-GO")
    ap.add_argument("--chapters", action="append",
                    help="tags, comma separated; default is §6's list")
    ap.add_argument("--mutants", type=int, default=MIN_MUTANTS)
    ap.add_argument("--negatives", type=int, default=MIN_NEGATIVES)
    ap.add_argument("--no-fetch", action="store_true",
                    help="use only cached material; never call gh")
    ap.add_argument("--model", help="override the agy model (§4: pro-high)")
    ap.add_argument("--page-limit", type=int)
    ap.add_argument("--scope", help="build a set for these assets only: "
                    "'study' (guide, flashcards, quiz) or a comma list")
    ap.add_argument("--no-gold", action="store_true",
                    help="leave out the adjudicated gold items (and their chapters)")
    ap.add_argument("--no-controls", action="store_true",
                    help="leave out the negative-control runs (they cost calls)")
    ap.add_argument("--qc-dir", help="use this _qc/ tree instead of the repo's")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even though the current set has already been "
                         "judged; the old judgments are then ignored and the "
                         "run starts again")
    a = ap.parse_args(argv)
    if not (a.build or a.run or a.report):
        ap.error("give --build, --run or --report")

    root = Path(a.qc_dir) if a.qc_dir else None
    asked = []
    for chunk in (a.chapters or []):
        asked.extend(x.strip() for x in str(chunk).split(",") if x.strip())

    if a.build:
        import qc_run
        doc = build(tags=asked or DEFAULT_CHAPTERS, root=root,
                    fetch=not a.no_fetch, n_mutants=a.mutants,
                    n_negatives=a.negatives, force=a.force,
                    scope=qc_run.parse_scope(a.scope),
                    with_gold=not a.no_gold, with_controls=not a.no_controls)
        c = doc["counts"]
        _print(f"נבנה ב-{doc['build_seconds']}s: {c['mutants']} מוטנטים, "
               f"{c['negatives']} שליליים, {c['gold_total']} פריטי זהב "
               f"({c['gold']}), {c['controls']} הרצות בקרה, "
               f"{c['discarded']} מוטנטים נפסלו")
    if a.run:
        # Without --chapters the run judges exactly what --build prepared,
        # which includes the gold chapters build() added: a run against
        # DEFAULT_CHAPTERS would leave §6's gold recall with no denominator.
        code = run_prepared(root=root, tags=asked or None, model=a.model,
                            page_limit=a.page_limit)
        if code != EXIT_OK:
            report(root=root)
            return code
    if a.report or a.run:
        out = report(root=root)
        _print(("GO" if out["go"] else "NO-GO")
               + ("" if out["go"] else " — " + "; ".join(out["why_not"])))
        _print(f"דוח: {cal_root(root) / 'report.md'}")
        return EXIT_OK if out["go"] else EXIT_NOGO
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
