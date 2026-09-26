#!/usr/bin/env python3
"""Grading, QC marks and errata for the study app — the rules, without Streamlit.

WHY A SEPARATE MODULE. app.py graded every question with one radio button and
one `isCorrect` flag. Measured on the 17 sampled chapters (2026-09-18 spec
review, §1): 17 of 42 items released since 2026-09-14 are types that widget
grades wrongly or not at all — a `multiple_select` with three keys scored a
learner wrong for picking one of them, and `fill_in_the_blank` and
`short_answer` had no input at all. None of that could be tested, because
testing it meant running Streamlit. Everything decidable is therefore a pure
function here, and app.py only draws.

WHAT THE RELEASED DATA ACTUALLY LOOKS LIKE. Counted over the 17 sampled
chapters (149 items): 107 carry **no `type` field at all** (the pre-typing
NotebookLM shape), 25 `multiple_choice`, 6 `multiple_select`, 6
`fill_in_the_blank`, 5 `short_answer`. Every untyped item has exactly one
`isCorrect` option, so an untyped item with options is graded as
multiple_choice — inferred, never assumed from the absent field. Five of the
six multiple_select items have three keys and one has two, which is why
all-or-nothing scoring has to show *all* of them when it fails.

WHAT A QC MARK IS. qc_fix.py (spec §5.9, Tier 1) embeds, in the item or card it
found wrong, a record

    qc: {kind, date, was, source: {book, printed_page, quote}, severity, status}

and rewrites nothing. This module turns that record into something the app can
draw, and enforces the two policies that cost the learner if they are wrong:
only `kind == "content"` with `status == "active"` is ever shown (a
`presentation` fix — the answer-order rebalance of §5.9 — is invisible by
design, and `withdrawn` means qc_revert took it back), and a choice the
textbook itself supports is never scored wrong against a disputed key.

BACKWARD COMPATIBILITY IS NOT OPTIONAL. The app is deployed from `main` and
reads live releases; every chapter published before the QC run has no `qc`
field and no `qc.json` asset, and must keep working exactly as it did. Every
function here takes the absence of QC as the normal case.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime

# ── Item types ──────────────────────────────────────────────────────────────
MULTIPLE_CHOICE = "multiple_choice"
MULTIPLE_SELECT = "multiple_select"
FILL_IN_THE_BLANK = "fill_in_the_blank"
SHORT_ANSWER = "short_answer"
UNKNOWN = "unknown"

TYPE_HE = {
    MULTIPLE_CHOICE: "אמריקאית — תשובה אחת",
    MULTIPLE_SELECT: "אמריקאית — כמה תשובות נכונות",
    FILL_IN_THE_BLANK: "השלמת מילה",
    SHORT_ANSWER: "תשובה פתוחה — בדיקה עצמית",
    UNKNOWN: "",
}

SEVERITY_HE = {"high": "חמור", "medium": "בינוני", "low": "קל"}

# The canonical book keys are qc_books.SPECS'. They are duplicated here on
# purpose: qc_books imports PyMuPDF and opens the PDFs, and the Cloud Run image
# carries neither (it copies app.py and sr_engine.py and fetches everything else
# over HTTP). A display name is the only thing the app needs from that module.
# Kartisiot is named in Hebrew wherever it appears — owner decision 2026-09-18,
# policy 4: material grounded in סיכומי כרטיסיות must say so.
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
_BOOK_ALIASES = [
    (r"dsm", "DSM-5-TR"),
    (r"dulcan", "Dulcan"),
    (r"stahl\s*kids|prescriber|pediatric|children|ילדים", "StahlKids"),
    (r"stahl\s*neuro|essential", "StahlNeuro"),
    (r"synopsis|kaplan", "Synopsis"),
    (r"syllabus|סילבוס", "Syllabus"),
    (r"כרטיסיות|kartisiot", "Kartisiot"),
]

# Asset names as the releases carry them → what the owner calls them.
ASSET_HE = {
    "quiz.json": "קוויז",
    "flashcards.json": "כרטיסיות",
    "study_guide.md": "מדריך לימוד",
    "report.md": "מדריך לימוד (legacy)",
    "facts.json": "עובדות",
    "audio.mp3": "פודקאסט",
    "video.mp4": "וידאו",
    "infographic": "אינפוגרפיקה",
    "sketchy": "מקרא סצנה",
}


# ── Normalization ───────────────────────────────────────────────────────────
# Mirrors qc_norm.norm() (spec §5.2). Duplicated for the same reason as BOOK_HE:
# the deployed image holds app files only. The one rule that is easy to miss and
# expensive to get wrong: a run of punctuation BETWEEN TWO DIGITS collapses to a
# single boundary instead of vanishing, so "2-8 weeks" (StahlKids p.69) never
# normalizes onto "28 weeks" — that pair is a real answer and a real distractor
# in chapter 019.
_INVISIBLE = dict.fromkeys(
    map(ord, "\u00ad\u200b\u200e\u200f\u2066\u2067\u2068\u2069"), None)
_LATEX_TEXT = re.compile(r"\\text\s*\{([^}]*)\}")
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+\s*")
# `[\W_]`, not `[^\w]`: `_` is a word character, and LaTeX subscripts put one
# between digits all over this material. A released study-042 card back reads
# "$\alpha_4, \alpha_6$" — with `[^\w]+` the judge saw "4|6" and the app saw
# "46", so the two sides disagreed about what a value IS on the comparison that
# decides whether a learner's answer matches the textbook.
_DIGIT_BOUNDARY = re.compile(r"(?<=\d)[\W_]+(?=\d)", re.UNICODE)
_VALUE = re.compile(r"\d+(?:\|\d+)*")


def _clean(s) -> str:
    """Everything norm() does EXCEPT deleting the separators.

    Spaces and punctuation survive here so that the same normalization can also
    be read as words and clauses (`_clause_tokens`), which is what the
    source-backed override needs to tell a value's role from its digits. The
    digit boundary is already applied, so "2-8" is one token and the "." of
    "1.4" is no longer punctuation by the time a clause is split on it.
    """
    s = unicodedata.normalize("NFKC", str(s if s is not None else ""))
    s = s.translate(_INVISIBLE)
    s = _LATEX_TEXT.sub(r"\1", s)
    s = s.replace("\\%", "%").replace("$", "")
    s = _LATEX_CMD.sub(" ", s)
    s = s.replace("**", "").replace("__", "").replace("`", "")
    # NFD + drop combining marks removes niqqud and Latin accents alike.
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    s = s.casefold()
    return _DIGIT_BOUNDARY.sub("\x00", s)


def norm(s) -> str:
    """Casefolded, diacritic-free, punctuation-free form used for every match.

    The material is written in Hebrew with English drug names, markdown bold,
    and LaTeX left over from NotebookLM ($GABA_{A}$, \\text{mg}, \\%): a learner
    typing "buspirone" must match a key written "**Buspirone**", and a niqqud or
    a geresh must not decide an answer.
    """
    s = "".join(c for c in _clean(s) if c.isalnum() or c == "\x00")
    return s.replace("\x00", "|")


def _values(toks) -> set[str]:
    """The tokens that are a bare value ("2|8", "300").

    `fullmatch`, not a scan: "5ht2a" and "gaba1" are identifiers, and reading a
    5 and a 1 out of them would let an option about a receptor be "supported" by
    any page with those digits on it.
    """
    return {t for t in toks if _VALUE.fullmatch(t)}


def _content_words(toks) -> set[str]:
    """Tokens of length ≥ 4 that are not values — what an option is ABOUT.

    Four letters is what keeps "מהו" and "the" out; the length is measured on
    tokens and not on norm() output, because norm deletes the spaces and leaves
    "2-8 weeks of treatment" as a single run.
    """
    return {t for t in toks if len(t) >= 4 and not t.isdigit()
            and not _VALUE.fullmatch(t)}


# A clause is where a claim assigns a value its ROLE. "The maximum dose is
# 100 mg/day; the usual starting dose is 40 mg/day" states two doses and the
# option "the maximum dose is 40 mg/day" agrees with neither — the digits of one
# clause with the role word of the other. The digit boundary has already run by
# the time this splits, so the "." of "1.4" and the "," of "2,400" are inside a
# token and cannot open a clause.
_CLAUSE_SPLIT = re.compile(r"[.,;:!?\n…׃؛]+")
# A negation between the start of the clause and the words the option matched
# turns agreement into its opposite: "Antidepressant monotherapy is NOT
# recommended as first-line treatment" contains, verbatim, the option it rules
# out. Only markers that negate the predicate — a list kept short on purpose,
# because every entry here is a case where the override is silently refused.
_NEGATORS = frozenset({
    "not", "no", "never", "nor", "neither", "without", "cannot",
    "rather", "instead", "unlike", "except", "contraindicated",
    "לא", "שלא", "אינו", "אינה", "אינם", "אינן", "אין", "ללא",
    "במקום", "אסור", "אמנם",
})


def _split_tokens(cleaned: str) -> list[str]:
    """`_clean` output as word tokens, in order, in norm()'s spelling."""
    out: list[str] = []
    cur: list[str] = []
    for ch in cleaned:
        if ch.isalnum() or ch == "\x00":
            cur.append(ch)
        elif cur:
            out.append("".join(cur).replace("\x00", "|"))
            cur = []
    if cur:
        out.append("".join(cur).replace("\x00", "|"))
    return out


def _clause_tokens(s) -> list[list[str]]:
    """The text as clauses, each a list of normalized word tokens."""
    return [toks for part in _CLAUSE_SPLIT.split(_clean(s))
            if (toks := _split_tokens(part))]


def _negated_before(clause: list[str], upto: int) -> bool:
    return any(t in _NEGATORS for t in clause[:upto])


def _run_start(clause: list[str], needle: list[str]) -> int:
    """Where `needle` occurs contiguously in `clause`, or -1."""
    if not needle or len(needle) > len(clause):
        return -1
    for i in range(len(clause) - len(needle) + 1):
        if clause[i:i + len(needle)] == needle:
            return i
    return -1


# ── Item normalization ──────────────────────────────────────────────────────
def _text(d: dict, *keys) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _strlist(v) -> list[str]:
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def item_id(item: dict) -> str:
    """Stable id for an item: its own `id` if qc_fix already stamped one
    (spec §5.9 — fixes.json references ids, never indexes), else sha1 of the
    stem, the same identity qc_fix computes."""
    if isinstance(item, dict):
        given = item.get("id")
        if isinstance(given, str) and given.strip():
            return given.strip()
    stem = _text(item if isinstance(item, dict) else {},
                 "question", "q", "stem", "front", "prompt")
    return hashlib.sha1(stem.encode("utf-8")).hexdigest()[:16]


def _options(raw: dict) -> list[dict]:
    ao = raw.get("answerOptions") or raw.get("options") or raw.get("choices") or []
    if not isinstance(ao, (list, tuple)) or not ao:
        return []
    out = []
    if isinstance(ao[0], dict):
        for i, o in enumerate(ao):
            out.append({
                "index": i,
                "text": _text(o, "text", "label", "option"),
                "correct": bool(o.get("isCorrect") or o.get("correct")),
                "rationale": _text(o, "rationale", "explanation"),
            })
        return out
    # Plain string options with a separate answer field — the shape app.py has
    # carried since the first NotebookLM exports. Kept so old releases render.
    correct = raw.get("correct", raw.get("answer", None))
    for i, t in enumerate(ao):
        txt = str(t)
        is_corr = (isinstance(correct, int) and not isinstance(correct, bool) and i == correct) \
            or (isinstance(correct, str) and norm(correct) == norm(txt))
        out.append({"index": i, "text": txt, "correct": is_corr, "rationale": ""})
    return out


def _infer_type(raw: dict, options: list[dict]) -> str:
    declared = str(raw.get("type") or "").strip().lower()
    if declared in (MULTIPLE_CHOICE, MULTIPLE_SELECT, FILL_IN_THE_BLANK, SHORT_ANSWER):
        return declared
    if isinstance(raw.get("grading"), dict) and raw["grading"].get("modelAnswer"):
        return SHORT_ANSWER
    if raw.get("bestAnswer") or raw.get("acceptableAnswers"):
        return FILL_IN_THE_BLANK
    if options:
        # 107 of the 149 sampled items are untyped; every one has exactly one
        # key. More than one key and it is a select, whatever the file says.
        return MULTIPLE_SELECT if sum(o["correct"] for o in options) > 1 else MULTIPLE_CHOICE
    return UNKNOWN


def normalize_item(item: dict) -> dict:
    """One shape for every quiz item the releases contain.

    Idempotent: normalize_item(normalize_item(x)) is x, so the app can hand a
    normalized item straight back to grade().
    """
    if isinstance(item, dict) and item.get("_normalized"):
        return item
    raw = item if isinstance(item, dict) else {}
    grading = raw.get("grading") if isinstance(raw.get("grading"), dict) else {}
    errors = grading.get("errors") if isinstance(grading.get("errors"), dict) else {}
    options = _options(raw)
    best = _text(raw, "bestAnswer", "best_answer", "answer")
    acceptable = _strlist(raw.get("acceptableAnswers") or raw.get("acceptable_answers"))
    if best and best not in acceptable:
        acceptable = [best] + acceptable
    return {
        "_normalized": True,
        "id": item_id(raw),
        "type": _infer_type(raw, options),
        "question": _text(raw, "question", "q", "stem", "prompt"),
        "options": options,
        "hint": _text(raw, "hint"),
        "rationale": _text(raw, "rationale") or _text(grading, "rationale"),
        "best_answer": best,
        "acceptable_answers": acceptable,
        "model_answer": _text(grading, "modelAnswer", "model_answer"),
        "required_attributes": _strlist(grading.get("requiredAttributes")
                                        or grading.get("required_attributes")),
        "misconceptions": _strlist(errors.get("misconceptions")),
        "bluffs": _strlist(errors.get("bluffs")),
        "images": _strlist(raw.get("imageUrls") or raw.get("images")),
        # Carried, not acted on: no sampled item sets either flag, and inventing
        # an "all of the above" option would invent its grading too.
        "flags": {"all_of_the_above": bool(raw.get("hasAllOfTheAbove")),
                  "none_of_the_above": bool(raw.get("hasNoneOfTheAbove"))},
        "qc": qc_mark(raw),
        "raw": raw,
    }


# ── QC marks ────────────────────────────────────────────────────────────────
def book_display(name) -> str:
    """The book as the owner reads it. Unknown names pass through unchanged —
    better a raw label than a silently wrong one."""
    s = str(name or "").strip()
    if not s:
        return ""
    if s in BOOK_HE:
        return BOOK_HE[s]
    low = s.casefold()
    for pat, key in _BOOK_ALIASES:
        if re.search(pat, low, re.I):
            return BOOK_HE[key]
    return s


def _sources(rec: dict) -> list[dict]:
    """Every source on a qc record, in the order it was written.

    A source_conflict carries two (spec §5.8: "two verified quotes with
    different values → both quotes shown"). qc_fix's record names one `source`;
    the second has no fixed spelling yet, so all three shapes are accepted —
    `source` as a list, a `sources` list, or a `second_source` beside it.
    """
    cands: list = []
    one = rec.get("source")
    if isinstance(one, dict):
        cands.append(one)
    elif isinstance(one, (list, tuple)):
        cands.extend(one)
    if isinstance(rec.get("sources"), (list, tuple)):
        cands.extend(rec["sources"])
    if isinstance(rec.get("second_source"), dict):
        cands.append(rec["second_source"])
    out = []
    for cand in cands:
        if not isinstance(cand, dict):
            continue
        page = cand.get("printed_page", cand.get("page", cand.get("printed")))
        out.append({
            "book": str(cand.get("book") or "").strip(),
            "book_he": book_display(cand.get("book")),
            "page": page,
            "quote": _text(cand, "quote", "source_quote", "text"),
        })
    return out


def qc_mark(item) -> dict | None:
    """The display form of an item's / card's embedded qc record, or None.

    None is the answer for everything the learner must not be shown: no record
    at all (every chapter released before the QC run), a `presentation` fix
    (the answer-order rebalance carries no marker by design, §5.9), a
    `withdrawn` record (qc_revert took the correction back), and anything whose
    kind the app does not know. Fail-closed in the display direction: an
    unrecognised kind shows nothing rather than a half-explained warning.
    """
    rec = item.get("qc") if isinstance(item, dict) else None
    if not isinstance(rec, dict):
        return None
    if str(rec.get("kind") or "").strip().lower() != "content":
        return None
    status = str(rec.get("status") or "active").strip().lower()
    if status != "active":
        return None
    sources = _sources(rec)
    # A content mark ASSERTS a comparison against a book — the headline below
    # says so in as many words — so a record with no book behind it must not
    # print one. Tested 2026-09-26: an impossible-citation flag relabelled
    # `content` renders as "נמצאה סתירה מול ספר הלימוד" with an empty source
    # list, which tells the learner a textbook disagrees with his material when
    # what was actually found is that a page number does not exist. The free
    # checks cannot reach this function today (they carry no `kind` the app
    # knows, which is why they show nothing), and this is what keeps that true
    # if anyone ever routes them here by renaming the kind.
    if not sources:
        return None
    severity = str(rec.get("severity") or "").strip().lower()
    conflict = len(sources) > 1
    return {
        "kind": "content",
        "status": status,
        "severity": severity,
        "severity_he": SEVERITY_HE.get(severity, severity),
        "date": str(rec.get("date") or "").strip(),
        "was": _text(rec, "was", "said"),
        "now": _text(rec, "now", "correction"),
        "unit": _text(rec, "unit"),
        "sources": sources,
        # The options the source itself supports, when qc_fix names them.
        # Empty for every record written so far, and _matches_source then falls
        # back to reading the quote — see its docstring for why that is the
        # weaker of the two.
        "supports": _strlist(rec.get("supports")),
        "conflict": conflict,
        "headline": "המקורות חלוקים זה עם זה" if conflict
                    else "נמצאה סתירה מול ספר הלימוד",
    }


def _matches_source(option_text: str, mark: dict) -> bool:
    """Is this option what the textbook says, on an item whose key is disputed?

    The case this exists for (spec §1): chapter 019 keys atomoxetine onset at
    "8-12 weeks" (StahlKids p.74) while p.69 says "full therapeutic benefits may
    take 2-8 weeks". A learner who picks the distractor that matches the quoted
    page is right, and must not be scored wrong by a key the QC run already
    marked. Two ways to qualify, both requiring real overlap with the quote:

      1. the option's own text occurs inside the quote (≥ 12 normalized chars,
         so "a" or "yes" cannot match), or
      2. the option's numeric values all occur in the quote AND it shares a
         content word with it — the shared word is what stops a bare "5" from
         matching a quote that happens to contain a 5.

    A Hebrew option against an English quote shares neither, so it stays graded
    against the key; it still carries the visible mark.

    NEITHER TEST KNOWS WHAT THE QUOTE SAYS, so both are scoped to one clause of
    it and both refuse a negation. Measured on the reviewed code, which asked
    only whether the text overlapped:

      * "Antidepressant monotherapy is **not** recommended as first-line
        treatment in bipolar depression" contains, word for word, the option it
        rules out, and the learner who picked it was told he was right.
      * "The maximum dose is 100 mg/day; the usual starting dose is 40 mg/day"
        licensed "the maximum dose is 40 mg/day" — the digits of one clause
        with the role word of the other, which for a dose question is the whole
        question.

    So test 1 needs the option's words contiguous inside ONE clause, and test 2
    needs that clause to carry the option's values AND every one of the option's
    own terms the quote uses anywhere: a clause that says "40" while the word
    "maximum" sits in the clause next door is not the book supporting this
    option. A negation marker standing between the start of the clause and the
    first word matched refuses both.

    This is still inference from text, and §5.11's promise is about what the
    source SAYS. `mark["supports"]` is the way out: when qc_fix — which had the
    judge's own verdict in front of it — names the option the source supports,
    that list is the whole answer and none of the above runs.
    """
    if not mark:
        return False
    named = mark.get("supports") or []
    if named:
        opt = norm(option_text)
        return bool(opt) and any(norm(x) == opt for x in named)

    opt_toks = [t for c in _clause_tokens(option_text) for t in c]
    if not opt_toks:
        return False
    opt_vals = _values(opt_toks)
    opt_words = _content_words(opt_toks)
    # The ≥ 12 floor of test 1, unchanged: "a" or "yes" proves nothing.
    whole = len(norm(option_text)) >= 12

    for src in mark.get("sources") or []:
        clauses = _clause_tokens(src.get("quote"))
        quoted = {t for c in clauses for t in c}
        for clause in clauses:
            here = set(clause)
            at = _run_start(clause, opt_toks) if whole else -1
            if at >= 0 and not _negated_before(clause, at):
                return True
            if not opt_vals or not opt_vals <= here:
                continue
            shared = opt_words & quoted
            if not shared or not shared <= here:
                continue
            first = min(clause.index(t) for t in (opt_vals | shared))
            if not _negated_before(clause, first):
                return True
    return False


# ── Grading ─────────────────────────────────────────────────────────────────
def _pick_option(item: dict, response) -> dict | None:
    """Resolve a response to one option — by index (what the app sends) or by
    text (what a test or a URL parameter might)."""
    opts = item["options"]
    if isinstance(response, bool):
        return None
    if isinstance(response, int):
        return opts[response] if 0 <= response < len(opts) else None
    if isinstance(response, str):
        for o in opts:
            if o["text"] == response:
                return o
        n = norm(response)
        for o in opts:
            if n and norm(o["text"]) == n:
                return o
    return None


def _blank(response) -> bool:
    if response is None:
        return True
    if isinstance(response, str):
        return not response.strip()
    if isinstance(response, (list, tuple, set)):
        return len(response) == 0
    return False


def _result(correct, expected, feedback, **extra) -> dict:
    out = {"correct": correct, "expected": expected, "feedback": feedback}
    out.update(extra)
    return out


def grade(item, response) -> dict:
    """Score one response. Always {'correct': bool|None, 'expected', 'feedback'}.

    `correct` is None whenever the app must not claim a verdict: nothing was
    answered, the item is a short_answer (self-graded — spec §5.11 forbids a
    runtime LLM, and a string-compare verdict on a paragraph would be worse than
    none), or the item has no gradable structure at all.
    """
    it = normalize_item(item)
    mark = it.get("qc")
    t = it["type"]

    if t == SHORT_ANSWER:
        return _result(
            None, it["model_answer"],
            "תשובה פתוחה — השווה את מה שכתבת לתשובת המודל ולנקודות הנדרשות.",
            required_attributes=it["required_attributes"],
            misconceptions=it["misconceptions"], bluffs=it["bluffs"],
            rationale=it["rationale"], qc=mark)

    if t == FILL_IN_THE_BLANK:
        expected = it["best_answer"] or (it["acceptable_answers"][0] if it["acceptable_answers"] else "")
        if _blank(response):
            return _result(None, expected, "לא נכתבה תשובה.",
                           acceptable=it["acceptable_answers"], qc=mark)
        given = norm(response)
        hits = [a for a in it["acceptable_answers"] if norm(a) == given]
        if hits:
            return _result(True, expected, it["rationale"] or "נכון.",
                           acceptable=it["acceptable_answers"], qc=mark)
        # Not a match, but worth telling apart from a plain miss: the learner
        # wrote the answer inside a sentence. Never scored correct — shown as a
        # near miss for them to judge, which is the same self-grading rule the
        # short_answer type already runs on.
        near = any(len(norm(a)) >= 4 and (norm(a) in given or given in norm(a))
                   for a in it["acceptable_answers"])
        return _result(False, expected,
                       f"התשובה המצופה: {expected}" if expected else "אין תשובה מוגדרת לשאלה זו.",
                       acceptable=it["acceptable_answers"], near=near,
                       rationale=it["rationale"], qc=mark)

    keys = [o for o in it["options"] if o["correct"]]

    if t == MULTIPLE_SELECT:
        expected = [o["text"] for o in keys]
        if _blank(response):
            return _result(None, expected, "לא נבחרו תשובות.", qc=mark)
        chosen = []
        for r in (response if isinstance(response, (list, tuple, set)) else [response]):
            o = _pick_option(it, r)
            if o is not None:
                chosen.append(o)
        picked = {o["index"] for o in chosen}
        wanted = {o["index"] for o in keys}
        # All-or-nothing (spec §5.11). Partial credit would tell a learner who
        # found one of three keys that they knew the answer.
        ok = bool(wanted) and picked == wanted
        missed = [o["text"] for o in keys if o["index"] not in picked]
        extra = [o["text"] for o in chosen if not o["correct"]]
        if ok:
            fb = "כל התשובות הנכונות סומנו, ולא נבחרה אף תשובה שגויה."
        else:
            parts = []
            if missed:
                parts.append("החסרת: " + " · ".join(missed))
            if extra:
                parts.append("סימנת בטעות: " + " · ".join(extra))
            parts.append("כל התשובות הנכונות: " + " · ".join(expected))
            fb = "\n".join(parts)
        return _result(ok, expected, fb, chosen=[o["text"] for o in chosen],
                       missed=missed, extra=extra,
                       rationales=[(o["text"], o["rationale"]) for o in it["options"]
                                   if o["index"] in picked or o["correct"]],
                       qc=mark)

    if t == MULTIPLE_CHOICE:
        expected = keys[0]["text"] if keys else ""
        if _blank(response):
            return _result(None, expected, "לא נבחרה תשובה.", qc=mark)
        chosen = _pick_option(it, response)
        if chosen is None:
            return _result(None, expected, "לא זוהתה הבחירה.", qc=mark)
        if chosen["correct"]:
            return _result(True, expected, chosen["rationale"] or "נכון.",
                           chosen=chosen["text"], chosen_rationale=chosen["rationale"],
                           expected_rationale=keys[0]["rationale"] if keys else "",
                           disputed=bool(mark), qc=mark)
        if _matches_source(chosen["text"], mark):
            # The key is disputed and this choice is what the quoted page says.
            # Scored correct, and the mark next to the item shows both.
            return _result(
                True, expected,
                "הבחירה שלך תואמת את מה שכתוב בספר, והמפתח של השאלה סומן כשגוי. "
                "ראה את הציטוט והעמוד בהערה שליד השאלה.",
                chosen=chosen["text"], chosen_rationale=chosen["rationale"],
                expected_rationale=keys[0]["rationale"] if keys else "",
                source_backed=True, disputed=True, qc=mark)
        return _result(False, expected,
                       f"התשובה הנכונה: {expected}" if expected else "לא סומנה תשובה נכונה בקובץ.",
                       chosen=chosen["text"], chosen_rationale=chosen["rationale"],
                       expected_rationale=keys[0]["rationale"] if keys else "",
                       disputed=bool(mark), qc=mark)

    return _result(None, "", "לא ניתן לבדוק את השאלה הזו (מבנה לא מוכר).", qc=mark)


# ── Flashcards ──────────────────────────────────────────────────────────────
def _as_date(value) -> date | None:
    """First 10 characters of an ISO timestamp, as a date. The two sides of the
    comparison are written by different programs: qc_fix stamps "2026-09-20",
    sr_engine stamps "2026-09-20T12:00:00"."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(s).date()
        except ValueError:
            return None


def card_due_override(card, state) -> bool:
    """Does a corrected card jump the SR schedule?

    A card the learner has already memorised is scheduled months out. If its
    back was wrong and QC fixed it, the wrong version is what they hold until
    that date — so a content correction dated after the last review pulls the
    card back into today's queue, showing old → new (spec §5.11).

    Same-day is treated as due: qc_fix stamps a date, sr_engine stamps a
    timestamp, so a card reviewed at 09:00 and corrected at 14:00 the same day
    compares equal. One extra card costs a few seconds; the other direction
    leaves a known error in memory until the interval elapses.

    This never writes anything. flashcard_progress.json is PUT whole by the app
    on every grade (last writer wins), so QC state must not live in it — the
    override is recomputed from the release's own card on each load, and it stops
    firing by itself once the card is reviewed and last_seen moves past the date.
    """
    mark = qc_mark(card)
    if not mark:
        return False
    if not isinstance(state, dict) or not state:
        return True
    last = _as_date(state.get("last_seen"))
    if last is None:
        return True
    when = _as_date(mark.get("date"))
    if when is None:
        # A correction with no readable date is still a correction: show it.
        return True
    return when >= last


# ── qc.json (release asset, spec §5.10) ─────────────────────────────────────
_NOT_CHECKED = {"not_checked", "unchecked", "skipped", "not_judged", "unjudged", "לא נבדק"}

STATUS_HE = {
    "approved": "אושר",
    "fixed": "תוקן",
    "needs_owner": "ממתין להחלטה שלך",
    "needs_regeneration": "דורש יצירה מחדש",
    "unjudged": "לא נשפט",
    "withdrawn": "בוטל",
    "active": "פעיל",
}


def asset_display(name) -> str:
    s = str(name or "").strip()
    if s in ASSET_HE:
        return ASSET_HE[s]
    for stem, he in ASSET_HE.items():
        if s.startswith(stem):
            return f"{he} ({s})"
    return s


def erratum_view(rec) -> dict | None:
    """One row of the תיקונים page, from one erratum in qc.json.

    Returns None for a withdrawn or non-content entry, exactly like qc_mark —
    the errata page and the in-item mark must never disagree about what the
    learner is told.
    """
    if not isinstance(rec, dict):
        return None
    view = qc_mark({"qc": {**rec, "kind": rec.get("kind", "content")}})
    if view is None:
        return None
    view["asset"] = str(rec.get("asset") or "").strip()
    view["asset_he"] = asset_display(rec.get("asset"))
    view["unit"] = view["unit"] or _text(rec, "unit", "id")
    view["id"] = str(rec.get("id") or "").strip()
    return view


def parse_qc_json(data) -> dict | None:
    """Read a release's `qc.json`. None when there is none — the normal case for
    every chapter published before the QC run, and the reason nothing in the app
    may treat its absence as an error.

    Key names follow spec §5.10 (status, per-asset sha256, errata). Aliases are
    accepted for the few fields the spec names in prose rather than in a schema,
    and an unreadable file is the same as a missing one: the app shows the
    chapter exactly as it did before QC existed.
    """
    if not isinstance(data, dict):
        return None
    errata = []
    for rec in (data.get("errata") or data.get("fixes") or []):
        view = erratum_view(rec)
        if view:
            errata.append(view)
    assets = []
    raw_assets = data.get("assets") or data.get("per_asset") or {}
    if isinstance(raw_assets, dict):
        pairs = list(raw_assets.items())
    elif isinstance(raw_assets, (list, tuple)):
        pairs = [(a.get("name"), a) for a in raw_assets if isinstance(a, dict)]
    else:
        pairs = []
    for name, val in pairs:
        if isinstance(val, dict):
            status = str(val.get("status") or val.get("qc") or "").strip().lower()
            sha = str(val.get("sha256") or "").strip()
        else:
            status, sha = str(val or "").strip().lower(), ""
        checked = None if not status else status not in _NOT_CHECKED
        assets.append({"name": str(name or ""), "name_he": asset_display(name),
                       "status": status, "checked": checked, "sha256": sha,
                       "n_errata": sum(1 for e in errata if e["asset"] == name)})
    status = str(data.get("status") or "").strip().lower()
    # The checks that did not run. qc_run writes them when a run skipped the
    # book-backed lint (--no-books), and the badge has to say so: a chapter is
    # only "checked against the textbooks" if a textbook was opened. Entries are
    # check names; keep them as written so the badge can list them.
    skipped = [str(s.get("check") if isinstance(s, dict) else s).strip()
               for s in (data.get("skipped") or [])]
    return {
        "status": status,
        "status_he": STATUS_HE.get(status, status),
        "skipped": [s for s in skipped if s],
        "date": str(data.get("date") or data.get("judged_at") or "").strip()[:10],
        "tag": str(data.get("tag") or "").strip(),
        "chapter": data.get("chapter", data.get("index")),
        "assets": assets,
        "errata": errata,
        "n_errata": len(errata),
    }


def badge_text(qc) -> str:
    """The one-line chapter badge of spec §5.11. Empty string when the chapter
    has no qc.json, which is how the app knows to draw nothing."""
    if not qc:
        return ""
    when = qc.get("date") or "?"
    n = qc.get("n_errata", 0)
    # "Checked against the textbooks" is a claim about what was READ, and a run
    # started with --no-books never opened one: the citation and locked-value
    # checks are skipped and qc.json says so. Saying it anyway would be the
    # failure this whole mechanism exists to prevent, one line further down the
    # chain — so the badge names the partial check instead (spec §5.11, §2.3).
    if qc.get("skipped"):
        head = (f"נבדק חלקית ב-{when}, {n} תיקונים · "
                f"לא נבדק מול הספרים: {', '.join(qc['skipped'])}")
    else:
        head = f"נבדק מול ספרי הלימוד ב-{when}, {n} תיקונים"
    if qc.get("status") in ("needs_owner", "needs_regeneration", "unjudged"):
        head += f" · {qc.get('status_he') or qc['status']}"
    return head


def asset_lines(qc) -> list[str]:
    """Per-asset lines under the badge ("וידאו: לא נבדק").

    Video is out of scope in v1 (spec §3) and the badge has to say so: a chapter
    marked "checked" with an unchecked video would be read as a video that
    passed.
    """
    if not qc:
        return []
    out = []
    for a in qc.get("assets", []):
        if a["checked"] is False:
            out.append(f"{a['name_he']}: לא נבדק")
        elif a["checked"] is None:
            out.append(f"{a['name_he']}: {a['status'] or 'לא ידוע'}")
        elif a["n_errata"]:
            out.append(f"{a['name_he']}: {a['n_errata']} תיקונים")
        else:
            out.append(f"{a['name_he']}: נבדק, ללא תיקונים")
    return out


def errata_for_asset(qc, asset_name: str) -> list[dict]:
    """The errata attached to one asset — what the legend card and the guide
    show. An erratum with no asset field belongs to no asset in particular and
    is listed only on the תיקונים page."""
    if not qc:
        return []
    return [e for e in qc.get("errata", []) if e["asset"] == asset_name]
