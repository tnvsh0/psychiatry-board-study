#!/usr/bin/env python3
"""Adversarial review, lens SAFETY: scripts/agy_attach.py.

Two things are demonstrated here, both offline and both against temp
directories — agy is never run and the owner's real `.gemini` store is never
touched (every call passes an explicit `gemini_root`).

  1. `cleanup()` deletes by string-formatting a conversation id into a path and
     handing the result to `shutil.rmtree`. The id is never validated, so a
     value that is not a conversation id deletes more than one conversation.
  2. The quota detector matches `\\b429\\b` against agy's STDERR. A stack trace
     line number, or an echoed prompt, is enough to pause the whole backlog.

  python -m unittest tests.qc.test_review_safety_agy
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

import agy_attach                                          # noqa: E402


class CleanupBlastRadiusTests(unittest.TestCase):
    """`cleanup` is called by qc_run with whatever the judge reported as its
    conversation id (qc_run.py ChapterRun._judge_one -> deps.agy_cleanup)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qc-gemini-root-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # A store with two conversations in it, as the owner's really is.
        for cid in ("0b19f38f", "3143f5bb"):
            (self.root / "brain" / cid).mkdir(parents=True)
            (self.root / "brain" / cid / "note.txt").write_text("x")
            (self.root / "conversations").mkdir(exist_ok=True)
            (self.root / "conversations" / f"{cid}.db").write_text("db")
        # Something of the owner's that is not a conversation at all.
        (self.root / "log").mkdir()
        (self.root / "log" / "cli-20260922.log").write_text("keep me")

    def test_a_conversation_id_of_dotdot_empties_the_whole_store(self):
        agy_attach.cleanup("..", gemini_root=self.root)
        self.assertTrue(
            (self.root / "brain" / "0b19f38f" / "note.txt").exists(),
            "cleanup('..') deleted brain/ for every conversation")
        self.assertTrue((self.root / "log" / "cli-20260922.log").exists(),
                        "cleanup('..') deleted the cli logs as well")

    def test_a_workspace_of_dotdot_deletes_its_parent(self):
        """The same shape on the second argument: qc_run passes the workspace
        string straight through from the judge's answer
        (ChapterRun._judge_one -> deps.agy_cleanup(cid, workspace=j.workspace)).

        DELIBERATELY SANDBOXED. The parent here is a directory this test made
        and owns. Do NOT rewrite this to use `mkdtemp()`'s own parent: that is
        the machine's %TEMP%, and cleanup() will start deleting it — measured,
        by doing it once.
        """
        base = Path(tempfile.mkdtemp(prefix="qc-blast-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        ws = base / "workspace"
        ws.mkdir()
        keep = base / "somebody-elses-file.txt"
        keep.write_text("not mine")

        agy_attach.cleanup(None, gemini_root=self.root,
                           workspace=str(ws / ".."))
        self.assertTrue(keep.exists(),
                        "a workspace of '<ws>/..' deleted its parent directory "
                        "and everything beside it")


class QuotaMisfireTests(unittest.TestCase):
    """`quota_reason` decides whether the backlog stops for up to 27 hours."""

    def test_a_stack_trace_line_number_reads_as_a_rate_limit(self):
        stderr = ('Traceback (most recent call last):\n'
                  '  File "C:\\agy\\cli.py", line 429, in main\n'
                  '    raise RuntimeError("boom")\n'
                  'RuntimeError: boom')
        self.assertIsNone(
            agy_attach.quota_reason(None, stderr, None),
            "a traceback line number was read as a quota stop; the run "
            "journals paused_quota and exits 4")

    def test_an_echoed_prompt_reads_as_a_rate_limit(self):
        """agy echoes its argv on an argument error, and the judge's prompt
        carries page numbers — `(StahlNeuro p. 429)` is in the 042 quiz, which
        is the very string qc_norm.py:434 records as the reason the detector
        may not look for "429" in text."""
        stderr = ('error: unexpected argument; received: -p "... unit Q7 '
                  'cites (StahlNeuro p. 429) ..."')
        self.assertIsNone(
            agy_attach.quota_reason(None, stderr, None),
            "a page number echoed in stderr was read as a quota stop")

    def test_a_real_quota_line_is_still_detected(self):
        """The control: the finding is about the false positives, not about
        loosening the detector."""
        real = ("RESOURCE_EXHAUSTED (code 429): Individual quota reached. "
                "Resets in 13h33m26s.")
        self.assertIsNotNone(agy_attach.quota_reason(None, "", real))
        self.assertEqual(agy_attach.parse_reset_seconds(real),
                         13 * 3600 + 33 * 60 + 26)


if __name__ == "__main__":
    unittest.main()
