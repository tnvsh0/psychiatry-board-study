#!/usr/bin/env python3
"""Offline tests for qc_cite: the strings that beat the old page gate.

Every case is a real form from the released corpus. The two that matter most:
`(StahlNeuro p. 289, 902)` in chapter 029's guide, where the old check read only
289 and let a page that does not exist in a 624-page book through, and the
source-map table that opens every guide, where the book is the column header and
the cell is bare numbers — invisible to a parser that looks for "Dulcan p.".

Book lookups are faked with an injected page map, so the parser is tested with
no PDFs on the machine. The two tests that must see the real mapping
(`impossible` against StahlNeuro's 624 pages) skip when the books are missing;
textbook text is never copied into fixtures.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import qc_books  # noqa: E402
import qc_cite  # noqa: E402
import qc_units  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
GUIDE_029 = FIX / "text" / "study-029-ssris-deep-dive" / "study_guide.md"

# The printed ranges measured by qc_books --check on 2026-09-18. Used as a fake
# so the parser's page arithmetic is testable without the PDFs.
MAX_PRINTED = {"DSM-5-TR": 1050, "Dulcan": 1156, "StahlNeuro": 624,
               "StahlKids": 509, "Syllabus": 59, "Kartisiot": 310}


class FakeBook:
    def __init__(self, key):
        self.key = key
        self.max_printed = MAX_PRINTED[key]

    def has_page(self, printed):
        return 1 <= printed <= self.max_printed


def books_available() -> bool:
    try:
        qc_books.book("StahlNeuro")
        return True
    except Exception:
        return False


def pages(cites, book=None):
    return [c["printed"] for c in cites
            if c["printed"] is not None and (book is None or c["book"] == book)]


class FakeBooksMixin:
    """Replace qc_books.book for the length of a test — parse() resolves a bare
    'Stahl' through it, and impossible() reads it directly."""

    def setUp(self):
        self._real = qc_books.book
        qc_books.book = FakeBook
        self.addCleanup(lambda: setattr(qc_books, "book", self._real))


class TestPageLists(FakeBooksMixin, unittest.TestCase):
    def test_every_page_in_a_list(self):
        c = qc_cite.parse("הגדרתן ההיסטורית (StahlNeuro p. 289, 902).", unit="G:1")
        self.assertEqual(pages(c), [289, 902])
        self.assertEqual({x["book"] for x in c}, {"StahlNeuro"})
        self.assertEqual({x["unit"] for x in c}, {"G:1"})
        self.assertEqual({x["raw"] for x in c}, {"StahlNeuro p. 289, 902"})

    def test_repeated_page_word_in_one_list(self):
        c = qc_cite.parse("(StahlKids p. 141, p. 243, p. 711)")
        self.assertEqual(pages(c), [141, 243, 711])

    def test_two_books_in_one_parenthesis(self):
        c = qc_cite.parse("(Dulcan p. 182, DSM-5-TR p. 71, Stahl Essential p. 465)")
        self.assertEqual([(x["book"], x["printed"]) for x in c],
                         [("Dulcan", 182), ("DSM-5-TR", 71), ("StahlNeuro", 465)])

    def test_semicolon_separated_books(self):
        c = qc_cite.parse("(Dulcan p. 715-716; StahlKids p. 70, 72)")
        self.assertEqual(pages(c, "Dulcan"), [715, 716])
        self.assertEqual(pages(c, "StahlKids"), [70, 72])

    def test_hebrew_page_word(self):
        self.assertEqual(pages(qc_cite.parse("(StahlNeuro עמ' 506)")), [506])
        self.assertEqual(pages(qc_cite.parse("(כרטיסיות עמ' 12)")), [12])
        self.assertEqual(qc_cite.parse("(כרטיסיות עמ' 12)")[0]["book"], "Kartisiot")

    def test_hebrew_page_word_does_not_eat_a_longer_word(self):
        # "עמ" is a page only standing alone: עמוד and עמודים are page words
        # too, but עמיתים (colleagues) merely starts with the same two letters.
        self.assertEqual(pages(qc_cite.parse("Dulcan עמוד 29")), [29])
        self.assertEqual(pages(qc_cite.parse("Dulcan עמודים 29-30")), [29, 30])
        self.assertEqual(qc_cite.parse("Dulcan עמיתים 29"), [])

    def test_span_locates_the_number_and_raw_keeps_the_citation(self):
        text = "abc (Dulcan p. 330) def"
        c = qc_cite.parse(text)[0]
        self.assertEqual(text[c["span"][0]:c["span"][1]], "330")
        self.assertEqual(c["raw"], "Dulcan p. 330")


class TestRanges(FakeBooksMixin, unittest.TestCase):
    def test_range_expands(self):
        self.assertEqual(pages(qc_cite.parse("(Dulcan pp. 12-15)")),
                         [12, 13, 14, 15])

    def test_en_dash_range(self):
        self.assertEqual(pages(qc_cite.parse("(DSM-5-TR p.313–315)")),
                         [313, 314, 315])

    def test_long_range_keeps_only_its_endpoints(self):
        # A 30-page "range" is a typo or a chapter span; expanding it would put
        # 28 unrelated pages into the judge's bundle.
        self.assertEqual(pages(qc_cite.parse("(Dulcan pp. 300-340)")), [300, 340])

    def test_range_at_the_cap(self):
        self.assertEqual(len(pages(qc_cite.parse("(Dulcan pp. 300-309)"))), 10)
        self.assertEqual(pages(qc_cite.parse("(Dulcan pp. 300-310)")), [300, 310])


class TestYearsAreNotPages(FakeBooksMixin, unittest.TestCase):
    def test_parenthesised_year(self):
        self.assertEqual(qc_cite.parse("לפי DSM-5-TR (2022) ההגדרה"), [])

    def test_hebrew_year(self):
        self.assertEqual(qc_cite.parse("DSM-5 בשנת 2013 הופיע"), [])

    def test_a_year_before_a_real_page_does_not_hide_it(self):
        self.assertEqual(pages(qc_cite.parse("(DSM-5-TR, 2022, p. 135)")), [135])

    def test_a_book_named_without_a_page(self):
        self.assertEqual(
            qc_cite.parse("According to StahlNeuro, what happens to 260 ions?"), [])


class TestSections(FakeBooksMixin, unittest.TestCase):
    def test_kaplan_dotted_number_is_a_section(self):
        c = qc_cite.parse("עלייה (Kaplan & Sadock p.33.2).")
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["book"], "Synopsis")
        self.assertIsNone(c[0]["printed"])
        self.assertEqual(c[0]["section"], "33.2")

    def test_kaplan_hyphenated_number_is_a_section_not_a_range(self):
        c = qc_cite.parse("(Kaplan & Sadock p. 2-16).")
        self.assertEqual([(x["printed"], x["section"]) for x in c],
                         [(None, "2-16")])

    def test_synopsis_chapter(self):
        c = qc_cite.parse("ADHD `(Synopsis ch.10 Trauma- and Stressor-Related)`")
        self.assertEqual([(x["book"], x["section"]) for x in c],
                         [("Synopsis", "10")])

    def test_chapter_and_page_together(self):
        c = qc_cite.parse("(Dulcan ch.7 p.412)")
        self.assertEqual([(x["book"], x["printed"], x["section"]) for x in c],
                         [("Dulcan", 412, "7")])

    def test_sections_are_never_impossible(self):
        c = qc_cite.parse("(Synopsis ch.99) (Kaplan & Sadock p.33.2)")
        self.assertEqual(qc_cite.impossible(c), [])


class TestBareStahl(FakeBooksMixin, unittest.TestCase):
    def test_resolved_when_only_one_stahl_has_the_page(self):
        c = qc_cite.parse("(Stahl p. 601)")[0]
        self.assertEqual(c["book"], "StahlNeuro")       # StahlKids ends at 509
        self.assertFalse(c["ambiguous"])

    def test_ambiguous_when_both_have_the_page(self):
        c = qc_cite.parse("(Stahl p. 100)")[0]
        self.assertEqual(c["book"], "Stahl?")
        self.assertTrue(c["ambiguous"])

    def test_ambiguous_page_is_impossible_only_past_the_larger_book(self):
        # Resolving a bare "Stahl" to the smaller book flagged 117 of chapter
        # 2's 134 citations; only 761 is genuinely impossible here.
        c = qc_cite.parse("(Stahl p. 761) and (Stahl p. 600)")
        self.assertEqual([x["printed"] for x in qc_cite.impossible(c)], [761])

    def test_named_stahl_books_are_never_ambiguous(self):
        for text in ("(StahlKids p. 69)", "(Stahl Essential p. 449)",
                     "(Stahl's Prescriber's Guide p. 74)", "(Stahl PG p. 601)"):
            with self.subTest(text=text):
                c = qc_cite.parse(text)
                self.assertEqual(len(c), 1)
                self.assertFalse(c[0]["ambiguous"], text)


class TestTables(FakeBooksMixin, unittest.TestCase):
    """The source-map table that opens every guide."""

    TABLE = (
        "| נושא משנה | DSM-5-TR עמ' | Dulcan עמ' | StahlNeuro עמ' |"
        " StahlKids עמ' | Synopsis פרק | סילבוס עמ' |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| **1. מבוא + הגדרה** | — | 732 | 289 | — | — | 4, 29 |\n"
        "| **2. drug_class** | 507-508 | 732, 736 | 292-296 | — | Synopsis ch.10"
        " | (מקור: הקשר רחב) |\n")

    def test_bare_numbers_take_the_column_s_book(self):
        c = qc_cite.parse(self.TABLE)
        got = {(x["book"], x["printed"]) for x in c if x["printed"] is not None}
        self.assertIn(("Dulcan", 732), got)
        self.assertIn(("StahlNeuro", 289), got)
        self.assertIn(("Syllabus", 4), got)
        self.assertIn(("Syllabus", 29), got)
        self.assertIn(("DSM-5-TR", 507), got)
        self.assertIn(("DSM-5-TR", 508), got)
        self.assertEqual(pages(c, "StahlNeuro"), [289, 292, 293, 294, 295, 296])

    def test_the_row_label_column_is_not_a_book(self):
        # "**1. מבוא**" and "**2. drug_class**" must not become pages 1 and 2.
        c = qc_cite.parse(self.TABLE)
        self.assertNotIn(1, pages(c))
        self.assertNotIn(2, pages(c))

    def test_an_explicit_citation_in_a_cell_is_not_double_counted(self):
        c = [x for x in qc_cite.parse(self.TABLE) if x["book"] == "Synopsis"]
        self.assertEqual([(x["printed"], x["section"]) for x in c], [(None, "10")])

    def test_a_cell_with_no_numbers_cites_nothing(self):
        self.assertNotIn("Syllabus",
                         {x["book"] for x in qc_cite.parse(self.TABLE)
                          if x["raw"].startswith("(מקור")})

    def test_prose_with_pipes_is_not_a_table(self):
        c = qc_cite.parse("| Dulcan is good | 732 is not a page |\n")
        self.assertEqual(c, [])

    def test_older_header_spellings(self):
        table = ("| נושא | Stahl Essential עמ' | Stahl Pediatric עמ' "
                 "(מתוך הסיכום) | כרטיסיות |\n| :--- | :--- | :--- | :--- |\n"
                 "| א | 449 | 74 | 12 |\n")
        c = qc_cite.parse(table)
        self.assertEqual([(x["book"], x["printed"]) for x in c],
                         [("StahlNeuro", 449), ("StahlKids", 74),
                          ("Kartisiot", 12)])


class TestLabels(unittest.TestCase):
    def test_agrees_with_qc_books_normalize_label(self):
        for raw in ("DSM-5-TR", "Dulcan", "StahlNeuro", "StahlKids", "Synopsis",
                    "סילבוס", "כרטיסיות", "Stahl"):
            with self.subTest(raw=raw):
                self.assertEqual(qc_cite.resolve_label(raw),
                                 qc_books.normalize_label(raw))

    def test_compound_names_qc_books_does_not_know(self):
        self.assertEqual(qc_cite.resolve_label("Stahl PG"), "StahlKids")
        self.assertEqual(qc_cite.resolve_label("Kaplan & Sadock"), "Synopsis")
        self.assertEqual(qc_cite.resolve_label("Dulcan's Textbook"), "Dulcan")
        self.assertIsNone(qc_cite.resolve_label("נושא משנה"))

    def test_every_key_is_a_qc_books_key(self):
        keys = {k for k, _ in qc_cite._BOOK_PATTERNS}
        self.assertEqual(keys - set(qc_books.SPECS), {"Synopsis", "Stahl?"})


class TestRealGuide(FakeBooksMixin, unittest.TestCase):
    """Chapter 029, the guide the old page gate passed."""

    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE_029.read_text(encoding="utf-8")

    def test_the_289_902_citation(self):
        c = [x for x in qc_cite.parse(self.text)
             if x["raw"] == "StahlNeuro p. 289, 902"]
        self.assertEqual(sorted(x["printed"] for x in c), [289, 902])

    def test_902_is_impossible_in_a_624_page_book(self):
        bad = qc_cite.impossible(qc_cite.parse(self.text))
        self.assertIn(("StahlNeuro", 902), {(x["book"], x["printed"]) for x in bad})
        self.assertNotIn(289, [x["printed"] for x in bad])

    def test_the_source_map_table_is_read(self):
        c = qc_cite.parse(self.text)
        # Every column of the guide's own source map contributes citations.
        self.assertEqual({x["book"] for x in c},
                         {"DSM-5-TR", "Dulcan", "StahlNeuro", "StahlKids",
                          "Syllabus"})
        self.assertGreater(len(c), 300)

    def test_cites_by_unit_keys_on_uids(self):
        units = qc_units.units_from_asset("study_guide.md", self.text)
        by_unit = qc_cite.cites_by_unit(units)
        self.assertEqual(set(by_unit), {u.uid for u in units})
        intro = by_unit["G:1. מבוא + הגדרה"]
        self.assertIn(902, [c["printed"] for c in intro])
        self.assertTrue(all(c["unit"] == "G:1. מבוא + הגדרה" for c in intro))


@unittest.skipUnless(books_available(), "Text BOOKS/ not on this machine")
class TestAgainstRealBooks(unittest.TestCase):
    """The same two verdicts, through the real page mapping rather than a fake."""

    def test_902_impossible_289_not(self):
        c = qc_cite.parse("(StahlNeuro p. 289, 902)")
        self.assertEqual([x["printed"] for x in qc_cite.impossible(c)], [902])

    def test_fake_max_printed_still_matches_the_books(self):
        for key, mx in MAX_PRINTED.items():
            with self.subTest(book=key):
                self.assertEqual(qc_books.book(key).max_printed, mx)


if __name__ == "__main__":
    unittest.main()
