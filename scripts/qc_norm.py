#!/usr/bin/env python3
"""Text normalization and quote verification — the part of the judge that is code.

A judge's verdict is only worth keeping if code can re-find, in the material
and in the book, the two quotes the verdict rests on. Everything here exists to
make that comparison survive the difference between what a model types and what
a PDF extractor produced, WITHOUT letting it swallow the differences that are
the whole point of the review.

Measured 2026-09-18 (spec review, `spec_review/norm.py`), on StahlKids printed
p.69, "full therapeutic benefits may take 2-8 weeks":

  - The naive normalization (whitespace + bullets + dashes + quotes) failed on
    every real reply. The model types `Doesn't` for `Doesn’t`, `2-8` for `2–8`,
    drops the bullet, and re-wraps the line; the extractor emits `SSRI- related`
    and `meta- analysis` from line-break hyphenation, a soft hyphen (U+00AD)
    inside `bene­fits`, and the `ﬁ` ligature. So the rule here is the opposite
    of a careful transliteration: delete every non-alphanumeric character on
    both sides and compare what is left.
  - That deletion is dangerous in exactly one place. `2–8 weeks` and `28 weeks`
    both collapse to `28weeks`, and "2-8 vs 8-12 weeks" (StahlKids p.69 vs p.74)
    is a real finding in the backlog. So a run of non-alphanumerics BETWEEN TWO
    DIGITS survives as one boundary character; `2|8weeks` can never be read as
    `28weeks`, and the near-miss `2-12` stays distinct too.

The rest of the input this has to absorb, from the same review's hygiene scan of
the released chapters: escaped LaTeX in 8 of 14 guides (`$1.4\\ mg/kg/day$`,
`$25\\%$`, `$20-40\\text{ mg}$`), markdown bold around the numbers themselves
(`**12–15 חודשים**`), Hebrew geresh/gershayim/maqaf, and bidi isolates left by
the mixed Hebrew/English text.

`locked_values` is the second half. The judge is told to dispute a *value*, and
§5.8 drops any finding whose source quote holds the same value as the material.
That test cannot be a string comparison: the material says `1.4 מ"ג/ק"ג/יום`
and StahlKids says `1.4 mg/kg/day`. So values are parsed to (amount, canonical
unit) with the Hebrew unit words mapped onto the English ones, and compared
inside a bucket of (kind, unit) — never across units, because a false
`contradicted` is the failure this whole mechanism exists to avoid.

    python scripts/qc_norm.py --norm "..."          normalized form
    python scripts/qc_norm.py --values "..."        locked values as JSON
    python scripts/qc_norm.py --find "quote" --book StahlKids --page 69
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The boundary that keeps "2-8" from reading as "28". Every literal "|" in the
# input is stripped in _scrub, so a "|" in a normalized string is always this
# sentinel and never came from the text.
BOUNDARY = "|"

# An echo or a quote shorter than this proves nothing: 30 normalized characters
# is about five words, and the judge is allowed to skip the floor only for a
# unit that is itself shorter (whole_unit=True), which §5.8 permits.
MIN_QUOTE_CHARS = 30
# A quote split by an ellipsis is only as strong as its weakest piece. A piece
# of three characters matches somewhere on every page, so it is not evidence.
MIN_PIECE_CHARS = 10
# §5.2: in-order token match >= 0.9, for quotes broken up by two-column
# extraction. Below that it is a paraphrase, and a paraphrase is not a quote.
FUZZY_MIN_SCORE = 0.9
# ...and the matched tokens must still be packed together. A two-column page
# interleaves the columns line by line, which at worst doubles the span a quote
# occupies; without this, 0.9 of a long quote can be assembled from tokens at
# opposite ends of the page.
FUZZY_MIN_DENSITY = 0.5

# NFKC leaves these in place and they are invisible, so a quote typed by a model
# never contains them while the extracted page does: soft hyphen, the two bidi
# marks, and the four bidi isolates that the mixed Hebrew/English material is
# full of.
_INVISIBLE = frozenset("­‎‏⁦⁧⁨⁩")

_STANDALONE_NUMBER_LINE = re.compile(r"^[ \t]*\d{1,4}[ \t]*$", re.M)


# ---------------------------------------------------------------------------
# phase 1 — scrub: NFKC, invisibles, LaTeX, markdown. Spaces, case and
# punctuation survive, because locked_values needs them to find unit words.

def _scrub_units(s: str) -> list[tuple[str, int, int]]:
    """(char, raw start, raw end) after NFKC, invisibles, LaTeX and markdown.

    NFKC is applied one combining cluster at a time, not to the whole string:
    that is what lets every produced character keep a raw index (the ligature
    `ﬁ` becomes two characters that both point at the one raw character) while
    still composing `e`+U+0301 into `é`. Canonical composition only ever joins
    a starter with the non-starters that follow it, so cluster-wise NFKC equals
    whole-string NFKC for everything in this material.
    """
    s = s or ""
    units: list[tuple[str, int, int]] = []
    i, n = 0, len(s)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(s[j]):
            j += 1
        for ch in unicodedata.normalize("NFKC", s[i:j]):
            if ch not in _INVISIBLE:
                units.append((ch, i, j))
        i = j

    out: list[tuple[str, int, int]] = []
    k, m = 0, len(units)
    at_line_start = True
    while k < m:
        ch = units[k][0]
        # `$` opens and closes the escaped math the guides are full of; braces
        # only ever come from LaTeX here, so `\text{ mg}` leaves ` mg`.
        if ch in "${}`|":
            k += 1
            continue
        if ch == "\\":
            j = k + 1
            if j < m and units[j][0].isalpha():
                # a command name (\alpha, \mathrm, \text) goes whole...
                while j < m and units[j][0].isalpha():
                    j += 1
                k = j
            else:
                # ...while `\%`, `\(`, `\)` and the `\ ` of `$1.4\ mg$` lose
                # only the backslash and keep the character they escaped.
                k += 1
            continue
        if ch in "*_" and k + 1 < m and units[k + 1][0] == ch:
            k += 2                      # markdown ** and __
            continue
        if ch == "#" and at_line_start:
            k += 1                      # heading marker; stays "line start"
            continue
        if ch == "\n":
            at_line_start = True
        elif not (at_line_start and ch in " \t"):
            at_line_start = False
        out.append(units[k])
        k += 1
    return out


def _scrub(s: str) -> tuple[str, list[int], list[int]]:
    units = _scrub_units(s)
    return ("".join(u[0] for u in units),
            [u[1] for u in units], [u[2] for u in units])


# ---------------------------------------------------------------------------
# phase 2 — squash: casefold, digit boundaries, delete everything else.

def _squash(units: list[tuple[str, int, int]]) -> tuple[str, list[int], list[int]]:
    folded: list[tuple[str, int, int]] = []
    for ch, a, b in units:
        # per character, so the two "s" of casefolded "ß" both point at it
        for c in ch.casefold():
            folded.append((c, a, b))

    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i, n = 0, len(folded)
    while i < n:
        c = folded[i][0]
        if c.isalnum():                 # Hebrew letters are alphanumeric
            chars.append(c)
            starts.append(folded[i][1])
            ends.append(folded[i][2])
            i += 1
            continue
        j = i
        while j < n and not folded[j][0].isalnum():
            j += 1
        if chars and chars[-1].isdigit() and j < n and folded[j][0].isdigit():
            chars.append(BOUNDARY)
            starts.append(folded[i][1])
            ends.append(folded[j - 1][2])
        i = j
    return "".join(chars), starts, ends


def _norm_full(s: str) -> tuple[str, list[int], list[int]]:
    return _squash(_scrub_units(s))


def norm(s: str) -> str:
    """The comparison form: see the module docstring for every rule."""
    return _norm_full(s)[0]


def norm_map(s: str) -> tuple[str, list[int]]:
    """(normalized, offsets) — offsets[i] is the raw index of normalized char i.

    qc_fix locates a guide span through this map and edits the raw text, so the
    offsets must point into the string as it was passed in, before NFKC.
    """
    text, starts, _ = _norm_full(s)
    return text, starts


# ---------------------------------------------------------------------------
# quote verification

_ELLIPSIS = re.compile(r"\s*(?:…|\.\s?\.\s?\.)\s*")


def _tokens(s: str) -> list[tuple[str, int, int]]:
    """Whitespace-delimited tokens, each normalized, with its raw span.

    Token-wise rather than character-wise because the fuzzy path has to know
    where the words end; the digit-boundary rule still applies inside a token,
    so the token "2–8" stays distinct from the token "28".
    """
    units = _scrub_units(s)
    out: list[tuple[str, int, int]] = []
    i, n = 0, len(units)
    while i < n:
        if units[i][0].isspace():
            i += 1
            continue
        j = i
        while j < n and not units[j][0].isspace():
            j += 1
        t, ts, te = _squash(units[i:j])
        if t:
            out.append((t, ts[0], te[-1]))
        i = j
    return out


def find_quote(quote: str, text: str, *, min_len: int = MIN_QUOTE_CHARS,
               whole_unit: bool = False) -> dict:
    """Locate `quote` in `text`.

    Returns {'match': 'exact'|'fuzzy'|None, 'span': (a, b) raw indexes into
    `text` or None, 'score': float, 'reason': str}. `reason` is an addition to
    the spec's three keys: the verdict log has to say WHY a quote was rejected,
    because "too short" and "not on the page" mean different things for the
    chapter's status (§5.8 verification 2 vs 3).

    An ellipsis splits the quote and the pieces must match in order — a model
    that elides the middle of a sentence is still quoting, a model that
    reorders it is not.

    `text` is meant to be one unit or one page window (source_window), never a
    whole book: in-order token matching across 1.5M characters finds every
    quote's tokens somewhere, and only the density guard below then rejects it.
    """
    pieces_raw = [p for p in _ELLIPSIS.split(quote or "") if p.strip()]
    pieces = [n for n in (norm(p) for p in pieces_raw) if n]
    if not pieces:
        return {"match": None, "span": None, "score": 0.0, "reason": "empty"}
    total = sum(len(p) for p in pieces)
    if not whole_unit:
        if total < min_len:
            return {"match": None, "span": None, "score": 0.0, "reason": "too_short"}
        if len(pieces) > 1 and min(len(p) for p in pieces) < MIN_PIECE_CHARS:
            return {"match": None, "span": None, "score": 0.0,
                    "reason": "piece_too_short"}

    ntext, tstarts, tends = _norm_full(text)
    # First occurrence of each piece, in order, without backtracking: a piece
    # that also appears earlier than a previous piece's match costs an exact
    # match here, and the fuzzy path below picks it up. Failing toward "not
    # verified" is the direction §2.3 asks for.
    pos = 0
    hits: list[tuple[int, int]] = []
    for p in pieces:
        k = ntext.find(p, pos)
        if k < 0:
            hits = []
            break
        hits.append((k, k + len(p)))
        pos = k + len(p)
    if hits:
        return {"match": "exact",
                "span": (tstarts[hits[0][0]], tends[hits[-1][1] - 1]),
                "score": 1.0, "reason": ""}

    return _fuzzy(pieces_raw, text)


def _fuzzy(pieces_raw: list[str], text: str) -> dict:
    """In-order token match, for a quote a two-column extraction broke apart."""
    qt = [t for t, _, _ in _tokens(" ".join(pieces_raw))]
    tt = _tokens(text)
    if not qt or not tt:
        return {"match": None, "span": None, "score": 0.0, "reason": "no_tokens"}
    sm = SequenceMatcher(None, qt, [t for t, _, _ in tt], autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    matched = sum(b.size for b in blocks)
    score = matched / len(qt)
    if not blocks:
        return {"match": None, "span": None, "score": 0.0, "reason": "not_found"}
    first = blocks[0].b
    last = blocks[-1].b + blocks[-1].size - 1
    density = matched / (last - first + 1)
    if score >= FUZZY_MIN_SCORE and density >= FUZZY_MIN_DENSITY:
        return {"match": "fuzzy", "span": (tt[first][1], tt[last][2]),
                "score": round(score, 4), "reason": ""}
    reason = "not_found" if score < FUZZY_MIN_SCORE else "scattered"
    return {"match": None, "span": None, "score": round(score, 4), "reason": reason}


# ---------------------------------------------------------------------------
def source_window(book_key: str, printed: int, radius: int = 1) -> str:
    """Printed pages `printed-radius .. printed+radius`, page numbers stripped.

    A source quote is matched against a window, never a single page: the
    2026-09-18 review found the Dulcan p.330/331 sentence ("Those receiving CBT
    in conjunction with a tablet...") split across the boundary, so a verbatim
    quote of it is on neither page alone. The standalone number lines go because
    they are the printed folio, not text a book ever "says" — leaving them in
    lets a quote pick up a page number as if it were content.
    """
    import qc_books                                    # needs PyMuPDF; keep lazy

    key = book_key
    if key not in qc_books.SPECS:
        resolved = qc_books.normalize_label(book_key)
        if not resolved or resolved not in qc_books.SPECS:
            raise ValueError(f"no printed pages for book {book_key!r} "
                             f"(the Synopsis is read via synopsis_slices())")
        key = resolved
    b = qc_books.book(key)
    parts: list[str] = []
    for p in range(printed - radius, printed + radius + 1):
        text = b.page_text(p)
        if text is None:
            continue
        # A reflowed book folds unnumbered printed pages into the previous
        # one's text (qc_books._build_inline), so neighbours can be the same
        # object; concatenating it twice would only inflate the window.
        if parts and text is parts[-1][1]:
            continue
        parts.append((p, text))
    return "\n".join(_STANDALONE_NUMBER_LINE.sub("", t).strip("\n")
                     for _, t in (parts or []))


# ---------------------------------------------------------------------------
# locked values

_LEAD_PUNCT = "([{“”‘’«»\"'"
_TRAIL_PUNCT = ".,;:!?)]}“”‘’«»\"'"
# Same, minus the comma: "p. 289, 902" chains the second page onto the first
# through that comma, so it is the one mark that must survive the strip.
_PAGE_LIST_STRIP = _TRAIL_PUNCT.replace(",", "")
_QUOTEISH = re.compile(r"[׳״'\"‘’“”]")
# "ל-6 חודשים", "ב-2 מ\"ג", "מ-12": a one or two letter Hebrew prefix glued to a
# number with a maqaf or hyphen. Without this every prefixed Hebrew number looks
# like an identifier (below) and no Hebrew duration would ever be read.
_HEB_PREFIX = re.compile(r"^[בהוכלמש]{1,2}"
                         r"[׳״'\"]?[-–—־]")
_HEB_PREFIX_LETTERS = "בהוכלמש"

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_NUMEXPR = re.compile(rf"^(?P<lo>{_NUM})(?:\s*[-–—־]\s*(?P<hi>{_NUM}))?")
_RANGE_WORDS = {"to", "through", "עד", "-", "–", "—"}

# THE `between` IDIOM. Hebrew writes a range as `בין <lo> ל-<hi>`, where the
# separator is GLUED to the high number, and English books write `between <lo>
# and <hi>`, where the separator is a word that means a range only here.
# Neither reached _NUMEXPR or _RANGE_WORDS, so both came back as two values:
# 019 C5 says `בין 8 ל-12 שבועות` and was read as the number 8 plus the
# duration 12 weeks, which matches nothing on its own StahlKids p.74
# ("improve for 8-12 weeks") — the card disputed its own source. The books say
# `between X and Y` in 44 sentences, so the same gap sat on the SOURCE side of
# every comparison too.
_BETWEEN_WORDS = frozenset({"בין", "between"})
# The introducer may stand a word or two back: `בין הגילאים 10 ל-12 שנים`,
# `בין גיל 3 ל-12 שנים`. Three tokens covers every form in the material.
_BETWEEN_LOOKBACK = 3
# Separators that mean a range ONLY after the introducer. Bare, each is
# something else: `1 ל-54 ילדים` is a ratio, `אחת ל-4 שבועות` is a frequency,
# and `8 and 12` with no `between` is a list. This is why the introducer is
# required rather than simply adding `ל` and `and` to _RANGE_WORDS.
_BETWEEN_SEPS = frozenset({"and", "ל"})
# The glued form, `ל-12`, `ל12`, `ל-30%`.
_HEB_TO = re.compile(rf"^ל[׳״'\"]?[-–—־]?\s*(?P<hi>{_NUM})\s*(?P<pct>%)?$")
# `40%-60%`: the percent sign sits between the low number and the dash, so
# _NUMEXPR stops at the low end and the high end is dropped on the floor.
_PCT_RANGE_TAIL = re.compile(rf"^%\s*[-–—־]\s*(?P<hi>{_NUM})\s*%?$")
# A clause ends the introducer's reach: a `בין` from the sentence before must
# not license a range here.
_CLAUSE_END = ".;:!?"

# Unit words, Hebrew mapped onto English so `1.4 מ"ג/ק"ג/יום` and
# `1.4 mg/kg/day` compare equal — the judge quotes the book in English and the
# material is in Hebrew, and §5.8 drops a finding only when the two values match.
_PART = {
    "mg": "mg", "milligram": "mg", "milligrams": "mg", "מג": "mg",
    "mcg": "mcg", "μg": "mcg", "ug": "mcg",
    "g": "g", "gram": "g", "grams": "g",
    "kg": "kg", "קג": "kg",
    "ml": "ml", "l": "l", "dl": "dl",
    "meq": "meq", "mmol": "mmol", "mol": "mol", "ng": "ng",
    "mmhg": "mmhg", "bpm": "bpm",
    "day": "day", "days": "day", "d": "day",
    "יום": "day", "ימים": "day",
    "week": "week", "weeks": "week", "wk": "week", "wks": "week",
    "שבוע": "week", "שבועות": "week",
    "month": "month", "months": "month", "mo": "month", "mos": "month",
    "חודש": "month", "חודשים": "month",
    "חדשים": "month",
    "year": "year", "years": "year", "yr": "year", "yrs": "year",
    "שנה": "year", "שנים": "year",
    "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour", "h": "hour",
    "שעה": "hour", "שעות": "hour",
    "min": "minute", "minute": "minute", "minutes": "minute",
    "דקה": "minute", "דקות": "minute",
}
_DURATION_PLURAL = {"day": "days", "week": "weeks", "month": "months",
                    "year": "years", "hour": "hours", "minute": "minutes"}
_PHYSICAL = frozenset({"mg", "mcg", "g", "kg", "ml", "l", "dl", "meq",
                       "mmol", "mol", "ng", "mmhg", "bpm"})

# A number in front of one of these is a count of criteria/items, which is the
# kind the DSM p.135 catatonia finding turns on ("three (or more)" of 12).
_COUNT_NOUNS = frozenset({
    "symptoms", "symptom", "criteria", "criterion", "items", "item",
    "features", "feature", "domains", "domain", "arms", "arm", "studies",
    "trials", "sessions", "episodes", "times",
    "סימפטומים", "סימפטום",
    "תסמינים", "תסמין",
    "קריטריונים", "קריטריון",
    "מאפיינים", "פריטים",
    "פעמים", "אפיזודות",
    "מחקרים", "תחומים",
})
# Spelled-out counts, because that is how the DSM states its thresholds.
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "אחד": 1, "אחת": 1,
    "שניים": 2, "שתיים": 2,
    "שני": 2, "שתי": 2,
    "שלושה": 3, "שלוש": 3,
    "ארבעה": 4, "ארבע": 4,
    "חמישה": 5, "חמש": 5,
    "שישה": 6, "שש": 6,
    "שבעה": 7, "שבע": 7,
    "שמונה": 8,
    "תשעה": 9, "תשע": 9,
    "עשרה": 10, "עשר": 10,
}
_OR_MORE = [("or", "more"), ("או", "יותר")]

# A number behind one of these is a page or a chapter, never a locked value.
# `(StahlNeuro p. 429)` in the 042 quiz is the reason the quota detector may not
# look for "429" in text either (§4); the same number must not become a finding.
_PAGE_MARKERS = frozenset({"p", "pp", "page", "pages", "ch", "chap", "chapter",
                           "fig", "figure", "table", "sec", "section",
                           "עמ", "עמוד",
                           "עמודים", "פרק",
                           "טבלה", "איור"})
_PAGE_GLUED = re.compile(
    r"^(?:p|pp|page|pages|ch|chap|chapter|fig|figure|table|sec|section|"
    r"עמ|פרק)[.\s]*\d", re.I)
# Four digits in this window with no unit is a publication year: "DSM-5-TR
# (2022)", "DSM-5 בשנת 2013". Sample sizes in this material are outside it.
_YEAR_LO, _YEAR_HI = 1900, 2099


def _number(raw: str):
    v = float(raw.replace(",", ""))
    return int(v) if v.is_integer() else v


def _heb_variants(word: str):
    """The word itself, then with one and two Hebrew prefix letters removed."""
    yield word
    for k in (1, 2):
        if len(word) > k and all(c in _HEB_PREFIX_LETTERS for c in word[:k]):
            yield word[k:]


def _clean_word(tok: str) -> str:
    return _QUOTEISH.sub("", tok.strip(_LEAD_PUNCT).strip(_TRAIL_PUNCT)).casefold()


def _canon_unit(tok: str):
    """('dose'|'duration', canonical unit) for a unit token, else None."""
    t = _clean_word(tok)
    # "a 50-week open-label trial": the hyphen of a compound adjective, not a
    # range — the range was already consumed by _NUMEXPR when there was one.
    t = t.lstrip("-–—־")
    if not t:
        return None
    parts = [p.strip(".") for p in re.split(r"[/·]", t) if p.strip(".")]
    if not parts:
        return None
    canon = []
    for p in parts:
        base = next((_PART[v] for v in _heb_variants(p) if v in _PART), None)
        if base is None:
            return None
        # A lone "d" or "h" is far more often an initial than a unit; inside a
        # compound ("mg/kg/d", "mEq/L") there is no such ambiguity.
        if len(parts) == 1 and len(p) == 1 and p != "g":
            return None
        canon.append(base)
    if len(canon) == 1:
        base = canon[0]
        if base in _DURATION_PLURAL:
            return ("duration", _DURATION_PLURAL[base])
        if base in _PHYSICAL:
            return ("dose", base)
        return None
    if any(b in _PHYSICAL for b in canon):
        return ("dose", "/".join(canon))
    return None


def _is_count_noun(tok: str) -> bool:
    w = _clean_word(tok)
    return any(v in _COUNT_NOUNS for v in _heb_variants(w))


# The "per" that is a word instead of a slash. `_canon_unit` already folds
# `mg/day` out of one token; these are the two ways the SAME dose is written
# across tokens in the released material, and `40 מ"ג ליום` has to reach
# (40, 'mg/day') or it lands in a different `_bucket` from the book's
# `40 mg/day` and `values_dispute` can neither drop it (§5.8) nor dispute it.
_PER_WORDS = frozenset({"per", "/"})
# Only ל and ב. The other prefix letters do not mean "per" (מ־ is "from",
# ה־ is "the"), and a BARE duration word is not a "per" either: 036's
# "$25\ mg$ יום כן יום לא" is every other day, not 25 mg/day.
_PER_PREFIX_LETTERS = "לב"


def _per_base(tok: str):
    """The canonical unit of a bare unit word, spelled as the slash form spells
    it: 'day' for day/days/יום, never the plural `_DURATION_PLURAL` uses, so a
    folded unit reads `mg/day` exactly like `_canon_unit("mg/day")`."""
    w = _clean_word(tok).lstrip("-–—־").strip(".")
    # The lone-letter rule of _canon_unit: "d" is an initial far more often
    # than a unit, and there is no compound here to disambiguate it.
    return _PART.get(w) if len(w) > 1 else None


def _per_unit(tok: str):
    """The unit behind a ל/ב-prefixed Hebrew unit word — 'day' for ליום/ביום,
    'kg' for לק"ג. None for anything that is not one, so only an explicit
    preposition ever folds a duration into a dose."""
    w = _clean_word(tok).lstrip("-–—־")
    if len(w) < 2 or w[0] not in _PER_PREFIX_LETTERS:
        return None
    return _per_base(w[1:])


_LIST_MARKER = re.compile(r"\d{1,2}[.)]")
_BULLETS = " \t*-•●▪◦>"


def _at_line_start(text: str, pos: int) -> bool:
    """True when only a bullet or blanks stand between the line break and pos."""
    nl = text.rfind("\n", 0, pos)
    return text[nl + 1:pos].strip(_BULLETS) == ""


def _tok_end(tok: str, start: int, end: int) -> int:
    """Where a token ends once its trailing punctuation is dropped, so that the
    `raw` of "6 חודשים," is "6 חודשים" and not the comma as well."""
    stripped = tok.rstrip(_TRAIL_PUNCT)
    return start + len(stripped) if stripped else end


def _bare(tok: str) -> str:
    """A token without the brackets and stops around it: "(ל-12)," -> "ל-12"."""
    return tok.lstrip(_LEAD_PUNCT).rstrip(_TRAIL_PUNCT)


def _between_before(toks: list, i: int) -> bool:
    """Does a `בין` / `between` introduce the number at token `i`?

    The scan stops at another number and at the end of a clause, so the `בין`
    of the previous sentence cannot license a range here, and neither can one
    that already has its own pair of numbers.
    """
    for k in range(i - 1, max(-1, i - 1 - _BETWEEN_LOOKBACK), -1):
        raw = toks[k][0]
        word = _clean_word(raw)
        # Only the ו of "ובין" is stripped. _heb_variants would also turn
        # "הבין" and "מבין" into "בין", which are different words entirely.
        if word in _BETWEEN_WORDS or (word[:1] == "ו" and word[1:] in _BETWEEN_WORDS):
            return True
        if any(c.isdigit() for c in word):
            return False
        if raw and raw[-1] in _CLAUSE_END:
            return False
    return False


def _range_partner(toks: list, i: int, *, pct: bool, between: bool):
    """The high end of a range spelled across tokens, or None.

    Returns (the high end's digits, the index of the last token it consumed).
    The DIGITS rather than the number, because `_is_year` has to know whether
    they were four of them before the span is dropped as a publication year.

    Three shapes, all measured in the released material and in the books:

        "2 עד 8", "2 to 8", "10% עד 20%"   a separator word, a number each side
        "בין 8 ל-12 שבועות"                 the separator glued to the high end
        "between 25% and 30%"              a separator that is a range only
                                           after the introducer

    "up to 8 weeks" is a bound on one value and not a range, so a separator
    only counts when a number stands on both sides of it; and a `%` on the
    high end only counts when the low end carries one too, so "10 עד 20%" is
    left exactly as it was rather than guessed at.
    """
    if between and i + 1 < len(toks):
        m = _HEB_TO.match(_bare(toks[i + 1][0]))
        if m and (not m.group("pct") or pct):
            return m.group("hi"), i + 1
    if i + 2 < len(toks):
        sep = _clean_word(toks[i + 1][0])
        if sep in _RANGE_WORDS or (between and sep in _BETWEEN_SEPS):
            nxt = _bare(toks[i + 2][0])
            hi_pct = nxt.endswith("%")
            if hi_pct:
                nxt = nxt[:-1].rstrip()
            m2 = _NUMEXPR.fullmatch(nxt)
            if m2 and not m2.group("hi") and (not hi_pct or pct):
                return m2.group("lo"), i + 2
    return None


def _is_year(raw: str | None, value) -> bool:
    """Four digits in the publication-year window, e.g. "DSM-5-TR (2022)".

    Sample sizes in this material are outside the window, and a year is never
    something anyone claimed — so it is not a value a finding may dispute.
    Both ends of "between 2007 and 2011" have to pass before the span is
    dropped: "between 30% and 50%" is a claim, and so is "1990 to 2000 mg".
    """
    return bool(raw) and len(raw) == 4 and _YEAR_LO <= value <= _YEAR_HI


def locked_values(s: str) -> list[dict]:
    """Every value a finding is allowed to dispute: [{kind, raw, value}].

    kind is number | range | percent | dose | duration | count. `value` is the
    amount for number/percent, (lo, hi) for a range, (amount, unit) for dose and
    duration — where amount may itself be (lo, hi), so "2-8 weeks" stays one
    value and cannot be read as either endpoint alone. `raw` is the slice of the
    string as it was passed in, LaTeX and all.

    A range is written six ways here and all six are one value: `2-8`, `2–8`,
    `2 to 8`, `2 עד 8`, the Hebrew `בין 2 ל-8` whose separator is glued to the
    high end, and the English `between 2 and 8`. The last two need their
    introducer — `1 ל-54 ילדים` is a ratio and `אחת ל-4 שבועות` is a frequency.

    What is deliberately NOT a value, all of it measured in released chapters:
    page and chapter numbers (`StahlNeuro p. 289, 902` — including the second
    one, which carries no `p.`), publication years, and any number fused to
    letters (`5-HT2A`, `CYP2D6`, `HLA-B*1502`, `t1/2`). Each of those would
    otherwise give the judge a value to "dispute" that no one ever claimed.

    The one page form this cannot see is a bare reference list with no marker
    of its own — the guides' coverage table writes a cell as `35, 68, 74` under
    a column named for a book, and those come back as `number`. Identifying
    that cell is qc_cite's job (§5.3, "table cells under a column that names a
    book"), so a caller judging a guide passes text with the citations already
    removed rather than expecting this to guess.
    """
    text, starts, ends = _scrub(s)
    toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    out: list[dict] = []
    pending_page = False        # the previous token was "p." / "עמ'"
    page_list = False           # ...and the number after it ended with a comma
    i = 0

    def emit(kind, value, a: int, b: int):
        out.append({"kind": kind, "raw": s[starts[a]:ends[b - 1]], "value": value})

    while i < len(toks):
        tok, ta, tb = toks[i]
        # "5. **Buspirone:** ..." — the guides number their bullets, and an
        # ordered-list marker is not something anyone claimed. Without this,
        # every numbered paragraph hands the judge a value to dispute.
        if (_LIST_MARKER.fullmatch(tok) and i + 1 < len(toks)
                and _at_line_start(text, ta)):
            pending_page, page_list = False, False
            i += 1
            continue
        core = tok
        off = 0
        mp = _HEB_PREFIX.match(core)
        if mp:
            off += mp.end()
            core = core[mp.end():]
        lead = len(core) - len(core.lstrip(_LEAD_PUNCT))
        off += lead
        core = core[lead:].rstrip(_TRAIL_PUNCT)
        m = _NUMEXPR.match(core) if core else None

        if not m:
            word = _clean_word(tok)
            if word in _PAGE_MARKERS:
                pending_page, page_list = True, False
                i += 1
                continue
            if _PAGE_GLUED.match(_QUOTEISH.sub("", tok.strip(_LEAD_PUNCT))):
                pending_page = False
                page_list = tok.rstrip(_PAGE_LIST_STRIP).endswith(",")
                i += 1
                continue
            val = next((_WORD_NUM[v] for v in _heb_variants(word)
                        if v in _WORD_NUM), None)
            if val is not None:
                nxt = toks[i + 1][0] if i + 1 < len(toks) else ""
                nxt2 = toks[i + 2][0] if i + 2 < len(toks) else ""
                pair = (_clean_word(nxt), _clean_word(nxt2))
                if _is_count_noun(nxt) or pair in _OR_MORE:
                    emit("count", val, ta, tb)
            pending_page, page_list = False, False
            i += 1
            continue

        # A page number is settled before anything else is tried. `(DSM-5-TR
        # pp. 68-70):` is followed by the heading "קריטריון A", and קריטריון is
        # a counting noun — so a page range read as a criteria count is one
        # token away whenever the citation ends a sentence.
        if pending_page or page_list:
            page_list = tok.rstrip(_PAGE_LIST_STRIP).endswith(",")
            pending_page = False
            i += 1
            continue

        lo_raw, hi_raw = m.group("lo"), m.group("hi")
        lo = _number(lo_raw)
        hi = _number(hi_raw) if hi_raw else None
        tail = core[m.end():]
        num_a = ta + off
        num_b = ta + off + m.end()
        last = i                      # last token consumed by this value

        # --- a unit glued to the number: "80mg", "25%", "5-HT2A" ------------
        pct = tail.startswith("%")
        unit = None if pct else (_canon_unit(tail) if tail else None)
        if tail and not pct and unit is None:
            # letters fused to a number are an identifier, not a measurement
            pending_page, page_list = False, False
            i += 1
            continue
        if tail:
            num_b = ta + off + len(core)

        # --- "40%-60%": both ends in one token, split by the percent sign ---
        if pct and hi is None and tail != "%":
            mt = _PCT_RANGE_TAIL.fullmatch(tail)
            if mt:
                hi_raw = mt.group("hi")
                hi = _number(hi_raw)

        # --- a range spelled across tokens ----------------------------------
        # "2 to 8", "2 עד 8", and the `between` idiom of both languages (see
        # _range_partner). A percent carries its sign in `tail`, so the low end
        # of "בין 20% ל-30%" reaches here with a tail and still has a partner.
        if hi is None and (not tail or pct):
            found = _range_partner(toks, i, pct=pct,
                                   between=_between_before(toks, i))
            if found:
                hi_raw, last = found[0], found[1]
                hi = _number(hi_raw)
                num_b = _tok_end(*toks[last])

        # --- a unit or a counted noun in the next token --------------------
        if unit is None and not pct and last + 1 < len(toks):
            nxt_tok = toks[last + 1][0]
            if _clean_word(nxt_tok) == "%" or nxt_tok.startswith("%"):
                pct = True
                last += 1
                num_b = _tok_end(*toks[last])
            else:
                cand = _canon_unit(nxt_tok)
                if cand:
                    unit = cand
                    last += 1
                    num_b = _tok_end(*toks[last])
                elif _is_count_noun(nxt_tok):
                    last += 1
                    num_b = _tok_end(*toks[last])
                    emit("count", (lo, hi) if hi is not None else lo, num_a, num_b)
                    pending_page, page_list = False, False
                    i = last + 1
                    continue

        # --- "40 מ\"ג ליום", "40 mg per day": the "per" spelled as a word ----
        # Folded onto the dose so that it is the same value as the slash form
        # the book is quoted in. Only a dose folds: `8 שבועות ליום` is not a
        # unit, and a duration that swallowed a "per" would compare against
        # nothing. The loop chains, so `1.4 מ"ג לק"ג ליום` reaches mg/kg/day.
        while unit and unit[0] == "dose" and last + 1 < len(toks):
            nxt_tok = toks[last + 1][0]
            base, step = _per_unit(nxt_tok), 1
            if base is None and _clean_word(nxt_tok) in _PER_WORDS:
                if last + 2 >= len(toks):
                    break
                base, step = _per_base(toks[last + 2][0]), 2
            if base is None or base in unit[1].split("/"):
                break
            unit = ("dose", f"{unit[1]}/{base}")
            last += step
            num_b = _tok_end(*toks[last])

        amount = (lo, hi) if hi is not None else lo
        if pct:
            emit("percent", amount, num_a, num_b)
        elif unit:
            emit(unit[0], (amount, unit[1]), num_a, num_b)
        elif _is_year(lo_raw, lo) and (hi is None or _is_year(hi_raw, hi)):
            pass                      # a publication year or a span of them
        elif hi is not None:
            emit("range", amount, num_a, num_b)
        else:
            emit("number", amount, num_a, num_b)
        pending_page, page_list = False, False
        i = last + 1
    return out


def _bucket(v: dict) -> tuple:
    """Values only compare inside a (kind, unit) bucket.

    Comparing across units would mean converting weeks to days and mg/day to
    mg/kg/day, and a wrong conversion produces a false `contradicted` — the one
    outcome §2.3 forbids. Different units are simply not comparable here.
    """
    if v["kind"] in ("dose", "duration"):
        return (v["kind"], v["value"][1])
    return (v["kind"], "")


def values_dispute(said: str, source_quote: str) -> dict:
    """Does the source quote actually contradict the value the material states?

    §5.8: a `contradicted` finding on a locked value needs a DIFFERENT value of
    the same kind in the source quote, and a finding whose quote holds the same
    value as `said` is dropped. `disputed` is decided per said-value — the
    source must offer a comparable value and none of them may equal it — so a
    quote that states both "2-8 weeks" and "8-12 weeks" does not by itself
    convict the material's "2-8 weeks".
    """
    sv = locked_values(said)
    ov = locked_values(source_quote)
    peers: dict[tuple, list] = {}
    for w in ov:
        peers.setdefault(_bucket(w), []).append(w["value"])
    disputed = same = False
    for v in sv:
        got = peers.get(_bucket(v))
        if not got:
            continue
        if any(g == v["value"] for g in got):
            same = True
        else:
            disputed = True
    return {"said": sv, "source": ov, "disputed": disputed, "same": same}


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--norm")
    ap.add_argument("--values")
    ap.add_argument("--find")
    ap.add_argument("--book")
    ap.add_argument("--page", type=int)
    ap.add_argument("--radius", type=int, default=1)
    a = ap.parse_args()
    if a.norm is not None:
        print(norm(a.norm))
    if a.values is not None:
        print(json.dumps(locked_values(a.values), ensure_ascii=False, indent=1))
    if a.find is not None:
        if not (a.book and a.page):
            ap.error("--find needs --book and --page")
        window = source_window(a.book, a.page, a.radius)
        res = find_quote(a.find, window)
        print(json.dumps(res, ensure_ascii=False))
        if res["span"]:
            x, y = res["span"]
            print("---")
            print(window[max(0, x - 60):y + 60])
    if a.norm is None and a.values is None and a.find is None:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
