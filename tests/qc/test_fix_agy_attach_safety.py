#!/usr/bin/env python3
"""The three agy_attach safety fixes, pinned from the code's side.

tests/qc/test_review_safety_agy.py and tests/qc/test_review_safety_quota_log.py
are the acceptance criteria and state the outcomes: nothing outside a
conversation gets deleted, a page number is not a rate limit, another process's
lockout is not ours. This file pins the mechanism those outcomes rest on, so a
later change cannot satisfy them by accident:

  1. the id and workspace checks RAISE on their own (cleanup swallows the
     exception on purpose — see its docstring — and a caller that wants the
     failure calls the check),
  2. `429` counts only beside a word that means quota,
  3. the cli log is bound to the call by a size snapshot, not by mtime.

    python -m unittest tests.qc.test_fix_agy_attach_safety

No network, no agy, no books.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import agy_attach                                          # noqa: E402


class ConversationIdCheck(unittest.TestCase):

    def test_a_real_id_is_accepted(self):
        for good in ("c1", "conv-0", "0b19f38f-4c2a-4f3e-8a11-6d0b2c9e1f77",
                     "never-existed", "A.1_2"):
            self.assertEqual(agy_attach.check_conversation_id(good), good)

    def test_everything_that_is_not_one_component_raises(self):
        for bad in ("..", ".", "", "../..", "a/b", "a\\b", "/etc", "C:\\x",
                    "-leading-dash", " space", "..\\..\\brain"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    agy_attach.check_conversation_id(bad)

    def test_cleanup_deletes_nothing_for_a_bad_id_and_says_so(self):
        root = Path(tempfile.mkdtemp(prefix="qc-fix-store-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "brain" / "c1").mkdir(parents=True)
        (root / "brain" / "c1" / "note.txt").write_text("x", encoding="utf-8")

        freed = agy_attach.cleanup("../brain", gemini_root=root)

        self.assertEqual(freed, 0)
        self.assertTrue((root / "brain" / "c1" / "note.txt").exists())

    def test_a_good_id_still_frees_its_own_conversation(self):
        """The control: the refusal must not cost the deletion that keeps a
        91%-full disk from filling up."""
        root = Path(tempfile.mkdtemp(prefix="qc-fix-store-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "brain" / "c1").mkdir(parents=True)
        (root / "brain" / "c1" / "media.pdf").write_bytes(b"x" * 900)
        (root / "conversations").mkdir()
        (root / "conversations" / "c1.db").write_bytes(b"y" * 100)
        (root / "brain" / "c2").mkdir()

        self.assertEqual(agy_attach.cleanup("c1", gemini_root=root), 1000)
        self.assertFalse((root / "brain" / "c1").exists())
        self.assertFalse((root / "conversations" / "c1.db").exists())
        self.assertTrue((root / "brain" / "c2").exists())


class WorkspaceCheck(unittest.TestCase):

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="qc-fix-ws-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.ws = agy_attach.make_workspace("study-042-catatonia", "A",
                                            base=self.base)

    def test_a_make_workspace_directory_is_accepted(self):
        self.assertEqual(agy_attach.check_workspace(self.ws),
                         Path(os.path.realpath(str(self.ws))))

    def test_a_parent_traversal_raises_before_the_filesystem_is_touched(self):
        with self.assertRaises(ValueError):
            agy_attach.check_workspace(str(self.ws / ".."))

    def test_a_directory_that_is_not_a_workspace_raises(self):
        for bad in (self.base, self.ws.parent, Path("relative/dir")):
            with self.subTest(bad=str(bad)):
                with self.assertRaises(ValueError):
                    agy_attach.check_workspace(bad)

    def test_cleanup_still_removes_a_real_workspace(self):
        (self.ws / "bundle.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 100)
        freed = agy_attach.cleanup(None, gemini_root=self.base,
                                   workspace=str(self.ws))
        self.assertEqual(freed, 109)
        self.assertFalse(self.ws.exists())


class QuotaNeedsAQuotaWord(unittest.TestCase):

    def test_429_alone_is_not_a_quota_stop(self):
        for text in ('  File "cli.py", line 429, in main',
                     "received: -p \"... (StahlNeuro p. 429) ...\"",
                     "elapsed 429 ms", "429"):
            with self.subTest(text=text):
                self.assertIsNone(agy_attach.quota_reason(None, text, None))

    def test_429_beside_a_quota_word_still_is(self):
        for text in ("HTTP 429: quota exceeded for this project",
                     "rate limit hit (429)",
                     "RESOURCE_EXHAUSTED (code 429): Individual quota reached.",
                     "the account exceeded its quota"):
            with self.subTest(text=text):
                self.assertIsNotNone(agy_attach.quota_reason(None, text, None))

    def test_a_quota_word_on_another_line_does_not_carry_the_number(self):
        """The 40-character window is per line: the echoed prompt and a quota
        message arriving in the same stderr blob must not combine."""
        self.assertIsNone(agy_attach.quota_reason(
            None, "cites (StahlNeuro p. 429)\nchecking quota settings...", None))


class LogIsBoundToTheCall(unittest.TestCase):

    LINE = ("2026-09-23T11:00:03Z ERROR RESOURCE_EXHAUSTED (code 429): "
            "Individual quota reached. Resets in 4h00m00s.\n")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qc-fix-log-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "log").mkdir(parents=True)

    def test_a_log_that_did_not_grow_is_not_this_calls(self):
        """mtime is not evidence: a file can be touched without being written
        to, and the plate pipeline touches this directory."""
        log = self.root / "log" / "cli-20260923_110000.log"
        log.write_text(self.LINE, encoding="utf-8")
        baseline = agy_attach.log_snapshot(gemini_root=self.root)
        since = time.time()
        os.utime(log, None)

        self.assertIsNone(agy_attach.quota_log_line(
            since, gemini_root=self.root, baseline=baseline))

    def test_only_the_bytes_this_call_appended_are_read(self):
        log = self.root / "log" / "cli-20260923_110000.log"
        log.write_text(self.LINE, encoding="utf-8")       # a previous lockout
        baseline = agy_attach.log_snapshot(gemini_root=self.root)
        since = time.time()
        with open(log, "a", encoding="utf-8") as fh:      # this call: fine
            fh.write("2026-09-23T11:05:00Z INFO request ok in 21.4s\n")

        self.assertIsNone(agy_attach.quota_log_line(
            since, gemini_root=self.root, baseline=baseline))

    def test_a_quota_line_this_call_appended_is_found(self):
        log = self.root / "log" / "cli-20260923_110000.log"
        log.write_text("2026-09-23T10:59:00Z INFO starting\n", encoding="utf-8")
        baseline = agy_attach.log_snapshot(gemini_root=self.root)
        since = time.time()
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(self.LINE)

        got = agy_attach.quota_log_line(since, gemini_root=self.root,
                                        baseline=baseline)
        self.assertIsNotNone(got)
        self.assertEqual(agy_attach.parse_reset_seconds(got), 4 * 3600)

    def test_two_logs_growing_at_once_are_not_attributable(self):
        baseline = agy_attach.log_snapshot(gemini_root=self.root)
        since = time.time()
        mine = self.root / "log" / "cli-20260923_110000.log"
        mine.write_text("2026-09-23T11:00:01Z INFO judging\n", encoding="utf-8")
        theirs = self.root / "log" / "cli-20260923_110002.log"
        theirs.write_text(self.LINE, encoding="utf-8")

        self.assertIsNone(agy_attach.quota_log_line(
            since, gemini_root=self.root, baseline=baseline))


if __name__ == "__main__":
    unittest.main()
