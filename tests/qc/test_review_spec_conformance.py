#!/usr/bin/env python3
"""Spec-conformance review tests. Each one FAILS against the current tree and
names the sentence of QC_REVIEW_SPEC.md it is holding the code to.

Two defects, both at seams no single builder owned:

1. `qc_run.write_qc_json` / `qc_run.errata_lines` write a `qc.json` that
   `app/quiz_logic.parse_qc_json` cannot read back. The keys do not line up, so
   the learner is shown a correction with no book, no printed page, no quote and
   no corrected value — which is precisely what §5.11 ("A mark shows the verified
   quote and printed page") and §2.4 ("Mark, never hide") require it to show.
   Nothing catches this today because each side is tested against its own idea
   of the document and never against the other's.

2. `qc_run.chapter_status` cannot see the coverage rows that failed
   verification, so §5.8 verification 2's approval bar ("Approval requires
   unchecked locked-value units <= a threshold set in calibration (start: 0)")
   is never applied: a chapter whose locked values were never checked is
   approved.

    python -m unittest tests.qc.test_review_spec_conformance
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "app"))
sys.path.insert(0, str(HERE))

import qc_run                                              # noqa: E402
import quiz_logic                                          # noqa: E402
from test_qc_run import (FakeHygiene, FakeJudge, FakeSources,  # noqa: E402
                         TAG, release_assets)
from test_qc_store import FakeGh                           # noqa: E402

# One verified, Tier-1-noted contradiction, in the shape qc_fix hands qc_run
# (tests/qc/test_qc_run.py's FakeTier1 returns exactly these keys, plus the
# `asset` and `tier` qc_run adds itself).
FIX = {
    "id": "A-1", "unit": "Q2", "asset": "quiz.json", "tier": 1,
    "kind": "contradicted", "severity": "high",
    "said": "two (or more) symptoms",
    "source_quote": "three (or more) of the following",
    "book": "DSM", "printed": 135,
    "correction": "three (or more)",
    "stable_id": "0" * 40,
}

VERDICT = {
    "tag": TAG, "status": "fixed",
    "started_at": "2026-09-22T19:00:00+00:00",
    "finished_at": "2026-09-22T20:00:00+00:00",
    "agy": {"version": "1.2.6", "model": "gemini-3.1-pro-high"},
    "inputs": {"quiz.json": {"sha256": "a" * 64, "asset_id": 1, "size": 10}},
    "outputs": {"quiz.json": {"sha256": "b" * 64, "uploaded": True}},
    "fixes": [FIX],
}


class QcJsonReachesTheApp(unittest.TestCase):
    """What qc_run publishes must be what the app reads. §5.10 -> §5.11."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-review-spec-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        opts = qc_run.Options(tags=[TAG], root=self.tmp / "_qc", dry_run=True)
        run = qc_run.ChapterRun(TAG, deps=qc_run.Deps(gh=None), opts=opts)
        run.inputs = dict(VERDICT["inputs"])
        run.outputs = dict(VERDICT["outputs"])
        path = run.write_qc_json(VERDICT)          # no upload: dry_run
        self.doc = json.loads(path.read_bytes().decode("utf-8"))
        self.qc = quiz_logic.parse_qc_json(self.doc)
        self.assertIsNotNone(self.qc, "the app could not parse qc.json at all")
        self.assertEqual(len(self.qc["errata"]), 1)
        self.erratum = self.qc["errata"][0]

    def test_the_mark_carries_the_book_page_and_quote(self):
        """§5.11: "A mark shows the verified quote and printed page"."""
        sources = self.erratum["sources"]
        self.assertTrue(
            sources,
            "the erratum reaches the app with no source at all: qc_run writes "
            "the book, the page and the quote as the flat keys book/page/source "
            "(qc_run.py:1191-1198) while quiz_logic._sources reads a nested "
            "source={book, printed_page, quote} (quiz_logic.py:287-316), so the "
            "learner is shown a correction he cannot check")
        self.assertEqual(str(sources[0]["book"]), "DSM")
        self.assertEqual(str(sources[0]["page"]), "135")
        self.assertIn("three (or more)", sources[0]["quote"])

    def test_the_mark_shows_old_and_new(self):
        """§5.11: a card's note "shows old -> new"; §5.9 Tier 1 records `was`
        and the source's value."""
        self.assertEqual(self.erratum["was"], FIX["said"])
        self.assertTrue(
            self.erratum["now"],
            "the corrected value never reaches the app: errata_lines emits "
            "`source` (the quote) and drops `correction`, and quiz_logic reads "
            "`now`/`correction` (quiz_logic.py:347)")

    def test_the_erratum_knows_which_asset_it_belongs_to(self):
        """§5.11: the guide, the legend and the item each show their own
        errata; the app selects them by asset name."""
        self.assertEqual(
            quiz_logic.errata_for_asset(self.qc, "quiz.json"), [self.erratum],
            "errata_lines (qc_run.py:1191-1199) does not copy the fix's "
            "`asset` through, so errata_for_asset matches nothing and the "
            "in-place corrections never render")

    def test_the_badge_carries_the_date(self):
        """§5.11: "נבדק מול ספרי הלימוד ב-<date>, N תיקונים"."""
        badge = quiz_logic.badge_text(self.qc)
        self.assertNotIn(
            "ב-?", badge,
            "qc_run writes the timestamp as `checked_at` (qc_run.py:920) while "
            "the app reads `date`/`judged_at` (quiz_logic.py:666), so the badge "
            f"the owner sees is: {badge!r}")

    def test_the_badge_says_the_video_was_not_checked(self):
        """§5.11: "with a per-asset line ("וידאו: לא נבדק")"; §3 keeps video
        out of v1 and the badge has to say so."""
        lines = quiz_logic.asset_lines(self.qc)
        self.assertTrue(
            any("וידאו" in ln for ln in lines),
            "qc_run states it in a `not_checked` list (qc_run.py:931) that "
            "quiz_logic.parse_qc_json never reads, and video.mp4 is absent from "
            f"the assets map, so the badge shows only: {lines}")

    def test_a_sha256_is_not_read_as_a_status(self):
        """§5.10: qc.json carries "status, per-asset sha256, errata"."""
        quiz = next(a for a in self.qc["assets"] if a["name"] == "quiz.json")
        self.assertEqual(
            quiz["sha256"], "b" * 64,
            "qc_run writes assets as {name: sha256} (qc_run.py:927-929) while "
            "the app expects {name: {status, sha256}}, so the sha256 lands in "
            f"the status field: status={quiz['status'][:16]!r}")
        self.assertEqual(quiz["n_errata"], 1)


class ApprovalNeedsTheLockedValuesChecked(unittest.TestCase):
    """§5.8 verification 2: "Approval requires unchecked locked-value units <= a
    threshold set in calibration (start: 0)"."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-review-spec-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "_qc"
        self.gh = FakeGh()
        self.gh.add_release(TAG, release_assets())

    def test_a_chapter_with_unchecked_locked_values_is_not_approved(self):
        judge = FakeJudge(default={"ok": True, "findings": [], "unchecked": 7})
        deps = qc_run.Deps(
            gh=self.gh, sources=FakeSources(), judge=judge,
            hygiene=FakeHygiene(),
            tier1=lambda **kw: {"assets": {}, "fixes": []},
            tier2=lambda **kw: {"assets": {}, "fixes": []},
            revert=lambda **kw: {"assets": {}},
            notify=lambda *a, **k: False,
            wait_for_network=lambda **k: True,
            cleanup=lambda cid, workspace=None: 0)
        opts = qc_run.Options(tags=[TAG], root=self.root, use_books=False,
                             no_upload=True)
        verdict = qc_run.ChapterRun(TAG, deps=deps, opts=opts).run()

        self.assertGreater(verdict["coverage"]["unchecked_locked_values"], 0)
        self.assertNotEqual(
            verdict["status"], "approved",
            "the verdict records "
            f"{verdict['coverage']['unchecked_locked_values']} locked values "
            "that were never verified, and the chapter is still approved: "
            "chapter_status (qc_run.py:577-599) takes judged/lint/fixes/"
            "owner_items/unverified_high and never sees the coverage counts, "
            "so §5.8's start-at-zero threshold is not applied anywhere")


if __name__ == "__main__":
    unittest.main()
