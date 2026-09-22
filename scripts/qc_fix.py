#!/usr/bin/env python3
"""What happens to a chapter once the judge has proved it wrong. Spec §5.9.

OWNER DECISION 2 (2026-09-18), AND WHY EVERY LINE HERE OBEYS IT: "note first,
rewrite later". A verified error is shown at once, beside the item, with the
textbook quote and the printed page — and the material itself is left exactly as
it was published. Rewriting starts only after §6's calibration puts the verified
false-positive rate at or under 2% on >= 200 source-confirmed-correct units, and
that gate is a measurement, not a flag someone flips in a hurry. So Tier 1 is
complete and runs on every chapter; Tier 2 is scaffolding that proposes a patch
and stops, and `apply_tier2` refuses to touch an asset whatever the flag says.

WHY IDS COME BEFORE EVERYTHING. fixes.json, qc.json and the app's "תיקונים"
page all point at items. Pointing at them by index was tried in the podcast
project and broke the first time a quiz was regenerated with one question
dropped: every erratum from that chapter then named the wrong question, and
nobody noticed until the owner studied it. So the first thing done to a chapter
is `assign_ids`: sha1 of the item's stem (the card's front) at the moment of
publication, 16 hex, written into the asset and never recomputed. It is the same
identity `app/quiz_logic.item_id` computes, so an item this run stamped and an
item the app has to identify on its own resolve to one string.

WHY HYGIENE RUNS BEFORE THE JUDGE, NOT AFTER. §5.9 and qc_run's order. A finding
anchors on a span of text; clean the escaped LaTeX afterwards and every span
moves. Measured in the 2026-09-18 sample: chatbot sign-offs in 8 of 12 guides,
escaped LaTeX in 8 of 14, leaked `General Instruction Filter` headers, and
NotebookLM `[7]` markers. Each of those is also text the judge would be asked to
find in a textbook that never contained it — an `unsupported` finding that costs
an owner review and proves nothing.

WHY THE ANSWER ORDER IS REBALANCED IN THE SAME PASS. Key position over the 132
single-answer items of the 17 sampled chapters: A 98 / B 31 / C 3 / D 0.
Answering A every time scores 74%, so the quiz measures a habit rather than the
learner's knowledge. Reordering changes no content, but it DOES change the unit
text the judge reads and quotes back, so it has to happen before the bundle is
built or every coverage echo lands on stale text.

WHAT THIS MODULE MAY NOT DO. It may not decide anything. Every record it writes
comes from a finding that qc_judge already verified in code — quote found on the
page of its evidence code, value actually disputed (qc_norm.values_dispute).
Where it cannot place a note unambiguously it writes nothing and says
`needs_owner`; §2.3's fail-closed direction is "the owner reads it", never "the
program guessed".

    python scripts/qc_fix.py _qc/work/study-019-... --hygiene
    python scripts/qc_fix.py _qc/work/study-019-... --tier1 findings.json --write out/
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qc_cite                                            # noqa: E402
import qc_lint                                            # noqa: E402
import qc_norm                                            # noqa: E402
import qc_store                                           # noqa: E402
import qc_units                                           # noqa: E402

# Windows gives a redirected stdout the locale codec (cp1255 here) and a single
# Hebrew character then raises UnicodeEncodeError. Every note this module writes
# is Hebrew, and the CLI is always run into a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                         # noqa: BLE001
    pass


class FixError(qc_store.QcStoreError):
    """A fix that cannot be carried out safely. Never swallowed silently.

    A QcStoreError and not a bare ValueError because the other caller of
    `apply_fix` is qc_revert, which replays the fixes that stay active onto the
    pristine snapshot and catches QcStoreError to print `[STOP] <reason>` and
    exit 2. A revert that raised straight through would show the owner a
    traceback on the one day he is already unhappy with this program.
    """


# ---------------------------------------------------------------------------
# constants

# §5.8: "Only `contradicted` may be high/medium and reach qc_fix." Tier 1 also
# accepts `source_conflict` when a caller hands it one (§5.9: "For
# source_conflict, both quotes"), because showing the learner both pages is
# still a note and still changes nothing.
TIER1_KINDS = ("contradicted", "source_conflict")
FIXABLE_SEVERITIES = ("high", "medium")

# 16 hex, sha1 of the stripped stem — byte for byte what app/quiz_logic.item_id
# falls back to when an item carries no id. The two MUST agree: an item stamped
# here and the same item identified by the app have to be one string, or the
# errata page lists a correction against an item the quiz page cannot find.
ID_CHARS = 16

# The planned key positions are drawn from shuffled copies of this block, so
# every four consecutive rebalanced items use each position exactly once.
KEY_BLOCK = (0, 1, 2, 3)
# §5.9: "<= 3 keys per position per 10". A 10-item window can touch four blocks
# (partial, full, full, partial), which would let one position reach 4 — so the
# block order is searched rather than just shuffled. See `key_plan`.
BALANCE_WINDOW = 10
MAX_KEYS_PER_POSITION = 3

# A `said` shorter than this cannot anchor a guide footnote: qc_norm's own
# MIN_PIECE_CHARS is the floor below which a fragment matches by accident.
MIN_ANCHOR_CHARS = qc_norm.MIN_PIECE_CHARS

# How close a key term has to be to a repeated value for the two to be one
# claim (§5.9's cross-artifact sweep). 200 characters is about one released
# paragraph or one quiz option with its rationale.
SAME_CLAIM_RADIUS = 200

# §5.9 patch checks: "the diff touches only the fields named (quiz: stem,
# options, isCorrect, rationale, hint)". Anything else in a proposed patch is a
# rewrite of something nobody verified.
PATCH_FIELDS = {
    "quiz.json": ("question", "stem", "answerOptions", "options", "hint"),
    "flashcards.json": ("front", "back"),
    "facts.json": ("value",),
    "study_guide.md": ("text",),
}
PATCH_OPTION_FIELDS = ("text", "isCorrect", "rationale")

# Owner decision 4, 2026-09-18: "סיכומי כרטיסיות שלב ב is a legitimate source,
# and anything grounded in it must say so explicitly". These names are written
# into the guide markdown and into the legend note lines, which are plain text
# with no app logic behind them, so the Hebrew has to be in the note itself.
# app/quiz_logic.BOOK_HE carries the same table for the records the app reads;
# it cannot be imported here (app/ is deployed without scripts/ and vice versa).
BOOK_HE = {
    "DSM-5-TR": "DSM-5-TR",
    "Dulcan": "Dulcan's Textbook",
    "StahlNeuro": "Stahl — Essential Psychopharmacology",
    "StahlKids": "Stahl — Prescriber's Guide, ילדים ומתבגרים",
    "Synopsis": "Kaplan & Sadock — Synopsis",
    "Syllabus": "הסילבוס הרשמי",
    "Kartisiot": "סיכומי כרטיסיות שלב ב",
    "Stahl?": "Stahl (לא ברור איזה כרך)",
}

# The visible half of a Tier-1 note in a text asset. It carries the finding id
# so that qc_revert can replay or recognise exactly one note, and so the owner
# reading a legend can quote the id back.
NOTE_MARK = "⚠"
NOTE_HE = "תיקון QC"
_NOTE_LINE_RE = re.compile(r"(?m)^[ \t]*" + re.escape(NOTE_MARK) + r" "
                           + re.escape(NOTE_HE) + r" \[[0-9a-zA-Z_.\-]+\].*$\n?")
_FOOTNOTE_MARK_RE = re.compile(r"\[\^qc-[0-9a-zA-Z_.\-]+\]")


# ---------------------------------------------------------------------------
# reading a finding
#
# qc_judge's findings arrive as dicts; qc_run may hand over the same record with
# the verification fields merged in (`book`, `printed` — "the recorded page is
# where code found the quote", §5.8.3). Everything is read through `_f` so a
# record that spells a field the other documented way still resolves, rather
# than silently producing a note with an empty page number.


def _f(obj: Any, *names: str, default=None):
    for n in names:
        if isinstance(obj, dict):
            if obj.get(n) not in (None, ""):
                return obj[n]
        elif getattr(obj, n, None) not in (None, ""):
            return getattr(obj, n)
    return default


def _source_of(finding: dict, *, second: bool = False) -> dict:
    """{book, printed_page, quote} for a finding's first or second source."""
    nested = _f(finding, "second_source" if second else "source", default={})
    if not isinstance(nested, dict):
        nested = {}
    if second:
        book = _f(finding, "second_book", default=None) or _f(nested, "book",
                                                              default="")
        page = _f(finding, "second_printed", "second_page", default=None)
        quote = _f(finding, "second_quote", default=None)
    else:
        book = _f(finding, "book", default=None) or _f(nested, "book", default="")
        page = _f(finding, "printed", "printed_page", "page", default=None)
        quote = _f(finding, "source_quote", "quote", default=None)
    if page is None:
        page = _f(nested, "printed_page", "printed", "page", default=None)
    if not quote:
        quote = _f(nested, "quote", "source_quote", default="")
    return {"book": _canon_book(book), "printed_page": page,
            "quote": str(quote or "")}


def _canon_book(raw) -> str:
    """The canonical qc_books key for whatever the judge wrote.

    Through qc_cite so that one table decides what "Stahl kids" means; an
    unresolvable label passes through unchanged, because a raw label the owner
    can still read beats a silently wrong book name on a correction.
    """
    s = str(raw or "").strip()
    if not s or s in BOOK_HE:
        return s
    try:
        return qc_cite.resolve_label(s) or s
    except Exception:                                     # noqa: BLE001
        return s


def book_he(key) -> str:
    s = str(key or "").strip()
    return BOOK_HE.get(s, s)


def _page_he(page) -> str:
    return f" עמ' {page}" if page not in (None, "") else ""


def _source_he(src: dict) -> str:
    return f"{book_he(src['book'])}{_page_he(src['printed_page'])}"


def _trim(s, n: int = 220) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _short(fid) -> str:
    """The 8 characters of a finding id that go in a visible marker."""
    s = re.sub(r"[^0-9A-Za-z_.\-]", "", str(fid or "")) or "qc"
    return s[:8]


# ---------------------------------------------------------------------------
# JSON that comes back byte-identical
#
# Every asset this module writes is uploaded by qc_store.safe_upload, which
# compares sha256 before and after and records both in the verdict. A re-dump
# that changes the indent or re-escapes every Hebrew character rewrites 30,000
# bytes for a two-line correction: the owner then cannot read the diff, and the
# "N תיקונים" badge sits on an asset whose every line changed. Measured on the
# fixtures: the released quiz files are indent=2 and the flashcard files of 001
# and 042 were dumped with ensure_ascii=True, so the style is DETECTED, never
# assumed.


@dataclass(frozen=True)
class JsonStyle:
    indent: int = 2
    ensure_ascii: bool = False
    newline: str = ""

    def dumps(self, obj) -> str:
        return json.dumps(obj, ensure_ascii=self.ensure_ascii,
                          indent=self.indent) + self.newline


_STYLE_CANDIDATES = [JsonStyle(i, a, n)
                     for i in (2, 1, 4) for a in (False, True)
                     for n in ("", "\n")]


def detect_json_style(text: str, data=None) -> JsonStyle:
    """The dump settings that reproduce `text` exactly, or the house default."""
    if data is None:
        data = json.loads(text)
    for style in _STYLE_CANDIDATES:
        try:
            if style.dumps(data) == text:
                return style
        except (TypeError, ValueError):                   # noqa: PERF203
            break
    return JsonStyle()


def _load(name: str, text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Fail closed (§2.3): a quiz.json that does not parse must not look like
        # a chapter with nothing to fix.
        raise FixError(f"{name}: not valid JSON ({exc})") from exc


def _item_list(name: str, data):
    """The questions or cards of a parsed asset, as the live list object."""
    keys = ("questions", "items", "quiz") if name == "quiz.json" else \
           ("cards", "flashcards", "items")
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
        raise FixError(f"{name}: object with no {keys[0]} list "
                       f"(keys: {sorted(data)})")
    if isinstance(data, list):
        return data
    raise FixError(f"{name}: expected a list, got {type(data).__name__}")


def _options(item: dict) -> tuple[str, list]:
    """(the key the options live under, the list) — `answerOptions` in every
    released chapter, `options` in the older ones."""
    for k in ("answerOptions", "options"):
        v = item.get(k)
        if isinstance(v, list):
            return k, v
    return "answerOptions", []


def _stem(item: dict) -> str:
    return str(item.get("question") or item.get("stem") or "")


def _front(card: dict) -> str:
    return str(card.get("front") or "")


# ---------------------------------------------------------------------------
# 1. stable ids


def item_id(head: str) -> str:
    """The id of an item with this stem / card with this front.

    `.strip()` before hashing, because that is what app/quiz_logic.item_id does
    and the two identities have to be the same string (see the module docstring).
    """
    return hashlib.sha1(str(head or "").strip().encode("utf-8")).hexdigest()[:ID_CHARS]


def assign_ids(assets: dict[str, str]) -> dict[str, str]:
    """Every quiz item and card gets a stable `id`. Idempotent.

    Idempotent in the strong sense §5.9 needs: an id already present is kept
    untouched even if the stem has since changed, because the id is a handle on
    the item as PUBLISHED. Re-deriving it after a hygiene pass cleaned the stem
    would silently rename the item and orphan every erratum pointing at it.

    The field is appended rather than inserted first: json.dumps follows
    insertion order, and putting `id` at the front of each item would rewrite
    every line of the file for a diff that should be one line per item.
    """
    out = dict(assets)
    for name in ("quiz.json", "flashcards.json"):
        text = assets.get(name)
        if not text:
            continue
        data = _load(name, text)
        style = detect_json_style(text, data)
        items = _item_list(name, data)
        changed = False
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise FixError(f"{name}: entry {i + 1} is {type(item).__name__}")
            given = item.get("id")
            if isinstance(given, str) and given.strip():
                continue
            head = _stem(item) if name == "quiz.json" else _front(item)
            # A stem-less item cannot be hashed on its stem, and hashing "" would
            # give every such item in the chapter the SAME id — the one outcome
            # an identifier may not have. qc_units falls back the same way.
            item["id"] = (item_id(head) if head.strip()
                          else item_id(f"{name}#{i}\n{json.dumps(item, sort_keys=True, ensure_ascii=False)}"))
            changed = True
        if changed:
            out[name] = style.dumps(data)
    return out


# ---------------------------------------------------------------------------
# 2. hygiene
#
# The check names below are qc_lint's, deliberately: lint flags
# `hygiene_signoff` as BLOCKING, so a phrase lint knows and hygiene does not
# clean is a chapter that can never be approved. The patterns are read off
# qc_lint rather than copied for the same reason — two lists that drift apart
# produce exactly that deadlock.

_SIGNOFFS = qc_lint._SIGNOFFS                             # noqa: SLF001
_LEAKED_LABELS = qc_lint._LEAKED_LABELS                   # noqa: SLF001
_ARABIC = qc_lint._ARABIC                                 # noqa: SLF001

# `[12]` only, and never the text of a markdown link (`[3](url)`).
_MARKER_RE = re.compile(r"[ \t]?\[\d{1,2}\](?!\()")
# The two escapes measured OUTSIDE math in released prose: `40 מ\"ג` is a JSON
# string escape that survived into the markdown, and `\%` is LaTeX's percent.
_LOOSE_ESCAPES = ((r"\%", "%"), (r"\"", '"'))
# Math as the corpus writes it: `$5\text{-HT}_{2C}$`, `\(\alpha_{2A}\)` and the
# double-backslash form `\\(...\\)`. Only a region with a LaTeX-ish character
# inside counts, so a bare `$5` is never touched (same guard as qc_lint._MATH_RE).
_MATH_REGIONS = (
    re.compile(r"\$[^$\n]{0,200}?[\\^_{][^$\n]{0,200}?\$"),
    re.compile(r"\\{1,2}\((?:(?!\\{1,2}\)).){0,300}\\{1,2}\)", re.S),
    re.compile(r"\\{1,2}\[(?:(?!\\{1,2}\]).){0,300}\\{1,2}\]", re.S),
)
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Delta": "Δ", "Gamma": "Γ", "Sigma": "Σ",
    "Omega": "Ω", "Phi": "Φ", "Psi": "Ψ", "Theta": "Θ", "Lambda": "Λ",
}
_SYMBOLS = {
    "times": "×", "pm": "±", "approx": "≈", "leq": "≤", "le": "≤",
    "geq": "≥", "ge": "≥", "neq": "≠", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "uparrow": "↑", "downarrow": "↓", "sim": "~",
    "cdot": "·", "ldots": "…", "dots": "…", "infty": "∞", "circ": "°",
}
_UNWRAP_CMDS = ("text", "mathrm", "textit", "textbf", "mathbf", "mathit",
                "emph", "operatorname", "textrm", "rm")
_UNWRAP_RE = re.compile(r"\\(?:" + "|".join(_UNWRAP_CMDS) + r")\s*\{([^{}]*)\}")
_SUBSUP_BRACED = re.compile(r"[_^]\{([^{}]*)\}")
_SUBSUP_BARE = re.compile(r"[_^]([0-9A-Za-z])")
_CMD_RE = re.compile(r"\\([A-Za-z]+)")
_RESIDUAL_LATEX = re.compile(r"\\[A-Za-z]")


def _collapse_math(region: str) -> str | None:
    """One `$…$` / `\\(…\\)` region as plain text, or None if it is not fully
    understood.

    None rather than a best effort on purpose. An unmapped `\\foo` left in place
    keeps qc_lint's `hygiene_latex` flag raised, which keeps the chapter
    blocking and puts the string in front of the owner — whereas dropping the
    command silently changes what a drug or a receptor is called, which is the
    one failure a cleanup pass must not cause.
    """
    body = region
    for _ in range(4):                     # \text{\mathrm{x}} nests at most twice
        new = _UNWRAP_RE.sub(r"\1", body)
        if new == body:
            break
        body = new
    body = _CMD_RE.sub(lambda m: _GREEK.get(m.group(1), _SYMBOLS.get(m.group(1),
                                                                    m.group(0))),
                       body)
    body = _SUBSUP_BRACED.sub(r"\1", body)
    body = _SUBSUP_BARE.sub(r"\1", body)
    body = re.sub(r"\\{1,2}[()\[\]]", "", body)
    body = body.replace("$", "")
    body = re.sub(r"\\([%&#_${}])", r"\1", body)
    if _RESIDUAL_LATEX.search(body) or "{" in body or "}" in body:
        return None
    return body.strip()


def collapse_latex(s: str) -> tuple[str, list[tuple[str, str, bool]]]:
    """(text, [(before, after, handled)]) — the escaped LaTeX of §5.4 as prose."""
    events: list[tuple[str, str, bool]] = []
    out = s
    for pattern in _MATH_REGIONS:
        pieces: list[str] = []
        last = 0
        for m in pattern.finditer(out):
            got = _collapse_math(m.group(0))
            if got is None:
                events.append((m.group(0), m.group(0), False))
                continue
            pieces.append(out[last:m.start()])
            pieces.append(got)
            last = m.end()
            events.append((m.group(0), got, True))
        pieces.append(out[last:])
        out = "".join(pieces)
    for bad, good in _LOOSE_ESCAPES:
        if bad in out:
            events.append((bad, good, True))
            out = out.replace(bad, good)
    return out, events


def strip_leaked_labels(s: str) -> tuple[str, list[str]]:
    """Drop a NotebookLM scaffolding header that leaked into the material."""
    hits = [lab for lab in _LEAKED_LABELS if lab in s]
    if not hits:
        return s, []
    kept = []
    for line in s.splitlines(keepends=True):
        bare = line.strip().strip("#*_`: ").strip()
        if bare in _LEAKED_LABELS:
            continue                       # the label WAS the line: drop it whole
        for lab in hits:
            line = line.replace(lab, "")
        kept.append(line)
    return "".join(kept), hits


def strip_markers(s: str) -> tuple[str, int]:
    """NotebookLM's inline `[7]` source markers, with one leading space each."""
    out, n = _MARKER_RE.subn("", s)
    return out, n


_SEPARATOR = re.compile(r"^[\s\-*_=#>💡✨🎯]*$")


def strip_signoff(md: str) -> tuple[str, list[str]]:
    """Trailing chatbot sign-off paragraphs of a guide. TRAILING is the rule.

    "האם תרצה" is a sign-off at the end of a generated guide and a clinical
    interview question in the middle of chapter 001 ("would you like to tell me
    about…"), so a global search-and-destroy would delete the material. The pass
    therefore walks BACK from the end and stops at the first block that is
    neither a sign-off nor a leftover separator — which is exactly the shape the
    8 affected guides have (`---` then `💡 *אשמח לעזור…*`).
    """
    blocks = re.split(r"(\n\s*\n)", md)    # keeps the separators as odd indexes
    dropped: list[str] = []
    end = len(blocks)
    while end > 0:
        block = blocks[end - 1]
        if not block.strip():
            end -= 1
            continue
        if any(p in block for p in _SIGNOFFS):
            dropped.append(_trim(block, 120))
            end -= 1
            continue
        if dropped and _SEPARATOR.match(block.strip()):
            end -= 1                       # the `---` the sign-off sat under
            continue
        break
    if not dropped:
        return md, []
    return "".join(blocks[:end]).rstrip() + "\n", dropped


def _clean_string(s: str, *, asset: str, where: str,
                  changes: list[dict]) -> str:
    """Every hygiene rule that applies to one string, logging each change."""
    before = s
    out, latex = collapse_latex(s)
    for old, new, handled in latex:
        changes.append(_change(asset, "hygiene_latex", where, old, new,
                               fixed=handled,
                               note="" if handled else
                               "פקודת LaTeX לא מוכרת — לא שונתה"))
    out, labels = strip_leaked_labels(out)
    for lab in labels:
        changes.append(_change(asset, "hygiene_leaked_label", where, lab, ""))
    out, n_markers = strip_markers(out)
    if n_markers:
        changes.append(_change(asset, "hygiene_marker", where,
                               f"{n_markers}× [n]", ""))
    m = _ARABIC.search(out)
    if m:
        # NOT auto-corrected. The two measured cases (`המمושגית`,
        # `המונوאמינים`) are Arabic letters fused into a Hebrew word, and every
        # repair is a guess at which Hebrew letter was meant. Logged unfixed so
        # qc_lint keeps the chapter blocking and the owner reads the word.
        changes.append(_change(asset, "hygiene_arabic", where,
                               _trim(out[max(0, m.start() - 18):m.end() + 18], 60),
                               "", fixed=False,
                               note="אות ערבית בתוך מילה עברית — דורש תיקון ידני"))
    return out if out != before else before


def _change(asset: str, check: str, where: str, before, after, *,
            fixed: bool = True, note: str = "") -> dict:
    return {"kind": "hygiene", "asset": asset, "check": check, "where": where,
            "before": _trim(before, 160), "after": _trim(after, 160),
            "fixed": bool(fixed), "note": note}


def _walk_strings(node, path: str, fn):
    """Rewrite every string leaf through `fn`, keeping the structure.

    `qc` is skipped: a Tier-1 record holds a verbatim textbook quote, and a
    quote that happens to contain `[3]` or `\\%` is evidence, not dirt. Hygiene
    runs before Tier 1 in a normal run, but qc_revert replays fixes onto a
    snapshot and the order is then whatever the caller chose.
    """
    if isinstance(node, str):
        return fn(node, path)
    if isinstance(node, dict):
        return {k: (v if k == "qc" else
                    _walk_strings(v, f"{path}.{k}" if path else str(k), fn))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_walk_strings(v, f"{path}[{i}]", fn) for i, v in enumerate(node)]
    return node


def hygiene(assets: dict[str, str]) -> tuple[dict[str, str], list[dict]]:
    """(assets, changes) — the released bytes with §5.4's dirt removed.

    Only the assets that actually changed come back in the first element, so a
    caller can tell "cleaned" from "read" without comparing hashes.
    """
    out: dict[str, str] = {}
    changes: list[dict] = []
    for name, text in assets.items():
        if text is None or not qc_units.is_judged_asset(name):
            continue
        if name.endswith(".json"):
            data = _load(name, text)
            style = detect_json_style(text, data)
            cleaned = _walk_strings(
                data, "",
                lambda s, p, _n=name: _clean_string(s, asset=_n, where=p or "_root",
                                                    changes=changes))
            # Re-dump only when a string actually changed. Style detection falls
            # back to the house default for a file nothing here has seen, and a
            # re-dump under the wrong style would rewrite every byte of an asset
            # this pass had no business touching — which qc_store would then
            # upload and the verdict would report as "corrected".
            new = style.dumps(cleaned) if cleaned != data else text
        else:
            new = text
            if name == "study_guide.md":
                new, dropped = strip_signoff(new)
                for d in dropped:
                    changes.append(_change(name, "hygiene_signoff", "tail", d, ""))
            new = _clean_string(new, asset=name, where="text", changes=changes)
        if new != text:
            out[name] = new
    return out, changes


# ---------------------------------------------------------------------------
# 3. answer-order rebalance (kind `presentation`)

_ALL_NONE_RE = re.compile(
    r"all of the above|none of the above|כל התשובות|אף אחת|כל הנ\"?ל", re.I)
# An item whose text points at an option BY LETTER cannot be reordered: the
# reference would then name a different option. Latin labels, Hebrew letters
# with a geresh (א' ב' ג'), and the all/none phrases. Deliberately NOT bare
# `מעל` or `לעיל`: "כמתואר לעיל" ("as described above") is in a large share of
# the released rationales and refers to the preceding sentence, not to an option
# — skipping on it would rebalance nothing at all.
_LETTER_REF_RES = (
    # The trailing lookahead, not a character class: `[\b...]` is a BACKSPACE
    # inside a class, and the first draft of this pattern silently matched
    # nothing for "Option B is a distractor."
    re.compile(r"\b(?:option|answer|choice)s?\s*\(?[A-E]\)?(?![A-Za-z0-9])", re.I),
    re.compile(r"(?:תשובה|אפשרות|סעיף|מענה)\s*\(?[A-E]\)?\b"),
    re.compile(r"(?<![A-Za-z0-9])\([A-E]\)(?![A-Za-z0-9])"),
    re.compile(r"(?:תשובה|אפשרות|סעיף|מענה)\s*[אבגדה][\u05F3']"),
    re.compile(r"(?<![\u0590-\u05FF])[אבגדה][\u05F3'](?![\u0590-\u05FF])"),
    _ALL_NONE_RE,
)


def _item_blob(item: dict) -> str:
    """Every string the learner reads on this item, for the letter-reference
    scan. qc_units.render_quiz is the one rendering of an item (its docstring
    says so), so the scan sees exactly what the app shows."""
    try:
        return qc_units.render_quiz(item)
    except Exception:                                     # noqa: BLE001
        return json.dumps(item, ensure_ascii=False)


def letter_reference(item: dict, *, ignore: Sequence[str] = ()) -> str | None:
    """The first by-letter reference in the item, or None.

    `ignore` drops one occurrence of each string before scanning, and
    `rebalance_keys` passes the text of the options it is about to PIN. An
    option that reads "All of the above" is not a reference to another option,
    it is the option §5.9 says to pin last; the same phrase written in a
    stem or a rationale ("כל התשובות שלעיל") IS one, and still skips the item.
    """
    blob = _item_blob(item)
    for text in ignore:
        if text:
            blob = blob.replace(str(text), "", 1)
    for rx in _LETTER_REF_RES:
        m = rx.search(blob)
        if m:
            return m.group(0).strip()
    return None


def _pinned_options(item: dict, opts: Sequence[dict]) -> list[dict]:
    """The options whose position carries meaning and may not be moved."""
    pinned = [o for o in opts if _ALL_NONE_RE.search(str(o.get("text", "")))]
    # The item-level flag says the LAST option is the all/none one even when its
    # text does not spell the phrase; no sampled item sets it, but an item that
    # did and got reordered would be silently wrong.
    if not pinned and (item.get("hasAllOfTheAbove")
                       or item.get("hasNoneOfTheAbove")):
        pinned = [opts[-1]]
    return pinned


def _window_ok(seq: Sequence[int]) -> bool:
    if len(seq) < BALANCE_WINDOW:
        return True                        # whole blocks are balanced by design
    for i in range(len(seq) - BALANCE_WINDOW + 1):
        counts = Counter(seq[i:i + BALANCE_WINDOW])
        if max(counts.values()) > MAX_KEYS_PER_POSITION:
            return False
    return True


def key_plan(tag: str, n: int) -> list[int]:
    """`n` planned key positions for this chapter. Deterministic in `tag`.

    Shuffled copies of [0,1,2,3], so four consecutive rebalanced items use each
    position once — but the NEXT block is chosen, not just shuffled. A 10-item
    window spans up to four blocks (one item, block, block, one item), and if
    the single item at each end happens to be the same position it appears four
    times in ten, breaking §5.9's "<= 3 keys per position per 10". Searching the
    24 permutations for one that keeps every window inside the bound costs
    nothing and makes the property true rather than likely.
    """
    if n <= 0:
        return []
    seed = int(hashlib.sha1(str(tag).encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    perms = [list(p) for p in itertools.permutations(KEY_BLOCK)]
    plan: list[int] = []
    while len(plan) < n:
        order = perms[:]
        rng.shuffle(order)
        for cand in order:
            if _window_ok(plan + cand):
                plan.extend(cand)
                break
        else:                              # unreachable: see the docstring
            plan.extend(order[0])
    return plan[:n]


def _sort_key(option) -> str:
    text = option.get("text", "") if isinstance(option, dict) else str(option)
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()


def rebalance_keys(quiz, tag: str, *, log: list | None = None):
    """Move each single-answer item's key onto its planned position.

    Idempotent because nothing depends on the order the options arrive in: the
    non-pinned options are first sorted by sha1 of their own text — a canonical
    order the item carries with it — and the key is then inserted at the planned
    index. Run twice, the second pass re-derives the same canonical list and the
    same index, so the file does not move. That is what makes it safe to run on
    every resume of a 24-hour backlog.

    Skipped, and why each one:
      * not exactly one key, or fewer than two options — there is no "key
        position" to balance (multiple_select, fill_in_the_blank, short_answer).
      * the item names an option by letter (`letter_reference`) — reordering
        would make the reference point at a different option.
      * the key IS an all/none-of-the-above option — its meaning is positional.
    An all/none option that is NOT the key is pinned last instead of sorted.

    `quiz` may be the parsed object or the file's text; the same type comes back.
    """
    text_in = quiz if isinstance(quiz, str) else None
    data = _load("quiz.json", text_in) if text_in is not None else quiz
    style = detect_json_style(text_in, data) if text_in is not None else None
    items = _item_list("quiz.json", data)

    eligible: list[tuple[int, dict, str, list]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        okey, opts = _options(item)
        dicts = [o for o in opts if isinstance(o, dict)]
        if len(dicts) != len(opts) or len(opts) < 2:
            continue
        keys = [o for o in opts if o.get("isCorrect")]
        if len(keys) != 1:
            continue
        if _ALL_NONE_RE.search(str(keys[0].get("text", ""))):
            _log(log, tag, i, item, "key is an all/none-of-the-above option")
            continue
        ref = letter_reference(item, ignore=[o.get("text", "") for o
                                             in _pinned_options(item, opts)])
        if ref:
            _log(log, tag, i, item, f"references an option by letter ({ref!r})")
            continue
        eligible.append((i, item, okey, opts))

    plan = key_plan(tag, len(eligible))
    for n, (i, item, okey, opts) in enumerate(eligible):
        pinned = _pinned_options(item, opts)
        free = [o for o in opts if o not in pinned]
        key = next(o for o in free if o.get("isCorrect"))
        rest = sorted((o for o in free if o is not key), key=_sort_key)
        pos = plan[n] % len(free)
        new = rest[:pos] + [key] + rest[pos:]
        new += pinned
        if new == opts:
            continue
        item[okey] = new
        if log is not None:
            log.append({
                "kind": "presentation", "asset": "quiz.json",
                "check": "key_position", "unit": f"Q{i + 1}",
                "id": item.get("id") or item_id(_stem(item)),
                "position": pos, "options": len(free),
                "note": "סדר התשובות אוזן; התוכן לא שונה", "fixed": True,
            })
    if text_in is None:
        return data
    return style.dumps(data)


def _log(log, tag: str, i: int, item: dict, why: str) -> None:
    if log is None:
        return
    log.append({"kind": "presentation", "asset": "quiz.json",
                "check": "key_position", "unit": f"Q{i + 1}",
                "id": item.get("id") or item_id(_stem(item)),
                "skipped": why, "fixed": False})


# ---------------------------------------------------------------------------
# 4. Tier 1 — the note


def qc_record(finding: dict, date: str) -> dict:
    """The record §5.9 embeds, in the shape app/quiz_logic.qc_mark reads.

    `now` and `correction` are the same field under both spellings the app
    accepts: the app reads `now` first, qc_run's errata line reads `correction`,
    and a record that carries only one of them shows the learner a blank.
    """
    src = _source_of(finding)
    rec = {
        "kind": "content",
        "date": date,
        "was": str(_f(finding, "said", "was", default="")),
        "now": str(_f(finding, "correction", "now", default="")),
        "correction": str(_f(finding, "correction", "now", default="")),
        "source": src,
        "severity": str(_f(finding, "severity", default="")).lower(),
        "status": "active",
        "finding_id": str(_f(finding, "id", "finding_id", default="")),
        "unit": str(_f(finding, "unit", "uid", default="")),
        "conflict_kind": str(_f(finding, "kind", default="contradicted")),
    }
    second = _source_of(finding, second=True)
    if second["quote"]:
        # §5.9: "For source_conflict, both quotes." quiz_logic._sources reads
        # `second_source` and shows the pair with the "המקורות חלוקים" headline.
        rec["second_source"] = second
    if src["book"] == "Kartisiot" or second.get("book") == "Kartisiot":
        # Owner decision 4: material grounded in the כרטיסיות must say so by
        # name, in the record itself and not only in the app's display table.
        rec["source_name_he"] = book_he("Kartisiot")
    return rec


def _note_body(rec: dict) -> str:
    """What was said, what the book says, and which book — in that order.

    The book is named in every note, and for the כרטיסיות by its Hebrew name
    (owner decision 4): a correction the learner cannot trace to a page is a
    correction he has to take on faith, which is the thing this whole mechanism
    exists not to ask of him.
    """
    src = rec["source"]
    parts = [f"החומר אמר \"{_trim(rec['was'], 120)}\"",
             f"{_source_he(src)}: \"{_trim(src['quote'], 160)}\""]
    second = rec.get("second_source")
    if second and second.get("quote"):
        parts.append(f"{_source_he(second)}: \"{_trim(second['quote'], 160)}\"")
    if rec.get("now"):
        parts.append(f"הנכון: {_trim(rec['now'], 120)}")
    return " — ".join(parts)


def note_line(rec: dict, fid: str) -> str:
    """The visible note for a plain-text asset (a legend row)."""
    return (f"{NOTE_MARK} {NOTE_HE} [{_short(fid)}] {rec['date']}: "
            + _note_body(rec))


def guide_footnote(rec: dict, fid: str) -> str:
    """The block that goes at the end of the section the span sits in.

    A markdown blockquote, not a real `[^x]` footnote definition: the app renders
    the guide with `st.markdown`, which does not link footnotes, and a note the
    learner cannot see is the same as no note (§2.4 "mark, never hide"). The
    `[^qc-…]` marker beside the span is what ties the two together visually and
    what qc_revert matches to take the note back out.
    """
    return (f"> {NOTE_MARK} **{NOTE_HE}** ({rec['date']}) [^qc-{_short(fid)}] — "
            + _note_body(rec))


def _units_by_uid(assets: dict[str, str]) -> dict[str, qc_units.Unit]:
    out: dict[str, qc_units.Unit] = {}
    for u in qc_units.units({n: t for n, t in assets.items() if t is not None}):
        out[u.uid] = u
    return out


def tier1(assets: dict[str, str], findings: Sequence[dict],
          date: str | None = None) -> tuple[dict[str, str], list[dict]]:
    """(assets, fixes) — every verified finding shown, nothing rewritten.

    The returned `assets` holds only the files that changed. Each fix record is
    self-contained enough for qc_revert to replay it onto the pristine snapshot
    (`apply_fix` below reads nothing else), which is what makes withdrawing one
    correction out of nine a rebuild rather than a hand-edit.
    """
    date = date or _date.today().isoformat()
    by_uid = _units_by_uid(assets)
    out = dict(assets)
    fixes: list[dict] = []

    # Grouped per asset because the guide and the legends are patched by
    # splicing text: two findings in one section must be applied together, back
    # to front, or the second one's offsets are already stale.
    grouped: dict[str, list[tuple[dict, qc_units.Unit | None]]] = {}
    for finding in findings:
        uid = str(_f(finding, "unit", "uid", default=""))
        unit = by_uid.get(uid)
        if unit is None:
            fixes.append(_unplaced(finding, date, "",
                                   f"no unit {uid!r} in this chapter"))
            continue
        grouped.setdefault(unit.artifact, []).append((finding, unit))

    for asset, group in grouped.items():
        text = out.get(asset)
        if not text:
            for finding, unit in group:
                fixes.append(_unplaced(finding, date, asset, "asset not loaded"))
            continue
        if asset in ("quiz.json", "flashcards.json"):
            text, got = _tier1_items(asset, text, group, date)
        elif asset == "study_guide.md":
            text, got = _tier1_guide(asset, text, group, date)
        elif asset == "facts.json":
            got = [_errata_only(f, u, date, asset) for f, u in group]
        else:
            text, got = _tier1_legend(asset, text, group, date)
        fixes.extend(got)
        if text != out.get(asset):
            out[asset] = text
    return {n: t for n, t in out.items() if t != assets.get(n)}, fixes


def _fix_record(finding: dict, unit, date: str, asset: str, rec: dict, *,
                embedded: str, needs_owner: bool = False, reason: str = "",
                **extra) -> dict:
    """One row of fixes.json.

    `said`, `book`, `printed` and `source_quote` are duplicated out of the
    embedded record on purpose: qc_run.errata_lines builds the owner's Hebrew
    erratum straight off this row, and the app's "תיקונים" page reads the same
    fields. A row that only nested them would render as an empty correction.
    """
    src = rec["source"]
    row = {
        "id": str(_f(finding, "id", "finding_id", default="")) or _short(unit and unit.uid),
        "finding_id": str(_f(finding, "id", "finding_id", default="")),
        "kind": "content",
        "tier": 1,
        "asset": asset,
        "unit": rec["unit"] or (unit.uid if unit else ""),
        "stable_id": unit.stable_id if unit else "",
        "severity": rec["severity"],
        "status": "active",
        "date": date,
        "said": rec["was"],
        "correction": rec["now"],
        "book": src["book"],
        "book_he": book_he(src["book"]),
        "printed": src["printed_page"],
        "source_quote": src["quote"],
        "embedded": embedded,
        "needs_owner": bool(needs_owner),
        "reason": reason,
        "record": rec,
    }
    second = rec.get("second_source")
    if second:
        row.update({"second_book": second["book"],
                    "second_printed": second["printed_page"],
                    "second_quote": second["quote"]})
    row.update(extra)
    return row


def _unplaced(finding: dict, date: str, asset: str, reason: str) -> dict:
    """A finding that could not be attached to anything. Never dropped: §2.4
    says the learner always sees the error, so an unplaceable one becomes an
    owner item rather than a silence."""
    rec = qc_record(finding, date)
    return _fix_record(finding, None, date, asset, rec, embedded="none",
                       needs_owner=True, reason=reason)


def _errata_only(finding: dict, unit, date: str, asset: str) -> dict:
    """facts.json: the note lives in qc.json, not in the file.

    There is nowhere in facts.json to put it. A value there is a bare scalar
    inside a tree whose every leaf qc_units turns into a unit, so a sibling `qc`
    key would itself become a unit and the next run would ask the judge to
    verify the correction against a textbook. The app already shows facts errata
    from qc.json's chapter-level list, which is where this row ends up.
    """
    rec = qc_record(finding, date)
    return _fix_record(finding, unit, date, asset, rec, embedded="errata_only",
                       reason="facts.json has no field to carry a note; "
                              "shown on the errata page")


def _tier1_items(asset: str, text: str,
                 group: Sequence[tuple[dict, Any]], date: str) -> tuple[str, list[dict]]:
    """quiz.json / flashcards.json — the record goes in the item."""
    data = _load(asset, text)
    style = detect_json_style(text, data)
    items = _item_list(asset, data)
    fixes: list[dict] = []
    for finding, unit in group:
        idx = unit.meta.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(items)):
            fixes.append(_unplaced(finding, date, asset,
                                   f"{unit.uid} is not at index {idx}"))
            continue
        item = items[idx]
        rec = qc_record(finding, date)
        head = _stem(item) if asset == "quiz.json" else _front(item)
        iid = item.get("id") or item_id(head)
        existing = item.get("qc")
        if isinstance(existing, dict) and existing.get("finding_id") != rec["finding_id"]:
            # A second verified error on the SAME item. The app draws one mark
            # per item (quiz_logic.qc_mark returns one record), so the second is
            # carried under `also` — present in the published bytes, listed on
            # the errata page through fixes.json, and flagged for the owner
            # because today nothing renders it beside the item.
            existing.setdefault("also", []).append(rec)
            fixes.append(_fix_record(finding, unit, date, asset, rec,
                                     embedded="item_also", needs_owner=True,
                                     reason="a second verified finding on the "
                                            "same item; the app shows one mark",
                                     item_id=iid, index=idx))
            continue
        item["qc"] = rec
        fixes.append(_fix_record(finding, unit, date, asset, rec,
                                 embedded="item", item_id=iid, index=idx))
    return style.dumps(data), fixes


def _guide_sections(text: str) -> dict[str, tuple[int, int]]:
    return {u.uid: tuple(u.meta["span"])
            for u in qc_units.units_from_asset("study_guide.md", text)
            if "span" in u.meta}


def _tier1_guide(asset: str, text: str,
                 group: Sequence[tuple[dict, Any]], date: str) -> tuple[str, list[dict]]:
    """The guide — a marker beside the span, the note at the end of its section.

    §5.9's exactly-one-match rule, applied to the SECTION and not to the file:
    "SSRI" occurs 40 times in a guide about SSRIs and the judge's `said` is
    often a short clause. One normalized occurrence inside the anchored section
    means the marker can only be beside the text the finding is about; anything
    else (none, or two) writes nothing and hands the chapter to the owner.
    """
    spans = _guide_sections(text)
    fixes: list[dict] = []
    # Section by section, each patched from its end backwards, then spliced in
    # descending order of position — qc_units guarantees the sections partition
    # the file exactly, so slicing is lossless.
    per_section: dict[str, list[tuple[dict, Any]]] = {}
    for finding, unit in group:
        per_section.setdefault(unit.uid, []).append((finding, unit))

    edits: list[tuple[int, int, str]] = []
    for uid, items in per_section.items():
        span = spans.get(uid)
        if span is None:
            for finding, unit in items:
                fixes.append(_unplaced(finding, date, asset,
                                       f"section {uid!r} is no longer in the guide"))
            continue
        a, b = span
        body = text[a:b]
        core, tail = body.rstrip(), body[len(body.rstrip()):]
        placements: list[tuple[int, str]] = []
        blocks: list[str] = []
        for finding, unit in items:
            rec = qc_record(finding, date)
            fid = rec["finding_id"] or _short(uid)
            said = rec["was"]
            at, why = _anchor(said, core)
            if at is None:
                fixes.append(_fix_record(finding, unit, date, asset, rec,
                                         embedded="none", needs_owner=True,
                                         reason=why, section=uid))
                continue
            placements.append((at, f"[^qc-{_short(fid)}]"))
            blocks.append(guide_footnote(rec, fid))
            fixes.append(_fix_record(finding, unit, date, asset, rec,
                                     embedded="guide_footnote", section=uid,
                                     anchor=said, at=at))
        if not placements:
            continue
        for at, mark in sorted(placements, reverse=True):
            core = core[:at] + mark + core[at:]
        new_body = core + "\n\n" + "\n\n".join(blocks) + tail
        edits.append((a, b, new_body))

    for a, b, new_body in sorted(edits, reverse=True):
        text = text[:a] + new_body + text[b:]
    return text, fixes


def _anchor(said: str, body: str) -> tuple[int | None, str]:
    """(index just past the one occurrence of `said` in `body`, or None + why).

    Counted on the normalized form so that `**bold**`, a geresh or a niqqud
    cannot decide it, and the raw span comes from qc_norm.find_quote, which
    returns raw offsets into the string exactly as passed in.
    """
    nsaid, nbody = qc_norm.norm(said), qc_norm.norm(body)
    if len(nsaid) < MIN_ANCHOR_CHARS:
        return None, (f"the disputed text is {len(nsaid)} normalized characters"
                      f" (< {MIN_ANCHOR_CHARS}): too short to anchor a footnote")
    n = nbody.count(nsaid)
    if n == 0:
        return None, "the disputed text is not in the section it was reported on"
    if n > 1:
        return None, (f"the disputed text occurs {n} times in this section; "
                      f"§5.9 places a note only on exactly one match")
    got = qc_norm.find_quote(said, body, whole_unit=True)
    if got["match"] != "exact" or not got["span"]:
        # A fuzzy hit means the normalized forms agree while the raw text does
        # not line up (a two-column artefact); placing a marker on a span code
        # is not sure of would put it inside a word.
        return None, f"no exact raw span for the disputed text ({got['reason'] or got['match']})"
    return got["span"][1], ""


def _tier1_legend(asset: str, text: str,
                  group: Sequence[tuple[dict, Any]], date: str) -> tuple[str, list[dict]]:
    """A legend — the note goes on its own line under the row it is about.

    Legend units carry no span (qc_units builds them by regex), so the row is
    located by its verbatim text, and the same exactly-one rule applies: 19 of
    the 48 rows in the 019 legend share a symbol name, and several share whole
    sentences, so a note placed on the first textual match could sit under the
    wrong row.
    """
    fixes: list[dict] = []
    edits: list[tuple[int, str]] = []
    for finding, unit in group:
        rec = qc_record(finding, date)
        fid = rec["finding_id"] or _short(unit.uid)
        body = unit.text.rstrip()
        n = text.count(body)
        if n != 1:
            fixes.append(_fix_record(
                finding, unit, date, asset, rec, embedded="none",
                needs_owner=True,
                reason=(f"the legend row for {unit.uid} occurs {n} times in the "
                        f"file; a note can only be placed on exactly one")))
            continue
        at = text.index(body) + len(body)
        edits.append((at, "\n   " + note_line(rec, fid)))
        fixes.append(_fix_record(finding, unit, date, asset, rec,
                                 embedded="legend_row", row=unit.meta.get("row"),
                                 anchor=body))
    for at, line in sorted(edits, reverse=True):
        text = text[:at] + line + text[at:]
    return text, fixes


# ---------------------------------------------------------------------------
# 5. the same wrong value everywhere else


def _entities(*texts: str) -> list[str]:
    """The names that make a repeated number the SAME claim.

    qc_lint.key_terms first — the drug, trial and scale names this material
    writes in English inside Hebrew sentences, which is what makes a cheap
    window possible at all. Hebrew tokens of 5+ characters are added because a
    finding whose `said` is written entirely in Hebrew would otherwise have no
    entity at all and the sweep would report nothing — except the unit and
    count nouns, which qc_norm already recognises: the word beside a duration is
    that duration's own unit, never the drug it is about, and accepting
    "שבועות" as an entity would match every duration in
    the course. A loose entity only widens a REPORT the owner reads; §5.9
    never lets this function edit anything.

    THE ARGUMENT ORDER IS THE PRIORITY, then length. Callers pass the narrowest
    text first (the `said`) and the widest last (the whole unit the finding sits
    on), and the list is cut to qc_lint.MAX_KEY_TERMS. Ranking by length alone
    spent the whole budget on the unit: `said` "ההשפעה מופיעה תוך 4 שבועות"
    contributes two 6-character words, and one SSRI quiz item contributes twelve
    longer ones (Desensitization, somatodendritic, autoreceptors …), so the two
    words the finding is actually ABOUT were dropped and the sweep found nothing
    — including in facts.json, where `drug_class.onset` is a unit of its own and
    the drug's name sits in a sibling key the window cannot see. Longest-first
    still decides within one text, where qc_lint's reason for it holds ("a long
    name is the one that identifies a page").
    """
    seen: dict[str, int] = {}
    for i, t in enumerate(texts):
        for w in qc_lint.key_terms(t or ""):
            seen.setdefault(w, i)
        for w in re.findall(r"[֐-׿]{5,}", t or ""):
            if qc_norm._canon_unit(w) or qc_norm._is_count_noun(w):   # noqa: SLF001
                continue
            seen.setdefault(w, i)
    return sorted(seen, key=lambda w: (seen[w], -len(w)))[:qc_lint.MAX_KEY_TERMS]


def parse_corpus(chapter_assets: dict[str, str],
                 other_chapters: dict[str, dict[str, str]] | None = None, *,
                 tag: str = "") -> list[tuple[str, list]]:
    """`[(tag, [Unit])]` — the chapter first, then every other chapter.

    Parsed once and handed to `old_value_hits` for each finding. A chapter has
    several verified findings on a bad day and the corpus is the whole backlog
    (§5.9: "every other chapter"), so re-reading 68 chapters' units per finding
    would turn a report into a minute of CPU per chapter for no new information.

    A chapter whose JSON does not parse is left out, not raised on: another
    chapter's broken asset is that chapter's own `unjudged` verdict, and it may
    not stop this chapter's sweep. qc_lint reports it where it belongs.
    """
    corpus: list[tuple[str, list]] = []
    for ctag, assets in ([(tag, chapter_assets)]
                         + sorted((other_chapters or {}).items())):
        try:
            units = qc_units.units({n: t for n, t in (assets or {}).items()
                                    if t is not None})
        except qc_units.UnitError:
            continue
        corpus.append((ctag, units))
    return corpus


def _corpus_unit_text(corpus: Sequence[tuple[str, list]], tag: str,
                      uid: str) -> str:
    """The text of one unit of `tag`, or "" when the corpus does not hold it."""
    if not uid:
        return ""
    for ctag, units in corpus:
        if ctag != tag:
            continue
        for u in units:
            if u.uid == uid:
                return u.text
    return ""


def _drop_qc_records(node):
    """Every embedded Tier-1 record out of a parsed JSON asset."""
    if isinstance(node, dict):
        return {k: _drop_qc_records(v) for k, v in node.items()
                if not (k == "qc" and isinstance(v, dict)
                        and "finding_id" in v)}
    if isinstance(node, list):
        return [_drop_qc_records(v) for v in node]
    return node


def without_qc_notes(assets: dict[str, str]) -> dict[str, str]:
    """`assets` with this mechanism's OWN notes removed, for the sweep to read.

    §5.9's sweep asks where the old value still stands as a claim. A Tier-1
    record embeds that same old value verbatim (`qc.was`, and the visible note
    line in a legend or guide), and qc_units renders every unknown item key
    into the unit text — so a chapter corrected on an earlier pass would report
    its own correction as an uncorrected copy. The owner who opens three such
    rows and finds all three already marked stops opening them, which is the
    whole sweep lost to noise.

    Only this mechanism's marks are removed, and only from a COPY: nothing here
    reaches a published asset.
    """
    out: dict[str, str] = {}
    for name, text in (assets or {}).items():
        if text is None:
            continue
        try:
            if name.endswith(".json"):
                out[name] = json.dumps(_drop_qc_records(json.loads(text)),
                                       ensure_ascii=False)
            else:
                out[name] = strip_notes(name, text)
        except Exception:                                 # noqa: BLE001
            # Unparseable is the other chapter's problem (parse_corpus drops
            # it); never a reason to lose the asset from the sweep.
            out[name] = text
    return out


def _value_positions(text: str, raw: str) -> list[tuple[int, int]]:
    out, start = [], 0
    while True:
        i = text.find(raw, start)
        if i < 0:
            return out
        out.append((i, i + len(raw)))
        start = i + 1


def old_value_hits(finding: dict, chapter_assets: dict[str, str],
                   other_chapters: dict[str, dict[str, str]] | None = None, *,
                   tag: str = "", radius: int = SAME_CLAIM_RADIUS,
                   corpus: Sequence[tuple[str, list]] | None = None) -> list[dict]:
    """Every place the corrected value still stands next to the same entity.

    §5.9: "After every locked-value correction, search every artifact of the
    chapter ... and every other chapter for the old value next to the same
    entity; attach every hit to the same finding." The 2026-09-18 sample is the
    reason: 019's atomoxetine onset appears in the quiz, in a card, in the
    guide's high-yield list AND in the sketchy legend's "מספרים לבחינה" chip,
    and correcting one of five leaves the learner four wrong copies.

    Values compare inside a (kind, unit) bucket — qc_norm's rule, so "2-8 weeks"
    never matches a bare 28 and mg/day never matches mg/kg/day. The entity must
    occur within `radius` characters of the value in the same unit; a bare value
    match is not enough (StahlKids repeats "may take 2-8 weeks" on 12 pages).

    `corpus` is `parse_corpus`'s output, for a caller sweeping several findings
    over one backlog that does not want it re-parsed per finding. Passed, it
    supersedes `chapter_assets` and `other_chapters`.
    """
    home_tag = tag or str(_f(finding, "tag", default=""))
    if corpus is None:
        corpus = parse_corpus(chapter_assets, other_chapters, tag=home_tag)
    said = str(_f(finding, "said", "was", default=""))
    wanted = qc_norm.locked_values(said)
    if not wanted:
        return []
    buckets = {(qc_norm._bucket(v), _value_repr(v["value"]))            # noqa: SLF001
               for v in wanted}
    home = str(_f(finding, "unit", "uid", default=""))
    # The unit the finding sits on is part of the key-term window (§5.7): a
    # `said` of "2-8 שבועות" names no entity on its own,
    # while the quiz item it came from names Atomoxetine in its stem. Without
    # this the 019 sweep — the case §5.9 was written for, where the same
    # onset is repeated in a card, the guide and the legend chip — finds nothing.
    terms = _entities(said, str(_f(finding, "problem", default="")),
                      str(_f(finding, "source_quote", "quote", default="")),
                      _corpus_unit_text(corpus, home_tag, home))
    if not terms:
        return []
    hits: list[dict] = []
    for ctag, units in corpus:
        for unit in units:
            if ctag == home_tag and unit.uid == home:
                continue                   # the unit the finding is already on
            found: dict | None = None
            for v in qc_norm.locked_values(unit.text):
                if (qc_norm._bucket(v), _value_repr(v["value"])) not in buckets:  # noqa: SLF001
                    continue
                for a, b in _value_positions(unit.text, v["raw"]):
                    window = unit.text[max(0, a - radius):b + radius]
                    near = [t for t in terms if t in window]
                    if not near:
                        continue
                    # One row per UNIT, not per occurrence. Legend row 24 of the
                    # 019 plate writes the same onset three ways in one sentence
                    # ("2-8wk" on the slate, "2-8 weeks" in the fact, "2-8
                    # שבועות" in the Hebrew); three
                    # report lines for one row is three things for the owner to
                    # open and one thing to fix.
                    if found is None:
                        found = {"tag": ctag, "asset": unit.artifact,
                                 "unit": unit.uid, "stable_id": unit.stable_id,
                                 "value": v["raw"], "kind": v["kind"],
                                 "term": near[0], "span": [a, b],
                                 "occurrences": 0,
                                 "snippet": _trim(window, 200)}
                        hits.append(found)
                    found["occurrences"] += 1
    return hits


def _value_repr(value) -> str:
    """A hashable, order-insensitive form of a locked value's `value`."""
    return json.dumps(value, default=str, sort_keys=True)


def other_chapters_on_disk(root=None, *, exclude: str = "") -> dict[str, dict[str, str]]:
    """Every OTHER chapter this backlog holds locally — tag → its assets.

    §5.9's sweep reaches "every other chapter", and the copies of those chapters
    this process may read without a network call are the ones qc_run already
    pulled: `_qc/work/<tag>/release/` (the release as it stood when that chapter
    was judged) and, for a chapter whose gitignored work tree has since been
    swept, `_qc/orig/<tag>/` — the pristine snapshot §5.5 keeps forever. The
    work copy wins because it is the newer of the two.

    Deliberately local-only. The sweep is a REPORT the owner reads (§5.10's
    global pass: "pairs logged in the report"), never an edit, so a chapter this
    machine has not fetched costs a line he checks by hand; 68 `gh` downloads
    inside a fix step would cost the backlog hours and a rate limit.
    """
    base = qc_store.qc_root(root)
    tags: set[str] = set()
    for parent, leaf in ((base / "work", "release"), (base / "orig", "")):
        if not parent.is_dir():
            continue
        try:
            for p in parent.iterdir():
                if (p / leaf if leaf else p).is_dir():
                    tags.add(p.name)
        except OSError:
            continue
    out: dict[str, dict[str, str]] = {}
    for t in sorted(tags - {exclude, ""}):
        for d in (base / "work" / t / "release", base / "orig" / t):
            if not d.is_dir():
                continue
            try:
                assets = _read_dir(d)
            except OSError:
                continue
            except UnicodeDecodeError:
                continue
            if assets:
                out[t] = assets
                break
    return out


def sweep_old_values(findings: Sequence[dict], assets: dict[str, str], *,
                     tag: str = "",
                     other_chapters: dict[str, dict[str, str]] | None = None,
                     root=None,
                     radius: int = SAME_CLAIM_RADIUS) -> list[list[dict]]:
    """§5.9's sweep for a whole chapter: one hit list per finding, in order.

    "After every locked-value correction, search every artifact of the chapter
    (guide incl. high-yield and summary, facts, quiz, cards, legends) and every
    other chapter for the old value next to the same entity; attach every hit to
    the same finding."

    `assets` is the chapter as it stood BEFORE this run's notes were embedded —
    a note carries the old value in `qc.was`, and sweeping the patched bytes
    would report every correction as a copy of itself. `without_qc_notes` takes
    out the marks an EARLIER pass left for the same reason.

    `other_chapters` defaults to `other_chapters_on_disk(root)`; pass `{}` for
    the chapter alone. Every hit is kept — §5.9 says "every hit", and a cap
    here would silently be the one copy the learner then reads.
    """
    rows = list(findings)
    if not rows:
        return []
    if other_chapters is None:
        other_chapters = other_chapters_on_disk(root, exclude=tag)
    corpus = parse_corpus(without_qc_notes(assets),
                          {t: without_qc_notes(a)
                           for t, a in (other_chapters or {}).items()},
                          tag=tag)
    return [old_value_hits(f, assets, tag=tag, radius=radius, corpus=corpus)
            for f in rows]


def _sweep_key(row: dict) -> tuple[str, str, str]:
    """What ties a fix row back to the finding it was built from.

    The id alone is not enough: §5.8's schema lets a finding arrive without one,
    and `_fix_record` then names the ROW after its unit (`_short(unit.uid)`)
    while the row's `finding_id` stays empty — so the two would never match on
    `id`. Unit and `said` are copied through verbatim, and two findings that
    agree on all three are the same claim twice and share their hits.
    """
    return (str(row.get("finding_id") or ""), str(row.get("unit") or ""),
            str(row.get("said") or ""))


def _finding_key(finding: dict) -> tuple[str, str, str]:
    """`_sweep_key` as read off the finding, before `_fix_record` sees it."""
    return (str(_f(finding, "id", "finding_id", default="")),
            str(_f(finding, "unit", "uid", default="")),
            str(_f(finding, "said", "was", default="")))


# ---------------------------------------------------------------------------
# 6. Tier 2 — scaffolding only
#
# Nothing below writes into an asset. §5.9 opens Tier 2 only after calibration
# shows verified false positives <= 2% on >= 200 source-confirmed-correct units,
# AND requires a second independent judge call to return the same corrected
# value, AND holds every patch for the owner during calibration and the first 10
# backlog chapters. Neither the measurement nor the owner's review exists yet,
# so the rewrite half is deliberately absent: `propose_patch` produces the
# record a reviewer would approve and `patch_checks` runs the deterministic half
# of §5.9's gate, and there is no code path from either to a modified file.


def tier2_enabled(root=None) -> bool:
    """§5.9's calibration gate, read from `_qc/state.json`. OFF by default.

    No CLI switch and no environment variable, on purpose: the gate is a
    measurement the owner signs off on (§6's go/no-go), and a flag that is easy
    to pass on a command line is a flag that gets passed by a tired operator at
    03:00 in the middle of a backlog run.
    """
    try:
        state = qc_store.load_state(root=root)
    except Exception:                                     # noqa: BLE001
        # A missing or unreadable state file means "not calibrated", never
        # "go ahead" (§2.3).
        return False
    return bool(state.get("tier2_enabled"))


def _patch_numbers(value) -> list[dict]:
    if isinstance(value, str):
        return qc_norm.locked_values(value)
    if isinstance(value, dict):
        return [v for x in value.values() for v in _patch_numbers(x)]
    if isinstance(value, (list, tuple)):
        return [v for x in value for v in _patch_numbers(x)]
    return []


def _patch_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_patch_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_patch_text(v) for v in value)
    return ""


def patch_checks(patch: dict, *, original: dict, source_text: str,
                 finding: dict | None = None,
                 other_heads: Iterable[str] = ()) -> dict:
    """§5.9's deterministic half of the Tier-2 gate. {ok, failures, checks}.

    Each check is one of the failures the podcast project's rewriter actually
    produced: a patch that "fixed" a dose by rewriting the whole vignette; a
    patch that introduced a number appearing neither in the source page nor in
    the item; a patch whose key no longer contained the value it was corrected
    to; and a patch that turned an item into a duplicate of another item in the
    same chapter. `ok` is false unless every one of them passes — there is no
    partial credit, because the next step after `ok` is publication.
    """
    asset = str(patch.get("asset") or "")
    allowed = PATCH_FIELDS.get(asset, ())
    proposed = patch.get("proposed") or {}
    failures: list[str] = []
    checks: dict[str, bool] = {}

    extra = [k for k in proposed if k not in allowed]
    checks["fields"] = not extra
    if extra:
        failures.append(f"touches fields outside {allowed}: {sorted(extra)}")
    for okey in ("answerOptions", "options"):
        for i, opt in enumerate(proposed.get(okey) or []):
            if isinstance(opt, dict):
                bad = [k for k in opt if k not in PATCH_OPTION_FIELDS]
                if bad:
                    checks["fields"] = False
                    failures.append(f"option {i} touches {sorted(bad)}")

    src_values = {_value_repr(v["value"]) for v in qc_norm.locked_values(source_text)}
    orig_values = {_value_repr(v["value"]) for v in _patch_numbers(original)}
    unknown = [v["raw"] for v in _patch_numbers(proposed)
               if _value_repr(v["value"]) not in src_values | orig_values]
    checks["numbers"] = not unknown
    if unknown:
        failures.append(f"introduces values found neither on the source page nor "
                        f"in the original item: {unknown}")

    want = {_value_repr(v["value"])
            for v in qc_norm.locked_values(str(_f(finding or {}, "correction",
                                                  "now", default="")))}
    if want:
        keys = [o for okey in ("answerOptions", "options")
                for o in (proposed.get(okey) or [])
                if isinstance(o, dict) and o.get("isCorrect")]
        holder = " ".join(_patch_text(o) for o in keys) or _patch_text(proposed)
        got = {_value_repr(v["value"]) for v in qc_norm.locked_values(holder)}
        checks["key_value"] = bool(want & got)
        if not checks["key_value"]:
            failures.append("the corrected key does not contain the source's value")
    else:
        checks["key_value"] = True

    head = str(proposed.get("question") or proposed.get("stem")
               or proposed.get("front") or "")
    if head:
        nhead = qc_norm.norm(head)
        clash = [h for h in other_heads if qc_norm.norm(h) == nhead]
        checks["duplicates"] = not clash
        if clash:
            failures.append("the rewritten stem duplicates another item in the "
                            "chapter (qc_lint's duplicate check would fail)")
    else:
        checks["duplicates"] = True

    return {"ok": not failures, "failures": failures, "checks": checks}


def propose_patch(finding: dict, *, unit, asset: str, proposed: dict,
                  source_text: str, original: dict | None = None,
                  second_opinion: dict | None = None,
                  value_elsewhere: bool | None = None,
                  other_heads: Iterable[str] = ()) -> dict:
    """A Tier-2 rewrite, written down for the owner. It is never applied here.

    The three gates of §5.9 are recorded as three named booleans rather than
    folded into one, because when a patch is held the owner has to be told WHICH
    gate held it:
      `value_elsewhere` — (a) the original value must appear on no cited page and
        nowhere in any book within the key-term window. Unknown (None) is not a
        pass: a caller that did not run the search has not cleared the gate, and
        a value that IS elsewhere makes this a `source_conflict`, Tier 1 with
        both quotes.
      `second_opinion`  — (b) a second independent judge call, fresh conversation
        and the same bundle, returning the same corrected value.
      `checks`          — (c) patch_checks above.
    """
    original = original if original is not None else {}
    checks = patch_checks({"asset": asset, "proposed": proposed},
                          original=original, source_text=source_text,
                          finding=finding, other_heads=other_heads)
    want = {_value_repr(v["value"])
            for v in qc_norm.locked_values(str(_f(finding, "correction", "now",
                                                  default="")))}
    got = {_value_repr(v["value"])
           for v in qc_norm.locked_values(str(_f(second_opinion or {},
                                                 "correction", "now", "value",
                                                 default="")))}
    gates = {
        "value_nowhere_else": value_elsewhere is False,
        "second_opinion_agrees": bool(want and got and want <= got),
        "patch_checks": checks["ok"],
    }
    held = [name for name, ok in gates.items() if not ok]
    return {
        "tier": 2,
        "kind": "content",
        "status": "awaiting_owner",
        "applied": False,
        "finding_id": str(_f(finding, "id", "finding_id", default="")),
        "unit": getattr(unit, "uid", str(_f(finding, "unit", default=""))),
        "stable_id": getattr(unit, "stable_id", ""),
        "asset": asset,
        "proposed": proposed,
        "original": original,
        "gates": gates,
        "held_by": held,
        "checks": checks,
        "second_opinion": second_opinion,
        "reason": ("every §5.9 gate passed; waiting for the owner's approval"
                   if not held else
                   "held by " + ", ".join(held)),
    }


# ---------------------------------------------------------------------------
# 7. adapters
#
# qc_run binds these by name (its `_HYGIENE_NAMES`, `_TIER1_NAMES`,
# `_TIER2_NAMES`, `_REVERT_NAMES`) and calls them with keywords only; qc_revert
# binds `apply_fix`. They exist so the functions above can keep the signatures
# §5.9 describes while the orchestrator keeps its own calling convention.


def apply_hygiene(*, assets: dict[str, str], tag: str = "", root=None,
                  rebalance: bool = True, **_kw) -> dict:
    """The whole pre-judge pass: ids, hygiene, answer order.

    In this order and no other. Ids are derived from the stem AS PUBLISHED, so
    they must be stamped before hygiene can touch a stem; hygiene must precede
    the rebalance so that an option's canonical sha1 is taken on its final text
    (otherwise a later hygiene pass re-sorts the same quiz and the "idempotent"
    promise is only true until the first `\\%` is cleaned).
    """
    stamped = assign_ids(assets)
    merged = {**assets, **stamped}
    cleaned, changes = hygiene(merged)
    merged.update(cleaned)
    out = {n: t for n, t in merged.items() if t != assets.get(n)}

    if rebalance and merged.get("quiz.json"):
        log: list[dict] = []
        new_quiz = rebalance_keys(merged["quiz.json"], tag or "", log=log)
        changes.extend(log)
        if new_quiz != assets.get("quiz.json"):
            out["quiz.json"] = new_quiz
    # `fixes` repeats the presentation records for a caller that keeps fixes and
    # hygiene apart; qc_run reads `changes` (its `_get(res, "changes", "fixes")`
    # takes the first) and files them under the verdict's hygiene record.
    return {"assets": out, "changes": changes,
            "fixes": [c for c in changes if c.get("kind") == "presentation"
                      and c.get("fixed")]}


def apply_tier1(*, findings: Sequence[dict], assets: dict[str, str],
                tag: str = "", units=None, root=None, date: str | None = None,
                other_chapters: dict[str, dict[str, str]] | None = None,
                sweep: bool = True, **_kw) -> dict:
    """qc_run's Tier-1 entry point.

    `units` is accepted and ignored: tier1 derives the units from `assets`
    itself, so a caller that passes units computed before the hygiene pass
    cannot make a note land on a span that no longer exists.

    Every embedded row comes back with `also_in`: §5.9's cross-artifact and
    cross-chapter sweep for that finding, run here because this is the one place
    that knows a locked-value correction was just made and still holds the
    chapter as it was before the note went in. An empty list is an answer — the
    sweep ran and this value stands nowhere else — so the key is always present;
    a row without it means the sweep did not run. `other_chapters` defaults to
    whatever `other_chapters_on_disk` finds under `root`; pass `{}` to sweep
    this chapter alone, or `sweep=False` to skip it entirely (qc_revert replays
    rows, it does not re-measure them).
    """
    date = date or _date.today().isoformat()
    held = [(f, _why_not_tier1(f)) for f in findings]
    accepted = [f for f, why in held if not why]
    changed, fixes = tier1(assets, accepted, date)
    if sweep and accepted:
        # On `assets`, not on `changed`: the note this run just embedded quotes
        # the old value in `qc.was`, and a sweep of the patched bytes would
        # report every correction as an uncorrected copy of itself.
        by_key: dict[tuple[str, str, str], list[dict]] = {}
        for f, hits in zip(accepted,
                           sweep_old_values(accepted, assets, tag=tag,
                                            other_chapters=other_chapters,
                                            root=root)):
            by_key.setdefault(_finding_key(f), hits)
        for row in fixes:
            row["also_in"] = by_key.get(_sweep_key(row), [])
    for f, why in held:
        if not why:
            continue
        # Recorded either way, never dropped: fixes.json is the only list that
        # says what this run did with each verified finding, and a finding that
        # appears in the verdict's counts but in no row reads as a correction
        # that was applied and then lost.
        kind = str(_f(f, "kind", default="contradicted"))
        fixes.append(_unplaced(f, date, "", why))
        if kind in TIER1_KINDS:
            # A `low` is style (§5.8's severity ladder). It is written down
            # and it does not stop the chapter.
            fixes[-1]["needs_owner"] = False
    return {"assets": changed, "fixes": fixes}


def _why_not_tier1(finding: dict) -> str:
    """"" when Tier 1 embeds this finding, else the reason it does not.

    §5.9 opens with "acts only on verified `contradicted` findings of
    severity high/medium", and §5.8 sends `unsupported` and `source_conflict`
    to the owner queue. qc_run already classifies, so anything caught here is a
    caller that did not — which is exactly when a silent drop would cost a
    verified error.
    """
    kind = str(_f(finding, "kind", default="contradicted"))
    if kind not in TIER1_KINDS:
        return (f"kind {kind!r} is not embedded by Tier 1 "
                f"(§5.8: it belongs in the owner queue)")
    sev = str(_f(finding, "severity", default="")).lower()
    if sev not in FIXABLE_SEVERITIES:
        return (f"severity {sev!r}: §5.9 Tier 1 acts on "
                f"{'/'.join(FIXABLE_SEVERITIES)}")
    return ""


def apply_tier2(*, findings: Sequence[dict], assets: dict[str, str],
                tag: str = "", units=None, root=None, **_kw) -> dict:
    """Refuses to rewrite. Always.

    Returns no assets whatever `tier2_enabled()` says: §5.9's condition (b) is a
    second judge call and its closing sentence holds every patch for the owner
    during calibration and the first 10 backlog chapters, so there is no state
    of this program in which a rewrite reaches a published asset unattended.
    qc_run only calls this when the flag is on; the check is repeated here so
    that the guarantee does not depend on the caller.
    """
    if not tier2_enabled(root):
        return {"assets": {}, "fixes": [], "patches": [],
                "skipped": "Tier 2 is off (_qc/state.json: tier2_enabled)"}
    return {"assets": {}, "fixes": [], "patches": [],
            "skipped": "Tier 2 proposes patches for the owner; no rewrite path "
                       "is implemented (§5.9 gates (b) and the owner hold)"}


def revert_patches(*, findings: Sequence[dict], assets: dict[str, str],
                   tag: str = "", root=None, **_kw) -> dict:
    """Undo a Tier-2 rewrite on a unit the re-judge flagged again.

    Nothing to undo while `apply_tier2` writes nothing, so this returns the
    assets untouched. It exists as a bound name because qc_run reaches for it on
    the regression path and a NotWired there would read as a wiring bug in the
    middle of a chapter rather than as "there were no patches".
    """
    return {"assets": {}, "fixes": [],
            "note": f"{len(list(findings))} regression(s); no Tier-2 patch was "
                    f"ever applied, so nothing was reverted"}


def apply_fix(asset: str, text: str, fix: dict) -> str:
    """Replay ONE fix onto the pristine asset. qc_revert's applier.

    qc_revert rebuilds a reverted chapter by replaying the fixes that stay
    active onto `_qc/orig/<tag>/`, so this must work from the fix record alone —
    no findings, no units cached from the run that produced it. That is why
    `_fix_record` stores the embedded record, the item id, the section and the
    anchor text rather than pointing back at a verdict.
    """
    rec = fix.get("record")
    if not isinstance(rec, dict):
        raise FixError(f"fix {fix.get('id')!r}: no embedded record to replay")
    if fix.get("embedded") in ("none", "errata_only"):
        return text                        # nothing was ever written into the file
    fid = rec.get("finding_id") or str(fix.get("id") or "")

    if asset in ("quiz.json", "flashcards.json"):
        data = _load(asset, text)
        style = detect_json_style(text, data)
        items = _item_list(asset, data)
        want = str(fix.get("item_id") or "")
        target = None
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            head = _stem(item) if asset == "quiz.json" else _front(item)
            if (str(item.get("id") or "") == want and want) or \
                    (not item.get("id") and want and item_id(head) == want):
                target = item
                break
        if target is None and isinstance(fix.get("index"), int) \
                and 0 <= fix["index"] < len(items):
            target = items[fix["index"]]
        if target is None:
            raise FixError(f"{asset}: no item with id {want!r} to replay fix "
                           f"{fix.get('id')!r} onto")
        if fix.get("embedded") == "item_also":
            existing = target.get("qc")
            if isinstance(existing, dict):
                existing.setdefault("also", []).append(rec)
            else:
                target["qc"] = rec
        else:
            target["qc"] = rec
        return style.dumps(data)

    if asset == "study_guide.md":
        uid, said = str(fix.get("section") or ""), str(fix.get("anchor") or "")
        spans = _guide_sections(text)
        if uid not in spans:
            raise FixError(f"study_guide.md: section {uid!r} is not in the "
                           f"snapshot; fix {fix.get('id')!r} cannot be replayed")
        a, b = spans[uid]
        body = text[a:b]
        core, tail = body.rstrip(), body[len(body.rstrip()):]
        at, why = _anchor(said, core)
        if at is None:
            raise FixError(f"study_guide.md: {why} (fix {fix.get('id')!r})")
        core = core[:at] + f"[^qc-{_short(fid)}]" + core[at:]
        return text[:a] + core + "\n\n" + guide_footnote(rec, fid) + tail + text[b:]

    anchor = str(fix.get("anchor") or "")
    n = text.count(anchor) if anchor else 0
    if n != 1:
        raise FixError(f"{asset}: the row fix {fix.get('id')!r} anchors on "
                       f"occurs {n} times; refusing to guess")
    at = text.index(anchor) + len(anchor)
    return text[:at] + "\n   " + note_line(rec, fid) + text[at:]


def apply_fixes(asset: str, text: str, fixes: Sequence[dict]) -> str:
    for fix in fixes:
        text = apply_fix(asset, text, fix)
    return text


def strip_notes(asset: str, text: str) -> str:
    """Every visible Tier-1 note back out of a text asset.

    Used by the tests and available to a hand revert; qc_revert itself never
    needs it because it rebuilds from the snapshot rather than un-editing.
    """
    if asset.endswith(".json"):
        return text
    text = _NOTE_LINE_RE.sub("", text)
    text = re.sub(r"(?m)^>\s*" + re.escape(NOTE_MARK) + r".*$\n?", "", text)
    return _FOOTNOTE_MARK_RE.sub("", text)


# ---------------------------------------------------------------------------
def _read_dir(d: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(d.iterdir())
            if p.is_file() and qc_units.is_judged_asset(p.name)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("directory", help="a directory holding a chapter's assets")
    ap.add_argument("--tag", default="", help="chapter tag (seeds the key plan)")
    ap.add_argument("--hygiene", action="store_true",
                    help="ids + hygiene + answer-order rebalance")
    ap.add_argument("--tier1", metavar="FINDINGS.JSON",
                    help="embed the notes for these verified findings")
    ap.add_argument("--date", help="the date stamped on every note")
    ap.add_argument("--write", metavar="DIR",
                    help="write the changed assets here (default: report only)")
    a = ap.parse_args(argv)

    d = Path(a.directory)
    assets = _read_dir(d)
    if not assets:
        print(f"no judged assets in {d}")
        return 1
    tag = a.tag or d.name
    changed: dict[str, str] = {}

    if a.hygiene or not a.tier1:
        res = apply_hygiene(assets=assets, tag=tag)
        changed.update(res["assets"])
        for c in res["changes"]:
            mark = "✓" if c.get("fixed") else "!"
            print(f"  {mark} {c['asset']:<20} {c.get('check', ''):<20} "
                  f"{c.get('where') or c.get('unit') or ''}  "
                  f"{c.get('skipped') or c.get('note') or c.get('before', '')[:60]}")
        print(f"-- hygiene: {len(res['changes'])} change(s) in "
              f"{len(res['assets'])} asset(s)")

    if a.tier1:
        findings = json.loads(Path(a.tier1).read_text(encoding="utf-8"))
        if isinstance(findings, dict):
            findings = findings.get("findings") or []
        merged = {**assets, **changed}
        res = apply_tier1(findings=findings, assets=merged, tag=tag, date=a.date)
        changed.update(res["assets"])
        for f in res["fixes"]:
            flag = "owner" if f.get("needs_owner") else f.get("embedded")
            print(f"  [{flag:<14}] {f['unit']:<28} {f['book']} "
                  f"עמ' {f['printed']}  {f.get('reason', '')}")
        print(f"-- tier 1: {len(res['fixes'])} fix(es), "
              f"{sum(1 for f in res['fixes'] if f.get('needs_owner'))} for the owner")

    if a.write and changed:
        out = Path(a.write)
        out.mkdir(parents=True, exist_ok=True)
        for name, text in sorted(changed.items()):
            (out / name).write_text(text, encoding="utf-8", newline="")
            print(f"  wrote {out / name}")
    elif changed:
        print(f"-- {len(changed)} asset(s) would change; --write DIR to save")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
