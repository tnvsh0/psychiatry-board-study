#!/usr/bin/env python3
"""Adversarial review, 2026-09-22: a FAILED judgment is cached forever.

qc_run._judge_one caches every judgment it gets back under
`_qc/<tag>/judgments/round<N>-<call>.json` and returns the cached file on the
next run without asking the judge:

    if cached.exists():
        return Judgment.from_json(...)

The cache is written for a judgment that did NOT verify as well as for one that
did. A canary failure, a parse failure or a truncated reply — the transient agy
failures the circuit breaker of §5.5 exists for, and the ones the owner is told
to recover from by re-running the backlog later — are therefore pinned to disk.
Every later run replays the failure, `judge_all` breaks at that call, and the
chapter stays `unjudged` for good. Nothing in the CLI clears it; the only cure
is deleting the directory by hand, which nothing tells the owner to do.

The two tests below use the same fakes as tests/qc/test_qc_run.py and never
touch agy, gh or the network.

    python -m unittest tests.qc.test_review_adversarial_judgments
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_run                                              # noqa: E402
import qc_store                                            # noqa: E402
from test_qc_run import (                                  # noqa: E402
    FakeGh, FakeHygiene, FakeJudge, FakeSources, FakeTier1, Forbidden,
    Recorder, TAG, release_assets,
)

# A call that came back without a verified canary. This is the shape §5.8 gives
# the failure, and the one the 2026-09-18 incident produced for three calls in
# a row before agy recovered on its own.
CANARY_FAILED = {"ok": False, "reason": "canary failed: code not returned"}


class FailedJudgmentIsCached(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-review-judgments-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "_qc"
        self.gh = FakeGh()
        self.gh.add_release(TAG, release_assets())

    def deps(self, judge):
        return qc_run.Deps(
            gh=self.gh, sources=FakeSources(), judge=judge,
            hygiene=FakeHygiene(), tier1=FakeTier1(),
            tier2=Forbidden("tier 2"), revert=Forbidden("tier 2 revert"),
            notify=Recorder(), wait_for_network=lambda **k: True,
            cleanup=lambda cid, workspace=None: 0)

    def opts(self):
        return qc_run.Options(tags=[TAG], root=self.root, use_books=False,
                              no_upload=True)

    def run_qc(self, judge):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = qc_run.run(self.opts(), self.deps(judge))
        return code, buf.getvalue()

    def status(self):
        return (qc_store.read_json(self.root / TAG / "verdict.json") or {}).get(
            "status")

    def test_a_transient_judge_failure_is_retried_on_the_next_run(self):
        """A chapter whose judge failed once must be judgeable again.

        Today the second run asks the judge nothing at all and the chapter is
        `unjudged` forever.
        """
        first = FakeJudge(responses={"A": CANARY_FAILED})
        self.run_qc(first)
        self.assertEqual(first.calls, ["A"])
        self.assertEqual(self.status(), "unjudged")

        second = FakeJudge()                    # agy is healthy again
        self.run_qc(second)
        self.assertTrue(
            second.calls,
            "the second run reused the cached FAILED judgment and never asked "
            "the judge; _qc/<tag>/judgments/round0-A.json pins the chapter at "
            "unjudged for every future run")
        self.assertNotEqual(
            self.status(), "unjudged",
            "a chapter whose only problem was a transient canary failure can "
            "never be judged again without deleting _qc/<tag>/judgments/")

    def test_a_verified_judgment_is_still_reused(self):
        """The half of the cache that is right: a call that VERIFIED is never
        paid for twice. This test must keep passing after the fix."""
        first = FakeJudge()
        self.run_qc(first)
        self.assertEqual(first.calls, ["A", "B"])
        self.assertNotEqual(self.status(), "unjudged")

        second = FakeJudge()
        self.run_qc(second)
        self.assertEqual(second.calls, [],
                         "a verified judgment must be reused, not re-asked")


if __name__ == "__main__":
    unittest.main()
