#!/usr/bin/env python3
"""Spec-conformance review, lens SPEC CONFORMANCE (§5.8.3 -> §5.9 -> §5.11).

A VERIFIED `source_conflict` never reaches the learner.

§5.9 Tier 1 is "always, deterministic, no LLM: embed in the item, card or
legend row `qc: {...}` ... For `source_conflict`, both quotes."
§5.11 then says "A mark shows the verified quote and printed page (two for a
conflict ...)" and "A choice matching the source is not graded wrong against a
disputed key."
§1's motivating case IS a conflict: chapter 019 keys atomoxetine onset at
"8-12 weeks" citing StahlKids p.74 while p.69 says "2-8 weeks".

What the code does instead: qc_run.classify_findings (qc_run.py:600-606) sends
every `source_conflict` to `out["owner"]` and only `out["fix"]` (contradicted)
is handed to qc_fix.apply_tier1 (qc_run.py:1255). The owner list is written to
a local `_qc/owner_queue.json` that is never uploaded and that
app/quiz_logic.py never reads, and errata_lines builds qc.json out of
`verdict["fixes"]` only. So a conflict the judge verified, with both quotes on
both pages, is embedded in no asset, appears in no erratum, draws no mark and
does not protect the learner who picks the option the book supports.

qc_fix is ready for it — TIER1_KINDS = ('contradicted', 'source_conflict'),
qc_record writes `second_source`, quiz_logic.qc_mark sets conflict=True and the
headline "המקורות חלוקים זה עם זה". Nothing ever calls it with one.

    python -m unittest tests.qc.test_review_specconf_conflict_marks
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
sys.path.insert(0, str(HERE))

import qc_fix                                              # noqa: E402
import qc_run                                              # noqa: E402
import quiz_logic                                          # noqa: E402
from test_qc_run import (TAG, FakeJudge, RunCase)          # noqa: E402

# The 019 case of §1, as §5.8's schema returns it once BOTH quotes verified.
CONFLICT = {
    "id": "A-9", "unit": "Q1", "kind": "source_conflict", "severity": "high",
    "category": "locked_value",
    "said": "ההשפעה מופיעה תוך 4 שבועות",
    "evidence_code": "K7QX2M",
    "source_quote": "full therapeutic benefits may take 8-12 weeks",
    "book": "StahlKids", "printed": 74,
    "second_evidence_code": "P3RT9Z",
    "second_quote": "full therapeutic benefits may take 2-8 weeks",
    "second_book": "StahlKids", "second_printed": 69,
    "problem": "שני עמודים בספר נוקבים בטווחים שונים",
    "correction": "",
    "verified": True,
}


class VerifiedConflictReachesNobody(RunCase):
    """One chapter, one verified source_conflict, nothing else."""

    def setUp(self):
        super().setUp()
        self.books_available()
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [CONFLICT]}})
        code, _out = self.run_qc(self.opts(), self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_OK)
        self.v = self.verdict()
        owner = (self.v.get("findings") or {}).get("owner") or []
        self.assertTrue(
            any(f.get("id") == "A-9" for f in owner),
            "the conflict was not even verified in this fixture; the rest of "
            "this test would be meaningless")
        self.assertEqual(self.v["status"], "needs_owner")

    def test_tier1_is_never_asked_to_mark_the_conflict(self):
        """§5.9: Tier 1 is unconditional, and names both quotes."""
        handed = [f["id"] for call in self.tier1.calls for f in call]
        self.assertIn(
            "A-9", handed,
            "qc_run.classify_findings (qc_run.py:600-606) routes a verified "
            "source_conflict to the owner list and hands qc_fix.apply_tier1 "
            "only classified['fix'] (qc_run.py:1255), so the Tier-1 note §5.9 "
            "calls unconditional is never written. qc_fix.TIER1_KINDS already "
            "contains 'source_conflict' and qc_record already emits "
            "second_source — the wiring, not the fixer, is what is missing")

    def test_the_conflict_becomes_an_erratum_the_app_can_read(self):
        """§5.11: the "תיקונים" page lists the errata by chapter."""
        doc = json.loads((self.root / TAG / "qc.json").read_bytes())
        qc = quiz_logic.parse_qc_json(doc)
        ids = [e.get("id") for e in (qc or {}).get("errata", [])]
        self.assertIn(
            "A-9", ids,
            "qc_run.errata_lines reads verdict['fixes'] only "
            "(qc_run.py:1548), and a source_conflict is never in it, so the "
            "published qc.json carries no record of it. The learner's badge "
            f"says {qc['n_errata']} corrections on a chapter whose key the "
            "judge proved the book disputes")

    def test_the_item_carries_a_two_quote_mark(self):
        """§5.11: "A mark shows the verified quote and printed page (two for a
        conflict ...)"; §2.4 "mark, never hide"."""
        # The bytes this run would publish: _qc/work/<tag>/out/ holds every
        # asset the pass changed, and _qc/orig/ the pristine release.
        out = self.root / "work" / TAG / "out" / "quiz.json"
        src = out if out.exists() else self.root / "orig" / TAG / "quiz.json"
        item = json.loads(src.read_bytes())["questions"][0]
        mark = quiz_logic.qc_mark(item)
        self.assertIsNotNone(
            mark, "the released quiz item carries no qc record at all, so "
                  "quiz_logic.qc_mark returns None and the learner sees the "
                  "disputed key with nothing beside it")
        self.assertTrue(mark["conflict"])
        self.assertEqual(len(mark["sources"]), 2)


class Tier1CanAlreadyDoIt(unittest.TestCase):
    """The fixer is ready; only qc_run's routing keeps it from running. This
    test documents that the fix is one line in classify_findings, not new code
    in qc_fix."""

    def test_qc_fix_embeds_a_conflict_when_it_is_handed_one(self):
        self.assertIn("source_conflict", qc_fix.TIER1_KINDS)
        self.assertEqual(qc_fix._why_not_tier1(CONFLICT), "",
                         "qc_fix would accept this finding")
        rec = qc_fix.qc_record(CONFLICT, "2026-09-23")
        self.assertTrue(rec.get("second_source", {}).get("quote"),
                        "qc_record already writes both quotes")
        mark = quiz_logic.qc_mark({"qc": rec})
        self.assertTrue(mark["conflict"])
        self.assertEqual(len(mark["sources"]), 2)


if __name__ == "__main__":
    unittest.main()
