#!/usr/bin/env python3
"""Adversarial review, lens SAFETY: textbook text the ignore file does not cover.

.gitignore ends with

    # QC: textbook text and per-call work files never go in git
    _qc/cache/
    _qc/work/
    _qc/src/

Those three are right and `git check-ignore` confirms them. The fourth place the
mechanism writes verbatim textbook text is not in the list.

`_qc/<tag>/judgments/round<N>-<call>.json` is written by
qc_run._judge_one (qc_run.py:900) through `Judgment.to_json()`, whose
`findings` and `coverage` fields are the judge's answer as it arrived. §5.8's
schema (qc_judge.py:243-253) makes BOTH of them carry verbatim book text:

    "coverage": [{"unit": ..., "echo": ..., "evidence_code": ...,
                  "source_quote": "<verbatim from that page>"}]

— one quote per unit, ~40-60 units per chapter, three calls per chapter, 68
chapters. That is a few thousand verbatim passages of six copyrighted
textbooks, written into a directory that sits beside the files §5.10 says to
commit (`verdict.json`, `fixes.json`, `_qc/orig/`, `REPORT.md`, `state.json`)
and that nothing excludes. One `git add _qc/` or `git add -A` — and the repo's
own status at the time of this review shows 60+ untracked files, so `-A` is how
this tree is worked — puts them in history permanently.

`_qc/<tag>/lint.json` has the same exposure by a narrower route: qc_lint's
`value_unsourced` detail names the book and page a value was found on.

The fix is one line in .gitignore (`_qc/*/judgments/`, or an allow-list form),
not a change to any script. Both tests FAIL as things stand.

  python -m unittest tests.qc.test_review_safety_gitignore
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_judge                                            # noqa: E402


def ignored(rel: str) -> bool:
    """True when git would leave this path out of `git add -A`."""
    p = subprocess.run(["git", "check-ignore", "-q", rel],
                       cwd=REPO, capture_output=True)
    return p.returncode == 0


class TextbookQuotesAreNotExcluded(unittest.TestCase):

    def test_the_schema_really_asks_for_verbatim_book_text_per_unit(self):
        """The premise, read off the prompt the judge is actually sent."""
        schema = qc_judge._SCHEMA if hasattr(qc_judge, "_SCHEMA") else ""
        blob = schema or Path(qc_judge.__file__).read_text(encoding="utf-8")
        self.assertIn('"source_quote": "<verbatim from that page>"', blob)

    def test_the_cached_judgments_are_gitignored(self):
        self.assertTrue(
            ignored("_qc/study-019-adhd-treatment-non-stimulants/judgments/"
                    "round0-A.json"),
            "_qc/<tag>/judgments/ holds one verbatim textbook quote per unit "
            "per call and is not ignored; `git add -A` commits it")

    def test_the_lint_report_is_gitignored_or_named_in_the_commit_list(self):
        self.assertTrue(
            ignored("_qc/study-019-adhd-treatment-non-stimulants/lint.json"),
            "_qc/<tag>/lint.json carries value_unsourced details naming book "
            "pages; §5.10's explicit-path commit list does not include it and "
            "nothing ignores it")

    def test_control_the_three_paths_the_ignore_file_does_name(self):
        """PASSES. The ignore file is right about what it covers."""
        for rel in ("_qc/cache/books/StahlKids.json",
                    "_qc/work/study-019/round0/A/bundle.pdf",
                    "_qc/src/anything.txt"):
            self.assertTrue(ignored(rel), rel)


if __name__ == "__main__":
    unittest.main()
