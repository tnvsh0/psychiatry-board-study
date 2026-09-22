#!/usr/bin/env python3
"""Adversarial bug hunt — resume after a crash, at the journal steps.

Offline. Everything here reuses the fakes of tests/qc/test_qc_run.py, which
never touch GitHub, agy or the network.

Each test names one defect found by reading the code and confirmed by running
it. They are expected to FAIL until the defect is fixed; that is what they are
for.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_run                                              # noqa: E402
import qc_store                                            # noqa: E402
from test_qc_run import (FINDING, RunCase, TAG, FakeJudge)  # noqa: E402


class HalfFinishedSwapTests(RunCase):
    """§5.5's swap is five stages; nothing in the shipped run finishes one.

    `qc_store.resume_upload` exists, is documented as the recovery for exactly
    this, and is referenced by qc_store's own docstrings — but nothing outside
    tests/qc/test_qc_store.py ever calls it, and `Journal.pending_uploads()` is
    likewise never read by qc_run. So a laptop that sleeps (or a network fault)
    between `orig_renamed` and `promoted` leaves the release with NO asset
    under that name, and no later run repairs it.
    """

    # `upload()` swaps the changed assets in sorted order, so flashcards.json
    # is the first one and the one the injected failure lands on.
    ASSET = "flashcards.json"

    def test_a_crash_between_the_two_renames_loses_the_asset(self):
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})
        # The corrected bytes are uploaded as `<name>.qc-<sha8>`, then the
        # ORIGINAL is renamed aside — and the process dies right there.
        self.gh.fail_after["rename_asset"] = 1
        self.run_qc(self.opts(no_upload=False), self.deps(judge=judge))

        # The mid-swap state is no longer guaranteed: qc_run retries a GhError,
        # so the first run often completes the swap by itself. That is a better
        # outcome, not a missing bug — what this test exists to pin is that when
        # a run DOES stop between the two renames, a later run repairs it. So
        # the staged state is asserted only if it actually happened.
        names = self.gh.names(TAG)
        journal = qc_store.Journal(TAG, root=self.root)
        mid_swap = self.ASSET not in names
        if mid_swap:
            self.assertTrue([n for n in names
                             if n.startswith(f"{self.ASSET}.pre-qc-")])
            self.assertEqual(journal.pending_uploads(), [self.ASSET],
                             "the journal must know the swap is unfinished")

        # A second run is the whole point of the journal. Nothing calls
        # resume_upload, so safe_upload raises UploadUnresolved, the chapter is
        # counted as a crash, and the release stays without the asset.
        self.gh.fail_after.clear()
        self.run_qc(self.opts(no_upload=False), self.deps(judge=judge))
        self.assertIn(
            self.ASSET, self.gh.names(TAG),
            f"the release has no {self.ASSET} at all: the swap stopped between "
            f"the two renames and no later run finishes or rolls it back")

    def test_nothing_in_the_pipeline_calls_resume_upload(self):
        """The recovery routine is unreachable from the orchestrator."""
        sources = [p for p in (REPO / "scripts").glob("*.py")
                   if p.name != "qc_store.py"]
        callers = [p.name for p in sources
                   if "resume_upload(" in p.read_text(encoding="utf-8")]
        self.assertTrue(
            callers,
            "no module outside qc_store.py calls resume_upload(); a swap that "
            "stops part-way can only be finished by hand")


class JournalIsNeverFinishedTests(RunCase):
    """`Journal.resume()` is documented as "the next step to run, or None when
    the chapter is finished". It can never return None on a real chapter.

    Two of the eleven STEPS are only ever marked on a branch that most chapters
    do not take: `rejudged` is marked only when a Tier-2 patch forced another
    round, and `upload_pending` only from inside qc_store.safe_upload, i.e.
    only when an asset actually changed. A clean chapter therefore finishes
    with `verdict` marked and `rejudged` missing, and resume() points at a step
    that has already been deliberately skipped — which is also the step name
    `_pause_on_quota` writes into state.json and into the owner's alert.
    """

    def test_a_finished_chapter_still_reports_a_next_step(self):
        self.books_available()
        code, _ = self.run_qc(self.opts(), self.deps())
        self.assertEqual(code, qc_run.EXIT_OK)
        journal = qc_store.Journal(TAG, root=self.root)
        self.assertTrue(journal.done("verdict"), "the chapter did finish")
        self.assertIsNone(
            journal.resume(),
            f"a finished chapter still reports {journal.resume()!r} as its "
            f"next step, so nothing can ask the journal whether a chapter is "
            f"done")


if __name__ == "__main__":
    unittest.main()
