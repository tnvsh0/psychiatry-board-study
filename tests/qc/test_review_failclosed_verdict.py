#!/usr/bin/env python3
"""Adversarial review, lens FAIL-CLOSED: paths that reach a clean verdict
without a verified check behind it.

Every test here FAILS on the code as reviewed and is meant to keep failing
until the hole is closed. Nothing here touches GitHub, agy or the network: the
fakes are test_qc_run.py's own, reused so that a reviewer can compare the
passing test beside the failing one.

  python -m unittest tests.qc.test_review_failclosed_verdict
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "app"))
sys.path.insert(0, str(HERE))

import qc_run                                              # noqa: E402
import qc_store                                            # noqa: E402
import quiz_logic                                          # noqa: E402
from test_qc_run import (                                  # noqa: E402
    TAG, FakeJudge, Forbidden, RunCase, release_assets,
)
from test_qc_store import FakeGh                           # noqa: E402

# facts.json with escaped LaTeX in it, so the hygiene pass really changes an
# asset that the sources fake below never plans a call for.
DIRTY_FACTS = json.dumps(
    {"drug_class": {"receptor": "$5\\text{-HT}_{2C}$ antagonism",
                    "onset": "ההשפעה מופיעה תוך 4 שבועות"}},
    ensure_ascii=False, indent=1)


class OnlyQuizSources:
    """A planner that covers the quiz and the cards and nothing else.

    Not a contrived shape: §5.7 splits a chapter by page budget, and a call
    whose bundle would blow the limit is the case the spec's own notes say to
    expect. The point is what the orchestrator does with the units NOBODY
    judged.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, *, tag, units, cites, assets, work_dir, page_limit,
                 root, **kw):
        uids = [u.uid for u in units
                if u.uid[:1] in ("Q", "C") and u.uid[1:2].isdigit()]
        self.calls.append(uids)
        d = Path(work_dir) / "A"
        d.mkdir(parents=True, exist_ok=True)
        bundle = d / "bundle.pdf"
        bundle.write_bytes(b"%PDF-1.4\n" + b"page\n" * 50)
        art = d / "artifacts.md"
        art.write_text("\n".join(f"### {u}" for u in uids), encoding="utf-8")
        return [{"call_id": "A", "kind": "A", "units": uids, "bundle": bundle,
                 "artifacts": art, "pages": 12,
                 "manifest": {"nonce": "nonce-A"}}]


class SkippedChecksTests(RunCase):
    """§2.3: "a chapter cannot look clean because a file was absent"."""

    def test_chapter_status_ignores_the_checks_that_never_ran(self):
        # qc_lint records the book-backed checks it could not run. This is
        # exactly the dict lint_chapter returns on a machine with no
        # `Text BOOKS/`, or under --no-books.
        lint = {
            "blocking": [],
            "skipped": [{"check": "impossible_citation", "reason": "no PDFs"},
                        {"check": "value_unsourced", "reason": "no PDFs"}],
        }
        status = qc_run.chapter_status(judged=True, lint=lint, fixes=[],
                                       owner_items=[], unverified_high=0)
        self.assertNotEqual(
            status, "approved",
            "a chapter whose impossible-citation check never ran was approved")

    def test_a_run_without_the_books_publishes_a_checked_badge(self):
        """End to end: no books, no findings -> `approved`, and the app then
        tells the learner the chapter was checked against the textbooks."""
        code, _ = self.run_qc(self.opts(use_books=False),
                              self.deps(judge=FakeJudge()))
        self.assertEqual(code, qc_run.EXIT_OK)
        v = self.verdict()
        skipped = [s["check"] for s in v["lint"]["skipped"]]
        self.assertIn("impossible_citation", skipped)

        doc = qc_store.read_json(self.root / TAG / "qc.json")
        badge = quiz_logic.badge_text(quiz_logic.parse_qc_json(doc))
        self.assertNotEqual(
            v["status"], "approved",
            f"status={v['status']} with {skipped} never run; the app badge "
            f"reads {badge!r}")


class JudgeSelfReportTests(RunCase):
    """The `ok` flag is the only gate on a whole chapter, so reading it has to
    be at least as strict as `_is_verified` is on a single finding."""

    def test_a_judge_that_says_verified_no_is_read_as_verified(self):
        call = qc_run.PlannedCall(call_id="A", kind="A", units=["Q1"])
        # qc_judge does not exist yet; its contract (qc_run's own
        # needs_changes_elsewhere) is "ok (or verified, or status in ...)".
        # A judge that answers the string "no" is not answering yes.
        j = qc_run._as_judgment({"verified": "no", "reason": "canary failed"},
                                call)
        self.assertFalse(
            j.ok, "a judgment reporting verified='no' was taken as verified")

    def test_a_judge_that_says_ok_false_as_a_string_is_read_as_verified(self):
        call = qc_run.PlannedCall(call_id="A", kind="A", units=["Q1"])
        j = qc_run._as_judgment({"ok": "false", "reason": "parse error"}, call)
        self.assertFalse(
            j.ok, "a judgment reporting ok='false' was taken as verified")

    def test_the_same_string_is_refused_on_a_finding(self):
        """The asymmetry itself: _is_verified gets this right, _as_judgment
        does not, and the second one gates more material than the first."""
        self.assertFalse(qc_run._is_verified({"verified": "no"}))

    def test_a_judge_that_covered_nothing_still_approves_the_chapter(self):
        """An empty coverage list is not evidence of anything, and it is the
        shape a judge that answered from memory returns (§5.8.2's echo check is
        what catches that, and the orchestrator never looks at its result)."""
        code, _ = self.run_qc(
            self.opts(use_books=False),
            self.deps(judge=FakeJudge(default={"ok": True, "coverage": [],
                                               "findings": []})))
        self.assertEqual(code, qc_run.EXIT_OK)
        v = self.verdict()
        self.assertEqual(v["coverage"]["rows"], 0)
        self.assertGreater(v["coverage"]["units"], 0)
        self.assertNotEqual(
            v["status"], "approved",
            f"{v['coverage']['units']} units, 0 coverage rows, status "
            f"{v['status']}")


class UncoveredAssetTests(RunCase):
    """§5.12: an asset may be republished only under a verdict that covers it."""

    def setUp(self):
        super().setUp()
        self.gh = FakeGh()
        self.gh.add_release(TAG, release_assets(
            **{"facts.json": DIRTY_FACTS.encode("utf-8")}))
        self.sources = OnlyQuizSources()

    def test_an_asset_no_call_judged_is_still_uploaded(self):
        code, _ = self.run_qc(self.opts(no_upload=False),
                              self.deps(judge=FakeJudge(),
                                        tier1=Forbidden("tier 1")))
        self.assertEqual(code, qc_run.EXIT_OK)
        v = self.verdict()
        # The units the judge was actually given: quiz and flashcard uids only,
        # never a facts.json unit.
        planned = set(self.sources.calls[0])
        self.assertFalse([u for u in planned if u.startswith("F:")],
                         "the fake planner was supposed to skip facts.json")

        uploaded = [name for name, rec in v["outputs"].items()
                    if rec.get("uploaded")]
        self.assertNotIn(
            "facts.json", uploaded,
            "facts.json was republished although no judge call covered a "
            "single one of its units")


if __name__ == "__main__":
    unittest.main()
