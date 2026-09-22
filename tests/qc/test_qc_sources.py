#!/usr/bin/env python3
"""Offline tests for qc_sources: the bundle a judge has to prove it read.

The measured case this whole module exists for is chapter 019 Q1. The item
cites StahlKids p.74 ("continue to improve for 8-12 weeks") and its key says
"2-8 שבועות", which is p.69. A bundle built only from the cited page would let
the judge call the item unsupported and cost an owner review; a bundle built
from every page carrying `2|8` would carry twelve StahlKids pages about other
drugs. So `test_search_finds_the_conflicting_page` asserts both 69 and 74 are
in Q1's call, and nothing else asserts a page count — the numbers move when the
books are re-cached.

Nothing here runs agy, gh or git. The tests that need a real page mapping read
it through qc_books at runtime and skip when the PDFs are not on this machine;
textbook text is never copied into fixtures. The rest fake the books with an
injected page map, the way tests/qc/test_qc_cite.py does.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import contextlib
import io
import json
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import qc_books  # noqa: E402
import qc_cite  # noqa: E402
import qc_sources  # noqa: E402
import qc_units  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
TAG_019 = "study-019-adhd-treatment-non-stimulants"
DIR_019 = FIX / "quiz" / TAG_019
DAY_019 = 19


def books_available() -> bool:
    try:
        qc_books.book("StahlKids")
        qc_books.book("Dulcan")
        return True
    except Exception:                                        # noqa: BLE001
        return False


HAVE_BOOKS = books_available()
needs_books = unittest.skipUnless(
    HAVE_BOOKS, "Text BOOKS/ not on this machine (qc_books cache is cold)")


def fixture_units():
    assets = {p.name: p.read_bytes().decode("utf-8")
              for p in sorted(DIR_019.iterdir())
              if qc_units.is_judged_asset(p.name)}
    units = qc_units.units(assets)
    return units, qc_cite.cites_by_unit(units)


def unit(uid: str, artifact: str, text: str) -> qc_units.Unit:
    return qc_units.Unit(uid=uid, artifact=artifact, text=text,
                         stable_id=uid.lower(), meta={})


# ---------------------------------------------------------------------------
# fakes


class FakeBook:
    """Enough of qc_books.Book to plan with, with no PDFs on the machine.

    `stride` is how many PDF pages a printed page spans: 1 for a laid-out book
    (Stahl), 2 for a reflowed one (DSM, Dulcan), where the limit has to count
    PDF pages or a 120-page bundle would arrive with 240 pages in it.
    """

    def __init__(self, key, max_printed=1000, stride=1, text=""):
        self.key, self.max_printed, self.stride, self._text = (
            key, max_printed, stride, text)

    def has_page(self, printed):
        return 1 <= printed <= self.max_printed

    def pdf_pages(self, printed):
        base = (printed - 1) * self.stride
        return list(range(base, base + self.stride))

    def page_text(self, printed):
        return self._text


class FakeBooksMixin:
    """qc_books.book -> FakeBook for the duration of one test."""
    STRIDE = {"DSM-5-TR": 2, "Dulcan": 2}

    def fake_books(self, **kw):
        real = qc_books.book
        books = {}

        def fake(key):
            if key not in books:
                books[key] = FakeBook(key, stride=self.STRIDE.get(key, 1), **kw)
            return books[key]

        qc_books.book = fake
        self.addCleanup(lambda: setattr(qc_books, "book", real))
        return books

    def fake_guide(self, reading: dict, day: int = 1) -> Path:
        d = Path(tempfile.mkdtemp())
        path = d / "reading_guide.json"
        path.write_text(json.dumps({"chapters": {str(day): {"reading": reading}}}),
                        encoding="utf-8")
        return path

    def no_synopsis(self):
        real = qc_sources.synopsis_pages
        qc_sources.synopsis_pages = lambda day, notes=None: {}
        self.addCleanup(lambda: setattr(qc_sources, "synopsis_pages", real))


# ---------------------------------------------------------------------------
# the reading guide


class ReadingRangesTest(unittest.TestCase):
    """Every string here is copied out of schedule/reading_guide.json."""

    def test_chapter_numbers_are_not_pages(self):
        # "פרק 1+4+5" is chapters 1, 4 and 5 of Dulcan, not pages 1, 4 and 5.
        self.assertEqual(qc_sources.reading_ranges("פרק 1+4+5, עמ' 3-16, 59-90"),
                         [(3, 16), (59, 90)])

    def test_parenthesised_list(self):
        self.assertEqual(
            qc_sources.reading_ranges(
                "atomoxetine, clonidine, guanfacine, bupropion "
                "(עמ' 69-96, 141-154, 243-254)"),
            [(69, 96), (141, 154), (243, 254)])

    def test_single_range_and_english_lead(self):
        self.assertEqual(qc_sources.reading_ranges("עמ' 283-285"), [(283, 285)])
        self.assertEqual(
            qc_sources.reading_ranges("Personality Disorders, עמ' 733-778"),
            [(733, 778)])

    def test_no_page_word_no_pages(self):
        # The Synopsis is cited by chapter; it has no printed pages at all.
        self.assertEqual(qc_sources.reading_ranges("פרק 1 (ספרות מחייבת)"), [])
        self.assertEqual(qc_sources.reading_ranges(""), [])

    def test_geresh_variant(self):
        self.assertEqual(qc_sources.reading_ranges("עמ׳ 12"), [(12, 12)])


class GuidePagesTest(FakeBooksMixin, unittest.TestCase):
    def test_book_names_as_a_person_writes_them(self):
        self.fake_books()
        guide = self.fake_guide({
            "Stahl Essential": "פרק 1, עמ' 1-3",
            "Stahl C&A": "atomoxetine (עמ' 69-70)",
            "כרטיסיות": "עמ' 283-284",
            "Synopsis": "פרק 1 (ספרות מחייבת)",     # no printed pages
            "note": "אין פרק ייעודי ב-Dulcan",       # not a book
        }, day=1)
        pages = qc_sources.guide_pages(1, path=guide)
        self.assertEqual(sorted(pages),
                         [("Kartisiot", 283), ("Kartisiot", 284),
                          ("StahlKids", 69), ("StahlKids", 70),
                          ("StahlNeuro", 1), ("StahlNeuro", 2), ("StahlNeuro", 3)])
        self.assertTrue(all(p["reason"] == "guide" for p in pages.values()))

    def test_absurd_range_is_dropped_with_a_note(self):
        self.fake_books()
        guide = self.fake_guide({"Dulcan": "עמ' 3-1600"}, day=1)
        notes = []
        self.assertEqual(qc_sources.guide_pages(1, path=guide, notes=notes), {})
        self.assertTrue(any("typo" in n for n in notes), notes)

    def test_missing_guide_is_a_note_not_a_crash(self):
        notes = []
        self.assertEqual(
            qc_sources.guide_pages(1, path=Path("nope/reading_guide.json"),
                                   notes=notes), {})
        self.assertTrue(notes)


# ---------------------------------------------------------------------------
# cited pages


class CitedPagesTest(FakeBooksMixin, unittest.TestCase):
    def test_radius_one(self):
        # The Dulcan CAMS sentence that contradicts 038 Q9 runs across printed
        # pages 330 and 331 and is on neither page alone.
        self.fake_books()
        pages = qc_sources.cited_pages(qc_cite.parse("(Dulcan p.330)"))
        self.assertEqual(sorted(p for _, p in pages), [329, 330, 331])

    def test_page_past_the_end_is_not_a_page(self):
        self.fake_books(max_printed=624)
        pages = qc_sources.cited_pages(qc_cite.parse("(StahlNeuro p. 289, 902)"))
        self.assertEqual(sorted(p for _, p in pages), [288, 289, 290])

    def test_ambiguous_stahl_brings_both_books(self):
        # qc_cite reports 'Stahl?' when neither Stahl book can be ruled out.
        # The judge is the only one that can tell from the content which was
        # meant, and only if it sees both.
        self.fake_books()
        pages = qc_sources.cited_pages(
            [{"book": "Stahl?", "printed": 100, "span": (0, 3)}])
        self.assertEqual(sorted({b for b, _ in pages}),
                         ["StahlKids", "StahlNeuro"])

    def test_section_only_reference_has_no_page(self):
        self.fake_books()
        self.assertEqual(qc_sources.cited_pages(qc_cite.parse("(Synopsis ch.9)")),
                         {})


# ---------------------------------------------------------------------------
# splitting


class SplitTest(FakeBooksMixin, unittest.TestCase):
    """§5.7: never drop a page, and keep every unit with its own evidence."""

    def setUp(self):
        self.fake_books()
        self.no_synopsis()
        self.guide = self.fake_guide({"Dulcan": "עמ' 500-520"}, day=1)

    def units(self, n, first=1, step=40):
        # Each item cites one Dulcan page, so each needs three printed pages
        # (+-1) and, Dulcan being reflowed, six PDF pages of its own.
        return [unit(f"Q{i + 1}", "quiz.json",
                     f"שאלה {i + 1} (Dulcan p.{first + i * step})")
                for i in range(n)]

    def plan(self, units, limit):
        cites = qc_cite.cites_by_unit(units)
        return qc_sources.plan("study-001-x", 1, units, cites, page_limit=limit,
                               reading_guide=self.guide, search=False)

    def all_pages(self, calls):
        return {(p["book"], p["printed"]) for c in calls for p in c.pages}

    def test_one_call_while_it_fits(self):
        calls = self.plan(self.units(3), 200)
        self.assertEqual([c.call_id for c in calls], ["A1"])
        self.assertEqual(calls[0].units, ["Q1", "Q2", "Q3"])

    def test_split_respects_the_limit_and_loses_no_page(self):
        units = self.units(10)
        whole = self.plan(units, 10_000)
        split = self.plan(units, 60)
        self.assertGreater(len(split), 1)
        self.assertEqual(self.all_pages(split), self.all_pages(whole))
        for c in split:
            self.assertLessEqual(c.pdf_page_count, 60, c.brief())

    def test_every_unit_lands_in_exactly_one_call_with_its_own_pages(self):
        units = self.units(10)
        calls = self.plan(units, 60)
        placed = [u for c in calls for u in c.units]
        self.assertEqual(sorted(placed), sorted(u.uid for u in units))
        self.assertEqual(len(placed), len(set(placed)))
        by_uid = {u.uid: u for u in units}
        for call in calls:
            have = {(p["book"], p["printed"]) for p in call.pages}
            for uid in call.units:
                mine = qc_sources.cited_pages(qc_cite.parse(by_uid[uid].text))
                self.assertTrue(set(mine) <= have, f"{uid} lost its cited pages")

    def test_a_unit_bigger_than_the_limit_gets_its_own_call(self):
        # 20 cited pages -> 60 printed -> 120 pdf pages, past any sane limit.
        fat = unit("Q1", "quiz.json",
                   " ".join(f"(Dulcan p.{100 + 10 * i})" for i in range(20)))
        small = unit("Q2", "quiz.json", "(Dulcan p.700)")
        calls = self.plan([fat, small], 60)
        owner = [c for c in calls if "Q1" in c.units][0]
        self.assertEqual(owner.units, ["Q1"])
        self.assertTrue(any("call of its own" in n for n in owner.notes),
                        owner.notes)

    def test_context_is_distributed_not_duplicated(self):
        units = self.units(10)
        calls = self.plan(units, 60)
        seen = []
        for c in calls:
            seen += [(p["book"], p["printed"]) for p in c.pages
                     if p["reason"] == "guide"]
        self.assertTrue(seen, "the reading guide never reached a bundle")
        self.assertEqual(len(seen), len(set(seen)),
                         "a context page was copied into two bundles")

    def test_kinds_are_separate_calls(self):
        units = [unit("Q1", "quiz.json", "(Dulcan p.100)"),
                 unit("C1", "flashcards.json", "(Dulcan p.100)"),
                 unit("G:x", "study_guide.md", "(Dulcan p.100)"),
                 unit("F:a.b", "facts.json", "(Dulcan p.100)"),
                 unit("L:sketchy_main.txt:1", "sketchy_main.txt", "(Dulcan p.100)")]
        calls = self.plan(units, 500)
        self.assertEqual({c.kind for c in calls}, {"A", "B", "C"})
        kinds = {c.kind: c.units for c in calls}
        self.assertEqual(sorted(kinds["A"]), ["C1", "Q1"])
        self.assertEqual(sorted(kinds["B"]), ["F:a.b", "G:x"])
        self.assertEqual(kinds["C"], ["L:sketchy_main.txt:1"])

    def test_unknown_artifact_fails_closed(self):
        with self.assertRaises(qc_sources.PlanError):
            self.plan([unit("X1", "video.mp4", "(Dulcan p.100)")], 200)


class SynopsisCallTest(FakeBooksMixin, unittest.TestCase):
    def setUp(self):
        self.fake_books()
        self.guide = self.fake_guide({}, day=70)
        self.real_synopsis = qc_sources.synopsis_pages
        self.addCleanup(
            lambda: setattr(qc_sources, "synopsis_pages", self.real_synopsis))

    def fake_slice(self, pages):
        slice_pages = {("Synopsis", n): qc_sources._page(
            "Synopsis", n, [n - 1], "synopsis", slice_ref="22",
            slice_file="synopsis-22-ect.pdf", slice_path="x.pdf")
            for n in range(1, pages + 1)}
        qc_sources.synopsis_pages = lambda day, notes=None: dict(slice_pages)

    def test_a_slice_bigger_than_the_budget_is_one_call_not_twenty(self):
        # synopsis-1_1-adult-patient.pdf is 215 pages. Splitting its units would
        # make a call per unit, each still carrying all 215 pages.
        self.fake_slice(215)
        units = [unit(f"Q{i}", "quiz.json", f"שאלה {i} (Synopsis ch.22)")
                 for i in range(1, 9)]
        calls = qc_sources.plan("study-070-ect", 70, units,
                                qc_cite.cites_by_unit(units), page_limit=180,
                                reading_guide=self.guide, search=False)
        syn = [c for c in calls if c.kind == "S"]
        self.assertEqual(len(syn), 1)
        self.assertEqual(len(syn[0].units), 8)
        self.assertTrue(any("splitting cannot make a slice smaller" in n
                            for n in syn[0].notes), syn[0].notes)

    def test_synopsis_only_chapter_gets_its_own_call(self):
        # §5.8: chapters 013 and 070 cite nothing but the Synopsis.
        self.fake_slice(5)
        units = [unit("Q1", "quiz.json", "(Synopsis ch.22)"),
                 unit("Q2", "quiz.json", "(Dulcan p.400)")]
        calls = qc_sources.plan("study-070-ect", 70, units,
                                qc_cite.cites_by_unit(units), page_limit=200,
                                reading_guide=self.guide, search=False)
        kinds = {c.kind: c for c in calls}
        self.assertIn("S", kinds)
        self.assertEqual(kinds["S"].units, ["Q1"])
        self.assertTrue(any(p["book"] == "Synopsis" for p in kinds["S"].pages))
        # Q2 cites a real page, so it stays in the ordinary quiz call with it.
        self.assertEqual(kinds["A"].units, ["Q2"])
        self.assertTrue(any(p["book"] == "Dulcan" for p in kinds["A"].pages))


# ---------------------------------------------------------------------------
# artifacts.md


class ArtifactsMdTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def units(self, n=12):
        return [unit(f"Q{i + 1}", "quiz.json",
                     f"שאלה {i + 1}\n" + ("טקסט ארוך למדי כדי למלא קובץ. " * 8))
                for i in range(n)]

    def test_text_is_verbatim_under_its_uid(self):
        units = self.units()
        path, nonce = qc_sources.build_artifacts_md(units, self.dir)
        body = path.read_bytes().decode("utf-8")
        for u in units:
            self.assertIn(f"### {u.uid}\n", body)
            # §5.8's coverage check searches Unit.text for the judge's echo. If
            # the block re-wrapped the text, every row of the call would fail.
            self.assertIn(u.text, body, u.uid)
        self.assertIn(nonce, body)

    def test_nonce_lands_in_the_last_tenth(self):
        rng = random.Random(7)
        for _ in range(20):
            path, nonce = qc_sources.build_artifacts_md(self.units(), self.dir,
                                                        rng=rng)
            body = path.read_bytes().decode("utf-8")
            at = body.index(qc_sources.NONCE_MARKER)
            self.assertGreaterEqual(at / len(body), 1 - qc_sources.NONCE_TAIL_FRACTION,
                                    f"nonce at {at / len(body):.0%}")
            self.assertEqual(body.count(nonce), 1)

    def test_nonce_never_lands_inside_a_unit(self):
        # A line dropped into the middle of Q7 would break any echo spanning it,
        # and the judge would be punished for the file we wrote.
        rng = random.Random(3)
        units = self.units()
        for _ in range(20):
            path, _ = qc_sources.build_artifacts_md(units, self.dir, rng=rng)
            body = path.read_bytes().decode("utf-8")
            for u in units:
                self.assertIn(u.text, body)

    def test_one_unit_still_gets_a_nonce(self):
        path, nonce = qc_sources.build_artifacts_md(self.units(1), self.dir)
        self.assertIn(nonce, path.read_bytes().decode("utf-8"))

    def test_no_units_is_an_error(self):
        with self.assertRaises(qc_sources.PlanError):
            qc_sources.build_artifacts_md([], self.dir)

    def test_crlf_is_never_written(self):
        # The bytes agy reads must be the bytes whose sha256 the verdict records;
        # write_text() on Windows would turn every "\n" into "\r\n".
        path, _ = qc_sources.build_artifacts_md(self.units(3), self.dir)
        self.assertNotIn(b"\r\n", path.read_bytes())


# ---------------------------------------------------------------------------
# canary


def fake_manifest(spec, page_count=None):
    """spec: [(book, printed)] in bundle order."""
    pages = [{"bundle_page": i + 2, "book": b, "printed": [p], "pdf_page": i,
              "reason": "cited", "reasons": ["cited"], "code": f"C{i:05d}"}
             for i, (b, p) in enumerate(spec)]
    return {"call_id": "A1", "kind": "A", "pages": pages, "index_pages": 1,
            "page_count": page_count or len(pages) + 1}


class CanaryTest(unittest.TestCase):
    def spec(self):
        return ([("DSM-5-TR", 100 + i) for i in range(40)]
                + [("Dulcan", 300 + i) for i in range(40)]
                + [("StahlKids", 60 + i) for i in range(20)])

    def test_one_page_per_book_and_one_in_the_tail(self):
        man = fake_manifest(self.spec())
        rng = random.Random(11)
        for _ in range(50):
            asked = qc_sources.canary_request(man, k=4, rng=rng)
            self.assertGreaterEqual(len(asked), 4)
            self.assertEqual({b for b, _, _ in asked},
                             {"DSM-5-TR", "Dulcan", "StahlKids"})
            by_code = {p["code"]: p for p in man["pages"]}
            for book, printed, code in asked:
                self.assertEqual(by_code[code]["book"], book)
                self.assertEqual(by_code[code]["printed"][0], printed)
            tail = man["pages"][int(len(man["pages"]) * 0.9):]
            first_tail = tail[0]["bundle_page"]
            self.assertTrue(
                any(by_code[c]["bundle_page"] >= first_tail for _, _, c in asked),
                "no canary page in the last 10% of the bundle")

    def test_more_books_than_k(self):
        man = fake_manifest([(b, 1) for b in
                             ("DSM-5-TR", "Dulcan", "StahlNeuro", "StahlKids",
                              "Syllabus", "Kartisiot")])
        asked = qc_sources.canary_request(man, k=2, rng=random.Random(1))
        self.assertEqual(len(asked), 6)

    def test_empty_bundle_is_an_error(self):
        with self.assertRaises(qc_sources.PlanError):
            qc_sources.canary_request({"pages": []})


class BundleGuardTest(unittest.TestCase):
    """The two ways build_bundle refuses rather than writing a useless PDF."""

    def test_no_pages_is_an_error(self):
        empty = qc_sources.CallPlan(call_id="A1", kind="A", units=["Q1"])
        with self.assertRaises(qc_sources.PlanError):
            qc_sources.build_bundle(empty, tempfile.mkdtemp())

    def test_a_synopsis_page_without_its_slice_is_an_error(self):
        # The Synopsis has no printed pages, so a page of it exists only inside
        # a slice PDF; without one there is nothing to copy.
        page = qc_sources._page("Synopsis", 3, [2], "synopsis", slice_ref="9")
        call = qc_sources.CallPlan(call_id="S1", kind="S", units=["Q1"],
                                   pages=[page])
        with self.assertRaises(qc_sources.PlanError):
            qc_sources.build_bundle(call, tempfile.mkdtemp())


class DayOfTagTest(unittest.TestCase):
    def test_reads_the_schedule_index(self):
        self.assertEqual(qc_sources.day_of_tag(TAG_019), 19)
        self.assertEqual(
            qc_sources.day_of_tag("study-001-psychiatric-examination"), 1)

    def test_a_tag_it_cannot_read_is_an_error(self):
        with self.assertRaises(qc_sources.PlanError):
            qc_sources.day_of_tag("v1.2.3")


# ---------------------------------------------------------------------------
# with the real books


@needs_books
class RealChapterPlanTest(unittest.TestCase):
    def setUp(self):
        self.units, self.cites = fixture_units()

    def test_search_finds_the_conflicting_page(self):
        """StahlKids p.69 ("2-8 weeks") for an item that cites p.74 ("8-12").

        This is the source conflict the spec names in §1, and the reason the
        bundle carries full-text hits at all: p.69 is not cited anywhere in the
        item, so only the value-plus-key-term search puts it in front of the
        judge.
        """
        q1 = next(u for u in self.units if u.uid == "Q1")
        rows = self.cites["Q1"]
        cited = qc_sources.cited_pages(rows)
        found = qc_sources.search_pages(q1.text, rows)
        self.assertIn(("StahlKids", 74), cited)
        self.assertIn(("StahlKids", 69), found)

        calls = qc_sources.plan(TAG_019, DAY_019, self.units, self.cites,
                                page_limit=180)
        owner = next(c for c in calls if "Q1" in c.units)
        have = {(p["book"], p["printed"]) for p in owner.pages}
        self.assertIn(("StahlKids", 69), have)
        self.assertIn(("StahlKids", 74), have)

    def test_a_bare_value_alone_is_not_a_hit(self):
        # §5.7: "A bare value match is not enough (StahlKids repeats 'may take
        # 2-8 weeks' on 12 pages)." With no name on the unit there is no window.
        self.assertEqual(qc_sources.search_pages("הערך הוא 2-8 שבועות", []), {})

    def test_split_loses_no_page(self):
        whole = qc_sources.plan(TAG_019, DAY_019, self.units, self.cites,
                                page_limit=10_000)
        split = qc_sources.plan(TAG_019, DAY_019, self.units, self.cites,
                                page_limit=120)
        self.assertEqual(len(whole), 1)
        self.assertGreater(len(split), 1)
        keys = lambda calls: {(p["book"], p["printed"])                # noqa: E731
                              for c in calls for p in c.pages}
        self.assertEqual(keys(split), keys(whole))
        for c in split:
            self.assertLessEqual(c.pdf_page_count, 120, c.brief())

    def test_reading_guide_pages_reach_the_plan(self):
        calls = qc_sources.plan(TAG_019, DAY_019, self.units, self.cites,
                                page_limit=180)
        reasons = {p["reason"] for c in calls for p in c.pages}
        self.assertEqual(reasons, {"cited", "search", "guide"})


@needs_books
class BundleTest(unittest.TestCase):
    """One real bundle, built small: the reading guide is replaced by a
    three-page range so the test costs seconds rather than a 170-page PDF."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        guide = Path(cls.tmp) / "reading_guide.json"
        guide.write_text(json.dumps({"chapters": {str(DAY_019): {"reading": {
            "כרטיסיות": "עמ' 133-135"}}}}), encoding="utf-8")
        cls.real_guide = qc_sources.READING_GUIDE
        qc_sources.READING_GUIDE = guide
        units, cites = fixture_units()
        # build_calls logs a line per call for the 24-hour run's log; captured
        # here so the suite's output stays readable.
        with contextlib.redirect_stdout(io.StringIO()):
            cls.calls = qc_sources.build_calls(
                tag=TAG_019, day=DAY_019, units=units, cites=cites,
                work_dir=Path(cls.tmp) / "work", page_limit=180, search=False,
                rng=random.Random(5))

    @classmethod
    def tearDownClass(cls):
        qc_sources.READING_GUIDE = cls.real_guide

    def test_every_stamped_code_is_extractable_and_matches_the_manifest(self):
        import fitz
        for call in self.calls:
            man = call.manifest
            with fitz.open(call.bundle) as doc:
                self.assertEqual(doc.page_count, man["page_count"])
                for page in man["pages"]:
                    text = doc[page["bundle_page"] - 1].get_text()
                    self.assertIn(page["code"], text,
                                  f"{call.call_id} p.{page['bundle_page']}")
            codes = [p["code"] for p in man["pages"]]
            self.assertEqual(len(codes), len(set(codes)), "a code was reused")

    def test_the_index_lists_every_page(self):
        import fitz
        for call in self.calls:
            man = call.manifest
            with fitz.open(call.bundle) as doc:
                index = "".join(doc[i].get_text()
                                for i in range(man["index_pages"]))
            listed = sorted(int(n) for n in re.findall(r"(?m)^(\d+)\.\s", index))
            self.assertEqual(listed,
                             sorted(p["bundle_page"] for p in man["pages"]))
            # ...and NOT the codes, or the canary could be answered from page 1.
            for page in man["pages"]:
                self.assertNotIn(page["code"], index)

    def test_the_stamp_names_the_book(self):
        import fitz
        # Arial's ToUnicode map returns U+00AD for the hyphen and U+00A0 for the
        # space, so the comparison is on the letters, not on the exact string.
        squash = lambda s: re.sub(r"[^0-9A-Za-z֐-׿]", "", s)  # noqa: E731
        for call in self.calls:
            with fitz.open(call.bundle) as doc:
                for page in call.manifest["pages"]:
                    text = squash(doc[page["bundle_page"] - 1].get_text())
                    name = (qc_books.HEBREW_NAME.get(page["book"], page["book"]))
                    self.assertIn(squash(name), text, page["book"])
                    self.assertIn(squash(f"p.{page['printed'][0]}"), text)

    def test_kartisiot_is_stamped_with_its_hebrew_name(self):
        # Owner decision, §2.4: anything grounded in סיכומי כרטיסיות שלב ב must
        # say so, and the judge can only say it if the page does.
        import fitz
        pages = [(c, p) for c in self.calls for p in c.manifest["pages"]
                 if p["book"] == "Kartisiot"]
        self.assertTrue(pages, "the trimmed reading guide should add Kartisiot")
        call, page = pages[0]
        with fitz.open(call.bundle) as doc:
            text = doc[page["bundle_page"] - 1].get_text().replace(" ", " ")
        self.assertIn("סיכומי כרטיסיות שלב ב", text)

    def test_manifest_describes_the_file_on_disk(self):
        import hashlib
        for call in self.calls:
            data = Path(call.bundle).read_bytes()
            self.assertEqual(call.manifest["bytes"], len(data))
            self.assertEqual(call.manifest["sha256"],
                             hashlib.sha256(data).hexdigest())
            self.assertTrue(call.manifest["stamps_verified"])
            for key, book in call.manifest["books"].items():
                self.assertEqual(len(book["sha256"]), 64)
                if ":" not in key:          # "Synopsis:<slice>.pdf" is a slice
                    self.assertEqual(book["file"], qc_books.book_file(key).name)

    def test_printed_to_pdf_pages_survive_into_the_manifest(self):
        for call in self.calls:
            for page in call.manifest["pages"]:
                self.assertTrue(page["printed"])
                self.assertIsInstance(page["pdf_page"], int)
                self.assertIn(page["reason"],
                              ("cited", "guide", "search", "synopsis"))

    def test_artifacts_md_holds_every_unit_of_its_call(self):
        by_uid = {u.uid: u for u in fixture_units()[0]}
        for call in self.calls:
            body = Path(call.artifacts).read_bytes().decode("utf-8")
            self.assertIn(call.nonce, body)
            self.assertEqual(call.manifest["nonce"], call.nonce)
            for uid in call.units:
                self.assertIn(f"### {uid}\n", body)
                self.assertIn(by_uid[uid].text, body)

    def test_canary_on_the_real_manifest(self):
        for call in self.calls:
            asked = qc_sources.canary_request(call.manifest, k=4,
                                              rng=random.Random(2))
            by_code = {p["code"]: p for p in call.manifest["pages"]}
            books = {p["book"] for p in call.manifest["pages"]}
            self.assertEqual({b for b, _, _ in asked}, books)
            for book, printed, code in asked:
                self.assertEqual(by_code[code]["book"], book)
                self.assertIn(printed, by_code[code]["printed"])


if __name__ == "__main__":
    unittest.main()
