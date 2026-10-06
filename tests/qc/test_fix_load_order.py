#!/usr/bin/env python3
"""A real calibration build reads the chapter as published, not a test fixture.

2026-10-06: load_chapter tried tests/qc/fixtures BEFORE GitHub. The study-scope
set took chapter 001 from an old fixture — a quiz and a card set, no study guide
— instead of the release regenerated on 2026-10-01. It seeded zero items there
and would have spent two agy calls measuring nothing; the same order may have
fed chapters 019 and 042 of the first calibration from their fixtures too.

    python -m unittest tests.qc.test_fix_load_order
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_calibrate as Q                                         # noqa: E402
import qc_store                                                  # noqa: E402

TAG = "study-001-psychiatric-examination-of-child-and-adolescent"   # has a fixture


class FakeGh:
    def __init__(self, files=None, fail=False):
        self.files = files or {}
        self.fail = fail

    def list_assets(self, tag):
        if self.fail:
            raise qc_store.GhError("network is down")
        return [qc_store.Asset(name=n, asset_id=str(i), size=len(t.encode("utf-8")),
                               sha256="")
                for i, (n, t) in enumerate(self.files.items())]

    def download(self, tag, name, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(self.files[name], encoding="utf-8")
        return dest


class LoadOrder(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_the_published_release_wins_over_the_fixture(self):
        gh = FakeGh({"study_guide.md": "# המדריך שפורסם", "quiz.json": "[]"})
        got = Q.load_chapter(TAG, root=self.root, gh=gh, fetch=True)
        self.assertIn("study_guide.md", got,
                      "the build took the fixture, which has no study guide")
        self.assertEqual(got["study_guide.md"], "# המדריך שפורסם")

    def test_offline_the_fixture_still_builds(self):
        got = Q.load_chapter(TAG, root=self.root, fetch=False)
        self.assertIn("quiz.json", got)

    def test_when_github_fails_the_fixture_is_the_fallback(self):
        got = Q.load_chapter(TAG, root=self.root, gh=FakeGh(fail=True), fetch=True)
        self.assertIn("quiz.json", got)

    def test_no_fixture_and_no_github_is_an_error_not_an_empty_chapter(self):
        with self.assertRaises(Q.CalibrationError):
            Q.load_chapter("study-999-nothing-here", root=self.root,
                           gh=FakeGh(fail=True), fetch=True)


if __name__ == "__main__":
    unittest.main()
