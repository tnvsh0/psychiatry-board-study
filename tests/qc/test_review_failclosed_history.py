#!/usr/bin/env python3
"""Adversarial review, lens FAIL-CLOSED: the owner's review history.

`_qc/<tag>/fixes.json` is the only record that the owner looked at a correction
and said no. qc_revert writes `status: withdrawn` into it; qc_run rewrites the
whole file from the fixes of the run that is happening now. These tests show
the two meeting.

Both tests FAIL on the code as reviewed.

  python -m unittest tests.qc.test_review_failclosed_history
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_revert                                           # noqa: E402
import qc_run                                              # noqa: E402
import qc_store                                            # noqa: E402
from test_qc_run import FINDING, TAG, FakeJudge, RunCase    # noqa: E402


class WithdrawnErratumTests(RunCase):
    """§5.9's `withdrawn` is a decision, not a cache entry."""

    def judge(self):
        return FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})

    def first_pass(self):
        """One chapter judged, one verified contradiction, one Tier-1 note."""
        code, _ = self.run_qc(self.opts(), self.deps(judge=self.judge()))
        self.assertEqual(code, qc_run.EXIT_OK)
        doc = qc_store.read_json(self.root / TAG / "fixes.json")
        self.assertEqual([f["id"] for f in doc["fixes"]], ["A-1"])
        return doc

    def withdraw(self):
        """The owner takes the correction back, through the real script."""
        p = qc_revert.plan(TAG, withdraw_all=True, gh=self.gh, root=self.root)
        qc_revert.apply_plan(p, gh=self.gh, root=self.root)
        doc = qc_store.read_json(self.root / TAG / "fixes.json")
        self.assertEqual(doc["fixes"][0]["status"], "withdrawn")
        return doc

    def test_a_second_run_erases_the_withdrawal(self):
        self.first_pass()
        self.withdraw()
        # The same chapter comes round again: a resumed backlog, a re-run after
        # a fix elsewhere, or `--all` a second time. The cached judgment means
        # the judge is not even asked.
        self.run_qc(self.opts(), self.deps(judge=self.judge()))

        doc = qc_store.read_json(self.root / TAG / "fixes.json")
        statuses = {f["id"]: f.get("status") for f in doc["fixes"]}
        self.assertEqual(
            statuses.get("A-1"), "withdrawn",
            f"the withdrawal is gone from fixes.json: {statuses}")

    def test_a_second_run_re_embeds_the_correction_the_owner_withdrew(self):
        self.first_pass()
        self.withdraw()
        tier1_before = len(self.tier1.calls)

        self.run_qc(self.opts(), self.deps(judge=self.judge()))

        applied = [f["id"] for call in self.tier1.calls[tier1_before:]
                   for f in call]
        self.assertNotIn(
            "A-1", applied,
            "the correction the owner withdrew was applied to the material "
            "again, and nothing in the pipeline reads `status: withdrawn`")


if __name__ == "__main__":
    unittest.main()
