#!/usr/bin/env python3
"""Offline tests for qc_units: nothing is lost, and every field is shown.

The module's whole job is that a chapter goes in and every claim in it comes
out addressable. So the tests count: the units of a real released quiz must
equal its questions, the guide's sections must reassemble into the guide byte
for byte, and each quiz type must render the fields that type's errors live in
(042's key error is inside one option's rationale; 019's atomoxetine conflict
is in a hint).

Fixtures are our own published material, trimmed. No books, no network.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import qc_units  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
QUIZ_CHAPTERS = sorted((FIX / "quiz").iterdir()) if (FIX / "quiz").is_dir() else []
TEXT_CHAPTERS = sorted((FIX / "text").iterdir()) if (FIX / "text").is_dir() else []
# 042 is the chapter that carries all four declared types; 001 predates `type`.
TYPED = FIX / "quiz" / "study-042-benzodiazepines-buspirone-sleep-aids-overview"
UNTYPED = FIX / "quiz" / "study-001-psychiatric-examination-of-child-and-adolescent"
GUIDE_029 = FIX / "text" / "study-029-ssris-deep-dive"
LEGEND = FIX / "legends" / "sketchy_main.txt"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def chapter_assets(d: Path) -> dict[str, str]:
    return {p.name: read(p) for p in sorted(d.iterdir())
            if p.is_file() and qc_units.is_judged_asset(p.name)}


def leaf_paths(node, path=""):
    """An independent re-implementation of the facts walk, so the test does not
    agree with the module by construction."""
    if isinstance(node, dict) and node:
        return [p for k, v in node.items()
                for p in leaf_paths(v, f"{path}.{k}" if path else str(k))]
    if isinstance(node, list) and any(isinstance(v, (dict, list)) for v in node):
        return [p for i, v in enumerate(node) for p in leaf_paths(v, f"{path}[{i}]")]
    return [path or "_root"]


class TestNothingLost(unittest.TestCase):
    """Counts, on every fixture chapter."""

    def test_one_unit_per_question(self):
        self.assertTrue(QUIZ_CHAPTERS, "no quiz fixtures")
        for d in QUIZ_CHAPTERS:
            with self.subTest(chapter=d.name):
                items = json.loads(read(d / "quiz.json"))["questions"]
                us = qc_units.units_from_asset("quiz.json", read(d / "quiz.json"))
                self.assertEqual(len(us), len(items))
                self.assertEqual([u.uid for u in us],
                                 [f"Q{i + 1}" for i in range(len(items))])

    def test_one_unit_per_card(self):
        for d in QUIZ_CHAPTERS:
            with self.subTest(chapter=d.name):
                cards = json.loads(read(d / "flashcards.json"))["cards"]
                us = qc_units.units_from_asset("flashcards.json",
                                               read(d / "flashcards.json"))
                self.assertEqual(len(us), len(cards))
                for u, card in zip(us, cards):
                    self.assertIn(card["front"], u.text)
                    self.assertIn(card["back"], u.text)

    def test_guide_sections_partition_the_file(self):
        for d in TEXT_CHAPTERS:
            guide = d / "study_guide.md"
            if not guide.exists():
                continue
            with self.subTest(chapter=d.name):
                text = read(guide)
                us = qc_units.units_from_asset("study_guide.md", text)
                # The judge quotes a span out of a section and qc_fix writes it
                # back into the file; that only works if the sections are the
                # file, with nothing dropped between them.
                self.assertEqual("".join(u.text for u in us), text)
                self.assertEqual(us[0].uid, "G:_intro")
                self.assertEqual(len([u for u in us if u.meta["heading"]]),
                                 sum(1 for ln in text.splitlines()
                                     if ln.startswith("## ")))

    def test_one_unit_per_facts_leaf(self):
        for d in TEXT_CHAPTERS:
            facts = d / "facts.json"
            if not facts.exists():
                continue
            with self.subTest(chapter=d.name):
                data = json.loads(read(facts))
                us = qc_units.units_from_asset("facts.json", read(facts))
                self.assertEqual([u.uid for u in us],
                                 [f"F:{p}" for p in leaf_paths(data)])
                for u in us:
                    self.assertEqual(json.loads(u.text), u.meta["value"])

    def test_chapter_units_have_unique_ids(self):
        for d in QUIZ_CHAPTERS + TEXT_CHAPTERS:
            with self.subTest(chapter=d.name):
                us = qc_units.units(chapter_assets(d))
                self.assertEqual(len(us), len({u.uid for u in us}))


class TestQuizRendering(unittest.TestCase):
    """Every type renders its own fields — the fields its errors live in."""

    @classmethod
    def setUpClass(cls):
        cls.items = json.loads(read(TYPED / "quiz.json"))["questions"]
        cls.units = qc_units.units_from_asset("quiz.json", read(TYPED / "quiz.json"))
        cls.by_type = {}
        for item, unit in zip(cls.items, cls.units):
            cls.by_type.setdefault(item.get("type") or "untyped", (item, unit))

    def test_fixture_covers_every_declared_type(self):
        self.assertLessEqual({"multiple_choice", "multiple_select",
                              "fill_in_the_blank", "short_answer"},
                             set(self.by_type))

    def test_stem_type_and_hint_always_shown(self):
        for t, (item, unit) in self.by_type.items():
            with self.subTest(type=t):
                self.assertIn(f"type: {t}", unit.text)
                self.assertIn(item["question"], unit.text)
                self.assertIn(item["hint"], unit.text)

    def test_every_option_and_rationale_with_key_marks(self):
        for t in ("multiple_choice", "multiple_select"):
            item, unit = self.by_type[t]
            with self.subTest(type=t):
                for opt in item["answerOptions"]:
                    self.assertIn(opt["text"], unit.text)
                    self.assertIn(opt["rationale"], unit.text)
                keys = [o for o in item["answerOptions"] if o["isCorrect"]]
                self.assertEqual(unit.text.count("[KEY]"), len(keys))
                for opt in keys:
                    self.assertIn(f"[KEY] {opt['text']}", unit.text)
                self.assertEqual(unit.meta["key_indexes"],
                                 [i for i, o in enumerate(item["answerOptions"])
                                  if o["isCorrect"]])

    def test_multiple_select_shows_more_than_one_key(self):
        # The app graded multiple_select with one radio button; the judge must
        # at least see that the item has several keys.
        _, unit = self.by_type["multiple_select"]
        self.assertGreater(unit.text.count("[KEY]"), 1)
        self.assertIn("hasNoneOfTheAbove:", unit.text)

    def test_fill_in_the_blank_fields(self):
        item, unit = self.by_type["fill_in_the_blank"]
        self.assertIn(f"bestAnswer: {item['bestAnswer']}", unit.text)
        for ans in item["acceptableAnswers"]:
            self.assertIn(f"acceptableAnswer: {ans}", unit.text)
        self.assertIn(item["rationale"], unit.text)

    def test_short_answer_fields(self):
        item, unit = self.by_type["short_answer"]
        g = item["grading"]
        self.assertIn(f"modelAnswer: {g['modelAnswer']}", unit.text)
        for attr in g["requiredAttributes"]:
            self.assertIn(f"requiredAttribute: {attr}", unit.text)
        for miss in g["errors"]["misconceptions"]:
            self.assertIn(f"misconception: {miss}", unit.text)
        for bluff in g["errors"]["bluffs"]:
            self.assertIn(f"bluff: {bluff}", unit.text)

    def test_untyped_item_still_renders_its_options(self):
        items = json.loads(read(UNTYPED / "quiz.json"))["questions"]
        us = qc_units.units_from_asset("quiz.json", read(UNTYPED / "quiz.json"))
        self.assertNotIn("type", items[0])
        self.assertIn("type: untyped", us[0].text)
        self.assertEqual(us[0].meta["type"], "untyped")
        for opt in items[0]["answerOptions"]:
            self.assertIn(opt["text"], us[0].text)

    def test_unknown_field_is_rendered_not_dropped(self):
        """The quiz schema has changed under this pipeline before."""
        asset = json.dumps({"questions": [{"type": "multiple_choice",
                                           "question": "q?", "hint": "h",
                                           "answerOptions": [],
                                           "newFangledField": ["x", 7]}]})
        text = qc_units.units_from_asset("quiz.json", asset)[0].text
        self.assertIn("newFangledField", text)
        self.assertIn("7", text)

    def test_empty_image_urls_do_not_pad_every_unit(self):
        asset = json.dumps({"questions": [{"question": "q?", "imageUrls": []}]})
        self.assertNotIn("imageUrls",
                         qc_units.units_from_asset("quiz.json", asset)[0].text)


class TestGuideSplit(unittest.TestCase):
    def test_h3_stays_inside_its_section(self):
        us = qc_units.units_from_asset("study_guide.md", read(GUIDE_029 / "study_guide.md"))
        indications = next(u for u in us if u.uid.startswith("G:4."))
        self.assertIn("### אינדיקציות מאושרות", indications.text)
        self.assertFalse(any(u.uid.startswith("G:###") for u in us))

    def test_duplicate_heading_gets_a_suffix(self):
        md = "intro\n\n## סיכום מבני\nא\n\n## אחר\nב\n\n## סיכום מבני\nג\n"
        us = qc_units.units_from_asset("study_guide.md", md)
        self.assertEqual([u.uid for u in us],
                         ["G:_intro", "G:סיכום מבני", "G:אחר", "G:סיכום מבני #2"])
        self.assertEqual("".join(u.text for u in us), md)

    def test_guide_with_no_headings_is_one_intro(self):
        us = qc_units.units_from_asset("study_guide.md", "just prose\n")
        self.assertEqual([u.uid for u in us], ["G:_intro"])

    def test_guide_opening_on_a_heading_has_no_empty_intro(self):
        us = qc_units.units_from_asset("study_guide.md", "## א\nגוף\n")
        self.assertEqual([u.uid for u in us], ["G:א"])


class TestFacts(unittest.TestCase):
    def test_nested_cluster_path(self):
        d = FIX / "text" / "study-016-adhd-core"
        us = qc_units.units_from_asset("facts.json", read(d / "facts.json"))
        uids = {u.uid for u in us}
        self.assertIn("F:criteria.clusters[0].items", uids)
        self.assertIn("F:criteria.thresholds", uids)
        items = next(u for u in us if u.uid == "F:criteria.clusters[0].items")
        # One unit per addressable entry: nine two-word symptoms are one claim.
        self.assertEqual(len(json.loads(items.text)), 9)

    def test_empty_containers_are_not_dropped(self):
        us = qc_units.units_from_asset("facts.json", '{"a": [], "b": {}}')
        self.assertEqual([u.uid for u in us], ["F:a", "F:b"])


class TestLegend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(LEGEND)
        cls.units = qc_units.units_from_asset("sketchy_main.txt", cls.text)

    def test_one_unit_per_numbered_row_plus_head_and_numbers(self):
        rows = [u for u in self.units if u.meta["kind"] == "legend"
                and u.meta["row"] != "0"]
        self.assertEqual(len(rows), 48)
        self.assertEqual(rows[0].uid, "L:sketchy_main.txt:1")
        self.assertEqual(self.units[0].uid, "L:sketchy_main.txt:0")
        self.assertEqual(self.units[-1].uid, "L:sketchy_main.txt:numbers")

    def test_rows_carry_their_whole_claim(self):
        row13 = next(u for u in self.units if u.uid.endswith(":13"))
        self.assertIn("-0.56", row13.text)
        self.assertIn("Dulcan p. 185", row13.text)

    def test_exam_number_chips_are_parsed(self):
        chips = self.units[-1].meta["chips"]
        self.assertIn("2-8wk", chips)
        self.assertIn("0.4%", chips)
        # qc_lint compares these against the pages cited in the same legend,
        # so the drawn/not-drawn note must stay in the unit's text.
        self.assertIn("לא צויר", self.units[-1].text)

    def test_prose_legend_becomes_paragraph_rows(self):
        us = qc_units.units_from_asset("sketchy_old.txt", "פסקה ראשונה\n\nפסקה שניה\n")
        self.assertEqual([u.uid for u in us],
                         ["L:sketchy_old.txt:1", "L:sketchy_old.txt:2"])

    def test_quiz_md_is_not_a_legend(self):
        self.assertFalse(qc_units.is_judged_asset("sketchy_main.quiz.md"))
        self.assertTrue(qc_units.is_judged_asset("sketchy_main.txt"))
        self.assertEqual(qc_units.units_from_asset("sketchy_main.quiz.md", "x"), [])


class TestStableIds(unittest.TestCase):
    def test_quiz_id_is_sha1_of_the_original_stem(self):
        items = json.loads(read(TYPED / "quiz.json"))["questions"]
        us = qc_units.units_from_asset("quiz.json", read(TYPED / "quiz.json"))
        self.assertEqual(us[0].stable_id,
                         hashlib.sha1(items[0]["question"].encode("utf-8")).hexdigest())

    def test_card_id_is_sha1_of_the_front(self):
        cards = json.loads(read(TYPED / "flashcards.json"))["cards"]
        us = qc_units.units_from_asset("flashcards.json",
                                       read(TYPED / "flashcards.json"))
        self.assertEqual(us[0].stable_id,
                         hashlib.sha1(cards[0]["front"].encode("utf-8")).hexdigest())

    def test_stemless_items_do_not_all_share_one_id(self):
        asset = json.dumps({"questions": [{"question": "", "hint": "a"},
                                          {"question": "", "hint": "b"}]})
        us = qc_units.units_from_asset("quiz.json", asset)
        self.assertNotEqual(us[0].stable_id, us[1].stable_id)

    def test_duplicate_stems_share_an_id_on_purpose(self):
        # They are the duplicate qc_lint reports, and one fix belongs to both.
        asset = json.dumps({"questions": [{"question": "same?"},
                                          {"question": "same?"}]})
        us = qc_units.units_from_asset("quiz.json", asset)
        self.assertEqual(us[0].stable_id, us[1].stable_id)

    def test_id_survives_reordering_but_uid_does_not(self):
        """fixes.json references ids, never indexes (spec §5.9): a presentation
        pass that reorders the file must not orphan a fix."""
        data = json.loads(read(TYPED / "quiz.json"))
        before = qc_units.units_from_asset("quiz.json", json.dumps(data))
        data["questions"].reverse()
        after = qc_units.units_from_asset("quiz.json", json.dumps(data))
        self.assertEqual([u.stable_id for u in before],
                         [u.stable_id for u in reversed(after)])
        # Q1 is a position, so after the reorder it names a different item —
        # which is exactly why a fix may not be keyed on it.
        self.assertEqual(before[0].uid, after[0].uid)
        self.assertNotEqual(before[0].stable_id, after[0].stable_id)


class TestAssetHandling(unittest.TestCase):
    def test_fixed_artifact_order(self):
        assets = {"sketchy_b.txt": "1. x", "quiz.json": '{"questions": []}',
                  "sketchy_a.txt": "1. y", "flashcards.json": '{"cards": []}',
                  "facts.json": '{"k": "v"}', "study_guide.md": "## a\nb\n",
                  "video.mp4": "ignored"}
        arts = [u.artifact for u in qc_units.units(assets)]
        self.assertEqual(arts, ["study_guide.md", "facts.json",
                                "sketchy_a.txt", "sketchy_b.txt"])

    def test_broken_json_raises_instead_of_looking_empty(self):
        # Fail closed: an asset that did not parse must never be mistaken for a
        # chapter with nothing to judge.
        for name, body in (("quiz.json", "{not json"),
                           ("flashcards.json", '{"cards": [3]}'),
                           ("facts.json", "[[")):
            with self.subTest(asset=name):
                with self.assertRaises(qc_units.UnitError):
                    qc_units.units_from_asset(name, body)

    def test_quiz_without_a_question_list_raises(self):
        with self.assertRaises(qc_units.UnitError):
            qc_units.units_from_asset("quiz.json", '{"title": "t"}')


if __name__ == "__main__":
    unittest.main()
