#!/usr/bin/env python3
"""Adversarial review, lens FAIL-CLOSED: what happens after something failed.

Failing closed is only half a mechanism — the other half is getting back out of
the closed state. Two ways this one does not:

  1. `_judge_one` caches EVERY judgment, including one that did not verify, and
     reads the cache before asking the judge. A chapter that failed its canary
     once answers `unjudged` from disk for ever; no invocation of qc_run can
     re-judge it.
  2. The §5.5 circuit breaker counts verdicts. A chapter that raises instead of
     reaching a verdict is not counted, so a judge that crashes every time is
     never stopped.

Both tests FAIL on the code as reviewed.

  python -m unittest tests.qc.test_review_failclosed_retry
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_run                                              # noqa: E402
from test_qc_run import TAG, TAGS, FakeJudge, RunCase, release_assets  # noqa: E402


class CachedFailureTests(RunCase):
    """A canary failure is a reason to try again later, not a permanent state.
    §5.5 journals it so the NEXT run picks it up."""

    def test_a_failed_call_is_never_re_judged(self):
        bad = FakeJudge(default={"ok": False,
                                 "reason": "canary: code K7QX2M not returned"})
        self.run_qc(self.opts(), self.deps(judge=bad))
        self.assertEqual(self.verdict()["status"], "unjudged")

        # The judge is fixed (a new agy version, the attachment bug gone) and
        # the owner runs the chapter again. Nothing else changed.
        good = FakeJudge()
        self.run_qc(self.opts(), self.deps(judge=good))

        self.assertTrue(
            good.calls,
            "the second run never asked the judge: _qc/<tag>/judgments/ still "
            "holds the FAILED answer and _judge_one returns it from disk")
        self.assertNotEqual(
            self.verdict()["status"], "unjudged",
            "the chapter is pinned to unjudged until someone deletes "
            "_qc/<tag>/judgments/ by hand")


class BreakerOnCrashTests(RunCase):
    """§5.5: three chapters in a row with no useful answer stop the run."""

    def setUp(self):
        super().setUp()
        for tag in TAGS:
            self.gh.add_release(tag, release_assets())

    def test_a_judge_that_raises_is_not_counted_by_the_breaker(self):
        class Exploding:
            def __init__(self):
                self.calls = 0

            def __call__(self, **kw):
                self.calls += 1
                # What a broken judge really does: agy answered, qc_judge blew
                # up reading the answer. The agy call is already paid for.
                raise RuntimeError("json parse failed: unterminated string")

        judge = Exploding()
        code, out = self.run_qc(self.opts(tags=list(TAGS)),
                                self.deps(judge=judge))

        self.assertEqual(
            code, qc_run.EXIT_STOP,
            f"the run walked through all {len(TAGS)} chapters "
            f"({judge.calls} judge calls) without the breaker firing; "
            f"exit={code}")


if __name__ == "__main__":
    unittest.main()
