#!/usr/bin/env python3
"""Offline tests for qc_norm: what must match, and — the important half — what
must not.

Every case in the first three classes is ported from the 2026-09-18 spec review
(`spec_review/norm.py`, `norm2.py`), which ran them against the real books and
the real released chapters. Fourteen had to match (a model retyping a quote:
ASCII apostrophe for `’`, hyphen for en dash, bullet dropped, line re-wrapped;
an extractor's line-break hyphenation, soft hyphen and `ﬁ` ligature; a sentence
split across a printed-page boundary) and four had to fail (a fabricated quote,
the near-miss `2-12`, `28` read out of `2–8`, and a Hebrew paraphrase).

The book-backed versions of those cases are kept and skipped when the PDFs are
not on this machine — book text is never copied into fixtures. Every one of
them is also run here against text built inline, so the suite still proves the
rules on a machine without the books.

Fixtures are our own released material, trimmed to the units under test.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import qc_norm as Q  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
QUIZ_019_Q5 = FIX / "quiz_019_q5.json"
GUIDE_016 = FIX / "guide_016_excerpt.md"

# StahlKids printed p.69, retyped from the page. The atomoxetine sentence is the
# spec's known-answer probe (§4) and the source side of the 019 conflict, so it
# is the one string every rule here is exercised on.
PAGE_69 = (
    "ATOMOXETINE (continued)\n"
    "Tips and Pearls\n"
    "• Doesn’t work right away; full therapeutic benefits may take 2–8 weeks\n"
    "• Can be dosed once daily in the morning or split\n"
    "• May be useful when stimulants are not tolerated\n"
)
# StahlKids printed p.74, the other side of that conflict.
PAGE_74 = (
    "ATOMOXETINE (continued)\n"
    "• Symptoms may continue to improve for 8–12 weeks after starting\n"
)


def books() -> bool:
    """True when the textbook PDFs are on this machine (and PyMuPDF is)."""
    try:
        import qc_books
        qc_books.book("StahlKids")
        qc_books.book("Dulcan")
        return True
    except Exception:
        return False


_HAVE_BOOKS: bool | None = None


def have_books() -> bool:
    global _HAVE_BOOKS
    if _HAVE_BOOKS is None:
        _HAVE_BOOKS = books()
    return _HAVE_BOOKS


# ---------------------------------------------------------------------------
class TestNorm(unittest.TestCase):
    """§5.2 rule by rule."""

    def test_digit_boundary_survives_and_only_between_digits(self):
        # the rule the whole 019 finding depends on
        self.assertEqual(Q.norm("2–8 weeks"), "2|8weeks")
        self.assertEqual(Q.norm("2-8 weeks"), Q.norm("2–8 weeks"))
        self.assertEqual(Q.norm("2 – 8 weeks"), Q.norm("2–8 weeks"))
        self.assertNotEqual(Q.norm("28 weeks"), Q.norm("2–8 weeks"))
        self.assertNotEqual(Q.norm("2-12 weeks"), Q.norm("2–8 weeks"))
        self.assertNotEqual(Q.norm("8–12 weeks"), Q.norm("2–8 weeks"))
        # one boundary per run, however long the run
        self.assertEqual(Q.norm("2 — , – 8"), "2|8")
        # and never outside a digit pair
        self.assertEqual(Q.norm("open-label"), "openlabel")
        self.assertEqual(Q.norm("p. 69"), "p69")

    def test_nfkc(self):
        self.assertEqual(Q.norm("beneﬁts"), "benefits")        # ﬁ ligature
        self.assertEqual(Q.norm("２８"), "28")              # fullwidth digits
        self.assertEqual(Q.norm("été"), Q.norm("été"))

    def test_invisible_characters_deleted(self):
        self.assertEqual(Q.norm("spe­cific"), "specific")       # soft hyphen
        self.assertEqual(Q.norm("‏שלום‎"), "שלום")
        self.assertEqual(Q.norm("⁦ADHD⁩ א"), "adhdא")

    def test_latex(self):
        self.assertEqual(Q.norm("$1.4\\ mg/kg/day$"), Q.norm("1.4 mg/kg/day"))
        self.assertEqual(Q.norm("$25\\%$"), Q.norm("25%"))
        self.assertEqual(Q.norm("$20-40\\text{ mg}$"), Q.norm("20–40 mg"))
        self.assertEqual(Q.norm("\\(x\\)"), "x")
        self.assertEqual(Q.norm("$\\alpha_{2}$ agonist"), "2agonist")
        # \text{x} -> x, not "textx"
        self.assertNotIn("text", Q.norm("$1.5\\text{ mEq/L}$"))

    def test_markdown(self):
        self.assertEqual(Q.norm("**bold**"), "bold")
        self.assertEqual(Q.norm("__under__"), "under")
        self.assertEqual(Q.norm("`code`"), "code")
        self.assertEqual(Q.norm("### Q5"), "q5")
        # bold inside a number must not fabricate a boundary
        self.assertEqual(Q.norm("**12**15"), Q.norm("1215"))
        self.assertNotEqual(Q.norm("**12**15"), Q.norm("12-15"))
        # a table pipe between two cells is a boundary like any other run
        self.assertEqual(Q.norm("| 330 | 331 |"), "330|331")

    def test_casefold(self):
        self.assertEqual(Q.norm("Atomoxetine"), Q.norm("ATOMOXETINE"))
        self.assertEqual(Q.norm("straße"), Q.norm("STRASSE"))

    def test_hebrew_letters_survive_its_punctuation_does_not(self):
        heb = "**נדרשים (6)** מהתסמינים־הבאים, עמ׳ 68"
        ascii_twin = "נדרשים (6) מהתסמינים-הבאים, עמ' 68"
        self.assertEqual(Q.norm(heb), Q.norm(ascii_twin))
        self.assertIn("נדרשים", Q.norm(heb))      # letters kept
        for bad in ("׳", "״", "־", "(", ")", ",", " "):
            self.assertNotIn(bad, Q.norm(heb))
        # gershayim and an ASCII double quote are the same word
        self.assertEqual(Q.norm("מ״ג"), Q.norm('מ"ג'))

    def test_line_break_hyphenation_and_rewrap(self):
        self.assertEqual(Q.norm("open-\nlabel"), Q.norm("open-label"))
        self.assertEqual(Q.norm("SSRI- related"), Q.norm("SSRI-related"))
        self.assertEqual(Q.norm("meta-\nanalysis"), Q.norm("meta-analysis"))

    def test_empty(self):
        self.assertEqual(Q.norm(""), "")
        self.assertEqual(Q.norm(None), "")


class TestNormMap(unittest.TestCase):
    def test_offsets_point_at_the_raw_characters(self):
        raw = "**2–8** שבועות"
        text, offsets = Q.norm_map(raw)
        self.assertEqual(text, Q.norm(raw))
        self.assertEqual(len(text), len(offsets))
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(raw[offsets[0]], "2")
        self.assertEqual(raw[offsets[1]], "–")   # the boundary's own run
        self.assertEqual(raw[offsets[2]], "8")
        self.assertEqual("".join(raw[i] for i in offsets[3:]), "שבועות")

    def test_span_of_a_match_is_the_raw_span(self):
        raw = "המינון הוא **1.4** מ״ג ליום."
        res = Q.find_quote("1.4 מ\"ג", raw, whole_unit=True)
        self.assertEqual(res["match"], "exact")
        a, b = res["span"]
        self.assertEqual(raw[a:b], "1.4** מ״ג")

    def test_offsets_survive_an_nfkc_expansion(self):
        raw = "beneﬁts"
        text, offsets = Q.norm_map(raw)
        self.assertEqual(text, "benefits")
        self.assertEqual(len(offsets), len(text))
        self.assertEqual(offsets[4], offsets[5])       # f and i share one raw char
        self.assertEqual(raw[offsets[4]], "ﬁ")


# ---------------------------------------------------------------------------
class TestFindQuoteInline(unittest.TestCase):
    """The ported must-match/must-fail cases, on text built here."""

    def test_must_match_a_retyped_quote(self):
        for name, q in [
            ("ASCII apostrophe",
             "Doesn't work right away; full therapeutic benefits may take 2–8 weeks"),
            ("hyphen for en dash",
             "Doesn’t work right away; full therapeutic benefits may take 2-8 weeks"),
            ("bullet kept",
             "• Doesn’t work right away; full therapeutic benefits may take 2–8 weeks"),
            ("re-wrapped",
             "Doesn't work right away;\nfull therapeutic benefits\nmay take 2-8 weeks"),
            ("upper case",
             "FULL THERAPEUTIC BENEFITS MAY TAKE 2-8 WEEKS"),
        ]:
            with self.subTest(name):
                res = Q.find_quote(q, PAGE_69)
                self.assertEqual(res["match"], "exact")
                self.assertIn("2–8 weeks", PAGE_69[res["span"][0]:res["span"][1]])

    def test_must_fail_near_miss_numbers(self):
        for name, q in [
            ("2-12 is not 2-8", "full therapeutic benefits may take 2-12 weeks"),
            ("28 is not 2-8", "full therapeutic benefits may take 28 weeks"),
            ("8-12 is the other page", "full therapeutic benefits may take 8–12 weeks"),
        ]:
            with self.subTest(name):
                self.assertIsNone(Q.find_quote(q, PAGE_69)["match"])
        # ...and the 8-12 quote does match where it belongs
        self.assertEqual(
            Q.find_quote("Symptoms may continue to improve for 8-12 weeks",
                         PAGE_74)["match"], "exact")

    def test_must_fail_a_fabricated_quote(self):
        # the 2026-09-18 probe's invented "verbatim" quote, which agy returned
        # with status SUCCESS after attaching nothing
        res = Q.find_quote(
            "Although atomoxetine can work as soon as the first day, it can take "
            "several weeks to begin working and 8 to 12 weeks to reach maximum "
            "therapeutic benefit", PAGE_69 + PAGE_74)
        self.assertIsNone(res["match"])
        self.assertLess(res["score"], Q.FUZZY_MIN_SCORE)

    def test_short_quotes_are_rejected_unless_whole_unit(self):
        short = "2–8 weeks"
        self.assertLess(len(Q.norm(short)), Q.MIN_QUOTE_CHARS)
        res = Q.find_quote(short, PAGE_69)
        self.assertIsNone(res["match"])
        self.assertEqual(res["reason"], "too_short")
        self.assertEqual(Q.find_quote(short, PAGE_69, whole_unit=True)["match"], "exact")
        self.assertEqual(Q.find_quote(short, PAGE_69, min_len=5)["match"], "exact")

    def test_ellipsis_pieces_must_appear_in_order(self):
        ok = "Doesn't work right away… benefits may take 2-8 weeks"
        self.assertEqual(Q.find_quote(ok, PAGE_69)["match"], "exact")
        self.assertEqual(Q.find_quote(ok.replace("…", "..."), PAGE_69)["match"],
                         "exact")
        reordered = "benefits may take 2-8 weeks … Doesn't work right away"
        self.assertIsNone(Q.find_quote(reordered, PAGE_69)["match"])

    def test_an_ellipsis_piece_too_short_to_be_evidence(self):
        res = Q.find_quote("Doesn't work right away; full therapeutic … 2-8",
                           PAGE_69)
        self.assertIsNone(res["match"])
        self.assertEqual(res["reason"], "piece_too_short")

    def test_fuzzy_for_a_two_column_page(self):
        # what a two-column extraction does: the other column's lines land
        # between the quote's own lines
        interleaved = (
            "Doesn't work right away; full therapeutic\n"
            "DOSING AND USE\n"
            "benefits may take 2–8 weeks to be seen\n"
            "Usual dosage range\n")
        quote = ("Doesn't work right away; full therapeutic benefits may take "
                 "2-8 weeks to be seen")
        # the exact path must not swallow the intruding tokens...
        self.assertNotIn(Q.norm(quote), Q.norm(interleaved))
        # ...but the quote is really there, so the fuzzy path finds it
        res = Q.find_quote(quote, interleaved)
        self.assertEqual(res["match"], "fuzzy")
        self.assertIn("2–8 weeks", interleaved[res["span"][0]:res["span"][1]])

    def test_fuzzy_needs_nine_tenths_of_the_tokens(self):
        base = ("alpha beta gamma delta epsilon zeta eta theta iota kappa "
                "lambda mu nu xi omicron pi rho sigma tau upsilon")
        one_word_off = base.replace("omicron", "omega")
        self.assertEqual(Q.find_quote(one_word_off, base)["match"], "fuzzy")
        four_words_off = (base.replace("omicron", "omega")
                          .replace("rho", "chi").replace("tau", "psi")
                          .replace("iota", "digamma"))
        self.assertIsNone(Q.find_quote(four_words_off, base)["match"])

    def test_fuzzy_will_not_assemble_a_quote_from_across_the_page(self):
        quote = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
        filler = " " + " ".join(["padding"] * 8) + " "
        scattered = filler.join(quote.split(" "))
        res = Q.find_quote(quote, scattered)
        self.assertIsNone(res["match"])
        self.assertEqual(res["reason"], "scattered")
        self.assertGreaterEqual(res["score"], Q.FUZZY_MIN_SCORE)


class TestFindQuoteOnReleasedMaterial(unittest.TestCase):
    """The material side of a finding: the judge's `said` must be in the unit."""

    def setUp(self):
        self.q5 = json.loads(QUIZ_019_Q5.read_text(encoding="utf-8"))["questions"][0]
        self.option = self.q5["answerOptions"][0]["text"]
        self.guide = GUIDE_016.read_text(encoding="utf-8")

    def test_judge_drops_the_latex_it_was_shown(self):
        # 019 Q5 option A is `מינון של $1.4\ mg/kg/day$ וניטור...`; a model
        # quotes it without the `$` and without the backslash-space
        typed = self.option.replace("$", "").replace("\\ ", " ")
        self.assertNotEqual(typed, self.option)
        self.assertEqual(Q.find_quote(typed, self.option, whole_unit=True)["match"],
                         "exact")

    def test_a_hebrew_paraphrase_is_not_a_quote(self):
        paraphrase = "מינון של 1.4 מ\"ג לק\"ג ליום וניטור דופק"
        self.assertIsNone(
            Q.find_quote(paraphrase, self.option, whole_unit=True)["match"])

    def test_a_guide_line_quoted_without_its_markdown(self):
        line = re.search(r"[^\n]*\*\*[^\n]*\*\*[^\n]*\(DSM[^\n]*",
                         self.guide).group(0)
        typed = line.replace("**", "")[:120]
        res = Q.find_quote(typed, self.guide)
        self.assertEqual(res["match"], "exact")
        a, b = res["span"]
        self.assertTrue(self.guide[a:b].startswith("1."))

    def test_the_same_line_with_ascii_for_hebrew_punctuation(self):
        line = re.search(r"[^\n]*\*\*[^\n]*\*\*[^\n]*\(DSM[^\n]*",
                         self.guide).group(0)
        typed = (line.replace("**", "").replace("־", "-")
                 .replace("׳", "'").replace("״", '"'))[:120]
        self.assertEqual(Q.find_quote(typed, self.guide)["match"], "exact")


# ---------------------------------------------------------------------------
class _FakeBook:
    """Just enough of qc_books.Book for source_window, with the folio lines the
    laid-out books (Stahl, Kartisiot) keep on the page."""

    def __init__(self, pages: dict[int, str]):
        self.pages = pages

    def page_text(self, printed: int):
        return self.pages.get(printed)


class TestSourceWindowInline(unittest.TestCase):
    """A quote crossing a page-number line — the Dulcan p.330/331 case, built
    here so it runs without the books."""

    PAGES = {
        330: ("Both groups improved over 12 weeks.\n"
              "Those receiving CBT in conjunction with a tablet\n330\n"),
        331: ("331\nknew that they were taking active medication, thus\n"
              "accentuating the expectation of improvement.\n"),
        332: ("332\nAdverse events were comparable across arms.\n"),
    }
    QUOTE = ("Those receiving CBT in conjunction with a tablet knew that they "
             "were taking active medication, thus accentuating the expectation")

    def setUp(self):
        import qc_books
        self._real = qc_books.book
        qc_books.book = lambda key: _FakeBook(self.PAGES)
        self.addCleanup(setattr, qc_books, "book", self._real)

    def test_the_page_number_line_costs_the_exact_match(self):
        # concatenating the pages as extracted leaves "330\n331" inside the
        # sentence: the quote is no longer verbatim anywhere, and only the
        # fuzzy path rescues it — which is why §5.2 strips the lines first.
        naive = self.PAGES[330] + self.PAGES[331]
        self.assertNotIn(Q.norm(self.QUOTE), Q.norm(naive))
        self.assertEqual(Q.find_quote(self.QUOTE, naive)["match"], "fuzzy")

    def test_source_window_strips_it_and_the_quote_is_found(self):
        window = Q.source_window("Dulcan", 330)
        self.assertNotRegex(window, r"(?m)^[ \t]*\d{1,4}[ \t]*$")
        res = Q.find_quote(self.QUOTE, window)
        self.assertEqual(res["match"], "exact")

    def test_neither_page_alone_holds_it(self):
        self.assertIsNone(Q.find_quote(self.QUOTE, self.PAGES[330])["match"])
        self.assertIsNone(Q.find_quote(self.QUOTE, self.PAGES[331])["match"])

    def test_radius_and_missing_pages(self):
        self.assertIn("Adverse events", Q.source_window("Dulcan", 331))
        self.assertNotIn("Adverse events", Q.source_window("Dulcan", 330))
        self.assertIn("Adverse events", Q.source_window("Dulcan", 330, radius=2))
        # a page that does not exist is skipped, not an error
        self.assertNotEqual(Q.source_window("Dulcan", 329), "")

    def test_an_unmappable_book_is_refused(self):
        with self.assertRaises(ValueError):
            Q.source_window("Synopsis", 12)


# ---------------------------------------------------------------------------
# Real strings from the released chapters (scratchpad qc_samples). Chapter tag
# in the first column; these are the forms locked_values actually meets.
SAMPLES = [
    ("019", "מינון של $1.4\\ mg/kg/day$ וניטור דופק, לחץ דם, גובה ומשקל.",
     [("dose", (1.4, "mg/kg/day"))]),
    # "ליום" is the preposition form of the slash in `80 mg/day`, and it has to
    # canonicalize the same way: StahlKids writes the dose as `80 mg/day`, and
    # (80, 'mg') sits in a different `_bucket` from (80, 'mg/day'), where
    # values_dispute can neither drop the identical value (§5.8) nor see a real
    # disagreement with it. Only an explicit ל/ב folds — 036's "יום כן יום לא"
    # below is every other day and stays (25, 'mg').
    ("019", "מינון קבוע של $80\\ mg$ ליום ללא קשר למשקל הילד.",
     [("dose", (80, "mg/day"))]),
    ("019", "מינון של $0.5\\ mg/kg$ פעם בשבוע וניטור רמות סוכר.",
     [("dose", (0.5, "mg/kg"))]),
    ("038", "Approximately $25\\%$ show clinical thyroid disease.",
     [("percent", 25)]),
    ("038", "A score of 17 or greater.", [("number", 17)]),
    ("035", "התרופה יעילה רק אם רמתה בדם נשמרת מעל $1.5\\text{ mEq/L}$.",
     [("dose", (1.5, "meq/l"))]),
    ("035", "בדיוק 12 שעות לאחר נטילת המנה האחרונה.",
     [("duration", (12, "hours"))]),
    ("035", "ביצוע Holter 24 שעות פעם בחודש.",
     [("duration", (24, "hours"))]),
    ("035", "* **מתבגרים/מבוגרים**: מתחילים במינון של **300 מ\"ג 2-3 פעמים ביום** או מנה יחידה של 300 מ\"ג בלילה (StahlKids p. 288).",
     [("dose", (300, "mg")), ("count", (2, 3)), ("dose", (300, "mg"))]),
    ("035", "* **TSH**: כל 6 חודשים, או בתדירות גבוהה יותר (Dulcan p. 765, p. 767).",
     [("duration", (6, "months"))]),
    ("036", "מתן מינון התחלתי של $25\\ mg$ יום כן יום לא למשך שבועיים.",
     [("dose", (25, "mg"))]),
    ("042", "5. **Buspirone:** ... דורש 2–4 שבועות להשגת אפקט טיפולי (StahlNeuro p.369, DSM p.621).",
     [("duration", ((2, 4), "weeks"))]),
    ("042", "Autonomic hyperactivity, such as sweating or a pulse rate > 100 bpm.",
     [("dose", (100, "bpm"))]),
    ("043", "Fasting Glucose and Lipid profile every 3 months", [("duration", (3, "months"))]),
    ("043", "16-23", [("range", (16, 23))]),
    ("039", "81% בקבוצה שקיבלה Sertraline בלבד.", [("percent", 81)]),
    ("017", "6 סימפטומים.", [("count", 6)]),
    ("049", "משך התסמינים בקריטריון B הוא **3 ימים עד 1 חודש** לאחר החשיפה לטראומה (DSM p.315).",
     [("duration", (3, "days")), ("duration", (1, "months"))]),
    ("008", "*   *הליכה עצמאית:* גיל **12–15 חודשים** (Dulcan p. 28).",
     [("duration", ((12, 15), "months"))]),
    ("008", "*   בגיל **2.5 עד 3 שנים:** מופיעה יכולת קפיצה במקום (Jumping).",
     [("duration", ((2.5, 3), "years"))]),
    ("057", "* **מינון מקסימלי:** 80 מ\"ג/יום בפדיאטריה (עד 160 מ\"ג/יום במבוגרים) `(StahlKids p.302)`.",
     [("dose", (80, "mg/day")), ("dose", (160, "mg/day"))]),
    ("029", "הורדה הדרגתית על פני תקופה של $2-4$ שבועות",
     [("duration", ((2, 4), "weeks"))]),
    ("029", "הורדה על פני $3$ ימים בלבד (כל פעם ב-$50\\%$)",
     [("duration", (3, "days")), ("percent", 50)]),
    # The forms that must yield NOTHING: identifiers, pages, years.
    ("005", "היא פועלת כ-Selective $5HT_{2A}$ inverse agonist ללא חסימת קולטני $D_{2}$.", []),
    ("036", "בדיקת גנוטיפ לנוכחות האלל $HLA-B*1502$.", []),
    ("029", "זמן מחצית חיים ($t_{1/2}$) קצר ועיכוב עצמי של המטבוליזם", []),
    ("042", "הראו (StahlNeuro p. 289, 902) וגם (Dulcan ch.7 p.412).", []),
    ("016", "DSM-5-TR (2022) החליף את DSM-5 משנת 2013.", []),
]


class TestLockedValues(unittest.TestCase):
    def test_real_strings_from_released_chapters(self):
        for tag, text, expect in SAMPLES:
            with self.subTest(tag=tag, text=text[:48]):
                got = [(v["kind"], v["value"]) for v in Q.locked_values(text)]
                self.assertEqual(got, expect)

    def test_raw_is_always_a_slice_of_the_input(self):
        for tag, text, _ in SAMPLES:
            for v in Q.locked_values(text):
                with self.subTest(tag=tag, raw=v["raw"]):
                    self.assertIn(v["raw"], text)
                    self.assertRegex(v["raw"], r"\d")

    def test_the_two_sides_of_the_019_conflict(self):
        self.assertEqual([v["value"] for v in Q.locked_values(
            "full therapeutic benefits may take 2–8 weeks")],
            [((2, 8), "weeks")])
        self.assertEqual([v["value"] for v in Q.locked_values(
            "continue to improve for 8–12 weeks")],
            [((8, 12), "weeks")])

    def test_spelled_out_criteria_counts(self):
        # DSM p.135 catatonia: "three (or more)" of 12 — the 042 Q2 finding
        self.assertEqual(
            [(v["kind"], v["value"]) for v in Q.locked_values(
                "three (or more) of the following symptoms")],
            [("count", 3)])
        self.assertEqual(
            [(v["kind"], v["value"]) for v in Q.locked_values(
                "נדרשים שני תסמינים לפחות")],
            [("count", 2)])

    def test_hebrew_and_english_units_land_on_the_same_value(self):
        self.assertEqual(Q.locked_values("1.4 מ\"ג/ק\"ג/יום")[0]["value"],
                         Q.locked_values("1.4 mg/kg/day")[0]["value"])
        self.assertEqual(Q.locked_values("2–4 שבועות")[0]["value"],
                         Q.locked_values("2 to 4 weeks")[0]["value"])
        self.assertEqual(Q.locked_values("תוך 3 חודשים")[0]["value"],
                         Q.locked_values("within 3 months")[0]["value"])

    def test_a_compound_adjective_is_still_a_duration(self):
        # StahlKids p.68 describes "a 50-week open-label, flexible-dose trial";
        # the trial length is a locked value, the hyphen is not a range
        self.assertEqual([v["value"] for v in Q.locked_values(
            "a 50-week open-label, flexible-dose trial")], [(50, "weeks")])
        self.assertEqual([v["value"] for v in Q.locked_values(
            "a 12-month follow-up")], [(12, "months")])
        self.assertEqual(Q.locked_values("5-HT2A receptors"), [])

    def test_up_to_is_a_bound_not_a_range(self):
        self.assertEqual([v["value"] for v in Q.locked_values("up to 8 weeks")],
                         [(8, "weeks")])
        self.assertEqual([v["value"] for v in Q.locked_values("עד 160 מ\"ג/יום")],
                         [(160, "mg/day")])

    def test_citations_never_become_values(self):
        # every citation shape §5.3 lists, none of which anyone "claimed"
        for cite in [
            "(DSM-5-TR pp. 68-70): **קריטריון A**",
            "(DSM-5-TR p. 68, Dulcan p. 174).",
            "(StahlKids p.141, p.243, p.711)",
            "(StahlNeuro p. 289, 902)",
            "Kaplan & Sadock p.33.2",
            "כרטיסיות עמ׳ 506, 507",
            "(Dulcan ch.7 p.412)",
            "(Synopsis ch.5)",
            "DSM-5-TR (2022) החליף את DSM-5 משנת 2013.",
        ]:
            with self.subTest(cite):
                self.assertEqual(Q.locked_values(cite), [])

    def test_an_ordered_list_marker_is_not_a_value(self):
        # "5. **Buspirone:** ... 2-4 שבועות" — the bullet's own number
        line = ("5. **Buspirone:** דורש 2–4 שבועות "
                "להשגת אפקט טיפולי")
        self.assertEqual([v["value"] for v in Q.locked_values(line)],
                         [((2, 4), "weeks")])
        # ...but a number that opens a line and is not a marker still counts
        self.assertEqual([v["kind"] for v in Q.locked_values("6 סימפטומים.")],
                         ["count"])

    def test_a_range_is_one_value_not_two(self):
        vals = Q.locked_values("2–8 weeks")
        self.assertEqual(len(vals), 1)
        self.assertEqual(vals[0]["value"], ((2, 8), "weeks"))
        self.assertNotEqual(Q.locked_values("28 weeks")[0]["value"],
                            vals[0]["value"])


class TestValuesDispute(unittest.TestCase):
    def test_the_019_atomoxetine_conflict(self):
        r = Q.values_dispute("האפקט המלא מושג תוך 2–8 שבועות",
                             "continue to improve for 8–12 weeks")
        self.assertTrue(r["disputed"])
        self.assertFalse(r["same"])

    def test_the_same_value_is_not_a_dispute(self):
        r = Q.values_dispute("האפקט המלא מושג תוך 2–8 שבועות",
                             "full therapeutic benefits may take 2-8 weeks")
        self.assertTrue(r["same"])
        self.assertFalse(r["disputed"])

    def test_28_and_2_8_and_2_12_are_three_different_claims(self):
        source = "full therapeutic benefits may take 2–8 weeks"
        self.assertTrue(Q.values_dispute("may take 28 weeks", source)["disputed"])
        self.assertTrue(Q.values_dispute("may take 2-12 weeks", source)["disputed"])
        self.assertFalse(Q.values_dispute("may take 2-8 weeks", source)["disputed"])

    def test_across_languages(self):
        said = "מינון של $1.4\\ mg/kg/day$"
        self.assertTrue(Q.values_dispute(said, "1.2 mg/kg/day")["disputed"])
        self.assertTrue(Q.values_dispute(said, "1.4 מ\"ג/ק\"ג/יום")["same"])

    def test_different_units_are_not_comparable(self):
        # a false `contradicted` is the outcome the whole mechanism guards
        # against, so mg/day never convicts mg/kg/day
        r = Q.values_dispute("1.4 mg/kg/day", "the target dose is 80 mg/day")
        self.assertFalse(r["disputed"])
        self.assertFalse(r["same"])
        self.assertFalse(Q.values_dispute("2–8 weeks", "56 days")["disputed"])

    def test_a_source_quote_with_no_value_disputes_nothing(self):
        r = Q.values_dispute("2–8 weeks", "atomoxetine is a norepinephrine "
                                              "reuptake inhibitor")
        self.assertFalse(r["disputed"])
        self.assertFalse(r["same"])
        self.assertEqual(r["source"], [])

    def test_the_criteria_count(self):
        r = Q.values_dispute("נדרשים שני תסמינים",
                             "three (or more) of the following symptoms")
        self.assertTrue(r["disputed"])

    def test_page_numbers_never_become_a_dispute(self):
        r = Q.values_dispute("ראו (StahlNeuro p. 289, 902)",
                             "see Dulcan p. 330 and p. 331")
        self.assertFalse(r["disputed"])
        self.assertEqual(r["said"], [])


# ---------------------------------------------------------------------------
@unittest.skipUnless(have_books(), "textbook PDFs not on this machine")
class TestAgainstTheBooks(unittest.TestCase):
    """The same ported cases, against the real extracted pages. Book text is
    read through qc_books at run time and never copied into a fixture."""

    @classmethod
    def setUpClass(cls):
        import qc_books
        cls.kids = qc_books.book("StahlKids")
        cls.dulcan = qc_books.book("Dulcan")

    def test_source_window_stahlkids_69(self):
        window = Q.source_window("StahlKids", 69)
        self.assertNotRegex(window, r"(?m)^[ \t]*\d{1,4}[ \t]*$")
        for page in (68, 69, 70):
            body = re.sub(r"(?m)^[ \t]*\d{1,4}[ \t]*$", "",
                          self.kids.page_text(page) or "").strip()
            self.assertIn(body.split("\n")[0].strip(), window)
        res = Q.find_quote("Doesn't work right away; full therapeutic benefits "
                           "may take 2-8 weeks", window)
        self.assertEqual(res["match"], "exact")
        self.assertIn("2", window[res["span"][0]:res["span"][1]])

    def test_the_known_answer_probe_and_its_near_misses(self):
        page = self.kids.page_text(69)
        self.assertEqual(Q.find_quote(
            "Doesn't work right away; full therapeutic benefits may take 2–8 weeks",
            page)["match"], "exact")
        for bad in ("full therapeutic benefits may take 2-12 weeks",
                    "full therapeutic benefits may take 28 weeks and more"):
            with self.subTest(bad):
                self.assertIsNone(Q.find_quote(bad, page)["match"])

    def test_the_fabricated_quote_is_on_no_page(self):
        hay = "\n".join(self.kids.page_text(p) or "" for p in (68, 69, 70))
        self.assertIsNone(Q.find_quote(
            "Although atomoxetine can work as soon as the first day, it can take "
            "several weeks to begin working and 8 to 12 weeks to reach maximum "
            "therapeutic benefit", hay)["match"])

    def test_extractor_hyphenation(self):
        self.assertEqual(Q.find_quote("a 50-week open-label, flexible-dose trial",
                                      self.kids.page_text(68))["match"], "exact")
        self.assertEqual(Q.find_quote(
            "Rare activation of suicidal ideation and behavior (suicidality) "
            "(short-term regulatory studies did not show any actual suicides",
            self.kids.page_text(70))["match"], "exact")
        d331 = self.dulcan.page_text(331)
        self.assertEqual(Q.find_quote(
            "the probability of SSRI-related improvement increased over time",
            d331)["match"], "exact")
        self.assertEqual(Q.find_quote(
            "In a recent meta-analysis (number of studies = 18; N = 2,631)",
            d331)["match"], "exact")

    def test_the_dulcan_330_331_boundary_needs_the_window(self):
        quote = ("Those receiving CBT in conjunction with a tablet knew that "
                 "they were taking active medication, thus accentuating the "
                 "expectation")
        self.assertEqual(Q.find_quote(quote, Q.source_window("Dulcan", 330))["match"],
                         "exact")
        self.assertIsNone(Q.find_quote(quote, self.dulcan.page_text(331))["match"])
        self.assertIsNone(Q.find_quote(quote, self.dulcan.page_text(330))["match"])

    def test_soft_hyphen_and_ligature_typed_as_a_reader_sees_them(self):
        whole = "\n".join(self.kids.texts)
        for name, pattern, clean in [
            ("soft hyphen", r"\w+­\s*\w+",
             lambda w: re.sub(r"\s+", " ", w.replace("­\n", "")
                              .replace("­", "")).strip()),
            ("ligature", r"\w*[ﬀ-ﬆ]\w*",
             lambda w: re.sub(r"\s+", " ", unicodedata.normalize("NFKC", w)).strip()),
        ]:
            with self.subTest(name):
                self.assertIsNotNone(re.search(pattern, whole),
                                     f"no {name} in StahlKids")
                t = next(t for t in self.kids.texts if re.search(pattern, t))
                mm = re.search(pattern, t)
                window = t[max(0, mm.start() - 60):mm.end() + 60]
                typed = clean(window)[10:-10]
                self.assertEqual(Q.find_quote(typed, t, whole_unit=True)["match"],
                                 "exact")

    def test_locked_values_on_the_probe_pages(self):
        p69 = self.kids.page_text(69)
        self.assertIn(((2, 8), "weeks"),
                      [v["value"] for v in Q.locked_values(p69)])
        p74 = self.kids.page_text(74)
        self.assertIn(((8, 12), "weeks"),
                      [v["value"] for v in Q.locked_values(p74)])


if __name__ == "__main__":
    unittest.main()
