#!/usr/bin/env python3
"""Offline tests for scripts/qc_run.py — the orchestrator of the QC pass.

NOTHING HERE TOUCHES THE OUTSIDE. GitHub is FakeGh (reused from
test_qc_store.py, where it is itself under test), agy is a fake judge that is
never even asked to look like agy — qc_run hands the judge a planned call and
takes back a dict — and ntfy is a recorder whose `enabled` is never set. That
matters more than usual here: this module's real job is to rename assets on
releases the owner studies from and to push Hebrew alerts to a public ntfy
topic, and neither can be undone.

WHAT THE TESTS ARE FOR. Each one pins a rule the spec pays for with a
measurement or an incident:
  - `--dry-run` reaches the bundle and stops (§5.10): a run that "just checks"
    must not spend an agy call or move an asset.
  - A verified `contradicted` finding becomes a Tier-1 note and nothing else
    (§5.9, owner decision 2 of 2026-09-18 — note first, rewrite later).
  - A quota stop persists, notifies ONCE and exits 4 (§4): the lockout measured
    on this account was 13h33m and the run may not sleep through it.
  - Three canary/parse failures in a row stop the whole run (§5.5): on
    2026-09-18 agy stopped attaching files and failed every call identically.
  - A resume re-judges nothing that already has a verified answer.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_lint                                             # noqa: E402
import qc_run                                              # noqa: E402
import qc_store                                            # noqa: E402
from agy_attach import QuotaPaused                         # noqa: E402
from test_qc_store import FakeGh                           # noqa: E402

FIXTURES = HERE / "fixtures" / "release_assets"

# Real schedule tags: qc_run resolves what to judge through
# qc_store.chapters(), which reads the repository's schedule.json. A made-up
# tag would be reported as "not a chapter" and judged nothing.
TAG = "study-005-serotonin-system"
TAGS = ["study-017-adhd-core",
        "study-018-adhd-treatment-stimulants",
        "study-019-adhd-treatment-non-stimulants",
        "study-035-lithium"]

FACTS = json.dumps(
    {"drug_class": {"mechanism": "Fluoxetine blocks SERT",
                    "onset": "ההשפעה מופיעה תוך 4 שבועות"}},
    ensure_ascii=False, indent=1)

# One verified locked-value contradiction, in the shape §5.8's schema gives a
# finding. The numbers are the atomoxetine onset case of §6's known-answer
# probe: StahlKids p.69 says 2-8 weeks, and a chapter that says 4 is wrong.
FINDING = {
    "id": "A-1", "unit": "Q1", "kind": "contradicted", "severity": "high",
    "category": "locked_value",
    "said": "ההשפעה מופיעה תוך 4 שבועות",
    "evidence_code": "K7QX2M",
    "source_quote": "onset may take 2-8 weeks",
    "book": "StahlKids", "printed": 69,
    "problem": "הפרק נוקב ב-4 שבועות", "correction": "2-8 שבועות",
    "verified": True,
}


def release_assets(**extra) -> dict:
    assets = {
        "quiz.json": (FIXTURES / "quiz.json").read_bytes(),
        "flashcards.json": (FIXTURES / "flashcards.json").read_bytes(),
        "study_guide.md": (FIXTURES / "study_guide.md").read_bytes(),
        "facts.json": FACTS.encode("utf-8"),
        "audio.mp3": b"\x00\x01not-text",
        "video.mp4": b"\x00\x02not-text",
    }
    assets.update(extra)
    return assets


# ── the fakes ───────────────────────────────────────────────────────────────
_LATEX_SUB = re.compile(r"_\{([^{}]*)\}")
_LATEX_CMD = re.compile(r"\\([A-Za-z]+)")


class FakeHygiene:
    """§5.9's presentation clean-up, reduced to the one check the fixtures
    actually trip: escaped LaTeX, which 8 of 14 sampled guides carry.

    It edits PARSED values, never the JSON source — running a regex over
    `quiz.json` turns the `\\\\alpha` of a JSON string into an invalid escape
    and the asset stops parsing. That is not a detail of the fake: it is what
    the real fixer has to do too.
    """

    def __init__(self):
        self.calls = []

    @staticmethod
    def _clean(text: str) -> str:
        return _LATEX_CMD.sub(r"\1", _LATEX_SUB.sub(r"\1", text.replace("$", "")))

    @classmethod
    def _walk(cls, node):
        if isinstance(node, str):
            return cls._clean(node)
        if isinstance(node, list):
            return [cls._walk(x) for x in node]
        if isinstance(node, dict):
            return {k: cls._walk(v) for k, v in node.items()}
        return node

    def __call__(self, *, tag, assets, root, **kw):
        self.calls.append(tag)
        out, changes = {}, []
        for name, text in assets.items():
            if not text or "$" not in text and "\\" not in text:
                continue
            if name.endswith(".json"):
                new = json.dumps(self._walk(json.loads(text)),
                                 ensure_ascii=False, indent=2)
            else:
                new = self._clean(text)
            if new != text:
                out[name] = new
                changes.append({"asset": name, "check": "hygiene_latex"})
        return {"assets": out, "changes": changes}


class FakeSources:
    """One bundle per call. A and B only when they have units, exactly as §5.7
    splits the work: these fixtures carry no legends, so there is no C call."""

    def __init__(self):
        self.calls = []

    @staticmethod
    def _kind(uid: str) -> str:
        if uid[:1] in ("Q", "C") and uid[1:2].isdigit():
            return "A"
        return "C" if uid.startswith("L:") else "B"

    def __call__(self, *, tag, units, cites, assets, work_dir, page_limit,
                 root, **kw):
        self.calls.append({"tag": tag, "units": len(units),
                           "page_limit": page_limit})
        by_kind: dict[str, list[str]] = {}
        for u in units:
            by_kind.setdefault(self._kind(u.uid), []).append(u.uid)
        out = []
        for kind in ("A", "B", "C"):
            uids = by_kind.get(kind)
            if not uids:
                continue
            d = Path(work_dir) / kind
            d.mkdir(parents=True, exist_ok=True)
            bundle = d / "bundle.pdf"
            bundle.write_bytes(b"%PDF-1.4\n" + b"page\n" * 500)
            art = d / "artifacts.md"
            art.write_text("\n".join(f"### {u}" for u in uids), encoding="utf-8")
            out.append({"call_id": kind, "kind": kind, "units": uids,
                        "bundle": bundle, "artifacts": art, "pages": 40,
                        "manifest": {"nonce": f"nonce-{kind}",
                                     "codes": {"StahlKids:69": "K7QX2M"}}})
        return out


class FakeJudge:
    """Returns a canned answer per call id; records what it was asked."""

    def __init__(self, responses=None, default=None):
        self.calls = []
        self.responses = responses or {}
        self.default = default if default is not None else {"ok": True}

    def __call__(self, *, tag, call, plan, **kw):
        self.calls.append(plan.call_id)
        r = self.responses.get(plan.call_id, self.default)
        if callable(r):
            r = r(plan)
        if isinstance(r, Exception):
            raise r
        return {"coverage": [{"unit": u, "status": "supported"}
                             for u in plan.units],
                "findings": [], "conversation_id": f"cid-{plan.call_id}",
                "workspace": f"ws-{plan.call_id}", "agy_version": "1.2.6",
                "model": "gemini-3.1-pro-high", "prompt_sha256": "a" * 64,
                "attempts": 1, "canary": {"requested": [["StahlKids", 69]],
                                          "returned": [["StahlKids", 69]],
                                          "ok": True},
                **r}


class FakeTier1:
    """Embeds the §5.9 marker beside the quiz item. Rewrites nothing.

    Both quotes when the finding carries two, exactly as qc_fix.qc_record does:
    §5.9's Tier 1 ends "For `source_conflict`, both quotes", and a double that
    dropped the second one would let a one-sided mark pass every test here
    while the app draws "המקורות חלוקים זה עם זה" from the pair.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, *, tag, findings, assets, units, root, **kw):
        self.calls.append([dict(f) for f in findings])
        data = json.loads(assets["quiz.json"])
        fixes = []
        for f in findings:
            idx = int(f["unit"][1:]) - 1
            item = data["questions"][idx]
            item["qc"] = {"kind": "content", "date": "2026-09-22",
                          "was": f["said"], "severity": f["severity"],
                          "source": {"book": f["book"],
                                     "printed_page": f["printed"],
                                     "quote": f["source_quote"]},
                          "status": "active"}
            if f.get("second_quote"):
                item["qc"]["second_source"] = {
                    "book": f.get("second_book", f["book"]),
                    "printed_page": f.get("second_printed"),
                    "quote": f["second_quote"]}
            fixes.append(dict(f, tier=1, asset="quiz.json",
                              stable_id="0" * 40))
        return {"assets": {"quiz.json": json.dumps(data, ensure_ascii=False,
                                                   indent=2)},
                "fixes": fixes}


class RoundJudge(FakeJudge):
    """A judge whose answer depends on which re-judge round is asking."""

    def __init__(self, by_round):
        super().__init__()
        self.by_round = by_round
        self.seen = []

    def __call__(self, *, tag, call, plan, round_no=0, **kw):
        self.seen.append((round_no, plan.call_id))
        self.responses = {plan.call_id:
                          self.by_round.get(round_no, {}).get(plan.call_id,
                                                              {"ok": True})}
        return super().__call__(tag=tag, call=call, plan=plan, **kw)


class FakeTier2:
    """A rewrite: the item's value really changes, so a re-judge is owed."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, tag, findings, assets, units, root, **kw):
        self.calls.append([dict(f) for f in findings])
        data = json.loads(assets["quiz.json"])
        fixes = []
        for f in findings:
            item = data["questions"][int(f["unit"][1:]) - 1]
            item["question"] = item["question"] + f" [{f['correction']}]"
            fixes.append(dict(f, tier=2, asset="quiz.json"))
        return {"assets": {"quiz.json": json.dumps(data, ensure_ascii=False,
                                                   indent=2)},
                "fixes": fixes}


class FakeRevert:
    def __init__(self, original):
        self.original = original
        self.calls = []

    def __call__(self, *, tag, findings, assets, root, **kw):
        self.calls.append([dict(f) for f in findings])
        return {"assets": {"quiz.json": self.original}}


class Recorder:
    """A notifier that keeps every message and sends none."""

    def __init__(self):
        self.sent = []

    def __call__(self, title, message, *, priority=3, tags=()):
        self.sent.append({"title": title, "message": message,
                          "priority": priority, "tags": list(tags)})
        return False


class Forbidden:
    """A collaborator this test says must never be called."""

    def __init__(self, what):
        self.what = what

    def __call__(self, **kw):
        raise AssertionError(f"{self.what} was called and must not have been")


# ── the case ────────────────────────────────────────────────────────────────
class RunCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-run-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "_qc"
        self.gh = FakeGh()
        self.gh.add_release(TAG, release_assets())
        self.sources = FakeSources()
        self.hygiene = FakeHygiene()
        self.tier1 = FakeTier1()
        self.notify = Recorder()
        self.cleaned = []
        self.probes = []
        self.probe = self._probe

    def _probe(self, **kw):
        """The §4 known-answer probe, faked. Set self.probe_ok False to fail it."""
        self.probes.append(kw)
        ok = getattr(self, "probe_ok", True)
        return {"ok": ok, "quote": "2-8 weeks" if ok else "",
                "detail": "faked probe"}

    # -- wiring ------------------------------------------------------------
    def deps(self, judge=None, **over):
        kw = dict(
            gh=self.gh, sources=self.sources, judge=judge or FakeJudge(),
            # §4's probe is a REAL agy call when it is not faked: it binds
            # lazily to qc_judge, so a test that changes the agy version
            # mid-run spends the owner's quota. Default to one that passes;
            # a test about the guard passes its own.
            probe=self.probe,
            hygiene=self.hygiene, tier1=self.tier1,
            tier2=Forbidden("tier 2"), revert=Forbidden("tier 2 revert"),
            notify=self.notify, wait_for_network=lambda **k: True,
            cleanup=lambda cid, workspace=None: (
                self.cleaned.append((cid, workspace)) or 0),
        )
        kw.update(over)
        return qc_run.Deps(**kw)

    def opts(self, **over):
        kw = dict(tags=[TAG], root=self.root, use_books=False, no_upload=True)
        kw.update(over)
        return qc_run.Options(**kw)

    def books_available(self):
        """Run as if `Text BOOKS/` were on this machine.

        Every test here is offline and passes `use_books=False`, so qc_lint
        reports `impossible_citation` and `value_unsourced` as skipped — and
        qc_run refuses to call a chapter with a skipped citation check
        `approved` (§2.3: a chapter must not "look clean because a file was
        absent"). That rule is right and is pinned by SkippedCheckTests below.
        A test whose subject is uploads, dedup or resume still has to be able
        to reach `approved`, so it declares the books present and lets the lint
        report nothing skipped. Nothing else about the lint is touched.
        """
        real = qc_lint.lint_chapter

        def patched(tag, assets, **kw):
            out = real(tag, assets, **kw)
            out["skipped"] = []
            return out

        qc_lint.lint_chapter = patched
        self.addCleanup(setattr, qc_lint, "lint_chapter", real)

    def run_qc(self, opts, deps):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = qc_run.run(opts, deps)
        return code, buf.getvalue()

    # -- helpers -----------------------------------------------------------
    def verdict(self, tag=TAG):
        return qc_store.read_json(self.root / tag / "verdict.json")

    def gh_writes(self):
        return [c for c in self.gh.calls
                if c[0] in ("upload", "rename_asset", "delete_asset")]


# ── dry run ─────────────────────────────────────────────────────────────────
class DryRunTests(RunCase):
    def test_dry_run_plans_the_calls_and_touches_nothing(self):
        code, out = self.run_qc(
            self.opts(dry_run=True, no_upload=False),
            self.deps(judge=Forbidden("the judge"), tier1=Forbidden("tier 1")))

        self.assertEqual(code, qc_run.EXIT_OK)
        # The two things a dry run must not do.
        self.assertEqual(self.gh_writes(), [])
        self.assertEqual(self.notify.sent, [])
        # ...and the one it must: a real plan, with real sizes.
        self.assertEqual(len(self.sources.calls), 1)
        plan = qc_store.read_json(self.root / TAG / "plan.json")
        self.assertEqual([c["call_id"] for c in plan], ["A", "B"])
        self.assertTrue(all(c["bundle_bytes"] > 0 for c in plan))
        self.assertIn("dry-run", out)
        self.assertIn("MB", out)
        # A dry run reaches no verdict, so it writes none.
        self.assertFalse((self.root / TAG / "verdict.json").exists())
        self.assertTrue((self.root / TAG / "dry_run.json").exists())
        # The snapshot is still taken: it is read-only against GitHub and it is
        # the copy §2.5 refuses to be without.
        self.assertTrue((self.root / "orig" / TAG / "quiz.json").exists())

    def test_dry_run_passes_the_page_limit_through(self):
        self.run_qc(self.opts(dry_run=True, page_limit=120),
                    self.deps(judge=Forbidden("the judge")))
        self.assertEqual(self.sources.calls[0]["page_limit"], 120)


# ── the whole pass, without uploading ───────────────────────────────────────
class EndToEndTests(RunCase):
    def judge_with_finding(self):
        return FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})

    def test_verified_contradiction_becomes_a_tier1_note_and_a_verdict(self):
        judge = self.judge_with_finding()
        code, out = self.run_qc(self.opts(), self.deps(judge=judge))

        self.assertEqual(code, qc_run.EXIT_OK)
        self.assertEqual(judge.calls, ["A", "B"])
        # The finding reached Tier 1, whole.
        self.assertEqual(len(self.tier1.calls), 1)
        self.assertEqual(self.tier1.calls[0][0]["id"], "A-1")

        v = self.verdict()
        self.assertEqual(v["status"], "fixed")
        self.assertEqual(len(v["fixes"]), 1)
        self.assertEqual(v["fixes"][0]["tier"], 1)
        self.assertEqual(v["findings"]["by_severity"], {"high": 1})

        # The note is in the bytes, and the bytes are local only.
        out_file = self.root / "work" / TAG / "out" / "quiz.json"
        patched = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(patched["questions"][0]["qc"]["source"]["printed_page"],
                         69)
        self.assertEqual(self.gh_writes(), [])
        self.assertFalse(v["outputs"]["quiz.json"]["uploaded"])

    def test_provenance_is_recorded_for_every_call(self):
        judge = self.judge_with_finding()
        self.run_qc(self.opts(), self.deps(judge=judge))
        v = self.verdict()
        self.assertEqual(v["agy"]["version"], "1.2.6")
        self.assertEqual(v["agy"]["model"], "gemini-3.1-pro-high")
        self.assertEqual(sorted(v["agy"]["conversations"]), ["A", "B"])
        self.assertEqual(v["inputs"]["quiz.json"]["sha256"],
                         qc_store.sha256_bytes(
                             (FIXTURES / "quiz.json").read_bytes()))
        self.assertEqual({b["call_id"] for b in v["bundle"]}, {"A", "B"})
        self.assertEqual(v["bundle"][0]["manifest"]["nonce"], "nonce-A")
        self.assertTrue(v["canary"]["A"]["ok"])
        self.assertEqual(v["coverage"]["rows"], v["coverage"]["units"])

    def test_agy_store_is_deleted_once_the_judgment_is_on_disk(self):
        self.run_qc(self.opts(), self.deps(judge=self.judge_with_finding()))
        self.assertEqual(sorted(self.cleaned),
                         [("cid-A", "ws-A"), ("cid-B", "ws-B")])

    def test_hygiene_runs_before_the_lint_and_before_the_judge(self):
        judge = self.judge_with_finding()
        self.run_qc(self.opts(), self.deps(judge=judge))
        v = self.verdict()
        # The released quiz carries escaped LaTeX; the lint recorded in the
        # verdict is the one taken AFTER the clean-up, so it is clean...
        self.assertNotIn("hygiene_latex", v["lint"]["counts"])
        self.assertEqual(v["lint"]["blocking"], [])
        # ...and what was wrong with the released bytes is still on record.
        self.assertTrue(any(c["check"] == "hygiene_latex"
                            for c in v["hygiene"]["changes"]))

    def test_report_and_qc_json_carry_the_errata_line(self):
        self.run_qc(self.opts(), self.deps(judge=self.judge_with_finding()))
        report = (self.root / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("החומר שפורסם אמר", report)
        # The book as the owner reads it, not the qc_books key. Owner decision
        # 4 and §5.8's rules require the כרטיסיות to be named by its Hebrew
        # title "wherever it appears", and REPORT.md and the per-chapter ntfy
        # are the two places that never pass through the app's own mapping
        # (quiz_logic.book_display). One name per book everywhere, so this
        # holds for StahlKids too.
        self.assertIn(f"{qc_run.BOOK_HE['StahlKids']} עמ' 69", report)
        self.assertNotIn("(StahlKids עמ'", report)
        self.assertIn("תוקן", report)

        qc = qc_store.read_json(self.root / TAG / "qc.json")
        self.assertEqual(qc["status"], "fixed")
        self.assertEqual(len(qc["errata"]), 1)
        self.assertEqual(qc["errata"][0]["unit"], "Q1")
        # Every judged asset is listed, not only the corrected one: the app has
        # to tell "checked and clean" from "never looked at".
        self.assertIn("study_guide.md", qc["assets"])

    def test_one_notification_per_corrected_chapter_plus_the_digest(self):
        self.run_qc(self.opts(), self.deps(judge=self.judge_with_finding()))
        titles = [n["title"] for n in self.notify.sent]
        self.assertEqual(len(titles), 2)
        self.assertIn("1 תיקונים", titles[0])
        self.assertIn("סיכום", titles[1])
        self.assertIn("→", self.notify.sent[0]["message"])

    def test_a_clean_chapter_is_approved_and_changes_nothing_to_upload(self):
        self.books_available()
        gh = FakeGh()
        # A release with no LaTeX to clean and nothing to find.
        clean_quiz = json.dumps(
            {"title": "t", "questions": [{
                "question": "מהו מנגנון הפעולה של Fluoxetine?",
                "answerOptions": [
                    {"text": "עיכוב SERT", "isCorrect": True,
                     "rationale": "Fluoxetine מעכב את משאבת הסרוטונין."},
                    {"text": "עיכוב DAT", "isCorrect": False,
                     "rationale": "זהו מנגנון של סטימולנטים."}]}]},
            ensure_ascii=False, indent=1).encode("utf-8")
        cards = json.dumps({"title": "t", "cards": [
            {"front": "מה מעכב Fluoxetine?", "back": "את משאבת הסרוטונין SERT."}]},
            ensure_ascii=False, indent=1).encode("utf-8")
        gh.add_release(TAG, {"quiz.json": clean_quiz, "flashcards.json": cards,
                             "facts.json": FACTS.encode("utf-8"),
                             "study_guide.md": "# מדריך\n\n## 1. מבוא\nטקסט.\n"
                                               .encode("utf-8"),
                             "audio.mp3": b"\x00", "video.mp4": b"\x00"})
        self.gh = gh
        code, _ = self.run_qc(self.opts(no_upload=False), self.deps())
        self.assertEqual(code, qc_run.EXIT_OK)
        v = self.verdict()
        self.assertEqual(v["status"], "approved")
        self.assertEqual(v["fixes"], [])
        # qc.json is the only thing added; no studied asset was swapped.
        self.assertEqual([c for c in self.gh_writes() if c[0] != "upload"], [])
        self.assertEqual([c[1][1] for c in self.gh_writes()], ["qc.json"])


# ── upload ──────────────────────────────────────────────────────────────────
class UploadTests(RunCase):
    def test_a_corrected_asset_is_swapped_never_clobbered(self):
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})
        code, _ = self.run_qc(self.opts(no_upload=False),
                              self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_OK)

        names = self.gh.names(TAG)
        self.assertIn("quiz.json", names)
        # §5.5: the original survives under its own name.
        pre = [n for n in names if n.startswith("quiz.json.pre-qc-")]
        self.assertEqual(len(pre), 1)
        self.assertEqual(self.gh.data(TAG, pre[0]),
                         (FIXTURES / "quiz.json").read_bytes())
        live = json.loads(self.gh.data(TAG, "quiz.json").decode("utf-8"))
        self.assertEqual(live["questions"][0]["qc"]["status"], "active")
        self.assertTrue(self.verdict()["outputs"]["quiz.json"]["uploaded"])
        self.assertIn("qc.json", names)

    def test_an_unjudged_chapter_publishes_nothing(self):
        judge = FakeJudge(default={"ok": False, "reason": "canary mismatch"})
        code, _ = self.run_qc(self.opts(no_upload=False),
                              self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_OK)     # one chapter, no breaker yet
        self.assertEqual(self.verdict()["status"], "unjudged")
        # The hygiene clean-up really did change bytes...
        self.assertTrue((self.root / "work" / TAG / "out" / "quiz.json").exists())
        # ...and none of them reached the release, qc.json included.
        self.assertEqual(self.gh_writes(), [])
        self.assertNotIn("qc.json", self.gh.names(TAG))

    def test_calibration_never_uploads(self):
        self.assertFalse(qc_run.Options(calibration=True).uploads_allowed)
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})
        self.run_qc(self.opts(no_upload=False, calibration=True),
                    self.deps(judge=judge))
        self.assertEqual(self.gh_writes(), [])
        self.assertTrue(self.verdict()["calibration"])
        # §5.10.4's per-chapter alert is for material the owner studies; a
        # calibration verdict is measurement, so it stays quiet.
        self.assertTrue(all("תיקונים" not in n["title"]
                            for n in self.notify.sent))


# ── tier 2 ──────────────────────────────────────────────────────────────────
class Tier2Tests(RunCase):
    """§5.9's second tier: off by default, and one re-judge when it fires."""

    def enable(self):
        qc_store.write_json_atomic(qc_store.state_path(root=self.root),
                                   {"runs": {}, "tier2_enabled": True})

    def test_tier2_is_off_until_the_state_flag_is_set(self):
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})
        # Forbidden() as the tier-2 fixer: the run must not reach it.
        self.run_qc(self.opts(), self.deps(judge=judge))
        self.assertEqual(self.verdict()["rejudge_rounds"], 0)

    def test_a_rewrite_is_confirmed_by_one_full_chapter_rejudge(self):
        self.enable()
        tier2 = FakeTier2()
        judge = RoundJudge({0: {"A": {"ok": True, "findings": [FINDING]}}})
        code, _ = self.run_qc(self.opts(),
                              self.deps(judge=judge, tier2=tier2,
                                        revert=Forbidden("tier 2 revert")))
        self.assertEqual(code, qc_run.EXIT_OK)
        self.assertEqual(len(tier2.calls), 1)
        # Round 0 judged A and B; round 1 judged the whole chapter again.
        self.assertEqual(judge.seen, [(0, "A"), (0, "B"), (1, "A"), (1, "B")])
        # §5.7: a fresh bundle per round, never the memorised one.
        self.assertEqual(len(self.sources.calls), 2)
        v = self.verdict()
        self.assertEqual(v["rejudge_rounds"], 1)
        self.assertEqual(sorted(f["tier"] for f in v["fixes"]), [1, 2])
        self.assertEqual(v["status"], "fixed")

    def test_a_finding_on_a_patched_unit_reverts_it_and_calls_the_owner(self):
        self.enable()
        original = (FIXTURES / "quiz.json").read_text(encoding="utf-8")
        revert = FakeRevert(original)
        judge = RoundJudge({0: {"A": {"ok": True, "findings": [FINDING]}},
                            1: {"A": {"ok": True, "findings": [
                                dict(FINDING, id="A-2")]}}})
        self.run_qc(self.opts(), self.deps(judge=judge, tier2=FakeTier2(),
                                           revert=revert))
        self.assertEqual(len(revert.calls), 1)
        v = self.verdict()
        # The rewrite is gone; the Tier-1 note stays; the owner decides.
        self.assertEqual([f["tier"] for f in v["fixes"]], [1])
        self.assertEqual(v["status"], "needs_owner")


# ── quota and resume ────────────────────────────────────────────────────────
class QuotaTests(RunCase):
    def quota(self):
        return QuotaPaused(time.time() + 3600,
                           "RESOURCE_EXHAUSTED (code 429): Individual quota "
                           "reached. Resets in 1h00m00s.", 3600)

    def test_quota_pause_persists_notifies_once_and_exits_4(self):
        judge = FakeJudge(responses={"A": {"ok": True},
                                     "B": self.quota()})
        code, out = self.run_qc(self.opts(), self.deps(judge=judge))

        self.assertEqual(code, qc_run.EXIT_QUOTA)
        st = qc_store.get_run_state(TAG, root=self.root)
        self.assertEqual(st["state"], "paused_quota")
        self.assertEqual(st["step"], "judged_B")
        self.assertTrue(st["resume_after"])
        self.assertIn("RESOURCE_EXHAUSTED", st["raw"])
        # ONE message, and it says how to start again.
        self.assertEqual(len(self.notify.sent), 1)
        self.assertIn("מכסת agy", self.notify.sent[0]["title"])
        self.assertIn("qc_run.py --chapters", self.notify.sent[0]["message"])
        self.assertIn("PAUSE", out)
        # No verdict: the chapter is unfinished, not unjudged.
        self.assertFalse((self.root / TAG / "verdict.json").exists())

    def test_resume_does_not_pay_twice_for_a_call_that_already_answered(self):
        self.books_available()
        first = FakeJudge(responses={"A": {"ok": True}, "B": self.quota()})
        self.run_qc(self.opts(), self.deps(judge=first))
        self.assertEqual(first.calls, ["A", "B"])
        journal = qc_store.Journal(TAG, root=self.root)
        self.assertTrue(journal.done("judged_A"))
        self.assertFalse(journal.done("judged_B"))

        second = FakeJudge()
        code, _ = self.run_qc(self.opts(), self.deps(judge=second))
        self.assertEqual(code, qc_run.EXIT_OK)
        # A's verified answer was on disk; only B was asked again.
        self.assertEqual(second.calls, ["B"])
        self.assertEqual(self.verdict()["status"], "approved")

    def test_a_quota_stop_does_not_count_against_the_circuit_breaker(self):
        # §4: "A quota stop never consumes a content retry" — and it must not
        # consume a breaker slot either. It raises out of run() before a
        # verdict exists, so there is nothing to count; the check is that four
        # quota-stopped chapters end on the quota code and stop at the first.
        for tag in TAGS:
            self.gh.add_release(tag, release_assets())
        judge = FakeJudge(default=self.quota())
        code, _ = self.run_qc(self.opts(tags=TAGS), self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_QUOTA)
        self.assertEqual(judge.calls, ["A"])
        self.assertEqual(len(self.notify.sent), 1)


# ── circuit breaker ─────────────────────────────────────────────────────────
class BreakerTests(RunCase):
    def setUp(self):
        super().setUp()
        for tag in TAGS:
            self.gh.add_release(tag, release_assets())

    def test_three_canary_failures_stop_the_run(self):
        judge = FakeJudge(default={"ok": False,
                                   "reason": "canary mismatch: the code for "
                                             "StahlKids p.69 was not returned"})
        code, out = self.run_qc(self.opts(tags=TAGS), self.deps(judge=judge))

        self.assertEqual(code, qc_run.EXIT_STOP)
        judged = [t for t in TAGS if (self.root / t / "verdict.json").exists()]
        self.assertEqual(judged, TAGS[:qc_run.CIRCUIT_BREAKER_N])
        self.assertEqual(self.verdict(TAGS[0])["status"], "unjudged")
        stop = [n for n in self.notify.sent if n["priority"] >= 5]
        self.assertEqual(len(stop), 1)
        self.assertIn("StahlKids", stop[0]["message"])
        self.assertIn("STOP", out)

    def test_a_chapter_held_for_the_owner_resets_the_breaker(self):
        """A `needs_owner` chapter is a judge that worked, not one that broke.

        Counting it would stop the run on the first three chapters with a
        genuine clinical finding — the opposite of what the backlog is for.
        """
        bad = {"ok": False, "reason": "canary mismatch"}
        good = {"ok": True, "findings": [dict(FINDING, kind="source_conflict",
                                              id="A-9")]}
        # fail, fail, a real finding, fail — three failures in the run but
        # never three in a row, so the breaker must not fire.
        seq = [bad, bad, good, bad]

        class Cycling(FakeJudge):
            def __call__(self, *, tag, call, plan, **kw):
                if plan.kind == "A":
                    self.responses = {"A": seq.pop(0)}
                return super().__call__(tag=tag, call=call, plan=plan, **kw)

        judge = Cycling()
        code, _ = self.run_qc(self.opts(tags=TAGS), self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_OK)
        self.assertEqual(self.verdict(TAGS[2])["status"], "needs_owner")
        queue = qc_store.read_json(self.root / "owner_queue.json")
        self.assertEqual([i["id"] for i in queue["items"]], ["A-9"])
        self.assertEqual(queue["items"][0]["kind"], "source_conflict")


# ── finding policy ──────────────────────────────────────────────────────────
class PolicyTests(RunCase):
    def test_an_unverified_high_finding_holds_the_chapter(self):
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [
            dict(FINDING, verified=False)]}})
        self.run_qc(self.opts(), self.deps(judge=judge, tier1=Forbidden("tier 1")))
        v = self.verdict()
        self.assertEqual(v["status"], "needs_owner")
        self.assertEqual(v["findings"]["unverified_high"], 1)
        self.assertEqual(v["fixes"], [])

    def test_a_finding_whose_quote_repeats_its_own_value_is_dropped(self):
        self.books_available()
        same = dict(FINDING, said="2-8 weeks",
                    source_quote="onset may take 2–8 weeks")
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [same]}})
        self.run_qc(self.opts(), self.deps(judge=judge, tier1=Forbidden("tier 1")))
        v = self.verdict()
        self.assertEqual(v["findings"]["dropped"], 1)
        self.assertEqual(v["fixes"], [])
        self.assertEqual(v["status"], "approved")

    def test_only_contradicted_high_or_medium_reaches_the_fixer(self):
        findings = [
            dict(FINDING, id="1", kind="contradicted", severity="high"),
            dict(FINDING, id="2", kind="contradicted", severity="medium"),
            dict(FINDING, id="3", kind="contradicted", severity="low"),
            dict(FINDING, id="4", kind="unsupported", severity="high"),
            dict(FINDING, id="5", kind="source_conflict", severity="medium"),
        ]
        got = qc_run.classify_findings(
            [qc_run.Judgment(call_id="A", kind="A", ok=True, findings=findings)])
        self.assertEqual([f["id"] for f in got["fix"]], ["1", "2"])
        self.assertEqual(sorted(f["id"] for f in got["owner"]), ["4", "5"])
        self.assertEqual([f["id"] for f in got["dropped"]], ["3"])

    def test_findings_of_a_call_that_failed_its_canary_are_never_counted(self):
        judge = FakeJudge(responses={
            "A": {"ok": False, "reason": "canary mismatch",
                  "findings": [FINDING]}})
        self.run_qc(self.opts(), self.deps(judge=judge, tier1=Forbidden("tier 1")))
        v = self.verdict()
        self.assertEqual(v["status"], "unjudged")
        self.assertEqual(v["findings"]["by_kind"], {})

    def test_status_precedence(self):
        clean = {"blocking": []}
        self.assertEqual(qc_run.chapter_status(
            judged=False, lint=clean, fixes=[], owner_items=[],
            unverified_high=0), "unjudged")
        self.assertEqual(qc_run.chapter_status(
            judged=True, lint={"blocking": ["missing_asset"]}, fixes=[],
            owner_items=[{"id": "x"}], unverified_high=0), "needs_owner")
        self.assertEqual(qc_run.chapter_status(
            judged=True, lint={"blocking": ["missing_asset"]}, fixes=[],
            owner_items=[], unverified_high=0), "needs_regeneration")
        self.assertEqual(qc_run.chapter_status(
            judged=True, lint={"blocking": ["quiz_unknown_type"]}, fixes=[],
            owner_items=[], unverified_high=0), "needs_owner")
        self.assertEqual(qc_run.chapter_status(
            judged=True, lint=clean, fixes=[{"id": "1"}], owner_items=[],
            unverified_high=0), "fixed")
        self.assertEqual(qc_run.chapter_status(
            judged=True, lint=clean, fixes=[], owner_items=[],
            unverified_high=0), "approved")


# ── wiring, selection and reports ───────────────────────────────────────────
class WiringTests(RunCase):
    def test_a_missing_collaborator_names_what_it_looked_for(self):
        lazy = qc_run._Lazy("qc_module_that_does_not_exist",
                            ("plan_calls",), "bundle planner")
        self.assertFalse(lazy.available)
        with self.assertRaises(qc_run.NotWired) as cm:
            lazy(tag="x")
        self.assertIn("plan_calls", str(cm.exception))

    def test_a_collaborator_that_rejects_the_keywords_is_a_wiring_error(self):
        lazy = qc_run._Lazy("qc_store", ("now_iso",), "clock")
        with self.assertRaises(qc_run.NotWired) as cm:
            lazy(tag="x", units=[])
        self.assertIn("keywords only", str(cm.exception))

    def test_an_unreleased_or_unknown_chapter_is_skipped_with_a_reason(self):
        code, out = self.run_qc(
            self.opts(tags=["study-999-not-a-thing", "42"]),
            self.deps(judge=Forbidden("the judge")))
        self.assertEqual(code, qc_run.EXIT_ERROR)
        self.assertIn("not a chapter: study-999-not-a-thing", out)
        self.assertIn("no published release", out)

    def test_chapters_can_be_named_by_schedule_index(self):
        judge = FakeJudge()
        code, _ = self.run_qc(self.opts(tags=["5"]), self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_OK)
        self.assertTrue((self.root / TAG / "verdict.json").exists())

    def test_an_orphan_release_is_reported_and_never_judged(self):
        self.gh.add_release("study-016-adhd-core", release_assets())
        judge = FakeJudge()
        _, out = self.run_qc(self.opts(), self.deps(judge=judge))
        self.assertIn("study-016-adhd-core", out)
        self.assertFalse((self.root / "study-016-adhd-core").exists())

    def test_ntfy_does_not_send_unless_it_is_turned_on(self):
        n = qc_run.Ntfy()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertFalse(n("t", "m"))
        self.assertEqual(len(n.sent), 1)
        self.assertIn("לא נשלח", buf.getvalue())

    def test_build_options_splits_comma_separated_chapters(self):
        ap_args = type("A", (), {
            "chapters": ["19,42", "5"], "all": False, "calibration": False,
            "dry_run": True, "no_upload": False, "page_limit": 180,
            "no_books": True, "model": None, "qc_dir": str(self.root)})()
        opts = qc_run.build_options(ap_args)
        self.assertEqual(opts.tags, ["19", "42", "5"])
        self.assertFalse(opts.use_books)
        self.assertFalse(opts.uploads_allowed)

    def test_digest_counts_what_the_owner_asked_for(self):
        verdicts = [
            {"tag": "a", "status": "fixed", "fixes": [{}, {}],
             "findings": {"by_severity": {"high": 1}}},
            {"tag": "b", "status": "needs_owner", "fixes": [],
             "findings": {"by_severity": {"medium": 2}}},
        ]
        text = qc_run.digest_text(verdicts, total=68, durations=[60.0, 60.0])
        self.assertIn("2/68", text)
        self.assertIn("2 תיקונים", text)
        self.assertIn("1 ממצאים בחומרה גבוהה", text)
        self.assertIn("1 מוחזקים", text)
        self.assertIn("שעות", text)

    def test_the_owner_queue_keeps_one_row_per_finding(self):
        items = [dict(FINDING, id="A-9", kind="source_conflict")]
        qc_run.update_owner_queue(self.root, TAG, items)
        qc_run.update_owner_queue(self.root, TAG, items)
        doc = qc_store.read_json(self.root / "owner_queue.json")
        self.assertEqual(len(doc["items"]), 1)
        self.assertEqual(doc["items"][0]["page"], 69)
        self.assertEqual(doc["items"][0]["status"], "open")


# ── the three gaps found by re-reading qc_run against the modules it drives ──
#
# qc_judge and qc_fix were finished AFTER qc_run was written, and every test
# above drives qc_run through fakes. Fakes agree with whatever the fake author
# believed, so three rules the spec pays for were being dropped between the
# real modules and this one and no test noticed. Each class below pins one, and
# each was confirmed against the REAL module before it was written.


class PresentationNoticeTests(RunCase):
    """§5.10.5 — the one notice that answer order was rebalanced.

    Confirmed against the real qc_fix on tests/qc/fixtures/quiz/study-019…:
    `apply_hygiene` returns 2 rows of `{kind: "presentation",
    check: "key_position", fixed: True}` and files them in `changes`, which
    qc_run stores under the verdict's HYGIENE record. Nothing of it reaches
    `verdict["fixes"]`, which holds Tier-1/Tier-2 content corrections only — so
    the original check (`any(kind == "presentation" for f in verdict["fixes"])`)
    could not fire on a real run at all.
    """

    class HygieneThatRebalances:
        def __init__(self, *, fixed=True):
            self.fixed = fixed

        def __call__(self, *, tag, assets, root, **kw):
            # Exactly the row shape the real rebalance_keys logs, including the
            # `fixed` flag it sets False on an item it declined to move.
            return {"assets": {}, "changes": [
                {"kind": "presentation", "asset": "quiz.json",
                 "check": "key_position", "unit": "Q1", "fixed": self.fixed,
                 "note": "סדר התשובות אוזן; התוכן לא שונה"}]}

    NOTICE = "סדר התשובות אוזן מחדש"

    def titles(self):
        return [n["title"] for n in self.notify.sent]

    def test_a_rebalanced_chapter_is_announced_once(self):
        self.run_qc(self.opts(),
                    self.deps(hygiene=self.HygieneThatRebalances()))
        self.assertIn(self.NOTICE, self.titles())
        # Once for the whole run, not once per chapter.
        self.assertEqual(self.titles().count(self.NOTICE), 1)

    def test_a_rebalance_the_fixer_declined_is_not_announced(self):
        # rebalance_keys logs the items it skips (a letter reference, an
        # all/none key). Nothing moved, so there is nothing to warn about.
        self.run_qc(self.opts(),
                    self.deps(hygiene=self.HygieneThatRebalances(fixed=False)))
        self.assertNotIn(self.NOTICE, self.titles())

    def test_a_chapter_with_no_presentation_change_is_not_announced(self):
        self.run_qc(self.opts(), self.deps())
        self.assertNotIn(self.NOTICE, self.titles())

    def test_rebalanced_reads_both_places_a_record_can_live(self):
        row = {"kind": "presentation", "fixed": True}
        self.assertTrue(qc_run.rebalanced({"hygiene": {"changes": [row]}}))
        self.assertTrue(qc_run.rebalanced({"fixes": [row]}))
        self.assertFalse(qc_run.rebalanced({"fixes": [{"kind": "content"}]}))
        self.assertFalse(qc_run.rebalanced({}))


class UncheckedTests(RunCase):
    """§5.8.2 — "Approval requires unchecked locked-value units <= a threshold
    set in calibration (start: 0)".

    qc_judge computes `unchecked` and applies the gate in its own
    `chapter_status`, but qc_run never calls that function: it computes the
    final status itself, and was carrying the number into the verdict's
    coverage block and then ignoring it. A chapter whose locked values nobody
    confirmed against a book was therefore reported `approved`.
    """

    def test_an_unchecked_locked_value_holds_the_chapter(self):
        judge = FakeJudge(responses={"A": {"ok": True, "unchecked": 1}})
        code, _ = self.run_qc(self.opts(), self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_OK)
        v = self.verdict()
        self.assertEqual(v["coverage"]["unchecked_locked_values"], 1)
        # Judged, clean, nothing for the fixer — and still not approved.
        self.assertEqual(v["findings"]["by_kind"], {})
        self.assertEqual(v["status"], "needs_owner")

    def test_a_chapter_with_nothing_unchecked_is_still_approved(self):
        self.books_available()
        code, _ = self.run_qc(self.opts(), self.deps())
        self.assertEqual(code, qc_run.EXIT_OK)
        self.assertEqual(self.verdict()["coverage"]["unchecked_locked_values"],
                         0)
        self.assertEqual(self.verdict()["status"], "approved")

    def test_a_failed_call_contributes_no_counts_either_way(self):
        # The 2026-09-18 incident produced well-formed JSON with invented
        # content. A failed call's `unchecked` is part of that invention, so it
        # may neither clear the chapter nor hold it; the chapter is `unjudged`
        # on its own account.
        judge = FakeJudge(responses={"A": {"ok": False, "reason": "canary",
                                           "unchecked": 7}})
        self.run_qc(self.opts(), self.deps(judge=judge))
        v = self.verdict()
        self.assertEqual(v["status"], "unjudged")
        self.assertEqual(v["coverage"]["unchecked_locked_values"], 0)

    def test_the_unchecked_threshold_matches_the_judge(self):
        # qc_run.MAX_UNCHECKED is a second copy of qc_judge.MAX_UNCHECKED,
        # written out rather than imported so that --dry-run never imports the
        # judge. This is the test that notices if the two drift.
        import qc_judge
        self.assertEqual(qc_run.MAX_UNCHECKED, qc_judge.MAX_UNCHECKED)

    def test_the_gate_is_on_the_status_function_itself(self):
        base = dict(judged=True, lint={}, fixes=[], owner_items=[],
                    unverified_high=0)
        self.assertEqual(qc_run.chapter_status(**base), "approved")
        self.assertEqual(qc_run.chapter_status(**base, unchecked=1),
                         "needs_owner")


class UnplacedFixTests(RunCase):
    """§2.4 "mark, never hide" — a Tier-1 note that was never embedded.

    Confirmed against the real qc_fix: a finding naming a unit the chapter does
    not have comes back as
        {embedded: "none", needs_owner: True, reason: "no unit 'Q99' in …"}
    with `assets` empty — qc_fix changed nothing. qc_run was counting that row
    as a fix, which made `chapter_status` return `fixed`: the chapter passed,
    the queue resumed, and the error it names was embedded nowhere.
    """

    class Tier1ThatCannotPlaceIt:
        def __call__(self, *, tag, findings, assets, units, root, **kw):
            return {"assets": {}, "fixes": [
                dict(f, tier=1, asset="", embedded="none", needs_owner=True,
                     finding_id=f["id"],
                     reason=f"no unit {f['unit']!r} in this chapter")
                for f in findings]}

    def run_with_unplaced(self):
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})
        return self.run_qc(self.opts(),
                           self.deps(judge=judge,
                                     tier1=self.Tier1ThatCannotPlaceIt()))

    def test_an_unplaced_note_holds_the_chapter_for_the_owner(self):
        code, _ = self.run_with_unplaced()
        self.assertEqual(code, qc_run.EXIT_OK)
        v = self.verdict()
        self.assertEqual(v["status"], "needs_owner")
        # The row survives as a record — fixes.json is what qc_revert replays,
        # and the errata page is how the learner still sees the error.
        self.assertEqual(len(v["fixes"]), 1)
        self.assertTrue(v["fixes"][0]["needs_owner"])
        # The asset was still rewritten — by HYGIENE, which cleans the escaped
        # LaTeX these fixtures carry — but it holds no `qc` record, because the
        # fixer embedded none. That distinction is the whole point: bytes
        # changed, the erratum did not land.
        quiz = json.loads(
            (self.root / "work" / TAG / "out" / "quiz.json")
            .read_text(encoding="utf-8"))
        self.assertTrue(all("qc" not in q for q in quiz["questions"]))

    def test_the_finding_not_the_row_reaches_the_owner_queue(self):
        self.run_with_unplaced()
        doc = qc_store.read_json(self.root / "owner_queue.json")
        self.assertEqual(len(doc["items"]), 1)
        row = doc["items"][0]
        self.assertEqual(row["id"], "A-1")
        # `problem` lives on the finding and not on the fix row; it is the
        # sentence the owner actually reads.
        self.assertEqual(row["problem"], FINDING["problem"])
        self.assertEqual(row["page"], 69)

    def test_the_reason_qc_fix_gave_is_kept_for_the_report(self):
        self.run_with_unplaced()
        self.assertTrue(any("Tier 1" in n for n in self.verdict()["notes"]))
        report = (self.root / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("ממתין להחלטת הבעלים", report)

    def test_a_placed_note_still_reports_fixed(self):
        # The control: the same finding through a fixer that DID embed it.
        judge = FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})
        self.run_qc(self.opts(), self.deps(judge=judge))
        self.assertEqual(self.verdict()["status"], "fixed")
        self.assertFalse((self.root / "owner_queue.json").exists())


class SkippedCheckTests(RunCase):
    """§2.3 — "a chapter cannot look clean because a file was absent".

    The book-backed lint checks need `Text BOOKS/`. Under `--no-books`, or on a
    machine without the PDFs, `impossible_citation` and `value_unsourced` never
    run — and §1 measured what that costs: the old page gate read only the
    first page after a book name, so `(StahlNeuro p. 289, 902)` passed in a
    623-page book. A chapter whose citations were never checked must not draw
    §5.11's unqualified "נבדק מול ספרי הלימוד" badge.
    """

    LINT = {"blocking": [],
            "skipped": [{"check": "impossible_citation", "reason": "no PDFs"}]}

    def test_a_skipped_citation_check_blocks_approval(self):
        self.assertEqual(
            qc_run.chapter_status(judged=True, lint=self.LINT, fixes=[],
                                  owner_items=[], unverified_high=0),
            "needs_owner")

    def test_it_does_not_downgrade_a_chapter_that_carries_errata(self):
        # `fixed` claims no cleanliness — it ships visible corrections — so a
        # run without the books still publishes what it did find. Without this,
        # every offline run would bury its own errata.
        self.assertEqual(
            qc_run.chapter_status(judged=True, lint=self.LINT, fixes=[{}],
                                  owner_items=[], unverified_high=0),
            "fixed")

    def test_with_the_books_the_same_chapter_approves(self):
        self.assertEqual(
            qc_run.chapter_status(judged=True, lint={"blocking": [],
                                                     "skipped": []},
                                  fixes=[], owner_items=[], unverified_high=0),
            "approved")

    def test_the_run_says_which_check_never_ran(self):
        self.run_qc(self.opts(), self.deps())
        v = self.verdict()
        self.assertEqual(v["status"], "needs_owner")
        self.assertTrue(any("impossible_citation" in n for n in v["notes"]),
                        f"nothing in the verdict says why: {v['notes']}")


class CoverageGateTests(RunCase):
    """§5.8.2 — "exactly one row per unit in artifacts.md"."""

    def test_a_judge_that_covered_nothing_is_not_approved(self):
        self.books_available()
        judge = FakeJudge(default={"ok": True, "coverage": [], "findings": []})
        self.run_qc(self.opts(), self.deps(judge=judge))
        v = self.verdict()
        self.assertEqual(v["coverage"]["rows"], 0)
        self.assertGreater(len(v["coverage"]["uncovered_units"]), 0)
        self.assertEqual(v["status"], "needs_owner")

    def test_only_a_verified_call_can_cover_a_unit(self):
        # A failed call's coverage rows are part of the same invented JSON its
        # findings are (2026-09-18: confident output from a call that received
        # no PDF), so they may not mark anything as checked.
        judge = FakeJudge(default={"ok": False, "reason": "canary",
                                   "coverage": [{"unit": "Q1",
                                                 "status": "supported"}]})
        self.run_qc(self.opts(), self.deps(judge=judge))
        self.assertEqual(self.verdict()["status"], "unjudged")

    def test_an_asset_no_verified_call_covered_is_not_uploaded(self):
        class OnlyQuiz:
            """§5.7 splits a chapter by page budget; this is a call that took
            the quiz and left facts.json for one that never happened."""

            def __call__(self, *, tag, units, cites, assets, work_dir,
                         page_limit, root, **kw):
                uids = [u.uid for u in units if u.uid.startswith("Q")]
                d = Path(work_dir) / "A"
                d.mkdir(parents=True, exist_ok=True)
                (d / "bundle.pdf").write_bytes(b"%PDF-1.4\n")
                (d / "artifacts.md").write_text("x", encoding="utf-8")
                return [{"call_id": "A", "kind": "A", "units": uids,
                         "bundle": d / "bundle.pdf",
                         "artifacts": d / "artifacts.md", "pages": 5,
                         "manifest": {}}]

        code, _ = self.run_qc(self.opts(no_upload=False),
                              self.deps(sources=OnlyQuiz()))
        self.assertEqual(code, qc_run.EXIT_OK)
        v = self.verdict()
        uploaded = [n for n, r in v["outputs"].items() if r.get("uploaded")]
        self.assertNotIn("facts.json", uploaded)
        self.assertNotIn("flashcards.json", uploaded)
        self.assertIn("quiz.json", uploaded)


class JudgeFlagTests(RunCase):
    """`ok` is the only gate on a whole chapter, so a string has to be read as
    a word. `bool("false")` is True."""

    def judgment(self, raw):
        call = qc_run.PlannedCall(call_id="A", kind="A", units=["Q1"])
        return qc_run._as_judgment(raw, call)

    def test_a_string_no_is_not_a_yes(self):
        for raw in ({"ok": "false"}, {"verified": "no"}, {"ok": "False"},
                    {"ok": ""}, {"verified": "unverified"}):
            with self.subTest(raw=raw):
                self.assertFalse(self.judgment(raw).ok)

    def test_a_string_yes_still_verifies(self):
        for raw in ({"ok": "true"}, {"verified": "yes"}, {"ok": "OK"}):
            with self.subTest(raw=raw):
                self.assertTrue(self.judgment(raw).ok)

    def test_the_finding_flag_and_the_call_flag_agree(self):
        # The asymmetry this closes: _is_verified already read its string while
        # _as_judgment did not, and _as_judgment gates far more material.
        for word in ("no", "false", ""):
            with self.subTest(word):
                self.assertFalse(qc_run._is_verified({"verified": word}))
                self.assertFalse(self.judgment({"ok": word}).ok)


class FailedCallRetryTests(RunCase):
    """A canary failure is a reason to try again later, not a permanent state."""

    def test_a_failed_call_is_not_cached(self):
        bad = FakeJudge(default={"ok": False, "reason": "canary: no K7QX2M"})
        self.run_qc(self.opts(), self.deps(judge=bad))
        self.assertEqual(self.verdict()["status"], "unjudged")
        self.assertFalse(list((self.root / TAG / "judgments").glob("*.json")),
                         "a failed judgment was written to the cache and will "
                         "be replayed for ever")

    def test_the_next_run_asks_the_judge_again(self):
        self.books_available()
        self.run_qc(self.opts(),
                    self.deps(judge=FakeJudge(default={"ok": False,
                                                       "reason": "canary"})))
        good = FakeJudge()
        self.run_qc(self.opts(), self.deps(judge=good))
        self.assertEqual(good.calls, ["A", "B"])
        self.assertEqual(self.verdict()["status"], "approved")

    def test_a_verified_call_is_still_cached(self):
        # The cache's actual job: a resume must not pay twice for an answer.
        first = FakeJudge()
        self.run_qc(self.opts(), self.deps(judge=first))
        second = FakeJudge()
        self.run_qc(self.opts(), self.deps(judge=second))
        self.assertEqual(second.calls, [])


class CrashBreakerTests(RunCase):
    """§5.5's breaker counted verdicts, so a chapter that raised was invisible
    to it — and a judge that crashes on every answer walked the whole backlog."""

    def setUp(self):
        super().setUp()
        for tag in TAGS:
            self.gh.add_release(tag, release_assets())

    class Exploding:
        def __init__(self):
            self.calls = 0

        def __call__(self, **kw):
            self.calls += 1
            raise RuntimeError("json parse failed: unterminated string")

    def test_three_crashes_in_a_row_stop_the_run(self):
        judge = self.Exploding()
        code, out = self.run_qc(self.opts(tags=list(TAGS)),
                                self.deps(judge=judge))
        self.assertEqual(code, qc_run.EXIT_STOP)
        # Stopped AT the third, so the fourth chapter was never started.
        self.assertEqual(judge.calls, qc_run.CIRCUIT_BREAKER_N)
        self.assertIn("STOP", out)

    def test_the_stop_names_the_probe_to_run(self):
        self.run_qc(self.opts(tags=list(TAGS)),
                    self.deps(judge=self.Exploding()))
        stops = [n for n in self.notify.sent if n["priority"] == 5]
        self.assertEqual(len(stops), 1)
        self.assertIn("StahlKids", stops[0]["message"])

    def test_one_crash_does_not_stop_the_run(self):
        calls = {"n": 0}

        def judge(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("json parse failed")
            return {"ok": True, "coverage": [], "findings": []}

        code, _ = self.run_qc(self.opts(tags=list(TAGS)),
                              self.deps(judge=judge))
        self.assertNotEqual(code, qc_run.EXIT_STOP)


class WithdrawnErrataTests(RunCase):
    """§5.9's `withdrawn` is the owner's decision, not a cache entry."""

    FIX = dict(FINDING, tier=1, asset="quiz.json", status="withdrawn",
               finding_id="A-1")

    def seed_withdrawn(self):
        """What qc_revert leaves behind after the owner rejects an erratum."""
        d = self.root / TAG
        d.mkdir(parents=True, exist_ok=True)
        qc_store.write_json_atomic(d / "fixes.json",
                                   {"tag": TAG, "fixes": [dict(self.FIX)]})

    def judge(self):
        return FakeJudge(responses={"A": {"ok": True, "findings": [FINDING]}})

    def test_a_withdrawn_finding_is_not_re_applied(self):
        self.seed_withdrawn()
        self.run_qc(self.opts(), self.deps(judge=self.judge()))
        applied = [f["id"] for call in self.tier1.calls for f in call]
        self.assertNotIn("A-1", applied)

    def test_the_withdrawal_survives_the_run(self):
        self.seed_withdrawn()
        self.run_qc(self.opts(), self.deps(judge=self.judge()))
        doc = qc_store.read_json(self.root / TAG / "fixes.json")
        statuses = {f.get("id"): f.get("status") for f in doc["fixes"]}
        self.assertEqual(statuses.get("A-1"), "withdrawn")

    def test_the_run_says_it_skipped_it(self):
        self.seed_withdrawn()
        self.run_qc(self.opts(), self.deps(judge=self.judge()))
        self.assertTrue(any("ביטל" in n for n in self.verdict()["notes"]))

    def test_an_active_fix_is_replaced_not_duplicated(self):
        # The ordinary path: a re-run of a chapter whose note still stands
        # updates the row rather than appending a second copy of it.
        self.run_qc(self.opts(), self.deps(judge=self.judge()))
        self.run_qc(self.opts(), self.deps(judge=self.judge()))
        doc = qc_store.read_json(self.root / TAG / "fixes.json")
        self.assertEqual([f["id"] for f in doc["fixes"]], ["A-1"])


class ErratumShapeTests(RunCase):
    """qc.json is the only thing that carries a verdict to the learner, and the
    app is the only reader that matters (owner decision 1, 2026-09-18)."""

    def erratum(self):
        self.run_qc(self.opts(),
                    self.deps(judge=FakeJudge(
                        responses={"A": {"ok": True, "findings": [FINDING]}})))
        doc = qc_store.read_json(self.root / TAG / "qc.json")
        self.assertEqual(len(doc["errata"]), 1)
        return doc, doc["errata"][0]

    def test_the_source_is_nested_where_the_app_reads_it(self):
        _, e = self.erratum()
        self.assertEqual(e["source"]["book"], "StahlKids")
        self.assertEqual(e["source"]["printed_page"], 69)
        self.assertIn("2-8 weeks", e["source"]["quote"])

    def test_the_corrected_value_and_the_asset_come_through(self):
        _, e = self.erratum()
        self.assertEqual(e["now"], FINDING["correction"])
        self.assertEqual(e["was"], FINDING["said"])
        self.assertEqual(e["asset"], "quiz.json")

    def test_the_kind_is_the_one_the_app_accepts(self):
        # quiz_logic.qc_mark drops any record whose kind is not exactly
        # "content"; the FINDING kind is a different taxonomy and is kept apart.
        _, e = self.erratum()
        self.assertEqual(e["kind"], "content")
        self.assertEqual(e["finding_kind"], "contradicted")

    def test_the_document_carries_a_date_and_per_asset_hashes(self):
        doc, _ = self.erratum()
        self.assertRegex(doc["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(len(doc["assets"]["quiz.json"]["sha256"]), 64)
        # This run is `use_books=False` (RunCase.opts), so the two book-backed
        # lint checks never ran. §2.3 — "a chapter cannot look clean because a
        # file was absent" — was enforced against `approved` only, but the app
        # reads this per-asset status and draws "נבדק, ללא תיקונים" from
        # anything outside quiz_logic._NOT_CHECKED, `fixed` included. So the
        # chapter's status is stamped on an asset only when the checks that
        # would justify it ran, and qc.json now names the ones that did not.
        self.assertEqual(doc["assets"]["quiz.json"]["status"],
                         qc_run.PARTIAL_ASSET_STATUS)
        self.assertEqual(doc["assets"]["quiz.json"]["chapter_status"],
                         doc["status"])
        self.assertIn("impossible_citation", doc["skipped"])

    def test_with_the_books_the_asset_carries_the_chapter_status(self):
        """The other half of the rule above: nothing skipped, nothing hedged."""
        self.books_available()
        doc, _ = self.erratum()
        self.assertEqual(doc["skipped"], [])
        self.assertEqual(doc["assets"]["quiz.json"]["status"], doc["status"])

    def test_the_video_is_declared_unchecked(self):
        doc, _ = self.erratum()
        self.assertEqual(doc["assets"]["video.mp4"]["status"], "not_checked")
        self.assertIn("video.mp4", doc["not_checked"])

    def test_a_presentation_fix_is_never_an_erratum(self):
        # §5.9: the answer-order rebalance "carries no app marker".
        rows = qc_run.errata_lines(
            {"fixes": [{"id": "p1", "kind": "presentation", "fixed": True},
                       {"id": "c1", "kind": "content", "said": "x",
                        "correction": "y", "book": "DSM", "printed": 135,
                        "source_quote": "y"}]})
        self.assertEqual([r["id"] for r in rows], ["c1"])


class RealCollaboratorTests(unittest.TestCase):
    """The real qc_sources / qc_judge / qc_fix, bound and signature-checked.

    Offline: binding imports the modules but calls nothing, so no agy, no gh
    and no PDF is touched. This is the test that would have caught the three
    defects above, and the only one here that does not run through a fake —
    every other test in this file agrees with its own fakes by construction,
    which is exactly how qc_run drifted from the modules it drives.
    """

    # What ChapterRun actually sends each collaborator, keyword for keyword.
    SENDS = {
        "sources": ("qc_sources", qc_run._SOURCES_NAMES,
                    ("tag", "units", "cites", "assets", "work_dir",
                     "page_limit", "root")),
        "judge": ("qc_judge", qc_run._JUDGE_NAMES,
                  ("tag", "call", "plan", "model", "root", "work_dir",
                   "round_no")),
        "hygiene": ("qc_fix", qc_run._HYGIENE_NAMES, ("tag", "assets", "root")),
        "tier1": ("qc_fix", qc_run._TIER1_NAMES,
                  ("tag", "findings", "assets", "units", "root")),
        "tier2": ("qc_fix", qc_run._TIER2_NAMES,
                  ("tag", "findings", "assets", "units", "root", "model",
                   "work_dir")),
        "revert": ("qc_fix", qc_run._REVERT_NAMES,
                   ("tag", "findings", "assets", "root")),
    }

    def test_every_collaborator_binds_to_a_real_function(self):
        for what, (module, names, _) in self.SENDS.items():
            with self.subTest(what):
                fn = qc_run._bind(module, names, what)
                self.assertTrue(callable(fn))

    def test_every_collaborator_accepts_the_keywords_qc_run_sends(self):
        import inspect
        for what, (module, names, kwargs) in self.SENDS.items():
            with self.subTest(what):
                fn = qc_run._bind(module, names, what)
                # bind() raises TypeError on an unexpected or missing keyword,
                # which is the NotWired case — caught here, at test time,
                # instead of 40 minutes into a 24-hour backlog run.
                inspect.signature(fn).bind(**{k: None for k in kwargs})

    def test_the_fixer_returns_the_keys_qc_run_reads_off_it(self):
        import qc_fix
        quiz = (FIXTURES / "quiz.json").read_bytes().decode("utf-8")
        res = qc_fix.apply_hygiene(assets={"quiz.json": quiz}, tag=TAG)
        # `_run_hygiene` reads these two and nothing else.
        self.assertIn("assets", res)
        self.assertIn("changes", res)

    def test_a_presentation_row_carries_the_kind_the_notice_matches_on(self):
        import qc_fix
        quiz = (FIXTURES / "quiz.json").read_bytes().decode("utf-8")
        res = qc_fix.apply_hygiene(assets={"quiz.json": quiz}, tag=TAG)
        rows = [c for c in res["changes"] if c.get("kind") == "presentation"]
        self.assertTrue(rows, "the fixture quiz should rebalance at least one "
                              "key; if this ever stops being true, pick "
                              "another fixture rather than deleting the test")
        # The verdict shape qc_run would build out of it must trip the notice.
        self.assertTrue(qc_run.rebalanced({"hygiene": {"changes": res["changes"]}}))


if __name__ == "__main__":
    unittest.main()
