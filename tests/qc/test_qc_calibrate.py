#!/usr/bin/env python3
"""Offline tests for scripts/qc_calibrate.py — §6's calibration.

NOTHING HERE TOUCHES THE OUTSIDE. GitHub is qc_calibrate's own LocalGh over a
temp directory, agy is a fake that answers in schema, and every test that would
otherwise open a textbook either fakes the page (FakePages, which parses a
string with the same qc_norm calls the real one uses) or is skipped when the
PDFs are not on this machine.

WHAT EACH TEST PINS, AND WHY IT IS WORTH A TEST.
  - A seeded mutant really changes a value AND the change really contradicts
    the page it cites (§6). A mutant that only *looks* wrong measures nothing:
    a correct "no finding" would be scored as a recall miss.
  - A mutant that does not contradict its page is DISCARDED, with a reason. The
    veto that matters most is the one for a value the page ALSO states — a
    finding there would be the judge being right.
  - Every unit of a seeded chapter except the mutated ones is byte-identical
    afterwards, so §6's "negatives ... untouched" is a property and not a hope.
  - The two negative controls are rejected by the REAL verification path
    (qc_judge.judge + make_validator), reproducing the 2026-09-18 incident:
    confident, in-schema JSON from a call that never read the bundle.
  - Clopper-Pearson against published values, because scipy is not installed
    and an interval nobody checked is worse than no interval.
  - The report renders before any judging has happened, because the owner will
    read it after `--build` and a crash there would look like a failed build.
  - A run resumes: a chapter already judged is not paid for twice, and a quota
    stop persists where it stopped.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_calibrate as Q                                   # noqa: E402
import qc_norm                                             # noqa: E402
import qc_store                                            # noqa: E402
from agy_attach import QuotaPaused                         # noqa: E402


def books_available() -> bool:
    """The textbook PDFs, or a warm cache of their text."""
    try:
        import qc_books
        return bool(qc_books.book("StahlKids").page_text(69))
    except Exception:                                      # noqa: BLE001
        return False


HAVE_BOOKS = books_available()
needs_books = unittest.skipUnless(HAVE_BOOKS,
                                  "Text BOOKS/ not on this machine")

# One page of one book, as its text really reads (qc_books probe, §5.1). Used
# by FakePages so the seeding tests do not need the PDFs.
STAHLKIDS_69 = ("Onset of Action Doesn't work right away; full therapeutic "
                "benefits may take 2–8 weeks. Dosing 0.5 mg/kg/day for "
                "4 days, then increase to 1.2 mg/kg/day.")

QUIZ = {
    "title": "t",
    "questions": [
        {"type": "multiple_choice",
         "question": "How long may the full therapeutic benefit of "
                     "atomoxetine take? (StahlKids p. 69)",
         "answerOptions": [
             {"text": "2-8 weeks", "isCorrect": True,
              "rationale": "StahlKids p. 69 gives the onset window."},
             {"text": "Immediately", "isCorrect": False,
              "rationale": "Not a reuptake inhibitor's onset."}],
         "hint": "Think weeks, not days."},
        {"type": "multiple_choice",
         "question": "What is the target dose of atomoxetine? "
                     "(StahlKids p. 69)",
         "answerOptions": [
             {"text": "1.2 mg/kg/day", "isCorrect": True,
              "rationale": "StahlKids p. 69 gives the target."},
             {"text": "10 mg/kg/day", "isCorrect": False,
              "rationale": "Far above the target."}]},
    ],
}


class FakePages:
    """qc_calibrate.Pages over a string instead of a PDF.

    Deliberately the SAME qc_norm calls the real one makes — locked_values for
    the page's values, a regex for the sentence — so a test that passes here is
    a test of the seeding rule and not of a simplified stand-in.
    """

    def __init__(self, text: str = STAHLKIDS_69):
        self.text = text

    def window(self, book, printed):
        return self.text

    def values(self, book, printed):
        out = {}
        for v in qc_norm.locked_values(self.text):
            out.setdefault((*Q._bucket(v), str(v["value"])), v["raw"])
        return out

    def quote_for(self, book, printed, raw):
        return Q.Pages.quote_for(self, book, printed, raw)


# Cards, for the value-bump tests. A quiz item can also be mutated by moving
# its key, and `key_swap` outranks a value bump when both kinds are equally
# under-represented — so a test that means to exercise the bump uses a unit
# type that has no key to move.
CARDS = {"cards": [
    {"front": "How long to the full benefit of atomoxetine?",
     "back": "2-8 weeks (StahlKids p. 69)."},
    {"front": "What is the target dose?",
     "back": "1.2 mg/kg/day (StahlKids p. 69)."},
]}


def confirmed(uid="C1", artifact="flashcards.json", raw="2-8 weeks",
              kind="duration", unit_type="card",
              tag="study-019-adhd-treatment-non-stimulants") -> Q.Confirmed:
    value = qc_norm.locked_values(raw)[0]["value"]
    return Q.Confirmed(tag=tag, uid=uid, artifact=artifact,
                       unit_type=unit_type, book="StahlKids", printed=69,
                       kind=kind, raw=raw, value=value,
                       quote="full therapeutic benefits may take 2–8 weeks")


class TempRoot(unittest.TestCase):
    """A throwaway `_qc/` tree. Nothing in these tests may write to the repo's."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-calib-test-"))
        self.root = self.tmp / "_qc"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
class TestClopperPearson(unittest.TestCase):
    """§6 asks every rate to carry an exact interval and scipy is not here."""

    def test_closed_forms_at_the_edges(self):
        # k = 0: lower is 0 and upper is 1 - (alpha/2)^(1/n) exactly.
        lo, hi = Q.clopper_pearson(0, 10)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 1 - 0.025 ** 0.1, places=9)
        lo, hi = Q.clopper_pearson(10, 10)
        self.assertAlmostEqual(lo, 0.025 ** 0.1, places=9)
        self.assertEqual(hi, 1.0)

    def test_published_values(self):
        # The textbook 95% Clopper-Pearson intervals for n = 10, to the four
        # decimals they are printed at (so the comparison is to 1e-4, not to
        # the last bit of a published rounding).
        for k, want in ((1, (0.0025, 0.4450)),
                        (5, (0.1871, 0.8129)),
                        (8, (0.4439, 0.9748))):
            lo, hi = Q.clopper_pearson(k, 10)
            self.assertAlmostEqual(lo, want[0], delta=1e-4, msg=f"k={k} lower")
            self.assertAlmostEqual(hi, want[1], delta=1e-4, msg=f"k={k} upper")

    def test_the_bar_is_inside_the_interval_it_reports(self):
        # 60 of 60 is the calibration's own best case; §6's 89% must sit below
        # the lower bound or the interval is not saying what it claims.
        lo, hi = Q.clopper_pearson(60, 60)
        self.assertGreater(lo, Q.BAR_RECALL)
        self.assertEqual(hi, 1.0)

    def test_an_empty_denominator_is_total_ignorance(self):
        self.assertEqual(Q.clopper_pearson(0, 0), (0.0, 1.0))
        self.assertIsNone(Q.rate(0, 0)["rate"])

    def test_betainc_matches_its_inverse(self):
        for a, b, x in ((2.0, 9.0, 0.3), (5.0, 5.0, 0.5), (1.0, 20.0, 0.05)):
            p = Q.betainc(a, b, x)
            self.assertAlmostEqual(Q.betainv(p, a, b), x, places=8)


# ---------------------------------------------------------------------------
class TestContradicts(unittest.TestCase):
    """§6: "Code confirms each contradicts its page first.\""""

    def test_a_changed_value_contradicts_its_page(self):
        ok, why = Q.contradicts("2-8 weeks", "3-8 weeks",
                                "full therapeutic benefits may take 2–8 "
                                "weeks", {})
        self.assertTrue(ok, why)
        self.assertIn("values_dispute", why)

    def test_a_value_the_page_also_states_is_discarded(self):
        # The veto that matters: the page says both 0.5 and 1.2 mg/kg/day, so
        # "correcting" 0.5 to 1.2 produces an item the page supports. A finding
        # on it would be the judge being RIGHT and would be scored as a miss.
        pages = FakePages()
        stated = pages.values("StahlKids", 69)
        ok, why = Q.contradicts("0.5 mg/kg/day", "1.2 mg/kg/day",
                                "0.5 mg/kg/day for 4 days", stated)
        self.assertFalse(ok)
        self.assertIn("the page also states", why)

    def test_an_unchanged_claim_does_not_contradict(self):
        ok, why = Q.contradicts("2-8 weeks", "2-8 weeks",
                                "may take 2–8 weeks", {})
        self.assertFalse(ok)

    def test_a_token_swap_needs_the_original_on_the_page(self):
        window = "Atomoxetine is a noradrenaline reuptake inhibitor."
        ok, why = Q.contradicts("Atomoxetine is a", "Guanfacine is a",
                                "quote without either name", {}, window)
        self.assertTrue(ok, why)
        ok, why = Q.contradicts("Sertraline is a", "Fluoxetine is a",
                                "quote", {}, window)
        self.assertFalse(ok)

    def test_the_token_check_is_whole_word(self):
        self.assertTrue(Q._word_in("CBT", "compared CBT + sertraline and CBT"))
        self.assertFalse(Q._word_in("one", "prednisone is not a count"))
        # The bug this replaced: qc_norm.norm deletes spaces, so a word
        # boundary test on its output never matched anything.
        self.assertTrue(Q._word_in("lithium clearance",
                                   "Carbamazepine also decreases lithium\n"
                                   "clearance, increasing the risk"))

    def test_bumping_never_makes_a_range_whose_ends_are_equal(self):
        self.assertEqual(Q._bump_number("2-3 ימים"),
                         "2-4 ימים")
        self.assertEqual(Q._bump_number("2-8 weeks"), "3-8 weeks")
        self.assertEqual(Q._bump_number("1.4 mg/kg/day"), "2.4 mg/kg/day")
        self.assertEqual(Q._bump_number("three (or more)"), "four (or more)")


# ---------------------------------------------------------------------------
class TestSeeding(unittest.TestCase):
    """The mutants, end to end, without a PDF."""

    def setUp(self):
        self.assets = {"flashcards.json": json.dumps(CARDS, ensure_ascii=False,
                                                     indent=2)}
        self.tag = "study-019-adhd-treatment-non-stimulants"
        self.by_tag = {self.tag: dict(self.assets)}
        self.pages = FakePages()

    def _units(self, assets):
        return {u.uid: u.text for u in Q.chapter_units(assets)}

    def test_a_mutant_changes_exactly_one_value_in_its_own_unit(self):
        mutants, seeded, _ = Q.seed_mutants(
            [confirmed()], self.by_tag, n=1, pages=self.pages)
        self.assertEqual(len(mutants), 1)
        m = mutants[0]
        before = self._units(self.assets)
        after = self._units(seeded[self.tag])
        changed = [u for u in before if before[u] != after[u]]
        self.assertEqual(changed, [m.uid])
        self.assertIn(m.before, before[m.uid])
        self.assertIn(m.after, after[m.uid])
        self.assertNotIn(m.before, after[m.uid])

    def test_the_seeded_value_really_contradicts_the_page(self):
        mutants, _, _ = Q.seed_mutants([confirmed()], self.by_tag, n=1,
                                       pages=self.pages)
        m = mutants[0]
        d = qc_norm.values_dispute(m.after, m.quote)
        self.assertTrue(d["disputed"])
        self.assertFalse(d["same"])
        # ...and the same call on the ORIGINAL value says the page agrees.
        d0 = qc_norm.values_dispute(m.before, m.quote)
        self.assertTrue(d0["same"])
        self.assertFalse(d0["disputed"])

    def test_a_non_contradicting_edit_is_discarded_with_a_reason(self):
        # The page states 1 AND 2 mg/kg/day, so bumping 1 to 2 lands on a value
        # the page supports. That is not a mutant; a finding on it would be the
        # judge being right, and counting the absence of one as a miss would
        # understate the judge for free.
        page = FakePages("Dosing 1 mg/kg/day for 4 days, then 2 mg/kg/day.")
        c = confirmed(raw="1 mg/kg/day", kind="dose")
        c.quote = "Dosing 1 mg/kg/day for 4 days, then 2 mg/kg/day."
        assets = {"flashcards.json": json.dumps(
            {"cards": [{"front": "Dose? (StahlKids p. 69)",
                        "back": "1 mg/kg/day"}]},
            ensure_ascii=False, indent=2)}
        mutants, seeded, discarded = Q.seed_mutants(
            [c], {self.tag: assets}, n=1, pages=page)
        self.assertEqual(mutants, [])
        self.assertTrue(discarded)
        self.assertIn("the page also states", discarded[0]["reason"])
        # And the asset is untouched: a discarded mutant leaves no trace.
        self.assertEqual(seeded[self.tag]["flashcards.json"],
                         assets["flashcards.json"])

    def test_a_value_that_is_not_unique_cannot_be_placed(self):
        assets = {"flashcards.json": json.dumps(
            {"cards": [{"front": "2-8 weeks? (StahlKids p. 69)",
                        "back": "2-8 weeks"}]},
            ensure_ascii=False, indent=2)}
        mutants, _, discarded = Q.seed_mutants(
            [confirmed()], {self.tag: assets}, n=1, pages=self.pages)
        reasons = " ".join(d["reason"] for d in discarded)
        self.assertEqual(mutants, [],
                         f"the ambiguous edit was placed anyway: {reasons}")
        self.assertIn("not unique", reasons)

    def test_a_quiz_item_can_be_mutated_by_moving_its_key(self):
        assets = {"quiz.json": json.dumps(QUIZ, ensure_ascii=False, indent=2)}
        c = confirmed(uid="Q1", artifact="quiz.json",
                      unit_type="multiple_choice")
        mutants, seeded, _ = Q.seed_mutants(
            [c], {self.tag: assets}, n=1, pages=self.pages)
        self.assertEqual([m.mutation for m in mutants], ["key_swap"])
        item = json.loads(seeded[self.tag]["quiz.json"])["questions"][0]
        keyed = [o["text"] for o in item["answerOptions"] if o["isCorrect"]]
        self.assertEqual(keyed, ["Immediately"])
        # Exactly one key, still: a "mutant" with two keys would be caught by
        # qc_lint before the judge ever saw it.
        self.assertEqual(len(keyed), 1)

    def test_every_other_unit_is_byte_identical_afterwards(self):
        mutants, seeded, _ = Q.seed_mutants(
            [confirmed(), confirmed(uid="C2", raw="1.2 mg/kg/day",
                                    kind="dose")],
            self.by_tag, n=1, pages=self.pages)
        before = self._units(self.assets)
        after = self._units(seeded[self.tag])
        untouched = [u for u in before if u != mutants[0].uid]
        for uid in untouched:
            self.assertEqual(before[uid], after[uid],
                             f"{uid} moved and it is not the mutant")

    def test_seeding_refuses_to_return_a_set_whose_negatives_moved(self):
        # The assertion itself, provoked: a json re-dump that reordered keys
        # would change every unit, and 200 negatives would silently become 200
        # units nobody checked.
        real = Q._dump_json

        def scrambling_dump(data):
            if isinstance(data, dict) and "cards" in data:
                for card in data["cards"]:
                    card["front"] = card["front"] + " "
            return real(data)

        Q._dump_json = scrambling_dump
        self.addCleanup(setattr, Q, "_dump_json", real)
        with self.assertRaises(Q.CalibrationError) as cm:
            Q.seed_mutants([confirmed()], self.by_tag, n=1, pages=self.pages)
        self.assertIn("no longer untouched", str(cm.exception))

    def test_a_protected_unit_is_never_mutated(self):
        mutants, _, _ = Q.seed_mutants(
            [confirmed()], self.by_tag, n=1, pages=self.pages,
            protect=[(self.tag, "C1")])
        self.assertEqual(mutants, [])


# ---------------------------------------------------------------------------
class TestNegatives(unittest.TestCase):

    def test_negatives_exclude_the_mutated_and_the_protected(self):
        pool = [confirmed(uid=f"C{i}") for i in range(1, 6)]
        m = Q.Mutant(mid="m", tag=pool[0].tag, uid="C1",
                     artifact="flashcards.json", artifact_type="cards",
                     unit_type="card", mutation="duration", book="StahlKids",
                     printed=69, quote="q", before="2-8 weeks",
                     after="3-8 weeks", confirmed_by="test")
        rows = Q.negatives(pool, [m], n=10, protect=[(pool[0].tag, "C2")])
        uids = sorted(r.uid for r in rows)
        self.assertEqual(uids, ["C3", "C4", "C5"])

    def test_one_row_per_unit(self):
        pool = [confirmed(uid="C1", raw="2-8 weeks"),
                confirmed(uid="C1", raw="1.2 mg/kg/day", kind="dose")]
        self.assertEqual(len(Q.negatives(pool, [], n=10)), 1)


# ---------------------------------------------------------------------------
@needs_books
class TestGold(unittest.TestCase):
    """§1's items, re-derived. Skipped without the books, because the point of
    the gold set is that a book decides it."""

    @classmethod
    def setUpClass(cls):
        cls.rows = Q.gold(fetch=False)
        cls.by_id = {}
        for r in cls.rows:
            cls.by_id.setdefault(r["gid"], []).append(r)

    def test_every_item_reaches_a_label(self):
        undecided = [r["gid"] + ": " + r["detail"] for r in self.rows
                     if r["label"] == "undecided"]
        self.assertEqual(undecided, [])

    def test_the_labels_are_the_ones_the_2026_09_18_review_measured(self):
        # §1, in order. If a book stops saying what §1 says it says, this
        # fails — which is the whole reason the labels are derived and not
        # written down in GOLD.
        want = {"042-Q2": "contradicted", "038-Q9": "contradicted",
                "043-Q1": "contradicted", "018-Q3": "supported",
                "036-Q10": "supported", "035-Q1": "item_logic",
                "042-Q3": "lint"}
        got = {gid: rows[0]["label"] for gid, rows in self.by_id.items()}
        for gid, label in want.items():
            self.assertEqual(got.get(gid), label, gid)
        for gid in ("019-Q1+C5",):
            self.assertTrue(all(r["label"] == "source_conflict"
                                for r in self.by_id[gid]), gid)
        # The buspirone pair is NOT a conflict: the two pages answer different
        # questions and no value disputes the other.
        self.assertTrue(all(r["label"] == "supported"
                            for r in self.by_id["042-Q5+C5"]))

    def test_each_label_carries_a_book_page_and_a_verbatim_quote(self):
        import qc_books
        for r in self.rows:
            if r["rule"] == "lint":
                continue                # no book decides a hygiene defect
            self.assertTrue(r["book"], r["gid"])
            self.assertTrue(r["printed"], r["gid"])
            self.assertGreater(len(r["quote"]), 20, r["gid"])
            page = qc_books.book(r["book"]).page_text(r["printed"]) or ""
            self.assertIn(qc_norm.norm(r["quote"][:60]), qc_norm.norm(page),
                          f"{r['gid']}: the quote is not on the page it names")

    def test_recall_is_measured_on_the_contradicted_items_only(self):
        s = Q.score({"gold": self.rows, "mutants": [], "negatives": [],
                     "chapters": []})
        self.assertEqual(len(s["gold"]), 3)

    def test_the_owner_item_is_flagged_and_never_fixable(self):
        mta = self.by_id["018-Q3"][0]
        self.assertTrue(mta["owner_review"])
        self.assertEqual(mta["label"], "supported")


# ---------------------------------------------------------------------------
@needs_books
class TestControls(unittest.TestCase):
    """§6's two negative controls, through qc_judge itself."""

    def test_both_controls_are_rejected_every_time(self):
        with tempfile.TemporaryDirectory(prefix="qc-ctl-") as d:
            rows = Q.controls(repeats=2, workdir=Path(d))
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r["rejected"] for r in rows),
                        [r for r in rows if not r["rejected"]])
        kinds = {r["control"] for r in rows}
        self.assertEqual(kinds, {"bundle_missing", "artifacts_truncated"})

    def test_the_missing_bundle_is_really_missing_and_still_answered(self):
        with tempfile.TemporaryDirectory(prefix="qc-ctl-") as d:
            rows = Q.controls(repeats=1, workdir=Path(d))
        blind = [r for r in rows if r["control"] == "bundle_missing"][0]
        self.assertFalse(blind["bundle_present"])
        # It answered in schema, three times, and the canary caught it every
        # time — which is the 2026-09-18 failure exactly.
        self.assertGreaterEqual(blind["attempts"], 1)
        self.assertIn("canary", blind["reason"])

    def test_the_truncated_artifacts_call_cannot_return_the_nonce(self):
        with tempfile.TemporaryDirectory(prefix="qc-ctl-") as d:
            rows = Q.controls(repeats=1, workdir=Path(d))
        partial = [r for r in rows if r["control"] == "artifacts_truncated"][0]
        self.assertTrue(partial["rejected"])
        self.assertTrue("nonce" in partial["reason"]
                        or "no block" in partial["reason"], partial["reason"])

    def test_the_control_metric_is_100_percent_or_it_is_not_a_control(self):
        # Each row names the gate that caught it: only a rejection that came
        # from the verification path counts (`control_rejected`), because a
        # harness that crashes rejects everything and would print 100%.
        doc = {"controls": [{"control": "bundle_missing", "rejected": True,
                             "gate": "verify", "attempts": 3},
                            {"control": "bundle_missing", "rejected": False,
                             "gate": "", "attempts": 3}]}
        m = Q._control_metrics(doc)
        self.assertEqual(m["overall"]["rate"], 0.5)
        go, why = Q.verdict_line(
            {"mutants": [], "negatives": [], "gold": [], **doc},
            {**Q.score({"chapters": [], "mutants": [], "negatives": [],
                        "gold": []}), "controls": m})
        self.assertFalse(go)
        self.assertTrue(any("100%" in w for w in why))


# ---------------------------------------------------------------------------
class TestReport(TempRoot):

    def _set(self, **kw):
        doc = {"built_at": "2026-09-23T00:00:00+00:00", "chapters": ["t"],
               "bar": {}, "gold": [], "mutants": [], "negatives": [],
               "discarded": [], "controls": [], "pool": 0}
        doc.update(kw)
        doc["counts"] = Q._set_counts(doc)
        qc_store.write_json_atomic(Q.cal_root(self.root) / "set.json", doc)
        return doc

    def test_it_renders_before_anything_has_been_judged(self):
        self._set(gold=[{"gid": "042-Q2", "tag": "t", "unit": "Q2",
                         "label": "contradicted", "book": "DSM-5-TR",
                         "printed": 135, "quote": "three (or more)",
                         "detail": "", "rule": "threshold_of_list"}])
        out = Q.report(root=self.root)
        text = (Q.cal_root(self.root) / "report.md").read_text(encoding="utf-8")
        self.assertFalse(out["go"])
        self.assertIn("NO-GO", text)
        self.assertIn("042-Q2", text)
        self.assertIn("מול הסף", text)  # "מול הסף"
        self.assertTrue((Q.cal_root(self.root) / "result.json").exists())

    def test_an_unjudged_set_never_prints_go(self):
        self._set(mutants=[], negatives=[])
        self.assertFalse(Q.report(root=self.root)["go"])

    def test_a_partial_run_never_prints_go(self):
        # The bug this pins: with the size checks on the PREPARED set, one
        # chapter at 100% recall printed GO on 9 mutants of 60.
        doc = self._set(
            mutants=[{"mid": f"m{i}", "tag": "t", "uid": f"Q{i}",
                      "artifact": "quiz.json", "artifact_type": "quiz",
                      "unit_type": "multiple_choice", "mutation": "number",
                      "book": "DSM-5-TR", "printed": 1, "quote": "q",
                      "before": "1", "after": "2", "confirmed_by": "x"}
                     for i in range(Q.MIN_MUTANTS)])
        s = Q.score(doc, root=self.root)
        go, why = Q.verdict_line(doc, s)
        self.assertFalse(go)
        self.assertTrue(any("נשפטו" in w
                            for w in why), why)   # "נשפטו"

    def test_the_headline_rates_are_the_unflattering_ones(self):
        # recall: the strict hit; false positives: any verified finding.
        doc = self._set()
        s = Q.score(doc, root=self.root)
        self.assertIn("recall_any", s)
        self.assertIn("false_positives_on_value", s)
        self.assertIsNot(s["recall"], s["recall_any"])


# ---------------------------------------------------------------------------
class TestSafety(TempRoot):
    """Nothing here may reach a release. The set is 60 deliberate errors."""

    def test_local_gh_serves_reads_and_refuses_every_write(self):
        d = Q.seeded_root(self.root) / "study-005-serotonin-system"
        d.mkdir(parents=True)
        (d / "quiz.json").write_text(json.dumps(QUIZ), encoding="utf-8")
        gh = Q.LocalGh(Q.seeded_root(self.root))
        assets = gh.list_assets("study-005-serotonin-system")
        self.assertEqual([a.name for a in assets], ["quiz.json"])
        self.assertEqual(assets[0].sha256,
                         qc_store.sha256_file(d / "quiz.json"))
        for call in (lambda: gh.upload("t", d / "quiz.json", "quiz.json"),
                     lambda: gh.rename_asset(1, "x"),
                     lambda: gh.delete_asset(1)):
            with self.assertRaises(Q.CalibrationError):
                call()

    def test_a_calibration_root_is_marked_so_nobody_reverts_from_it(self):
        marker = Q.judge_root(self.root) / Q.SEEDED_MARKER
        self.assertTrue(marker.exists())
        self.assertIn("NOT the pristine release bytes",
                      marker.read_text(encoding="utf-8"))

    def test_the_run_options_can_never_upload(self):
        import qc_run
        opts = qc_run.Options(tags=["t"], calibration=True, no_upload=True,
                              root=Q.judge_root(self.root))
        self.assertFalse(opts.uploads_allowed)


# ---------------------------------------------------------------------------
class FakeSources:
    """qc_sources, replaced by two files on disk and a manifest.

    The bundle is not a PDF and does not need to be: qc_run hands whatever
    qc_sources returned straight to the judge, and these tests inject the judge
    too. What is exercised is the orchestration — journal, resume, pause.
    """

    def __init__(self, workdir: Path):
        self.dir = Path(workdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0

    def __call__(self, *, tag, units, cites, assets, work_dir, page_limit,
                 root, **_kw):
        self.calls += 1
        bundle = self.dir / "bundle.pdf"
        bundle.write_bytes(b"%PDF-1.4 fake\n")
        md = self.dir / "artifacts.md"
        md.write_text("### Q1\nstem\n", encoding="utf-8")
        uids = [u.uid for u in units]
        return [{"call_id": "A1", "kind": "A", "units": uids,
                 "bundle": str(bundle), "artifacts": str(md), "pages": 3,
                 "manifest": {"pages": [], "nonce": "N"}}]


def ok_judgment(**kw):
    plan = kw["plan"]
    return {"call_id": plan.call_id, "kind": plan.kind, "ok": True,
            "reason": "fake", "findings": [], "attempts": 1, "duration_s": 3.0,
            "coverage": [{"unit": u, "status": "supported", "echo": ""}
                         for u in plan.units],
            "unchecked": 0, "canary": {"requested": [["X", 1]],
                                       "returned": [{"code": "A"}],
                                       "verified": True},
            "tempmedia_ok": True, "model": "fake", "agy_version": "fake"}


class TestRunResume(TempRoot):

    def setUp(self):
        super().setUp()
        self.tag = "study-005-serotonin-system"
        d = Q.seeded_root(self.root) / self.tag
        d.mkdir(parents=True)
        (d / "quiz.json").write_text(json.dumps(QUIZ, ensure_ascii=False,
                                                indent=2), encoding="utf-8")
        doc = {"built_at": "x", "chapters": [self.tag], "gold": [],
               "mutants": [{"mid": "m1", "tag": self.tag, "uid": "Q1",
                            "artifact": "quiz.json", "artifact_type": "quiz",
                            "unit_type": "multiple_choice",
                            "mutation": "duration", "book": "StahlKids",
                            "printed": 69, "quote": "q", "before": "2-8 weeks",
                            "after": "3-8 weeks", "confirmed_by": "t"}],
               "negatives": [], "discarded": [], "controls": [], "bar": {}}
        doc["counts"] = Q._set_counts(doc)
        qc_store.write_json_atomic(Q.cal_root(self.root) / "set.json", doc)
        self.sources = FakeSources(self.tmp / "work")

    def _run(self, judge):
        # stderr too: qc_run prints the traceback of a chapter that blew up,
        # and one test provokes exactly that. A suite that scrolls a fake
        # traceball past the reader teaches them to ignore real ones.
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            code = Q.run_prepared(root=self.root, judge=judge,
                                  sources=self.sources)
        return code, buf.getvalue()

    def test_a_judged_chapter_is_not_paid_for_twice(self):
        code, _ = self._run(ok_judgment)
        self.assertEqual(code, Q.EXIT_OK)
        self.assertTrue(Q.RunJournal(self.root).done(self.tag))
        calls_before = self.sources.calls

        def never(**kw):
            raise AssertionError("the resume re-judged a finished chapter")

        code, out = self._run(never)
        self.assertEqual(code, Q.EXIT_OK)
        self.assertEqual(self.sources.calls, calls_before)
        self.assertIn("מדלג", out)      # "מדלג"

    def test_a_quota_stop_persists_where_it_stopped_and_exits_4(self):
        def quota(**kw):
            raise QuotaPaused(2_000_000_000.0, "RESOURCE_EXHAUSTED ... "
                                               "Resets in 13h33m0s")

        code, out = self._run(quota)
        self.assertEqual(code, Q.EXIT_QUOTA)
        paused = Q.RunJournal(self.root).data["paused"]
        self.assertEqual(paused["tag"], self.tag)
        self.assertTrue(paused["step"])
        self.assertIn("RESOURCE_EXHAUSTED", paused["raw"])
        state = qc_store.get_run_state(self.tag, root=Q.judge_root(self.root))
        self.assertEqual(state["state"], "paused_quota")
        # ...and the chapter is NOT marked done, so the resume redoes it.
        self.assertFalse(Q.RunJournal(self.root).done(self.tag))

        code, _ = self._run(ok_judgment)
        self.assertEqual(code, Q.EXIT_OK)
        self.assertTrue(Q.RunJournal(self.root).done(self.tag))
        self.assertIsNone(Q.RunJournal(self.root).data["paused"])

    def test_the_run_journals_one_call_record_per_judge_call(self):
        self._run(ok_judgment)
        calls = Q.RunJournal(self.root).data["calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["call_id"], "A1")
        self.assertTrue(calls[0]["ok"])
        self.assertEqual(calls[0]["duration_s"], 3.0)
        self.assertEqual(calls[0]["attempts"], 1)

    def test_a_chapter_that_blows_up_is_recorded_and_the_run_continues(self):
        def boom(**kw):
            raise RuntimeError("the judge exploded")

        code, _ = self._run(boom)
        self.assertEqual(code, Q.EXIT_ERROR)
        rec = Q.RunJournal(self.root).data["chapters"][self.tag]
        self.assertIn("exploded", rec["error"])
        self.assertNotIn("finished_at", rec)

    def test_a_chapter_that_never_reached_a_verdict_is_named_in_the_report(self):
        # Measured on 070: qc_sources leaves an empty B call on a Synopsis-
        # heavy chapter and refuses the plan, so a chapter §6 requires cannot
        # be judged. That is not a judge failure, and a NO-GO has to say so.
        def boom(**kw):
            raise RuntimeError("B1: no pages")

        self._run(boom)
        Q.report(root=self.root)
        text = (Q.cal_root(self.root) / "report.md").read_text(encoding="utf-8")
        self.assertIn("no pages", text)
        self.assertIn(self.tag, text)

    def test_the_run_never_commits_the_seeded_snapshot(self):
        # qc_store.snapshot() commits `_qc/orig/` by explicit path (§2.5), and
        # under the calibration root that directory holds the SEEDED bytes —
        # 60 deliberate errors, in the history of the repository that publishes
        # the owner's material.
        import os
        seen = []
        real = qc_store.snapshot

        def spy(tag, **kw):
            seen.append(os.environ.get(qc_store.NO_COMMIT_ENV))
            return real(tag, **kw)

        qc_store.snapshot = spy
        self.addCleanup(setattr, qc_store, "snapshot", real)
        self._run(ok_judgment)
        self.assertEqual(seen, ["1"])

    def test_the_environment_guard_is_restored_afterwards(self):
        import os
        before = os.environ.get(qc_store.NO_COMMIT_ENV)
        self._run(ok_judgment)
        self.assertEqual(os.environ.get(qc_store.NO_COMMIT_ENV), before)


# ---------------------------------------------------------------------------
class TestScoring(TempRoot):
    """What counts as a hit, and what counts as a false positive."""

    def _judgment(self, tag, findings, units=None):
        # `units` is what the call was planned over, and since 2026-09-24 that
        # (with the coverage rows) is the ONLY thing that makes a unit judged —
        # a finding no longer can, or a judge naming a unit no call showed it
        # would refill the negatives with units nobody read. A real judgment
        # always carries it; these fixtures now do too, defaulting to the units
        # their findings are about.
        d = Q.judge_root(self.root) / tag / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        uids = units if units is not None else sorted(
            {str(f.get("unit")) for f in findings if f.get("unit")})
        qc_store.write_json_atomic(d / "round0-A1.json",
                                   {"call_id": "A1", "ok": True,
                                    "units": uids,
                                    "findings": findings, "coverage": []})

    def _doc(self, mutants=(), negatives=()):
        return {"chapters": ["t"], "gold": [], "mutants": list(mutants),
                "negatives": list(negatives), "controls": [], "counts": {}}

    def test_an_unverified_finding_is_not_a_hit(self):
        self._judgment("t", [{"unit": "Q1", "verified": False,
                              "said": "3-8 weeks", "kind": "contradicted"}])
        doc = self._doc(mutants=[{"mid": "m", "tag": "t", "uid": "Q1",
                                  "artifact": "quiz.json",
                                  "artifact_type": "quiz",
                                  "unit_type": "multiple_choice",
                                  "mutation": "duration", "book": "b",
                                  "printed": 1, "quote": "q",
                                  "before": "2-8 weeks", "after": "3-8 weeks",
                                  "confirmed_by": "t"}])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["recall"]["k"], 0)
        self.assertEqual(s["recall"]["n"], 1)

    def test_a_hit_must_name_the_seeded_value_to_count_strictly(self):
        self._judgment("t", [{"unit": "Q1", "verified": True,
                              "said": "something else entirely",
                              "kind": "contradicted"}])
        doc = self._doc(mutants=[{"mid": "m", "tag": "t", "uid": "Q1",
                                  "artifact": "quiz.json",
                                  "artifact_type": "quiz",
                                  "unit_type": "multiple_choice",
                                  "mutation": "duration", "book": "b",
                                  "printed": 1, "quote": "q",
                                  "before": "2-8 weeks", "after": "3-8 weeks",
                                  "confirmed_by": "t"}])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["recall"]["k"], 0)          # headline: strict
        self.assertEqual(s["recall_any"]["k"], 1)      # bound: anything

    def test_any_verified_finding_on_a_negative_is_a_false_positive(self):
        self._judgment("t", [{"unit": "C3", "verified": True,
                              "said": "an unrelated claim",
                              "kind": "contradicted"}])
        doc = self._doc(negatives=[{"nid": "n", "tag": "t", "uid": "C3",
                                    "artifact": "flashcards.json",
                                    "artifact_type": "cards",
                                    "unit_type": "card", "book": "b",
                                    "printed": 1, "raw": "5 mg",
                                    "quote": "q"}])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["false_positives"]["k"], 1)
        self.assertEqual(s["false_positives_on_value"]["k"], 0)

    def test_findings_of_a_failed_call_are_never_counted(self):
        d = Q.judge_root(self.root) / "t" / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        qc_store.write_json_atomic(
            d / "round0-A1.json",
            {"call_id": "A1", "ok": False,
             "findings": [{"unit": "Q1", "verified": True, "said": "3-8 weeks",
                           "kind": "contradicted"}]})
        doc = self._doc(mutants=[{"mid": "m", "tag": "t", "uid": "Q1",
                                  "artifact": "quiz.json",
                                  "artifact_type": "quiz",
                                  "unit_type": "multiple_choice",
                                  "mutation": "duration", "book": "b",
                                  "printed": 1, "quote": "q",
                                  "before": "2-8 weeks", "after": "3-8 weeks",
                                  "confirmed_by": "t"}])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["recall"]["k"], 0)


# ---------------------------------------------------------------------------
class TestJudgedIsAMeasurement(TempRoot):
    """What may be counted: a unit a VERIFIED call actually covered, in a
    judgment about THIS set. Everything else is an item nobody read, and §6's
    floors are the size of the measurement, not the size of the plan."""

    def _judgment(self, tag="t", name="round0-A1.json", **kw):
        d = Q.judge_root(self.root) / tag / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"call_id": "A1", "kind": "A", "ok": True, "units": [],
               "coverage": [], "findings": []}
        rec.update(kw)
        qc_store.write_json_atomic(d / name, rec)
        return d / name

    def _doc(self, **kw):
        doc = {"built_at": "2026-09-23T00:00:00+00:00", "chapters": ["t"],
               "gold": [], "mutants": [], "negatives": [], "controls": [],
               "counts": {}}
        doc.update(kw)
        return doc

    def _neg(self, uid, artifact="study_guide.md"):
        return {"nid": f"t:{artifact}:{uid}", "tag": "t", "uid": uid,
                "artifact": artifact, "artifact_type": Q.artifact_type(artifact),
                "unit_type": "unit", "book": "Dulcan", "printed": 330,
                "raw": "5 mg", "quote": "q"}

    def test_a_coverage_row_is_proof_the_call_covered_the_unit(self):
        # §5.8.2 requires exactly one coverage row per unit of the call, so the
        # rows are the record of what was read even when `units` is absent.
        self._judgment(coverage=[{"unit": "G1", "status": "supported"}])
        s = Q.score(self._doc(negatives=[self._neg("G1"), self._neg("G2")]),
                    root=self.root)
        self.assertEqual(s["false_positives"]["n"], 1)

    def test_the_b_and_c_calls_of_a_stopped_chapter_are_not_counted(self):
        # qc_run stops a chapter at the first call that did not verify, so a
        # cached A call says nothing about the guide, the facts or the legends.
        self._judgment(units=[f"Q{i}" for i in range(4)])
        negs = [self._neg(f"Q{i}", artifact="quiz.json") for i in range(4)]
        negs += [self._neg(f"G{i}") for i in range(50)]
        s = Q.score(self._doc(negatives=negs), root=self.root)
        self.assertEqual(s["false_positives"]["n"], 4)
        self.assertEqual(s["judged_units"], 4)

    def test_a_judgment_about_another_set_is_not_counted(self):
        self._judgment(units=["Q1"], set_id="0000000000000000")
        doc = self._doc(set_id="1111111111111111",
                        negatives=[self._neg("Q1", artifact="quiz.json")])
        s = Q.score(doc, root=self.root)
        self.assertEqual(s["false_positives"]["n"], 0)
        self.assertEqual(s["stale_judgments"], {"t": 1})


# ---------------------------------------------------------------------------
class TestSetIdentity(TempRoot):
    """A report is about ONE experiment. `--build` reseeds from a walk over a
    pool whose size changed, so a unit that carried a mutant can come back as a
    negative while the old verdicts are still on disk."""

    def test_the_id_moves_when_the_set_moves(self):
        a = {"seed": 1, "mutants": [{"mid": "m1"}], "negatives": [], "gold": []}
        b = {"seed": 1, "mutants": [{"mid": "m2"}], "negatives": [], "gold": []}
        self.assertNotEqual(Q.set_id(a), Q.set_id(b))
        self.assertEqual(Q.set_id(a), Q.set_id(dict(a)))

    def test_a_rebuild_over_a_judged_set_is_refused(self):
        qc_store.write_json_atomic(Q.cal_root(self.root) / "set.json",
                                   {"built_at": "x", "chapters": []})
        d = Q.judge_root(self.root) / "t" / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        qc_store.write_json_atomic(d / "round0-A1.json", {"ok": True})
        with self.assertRaises(Q.CalibrationError) as cm:
            Q.build(tags=[], root=self.root, fetch=False, with_gold=False,
                    with_controls=False)
        self.assertIn("--force", str(cm.exception))

    def test_a_forced_rebuild_that_changes_the_set_supersedes_its_verdicts(self):
        # qc_run REUSES a cached judgment without rewriting it, so a re-run
        # after a rebuild would silently re-adopt verdicts about the old seeded
        # bytes — and scoring, which refuses them as older than the set, would
        # be stuck at NO-GO with no way forward but deleting files by hand.
        d = Q.judge_root(self.root) / "t" / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        qc_store.write_json_atomic(d / "round0-A1.json", {"ok": True})
        qc_store.write_json_atomic(Q.cal_root(self.root) / "set.json",
                                   {"built_at": "x", "set_id": "old",
                                    "chapters": []})
        with redirect_stdout(io.StringIO()):
            doc = Q.build(tags=[], root=self.root, fetch=False,
                          with_gold=False, with_controls=False, force=True)
        self.assertEqual(doc["superseded_judgments"], ["t"])
        self.assertFalse((d).exists())
        self.assertTrue(list((Q.judge_root(self.root) / "t").glob(
            "judgments.superseded-*")))

    def test_an_identical_rebuild_keeps_the_measurement(self):
        # `--build` twice is idempotent (the walk is seeded); throwing away a
        # day of agy time for a no-op would be its own failure.
        doc = Q.build(tags=[], root=self.root, fetch=False, with_gold=False,
                      with_controls=False)
        d = Q.judge_root(self.root) / "t" / "judgments"
        d.mkdir(parents=True, exist_ok=True)
        qc_store.write_json_atomic(d / "round0-A1.json", {"ok": True})
        again = Q.build(tags=[], root=self.root, fetch=False, with_gold=False,
                        with_controls=False, force=True)
        self.assertEqual(again["set_id"], doc["set_id"])
        self.assertEqual(again.get("superseded_judgments"), None)
        self.assertTrue((d / "round0-A1.json").exists())

    def test_a_journal_bound_to_another_set_stops_skipping_chapters(self):
        j = Q.RunJournal(self.root)
        j.bind("aaaaaaaaaaaaaaaa")
        j.finish("t", {"status": "approved"}, [])
        self.assertTrue(Q.RunJournal(self.root).done("t"))
        j2 = Q.RunJournal(self.root)
        j2.bind("bbbbbbbbbbbbbbbb")
        self.assertFalse(j2.done("t"),
                         "a chapter measured on another set was skipped")

    def test_the_go_line_refuses_a_journal_about_another_set(self):
        j = Q.RunJournal(self.root)
        j.bind("aaaaaaaaaaaaaaaa")
        m = Q._call_metrics({"chapters": [], "set_id": "bbbbbbbbbbbbbbbb"},
                            root=self.root)
        self.assertTrue(m["set_mismatch"])


# ---------------------------------------------------------------------------
class TestControlsCountOnlyTheMechanism(unittest.TestCase):
    """§6's controls are the only test of §2.1 that reproduces the 2026-09-18
    incident. A rejection that did not come from the verification path proves
    the harness is broken, not that the mechanism works."""

    def test_a_verify_rejection_that_never_reached_the_judge_does_not_count(self):
        self.assertFalse(Q.control_rejected(
            {"rejected": True, "gate": "verify", "attempts": 0}))
        self.assertTrue(Q.control_rejected(
            {"rejected": True, "gate": "verify", "attempts": 1}))

    def test_qc_judges_own_refusal_counts(self):
        self.assertTrue(Q.control_rejected(
            {"rejected": True, "gate": "JudgeError", "attempts": 0}))

    def test_any_other_exception_does_not(self):
        for gate in ("TypeError", "ImportError", "FileNotFoundError"):
            self.assertFalse(Q.control_rejected(
                {"rejected": True, "gate": gate, "attempts": 3}), gate)

    def test_an_unexpected_gate_is_named_for_the_go_line(self):
        m = Q._control_metrics({"controls": [
            {"control": "bundle_missing", "rejected": True,
             "gate": "TypeError", "reason": "unexpected keyword",
             "attempts": 0}]})
        self.assertEqual(m["overall"]["k"], 0)
        self.assertEqual([u["gate"] for u in m["unexpected"]], ["TypeError"])
        go, why = Q.verdict_line({"gold": [], "mutants": [], "negatives": []},
                                 {**Q.score({"chapters": [], "mutants": [],
                                             "negatives": [], "gold": []}),
                                  "controls": m})
        self.assertFalse(go)
        self.assertTrue(any("מנגנון" in w for w in why), why)


# ---------------------------------------------------------------------------
class TestKeySwapIsReallyWrong(unittest.TestCase):
    """A key swap has to leave an item its own source refutes. §6's mutants are
    "single locked-value edits ... Code confirms each contradicts its page
    first", and a key moved onto an option the page also states is an item that
    is still correct — the judge's silence would be scored as a recall miss."""

    PAGE = ("Atomoxetine is approved as monotherapy for ADHD. Guanfacine ER is "
            "approved as monotherapy for ADHD. Full therapeutic benefits may "
            "take 2–8 weeks.")

    def test_the_page_stating_the_new_key_vetoes_the_swap(self):
        ok, why = Q.key_swap_contradicts(
            confirmed(raw="2-8 weeks"),
            "2-8 weeks", "Guanfacine ER, an alpha-2A agonist", self.PAGE)
        self.assertFalse(ok)
        self.assertIn("also states", why)

    def test_the_key_must_sit_on_what_the_page_confirms(self):
        ok, why = Q.key_swap_contradicts(
            confirmed(raw="2-8 weeks"), "Atomoxetine", "Immediately", self.PAGE)
        self.assertFalse(ok)
        self.assertIn("not in the keyed option", why)

    def test_a_real_swap_passes_both_gates(self):
        ok, why = Q.key_swap_contradicts(
            confirmed(raw="2-8 weeks"), "2-8 weeks", "Immediately", self.PAGE)
        self.assertTrue(ok, why)

    def test_the_hit_needle_is_the_entity_not_the_whole_option(self):
        needles = Q._key_needles("Guanfacine ER, an alpha-2A adrenergic "
                                 "receptor agonist dosed at bedtime")
        self.assertIn("Guanfacine ER", needles)


# ---------------------------------------------------------------------------
class TestGoNeedsEveryBarMeasured(TempRoot):
    """The whole point of the file, in one place: GO is the owner's authority
    to let Tier 2 rewrite the material he is revising for a board exam."""

    GOLD = [{"gid": g, "tag": "t", "unit": g.split("-")[1], "rule": "r",
             "label": "contradicted", "book": "DSM-5-TR", "printed": 1,
             "quote": "q", "said": "", "detail": "", "other_hits": [],
             "owner_review": False} for g in Q.REQUIRED_GOLD]

    def _passing(self, **kw):
        s = {"judged_chapters": ["t"], "judged_units": 300,
             "recall": Q.rate(63, 63), "recall_any": Q.rate(63, 63),
             "recall_mutants": Q.rate(60, 60), "recall_gold": Q.rate(3, 3),
             "false_positives": Q.rate(0, 200),
             "false_positives_on_value": Q.rate(0, 200),
             "controls": {"overall": Q.rate(10, 10), "by_control": {},
                          "gates": ["verify"], "unexpected": [], "error": ""},
             "canary_first_attempt": Q.rate(10, 10),
             "canary_after_retries": Q.rate(10, 10),
             "failed_chapters": {}, "unfinished_chapters": [], "paused": None,
             "set_mismatch": False,
             "gold": [{**g, "judged": True, "hit_exact": True, "hit_any": True}
                      for g in self.GOLD],
             "by_artifact": {}, "by_unit_type": {}, "by_book": {},
             "by_mutation": {}, "mutants": [], "negatives": []}
        s.update(kw)
        return s

    def _doc(self, **kw):
        doc = {"built_at": "2026-09-23T00:00:00+00:00", "chapters": ["t"],
               "gold": list(self.GOLD), "mutants": [], "negatives": [],
               "controls": [], "bar": {}, "pool": 0}
        doc.update(kw)
        return doc

    def test_the_reference_case_is_a_go(self):
        # If this ever fails, every other test in the class is vacuous.
        go, why = Q.verdict_line(self._doc(), self._passing())
        self.assertTrue(go, why)

    def test_one_missing_gold_item_blocks(self):
        for gid in Q.REQUIRED_GOLD:
            rest = [g for g in self.GOLD if g["gid"] != gid]
            go, why = Q.verdict_line(
                self._doc(gold=rest),
                self._passing(gold=[{**g, "judged": True, "hit_exact": True,
                                     "hit_any": True} for g in rest]))
            self.assertFalse(go, f"GO without §1's {gid}")
            self.assertTrue(any(gid in w for w in why), why)

    def test_a_gold_item_that_was_never_judged_blocks(self):
        s = self._passing(gold=[{**g, "judged": g["gid"] != "038-Q9",
                                 "hit_exact": True, "hit_any": True}
                                for g in self.GOLD])
        go, why = Q.verdict_line(self._doc(), s)
        self.assertFalse(go)
        self.assertTrue(any("038-Q9" in w for w in why), why)

    def test_a_chapter_nobody_judged_blocks(self):
        go, why = Q.verdict_line(
            self._doc(), self._passing(unfinished_chapters=["study-070-ect"]))
        self.assertFalse(go)
        self.assertTrue(any("070" in w for w in why), why)

    def test_a_control_caught_by_the_wrong_gate_blocks(self):
        s = self._passing()
        s["controls"] = {**s["controls"],
                         "unexpected": [{"control": "bundle_missing",
                                         "gate": "TypeError", "reason": "x"}]}
        self.assertFalse(Q.verdict_line(self._doc(), s)[0])

    def test_controls_that_could_not_be_re_run_block(self):
        s = self._passing()
        s["controls"] = {**s["controls"], "error": "no books on this machine"}
        self.assertFalse(Q.verdict_line(self._doc(), s)[0])


# ---------------------------------------------------------------------------
class TestNegativesSayHowMuchOfThemIsConfirmed(unittest.TestCase):
    """§6 asks for confirmed UNITS; `confirm_unit` confirms one locked VALUE
    against one cited page. The headline FP rate stays on the strict reading,
    and the rows say what it rests on."""

    def test_a_row_carries_its_confirmed_and_total_value_counts(self):
        pool = [confirmed(uid="C1", raw="2-8 weeks"),
                confirmed(uid="C1", raw="1.2 mg/kg/day", kind="dose")]
        rows = Q.negatives(pool, [], n=5,
                           values_by_unit={(pool[0].tag, "C1"): 3})
        self.assertEqual(rows[0].values_confirmed, 2)
        self.assertEqual(rows[0].values_total, 3)
        counts = Q._set_counts({"negatives": [r.as_dict() for r in rows]})
        self.assertEqual(counts["negatives_fully_confirmed"], 0)


# ---------------------------------------------------------------------------
class TestAnUnjudgedChapterIsNotFinished(TempRoot):
    """§2.3: "No verified verdict → `unjudged`, never approved."" """

    def setUp(self):
        super().setUp()
        self.tag = "study-005-serotonin-system"
        d = Q.seeded_root(self.root) / self.tag
        d.mkdir(parents=True)
        (d / "quiz.json").write_text(json.dumps(QUIZ, ensure_ascii=False,
                                                indent=2), encoding="utf-8")
        doc = {"built_at": "x", "chapters": [self.tag], "gold": [],
               "mutants": [], "negatives": [], "discarded": [], "controls": [],
               "bar": {}}
        doc["set_id"] = Q.set_id(doc)
        doc["counts"] = Q._set_counts(doc)
        qc_store.write_json_atomic(Q.cal_root(self.root) / "set.json", doc)
        self.sources = FakeSources(self.tmp / "work")

    def test_it_is_recorded_as_a_chapter_that_did_not_reach_a_verdict(self):
        def refused(**kw):
            plan = kw["plan"]
            return {"call_id": plan.call_id, "kind": plan.kind, "ok": False,
                    "reason": "canary: 2 of 4 codes did not match",
                    "findings": [], "coverage": [], "attempts": 3,
                    "duration_s": 3.0, "unchecked": 0,
                    "canary": {"requested": [["X", 1]], "returned": [],
                               "verified": False},
                    "tempmedia_ok": True, "model": "fake", "agy_version": "fake"}

        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            code = Q.run_prepared(root=self.root, judge=refused,
                                  sources=self.sources)
        self.assertEqual(code, Q.EXIT_ERROR)
        self.assertFalse(Q.RunJournal(self.root).done(self.tag))
        out = Q.report(root=self.root)
        self.assertFalse(out["go"])
        self.assertTrue(any(self.tag in w for w in out["why_not"]),
                        out["why_not"])


# ---------------------------------------------------------------------------
class TestTextHelpers(unittest.TestCase):

    def test_a_phrase_matches_across_a_line_break(self):
        import re
        page = "Carbamazepine also decreases lithium\nclearance, increasing"
        self.assertTrue(re.search(Q.phrase("decreases lithium clearance"),
                                  page, re.I))

    def test_a_phrase_matches_an_en_dash_written_as_a_hyphen(self):
        import re
        self.assertTrue(re.search(Q.phrase("may take 2-8 weeks"),
                                  "may take 2–8 weeks", re.I))

    def test_a_sentence_is_returned_verbatim_with_its_whitespace_collapsed(self):
        page = "First one. Second\nsentence  here. Third one."
        got = Q.sentence_at(page, page.index("Second"),
                            page.index("Second") + 6)
        self.assertIn("Second sentence here", got)
        self.assertNotIn("First one", got)


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()
