#!/usr/bin/env python3
"""What the audit of the free checks found, closed one test at a time.

2026-09-25. Five agents audited qc_lint's check families against the released
bytes and three adversarial agents tried to refute them. What survived:

  * The marker regex matched `[12]` only, so it found 311 of the 614 NotebookLM
    markers in the corpus. A BLOCKING check passed guides still reading
    `(Dulcan p. 176) [2, 3]`, and qc_fix stripped half of each chapter's markers
    and left the rest in front of the learner.
  * qc_lint and qc_fix kept two spellings of that regex, which is the drift the
    module's own comment warns about: a marker the lint flags and the fix cannot
    strip is a chapter that can never pass.
  * hygiene_latex used `search`, so chapter 059's monitoring table — 26 cells
    reading `\(\checkmark\)` — was one flag quoting one cell.
  * 136 of 156 backfill_asset flags were audio and infographics, which
    bulk_run.py disables on purpose (SKIP_QUOTA_ARTIFACTS): a ledger of a
    decision reported as 68 chapters of holes.
  * rebalance_keys recorded where it MOVED each key and never where the key was,
    so the repair erased the evidence that the published quizzes answered A in
    544 of 621 single-answer items.

    python -m unittest tests.qc.test_fix_lint_audit
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_fix                                                # noqa: E402
import qc_lint                                               # noqa: E402


class MarkersAreLists(unittest.TestCase):
    """`[2, 3]` and `[13-16]` are markers; `[1234]` and a link are not."""

    REAL = "מופיעה בקרב כ-7.2% מהילדים והמתבגרים בעולם (Dulcan p. 176) [2, 3]"

    def test_the_multi_number_marker_the_blocking_check_passed(self):
        self.assertTrue(qc_lint._MARKER_RE.search(self.REAL))
        self.assertTrue(qc_fix._MARKER_RE.search(self.REAL))

    def test_a_dash_range_is_a_marker(self):
        for form in ("x [13-16] y", "x [13\u201316] y"):
            self.assertTrue(qc_lint._MARKER_RE.search(form), form)

    def test_a_single_number_still_is_one(self):
        self.assertTrue(qc_lint._MARKER_RE.search("סוף משפט [7]"))

    def test_four_digits_is_not_a_marker(self):
        self.assertFalse(qc_lint._MARKER_RE.search("[1234]"))

    def test_a_json_list_across_lines_is_not_a_marker(self):
        """facts.json is dumped with indent=1, so `[\n 1,` must stay quiet."""
        self.assertFalse(qc_lint._MARKER_RE.search('{"pages": [\n 1,\n 2\n ]}'))

    def test_a_markdown_link_is_not_a_marker_in_either_module(self):
        """The drift that would deadlock a chapter: flagged and unfixable."""
        link = "see [3](http://example.com)"
        self.assertFalse(qc_lint._MARKER_RE.search(link))
        self.assertFalse(qc_fix._MARKER_RE.search(link))

    def test_one_pattern_feeds_both_modules(self):
        self.assertIn(qc_lint._MARKER_BODY, qc_fix._MARKER_RE.pattern)


class TheFlagSaysHowMany(unittest.TestCase):

    def flag(self, text, check="hygiene_latex", *patterns):
        flags = {}
        qc_lint._span_flag(flags, "U1", check, text,
                           *(patterns or (qc_lint._LATEX_RE, qc_lint._MATH_RE)))
        return (flags.get("U1") or [{}])[0].get("detail", "")

    def test_one_span_is_quoted_plainly(self):
        d = self.flag(r"טבלה $5	ext{-HT}_{2C}$ בלבד", "hygiene_latex",
                      qc_lint._MATH_RE)
        self.assertTrue(d)
        self.assertNotIn("נוספים", d)

    def test_twenty_six_cells_do_not_look_like_one(self):
        """One cell is three spans to clean (`\(`, `\checkmark`, `\)`), so the
        count is of spans and 26 cells must not read as one finding."""
        d = self.flag(r"\(\checkmark\) " * 26)
        self.assertIn("נוספים", d)
        n = int(d.split("(+")[1].split(" ")[0])
        self.assertGreaterEqual(n, 25, d)

    def test_a_clean_unit_gets_no_flag(self):
        self.assertEqual(self.flag("טקסט נקי לגמרי"), "")


class DeliberatelyOffIsNotAGap(unittest.TestCase):

    def counts(self, names):
        chapter = []
        qc_lint._lint_completeness({n: None for n in names}, chapter, [])
        out = {}
        for f in chapter:
            out.setdefault(f["check"], []).append(f["detail"])
        return out

    def test_audio_and_infographics_are_deferred_not_missing(self):
        got = self.counts(["study_guide.md", "facts.json", "quiz.json",
                           "flashcards.json", "video.mp4"])
        self.assertEqual(got.get("backfill_asset"), None)
        self.assertEqual(len(got.get("deferred_asset") or []), 2)
        self.assertNotIn("missing_asset", got)

    def test_a_plate_without_a_legend_is_still_a_real_gap(self):
        got = self.counts(["study_guide.md", "facts.json", "quiz.json",
                           "flashcards.json", "video.mp4", "audio.mp3",
                           "infographic.png", "sketchy_ch01.png"])
        self.assertTrue(any("אגדה חסרה" in d
                            for d in got.get("backfill_asset") or []))

    def test_an_essential_asset_still_blocks(self):
        got = self.counts(["study_guide.md", "quiz.json", "flashcards.json"])
        self.assertIn("facts.json", got.get("missing_asset") or [])
        self.assertTrue(qc_lint.CHECKS["missing_asset"]["blocking"])
        self.assertFalse(qc_lint.CHECKS["deferred_asset"]["blocking"])


class TheRepairKeepsItsEvidence(unittest.TestCase):

    def quiz(self, n_items):
        """n items whose key is option A — the corpus's actual shape."""
        return {"items": [
            {"id": f"Q{i}", "question": f"שאלה מספרת {i} שדורשת אינטגרציה קלינית",
             "answerOptions": [
                 {"text": f"התשובה הנכונה {i}", "isCorrect": True},
                 {"text": f"מסיח ראשון {i}", "isCorrect": False},
                 {"text": f"מסיח שני {i}", "isCorrect": False},
                 {"text": f"מסיח שלישי {i}", "isCorrect": False}]}
            for i in range(n_items)]}

    def test_it_records_where_the_key_was(self):
        log = []
        qc_fix.rebalance_keys(self.quiz(8), "study-001-x", log=log)
        moved = [c for c in log if c.get("fixed")]
        self.assertTrue(moved, "nothing was rebalanced")
        for c in moved:
            self.assertIn("was_position", c)
            self.assertEqual(c["was_position"], 0,
                             "the release had the key on A; the log must say so")
        # A balanced plan legitimately leaves some keys on A, so "it moved" is
        # not the claim — "we know where it started" is.
        self.assertTrue(any(c["position"] != c["was_position"] for c in moved))

    def test_the_move_is_still_idempotent(self):
        once = qc_fix.rebalance_keys(self.quiz(8), "study-001-x")
        log = []
        twice = qc_fix.rebalance_keys(once, "study-001-x", log=log)
        self.assertEqual(once, twice)
        self.assertEqual([c for c in log if c.get("fixed")], [])

    def test_the_key_is_still_the_same_answer(self):
        out = qc_fix.rebalance_keys(self.quiz(8), "study-001-x")
        for item in out["items"]:
            keys = [o for o in item["answerOptions"] if o.get("isCorrect")]
            self.assertEqual(len(keys), 1)
            self.assertTrue(keys[0]["text"].startswith("התשובה הנכונה"))


if __name__ == "__main__":
    unittest.main()


class TheCitationSaysWhereToLook(unittest.TestCase):
    """"This page does not exist" is true and leaves him nowhere.

    70% of the family is one citation listing several pages for one book where a
    later one is out of range — `(StahlNeuro p.80, p.843)`. Naming the number
    that IS valid is the note: he keeps p.80 and asks about p.843, instead of
    losing both.
    """

    def cites(self, *specs):
        return [{"book": b, "printed": p, "raw": raw, "section": None,
                 "unit": "G:1", "span": [0, 1], "ambiguous": False}
                for b, p, raw in specs]

    def details(self, cites):
        flags, skipped = {}, []
        qc_lint._lint_citations({"G:1": cites}, flags, skipped)
        self.assertEqual(skipped, [], "the books were not available")
        return [f["detail"] for v in flags.values() for f in v]

    def test_a_valid_page_in_the_same_citation_is_named(self):
        got = self.details(self.cites(
            ("StahlNeuro", 80, "StahlNeuro p.80, p.843"),
            ("StahlNeuro", 843, "StahlNeuro p.80, p.843")))
        self.assertEqual(len(got), 1, got)
        self.assertIn("843", got[0])
        self.assertIn("עמ' 80 כן תקין", got[0])

    def test_a_book_cited_in_the_same_unit_that_has_the_page(self):
        got = self.details(self.cites(
            ("StahlNeuro", 666, "StahlNeuro p. 666"),
            ("Dulcan", 300, "Dulcan p. 300")))
        self.assertEqual(len(got), 1, got)
        self.assertIn("Dulcan", got[0])
        self.assertIn("שמצוטט באותה יחידה", got[0])

    def test_otherwise_it_says_which_book_does_have_it(self):
        got = self.details(self.cites(("StahlNeuro", 642, "StahlNeuro p. 642")))
        self.assertEqual(len(got), 1, got)
        self.assertIn("יש טקסט ב", got[0])

    def test_a_page_in_no_book_at_all_says_so(self):
        got = self.details(self.cites(("StahlKids", 1184, "StahlKids p.1184")))
        self.assertEqual(len(got), 1, got)
        self.assertIn("אין עמוד כזה באף ספר", got[0])

    def test_a_valid_citation_is_not_flagged(self):
        self.assertEqual(self.details(self.cites(
            ("StahlNeuro", 80, "StahlNeuro p.80"))), [])
