#!/usr/bin/env python3
"""Adversarial review, 2026-09-22: qc_run writes a qc.json the app cannot read.

`qc.json` is the only thing that carries a verdict from the QC pass to the
learner (§5.10 writes it, §5.11 draws it). Both ends are built and both are
green on their own tests, because neither side has a test that puts one's
output into the other's input. This file does exactly that: it runs a real
`qc_run.ChapterRun` over the real fixtures — only the judge and the fixer are
faked, exactly as tests/qc/test_qc_run.py fakes them — and reads the qc.json it
produced with the real `app/quiz_logic.parse_qc_json`.

Four fields do not survive the trip:

  errata[].asset     qc_run.errata_lines() never emits it, so
                     errata_for_asset() finds nothing and the per-asset line
                     says "קוויז: נבדק, ללא תיקונים" on a quiz that HAS a
                     verified correction — the app affirmatively telling the
                     learner there is nothing wrong (§2.4 "mark, never hide").
  errata[].source    written as a bare string plus sibling book/page keys;
                     the app reads a dict, so the card loses the quote, the
                     book and the page — the evidence the correction rests on.
  assets{}           written as {name: sha256}; the app reads {name: {status,
                     sha256}}, so every asset reports itself "checked", the
                     sha256 is dropped, and video.mp4 — which qc_run puts in a
                     separate `not_checked` list the app never reads — never
                     produces the "וידאו: לא נבדק" line §5.11 requires.
  date               written as `checked_at`; the app reads `date`/`judged_at`,
                     so the badge renders "נבדק מול ספרי הלימוד ב-?".

    python -m unittest tests.qc.test_review_adversarial_qc_json
"""
from __future__ import annotations

import contextlib
import io
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
import qc_store                                            # noqa: E402
import quiz_logic                                          # noqa: E402
from test_qc_run import (                                  # noqa: E402
    FINDING, FakeGh, FakeHygiene, FakeJudge, FakeSources, FakeTier1,
    Forbidden, Recorder, TAG, release_assets,
)


class QcJsonReachesTheApp(unittest.TestCase):
    """One verified contradiction on Q1, judged and fixed, then read by the app."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="qc-review-qcjson-"))
        cls.root = cls.tmp / "_qc"
        gh = FakeGh()
        gh.add_release(TAG, release_assets())
        deps = qc_run.Deps(
            gh=gh, sources=FakeSources(),
            judge=FakeJudge(responses={"A": {"ok": True,
                                             "findings": [FINDING]}}),
            hygiene=FakeHygiene(), tier1=FakeTier1(),
            tier2=Forbidden("tier 2"), revert=Forbidden("tier 2 revert"),
            notify=Recorder(), wait_for_network=lambda **k: True,
            cleanup=lambda cid, workspace=None: 0)
        opts = qc_run.Options(tags=[TAG], root=cls.root, use_books=False,
                              no_upload=True)
        with contextlib.redirect_stdout(io.StringIO()):
            qc_run.run(opts, deps)
        cls.verdict = qc_store.read_json(cls.root / TAG / "verdict.json")
        cls.doc = qc_store.read_json(cls.root / TAG / "qc.json")
        cls.parsed = quiz_logic.parse_qc_json(cls.doc)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_run_really_produced_one_correction(self):
        """The premise of every assertion below."""
        self.assertEqual(self.verdict["status"], "fixed")
        self.assertEqual(len(self.doc["errata"]), 1)
        self.assertEqual(self.parsed["n_errata"], 1)

    def test_the_correction_is_attached_to_the_asset_it_corrects(self):
        """§5.11 draws a chapter's corrections beside the asset they belong to.

        Today errata_lines() drops `asset`, so the quiz's own correction is
        invisible on the quiz and the per-asset line claims the opposite.
        """
        self.assertEqual(
            [e["id"] for e in quiz_logic.errata_for_asset(self.parsed,
                                                          "quiz.json")],
            ["A-1"],
            "the erratum carries no `asset`, so nothing attaches it to quiz.json")
        self.assertNotIn(
            "קוויז: נבדק, ללא תיקונים", quiz_logic.asset_lines(self.parsed),
            "the app tells the learner the quiz has no corrections while a "
            "verified contradiction on Q1 is in this very document")

    def test_the_correction_keeps_the_quote_book_and_page(self):
        """A correction the learner cannot check is a correction they must take
        on faith; §5.8 pays three agy calls precisely for the quote."""
        erratum = self.parsed["errata"][0]
        self.assertTrue(
            erratum["sources"],
            "errata_lines() writes `source` as a plain string beside `book` and"
            " `page`; quiz_logic._sources() reads a dict and drops all three")
        src = erratum["sources"][0]
        self.assertEqual(src["book"], "StahlKids")
        self.assertEqual(src["page"], 69)
        self.assertIn("2-8 weeks", src["quote"])

    def test_the_badge_says_when_the_chapter_was_checked(self):
        """qc_run writes `checked_at`; the app reads `date`/`judged_at`."""
        self.assertTrue(
            self.parsed["date"],
            f"the date is empty, so the badge reads "
            f"{quiz_logic.badge_text(self.parsed)!r}")
        self.assertIn(self.verdict["finished_at"][:10], self.parsed["date"])

    def test_the_video_is_declared_unchecked(self):
        """§3 leaves video to v2 and §5.11 makes the badge say so: an unchecked
        video under a "checked" badge reads as a video that passed."""
        self.assertIn("video.mp4", self.doc["not_checked"])   # qc_run's side
        self.assertIn(
            "וידאו: לא נבדק", quiz_logic.asset_lines(self.parsed),
            "`not_checked` is a key the app never reads, and video.mp4 is "
            "absent from `assets`, so the line is never drawn")

    def test_each_asset_carries_the_sha256_the_app_downloads(self):
        """The sha256 is how the app knows the bytes it holds are the bytes
        that were judged; qc_run writes it where the app reads a status."""
        # Written when qc.json still put a bare sha256 under each asset name.
        # That is the bug this file reported, and the fix gives every asset the
        # {status, sha256} object the app reads — so the assertion now names the
        # sha256 inside it. The second half is unchanged and still the point:
        # the status must never be the hash (spec conformance pins the same
        # contract from the other side, test_a_sha256_is_not_read_as_a_status).
        by_name = {a["name"]: a for a in self.parsed["assets"]}
        raw = self.doc["assets"]["quiz.json"]
        self.assertEqual(by_name["quiz.json"]["sha256"], raw["sha256"])
        self.assertNotEqual(
            by_name["quiz.json"]["status"], raw["sha256"],
            "the app read the sha256 as the asset's QC status")


if __name__ == "__main__":
    unittest.main()
