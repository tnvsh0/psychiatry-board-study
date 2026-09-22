#!/usr/bin/env python3
"""What the round-2 fixes must keep true, beyond the reviewers' own cases.

Offline: no books, no agy, no gh, no network. The reviewer tests
(test_review_bughunt_values, _bundle, _empty, test_review_safety_grading) are
the acceptance criteria; these pin the edges the fixes could have broken while
satisfying them.

    python -m unittest tests.qc.test_fix_review_round2
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "app"))

import qc_lint                                              # noqa: E402
import qc_norm                                              # noqa: E402
import qc_sources                                           # noqa: E402
import qc_units                                             # noqa: E402
import quiz_logic                                           # noqa: E402


class PerDayFoldsOnlyOnAPreposition(unittest.TestCase):
    """qc_norm.locked_values — the "per" that is a word, not a slash."""

    def test_the_two_spellings_are_one_value(self):
        for said, book in [('40 מ"ג ליום', "40 mg/day"),
                           ('10 מ"ג ביום', "10 mg/day"),
                           ("2,400 mg ליום", "2,400 mg/day"),
                           ('1.4 מ"ג לק"ג ליום', "1.4 mg/kg/day"),
                           ("2 mg per kg per day", "2 mg/kg/day")]:
            with self.subTest(said=said):
                self.assertEqual(qc_norm.locked_values(said)[0]["value"],
                                 qc_norm.locked_values(book)[0]["value"])

    def test_a_bare_duration_word_after_a_dose_is_not_a_per(self):
        """036: "$25\\ mg$ יום כן יום לא" is every OTHER day. Folding a bare
        `יום` into the unit would turn it into 25 mg/day — a dose the book
        never states, and one that would then "contradict" the real one."""
        self.assertEqual(
            qc_norm.locked_values("מתן מינון התחלתי של $25\\ mg$ יום כן יום לא")
            [0]["value"], (25, "mg"))

    def test_a_hebrew_word_that_merely_starts_with_bet_is_not_a_per(self):
        self.assertEqual(qc_norm.locked_values('5 מ"ג בלילה')[0]["value"],
                         (5, "mg"))

    def test_a_duration_never_swallows_a_per(self):
        # `8 שבועות ליום` is not a unit, and a duration that folded one would
        # compare against a bucket no book ever writes.
        self.assertEqual(qc_norm.locked_values("8 שבועות ליום")[0]["value"],
                         (8, "weeks"))

    def test_the_raw_still_slices_the_input(self):
        s = 'מינון של 40 מ"ג ליום (StahlKids p.69).'
        v = qc_norm.locked_values(s)[0]
        self.assertIn(v["raw"], s)
        self.assertEqual(v["raw"], '40 מ"ג ליום')


class SynopsisPagesAreKeyedByTheirSlice(unittest.TestCase):
    """qc_sources — a page number is relative to the FILE that holds it."""

    def test_two_slices_do_not_collide(self):
        pages = {}
        for name in ("synopsis-5-schizophrenia.pdf", "synopsis-33-neuro.pdf"):
            for i in range(3):
                qc_sources._merge(pages, qc_sources._page(
                    "Synopsis", i + 1, [i], "synopsis", slice_ref="9",
                    slice_file=name, slice_path=f"x/{name}"))
        self.assertEqual(len(pages), 6)
        self.assertEqual(qc_sources._cost(pages), 6)
        self.assertEqual(sorted({p["slice_file"] for p in pages.values()}),
                         ["synopsis-33-neuro.pdf", "synopsis-5-schizophrenia.pdf"])

    def test_a_book_page_is_still_keyed_on_the_book(self):
        pages = {}
        qc_sources._merge(pages, qc_sources._page("Dulcan", 330, [412], "cited"))
        qc_sources._merge(pages, qc_sources._page("Dulcan", 330, [412], "guide"))
        self.assertEqual(list(pages), [("Dulcan", 330)])
        self.assertEqual(pages[("Dulcan", 330)]["reasons"], ["cited", "guide"])

    def test_the_book_name_the_stamp_and_the_index_read_is_untouched(self):
        page = qc_sources._page("Synopsis", 1, [0], "synopsis", slice_ref="9",
                                slice_file="synopsis-9.pdf")
        self.assertEqual(page["book"], "Synopsis")
        self.assertIn("Synopsis ch.9", qc_sources._stamp_text(page, "AB12CD"))


class AnEmptyRequiredAssetIsBlocking(unittest.TestCase):
    """qc_lint — present, parses, and holds nothing a judge could read."""

    HEALTHY = {
        "study_guide.md": "# מדריך\n\n## 1. מבוא\nטקסט על Atomoxetine.\n",
        "facts.json": json.dumps({"drug": {"name": "Atomoxetine"}},
                                 ensure_ascii=False),
        "quiz.json": json.dumps(
            {"questions": [{"question": "מהו מנגנון הפעולה?",
                            "answerOptions": [
                                {"text": "עיכוב NET", "isCorrect": True},
                                {"text": "עיכוב DAT", "isCorrect": False}]}]},
            ensure_ascii=False),
        "flashcards.json": json.dumps(
            {"cards": [{"front": "מה מעכב?", "back": "את NET."}]},
            ensure_ascii=False),
        "audio.mp3": None, "video.mp4": None, "infographic.png": None,
    }

    def test_empty_asset_is_in_the_blocking_list(self):
        self.assertTrue(qc_lint.CHECKS["empty_asset"]["blocking"])
        res = qc_lint.lint_chapter(
            "t", dict(self.HEALTHY) | {"quiz.json": "[]"}, use_books=False)
        self.assertIn("empty_asset", res["blocking"])
        self.assertEqual([f["detail"] for f in res["chapter"]
                          if f["check"] == "empty_asset"], ["quiz.json"])

    def test_a_missing_asset_is_not_also_reported_empty(self):
        assets = {k: v for k, v in self.HEALTHY.items() if k != "quiz.json"}
        res = qc_lint.lint_chapter("t", assets, use_books=False)
        checks = [f["check"] for f in res["chapter"]]
        self.assertIn("missing_asset", checks)
        self.assertNotIn("empty_asset", checks)

    def test_a_binary_asset_is_never_empty(self):
        """audio.mp3 maps to None and yields no units by design."""
        res = qc_lint.lint_chapter("t", dict(self.HEALTHY), use_books=False)
        self.assertNotIn("empty_asset", res["counts"])


class LegendRowsStillCarryTheirNumber(unittest.TestCase):
    """qc_units — the rename that cleared _check_scope changed no output."""

    def test_a_numbered_legend_keeps_its_uids_and_row_meta(self):
        units = qc_units.units_from_asset(
            "sketchy_main.txt",
            "כותרת\n\n1. סימן [Eng]: טענה\n2. סימן אחר: טענה\n"
            "מספרים לבחינה: 5 · 12\n")
        self.assertEqual([u.uid for u in units],
                         ["L:sketchy_main.txt:0", "L:sketchy_main.txt:1",
                          "L:sketchy_main.txt:2", "L:sketchy_main.txt:numbers"])
        self.assertEqual([u.meta["row"] for u in units[:3]], ["0", "1", "2"])


class TheOverrideNeedsTheClauseAndNotJustTheWords(unittest.TestCase):
    """quiz_logic._matches_source — narrowed, not removed."""

    def mark(self, quote):
        return quiz_logic.qc_mark(
            {"qc": {"kind": "content", "status": "active",
                    "source": {"book": "StahlKids", "printed_page": 69,
                               "quote": quote}}})

    def test_the_019_case_still_passes(self):
        self.assertTrue(quiz_logic._matches_source(
            "2-8 weeks",
            self.mark("full therapeutic benefits may take 2-8 weeks")))

    def test_a_negation_after_the_match_does_not_refuse(self):
        """"…is 2-8 weeks, not 8-12" supports the option; the marker has to
        stand BEFORE the words matched, or every corrected quote is refused."""
        self.assertTrue(quiz_logic._matches_source(
            "2-8 weeks",
            self.mark("onset takes 2-8 weeks and not 8-12 weeks")))

    def test_a_value_in_another_clause_role_is_refused(self):
        self.assertFalse(quiz_logic._matches_source(
            "The maximum dose is 40 mg/day",
            self.mark("The maximum dose is 100 mg/day; the usual starting "
                      "dose is 40 mg/day.")))

    def test_the_value_with_its_own_role_is_accepted(self):
        self.assertTrue(quiz_logic._matches_source(
            "The maximum dose is 100 mg/day",
            self.mark("The maximum dose is 100 mg/day; the usual starting "
                      "dose is 40 mg/day.")))

    def test_an_explicit_supports_field_is_the_whole_answer(self):
        mark = quiz_logic.qc_mark(
            {"qc": {"kind": "content", "status": "active",
                    "supports": ["8-12 weeks"],
                    "source": {"book": "StahlKids", "printed_page": 69,
                               "quote": "benefits may take 2-8 weeks"}}})
        self.assertEqual(mark["supports"], ["8-12 weeks"])
        self.assertTrue(quiz_logic._matches_source("8-12 weeks", mark))
        # ...and the quote no longer decides anything, in either direction.
        self.assertFalse(quiz_logic._matches_source("2-8 weeks", mark))

    def test_norm_is_unchanged_by_the_clause_refactor(self):
        """norm() now goes through _clean(); its output is the contract every
        other match in the app and the judge's echo verification rest on, so it
        has to be character-for-character what it was."""
        for s, want in [("2-8 weeks", "2|8weeks"),
                        ("28 weeks", "28weeks"),
                        ("1.4 mg/kg/day", "1|4mgkgday"),
                        ("**12–15 חודשים**", "12|15חודשים"),
                        ("$5\\text{-HT}_{2C}$", "5ht2c"),
                        ("2,400 mg", "2|400mg"),
                        ("", "")]:
            with self.subTest(s=s):
                self.assertEqual(quiz_logic.norm(s), want)


if __name__ == "__main__":
    unittest.main()
