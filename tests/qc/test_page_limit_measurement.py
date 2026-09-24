#!/usr/bin/env python3
"""§6's page-limit experiment: everything up to the agy call, and the scoring.

"The largest bundle whose last-10% canary passes", trying 120, 180 and 250
(§6). Measured on six released chapters (2026-09-24, `qc_sources.plan` only, no
agy call): 250 instead of 180 takes 013 from 4 calls to 3, 019 from 8 to 5, 029
from 6 to 3, 042 from 3 to 2, 047 from 4 to 3 and 070 from 7 to 5 — 32 calls
down to 21 over those six chapters. That is worth an experiment and must not be
worth a guess, so `--measure-limit` prepares one and stops: the agy call is the
owner's.

WHAT IS PINNED HERE

  the canary   three pages at 5%, 50% and 95% of the bundle, distinct, and the
               95% one never sliding to the front — the tail is the measurement.
  the prompt   the real one qc_judge would send, naming the pages and NEVER the
               codes.
  the scoring  --check-answer reports which of the three verified, through
               qc_judge's own validator, so a limit cannot pass here on a
               friendlier rule than the real run applies.
  threading    --page-limit reaches the splits at 120, 180 and 250 without
               dropping a unit.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

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

import qc_cite                                             # noqa: E402
import qc_sources                                          # noqa: E402
import qc_units                                            # noqa: E402
from test_qc_sources import (DAY_019, HAVE_BOOKS, TAG_019,  # noqa: E402
                            FakeBooksMixin, fixture_units, needs_books,
                            unit as make_unit)


def fake_manifest(n: int, book: str = "Dulcan") -> dict:
    """A manifest of `n` stamped pages, in bundle order."""
    return {"pages": [{"bundle_page": i + 1, "book": book, "printed": [100 + i],
                       "pdf_page": i, "reason": "cited",
                       "code": f"C{i:05d}"} for i in range(n)],
            "nonce": "NONCE12345AB"}


class CanaryTests(unittest.TestCase):
    def test_three_pages_at_the_fractions_asked_for(self):
        rows = qc_sources.measure_canary(fake_manifest(100))
        self.assertEqual([r["fraction"] for r in rows], [0.05, 0.50, 0.95])
        self.assertEqual([r["bundle_page"] for r in rows], [6, 51, 95])
        self.assertEqual(len({r["code"] for r in rows}), 3)

    def test_the_tail_code_is_inside_the_last_tenth(self):
        """§5.8's rule, which is also what the limit is being measured against:
        at least one canary page in the last 10% of the bundle."""
        for n in (12, 45, 100, 180, 250):
            rows = qc_sources.measure_canary(fake_manifest(n))
            tail = max(rows, key=lambda r: r["fraction"])
            self.assertGreaterEqual(tail["bundle_page"], n * 0.9,
                                    f"{n}-page bundle: {tail}")

    def test_a_crowded_bundle_never_moves_the_tail_to_the_front(self):
        rows = qc_sources.measure_canary(fake_manifest(2))
        tail = max(rows, key=lambda r: r["fraction"])
        self.assertEqual(tail["bundle_page"], 2)

    def test_an_empty_bundle_is_an_error(self):
        with self.assertRaises(qc_sources.PlanError):
            qc_sources.measure_canary({"pages": []})

    def test_the_tuples_are_the_shape_the_judge_takes(self):
        rows = qc_sources.measure_canary(fake_manifest(30))
        self.assertEqual(qc_sources.canary_tuples(rows),
                         [(r["book"], r["printed"], r["code"]) for r in rows])


class CheckAnswerTests(unittest.TestCase):
    """Scoring a reply, with no bundle and no book on the machine."""

    def setUp(self):
        self.manifest = fake_manifest(100)
        self.rows = qc_sources.measure_canary(self.manifest)
        self.rec = {"tag": TAG_019, "target_pages": 100, "pdf_pages": 100,
                    "canary": self.rows, "nonce": self.manifest["nonce"],
                    "manifest": self.manifest}

    def reply(self, rows, nonce=None):
        return {"canary": rows, "nonce": nonce or self.manifest["nonce"],
                "coverage": [], "findings": []}

    def all_three(self):
        return [{"page": f"{r['book']} p.{r['printed'][0] if isinstance(r['printed'], list) else r['printed']}",
                 "code": r["code"]} for r in self.rows]

    def test_all_three_codes_pass(self):
        res = qc_sources.check_answer(self.rec, self.reply(self.all_three()))
        self.assertEqual(res["verified"], 3)
        self.assertTrue(res["tail_verified"])
        self.assertTrue(res["nonce_ok"])
        self.assertEqual(res["problems"], [])
        self.assertTrue(res["passes"])

    def test_the_front_codes_alone_do_not_pass(self):
        """The measurement: a judge that read the first pages and stopped."""
        rows = self.all_three()[:2]
        res = qc_sources.check_answer(self.rec, self.reply(rows))
        self.assertEqual(res["verified"], 2)
        self.assertFalse(res["tail_verified"])
        self.assertFalse(res["passes"])
        self.assertTrue(res["problems"])
        codes = {r["fraction"]: r["verified"] for r in res["codes"]}
        self.assertEqual(codes, {0.05: True, 0.50: True, 0.95: False})

    def test_an_invented_code_does_not_pass(self):
        rows = [{"page": "Dulcan p.100", "code": "ZZZZZZ"}]
        res = qc_sources.check_answer(self.rec, self.reply(rows))
        self.assertEqual(res["verified"], 0)
        self.assertFalse(res["passes"])

    def test_the_nonce_is_checked_too(self):
        res = qc_sources.check_answer(self.rec,
                                      self.reply(self.all_three(), nonce="X"))
        self.assertFalse(res["nonce_ok"])
        self.assertFalse(res["passes"])
        self.assertIn("nonce", res["nonce_problem"])

    def test_a_reply_that_is_not_an_object_is_refused(self):
        with self.assertRaises(qc_sources.PlanError):
            qc_sources.check_answer(self.rec, json.dumps([1, 2, 3]))

    def test_the_reply_may_arrive_as_text(self):
        res = qc_sources.check_answer(
            self.rec, json.dumps(self.reply(self.all_three())))
        self.assertTrue(res["passes"])


class PageLimitThreadingTests(FakeBooksMixin, unittest.TestCase):
    """§5.7's split, at each of §6's three candidate limits."""

    LIMITS = (120, 180, 250)

    def setUp(self):
        self.fake_books()
        self.no_synopsis()
        self.empty_guide = self.fake_guide({}, day=1)
        self.big_guide = self.fake_guide({"Dulcan": "עמ' 200-260"}, day=1)

    def _units(self):
        # 20 units, each citing a different run of pages, so the chapter cannot
        # fit one call at any of the three limits.
        return [make_unit(f"Q{i}", "quiz.json",
                          f"שאלה {i} (Dulcan p. {100 + i * 7}, "
                          f"{300 + i * 11}) (DSM p. {400 + i * 9})")
                for i in range(1, 21)]

    def _plan(self, limit, guide):
        units = self._units()
        calls = qc_sources.plan("study-019-x", 1, units,
                                qc_cite.cites_by_unit(units),
                                page_limit=limit, reading_guide=guide)
        planned = [uid for c in calls for uid in c.units]
        self.assertEqual(sorted(planned), sorted(u.uid for u in units),
                         f"limit {limit}")
        self.assertEqual(len(planned), len(set(planned)))
        return calls

    def test_no_unit_is_dropped_and_the_split_respects_the_limit(self):
        seen = {}
        for limit in self.LIMITS:
            calls = self._plan(limit, self.empty_guide)
            for c in calls:
                self.assertLessEqual(c.pdf_page_count, limit,
                                     f"limit {limit}, {c.brief()}")
            seen[limit] = len(calls)
        # A bigger bundle is never more calls: that is the whole point of §6's
        # measurement, and a split that did not shrink would mean the limit is
        # not what decides the cost.
        self.assertGreater(seen[120], seen[250])
        self.assertGreaterEqual(seen[120], seen[180])
        self.assertGreaterEqual(seen[180], seen[250])

    def test_context_that_fits_nowhere_goes_over_the_limit_and_says_so(self):
        """§5.7's "never drop pages" outranks the limit, at every limit.

        61 printed Dulcan pages of reading guide on a chapter whose units
        already fill their calls: the pages are carried anyway and the call that
        carries them records it, which is how the measurement can tell a bundle
        that ran over from one that did not.
        """
        for limit in self.LIMITS:
            calls = self._plan(limit, self.big_guide)
            over = [c for c in calls if c.pdf_page_count > limit]
            for c in over:
                self.assertTrue(
                    any("over the" in n for n in c.notes),
                    f"limit {limit}: {c.brief()} is over and says nothing")

    @needs_books
    def test_the_real_chapter_keeps_every_unit_at_every_limit(self):
        units, cites = fixture_units()
        for limit in self.LIMITS:
            calls = qc_sources.plan(TAG_019, DAY_019, units, cites,
                                    page_limit=limit)
            planned = [uid for c in calls for uid in c.units]
            self.assertEqual(sorted(planned),
                             sorted(u.uid for u in units), f"limit {limit}")
            for c in calls:
                if c.pdf_page_count > limit:
                    # The documented exception (§5.7 "never drop pages"): a unit
                    # whose own evidence is bigger than the limit, or context
                    # that fitted nowhere. Either way the call says so.
                    self.assertTrue(c.notes, f"limit {limit}: {c.brief()}")


@unittest.skipUnless(HAVE_BOOKS, "Text BOOKS/ not on this machine")
class MeasureLimitTests(unittest.TestCase):
    """The one path that builds a real bundle. No agy call is made, and none
    can be: `tests/qc/__init__.py` sets QC_NO_AGY for the whole process."""

    TARGET = 60

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="qc-measure-"))
        units, cites = fixture_units()
        cls.rec = qc_sources.measure_limit(
            tag=TAG_019, day=DAY_019, units=units, cites=cites,
            target=cls.TARGET, work_dir=cls.tmp / "m", search=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_it_builds_a_bundle_of_about_the_target_size(self):
        self.assertGreaterEqual(self.rec["pdf_pages"], self.TARGET * 0.8)
        self.assertTrue(Path(self.rec["bundle"]).exists())
        self.assertTrue(Path(self.rec["artifacts"]).exists())
        self.assertGreater(self.rec["bundle_bytes"], 0)
        # The bundle is the pages plus its index sheet(s).
        self.assertEqual(self.rec["bundle_pages"],
                         self.rec["index_pages"] + self.rec["pdf_pages"])

    def test_the_canary_codes_are_really_stamped_in_the_manifest(self):
        codes = {p["code"] for p in self.rec["manifest"]["pages"]}
        for row in self.rec["canary"]:
            self.assertIn(row["code"], codes)
        self.assertEqual(len(self.rec["canary"]), 3)

    def test_the_prompt_names_the_pages_and_never_the_codes(self):
        prompt = self.rec["prompt"]
        for row in self.rec["canary"]:
            self.assertNotIn(row["code"], prompt)
            self.assertIn(f"p.{row['printed']}", prompt)
        # The nonce is in artifacts.md and asked for by name — never in the
        # prompt, or a judge could answer it without opening the file.
        self.assertIn(qc_sources.NONCE_MARKER, prompt)
        self.assertNotIn(self.rec["nonce"], prompt)
        self.assertIn(self.rec["nonce"],
                      Path(self.rec["artifacts"]).read_bytes().decode("utf-8"))

    def test_the_measurement_is_written_where_check_answer_looks(self):
        """In the `<workspace>.key` sibling, and NOT in the workspace.

        The record carries the three canary codes and the nonce in plain
        text, and the workspace is the directory the owner is told to
        attach from, so the key cannot live in it (the reason is in
        test_review_levers_measurement.py). `--check-answer --out` is
        pointed at the sibling by `how_to_run`.
        """
        work = Path(self.rec["bundle"]).parent
        path = work.parent / f"{work.name}.key" / qc_sources.MEASURE_FILE
        self.assertEqual(Path(self.rec["measurement_file"]), path)
        self.assertTrue(path.exists())
        self.assertFalse((work / qc_sources.MEASURE_FILE).exists(),
                         "the answer key is beside the bundle")
        self.assertIn(str(path.parent), self.rec["how_to_run"])
        saved = json.loads(path.read_bytes().decode("utf-8"))
        self.assertEqual(saved["prompt_sha256"], self.rec["prompt_sha256"])
        self.assertEqual([r["code"] for r in saved["canary"]],
                         [r["code"] for r in self.rec["canary"]])

    def test_a_reply_with_the_three_codes_scores_as_a_pass(self):
        reply = {"canary": [{"page": f"{r['book']} p.{r['printed']}",
                             "code": r["code"]} for r in self.rec["canary"]],
                 "nonce": self.rec["nonce"]}
        res = qc_sources.check_answer(self.rec, reply)
        self.assertTrue(res["passes"], res)
        self.assertEqual(res["verified"], 3)


class ChapterLookupTests(unittest.TestCase):
    def test_a_directory_is_taken_as_it_is(self):
        d, tag, day = qc_sources.find_chapter(str(HERE / "fixtures" / "quiz"
                                                  / TAG_019))
        self.assertEqual((tag, day), (TAG_019, DAY_019))
        self.assertTrue((d / "quiz.json").exists())

    def test_an_unknown_chapter_says_what_to_do(self):
        with self.assertRaises(qc_sources.PlanError) as caught:
            qc_sources.find_chapter("999")
        self.assertIn("no local copy", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
