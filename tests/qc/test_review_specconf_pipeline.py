#!/usr/bin/env python3
"""Spec-conformance review, lens SPEC CONFORMANCE: four §4/§5.5/§5.9/§5.10
obligations the pipeline never performs. Every test here FAILS today and names
the sentence of QC_REVIEW_SPEC.md it holds the code to.

1. §2.5 / §5.5 / §5.10 — "Originals are snapshotted and COMMITTED before any
   change"; "committed by explicit path". No module in the QC mechanism runs
   git at all, so `_qc/orig/` (the only pristine copy, and the one qc_revert
   rebuilds from) lives in an untracked directory that `--all` and a `_qc`
   wipe both destroy.

2. §4 — "the agy version is recorded on every call; if it CHANGES MID-RUN, run
   the atomoxetine known-answer probe (StahlKids p.69, "2-8 weeks") and STOP if
   it fails." The version is recorded and never compared; nothing probes and
   nothing stops. The words are in `_stop_on_breaker`'s alert text only, where
   they tell the OWNER to run a probe by hand.

3. §5.5 / §5.8.2 — the circuit breaker fires on "a failed canary or parse". A
   judge whose coverage rows never match is rejected by the same validator, is
   retried the same three times and leaves the chapter `unjudged` the same way,
   but `_BREAKER_REASON` does not match the word qc_judge writes, so 68
   chapters in a row can burn on it without stopping.

4. §5.9 — "After every locked-value correction, search every artifact of the
   chapter (guide incl. high-yield and summary, facts, quiz, cards, legends)
   and every other chapter for the old value next to the same entity; attach
   every hit to the same finding." qc_fix.old_value_hits implements exactly
   this and no caller in the pipeline ever invokes it, so a value corrected in
   the quiz stays uncorrected and unmarked in the guide next to it.

    python -m unittest tests.qc.test_review_specconf_pipeline
"""
from __future__ import annotations

import inspect
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "app"))
sys.path.insert(0, str(HERE))

import qc_fix                                              # noqa: E402
import qc_run                                              # noqa: E402
import qc_store                                            # noqa: E402
from test_qc_run import (TAG, FINDING, FakeJudge,          # noqa: E402
                         RunCase, release_assets)

QC_MODULES = ("qc_run", "qc_store", "qc_revert", "qc_fix", "qc_sources",
              "qc_judge", "qc_lint", "qc_units", "qc_cite", "qc_norm")


class SnapshotIsNeverCommitted(RunCase):
    """§2.5: "Originals are snapshotted and committed before any change"."""

    def test_the_mechanism_runs_git_somewhere(self):
        callers = []
        for name in QC_MODULES:
            src = (REPO / "scripts" / f"{name}.py").read_text(encoding="utf-8")
            # A git invocation in any form: subprocess argv, a shell string, or
            # a library. Comments and prose are stripped first so the many
            # mentions of "git" in the docstrings do not pass this for free.
            code = re.sub(r"(?m)#.*$", "", src)
            code = re.sub(r'"""(?:.|\n)*?"""', "", code)
            if re.search(r"""['"]git['"]|\bgit\s+(?:add|commit)\b|"""
                         r"""\bimport\s+(?:git|pygit2|dulwich)\b""", code):
                callers.append(name)
        self.assertTrue(
            callers,
            "no QC module invokes git. qc_store.snapshot writes _qc/orig/<tag>/ "
            "and a manifest and stops there (qc_store.py, `snapshot`), and "
            "qc_run writes verdict.json / fixes.json / REPORT.md / state.json "
            "without committing any of them (qc_run.py `_save`, "
            "`write_report`). §5.5 requires the snapshot 'committed by explicit "
            "path' and §5.10 lists exactly which paths; as it stands the only "
            "pristine copy of an asset this pass is about to overwrite lives in "
            "an untracked directory, which is the one thing §2.5 ('never lose "
            "an asset') was written to prevent")

    def test_the_snapshot_at_least_lands_where_the_spec_says(self):
        """Control: the snapshot itself is taken and verified. The gap is only
        that nothing records it in git."""
        self.books_available()
        self.run_qc(self.opts(dry_run=True),
                    self.deps(judge=lambda **kw: {"ok": True}))
        self.assertTrue((self.root / "orig" / TAG / "quiz.json").exists())


class AgyVersionChangeIsNeverProbed(RunCase):
    """§4: a version change mid-run triggers a known-answer probe and a stop."""

    class VersionedJudge(FakeJudge):
        """A judge that reports a different agy version per chapter — an
        auto-update landing mid-backlog, which §4 pins AGY_CLI_DISABLE_AUTO_
        UPDATE=1 against and then plans for anyway."""

        def __init__(self, by_tag):
            super().__init__()
            self.by_tag = by_tag

        def __call__(self, *, tag, call, plan, **kw):
            out = super().__call__(tag=tag, call=call, plan=plan, **kw)
            out["agy_version"] = self.by_tag.get(tag, "1.2.6")
            return out

    def test_two_chapters_judged_by_two_binaries_stop_the_run(self):
        # Written when qc_judge exposed no probe at all, so §4's guard could
        # only ever resolve to "no probe is wired" — a failed probe, and a STOP.
        # The probe exists now (qc_judge.known_answer_probe, measured against
        # StahlKids p.69 on agy 1.2.8), so the contract has two halves and this
        # test pins the one it was written for: a version change that cannot be
        # cleared stops the run. The other half — a probe that passes lets the
        # run continue, with the seam recorded — is the test below.
        tags = ["study-005-serotonin-system", "study-035-lithium"]
        for t in tags:
            self.gh.add_release(t, release_assets())
        self.books_available()
        self.probe_ok = False
        judge = self.VersionedJudge({tags[0]: "1.2.6", tags[1]: "1.3.0"})
        code, out = self.run_qc(self.opts(tags=tags), self.deps(judge=judge))

        versions = {t: (qc_store.read_json(self.root / t / "verdict.json")
                        or {}).get("agy", {}).get("version") for t in tags}
        self.assertEqual(versions, {tags[0]: "1.2.6", tags[1]: "1.3.0"},
                         "the fixture did not actually change the version")
        self.assertEqual(
            code, qc_run.EXIT_STOP,
            f"qc_run judged both chapters with two different agy binaries "
            f"({versions}) and exited {code} with nothing said about it. §4: "
            f"\"the agy version is recorded on every call; if it changes "
            f"mid-run, run the atomoxetine known-answer probe (StahlKids p.69, "
            f"'2-8 weeks') and stop if it fails.\" qc_run writes the version "
            f"into each verdict (qc_run.py:1464) and never reads two of them "
            f"back, so a 24-51 hour backlog can silently be judged by two "
            f"models and the report cannot say where the seam is")

    def test_a_probe_that_passes_lets_the_run_go_on_and_records_the_seam(self):
        """§4 stops "if it fails" — a probe that passes is evidence, not noise.

        The run continues, and the chapter that straddles two binaries is still
        held so the report can say where the seam is (qc_run.status_holds).
        """
        tags = ["study-005-serotonin-system", "study-035-lithium"]
        for t in tags:
            self.gh.add_release(t, release_assets())
        self.books_available()
        self.probe_ok = True
        judge = self.VersionedJudge({tags[0]: "1.2.6", tags[1]: "1.3.0"})
        code, _ = self.run_qc(self.opts(tags=tags), self.deps(judge=judge))

        self.assertNotEqual(code, qc_run.EXIT_STOP,
                            "a probe that passed still stopped the run")
        self.assertTrue(self.probes, "the version changed and nothing probed")
        versions = {t: (qc_store.read_json(self.root / t / "verdict.json")
                        or {}).get("agy", {}).get("version") for t in tags}
        self.assertEqual(versions, {tags[0]: "1.2.6", tags[1]: "1.3.0"},
                         "the verdicts do not record which binary judged them")

    def test_a_known_answer_probe_exists_to_be_run(self):
        names = []
        for name in QC_MODULES + ("agy_attach",):
            mod = __import__(name)
            names += [n for n in dir(mod)
                      if "probe" in n.lower() or "known_answer" in n.lower()]
        self.assertTrue(
            names,
            "no module exposes the atomoxetine known-answer probe §4 requires. "
            "The only place the words appear is the Hebrew text of "
            "qc_run._stop_on_breaker (qc_run.py:1934), which asks the OWNER to "
            "run it by hand and gives him nothing to run")


class BreakerMissesACoverageRejection(unittest.TestCase):
    """§5.5 + §5.8.2: a judge that will not cover the units is a judge that is
    not answering, and three chapters of it must stop the run."""

    @staticmethod
    def _unjudged(reason: str) -> dict:
        return {"status": "unjudged", "notes": [],
                "judgments": [{"call_id": "A", "ok": False, "reason": reason}]}

    def test_a_canary_failure_counts(self):
        """Control: the reasons the breaker was built for do count."""
        self.assertEqual(
            qc_run._breaker_count(
                self._unjudged("rejected: canary: code for StahlKids p.69 not "
                               "returned"), 0), 1)

    def test_a_coverage_failure_counts_too(self):
        # The exact string qc_judge.coverage_problems produces and
        # agy_attach.ask_with_files stores as res.diagnostic, which
        # qc_judge.judge copies into verdict["reason"].
        reason = ("rejected: coverage: 31 units have no row "
                  "(Q1, Q2, Q3, Q4)")
        self.assertEqual(
            qc_run._breaker_count(self._unjudged(reason), 0), 1,
            "qc_run._BREAKER_REASON (qc_run.py:148) is "
            "`canary|nonce|parse|json|schema`, and qc_judge's coverage "
            "rejections all begin 'coverage:' (qc_judge.coverage_problems). "
            "A judge that answers about a third of the units is discarded, "
            "retried three times and leaves the chapter `unjudged` exactly as "
            "a canary miss does — but the breaker never counts it, so the run "
            "walks all 68 chapters spending three agy calls each on a judge "
            "that is not answering")


class OldValueSweepIsNeverRun(RunCase):
    """§5.9's cross-artifact / cross-chapter sweep."""

    # The corrected value also stands, uncorrected, in the study guide of the
    # SAME chapter, beside the same drug name — the case qc_fix.old_value_hits
    # was measured on (019: the onset repeated in sketchy_main.txt row 24).
    GUIDE = ("# פרק\n\n## מנגנון\n\nFluoxetine: ההשפעה מופיעה תוך 4 שבועות "
             "אצל רוב המטופלים.\n")

    def test_a_correction_reports_the_same_value_elsewhere(self):
        self.gh = type(self.gh)()
        self.gh.add_release(TAG, release_assets(
            study_guide=self.GUIDE.encode("utf-8")))
        self.books_available()
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})
        # The REAL fixer, not RunCase's FakeTier1: the sweep lives in
        # qc_fix.apply_tier1, so a fake stands exactly where the behaviour under
        # test would be. Everything else stays faked — this asks whether the
        # orchestrator's Tier-1 call reaches the sweep and carries `also_in`
        # back into the verdict, which is what §5.9 promises.
        code, _ = self.run_qc(self.opts(),
                              self.deps(judge=judge, tier1=qc_fix.apply_tier1))
        self.assertEqual(code, qc_run.EXIT_OK)
        v = self.verdict()
        rows = [f for f in (v.get("fixes") or []) if f.get("id") == "A-1"]
        self.assertTrue(rows, "the contradiction was not fixed at all")
        self.assertIn(
            "also_in", rows[0],
            "qc_fix.old_value_hits (qc_fix.py:1233) implements §5.9's sweep "
            "and nothing calls it: it appears in scripts/ only at its own "
            "definition. apply_tier1 (qc_fix.py:1529) never invokes it and "
            "qc_run never asks for it, so the fix row carries no record of "
            "where else the corrected value still stands. The learner reads "
            "the marked quiz item and then the same wrong number, unmarked, "
            "in the guide beside it (§2.4 'mark, never hide')")

    def test_the_sweep_function_itself_is_unreferenced(self):
        """Cheap, deterministic evidence for the same gap."""
        hits = []
        for p in sorted((REPO / "scripts").glob("*.py")):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if "old_value_hits" in line:
                    hits.append(f"{p.name}:{i}")
        self.assertGreater(
            len(hits), 1,
            f"old_value_hits is referenced only at {hits} — its own def line. "
            f"§5.9's sweep is dead code in the shipped pipeline")


class Tier1SourceIsNamedInHebrewEverywhere(unittest.TestCase):
    """§5.8 rules and §5.9: "When Kartisiot is the source, the finding and any
    note say so by name"; "For Kartisiot, the source is named"."""

    FIX = {
        "id": "A-3", "unit": "Q4", "asset": "quiz.json", "tier": 1,
        "kind": "content", "severity": "high", "status": "active",
        "date": "2026-09-23",
        "said": "3 קריטריונים", "correction": "5 קריטריונים",
        "book": "Kartisiot", "book_he": "סיכומי כרטיסיות שלב ב",
        "printed": 506, "source_quote": "חמישה קריטריונים",
    }

    def test_the_hebrew_erratum_line_names_the_kartisiot_in_hebrew(self):
        line = qc_run.errata_lines({"fixes": [self.FIX]})[0]["he"]
        self.assertIn(
            "סיכומי כרטיסיות שלב ב", line,
            f"qc_run.errata_lines (qc_run.py:1577) builds the owner's Hebrew "
            f"line from the raw qc_books key and produces {line!r}. The fix "
            f"row already carries `book_he` (qc_fix._fix_record, qc_fix.py:977) "
            f"and this function ignores it, so _qc/REPORT.md and the per-"
            f"chapter ntfy of §5.10.4 name the source 'Kartisiot' — the one "
            f"book owner decision 4 says must be named by its Hebrew title "
            f"wherever it appears")

    def test_the_erratum_carries_book_he_for_any_reader(self):
        rec = qc_run.errata_lines({"fixes": [self.FIX]})[0]
        self.assertEqual(
            rec.get("book_he"), "סיכומי כרטיסיות שלב ב",
            "the erratum drops `book_he` on the floor; only the app recovers "
            "it, by mapping the key itself (quiz_logic.book_display)")


if __name__ == "__main__":
    unittest.main()
