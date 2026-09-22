#!/usr/bin/env python3
"""Adversarial bug hunt — an asset that is present but holds nothing.

Offline: the qc_run fakes of tests/qc/test_qc_run.py, plus qc_units/qc_lint
called directly. No agy, no gh, no network.

THE DEFECT. Every gate in the mechanism is expressed over UNITS. An asset that
parses but yields no units is therefore indistinguishable from a healthy one:

  * `qc_units.units_from_asset("quiz.json", "[]")` -> `[]`, no UnitError.
  * `qc_lint.lint_chapter` reports nothing: `_lint_completeness` only checks
    that the NAME is on the release, and every per-item check is keyed on a uid
    that does not exist.
  * `qc_run.uncovered_units()` is empty, so the coverage hold never fires, and
    the chapter reaches `approved`.
  * `qc_store.snapshot` accepts it too: `_verify_snapshot_file` asks for
    non-zero bytes and valid JSON, and `[]` is both.

So a truncated or failed generation that published `[]` (or `{"questions": []}`,
or a whitespace-only study_guide.md) is certified "נבדק מול ספרי הלימוד"
with a badge that says every asset was checked, while the learner opens a
chapter with no quiz at all. §2.3 asks the opposite: an asset nobody could
check must not look like an asset that passed.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_lint                                             # noqa: E402
import qc_run                                              # noqa: E402
import qc_units                                            # noqa: E402
from test_qc_run import RunCase, TAG, release_assets       # noqa: E402

EMPTY = {
    "quiz.json": "[]",
    "quiz.json (object form)": '{"title": "t", "questions": []}',
    "flashcards.json": '{"title": "t", "cards": []}',
    "study_guide.md": "\n \n\n",
}


# A minimal but complete and healthy chapter, so the only thing wrong with the
# linted chapter below is the one asset each subtest empties.
HEALTHY = {
    "study_guide.md": "# מדריך\n\n## 1. מבוא\nטקסט על Atomoxetine.\n",
    "facts.json": json.dumps({"drug": {"name": "Atomoxetine"}},
                             ensure_ascii=False),
    "quiz.json": json.dumps(
        {"questions": [{"question": "מהו מנגנון הפעולה של Atomoxetine?",
                        "answerOptions": [
                            {"text": "עיכוב NET", "isCorrect": True},
                            {"text": "עיכוב DAT", "isCorrect": False}]}]},
        ensure_ascii=False),
    "flashcards.json": json.dumps(
        {"cards": [{"front": "מה מעכב Atomoxetine?", "back": "את NET."}]},
        ensure_ascii=False),
    "audio.mp3": None, "video.mp4": None, "infographic.png": None,
}


class AnEmptyAssetHasNoUnits(unittest.TestCase):
    def test_lint_says_something_about_an_asset_with_no_units(self):
        base = qc_lint.lint_chapter("t", dict(HEALTHY), use_books=False)
        self.assertEqual(base["counts"], {},
                         "precondition: the healthy chapter lints clean")
        for label, body in EMPTY.items():
            name = label.split(" ")[0]
            with self.subTest(asset=label):
                self.assertEqual(
                    qc_units.units_from_asset(name, body), [],
                    "precondition: the asset really does yield no units")
                res = qc_lint.lint_chapter(
                    "t", dict(HEALTHY) | {name: body}, use_books=False)
                self.assertTrue(
                    res["counts"],
                    f"{label} holds no judgeable content and qc_lint reports "
                    f"nothing about it; a truncated asset is indistinguishable "
                    f"from a clean one")


class AnEmptyQuizStillGetsApproved(RunCase):
    def test_a_chapter_whose_quiz_is_an_empty_list_is_not_approved(self):
        self.books_available()
        self.gh.put(TAG, "quiz.json", b'{"title": "t", "questions": []}')
        code, _ = self.run_qc(self.opts(), self.deps())
        self.assertEqual(code, qc_run.EXIT_OK)
        v = self.verdict()
        # The quiz asset really is empty and really did produce nothing.
        self.assertEqual(
            json.loads(self.gh.data(TAG, "quiz.json"))["questions"], [])
        self.assertNotEqual(
            v["status"], "approved",
            "a chapter whose quiz.json holds no questions was approved and "
            "qc.json now tells the app the quiz was checked against the "
            "textbooks")


if __name__ == "__main__":
    unittest.main()
