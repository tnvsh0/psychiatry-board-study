#!/usr/bin/env python3
"""NotebookLM's new flashcard format must reach the learner as text.

Around 2026-10-01 NotebookLM began returning a card side as
`{"flashcardContentBlock": [{"type": "text", "content": "…"}]}`. Every set made
since — 14 chapters, five in week 1 — was published that way, and three things
broke at once:

  * the app handed the dict to the page as the question;
  * quiz_logic._text skipped it as a non-string, so item_id hashed "" and a whole
    chapter's cards shared ONE id in the spaced-repetition progress;
  * qc_units crashed on it, which is how it was found — building a calibration
    set on 2026-10-06.

    python -m unittest tests.qc.test_fix_flashcard_format
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "app"))

import qc_units                                                   # noqa: E402
import quiz_logic as ql                                           # noqa: E402


def block(*texts, kind="text"):
    return {"flashcardContentBlock": [{"type": kind, "content": t} for t in texts]}


NEW = {"cards": [
    {"front": block("מהו מנגנון הפעולה של Methylphenidate?"),
     "back": block("חוסם DAT ו-NET.", "(StahlKids p.69)")},
    {"front": block("מהו הטווח לטיטרציה?"), "back": block("שבוע עד שבועיים.")},
]}
OLD = {"cards": [{"front": "שאלה ישנה", "back": "תשובה ישנה"}]}


class ThePipelineWritesText(unittest.TestCase):

    def test_both_sides_become_the_join_of_their_text_blocks(self):
        import copy
        data, n = qc_units.flatten_flashcards(copy.deepcopy(NEW))
        self.assertEqual(n, 4)
        self.assertEqual(data["cards"][0]["back"], "חוסם DAT ו-NET.\n(StahlKids p.69)")
        self.assertEqual(data["cards"][1]["front"], "מהו הטווח לטיטרציה?")

    def test_the_old_format_is_left_alone(self):
        import copy
        data, n = qc_units.flatten_flashcards(copy.deepcopy(OLD))
        self.assertEqual(n, 0)
        self.assertEqual(data, OLD)

    def test_a_block_that_is_not_text_is_refused_not_dropped(self):
        bad = {"cards": [{"front": block("q"), "back": block("img", kind="image")}]}
        with self.assertRaises(qc_units.UnitError):
            qc_units.flatten_flashcards(bad)

    def test_qc_units_reads_the_new_format_instead_of_crashing(self):
        import json
        units = qc_units.units({"flashcards.json": json.dumps(NEW, ensure_ascii=False)})
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].meta["front"], "מהו מנגנון הפעולה של Methylphenidate?")


class TheAppShowsText(unittest.TestCase):

    def test_side_text_reads_both_formats(self):
        self.assertEqual(ql.side_text("plain"), "plain")
        self.assertEqual(ql.side_text(block("a", "b")), "a\nb")
        self.assertEqual(ql.side_text({"other": 1}), "")
        self.assertEqual(ql.side_text(None), "")

    def test_every_card_keeps_its_own_id(self):
        """The bug that would have merged a chapter into one SRS card."""
        ids = {ql.item_id(c) for c in NEW["cards"]}
        self.assertEqual(len(ids), len(NEW["cards"]))

    def test_an_old_card_keeps_the_id_it_always_had(self):
        import hashlib
        card = OLD["cards"][0]
        self.assertEqual(ql.item_id(card),
                         hashlib.sha1("שאלה ישנה".encode("utf-8")).hexdigest()[:16])


if __name__ == "__main__":
    unittest.main()
