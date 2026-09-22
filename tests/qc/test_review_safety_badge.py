#!/usr/bin/env python3
"""Adversarial review, lens SAFETY: the badge claims a check that never ran.

§2.3 fails closed so that a chapter cannot "look clean because a file was
absent". qc_run.chapter_status enforces that against `approved` only, and its
comment gives the reason (scripts/qc_run.py:686-692):

    "`approved` is the only status that claims cleanliness ... A `fixed`
     chapter makes no such claim — it ships visible errata beside the material"

That reason is false against the app that draws it. app/quiz_logic.py:

  * `badge_text` (line 675) writes "נבדק מול ספרי הלימוד ב-<date>, N תיקונים"
    for EVERY status and appends the qualifier only for
    needs_owner / needs_regeneration / unjudged. `fixed` gets no qualifier.
  * `parse_qc_json` (line 658) reads `checked = status not in _NOT_CHECKED`,
    and `_NOT_CHECKED` (line 585) is
    {not_checked, unchecked, skipped, not_judged, unjudged, "לא נבדק"}.
    "fixed" is not in it, so every asset comes back checked=True and
    `asset_lines` prints "קוויז: 1 תיקונים" — never "לא נבדק".
  * qc_run._qc_assets (line 1160) stamps the CHAPTER status onto every asset,
    so nothing per-asset can carry the qualification either.

The run this reproduces is `python scripts/qc_run.py --chapters N --no-books`
on a machine that HAS the PDFs: --no-books only reaches qc_lint
(qc_run passes `use_books` to lint_chapter and nowhere else), so the judge
still verifies findings and still produces fixes, while
`impossible_citation` and `value_unsourced` — the two checks that read the
books — are recorded as skipped and never run. The chapter lands on `fixed`,
and the learner is told, without qualification, that it was checked against the
textbooks.

Nothing in qc.json carries `skipped` at all, so the app could not qualify it
even if it wanted to.

Every test here FAILS on the code as reviewed.

  python -m unittest tests.qc.test_review_safety_badge
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


class NoBooksStillBadgesAsChecked(unittest.TestCase):
    """A --no-books pass, read back through the real app code."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="qc-review-badge-"))
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
        # use_books=False is exactly what `--no-books` sets.
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

    def test_the_two_book_backed_checks_really_did_not_run(self):
        """The premise: this chapter's citations were never checked."""
        skipped = [s["check"] for s in self.verdict["lint"]["skipped"]]
        self.assertIn("impossible_citation", skipped)
        self.assertIn("value_unsourced", skipped)
        self.assertEqual(self.verdict["status"], "fixed")

    def test_the_badge_does_not_say_the_checks_were_skipped(self):
        badge = quiz_logic.badge_text(self.parsed)
        self.assertNotIn(
            "נבדק מול ספרי הלימוד", badge,
            f"the learner is told the chapter was checked against the "
            f"textbooks on a run where the citation and locked-value checks "
            f"never opened a book: {badge!r}")

    def test_qc_json_carries_the_skipped_checks_at_all(self):
        """Even a qualified badge is impossible while this is missing."""
        self.assertTrue(
            self.doc.get("skipped") or self.doc.get("lint", {}).get("skipped"),
            "qc.json has no record that any check was skipped, so neither the "
            "app nor the owner can tell this chapter from one checked in full "
            f"(keys: {sorted(self.doc)})")

    def test_no_asset_is_reported_checked_against_a_book_nobody_opened(self):
        by_name = {a["name"]: a for a in self.parsed["assets"]}
        quiz = by_name["quiz.json"]
        self.assertIsNot(
            quiz["checked"], True,
            "quiz.json is drawn as checked; parse_qc_json reads the chapter "
            "status 'fixed', which is not in quiz_logic._NOT_CHECKED, so "
            "asset_lines prints a corrections count and never 'לא נבדק'")

    def test_the_asset_lines_do_not_claim_a_clean_citation_check(self):
        lines = quiz_logic.asset_lines(self.parsed)
        self.assertTrue(
            any("לא נבדק" in ln for ln in lines if "וידאו" not in ln),
            f"every line but the video's reports a completed check: {lines}")


if __name__ == "__main__":
    unittest.main()
