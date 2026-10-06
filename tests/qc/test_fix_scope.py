#!/usr/bin/env python3
"""The judge reads what the owner studies from, and says what it did not read.

Owner's decision, 2026-10-01: the agy quota buys ~55 calls a week, half of them
went to facts.json and the study guide, and facts.json was the worst place the
judge measured (28% false positives) while being material he does not study from
— it feeds plates he draws only after studying a chapter. So the judge's scope is
the study guide, the flashcards and the quiz.

Leaving assets out of the BUNDLE must not leave them out of the CHAPTER. A unit
the judge never read is uncovered, the asset it belongs to is not publishable,
and the verdict cannot call the chapter fully covered — the same fail-closed
rule as a call that died on quota (§2.3).

    python -m unittest tests.qc.test_fix_scope
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_run                                                    # noqa: E402
from test_qc_run import RunCase                                  # noqa: E402


class Recording:
    """FakeSources that also remembers which unit ids it was handed."""

    def __init__(self, inner):
        self.inner = inner
        self.uids: list[str] = []

    def __call__(self, **kw):
        self.uids.extend(u.uid for u in kw["units"])
        return self.inner(**kw)


class ParseScope(unittest.TestCase):

    def test_empty_means_everything(self):
        self.assertIsNone(qc_run.parse_scope(None))
        self.assertIsNone(qc_run.parse_scope(""))

    def test_study_is_the_guide_the_cards_and_the_quiz(self):
        got = qc_run.parse_scope("study")
        self.assertEqual(got, qc_run.STUDY_SCOPE)
        self.assertIn("study_guide.md", got)
        self.assertIn("report.md", got, "the oldest chapters name the guide report.md")
        self.assertNotIn("facts.json", got)

    def test_a_comma_list(self):
        self.assertEqual(qc_run.parse_scope("quiz.json, flashcards.json"),
                         frozenset({"quiz.json", "flashcards.json"}))


class ScopedRun(RunCase):

    def facts_uids(self):
        return {u.uid for u in qc_run.qc_units.units(
            {"facts.json": self.gh_facts()}) }

    def gh_facts(self):
        import test_qc_run
        return test_qc_run.FACTS

    def test_out_of_scope_units_never_reach_the_planner(self):
        rec = Recording(self.sources)
        self.run_qc(self.opts(scope=qc_run.STUDY_SCOPE), self.deps(sources=rec))
        facts = self.facts_uids()
        self.assertTrue(facts, "the fixture has no facts units to leave out")
        self.assertFalse(set(rec.uids) & facts,
                         "facts.json units were handed to the judge's planner")
        self.assertTrue(any(u.startswith("Q") for u in rec.uids),
                        "the quiz was left out as well")

    def test_without_a_scope_facts_are_planned_as_before(self):
        rec = Recording(self.sources)
        self.run_qc(self.opts(), self.deps(sources=rec))
        self.assertTrue(set(rec.uids) & self.facts_uids())

    def test_out_of_scope_units_are_uncovered_not_approved(self):
        self.run_qc(self.opts(scope=qc_run.STUDY_SCOPE), self.deps())
        v = self.verdict()
        uncovered = set((v.get("coverage") or {}).get("uncovered_units") or [])
        self.assertTrue(uncovered & self.facts_uids(),
                        "a unit the judge never read was counted as covered")
        self.assertNotEqual(v.get("status"), "approved")
        # Known, and deliberate for now: the hold is the generic "no coverage
        # row" one, so under a scope EVERY chapter reads needs_owner. Harmless
        # for calibration, which scores items; a production run needs the
        # out-of-scope assets reported per asset instead, or 144 false holds
        # would bury the real owner items.
        self.assertTrue(any("no coverage row" in h for h in v.get("holds") or []))

    def test_an_out_of_scope_asset_is_not_published(self):
        self.books_available()
        self.run_qc(self.opts(scope=qc_run.STUDY_SCOPE, no_upload=False),
                    self.deps())
        v = self.verdict()
        uploaded = {n for n, r in (v.get("outputs") or {}).items()
                    if r.get("uploaded")}
        self.assertNotIn("facts.json", uploaded)

    def test_the_verdict_says_what_was_left_out(self):
        self.run_qc(self.opts(scope=qc_run.STUDY_SCOPE), self.deps())
        notes = " ".join(self.verdict().get("notes") or [])
        self.assertIn("facts.json", notes)


class CalibrationSetIdentity(unittest.TestCase):
    """A scoped set is its own experiment; an unscoped one keeps its id."""

    def doc(self, **extra):
        return {"mutants": [{"mid": "m1"}], "negatives": [{"nid": "n1"}],
                "gold": [], "seed": 1, **extra}

    def test_an_old_set_keeps_the_id_its_judgments_are_bound_to(self):
        import qc_calibrate as Q
        self.assertEqual(Q.set_id(self.doc()), Q.set_id(self.doc(scope=None)))

    def test_a_scoped_set_has_its_own_id(self):
        import qc_calibrate as Q
        self.assertNotEqual(Q.set_id(self.doc()),
                            Q.set_id(self.doc(scope=["quiz.json"])))


if __name__ == "__main__":
    unittest.main()
