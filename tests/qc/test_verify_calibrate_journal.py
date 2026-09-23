#!/usr/bin/env python3
"""Independent verification of the run journal, after the fix.

Everything the journal holds is now filtered by which SET it is about, except
one thing: the calls.

  * `_judgments_of` refuses a verdict stamped with another set_id, or one
    written before `built_at` (qc_calibrate.py:2483-2500);
  * `RunJournal.done` refuses a `finished_at` written under another set_id
    (qc_calibrate.py:2242-2247);
  * `_call_metrics` reports `set_mismatch` when the journal names a different
    set from the one being scored (qc_calibrate.py:2903-2906).

But `RunJournal.bind` OVERWRITES `set_id` in place — it only files the old one
under `previous_set_id` — while `self.data["calls"]` keeps every call of every
run that ever used this journal. So after a rebuild, `set_mismatch` is False by
construction and §6's first-attempt canary rate, its median and p90 durations,
its `pages_per_call` and its `calls_before_first_quota` are all computed over
two experiments at once.

That matters because the canary rate is one of the four bars `verdict_line`
gates GO on, and the contamination runs in the flattering direction: a good run
against the old set carries a bad run against the new one over §6's 90%. The
first test measures it — 30 first-attempt calls from the superseded set plus 3
calls of the current set that each burned three attempts print 91%, on a set
whose own first-attempt rate is 0 of 3.

These tests FAIL against the current scripts/qc_calibrate.py and are left in
place deliberately. Nothing here runs agy, gh or git.

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

import qc_calibrate as Q                                   # noqa: E402


class Journal(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-verify-journal-"))
        self.root = self.tmp / "_qc"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def run_of(self, sid, *, n, attempts, day, ok=True, raised=None):
        """`n` calls of one run, recorded the way `_timed_judge` records them."""
        j = Q.RunJournal(self.root)
        j.bind(sid)
        for i in range(n):
            rec = {"tag": "t", "at": f"2026-09-{day:02d}T00:{i:02d}:00",
                   "call_id": f"{sid[:1]}{i}", "kind": "A",
                   "duration_s": 10.0, "attempts": attempts, "ok": ok,
                   "canary_verified": ok, "pages": 100}
            if raised:
                rec["raised"] = raised
            j.add_call(rec)
        return j


# ---------------------------------------------------------------------------
class TestCallsBelongToOneSet(Journal):
    """§6's per-call numbers describe the run that produced THIS set's
    verdicts. A rebuild starts a new experiment (`build()` supersedes the old
    judgments for exactly that reason, qc_calibrate.py:2073-2078) and the calls
    that measured the old one are not evidence about the new one."""

    def test_the_canary_rate_drops_a_superseded_run_s_calls(self):
        self.run_of("OLDSET", n=30, attempts=1, day=20)
        self.run_of("NEWSET", n=3, attempts=3, day=24)
        m = Q._call_metrics({"chapters": [], "set_id": "NEWSET"},
                            root=self.root)
        self.assertEqual(
            m["canary_first_attempt"]["n"], 3,
            f"the canary bar was computed over "
            f"{m['canary_first_attempt']['n']} calls, 30 of which measured the "
            f"superseded set; this set's own first-attempt rate is 0 of 3")

    def test_the_mixed_rate_is_what_carries_the_go_line(self):
        self.run_of("OLDSET", n=30, attempts=1, day=20)
        self.run_of("NEWSET", n=3, attempts=3, day=24)
        m = Q._call_metrics({"chapters": [], "set_id": "NEWSET"},
                            root=self.root)
        self.assertLess(
            m["canary_first_attempt"]["rate"] or 0, Q.BAR_CANARY_FIRST,
            f"{m['canary_first_attempt']['k']}/"
            f"{m['canary_first_attempt']['n']} = "
            f"{m['canary_first_attempt']['rate']:.1%} clears §6's "
            f"{Q.BAR_CANARY_FIRST:.0%} first-attempt bar, and every call that "
            f"cleared it was made against a set whose verdicts this report "
            f"already refuses to count")

    def test_a_quota_abort_of_the_old_run_is_not_this_run_s(self):
        self.run_of("OLDSET", n=2, attempts=0, day=20, ok=False,
                    raised="QuotaPaused")
        self.run_of("NEWSET", n=4, attempts=1, day=24)
        m = Q._call_metrics({"chapters": [], "set_id": "NEWSET"},
                            root=self.root)
        self.assertEqual(
            m["quota_aborts"], 0,
            "§6 asks how many pro-high calls this run made before its first "
            "429; the aborts counted here happened during the run that "
            "measured the superseded set")

    def test_set_mismatch_notices_that_the_journal_spans_two_sets(self):
        # Nothing else does. `bind` files the old id under `previous_set_id`
        # and then makes `set_id` equal to the new one, so the one flag that
        # exists for "these numbers are two experiments" reads False.
        self.run_of("OLDSET", n=30, attempts=1, day=20)
        self.run_of("NEWSET", n=3, attempts=3, day=24)
        j = Q.RunJournal(self.root)
        self.assertEqual(j.data.get("previous_set_id"), "OLDSET",
                         "the journal does record that the set changed")
        m = Q._call_metrics({"chapters": [], "set_id": "NEWSET"},
                            root=self.root)
        self.assertTrue(
            m["set_mismatch"],
            "the journal still holds 30 calls made against OLDSET, and "
            "set_mismatch — the only line that would tell the owner the "
            "numbers describe two experiments — says they do not")


# ---------------------------------------------------------------------------
class TestTheFixedJournalRulesStayFixed(Journal):
    """These pass today; they pin the parts of the fix that did land."""

    def test_a_chapter_finished_under_another_set_is_not_done(self):
        j = Q.RunJournal(self.root)
        j.bind("OLDSET")
        j.finish("t", {"status": "approved"}, [])
        self.assertTrue(j.done("t"))
        j2 = Q.RunJournal(self.root)
        j2.bind("NEWSET")
        self.assertFalse(
            j2.done("t"),
            "a chapter measured against the old seeded bytes must be judged "
            "again, not skipped with 'כבר נשפט'")

    def test_fail_clears_finished_so_the_run_comes_back(self):
        j = Q.RunJournal(self.root)
        j.bind("S")
        j.finish("t", {"status": "approved"}, [])
        j.fail("t", "status unjudged: canary failed")
        self.assertFalse(j.done("t"))
        m = Q._call_metrics({"chapters": ["t"], "set_id": "S"},
                            root=self.root)
        self.assertIn("t", m["failed_chapters"],
                      "§2.3 is fail-closed: an unjudged chapter has to appear "
                      "in the GO line's reasons")

    def test_a_chapter_never_attempted_is_reported_unfinished(self):
        j = Q.RunJournal(self.root)
        j.bind("S")
        j.finish("a", {"status": "approved"}, [])
        m = Q._call_metrics({"chapters": ["a", "b"], "set_id": "S"},
                            root=self.root)
        self.assertEqual(m["unfinished_chapters"], ["b"])

    def test_a_quota_abort_does_not_consume_a_canary_statistic(self):
        self.run_of("S", n=1, attempts=0, day=24, ok=False,
                    raised="QuotaPaused")
        self.run_of("S", n=4, attempts=1, day=24)
        m = Q._call_metrics({"chapters": [], "set_id": "S"}, root=self.root)
        self.assertEqual(m["canary_first_attempt"]["n"], 4)
        self.assertEqual(m["canary_first_attempt"]["k"], 4)

    def test_a_discarded_call_stays_in_the_canary_denominator(self):
        self.run_of("S", n=5, attempts=1, day=24)
        self.run_of("S", n=5, attempts=3, day=24, ok=False)
        m = Q._call_metrics({"chapters": [], "set_id": "S"}, root=self.root)
        self.assertEqual(m["canary_first_attempt"]["n"], 10,
                         "a call that burned all three attempts is the "
                         "judge's worst behaviour and §6's bar is about it")
        self.assertEqual(m["canary_first_attempt"]["k"], 5)


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
