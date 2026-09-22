#!/usr/bin/env python3
"""Offline tests for qc_lint: every check fires on a defect that really shipped.

The rule this file follows: a positive case is a string taken out of a released
chapter, and a negative case is a released chapter that is clean. Inventing both
sides would only prove the regex matches itself.

What comes from the fixtures (our own material, trimmed):
  - `quiz/study-001` — 8 single-answer items, key on A in all 8. The measurement
    §5.4 is built on (A 98 / B 31 / C 3 / D 0 over 132 items, always-A = 74%).
  - `quiz/study-042` — the chapter that carries all four declared types, a
    leaked `General Instruction Filter`, and `$3.2\\text{ mEq/L}$`.
  - `text/study-029` — the guide with `(StahlNeuro p. 289, 902)` and
    `p. 266-267, 290, 1030` in a 624-page book: the two impossible pages the old
    first-number-only gate passed.
  - `hygiene/guide_049_signoff.md` — the head and tail of chapter 049's guide,
    ending in "אשמח לעזור בהרחבת תרגול נוסף…".
  - `legends/sketchy_main.txt` — a released legend whose "מספרים לבחינה" chips
    are clean, as the negative for the chip check.

Books: the citation and locked-value checks read the PDFs through qc_books, so
those tests skip when the books are not on this machine. Textbook text is never
copied into a fixture.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import qc_books  # noqa: E402
import qc_lint   # noqa: E402
import qc_units  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
QUIZ_001 = FIX / "quiz" / "study-001-psychiatric-examination-of-child-and-adolescent"
QUIZ_042 = FIX / "quiz" / "study-042-benzodiazepines-buspirone-sleep-aids-overview"
GUIDE_029 = FIX / "text" / "study-029-ssris-deep-dive" / "study_guide.md"
GUIDE_049 = FIX / "hygiene" / "guide_049_signoff.md"
LEGEND = FIX / "legends" / "sketchy_main.txt"


def books_available() -> bool:
    """The probe qc_books --check uses, without the cost of the other five."""
    try:
        return qc_books.book("StahlKids").has_page(69)
    except Exception:
        return False


HAVE_BOOKS = books_available()
needs_books = unittest.skipUnless(HAVE_BOOKS, "Text BOOKS/ not on this machine")


def checks(res: dict, uid: str | None = None) -> list[str]:
    """Every check name that fired, optionally for one unit."""
    if uid is not None:
        return [f["check"] for f in res["flags"].get(uid, [])]
    return ([f["check"] for items in res["flags"].values() for f in items]
            + [f["check"] for f in res["chapter"]])


def details(res: dict, check: str) -> list[str]:
    return ([f["detail"] for items in res["flags"].values() for f in items
             if f["check"] == check]
            + [f["detail"] for f in res["chapter"] if f["check"] == check])


def quiz_asset(*items: dict) -> dict:
    return {"quiz.json": json.dumps({"questions": list(items)}, ensure_ascii=False)}


def cards_asset(*cards: dict) -> dict:
    return {"flashcards.json": json.dumps({"cards": list(cards)}, ensure_ascii=False)}


def mc(stem="מהו מנגנון הפעולה העיקרי של התרופה הזו בקליניקה?",
       options=("אלפא", "בטא", "גמא", "דלתא"), key=0, **extra) -> dict:
    item = {"type": "multiple_choice", "question": stem,
            "answerOptions": [{"text": t, "isCorrect": i == key}
                              for i, t in enumerate(options)]}
    item.update(extra)
    return item


def offline(assets: dict, tag: str = "study-000-test") -> dict:
    return qc_lint.lint_chapter(tag, assets, use_books=False)


# ---------------------------------------------------------------------------
class TestQuizSchema(unittest.TestCase):
    """§5.4: types, exactly one key, >= 2 options, empty fields."""

    def test_unknown_type_is_flagged(self):
        res = offline(quiz_asset(mc(type="ordering")))
        self.assertIn("quiz_unknown_type", checks(res, "Q1"))
        self.assertEqual(details(res, "quiz_unknown_type"), ["ordering"])

    def test_declared_types_are_not_unknown(self):
        # 042 carries multiple_choice, multiple_select, fill_in_the_blank and
        # short_answer; none of them is an unknown type.
        res = offline(qc_lint.read_chapter_dir(QUIZ_042))
        self.assertNotIn("quiz_unknown_type", res["counts"])
        self.assertEqual(set(res["stats"]["types"]) - qc_lint.KNOWN_TYPES, set())

    def test_untyped_is_graded_as_single_answer(self):
        # 107 of the 149 sampled items predate the `type` field. They are not an
        # error, but they are held to the one-key rule the app grades them by.
        res = offline(quiz_asset({"question": "שאלה ללא type אך עם שתי אפשרויות",
                                  "answerOptions": [{"text": "א", "isCorrect": True},
                                                    {"text": "ב", "isCorrect": True}]}))
        self.assertNotIn("quiz_unknown_type", res["counts"])
        self.assertIn("quiz_key_count", checks(res, "Q1"))

    def test_two_keys_on_a_multiple_choice(self):
        item = mc()
        item["answerOptions"][1]["isCorrect"] = True
        res = offline(quiz_asset(item))
        self.assertIn("quiz_key_count", checks(res, "Q1"))
        self.assertIn("2", details(res, "quiz_key_count")[0])

    def test_no_key_at_all(self):
        res = offline(quiz_asset(mc(key=-99)))
        self.assertIn("quiz_key_count", checks(res, "Q1"))

    def test_fewer_than_two_options(self):
        res = offline(quiz_asset(mc(options=("רק אחת",))))
        self.assertIn("quiz_too_few_options", checks(res, "Q1"))
        # ...and a one-option item is not also counted in the key statistics.
        self.assertEqual(res["stats"]["single_answer"], 0)

    def test_empty_fields(self):
        res = offline(quiz_asset(mc(stem="   ", options=("א", ""), hint="")))
        fired = checks(res, "Q1")
        self.assertEqual(fired.count("quiz_empty_field"), 3)

    def test_multiple_select_with_one_key(self):
        # The app grades multiple_select all-or-nothing; one key means it was
        # mislabelled and the learner is asked to tick a set that does not exist.
        item = mc(key=0)
        item["type"] = "multiple_select"
        res = offline(quiz_asset(item))
        self.assertIn("quiz_key_count", checks(res, "Q1"))

    def test_fill_in_the_blank_without_an_answer(self):
        res = offline(quiz_asset({"type": "fill_in_the_blank",
                                  "question": "הרמה הטיפולית של ליתיום היא ___"}))
        self.assertIn("quiz_missing_answer", checks(res, "Q1"))
        ok = offline(quiz_asset({"type": "fill_in_the_blank",
                                 "question": "הרמה הטיפולית של ליתיום היא ___",
                                 "bestAnswer": "0.6-1.2 mEq/L"}))
        self.assertNotIn("quiz_missing_answer", ok["counts"])

    def test_short_answer_without_a_model_answer(self):
        res = offline(quiz_asset({"type": "short_answer",
                                  "question": "תאר את מנגנון הפעולה של ליתיום"}))
        self.assertIn("quiz_missing_answer", checks(res, "Q1"))
        ok = offline(quiz_asset({"type": "short_answer", "question": "תאר",
                                 "grading": {"modelAnswer": "עיכוב GSK-3"}}))
        self.assertNotIn("quiz_missing_answer", ok["counts"])

    def test_released_quizzes_have_no_schema_errors(self):
        for d in (QUIZ_001, QUIZ_042):
            with self.subTest(chapter=d.name):
                res = offline(qc_lint.read_chapter_dir(d))
                for check in ("quiz_key_count", "quiz_too_few_options",
                              "quiz_empty_field", "quiz_missing_answer",
                              "card_empty"):
                    self.assertNotIn(check, res["counts"])


# ---------------------------------------------------------------------------
class TestCards(unittest.TestCase):

    def test_empty_front_or_back(self):
        res = offline(cards_asset({"front": "", "back": "תשובה"},
                                  {"front": "שאלה", "back": "  "}))
        self.assertEqual(checks(res, "C1"), ["card_empty"])
        self.assertEqual(checks(res, "C2"), ["card_empty"])


# ---------------------------------------------------------------------------
class TestKeyPosition(unittest.TestCase):
    """The measurement that says the learner's score is not his knowledge."""

    def test_released_quizzes_are_a_heavy(self):
        # Every single-answer key in both fixture chapters sits on A. Across the
        # 17 sampled chapters it was 98 of 132; here the sample is total.
        for d in (QUIZ_001, QUIZ_042):
            with self.subTest(chapter=d.name):
                stats = offline(qc_lint.read_chapter_dir(d))["stats"]
                n = stats["single_answer"]
                self.assertGreater(n, 0)
                self.assertEqual(stats["key_positions"], {"A": n})

    def test_skew_fires_only_with_enough_items(self):
        # 001 has 8 single-answer items, 042 has 5. Below the floor the
        # distribution is noise and flagging it would cry wolf on every chapter.
        big = offline(qc_lint.read_chapter_dir(QUIZ_001))
        small = offline(qc_lint.read_chapter_dir(QUIZ_042))
        self.assertGreaterEqual(big["stats"]["single_answer"],
                                qc_lint.KEY_POS_MIN_ITEMS)
        self.assertLess(small["stats"]["single_answer"], qc_lint.KEY_POS_MIN_ITEMS)
        self.assertIn("key_position_skew", checks(big))
        self.assertNotIn("key_position_skew", checks(small))
        self.assertIn("100%", details(big, "key_position_skew")[0])

    def test_a_balanced_quiz_is_not_flagged(self):
        items = [mc(stem=f"שאלה מספר {i} על נושא הפרק הזה", key=i % 4)
                 for i in range(12)]
        res = offline(quiz_asset(*items))
        self.assertEqual(res["stats"]["key_positions"],
                         {"A": 3, "B": 3, "C": 3, "D": 3})
        self.assertNotIn("key_position_skew", checks(res))

    def test_key_position_is_never_blocking(self):
        res = offline(qc_lint.read_chapter_dir(QUIZ_001))
        self.assertIn("key_position_skew", res["counts"])
        self.assertNotIn("key_position_skew", res["blocking"])

    def test_key_longest_by_more_than_thirty_percent(self):
        long_key = "א" * 40
        res = offline(quiz_asset(mc(options=(long_key, "ב" * 30, "ג" * 10))))
        self.assertIn("key_longest", checks(res, "Q1"))
        # 40 vs 30 is only 33% longer; 40 vs 31 is not enough.
        near = offline(quiz_asset(mc(options=(long_key, "ב" * 31, "ג" * 10))))
        self.assertNotIn("key_longest", checks(near, "Q1"))


# ---------------------------------------------------------------------------
class TestHygiene(unittest.TestCase):
    """Every pattern here is a string that reached the learner."""

    def test_chatbot_signoff_in_a_released_guide(self):
        res = offline({"study_guide.md": GUIDE_049.read_text(encoding="utf-8")})
        self.assertIn("hygiene_signoff", checks(res))
        self.assertEqual(details(res, "hygiene_signoff"), ["אשמח ל"])
        self.assertIn("hygiene_signoff", res["blocking"])

    def test_the_other_two_signoffs(self):
        for phrase in ("האם תרצה שנעבור כעת לפתור קוויז ממוקד",
                       "מדריך זה כולל את מלוא המידע הנדרש"):
            with self.subTest(phrase=phrase[:20]):
                res = offline({"study_guide.md": f"# פרק\n\n{phrase}\n"})
                self.assertIn("hygiene_signoff", checks(res))

    def test_escaped_latex(self):
        # All three forms, verbatim from released guides.
        for snippet in (r"NE נקשר לקולטני \\(\alpha_{2A}\\) פוסט-סינפטיים",
                        r"אנטגוניזם לקולטני $5\text{-HT}_{2C}$ (StahlNeuro p. 292)",
                        r"אזהרת QTc במינון מעל 40 מ\"ג"):
            with self.subTest(snippet=snippet[:24]):
                res = offline({"study_guide.md": f"# פרק\n\n{snippet}\n"})
                self.assertIn("hygiene_latex", checks(res))

    def test_plain_hebrew_markdown_is_not_latex(self):
        clean = ("# מדריך\n\n## 1. מבוא\n\n* **קו ראשון**: SSRI (Dulcan p. 330)\n"
                 "| נושא | עמ' |\n| :--- | :--- |\n| מבוא | 12 |\n")
        res = offline({"study_guide.md": clean})
        self.assertEqual([c for c in checks(res) if c.startswith("hygiene")], [])

    def test_json_escaping_in_facts_is_not_latex(self):
        # qc_units renders a facts leaf with json.dumps, which re-escapes every
        # `"` in the value — and `\"` is one of the escaped-LaTeX signatures.
        # The check reads the parsed string, so the quote does not fire.
        facts = json.dumps({"note": 'המינון הוא 40 מ"ג ליום'}, ensure_ascii=False)
        self.assertIn('\\"', json.dumps(json.loads(facts)["note"]))
        res = offline({"facts.json": facts})
        self.assertNotIn("hygiene_latex", res["counts"])

    def test_latex_inside_a_facts_value_still_fires(self):
        facts = json.dumps({"mechanism": r"אנטגוניזם ל-$5\text{-HT}_{2A}$"},
                           ensure_ascii=False)
        res = offline({"facts.json": facts})
        self.assertIn("hygiene_latex", checks(res, "F:mechanism"))

    def test_leaked_labels(self):
        for label in ("General Instruction Filter", "Source Summary"):
            with self.subTest(label=label):
                res = offline(quiz_asset(mc(stem=f"שאלה קלינית {label} כאן")))
                self.assertEqual(details(res, "hygiene_leaked_label"), [label])
        # ...and it is a real released defect, not only a constructed one.
        released = offline(qc_lint.read_chapter_dir(QUIZ_042))
        self.assertEqual(released["counts"].get("hygiene_leaked_label"), 1)

    def test_notebooklm_markers(self):
        res = offline({"facts.json": json.dumps(
            {"kinetics": "Amphetamine half-life is 10-12 hours [9]"},
            ensure_ascii=False)})
        self.assertIn("hygiene_marker", checks(res, "F:kinetics"))

    def test_a_markdown_link_is_not_a_marker(self):
        res = offline({"study_guide.md": "# פרק\n\n[מקור 12](https://example.org)\n"})
        self.assertNotIn("hygiene_marker", res["counts"])

    def test_arabic_letter_fused_into_a_hebrew_word(self):
        # Chapter 023, "ההבחנה המושגית": the mem is Arabic U+0645.
        guide = "# פרק\n\nההבחנה ה\u05de\u0645ושגית בין דיבור לשפה\n"
        res = offline({"study_guide.md": guide})
        self.assertIn("hygiene_arabic", checks(res))

    def test_english_only_stem(self):
        english = ("A 9-year-old boy presents with a 7-month history of "
                   "excessive worry regarding academic performance.")
        res = offline(quiz_asset(mc(stem=english)))
        self.assertIn("hygiene_english_stem", checks(res, "Q1"))
        # Never blocking: translating a vignette is not something code can do,
        # and holding a chapter on it would stall the backlog on style.
        self.assertNotIn("hygiene_english_stem", res["blocking"])

    def test_a_short_english_fragment_is_not_an_english_stem(self):
        res = offline(quiz_asset(mc(stem="MTA study?")))
        self.assertNotIn("hygiene_english_stem", res["counts"])


# ---------------------------------------------------------------------------
class TestCitations(unittest.TestCase):

    @needs_books
    def test_the_029_guide_impossible_pages(self):
        res = qc_lint.lint_chapter(
            "study-029", {"study_guide.md": GUIDE_029.read_text(encoding="utf-8")})
        got = details(res, "impossible_citation")
        self.assertEqual(len(got), 2, got)
        self.assertTrue(all("StahlNeuro" in d for d in got), got)
        self.assertTrue(any("902" in d for d in got), got)
        self.assertTrue(any("1030" in d for d in got), got)
        self.assertIn("impossible_citation", res["blocking"])

    @needs_books
    def test_a_real_page_is_not_impossible(self):
        res = qc_lint.lint_chapter("t", cards_asset(
            {"front": "אטומוקסטין", "back": "משך ההשפעה (StahlKids p. 69)"}))
        self.assertNotIn("impossible_citation", res["counts"])

    @needs_books
    def test_every_page_in_a_list_is_checked(self):
        # The defect the old gate had: it read only the first number, so the
        # second page of "(StahlNeuro p. 289, 902)" was never looked at.
        res = qc_lint.lint_chapter("t", cards_asset(
            {"front": "מסלול", "back": "(StahlNeuro p. 289, 902)"}))
        self.assertEqual(res["counts"].get("impossible_citation"), 1)

    def test_missing_books_are_recorded_not_swallowed(self):
        res = qc_lint.lint_chapter("t", {"study_guide.md": "# פרק\n\n(DSM p.135)\n"},
                                   use_books=False)
        self.assertNotIn("impossible_citation", res["counts"])
        self.assertEqual({s["check"] for s in res["skipped"]},
                         {"impossible_citation", "value_unsourced"})


# ---------------------------------------------------------------------------
class TestLockedValues(unittest.TestCase):
    """A priority for the judge, never a verdict."""

    CITED = "התועלת הטיפולית המלאה של Atomoxetine עשויה להימשך {} (StahlKids p. 69)."

    @needs_books
    def test_a_value_on_the_cited_page_is_not_flagged(self):
        # StahlKids p.69: "full therapeutic benefits may take 2-8 weeks".
        res = qc_lint.lint_chapter("t", cards_asset(
            {"front": "Atomoxetine onset", "back": self.CITED.format("2-8 weeks")}))
        self.assertNotIn("value_unsourced", res["counts"])

    @needs_books
    def test_a_value_that_is_on_no_cited_page_is_flagged(self):
        res = qc_lint.lint_chapter("t", cards_asset(
            {"front": "Atomoxetine onset", "back": self.CITED.format("5-9 weeks")}))
        self.assertEqual(res["counts"].get("value_unsourced"), 1)
        self.assertIn("5-9 weeks", details(res, "value_unsourced")[0])
        # It orders the judge's work; it never decides anything.
        self.assertNotIn("value_unsourced", res["blocking"])

    @needs_books
    def test_a_page_number_is_never_a_locked_value(self):
        # `(StahlNeuro p. 429)` in the 042 quiz is why the quota detector may
        # not look for "429" in text; it must not become a value either.
        res = qc_lint.lint_chapter("t", cards_asset(
            {"front": "Buspirone", "back": "Buspirone מנגנון (StahlNeuro p. 429)"}))
        self.assertNotIn("value_unsourced", res["counts"])

    @needs_books
    def test_an_uncited_unit_is_left_to_the_judge(self):
        res = qc_lint.lint_chapter("t", cards_asset(
            {"front": "Atomoxetine", "back": "משך ההשפעה 5-9 weeks"}))
        self.assertNotIn("value_unsourced", res["counts"])

    def test_key_terms_drop_the_renderer_labels(self):
        # "front", "back" and "weeks" are in every card's text because the
        # renderer puts them there, and they occur on half the pages of every
        # book — a window built from them would accept any page.
        terms = qc_lint.key_terms("front: Atomoxetine onset\nback: 2-8 weeks")
        self.assertIn("Atomoxetine", terms)
        for label in ("front", "back", "weeks"):
            self.assertNotIn(label, terms)


# ---------------------------------------------------------------------------
class TestLegendChips(unittest.TestCase):

    LEGEND_TEXT = (
        "ADHD treatment — טיפול\n"
        "השלט: \"CLONMEL HILL FARM\" — חוות גבעה.\n\n"
        "1. להקת זאבים [Wolf pack]: Clonidine ER — אגוניסט אלפא 2 "
        "(Dulcan p. 711; StahlNeuro p. 481-482) — הסבר.\n"
        "2. חותמת תאריך [Date stamp]: Atomoxetine חוסם NET "
        "(Dulcan p. 715) — הסבר.\n\n"
        "לא צויר: ~18h\n\n"
        "מספרים לבחינה: ~18h · 0.4% · {chips}\n")

    def test_a_chip_that_is_a_cited_page(self):
        text = self.LEGEND_TEXT.format(chips="711")
        res = offline({"sketchy_main.txt": text})
        self.assertIn("legend_chip_is_page", checks(res, "L:sketchy_main.txt:numbers"))
        self.assertIn("711", details(res, "legend_chip_is_page")[0])
        self.assertIn("legend_chip_is_page", res["blocking"])

    def test_a_number_that_is_not_a_cited_page(self):
        res = offline({"sketchy_main.txt": self.LEGEND_TEXT.format(chips="24")})
        self.assertNotIn("legend_chip_is_page", res["counts"])

    def test_a_chip_with_a_unit_is_never_a_page(self):
        # "711%" or "711h" cannot be a folio, whatever the legend cites.
        res = offline({"sketchy_main.txt": self.LEGEND_TEXT.format(chips="711h")})
        self.assertNotIn("legend_chip_is_page", res["counts"])

    def test_a_released_legend_is_clean(self):
        res = offline({"sketchy_main.txt": LEGEND.read_text(encoding="utf-8")})
        self.assertNotIn("legend_chip_is_page", res["counts"])
        # ...and it really did have chips and citations to compare.
        numbers = [u for u in qc_units.units_from_asset(
            "sketchy_main.txt", LEGEND.read_text(encoding="utf-8"))
            if u.meta.get("kind") == "legend_numbers"]
        self.assertEqual(len(numbers), 1)
        self.assertGreater(len(numbers[0].meta["chips"]), 5)
        self.assertGreater(res["stats"]["citations"], 20)


# ---------------------------------------------------------------------------
class TestCompleteness(unittest.TestCase):

    def test_a_missing_judged_asset_blocks(self):
        res = offline(cards_asset({"front": "א", "back": "ב"}))
        missing = set(details(res, "missing_asset"))
        self.assertEqual(missing, {"study_guide.md", "facts.json", "quiz.json"})
        self.assertIn("missing_asset", res["blocking"])

    def test_a_missing_video_is_a_backfill_item(self):
        res = offline(cards_asset({"front": "א", "back": "ב"}))
        self.assertTrue(any("video.mp4" in d for d in details(res, "backfill_asset")))
        self.assertNotIn("backfill_asset", res["blocking"])

    def test_a_complete_chapter_has_neither(self):
        assets = {"study_guide.md": "# פרק\n", "facts.json": "{}",
                  "quiz.json": '{"questions": []}', "flashcards.json": '{"cards": []}',
                  "audio.mp3": None, "video.mp4": None, "infographic_1.png": None,
                  "sketchy_main.png": None, "sketchy_main.txt": "לוח\n"}
        res = offline(assets)
        self.assertNotIn("missing_asset", res["counts"])
        self.assertNotIn("backfill_asset", res["counts"])

    def test_a_plate_without_its_legend(self):
        assets = {"study_guide.md": "# פרק\n", "facts.json": "{}",
                  "quiz.json": '{"questions": []}', "flashcards.json": '{"cards": []}',
                  "audio.mp3": None, "video.mp4": None, "infographic.png": None,
                  "sketchy_criteria.png": None}
        res = offline(assets)
        self.assertEqual(details(res, "backfill_asset"),
                         ["sketchy_criteria.txt (אגדה חסרה)"])


# ---------------------------------------------------------------------------
class TestDuplicatesAndParsing(unittest.TestCase):

    def test_two_items_with_the_same_stem(self):
        stem = "מהו מנגנון הפעולה של Atomoxetine בטיפול ב-ADHD?"
        res = offline(quiz_asset(mc(stem=stem), mc(stem=stem)))
        self.assertIn("duplicate_unit", checks(res, "Q1"))
        self.assertIn("duplicate_unit", checks(res, "Q2"))
        self.assertIn("Q2", details(res, "duplicate_unit")[0])

    def test_punctuation_only_differences_still_duplicate(self):
        res = offline(cards_asset(
            {"front": "מהו מנגנון הפעולה של Atomoxetine?", "back": "חסימת NET"},
            {"front": "מהו מנגנון הפעולה של Atomoxetine", "back": "חסימת NET"}))
        self.assertEqual(res["counts"]["duplicate_unit"], 2)

    def test_different_items_are_not_duplicates(self):
        res = offline(qc_lint.read_chapter_dir(QUIZ_042))
        self.assertNotIn("duplicate_unit", res["counts"])

    def test_unparsable_asset_is_reported_and_the_rest_still_linted(self):
        assets = {"quiz.json": "{not json",
                  "flashcards.json": json.dumps(
                      {"cards": [{"front": "", "back": "ב"}]})}
        res = offline(assets)
        self.assertIn("asset_unparsable", res["counts"])
        self.assertIn("asset_unparsable", res["blocking"])
        self.assertIn("card_empty", res["counts"])      # the other asset survived

    def test_a_quiz_with_no_question_list(self):
        res = offline({"quiz.json": json.dumps({"title": "פרק"})})
        self.assertIn("asset_unparsable", res["counts"])

    def test_chapter_units_reports_the_break_and_keeps_the_rest(self):
        units, broken = qc_lint.chapter_units(
            {"quiz.json": "{not json", "flashcards.json": '{"cards": [{"front": "א"}]}',
             "audio.mp3": None})
        self.assertEqual([u.uid for u in units], ["C1"])
        self.assertEqual(len(broken), 1)
        self.assertIn("quiz.json", broken[0])


# ---------------------------------------------------------------------------
class TestGlobal(unittest.TestCase):
    """Cross-chapter near-duplicates: token Jaccard >= 0.5 on stem/front."""

    A = ("מדוע ל-Atomoxetine אין פוטנציאל Abuse בהשוואה לסטימולנטים?",
         "אילו מדדים גופניים יש לנטר באופן קבוע בילד המטופל ב-Methylphenidate?")
    B = ("מדוע ל-Atomoxetine אין פוטנציאל ל-Abuse בהשוואה ל-Stimulants?",
         "מהי הרמה הטיפולית של ליתיום בדם בטיפול אחזקתי במתבגרים?")

    def units(self, fronts):
        return qc_units.units_from_asset("flashcards.json", json.dumps(
            {"cards": [{"front": f, "back": "תשובה"} for f in fronts]},
            ensure_ascii=False))

    def test_a_near_duplicate_across_chapters(self):
        res = qc_lint.lint_global({"study-018-x": self.units(self.A),
                                   "study-019-y": self.units(self.B)})
        self.assertEqual(res["counts"]["cross_duplicate"], 1)
        pair = res["pairs"][0]
        self.assertEqual({pair["a_tag"], pair["b_tag"]}, {"study-018-x", "study-019-y"})
        self.assertGreaterEqual(pair["jaccard"], qc_lint.JACCARD_MIN)

    def test_duplicates_inside_one_chapter_are_not_reported_here(self):
        res = qc_lint.lint_global({"study-018-x": self.units(self.A + self.A)})
        self.assertEqual(res["counts"]["cross_duplicate"], 0)

    def test_short_fronts_are_not_compared(self):
        # Two 3-token fronts sharing 2 tokens score 0.5 while saying different
        # things, which is why DUP_MIN_TOKENS exists.
        res = qc_lint.lint_global({"a": self.units(("מהו המינון ההתחלתי?",)),
                                   "b": self.units(("מהו המינון המרבי?",))})
        self.assertEqual(res["compared"], 0)
        self.assertEqual(res["counts"]["cross_duplicate"], 0)

    def test_released_chapters_pair_only_with_each_other(self):
        chapters = {}
        for d in (QUIZ_001, QUIZ_042):
            assets = qc_lint.read_chapter_dir(d)
            units = []
            for name, text in assets.items():
                if isinstance(text, str):
                    units.extend(qc_units.units_from_asset(name, text))
            chapters[d.name] = units
        res = qc_lint.lint_global(chapters)
        self.assertGreater(res["compared"], 20)
        for p in res["pairs"]:
            self.assertNotEqual(p["a_tag"], p["b_tag"])


# ---------------------------------------------------------------------------
class TestResultShape(unittest.TestCase):
    """The contract qc_run and _qc/REPORT.md read."""

    def setUp(self):
        self.res = offline(qc_lint.read_chapter_dir(QUIZ_001), tag=QUIZ_001.name)

    def test_counts_equal_the_flags(self):
        total = (sum(len(v) for v in self.res["flags"].values())
                 + len(self.res["chapter"]))
        self.assertEqual(sum(self.res["counts"].values()), total)

    def test_every_check_name_is_registered(self):
        for check in self.res["counts"]:
            self.assertIn(check, qc_lint.CHECKS)
            self.assertIn("he", qc_lint.CHECKS[check])

    def test_blocking_is_exactly_the_blocking_checks_that_fired(self):
        expected = sorted(c for c in self.res["counts"]
                          if qc_lint.CHECKS[c]["blocking"])
        self.assertEqual(self.res["blocking"], expected)

    def test_flags_are_keyed_by_unit_id(self):
        uids = {u.uid for u in qc_units.units(
            {n: t for n, t in qc_lint.read_chapter_dir(QUIZ_001).items()
             if isinstance(t, str)})}
        self.assertTrue(set(self.res["flags"]) <= uids)
        self.assertEqual(self.res["units"], len(uids))

    def test_the_result_is_json(self):
        json.dumps(self.res, ensure_ascii=False)

    def test_tag_and_assets_are_carried(self):
        self.assertEqual(self.res["tag"], QUIZ_001.name)
        self.assertEqual(self.res["assets"], ["flashcards.json", "quiz.json"])


if __name__ == "__main__":
    unittest.main()
