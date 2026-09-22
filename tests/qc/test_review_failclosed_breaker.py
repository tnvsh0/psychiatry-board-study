#!/usr/bin/env python3
"""Adversarial review, lens FAIL-CLOSED: the STOP that does not stop.

qc_run's circuit breaker exists for exactly one measured event. Its own
docstring says so (scripts/qc_run.py:83-87):

    "Measured on 2026-09-18: when agy stopped attaching files it failed EVERY
     call the same way, so the fourth attempt would have been the 68th wasted
     one. Stop at three and say which probe to run."

The reason string agy_attach produces for that event is written at
scripts/agy_attach.py:816 —

    res.diagnostic = f"not ingested (no sha256 match under .tempmediaStorage): {unread}"

— and the breaker's filter is scripts/qc_run.py:148:

    _BREAKER_REASON = re.compile(r"canary|nonce|parse|json|schema", re.I)

"not ingested" matches none of those five words. Neither does the tool-audit
rejection (agy_attach.py:823) nor a coverage rejection, which arrives as
`rejected: coverage: N units have no row` (agy_attach.py:835 wrapping
qc_judge.make_validator). So the three ways a call can fail its MECHANICAL
proof — the bundle never reached the model, the model did something other than
read the two files, the model answered about a fraction of the units — all walk
the whole backlog: 68 chapters, three calls each, three attempts each, at ~25
minutes of quota per attempt, with every chapter coming back `unjudged`.

Only the JSON-shape failures stop the run, and those are the ones a person
would notice from the first chapter anyway.

Every test here FAILS on the code as reviewed.

  python -m unittest tests.qc.test_review_failclosed_breaker
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
from test_qc_run import TAGS, FakeJudge, RunCase, release_assets  # noqa: E402

# The three diagnostics agy_attach really writes when a call fails a mechanical
# proof, copied from the f-strings that produce them.
NOT_INGESTED = ("not ingested (no sha256 match under .tempmediaStorage): "
                "['bundle.pdf']")
TOOL_AUDIT = ("tool audit failed: run_command is not allowed; view_file of "
              "C:\\Users\\User outside the workspace")
COVERAGE = ("rejected: coverage: 29 units have no row (Q3, Q4, Q5, Q6) "
            "(+2 more)")


class BreakerMissesTheMechanicalProofs(RunCase):
    """§5.5: three consecutive chapters with no useful answer → STOP + ntfy."""

    def setUp(self):
        super().setUp()
        for tag in TAGS:
            self.gh.add_release(tag, release_assets())

    def _walk(self, reason):
        judge = FakeJudge(default={"ok": False, "reason": reason})
        code, out = self.run_qc(self.opts(tags=list(TAGS)),
                                self.deps(judge=judge))
        return code, judge, out

    def test_a_bundle_that_never_reached_the_model_does_not_stop_the_run(self):
        """The 2026-09-18 incident itself, replayed across four chapters."""
        code, judge, _ = self._walk(NOT_INGESTED)

        # Originally this asserted a verdict for all four tags AND the STOP,
        # which cannot both hold: §5.5 stops AT the third consecutive failure,
        # so the fourth chapter is never started — that is the whole point of a
        # breaker. The contract is: every chapter it DID judge is unjudged, it
        # judged exactly CIRCUIT_BREAKER_N of them, and it stopped.
        judged = [t for t in TAGS if self.verdict(t) is not None]
        self.assertEqual(len(judged), qc_run.CIRCUIT_BREAKER_N,
                         f"the breaker should stop at chapter "
                         f"{qc_run.CIRCUIT_BREAKER_N}, judged {judged}")
        self.assertTrue(all(self.verdict(t)["status"] == "unjudged"
                            for t in judged),
                        "every chapter it judged should be unjudged here")
        self.assertEqual(
            code, qc_run.EXIT_STOP,
            f"the run judged all {len(TAGS)} chapters "
            f"({len(judge.calls)} calls) with the bundle never attached and "
            f"exited {code}; _BREAKER_REASON does not match 'not ingested', "
            f"so the breaker built for this exact failure never fires")

    def test_a_forbidden_tool_call_does_not_stop_the_run(self):
        code, judge, _ = self._walk(TOOL_AUDIT)
        self.assertEqual(
            code, qc_run.EXIT_STOP,
            f"exit={code}: a judge running run_command on every chapter is "
            f"not counted by the breaker ({len(judge.calls)} calls made)")

    def test_a_judge_answering_about_a_fraction_of_the_units_does_not_stop(self):
        code, judge, _ = self._walk(COVERAGE)
        self.assertEqual(
            code, qc_run.EXIT_STOP,
            f"exit={code}: qc_judge's rejection reasons start 'coverage:', "
            f"which _BREAKER_REASON does not list ({len(judge.calls)} calls)")

    def test_the_shape_failure_it_does_catch_still_works(self):
        """The control: the breaker is not broken outright, only blind to the
        proofs that matter most."""
        code, _, _ = self._walk("no JSON envelope (rc=1): b''")
        self.assertEqual(code, qc_run.EXIT_STOP)


class BreakerReasonIsTheContract(unittest.TestCase):
    """Stated at the unit, without the orchestrator, so the fix is one regex."""

    def test_the_diagnostics_agy_attach_writes_are_all_matched(self):
        missed = [r for r in (NOT_INGESTED, TOOL_AUDIT, COVERAGE)
                  if not qc_run._BREAKER_REASON.search(r)]
        self.assertEqual(
            missed, [],
            "qc_run._BREAKER_REASON matches none of these, so none of them "
            "counts toward the 3-chapter STOP")


if __name__ == "__main__":
    unittest.main()
