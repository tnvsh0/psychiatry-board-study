#!/usr/bin/env python3
"""Offline tests for qc_fix: the note is shown, and the material is not touched.

Three properties carry the weight, because each one is a way the owner loses
something he cannot get back:

  1. NOTHING IS REWRITTEN. Owner decision 2 (2026-09-18) is "note first, rewrite
     later", so Tier 1's test is not "the note is there" but "every text field
     of the item is byte-identical afterwards", checked by stripping the `qc`
     keys back out and comparing the parsed structures.
  2. RUNNING TWICE CHANGES NOTHING. A backlog run of ~68 chapters is resumed
     after every quota stop, and an id pass or a shuffle that moves on the
     second run would republish every quiz he has already studied, with the
     answers in new places. assign_ids and rebalance_keys are applied twice to
     every real fixture and the bytes must match.
  3. TIER 2 CANNOT FIRE. The rewrite gate is a measurement nobody has made yet
     (§6: verified false positives <= 2% on >= 200 negatives). The tests drive
     `apply_tier2` with the flag off AND on, with every §5.9 gate passing, and
     assert that no asset comes back changed either way.

Fixtures are our own published material. `fixtures/quiz/` and `fixtures/text/`
are shared with the other QC modules' tests; `fixtures/balance/` holds three
more released quizzes (018, 030, 038 — 9, 10 and 10 single-answer items) copied
for the key-position test, which needs a chapter long enough to have a 10-item
window at all. No textbook text, no books, no network, no agy, no gh.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "app"))

import qc_fix        # noqa: E402
import qc_units      # noqa: E402
import quiz_logic    # noqa: E402  the app's reader; the two identities must agree

FIX = Path(__file__).resolve().parent / "fixtures"
QUIZ_DIRS = sorted(p for p in (FIX / "quiz").iterdir() if p.is_dir())
BALANCE = sorted((FIX / "balance").glob("quiz_*.json")) \
    if (FIX / "balance").is_dir() else []
GUIDE_029 = FIX / "text" / "study-029-ssris-deep-dive" / "study_guide.md"
FACTS_029 = FIX / "text" / "study-029-ssris-deep-dive" / "facts.json"
SIGNOFF_GUIDE = FIX / "hygiene" / "guide_049_signoff.md"
LEGEND = FIX / "legends" / "sketchy_main.txt"
Q019 = FIX / "quiz" / "study-019-adhd-treatment-non-stimulants"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def chapter(d: Path) -> dict[str, str]:
    return {p.name: read(p) for p in sorted(d.iterdir())
            if p.is_file() and qc_units.is_judged_asset(p.name)}


def items_of(text: str, name: str = "quiz.json") -> list[dict]:
    data = json.loads(text)
    if isinstance(data, list):
        return data
    for k in ("questions", "items", "cards", "flashcards", "quiz"):
        if isinstance(data.get(k), list):
            return data[k]
    raise AssertionError(f"{name}: no item list")


def options_of(item: dict) -> list[dict]:
    return item.get("answerOptions") or item.get("options") or []


def key_positions(text: str) -> list[int]:
    """The index of the key in every single-answer item, in file order."""
    out = []
    for item in items_of(text):
        opts = options_of(item)
        keys = [i for i, o in enumerate(opts)
                if isinstance(o, dict) and o.get("isCorrect")]
        if len(opts) >= 2 and len(keys) == 1:
            out.append(keys[0])
    return out


def strip_qc(node):
    """The same structure with every embedded `qc` record removed — what the
    asset must still be, byte for byte, after Tier 1."""
    if isinstance(node, dict):
        return {k: strip_qc(v) for k, v in node.items() if k != "qc"}
    if isinstance(node, list):
        return [strip_qc(v) for v in node]
    return node


def finding(**kw) -> dict:
    """A verified `contradicted` finding in the shape qc_judge hands over."""
    base = {
        "id": "f001", "unit": "Q1", "kind": "contradicted", "severity": "high",
        "category": "locked_value",
        "said": "השפעה מלאה עשויה להימשך 2-8 שבועות",
        "correction": "8-12 שבועות",
        "book": "StahlKids", "printed": 74,
        "source_quote": "full therapeutic benefits may take 8-12 weeks",
        "problem": "onset", "evidence_code": "AB23CD",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
class TestAssignIds(unittest.TestCase):

    def test_every_item_and_card_gets_one(self):
        for d in QUIZ_DIRS:
            with self.subTest(d.name):
                out = qc_fix.assign_ids(chapter(d))
                for name in ("quiz.json", "flashcards.json"):
                    for item in items_of(out[name], name):
                        got = item.get("id")
                        self.assertIsInstance(got, str)
                        self.assertRegex(got, r"^[0-9a-f]{16}$")

    def test_ids_are_unique_within_a_chapter(self):
        for d in QUIZ_DIRS:
            out = qc_fix.assign_ids(chapter(d))
            for name in ("quiz.json", "flashcards.json"):
                ids = [i["id"] for i in items_of(out[name], name)]
                with self.subTest(f"{d.name}/{name}"):
                    self.assertEqual(len(ids), len(set(ids)))

    def test_idempotent(self):
        """Applied twice, the bytes are identical — the resume property."""
        for d in QUIZ_DIRS:
            once = qc_fix.assign_ids(chapter(d))
            twice = qc_fix.assign_ids(once)
            with self.subTest(d.name):
                self.assertEqual(once, twice)

    def test_the_app_computes_the_same_id(self):
        """app/quiz_logic.item_id falls back to sha1(stem)[:16] for an item
        with no id. If this module stamped anything else, every erratum would
        name an item the quiz page cannot find."""
        for d in QUIZ_DIRS:
            src = chapter(d)
            out = qc_fix.assign_ids(src)
            before = items_of(src["quiz.json"])
            after = items_of(out["quiz.json"])
            for raw, stamped in zip(before, after):
                if raw.get("question") or raw.get("stem"):
                    with self.subTest(d.name):
                        self.assertEqual(stamped["id"], quiz_logic.item_id(raw))

    def test_an_existing_id_is_never_recomputed(self):
        text = json.dumps({"questions": [{"question": "q", "id": "deadbeefdeadbeef",
                                          "answerOptions": []}]},
                          ensure_ascii=False, indent=2)
        out = qc_fix.assign_ids({"quiz.json": text})
        self.assertEqual(items_of(out["quiz.json"])[0]["id"], "deadbeefdeadbeef")

    def test_stemless_items_do_not_collide(self):
        text = json.dumps({"questions": [{"question": "", "answerOptions": [{"text": "a"}]},
                                         {"question": "", "answerOptions": [{"text": "b"}]}]},
                          ensure_ascii=False, indent=2)
        ids = [i["id"] for i in items_of(qc_fix.assign_ids({"quiz.json": text})["quiz.json"])]
        self.assertEqual(len(set(ids)), 2)

    def test_only_the_id_lines_change(self):
        """The diff a reviewer reads has to be one line per item — the `id`
        itself, plus the comma JSON adds to the line above it."""
        src = chapter(Q019)
        out = qc_fix.assign_ids(src)
        before = set(src["quiz.json"].splitlines())
        added = [ln for ln in out["quiz.json"].splitlines() if ln not in before]
        self.assertTrue(added)
        for ln in added:
            with self.subTest(ln[:40]):
                self.assertTrue('"id"' in ln or ln.rstrip(",") in before, ln)


# ---------------------------------------------------------------------------
class TestHygiene(unittest.TestCase):

    def test_trailing_signoff_is_removed(self):
        md = read(SIGNOFF_GUIDE)
        out, changes = qc_fix.hygiene({"study_guide.md": md})
        new = out["study_guide.md"]
        self.assertNotIn("אשמח ל", new)
        self.assertIn("hygiene_signoff", [c["check"] for c in changes])
        # The material above it survives untouched, including the last real
        # section — a cleanup that ate content would be worse than the sign-off.
        self.assertIn("## סיכום מבני", new)
        self.assertIn("TF-CBT (PRACTICE Model)", new)
        self.assertTrue(md.startswith(new[:200]))

    def test_a_signoff_phrase_in_the_middle_is_content(self):
        """"האם תרצה" is how chapter 001 quotes a clinician opening an
        interview. Only a TRAILING block is a sign-off."""
        md = "# כותרת\n\nהמראיין שואל: האם תרצה לספר לי על בית הספר?\n\n## סיכום\n\nשורה.\n"
        out, changes = qc_fix.hygiene({"study_guide.md": md})
        self.assertEqual(out, {})
        self.assertEqual(changes, [])

    def test_escaped_latex_collapses_in_a_released_quiz(self):
        src = chapter(Q019)
        self.assertIn(r"$\\alpha_{2}$", src["quiz.json"])   # as the file stores it
        out, changes = qc_fix.hygiene(src)
        self.assertNotIn(r"\\alpha", out["quiz.json"])
        self.assertIn("α2", out["quiz.json"])
        self.assertTrue(any(c["check"] == "hygiene_latex" and c["fixed"]
                            for c in changes))

    def test_known_latex_shapes(self):
        for before, after in ((r"$5\text{-HT}_{2C}$", "5-HT2C"),
                              (r"\(\alpha_{2A}\)", "α2A"),
                              (r"\\(\beta_{1}\\)", "β1"),
                              (r"$GABA_{A}$", "GABAA")):
            got, events = qc_fix.collapse_latex(f"קולטן {before} בקליפה")
            with self.subTest(before):
                self.assertEqual(got, f"קולטן {after} בקליפה")
                self.assertTrue(events[0][2])

    def test_an_unknown_latex_command_is_left_alone_and_flagged(self):
        """Dropping a command nobody taught this module about would silently
        rename a receptor. It stays, qc_lint keeps flagging it, the owner reads
        it."""
        got, events = qc_fix.collapse_latex(r"ריכוז $\unknowncmd{x}_{2}$ בדם")
        self.assertIn(r"\unknowncmd", got)
        self.assertFalse(events[0][2])

    def test_json_escape_that_leaked_into_prose(self):
        got, _ = qc_fix.collapse_latex('מינון של 40 מ\\"ג ליום')
        self.assertEqual(got, 'מינון של 40 מ"ג ליום')

    def test_leaked_label_line_is_dropped(self):
        md = "# כותרת\n\nSource Summary\n\nתוכן אמיתי.\n"
        out, changes = qc_fix.hygiene({"study_guide.md": md})
        self.assertNotIn("Source Summary", out["study_guide.md"])
        self.assertIn("תוכן אמיתי.", out["study_guide.md"])
        self.assertIn("hygiene_leaked_label", [c["check"] for c in changes])

    def test_notebooklm_markers_are_stripped_from_facts(self):
        facts = json.dumps({"onset": "2-8 שבועות [3]", "note": "ראה [12] גם"},
                           ensure_ascii=False, indent=2)
        out, changes = qc_fix.hygiene({"facts.json": facts})
        data = json.loads(out["facts.json"])
        self.assertEqual(data["onset"], "2-8 שבועות")
        self.assertEqual(data["note"], "ראה גם")
        self.assertIn("hygiene_marker", [c["check"] for c in changes])

    def test_a_markdown_link_is_not_a_marker(self):
        md = "# כותרת\n\nראה [3](https://example.com) כאן.\n"
        out, _ = qc_fix.hygiene({"study_guide.md": md})
        self.assertEqual(out, {})

    def test_arabic_is_reported_but_never_guessed(self):
        """`המمושגית` is an Arabic letter fused into a Hebrew word. Every repair
        is a guess at which Hebrew letter was meant, so it is flagged unfixed and
        qc_lint keeps the chapter blocking."""
        md = "# כותרת\n\nהגישה המ\u0645ושגית מתארת.\n"
        out, changes = qc_fix.hygiene({"study_guide.md": md})
        self.assertEqual(out, {})
        arabic = [c for c in changes if c["check"] == "hygiene_arabic"]
        self.assertEqual(len(arabic), 1)
        self.assertFalse(arabic[0]["fixed"])

    def test_a_clean_asset_comes_back_untouched(self):
        """Including the JSON: a re-dump under a guessed style would rewrite
        every byte of an asset this pass had no business touching."""
        out, changes = qc_fix.hygiene({"facts.json": read(FACTS_029)})
        self.assertEqual(out, {})
        self.assertEqual(changes, [])

    def test_the_released_029_guide_really_is_dirty(self):
        """8 of the 14 sampled guides carry escaped LaTeX. This is one of them,
        so the cleaner is measured against released bytes and not only against
        strings written for the test."""
        out, changes = qc_fix.hygiene({"study_guide.md": read(GUIDE_029)})
        latex = [c for c in changes if c["check"] == "hygiene_latex"]
        self.assertGreaterEqual(len(latex), 10)
        self.assertTrue(all(c["fixed"] for c in latex), latex)
        self.assertNotIn(chr(92) + "text{", out["study_guide.md"])
        self.assertIn("5-HT2C", out["study_guide.md"])

    def test_hygiene_is_idempotent(self):
        for d in QUIZ_DIRS:
            src = chapter(d)
            once, _ = qc_fix.hygiene(src)
            merged = {**src, **once}
            twice, changes = qc_fix.hygiene(merged)
            with self.subTest(d.name):
                self.assertEqual({**merged, **twice}, merged)
                self.assertFalse([c for c in changes if c["fixed"]])


# ---------------------------------------------------------------------------
class TestRebalance(unittest.TestCase):

    def all_quizzes(self):
        for d in QUIZ_DIRS:
            yield d.name, read(d / "quiz.json")
        for p in BALANCE:
            yield p.stem, read(p)

    def test_idempotent(self):
        for tag, text in self.all_quizzes():
            once = qc_fix.rebalance_keys(text, tag)
            twice = qc_fix.rebalance_keys(once, tag)
            with self.subTest(tag):
                self.assertEqual(once, twice)

    def test_balance_on_real_quizzes(self):
        """§5.9: no position holds the key more than 3 times in 10 items."""
        self.assertTrue(BALANCE, "no balance fixtures")
        for tag, text in self.all_quizzes():
            pos = key_positions(qc_fix.rebalance_keys(text, tag))
            with self.subTest(tag, n=len(pos)):
                self.assertTrue(pos)
                for i in range(max(1, len(pos) - qc_fix.BALANCE_WINDOW + 1)):
                    window = pos[i:i + qc_fix.BALANCE_WINDOW]
                    worst = Counter(window).most_common(1)[0]
                    self.assertLessEqual(worst[1], qc_fix.MAX_KEYS_PER_POSITION,
                                         f"{tag}: position {worst[0]} holds the "
                                         f"key {worst[1]} times in {window}")

    def test_the_released_bias_is_gone(self):
        """Measured before: A 98 / B 31 / C 3 / D 0 over 132 items, so always-A
        scores 74%. After the rebalance no single position may carry more than
        40% of the keys of the whole sample."""
        pos = [p for tag, text in self.all_quizzes()
               for p in key_positions(qc_fix.rebalance_keys(text, tag))]
        self.assertGreaterEqual(len(pos), 30)
        worst = Counter(pos).most_common(1)[0][1]
        self.assertLessEqual(worst / len(pos), 0.4, Counter(pos))

    def test_the_plan_holds_the_window_for_long_chapters(self):
        """Exhaustively, on more chapters and more items than exist: the block
        search, not luck, is what makes the bound true."""
        for n in (4, 9, 10, 17, 40):
            for k in range(60):
                plan = qc_fix.key_plan(f"study-{k:03d}-x", n)
                with self.subTest(n=n, k=k):
                    self.assertEqual(len(plan), n)
                    for i in range(max(1, n - qc_fix.BALANCE_WINDOW + 1)):
                        c = Counter(plan[i:i + qc_fix.BALANCE_WINDOW])
                        self.assertLessEqual(max(c.values()),
                                             qc_fix.MAX_KEYS_PER_POSITION)

    def test_the_plan_depends_only_on_the_tag(self):
        self.assertEqual(qc_fix.key_plan("study-019-x", 12),
                         qc_fix.key_plan("study-019-x", 12))
        self.assertNotEqual(qc_fix.key_plan("study-019-x", 12),
                            qc_fix.key_plan("study-020-x", 12))

    def test_content_is_untouched(self):
        """Only the ORDER moves: the same option objects, the same key."""
        for tag, text in self.all_quizzes():
            before, after = items_of(text), items_of(qc_fix.rebalance_keys(text, tag))
            for a, b in zip(before, after):
                with self.subTest(tag):
                    self.assertEqual(sorted(json.dumps(o, sort_keys=True,
                                                       ensure_ascii=False)
                                            for o in options_of(a)),
                                     sorted(json.dumps(o, sort_keys=True,
                                                       ensure_ascii=False)
                                            for o in options_of(b)))
                    self.assertEqual(a.get("question"), b.get("question"))
                    self.assertEqual(a.get("hint"), b.get("hint"))
                    self.assertEqual(
                        [o["text"] for o in options_of(a) if o.get("isCorrect")],
                        [o["text"] for o in options_of(b) if o.get("isCorrect")])

    def test_an_item_that_names_an_option_by_letter_is_skipped(self):
        quiz = {"questions": [{
            "question": "מה נכון? תשובה ב׳ שגויה.",
            "answerOptions": [{"text": "alpha", "isCorrect": True},
                              {"text": "beta"}, {"text": "gamma"},
                              {"text": "delta"}]}]}
        log: list[dict] = []
        out = qc_fix.rebalance_keys(quiz, "study-001-x", log=log)
        self.assertTrue(out["questions"][0]["answerOptions"][0]["isCorrect"])
        self.assertIn("letter", log[0]["skipped"])

    def test_bare_hebrew_above_is_not_a_letter_reference(self):
        """"כמתואר לעיל" refers to the preceding sentence and is in a large
        share of the released rationales; skipping on it would rebalance nothing."""
        item = {"question": "מה המינון?",
                "answerOptions": [{"text": "כמתואר לעיל, 10 מ\"ג", "isCorrect": True},
                                  {"text": "מעל 20 מ\"ג"}]}
        self.assertIsNone(qc_fix.letter_reference(item))

    def test_no_released_item_trips_the_letter_check(self):
        """149 real items, 0 false positives — the reason the check may be
        allowed to skip an item silently."""
        for tag, text in self.all_quizzes():
            for i, item in enumerate(items_of(text)):
                with self.subTest(tag, q=i + 1):
                    self.assertIsNone(qc_fix.letter_reference(item))

    def test_all_of_the_above_is_pinned_last(self):
        quiz = {"questions": [{
            "question": "מהו הטיפול?",
            "answerOptions": [{"text": "All of the above"},
                              {"text": "Sertraline", "isCorrect": True},
                              {"text": "Fluoxetine"}, {"text": "Citalopram"}]}]}
        out = qc_fix.rebalance_keys(quiz, "study-029-x")
        opts = out["questions"][0]["answerOptions"]
        self.assertEqual(opts[-1]["text"], "All of the above")

    def test_an_all_of_the_above_key_is_skipped(self):
        quiz = {"questions": [{
            "question": "מהו הטיפול?",
            "answerOptions": [{"text": "Sertraline"}, {"text": "Fluoxetine"},
                              {"text": "כל התשובות נכונות", "isCorrect": True}]}]}
        log: list[dict] = []
        out = qc_fix.rebalance_keys(quiz, "study-029-x", log=log)
        self.assertEqual(out["questions"][0]["answerOptions"][2]["text"],
                         "כל התשובות נכונות")
        self.assertTrue(log[0]["skipped"])

    def test_multiple_select_is_left_alone(self):
        quiz = {"questions": [{
            "type": "multiple_select", "question": "בחר את כל הנכונות",
            "answerOptions": [{"text": "a", "isCorrect": True},
                              {"text": "b", "isCorrect": True},
                              {"text": "c"}]}]}
        out = qc_fix.rebalance_keys(quiz, "study-042-x")
        self.assertEqual([o["text"] for o in out["questions"][0]["answerOptions"]],
                         ["a", "b", "c"])

    def test_the_presentation_change_is_logged_for_the_owner(self):
        """§5.10.5 sends one notice that answer order changed; it is driven by
        these records, and a record with no `kind` would never reach it."""
        log: list[dict] = []
        qc_fix.rebalance_keys(read(Q019 / "quiz.json"), "study-019-x", log=log)
        moved = [c for c in log if c.get("fixed")]
        self.assertTrue(moved)
        for c in moved:
            self.assertEqual(c["kind"], "presentation")
            self.assertEqual(c["asset"], "quiz.json")


# ---------------------------------------------------------------------------
class TestApplyHygiene(unittest.TestCase):
    """The pass qc_run calls: ids, then hygiene, then the rebalance."""

    def test_the_whole_pass_is_idempotent_on_real_chapters(self):
        for d in QUIZ_DIRS:
            src = chapter(d)
            first = qc_fix.apply_hygiene(assets=src, tag=d.name)
            once = {**src, **first["assets"]}
            second = qc_fix.apply_hygiene(assets=once, tag=d.name)
            with self.subTest(d.name):
                self.assertEqual({**once, **second["assets"]}, once)
                self.assertFalse([c for c in second["changes"] if c.get("fixed")])

    def test_ids_are_taken_before_hygiene_rewrites_a_stem(self):
        """The id is a handle on the item AS PUBLISHED. Derived after the LaTeX
        was collapsed it would differ from what the app computes on the released
        bytes of every chapter that has not been re-fetched yet."""
        stem = r"מהי ההשפעה על $\alpha_{2}$?"
        text = json.dumps({"questions": [{"question": stem,
                                          "answerOptions": [{"text": "a", "isCorrect": True},
                                                            {"text": "b"}]}]},
                          ensure_ascii=False, indent=2)
        out = qc_fix.apply_hygiene(assets={"quiz.json": text}, tag="t")
        item = items_of(out["assets"]["quiz.json"])[0]
        self.assertEqual(item["id"], qc_fix.item_id(stem))
        self.assertNotIn(r"\alpha", item["question"])

    def test_only_changed_assets_come_back(self):
        """A chapter with nothing to clean uploads nothing: qc_store compares
        sha256 and an asset returned unchanged-but-rewritten would be published
        and hashed for no reason."""
        out = qc_fix.apply_hygiene(assets={"facts.json": read(FACTS_029)},
                                   tag="study-029-ssris-deep-dive")
        self.assertEqual(out["assets"], {})
        self.assertEqual(out["changes"], [])


# ---------------------------------------------------------------------------
class TestTier1(unittest.TestCase):

    def test_the_record_is_embedded_in_the_item(self):
        src = chapter(Q019)
        out, fixes = qc_fix.tier1(src, [finding()], "2026-09-23")
        item = items_of(out["quiz.json"])[0]
        rec = item["qc"]
        self.assertEqual(rec["kind"], "content")
        self.assertEqual(rec["status"], "active")
        self.assertEqual(rec["date"], "2026-09-23")
        self.assertEqual(rec["was"], finding()["said"])
        self.assertEqual(rec["source"]["book"], "StahlKids")
        self.assertEqual(rec["source"]["printed_page"], 74)
        self.assertEqual(rec["source"]["quote"], finding()["source_quote"])
        self.assertEqual(fixes[0]["embedded"], "item")
        self.assertFalse(fixes[0]["needs_owner"])

    def test_nothing_else_in_the_asset_changes(self):
        """The property owner decision 2 is: a note, and not one edited word."""
        src = chapter(Q019)
        out, _ = qc_fix.tier1(src, [finding(), finding(id="f002", unit="C1")],
                              "2026-09-23")
        for name in ("quiz.json", "flashcards.json"):
            with self.subTest(name):
                self.assertEqual(strip_qc(json.loads(out[name])),
                                 json.loads(src[name]))

    def test_the_app_reads_what_is_written(self):
        """quiz_logic.qc_mark is the only thing that ever renders this record.
        A field spelled the other way shows the learner a blank mark."""
        src = chapter(Q019)
        out, _ = qc_fix.tier1(src, [finding()], "2026-09-23")
        mark = quiz_logic.qc_mark(items_of(out["quiz.json"])[0])
        self.assertIsNotNone(mark)
        self.assertEqual(mark["was"], finding()["said"])
        self.assertEqual(mark["now"], finding()["correction"])
        self.assertEqual(mark["severity"], "high")
        self.assertEqual(mark["sources"][0]["page"], 74)
        self.assertFalse(mark["conflict"])

    def test_a_source_conflict_carries_both_quotes(self):
        src = chapter(Q019)
        f = finding(kind="source_conflict", severity="medium",
                    said="2-8 שבועות", source_quote="may take 2-8 weeks",
                    printed=69, second_book="StahlKids", second_printed=74,
                    second_quote="8-12 weeks")
        out, fixes = qc_fix.tier1(src, [f], "2026-09-23")
        mark = quiz_logic.qc_mark(items_of(out["quiz.json"])[0])
        self.assertTrue(mark["conflict"])
        self.assertEqual([s["page"] for s in mark["sources"]], [69, 74])
        self.assertEqual(fixes[0]["second_quote"], "8-12 weeks")

    def test_kartisiot_is_named(self):
        """Owner decision 4: anything grounded in סיכומי כרטיסיות says so."""
        src = {"sketchy_main.txt": read(LEGEND)}
        row = qc_units.units(src)[1]
        f = finding(unit=row.uid, book="Kartisiot", printed=506,
                    said="Clonidine ER", source_quote="קלונידין ER")
        out, fixes = qc_fix.tier1(src, [f], "2026-09-23")
        self.assertIn("סיכומי כרטיסיות שלב ב", out["sketchy_main.txt"])
        self.assertEqual(fixes[0]["record"]["source_name_he"],
                         "סיכומי כרטיסיות שלב ב")

    def test_a_legend_row_gets_a_note_line_under_it(self):
        src = {"sketchy_main.txt": read(LEGEND)}
        rows = [u for u in qc_units.units(src) if u.meta.get("row") == "24"]
        f = finding(unit=rows[0].uid, said="2-8 weeks for Atomoxetine")
        out, fixes = qc_fix.tier1(src, [f], "2026-09-23")
        new = out["sketchy_main.txt"]
        self.assertEqual(fixes[0]["embedded"], "legend_row")
        self.assertIn(qc_fix.NOTE_MARK, new)
        # Under its own row, above the next one.
        note = new.index(qc_fix.NOTE_MARK)
        self.assertLess(new.index("24. לוח ספירה"), note)
        self.assertLess(note, new.index("25. תרסיסי סימון"))
        self.assertEqual(qc_fix.strip_notes("sketchy_main.txt", new),
                         src["sketchy_main.txt"])

    def test_a_guide_span_gets_a_marker_and_a_footnote(self):
        src = {"study_guide.md": read(GUIDE_029)}
        said = "קישור לאתר אלוסטרי (allosteric site) על גבי הטרנספורטר SERT"
        f = finding(unit="G:3. drug_mechanism", said=said, book="StahlNeuro",
                    printed=289, source_quote="SSRIs bind to the transporter")
        out, fixes = qc_fix.tier1(src, [f], "2026-09-23")
        new = out["study_guide.md"]
        self.assertEqual(fixes[0]["embedded"], "guide_footnote")
        self.assertIn(said + "[^qc-f001]", new)
        self.assertIn("> " + qc_fix.NOTE_MARK, new)
        # The note names the book the way the owner reads it, and the page.
        self.assertIn(qc_fix.book_he("StahlNeuro") + " עמ' 289", new)
        # Only additions: with the notes taken back out the guide is itself.
        back = qc_fix.strip_notes("study_guide.md", new)
        self.assertEqual(re.sub(r"\n{3,}", "\n\n", back),
                         re.sub(r"\n{3,}", "\n\n", src["study_guide.md"]))

    def test_a_duplicated_span_goes_to_the_owner(self):
        """§5.9 places a note only on exactly one match inside the section."""
        src = {"study_guide.md": read(GUIDE_029)}
        f = finding(unit="G:3. drug_mechanism", said="(StahlNeuro p. 33, 289)")
        out, fixes = qc_fix.tier1(src, [f], "2026-09-23")
        self.assertEqual(out, {})
        self.assertEqual(fixes[0]["embedded"], "none")
        self.assertTrue(fixes[0]["needs_owner"])
        self.assertIn("occurs 2 times", fixes[0]["reason"])

    def test_a_span_that_is_not_there_goes_to_the_owner(self):
        src = {"study_guide.md": read(GUIDE_029)}
        f = finding(unit="G:3. drug_mechanism",
                    said="משפט שמעולם לא נכתב במדריך הזה בכלל")
        out, fixes = qc_fix.tier1(src, [f], "2026-09-23")
        self.assertEqual(out, {})
        self.assertTrue(fixes[0]["needs_owner"])
        self.assertIn("not in the section", fixes[0]["reason"])

    def test_too_short_to_anchor_goes_to_the_owner(self):
        src = {"study_guide.md": read(GUIDE_029)}
        out, fixes = qc_fix.tier1(src, [finding(unit="G:3. drug_mechanism",
                                                said="SERT")], "2026-09-23")
        self.assertEqual(out, {})
        self.assertTrue(fixes[0]["needs_owner"])
        self.assertIn("too short", fixes[0]["reason"])

    def test_two_findings_in_one_section_both_land(self):
        """Applied back to front, or the second one's offsets are already stale."""
        src = {"study_guide.md": read(GUIDE_029)}
        unit = next(u for u in qc_units.units(src)
                    if u.uid == "G:3. drug_mechanism")
        body = unit.text
        spans = [body[m.start():m.start() + 60]
                 for m in re.finditer(r"[^\s]", body)]
        unique = [s for s in spans
                  if len(s) == 60 and body.count(s) == 1
                  and chr(10) not in s]
        a, b = unique[0], next(s for s in unique
                               if body.index(s) > body.index(unique[0]) + 60)
        out, fixes = qc_fix.tier1(
            src, [finding(id="fa", unit=unit.uid, said=a),
                  finding(id="fb", unit=unit.uid, said=b)], "2026-09-23")
        new = out["study_guide.md"]
        self.assertIn(a + "[^qc-fa]", new)
        self.assertIn(b + "[^qc-fb]", new)
        self.assertFalse([f for f in fixes if f["needs_owner"]])

    def test_a_second_finding_on_one_item_is_kept_and_flagged(self):
        """The app draws one mark per item, so the second is carried under
        `also` and handed to the owner rather than dropped."""
        src = chapter(Q019)
        out, fixes = qc_fix.tier1(src, [finding(id="f1"), finding(id="f2")],
                                  "2026-09-23")
        rec = items_of(out["quiz.json"])[0]["qc"]
        self.assertEqual(rec["finding_id"], "f1")
        self.assertEqual(rec["also"][0]["finding_id"], "f2")
        self.assertTrue(fixes[1]["needs_owner"])

    def test_facts_are_errata_only(self):
        """There is nowhere in facts.json to put a record that would not itself
        become a unit the next run asks the judge to verify."""
        src = {"facts.json": read(FACTS_029)}
        unit = qc_units.units(src)[0]
        out, fixes = qc_fix.tier1(src, [finding(unit=unit.uid)], "2026-09-23")
        self.assertEqual(out, {})
        self.assertEqual(fixes[0]["embedded"], "errata_only")
        self.assertFalse(fixes[0]["needs_owner"])

    def test_an_unknown_unit_is_never_silently_dropped(self):
        out, fixes = qc_fix.tier1(chapter(Q019), [finding(unit="Q99")],
                                  "2026-09-23")
        self.assertEqual(out, {})
        self.assertTrue(fixes[0]["needs_owner"])

    def test_unsupported_findings_do_not_reach_the_item(self):
        """§5.8: only `contradicted` may reach qc_fix; `unsupported` is the
        owner's queue and never becomes a mark the learner sees."""
        res = qc_fix.apply_tier1(findings=[finding(kind="unsupported")],
                                 assets=chapter(Q019), date="2026-09-23")
        self.assertEqual(res["assets"], {})
        self.assertTrue(res["fixes"][0]["needs_owner"])

    def test_a_low_severity_finding_is_recorded_but_not_embedded(self):
        """§5.9 acts on high/medium. A `low` is style: written down in
        fixes.json, never a mark beside the item, and it does not hold the
        chapter."""
        res = qc_fix.apply_tier1(findings=[finding(severity="low")],
                                 assets=chapter(Q019), date="2026-09-23")
        self.assertEqual(res["assets"], {})
        self.assertFalse(res["fixes"][0]["needs_owner"])
        self.assertIn("high/medium", res["fixes"][0]["reason"])

    def test_the_fix_row_carries_what_the_report_prints(self):
        """qc_run.errata_lines builds the owner's Hebrew erratum off these
        fields; a row that only nested them renders as an empty correction."""
        _, fixes = qc_fix.tier1(chapter(Q019), [finding()], "2026-09-23")
        row = fixes[0]
        for key in ("id", "asset", "unit", "severity", "said", "correction",
                    "book", "printed", "source_quote", "status", "date"):
            with self.subTest(key):
                self.assertTrue(row[key] not in (None, ""), key)
        self.assertEqual(row["asset"], "quiz.json")
        self.assertEqual(row["status"], "active")


# ---------------------------------------------------------------------------
class TestReplay(unittest.TestCase):
    """qc_revert rebuilds from the pristine snapshot and replays the fixes that
    stay active, so a fix record has to be enough on its own."""

    def replayable(self, src, findings):
        out, fixes = qc_fix.tier1(src, findings, "2026-09-23")
        return out, fixes

    def test_quiz_replay_matches(self):
        src = chapter(Q019)
        out, fixes = self.replayable(src, [finding()])
        self.assertEqual(qc_fix.apply_fix("quiz.json", src["quiz.json"], fixes[0]),
                         out["quiz.json"])

    def test_guide_replay_matches(self):
        src = {"study_guide.md": read(GUIDE_029)}
        said = "קישור לאתר אלוסטרי (allosteric site) על גבי הטרנספורטר SERT"
        out, fixes = self.replayable(src, [finding(unit="G:3. drug_mechanism",
                                                   said=said)])
        self.assertEqual(qc_fix.apply_fix("study_guide.md",
                                          src["study_guide.md"], fixes[0]),
                         out["study_guide.md"])

    def test_legend_replay_matches(self):
        src = {"sketchy_main.txt": read(LEGEND)}
        row = [u for u in qc_units.units(src) if u.meta.get("row") == "24"][0]
        out, fixes = self.replayable(src, [finding(unit=row.uid,
                                                   said="2-8 weeks")])
        self.assertEqual(qc_fix.apply_fix("sketchy_main.txt",
                                          src["sketchy_main.txt"], fixes[0]),
                         out["sketchy_main.txt"])

    def test_replaying_several_fixes_in_order(self):
        src = chapter(Q019)
        out, fixes = self.replayable(src, [finding(id="f1", unit="Q1"),
                                           finding(id="f2", unit="Q2")])
        self.assertEqual(qc_fix.apply_fixes("quiz.json", src["quiz.json"], fixes),
                         out["quiz.json"])

    def test_an_owner_row_replays_as_a_no_op(self):
        src = {"facts.json": read(FACTS_029)}
        unit = qc_units.units(src)[0]
        _, fixes = qc_fix.tier1(src, [finding(unit=unit.uid)], "2026-09-23")
        self.assertEqual(qc_fix.apply_fix("facts.json", src["facts.json"],
                                          fixes[0]), src["facts.json"])

    def test_a_fix_whose_item_is_gone_refuses(self):
        """Better a loud refusal than a note attached to the wrong question."""
        src = chapter(Q019)
        _, fixes = qc_fix.tier1(src, [finding()], "2026-09-23")
        bad = dict(fixes[0], item_id="0" * 16)
        bad.pop("index")
        with self.assertRaises(qc_fix.FixError):
            qc_fix.apply_fix("quiz.json", src["quiz.json"], bad)


# ---------------------------------------------------------------------------
class TestOldValueHits(unittest.TestCase):

    def assets(self):
        src = chapter(Q019)
        src["sketchy_main.txt"] = read(LEGEND)
        return src

    def test_the_same_onset_in_the_legend_is_found(self):
        """§5.9's sweep, on the case it was written for: 019's atomoxetine onset
        is repeated in the plate legend's row 24."""
        hits = qc_fix.old_value_hits(finding(said="2-8 שבועות"), self.assets(),
                                     tag="study-019")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["asset"], "sketchy_main.txt")
        self.assertIn("Atomoxetine", hits[0]["term"])

    def test_the_findings_own_unit_is_not_reported_back(self):
        hits = qc_fix.old_value_hits(finding(said="2-8 שבועות"), self.assets(),
                                     tag="study-019")
        self.assertNotIn("Q1", [h["unit"] for h in hits])

    def test_a_bare_value_with_no_entity_nearby_is_not_a_hit(self):
        """StahlKids repeats "may take 2-8 weeks" on 12 pages; a number alone
        is not the same claim."""
        other = {"study-099": {"facts.json": json.dumps(
            {"note": "המפגש נמשך 2-8 שבועות בקבוצה"}, ensure_ascii=False)}}
        hits = qc_fix.old_value_hits(finding(said="2-8 שבועות"),
                                     {"quiz.json": chapter(Q019)["quiz.json"]},
                                     other, tag="study-019")
        self.assertFalse([h for h in hits if h["tag"] == "study-099"])

    def test_another_chapter_is_searched(self):
        other = {"study-018": {"flashcards.json": json.dumps(
            {"cards": [{"front": "Atomoxetine onset?",
                        "back": "ההשפעה המלאה מגיעה תוך 2-8 שבועות"}]},
            ensure_ascii=False)}}
        hits = qc_fix.old_value_hits(finding(said="2-8 שבועות"), self.assets(),
                                     other, tag="study-019")
        self.assertIn("study-018", [h["tag"] for h in hits])

    def test_a_finding_with_no_locked_value_reports_nothing(self):
        self.assertEqual(
            qc_fix.old_value_hits(finding(said="הטיפול יעיל מאוד"),
                                  self.assets(), tag="study-019"), [])

    def test_a_broken_sibling_chapter_does_not_stop_the_sweep(self):
        other = {"study-077": {"quiz.json": "{not json"}}
        hits = qc_fix.old_value_hits(finding(said="2-8 שבועות"), self.assets(),
                                     other, tag="study-019")
        self.assertTrue(hits)


# ---------------------------------------------------------------------------
class TestTier2IsUnreachable(unittest.TestCase):
    """§5.9's rewrite gate. Nothing below may change a byte of a released asset."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def state(self, **fields):
        (self.root / "state.json").write_text(
            json.dumps({"runs": {}, **fields}), encoding="utf-8")
        return self.root

    def test_off_by_default(self):
        self.assertFalse(qc_fix.tier2_enabled(self.root))          # no file
        self.assertFalse(qc_fix.tier2_enabled(self.state()))       # no flag
        self.assertFalse(qc_fix.tier2_enabled(self.state(tier2_enabled=False)))

    def test_an_unreadable_state_file_is_off(self):
        (self.root / "state.json").write_text("{ broken", encoding="utf-8")
        self.assertFalse(qc_fix.tier2_enabled(self.root))

    def test_apply_tier2_changes_nothing_with_the_flag_off(self):
        src = chapter(Q019)
        res = qc_fix.apply_tier2(findings=[finding()], assets=src,
                                 tag="study-019", root=self.root)
        self.assertEqual(res["assets"], {})
        self.assertEqual(res["fixes"], [])
        self.assertEqual(src, chapter(Q019))                       # not mutated
        self.assertIn("off", res["skipped"])

    def test_apply_tier2_changes_nothing_with_the_flag_on_either(self):
        """The flag is only the first of three gates (§5.9 a/b/c) and the
        closing sentence holds every patch for the owner, so there is no state
        of this program in which a rewrite reaches a published asset."""
        root = self.state(tier2_enabled=True)
        self.assertTrue(qc_fix.tier2_enabled(root))
        src = chapter(Q019)
        res = qc_fix.apply_tier2(findings=[finding()], assets=src,
                                 tag="study-019", root=root)
        self.assertEqual(res["assets"], {})
        self.assertEqual(res["fixes"], [])
        self.assertEqual(src, chapter(Q019))

    def test_a_patch_with_every_gate_passing_still_waits(self):
        original = {"question": "כמה זמן לוקח ל-Atomoxetine להשפיע?",
                    "answerOptions": [{"text": "2-8 שבועות", "isCorrect": True},
                                      {"text": "יום אחד"}]}
        proposed = {"answerOptions": [{"text": "8-12 שבועות", "isCorrect": True},
                                      {"text": "יום אחד"}]}
        patch = qc_fix.propose_patch(
            finding(), unit=None, asset="quiz.json", proposed=proposed,
            source_text="full therapeutic benefits may take 8-12 weeks",
            original=original, second_opinion={"correction": "8-12 שבועות"},
            value_elsewhere=False)
        self.assertTrue(all(patch["gates"].values()))
        self.assertEqual(patch["held_by"], [])
        self.assertEqual(patch["status"], "awaiting_owner")
        self.assertFalse(patch["applied"])

    def test_an_unknown_search_result_is_not_a_pass(self):
        """A caller that did not run §5.9(a)'s full-text search has not cleared
        the gate; `None` must not read as "the value is nowhere else"."""
        patch = qc_fix.propose_patch(finding(), unit=None, asset="quiz.json",
                                     proposed={"hint": "8-12 שבועות"},
                                     source_text="may take 8-12 weeks",
                                     original={}, value_elsewhere=None)
        self.assertIn("value_nowhere_else", patch["held_by"])

    def test_the_revert_hook_answers_instead_of_failing(self):
        res = qc_fix.revert_patches(findings=[finding()], assets=chapter(Q019),
                                    tag="study-019", root=self.root)
        self.assertEqual(res["assets"], {})


# ---------------------------------------------------------------------------
class TestPatchChecks(unittest.TestCase):

    ORIGINAL = {"question": "כמה זמן?",
                "answerOptions": [{"text": "2-8 שבועות", "isCorrect": True,
                                   "rationale": "r"}, {"text": "יום", "rationale": "r2"}],
                "hint": "h"}
    SOURCE = "Atomoxetine: full therapeutic benefits may take 8-12 weeks."

    def check(self, proposed, **kw):
        return qc_fix.patch_checks({"asset": "quiz.json", "proposed": proposed},
                                   original=self.ORIGINAL, source_text=self.SOURCE,
                                   finding=finding(), **kw)

    def test_a_good_patch_passes(self):
        got = self.check({"answerOptions": [
            {"text": "8-12 שבועות", "isCorrect": True, "rationale": "r"},
            {"text": "יום", "rationale": "r2"}]})
        self.assertTrue(got["ok"], got["failures"])

    def test_a_field_outside_the_list_fails(self):
        got = self.check({"title": "x"})
        self.assertFalse(got["ok"])
        self.assertFalse(got["checks"]["fields"])

    def test_an_option_field_outside_the_list_fails(self):
        got = self.check({"answerOptions": [
            {"text": "8-12 שבועות", "isCorrect": True, "imageUrl": "u"}]})
        self.assertFalse(got["checks"]["fields"])

    def test_a_number_from_nowhere_fails(self):
        got = self.check({"answerOptions": [{"text": "20-30 שבועות",
                                             "isCorrect": True}]})
        self.assertFalse(got["checks"]["numbers"])

    def test_a_key_without_the_sources_value_fails(self):
        got = self.check({"answerOptions": [{"text": "יום", "isCorrect": True},
                                            {"text": "8-12 שבועות"}]})
        self.assertFalse(got["checks"]["key_value"])

    def test_a_rewritten_stem_that_duplicates_another_item_fails(self):
        got = self.check({"question": "כמה זמן לוקח?"},
                         other_heads=["כמה זמן לוקח?"])
        self.assertFalse(got["checks"]["duplicates"])


# ---------------------------------------------------------------------------
class TestJsonStyle(unittest.TestCase):
    """Every asset written here is uploaded and hashed; a re-dump under the
    wrong style rewrites 30,000 bytes for a two-line correction."""

    def test_the_released_style_round_trips(self):
        for d in QUIZ_DIRS:
            for name in ("quiz.json", "flashcards.json"):
                text = read(d / name)
                style = qc_fix.detect_json_style(text)
                with self.subTest(f"{d.name}/{name}"):
                    self.assertEqual(style.dumps(json.loads(text)), text)

    def test_an_unknown_style_falls_back_without_raising(self):
        text = '{"questions":[]}'
        self.assertEqual(qc_fix.detect_json_style(text), qc_fix.JsonStyle())


# ---------------------------------------------------------------------------
class TestCli(unittest.TestCase):

    def test_hygiene_run_writes_the_changed_assets(self):
        out = Path(tempfile.mkdtemp())
        rc = qc_fix.main([str(Q019), "--tag", "study-019-x", "--hygiene",
                          "--write", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue((out / "quiz.json").exists())
        self.assertNotEqual(read(out / "quiz.json"), read(Q019 / "quiz.json"))


if __name__ == "__main__":
    unittest.main()
