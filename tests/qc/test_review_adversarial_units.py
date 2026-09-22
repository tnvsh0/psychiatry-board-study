#!/usr/bin/env python3
"""Adversarial review, 2026-09-22: a malformed asset escapes the UnitError path.

qc_units promises one thing about bad input: it raises `UnitError`, "never
swallowed", so that qc_lint can report `asset_unparsable` (blocking → the
chapter becomes `needs_regeneration`) instead of the asset looking like a
chapter with no items. qc_lint.chapter_units() implements exactly that, and it
catches `UnitError` and nothing else.

A quiz item whose `question` is not a string slips past it: `_quiz_units` hands
the value straight to `_sha1`, which raises `AttributeError` from inside
`hashlib`. That exception crosses chapter_units' except clause untouched, so
`lint_chapter` itself raises and qc_run's per-chapter handler prints a
traceback — the chapter ends with no verdict and no `asset_unparsable` finding,
which is the one state the fail-closed design has a name for.

Not observed in the 149 sampled released items (every one has a string stem);
this is the robustness boundary of the contract, not a live incident. The fix
is one `isinstance` check where the stem is read.

    python -m unittest tests.qc.test_review_adversarial_units
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_lint                                             # noqa: E402
import qc_units                                            # noqa: E402

BAD_QUIZ = json.dumps({"questions": [
    {"question": {"text": "מהו מנגנון הפעולה?"},          # a dict, not a string
     "hint": "SERT",
     "answerOptions": [{"text": "חסימת SERT", "isCorrect": True},
                       {"text": "חסימת NET", "isCorrect": False}]}]},
    ensure_ascii=False)


class MalformedAssetIsClassified(unittest.TestCase):

    def test_a_non_string_stem_raises_UnitError(self):
        with self.assertRaises(qc_units.UnitError):
            qc_units.units_from_asset("quiz.json", BAD_QUIZ)

    def test_lint_reports_it_instead_of_crashing(self):
        """§5.4's `asset_unparsable` is blocking, and blocking is what sends a
        chapter to `needs_regeneration` rather than to a traceback."""
        res = qc_lint.lint_chapter("study-test", {"quiz.json": BAD_QUIZ},
                                   use_books=False)
        self.assertIn("asset_unparsable", res["counts"])
        self.assertIn("asset_unparsable", res["blocking"])


if __name__ == "__main__":
    unittest.main()
