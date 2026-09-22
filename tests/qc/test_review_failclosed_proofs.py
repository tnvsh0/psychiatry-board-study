#!/usr/bin/env python3
"""Adversarial review, lens FAIL-CLOSED: the proofs the orchestrator files but
never reads.

§2.1 is the principle the whole mechanism is built on — "a judge must prove it
read the sources and the material" — and §5.8.1 makes the canary and the
ingestion proof the two mechanical halves of it. Both are enforced inside
agy_attach/qc_judge, and both are then recorded in the judgment that qc_run
stores. qc_run never looks at either:

  * `Judgment.canary` is carried through `_as_judgment` (qc_run.py:517) into
    `verdict["canary"]` (qc_run.py:1475) and is read by nothing. Grep for
    `\\.canary` in scripts/qc_run.py: two assignments, no comparison.
  * `tempmedia_ok` — qc_judge writes it (qc_judge.py:1133) and agy_attach
    computes it from the sha256 of the bytes actually found under
    `brain/<cid>/.tempmediaStorage/` — is not among the fields `_as_judgment`
    reads at all, so it is absent from the verdict a resume or an audit reads.

The one gate is `ok`, a boolean the judge module sets about itself. That is
sound while qc_judge is the only judge and is never resumed — but `_judge_one`
(qc_run.py:886) reads a CACHED judgment straight off disk with
`Judgment.from_json` and re-validates nothing, so on every resume the gate is a
JSON field in `_qc/<tag>/judgments/` rather than a proof.

Both tests here FAIL on the code as reviewed. The fix is two assertions in
`_as_judgment`: refuse a judgment whose canary did not verify, and refuse one
whose ingestion proof is not True.

  python -m unittest tests.qc.test_review_failclosed_proofs
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
import qc_store                                            # noqa: E402
from test_qc_run import TAG, FakeJudge, RunCase            # noqa: E402


class CanaryIsNeverCheckedByTheOrchestrator(RunCase):

    def test_a_judgment_that_says_its_canary_failed_still_approves(self):
        """The judge reports, in the same object, that it did not prove it read
        the pages. qc_run stores the report and approves the chapter."""
        self.books_available()
        judge = FakeJudge(default={
            "ok": True,
            "canary": {"requested": [["StahlKids", 69], ["Dulcan", 330]],
                       "returned": [], "verified": False},
        })
        self.run_qc(self.opts(), self.deps(judge=judge))
        v = self.verdict()

        # The report really is there, and really says it failed.
        self.assertFalse(v["canary"]["A"]["verified"])
        self.assertEqual(v["canary"]["A"]["returned"], [])
        self.assertNotEqual(
            v["status"], "approved",
            "the chapter was approved on a judgment whose own canary record "
            "says no code came back; qc_run reads `ok` and never "
            "`canary.verified`")

    def test_control_the_coverage_gate_does_catch_a_tampered_cache(self):
        """PASSES. The only thing standing between a hand-edited
        `_qc/<tag>/judgments/*.json` and an approved chapter is the coverage
        gate — `ok` and `canary` are taken on trust, but the missing coverage
        rows are noticed. Recorded so the fix to the two failing tests above is
        not mistaken for this one."""
        self.books_available()
        judge = FakeJudge()
        self.run_qc(self.opts(), self.deps(judge=judge))

        cached = sorted((self.root / TAG / "judgments").glob("*.json"))
        self.assertTrue(cached, "the first run should have cached a judgment")
        # Whatever put these bytes here — a half-written resume, an older
        # schema, a hand edit — this is the whole proof the next run has.
        for p in cached:
            doc = qc_store.read_json(p)
            doc["canary"] = {"requested": [["StahlKids", 69]], "returned": [],
                             "verified": False}
            doc["coverage"] = []
            qc_store.write_json_atomic(p, doc)

        again = FakeJudge()
        self.run_qc(self.opts(), self.deps(judge=again))
        self.assertEqual(again.calls, [], "premise: the cache was reused")
        self.assertNotEqual(
            self.verdict()["status"], "approved",
            "a cached judgment with an empty canary and no coverage rows was "
            "re-read as a verified verdict")


class IngestionProofIsNotInTheVerdict(RunCase):

    def test_tempmedia_ok_false_still_approves(self):
        """The 2026-09-18 failure exactly: SUCCESS, well-formed JSON, and an
        empty .tempmediaStorage. agy_attach catches it; nothing downstream
        would, and the verdict does not even record it."""
        self.books_available()
        judge = FakeJudge(default={"ok": True, "tempmedia_ok": False,
                                   "tool_audit": [[], ["run_command"]]})
        self.run_qc(self.opts(), self.deps(judge=judge))
        v = self.verdict()

        self.assertIn(
            "tempmedia_ok", sorted(v["agy"]),
            f"verdict['agy'] is {sorted(v['agy'])} — no record of whether the "
            f"bundle was ingested or which tools ran, so an audit of "
            f"_qc/<tag>/verdict.json cannot tell a judged chapter from one "
            f"answered from memory")
        self.assertNotEqual(v["status"], "approved")


class AgyVersionIsRecordedAndNotPoliced(RunCase):
    """§4: "the agy version is recorded on every call; if it changes mid-run,
    run the atomoxetine known-answer probe (StahlKids p.69, "2-8 weeks") and
    stop if it fails."

    qc_run records it (qc_run.py:1464) and compares it with nothing. Grep
    `agy_version` in scripts/qc_run.py: a dataclass field, one read in
    `_as_judgment`, one write into the verdict. There is no probe anywhere in
    the module, and no notification when the version moves.
    """

    def test_the_version_changing_between_calls_is_not_noticed(self):
        self.books_available()

        class Upgrading:
            """agy auto-updated between the A and B calls of one chapter."""

            def __init__(self):
                self.seen = []
                self.inner = FakeJudge()

            def __call__(self, *, tag, call, plan, **kw):
                out = self.inner(tag=tag, call=call, plan=plan, **kw)
                out["agy_version"] = "1.2.6" if plan.call_id == "A" else "1.4.0"
                self.seen.append(out["agy_version"])
                return out

        judge = Upgrading()
        code, out = self.run_qc(self.opts(), self.deps(judge=judge))
        v = self.verdict()

        self.assertEqual(judge.seen, ["1.2.6", "1.4.0"], "premise")
        # The verdict keeps only the FIRST non-empty version, so even the
        # record §4 asks for is lost.
        self.assertEqual(v["agy"]["version"], "1.2.6")
        self.assertNotEqual(
            v["status"], "approved",
            "two calls of one chapter were answered by two different agy "
            "versions and the chapter was approved; no probe ran, no note was "
            "written, and verdict['agy']['version'] names only the first")
        self.assertTrue(
            any("1.4.0" in str(n) for n in v["notes"]),
            f"nothing in the verdict records the second version: {v['notes']}")


if __name__ == "__main__":
    unittest.main()
