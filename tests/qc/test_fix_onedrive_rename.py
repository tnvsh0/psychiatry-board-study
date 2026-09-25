#!/usr/bin/env python3
"""One locked rename must not cost a chapter.

The 144-chapter sweep of 2026-09-25 crashed chapter 085 with
`PermissionError: [WinError 5] Access is denied` renaming
`journal.json.tmp18292` over `journal.json`, and exited 2 over it. The repo
lives inside a OneDrive folder, so the sync engine holds a target open for a
few milliseconds now and then — and an indexer or a scanner does the same.

    python -m unittest tests.qc.test_fix_onedrive_rename
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_store                                             # noqa: E402


class RenameUnderALock(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_a_transient_lock_is_waited_out(self):
        calls = {"n": 0}
        real = qc_store.os.replace
        slept = []

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError(5, "Access is denied")
            return real(src, dst)

        src, dst = self.dir / "a.tmp", self.dir / "a.json"
        src.write_text("{}", encoding="utf-8")
        qc_store.os.replace = flaky
        try:
            qc_store.replace_with_retry(src, dst, sleep=slept.append)
        finally:
            qc_store.os.replace = real
        self.assertTrue(dst.exists())
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(slept), 2, "it did not back off between attempts")
        self.assertGreater(slept[1], slept[0], "the backoff is not growing")

    def test_a_permanent_lock_still_raises_with_both_paths(self):
        real = qc_store.os.replace

        def always(src, dst):
            raise PermissionError(5, f"Access is denied: {src!r} -> {dst!r}")

        src, dst = self.dir / "b.tmp", self.dir / "b.json"
        src.write_text('{"kept": true}', encoding="utf-8")
        qc_store.os.replace = always
        try:
            with self.assertRaises(PermissionError) as caught:
                qc_store.replace_with_retry(src, dst, attempts=2,
                                            sleep=lambda _s: None)
        finally:
            qc_store.os.replace = real
        self.assertIn("b.tmp", str(caught.exception))
        self.assertTrue(src.exists(), "the complete bytes were thrown away")

    def test_the_writer_uses_it(self):
        calls = {"n": 0}
        real = qc_store.os.replace

        def once(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(5, "Access is denied")
            return real(src, dst)

        qc_store.os.replace = once
        try:
            p = qc_store.write_json_atomic(self.dir / "c.json", {"x": 1})
        finally:
            qc_store.os.replace = real
        self.assertEqual(qc_store.read_json(p), {"x": 1})


if __name__ == "__main__":
    unittest.main()
