#!/usr/bin/env python3
"""The judge call, and the code that refuses to take its word for anything.

WHY THIS MODULE DISTRUSTS ITS OWN ANSWER

Measured 2026-09-18 (agy 1.2.5, one page of StahlKids): a call that never
received its PDF returned status SUCCESS, confident JSON, a "verbatim" quote
that exists in no book, and ruled a wrong flashcard *supported*. Nothing in the
reply said so. The same day, a reviewer working from its own knowledge reported
9 errors in 17 released chapters; re-adjudicated against the full text of the
books, 3 were contradicted, 4 were **supported** and 2 were item-logic problems.
Both failures are the same failure: a fluent answer nobody could check.

So the proof is layered, and each layer is checked by code that cannot be
talked out of it:

  agy_attach   the file was ingested       (sha256 under .tempmediaStorage)
  qc_sources   the pages were turned       (a stamp code that exists nowhere else)
  qc_judge     the answer is anchored      every quote is found, by us, in text
               we extracted ourselves — the echo inside the unit it names, the
               source quote inside the printed-page window its evidence code
               points at.

Anything that fails a layer is `unverified`: never shown to the learner, never
patched, and — if the judge called it `high` — a reason to stop the chapter
(§2.3, §5.8.3). Failing toward "not verified" is always the cheap direction: a
missed error costs one owner review, a wrong "correction" costs the material.

WHY DROPPING MATTERS AS MUCH AS FINDING

The podcast project's judge produced rows whose own source text quoted the
excerpt CONFIRMING what was said, word for word, and dulcan-027 shipped with
two of them. Here the same artefact appears as "2-8 שבועות is wrong, the source
says 2-8 weeks". §5.8.4 drops those before anyone counts severities, and
`_clean_discrepancies` is ported from qc_book_review.py for exactly that. The
drop is restricted to `contradicted`: in a `source_conflict` the first quote is
SUPPOSED to agree with the material — that is what makes the second one a
conflict — and a confirmation filter would delete every real conflict we have.

WHAT THE JUDGE IS NEVER ASKED TO DO

Resolve a conflict, rewrite an item, or weigh a book against newer literature.
Owner decision, 2026-09-18: conflicts are shown with both pages, never fixed.
StahlKids p.69 ("2-8 weeks") against p.74 ("8-12 weeks") is one book disagreeing
with itself about atomoxetine onset, and the right output is both quotes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agy_attach  # noqa: E402  the call itself, and the ingestion proof
import qc_books  # noqa: E402   printed pages, Hebrew book names, slices
import qc_cite  # noqa: E402    citations, so a page number is not a "value"
import qc_lint  # noqa: E402    key_terms and the citation blanker
import qc_norm  # noqa: E402    find_quote, locked_values, values_dispute
import qc_sources  # noqa: E402 canary_request, NONCE_MARKER, search_pages

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass


# §4 and §5.8: "Rules and schema are inline and under 10,000 characters."
# prompt_for() enforces it rather than documenting it, because the failure mode
# of an over-long argv is truncation (agy_attach.MAX_CMDLINE) and a judge that
# received half its schema answers anyway.
MAX_PROMPT_CHARS = 10000
# What is left for the unit list once the rules, the checklists, the schema and
# the canary are on the page. Measured on the longest real call this repo has
# (029's guide + facts, 41 units with Hebrew headings): the list is 1.6 KB.
UNIT_LIST_BUDGET = 2200

# §5.8's output schema. Findings are capped in the PROMPT ("the 40 that matter
# most"), never here: a 41st finding that arrived anyway is still a finding, and
# the 2026-08-25 podcast run that returned 1,310 lines is the reason the cap is
# asked for at all, not a reason to delete evidence on arrival.
MAX_FINDINGS = 40
COVERAGE_STATUSES = ("supported", "contradicted", "unsupported",
                     "source_conflict", "not_checkable")
FINDING_KINDS = ("contradicted", "unsupported", "source_conflict")
SEVERITIES = ("high", "medium", "low")

# §5.8.2: "Approval requires unchecked locked-value units <= a threshold set in
# calibration (start: 0)."
MAX_UNCHECKED = 0

# The window a source quote is matched against: printed page +-1, never a single
# page. The Dulcan CAMS sentence that contradicts 038 Q9 is split across printed
# 330 and 331 and is verbatim on neither alone.
QUOTE_RADIUS = 1

DEFAULT_MODEL = agy_attach.DEFAULT_MODEL

# A quote that is not in the material at all. §2.2 wants a quote from BOTH
# sides of every claim, and the 2026-09-18 invented-quote failure was on the
# source side; `said` is checked the same way for the same reason.
_NOT_IN_UNIT = "said is not in the unit it names"


class JudgeError(ValueError):
    """The call cannot be assembled — no units, no bundle, no manifest.

    Never swallowed. A chapter that stops is recoverable; a chapter judged
    against the wrong bundle is a verdict nobody can trust afterwards.
    """


def _attr(obj: Any, *names: str, default=None):
    """Read a field off a CallPlan, a Manifest or the dict either became.

    qc_run normalizes qc_sources' dataclasses into its own PlannedCall and
    hands BOTH across (`call` and `plan`), and a resumed run reads them back
    out of JSON. One reader here keeps every line below indifferent to which.
    """
    for n in names:
        if isinstance(obj, dict):
            if n in obj:
                return obj[n]
        elif hasattr(obj, n):
            return getattr(obj, n)
    return default


# ---------------------------------------------------------------------------
# the prompt
#
# Ported from psychiatry-book-podcasts/scripts/qc_book_review.py:36-121 and
# adapted from audio-against-a-chapter to text-against-a-page-bundle. Three
# things changed in the port and each one is load-bearing:
#
#  * "the excerpt" became "the bundle", and the bundle is many pages from
#    several books. So a claim the bundle supports ON ANOTHER PAGE is not a
#    discrepancy — that rule existed there too, but here it is the difference
#    between 4 false errors and 0 (the 2026-09-18 re-adjudication).
#  * every quote must be VERBATIM, because here the quotes are checked. The
#    podcast judge was asked for "quote or close paraphrase"; a paraphrase here
#    fails find_quote and costs the chapter its verdict.
#  * the canary and the nonce are new. They are the only part of the answer
#    that cannot be produced without opening the files.


_RULES = """\
QC REVIEW. You are given two files and nothing else.

  bundle.pdf   photocopies of textbook pages. The first page(s) are an index
               (bundle page -> book and printed page). EVERY source page carries
               a stamp in its top margin: "<book> p.<printed page> - <CODE>",
               where CODE is six characters. The codes exist nowhere else.
  artifacts.md the material under review: one block per unit, starting with
               "### <unit id>", holding that unit's text exactly as it was
               published.

THE BUNDLE IS THE GROUND TRUTH, IN BOTH DIRECTIONS. Your own psychiatric
knowledge is never evidence here and never penalizes the material. If the
bundle supports a claim, that claim is correct FOR THIS REVIEW even where you
believe otherwise; the books may be newer than you, or may report something you
do not recognise. If you catch yourself reasoning "this is wrong because I know
X", stop: unless a bundle page says X, you have no finding.

LOCKED VALUES are what a wrong answer is usually made of: numbers, doses,
durations, frequencies, percentages, rates, effect sizes, criteria counts and
thresholds, trial names and their arms, and the drug named in a claim. A locked
value that contradicts the page it is cited from is the finding this review
exists to catch.

NOT a discrepancy, and reporting one is itself an error:
  * paraphrase, summary, or Hebrew translation of an English source;
  * pedagogical framing, mnemonics, exam tips, "remember that...";
  * a correct fact that the bundle states on a DIFFERENT page than the one the
    material cites - the material is judged against the bundle, not against one
    page of it;
  * material the bundle simply does not discuss (that is `unsupported`, and
    only when a locked value is involved);
  * formatting, markdown, LaTeX, spelling, style, ordering, missing coverage.

SEVERITY:
  high    a learner acting on this would be clinically misled, or would answer
          a board question wrong: a wrong dose, number, duration or criteria
          count; a reversed direction of effect; a wrong drug; a wrong answer
          key or a rationale that defends one.
  medium  inaccurate but not decisive for a clinical action or an exam answer.
  low     style, wording, emphasis.

WHEN THE SOURCE IS THE KARTISIOT SUMMARY (stamped "סיכומי כרטיסיות שלב ב"), say so by name in
`problem` and in `correction`. It is a legitimate source and the learner is
entitled to know a correction came from it rather than from a textbook.

QUOTE VERBATIM ON BOTH SIDES. `echo`, `said` are copied character for character
out of artifacts.md; `source_quote`, `second_quote` are copied character for
character off the bundle page whose code you give. Code re-finds all four in the
files. A quote that is retyped from memory, translated, shortened or tidied
fails, and the whole answer is discarded and re-asked - so copy, never compose.
Use "..." only to elide a middle, and keep both sides in order.
"""

# The per-artifact checklists of §5.8. Keyed by the uid prefix rather than by
# call kind, because a Synopsis-heavy chapter (013, 070) gets an S call holding
# whatever units lean on the Synopsis, and a quiz item judged with the guide's
# checklist is judged against the wrong questions.
_CHECKLIST = {
    "Q": """\
QUIZ ITEMS (### Q<n>) - judge the KEY and EVERY distractor:
  multiple_choice   is the keyed option correct on the bundle, and is each
                    distractor actually wrong on the bundle? A rationale that
                    defends the key with a wrong fact is a finding on the item.
  multiple_select   every option, one verdict each: keyed options correct,
                    unkeyed options wrong.
  fill_in_the_blank bestAnswer correct; every accepted answer correct AND
                    specific enough; the stem admits exactly one answer.
  short_answer      modelAnswer and every requiredAttribute correct; every
                    listed misconception really is a misconception.
  every type        the hint is correct and does not give the answer away.""",
    "C": """\
FLASHCARDS (### C<n>): the back is correct on the bundle; one fact per card;
the card is about this chapter's subject.""",
    "G": """\
STUDY-GUIDE SECTIONS (### G:<heading>), one verdict per section: clinical
errors; every locked value against the bundle; contradictions with another
section of the same guide; content that belongs to a different chapter.""",
    "F": """\
FACTS (### F:<key path>): one verdict per fact, against the bundle.""",
    "L": """\
PLATE LEGENDS (### L:<file>:<row>), one verdict per row. The learner reads
these as exam numbers, so a wrong number here is `high`.""",
}

_SCHEMA = """\
ANSWER WITH ONE JSON OBJECT AND NOTHING ELSE:

{"canary": [{"page": "<book> p.<printed page>", "code": "<its stamped code>"}],
 "nonce": "<the token on the QC-NONCE line in artifacts.md>",
 "coverage": [{"unit": "<unit id>",
               "status": "supported|contradicted|unsupported|source_conflict|not_checkable",
               "echo": "<>=30 characters copied verbatim from THIS unit>",
               "evidence_code": "<code of the page you judged it on>",
               "source_quote": "<verbatim from that page>"}],
 "findings": [{"id": "<short, unique>", "unit": "<unit id>",
               "kind": "contradicted|unsupported|source_conflict",
               "severity": "high|medium|low", "category": "<2-3 words>",
               "said": "<verbatim from the unit>",
               "evidence_code": "<code>", "source_quote": "<verbatim>",
               "second_evidence_code": "<code, source_conflict only>",
               "second_quote": "<verbatim, source_conflict only>",
               "problem": "<one sentence>",
               "correction": "<what the material should say>"}]}

COVERAGE IS EVERY UNIT: exactly one row for each unit id listed above, even
when the bundle cannot settle it (`not_checkable`). A unit whose row is missing
discards the whole answer. Note that a guide section keeps its own "### "
subheads inside its text - those are not units, and the unit ids are the list
above. When a unit carries a locked value and you call it `supported`, the row
MUST carry the evidence_code and the verbatim source_quote that support it.

STATUS MEANINGS:
  supported       the bundle says it.
  contradicted    a bundle page says something different. `said` holds the
                  material's value, `source_quote` holds the page's, and
                  `correction` uses the page's.
  unsupported     no bundle page states this locked value at all.
  source_conflict two bundle pages disagree with each other about it. Give
                  both: evidence_code/source_quote and
                  second_evidence_code/second_quote. Do not choose between them.
  not_checkable   the bundle does not cover the claim either way.

FINDINGS: only real problems, most severe first, at most 40. A supported unit
produces a coverage row and NO finding. Do not pad: a wording preference in a
slot that a wrong dose needs is a defect in this review.
"""


def _uid_prefix(uid: str) -> str:
    """Q1 -> Q, `G:רקע` -> G, `L:sketchy_main.txt:3` -> L."""
    return (uid or "?")[:1].upper()


def _checklists(kind: str, unit_ids: Sequence[str]) -> str:
    """Only the checklists the call's units actually need.

    A B call that also carried one legend row would otherwise be judged without
    the row checklist, and the prompt budget is too tight to always send all
    five: the full set is 1.4 KB of the 10,000.
    """
    want = []
    for uid in unit_ids:
        p = _uid_prefix(uid)
        if p in _CHECKLIST and p not in want:
            want.append(p)
    if not want:
        raise JudgeError(f"call {kind}: no unit id has a known prefix "
                         f"({list(unit_ids)[:4]}); qc_units and qc_judge "
                         f"disagree about what a unit is called")
    return "\n\n".join(_CHECKLIST[p] for p in sorted(want, key="QCGFL".index))


def _unit_list(unit_ids: Sequence[str], budget: int = UNIT_LIST_BUDGET) -> str:
    """The uids, comma separated, truncated only as a last resort.

    The list is here so the judge can count its own coverage rows before it
    answers; artifacts.md remains the authority, which is what makes truncating
    safe rather than silently lossy.
    """
    ids = [str(u) for u in unit_ids]
    full = ", ".join(ids)
    if len(full) <= budget:
        return full
    kept: list[str] = []
    used = 0
    for uid in ids:
        if used + len(uid) + 2 > budget - 80:
            break
        kept.append(uid)
        used += len(uid) + 2
    return (", ".join(kept) + f", ... and {len(ids) - len(kept)} more - the "
            f"authoritative list is every '### ' heading in artifacts.md")


def prompt_for(call_plan: Any, canary_request: Sequence[tuple],
               unit_ids: Sequence[str], kind: str, *,
               max_chars: int = MAX_PROMPT_CHARS) -> str:
    """The inline rules, checklists, schema, canary and nonce request.

    `canary_request` is qc_sources.canary_request()'s output — (book, printed,
    expected_code) triples — and ONLY the first two are written into the
    prompt. A prompt that named the codes would be a prompt the judge could
    answer without opening the bundle, which is the one thing the canary exists
    to make impossible.
    """
    ids = [str(u) for u in (unit_ids or [])]
    if not ids:
        raise JudgeError(f"call {kind}: no units to judge")
    asked = [(str(b), p) for b, p, _code in (canary_request or [])]
    if not asked:
        raise JudgeError(f"call {kind}: no canary pages; an unchecked bundle "
                         f"is an unchecked answer (§5.8.1)")

    pages = ["{} p.{}".format(qc_books.HEBREW_NAME.get(b, b), p)
             for b, p in asked]
    # The size of the bundle, from the plan rather than from the file. A judge
    # told "39 source pages" is a judge that can notice it only opened four —
    # cheap, and the 2026-09-18 failure was a judge that opened none.
    n_pages = len(_attr(call_plan, "pages", default=[]) or [])
    body = (
        f"{_RULES}\n"
        f"WHAT THIS CALL JUDGES ({kind}), {len(ids)} units against "
        f"{n_pages} source pages:\n"
        f"{_unit_list(ids)}\n\n"
        f"{_checklists(kind, ids)}\n\n"
        f"BEFORE ANYTHING ELSE, PROVE YOU OPENED BOTH FILES.\n"
        f"Find these pages in bundle.pdf and copy the six-character code "
        f"stamped at the top of each into `canary`:\n"
        f"  {'; '.join(pages)}\n"
        f"artifacts.md carries one line reading "
        f"\"{qc_sources.NONCE_MARKER} <token>\". Copy the token into `nonce`.\n"
        f"A wrong or missing code or token discards the entire answer, however "
        f"good the rest of it is.\n\n"
        f"{_SCHEMA}")
    if len(body) > max_chars:
        # Not a warning: agy_attach would still send it, CreateProcess would
        # truncate the tail (the schema) and the judge would answer in a shape
        # nothing can read. Raising here stops one chapter; sending it would
        # cost 25 minutes and produce an unjudged chapter anyway.
        raise JudgeError(
            f"call {kind}: prompt is {len(body)} characters, over the "
            f"{max_chars} limit of §4 with {len(ids)} units. Split the "
            f"call (qc_sources.plan) rather than trimming the rules.")
    return body


# ---------------------------------------------------------------------------
# reading the two files back


_HEADING = re.compile(r"(?m)^###[ \t]+(.*?)[ \t]*$")


def read_artifacts(path: Path | str, unit_ids: Sequence[str]) -> dict[str, str]:
    """artifacts.md -> {uid: the exact text the judge was shown}.

    Split on the KNOWN uids only. A study-guide section keeps its own `### `
    subheads (qc_units splits guides on `## ` alone — 029 has 14 of them), so a
    parser that trusted every `### ` line would cut Q-sections out of the middle
    of a guide unit and then fail every echo inside them.

    This is the text the coverage check searches, deliberately: qc_sources
    writes Unit.text byte-for-byte, and if the two ever disagree the file the
    judge actually read is the one that decides.
    """
    text = Path(path).read_bytes().decode("utf-8")
    wanted = {str(u) for u in unit_ids}
    marks: list[tuple[int, int, str]] = []
    for m in _HEADING.finditer(text):
        uid = m.group(1).strip()
        if uid in wanted:
            marks.append((m.start(), m.end(), uid))
    out: dict[str, str] = {}
    for i, (_start, end, uid) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        block = text[end:stop]
        # The nonce line sits BETWEEN blocks (qc_sources never writes it inside
        # one), so it lands at the tail of whichever block precedes it. Left in,
        # it would let an echo "match" a token that is not the unit's text.
        block = re.sub(rf"(?m)^{re.escape(qc_sources.NONCE_MARKER)}[ \t]+\S+[ \t]*$",
                       "", block)
        out[uid] = block.strip("\n")
    missing = [u for u in unit_ids if u not in out]
    if missing:
        raise JudgeError(
            f"{Path(path).name} has no block for {missing[:4]} "
            f"({len(missing)} of {len(unit_ids)}); the bundle and the unit list "
            f"describe different calls")
    return out


def unit_texts(units: Any, unit_ids: Sequence[str] | None = None,
               artifacts_path: Path | str | None = None) -> dict[str, str]:
    """{uid: text} from qc_units.Unit objects, a plain dict, or artifacts.md.

    qc_run hands the judge a CallPlan and nothing else — it never carries the
    Unit objects across — so the fallback is not a convenience, it is the path
    the orchestrator actually takes.
    """
    if isinstance(units, dict):
        got = {str(k): str(v) for k, v in units.items()}
        return {u: got[u] for u in unit_ids if u in got} if unit_ids else got
    if units:
        out = {}
        for u in units:
            uid = str(getattr(u, "uid", "") or (u.get("uid") if isinstance(u, dict) else ""))
            txt = getattr(u, "text", None)
            if txt is None and isinstance(u, dict):
                txt = u.get("text")
            out[uid] = str(txt or "")
        if unit_ids:
            out = {u: out[u] for u in unit_ids if u in out}
        return out
    if artifacts_path and unit_ids:
        return read_artifacts(artifacts_path, unit_ids)
    raise JudgeError("no units: pass qc_units.Unit objects, a {uid: text} map, "
                     "or an artifacts.md plus its unit ids")


# ---------------------------------------------------------------------------
# locating evidence


def _token(s: Any) -> str:
    """Codes and nonces compared on their alphanumerics only.

    qc_sources draws both from CODE_ALPHABET (upper case, no I/L/O/0/1), so
    case folding cannot collide two real codes, while "- K7QX2M" and "K7QX2M"
    are the same answer written by two models.
    """
    return "".join(ch for ch in str(s or "").upper() if ch.isalnum())


def manifest_pages(manifest: Any) -> list[dict]:
    if isinstance(manifest, dict):
        return list(manifest.get("pages") or [])
    return list(getattr(manifest, "pages", None) or [])


def page_by_code(manifest: Any, code: Any) -> dict | None:
    """The bundle page a returned evidence code names, or None."""
    want = _token(code)
    if not want:
        return None
    for page in manifest_pages(manifest):
        if _token(page.get("code")) == want:
            return page
    return None


def _printed_list(page: dict) -> list[int]:
    printed = page.get("printed")
    if isinstance(printed, (list, tuple)):
        return [int(p) for p in printed]
    return [int(printed)] if printed is not None else []


def _synopsis_text(page: dict, radius: int) -> str:
    """A Synopsis slice page and its neighbours.

    The Synopsis has no printed page numbers at all (qc_books), so a page of it
    exists only as page N of a slice PDF; the manifest carries the slice file
    name and `printed` is the position inside it.
    """
    import fitz                                   # lazy: only S calls need it

    name = page.get("slice_file") or ""
    path = qc_books.SLICES_DIR / name
    if not path.exists():
        for s in qc_books.synopsis_slices():
            if Path(s["path"]).name == name:
                path = Path(s["path"])
                break
    if not path.exists():
        raise FileNotFoundError(f"Synopsis slice {name!r} is not on this machine")
    idx = int(page.get("pdf_page", (_printed_list(page) or [1])[0] - 1))
    parts = []
    with fitz.open(path) as doc:
        for n in range(max(0, idx - radius), min(doc.page_count, idx + radius + 1)):
            parts.append(doc[n].get_text())
    return "\n".join(parts)


def source_text(page: dict, *, radius: int = QUOTE_RADIUS) -> str:
    """The text a quote on this bundle page is allowed to be found in.

    One window per printed page the bundle page carries, concatenated. A
    reflowed DSM/Dulcan bundle page can carry two printed pages, and their
    windows overlap; the duplicated text costs nothing (find_quote reports the
    first hit and the page label comes from the manifest either way) and the
    alternative — a second implementation of qc_norm.source_window that takes a
    range — is a copy that would drift from the one qc_lint and qc_fix use.
    """
    book = page.get("book")
    if book == "Synopsis":
        return _synopsis_text(page, radius)
    printed = _printed_list(page)
    if not printed:
        raise JudgeError(f"manifest page {page.get('bundle_page')} names no "
                         f"printed page")
    return "\n".join(qc_norm.source_window(book, p, radius) for p in printed)


def where(page: dict) -> str:
    """'StahlKids p.74' — how a finding names its evidence to the owner."""
    printed = _printed_list(page)
    label = "-".join(str(p) for p in printed) or "?"
    if page.get("book") == "Synopsis":
        return f"Synopsis ch.{page.get('slice_ref') or '?'} p.{label}"
    return f"{qc_books.HEBREW_NAME.get(page.get('book'), page.get('book'))} p.{label}"


def verify_quote(quote: str, page: dict, *, radius: int = QUOTE_RADIUS) -> dict:
    """{ok, match, score, reason, book, printed, bundle_page, page_label}.

    `match` survives into the verdict because 'fuzzy' is a real match through a
    two-column extraction, but a weaker one, and the report is entitled to the
    difference (qc_norm.FUZZY_MIN_SCORE = 0.9).
    """
    rec = {"ok": False, "match": None, "score": 0.0, "reason": "",
           "book": page.get("book"), "printed": _printed_list(page),
           "bundle_page": page.get("bundle_page"), "page_label": where(page)}
    try:
        text = source_text(page, radius=radius)
    except Exception as exc:                                 # noqa: BLE001
        # Fail closed and say why: on a machine without `Text BOOKS/` every
        # quote is unverifiable, and a verdict that called them all verified
        # would approve a chapter nobody checked (§2.3).
        rec["reason"] = f"source unreadable: {exc}"
        return rec
    hit = qc_norm.find_quote(quote or "", text)
    rec.update(match=hit["match"], score=hit["score"],
               reason=hit["reason"], ok=bool(hit["match"]))
    return rec


# ---------------------------------------------------------------------------
# validation: what makes an answer worth reading at all (§5.8.1, §5.8.2)


def canary_problems(data: dict, request: Sequence[tuple],
                    manifest: Any = None) -> list[str]:
    """Every requested code must come back, on a page label that fits it.

    The code is the proof; the label is a cross-check on a judge that read the
    pages but listed the codes in the wrong order. A label naming the BUNDLE
    page rather than the printed one is accepted — both numbers are ours and
    both mean the judge was looking at the right sheet of paper.
    """
    rows = data.get("canary")
    if not isinstance(rows, list) or not rows:
        return ["canary: no codes returned"]
    by_code: dict[str, list[str]] = {}
    for row in rows:
        if isinstance(row, dict):
            by_code.setdefault(_token(row.get("code")), []).append(
                str(row.get("page") or ""))
        else:                              # a bare list of codes, seen in the wild
            by_code.setdefault(_token(row), []).append("")
    problems = []
    for book, printed, code in request or []:
        labels = by_code.get(_token(code))
        if labels is None:
            problems.append(f"canary: code for {book} p.{printed} not returned")
            continue
        page = page_by_code(manifest, code) if manifest is not None else None
        ok_numbers = {str(printed)}
        if page:
            ok_numbers.update(str(p) for p in _printed_list(page))
            if page.get("bundle_page"):
                ok_numbers.add(str(page["bundle_page"]))
        for label in labels:
            digits = set(re.findall(r"\d+", label))
            if digits and not (digits & ok_numbers):
                problems.append(f"canary: code for {book} p.{printed} came "
                                f"back on page {label!r}")
    return problems


def nonce_problem(data: dict, nonce: str) -> str | None:
    got = _token(data.get("nonce"))
    if not got:
        return "nonce: not returned"
    if got != _token(nonce):
        return f"nonce: {data.get('nonce')!r} is not the token in artifacts.md"
    return None


def _echo_ok(echo: str, text: str) -> dict:
    """§5.8.2's echo rule: >= 30 normalized characters, or the whole unit.

    `whole_unit` is for the units that are legitimately shorter than the
    threshold — a fact leaf, a legend row — where "quote 30 characters" is not
    a thing the judge can do (integration note from qc_norm).
    """
    whole = len(qc_norm.norm(text)) < qc_norm.MIN_QUOTE_CHARS
    return qc_norm.find_quote(echo or "", text, whole_unit=whole)


def coverage_problems(data: dict, texts: dict[str, str]) -> list[str]:
    """Exactly one row per unit, each echoing text that is really in that unit.

    This is the half of the proof that says the judge read artifacts.md, and it
    is why coverage is uncapped while findings are not: a judge that answers
    about 12 of 41 units has not judged the chapter, however good those 12 are.
    """
    rows = data.get("coverage")
    if not isinstance(rows, list):
        return ["coverage: missing"]
    seen: dict[str, int] = {}
    problems: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            problems.append(f"coverage: row is not an object ({row!r:.40})")
            continue
        uid = str(row.get("unit") or "").strip()
        if uid not in texts:
            problems.append(f"coverage: row for {uid!r}, which is not a unit "
                            f"of this call")
            continue
        seen[uid] = seen.get(uid, 0) + 1
        if seen[uid] > 1:
            problems.append(f"coverage: {uid} has {seen[uid]} rows")
            continue
        status = str(row.get("status") or "")
        if status not in COVERAGE_STATUSES:
            problems.append(f"coverage: {uid} has status {status!r}")
        hit = _echo_ok(row.get("echo"), texts[uid])
        if not hit["match"]:
            problems.append(f"coverage: {uid} echo is not in the unit "
                            f"({hit['reason']})")
    missing = [u for u in texts if u not in seen]
    if missing:
        problems.append(f"coverage: {len(missing)} units have no row "
                        f"({', '.join(missing[:4])})")
    return problems


def make_validator(request: Sequence[tuple], nonce: str,
                   texts: dict[str, str], manifest: Any = None) -> Callable:
    """The `validate` agy_attach calls before it accepts an attempt.

    Returning a reason here costs one content attempt (3 by default) and then
    the chapter is `unjudged` — which is the point: §5.8.1 and §5.8.2 both end
    "discard, retry, then unjudged", and neither a missed canary nor a missing
    coverage row is something a human can repair afterwards from the reply.
    """
    def validate(data: Any) -> str | None:
        if not isinstance(data, dict):
            return "schema: the reply is not a JSON object"
        problems = canary_problems(data, request, manifest)
        bad_nonce = nonce_problem(data, nonce)
        if bad_nonce:
            problems.append(bad_nonce)
        problems += coverage_problems(data, texts)
        if not problems:
            return None
        head = "; ".join(problems[:3])
        return (head if len(problems) <= 3
                else f"{head} (+{len(problems) - 3} more)")
    return validate


# ---------------------------------------------------------------------------
# §5.8.4: dedup and the noise filter, before anything is counted
#
# Ported from qc_book_review.py's _clean_discrepancies. Two changes:
#   * the field is `source_quote`, not `source`;
#   * it runs on `contradicted` only. There, every row was a discrepancy. Here,
#     a `source_conflict`'s first quote is meant to AGREE with the material —
#     that agreement is what makes the second quote a conflict — so the
#     confirmation filter would delete exactly the findings §2 says to show.

# Meta-commentary only — phrases a JUDGE writes about a quote, never phrases a
# textbook could contain. Here `source_quote` is verbatim book text, so a
# marker like "supports the" or a bare "תומך" would delete a real finding whose
# page happens to say "the evidence supports the use of...". A Kartisiot quote
# is Hebrew, so the Hebrew markers have to clear the same bar.
_CONFIRMATION_MARKERS = (
    "תואם את הנאמר", "זה תואם", "תואם לנאמר", "זהה לנאמר",
    "matches what was said", "consistent with what was said",
    "confirms what the material",
)
_CONTRADICTION_MARKERS = (
    "לא מופיע", "אינו", "אינם", "שגוי", "במקום", "עם זאת", "אך ", "אלא",
    "not supported", "does not", "incorrect",
)
_NOT_X = re.compile(r"^(.+?),?\s*(?:לא|not)\s+(.+)$")


def _flat(s: Any) -> str:
    return " ".join(str(s or "").split()).strip(" .,:;\"'()").lower()


def _clean_discrepancies(items: Iterable[Any]) -> tuple[list[dict], list[dict]]:
    """(kept, dropped). Only the noise goes; anything ambiguous stays."""
    kept, dropped = [], []
    for d in items or []:
        if not isinstance(d, dict):
            continue
        said, src = _flat(d.get("said")), _flat(d.get("source_quote"))
        if not said and not src:
            # Recorded rather than skipped: §5.10's verdict has to account for
            # every row the judge returned, and a finding that vanished without
            # a reason is indistinguishable from one this code never saw.
            dropped.append({**d, "dropped_because": "empty on both sides"})
            continue
        if str(d.get("kind") or "") != "contradicted":
            kept.append(d)
            continue
        if said and src and said == src:
            dropped.append({**d, "dropped_because": "said and source_quote are "
                                                    "the same text"})
            continue
        # "the journal is <X>, not <X>" — the same name on both sides of the
        # contrast, so nothing is actually being corrected.
        m = _NOT_X.match(src)
        if m and len(_flat(m.group(2))) >= 6 and _flat(m.group(1)).endswith(
                _flat(m.group(2))):
            dropped.append({**d, "dropped_because": "the source quote corrects "
                                                    "a value to itself"})
            continue
        if (any(k in src for k in (_flat(x) for x in _CONFIRMATION_MARKERS))
                and not any(k in src for k in
                            (_flat(x) for x in _CONTRADICTION_MARKERS))):
            dropped.append({**d, "dropped_because": "the source quote only "
                                                    "confirms the material"})
            continue
        kept.append(d)
    return kept, dropped


def _dedup(items: Sequence[dict]) -> tuple[list[dict], list[dict]]:
    """One row per (unit, kind, said, source_quote). The judge repeats itself
    across a guide section that states the same value three times, and three
    notes on one error read to the owner as three errors."""
    seen: set[tuple] = set()
    kept, dropped = [], []
    for d in items:
        key = (str(d.get("unit") or ""), str(d.get("kind") or ""),
               qc_norm.norm(d.get("said")), qc_norm.norm(d.get("source_quote")))
        if key in seen:
            dropped.append({**d, "dropped_because": "duplicate of an earlier "
                                                    "finding on the same unit"})
            continue
        seen.add(key)
        kept.append(d)
    return kept, dropped


# ---------------------------------------------------------------------------
# §5.8.3: the findings, one kind at a time


def _search_text(said: str, unit_text: str) -> str:
    """What the `unsupported` full-text search is run on.

    The claim itself, plus the unit's English names when the claim carries
    fewer than MIN_TERM_HITS of its own. qc_sources' window needs two key terms
    on the page, and this material writes drug and trial names in English
    inside Hebrew sentences — a `said` of "2-8 שבועות" names nothing, while the
    item around it names Atomoxetine. Appending TERMS only (never the unit's
    text) is deliberate: the values searched for must stay the claim's own.
    """
    if len(qc_lint.key_terms(said or "")) >= qc_sources.MIN_TERM_HITS:
        return said or ""
    return (said or "") + "\n" + " ".join(qc_lint.key_terms(unit_text or ""))


def _found_elsewhere(said: str, unit_text: str) -> list[str]:
    """Page labels where the claim's value sits beside its key terms.

    §5.8.3: "code searches the full text of every book for the value within the
    key-term window. Found -> finding dropped, page logged as evidence."
    """
    text = _search_text(said, unit_text)
    hits = qc_sources.search_pages(text, qc_cite.parse(text))
    return [f"{b} p.{p}" for (b, p) in sorted(hits)]


def _verified(rec: dict, why: str = "verified") -> dict:
    rec["verified"], rec["verification"] = True, why
    return rec


def _unverified(rec: dict, why: str) -> dict:
    rec["verified"], rec["verification"] = False, why
    return rec


def _check_said(rec: dict, texts: dict[str, str]) -> dict | None:
    """§2.2's material side: the claim has to be in the unit it names.

    whole_unit, because `said` is often one value ("2-8 שבועות") and the
    30-character floor is about quoting a PAGE, not about quoting a number the
    item really states.
    """
    unit = str(rec.get("unit") or "")
    text = texts.get(unit)
    if text is None:
        return _unverified(rec, f"unit {unit!r} is not in this call")
    if not qc_norm.norm(rec.get("said")):
        return _unverified(rec, "said is empty")
    hit = qc_norm.find_quote(rec.get("said") or "", text, whole_unit=True)
    if not hit["match"]:
        return _unverified(rec, _NOT_IN_UNIT)
    rec["said_match"] = hit["match"]
    return None


def _verify_contradicted(rec: dict, manifest: Any, texts: dict[str, str]) -> dict:
    page = page_by_code(manifest, rec.get("evidence_code"))
    if page is None:
        return _unverified(rec, f"evidence code {rec.get('evidence_code')!r} "
                                f"is not stamped on any page of this bundle")
    hit = verify_quote(rec.get("source_quote") or "", page)
    rec["evidence"] = hit
    # "The recorded page is where code found the quote" (§5.8.3) — the manifest
    # page, not whatever the judge wrote in prose.
    rec["page"] = hit["page_label"]
    if not hit["ok"]:
        return _unverified(rec, f"source quote is not on {hit['page_label']} "
                                f"({hit['reason'] or 'not found'})")
    disp = qc_norm.values_dispute(rec.get("said") or "",
                                  rec.get("source_quote") or "")
    rec["value_dispute"] = {"disputed": disp["disputed"], "same": disp["same"]}
    if disp["same"]:
        # Belt and braces: the same-value drop already ran as a pre-pass, but
        # that one compares text and this one compares parsed values, so
        # "2-8 שבועות" against "2 to 8 weeks" only dies here.
        rec["dropped_because"] = "the source quote holds the same value as said"
        return _unverified(rec, "same value on both sides")
    if not disp["disputed"] and disp["said"]:
        # The material states a value; the page states values of other kinds.
        # Verified as a textual contradiction, flagged so qc_fix's Tier 2 (which
        # needs "the key contains the source's locked value") can refuse it.
        rec["value_dispute"]["comparable"] = False
    # §5.8.3: "`correction` uses the source's value." Recorded, not enforced:
    # refusing the finding would throw away a real error over its phrasing,
    # while qc_fix's patch checks are where a correction has to carry the value.
    rec["correction_matches_source"] = bool(
        qc_norm.values_dispute(rec.get("correction") or "",
                               rec.get("source_quote") or "").get("same"))
    return _verified(rec)


def _verify_unsupported(rec: dict, manifest: Any, texts: dict[str, str]) -> dict:
    text = texts.get(str(rec.get("unit") or ""), "")
    try:
        pages = _found_elsewhere(rec.get("said") or "", text)
    except Exception as exc:                                 # noqa: BLE001
        # No books on this machine, or a cold cache. The claim is NOT cleared:
        # an unsupported finding whose clearing search never ran goes to the
        # owner, because the alternative is dropping it unchecked (§2.3).
        rec["search_skipped"] = str(exc)[:200]
        return _verified(rec, "unsupported; the clearing search could not run")
    if pages:
        rec["found_on"] = pages
        rec["dropped_because"] = ("the value is in the books beside this "
                                  "claim's key terms: " + ", ".join(pages[:4]))
        return _unverified(rec, "found elsewhere")
    return _verified(rec, "unsupported; not found in any book beside its key terms")


def _verify_source_conflict(rec: dict, manifest: Any,
                            texts: dict[str, str]) -> dict:
    out = []
    for code_key, quote_key in (("evidence_code", "source_quote"),
                                ("second_evidence_code", "second_quote")):
        page = page_by_code(manifest, rec.get(code_key))
        if page is None:
            return _unverified(rec, f"{code_key} {rec.get(code_key)!r} is not "
                                    f"stamped on any page of this bundle")
        hit = verify_quote(rec.get(quote_key) or "", page)
        if not hit["ok"]:
            return _unverified(rec, f"{quote_key} is not on "
                                    f"{hit['page_label']} "
                                    f"({hit['reason'] or 'not found'})")
        out.append(hit)
    rec["evidence"], rec["second_evidence"] = out
    rec["page"], rec["second_page"] = out[0]["page_label"], out[1]["page_label"]
    disp = qc_norm.values_dispute(rec.get("source_quote") or "",
                                  rec.get("second_quote") or "")
    rec["value_dispute"] = {"disputed": disp["disputed"], "same": disp["same"]}
    if disp["disputed"]:
        return _verified(rec, "two verified quotes with different values")
    if disp["same"]:
        # `same`, not "both quotes carry values": two quotes stating a duration
        # and a dose have no comparable value at all (qc_norm compares only
        # inside a (kind, unit) bucket), and calling that agreement would hide
        # a real disagreement in the words around them.
        return _unverified(rec, "the two quotes state the same value")
    if qc_norm.norm(rec.get("source_quote")) == qc_norm.norm(rec.get("second_quote")):
        return _unverified(rec, "the two quotes are the same text")
    # Neither page states a comparable value: the books disagree in words
    # (a recommendation, an indication). Owner decision 2026-09-18 is that
    # conflicts are SHOWN, never resolved, so a textual one is still shown —
    # it costs one owner review, and hiding it costs the learner.
    rec["value_dispute"]["comparable"] = False
    return _verified(rec, "two verified quotes that disagree in words")


_VERIFIERS = {"contradicted": _verify_contradicted,
              "unsupported": _verify_unsupported,
              "source_conflict": _verify_source_conflict}


def verify(verdict: dict, manifest: Any, units: Any, *,
           call_id: str = "", unit_ids: Sequence[str] | None = None) -> dict:
    """Classify one judge answer against the bundle it was given (§5.8.3-4).

    Returns {findings, dropped, coverage, unchecked, counts, owner, fix,
    unverified_high, notes}. `findings` is what the orchestrator sees: the
    dropped rows are kept separately for the verdict record, because a dropped
    finding left in the list would be counted as `unverified` by qc_run and a
    dropped `high` would then stop the chapter for noise.
    """
    texts = unit_texts(units, unit_ids)
    raw = verdict.get("findings")
    raw = list(raw) if isinstance(raw, list) else []
    notes: list[str] = []
    if len(raw) > MAX_FINDINGS:
        notes.append(f"the judge returned {len(raw)} findings, over the "
                     f"{MAX_FINDINGS} the prompt asks for; every one of them "
                     f"was still put through verification")

    rows = [dict(f) for f in raw if isinstance(f, dict)]
    for i, rec in enumerate(rows):
        rec.setdefault("id", f"{call_id or 'x'}-{i + 1}")
    rows, dropped = _dedup(rows)
    kept, noise = _clean_discrepancies(rows)
    dropped += noise

    findings: list[dict] = []
    for rec in kept:
        kind = str(rec.get("kind") or "")
        if kind not in FINDING_KINDS:
            findings.append(_unverified(rec, f"unknown kind {kind!r}"))
            continue
        if str(rec.get("severity") or "") not in SEVERITIES:
            # Not fatal: §2.3 only forbids ACTING on an unverified finding, and
            # a severity we cannot read is safest read as the one that stops
            # the chapter rather than as the one that hides it.
            rec["severity"] = "high"
            rec["severity_repaired"] = True
        bad_said = _check_said(rec, texts)
        if bad_said is not None:
            findings.append(bad_said)
            continue
        rec = _VERIFIERS[kind](rec, manifest, texts)
        if rec.get("dropped_because"):
            dropped.append(rec)
            continue
        findings.append(rec)

    coverage, unchecked = _verify_coverage(verdict, manifest, texts, findings)
    counts: dict[str, int] = {}
    for rec in findings:
        counts[str(rec.get("kind"))] = counts.get(str(rec.get("kind")), 0) + 1
    return {
        "findings": findings,
        "dropped": dropped,
        "coverage": coverage,
        "unchecked": unchecked,
        "counts": counts,
        # Never "verified": qc_run reads `ok` OR `verified` off a judgment to
        # decide whether a whole chapter was judged, and an integer 0 there
        # would read as "not verified" on a clean chapter.
        "verified_findings": sum(1 for f in findings if f.get("verified")),
        "unverified_high": sum(1 for f in findings if not f.get("verified")
                               and f.get("severity") == "high"),
        "owner": [f["id"] for f in findings if f.get("verified")
                  and f.get("kind") in ("unsupported", "source_conflict")],
        "fix": [f["id"] for f in findings if f.get("verified")
                and f.get("kind") == "contradicted"
                and f.get("severity") in ("high", "medium")],
        "notes": notes,
    }


def _has_locked_values(text: str) -> bool:
    """Does this unit state a value that a source has to be checked against?

    Citations are blanked first. Without it every unit "has locked values" —
    `(StahlKids p. 74, Dulcan p. 716)` is three numbers — and the unchecked
    count, which gates approval at 0, would never reach zero for any chapter.
    """
    blanked = qc_lint._blank_citations(text, qc_cite.parse(text))
    return bool(qc_norm.locked_values(blanked))


def _verify_coverage(verdict: dict, manifest: Any, texts: dict[str, str],
                     findings: Sequence[dict]) -> tuple[list[dict], int]:
    """§5.8.2's second half: every `supported` claim on a locked value is
    checked, and a row that fails counts as unchecked.

    A unit counts as checked when its `supported` row carries a source quote we
    found on the page its code names, OR when it produced a verified finding —
    a contradicted unit was read against the source just as hard as a supported
    one, and counting it unchecked would gate approval on the chapters that
    worked best.
    """
    verified_units = {str(f.get("unit")) for f in findings if f.get("verified")}
    rows_in = verdict.get("coverage")
    rows_in = rows_in if isinstance(rows_in, list) else []
    by_uid = {str(r.get("unit")): r for r in rows_in if isinstance(r, dict)}
    out: list[dict] = []
    unchecked = 0
    for uid, text in texts.items():
        row = dict(by_uid.get(uid) or {"unit": uid, "status": "not_checkable"})
        row["unit"] = uid
        needs = _has_locked_values(text)
        row["locked_values"] = needs
        if not needs:
            row["checked"] = True
            out.append(row)
            continue
        if str(row.get("status")) == "supported":
            page = page_by_code(manifest, row.get("evidence_code"))
            if page is None:
                row["checked"] = False
                row["unchecked_reason"] = "no evidence code for a supported value"
            else:
                hit = verify_quote(row.get("source_quote") or "", page)
                row["evidence"] = hit
                row["page"] = hit["page_label"]
                row["checked"] = hit["ok"]
                if not hit["ok"]:
                    row["unchecked_reason"] = (
                        f"source quote is not on {hit['page_label']} "
                        f"({hit['reason'] or 'not found'})")
        else:
            row["checked"] = uid in verified_units
            if not row["checked"]:
                row["unchecked_reason"] = (
                    f"status {row.get('status')!r} with no verified finding")
        if not row["checked"]:
            unchecked += 1
        out.append(row)
    return out, unchecked


# ---------------------------------------------------------------------------
# the call


def judge(call_plan: Any, manifest: Any, artifacts_path: Path | str,
          nonce: str, units: Any, *,
          ask: Callable = agy_attach.ask_with_files,
          canary: Sequence[tuple] | None = None,
          model: str | None = None, tag: str = "qc",
          attempts: int = 3, raw_dir: Path | str | None = None,
          rng=None) -> dict:
    """One judge call, verified. Returns the verdict record qc_run stores.

    `ask` is injected so every test in tests/qc/test_qc_judge.py runs offline;
    the default is the real agy_attach call, which is the only thing in this
    module that costs money or quota.

    The verdict is a plain dict on purpose: qc_run reads it through `_get`,
    qc_store writes it to JSON, and neither should have to import this module.
    """
    call_id = str(_attr(call_plan, "call_id", default="A"))
    kind = str(_attr(call_plan, "kind", default="A"))
    uids = [str(u) for u in (_attr(call_plan, "units", default=[]) or [])]
    bundle = _attr(call_plan, "bundle")
    if not bundle:
        raise JudgeError(f"{call_id}: no bundle.pdf; qc_sources.build_bundle "
                         f"has not run for this call")
    artifacts_path = Path(artifacts_path)
    texts = unit_texts(units, uids, artifacts_path)
    request = list(canary) if canary else qc_sources.canary_request(manifest,
                                                                    rng=rng)
    prompt = prompt_for(call_plan, request, uids, kind)
    verdict = {
        "call_id": call_id, "kind": kind, "units": uids, "ok": False,
        "reason": "", "findings": [], "dropped": [], "coverage": [],
        "unchecked": 0, "counts": {}, "owner": [], "fix": [],
        "unverified_high": 0, "notes": [],
        "model": model or DEFAULT_MODEL,
        "prompt_sha256": prompt_sha256(prompt),
        "prompt_chars": len(prompt),
        "canary": {"requested": [[b, p] for b, p, _ in request],
                   "returned": [], "verified": False},
        "nonce": nonce,
    }
    t0 = time.time()
    res = ask(prompt, [Path(bundle), artifacts_path],
              model=model or DEFAULT_MODEL, attempts=attempts,
              validate=make_validator(request, nonce, texts, manifest),
              tag=tag, call_id=call_id)

    verdict.update(
        conversation_id=getattr(res, "conversation_id", None),
        workspace=getattr(res, "workspace", ""),
        agy_version=getattr(res, "agy_version", ""),
        attempts=int(getattr(res, "attempts", 0) or 0),
        duration_s=round(getattr(res, "duration_s", 0.0) or
                         (time.time() - t0), 1),
        tempmedia_ok=getattr(res, "tempmedia_ok", None),
        tool_audit=list(getattr(res, "tool_audit", []) or []),
        history=list(getattr(res, "history", []) or []))
    raw_path = _save_raw(res, raw_dir or artifacts_path.parent, call_id)
    if raw_path:
        verdict["raw_path"] = str(raw_path)

    data = getattr(res, "data", None)
    if not data:
        # §2.3 fail closed. The reason string reaches qc_run's circuit breaker,
        # which counts "canary|nonce|parse|json|schema" as "the judge is not
        # answering usefully" — so the words in make_validator's reasons are
        # part of the contract, not decoration.
        verdict["reason"] = (getattr(res, "diagnostic", "")
                             or "the judge returned nothing usable")
        return verdict

    verdict["canary"] = {
        "requested": [[b, p] for b, p, _ in request],
        "returned": [r for r in (data.get("canary") or [])],
        "verified": True}
    verdict.update(verify(data, manifest, texts, call_id=call_id,
                          unit_ids=uids))
    verdict["ok"] = True
    verdict["reason"] = (f"{len(verdict['findings'])} findings, "
                         f"{verdict['unchecked']} unchecked of "
                         f"{len(texts)} units")
    return verdict


def prompt_sha256(text: str) -> str:
    """§5.10 records the prompt hash beside the agy version, so a verdict can
    be tied to the exact rules that produced it. Editing _RULES changes it, and
    that is the point: two chapters judged by different rules are two different
    measurements, and the report has to be able to say so."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _save_raw(res: Any, directory: Path | str, call_id: str) -> Path | None:
    """The reply as it arrived, beside the bundle that produced it.

    §5.10 asks for a raw response path in every verdict. Written even for a
    rejected answer: the attempt that failed its canary is the only evidence of
    WHY a chapter came back unjudged, and agy's own conversation store is
    deleted minutes later to keep the disk under 91%.
    """
    text = getattr(res, "text", "") or ""
    if not text:
        return None
    try:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"response-{call_id}.txt"
        path.write_bytes(text.encode("utf-8"))
        return path
    except OSError:
        return None


# ---------------------------------------------------------------------------
# the chapter


def chapter_status(verdicts: Sequence[dict]) -> tuple[str, list[str]]:
    """'approved' | 'needs_owner' | 'unjudged', and why, fail closed.

    This is the JUDGE's view of a chapter and it stops at three words. qc_run
    adds `fixed` and `needs_regeneration` after qc_fix and the lint have had
    their say; here a verified error is still open, so a chapter that has one
    is `needs_owner` and not `approved` — §2.3's "an open verified error,
    conflict or unverified-high -> needs_owner, and the queue does not resume".

    No verdicts at all is `unjudged`, never `approved`: the failure this
    guards is a chapter that looks clean because nothing ran on it.
    """
    verdicts = list(verdicts or [])
    if not verdicts:
        return "unjudged", ["no judge call was answered"]
    reasons: list[str] = []
    bad = [v for v in verdicts if not v.get("ok")]
    if bad:
        for v in bad:
            reasons.append(f"{v.get('call_id', '?')}: "
                           f"{v.get('reason') or 'not verified'}")
        return "unjudged", reasons

    high = sum(int(v.get("unverified_high") or 0) for v in verdicts)
    owner = [i for v in verdicts for i in (v.get("owner") or [])]
    # high/medium only, the severities §5.9 lets qc_fix act on. A verified
    # `low` contradiction is recorded in the verdict and shown in the report,
    # but nothing downstream will ever mark it, so holding the chapter on one
    # would hold it forever — and §7's queue does not resume until every
    # chapter clears.
    open_errors = [f["id"] for v in verdicts for f in (v.get("findings") or [])
                   if f.get("verified") and f.get("kind") == "contradicted"
                   and f.get("severity") in ("high", "medium")]
    unchecked = sum(int(v.get("unchecked") or 0) for v in verdicts)

    if high:
        reasons.append(f"{high} unverified findings claimed high severity")
    if owner:
        reasons.append(f"{len(owner)} findings for the owner "
                       f"({', '.join(owner[:4])})")
    if open_errors:
        reasons.append(f"{len(open_errors)} verified contradictions "
                       f"({', '.join(open_errors[:4])})")
    if unchecked > MAX_UNCHECKED:
        reasons.append(f"{unchecked} units with a locked value were not "
                       f"checked against a source (limit {MAX_UNCHECKED})")
    if reasons:
        return "needs_owner", reasons
    return "approved", [f"{sum(len(v.get('coverage') or []) for v in verdicts)} "
                        f"units judged, nothing open"]


# ---------------------------------------------------------------------------
# the name qc_run binds (_JUDGE_NAMES = judge_call, judge_chapter, judge)


def judge_call(*, call=None, plan=None, tag: str = "qc", model: str | None = None,
               work_dir=None, round_no: int = 0, root=None, units=None,
               ask: Callable = agy_attach.ask_with_files, **_ignored) -> dict:
    """qc_run's adapter: keyword-only, tolerant of keywords it does not use.

    qc_run hands over the CallPlan it got back from qc_sources (`call`) and its
    own normalized copy (`plan`), and no Unit objects — which is why `units`
    defaults to None and the texts come back out of artifacts.md.
    """
    source = call if call is not None else plan
    if source is None:
        raise JudgeError("judge_call: no call to judge (qc_run passes `call`)")
    manifest = _attr(source, "manifest", default=None)
    if not manifest:
        raise JudgeError(f"{_attr(source, 'call_id', default='?')}: no manifest; "
                         f"without it an evidence code names no page")
    artifacts = _attr(source, "artifacts", "artifacts_md")
    if not artifacts:
        raise JudgeError(f"{_attr(source, 'call_id', default='?')}: no "
                         f"artifacts.md")
    nonce = (_attr(source, "nonce", default="")
             or (manifest.get("nonce") if isinstance(manifest, dict) else "")
             or "")
    if not nonce:
        raise JudgeError("no nonce: the artifacts half of the proof (§5.8.1) "
                         "cannot be checked")
    return judge(source, manifest, artifacts, nonce, units, ask=ask,
                 model=model, tag=tag)


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build and check a judge call. Offline unless --run.")
    ap.add_argument("call_dir", help="a directory holding bundle.pdf, "
                                     "artifacts.md and manifest.json")
    ap.add_argument("--prompt", action="store_true",
                    help="print the prompt that would be sent")
    ap.add_argument("--verify", metavar="REPLY.json",
                    help="classify a saved judge reply against this bundle")
    ap.add_argument("--run", action="store_true",
                    help="actually call agy (costs quota)")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    args = ap.parse_args()

    d = Path(args.call_dir)
    manifest = json.loads((d / "manifest.json").read_bytes().decode("utf-8"))
    uids = [str(u) for u in manifest.get("units") or []]
    plan = {"call_id": manifest.get("call_id", d.name),
            "kind": manifest.get("kind", "A"), "units": uids,
            "bundle": d / "bundle.pdf", "artifacts": d / "artifacts.md",
            "manifest": manifest, "nonce": manifest.get("nonce", ""),
            "pages": manifest.get("pages") or []}

    if args.verify:
        data = json.loads(Path(args.verify).read_bytes().decode("utf-8"))
        out = verify(data, manifest, unit_texts(None, uids, d / "artifacts.md"),
                     call_id=plan["call_id"], unit_ids=uids)
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            for f in out["findings"]:
                flag = "OK " if f.get("verified") else "?? "
                print(f"{flag}{f['id']} [{f.get('kind')}/{f.get('severity')}] "
                      f"{f.get('unit')} — {f.get('verification')}")
            for f in out["dropped"]:
                print(f"-- {f.get('id')} dropped: {f.get('dropped_because')}")
            print(f"{out['unchecked']} unchecked units, "
                  f"{out['unverified_high']} unverified high")
        return 0

    request = qc_sources.canary_request(manifest)
    text = prompt_for(plan, request, uids, plan["kind"])
    if args.prompt:
        print(text)
        return 0
    if args.run:
        v = judge(plan, manifest, plan["artifacts"], plan["nonce"], None)
        print(json.dumps(v, ensure_ascii=False, indent=2, default=str))
        return 0 if v.get("ok") else 1

    print(f"{plan['call_id']} [{plan['kind']}] {len(uids)} units, "
          f"{len(manifest.get('pages') or [])} pages, prompt {len(text)} chars "
          f"(limit {MAX_PROMPT_CHARS})")
    print("canary: " + "; ".join(f"{b} p.{p}" for b, p, _ in request))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
