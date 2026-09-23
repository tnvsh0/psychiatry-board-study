#!/usr/bin/env python3
"""A printed page is not one sheet, and the canary must not pretend it is.

Calibration run, 2026-09-24: three of the first three judge calls were thrown
away, each after burning all three content attempts, on "canary: code for
Dulcan p.738 not returned". The judge had answered REZD64, which is the stamp
on bundle page 5 — one of the FOUR bundle pages (5, 6, 7, 8) that carry printed
page 738, because Dulcan is a reflowed ebook (§5.1). The check demanded the one
row canary_request happened to draw.

So: any stamp of the requested printed page is a correct answer; a stamp of
another page is not; and a code that is in no manifest at all is not.

    python -m unittest tests.qc.test_fix_canary_sheets
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_judge                                             # noqa: E402

# Four sheets of Dulcan p.738, one sheet of StahlNeuro p.169 — the shape the
# study-005 call actually had.
MANIFEST = {"pages": [
    {"bundle_page": 5, "book": "Dulcan", "printed": [738], "code": "REZD64"},
    {"bundle_page": 6, "book": "Dulcan", "printed": [738], "code": "NAVRTA"},
    {"bundle_page": 7, "book": "Dulcan", "printed": [738], "code": "97ZECM"},
    {"bundle_page": 8, "book": "Dulcan", "printed": [738, 739], "code": "W4AC9V"},
    {"bundle_page": 36, "book": "StahlNeuro", "printed": [169], "code": "P8U4WU"},
    {"bundle_page": 90, "book": "Dulcan", "printed": [901], "code": "ZZZZZZ"},
]}


def answer(*codes):
    return {"canary": [{"page": "p", "code": c} for c in codes]}


class AnySheetOfTheRequestedPage(unittest.TestCase):

    def test_the_sheet_we_drew(self):
        req = [("Dulcan", 738, "REZD64")]
        self.assertEqual(qc_judge.canary_problems(answer("REZD64"), req, MANIFEST), [])

    def test_another_sheet_of_the_same_printed_page(self):
        """The failure that cost three calibration calls."""
        req = [("Dulcan", 738, "NAVRTA")]
        self.assertEqual(qc_judge.canary_problems(answer("REZD64"), req, MANIFEST), [])

    def test_a_sheet_that_carries_the_page_among_others(self):
        req = [("Dulcan", 738, "REZD64")]
        self.assertEqual(qc_judge.canary_problems(answer("W4AC9V"), req, MANIFEST), [])

    def test_a_stamp_of_a_different_page_is_still_a_miss(self):
        req = [("Dulcan", 738, "REZD64")]
        self.assertTrue(qc_judge.canary_problems(answer("ZZZZZZ"), req, MANIFEST))

    def test_a_code_in_no_manifest_is_still_a_miss(self):
        req = [("Dulcan", 738, "REZD64")]
        self.assertTrue(qc_judge.canary_problems(answer("QQQQQQ"), req, MANIFEST))

    def test_one_page_answered_does_not_cover_the_others(self):
        req = [("Dulcan", 738, "REZD64"), ("StahlNeuro", 169, "P8U4WU")]
        problems = qc_judge.canary_problems(answer("NAVRTA"), req, MANIFEST)
        self.assertEqual(len(problems), 1)
        self.assertIn("StahlNeuro p.169", problems[0])

    def test_without_a_manifest_the_exact_code_is_still_required(self):
        req = [("Dulcan", 738, "REZD64")]
        self.assertEqual(qc_judge.canary_problems(answer("REZD64"), req, None), [])
        self.assertTrue(qc_judge.canary_problems(answer("NAVRTA"), req, None))


if __name__ == "__main__":
    unittest.main()
