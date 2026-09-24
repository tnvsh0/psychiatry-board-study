#!/usr/bin/env python3
"""A verdict is about one page limit, or it is about nothing.

2026-09-24: the per-call page limit moved from 180 to 250 while the
calibration set was a third judged. The limit is not a detail of packaging —
it decides how a chapter is CUT INTO CALLS, and replanning the six chapters
already judged showed every one of them splitting differently (4->3, 5->4,
8->5, 6->4, 4->3, 6->3). A recall measured over both halves describes neither,
and what that number licenses is Tier 2 rewriting the owner's exam material.

So the limit is stamped like the set id: a chapter measured at another one
stops being done, calls made at another one are not counted, and a journal
that holds both cannot print GO.

    python -m unittest tests.qc.test_fix_limit_regime
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_calibrate as Q                                     # noqa: E402


class LimitRegime(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        Q.cal_root(self.root).mkdir(parents=True, exist_ok=True)

    def journal(self, sid="aaaaaaaaaaaaaaaa", limit=None):
        j = Q.RunJournal(self.root)
        j.bind(sid)
        if limit:
            j.bind_limit(limit)
        return j

    def test_a_chapter_judged_at_another_limit_is_re_run(self):
        j = self.journal(limit=180)
        j.finish("t", {"status": "approved"}, [])
        self.assertTrue(self.journal(limit=180).done("t"))
        self.assertFalse(self.journal(limit=250).done("t"),
                         "a chapter cut into calls at 180 was skipped at 250")

    def test_a_chapter_judged_before_the_stamping_is_re_run(self):
        """Fail-closed: no stamp is not evidence of the same limit."""
        j = self.journal()
        j.finish("t", {"status": "approved"}, [])
        j.data["chapters"]["t"].pop("page_limit", None)
        j.save()
        self.assertTrue(self.journal().done("t"))       # no limit bound yet
        self.assertFalse(self.journal(limit=250).done("t"))

    def test_calls_made_at_another_limit_do_not_count(self):
        j = self.journal(limit=180)
        j.add_call({"tag": "t", "call_id": "A1", "ok": True})
        j2 = self.journal(limit=250)
        j2.add_call({"tag": "t", "call_id": "A1", "ok": True})
        self.assertEqual([c["page_limit"] for c in j2.calls_of_set()], [250])
        self.assertEqual(len(j2.data["superseded_calls"]), 1,
                         "the 180-page call was deleted rather than set aside")

    def test_the_limit_change_sets_the_cached_judgments_aside(self):
        """qc_run reuses a cached judgment by call id — and at 250 the call
        with that id read a different bundle."""
        d = Q.judge_root(self.root) / "t" / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        (d / "round0-A1.json").write_text("{}", encoding="utf-8")
        j = self.journal(limit=180)
        j.add_call({"tag": "t", "call_id": "A1", "ok": True})
        self.assertTrue(self.journal(limit=250).data["previous_page_limit"])
        Q.supersede_judgments(self.root)
        self.assertFalse((d / "round0-A1.json").exists())
        self.assertTrue(list((Q.judge_root(self.root) / "t").glob(
            "judgments.superseded-*")))

    def test_a_fresh_journal_is_not_a_regime_change(self):
        self.assertFalse(Q.RunJournal(self.root).bind_limit(250))

    def test_the_go_line_refuses_a_journal_that_holds_both(self):
        # A run that changed the limit without rebinding: two stamps in one
        # list of calls, which is the shape no rate may be computed over.
        j = self.journal(limit=250)
        j.add_call({"tag": "t", "call_id": "A1", "ok": True})
        j.data["calls"].append({"tag": "t", "call_id": "B1", "ok": True,
                                "set_id": "aaaaaaaaaaaaaaaa", "page_limit": 180})
        j.save()
        doc = {"chapters": [], "set_id": "aaaaaaaaaaaaaaaa", "mutants": [],
               "negatives": [], "gold": [], "controls": []}
        s = Q.score(doc, root=self.root)
        self.assertTrue(s["limit_mismatch"])
        self.assertEqual(s["page_limit"], 250)
        go, why = Q.verdict_line(doc, s)
        self.assertFalse(go)
        self.assertTrue(any("תקרות עמודים" in w for w in why), why)

    def test_one_limit_throughout_says_nothing(self):
        j = self.journal(limit=250)
        j.add_call({"tag": "t", "call_id": "A1", "ok": True})
        m = Q._call_metrics({"chapters": [], "set_id": "aaaaaaaaaaaaaaaa"},
                            root=self.root)
        self.assertFalse(m["limit_mismatch"])
        self.assertEqual(m["page_limit"], 250)


if __name__ == "__main__":
    unittest.main()
