#!/usr/bin/env python3
"""Adversarial review, 2026-09-22: the app's norm() is not qc_norm's.

`app/quiz_logic.norm` is a deliberate copy of `qc_norm.norm` (the Cloud Run
image cannot carry PyMuPDF and the PDFs), and its docstring names the one rule
that is expensive to get wrong: "a run of punctuation BETWEEN TWO DIGITS
collapses to a single boundary instead of vanishing, so '2-8 weeks' never
normalizes onto '28 weeks'".

The copy loses that rule whenever the run between the two digits contains an
underscore, because it is written `(?<=\\d)[^\\w]+(?=\\d)` and `_` is a word
character. LaTeX subscripts put an underscore next to a digit throughout this
material: `$\\alpha_4, \\alpha_6$` is read by the judge as `4|6` and by the app
as `46`. The string below is not invented — it is a flashcard back in
tests/qc/fixtures/quiz/study-042-…/flashcards.json, copied from the released
chapter.

Consequence: the app and the judge disagree about what a value IS, on the
comparison that decides whether a learner's answer is what the textbook says
(`_matches_source` compares `_values(norm(option))` against `_values(norm(quote))`)
and on every other place the two sides are supposed to see the same string.

    python -m unittest tests.qc.test_review_adversarial_norm_parity
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

import qc_norm                                             # noqa: E402
import quiz_logic                                          # noqa: E402

FIXTURES = HERE / "fixtures"
RELEASED = [
    FIXTURES / "quiz" / "study-042-benzodiazepines-buspirone-sleep-aids-overview"
    / "quiz.json",
    FIXTURES / "quiz" / "study-042-benzodiazepines-buspirone-sleep-aids-overview"
    / "flashcards.json",
    FIXTURES / "release_assets" / "quiz.json",
]

# study-042, flashcards.json — a released card back, verbatim.
SUBSCRIPTS = ('אלו רצפטורים המכילים תת-יחידות $\\alpha_4, \\alpha_6$ או '
              '$\\delta$, והם מגיבים ל-Neuroactive steroids ולא ל-BZD '
              '(StahlNeuro p.262).')


def strings(node, out):
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            strings(v, out)
    elif isinstance(node, list):
        for v in node:
            strings(v, out)
    return out


class NormParity(unittest.TestCase):

    def test_the_digit_boundary_survives_a_latex_subscript(self):
        self.assertIn("4|6", qc_norm.norm(SUBSCRIPTS))      # the judge's view
        self.assertIn(
            "4|6", quiz_logic.norm(SUBSCRIPTS),
            "the app collapsed two subunit numbers into one value; the "
            "underscore of `\\alpha_4` is a \\w character, so the "
            "(?<=\\d)[^\\w]+(?=\\d) boundary rule never fires")

    def test_the_two_norms_agree_on_the_released_fixtures(self):
        seen = 0
        bad = []
        for path in RELEASED:
            for s in strings(json.loads(path.read_text(encoding="utf-8")), []):
                seen += 1
                if qc_norm.norm(s) != quiz_logic.norm(s):
                    bad.append((path.name, s[:80]))
        self.assertTrue(seen > 100, "the fixtures did not load")
        self.assertEqual(
            bad, [],
            "app/quiz_logic.norm and qc_norm.norm disagree on released "
            "material; the judge verifies a quote against one form and the app "
            "grades the learner against another")


if __name__ == "__main__":
    unittest.main()
