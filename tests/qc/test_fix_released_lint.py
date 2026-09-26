#!/usr/bin/env python3
"""What is published, and what still needs work, are two measurements.

Measured over the 144 released snapshots on 2026-09-26. The post-hygiene lint —
the only one the run recorded — reported 34 hygiene_latex flags in 18 chapters,
no NotebookLM markers, no chatbot sign-offs and no skewed answer keys. The same
checks on the bytes as published report 925 LaTeX contaminations in 126 chapters,
117 markers in 8, 24 sign-offs in 24 and 53 skewed keys.

Every one of those had been repaired in memory before the check ran. Under
--dry-run or --no-upload the repair is never published, so the run declared clean
the chapters whose published material was not.

Inverting the order is the wrong fix and broke 12 tests that each record why the
judge must read cleaned text (§5.9). Recording BOTH is the right one.

    python -m unittest tests.qc.test_fix_released_lint
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_lint                                                # noqa: E402

# A guide as released: an escaped-LaTeX span and a NotebookLM marker list.
DIRTY = ("# מדריך\n\n## 1. מבוא\n"
         "הקולטן \(\alpha_{2A}\) מווסת קשב (Dulcan p. 176) [2, 3].\n"
         + "\n".join(f"- פרט {i} (DSM p.{100 + i})" for i in range(14)))


class TheReleasedBytesAreMeasuredToo(unittest.TestCase):

    def lint(self, text):
        return qc_lint.lint_chapter(
            "study-001-x",
            {"study_guide.md": text, "facts.json": "{}", "quiz.json": "{}",
             "flashcards.json": "{}"},
            use_books=False)

    def test_the_released_bytes_show_their_contamination(self):
        counts = self.lint(DIRTY).get("counts") or {}
        self.assertGreaterEqual(counts.get("hygiene_latex", 0), 1)
        self.assertGreaterEqual(counts.get("hygiene_marker", 0), 1)

    def test_the_same_text_cleaned_shows_nothing(self):
        """Why one measurement hid the other: this is what the run recorded."""
        import qc_fix
        cleaned = qc_fix.clean_text(DIRTY) if hasattr(qc_fix, "clean_text") else None
        if cleaned is None:
            self.skipTest("qc_fix exposes no single-text cleaner")
        counts = self.lint(cleaned).get("counts") or {}
        self.assertNotIn("hygiene_marker", counts)

    def test_the_offline_pass_needs_no_books(self):
        """It runs on every chapter, so it must not depend on the PDFs."""
        res = self.lint(DIRTY)
        skipped = {s.get("check") for s in res.get("skipped") or []}
        self.assertIn("impossible_citation", skipped)
        self.assertIn("value_unsourced", skipped)

    def test_the_runner_keeps_both(self):
        import qc_run
        src = Path(qc_run.__file__).read_text(encoding="utf-8")
        self.assertIn("self.released_lint", src)
        self.assertIn("lint_released.json", src)
        # and the released pass must come BEFORE hygiene replaces the assets
        i_rel = src.index("self.released_lint = qc_lint.lint_chapter")
        i_hyg = src.index("self.hygiene = self._run_hygiene()")
        self.assertLess(i_rel, i_hyg,
                        "the released-bytes lint must run before the repair")


if __name__ == "__main__":
    unittest.main()
