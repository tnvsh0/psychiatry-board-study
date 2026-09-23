#!/usr/bin/env python3
"""Adversarial review of scripts/qc_calibrate.py — when GO may be printed.

§6 names four numbers and `verdict_line()` is the only place that decides GO.
GO is not a report line: it is the owner's authority to run the judge over 68
released chapters and, through §5.9, to let Tier 2 REWRITE his study material.
So the question these tests ask is never "is the metric pretty" but "could this
line say GO while a bar §6 names was not actually met".

Three ways it can today, each measured below on `verdict_line` itself:
  * the first-attempt canary rate drops the calls that never verified out of
    its own denominator, so 5 good calls out of 10 print 100%;
  * the contradicted-gold requirement is derived from the gold rows, so a gold
    set that degraded to `undecided` (no books on the machine, a page that
    stopped saying what §1 says it says) removes its own bar;
  * a chapter that blew up or a run still parked on a quota pause is reported
    and never gates.

Nothing here runs agy, gh or git.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_calibrate as Q                                   # noqa: E402
import qc_store                                            # noqa: E402


def passing_metrics(**kw) -> dict:
    """A score dict that clears every §6 bar, so each test moves one thing."""
    s = {"judged_chapters": ["t"],
         "recall": Q.rate(60, 60), "recall_any": Q.rate(60, 60),
         "recall_mutants": Q.rate(60, 60), "recall_gold": Q.rate(0, 0),
         "false_positives": Q.rate(0, 200),
         "false_positives_on_value": Q.rate(0, 200),
         "controls": {"overall": Q.rate(10, 10), "by_control": {},
                      "gates": ["verify"]},
         "canary_first_attempt": Q.rate(10, 10),
         "canary_after_retries": Q.rate(10, 10),
         "failed_chapters": {}, "paused": None,
         "by_artifact": {}, "by_unit_type": {}, "by_book": {},
         "by_mutation": {}, "mutants": [], "negatives": [], "gold": []}
    s.update(kw)
    return s


def a_set(**kw) -> dict:
    doc = {"built_at": "2026-09-23T00:00:00+00:00", "chapters": ["t"],
           "gold": [], "mutants": [], "negatives": [], "discarded": [],
           "controls": [], "bar": {}, "pool": 0}
    doc.update(kw)
    doc["counts"] = Q._set_counts(doc)
    return doc


# ---------------------------------------------------------------------------
class TestTheCanaryRateCountsEveryCall(unittest.TestCase):
    """§6: "first-attempt canary pass rate reported (target >= 90%) and after
    retries". The first-attempt rate is a statement about what the judge does
    when it is asked once — §2.1's whole reason for the canary is the
    2026-09-18 run that answered SUCCESS with invented page codes.

    `_call_metrics` computes it as `len(first_try) / len(ok)`: both sides are
    restricted to calls that eventually verified, so a call that burned all
    three attempts and was discarded leaves the measurement entirely. The
    judge's worst behaviour is precisely what disappears.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-calib-bars-"))
        self.root = self.tmp / "_qc"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _calls(self, ok_first: int, discarded: int):
        j = Q.RunJournal(self.root)
        for i in range(ok_first):
            j.add_call({"tag": "t", "at": f"2026-09-23T00:{i:02d}:00",
                        "call_id": f"A{i}", "kind": "A", "duration_s": 10.0,
                        "attempts": 1, "ok": True, "canary_verified": True,
                        "pages": 100})
        for i in range(discarded):
            j.add_call({"tag": "t", "at": f"2026-09-23T01:{i:02d}:00",
                        "call_id": f"B{i}", "kind": "B", "duration_s": 10.0,
                        "attempts": 3, "ok": False, "canary_verified": False,
                        "pages": 100})

    def test_calls_that_never_verified_stay_in_the_denominator(self):
        self._calls(ok_first=5, discarded=5)
        m = Q._call_metrics({"chapters": ["t"]}, root=self.root)
        self.assertEqual(
            m["canary_first_attempt"]["n"], 10,
            "the five calls that never verified were dropped from the "
            "first-attempt canary rate")
        self.assertEqual(m["canary_first_attempt"]["k"], 5)

    def test_a_quota_abort_is_not_a_failed_canary(self):
        # §4: "A quota stop never consumes a content retry." It must not
        # consume a canary statistic either — the judge never answered, so
        # there was nothing to verify. `_timed_judge` records the aborted call
        # as `ok: False` and `_call_metrics` divides by every call, so the
        # 13h33m lockout measured on this account writes one phantom canary
        # failure per paused chapter into the rate §6 asks to be reported.
        j = Q.RunJournal(self.root)
        j.add_call({"tag": "t", "at": "2026-09-23T00:00:00", "call_id": "A1",
                    "kind": "A", "duration_s": 0.0, "ok": False,
                    "raised": "QuotaPaused"})
        j.add_call({"tag": "t", "at": "2026-09-24T00:00:00", "call_id": "A1",
                    "kind": "A", "duration_s": 10.0, "attempts": 1, "ok": True,
                    "canary_verified": True, "pages": 100})
        m = Q._call_metrics({"chapters": ["t"]}, root=self.root)
        self.assertEqual(
            m["canary_after_retries"], Q.rate(1, 1),
            "the call that never happened because agy was out of quota was "
            "counted as a canary failure")

    def test_a_judge_that_fails_half_its_calls_does_not_print_go(self):
        self._calls(ok_first=5, discarded=5)
        m = Q._call_metrics({"chapters": ["t"]}, root=self.root)
        s = passing_metrics(canary_first_attempt=m["canary_first_attempt"])
        go, why = Q.verdict_line(a_set(), s)
        self.assertFalse(
            go, f"GO on a judge that verified 5 of 10 calls; §6's bar is "
                f"{Q.BAR_CANARY_FIRST:.0%} and the line read "
                f"{m['canary_first_attempt']}")
        self.assertTrue(any("canary" in w for w in why), why)


# ---------------------------------------------------------------------------
class TestGoldCannotRemoveItsOwnBar(unittest.TestCase):
    """§6's recall is "on mutants + contradicted gold", and §1 adjudicated
    exactly three contradicted items: 042 Q2 (DSM p.135), 038 Q9 (Dulcan
    p.330), 043 Q1 (DSM p.266). They are the only part of the calibration made
    of errors a human found in the owner's own released material; the 60
    mutants are errors this file wrote itself.

    `gold()` derives its labels instead of storing them — which is right, and
    is why every failure to derive one comes back as `undecided` rather than as
    a crash: no books on the machine, no cached material, a page whose text
    moved. `verdict_line` then counts the contradicted rows IT WAS GIVEN, so a
    gold set that derived nothing demands nothing. The calibration that is
    supposed to fail loudly ("which is the point", qc_calibrate.py:29) goes
    quiet instead.
    """

    GIDS = ("042-Q2", "038-Q9", "043-Q1")

    def _gold(self, label):
        return [{"gid": g, "tag": "t", "unit": g.split("-")[1], "rule": "r",
                 "label": label, "book": "", "printed": None, "quote": "",
                 "said": "", "detail": "could not be adjudicated",
                 "other_hits": [], "owner_review": False} for g in self.GIDS]

    def test_an_undecided_gold_set_never_prints_go(self):
        doc = a_set(gold=self._gold("undecided"))
        go, why = Q.verdict_line(doc, passing_metrics())
        self.assertFalse(
            go, "GO with a gold set that adjudicated nothing: §6's recall is "
                "on mutants AND contradicted gold, and no gold was measured")

    def test_an_empty_gold_set_never_prints_go(self):
        # The same hole reached the other way: `build(with_gold=False)`, or a
        # set.json written before gold() ran at all.
        go, why = Q.verdict_line(a_set(gold=[]), passing_metrics())
        self.assertFalse(go, "GO with no gold set at all")

    def test_a_gold_item_is_a_hit_only_on_a_contradiction(self):
        # `score()` scores a gold item as `hit_exact = bool(found)` — ANY
        # verified finding of any kind. §5.8 routes `unsupported` to the owner
        # queue and never lets it reach qc_fix; it is not the judge finding
        # what §1 found. The 60 mutants are held to the value test, and the
        # three gold items sit in the same headline recall.
        tmp = Path(tempfile.mkdtemp(prefix="qc-calib-gold-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "_qc"
        root.mkdir(parents=True)
        d = Q.judge_root(root) / "t" / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        qc_store.write_json_atomic(d / "round0-A1.json", {
            "call_id": "A1", "ok": True, "units": ["Q2"], "coverage": [],
            "findings": [{"unit": "Q2", "verified": True, "kind": "unsupported",
                          "said": "some other claim in the same item",
                          "severity": "low"}]})
        doc = a_set(gold=[{"gid": "042-Q2", "tag": "t", "unit": "Q2",
                           "rule": "threshold_of_list", "label": "contradicted",
                           "book": "DSM-5-TR", "printed": 135,
                           "quote": "three (or more) of the following",
                           "said": "Catalepsy", "detail": "",
                           "other_hits": [], "owner_review": False}])
        s = Q.score(doc, root=root)
        self.assertEqual(
            s["recall_gold"]["k"], 0,
            "an `unsupported` finding on another claim in the same item was "
            "counted as recall on a contradicted gold item")


# ---------------------------------------------------------------------------
class TestAPartialRunCannotPrintGo(unittest.TestCase):
    """§6 names three chapters the set must include — 005, 056 and 070 — and
    the builder's own open_issues records that 070 cannot be planned at all
    (`PlanError: B1: no pages`). `_call_metrics` reports `failed_chapters` and
    `paused` and `verdict_line` reads neither, so a run that judged 12 of 16
    chapters and is still parked on a 13h33m quota lockout can print GO as
    long as the counts happen to add up over the chapters that did run.

    §2.3 is fail-closed. A calibration that did not finish is not a GO.
    """

    def test_a_chapter_that_never_reached_a_verdict_blocks_go(self):
        s = passing_metrics(
            failed_chapters={"study-070-ect-and-other-somatic-therapies":
                             "PlanError: B1: no pages"})
        go, why = Q.verdict_line(a_set(), s)
        self.assertFalse(
            go, "GO while a chapter §6 names never reached a verdict")
        self.assertTrue(any("070" in w for w in why), why)

    def test_a_run_still_paused_on_quota_blocks_go(self):
        s = passing_metrics(paused={"tag": "study-005-serotonin-system",
                                    "step": "judged_B",
                                    "resume_after": "2026-09-24T09:12:00"})
        go, why = Q.verdict_line(a_set(), s)
        self.assertFalse(go, "GO while the run is still paused on quota")


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
