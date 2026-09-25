#!/usr/bin/env python3
"""A run that throws away a good guide is a run that had one.

Chapter 78 was refused a release twice — "missing study_guide.md" — while a
complete 26,513-char guide sat in `_generated/day-078/`, defect-free against
the project's own validator. `ask_study_guide` returning False set the caller's
path to None, and the release guard reported the file it had stopped looking at
as missing. NotebookLM had answered with a studio announcement on that run and
with the real guide on an earlier one.

The guide is kept only when it passes `_study_guide_defects`: the same test the
new answers failed, which is strictly stronger than the existence check the
release guard does.

    python -m unittest tests.qc.test_fix_guide_kept
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import daily_study as D                                      # noqa: E402

GOOD = REPO / "_generated" / "day-078" / "study_guide.md"


class KeepTheEarlierGuide(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, text):
        p = self.dir / "study_guide.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_no_file_is_not_a_guide(self):
        self.assertFalse(D._guide_kept_from_earlier_attempt(
            self.dir / "study_guide.md"))
        self.assertFalse(D._guide_kept_from_earlier_attempt(None))

    def test_an_empty_file_is_not_a_guide(self):
        self.assertFalse(D._guide_kept_from_earlier_attempt(self.write("")))

    def test_a_studio_receipt_on_disk_is_not_a_guide(self):
        """The exact failure mode the caller's `return False` protects."""
        receipt = ("הכנתי עבורך מדריך לימוד מלא והוא זמין בפאנל הסטודיו. "
                   "המדריך מכיל את כל הקריטריונים והטבלאות. " * 40)
        self.assertTrue(D._study_guide_defects(receipt))
        self.assertFalse(D._guide_kept_from_earlier_attempt(self.write(receipt)))

    def test_a_short_file_is_not_a_guide(self):
        self.assertFalse(D._guide_kept_from_earlier_attempt(
            self.write("# מדריך\n(DSM p.399)\n" * 3)))

    @unittest.skipUnless(GOOD.exists(), "chapter 78's guide is not on disk")
    def test_the_guide_chapter_78_was_refused_a_release_over_is_kept(self):
        text = GOOD.read_text(encoding="utf-8")
        self.assertEqual(D._study_guide_defects(text), [])
        self.assertTrue(D._guide_kept_from_earlier_attempt(self.write(text)))


if __name__ == "__main__":
    unittest.main()
