#!/usr/bin/env python3
"""Proof for the qc_run fixes of the 2026-09-23 adversarial review.

Offline, on the fakes of tests/qc/test_qc_run.py: no GitHub, no agy, no ntfy.
Each class states the contract the fix has to keep — not the shape of the bug it
came from — so that a later change to the orchestrator has to break a rule of
the spec before it can break one of these.

    python -m unittest tests.qc.test_fix_qc_run
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

import qc_run                                              # noqa: E402
import qc_store                                            # noqa: E402
import quiz_logic                                          # noqa: E402
from test_qc_run import (FINDING, FakeJudge, FakeTier2,    # noqa: E402
                         Forbidden, RunCase, TAG, TAGS, release_assets)

# The 019 atomoxetine case of §1: two verified quotes, two printed pages.
CONFLICT = dict(FINDING, id="A-9", kind="source_conflict",
                source_quote="full therapeutic benefits may take 8-12 weeks",
                printed=74, second_book="StahlKids", second_printed=69,
                second_quote="full therapeutic benefits may take 2-8 weeks",
                correction="")

# The diagnostic agy_attach writes when the bundle never reached the model
# (agy_attach.py, `not ingested`) — the measured 2026-09-18 incident.
NOT_INGESTED = ("not ingested (no sha256 match under .tempmediaStorage): "
                "['bundle.pdf']")


class AConflictIsMarkedAndQueuedButNeverRewritten(RunCase):
    """§5.9 Tier 1 "always ... For source_conflict, both quotes"; §5.8.3 sends
    the same finding to the owner and never lets it be auto-fixed."""

    def setUp(self):
        super().setUp()
        self.books_available()
        qc_store.write_json_atomic(qc_store.state_path(root=self.root),
                                   {"runs": {}, "tier2_enabled": True})

    def test_the_conflict_is_marked_and_tier2_never_sees_it(self):
        tier2 = FakeTier2()
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [CONFLICT]}})
        code, _ = self.run_qc(self.opts(),
                              self.deps(judge=judge, tier2=tier2,
                                        revert=Forbidden("tier 2 revert")))
        self.assertEqual(code, qc_run.EXIT_OK)
        self.assertEqual([f["id"] for call in self.tier1.calls for f in call],
                         ["A-9"], "Tier 1 is unconditional (§5.9)")
        self.assertEqual(tier2.calls, [],
                         "§5.8.3: a source_conflict is never auto-fixed")
        v = self.verdict()
        self.assertEqual([f["id"] for f in v["findings"]["owner"]], ["A-9"],
                         "and the decision is still the owner's (§5.8.3)")
        self.assertEqual(v["status"], "needs_owner")
        self.assertEqual(v["rejudge_rounds"], 0,
                         "a mark changes no studied content, so it is owed no "
                         "re-judge (§5.9)")

    def test_the_learner_is_shown_both_quotes(self):
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [CONFLICT]}})
        self.run_qc(self.opts(), self.deps(judge=judge))
        doc = qc_store.read_json(self.root / TAG / "qc.json")
        erratum = quiz_logic.parse_qc_json(doc)["errata"][0]
        self.assertEqual(len(erratum["sources"]), 2)
        self.assertEqual(sorted(s["page"] for s in erratum["sources"]), [69, 74])
        item = json.loads(
            (self.root / "work" / TAG / "out" / "quiz.json").read_bytes()
        )["questions"][0]
        self.assertTrue(quiz_logic.qc_mark(item)["conflict"])


class AHalfFinishedSwapIsFinishedByTheNextRun(RunCase):
    """§5.5's swap is five stages, and the machine can stop inside it.

    The release is then left with `<name>.pre-qc-<date>` and `<name>.qc-<sha8>`
    and NO `<name>`, which the app 404s for every learner. qc_store ships the
    recovery; this is the wiring that reaches it.
    """

    ASSET = "flashcards.json"          # the first asset `upload()` swaps

    def test_the_asset_comes_back_and_nothing_is_swapped_twice(self):
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})
        # The original is renamed aside and the answer is lost right there.
        self.gh.fail_after["rename_asset"] = 1
        # The process really stops: the laptop slept, so there is no in-run
        # second attempt to hide the half-finished state.
        code, _ = self.run_qc(
            self.opts(no_upload=False),
            self.deps(judge=judge, wait_for_network=lambda **k: False))
        self.assertEqual(code, qc_run.EXIT_ERROR)
        names = self.gh.names(TAG)
        self.assertNotIn(self.ASSET, names, "premise: the swap stopped inside")
        self.assertTrue([n for n in names
                         if n.startswith(f"{self.ASSET}.pre-qc-")])
        self.assertEqual(qc_store.Journal(TAG, root=self.root)
                         .pending_uploads(), [self.ASSET])

        self.gh.fail_after.clear()
        code, _ = self.run_qc(self.opts(no_upload=False),
                              self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_OK)
        self.assertIn(self.ASSET, self.gh.names(TAG),
                      "the next run finished the swap")
        self.assertEqual(qc_store.Journal(TAG, root=self.root)
                         .pending_uploads(), [])
        v = self.verdict()
        self.assertTrue(any("העלאה שנקטעה" in str(n) for n in v["notes"]),
                        f"the repair is not in the report: {v['notes']}")
        # And the finished swap is not mistaken for a stale release: the second
        # run compares this pass's bytes to what is now published, so it does
        # not try to swap the same asset a second time.
        pre = [n for n in self.gh.names(TAG)
               if n.startswith(f"{self.ASSET}.pre-qc-")]
        self.assertEqual(len(pre), 1)


class TheBreakerSeesEveryFailedProof(RunCase):
    """§5.5 + §5.8.1: a bundle that never reached the model is the failure the
    breaker was written for."""

    def setUp(self):
        super().setUp()
        for tag in TAGS:
            self.gh.add_release(tag, release_assets())

    def test_three_chapters_of_it_stop_at_the_third(self):
        judge = FakeJudge(default={"ok": False, "reason": NOT_INGESTED})
        code, out = self.run_qc(self.opts(tags=list(TAGS)),
                                self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_STOP)
        judged = [t for t in TAGS if (self.root / t / "verdict.json").exists()]
        self.assertEqual(judged, TAGS[:qc_run.CIRCUIT_BREAKER_N],
                         "the fourth chapter must never be started: that is "
                         "the 25 minutes of quota per attempt the STOP saves")
        self.assertTrue(all(self.verdict(t)["status"] == "unjudged"
                            for t in judged))
        self.assertIn("STOP", out)
        stop = [n for n in self.notify.sent if n["priority"] >= 5]
        self.assertEqual(len(stop), 1)
        self.assertIn("--probe", stop[0]["message"],
                      "the alert names something the owner can actually run")

    def test_a_machine_readable_reason_code_counts_on_its_own(self):
        """The contract qc_judge can be given instead of prose."""
        verdict = {"status": "unjudged", "notes": [],
                   "judgments": [{"call_id": "A", "ok": False, "reason": "",
                                  "reason_code": "ingestion"}]}
        self.assertEqual(qc_run._breaker_count(verdict, 0), 1)


class AJudgmentIsNotBelievedAgainstItsOwnProofs(RunCase):
    """§2.1: "a judge must prove it read the sources and the material"."""

    def test_a_judgment_that_reports_its_own_canary_failed_is_refused(self):
        call = qc_run.PlannedCall(call_id="A", kind="A", units=["Q1"])
        j = qc_run._as_judgment(
            {"ok": True, "canary": {"requested": [["StahlKids", 69]],
                                    "returned": [], "verified": False}}, call)
        self.assertFalse(j.ok)
        self.assertIn("canary", j.reason)

    def test_an_unreported_canary_is_left_to_the_judge_module(self):
        """qc_judge runs the check itself; `ok` is its answer, and there is
        nothing here to compare it against."""
        call = qc_run.PlannedCall(call_id="A", kind="A", units=["Q1"])
        self.assertTrue(qc_run._as_judgment({"ok": True}, call).ok)

    def test_the_ingestion_proof_reaches_the_verdict(self):
        self.books_available()
        judge = FakeJudge(default={"ok": True, "tempmedia_ok": True,
                                   "tool_audit": ["view_file"]})
        self.run_qc(self.opts(), self.deps(judge=judge))
        agy = self.verdict()["agy"]
        self.assertEqual(agy["tempmedia_ok"], {"A": True, "B": True})
        self.assertEqual(agy["tool_audit"]["A"], ["view_file"])

    def test_a_tampered_cache_is_re_checked_on_the_resume(self):
        self.books_available()
        self.run_qc(self.opts(), self.deps(judge=FakeJudge()))
        for p in sorted((self.root / TAG / "judgments").glob("*.json")):
            doc = qc_store.read_json(p)
            doc["canary"] = {"requested": [["StahlKids", 69]], "returned": [],
                             "verified": False}
            qc_store.write_json_atomic(p, doc)
        again = FakeJudge()
        self.run_qc(self.opts(), self.deps(judge=again))
        self.assertEqual(again.calls, [], "premise: the cache was reused")
        self.assertEqual(self.verdict()["status"], "unjudged")


class TheAgyVersionIsPoliced(RunCase):
    """§4: "the agy version is recorded on every call; if it changes mid-run,
    run the atomoxetine known-answer probe ... and stop if it fails"."""

    class Versioned(FakeJudge):
        def __init__(self, by_call):
            super().__init__()
            self.by_call = by_call

        def __call__(self, *, tag, call, plan, **kw):
            out = super().__call__(tag=tag, call=call, plan=plan, **kw)
            out["agy_version"] = self.by_call.get(plan.call_id, "1.2.6")
            return out

    def test_every_version_that_answered_is_recorded(self):
        self.books_available()
        self.run_qc(self.opts(),
                    self.deps(judge=self.Versioned({"B": "1.4.0"})))
        v = self.verdict()
        self.assertEqual(v["agy"]["versions"], ["1.2.6", "1.4.0"])
        self.assertEqual(v["agy"]["version"], "1.2.6")
        self.assertNotEqual(v["status"], "approved")
        self.assertTrue(any("1.4.0" in str(n) for n in v["notes"]))

    def test_one_binary_all_through_is_not_held(self):
        self.books_available()
        code, _ = self.run_qc(self.opts(), self.deps(judge=FakeJudge()))
        self.assertEqual(code, qc_run.EXIT_OK)
        self.assertEqual(self.verdict()["status"], "approved")
        self.assertEqual(
            qc_store.load_state(root=self.root).get("agy_version"), "1.2.6",
            "the version the backlog is being judged by is remembered across "
            "runs: §4's 'mid-run' is the whole pass, not one invocation")

    def test_a_probe_that_cannot_run_is_a_failed_probe(self):
        res = qc_run.known_answer_probe(
            qc_run.Deps(probe=qc_run._Lazy("qc_module_that_does_not_exist",
                                           ("probe",), "known-answer probe")))
        self.assertFalse(res["ok"])
        self.assertIn("probe", res["detail"])

    def test_a_probe_that_does_not_quote_the_page_fails(self):
        res = qc_run.known_answer_probe(
            qc_run.Deps(probe=lambda **kw: {"ok": True,
                                            "quote": "onset is immediate"}))
        self.assertFalse(res["ok"])
        self.assertIn("2-8 weeks", res["detail"])

    def test_the_probe_passes_when_the_page_comes_back(self):
        res = qc_run.known_answer_probe(qc_run.Deps(probe=lambda **kw: {
            "ok": True,
            "quote": "full therapeutic benefits may take 2-8 weeks"}))
        self.assertTrue(res["ok"], res["detail"])


class ASkippedCheckIsVisibleToTheApp(RunCase):
    """§2.3: a chapter must not "look clean because a file was absent"."""

    def test_the_asset_line_says_the_citation_check_never_ran(self):
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})
        self.run_qc(self.opts(), self.deps(judge=judge))   # use_books=False
        doc = qc_store.read_json(self.root / TAG / "qc.json")
        self.assertIn("impossible_citation", doc["skipped"])
        qc = quiz_logic.parse_qc_json(doc)
        lines = quiz_logic.asset_lines(qc)
        self.assertTrue(any("קוויז" in ln and "לא נבדק" in ln for ln in lines),
                        f"the app still claims a completed check: {lines}")


if __name__ == "__main__":
    unittest.main()
