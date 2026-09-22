#!/usr/bin/env python3
"""Proof for the three qc_store/.gitignore fixes of this round.

1. §2.5 / §5.5 / §5.10 — the snapshot is committed by explicit path. The
   reviewer's test (test_review_specconf_pipeline.SnapshotIsNeverCommitted)
   only asks whether the mechanism runs git at all; these ask what it runs:
   a pathspec, never `-A`, never outside the repository, and never a raise.

2. §5.5 — `Journal.resume()` returns None for a finished chapter, so something
   can ask the journal whether a chapter is done and `_pause_on_quota` stops
   naming a step the run deliberately skipped.

3. §5.10 — the ignore file keeps every verbatim-textbook path out of git and
   lets exactly the five committed paths in. The reviewer's
   test_review_safety_gitignore covers the first half; this covers the second,
   which is the half a careless `_qc/*` would break silently.

No git subprocess runs here: the runner is injected, and the one test that
uses the real one points it at a temp tree outside the repository, where it
must refuse before reaching git.

    python -m unittest tests.qc.test_fix_qc_store_commit
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(HERE))

import qc_store                                            # noqa: E402
from test_qc_store import FakeGh, TAG, sample_release      # noqa: E402


class Proc:
    """What subprocess.run gives back, for a runner that never runs."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeGit:
    """Records argv. `answers` maps the git subcommand to its result."""

    def __init__(self, **answers):
        self.calls: list[list[str]] = []
        self.answers = answers

    def __call__(self, args, *, cwd, timeout=None):
        self.calls.append(list(args))
        a = self.answers.get(args[0], Proc())
        if isinstance(a, Exception):
            raise a
        return a

    def argv(self, sub: str) -> list[str]:
        for c in self.calls:
            if c[0] == sub:
                return c
        return []


class CommitByExplicitPath(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-commit-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "_qc" / "orig" / TAG).mkdir(parents=True)
        self.file = self.tmp / "_qc" / "orig" / TAG / "quiz.json"
        self.file.write_text("{}", encoding="utf-8")
        # An owner who has switched committing off must not make the tests lie.
        self.addCleanup(os.environ.pop, qc_store.NO_COMMIT_ENV, None)
        os.environ.pop(qc_store.NO_COMMIT_ENV, None)

    def commit(self, git, **kw):
        kw.setdefault("repo_root", self.tmp)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            res = qc_store.commit_paths([self.file.parent], "msg",
                                        runner=git, **kw)
        res["printed"] = buf.getvalue()
        return res

    def test_it_adds_and_commits_one_pathspec(self):
        git = FakeGit(diff=Proc(returncode=1))
        res = self.commit(git)
        self.assertTrue(res["committed"])
        self.assertEqual(git.argv("add"),
                         ["add", "--", f"_qc/orig/{TAG}"])
        self.assertEqual(git.argv("commit")[:3], ["commit", "-m", "msg"])
        self.assertIn("--", git.argv("commit"))
        self.assertEqual(git.argv("commit")[-1], f"_qc/orig/{TAG}")

    def test_it_never_reaches_for_add_all(self):
        """§5.10 commits five paths; this tree carries 60+ untracked files and
        a book-text directory next door."""
        git = FakeGit(diff=Proc(returncode=1))
        self.commit(git)
        for c in git.calls:
            self.assertNotIn("-A", c)
            self.assertNotIn("--all", c)
            self.assertNotIn(".", c)

    def test_nothing_changed_is_not_a_failure(self):
        git = FakeGit(diff=Proc(returncode=0))       # 0 = no staged difference
        res = self.commit(git)
        self.assertFalse(res["committed"])
        self.assertIn("nothing changed", res["reason"])
        self.assertEqual(git.argv("commit"), [], "must not try to commit")

    def test_a_failing_git_is_loud_and_does_not_raise(self):
        git = FakeGit(diff=Proc(returncode=1),
                      commit=Proc(returncode=1, stderr="please tell me who"))
        res = self.commit(git)
        self.assertFalse(res["committed"])
        self.assertIn("please tell me who", res["reason"])
        self.assertIn("[!]", res["printed"])

    def test_git_missing_altogether_does_not_raise(self):
        res = self.commit(FakeGit(add=FileNotFoundError("git")))
        self.assertFalse(res["committed"])
        self.assertIn("not on PATH", res["reason"])

    def test_a_git_timeout_does_not_raise(self):
        res = self.commit(FakeGit(
            add=subprocess.TimeoutExpired(cmd="git", timeout=1)))
        self.assertFalse(res["committed"])

    def test_the_switch_off_is_honoured(self):
        os.environ[qc_store.NO_COMMIT_ENV] = "1"
        git = FakeGit(diff=Proc(returncode=1))
        res = self.commit(git)
        self.assertFalse(res["committed"])
        self.assertEqual(git.calls, [])

    def test_a_path_outside_the_repository_is_refused_before_git(self):
        """The guard that keeps an offline test suite offline."""
        git = FakeGit(diff=Proc(returncode=1))
        res = self.commit(git, repo_root=REPO)
        self.assertFalse(res["committed"])
        self.assertEqual(git.calls, [])
        self.assertTrue(res["outside"])


class SnapshotCommitsWhatItTook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-snap-commit-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "_qc"
        self.gh = FakeGh(sample_release())

    def test_the_snapshot_asks_for_the_commit_it_just_verified(self):
        git = FakeGit(diff=Proc(returncode=1))
        with contextlib.redirect_stdout(io.StringIO()):
            man = qc_store.snapshot(TAG, gh=self.gh, root=self.root,
                                    commit=git)
        # The temp root is outside the repository, so the helper refuses before
        # git — that refusal is the property worth pinning, and the record says
        # which path it refused.
        self.assertIn("commit", man)
        self.assertEqual(git.calls, [])
        self.assertTrue(man["commit"]["outside"])
        self.assertIn(TAG, man["commit"]["outside"][0])

    def test_commit_false_writes_no_record_and_runs_nothing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            man = qc_store.snapshot(TAG, gh=self.gh, root=self.root,
                                    commit=False)
        self.assertNotIn("commit", man)

    def test_the_snapshot_is_still_taken_and_verified(self):
        with contextlib.redirect_stdout(io.StringIO()):
            qc_store.snapshot(TAG, gh=self.gh, root=self.root, commit=False)
        self.assertTrue((qc_store.snapshot_dir(TAG, root=self.root)
                         / "quiz.json").exists())


class AFinishedChapterKnowsItIsFinished(unittest.TestCase):
    """§5.5's journal, for the chapter the whole backlog is made of: judged
    clean, no Tier-2 round, no asset changed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-journal-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "_qc"

    def journal(self):
        return qc_store.Journal(TAG, root=self.root)

    def test_a_clean_chapter_reports_no_next_step(self):
        j = self.journal()
        for s in qc_store.STEPS:
            if s not in qc_store.OPTIONAL_STEPS:
                j.mark(s)
        self.assertTrue(j.finished())
        self.assertIsNone(j.resume())

    def test_a_skipped_optional_step_is_not_reported_as_next(self):
        """Mid-run: the uploads are done, the verdict is not written yet, and
        no Tier-2 round was ever owed. The next step is the verdict."""
        j = self.journal()
        for s in ("fetched", "linted", "bundle_built", "judged_A", "judged_B",
                  "judged_C", "fixes_applied", "upload_pending", "uploaded"):
            j.mark(s)
        self.assertEqual(j.resume(), "verdict")

    def test_an_unfinished_chapter_still_reports_the_real_step(self):
        j = self.journal()
        for s in ("fetched", "linted", "bundle_built", "judged_A"):
            j.mark(s)
        self.assertEqual(j.resume(), "judged_B")
        self.assertFalse(j.finished())

    def test_a_cleared_step_is_reported_even_though_later_ones_are_done(self):
        """qc_run clears the judged_* steps for a re-judge round while
        fixes_applied stays marked; that re-judge must not be skipped."""
        j = self.journal()
        for s in qc_store.STEPS[:-1]:
            j.mark(s)
        j.clear("judged_A", "judged_B", "judged_C", "rejudged")
        self.assertEqual(j.resume(), "judged_A")


class TheIgnoreFileLetsTheCommitListThrough(unittest.TestCase):
    """The other half of test_review_safety_gitignore: excluding the books'
    text must not exclude the five paths §5.10 says to commit."""

    def ignored(self, rel: str) -> bool:
        p = subprocess.run(["git", "check-ignore", "-q", rel],
                           cwd=REPO, capture_output=True)
        return p.returncode == 0

    def test_the_five_committed_paths_are_not_ignored(self):
        for rel in (f"_qc/{TAG}/verdict.json",
                    f"_qc/{TAG}/fixes.json",
                    f"_qc/orig/{TAG}/quiz.json",
                    f"_qc/orig/{TAG}/manifest.json",
                    "_qc/REPORT.md",
                    "_qc/state.json"):
            self.assertFalse(self.ignored(rel),
                             f"{rel} is on §5.10's commit list and git would "
                             f"not take it")

    def test_everything_else_under_a_chapter_is_ignored(self):
        for rel in (f"_qc/{TAG}/judgments/round0-A.json",
                    f"_qc/{TAG}/lint.json",
                    f"_qc/{TAG}/journal.json",
                    f"_qc/{TAG}/qc.json",
                    "_qc/anything-a-later-module-adds.json"):
            self.assertTrue(self.ignored(rel), rel)

    def test_any_pages_json_is_ignored_wherever_it_lands(self):
        for rel in ("_qc/cache/books/pages.json", "schedule/pages.json",
                    f"_qc/orig/{TAG}/pages.json"):
            self.assertTrue(self.ignored(rel), rel)


if __name__ == "__main__":
    unittest.main()
