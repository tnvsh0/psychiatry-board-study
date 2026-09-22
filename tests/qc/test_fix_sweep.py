#!/usr/bin/env python3
"""§5.9's cross-artifact / cross-chapter sweep, reached through the public API.

"After every locked-value correction, search every artifact of the chapter
(guide incl. high-yield and summary, facts, quiz, cards, legends) and every
other chapter for the old value next to the same entity; attach every hit to the
same finding."

`qc_fix.old_value_hits` has implemented the search since the module was written
and nothing called it, so the sweep was specified and never ran: the quiz item
got its mark while the same wrong number, beside the same drug name, stood
unmarked in the guide and the legend chip of the same chapter and unchanged in
every other chapter that repeats it. These tests hold `apply_tier1` — the entry
point qc_run binds (`qc_run._TIER1_NAMES`) — to running it.

The four things that can go wrong once it does run, each with a test:

  1. It is not reached at all. `test_apply_tier1_*`.
  2. It is reached but sees only the quiz, so the guide copy is still missed.
  3. It reports the chapter's OWN correction back as an uncorrected copy — a
     Tier-1 record embeds the old value in `qc.was`, so a chapter corrected on
     an earlier pass would fill the report with rows the owner opens and finds
     already marked, and a report like that stops being opened.
  4. It is never given the other chapters at all, because nothing passed them
     in. `other_chapters_on_disk` reads what qc_run already fetched.

Offline: no agy, no gh, no network, no book. The corpus is a temp `_qc` tree.

    python -m unittest tests.qc.test_fix_sweep
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import qc_fix        # noqa: E402
import qc_units      # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
Q019 = FIX / "quiz" / "study-019-adhd-treatment-non-stimulants"
LEGEND = FIX / "legends" / "sketchy_main.txt"
TAG = "study-019-adhd-treatment-non-stimulants"
DATE = "2026-09-23"

# 019's atomoxetine onset — the 2026-09-18 case §5.9 was written for. StahlKids
# p.69 says 2-8 weeks and p.74 says the full benefit takes 8-12, and the chapter
# repeats the 2-8 in the quiz, in the guide and in the plate legend's chip.
SAID = "2-8 שבועות"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def chapter() -> dict[str, str]:
    out = {p.name: read(p) for p in sorted(Q019.iterdir())
           if p.is_file() and qc_units.is_judged_asset(p.name)}
    out["sketchy_main.txt"] = read(LEGEND)
    return out


def finding(**kw) -> dict:
    base = {
        "id": "f001", "unit": "Q1", "kind": "contradicted", "severity": "high",
        "category": "locked_value", "said": SAID, "correction": "8-12 שבועות",
        "book": "StahlKids", "printed": 74,
        "source_quote": "full therapeutic benefits may take 8-12 weeks",
        "problem": "Atomoxetine onset", "evidence_code": "AB23CD",
    }
    base.update(kw)
    return base


def rows_for(res: dict, fid: str = "f001") -> list[dict]:
    return [r for r in res["fixes"] if r.get("finding_id") == fid]


# ---------------------------------------------------------------------------
class TestApplyTier1RunsTheSweep(unittest.TestCase):
    """The blocking gap: the sweep exists and the pipeline never asks for it."""

    def test_every_embedded_row_carries_also_in(self):
        res = qc_fix.apply_tier1(findings=[finding()], assets=chapter(),
                                 tag=TAG, date=DATE, other_chapters={})
        rows = rows_for(res)
        self.assertTrue(rows, "the contradiction was not embedded at all")
        for r in rows:
            self.assertIn("also_in", r,
                          "§5.9's sweep leaves no record on the fix row, so "
                          "§5.10's global pass and _qc/REPORT.md have nothing "
                          "to work from")

    def test_the_legend_copy_of_the_same_onset_is_reported(self):
        """The quiz item gets the mark; the legend chip must at least be named.

        Correcting one of five copies and saying nothing about the other four
        is the §2.4 failure ("mark, never hide") in its quietest form: the
        learner reads the marked item, turns to the plate, and the plate still
        says the wrong thing with no mark on it.
        """
        res = qc_fix.apply_tier1(findings=[finding()], assets=chapter(),
                                 tag=TAG, date=DATE, other_chapters={})
        hits = rows_for(res)[0]["also_in"]
        self.assertIn("sketchy_main.txt", [h["asset"] for h in hits])
        self.assertTrue(all(h["tag"] == TAG for h in hits))
        self.assertNotIn("Q1", [h["unit"] for h in hits],
                         "the unit the finding is already on is not a second "
                         "copy of it")

    def test_another_chapter_repeating_the_value_is_reported(self):
        other = {"study-018-adhd-treatment-stimulants": {
            "flashcards.json": json.dumps(
                {"cards": [{"front": "Atomoxetine onset?",
                            "back": "ההשפעה המלאה מגיעה תוך 2-8 שבועות"}]},
                ensure_ascii=False)}}
        res = qc_fix.apply_tier1(findings=[finding()], assets=chapter(),
                                 tag=TAG, date=DATE, other_chapters=other)
        hits = rows_for(res)[0]["also_in"]
        self.assertIn("study-018-adhd-treatment-stimulants",
                      [h["tag"] for h in hits])

    def test_a_row_with_no_locked_value_still_says_the_sweep_ran(self):
        """An empty list is an answer; a missing key is a sweep that did not
        run, and the two must not read the same to the owner."""
        res = qc_fix.apply_tier1(
            findings=[finding(said="הטיפול יעיל מאוד", correction="יעיל בינוני")],
            assets=chapter(), tag=TAG, date=DATE, other_chapters={})
        rows = rows_for(res)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["also_in"], [])

    def test_sweep_false_turns_it_off_for_a_replay(self):
        res = qc_fix.apply_tier1(findings=[finding()], assets=chapter(),
                                 tag=TAG, date=DATE, sweep=False)
        self.assertNotIn("also_in", rows_for(res)[0])

    def test_a_held_finding_gets_no_sweep(self):
        """§5.9 sweeps after a CORRECTION. A `low` is written down and embedded
        by nothing, so there is no correction to sweep behind."""
        res = qc_fix.apply_tier1(findings=[finding(severity="low")],
                                 assets=chapter(), tag=TAG, date=DATE,
                                 other_chapters={})
        rows = rows_for(res)
        self.assertTrue(rows)
        self.assertNotIn("also_in", rows[0])

    def test_two_findings_get_their_own_hits(self):
        both = [finding(), finding(id="f002", unit="Q2",
                                   said="60 מ\"ג", correction="100 מ\"ג")]
        res = qc_fix.apply_tier1(findings=both, assets=chapter(), tag=TAG,
                                 date=DATE, other_chapters={})
        by_id = {r["finding_id"]: r for r in res["fixes"]}
        self.assertIn("also_in", by_id["f001"])
        self.assertIn("also_in", by_id["f002"])
        self.assertNotEqual(by_id["f001"]["also_in"],
                            by_id["f002"]["also_in"])

    def test_the_sweep_does_not_rewrite_anything(self):
        """It is a report. §5.9 Tier 1 changes no studied content, and adding a
        search to it may not change that."""
        src = chapter()
        with_sweep = qc_fix.apply_tier1(findings=[finding()], assets=dict(src),
                                        tag=TAG, date=DATE, other_chapters={})
        without = qc_fix.apply_tier1(findings=[finding()], assets=dict(src),
                                     tag=TAG, date=DATE, sweep=False)
        self.assertEqual(with_sweep["assets"], without["assets"])


# ---------------------------------------------------------------------------
class TestTheSweepReadsPastItsOwnNotes(unittest.TestCase):
    """A chapter corrected on an earlier pass must not report itself."""

    def test_an_embedded_record_is_not_a_second_copy_of_the_value(self):
        src = chapter()
        first = qc_fix.apply_tier1(findings=[finding()], assets=src, tag=TAG,
                                   date=DATE, other_chapters={})
        marked = {**src, **first["assets"]}
        self.assertNotEqual(marked["quiz.json"], src["quiz.json"],
                            "the fixture did not actually get a note")
        again = qc_fix.apply_tier1(findings=[finding()], assets=marked,
                                   tag=TAG, date=DATE, other_chapters={})
        self.assertEqual([h["unit"] for h in rows_for(first)[0]["also_in"]],
                         [h["unit"] for h in rows_for(again)[0]["also_in"]],
                         "the second pass reported the notes the first pass "
                         "wrote; §5.9's sweep asks where the old value still "
                         "stands as a CLAIM, and a correction is not a claim")

    def test_without_qc_notes_leaves_the_material_alone(self):
        src = chapter()
        clean = qc_fix.without_qc_notes(src)
        self.assertEqual(json.loads(clean["quiz.json"]),
                         json.loads(src["quiz.json"]))
        self.assertEqual(clean["sketchy_main.txt"], src["sketchy_main.txt"])

    def test_without_qc_notes_keeps_an_asset_it_cannot_parse(self):
        got = qc_fix.without_qc_notes({"quiz.json": "{not json"})
        self.assertEqual(got["quiz.json"], "{not json")


# ---------------------------------------------------------------------------
class TestOtherChaptersOnDisk(unittest.TestCase):
    """Where "every other chapter" comes from when nobody passes it in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, rel: str, **assets: str) -> None:
        d = self.root / rel
        d.mkdir(parents=True, exist_ok=True)
        for name, text in assets.items():
            (d / name).write_text(text, encoding="utf-8", newline="")

    CARDS = json.dumps({"cards": [{"front": "Atomoxetine onset?",
                                   "back": "מגיעה תוך 2-8 שבועות"}]},
                       ensure_ascii=False)

    def test_a_fetched_release_is_found(self):
        self.write("work/study-018-x/release", **{"flashcards.json": self.CARDS})
        got = qc_fix.other_chapters_on_disk(self.root)
        self.assertEqual(sorted(got), ["study-018-x"])
        self.assertIn("flashcards.json", got["study-018-x"])

    def test_a_snapshot_is_found_when_the_work_tree_was_swept(self):
        """`_qc/work/` is gitignored and bulk_run sweeps it; `_qc/orig/` is the
        copy §5.5 keeps forever."""
        self.write("orig/study-030-y", **{"flashcards.json": self.CARDS})
        self.assertEqual(sorted(qc_fix.other_chapters_on_disk(self.root)),
                         ["study-030-y"])

    def test_the_work_copy_wins_over_the_snapshot(self):
        self.write("orig/study-018-x", **{"flashcards.json": self.CARDS})
        self.write("work/study-018-x/release",
                   **{"flashcards.json": self.CARDS.replace("2-8", "8-12")})
        got = qc_fix.other_chapters_on_disk(self.root)
        self.assertIn("8-12", got["study-018-x"]["flashcards.json"])

    def test_the_chapter_being_fixed_is_excluded(self):
        self.write("work/study-018-x/release", **{"flashcards.json": self.CARDS})
        self.assertEqual(
            qc_fix.other_chapters_on_disk(self.root, exclude="study-018-x"), {})

    def test_manifests_and_journals_are_not_assets(self):
        self.write("orig/study-018-x", **{"flashcards.json": self.CARDS,
                                          "manifest.json": "{}"})
        got = qc_fix.other_chapters_on_disk(self.root)
        self.assertEqual(sorted(got["study-018-x"]), ["flashcards.json"])

    def test_an_empty_root_is_not_an_error(self):
        self.assertEqual(qc_fix.other_chapters_on_disk(self.root), {})
        self.assertEqual(
            qc_fix.other_chapters_on_disk(self.root / "nope"), {})

    def test_apply_tier1_finds_them_through_root_alone(self):
        """qc_run passes `root` and nothing else; the sweep has to reach the
        backlog from there or "every other chapter" is one chapter."""
        self.write("work/study-018-x/release", **{"flashcards.json": self.CARDS})
        res = qc_fix.apply_tier1(findings=[finding()], assets=chapter(),
                                 tag=TAG, date=DATE, root=str(self.root))
        self.assertIn("study-018-x",
                      [h["tag"] for h in rows_for(res)[0]["also_in"]])

    def test_a_broken_sibling_chapter_does_not_stop_the_sweep(self):
        self.write("orig/study-077-broken", **{"quiz.json": "{not json"})
        self.write("work/study-018-x/release", **{"flashcards.json": self.CARDS})
        res = qc_fix.apply_tier1(findings=[finding()], assets=chapter(),
                                 tag=TAG, date=DATE, root=str(self.root))
        tags = [h["tag"] for h in rows_for(res)[0]["also_in"]]
        self.assertIn("study-018-x", tags)
        self.assertNotIn("study-077-broken", tags)


# ---------------------------------------------------------------------------
class TestTheEntityWindowKeepsTheFindingsOwnWords(unittest.TestCase):
    """Why a wired-up sweep still reported nothing on the §5.9 sample.

    `_entities` is cut to qc_lint.MAX_KEY_TERMS (12). Ranked by length alone,
    one SSRI quiz item's English names (Desensitization, somatodendritic,
    autoreceptors …) filled the budget and the two Hebrew words the finding is
    about were dropped — so the copy in facts.json, whose unit is the bare value
    with the drug's name in a sibling key, matched no term and was never
    reported. The `said` is the narrowest text there is and goes first.
    """

    SAID = "ההשפעה מופיעה תוך 4 שבועות"
    UNIT = ("stem: Desensitization of somatodendritic autoreceptors — "
            "Upregulation, Postsynaptic Transporter, Depressive, Fluoxetine, "
            "disinhibited הסרוטונינרגיים סנסיטיזציה הטרנספורטר")

    def test_the_saids_own_words_survive_the_cap(self):
        terms = qc_fix._entities(self.SAID, "", "", self.UNIT)
        self.assertIn("ההשפעה", terms)
        self.assertIn("מופיעה", terms)

    def test_the_unit_still_contributes_longest_first(self):
        terms = qc_fix._entities("", "", "", self.UNIT)
        self.assertEqual(terms[:2], sorted(terms[:2], key=len, reverse=True))
        self.assertIn("Desensitization", terms)

    def test_a_facts_leaf_naming_no_drug_is_reached(self):
        facts = json.dumps(
            {"drug_class": {"mechanism": "Fluoxetine blocks SERT",
                            "onset": self.SAID}}, ensure_ascii=False)
        hits = qc_fix.old_value_hits(
            finding(said=self.SAID, unit="Q1", problem="", source_quote=""),
            {"facts.json": facts, "quiz.json": json.dumps(
                {"questions": [{"question": self.UNIT, "answerOptions": []}]},
                ensure_ascii=False)}, tag=TAG)
        self.assertEqual([h["unit"] for h in hits], ["F:drug_class.onset"])


# ---------------------------------------------------------------------------
class TestReachableFromWhatTheOrchestratorBinds(unittest.TestCase):
    """qc_run resolves the fixer by name (`qc_run._TIER1_NAMES`). The sweep has
    to be on the far side of THAT name, not of a helper only a test calls."""

    def test_the_bound_tier1_sweeps(self):
        sys.path.insert(0, str(ROOT / "app"))
        import qc_run
        fn = qc_run._bind("qc_fix", qc_run._TIER1_NAMES, "tier1")
        res = fn(findings=[finding()], assets=chapter(), tag=TAG, units=None,
                 root=None, date=DATE, other_chapters={})
        self.assertIn("also_in", rows_for(res)[0])


# ---------------------------------------------------------------------------
class TestParsedCorpusIsTheSameAnswer(unittest.TestCase):
    """`parse_corpus` exists so 68 chapters are read once per chapter and not
    once per finding. It may not change what the sweep reports."""

    def test_a_preparsed_corpus_gives_the_identical_hits(self):
        other = {"study-018-x": {"flashcards.json": json.dumps(
            {"cards": [{"front": "Atomoxetine onset?",
                        "back": "מגיעה תוך 2-8 שבועות"}]}, ensure_ascii=False)}}
        direct = qc_fix.old_value_hits(finding(), chapter(), other, tag=TAG)
        corpus = qc_fix.parse_corpus(chapter(), other, tag=TAG)
        viacorpus = qc_fix.old_value_hits(finding(), {}, tag=TAG, corpus=corpus)
        self.assertEqual(direct, viacorpus)
        self.assertTrue(direct)


if __name__ == "__main__":
    unittest.main()
