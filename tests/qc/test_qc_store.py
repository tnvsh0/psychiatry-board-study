#!/usr/bin/env python3
"""Offline tests for scripts/qc_store.py and scripts/qc_revert.py.

Everything GitHub runs through FakeGh, an in-memory release. That is not a
convenience: the writes under test are renames on assets the owner studies
from, and a test that reached the real repository could publish a
`.pre-qc-<date>` rename onto a live chapter. There is no code path here that
constructs a real Gh against a real tag.

    python -m unittest discover -s tests/qc -t .
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import qc_revert                                          # noqa: E402
import qc_store                                           # noqa: E402
from qc_store import (                                    # noqa: E402
    GhError, Journal, QcStoreError, SnapshotConflict, Stale, UploadUnresolved,
    sha256_bytes,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "release_assets"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ── the fake release ────────────────────────────────────────────────────────
class FakeGh:
    """An in-memory stand-in for qc_store.Gh, with injectable failures.

    Two kinds of failure, because they are genuinely different for a resume:
      fail_on     the call raises BEFORE doing anything (the request never
                  reached GitHub)
      fail_after  the call does its work and THEN raises (GitHub did it; the
                  answer was lost). This is the case that breaks any resume
                  which trusts the journal instead of the release.
    """

    def __init__(self, releases=None, repo="fake/psychiatry-board-study",
                 report_digest=True):
        self.repo = repo
        self.report_digest = report_digest
        self._rel: dict[str, dict] = {}
        self._ids: dict[tuple[str, str], int] = {}
        self._next_id = 1000
        self.calls: list[tuple[str, tuple]] = []
        self.counts: dict[str, int] = {}
        self.fail_on: dict[str, int] = {}
        self.fail_after: dict[str, int] = {}
        for tag, spec in (releases or {}).items():
            self.add_release(tag, spec.get("assets", {}),
                             draft=spec.get("draft", False))

    # -- building ----------------------------------------------------------
    def add_release(self, tag, assets=None, *, draft=False):
        self._rel[tag] = {"draft": draft, "assets": {}}
        for name, data in (assets or {}).items():
            self.put(tag, name, data)
        return self

    def put(self, tag, name, data: bytes):
        """Install bytes directly — used to set a release up, and to simulate
        somebody else replacing an asset behind QC's back."""
        self._rel.setdefault(tag, {"draft": False, "assets": {}})
        if (tag, name) not in self._ids:
            self._next_id += 1
            self._ids[(tag, name)] = self._next_id
        self._rel[tag]["assets"][name] = data
        return self

    def names(self, tag) -> list[str]:
        return sorted(self._rel[tag]["assets"])

    def data(self, tag, name) -> bytes:
        return self._rel[tag]["assets"][name]

    # -- failure injection -------------------------------------------------
    def _tick(self, method, args, *, after=False):
        self.counts[method] = self.counts.get(method, 0) + (0 if after else 1)
        n = self.counts[method]
        table = self.fail_after if after else self.fail_on
        if table.get(method) == n:
            raise GhError(f"injected failure {'after' if after else 'on'} "
                          f"{method} call {n}")

    # -- the Gh interface --------------------------------------------------
    def list_releases(self):
        self.calls.append(("list_releases", ()))
        self._tick("list_releases", ())
        return [{"tagName": t, "isDraft": r["draft"]}
                for t, r in sorted(self._rel.items())]

    def list_assets(self, tag):
        self.calls.append(("list_assets", (tag,)))
        self._tick("list_assets", (tag,))
        rel = self._rel.get(tag)
        if rel is None:
            raise GhError(f"release not found: {tag}")
        return [qc_store.Asset(
            name=n, asset_id=self._ids[(tag, n)], size=len(d),
            sha256=sha256_bytes(d) if self.report_digest else None)
            for n, d in sorted(rel["assets"].items())]

    def asset(self, tag, name):
        for a in self.list_assets(tag):
            if a.name == name:
                return a
        return None

    def download(self, tag, name, dest):
        self.calls.append(("download", (tag, name)))
        self._tick("download", (tag, name))
        rel = self._rel.get(tag) or {"assets": {}}
        if name not in rel["assets"]:
            raise GhError(f"{tag}: no asset {name}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(rel["assets"][name])
        self._tick("download", (tag, name), after=True)
        return dest

    def upload(self, tag, path, name=None):
        name = name or Path(path).name
        self.calls.append(("upload", (tag, name)))
        self._tick("upload", (tag, name))
        if name in self._rel[tag]["assets"]:
            # Exactly what gh does without its overwrite flag, and the reason
            # the swap can never clobber a live asset by accident.
            raise GhError(f"{tag}: asset {name} already exists")
        self.put(tag, name, Path(path).read_bytes())
        self._tick("upload", (tag, name), after=True)

    def rename_asset(self, asset_id, new_name):
        self.calls.append(("rename_asset", (asset_id, new_name)))
        self._tick("rename_asset", (asset_id, new_name))
        for (tag, name), aid in list(self._ids.items()):
            if aid != asset_id or name not in self._rel[tag]["assets"]:
                continue
            if new_name in self._rel[tag]["assets"]:
                raise GhError(f"{tag}: {new_name} already exists")
            data = self._rel[tag]["assets"].pop(name)
            self._rel[tag]["assets"][new_name] = data
            del self._ids[(tag, name)]
            self._ids[(tag, new_name)] = aid      # the id survives a rename
            self._tick("rename_asset", (asset_id, new_name), after=True)
            return new_name
        raise GhError(f"no asset with id {asset_id}")

    def delete_asset(self, asset_id):
        self.calls.append(("delete_asset", (asset_id,)))
        self._tick("delete_asset", (asset_id,))
        for (tag, name), aid in list(self._ids.items()):
            if aid == asset_id and name in self._rel[tag]["assets"]:
                del self._rel[tag]["assets"][name]
                del self._ids[(tag, name)]
                self._tick("delete_asset", (asset_id,), after=True)
                return
        raise GhError(f"no asset with id {asset_id}")


TAG = "study-005-serotonin-system"


def sample_release(**extra) -> dict:
    assets = {
        "quiz.json": fixture("quiz.json"),
        "flashcards.json": fixture("flashcards.json"),
        "study_guide.md": fixture("study_guide.md"),
        "sketchy_main.txt": "מספרים לבחינה: 5HT1A\n".encode("utf-8"),
        "video.mp4": b"\x00\x01binary-not-text\x02",
    }
    assets.update(extra)
    return {TAG: {"assets": assets}}


class TmpRootCase(unittest.TestCase):
    """Every test gets its own _qc/ tree; nothing writes into the repo."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-test-"))
        self.root = self.tmp / "_qc"
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# ── chapter tags ────────────────────────────────────────────────────────────
def _app_functions():
    """slugify/day_tag lifted out of app/app.py without importing it.

    Importing app.py executes the whole Streamlit page at module level, so the
    only way to test the copy in qc_store against the original is to compile
    those two function definitions on their own. app.py's slugify does its
    `import re` inside the function, so the pair is self-contained.
    """
    src = (REPO / "app" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"slugify", "day_tag"}
    defs = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in wanted]
    if {d.name for d in defs} != wanted:
        raise AssertionError(f"app/app.py no longer defines {wanted} at module "
                             f"level; found {[d.name for d in defs]}")
    ns: dict = {}
    exec(compile(ast.Module(body=defs, type_ignores=[]), "app.py", "exec"), ns)
    return ns["slugify"], ns["day_tag"]


class DayTagTests(unittest.TestCase):
    def test_matches_app_on_every_scheduled_day(self):
        app_slugify, app_day_tag = _app_functions()
        days = qc_store.load_days()
        self.assertEqual(len(days), 144, "the curriculum is 144 chapters")
        for d in days:
            self.assertEqual(qc_store.day_tag(d), app_day_tag(d),
                             f"day {d.get('index')} tags differ")
            self.assertEqual(qc_store.slugify(d.get("title_en", "")),
                             app_slugify(d.get("title_en", "")))

    def test_tags_are_unique(self):
        tags = [qc_store.day_tag(d) for d in qc_store.load_days()]
        self.assertEqual(len(tags), len(set(tags)))


class ChaptersTests(unittest.TestCase):
    def test_marks_released_and_separates_orphans(self):
        gh = FakeGh({
            "study-001-psychiatric-examination-of-child-and-adolescent":
                {"assets": {"quiz.json": b"{}"}},
            "study-017-adhd-core": {"assets": {"quiz.json": b"{}"}},
            "study-072-something": {"assets": {}, "draft": True},
            # Real orphan: chapter 16 was renumbered, and the old release is
            # still published under a tag the schedule no longer produces.
            "study-016-adhd-core": {"assets": {"quiz.json": b"{}"}},
        })
        chs, orphans = qc_store.chapters(gh=gh)
        self.assertEqual(len(chs), 144)
        by_tag = {c.tag: c for c in chs}
        self.assertTrue(by_tag["study-017-adhd-core"].released)
        self.assertFalse(
            by_tag["study-016-principles-of-pediatric-psychopharmacology"]
            .released)
        self.assertEqual(orphans, ["study-016-adhd-core",
                                   "study-072-something"])

    def test_draft_is_not_released(self):
        gh = FakeGh({"study-017-adhd-core": {"assets": {}, "draft": True}})
        chs, _ = qc_store.chapters(gh=gh)
        c = next(c for c in chs if c.tag == "study-017-adhd-core")
        self.assertFalse(c.released)
        self.assertTrue(c.draft)

    def test_without_gh_release_state_is_unknown_not_false(self):
        chs, orphans = qc_store.chapters()
        self.assertIsNone(chs[0].released)
        self.assertEqual(orphans, [])


class JudgedAssetTests(unittest.TestCase):
    def test_scope_matches_spec_section_3(self):
        for name in ("quiz.json", "flashcards.json", "facts.json",
                     "study_guide.md", "report.md", "sketchy_main.txt"):
            self.assertTrue(qc_store.is_judged(name), name)
        for name in ("sketchy_main.quiz.md", "narration_main.txt", "video.mp4",
                     "audio.mp3", "sketchy_main.png"):
            self.assertFalse(qc_store.is_judged(name), name)

    def test_text_assets(self):
        self.assertTrue(qc_store.is_text_asset("quiz.json"))
        self.assertTrue(qc_store.is_text_asset("narration_main.txt"))
        self.assertFalse(qc_store.is_text_asset("video.mp4"))


# ── fetch ───────────────────────────────────────────────────────────────────
class FetchTests(TmpRootCase):
    def test_text_only_by_default(self):
        gh = FakeGh(sample_release())
        got = qc_store.fetch(TAG, self.tmp / "dl", gh=gh)
        self.assertNotIn("video.mp4", got)
        self.assertEqual(sorted(got), ["flashcards.json", "quiz.json",
                                       "sketchy_main.txt", "study_guide.md"])
        rec = got["quiz.json"]
        self.assertEqual(rec["sha256"], sha256_bytes(fixture("quiz.json")))
        self.assertEqual(rec["size"], len(fixture("quiz.json")))
        self.assertTrue(rec["asset_id"])
        self.assertTrue(Path(rec["path"]).exists())

    def test_named_assets_include_binaries_and_report_missing(self):
        gh = FakeGh(sample_release())
        got = qc_store.fetch(TAG, self.tmp / "dl", gh=gh, names=["video.mp4"])
        self.assertEqual(list(got), ["video.mp4"])
        with self.assertRaises(GhError) as cm:
            qc_store.fetch(TAG, self.tmp / "dl2", gh=gh, names=["nope.json"])
        self.assertIn("nope.json", str(cm.exception))

    def test_truncated_download_is_caught(self):
        gh = FakeGh(sample_release())

        original = gh.download

        def truncating(tag, name, dest):
            p = original(tag, name, dest)
            p.write_bytes(p.read_bytes()[:10])
            return p

        gh.download = truncating
        with self.assertRaises(GhError):
            qc_store.fetch(TAG, self.tmp / "dl", gh=gh, names=["quiz.json"])

    def test_missing_digest_falls_back_to_hashing(self):
        gh = FakeGh(sample_release(), report_digest=False)
        got = qc_store.fetch(TAG, self.tmp / "dl", gh=gh, names=["quiz.json"])
        self.assertEqual(got["quiz.json"]["sha256"],
                         sha256_bytes(fixture("quiz.json")))


# ── snapshot ────────────────────────────────────────────────────────────────
class SnapshotTests(TmpRootCase):
    def test_writes_files_and_manifest(self):
        gh = FakeGh(sample_release())
        man = qc_store.snapshot(TAG, gh=gh, root=self.root)
        d = qc_store.snapshot_dir(TAG, root=self.root)
        self.assertEqual(
            sorted(p.name for p in d.iterdir()),
            ["flashcards.json", "manifest.json", "quiz.json",
             "sketchy_main.txt", "study_guide.md"])
        self.assertNotIn("video.mp4", [e["name"] for e in man["assets"]])
        entry = next(e for e in man["assets"] if e["name"] == "quiz.json")
        self.assertEqual(entry["sha256"], sha256_bytes(fixture("quiz.json")))
        self.assertTrue(entry["asset_id"])
        self.assertTrue(entry["fetched_at"])
        self.assertEqual((d / "quiz.json").read_bytes(), fixture("quiz.json"))

    def test_idempotent_second_call_downloads_nothing(self):
        gh = FakeGh(sample_release())
        qc_store.snapshot(TAG, gh=gh, root=self.root)
        before = gh.counts.get("download", 0)
        again = qc_store.snapshot(TAG, gh=gh, root=self.root)
        self.assertEqual(gh.counts.get("download", 0), before)
        self.assertEqual(len(again["assets"]), 4)

    def test_refuses_to_overwrite_when_the_live_asset_changed(self):
        gh = FakeGh(sample_release())
        qc_store.snapshot(TAG, gh=gh, root=self.root)
        gh.put(TAG, "quiz.json", b'{"questions": []}')     # somebody else
        with self.assertRaises(SnapshotConflict):
            qc_store.snapshot(TAG, gh=gh, root=self.root)
        kept = qc_store.snapshot_dir(TAG, root=self.root) / "quiz.json"
        self.assertEqual(kept.read_bytes(), fixture("quiz.json"),
                         "the pristine copy must survive the refusal")

    def test_refuses_when_the_snapshot_on_disk_was_edited(self):
        gh = FakeGh(sample_release())
        qc_store.snapshot(TAG, gh=gh, root=self.root)
        p = qc_store.snapshot_dir(TAG, root=self.root) / "quiz.json"
        p.write_bytes(b'{"questions": [1]}')
        with self.assertRaises(SnapshotConflict):
            qc_store.snapshot(TAG, gh=gh, root=self.root)

    def test_rejects_an_empty_asset_and_leaves_nothing_behind(self):
        gh = FakeGh(sample_release(**{"facts.json": b""}))
        with self.assertRaises(QcStoreError):
            qc_store.snapshot(TAG, gh=gh, root=self.root)
        self.assertFalse(
            (qc_store.snapshot_dir(TAG, root=self.root) / "facts.json").exists())

    def test_rejects_json_that_does_not_parse(self):
        gh = FakeGh(sample_release(**{"facts.json": b"{not json"}))
        with self.assertRaises(QcStoreError) as cm:
            qc_store.snapshot(TAG, gh=gh, root=self.root)
        self.assertIn("does not parse", str(cm.exception))

    def test_a_new_asset_is_added_without_touching_the_others(self):
        gh = FakeGh(sample_release())
        qc_store.snapshot(TAG, gh=gh, root=self.root)
        gh.put(TAG, "facts.json", b'{"a": 1}')
        man = qc_store.snapshot(TAG, gh=gh, root=self.root)
        self.assertIn("facts.json", [e["name"] for e in man["assets"]])
        self.assertEqual(len(man["assets"]), 5)


# ── journal and state ───────────────────────────────────────────────────────
class JournalTests(TmpRootCase):
    def test_resume_walks_the_steps_in_order(self):
        j = Journal(TAG, root=self.root)
        self.assertEqual(j.resume(), "fetched")
        j.mark("fetched", assets=4)
        self.assertEqual(j.resume(), "linted")
        for s in qc_store.STEPS:
            j.mark(s)
        self.assertIsNone(j.resume())

    def test_a_reopened_journal_sees_what_was_written(self):
        Journal(TAG, root=self.root).mark("fetched", n=3)
        j = Journal(TAG, root=self.root)
        self.assertTrue(j.done("fetched"))
        self.assertEqual(j.data["steps"]["fetched"]["detail"], {"n": 3})
        self.assertEqual(j.resume(), "linted")

    def test_clear_sends_a_step_back_to_be_redone(self):
        j = Journal(TAG, root=self.root)
        for s in ("fetched", "linted", "bundle_built", "judged_A"):
            j.mark(s)
        j.clear("judged_A")
        self.assertEqual(j.resume(), "judged_A")

    def test_unknown_step_is_refused(self):
        with self.assertRaises(QcStoreError):
            Journal(TAG, root=self.root).mark("judged_D")

    def test_every_write_leaves_valid_json(self):
        j = Journal(TAG, root=self.root)
        j.mark("fetched")
        j.set_upload("quiz.json", stage="checked")
        json.loads(j.path.read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in j.path.parent.glob("*.tmp*")], [])


class StateTests(TmpRootCase):
    def test_roundtrip_and_clear(self):
        qc_store.set_run_state(TAG, "in_progress", root=self.root, step="linted")
        self.assertEqual(qc_store.get_run_state(TAG, root=self.root)["state"],
                         "in_progress")
        qc_store.clear_run_state(TAG, root=self.root)
        self.assertIsNone(qc_store.get_run_state(TAG, root=self.root))

    def test_paused_quota_must_say_when_and_what(self):
        with self.assertRaises(QcStoreError):
            qc_store.set_run_state(TAG, "paused_quota", root=self.root)
        with self.assertRaises(QcStoreError):
            qc_store.set_run_state(TAG, "paused_quota", root=self.root,
                                   resume_after="2026-09-23T04:00:00+00:00")
        rec = qc_store.set_run_state(
            TAG, "paused_quota", root=self.root, step="judged_B",
            resume_after="2026-09-23T04:00:00+00:00")
        self.assertEqual(rec["step"], "judged_B")

    def test_unknown_state_is_refused(self):
        with self.assertRaises(QcStoreError):
            qc_store.set_run_state(TAG, "probably_fine", root=self.root)

    def test_runs_do_not_overwrite_each_other(self):
        qc_store.set_run_state("a", "in_progress", root=self.root)
        qc_store.set_run_state("b", "stale", root=self.root)
        runs = qc_store.load_state(root=self.root)["runs"]
        self.assertEqual(sorted(runs), ["a", "b"])


# ── safe upload ─────────────────────────────────────────────────────────────
NEW_QUIZ = json.dumps({"title": "corrected", "questions": []},
                      ensure_ascii=False).encode("utf-8")
TODAY = datetime(2026, 9, 22)


class SafeUploadTests(TmpRootCase):
    def setUp(self):
        super().setUp()
        self.gh = FakeGh(sample_release())
        self.journal = Journal(TAG, root=self.root)
        self.local = self.tmp / "quiz.json"
        self.local.write_bytes(NEW_QUIZ)
        self.judged = sha256_bytes(fixture("quiz.json"))

    def _upload(self, **kw):
        return qc_store.safe_upload(
            TAG, "quiz.json", self.local, self.judged, gh=self.gh,
            journal=self.journal, root=self.root, today=TODAY, **kw)

    def _resume(self, **kw):
        return qc_store.resume_upload(
            TAG, "quiz.json", gh=self.gh, journal=self.journal,
            root=self.root, today=TODAY, **kw)

    def assert_swapped(self):
        self.assertEqual(self.gh.data(TAG, "quiz.json"), NEW_QUIZ)
        self.assertEqual(self.gh.data(TAG, "quiz.json.pre-qc-20260922"),
                         fixture("quiz.json"))
        leftover = [n for n in self.gh.names(TAG) if ".qc-" in n]
        self.assertEqual(leftover, [], "the temp asset must not survive")

    # -- the ordinary path -------------------------------------------------
    def test_swap_keeps_the_original_under_pre_qc(self):
        res = self._upload()
        self.assertEqual(res["stage"], "done")
        self.assert_swapped()
        self.assertTrue(self.journal.done("uploaded"))
        self.assertIsNone(qc_store.get_run_state(TAG, root=self.root))

    def test_identical_bytes_change_nothing(self):
        self.local.write_bytes(fixture("quiz.json"))
        res = self._upload()
        self.assertFalse(res["changed"])
        self.assertEqual(self.gh.names(TAG), sorted(sample_release()[TAG]
                                                    ["assets"]))

    def test_stale_live_asset_stops_the_upload(self):
        self.gh.put(TAG, "quiz.json", b'{"questions": ["someone else"]}')
        with self.assertRaises(Stale):
            self._upload()
        self.assertEqual(
            qc_store.get_run_state(TAG, root=self.root)["state"], "stale")
        self.assertNotIn("quiz.json.pre-qc-20260922", self.gh.names(TAG))

    def test_a_missing_asset_is_stale_not_a_crash(self):
        del self.gh._rel[TAG]["assets"]["quiz.json"]
        with self.assertRaises(Stale):
            self._upload()

    # -- interruptions -----------------------------------------------------
    def test_resume_after_the_temp_upload(self):
        """Died at step 3: the temp is up and verified, nothing renamed."""
        self.gh.fail_on["rename_asset"] = 1
        with self.assertRaises(GhError):
            self._upload()
        self.assertEqual(
            self.journal.upload_record("quiz.json")["stage"], "temp_verified")
        self.assertEqual(self.gh.data(TAG, "quiz.json"), fixture("quiz.json"))

        self.gh.fail_on.clear()
        res = self._resume()
        self.assertEqual(res["stage"], "done")
        self.assert_swapped()

    def test_resume_after_the_first_rename(self):
        """Died between the two renames: `quiz.json` does not exist at all."""
        self.gh.fail_on["rename_asset"] = 2
        with self.assertRaises(GhError):
            self._upload()
        self.assertNotIn("quiz.json", self.gh.names(TAG))
        self.assertIn("quiz.json.pre-qc-20260922", self.gh.names(TAG))

        self.gh.fail_on.clear()
        res = self._resume()
        self.assertEqual(res["stage"], "done")
        self.assert_swapped()

    def test_resume_when_the_rename_landed_but_the_answer_was_lost(self):
        """The journal says `temp_verified`; GitHub has already renamed.

        This is the case that makes the journal untrustworthy as a source of
        truth, so the resume finds the `.pre-qc-` asset by looking, not by
        reading its own record.
        """
        self.gh.fail_after["rename_asset"] = 1
        with self.assertRaises(GhError):
            self._upload()
        rec = self.journal.upload_record("quiz.json")
        self.assertEqual(rec["stage"], "temp_verified")
        self.assertIsNone(rec.get("pre_qc_name"))
        self.assertNotIn("quiz.json", self.gh.names(TAG))

        self.gh.fail_after.clear()
        res = self._resume()
        self.assertEqual(res["stage"], "done")
        self.assert_swapped()

    def test_resume_is_idempotent_once_the_swap_completed(self):
        self._upload()
        res = self._resume()
        self.assertEqual(res["stage"], "done")
        self.assert_swapped()

    def test_rollback_before_the_first_rename_leaves_the_original_alone(self):
        self.gh.fail_on["rename_asset"] = 1
        with self.assertRaises(GhError):
            self._upload()
        self.gh.fail_on.clear()
        res = self._resume(rollback=True)
        self.assertEqual(res["stage"], "rolled_back")
        self.assertEqual(self.gh.data(TAG, "quiz.json"), fixture("quiz.json"))
        self.assertEqual([n for n in self.gh.names(TAG) if ".qc-" in n], [])
        self.assertIsNone(self.journal.upload_record("quiz.json"))

    def test_rollback_after_the_first_rename_puts_the_original_back(self):
        self.gh.fail_on["rename_asset"] = 2
        with self.assertRaises(GhError):
            self._upload()
        self.gh.fail_on.clear()
        res = self._resume(rollback=True)
        self.assertEqual(res["stage"], "rolled_back")
        self.assertEqual(self.gh.data(TAG, "quiz.json"), fixture("quiz.json"))
        self.assertNotIn("quiz.json.pre-qc-20260922", self.gh.names(TAG))

    def test_lost_temp_and_lost_local_restores_the_original(self):
        self.gh.fail_on["rename_asset"] = 2
        with self.assertRaises(GhError):
            self._upload()
        # The temp asset and the corrected file both vanish (a swept work dir).
        tmp_name = self.journal.upload_record("quiz.json")["temp_name"]
        self.gh.delete_asset(self.gh._ids[(TAG, tmp_name)])
        self.local.unlink()
        self.gh.fail_on.clear()
        with self.assertRaises(UploadUnresolved):
            self._resume()
        self.assertEqual(self.gh.data(TAG, "quiz.json"), fixture("quiz.json"),
                         "the original must be back under its real name")

    def test_resume_without_a_record_refuses(self):
        with self.assertRaises(UploadUnresolved):
            self._resume()

    # -- the temp name -----------------------------------------------------
    def test_only_declared_stages_are_journalled(self):
        """A stage name the resume logic does not know is a silent dead end."""
        self.gh.fail_on["rename_asset"] = 2
        with self.assertRaises(GhError):
            self._upload()
        self.gh.fail_on.clear()
        self._resume()
        seen = {r["stage"] for r in self.journal.data["uploads"].values()}
        self.assertTrue(seen <= set(qc_store.UPLOAD_STAGES), seen)

    def test_a_mid_swap_asset_is_not_reported_as_stale(self):
        """Re-running safe_upload after a crash must not say "re-judge"."""
        self.gh.fail_on["rename_asset"] = 2
        with self.assertRaises(GhError):
            self._upload()
        with self.assertRaises(UploadUnresolved) as cm:
            self._upload()
        self.assertIn("resume_upload", str(cm.exception))

    def test_the_newest_pre_qc_is_picked_by_parsed_date_and_sequence(self):
        across_days = ["quiz.json.pre-qc-20260922-2",
                       "quiz.json.pre-qc-20261001"]
        self.assertEqual(qc_store._latest_pre_qc("quiz.json", across_days),
                         "quiz.json.pre-qc-20261001")
        # Same day, tenth pass: string order puts -10 before -2, so a naive
        # sorted()[-1] would restore the second-oldest copy.
        same_day = [f"quiz.json.pre-qc-20260922-{n}" for n in (2, 9, 10)]
        self.assertEqual(sorted(same_day)[-1], "quiz.json.pre-qc-20260922-9")
        self.assertEqual(qc_store._latest_pre_qc("quiz.json", same_day),
                         "quiz.json.pre-qc-20260922-10")
        self.assertIsNone(qc_store._latest_pre_qc("quiz.json",
                                                  ["quiz.json", "facts.json"]))

    def test_temp_name_carries_the_content_hash(self):
        self.assertEqual(qc_store.temp_asset_name("quiz.json", "abcdef1234"),
                         "quiz.json.qc-abcdef12")

    def test_a_second_pass_on_the_same_day_does_not_collide(self):
        self._upload()
        second = self.tmp / "quiz2.json"
        second.write_bytes(b'{"title": "again"}')
        qc_store.safe_upload(TAG, "quiz.json", second, sha256_bytes(NEW_QUIZ),
                             gh=self.gh, journal=self.journal, root=self.root,
                             today=TODAY)
        self.assertIn("quiz.json.pre-qc-20260922", self.gh.names(TAG))
        self.assertIn("quiz.json.pre-qc-20260922-2", self.gh.names(TAG))
        self.assertEqual(self.gh.data(TAG, "quiz.json"), b'{"title": "again"}')


# ── the gh command lines ────────────────────────────────────────────────────
class GhCommandTests(unittest.TestCase):
    """Pin the argv Gh builds. These are the only lines in the QC mechanism
    that can change a live release, and the overwrite flag must never appear
    in one of them (spec §2.5, §5.5)."""

    def setUp(self):
        self.gh = qc_store.Gh(repo="o/r")
        self.seen: list[list[str]] = []

        def recorder(args, *, timeout=None, retries=1):
            self.seen.append(list(args))
            if args[:2] == ["release", "download"]:
                Path(args[args.index("--output") + 1]).write_bytes(b"x")
                return ""
            return "{}"

        self.gh._run = recorder

    def test_upload_names_the_asset_by_staging_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "quiz.json"
            p.write_bytes(b"{}")
            self.gh.upload("t", p, "quiz.json.qc-deadbeef")
        argv = self.seen[0]
        self.assertEqual(argv[:3], ["release", "upload", "t"])
        self.assertTrue(argv[3].endswith("quiz.json.qc-deadbeef"))
        self.assertNotIn("--clobber", argv)

    def test_rename_uses_the_rest_asset_endpoint(self):
        self.gh.rename_asset(526373593, "quiz.json.pre-qc-20260922")
        self.assertEqual(self.seen[0], [
            "api", "-X", "PATCH", "repos/o/r/releases/assets/526373593",
            "-f", "name=quiz.json.pre-qc-20260922"])

    def test_rename_without_a_numeric_id_is_refused(self):
        with self.assertRaises(GhError):
            self.gh.rename_asset(0, "whatever")

    def test_download_addresses_one_asset_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            self.gh.download("t", "quiz.json", Path(d) / "out.json")
        argv = self.seen[0]
        self.assertIn("--pattern", argv)
        self.assertEqual(argv[argv.index("--pattern") + 1], "quiz.json")
        self.assertNotIn("--clobber", argv)

    def test_a_glob_in_an_asset_name_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(GhError):
                self.gh.download("t", "quiz*.json", Path(d) / "out.json")

    def test_asset_id_is_parsed_from_apiurl_not_the_node_id(self):
        gh = qc_store.Gh(repo="o/r")
        gh._run = lambda *a, **k: json.dumps({"assets": [{
            "name": "facts.json", "size": 6742, "state": "uploaded",
            "id": "RA_kwDOSZjr4c4fX9LZ",
            "digest": "sha256:" + "b9" * 32,
            "apiUrl": "https://api.github.com/repos/o/r/releases/assets/526373593",
        }]})
        a = gh.list_assets("t")[0]
        self.assertEqual(a.asset_id, 526373593)
        self.assertEqual(a.sha256, "b9" * 32)


class NoOverwriteFlagTests(unittest.TestCase):
    def test_the_qc_modules_never_reach_for_it(self):
        for name in ("qc_store.py", "qc_revert.py"):
            src = (REPO / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("--clobber", src, name)
            self.assertNotIn("clobber", src, name)


# ── keep_awake ──────────────────────────────────────────────────────────────
class KeepAwakeTests(unittest.TestCase):
    def test_it_is_a_context_manager_that_always_releases(self):
        with qc_store.keep_awake():
            pass
        with self.assertRaises(ValueError):
            with qc_store.keep_awake():
                raise ValueError("boom")


# ── qc_revert ───────────────────────────────────────────────────────────────
FIXED_QUIZ = b'{"title": "fixed", "questions": [1, 2]}'


def fake_applier(asset, text, fixes):
    """Stands in for qc_fix.apply_fix. Appends a marker line per fix, which is
    enough to tell "replayed" from "not replayed" without pulling in a module
    this task does not own."""
    for f in fixes:
        text = text.rstrip() + f"\n<!-- qc:{f['id']} -->"
    return text


class RevertTests(TmpRootCase):
    def setUp(self):
        super().setUp()
        self.gh = FakeGh(sample_release())
        qc_store.snapshot(TAG, gh=self.gh, root=self.root)
        # The release now carries the corrected quiz, as it would after qc_fix.
        self.gh.put(TAG, "quiz.json", FIXED_QUIZ)
        self.fixes = {"tag": TAG, "fixes": [
            {"id": "f1", "asset": "quiz.json", "unit": "Q1",
             "status": "active"},
            {"id": "f2", "asset": "study_guide.md", "unit": "G:מינון",
             "status": "active"},
            {"id": "f0", "asset": "quiz.json", "unit": "Q9",
             "status": "withdrawn"},
        ]}
        qc_store.write_json_atomic(
            qc_revert.fixes_path(TAG, root=self.root), self.fixes)

    def _plan(self, **kw):
        kw.setdefault("gh", self.gh)
        kw.setdefault("root", self.root)
        return qc_revert.plan(TAG, **kw)

    def _patch_gh(self):
        """Make the CLI's own `Gh()` hand back the fake.

        Without this the --dry-run test built a real Gh and read the live
        study-005 release over the network — an offline suite that was quietly
        online, and one typo away from a write against the owner's material.
        """
        original = qc_revert.Gh
        qc_revert.Gh = lambda *a, **k: self.gh
        self.addCleanup(setattr, qc_revert, "Gh", original)

    def test_all_restores_the_snapshot_and_marks_the_errata(self):
        p = self._plan(withdraw_all=True)
        self.assertEqual([s["asset"] for s in p["steps"]],
                         ["quiz.json", "study_guide.md"])
        qc_revert.apply_plan(p, gh=self.gh, root=self.root, today=TODAY)
        self.assertEqual(self.gh.data(TAG, "quiz.json"), fixture("quiz.json"))
        self.assertEqual(self.gh.data(TAG, "quiz.json.pre-qc-20260922"),
                         FIXED_QUIZ)
        stored = json.loads(qc_revert.fixes_path(TAG, root=self.root)
                            .read_text(encoding="utf-8"))
        self.assertEqual({f["id"]: f["status"] for f in stored["fixes"]},
                         {"f0": "withdrawn", "f1": "withdrawn",
                          "f2": "withdrawn"})
        self.assertTrue(all(f.get("withdrawn_at")
                            for f in stored["fixes"] if f["id"] != "f0"))

    def test_all_needs_no_fix_engine(self):
        p = self._plan(withdraw_all=True, applier=None)
        self.assertTrue(p["steps"])

    def test_withdrawing_one_fix_replays_the_rest(self):
        self.fixes["fixes"].append({"id": "f3", "asset": "quiz.json",
                                    "unit": "Q4", "status": "active"})
        qc_store.write_json_atomic(
            qc_revert.fixes_path(TAG, root=self.root), self.fixes)
        p = self._plan(withdraw_ids=["f1"], applier=fake_applier)
        self.assertEqual([s["asset"] for s in p["steps"]], ["quiz.json"])
        rebuilt = p["rebuilt"]["quiz.json"].decode("utf-8")
        self.assertIn("<!-- qc:f3 -->", rebuilt)
        self.assertNotIn("<!-- qc:f1 -->", rebuilt)
        qc_revert.apply_plan(p, gh=self.gh, root=self.root, today=TODAY)
        self.assertEqual(self.gh.data(TAG, "quiz.json"),
                         rebuilt.encode("utf-8"))
        stored = json.loads(qc_revert.fixes_path(TAG, root=self.root)
                            .read_text(encoding="utf-8"))
        self.assertEqual({f["id"]: f["status"] for f in stored["fixes"]}["f3"],
                         "active")

    def test_a_kept_fix_without_an_applier_is_refused(self):
        self.fixes["fixes"].append({"id": "f3", "asset": "quiz.json",
                                    "status": "active"})
        qc_store.write_json_atomic(
            qc_revert.fixes_path(TAG, root=self.root), self.fixes)
        with self.assertRaises(qc_revert.RevertError):
            self._plan(withdraw_ids=["f1"], applier=None)

    def test_dry_run_changes_nothing(self):
        before = dict(self.gh._rel[TAG]["assets"])
        # main() builds its own Gh, so point the plan at the fake one; the
        # dry run must not reach GitHub even to look.
        self._patch_gh()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = qc_revert.main([TAG, "--all", "--dry-run",
                                 "--qc-dir", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.gh._rel[TAG]["assets"], before)
        stored = json.loads(qc_revert.fixes_path(TAG, root=self.root)
                            .read_text(encoding="utf-8"))
        self.assertEqual(stored["fixes"][0]["status"], "active")

    def test_unknown_fix_id_is_refused(self):
        with self.assertRaises(qc_revert.RevertError):
            self._plan(withdraw_ids=["nope"])

    def test_an_already_withdrawn_fix_is_refused(self):
        with self.assertRaises(qc_revert.RevertError):
            self._plan(withdraw_ids=["f0"])

    def test_without_a_snapshot_it_refuses_rather_than_guessing(self):
        shutil.rmtree(qc_store.snapshot_dir(TAG, root=self.root))
        with self.assertRaises(qc_revert.RevertError) as cm:
            self._plan(withdraw_all=True)
        self.assertIn("pristine", str(cm.exception))

    def test_a_release_that_moved_under_the_plan_is_stale(self):
        p = self._plan(withdraw_all=True)
        self.gh.put(TAG, "quiz.json", b'{"someone": "else"}')
        with self.assertRaises(Stale):
            qc_revert.apply_plan(p, gh=self.gh, root=self.root, today=TODAY)


if __name__ == "__main__":
    unittest.main()
