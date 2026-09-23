#!/usr/bin/env python3
"""Independent verification of §6's judged-ness rule, after the fix.

`_judged_units` now decides "was this unit read?" per UNIT instead of per
chapter, which closed the reviewer's first blocking finding. It builds the set
from THREE sources and its docstring calls all three "proof the call covered
the unit":

    for uid in j.get("units") or []:        # the uids the call was planned over
    for row in j.get("coverage") or []:     # §5.8.2's one row per unit
    for f in j.get("findings") or []:       # the units the findings name

The third is not proof. qc_judge keeps a finding whose `unit` is not in the
call and marks it UNVERIFIED for exactly that reason — `qc_judge.py:834`,
`_unverified(rec, f"unit {unit!r} is not in this call")` — and an unverified
finding is then filtered out of `_findings_by_unit`'s scoring by `_verified`.
So the unit is counted as judged and counted as CLEAN: a zero in the numerator
of §5.9's false-positive gate and a one in its denominator, from a call that
never saw it.

That is the same number the blocking finding was about, reached through a
different door. The test below measures it: one A call over one unit, whose
judge also named 200 units of the B call that never ran, gives
false_positives 0/200 with a Clopper-Pearson upper bound of 1.8% — clearing
§6's ">= 200 negatives" floor and §5.9's "<= 2%" Tier-2 bar on 200 units
nobody read.

These tests FAIL against the current scripts/qc_calibrate.py and are left in
place deliberately. Nothing here runs agy, gh or git.

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


class Root(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-verify-judged-"))
        self.root = self.tmp / "_qc"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def judgment(self, tag="t", name="round0-A1.json", **kw):
        d = Q.judge_root(self.root) / tag / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"call_id": name.split("-", 1)[1].split(".")[0], "kind": "A",
               "ok": True, "units": [], "findings": [], "coverage": []}
        rec.update(kw)
        qc_store.write_json_atomic(d / name, rec)

    def negative(self, uid, tag="t", artifact="study_guide.md"):
        return {"nid": f"{tag}:{artifact}:{uid}", "tag": tag, "uid": uid,
                "artifact": artifact,
                "artifact_type": Q.artifact_type(artifact),
                "unit_type": "unit", "book": "Dulcan", "printed": 330,
                "raw": "5 mg", "quote": "a sentence the page states"}

    def mutant(self, uid, tag="t", artifact="quiz.json", after="3-8 weeks"):
        return {"mid": f"{tag}:{artifact}:{uid}:duration", "tag": tag,
                "uid": uid, "artifact": artifact,
                "artifact_type": Q.artifact_type(artifact),
                "unit_type": "multiple_choice", "mutation": "duration",
                "book": "StahlKids", "printed": 69, "quote": "q",
                "before": "2-8 weeks", "after": after, "confirmed_by": tag}

    def doc(self, **kw):
        d = {"built_at": "2026-09-23T00:00:00+00:00", "chapters": ["t"],
             "gold": [], "mutants": [], "negatives": [], "discarded": [],
             "controls": [], "bar": {}, "pool": 0, "seed": 1}
        d.update(kw)
        d["set_id"] = Q.set_id(d)
        d["counts"] = Q._set_counts(d)
        return d

    @staticmethod
    def out_of_call_finding(uid):
        """What qc_judge writes for a finding naming a unit of another call.

        `qc_judge._check_said` -> `_unverified(rec, f"unit {unit!r} is not in
        this call")`. It stays in `findings`; it is not moved to `dropped`.
        """
        return {"unit": uid, "kind": "contradicted", "severity": "high",
                "said": "5 mg", "verified": False,
                "verification": f"unit {uid!r} is not in this call"}


# ---------------------------------------------------------------------------
class TestAFindingCannotMakeItsUnitJudged(Root):
    """A unit is judged when a call READ it, never when a call MENTIONED it."""

    def test_an_out_of_call_finding_does_not_make_its_unit_judged(self):
        self.judgment(units=["Q1"],
                      coverage=[{"unit": "Q1", "status": "supported"}],
                      findings=[self.out_of_call_finding("G1")])
        doc = self.doc(negatives=[self.negative("Q1", artifact="quiz.json"),
                                  self.negative("G1")])
        s = Q.score(doc, root=self.root)
        self.assertEqual(
            s["false_positives"]["n"], 1,
            "G1 was never in a call — qc_judge said so when it marked the "
            "finding unverified ('unit \\'G1\\' is not in this call', "
            "qc_judge.py:834) — yet _judged_units took the uid off that very "
            "finding and scored G1 as a judged, clean negative")

    def test_a_single_call_cannot_refill_the_tier_2_gate(self):
        # The blocking finding's own number, reached through the findings
        # branch instead of through the per-tag one.
        negs = ([self.negative("Q1", artifact="quiz.json")]
                + [self.negative(f"G{i}") for i in range(1, 201)])
        self.judgment(units=["Q1"],
                      coverage=[{"unit": "Q1", "status": "supported"}],
                      findings=[self.out_of_call_finding(f"G{i}")
                                for i in range(1, 201)])
        s = Q.score(self.doc(negatives=negs), root=self.root)
        self.assertLess(
            s["false_positives"]["n"], Q.MIN_NEGATIVES,
            f"one A call over one unit produced "
            f"{s['false_positives']['k']}/{s['false_positives']['n']} false "
            f"positives (upper bound {s['false_positives']['hi']:.2%}), which "
            f"clears §6's {Q.MIN_NEGATIVES}-negative floor and §5.9's "
            f"{Q.BAR_FALSE_POSITIVES:.0%} Tier-2 bar on units nobody read")

    def test_an_out_of_call_finding_does_not_score_a_mutant(self):
        # The same door on the other side of §6's recall: a judge that names a
        # unit of a call that never ran, with the seeded value in `said`,
        # scores a hit on a mutant nobody was asked about.
        hit = {**self.out_of_call_finding("Q9"), "said": "3-8 weeks"}
        self.judgment(units=["Q1"],
                      coverage=[{"unit": "Q1", "status": "supported"}],
                      findings=[hit])
        s = Q.score(self.doc(mutants=[self.mutant("Q9")]), root=self.root)
        self.assertEqual(
            s["recall_mutants"]["n"], 0,
            "the mutant in Q9 entered §6's recall denominator on the strength "
            "of an unverified finding from the call that did not contain it")

    def test_units_and_coverage_remain_the_two_sources_that_count(self):
        """The fix must not go the other way: the two real proofs still hold.

        `units` is written by qc_judge from the plan (qc_judge.py:1109) and
        `coverage` is §5.8.2's one row per unit, so each on its own is enough.
        """
        self.judgment(name="round0-A1.json", units=["Q1"], coverage=[])
        self.judgment(name="round0-B1.json", units=[],
                      coverage=[{"unit": "G1", "status": "supported"}])
        doc = self.doc(negatives=[self.negative("Q1", artifact="quiz.json"),
                                  self.negative("G1")])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["false_positives"]["n"], 2,
                         "a unit the call was planned over, or that has a "
                         "§5.8.2 coverage row, was read and must count")


# ---------------------------------------------------------------------------
class TestTheFixedRulesStayFixed(Root):
    """The reviewer's first blocking finding, re-measured from the outside.

    These pass today. They are here so the per-unit rule cannot quietly become
    per-chapter again through some other edit.
    """

    def test_a_failed_call_proves_nothing_about_its_units(self):
        self.judgment(ok=False, units=["Q1", "G1"],
                      reason="canary: 2 of 4 codes did not match",
                      coverage=[{"unit": "Q1", "status": "supported"}])
        doc = self.doc(negatives=[self.negative("Q1", artifact="quiz.json"),
                                  self.negative("G1")])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["false_positives"]["n"], 0)
        self.assertEqual(s["judged_chapters"], [])

    def test_a_judgment_older_than_the_set_is_not_counted(self):
        import os
        import time
        self.judgment(units=["Q1"],
                      coverage=[{"unit": "Q1", "status": "supported"}])
        p = (Q.judge_root(self.root) / "t" / "judgments" / "round0-A1.json")
        old = time.time() - 30 * 24 * 3600
        os.utime(p, (old, old))
        doc = self.doc(built_at="2026-09-23T00:00:00+00:00",
                       negatives=[self.negative("Q1", artifact="quiz.json")])
        s = Q.score(doc, root=self.root)
        self.assertEqual(
            s["false_positives"]["n"], 0,
            "a verdict written before this set was built is about other "
            "seeded bytes and must not be added to this set's numbers")
        self.assertEqual(s["stale_judgments"], {"t": 1},
                         "and the report has to say it was skipped")

    def test_a_judgment_stamped_with_another_set_is_not_counted(self):
        self.judgment(units=["Q1"], set_id="SOMEOTHERSET",
                      coverage=[{"unit": "Q1", "status": "supported"}])
        doc = self.doc(negatives=[self.negative("Q1", artifact="quiz.json")])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["false_positives"]["n"], 0)


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
