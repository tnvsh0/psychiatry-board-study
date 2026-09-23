#!/usr/bin/env python3
"""Adversarial review of scripts/qc_calibrate.py — the seeded errors and the
negative controls.

A calibration set is only worth the two claims under it: every mutant really
contradicts the page it cites (§6, "Code confirms each contradicts its page
first"), and a hit is really the judge having found THAT error. Break either
and the recall stops being a measurement — a mutant that does not contradict
turns a correct silence into a miss, and a hit rule that matches anything turns
an unrelated finding into a success.

The third claim is §6's negative controls: "a call whose bundle.pdf is missing,
and one whose artifacts.md is truncated, must be rejected 100%". That bar is
only meaningful if the rejection came from the verification path. Today any
exception at all counts.

Nothing here runs agy, gh or git; the books are faked with the same FakePages
the module's own suite uses.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_calibrate as Q                                   # noqa: E402
import qc_norm                                             # noqa: E402
import qc_store                                            # noqa: E402
import qc_units                                            # noqa: E402

from test_qc_calibrate import FakePages                    # noqa: E402


# ---------------------------------------------------------------------------
class TestAHitNamesTheSeededValue(unittest.TestCase):
    """`_hits_value` is `qc_norm.norm(needle) in qc_norm.norm(said)`.

    qc_norm.norm deletes the spaces, so on a needle of one or two digits the
    test is "does the judge's sentence contain this digit anywhere". `_bump_number`
    produces exactly such needles: a criteria count of 8 becomes "9", a plain
    number 3 becomes "4". The judge then only has to report SOMETHING verified
    on that unit — a real error elsewhere in the same stem, a dose, a year —
    and the mutant scores as found.

    This is the flattering direction, and qc_calibrate.py:12 promises the
    opposite ("the headline is the reading that makes the judge look WORSE").
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-calib-hit-"))
        self.root = self.tmp / "_qc"
        self.root.mkdir(parents=True)
        self.addCleanup(__import__("shutil").rmtree, self.tmp,
                        ignore_errors=True)

    def _score(self, after, said):
        d = Q.judge_root(self.root) / "t" / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        qc_store.write_json_atomic(d / "round0-A1.json", {
            "call_id": "A1", "ok": True, "units": ["Q1"], "coverage": [],
            "findings": [{"unit": "Q1", "verified": True,
                          "kind": "contradicted", "said": said,
                          "severity": "medium"}]})
        doc = {"built_at": "2026-09-23T00:00:00+00:00", "chapters": ["t"],
               "gold": [], "negatives": [], "controls": [], "counts": {},
               "mutants": [{"mid": "m", "tag": "t", "uid": "Q1",
                            "artifact": "quiz.json", "artifact_type": "quiz",
                            "unit_type": "multiple_choice",
                            "mutation": "criteria_count", "book": "DSM-5-TR",
                            "printed": 135, "quote": "q", "before": "8",
                            "after": after, "confirmed_by": "t"}]}
        return Q.score(doc, root=self.root)

    def test_a_digit_inside_a_longer_number_is_not_a_hit(self):
        s = self._score(after="9", said='מינון של 19 מ"ג ליום')
        self.assertEqual(
            s["recall"]["k"], 0,
            "a finding about 19 mg was scored as having found the seeded 9")

    def test_a_bumped_count_is_not_hit_by_an_unrelated_number(self):
        s = self._score(after="4", said="the stem says 14 days")
        self.assertEqual(
            s["recall"]["k"], 0,
            "a finding about 14 days was scored as having found the seeded 4")

    def test_the_same_leak_does_not_hide_a_false_positive(self):
        # The mirror image on the other side of the ledger: `fp_on_value` uses
        # the same containment on the negative's `raw`, so the split between
        # "any verified finding" and "a finding that disputes the confirmed
        # value" is decided by a digit.
        self.assertFalse(
            Q._hits_value({"said": "19 mg"}, "9"),
            "'9' matched inside '19 mg'")


# ---------------------------------------------------------------------------
class TestKeySwapMutants(unittest.TestCase):
    """§6 lists `key swap` among the eight mutations, and the builder's set has
    seven of them — 12% of the 60.

    Two things are wrong with them, and they push in opposite directions.

      * `_make_mutant` does not run `contradicts()` for a key swap. It checks
        only that the new key's text is not inside the ONE confirmed sentence,
        so `contradicts()`'s central veto — "whatever the seeded value is, it
        must not be stated anywhere on the window", the rule that keeps a
        mutant from being a claim the page supports — never runs. A key moved
        onto an option the page also states is an item that is still right, and
        the judge's correct silence is scored as a miss.
      * `score()` then needs the judge's `said` to contain the whole option
        text (up to 160 characters, `_key_swap`) before the hit counts. §5.8's
        schema says `said` carries the disputed value, not a transcript of the
        option, so a judge that names the moved key correctly still scores 0.
    """

    QUIZ = {"title": "t", "questions": [{
        "type": "multiple_choice",
        "question": "Which agent is approved as monotherapy for ADHD? "
                    "(StahlKids p. 69)",
        "answerOptions": [
            {"text": "Atomoxetine", "isCorrect": True,
             "rationale": "StahlKids p. 69."},
            {"text": "Guanfacine ER", "isCorrect": False,
             "rationale": "StahlKids p. 69."}],
        "hint": "h"}]}

    # Both options are stated on this page. Moving the key from one to the
    # other leaves an item its own source still supports.
    PAGE = ("Atomoxetine is approved as monotherapy for ADHD. Guanfacine ER "
            "is approved as monotherapy for ADHD. Full therapeutic benefits "
            "may take 2–8 weeks.")

    def test_a_key_moved_onto_an_option_the_page_states_is_discarded(self):
        text = json.dumps(self.QUIZ, ensure_ascii=False, indent=2)
        unit = qc_units.units_from_asset("quiz.json", text)[0]
        c = Q.Confirmed(
            tag="t", uid=unit.uid, artifact="quiz.json",
            unit_type="multiple_choice", book="StahlKids", printed=69,
            kind="duration", raw="2-8 weeks",
            value=qc_norm.locked_values("2-8 weeks")[0]["value"],
            quote="Full therapeutic benefits may take 2–8 weeks.")
        discarded: list[dict] = []
        # Every other kind is already over-represented, so the key swap is the
        # first edit tried — which is how the real walk reaches it (§6 asks for
        # all eight kinds and `MUTATION_RANK` spreads them).
        counts = {k: 9 for k in Q.MUTATION_NAMES}
        counts["key_swap"] = 0
        made = Q._make_mutant(c, {"t": {"quiz.json": text}},
                              FakePages(self.PAGE), discarded, counts, unit)
        if made is not None and made[0].mutation == "key_swap":
            self.fail("a key swap onto an option the page also states was "
                      "kept as a mutant: the item is still correct and the "
                      "judge's silence will be scored as a recall miss")
        self.assertTrue(
            [d for d in discarded if d["mutation"] == "key_swap"],
            "the key swap was neither kept nor discarded with a reason")

    def test_a_correct_key_swap_finding_counts_as_a_hit(self):
        import shutil
        tmp = Path(tempfile.mkdtemp(prefix="qc-calib-key-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "_qc"
        root.mkdir(parents=True)
        d = Q.judge_root(root) / "t" / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        # What a judge that found the moved key actually returns: the claim it
        # disputes, not the option pasted verbatim.
        qc_store.write_json_atomic(d / "round0-A1.json", {
            "call_id": "A1", "ok": True, "units": ["Q1"], "coverage": [],
            "findings": [{"unit": "Q1", "verified": True,
                          "kind": "contradicted", "severity": "high",
                          "said": "the key is marked on Guanfacine ER",
                          "problem": "the item keys the wrong option"}]})
        after = ("Guanfacine ER, an alpha-2A adrenergic receptor agonist "
                 "dosed at bedtime")
        doc = {"built_at": "x", "chapters": ["t"], "gold": [], "negatives": [],
               "controls": [], "counts": {},
               "mutants": [{"mid": "m", "tag": "t", "uid": "Q1",
                            "artifact": "quiz.json", "artifact_type": "quiz",
                            "unit_type": "multiple_choice",
                            "mutation": "key_swap", "book": "StahlKids",
                            "printed": 69, "quote": "q",
                            "before": "Atomoxetine", "after": after,
                            "confirmed_by": "the key moved"}]}
        s = Q.score(doc, root=root)
        self.assertEqual(
            s["recall"]["k"], 1,
            "a correct key-swap detection scored as a miss: the strict hit "
            "test asks for the whole option text inside the judge's `said`")


# ---------------------------------------------------------------------------
class TestControlsProveTheVerificationPath(unittest.TestCase):
    """§6's controls are the only test of §2.1 that reproduces the 2026-09-18
    incident — an agy run that never received its PDF and returned SUCCESS with
    an invented "verbatim" quote.

    `controls()` wraps `qc_judge.judge` in a bare `except Exception` and records
    `rejected=True` for anything it catches. A signature drift, an import
    error, a bad path out of `_control_call` — any of them prints "negative
    controls rejected 100%", and `verdict_line` checks only that rate. The one
    bar that is supposed to prove the mechanism still works is the one bar a
    broken mechanism passes.
    """

    class _Manifest:
        def __init__(self, path):
            self.path = str(path)

        def as_dict(self):
            return {"pages": [], "nonce": "n"}

    def _fake_control_call(self, workdir, **_kw):
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        bundle = workdir / "bundle.pdf"
        bundle.write_bytes(b"%PDF-1.4 fake")
        artifacts = workdir / "artifacts.md"
        artifacts.write_text("### Q1\nsome text\nNONCE abcdef\n",
                             encoding="utf-8")
        return (object(), self._Manifest(bundle), artifacts, "abcdef",
                {"Q1": "some text"})

    def test_a_crash_in_the_harness_is_not_a_rejection(self):
        import types

        def boom(*_a, **_k):
            raise TypeError("judge() got an unexpected keyword argument "
                            "'raw_dir'")

        stub = types.SimpleNamespace(judge=boom, manifest_pages=lambda m: [])
        with mock.patch.object(Q, "_control_call", self._fake_control_call), \
                mock.patch.object(Q, "qc_judge_module", lambda: stub):
            rows = Q.controls(repeats=2,
                              workdir=Path(tempfile.mkdtemp(prefix="ctl-")))
        self.assertFalse(
            any(r["rejected"] for r in rows),
            "a TypeError from a drifted qc_judge.judge signature was recorded "
            "as the negative control being rejected; §6's 100% bar then "
            "passes without the verification path running once")

    def test_a_control_that_never_reached_the_judge_blocks_go(self):
        # The same thing one layer up: whatever `controls()` decides, the GO
        # line must not read 100% off records whose gate is not a gate.
        rows = [{"control": "bundle_missing", "run": i, "rejected": True,
                 "gate": "TypeError", "reason": "unexpected keyword",
                 "attempts": 0, "bundle_present": False} for i in range(5)]
        rows += [{"control": "artifacts_truncated", "run": i, "rejected": True,
                  "gate": "TypeError", "reason": "unexpected keyword",
                  "attempts": 0, "bundle_present": True} for i in range(5)]
        m = Q._control_metrics({"controls": rows})
        self.assertEqual(
            m["overall"]["k"], 0,
            "controls that never reached the judge (attempts = 0) were "
            "counted as rejections")


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
