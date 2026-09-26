#!/usr/bin/env python3
"""Everything about a released chapter that code can decide without a judge.

Spec §5.4. This runs before the bundle is built and before a single agy call is
spent, for two reasons.

FIRST, IT IS FREE AND THE JUDGE IS NOT. The backlog estimate is 24-51 h of
serial agy time plus up to ~27 h per quota lockout. Every defect that a regex
can prove is a defect the judge must not be asked about — and several of the
worst ones in the 2026-09-18 sample are exactly that shape:

  - `(StahlNeuro p. 289, 902)` in chapter 029's guide. StahlNeuro has 624
    printed pages. The old gate (`qc_review.check_page_citations`) read only the
    first number after a book name and passed it. qc_cite reads every number in
    the list, and this module fails the chapter on it. Measured on the 029 guide
    as released: 343 citations, 2 impossible (p.902 and p.1030).
  - Key position A 98 / B 31 / C 3 / D 0 over the 132 single-answer items of the
    17 sampled chapters: answering A every time scores 74%. The key is also the
    unique longest option in 61% of them, and longer than the longest distractor
    by more than 30% in 23%. Neither is a wrong fact, so neither belongs to the
    judge; both are why the learner's score does not measure his knowledge.
  - Hygiene: a chatbot sign-off in 3 of the 14 sampled guides ("האם תרצה שנעבור
    כעת לפתור קוויז..."), escaped LaTeX in 8 (`\\(\alpha_{2A}\\)`,
    `$5\text{-HT}_{2C}$`, and `מ\\"ג` — a JSON escape that leaked into the
    markdown), and Arabic letters fused into Hebrew words in 2
    (`המمושגית`, `המונوאמינים`). §5.9 requires the hygiene pass to run
    BEFORE judging, so that the spans a verdict quotes are the spans of the
    final text; this module is what finds them.

SECOND, HYGIENE POISONS THE JUDGE'S EVIDENCE. A leaked `General Instruction
Filter` or a NotebookLM `[7]` marker inside a unit is text the judge is asked to
verify against a textbook that never contained it — an `unsupported` finding
that costs an owner review and proves nothing.

WHAT THIS MODULE MAY NOT DO

It never decides a fact is wrong. The locked-value check below can say "this
number is on no page this unit cites, and nowhere in that book beside any term
this unit names" — and that is a PRIORITY for the judge, never a verdict
(§5.4). Deciding correctness needs the book in front of a reader, which is
§5.8's job. So `value_unsourced` is non-blocking by construction.

    python scripts/qc_lint.py --dir _qc/work/study-029-ssris-deep-dive \\
                              --tag study-029-ssris-deep-dive
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qc_books  # noqa: E402  page model + full-text search for the value check
import qc_cite   # noqa: E402  every citation, every page in a list
import qc_norm   # noqa: E402  the comparison form and locked_values
import qc_units  # noqa: E402  the unit: what a flag is attached to

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# The checks. `blocking` means "the chapter cannot be approved while this
# stands" — not "stop the run"; qc_run collects these and decides. The Hebrew
# label is what the owner reads in the CLI summary and in _qc/REPORT.md.
#
# Everything presentational is non-blocking on purpose: §5.9 fixes key position
# and longest-option bias itself, without an LLM and without the owner, so
# holding a chapter for them would stall the backlog on a cosmetic defect.
CHECKS: dict[str, dict] = {
    "asset_unparsable":     {"he": "נכס שלא ניתן לפענוח", "blocking": True},
    "quiz_unknown_type":    {"he": "סוג שאלה לא מוכר", "blocking": True},
    "quiz_key_count":       {"he": "מספר תשובות נכונות שגוי", "blocking": True},
    "quiz_too_few_options": {"he": "פחות משתי אפשרויות", "blocking": True},
    "quiz_empty_field":     {"he": "שדה ריק בשאלה", "blocking": True},
    "quiz_missing_answer":  {"he": "אין תשובה מוגדרת", "blocking": True},
    "card_empty":           {"he": "כרטיסייה עם צד ריק", "blocking": True},
    "key_position_skew":    {"he": "הטיה במיקום התשובה הנכונה", "blocking": False},
    "key_longest":          {"he": "המסיח הנכון ארוך ב-30% מכל האחרים",
                             "blocking": False},
    "impossible_citation":  {"he": "ציטוט של עמוד שאינו קיים", "blocking": True},
    "value_unsourced":      {"he": "ערך שלא נמצא בעמוד המצוטט (רמז לשופט)",
                             "blocking": False},
    "hygiene_signoff":      {"he": "סיום שיחה של צ'אטבוט", "blocking": True},
    "hygiene_latex":        {"he": "LaTeX או תווי בריחה בטקסט", "blocking": True},
    "hygiene_leaked_label": {"he": "תווית פנימית שדלפה", "blocking": True},
    "hygiene_marker":       {"he": "סימוני [n] של NotebookLM", "blocking": True},
    "hygiene_arabic":       {"he": "אותיות ערביות בתוך עברית", "blocking": True},
    "hygiene_english_stem": {"he": "גוף שאלה באנגלית בלבד", "blocking": False},
    "legend_chip_is_page":  {"he": "'מספרים לבחינה' שהוא מספר עמוד", "blocking": True},
    "legend_chip_lost_unit": {"he": "'מספרים לבחינה' שאיבד יחידה", "blocking": False},
    "missing_asset":        {"he": "נכס חובה חסר", "blocking": True},
    "empty_asset":          {"he": "נכס חובה ריק (אין מה לשפוט)", "blocking": True},
    "backfill_asset":       {"he": "נכס להשלמה (אינו חוסם)", "blocking": False},
    "deferred_asset":       {"he": "נכס שכובה בכוונה (מכסה)", "blocking": False},
    "duplicate_unit":       {"he": "כפילות בתוך הפרק", "blocking": False},
    "cross_duplicate":      {"he": "כפילות בין פרקים", "blocking": False},
}

# §5.4's type list. `untyped` is not an error: 107 of the 149 sampled items
# predate the `type` field and the app grades them as single-answer, which is
# what they are. Anything outside this set is a type the app cannot grade.
KNOWN_TYPES = frozenset({"multiple_choice", "multiple_select",
                         "fill_in_the_blank", "short_answer", "untyped"})
# Types graded as one radio button: exactly one key, at least two options.
SINGLE_ANSWER_TYPES = frozenset({"multiple_choice", "untyped"})

# The four assets §3 judges. A chapter missing one of them is not a chapter —
# daily_study already refuses to publish without the guide and facts, so a gap
# here means the release was assembled by an older pipeline (001, 003, 008-014,
# 028, 032) and §5.9 stages the missing asset before judging.
REQUIRED_ASSETS = ("study_guide.md", "facts.json", "quiz.json", "flashcards.json")
# Present in a complete release, absent in many. Never blocking: "video: not
# checked" is a badge line (§5.11), not a reason to hold the chapter.
BACKFILL_ASSETS = {"video.mp4": "וידאו"}
# Absent BY DECISION, not by failure: bulk_run.py sets SKIP_QUOTA_ARTIFACTS=1
# because NotebookLM throttles audio and infographics after about two chapters a
# day, and a purpose-built backfill script fills them in later. Counting those
# as gaps produced 136 of the 156 backfill_asset flags in the 2026-09-25 sweep —
# 68 of 70 chapters, which read as "the corpus is full of holes" when what it
# says is "we turned two generators off on purpose". They get their own quiet
# check so the real gap (a plate with no legend) is visible again.
DEFERRED_ASSETS = {"audio.mp3": "אודיו"}
_INFOGRAPHIC_RE = re.compile(r"^infographic(?:_.+)?\.png$", re.I)
_PLATE_PNG_RE = re.compile(r"^(sketchy_[\w\-]*)\.png$", re.I)

# --- key position -----------------------------------------------------------
# Under 8 single-answer items the distribution is noise: 5 items with 4 options
# put 3 keys on one letter by chance often enough to flag half the chapters.
KEY_POS_MIN_ITEMS = 8
# §5.9's fixer targets <= 3 keys per position per 10 (share 0.3). The lint bar
# sits well above it so that a chapter which has just been rebalanced can never
# come back flagged — a gate that fires on its own fix is a gate nobody trusts.
KEY_POS_MAX_SHARE = 0.5
# "Longest by > 30%": the key is longer than the longest DISTRACTOR by a third.
# Measured on the 132 sampled single-answer items: 61% have the key as the
# unique longest, 23% clear this bar. The bar, not the 61%, is what a learner
# can actually see at a glance.
KEY_LONGEST_RATIO = 1.3

# --- near-duplicates --------------------------------------------------------
JACCARD_MIN = 0.5
# Below this a Jaccard is meaningless: two 4-token card fronts that share
# "מהו" and "של" score 0.5 while saying different things.
DUP_MIN_TOKENS = 5

# --- locked values ----------------------------------------------------------
# A page window of +-1 around the cited page, the same radius qc_norm.source_window
# uses, because the Dulcan p.330/331 sentence lives on neither page alone.
VALUE_PAGE_RADIUS = 1
# A value that occurs on more than this many pages of the book is not evidence
# of anything: "3" is on nearly every page of the DSM. Scanning them all for a
# key term would cost seconds per value and could only ever return "found".
MAX_VALUE_PAGES = 200
# The unit has to name something for a full-text hit to mean the same claim.
# §5.7: "A bare value match is not enough (StahlKids repeats 'may take 2-8
# weeks' on 12 pages)."
MAX_KEY_TERMS = 12
MIN_KEY_TERM_LEN = 4

_NUM_RUN = re.compile(r"\d+(?:\|\d+)*")
_HEB = re.compile(r"[\u0590-\u05FF]")
_ARABIC = re.compile(r"[\u0600-\u06FF]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]{%d,}" % (MIN_KEY_TERM_LEN - 1))

# The three sign-offs §5.4 names, each taken verbatim from a released guide.
_SIGNOFFS = ("האם תרצה", "אשמח ל", "מדריך זה כולל את מלוא")
_LEAKED_LABELS = ("General Instruction Filter", "Source Summary")
# NotebookLM's inline source markers. A marker is a LIST of source numbers and
# not always one of them: `[2]`, `[2, 3]`, `[13-16]`. Matching `\[\d{1,2}\]`
# alone found 311 of the 614 markers in the corpus (measured 2026-09-25 over
# _qc/orig), so this BLOCKING check passed guides still reading
# `(Dulcan p. 176) [2, 3]`, and qc_fix stripped half of a chapter's markers and
# left the rest in front of the learner.
# Newlines are excluded on purpose, not by accident: facts.json's lists are
# dumped with indent=1, so `[\n 1,` must never look like a marker. A markdown
# link is `[text](url)`, whose text is never digits-only; qc_fix's own pattern
# adds `(?!\()` on top of that.
_MARKER_BODY = (r"\[[ \t]*\d{1,3}(?:[ \t]*[,–—-][ \t]*\d{1,3})*[ \t]*\]"
                r"(?!\()")
_MARKER_RE = re.compile(_MARKER_BODY)
# Escaped LaTeX as it actually appears in the corpus: `\\(` and `\\)` around
# `\alpha_{2A}`, `$5\text{-HT}_{2C}$`, `\%`, and the stray `\"` of `40 מ\"ג`,
# which is a JSON escape that survived into markdown. One backslash or two.
_LATEX_RE = re.compile(r"\\{1,2}(?:[A-Za-z]{2,}|[(){}\[\]%$_^&#~\"'])")
# Inline math: `$...$` with something LaTeX-ish inside, so a plain "$5" (a price
# this material never quotes, but still) does not fire on its own.
_MATH_RE = re.compile(r"\$[^$\n]{0,120}[\\^_{][^$\n]{0,120}\$")
# A stem this long with no Hebrew letter at all is an untranslated item: the
# owner studies in Hebrew and the app renders RTL. Short English stems ("MTA
# study?") are not the failure; whole vignettes are (36 of the sampled items).
MIN_ENGLISH_STEM_CHARS = 40

# Words that carry no identity, so a page containing only these has not been
# shown to be about the same claim. Kept small and English-only: the key terms
# this filters are drug, trial and disorder names, which the material writes in
# English even inside Hebrew sentences.
#
# The second block is qc_units' own field labels. They are in every unit's text
# because the renderer puts them there, and "front" or "answer" occurs on half
# the pages of every book — a key-term window built from them would accept any
# page at all.
_KEY_TERM_STOP = frozenset("""
about above after against among because been before being between both
during each either from have here into like more most much must only other
over same some such than that their them then there these they this those
through under using were what when where which while with would your
page pages chapter chapters table figure section
dsm dulcan stahl stahlkids stahlneuro synopsis kaplan sadock syllabus
days weeks months years hours minutes dose doses level levels effect effects
study studies trial trials patients children adolescents treatment

type untyped stem question front back hint rationale option options
answer answers bestanswer acceptableanswer modelanswer grading
requiredattribute misconception bluff choice select fill blank short multiple
""".split())


# ---------------------------------------------------------------------------
# small helpers


def _add(flags: dict, uid: str, check: str, detail: str) -> None:
    flags.setdefault(uid, []).append({"check": check, "detail": detail})


def _strings(node) -> list[str]:
    """Every string leaf of a parsed JSON value, in order."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _hygiene_text(u: qc_units.Unit) -> str:
    """The unit's text as a human sees it, not as JSON stores it.

    facts.json units are rendered by qc_units as `json.dumps(value)`, which
    re-escapes every `"` inside a Hebrew string — and `\\"` is exactly the
    escaped-LaTeX signature below. Linting the parsed strings instead keeps the
    check honest about which backslashes were in the released prose.
    """
    if u.meta.get("kind") == "facts":
        return "\n".join(_strings(u.meta.get("value")))
    return u.text


_QUIZ_FIELD_LINE = re.compile(r"^(?:[A-Z]{1,2}\. |\s+\w+: |[A-Za-z][\w.]*: )")


def head_text(u: qc_units.Unit) -> str | None:
    """The stem of a quiz item or the front of a card — what a duplicate is.

    Cards carry their front in `meta`. Quiz items do not carry their stem, so it
    is read back out of the ONE rendering (qc_units.render_quiz writes `stem: `
    as line 2 and every following field on its own labelled line). No stem in
    the 149 sampled items contains a newline, but reading to the next field line
    rather than to the next newline costs nothing and survives one that does.
    """
    kind = u.meta.get("kind")
    if kind == "card":
        return u.meta.get("front") or None
    if kind != "quiz":
        return None
    lines = u.text.split("\n")
    out: list[str] = []
    for line in lines[1:]:
        if out and _QUIZ_FIELD_LINE.match(line):
            break
        if not out:
            if not line.startswith("stem: "):
                return None
            out.append(line[len("stem: "):])
        else:
            out.append(line)
    text = "\n".join(out).strip()
    return text or None


def _dup_tokens(text: str) -> set[str]:
    """Normalized word tokens, for the Jaccard. Single characters go: a Hebrew
    one-letter preposition is not evidence that two questions are the same."""
    toks = {qc_norm.norm(w) for w in text.split()}
    return {t for t in toks if len(t) > 1}


_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _letter(i: int) -> str:
    """A, B, C… then AA, AB — the same labels qc_units.render_quiz writes into
    the bundle, so a lint flag and a judge's verdict name the same option."""
    if i < 26:
        return _LETTERS[i]
    return _LETTERS[i // 26 - 1] + _LETTERS[i % 26]


def _options(item: dict) -> list:
    opts = item.get("answerOptions")
    if opts is None:
        opts = item.get("options")
    return opts if isinstance(opts, list) else []


def _quiz_item_list(data):
    """The question list, by the same key search qc_units uses.

    Duplicated rather than imported because it is qc_units' private helper; it
    cannot disagree with it, because a shape this misses raised UnitError before
    this module ever got here.
    """
    if isinstance(data, dict):
        for key in ("questions", "items", "quiz"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# quiz schema and types


def _lint_quiz(units_by_uid: dict, raw_text: str, flags: dict) -> dict:
    """Schema, types and empty fields, plus the key-position statistics."""
    try:
        items = _quiz_item_list(json.loads(raw_text))
    except json.JSONDecodeError:
        return {}                     # already reported as asset_unparsable

    positions: Counter = Counter()
    single = 0
    types: Counter = Counter()

    for i, item in enumerate(items):
        uid = f"Q{i + 1}"
        if uid not in units_by_uid or not isinstance(item, dict):
            continue
        qtype = item.get("type") or "untyped"
        types[qtype] += 1
        if qtype not in KNOWN_TYPES:
            # The app grades what it knows and silently shows the rest ungraded:
            # 17 of 42 items released since 2026-09-14 were in that state.
            _add(flags, uid, "quiz_unknown_type", qtype)

        opts = _options(item)
        keys = [j for j, o in enumerate(opts)
                if isinstance(o, dict) and o.get("isCorrect")]

        # str() because the field has been a number and a list in releases from
        # older pipelines; a TypeError here would lose the rest of the chapter.
        if not str(item.get("question") or item.get("stem") or "").strip():
            _add(flags, uid, "quiz_empty_field", "stem ריק")
        for j, o in enumerate(opts):
            text = o.get("text", "") if isinstance(o, dict) else str(o)
            if not str(text).strip():
                _add(flags, uid, "quiz_empty_field", f"אפשרות {j + 1} ריקה")
        hint = item.get("hint")
        if isinstance(hint, str) and not hint.strip():
            _add(flags, uid, "quiz_empty_field", "hint ריק")

        if qtype in SINGLE_ANSWER_TYPES or (qtype not in KNOWN_TYPES and opts):
            if len(opts) < 2:
                _add(flags, uid, "quiz_too_few_options", f"{len(opts)} אפשרויות")
            elif len(keys) != 1:
                _add(flags, uid, "quiz_key_count",
                     f"{len(keys)} תשובות נכונות מתוך {len(opts)}")
            else:
                single += 1
                positions[keys[0]] += 1
                texts = [str(o.get("text", "")) if isinstance(o, dict) else str(o)
                         for o in opts]
                others = max(len(t) for j, t in enumerate(texts) if j != keys[0])
                if others and len(texts[keys[0]]) > KEY_LONGEST_RATIO * others:
                    _add(flags, uid, "key_longest",
                         f"{len(texts[keys[0]])} תווים מול {others}")
        elif qtype == "multiple_select":
            if len(opts) < 2:
                _add(flags, uid, "quiz_too_few_options", f"{len(opts)} אפשרויות")
            if len(keys) < 2:
                # A multiple_select with one key is a multiple_choice mislabelled,
                # and the app grades it all-or-nothing against a single answer.
                _add(flags, uid, "quiz_key_count",
                     f"multiple_select עם {len(keys)} תשובות נכונות")
        elif qtype == "fill_in_the_blank":
            answers = [a for a in ([item.get("bestAnswer")]
                                   + list(item.get("acceptableAnswers") or []))
                       if isinstance(a, str) and a.strip()]
            if not answers:
                _add(flags, uid, "quiz_missing_answer", "אין bestAnswer")
        elif qtype == "short_answer":
            grading = item.get("grading")
            model = grading.get("modelAnswer") if isinstance(grading, dict) else None
            if not (isinstance(model, str) and model.strip()):
                _add(flags, uid, "quiz_missing_answer", "אין modelAnswer")

    return {"types": dict(types), "single_answer": single,
            "key_positions": {_letter(i): n for i, n in sorted(positions.items())}}


def _lint_key_positions(stats: dict, chapter: list) -> None:
    """The distribution, judged as a whole. Per item it is not a defect."""
    positions = stats.get("key_positions") or {}
    n = stats.get("single_answer") or 0
    if n < KEY_POS_MIN_ITEMS or not positions:
        return
    letter, top = max(positions.items(), key=lambda kv: kv[1])
    share = top / n
    if share > KEY_POS_MAX_SHARE:
        chapter.append({
            "check": "key_position_skew",
            "detail": (f"{letter} ב-{top} מתוך {n} שאלות ({share:.0%}); "
                       f"התפלגות " + " ".join(f"{k}={v}" for k, v in
                                              sorted(positions.items())))})


# ---------------------------------------------------------------------------
# cards


def _lint_cards(units_by_uid: dict, flags: dict) -> None:
    for uid, u in units_by_uid.items():
        if u.meta.get("kind") != "card":
            continue
        if not str(u.meta.get("front", "")).strip():
            _add(flags, uid, "card_empty", "front ריק")
        if not str(u.meta.get("back", "")).strip():
            _add(flags, uid, "card_empty", "back ריק")


# ---------------------------------------------------------------------------
# hygiene


def _lint_hygiene(u: qc_units.Unit, flags: dict) -> None:
    text = _hygiene_text(u)
    for phrase in _SIGNOFFS:
        if phrase in text:
            _add(flags, u.uid, "hygiene_signoff", phrase)
    _span_flag(flags, u.uid, "hygiene_latex", text, _LATEX_RE, _MATH_RE)
    for label in _LEAKED_LABELS:
        if label in text:
            _add(flags, u.uid, "hygiene_leaked_label", label)
    _span_flag(flags, u.uid, "hygiene_marker", text, _MARKER_RE)
    m = _ARABIC.search(text)
    if m:
        # The letter is fused INTO a Hebrew word (`המ\u0645ושגית`), so the flag shows
        # the word: an owner reading "\u0645" alone cannot find it in the file.
        _add(flags, u.uid, "hygiene_arabic", _snip(text, m.start(), m.end(), 18))
    if u.meta.get("kind") == "quiz":
        stem = head_text(u) or ""
        if len(stem) >= MIN_ENGLISH_STEM_CHARS and not _HEB.search(stem):
            _add(flags, u.uid, "hygiene_english_stem", stem[:80])


def _snip(text: str, a: int, b: int, pad: int = 30) -> str:
    return " ".join(text[max(0, a - pad):b + pad].split())


def _span_flag(flags: dict, uid: str, check: str, text: str, *patterns,
               pad: int = 30) -> None:
    """One flag per unit, but say how many spans are behind it.

    `search` alone made the owner's report understate the work: chapter 059's
    SGA monitoring table has 26 cells reading `\\(\\checkmark\\)` and produced a
    single hygiene_latex flag quoting one of them. One flag per unit is the
    right unit of work — qc_fix places a note by uid, and inflating the count
    would also move every false-positive RATE this family is measured by — so
    the count goes in the detail instead of into a second flag.
    """
    hits = [m for p in patterns for m in p.finditer(text)]
    if not hits:
        return
    hits.sort(key=lambda m: m.start())
    detail = _snip(text, hits[0].start(), hits[0].end(), pad)
    if len(hits) > 1:
        detail += f"  (+{len(hits) - 1} נוספים ביחידה)"
    _add(flags, uid, check, detail)


# ---------------------------------------------------------------------------
# citations


def _page_donors(book: str, printed: int, unit_cites: list) -> tuple[list, list]:
    """Which OTHER books of the notebook do have this printed page.

    "StahlKids has no page 774" is true and leaves the owner nowhere. The
    2026-09-25 audit opened the pages by hand and found the shape behind a
    quarter of this family: the NUMBER is right and the BOOK is wrong, the page
    having bled off a neighbouring citation — `(Dulcan p.835, StahlKids p.835)`
    is the bug bare, one number attributed to two books. The note that says
    "this page does not exist" invites him to delete a correct pointer.

    Two tiers, because they are not equally strong evidence:
      * `near`  — a book cited ELSEWHERE IN THE SAME UNIT that has the page.
                  That is the bleed, and it names the likely donor.
      * `any_`  — any other book that has it. Weaker, but it is still the
                  difference between "look here" and "nowhere".
    Neither decides the fact: only a reader with the book open does (§5.8).
    """
    def has(key: str) -> bool:
        try:
            return bool(qc_books.book(key).has_page(printed))
        except Exception:
            return False

    near_keys = {c["book"] for c in unit_cites
                 if c.get("book") and c["book"] != book}
    near = sorted(k for k in near_keys if k in qc_books.SPECS and has(k))
    any_ = sorted(k for k in qc_books.SPECS
                  if k != book and k not in near and has(k))
    return near, any_


def _lint_citations(cites_by_uid: dict, flags: dict, skipped: list) -> None:
    """Impossible pages, per unit. Needs the PDFs.

    qc_cite.impossible raises when `Text BOOKS/` is missing, and the whole point
    of the check is that a chapter cannot look clean because a file was absent
    — so the failure is recorded, loudly, instead of swallowed.
    """
    tops: dict[str, int] = {}
    for uid, cites in cites_by_uid.items():
        try:
            bad = qc_cite.impossible(cites)
        except Exception as exc:
            skipped.append({"check": "impossible_citation", "reason": str(exc)})
            return
        for c in bad:
            # Only the books that actually produced a bad page are opened for
            # their max_printed: building all six costs ~15 s and 6 MB of cache
            # to print a number in a message.
            if c["book"] not in tops:
                try:
                    tops[c["book"]] = qc_books.book(c["book"]).max_printed
                except Exception:
                    tops[c["book"]] = 0
            top = tops[c["book"]]
            near, any_ = _page_donors(c["book"], c["printed"], cites)
            # Tier 0, and the commonest shape in the corpus: ONE citation listing
            # several pages for one book, of which the first is valid and a later
            # one is not — `(StahlNeuro p. 114, 654)`, `(StahlNeuro p. 289, 902)`.
            # Saying which of the two numbers is the good one is the whole note:
            # the owner keeps p.114 and asks about p.654, instead of losing both.
            ok_same_raw = sorted({s["printed"] for s in cites
                                  if s.get("raw") == c.get("raw")
                                  and s.get("book") == c.get("book")
                                  and s.get("printed") is not None
                                  and s not in bad})
            if ok_same_raw:
                where = (f"; באותו ציטוט עמ' "
                         + ", ".join(str(p) for p in ok_same_raw[:3])
                         + " כן תקין — כנראה מספר עמוד עודף או של ספר אחר")
            elif near:
                where = ("; העמוד הזה קיים ב-" + ", ".join(near[:2])
                         + ", שמצוטט באותה יחידה — ייתכן ששם הספר הוא הטעות")
            elif any_:
                where = "; לעמוד הזה יש טקסט ב-" + ", ".join(any_[:2])
            else:
                where = "; אין עמוד כזה באף ספר במחברת"
            _add(flags, uid, "impossible_citation",
                 f"{c['book']} עמ' {c['printed']}"
                 + (f" (עד {top})" if top else "") + where
                 + f" — {c['raw'][:60]}")


# ---------------------------------------------------------------------------
# locked values against the cited pages


@lru_cache(maxsize=None)
def _book_numbers(key: str) -> tuple[dict, dict]:
    """(printed -> set of number tokens, token -> sorted printed pages).

    The tokens come from `qc_norm.norm`, so "1.4" and "2-8" are stored as `1|4`
    and `2|8` and can never be read as "14" or "28" — the collapse the boundary
    sentinel exists to prevent. Built once per book per process: measured 1.3 s
    for StahlKids' 493 distinct pages, ~3 s each for the DSM and Dulcan.
    """
    b = qc_books.book(key)
    per_page: dict[int, frozenset] = {}
    by_text: dict[int, frozenset] = {}
    for n in range(1, b.max_printed + 1):
        text = b.page_text(n)
        if text is None:
            continue
        # A reflowed book folds unnumbered printed pages into the previous
        # page's text object; normalizing it once per object rather than once
        # per page is the difference between 3 s and 12 s on Dulcan.
        ident = id(text)
        if ident not in by_text:
            by_text[ident] = frozenset(_NUM_RUN.findall(qc_norm.norm(text)))
        per_page[n] = by_text[ident]
    inverted: dict[str, list] = {}
    for n, toks in per_page.items():
        for t in toks:
            inverted.setdefault(t, []).append(n)
    return per_page, {t: sorted(v) for t, v in inverted.items()}


def _value_token(raw: str) -> str | None:
    """The normalized number of a locked value: `1.4 mg/kg/day` -> `1|4`."""
    m = _NUM_RUN.search(qc_norm.norm(raw))
    return m.group(0) if m else None


def key_terms(text: str) -> list[str]:
    """The English names a unit uses: drugs, trials, disorders, scales.

    This material writes them in English inside Hebrew sentences (Atomoxetine,
    CAMS, GAD-7), which is what makes a cheap full-text window possible at all.
    Longest first, because a long name is the one that identifies a page.
    """
    seen: dict[str, None] = {}
    for m in _LATIN_WORD.finditer(text):
        w = m.group(0)
        if w.lower() not in _KEY_TERM_STOP:
            seen.setdefault(w, None)
    return sorted(seen, key=len, reverse=True)[:MAX_KEY_TERMS]


def _blank_citations(text: str, cites: list) -> str:
    """The unit's text with every citation's number blanked, offsets intact.

    qc_norm.locked_values already suppresses `p. 141`, `(StahlNeuro p. 289, 902)`
    and `עמ' 506`, but it cannot see the guides' coverage table, where the book
    is the column header and the cell is bare numbers (`35, 68, 74`). Blanking
    the spans qc_cite found is how that cell stops handing the judge three page
    numbers to "dispute". Spaces, not deletion: `raw` slices stay meaningful.
    """
    if not cites:
        return text
    out = list(text)
    for c in cites:
        a, b = c["span"]
        for i in range(max(0, a), min(len(out), b)):
            out[i] = " "
    return "".join(out)


def _lint_values(u: qc_units.Unit, cites: list, flags: dict) -> None:
    """A locked value that is on no cited page ±1 and nowhere in the cited book
    beside a term this unit names. A PRIORITY for the judge, never a verdict.

    The match is on the whole normalized number run, so the material's
    `1.5-3 שעות` (`1|5|3`) does not match a page that writes the same span as
    "1.5 to 3 hours" (`1|5` … `3`). Splitting the run to cover that would mean
    accepting any page carrying a lone "3", which is every page — and the value
    of a hint list is that it is short. A missed hint costs nothing: §5.8 checks
    every unit regardless of what this ordered first.
    """
    pages_by_book: dict[str, list] = {}
    for c in cites:
        if c["printed"] is None or c["book"] not in qc_books.SPECS:
            continue                 # Synopsis has no pages; `Stahl?` no book
        pages_by_book.setdefault(c["book"], []).append(c["printed"])
    if not pages_by_book:
        return                       # nothing cited: that is the judge's call
    terms = key_terms(u.text)
    if not terms:
        # With no name on the unit, a full-text hit could not mean this claim,
        # so the second half of the test can never pass and every value would
        # flag. Silence beats a page of hints nobody can act on.
        return
    term_re = re.compile("|".join(re.escape(t) for t in terms), re.I)

    for v in qc_norm.locked_values(_blank_citations(u.text, cites)):
        token = _value_token(v["raw"])
        if token is None:
            continue                 # a spelled-out count ("three"): no digits
        where = None
        for book_key, printed_pages in pages_by_book.items():
            per_page, inverted = _book_numbers(book_key)
            window = {p + d for p in printed_pages
                      for d in range(-VALUE_PAGE_RADIUS, VALUE_PAGE_RADIUS + 1)}
            if any(token in per_page.get(p, ()) for p in window):
                where = "cited"
                break
            hits = inverted.get(token, [])
            if len(hits) > MAX_VALUE_PAGES:
                where = "common"     # too common to prove anything either way
                break
            page = next((p for p in hits
                         if term_re.search(qc_books.book(book_key).page_text(p) or "")),
                        None)
            if page is not None:
                where = f"{book_key} עמ' {page}"
                break
        if where is None:
            _add(flags, u.uid, "value_unsourced",
                 f"{v['raw'].strip()} ({v['kind']}) — לא בעמודים המצוטטים: "
                 + ", ".join(f"{b} {_pages_brief(p)}"
                             for b, p in sorted(pages_by_book.items())))


def _pages_brief(pages: list, keep: int = 6) -> str:
    uniq = sorted(set(pages))
    shown = ", ".join(str(p) for p in uniq[:keep])
    return f"עמ' {shown}" + (f" ועוד {len(uniq) - keep}" if len(uniq) > keep else "")


# ---------------------------------------------------------------------------
# legends


_CHIP_PAGE_RE = re.compile(r"^~?\s*(\d{1,4})$")


def _lint_legend_chips(units: list, cites_by_uid: dict, flags: dict) -> None:
    """A "מספרים לבחינה" chip that is a page number cited in the same legend.

    The chip row is what the app puts in front of the learner as the numbers to
    memorise. A page number in it is not a number to memorise, and there is no
    way to tell one from the other except that the same legend cites it as a
    page. Only a bare integer can be a page: `0.4%`, `2-8wk` and `~18h` carry
    their own unit and are never a folio.
    """
    for u in units:
        if u.meta.get("kind") != "legend_numbers":
            continue
        cited = {c["printed"] for uid, cs in cites_by_uid.items()
                 for c in cs if c["printed"] is not None
                 and uid.startswith(f"L:{u.meta.get('file')}:")}
        chips = [str(x).strip() for x in (u.meta.get("chips") or [])]
        for chip in chips:
            m = _CHIP_PAGE_RE.match(chip)
            if not m or int(m.group(1)) not in cited:
                continue
            # Half of this check's flags were a different defect wearing the
            # same clothes (audited 2026-09-25, 4 of 8). Chapter 008's row reads
            # `18mo · עד18mo · 18 · 18-36mo`: the bare `18` is 18 months with the
            # unit dropped, and it coincides with a cited page. Chapter 020's
            # reads `40% · 140 · 460 · 141 · 67`, where nothing carries those
            # numbers with a unit and the legend cites `(Dulcan p.140, 493)` and
            # `(Dulcan p.141)` — those are folios in the row of numbers to
            # memorise. The twin is what tells them apart, so each is now named
            # for what it is instead of one of them being wrong.
            digits = m.group(1)
            bounded = re.compile(rf"(?<!\d){re.escape(digits)}(?!\d)")
            twin = any(other != chip and bounded.search(other)
                       for other in chips)
            if twin:
                _add(flags, u.uid, "legend_chip_lost_unit",
                     f"{chip} — מופיע באותה שורה גם עם יחידה; כנראה נשמטה")
            else:
                _add(flags, u.uid, "legend_chip_is_page",
                     f"{chip} — עמוד מצוטט באותה אגדה")


# ---------------------------------------------------------------------------
# completeness and duplicates


def _lint_completeness(assets: dict, chapter: list, units: list) -> None:
    """Present, and holding something a judge could read.

    Every other gate in the mechanism is expressed over UNITS, so an asset that
    parses but yields none of them is indistinguishable from a healthy one:
    `quiz.json` = `[]` raises no UnitError, every per-item check is keyed on a
    uid that does not exist, qc_run's coverage hold sees nothing uncovered, and
    the chapter reaches `approved` with qc.json telling the app the quiz was
    checked against the textbooks. §2.3 asks the opposite — an asset nobody
    could check must not look like an asset that passed — so a truncated or
    failed generation is a blocking defect here, next to the missing one.
    """
    names = set(assets)
    have_units = {u.artifact for u in units}
    for name in REQUIRED_ASSETS:
        if name not in names:
            chapter.append({"check": "missing_asset", "detail": name})
        elif isinstance(assets.get(name), str) and name not in have_units:
            chapter.append({"check": "empty_asset", "detail": name})
    for name, he in BACKFILL_ASSETS.items():
        if name not in names:
            chapter.append({"check": "backfill_asset", "detail": f"{name} ({he})"})
    for name, he in DEFERRED_ASSETS.items():
        if name not in names:
            chapter.append({"check": "deferred_asset", "detail": f"{name} ({he})"})
    if not any(_INFOGRAPHIC_RE.match(n) for n in names):
        chapter.append({"check": "deferred_asset", "detail": "infographic*.png"})
    for name in sorted(names):
        m = _PLATE_PNG_RE.match(name)
        if m and f"{m.group(1)}.txt" not in names:
            # The legend is what makes the plate teachable and is the only part
            # of it §3 judges; a plate without one is an unjudgeable asset.
            chapter.append({"check": "backfill_asset",
                            "detail": f"{m.group(1)}.txt (אגדה חסרה)"})


def _lint_duplicates(units: list, flags: dict) -> None:
    """Two items in one chapter asking the same thing, by normalized stem/front.

    Exact equality on the normalized form, not Jaccard: inside one chapter the
    duplicates are copies, and a near-miss here is usually a deliberate pair
    (the same drug asked from two angles), which the owner does want.
    """
    groups: dict[str, list] = {}
    for u in units:
        head = head_text(u)
        if head:
            groups.setdefault(qc_norm.norm(head), []).append(u.uid)
    for key, uids in groups.items():
        if len(uids) > 1:
            for uid in uids:
                _add(flags, uid, "duplicate_unit",
                     "זהה ל-" + ", ".join(x for x in uids if x != uid))


# ---------------------------------------------------------------------------
# the chapter


def chapter_units(assets: dict) -> tuple[list, list[str]]:
    """(every unit of the chapter, the UnitError messages of the assets that
    could not be turned into units).

    Fail closed and stay standing: a quiz.json that does not parse is reported
    as the blocking defect it is (§2.3 — it must not look like a chapter with no
    quiz), and the other assets are still linted so one bad file cannot hide
    the defects in the rest. qc_run feeds the units half straight into
    lint_global rather than deriving them a second time.
    """
    units: list = []
    broken: list[str] = []
    for name in sorted(assets):
        text = assets.get(name)
        if not isinstance(text, str) or not qc_units.is_judged_asset(name):
            continue
        try:
            units.extend(qc_units.units_from_asset(name, text))
        except qc_units.UnitError as exc:
            broken.append(str(exc))
    return units, broken


def lint_chapter(tag: str, assets: dict, *, use_books: bool | None = None) -> dict:
    """Every deterministic check of §5.4, on one chapter's released bytes.

    `assets` maps release asset name -> its text. A binary asset (audio.mp3,
    video.mp4, a plate PNG) maps to None: its bytes are not linted, only its
    presence, which is what the completeness check needs.

    Returns {tag, assets, units, counts, flags, chapter, stats, blocking,
    skipped}: `counts` is per check, `flags` is uid -> [{check, detail}] and
    `chapter` holds the findings that belong to no single unit (completeness,
    key-position distribution). `blocking` lists the checks that fired and that
    CHECKS marks as blocking — qc_run turns that into a status, this does not.

    `use_books=False` runs the offline checks only. The default tries the books
    and records what it could not run in `skipped`, because a chapter that looks
    clean only because a PDF was missing is the failure §2.3 forbids.
    """
    flags: dict[str, list] = {}
    skipped: list = []

    units, broken = chapter_units(assets)
    units_by_uid = {u.uid: u for u in units}
    chapter: list = [{"check": "asset_unparsable", "detail": msg} for msg in broken]

    stats: dict = {}
    if isinstance(assets.get("quiz.json"), str):
        stats = _lint_quiz(units_by_uid, assets["quiz.json"], flags)
        _lint_key_positions(stats, chapter)
    _lint_cards(units_by_uid, flags)

    for u in units:
        _lint_hygiene(u, flags)

    cites_by_uid = qc_cite.cites_by_unit(units)
    _lint_legend_chips(units, cites_by_uid, flags)
    _lint_duplicates(units, flags)
    _lint_completeness(assets, chapter, units)

    if use_books is not False:
        _lint_citations(cites_by_uid, flags, skipped)
        try:
            for u in units:
                _lint_values(u, cites_by_uid.get(u.uid, []), flags)
        except Exception as exc:                 # no PDFs, no PyMuPDF, no cache
            skipped.append({"check": "value_unsourced", "reason": str(exc)})
    else:
        for check in ("impossible_citation", "value_unsourced"):
            skipped.append({"check": check, "reason": "use_books=False"})

    counts: Counter = Counter()
    for items in flags.values():
        for f in items:
            counts[f["check"]] += 1
    for f in chapter:
        counts[f["check"]] += 1

    stats["citations"] = sum(len(v) for v in cites_by_uid.values())
    return {
        "tag": tag,
        "assets": sorted(assets),
        "units": len(units),
        "counts": dict(sorted(counts.items())),
        "flags": {uid: flags[uid] for uid in sorted(flags)},
        "chapter": chapter,
        "stats": stats,
        "blocking": sorted({c for c in counts
                            if CHECKS.get(c, {}).get("blocking")}),
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# across chapters


def lint_global(chapters: dict) -> dict:
    """Near-duplicate stems and card fronts across chapters (§5.4).

    `chapters` maps tag -> that chapter's qc_units.Unit list. Token Jaccard on
    the normalized stem/front, threshold 0.5.

    Why it is a separate pass and not part of lint_chapter: a duplicate is a
    property of the pair, and the owner's question about it ("am I answering
    the same item twice in one week?") is only answerable once every chapter is
    in hand. It is also why the result is a report and never blocks — the two
    chapters may legitimately both teach lithium levels.
    """
    entries: list = []
    for tag in sorted(chapters):
        for u in chapters[tag]:
            head = head_text(u)
            if not head:
                continue
            toks = _dup_tokens(head)
            if len(toks) >= DUP_MIN_TOKENS:
                entries.append((tag, u.uid, head, toks))
    # Sorted by token count so the size band below can stop early: |A∩B|/|A∪B|
    # >= 0.5 forces min(|A|,|B|) >= 0.5*max(|A|,|B|), so a longer B than that is
    # not a candidate and neither is anything after it. ~1,500 entries over the
    # 68 chapters, which is small enough that the band is a courtesy, not a need.
    entries.sort(key=lambda e: len(e[3]))

    pairs: list = []
    for i, (tag_a, uid_a, head_a, ta) in enumerate(entries):
        for tag_b, uid_b, head_b, tb in entries[i + 1:]:
            if len(ta) < JACCARD_MIN * len(tb):
                break
            if tag_a == tag_b:
                continue             # within a chapter: _lint_duplicates
            inter = len(ta & tb)
            if not inter:
                continue
            j = inter / len(ta | tb)
            if j >= JACCARD_MIN:
                pairs.append({"a_tag": tag_a, "a_uid": uid_a, "a_text": head_a[:160],
                              "b_tag": tag_b, "b_uid": uid_b, "b_text": head_b[:160],
                              "jaccard": round(j, 3)})
    pairs.sort(key=lambda p: -p["jaccard"])
    return {"chapters": len(chapters), "compared": len(entries),
            "counts": {"cross_duplicate": len(pairs)}, "pairs": pairs}


# ---------------------------------------------------------------------------
# CLI


def read_chapter_dir(d: Path) -> dict:
    """Asset name -> text, or None for the assets that are bytes.

    The directory is whatever qc_store.fetch put there: the release's own asset
    names, unrenamed, because every check below keys on them.
    """
    assets: dict = {}
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        if qc_units.is_judged_asset(p.name):
            assets[p.name] = p.read_text(encoding="utf-8")
        else:
            assets[p.name] = None
    return assets


def _print_summary(res: dict) -> None:
    print(f"\n=== {res['tag']} — {res['units']} יחידות, "
          f"{len(res['assets'])} נכסים, {res['stats'].get('citations', 0)} ציטוטים")
    if not res["counts"]:
        print("  אין ממצאים.")
    for check, n in sorted(res["counts"].items(),
                           key=lambda kv: (not CHECKS.get(kv[0], {}).get("blocking"),
                                           -kv[1])):
        info = CHECKS.get(check, {"he": check, "blocking": False})
        mark = "חוסם " if info["blocking"] else "      "
        print(f"  {mark}{info['he']:<36} {n}")
        shown = [f"{uid}: {f['detail']}" for uid, items in res["flags"].items()
                 for f in items if f["check"] == check]
        shown += [f["detail"] for f in res["chapter"] if f["check"] == check]
        for line in shown[:4]:
            print(f"        - {line}")
        if len(shown) > 4:
            print(f"        ... ועוד {len(shown) - 4}")
    pos = res["stats"].get("key_positions")
    if pos:
        n = res["stats"].get("single_answer") or 1
        print("  מיקום התשובה הנכונה: "
              + " ".join(f"{k}={v}" for k, v in sorted(pos.items()))
              + f"  (תמיד {max(pos, key=pos.get)}: {max(pos.values()) / n:.0%})")
    for s in res["skipped"]:
        print(f"  [!] לא נבדק — {CHECKS.get(s['check'], {}).get('he', s['check'])}:"
              f" {s['reason']}")
    if res["blocking"]:
        print("  חוסם אישור: " + ", ".join(res["blocking"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", action="append", required=True, type=Path,
                    help="a folder holding one chapter's release assets "
                         "(repeat it to also get the cross-chapter report)")
    ap.add_argument("--tag", help="the chapter tag; defaults to the folder name")
    ap.add_argument("--out", type=Path,
                    help="where the JSON goes (default _qc/<tag>/lint.json)")
    ap.add_argument("--no-books", action="store_true",
                    help="skip the checks that need the PDFs")
    a = ap.parse_args()
    if a.tag and len(a.dir) > 1:
        ap.error("--tag names one chapter; drop it when passing several --dir")

    results: list = []
    per_tag_units: dict = {}
    for d in a.dir:
        tag = a.tag or d.name
        assets = read_chapter_dir(d)
        res = lint_chapter(tag, assets, use_books=False if a.no_books else None)
        results.append(res)
        _print_summary(res)
        per_tag_units[tag] = chapter_units(assets)[0]

    payload: dict = results[0] if len(results) == 1 else {"chapters": results}
    if len(results) > 1:
        payload["global"] = lint_global(per_tag_units)
        print(f"\n=== כפילויות בין פרקים: "
              f"{payload['global']['counts']['cross_duplicate']}")
        for p in payload["global"]["pairs"][:10]:
            print(f"  {p['jaccard']:.2f}  {p['a_tag']} {p['a_uid']} ≈ "
                  f"{p['b_tag']} {p['b_uid']}")

    out = a.out or (REPO_ROOT / "_qc" / results[0]["tag"] / "lint.json"
                    if len(results) == 1 else REPO_ROOT / "_qc" / "lint.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(out)
    print(f"\nנכתב: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
