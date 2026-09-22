#!/usr/bin/env python3
"""Offline tests for qc_judge: the answer is not believed until code finds it.

Nothing here runs agy, gh or git. `FakeAsk` stands in for
agy_attach.ask_with_files and — this is the part that matters — runs the
caller's own `validate` exactly where the real one does, so a canary miss in a
test costs an attempt and then the chapter, the same way it costs them in
production.

The one case that reads real book text is the acceptance case of spec §1:
chapter 019's atomoxetine onset, StahlKids p.69 ("2-8 weeks") against p.74
("8-12 weeks") — one book disagreeing with itself. It is read out of qc_books
at runtime and skipped when the PDFs are not on this machine; no textbook text
is copied into tests/qc/fixtures.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import random
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import qc_books  # noqa: E402
import qc_judge  # noqa: E402
import qc_norm  # noqa: E402
import qc_sources  # noqa: E402
import qc_units  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
TAG_019 = "study-019-adhd-treatment-non-stimulants"
DIR_019 = FIX / "quiz" / TAG_019


def books_available() -> bool:
    try:
        qc_books.book("StahlKids").page_text(69)
        return True
    except Exception:                                        # noqa: BLE001
        return False


needs_books = unittest.skipUnless(
    books_available(), "Text BOOKS/ not on this machine (qc_books cache is cold)")


def page_sentence(book: str, printed: int, pattern: str, before: int = 90,
                  after: int = 40) -> str:
    """A verbatim slice of a real printed page, read at runtime.

    Fixtures may not hold textbook text (copyright), and a quote invented here
    would prove nothing: the whole point of the source side is that the words
    are the book's. So the test pulls them out of qc_books the same way
    qc_judge will.
    """
    window = qc_norm.source_window(book, printed, 0)
    m = re.search(pattern, window)
    if not m:
        raise unittest.SkipTest(f"{book} p.{printed} no longer matches "
                                f"{pattern!r}; qc_books.PROBES covers the map")
    return window[max(0, m.start() - before):m.end() + after]


# ---------------------------------------------------------------------------
# the fakes


class FakeAsk:
    """agy_attach.ask_with_files with the agy taken out.

    Replays one scripted reply per attempt and runs `validate` on each, which
    is what makes "canary miss -> retry -> unjudged" testable at all: the retry
    loop lives inside ask_with_files, so a fake that ignored validate would
    make every rejection look like a first-attempt success.
    """

    def __init__(self, replies, *, conversation_id="cid-test"):
        self.replies = list(replies)
        self.conversation_id = conversation_id
        self.prompts: list[str] = []
        self.files: list[list[Path]] = []
        self.rejections: list[str] = []

    def __call__(self, prompt, files, *, model=None, attempts=3, validate=None,
                 tag="qc", call_id=None, **_kw):
        self.prompts.append(prompt)
        self.files.append([Path(f) for f in files])
        res = SimpleNamespace(data=None, text="", conversation_id=self.conversation_id,
                              duration_s=0.1, attempts=0, diagnostic="",
                              history=[], agy_version="fake-1.0",
                              tempmedia_ok=True, tool_audit=["view_file"],
                              workspace="ws", argv_len=100)
        while res.attempts < attempts:
            i = res.attempts
            res.attempts += 1
            data = self.replies[i] if i < len(self.replies) else self.replies[-1]
            res.text = repr(data)[:4000]
            why = validate(data) if validate else None
            if why:
                self.rejections.append(why)
                res.diagnostic = f"rejected: {why}"
                res.history.append(f"attempt {res.attempts}: {res.diagnostic}")
                continue
            res.data = data
            res.diagnostic = "ok"
            res.history.append(f"attempt {res.attempts}: ok")
            return res
        return res


NONCE = "QWERTYUIOPAS"
CODE_A, CODE_B = "AAA111", "BBB222"


def manifest(pages, *, units=("Q1",), nonce=NONCE) -> dict:
    """The shape qc_sources.build_bundle returns, hand-built.

    `printed` is a LIST because a reflowed DSM/Dulcan bundle page carries two
    printed pages, and an evidence code on such a page has to resolve to both.
    """
    return {"call_id": "A", "kind": "A", "units": list(units), "nonce": nonce,
            "books": {}, "index_pages": 1, "page_count": len(pages) + 1,
            "bytes": 1, "sha256": "0" * 64, "path": "bundle.pdf",
            "stamps_verified": True,
            "pages": [dict(p, bundle_page=i + 2) for i, p in enumerate(pages)]}


def page(book="StahlKids", printed=74, code=CODE_A, reason="cited") -> dict:
    return {"book": book, "printed": [printed], "pdf_page": printed + 15,
            "reason": reason, "reasons": [reason], "code": code}


def fixture_units(names=("quiz.json", "flashcards.json")):
    assets = {p.name: p.read_bytes().decode("utf-8")
              for p in sorted(DIR_019.iterdir()) if p.name in names}
    return qc_units.units(assets)


def reply(units, *, canary=(("StahlKids p.74", CODE_A),), nonce=NONCE,
          findings=(), statuses=None, echoes=None) -> dict:
    """A well-formed answer over `units` (a {uid: text} map)."""
    statuses = statuses or {}
    echoes = echoes or {}
    return {
        "canary": [{"page": label, "code": code} for label, code in canary],
        "nonce": nonce,
        "coverage": [{"unit": uid,
                      "status": statuses.get(uid, "supported"),
                      "echo": echoes.get(uid, text[:160]),
                      "evidence_code": CODE_A,
                      "source_quote": "irrelevant unless the unit has a value"}
                     for uid, text in units.items()],
        "findings": list(findings)}


class JudgeCase(unittest.TestCase):
    """One built call on disk: a stub bundle, a real artifacts.md, a manifest."""

    units_wanted = ("quiz.json",)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.units = fixture_units(self.units_wanted)
        self.uids = [u.uid for u in self.units]
        # The real writer, so the readback test is a test of the real contract
        # ("### <uid>" + Unit.text byte for byte) and not of a copy of it.
        self.artifacts, self.nonce = qc_sources.build_artifacts_md(
            self.units, self.dir, rng=random.Random(7))
        self.bundle = self.dir / "bundle.pdf"
        self.bundle.write_bytes(b"%PDF-1.4\n")
        self.manifest = manifest([page(code=CODE_A), page(printed=69, code=CODE_B)],
                                 units=self.uids, nonce=self.nonce)
        self.plan = {"call_id": "A", "kind": "A", "units": self.uids,
                     "bundle": self.bundle, "artifacts": self.artifacts,
                     "manifest": self.manifest, "nonce": self.nonce,
                     "pages": self.manifest["pages"]}
        self.canary = [("StahlKids", 74, CODE_A), ("StahlKids", 69, CODE_B)]
        self.texts = qc_judge.read_artifacts(self.artifacts, self.uids)

    def run_judge(self, ask, **kw):
        return qc_judge.judge(self.plan, self.manifest, self.artifacts,
                              self.nonce, None, ask=ask, canary=self.canary,
                              **kw)

    def good_reply(self, **kw):
        return reply(self.texts, nonce=self.nonce,
                     canary=(("StahlKids p.74", CODE_A),
                             ("StahlKids p.69", CODE_B)), **kw)


# ---------------------------------------------------------------------------


class PromptTests(unittest.TestCase):

    def test_the_prompt_stays_under_the_inline_limit(self):
        """§4: rules and schema inline and under 10,000 characters. The list of
        units is the only part that grows with the chapter, so the worst real
        case is a B call over a long guide."""
        uids = [f"G:{'מינונים וטיפול' * 3} {i}" for i in range(40)]
        uids += [f"F:medications.atomoxetine.dose.{i}" for i in range(60)]
        plan = {"call_id": "B1", "kind": "B", "units": uids, "pages": [1] * 180}
        req = [("StahlKids", 74, CODE_A), ("Dulcan", 716, CODE_B),
               ("DSM-5-TR", 135, "CCC333"), ("Kartisiot", 133, "DDD444")]
        text = qc_judge.prompt_for(plan, req, uids, "B")
        self.assertLess(len(text), qc_judge.MAX_PROMPT_CHARS,
                        f"the prompt is {len(text)} characters with "
                        f"{len(uids)} units")
        self.assertIn("artifacts.md", text)

    def test_the_prompt_never_names_a_code(self):
        """A prompt that carried the codes would let a judge answer the canary
        without opening bundle.pdf — the one thing it exists to prevent."""
        uids = ["Q1", "C1"]
        req = [("StahlKids", 74, CODE_A), ("Dulcan", 716, CODE_B)]
        text = qc_judge.prompt_for({"pages": []}, req, uids, "A")
        self.assertNotIn(CODE_A, text)
        self.assertNotIn(CODE_B, text)
        self.assertIn("p.74", text)

    def test_only_the_checklists_the_units_need(self):
        text = qc_judge.prompt_for({"pages": []}, [("StahlKids", 74, CODE_A)],
                                   ["Q1", "Q2"], "A")
        self.assertIn("QUIZ ITEMS", text)
        self.assertNotIn("PLATE LEGENDS", text)

    def test_the_kartisiot_naming_rule_is_in_the_rules(self):
        """Owner decision 2026-09-18.4: anything grounded in the Kartisiot
        summary must say so by name."""
        self.assertIn(qc_books.HEBREW_NAME["Kartisiot"], qc_judge._RULES)


class ArtifactsReadbackTests(unittest.TestCase):
    """The coverage check searches the text the judge was actually shown."""

    def test_a_guide_subhead_does_not_start_a_new_unit(self):
        """qc_units splits guides on `## ` alone, so a section's own `### `
        subheads travel inside its text. A reader that split on every `### `
        would cut the section in half and fail every echo below the cut."""
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        body = ("### G:רקע\n\nfirst line\n\n### תת-כותרת"
                "\n\nstill the same section\n\n### Q1\n\nthe quiz item\n")
        (d / "artifacts.md").write_bytes(body.encode("utf-8"))
        got = qc_judge.read_artifacts(d / "artifacts.md", ["G:רקע", "Q1"])
        self.assertIn("still the same section", got["G:רקע"],
                      "the subhead was treated as a unit boundary")
        self.assertEqual(got["Q1"], "the quiz item")

    def test_the_nonce_line_is_not_part_of_a_unit(self):
        d = Path(tempfile.mkdtemp())
        body = (f"### Q1\n\nthe item\n\n{qc_sources.NONCE_MARKER} {NONCE}\n")
        (d / "artifacts.md").write_bytes(body.encode("utf-8"))
        got = qc_judge.read_artifacts(d / "artifacts.md", ["Q1"])
        self.assertNotIn(NONCE, got["Q1"])

    def test_a_missing_block_is_an_error_not_an_empty_unit(self):
        d = Path(tempfile.mkdtemp())
        (d / "artifacts.md").write_bytes(b"### Q1\n\nthe item\n")
        with self.assertRaises(qc_judge.JudgeError):
            qc_judge.read_artifacts(d / "artifacts.md", ["Q1", "Q2"])


class CanaryAndCoverageTests(JudgeCase):
    """§5.8.1-2: any miss discards the verdict, retries, then `unjudged`."""

    def test_a_missed_canary_code_is_retried_and_then_unjudged(self):
        bad = self.good_reply()
        bad["canary"] = [{"page": "p.74", "code": "WRONG1"},
                         {"page": "p.69", "code": CODE_B}]
        ask = FakeAsk([bad, bad, bad])
        v = self.run_judge(ask)
        self.assertFalse(v["ok"])
        self.assertEqual(ask.prompts and len(ask.rejections), 3,
                         "the judge was not retried three times")
        self.assertIn("canary", v["reason"])
        status, reasons = qc_judge.chapter_status([v])
        self.assertEqual(status, "unjudged", reasons)

    def test_a_missing_coverage_row_is_retried_and_the_next_answer_stands(self):
        short = self.good_reply()
        short["coverage"] = short["coverage"][:-1]
        ask = FakeAsk([short, self.good_reply()])
        with mock.patch.object(qc_judge, "source_text", return_value="x" * 50):
            v = self.run_judge(ask)
        self.assertTrue(v["ok"], v["reason"])
        self.assertEqual(v["attempts"], 2)
        self.assertIn("coverage", ask.rejections[0])
        self.assertIn(self.uids[-1], ask.rejections[0])

    def test_a_wrong_nonce_discards_the_answer(self):
        bad = self.good_reply()
        bad["nonce"] = "NOTTHEONE12"
        v = self.run_judge(FakeAsk([bad]))
        self.assertFalse(v["ok"])
        self.assertIn("nonce", v["reason"])

    def test_an_echo_that_is_not_in_its_unit_discards_the_answer(self):
        bad = self.good_reply()
        bad["coverage"][0]["echo"] = ("טקסט שאינו מופיע "
                                      "בשום יחידה של הפרק הזה")
        v = self.run_judge(FakeAsk([bad]))
        self.assertFalse(v["ok"])
        self.assertIn("echo", v["reason"])

    def test_a_code_returned_on_the_wrong_page_discards_the_answer(self):
        """The code proves a page was opened; the label proves it was the page
        we asked about. A judge that read both pages and swapped the labels has
        not shown it can tell them apart."""
        bad = self.good_reply()
        bad["canary"] = [{"page": "StahlKids p.69", "code": CODE_A},
                         {"page": "StahlKids p.74", "code": CODE_B}]
        v = self.run_judge(FakeAsk([bad]))
        self.assertFalse(v["ok"])
        self.assertIn("canary", v["reason"])

    def test_a_row_for_a_unit_that_is_not_in_the_call_discards_the_answer(self):
        bad = self.good_reply()
        bad["coverage"].append({"unit": "Q99", "status": "supported",
                                "echo": "invented", "evidence_code": CODE_A})
        v = self.run_judge(FakeAsk([bad]))
        self.assertFalse(v["ok"])

    def test_a_clean_answer_is_accepted_on_the_first_attempt(self):
        ask = FakeAsk([self.good_reply()])
        with mock.patch.object(qc_judge, "source_text", return_value="x" * 50):
            v = self.run_judge(ask)
        self.assertTrue(v["ok"], v["reason"])
        self.assertEqual(v["attempts"], 1)
        self.assertEqual(len(v["coverage"]), len(self.uids))
        self.assertEqual(v["canary"]["verified"], True)


# ---------------------------------------------------------------------------
# §5.8.3: the findings


SAID_2_8 = "השפעה מלאה עשויה להימשך 2-8 שבועות"


def finding(**kw) -> dict:
    base = {"id": "F1", "unit": "Q1", "kind": "contradicted", "severity": "high",
            "category": "duration", "said": SAID_2_8, "evidence_code": CODE_A,
            "source_quote": "", "problem": "wrong onset", "correction": "8-12"}
    base.update(kw)
    return base


class FindingTests(JudgeCase):

    def verify(self, findings, *, source="", coverage=None):
        data = {"canary": [], "nonce": self.nonce,
                "coverage": coverage if coverage is not None
                else [{"unit": u, "status": "supported", "echo": t[:120],
                       "evidence_code": CODE_A, "source_quote": source}
                      for u, t in self.texts.items()],
                "findings": list(findings)}
        with mock.patch.object(qc_judge, "source_text", return_value=source):
            return qc_judge.verify(data, self.manifest, self.texts,
                                   call_id="A", unit_ids=self.uids)

    def test_contradicted_is_verified_on_its_evidence_page(self):
        src = ("Therapeutic actions may continue to improve for 8-12 weeks "
               "after the start of treatment.")
        out = self.verify([finding(source_quote=src)], source=src)
        f = out["findings"][0]
        self.assertTrue(f["verified"], f["verification"])
        self.assertEqual(f["page"], "StahlKids p.74")
        self.assertTrue(f["value_dispute"]["disputed"])
        self.assertIn("F1", out["fix"])

    def test_a_quote_holding_the_same_value_is_dropped(self):
        """§5.8.3: "A finding whose quote holds the same value as `said` is
        dropped." The 2026-09-18 sample had a judge correcting 2-8 weeks to
        2-8 weeks, and a dropped row must not reach the owner as an error."""
        src = ("Doesn't work right away; full therapeutic benefits may take "
               "2 to 8 weeks of treatment.")
        out = self.verify([finding(source_quote=src)], source=src)
        self.assertEqual(out["findings"], [])
        self.assertEqual(len(out["dropped"]), 1)
        self.assertIn("same value", out["dropped"][0]["dropped_because"])
        self.assertEqual(out["unverified_high"], 0,
                         "a dropped finding was still counted as unverified "
                         "high and would stop the chapter")

    def test_a_quote_that_is_not_on_the_page_is_unverified(self):
        out = self.verify([finding(source_quote="a sentence no page carries "
                                                "anywhere in this bundle")],
                          source="unrelated page text " * 6)
        f = out["findings"][0]
        self.assertFalse(f["verified"])
        self.assertIn("not on StahlKids p.74", f["verification"])
        self.assertEqual(out["unverified_high"], 1)

    def test_an_evidence_code_that_is_not_in_the_bundle_is_unverified(self):
        out = self.verify([finding(evidence_code="ZZZ999",
                                   source_quote="whatever it says")])
        self.assertFalse(out["findings"][0]["verified"])
        self.assertIn("not stamped", out["findings"][0]["verification"])

    def test_said_that_is_not_in_the_unit_is_never_verified(self):
        """§2.2 wants a verbatim quote on BOTH sides. The 2026-09-18 failure
        invented the source side; nothing stops a judge inventing the other."""
        src = "Therapeutic actions may continue to improve for 8-12 weeks."
        out = self.verify([finding(said="היא מעולם לא אמרה את זה",
                                   source_quote=src)], source=src)
        f = out["findings"][0]
        self.assertFalse(f["verified"])
        self.assertIn("said", f["verification"])

    def test_unsupported_found_elsewhere_is_dropped(self):
        """§5.8.3: found in the books beside the claim's key terms -> the
        finding goes, the page is logged. search_pages is stubbed here because
        it costs a full-text index build; tests/qc/test_qc_sources.py owns the
        real one."""
        with mock.patch.object(qc_sources, "search_pages",
                               return_value={("StahlKids", 69): {}}):
            out = self.verify([finding(kind="unsupported", source_quote="")])
        self.assertEqual(out["findings"], [])
        self.assertEqual(out["dropped"][0]["found_on"], ["StahlKids p.69"])

    def test_unsupported_found_nowhere_goes_to_the_owner(self):
        with mock.patch.object(qc_sources, "search_pages", return_value={}):
            out = self.verify([finding(kind="unsupported", source_quote="")])
        f = out["findings"][0]
        self.assertTrue(f["verified"])
        self.assertIn("F1", out["owner"])
        self.assertNotIn("F1", out["fix"], "an unsupported finding reached "
                                           "qc_fix; §5.8.3 forbids it")

    def test_a_search_that_cannot_run_keeps_the_finding(self):
        """No books on this machine is not evidence of absence, and it is not
        evidence of presence either. Fail closed: the owner sees it."""
        with mock.patch.object(qc_sources, "search_pages",
                               side_effect=RuntimeError("no Text BOOKS/")):
            out = self.verify([finding(kind="unsupported", source_quote="")])
        self.assertTrue(out["findings"][0]["verified"])
        self.assertIn("no Text BOOKS/", out["findings"][0]["search_skipped"])

    def test_duplicate_findings_collapse(self):
        src = "Therapeutic actions may continue to improve for 8-12 weeks."
        out = self.verify([finding(source_quote=src),
                           finding(id="F2", source_quote=src)], source=src)
        self.assertEqual(len(out["findings"]), 1)
        self.assertIn("duplicate", out["dropped"][0]["dropped_because"])

    def test_pure_confirmation_is_dropped_only_for_contradicted(self):
        """Ported from qc_book_review._clean_discrepancies. A source_conflict's
        FIRST quote is supposed to agree with the material — filtering those
        would delete every conflict this review exists to show."""
        conf = "המקור תואם את הנאמר בפריט"
        out = self.verify([finding(source_quote=conf)])
        self.assertEqual(out["findings"], [])
        out2 = self.verify([finding(kind="source_conflict", source_quote=conf,
                                    second_evidence_code=CODE_B,
                                    second_quote="something else entirely")])
        self.assertEqual(len(out2["findings"]) + len(out2["dropped"]), 1)
        self.assertEqual(out2["dropped"], [],
                         "a source_conflict was filtered as a confirmation")

    def test_two_quotes_whose_values_are_not_comparable_are_still_shown(self):
        """A duration on one page and a dose on the other have no comparable
        value at all — qc_norm compares only inside a (kind, unit) bucket — and
        reading that as agreement would hide a disagreement in the words.
        Owner decision 2026-09-18.3: conflicts are shown, never resolved."""
        with mock.patch.object(qc_judge, "source_text",
                               side_effect=lambda p, **kw:
                               "treat for 8-12 weeks before judging response"
                               if p["printed"] == [74] else
                               "the usual target dose is 1.2 mg/kg/day"):
            out = qc_judge.verify(
                {"coverage": [], "findings": [finding(
                    kind="source_conflict", severity="medium",
                    evidence_code=CODE_A,
                    source_quote="treat for 8-12 weeks before judging response",
                    second_evidence_code=CODE_B,
                    second_quote="the usual target dose is 1.2 mg/kg/day")]},
                self.manifest, self.texts, call_id="A", unit_ids=self.uids)
        f = out["findings"][0]
        self.assertTrue(f["verified"], f["verification"])
        self.assertFalse(f["value_dispute"]["comparable"])

    def test_the_search_borrows_key_terms_from_the_unit(self):
        """A Hebrew claim names nothing a book index can match; the item around
        it names Atomoxetine. Terms are borrowed, values never are — searching
        for the unit's other numbers would clear a claim nobody checked."""
        text = qc_judge._search_text(SAID_2_8, self.texts["Q1"])
        self.assertIn("Atomoxetine", text)
        self.assertNotIn("Nucleus", qc_judge._search_text(
            SAID_2_8 + " Atomoxetine Stimulants", self.texts["Q1"]),
            "the claim already named two terms; the unit's were borrowed anyway")

    def test_an_unreadable_severity_is_read_as_high(self):
        """Never as `low`: §2.3's direction is that an answer we cannot read
        stops the chapter rather than hiding from it."""
        out = self.verify([finding(severity="catastrophic",
                                   source_quote="not on the page at all")],
                          source="unrelated " * 12)
        self.assertEqual(out["findings"][0]["severity"], "high")
        self.assertEqual(out["unverified_high"], 1)


class SourceConflictTests(JudgeCase):
    """Spec §1's acceptance case, on the real books."""

    @needs_books
    def test_stahlkids_69_against_74_is_a_verified_conflict(self):
        q69 = page_sentence("StahlKids", 69, r"2\s*[-–]\s*8")
        q74 = page_sentence("StahlKids", 74, r"8\s*[-–]\s*12")
        self.manifest = manifest([page(printed=74, code=CODE_A),
                                  page(printed=69, code=CODE_B)],
                                 units=self.uids, nonce=self.nonce)
        data = {"canary": [], "nonce": self.nonce,
                "coverage": [{"unit": u, "status": "source_conflict",
                              "echo": t[:120], "evidence_code": CODE_B,
                              "source_quote": q69}
                             for u, t in self.texts.items()],
                "findings": [finding(kind="source_conflict", severity="medium",
                                     evidence_code=CODE_B, source_quote=q69,
                                     second_evidence_code=CODE_A,
                                     second_quote=q74)]}
        out = qc_judge.verify(data, self.manifest, self.texts, call_id="A",
                              unit_ids=self.uids)
        f = out["findings"][0]
        self.assertTrue(f["verified"], f["verification"])
        self.assertEqual((f["page"], f["second_page"]),
                         ("StahlKids p.69", "StahlKids p.74"))
        self.assertTrue(f["value_dispute"]["disputed"])
        self.assertIn("F1", out["owner"])
        self.assertNotIn("F1", out["fix"],
                         "§5.8.3: a source_conflict is never auto-fixed")

    @needs_books
    def test_two_quotes_stating_the_same_value_are_not_a_conflict(self):
        q69 = page_sentence("StahlKids", 69, r"2\s*[-–]\s*8")
        self.manifest = manifest([page(printed=69, code=CODE_A),
                                  page(printed=69, code=CODE_B)],
                                 units=self.uids, nonce=self.nonce)
        data = {"canary": [], "nonce": self.nonce, "coverage": [],
                "findings": [finding(kind="source_conflict",
                                     evidence_code=CODE_A, source_quote=q69,
                                     second_evidence_code=CODE_B,
                                     second_quote=q69 + " ")]}
        out = qc_judge.verify(data, self.manifest, self.texts, call_id="A",
                              unit_ids=self.uids)
        self.assertFalse(out["findings"][0]["verified"])

    @needs_books
    def test_a_quote_that_straddles_a_page_boundary_still_verifies(self):
        """qc_norm.source_window is +-1 for exactly this: the Dulcan sentence
        behind 038 Q9 is split across printed 330 and 331 and is verbatim on
        neither page alone. Built here the same way — the tail of one printed
        page plus the head of the next — so the test fails if the window ever
        narrows to a single page."""
        tail = qc_norm.source_window("Dulcan", 330, 0)[-90:]
        head = qc_norm.source_window("Dulcan", 331, 0)[:90]
        straddling = f"{tail} {head}"
        self.assertIsNone(
            qc_norm.find_quote(straddling, qc_norm.source_window("Dulcan", 330, 0)
                               )["match"],
            "the quote does not actually cross the boundary any more")
        hit = qc_judge.verify_quote(straddling,
                                    page(book="Dulcan", printed=331, code=CODE_A))
        self.assertTrue(hit["ok"], f"{hit['reason']} score={hit['score']}")


class CoverageAccountingTests(JudgeCase):
    """§5.8.2: a supported row on a locked value must carry a verified quote,
    and a row that fails counts as unchecked."""

    def test_a_supported_value_with_no_quote_is_unchecked(self):
        rows = [{"unit": u, "status": "supported", "echo": t[:120]}
                for u, t in self.texts.items()]
        with mock.patch.object(qc_judge, "source_text", return_value="nothing"):
            out = qc_judge.verify({"coverage": rows, "findings": []},
                                  self.manifest, self.texts, unit_ids=self.uids)
        self.assertGreater(out["unchecked"], 0)
        self.assertIn("no evidence code",
                      [r.get("unchecked_reason") for r in out["coverage"]][0])

    def test_a_supported_value_with_a_quote_we_can_find_is_checked(self):
        src = "Atomoxetine full therapeutic benefits may take 2-8 weeks."
        rows = [{"unit": u, "status": "supported", "echo": t[:120],
                 "evidence_code": CODE_A, "source_quote": src}
                for u, t in self.texts.items()]
        with mock.patch.object(qc_judge, "source_text", return_value=src):
            out = qc_judge.verify({"coverage": rows, "findings": []},
                                  self.manifest, self.texts, unit_ids=self.uids)
        self.assertEqual(out["unchecked"], 0)

    def test_a_citation_only_unit_does_not_need_a_source_quote(self):
        """`(StahlKids p. 74, Dulcan p. 716)` is three numbers and no claim. If
        citations counted as locked values, no chapter would ever reach the
        approval gate of 0 unchecked units."""
        texts = {"G:x": "רקע כללי לפרק (StahlKids p. 74, Dulcan p. 716)"}
        out = qc_judge.verify(
            {"coverage": [{"unit": "G:x", "status": "not_checkable",
                           "echo": "רקע"}], "findings": []},
            self.manifest, texts, unit_ids=["G:x"])
        self.assertEqual(out["unchecked"], 0)


class ChapterStatusTests(unittest.TestCase):
    """Fail closed, in the three words §5.8 allows the judge."""

    def verdict(self, **kw):
        base = {"call_id": "A", "ok": True, "reason": "", "findings": [],
                "owner": [], "unverified_high": 0, "unchecked": 0,
                "coverage": [{"unit": "Q1"}]}
        base.update(kw)
        return base

    def test_no_verdicts_is_unjudged(self):
        self.assertEqual(qc_judge.chapter_status([])[0], "unjudged")

    def test_one_failed_call_makes_the_chapter_unjudged(self):
        status, reasons = qc_judge.chapter_status(
            [self.verdict(), self.verdict(call_id="B", ok=False,
                                          reason="canary: code not returned")])
        self.assertEqual(status, "unjudged")
        self.assertIn("canary", " ".join(reasons))

    def test_an_unverified_high_needs_the_owner(self):
        """§5.8.3: "An unverified finding claimed `high` puts the chapter in
        `needs_owner`" — silence about a claim we could not check is not the
        same as the claim being fine."""
        status, reasons = qc_judge.chapter_status(
            [self.verdict(unverified_high=1,
                          findings=[{"id": "F1", "verified": False,
                                     "severity": "high", "kind": "contradicted"}])])
        self.assertEqual(status, "needs_owner")
        self.assertIn("high", " ".join(reasons))

    def test_a_verified_contradiction_is_an_open_error(self):
        status, _ = qc_judge.chapter_status(
            [self.verdict(findings=[{"id": "F1", "verified": True,
                                     "severity": "medium",
                                     "kind": "contradicted"}])])
        self.assertEqual(status, "needs_owner")

    def test_an_unchecked_locked_value_unit_blocks_approval(self):
        status, reasons = qc_judge.chapter_status([self.verdict(unchecked=1)])
        self.assertEqual(status, "needs_owner")
        self.assertIn("not checked", " ".join(reasons))

    def test_a_clean_chapter_is_approved(self):
        status, _ = qc_judge.chapter_status([self.verdict()])
        self.assertEqual(status, "approved")


class WiringTests(JudgeCase):
    """qc_run binds `judge_call` by name and hands it keywords this module does
    not use; a signature that rejects one is reported as NotWired at hour 14 of
    a 24-hour run."""

    def test_judge_call_takes_what_qc_run_sends(self):
        ask = FakeAsk([self.good_reply()])
        plan = dict(self.plan)
        with mock.patch.object(qc_judge, "source_text", return_value="x" * 50), \
                mock.patch.object(qc_sources, "canary_request",
                                  return_value=self.canary):
            v = qc_judge.judge_call(tag=TAG_019, call=plan, plan=plan,
                                    model="gemini-3.1-pro-high",
                                    root=str(self.dir), work_dir=self.dir,
                                    round_no=0, ask=ask)
        self.assertTrue(v["ok"], v["reason"])
        self.assertEqual(v["model"], "gemini-3.1-pro-high")
        self.assertTrue(v["prompt_sha256"])

    def test_a_call_with_no_manifest_stops_rather_than_guesses(self):
        with self.assertRaises(qc_judge.JudgeError):
            qc_judge.judge_call(call={"call_id": "A", "units": ["Q1"]})

    def test_the_raw_reply_is_kept_even_when_it_is_rejected(self):
        """§5.10 wants a raw response path; agy's own store is deleted minutes
        later, so a rejected attempt is otherwise unexplainable afterwards."""
        bad = self.good_reply()
        bad["nonce"] = "NOPE"
        v = self.run_judge(FakeAsk([bad]))
        self.assertTrue(Path(v["raw_path"]).exists())


if __name__ == "__main__":
    unittest.main()
