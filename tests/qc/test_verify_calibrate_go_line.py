#!/usr/bin/env python3
"""Every way I could think of to make `verdict_line` print GO on a measurement
that did not happen. These all block today; they are here so they keep doing
it.

GO is the owner's authority to run the judge over 68 released chapters and,
through §5.9, to let Tier 2 REWRITE material he is revising for a board exam.
So the question is never "is the number pretty" but "did the thing the number
describes actually happen on THIS set".

The first test is the one that makes the rest mean anything: a `verdict_line`
that returned NO-GO unconditionally would pass every other test in this file
and be useless, so the baseline proves the line can still say GO when all four
of §6's bars really were measured. Each test after it moves exactly one thing
off that baseline.

Two attacks that do NOT block are in test_verify_calibrate_judged.py and
test_verify_calibrate_journal.py. Nothing here runs agy, gh or git.

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


def metrics(**kw) -> dict:
    """A score dict in which all four of §6's bars were really measured."""
    s = {"judged_chapters": ["t"], "judged_units": 300,
         "recall": Q.rate(63, 63), "recall_any": Q.rate(63, 63),
         "recall_mutants": Q.rate(60, 60), "recall_gold": Q.rate(3, 3),
         "false_positives": Q.rate(0, 200),
         "false_positives_on_value": Q.rate(0, 200),
         "controls": {"overall": Q.rate(10, 10), "by_control": {},
                      "gates": ["verify"], "unexpected": [], "error": ""},
         "canary_first_attempt": Q.rate(10, 10),
         "canary_after_retries": Q.rate(10, 10),
         "failed_chapters": {}, "unfinished_chapters": [], "paused": None,
         "set_mismatch": False, "stale_judgments": {},
         "by_artifact": {}, "by_unit_type": {}, "by_book": {},
         "by_mutation": {}, "mutants": [], "negatives": [],
         "gold": [{"gid": g, "tag": "t", "unit": f"U{i}", "judged": True,
                   "hit_exact": True, "label": "contradicted"}
                  for i, g in enumerate(Q.REQUIRED_GOLD)]}
    s.update(kw)
    return s


def a_set(**kw) -> dict:
    doc = {"built_at": "2026-09-23T00:00:00+00:00", "chapters": ["t"],
           "gold": [{"gid": g, "tag": "t", "unit": f"U{i}",
                     "label": "contradicted"}
                    for i, g in enumerate(Q.REQUIRED_GOLD)],
           "mutants": [], "negatives": [], "discarded": [], "controls": [],
           "bar": {}, "pool": 0, "seed": 1}
    doc.update(kw)
    doc["set_id"] = Q.set_id(doc)
    doc["counts"] = Q._set_counts(doc)
    return doc


class GoLine(unittest.TestCase):
    """Only the shared assertion. The baseline is one test, below."""

    def assertNoGo(self, doc, s, because):
        go, why = Q.verdict_line(doc, s)
        self.assertFalse(go, f"GO was printed although {because}")
        self.assertTrue(why, "NO-GO with no reason given")
        return why


class TestTheBaselineIsGo(unittest.TestCase):
    def test_the_baseline_really_is_go(self):
        go, why = Q.verdict_line(a_set(), metrics())
        self.assertTrue(go, f"the baseline is not GO, so nothing below is a "
                            f"test of anything: {why}")


# ---------------------------------------------------------------------------
class TestAnEmptyDenominatorIsNotAPass(GoLine):
    """§2.3: `n = 0` means the thing was not measured. Every bar, one by one."""

    def test_no_mutant_or_gold_item_was_judged(self):
        self.assertNoGo(a_set(), metrics(recall=Q.rate(0, 0)),
                        "nothing was in §6's recall at all")

    def test_no_negative_was_judged(self):
        self.assertNoGo(a_set(), metrics(false_positives=Q.rate(0, 0)),
                        "no negative was judged")

    def test_no_control_was_run(self):
        self.assertNoGo(
            a_set(), metrics(controls={"overall": Q.rate(0, 0),
                                       "by_control": {}, "gates": [],
                                       "unexpected": [], "error": ""}),
            "the negative controls never ran")

    def test_no_call_to_measure_the_canary_on(self):
        self.assertNoGo(a_set(), metrics(canary_first_attempt=Q.rate(0, 0)),
                        "there was no verified call to measure a canary on")


# ---------------------------------------------------------------------------
class TestAChapterJudgedOnOneCall(GoLine):
    """§6's floors are the size of the MEASUREMENT. A run that stopped after
    one chapter had 9 mutants at 100% recall and 12 negatives at 0% false
    positives — every rate perfect, nothing measured."""

    def test_nine_mutants_at_100_percent_is_not_sixty(self):
        why = self.assertNoGo(
            a_set(), metrics(recall=Q.rate(9, 9), recall_mutants=Q.rate(9, 9),
                             recall_gold=Q.rate(0, 0),
                             false_positives=Q.rate(0, 12)),
            f"only 9 of §6's {Q.MIN_MUTANTS} mutants were judged")
        self.assertTrue(any(str(Q.MIN_MUTANTS) in w for w in why))
        self.assertTrue(any(str(Q.MIN_NEGATIVES) in w for w in why))

    def test_the_negatives_floor_is_counted_on_what_was_judged(self):
        # 200 negatives PREPARED, 12 judged.
        doc = a_set(negatives=[{"nid": f"n{i}", "tag": "t", "uid": f"U{i}"}
                               for i in range(200)])
        self.assertNoGo(doc, metrics(false_positives=Q.rate(0, 12)),
                        "188 of the 200 negatives were never read")


# ---------------------------------------------------------------------------
class TestAGoldItemThatFailedToAdjudicate(GoLine):
    """`gold()` degrades to `undecided` on every failure to adjudicate — no
    books on the machine, no cached material, a page whose text moved. §1's
    three contradicted items are the only part of the set made of errors a
    human found in real released material, and the bar for them is pinned to
    their ids and never derived from the rows the call was handed."""

    def test_all_three_undecided_does_not_remove_the_bar(self):
        undecided = [{"gid": g, "tag": "t", "unit": f"U{i}",
                      "label": "undecided", "detail": "no books on this machine"}
                     for i, g in enumerate(Q.REQUIRED_GOLD)]
        why = self.assertNoGo(
            a_set(gold=undecided),
            metrics(recall_gold=Q.rate(0, 0), gold=[]),
            "no gold item was adjudicated at all")
        for gid in Q.REQUIRED_GOLD:
            self.assertTrue(any(gid in w for w in why),
                            f"{gid} is not named in the NO-GO reasons")

    def test_an_absent_gold_set_does_not_remove_the_bar(self):
        self.assertNoGo(a_set(gold=[]),
                        metrics(recall_gold=Q.rate(0, 0), gold=[]),
                        "the set carries no gold rows at all "
                        "(--build --no-gold, or a set.json from before "
                        "gold() ran)")

    def test_one_missing_gold_item_blocks(self):
        keep = [{"gid": g, "tag": "t", "unit": f"U{i}", "label": "contradicted"}
                for i, g in enumerate(Q.REQUIRED_GOLD[:2])]
        why = self.assertNoGo(
            a_set(gold=keep),
            metrics(recall_gold=Q.rate(2, 2),
                    gold=[{**r, "judged": True, "hit_exact": True}
                          for r in keep]),
            f"{Q.REQUIRED_GOLD[2]} is missing from the set")
        self.assertTrue(any(Q.REQUIRED_GOLD[2] in w for w in why))

    def test_a_gold_item_present_but_never_judged_blocks(self):
        s = metrics(recall_gold=Q.rate(2, 3))
        s["gold"][1]["judged"] = False
        why = self.assertNoGo(a_set(), s,
                              f"{Q.REQUIRED_GOLD[1]} was never read by a call")
        self.assertTrue(any(Q.REQUIRED_GOLD[1] in w for w in why))

    def test_a_gold_item_relabelled_away_from_contradicted_blocks(self):
        rows = [{"gid": g, "tag": "t", "unit": f"U{i}",
                 "label": "supported" if i == 0 else "contradicted"}
                for i, g in enumerate(Q.REQUIRED_GOLD)]
        self.assertNoGo(a_set(gold=rows),
                        metrics(recall_gold=Q.rate(2, 2),
                                gold=[{**r, "judged": True, "hit_exact": True}
                                      for r in rows if r["label"]
                                      == "contradicted"]),
                        f"{Q.REQUIRED_GOLD[0]} stopped being §1's "
                        f"contradicted item")


# ---------------------------------------------------------------------------
class TestControlsThatWereNotCaughtByTheMechanism(GoLine):
    """§6's controls exist to prove the VERIFICATION PATH refuses a call that
    never read its bundle. A row whose gate is an exception from somewhere else
    proves only that the harness is broken — and 100% is the one bar a broken
    harness would otherwise pass."""

    @staticmethod
    def rows(gate, *, n=10, attempts=3, rejected=True):
        return [{"control": "bundle_missing" if i % 2 else
                            "artifacts_truncated", "run": i // 2,
                 "rejected": rejected, "gate": gate, "reason": "boom",
                 "attempts": attempts} for i in range(n)]

    def test_an_unrelated_exception_is_not_a_rejection(self):
        m = Q._control_metrics({"controls": self.rows("TypeError")})
        self.assertEqual(
            m["overall"]["k"], 0,
            "a TypeError out of _control_call was counted as the "
            "verification path refusing the answer")
        self.assertEqual(len(m["unexpected"]), 10)
        self.assertNoGo(a_set(), metrics(controls=m),
                        "every control was 'rejected' by a TypeError")

    def test_an_import_error_is_not_a_rejection(self):
        m = Q._control_metrics({"controls": self.rows("ImportError")})
        self.assertEqual(m["overall"]["k"], 0)
        self.assertNoGo(a_set(), metrics(controls=m),
                        "the controls never reached qc_judge at all")

    def test_a_verify_gate_that_spent_no_attempt_is_not_a_rejection(self):
        m = Q._control_metrics({"controls": self.rows("verify", attempts=0)})
        self.assertEqual(
            m["overall"]["k"], 0,
            "ok=false without a single attempt spent on the answer is not "
            "the mechanism convicting anything")
        self.assertNoGo(a_set(), metrics(controls=m),
                        "no control was actually put to the judge")

    def test_the_real_gates_still_count(self):
        for gate in ("verify", "JudgeError"):
            m = Q._control_metrics({"controls": self.rows(gate)})
            self.assertEqual(m["overall"]["k"], 10, gate)
            go, why = Q.verdict_line(a_set(), metrics(controls=m))
            self.assertTrue(go, f"{gate} is one of §6's two real gates: {why}")

    def test_a_control_rerun_that_blew_up_blocks(self):
        m = Q._control_metrics({"controls": self.rows("verify"),
                                "controls_error": "ImportError: qc_judge"})
        self.assertNoGo(a_set(), metrics(controls=m),
                        "the frozen 100% could not be re-measured against the "
                        "qc_judge being reported on")


# ---------------------------------------------------------------------------
class TestARunThatDidNotFinish(GoLine):
    def test_a_quota_pause_blocks(self):
        why = self.assertNoGo(
            a_set(), metrics(paused={"tag": "t", "step": "judge",
                                     "resume_after": "08:00 24/09"}),
            "the run is still parked on an agy quota pause")
        self.assertTrue(any("08:00 24/09" in w for w in why),
                        "the owner is not told when he may resume")

    def test_a_chapter_that_never_reached_a_verdict_blocks(self):
        self.assertNoGo(
            a_set(), metrics(failed_chapters={"t": "PlanError: B1: no pages"}),
            "a chapter of the set blew up instead of being measured")

    def test_a_chapter_never_attempted_blocks(self):
        self.assertNoGo(a_set(), metrics(unfinished_chapters=["study-070"]),
                        "a chapter of the set was never judged at all")

    def test_a_journal_about_another_set_blocks(self):
        self.assertNoGo(a_set(), metrics(set_mismatch=True),
                        "the verdicts on disk and the set being scored are "
                        "two experiments")


# ---------------------------------------------------------------------------
class TestAOneCharacterSeededValue(unittest.TestCase):
    """A mutant is scored found when the judge NAMES the value that was seeded.
    On the one- and two-character values `_bump_number` routinely produces, a
    substring test degenerates into "does the sentence contain this digit
    anywhere" and a finding about 19 mg scores as having found the seeded 9 —
    flattering §6's recall with hits the judge never made."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-verify-onechar-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_one_digit_value_is_not_found_inside_a_bigger_number(self):
        for needle, said in (("9", "the item says 19 mg"),
                             ("9", "19"),
                             ("4", "14 criteria are listed"),
                             ("8", "dose 18 mg"),
                             ("2", "28 weeks"),
                             ("2", "2-8 weeks")):
            with self.subTest(needle=needle, said=said):
                self.assertFalse(
                    Q._hits_value({"said": said, "problem": ""}, needle),
                    f"{needle!r} was scored as found inside {said!r}")

    def test_a_one_digit_value_is_still_found_when_it_is_named(self):
        self.assertTrue(
            Q._hits_value({"said": "the answer is 9", "problem": ""}, "9"))

    def test_an_empty_seeded_value_is_never_a_hit(self):
        for said in ("anything at all", "", "9"):
            self.assertFalse(
                Q._hits_value({"said": said, "problem": ""}, ""),
                "an empty `after` scored a recall hit")

    def test_no_swap_table_can_seed_a_one_character_token(self):
        """The numeric path is strict; the TOKEN path is whole-word
        containment with no length floor, so a one- or two-character `after`
        would score a hit on any sentence using that character as a word
        ('a', 'x', 'ה'). Nothing produces one today — the shortest replacement
        in the three tables is 'ERP' — and this is what keeps it that way."""
        short = sorted({b for table in (Q.DRUG_SWAPS, Q.ARM_SWAPS,
                                        Q.DIRECTION_SWAPS)
                        for _, b in table if len(b.strip()) < 3})
        self.assertEqual(
            short, [],
            f"these replacements are shorter than three characters: {short}. "
            f"_hits_value falls back to whole-word containment for a token, "
            f"so each would score a recall hit on any finding whose sentence "
            f"happens to use it as a word")

    def test_a_one_character_token_would_be_a_false_hit(self):
        # Documenting WHY the guard above matters, without asserting on
        # behaviour nothing reaches today.
        self.assertTrue(
            Q._hits_value({"said": "a 2-8 week window", "problem": ""}, "a"),
            "if this ever stops being true the guard above can be relaxed")


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
