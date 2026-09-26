#!/usr/bin/env python3
"""A content mark asserts a book comparison, so it needs a book.

Tested 2026-09-26 against the live parser: an impossible_citation flag relabelled
`kind: "content"` renders as "נמצאה סתירה מול ספר הלימוד" with an EMPTY source
list — it tells the learner a textbook disagrees with his material when what was
found is that a page number does not exist.

The free checks cannot reach qc_mark today: they carry no kind the app knows, so
they show nothing (that is also verified here, because it is the claim the owner
was given). This closes the door that renaming a kind would open.

    python -m unittest tests.qc.test_fix_sourceless_mark
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "app"))

import quiz_logic as ql                                      # noqa: E402

SOURCE = {"book": "StahlKids", "printed_page": 69,
          "quote": "full therapeutic benefits may take 2-8 weeks"}


class AContentMarkNeedsABook(unittest.TestCase):

    def test_a_judge_finding_with_a_quote_still_shows(self):
        m = ql.qc_mark({"qc": {"kind": "content", "status": "active",
                               "was": "8-12 weeks", "now": "2-8 weeks",
                               "source": SOURCE}})
        self.assertIsNotNone(m)
        self.assertEqual(m["sources"][0]["page"], 69)

    def test_a_content_record_with_no_source_shows_nothing(self):
        m = ql.qc_mark({"qc": {"kind": "content", "status": "active",
                               "was": "StahlNeuro p. 114, 654",
                               "now": "p.654 does not exist; p.114 does"}})
        self.assertIsNone(m, "it would claim a textbook contradiction with no "
                             "textbook behind it")

    def test_a_free_check_finding_cannot_reach_the_learner(self):
        """The claim the owner was given on 2026-09-25, kept true."""
        for kind in ("reference", "lint", "hygiene", "impossible_citation"):
            doc = {"errata": [{"kind": kind, "asset": "study_guide.md",
                               "unit": "G:3", "status": "active",
                               "was": "StahlNeuro p. 654", "now": "no such page"}]}
            parsed = ql.parse_qc_json(doc)
            self.assertEqual(parsed["errata"], [], kind)

    def test_the_errata_page_and_the_item_mark_still_agree(self):
        rec = {"kind": "content", "asset": "quiz.json", "unit": "Q1",
               "status": "active", "was": "a", "now": "b"}
        self.assertIsNone(ql.qc_mark({"qc": rec}))
        self.assertIsNone(ql.erratum_view(rec))


if __name__ == "__main__":
    unittest.main()
