#!/usr/bin/env python3
"""Lever 2's measurement: the answer key must not sit in the judge's workspace.

`--measure-limit` builds bundle.pdf and artifacts.md, then writes
`measurement.json` NEXT TO THEM — and that file carries the three canary codes
and the nonce in plain text. Its own printed instructions send the owner to that
directory:

    the agy call is the owner's to make: attach bundle.pdf and artifacts.md
    from this directory, send `prompt` with gemini-3.1-pro-high, then …

§5.6 says the opposite, and says why: each call runs in "a fresh directory
outside the repo holding exactly `bundle.pdf` and `artifacts.md`; cwd is that
directory", because — scripts/agy_attach.py:34 — "agy resolves what it likes
relative to cwd, and a judge that can see the repo can read" what it must not.
An agy run started in the measurement directory can answer all three codes and
the nonce out of measurement.json without opening the bundle, and the whole
question this experiment exists to answer is whether a judge reads the END of a
bundle that size (§6, "the largest bundle whose last-10% canary passes"). A
measurement that can pass without the bundle decides the page limit for all 68
backlog chapters on nothing.

This is §2.1's own incident, one directory over: an agy run that never received
its PDF returned SUCCESS with confident JSON and an invented verbatim quote.

WHAT IS PINNED HERE

  no code and no nonce is readable in the directory the owner is told to attach
  from, other than inside the two files a judge is supposed to read.

The test builds a real bundle (no agy call — tests/qc/__init__.py sets
QC_NO_AGY) and needs the books, so it skips where they are absent.

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

import qc_sources                                           # noqa: E402
from test_qc_sources import (DAY_019, HAVE_BOOKS, TAG_019,   # noqa: E402
                             fixture_units)

ATTACHED = {"bundle.pdf", "artifacts.md"}


@unittest.skipUnless(HAVE_BOOKS, "Text BOOKS/ not on this machine")
class MeasurementWorkspaceTests(unittest.TestCase):
    TARGET = 60

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="qc-measure-review-"))
        units, cites = fixture_units()
        cls.rec = qc_sources.measure_limit(
            tag=TAG_019, day=DAY_019, units=units, cites=cites,
            target=cls.TARGET, work_dir=cls.tmp / "m", search=False)
        cls.here = Path(cls.rec["bundle"]).parent

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def others(self) -> list[Path]:
        """Every file in the workspace that is not one of the two attachments."""
        return [p for p in sorted(self.here.iterdir())
                if p.is_file() and p.name not in ATTACHED]

    def test_no_canary_code_is_readable_beside_the_bundle(self):
        codes = [row["code"] for row in self.rec["canary"]]
        self.assertEqual(len(codes), 3)
        leaked = []
        for path in self.others():
            blob = path.read_bytes().decode("utf-8", "replace")
            for code in codes:
                if code in blob:
                    leaked.append(f"{path.name} carries {code}")
        self.assertEqual(
            leaked, [],
            "the canary's answers are in the directory the judge is pointed "
            "at:\n  " + "\n  ".join(leaked))

    def test_the_nonce_is_not_readable_beside_the_bundle(self):
        nonce = self.rec["nonce"]
        self.assertTrue(nonce)
        leaked = [p.name for p in self.others()
                  if nonce in p.read_bytes().decode("utf-8", "replace")]
        self.assertEqual(leaked, [],
                         f"the nonce is readable in {leaked} without opening "
                         f"artifacts.md")

    def test_the_workspace_holds_exactly_the_two_files_to_attach(self):
        """§5.6's shape, which `how_to_run` tells the owner this directory is."""
        self.assertIn("from this directory", self.rec["how_to_run"])
        self.assertEqual(sorted(p.name for p in self.here.iterdir()),
                         sorted(ATTACHED))


if __name__ == "__main__":
    unittest.main()
