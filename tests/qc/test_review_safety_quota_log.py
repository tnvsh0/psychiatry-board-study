#!/usr/bin/env python3
"""Adversarial review, lens SAFETY: a stale quota line pauses the backlog.

§4 allows exactly three sources for a quota stop: the JSON envelope's
status/error, stderr, and "a `RESOURCE_EXHAUSTED` line in THAT RUN's
`~/.gemini/antigravity-cli/log/cli-*.log`".

`agy_attach.quota_log_line` (agy_attach.py:601) implements "that run's" as two
approximations, and neither holds:

  1. **Newest file, no process binding.** It takes `max(logs, key=mtime)` over
     every `cli-*.log` in the directory. Any other agy process writing at the
     same moment — the plate pipeline's `agy_image`, a hand-run `agy` in
     another terminal — owns the newest log, and ITS rate limit is read as the
     judge's.
  2. **Newest 256 KB of that file, no line timestamp.** The only freshness test
     is the FILE's mtime. A `RESOURCE_EXHAUSTED` line written hours earlier,
     still inside `LOG_TAIL_BYTES` of the tail, is returned as this call's
     quota stop as soon as anything appends to the file.

The cost is not a wrong answer, it is a stopped backlog: `ask_with_files` raises
`QuotaPaused` with the reset time parsed out of that stale line, qc_run writes
`paused_quota{resume_after}` and exits 4, and the measured reset in the one real
lockout was 13h33m. `parse_reset_seconds` reads the time from the line itself,
so an old line pauses for its own old window.

Both tests are offline: they write a fake log directory and call the function.
Both FAIL on the code as reviewed.

  python -m unittest tests.qc.test_review_safety_quota_log
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

OLD_LINE = ("2026-09-18T09:14:02Z ERROR RESOURCE_EXHAUSTED (code 429): "
            "Individual quota reached for model gemini-3.1-pro-high. "
            "Resets in 13h33m26s.")
FRESH_NOISE = "2026-09-23T11:02:10Z INFO request ok in 21.4s\n"


class StaleLineInAFreshLog(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="qc-review-quota-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "log").mkdir(parents=True)

    def test_a_quota_line_from_an_earlier_call_is_read_as_this_one(self):
        """One log file, appended to across calls — which is what agy does
        whenever a process handles more than one request."""
        log = self.root / "log" / "cli-20260923_110000.log"
        # Hours of traffic, an old lockout in the middle, then this call's own
        # successful lines. Everything is well inside LOG_TAIL_BYTES.
        log.write_text(OLD_LINE + "\n" + FRESH_NOISE * 200,
                       encoding="utf-8")
        since = time.time()
        os.utime(log, None)            # this call really did touch the file

        self.assertIsNone(
            agy_attach.quota_log_line(since, gemini_root=self.root),
            "a RESOURCE_EXHAUSTED line that predates the call by hours was "
            "returned as this call's quota stop; the only freshness test is "
            "the FILE's mtime, and `Resets in 13h33m26s` on that line is what "
            "qc_run writes as resume_after")

    def test_another_agy_process_log_is_read_as_this_one(self):
        """The plate pipeline (agy_image) shares this log directory and a
        different quota. §4 permits QC to run beside it."""
        since = time.time()
        mine = self.root / "log" / "cli-20260923_110000.log"
        mine.write_text(FRESH_NOISE, encoding="utf-8")
        theirs = self.root / "log" / "cli-20260923_110002.log"
        theirs.write_text(
            "2026-09-23T11:00:03Z ERROR RESOURCE_EXHAUSTED (code 429): "
            "image generation quota. Resets in 4h00m00s.\n", encoding="utf-8")
        # Explicit, because NTFS mtime granularity would otherwise tie the two
        # and `max` would pick whichever glob returned first.
        os.utime(mine, (since + 1, since + 1))
        os.utime(theirs, (since + 2, since + 2))

        self.assertIsNone(
            agy_attach.quota_log_line(since, gemini_root=self.root),
            "the image pipeline's rate limit was read as the judge's and "
            "would pause the whole chapter backlog for 4 hours")

    def test_control_this_calls_own_quota_line_is_still_found(self):
        """PASSES. The detection itself works; only its scoping does not."""
        since = time.time()
        log = self.root / "log" / "cli-20260923_110005.log"
        log.write_text(FRESH_NOISE + OLD_LINE + "\n", encoding="utf-8")
        got = agy_attach.quota_log_line(since, gemini_root=self.root)
        self.assertIsNotNone(got)
        self.assertEqual(agy_attach.parse_reset_seconds(got), 13 * 3600 + 33 * 60 + 26)


if __name__ == "__main__":
    unittest.main()
