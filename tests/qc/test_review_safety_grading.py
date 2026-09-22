#!/usr/bin/env python3
"""Adversarial review, lens SAFETY: app/quiz_logic.py's source-backed override.

`grade()` scores a wrong answer CORRECT when `_matches_source()` says the
textbook quote supports it. The test is substring containment or "the option's
numbers all appear in the quote plus one shared word ≥ 4 letters" — neither
knows what the quote says ABOUT those words, so a quote that negates the option,
or that gives its number a different role, licenses the override.

This only fires on an item the QC run marked disputed, which is the whole point:
the mechanism exists to protect the learner from a bad key, and here it teaches
him the wrong answer instead.

Both tests FAIL on the code as reviewed.

  python -m unittest tests.qc.test_review_safety_grading
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "app"))

import quiz_logic                                          # noqa: E402


def item(options, key_index, quote, *, book="StahlNeuro", page=402):
    """A released quiz item whose key qc_fix has marked disputed."""
    return {
        "question": "Which statement is correct?",
        "options": list(options),
        "correct_answer_index": key_index,
        "qc": {
            "kind": "content", "status": "active", "severity": "high",
            "date": "2026-09-22",
            "was": options[key_index],
            "source": {"book": book, "printed_page": page, "quote": quote},
        },
    }


class NegationTests(unittest.TestCase):
    def test_an_option_the_quote_negates_is_scored_correct(self):
        quote = ("Antidepressant monotherapy is not recommended as first-line "
                 "treatment in bipolar depression.")
        it = item(["Mood stabiliser first",
                   "Recommended as first-line treatment in bipolar depression"],
                  0, quote)
        norm_item = quiz_logic.normalize_item(it)
        res = quiz_logic.grade(norm_item, 1)
        self.assertFalse(
            res["correct"],
            "the learner picked the option the textbook explicitly rules out "
            "and was told he was right: " + res["feedback"][:80])

    def test_matches_source_reads_containment_and_not_meaning(self):
        """The same thing at the unit that decides it."""
        mark = quiz_logic.qc_mark(
            {"qc": {"kind": "content", "status": "active",
                    "source": {"book": "StahlNeuro", "printed_page": 402,
                               "quote": "Antidepressant monotherapy is not "
                                        "recommended as first-line treatment "
                                        "in bipolar depression."}}})
        self.assertFalse(
            quiz_logic._matches_source(
                "Recommended as first-line treatment in bipolar depression",
                mark))


class NumberRoleTests(unittest.TestCase):
    def test_a_number_in_the_wrong_role_is_scored_correct(self):
        """Rule 2 needs the option's numbers to be IN the quote and one shared
        word. A quote that states two doses licenses either of them in either
        role — which for a dose question is the difference that matters."""
        quote = ("The maximum dose is 100 mg/day; the usual starting dose is "
                 "40 mg/day.")
        it = item(["The maximum dose is 100 mg/day",
                   "The maximum dose is 40 mg/day"], 0, quote,
                  book="StahlKids", page=141)
        norm_item = quiz_logic.normalize_item(it)
        res = quiz_logic.grade(norm_item, 1)
        self.assertFalse(
            res["correct"],
            "40 mg/day was accepted as the maximum because 40 appears "
            "somewhere in the quote")

    def test_the_case_the_override_exists_for_still_works(self):
        """The control: the 019 atomoxetine case §1 names must keep passing,
        so the finding is about narrowing the rule, not removing it."""
        quote = ("Full therapeutic benefits of atomoxetine may take 2-8 weeks "
                 "to be seen.")
        it = item(["8-12 weeks", "2-8 weeks"], 0, quote,
                  book="StahlKids", page=69)
        res = quiz_logic.grade(quiz_logic.normalize_item(it), 1)
        self.assertTrue(res["correct"])
        self.assertTrue(res.get("source_backed"))


if __name__ == "__main__":
    unittest.main()
