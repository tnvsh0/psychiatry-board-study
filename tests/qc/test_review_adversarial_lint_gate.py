#!/usr/bin/env python3
"""Adversarial review, 2026-09-22: a chapter is `approved` on checks that never ran.

qc_lint records what it could not run in `result["skipped"]` and its author
wrote the reason down: "Never read a clean result without reading `skipped`:
with no `Text BOOKS/` the two book-backed checks do not run, and the whole
point of recording that is so a chapter cannot look approved because a PDF was
absent." qc_run copies `skipped` into verdict.json — and then
`chapter_status()` decides on `lint["blocking"]` alone. A chapter linted
without the PDFs (a moved `Text BOOKS/`, a `--no-books` run) has had NO
citation check and NO locked-value check, and comes back `approved`.

The chapter below is not a construction: its guide sentence, citation and all,
is the opening paragraph of the released study-029-ssris-deep-dive guide, which
cites `StahlNeuro p. 289, 902` in a book whose last printed page is 624. Six of
the 24 sampled chapters (016, 017, 018, 029, 036, 038) carry at least one such
citation, so this is the ordinary case, not the corner.

The first test needs no PDFs. The second corroborates it against the real books
and is skipped when they are not on the machine.

    python -m unittest tests.qc.test_review_adversarial_lint_gate
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_books                                            # noqa: E402
import qc_lint                                             # noqa: E402
import qc_run                                              # noqa: E402

TAG = "study-029-ssris-deep-dive"

# The first paragraph of the released 029 guide, verbatim, plus a second
# section so the guide has the shape qc_units expects.
GUIDE = """# SSRIs — deep dive

## 1. מבוא

הגדרתן ההיסטורית של תרופות אלו כ"נוגדות דיכאון" (antidepressants) היא מצמצמת \
ואינה משקפת נאמנה את מנגנוני הפעולה שלהן (StahlNeuro p. 289, 902).

## 2. יעילות קלינית

התרופות משמשות גם בהפרעה טורדנית-כפייתית (Dulcan p. 730).
"""
FACTS = json.dumps({"drug_class": {"name": "SSRI",
                                   "onset": "ההשפעה מופיעה תוך 2-8 שבועות"}},
                   ensure_ascii=False, indent=1)
QUIZ = json.dumps({"questions": [
    {"type": "multiple_choice", "question": "מהו מנגנון הפעולה העיקרי של SSRI?",
     "hint": "SERT",
     "answerOptions": [
         {"text": "חסימת SERT", "isCorrect": True, "rationale": "זהו המנגנון."},
         {"text": "חסימת NET", "isCorrect": False,
          "rationale": "זהו מנגנון של SNRI."}]},
]}, ensure_ascii=False, indent=1)
CARDS = json.dumps({"cards": [
    {"front": "מהו SERT?", "back": "מוביל הסרוטונין הפרה-סינפטי."}]},
    ensure_ascii=False, indent=1)

ASSETS = {"study_guide.md": GUIDE, "facts.json": FACTS,
          "quiz.json": QUIZ, "flashcards.json": CARDS}


def books_available() -> bool:
    try:
        return qc_books.book("StahlKids").has_page(69)
    except Exception:                                      # noqa: BLE001
        return False


needs_books = unittest.skipUnless(books_available(),
                                  "Text BOOKS/ not on this machine")


def status(lint: dict) -> str:
    return qc_run.chapter_status(judged=True, lint=lint, fixes=[],
                                 owner_items=[], unverified_high=0)


class SkippedChecksAreNotCleanChecks(unittest.TestCase):

    def test_a_chapter_linted_without_the_books_is_not_approved(self):
        lint = qc_lint.lint_chapter(TAG, ASSETS, use_books=False)
        self.assertEqual(lint["blocking"], [])
        self.assertIn("impossible_citation",
                      [s["check"] for s in lint["skipped"]])
        self.assertNotEqual(
            status(lint), "approved",
            "the citation and locked-value checks never ran, and the chapter "
            "was approved anyway; chapter_status() reads lint['blocking'] and "
            "never looks at lint['skipped']")

    @needs_books
    def test_the_same_chapter_with_the_books_is_held(self):
        """What the skipped check would have found: StahlNeuro has 624 printed
        pages and the guide cites p. 902."""
        lint = qc_lint.lint_chapter(TAG, ASSETS, use_books=True)
        self.assertIn("impossible_citation", lint["blocking"])
        self.assertEqual(status(lint), "needs_owner")


if __name__ == "__main__":
    unittest.main()
