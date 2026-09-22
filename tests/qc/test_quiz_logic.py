#!/usr/bin/env python3
"""Offline tests for app/quiz_logic.py — the grader and the QC marks.

Every grading case runs against REAL released items (tests/qc/fixtures/quiz/,
trimmed copies of two published chapters), because the shapes that broke the app
are the shapes NotebookLM actually emits: an item with no `type` field, a
multiple_select with three keys, a fill-in with three acceptable answers, a
short_answer whose whole verdict is a paragraph of required attributes.

The QC cases are synthetic — qc_fix has not run yet, so no released asset has a
`qc` record. They replay the two findings the 2026-09-18 spec review adjudicated
against the books: the atomoxetine onset conflict (StahlKids p.69 "2-8 weeks"
vs p.74 "8-12 weeks") and a plain contradiction.

    python -m unittest discover -s tests/qc -t .

No network, no agy, no books, no Streamlit runtime.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
import quiz_logic as ql  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "quiz"
TAG_042 = "study-042-benzodiazepines-buspirone-sleep-aids-overview"
TAG_001 = "study-001-psychiatric-examination-of-child-and-adolescent"


def load(tag: str, name: str):
    return json.loads((FIXTURES / tag / name).read_text(encoding="utf-8"))


def items(tag: str) -> list[dict]:
    return load(tag, "quiz.json")["questions"]


def by_type(tag: str, t: str) -> dict:
    """The first item of type `t`, or a skip.

    The fixtures under tests/qc/fixtures/ are shared with the other QC modules'
    tests and get trimmed as those grow. Nothing here may depend on how many
    items a file holds or on their order — only on a real item of the type
    under test being present, and on a skip that says so when it is not.
    """
    for q in items(tag):
        if (q.get("type") or "") == t:
            return q
    raise unittest.SkipTest(f"no {t or 'untyped'} item in {tag}/quiz.json")


# The atomoxetine source conflict, as qc_fix would embed it (spec §5.9 Tier 1).
ATOMOXETINE = {
    "type": "multiple_choice",
    "question": "How long until atomoxetine's full therapeutic benefit?",
    "answerOptions": [
        {"text": "8-12 weeks", "isCorrect": True, "rationale": "StahlKids p.74."},
        {"text": "2-8 weeks", "isCorrect": False, "rationale": "Too early."},
        {"text": "Within 48 hours", "isCorrect": False, "rationale": "Stimulant-like onset."},
    ],
    "hint": "Not a stimulant.",
    "qc": {
        "kind": "content", "date": "2026-09-20", "severity": "high",
        "status": "active", "was": "8-12 weeks",
        "source": {"book": "StahlKids", "printed_page": 69,
                   "quote": "full therapeutic benefits may take 2-8 weeks"},
    },
}


class NormalizeItem(unittest.TestCase):
    def test_declared_types_survive(self):
        seen = set()
        for q in items(TAG_042):
            self.assertEqual(ql.normalize_item(q)["type"], q["type"])
            seen.add(q["type"])
        # The chapter the QC review sampled for the type zoo: it is the one
        # fixture that holds all four declared types at once.
        self.assertEqual(seen, {ql.MULTIPLE_CHOICE, ql.MULTIPLE_SELECT,
                                ql.FILL_IN_THE_BLANK, ql.SHORT_ANSWER})

    def test_untyped_item_is_multiple_choice(self):
        # 107 of the 149 sampled released items carry no `type` at all, and every
        # one has exactly one key. Inferred from the options, not assumed.
        untyped = [q for q in items(TAG_001) if not q.get("type")]
        self.assertTrue(untyped, "fixture no longer holds an untyped item")
        for q in untyped:
            it = ql.normalize_item(q)
            self.assertEqual(it["type"], ql.MULTIPLE_CHOICE)
            self.assertEqual(sum(o["correct"] for o in it["options"]), 1)

    def test_options_carry_text_flag_and_rationale(self):
        it = ql.normalize_item(by_type(TAG_001, ""))
        self.assertGreaterEqual(len(it["options"]), 2)
        self.assertEqual([o["index"] for o in it["options"]],
                         list(range(len(it["options"]))))
        for o in it["options"]:
            self.assertTrue(o["text"])
            self.assertTrue(o["rationale"])      # every released option has one
        self.assertTrue(any(o["correct"] for o in it["options"]))

    def test_short_answer_grading_block_is_flattened(self):
        raw = by_type(TAG_042, "short_answer")
        it = ql.normalize_item(raw)
        self.assertEqual(it["model_answer"], raw["grading"]["modelAnswer"])
        self.assertEqual(it["required_attributes"],
                         raw["grading"]["requiredAttributes"])
        self.assertEqual(it["misconceptions"],
                         raw["grading"]["errors"]["misconceptions"])
        self.assertEqual(it["rationale"], raw["grading"]["rationale"])

    def test_fill_in_the_blank_best_answer_is_first_acceptable(self):
        raw = by_type(TAG_042, "fill_in_the_blank")
        it = ql.normalize_item(raw)
        self.assertEqual(it["best_answer"], raw["bestAnswer"])
        self.assertEqual(it["acceptable_answers"][0], raw["bestAnswer"])
        for a in raw["acceptableAnswers"]:
            self.assertIn(a, it["acceptable_answers"])

    def test_idempotent_and_stable_id(self):
        raw = by_type(TAG_001, "")
        once = ql.normalize_item(raw)
        self.assertIs(ql.normalize_item(once), once)
        self.assertEqual(once["id"], ql.normalize_item(dict(raw))["id"])
        other = by_type(TAG_042, ql.MULTIPLE_SELECT)
        self.assertNotEqual(once["id"], ql.normalize_item(other)["id"])
        # An id qc_fix already stamped wins over the recomputed one: fixes.json
        # references ids, never indexes (spec §5.9).
        self.assertEqual(ql.normalize_item({**raw, "id": "abc"})["id"], "abc")

    def test_plain_string_options_still_render(self):
        # The oldest export shape, still live in the first releases.
        it = ql.normalize_item({"question": "q", "options": ["a", "b"], "answer": "b"})
        self.assertEqual(it["type"], ql.MULTIPLE_CHOICE)
        self.assertTrue(it["options"][1]["correct"])

    def test_unknown_shape_is_not_guessed(self):
        self.assertEqual(ql.normalize_item({"question": "q"})["type"], ql.UNKNOWN)
        self.assertEqual(ql.normalize_item(None)["type"], ql.UNKNOWN)


class GradeMultipleChoice(unittest.TestCase):
    def setUp(self):
        self.item = by_type(TAG_042, "multiple_choice")

    def test_key_by_index_and_by_text(self):
        for response in (0, self.item["answerOptions"][0]["text"]):
            res = ql.grade(self.item, response)
            self.assertIs(res["correct"], True)
            self.assertEqual(res["expected"], self.item["answerOptions"][0]["text"])

    def test_distractor_is_wrong_and_names_the_key(self):
        res = ql.grade(self.item, 2)
        self.assertIs(res["correct"], False)
        self.assertIn(self.item["answerOptions"][0]["text"], res["feedback"])
        self.assertTrue(res["expected_rationale"])

    def test_nothing_chosen_is_not_wrong(self):
        self.assertIsNone(ql.grade(self.item, None)["correct"])

    def test_unrecognised_choice_is_not_wrong(self):
        self.assertIsNone(ql.grade(self.item, "something else")["correct"])
        self.assertIsNone(ql.grade(self.item, 99)["correct"])


class GradeMultipleSelect(unittest.TestCase):
    """All-or-nothing, and every key shown when it fails. Five of the six
    released multiple_select items have three keys; the old single radio button
    scored a learner wrong for picking one of them."""

    def setUp(self):
        self.item = by_type(TAG_042, "multiple_select")
        self.keys = [i for i, o in enumerate(self.item["answerOptions"])
                     if o["isCorrect"]]
        self.assertGreaterEqual(len(self.keys), 2)

    def test_all_keys_is_correct(self):
        res = ql.grade(self.item, self.keys)
        self.assertIs(res["correct"], True)
        self.assertEqual(len(res["expected"]), len(self.keys))

    def test_partial_is_wrong_and_lists_every_key(self):
        res = ql.grade(self.item, self.keys[:1])
        self.assertIs(res["correct"], False)
        self.assertEqual(len(res["missed"]), len(self.keys) - 1)
        for o in self.item["answerOptions"]:
            if o["isCorrect"]:
                self.assertIn(o["text"], res["feedback"])

    def test_extra_pick_is_wrong(self):
        wrong = next(i for i, o in enumerate(self.item["answerOptions"])
                     if not o["isCorrect"])
        res = ql.grade(self.item, self.keys + [wrong])
        self.assertIs(res["correct"], False)
        self.assertEqual(len(res["extra"]), 1)

    def test_text_responses_work_too(self):
        texts = [self.item["answerOptions"][i]["text"] for i in self.keys]
        self.assertIs(ql.grade(self.item, texts)["correct"], True)

    def test_empty_selection_is_not_wrong(self):
        self.assertIsNone(ql.grade(self.item, [])["correct"])


class GradeFillInTheBlank(unittest.TestCase):
    def setUp(self):
        self.item = by_type(TAG_042, "fill_in_the_blank")
        self.best = self.item["bestAnswer"]

    def test_every_acceptable_answer_is_accepted(self):
        # Three spellings of the same answer ship in one released item
        # ("Buspirone", "SSRI", "Selective serotonin reuptake inhibitors").
        for a in [self.best] + list(self.item["acceptableAnswers"]):
            self.assertIs(ql.grade(self.item, a)["correct"], True, a)

    def test_case_spacing_and_punctuation_are_ignored(self):
        for a in [f"  {self.best.upper()}  ", f"**{self.best}**",
                  f"{self.best}.", f"“{self.best}”"]:
            self.assertIs(ql.grade(self.item, a)["correct"], True, a)

    def test_wrong_answer_is_wrong_and_shows_the_expected_one(self):
        res = ql.grade(self.item, "phenobarbital")
        self.assertIs(res["correct"], False)
        self.assertEqual(res["expected"], self.best)
        self.assertFalse(res["near"])

    def test_answer_inside_a_sentence_is_a_near_miss_not_a_pass(self):
        res = ql.grade(self.item, f"probably {self.best} I think")
        self.assertIs(res["correct"], False)
        self.assertTrue(res["near"])

    def test_blank_is_not_wrong(self):
        self.assertIsNone(ql.grade(self.item, "   ")["correct"])

    def test_hebrew_latex_and_diacritics_normalize(self):
        item = {"type": "fill_in_the_blank",
                "bestAnswer": "ליתיום",
                "acceptableAnswers": ["$\\text{Lithium}$"]}
        self.assertIs(ql.grade(item, "  לִיתְיוּם ")["correct"], True)
        self.assertIs(ql.grade(item, "lithium")["correct"], True)

    def test_a_range_is_not_the_concatenated_number(self):
        # StahlKids p.69 "2-8 weeks": "28 weeks" must not pass as "2-8 weeks".
        item = {"type": "fill_in_the_blank", "bestAnswer": "2-8 weeks"}
        self.assertIs(ql.grade(item, "2–8 weeks")["correct"], True)
        self.assertIs(ql.grade(item, "28 weeks")["correct"], False)


class GradeShortAnswer(unittest.TestCase):
    """Self-graded: spec §5.11 forbids a runtime LLM, and a string compare on a
    paragraph would be a verdict worse than none."""

    def setUp(self):
        self.item = by_type(TAG_042, "short_answer")

    def test_no_verdict_but_the_model_answer_and_the_checklist(self):
        res = ql.grade(self.item, "anything at all")
        self.assertIsNone(res["correct"])
        self.assertEqual(res["expected"],
                         self.item["grading"]["modelAnswer"])
        self.assertEqual(res["required_attributes"],
                         self.item["grading"]["requiredAttributes"])
        self.assertTrue(res["required_attributes"])

    def test_empty_response_still_shows_the_model_answer(self):
        self.assertIsNone(ql.grade(self.item, "")["correct"])
        self.assertTrue(ql.grade(self.item, "")["expected"])


class QcMarks(unittest.TestCase):
    def rec(self, **over):
        base = dict(ATOMOXETINE["qc"])
        base.update(over)
        return {"qc": base}

    def test_active_content_record_becomes_a_mark(self):
        m = ql.qc_mark(self.rec())
        self.assertEqual(m["severity_he"], "חמור")
        self.assertEqual(m["was"], "8-12 weeks")
        self.assertEqual(m["sources"][0]["page"], 69)
        self.assertFalse(m["conflict"])
        self.assertIn("סתירה", m["headline"])

    def test_presentation_fix_shows_nothing(self):
        # The answer-order rebalance (§5.9) is invisible by design.
        self.assertIsNone(ql.qc_mark(self.rec(kind="presentation")))

    def test_withdrawn_shows_nothing(self):
        self.assertIsNone(ql.qc_mark(self.rec(status="withdrawn")))

    def test_unknown_kind_shows_nothing(self):
        self.assertIsNone(ql.qc_mark(self.rec(kind="lint")))

    def test_item_without_qc_has_no_mark(self):
        for q in items(TAG_042) + items(TAG_001):
            self.assertIsNone(ql.qc_mark(q))
        self.assertIsNone(ql.qc_mark({}))
        self.assertIsNone(ql.qc_mark(None))

    def test_source_conflict_shows_both_quotes(self):
        for rec in (
            self.rec(kind="content", source=[
                {"book": "StahlKids", "printed_page": 69, "quote": "2-8 weeks"},
                {"book": "StahlKids", "printed_page": 74, "quote": "8-12 weeks"}]),
            self.rec(second_source={"book": "StahlKids", "printed_page": 74,
                                    "quote": "8-12 weeks"}),
        ):
            m = ql.qc_mark(rec)
            self.assertTrue(m["conflict"])
            self.assertEqual(len(m["sources"]), 2)
            self.assertIn("חלוקים", m["headline"])

    def test_kartisiot_is_named_in_hebrew(self):
        # Owner decision 2026-09-18, policy 4: anything grounded in the
        # flashcard summaries must say so.
        m = ql.qc_mark(self.rec(source={"book": "Kartisiot", "printed_page": 12,
                                        "quote": "…"}))
        self.assertEqual(m["sources"][0]["book_he"], "סיכומי כרטיסיות שלב ב")

    def test_page_key_aliases(self):
        m = ql.qc_mark(self.rec(source={"book": "DSM-5-TR", "page": 135,
                                        "quote": "three (or more)"}))
        self.assertEqual(m["sources"][0]["page"], 135)
        self.assertEqual(m["sources"][0]["book_he"], "DSM-5-TR")

    def test_normalized_item_exposes_the_mark(self):
        self.assertIsNotNone(ql.normalize_item(ATOMOXETINE)["qc"])


class DisputedKey(unittest.TestCase):
    """A choice the quoted page supports is not scored wrong against a key the
    QC run already marked (spec §5.11)."""

    def test_source_backed_choice_is_accepted(self):
        res = ql.grade(ATOMOXETINE, 1)          # "2-8 weeks", the quoted value
        self.assertIs(res["correct"], True)
        self.assertTrue(res["source_backed"])
        self.assertIsNotNone(res["qc"])

    def test_the_disputed_key_is_still_accepted(self):
        res = ql.grade(ATOMOXETINE, 0)
        self.assertIs(res["correct"], True)
        self.assertTrue(res["disputed"])

    def test_an_unrelated_distractor_is_still_wrong(self):
        res = ql.grade(ATOMOXETINE, 2)          # "Within 48 hours"
        self.assertIs(res["correct"], False)
        self.assertFalse(res.get("source_backed", False))

    def test_a_number_alone_does_not_buy_a_pass(self):
        item = dict(ATOMOXETINE)
        item["answerOptions"] = [
            {"text": "8-12 weeks", "isCorrect": True},
            {"text": "2-8 tablets", "isCorrect": False},
        ]
        self.assertIs(ql.grade(item, 1)["correct"], False)

    def test_wrong_answers_on_an_unmarked_item_are_unaffected(self):
        item = {k: v for k, v in ATOMOXETINE.items() if k != "qc"}
        self.assertIs(ql.grade(item, 1)["correct"], False)


class CardDueOverride(unittest.TestCase):
    """A corrected card jumps the SR schedule; nothing is written to do it."""

    CARD = {"front": "Atomoxetine onset?", "back": "8-12 weeks",
            "qc": dict(ATOMOXETINE["qc"])}

    def test_no_state_is_due(self):
        self.assertTrue(ql.card_due_override(self.CARD, None))
        self.assertTrue(ql.card_due_override(self.CARD, {}))

    def test_state_never_seen_is_due(self):
        self.assertTrue(ql.card_due_override(
            self.CARD, {"due_date": "2027-01-01", "last_seen": None}))

    def test_seen_before_the_correction_is_due(self):
        self.assertTrue(ql.card_due_override(
            self.CARD, {"last_seen": "2026-09-19T22:10:00", "interval_days": 90}))

    def test_seen_the_same_day_is_due(self):
        # qc_fix stamps a date, sr_engine stamps a timestamp: same-day cannot be
        # ordered, and the learner-safe direction is to show the card again.
        self.assertTrue(ql.card_due_override(
            self.CARD, {"last_seen": "2026-09-20T09:00:00"}))

    def test_seen_after_the_correction_is_not_forced(self):
        self.assertFalse(ql.card_due_override(
            self.CARD, {"last_seen": "2026-09-21T09:00:00"}))

    def test_presentation_and_withdrawn_do_not_force(self):
        for over in ({"kind": "presentation"}, {"status": "withdrawn"}):
            card = {**self.CARD, "qc": {**self.CARD["qc"], **over}}
            self.assertFalse(ql.card_due_override(card, {"last_seen": "2026-01-01"}))

    def test_cards_without_qc_are_untouched(self):
        for c in load(TAG_001, "flashcards.json")["cards"]:
            self.assertFalse(ql.card_due_override(c, None))
            self.assertFalse(ql.card_due_override(c, {"last_seen": "2026-01-01"}))

    def test_unreadable_date_still_shows_the_correction(self):
        card = {**self.CARD, "qc": {**self.CARD["qc"], "date": "לא ידוע"}}
        self.assertTrue(ql.card_due_override(card, {"last_seen": "2026-09-21"}))


QC_JSON = {
    "tag": TAG_042, "chapter": 42, "status": "fixed", "date": "2026-09-21",
    "assets": {
        "quiz.json": {"status": "checked", "sha256": "a" * 64},
        "flashcards.json": {"status": "checked", "sha256": "b" * 64},
        "video.mp4": {"status": "not_checked"},
    },
    "errata": [
        {"id": "f1", "asset": "quiz.json", "unit": "Q2", "kind": "content",
         "severity": "high", "date": "2026-09-21", "status": "active",
         "was": "8-12 weeks", "now": "2-8 weeks",
         "source": {"book": "StahlKids", "printed_page": 69,
                    "quote": "full therapeutic benefits may take 2-8 weeks"}},
        {"id": "f2", "asset": "sketchy_main.txt", "unit": "L:3", "kind": "content",
         "severity": "medium", "date": "2026-09-21", "status": "withdrawn",
         "was": "300 mg", "source": {"book": "Dulcan", "printed_page": 771,
                                     "quote": "…"}},
    ],
}


class QcJsonDocument(unittest.TestCase):
    def test_withdrawn_erratum_is_not_listed(self):
        qc = ql.parse_qc_json(QC_JSON)
        self.assertEqual(qc["n_errata"], 1)
        self.assertEqual(qc["errata"][0]["id"], "f1")

    def test_badge_and_asset_lines(self):
        qc = ql.parse_qc_json(QC_JSON)
        self.assertEqual(ql.badge_text(qc),
                         "נבדק מול ספרי הלימוד ב-2026-09-21, 1 תיקונים")
        lines = ql.asset_lines(qc)
        self.assertIn("וידאו: לא נבדק", lines)
        self.assertIn("קוויז: 1 תיקונים", lines)
        self.assertIn("כרטיסיות: נבדק, ללא תיקונים", lines)

    def test_needs_owner_is_said_out_loud(self):
        qc = ql.parse_qc_json({**QC_JSON, "status": "needs_owner"})
        self.assertIn("ממתין להחלטה שלך", ql.badge_text(qc))

    def test_errata_are_filtered_per_asset(self):
        qc = ql.parse_qc_json(QC_JSON)
        self.assertEqual(len(ql.errata_for_asset(qc, "quiz.json")), 1)
        self.assertEqual(ql.errata_for_asset(qc, "sketchy_main.txt"), [])
        self.assertEqual(ql.errata_for_asset(qc, "study_guide.md"), [])

    def test_assets_as_a_list_of_dicts(self):
        qc = ql.parse_qc_json({**QC_JSON, "assets": [
            {"name": "video.mp4", "status": "not_checked"}]})
        self.assertEqual(ql.asset_lines(qc), ["וידאו: לא נבדק"])


class BackwardCompatibility(unittest.TestCase):
    """The app is deployed from main and reads live releases. Every chapter
    published before the QC run has no qc field and no qc.json."""

    def test_no_qc_json_draws_nothing(self):
        self.assertIsNone(ql.parse_qc_json(None))
        self.assertIsNone(ql.parse_qc_json("not json"))
        self.assertEqual(ql.badge_text(None), "")
        self.assertEqual(ql.asset_lines(None), [])
        self.assertEqual(ql.errata_for_asset(None, "quiz.json"), [])

    def test_empty_qc_json_is_a_chapter_with_no_errata(self):
        qc = ql.parse_qc_json({})
        self.assertEqual(qc["n_errata"], 0)
        self.assertEqual(qc["assets"], [])
        self.assertTrue(ql.badge_text(qc).endswith("0 תיקונים"))

    def test_every_released_item_still_grades(self):
        for tag in (TAG_042, TAG_001):
            for q in items(tag):
                it = ql.normalize_item(q)
                self.assertIsNone(it["qc"])
                res = ql.grade(q, 0 if it["options"] else "x")
                self.assertIn("correct", res)
                self.assertIn("expected", res)
                self.assertIn("feedback", res)


class AppWiring(unittest.TestCase):
    """app.py is imported, not run: the module body only defines functions and
    registers caches. It catches the mistakes that a Streamlit-free test can
    still catch — a missing import, a renamed helper, an unescaped quote."""

    @classmethod
    def setUpClass(cls):
        try:
            import streamlit  # noqa: F401
        except Exception as e:                       # pragma: no cover
            raise unittest.SkipTest(f"streamlit not installed: {e}")
        sys.path.insert(0, str(ROOT / "app"))
        import app
        cls.app = app

    def test_quiz_logic_is_wired_in(self):
        for name in ("render_quiz_item", "render_qc_mark", "render_qc_badge",
                     "render_errata_page", "load_release_qc", "load_errata_index"):
            self.assertTrue(callable(getattr(self.app, name)), name)

    def test_mark_html_escapes_the_quote(self):
        mark = ql.qc_mark({"qc": {**ATOMOXETINE["qc"], "was": "<script>x</script>",
                                  "source": {"book": "Dulcan", "printed_page": 771,
                                             "quote": "doses <50 mg & up"}}})
        html = self.app._qc_mark_html(mark)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;50 mg &amp; up", html)
        self.assertIn("Dulcan", html)
        self.assertIn("771", html)

    def test_legend_rendering_still_parses_a_real_legend(self):
        lg = self.app.parse_legend("השלט: כאן\nהמקום: שם\n1. רמז: עובדה — וו\n")
        self.assertEqual(lg["sign"], "כאן")
        self.assertEqual(len(lg["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
