#!/usr/bin/env python3
"""Adversarial review of scripts/qc_calibrate.py — what the denominators count.

§6 does not ask "did the judge find the mutants it was shown". It asks for a
recall and a false-positive rate over a MEASURED set: ">= 60 seeded mutants",
">= 200 negatives", "verified false positives <= 2%" — and that last number is
the switch §5.9 puts Tier-2 rewriting behind. A denominator that counts units
no judge ever read does not make the rate noisy; it makes it lower, because an
unread unit contributes a zero to the numerator every time. That is the one
direction qc_calibrate's own docstring promises never to go ("where a metric
could be read two ways, the headline is the reading that makes the judge look
WORSE").

Every test here is written against `score()`/`run_prepared()` as they behave
today and fails on today's code. Nothing here runs agy, gh or git.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_calibrate as Q                                   # noqa: E402
import qc_store                                            # noqa: E402

from test_qc_calibrate import QUIZ, FakeSources            # noqa: E402


class Root(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-calib-denom-"))
        self.root = self.tmp / "_qc"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def judgment(self, tag, name="round0-A1.json", **kw):
        d = Q.judge_root(self.root) / tag / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"call_id": name.split("-", 1)[1].split(".")[0], "kind": "A",
               "ok": True, "units": [], "findings": [], "coverage": []}
        rec.update(kw)
        qc_store.write_json_atomic(d / name, rec)
        return d / name

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
                "before": "2-8 weeks", "after": after, "confirmed_by": "t"}

    def doc(self, **kw):
        d = {"built_at": "2026-09-23T00:00:00+00:00", "chapters": ["t"],
             "gold": [], "mutants": [], "negatives": [], "discarded": [],
             "controls": [], "bar": {}, "pool": 0}
        d.update(kw)
        d["counts"] = Q._set_counts(d)
        return d


# ---------------------------------------------------------------------------
class TestJudgedIsPerUnitNotPerChapter(Root):
    """A chapter is three or more calls (§5.8 A / B / C, plus the S calls a
    Synopsis-heavy chapter gets). qc_run caches ONE file per call and only for
    a call that verified, and it stops the chapter at the first call that did
    not (`qc_run.py:1050`, "the journal keeps what did verify"). So "this tag
    has a judgment on disk" says nothing about whether the guide, the facts or
    the legends were ever read.

    `score()` nevertheless decides `judged` per TAG, and every negative in the
    chapter then contributes a clean row. The measured effect is in
    `test_a_single_quiz_call_fills_the_whole_tier_2_gate` below: one cached A
    call over one unit produced false_positives 0/200 with a Clopper-Pearson
    upper bound of 1.8% — under §5.9's 2% Tier-2 bar, on 199 units nobody
    judged.
    """

    def test_a_negative_in_a_call_that_never_ran_is_not_judged(self):
        self.judgment("t", units=["Q1"],
                      coverage=[{"unit": "Q1", "status": "supported"}])
        doc = self.doc(negatives=[self.negative("Q1", artifact="quiz.json"),
                                  self.negative("G1"), self.negative("G2")])
        s = Q.score(doc, root=self.root)
        # Only Q1 was in a call that verified. G1 and G2 live in the B call
        # (study_guide.md), which never produced a judgment at all.
        self.assertEqual(s["false_positives"]["n"], 1,
                         "negatives from a call that never ran are being "
                         "counted as judged-and-clean")

    def test_a_mutant_in_a_call_that_never_ran_is_not_a_miss(self):
        self.judgment("t", units=["Q1"])
        doc = self.doc(mutants=[self.mutant("Q1"),
                                self.mutant("G7", artifact="study_guide.md")])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["recall"]["n"], 1,
                         "a mutant in a call that was never made is being "
                         "scored as a recall miss")

    def test_a_failed_call_does_not_make_its_chapter_judged(self):
        # §5.8: a call that fails its canary produced the confident invented
        # JSON of the 2026-09-18 incident. qc_run refuses to cache it at all,
        # so bytes like these on disk are an older schema or a half-written
        # resume — and they certainly are not a measurement.
        self.judgment("t", ok=False, units=["Q1", "G1"])
        doc = self.doc(negatives=[self.negative("Q1", artifact="quiz.json"),
                                  self.negative("G1")],
                       mutants=[self.mutant("Q2", artifact="quiz.json")])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["false_positives"]["n"], 0,
                         "a chapter whose only judgment FAILED is being "
                         "counted as judged")
        self.assertEqual(s["recall"]["n"], 0)
        self.assertEqual(s["judged_chapters"], [])

    def test_a_single_quiz_call_fills_the_whole_tier_2_gate(self):
        # The whole defect in one number, at §6's real size. One cached A call
        # over one unit; 200 negatives spread over the guide, the facts and the
        # legends — the assets of the B and C calls that never happened.
        self.judgment("t", units=["Q1"])
        negs = [self.negative("Q1", artifact="quiz.json")]
        negs += [self.negative(f"G{i}") for i in range(199)]
        doc = self.doc(negatives=negs,
                       mutants=[self.mutant(f"Q{i}") for i in range(60)])
        s = Q.score(doc, root=self.root)
        go, why = Q.verdict_line(doc, s)
        self.assertLess(s["false_positives"]["n"], Q.MIN_NEGATIVES,
                        f"§6's >= 200 negatives floor was cleared with one "
                        f"judged call: false_positives={s['false_positives']}")
        self.assertTrue(any("שליליים" in w for w in why),
                        f"nothing objected to the negatives: {why}")


# ---------------------------------------------------------------------------
class TestAnUnjudgedChapterIsRetried(Root):
    """§2.3 is fail-closed: "No verified verdict -> `unjudged`, never
    approved." A calibration has to treat that the same way — the chapter was
    not measured, so the run must come back to it. Today `run_prepared` writes
    `finished_at` for whatever `ChapterRun.run()` returned, including the
    `unjudged` verdict it returns when a call fails its canary
    (`qc_run.py:1536`), and the next run prints "כבר נשפט, מדלג" for ever.

    Combined with the per-tag `judged` above, that is the worst case in this
    file: the chapter keeps contributing clean negatives to the Tier-2 gate,
    and the only way to make it contribute a measurement instead is to delete
    journal.json by hand.
    """

    def setUp(self):
        super().setUp()
        self.tag = "study-005-serotonin-system"
        d = Q.seeded_root(self.root) / self.tag
        d.mkdir(parents=True)
        (d / "quiz.json").write_text(json.dumps(QUIZ, ensure_ascii=False,
                                                indent=2), encoding="utf-8")
        qc_store.write_json_atomic(
            Q.cal_root(self.root) / "set.json",
            self.doc(chapters=[self.tag],
                     mutants=[self.mutant("Q1", tag=self.tag)]))
        self.sources = FakeSources(self.tmp / "work")

    def _run(self, judge):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            code = Q.run_prepared(root=self.root, judge=judge,
                                  sources=self.sources)
        return code, buf.getvalue()

    def test_a_chapter_whose_call_failed_is_not_marked_done(self):
        def refused(**kw):
            plan = kw["plan"]
            # Exactly what agy_attach hands back after three attempts whose
            # canary codes did not match: an answer, and a refusal.
            return {"call_id": plan.call_id, "kind": plan.kind, "ok": False,
                    "reason": "canary: 2 of 4 codes did not match",
                    "findings": [], "coverage": [], "attempts": 3,
                    "duration_s": 3.0, "unchecked": 0,
                    "canary": {"requested": [["X", 1]], "returned": [],
                               "verified": False},
                    "tempmedia_ok": True, "model": "fake",
                    "agy_version": "fake"}

        self._run(refused)
        self.assertFalse(
            Q.RunJournal(self.root).done(self.tag),
            "a chapter that never reached a verified verdict was marked "
            "finished, so no later run will ever judge it")


# ---------------------------------------------------------------------------
class TestAReportIsAboutOneExperiment(Root):
    """`--build` is not idempotent and nothing ties a judgment to the set it
    was made against.

    The builder's own open_issues names `--build --mutants 70` as the operator
    workaround for chapter 070, i.e. a rebuild AFTER a run. A rebuild reseeds
    from a fresh `random.Random(seed)` walk over a pool whose size changed, so
    a unit that carried a mutant can come back as a negative and vice versa —
    while `_qc/calibration/qc/<tag>/judgments/` still holds the verdicts on the
    OLD bytes and `journal.json` still says the chapter is done. `score()` then
    reads the new set against the old judgments and prints a recall and a
    false-positive rate about two different experiments.
    """

    def test_a_judgment_older_than_the_set_is_not_counted(self):
        path = self.judgment("t", units=["Q1"])
        # The judgment was written before the set it is about to be scored
        # against. os.utime rather than a sleep: the ordering is the point.
        old = time.time() - 3600
        os.utime(path, (old, old))
        doc = self.doc(built_at=Q._now_iso(),
                       negatives=[self.negative("Q1", artifact="quiz.json")])
        s = Q.score(doc, root=self.root)
        self.assertEqual(
            s["judged_chapters"], [],
            "a verdict made before this set was built is being scored as a "
            "measurement of it")


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
