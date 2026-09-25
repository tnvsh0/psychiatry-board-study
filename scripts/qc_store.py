#!/usr/bin/env python3
"""The releases are the master copy: fetch, snapshot, journal, and swap an
asset without ever destroying the bytes that are live.

WHY THE RELEASE AND NOT `_generated/`. bulk_run.sweep_local() deletes every
local artifact once the chapter's release exists (bulk_run.py:127-146) and
keeps only facts.json. For the ~60 published chapters the release asset is the
only copy of the quiz, guide and cards the owner actually studies from, so a QC
pass that read `_generated/` would be judging files that no longer exist — or
an older draft of them. Everything here reads GitHub.

WHY NOTHING HERE OVERWRITES. daily_study.py:2467 and regen_image.py:112 upload
over a live asset with gh's overwrite flag. gh implements that as delete-then-
upload: there is a window with no asset at all, and when the upload half fails
the chapter is left without the file and with nothing to roll back to. Every
chapter in this backlog is material the owner has already studied from, and the
spec's fifth principle is "never lose an asset" — so a swap here is
upload → verify → rename the original aside → promote → verify (spec §5.5). The
original survives the whole sequence under `<name>.pre-qc-<date>`.

WHY RESUME READS THE RELEASE, NOT THE JOURNAL. The stage that gets lost is
exactly the stage whose journal write never happened: an upload that times out
locally may well have landed on GitHub's side. So resume_upload() lists the
release's assets and decides from what is actually there. The journal is
evidence and an audit trail, never the source of truth about the remote.

WHY THE NUMERIC ASSET ID IS PARSED OUT OF apiUrl. `gh release view --json
assets` reports `id` as the GraphQL node id (`RA_kwDOSZjr4c4fX9LZ`), which the
REST endpoint this module PATCHes for renames does not accept. The numeric id
(526373593) appears only inside `apiUrl`. Measured against the real release
study-005-serotonin-system, 2026-09-22.

    python scripts/qc_store.py chapters          schedule tags + release state
    python scripts/qc_store.py snapshot <tag>    originals into _qc/orig/<tag>/
    python scripts/qc_store.py status [<tag>]    run states and open uploads
    python scripts/qc_store.py commit <tag>      spec §5.10's paths into git
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Windows gives a redirected stdout the locale codec (cp1255 here) and a single
# Hebrew character then raises UnicodeEncodeError; every chapter title in this
# course is Hebrew and every one of these CLIs gets piped into a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                   # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = REPO_ROOT / "schedule" / "schedule.json"
# QC_DIR is overridable so the tests can run the whole module against a
# throwaway tree; nothing here ever writes outside it.
QC_DIR = Path(os.environ.get("QC_DIR") or (REPO_ROOT / "_qc"))
GH_REPO = os.environ.get("GH_REPO", "tnvsh0/psychiatry-board-study")


class QcStoreError(Exception):
    """Anything this module refuses to do."""


class GhError(QcStoreError):
    """A gh invocation failed, or answered something unusable."""


class Stale(QcStoreError):
    """The live asset no longer holds the bytes that were judged.

    Spec §5.5(1). Uploading over it would publish a correction computed against
    a version of the chapter that is no longer there.
    """


class SnapshotConflict(QcStoreError):
    """The pristine copy on disk and the live asset disagree.

    Never resolved automatically: whichever side moved, overwriting the
    snapshot destroys the only untouched copy of what was published.
    """


class UploadUnresolved(QcStoreError):
    """A half-finished swap that code must not decide about on its own."""


# ── small helpers ───────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json_atomic(path, obj) -> Path:
    """Write JSON so a reader never sees half of it.

    The journal is read by the next run after a crash, and a crash during the
    write is the case it exists for. A truncated journal.json is worse than a
    missing one: it parses as "this chapter never reached the step" for some
    steps and raises for others. tmp + os.replace is atomic within a directory
    on NTFS, which is where every one of these files lives.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    replace_with_retry(tmp, path)
    return path


def replace_with_retry(tmp, path, *, attempts: int = 6, sleep=time.sleep) -> None:
    """os.replace, retried — this tree lives inside a OneDrive folder.

    The 144-chapter sweep of 2026-09-25 lost chapter 085 to
    `PermissionError: [WinError 5] Access is denied` renaming
    journal.json.tmp18292 over journal.json. Nothing was wrong with the bytes:
    OneDrive's sync engine (an indexer or a scanner does it too) held the
    target open for the few milliseconds the rename needed. The chapter's whole
    run died on it and the sweep exited non-zero over one lost file.

    A truncated file is still never published — the tmp is complete before the
    first attempt, and a failure after the last one re-raises with both paths
    in the message, so the bytes are recoverable by hand.
    """
    delay = 0.05
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            sleep(delay)
            delay *= 2


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def qc_root(root=None) -> Path:
    """The _qc/ tree to work in. Every path in this module and in qc_revert
    hangs off it, so a test can point the whole mechanism at a temp dir."""
    return Path(root) if root is not None else QC_DIR


_root = qc_root          # internal shorthand; same function


# ── chapter tags ────────────────────────────────────────────────────────────
# Copied verbatim from app/app.py:376-382 rather than imported: importing
# app.py executes the Streamlit page at module level (st.set_page_config, the
# whole layout), which needs a running Streamlit and hits the network. The copy
# is not trusted on faith — tests/qc/test_qc_store.py extracts these two
# functions out of app/app.py with `ast` and asserts identical output on all
# 144 days. bulk_run.py:415-419 records what a drifted copy costs: the two
# disagreed on 92 of 144 chapters, so released() looked up tags that do not
# exist and every affected chapter would have been marked FAILED after
# succeeding.
def slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower()[:60]


def day_tag(day: dict) -> str:
    return f"study-{day['index']:03d}-{slugify(day.get('title_en', ''))}"


@dataclass(frozen=True)
class Chapter:
    index: int
    tag: str
    title_en: str
    title_he: str
    # None means "not asked" — chapters() was called without a Gh.
    released: bool | None = None
    draft: bool | None = None


def load_days(schedule_path=None) -> list[dict]:
    path = Path(schedule_path) if schedule_path else SCHEDULE
    return json.loads(path.read_text(encoding="utf-8"))["days"]


def chapters(*, gh=None, schedule_path=None) -> tuple[list[Chapter], list[str]]:
    """(chapters in schedule order, orphan release tags).

    Measured against the live repository 2026-09-22: 68 of the 144 chapters
    are released, no drafts, and exactly one orphan — `study-016-adhd-core`
    (the chapter was renumbered; schedule 016 is now
    `study-016-principles-of-pediatric-psychopharmacology`). Spec §3
    excludes it from the backlog and lists it for the owner, so it is returned
    separately rather than silently folded in or silently dropped — judging it
    would spend agy time on material the app never shows.

    A draft release counts as existing but not as published; bulk_run.released()
    learned that the hard way (bulk_run.py:105-110) after a peer project walked
    past five unpublished episodes every week for two months.
    """
    days = load_days(schedule_path)
    published: set[str] = set()
    drafts: set[str] = set()
    if gh is not None:
        for r in gh.list_releases():
            tag = r.get("tagName")
            if not tag:
                continue
            (drafts if r.get("isDraft") else published).add(tag)

    out: list[Chapter] = []
    for d in days:
        tag = day_tag(d)
        out.append(Chapter(
            index=int(d["index"]), tag=tag,
            title_en=d.get("title_en", ""), title_he=d.get("title_he", ""),
            released=(tag in published) if gh is not None else None,
            draft=(tag in drafts) if gh is not None else None,
        ))
    orphans = sorted((published | drafts) - {c.tag for c in out})
    return out, orphans


# ── which assets QC reads ───────────────────────────────────────────────────
TEXT_SUFFIXES = (".md", ".json", ".txt")

# Judged in v1 per spec §3. report.md is study_guide.md's name in the chapters
# built by the oldest pipeline (001 has report.md and no study_guide.md).
JUDGED_EXACT = frozenset({
    "study_guide.md", "report.md", "quiz.json", "flashcards.json", "facts.json",
})
# The plate legends: the app renders them and they carry "numbers for the exam".
JUDGED_GLOBS = ("sketchy_*.txt",)
# Text on the release that spec §3 explicitly does NOT judge — the app renders
# neither, so a finding in them would never reach the owner. They are still
# snapshotted (they are cheap, and the snapshot is the safety net), just not
# handed to the judge.
NOT_JUDGED_GLOBS = ("*.quiz.md", "narration_*.txt")


def is_text_asset(name: str) -> bool:
    return name.lower().endswith(TEXT_SUFFIXES)


def is_judged(name: str) -> bool:
    if any(fnmatch.fnmatch(name, g) for g in NOT_JUDGED_GLOBS):
        return False
    return name in JUDGED_EXACT or any(
        fnmatch.fnmatch(name, g) for g in JUDGED_GLOBS)


# ── GitHub, behind one class ────────────────────────────────────────────────
@dataclass(frozen=True)
class Asset:
    name: str
    asset_id: int            # the REST numeric id; 0 when gh did not give one
    size: int
    sha256: str | None       # GitHub's own digest, when it records one
    state: str = "uploaded"


_ASSET_ID_RX = re.compile(r"/releases/assets/(\d+)")
# filepath.Match metacharacters. gh matches --pattern with Go's filepath.Match,
# whose backslash escaping is DISABLED on Windows — so a name containing one of
# these cannot be addressed exactly and is refused rather than guessed at. No
# asset this project publishes contains one.
_GLOB_META = set("*?[]\\")


class Gh:
    """Every GitHub call this module makes, in one injectable place.

    The tests replace it wholesale with an in-memory FakeGh. Nothing else in
    qc_store or qc_revert shells out, so a test can never reach the real
    releases — which matters more here than usual, because the writes are
    renames on assets the owner studies from.
    """

    def __init__(self, repo: str = GH_REPO, *, exe: str = "gh",
                 timeout: int = 300):
        self.repo = repo
        self.exe = exe
        self.timeout = timeout

    # -- plumbing ----------------------------------------------------------
    def _run(self, args: list[str], *, timeout: int | None = None,
             retries: int = 1) -> str:
        """Run gh and return stdout, or raise GhError.

        `retries` is only ever > 1 for reads. A write that times out may have
        landed — GitHub 502s after doing the work are routine — so retrying an
        upload or a rename blindly is how you get two assets where you wanted
        one. The recovery for a failed write is resume_upload(), which looks at
        what the release actually holds.
        """
        cmd = [self.exe, *args]
        last = ""
        for attempt in range(max(1, retries)):
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=timeout or self.timeout)
            except subprocess.TimeoutExpired:
                last = f"timed out after {timeout or self.timeout}s"
            except FileNotFoundError as e:
                raise GhError(f"{self.exe} not found on PATH: {e}") from e
            else:
                if p.returncode == 0:
                    return p.stdout
                last = " ".join(((p.stderr or "") + " "
                                 + (p.stdout or "")).split())[:400]
            if attempt + 1 < max(1, retries):
                time.sleep(2 * (attempt + 1))
        raise GhError(f"gh {' '.join(args[:3])} failed: {last}")

    # -- reads -------------------------------------------------------------
    def list_releases(self) -> list[dict]:
        out = self._run(["release", "list", "--repo", self.repo,
                         "--limit", "500", "--json", "tagName,isDraft"],
                        timeout=120, retries=3)
        try:
            return json.loads(out or "[]")
        except json.JSONDecodeError as e:
            raise GhError(f"release list: unparseable JSON: {e}") from e

    def list_assets(self, tag: str) -> list[Asset]:
        out = self._run(["release", "view", tag, "--repo", self.repo,
                         "--json", "assets"], timeout=120, retries=3)
        try:
            raw = json.loads(out or "{}").get("assets", [])
        except json.JSONDecodeError as e:
            raise GhError(f"release view {tag}: unparseable JSON: {e}") from e
        assets = []
        for a in raw:
            m = _ASSET_ID_RX.search(a.get("apiUrl") or "")
            digest = (a.get("digest") or "")
            assets.append(Asset(
                name=a.get("name", ""),
                asset_id=int(m.group(1)) if m else 0,
                size=int(a.get("size") or 0),
                sha256=digest.split("sha256:")[-1] if "sha256:" in digest
                else None,
                state=a.get("state") or "uploaded",
            ))
        return assets

    def asset(self, tag: str, name: str) -> Asset | None:
        for a in self.list_assets(tag):
            if a.name == name:
                return a
        return None

    def download(self, tag: str, name: str, dest) -> Path:
        """One asset, by exact name, to `dest` (a file path)."""
        bad = set(name) & _GLOB_META
        if bad:
            raise GhError(f"{name!r} contains {sorted(bad)}, which gh's "
                          f"--pattern would read as a glob")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Download beside the destination, not into %TEMP%: os.replace is
        # atomic only within a filesystem, and a partial download must never
        # be visible under the real name. A sibling temp dir also means gh is
        # never asked to write onto an existing file, which it refuses to do
        # without the overwrite flag this module does not use.
        stage = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".dl-"))
        try:
            tmp = stage / "asset.bin"
            self._run(["release", "download", tag, "--repo", self.repo,
                       "--pattern", name, "--output", str(tmp)], retries=3)
            if not tmp.exists() or tmp.stat().st_size == 0:
                raise GhError(f"{tag}/{name}: gh wrote nothing")
            os.replace(tmp, dest)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return dest

    # -- writes ------------------------------------------------------------
    def upload(self, tag: str, path, name: str | None = None) -> None:
        """Publish `path` as a NEW asset called `name`.

        gh takes the asset name from the file's basename and has no flag to set
        it (`file#label` sets the label, a different field), so the file is
        staged under the name we want. There is deliberately no overwrite flag:
        if an asset of that name already exists gh fails, and failing is the
        behaviour this whole module is built around.
        """
        path = Path(path)
        name = name or path.name
        stage = Path(tempfile.mkdtemp(prefix="qc-upload-"))
        try:
            staged = stage / name
            shutil.copyfile(path, staged)
            self._run(["release", "upload", tag, str(staged),
                       "--repo", self.repo])
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def rename_asset(self, asset_id: int, new_name: str) -> str:
        """Rename in place — the only operation that moves an asset without
        the bytes ever leaving GitHub, which is why the swap is built on it."""
        if not asset_id:
            raise GhError(f"no numeric asset id for the rename to {new_name!r}"
                          f" — gh's `id` field is a node id, the REST id lives"
                          f" in apiUrl")
        out = self._run(["api", "-X", "PATCH",
                         f"repos/{self.repo}/releases/assets/{int(asset_id)}",
                         "-f", f"name={new_name}"])
        try:
            return json.loads(out or "{}").get("name", new_name)
        except json.JSONDecodeError:
            return new_name

    def delete_asset(self, asset_id: int) -> None:
        """Only ever used on an asset THIS module uploaded minutes earlier
        (a `.qc-<sha8>` temp being rolled back). Nothing published is deleted."""
        if not asset_id:
            raise GhError("no numeric asset id for delete")
        self._run(["api", "-X", "DELETE",
                   f"repos/{self.repo}/releases/assets/{int(asset_id)}"])


# ── fetch ───────────────────────────────────────────────────────────────────
def live_sha256(gh: Gh, tag: str, name: str, asset: Asset | None = None) -> str:
    """The sha256 of what is live now.

    GitHub records a digest on every asset it has been given one for, and
    reading it costs nothing. When it is absent (older uploads) the bytes are
    fetched and hashed rather than assumed — this value is the gate that stops
    a correction being published over a chapter that moved underneath it.
    """
    a = asset or gh.asset(tag, name)
    if a is None:
        raise Stale(f"{tag}: no asset named {name}")
    if a.sha256:
        return a.sha256
    stage = Path(tempfile.mkdtemp(prefix="qc-sha-"))
    try:
        return sha256_file(gh.download(tag, name, stage / name))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def fetch(tag: str, dest, *, gh: Gh | None = None,
          names=None, text_only: bool = True) -> dict[str, dict]:
    """Download the release's assets into `dest`.

    Returns name -> {path, sha256, asset_id, size}. Every download is checked
    against GitHub's recorded digest and size: a truncated transfer that went
    unnoticed here would become "the original" in the snapshot, and the
    original is the only thing qc_revert can rebuild from.
    """
    gh = gh or Gh()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    wanted = None if names is None else list(names)
    out: dict[str, dict] = {}
    seen = set()
    for a in gh.list_assets(tag):
        seen.add(a.name)
        if wanted is not None:
            if a.name not in wanted:
                continue
        elif text_only and not is_text_asset(a.name):
            continue
        path = gh.download(tag, a.name, dest / a.name)
        digest = sha256_file(path)
        size = path.stat().st_size
        if a.sha256 and a.sha256 != digest:
            raise GhError(f"{tag}/{a.name}: downloaded bytes hash {digest[:8]},"
                          f" GitHub records {a.sha256[:8]}")
        if a.size and a.size != size:
            raise GhError(f"{tag}/{a.name}: {size} bytes downloaded, "
                          f"GitHub records {a.size}")
        out[a.name] = {"path": path, "sha256": digest,
                       "asset_id": a.asset_id, "size": size}
    if wanted is not None:
        missing = [n for n in wanted if n not in out]
        if missing:
            raise GhError(f"{tag}: release has no asset(s) {missing} "
                          f"(it has {sorted(seen)})")
    return out


# ── committing by explicit path ─────────────────────────────────────────────
# Spec §2.5: "Originals are snapshotted and COMMITTED before any change";
# §5.5: the snapshot is "committed by explicit path"; §5.10 names what goes
# into git and nothing else:
#
#     _qc/<tag>/verdict.json   _qc/<tag>/fixes.json
#     _qc/orig/                _qc/REPORT.md   _qc/state.json
#
# WHY IT MATTERS HERE. `_qc/orig/<tag>/` is the only pristine copy of the bytes
# this pass is about to swap on the release, and it is what qc_revert.rebuild()
# reads to undo a correction. Left untracked it is one `git clean -xdf` or one
# "delete _qc and re-run" away from taking the originals of 68 published
# chapters with it — the single outcome §2.5 exists to prevent.
#
# WHY A PATHSPEC AND NEVER `git add -A`. This tree is worked with dozens of
# untracked files at any moment, and the .gitignore block is what keeps the
# books' text out of history. `git add -A` would stage whatever a later module
# drops under `_qc/` before anyone writes a rule for it. `git add -- <paths>`
# and then `git commit -- <paths>` touch those paths only: a tree dirty
# elsewhere stays exactly as dirty, and work staged elsewhere stays staged.
#
# WHY A FAILURE HERE NEVER RAISES. The commit is insurance on top of a snapshot
# that is already on disk and already verified by the time this runs. A backlog
# of 68 chapters must not die on an index lock, an unset user.email or a
# detached HEAD, so this prints loudly and returns a record of what happened.
#
# WHY IT REFUSES ANYTHING OUTSIDE THE REPOSITORY. Every test points the whole
# mechanism at a throwaway `_qc/` under %TEMP% (`root=`), and a commit helper
# that ran git there — or worse, resolved back into the working repository —
# would turn an offline suite into one that writes to the owner's history.
GIT_TIMEOUT = 120
# Set it to anything non-empty to run the mechanism without touching git.
NO_COMMIT_ENV = "QC_NO_COMMIT"
# The per-chapter half of §5.10's list. `_qc/orig/` is committed by snapshot().
COMMITTED_PER_CHAPTER = ("verdict.json", "fixes.json")


def _git_run(args: list[str], *, cwd, timeout: int = GIT_TIMEOUT):
    """Run one git command and hand back the CompletedProcess.

    The only place this module shells out to anything but gh. Injectable
    (`runner=`) so the tests can assert on the argv — which is the point of the
    test: that the argv is a pathspec and never `-A`.
    """
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def _git_says(proc) -> str:
    return " ".join((((getattr(proc, "stderr", "") or "")
                      + " " + (getattr(proc, "stdout", "") or "")).split()))[:400]


def commit_paths(paths, message: str, *, repo_root=None, runner=None) -> dict:
    """`git add -- <paths>` then `git commit -- <paths>`. Never raises.

    Returns {committed, paths, reason, ...}. `paths` that do not exist are
    dropped; paths outside the repository are refused and named in the result.
    """
    run = runner or _git_run
    root = Path(repo_root or REPO_ROOT).resolve()
    rels: list[str] = []
    outside: list[str] = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        try:
            rels.append(p.resolve().relative_to(root).as_posix())
        except ValueError:
            outside.append(str(p))
    if outside:
        print(f"[i] not committing {len(outside)} path(s) outside "
              f"{root}: {', '.join(outside)}", flush=True)
    if not rels:
        return {"committed": False, "paths": [], "outside": outside,
                "reason": ("every path is outside the repository" if outside
                           else "nothing to commit")}
    if os.environ.get(NO_COMMIT_ENV):
        return {"committed": False, "paths": rels, "outside": outside,
                "reason": f"{NO_COMMIT_ENV} is set"}

    def failed(stage: str, detail: str) -> dict:
        # Loud, and only that: see the header. The snapshot is already on disk.
        print(f"[!] git {stage} failed, so {', '.join(rels)} is NOT in git "
              f"yet ({detail}). Spec §2.5 wants the originals committed before "
              f"the swap — commit them by hand before trusting qc_revert.",
              flush=True)
        return {"committed": False, "paths": rels, "outside": outside,
                "reason": f"git {stage} failed: {detail}"}

    stage = "add"
    try:
        add = run(["add", "--", *rels], cwd=root)
        if add.returncode != 0:
            return failed(stage, _git_says(add))
        # 0 = the pathspec holds nothing new. `git commit` would exit 1 on it
        # and the caller would read a clean no-op as a failure.
        stage = "diff"
        staged = run(["diff", "--cached", "--quiet", "--", *rels], cwd=root)
        if staged.returncode == 0:
            return {"committed": False, "paths": rels, "outside": outside,
                    "reason": "already committed; nothing changed"}
        stage = "commit"
        out = run(["commit", "-m", message, "--", *rels], cwd=root)
        if out.returncode != 0:
            return failed(stage, _git_says(out))
    except FileNotFoundError:
        return failed(stage, "git is not on PATH")
    except subprocess.TimeoutExpired:
        return failed(stage, f"git did not answer within {GIT_TIMEOUT}s")
    except OSError as e:                                  # noqa: BLE001
        return failed(stage, str(e))
    return {"committed": True, "paths": rels, "outside": outside,
            "reason": "committed", "message": message}


def commit_snapshot(tag: str, *, root=None, runner=None) -> dict:
    """§5.5: the pristine originals, in git, before anything is swapped."""
    return commit_paths(
        [snapshot_dir(tag, root=root)],
        f"qc: snapshot {tag} before any change\n\n"
        f"The released bytes exactly as they were published (spec §2.5, §5.5)."
        f" qc_revert rebuilds from this copy.",
        runner=runner)


def commit_verdict(tag: str, *, root=None, runner=None) -> dict:
    """§5.10's other three: the chapter's verdict and fixes, the report and the
    run state. Called after the verdict is written — from qc_run's verdict step
    or from `python scripts/qc_store.py commit <tag>`."""
    base = _root(root)
    return commit_paths(
        [base / tag / n for n in COMMITTED_PER_CHAPTER]
        + [base / "REPORT.md", base / "state.json"],
        f"qc: verdict for {tag}",
        runner=runner)


# ── snapshot ────────────────────────────────────────────────────────────────
def _verify_snapshot_file(path: Path) -> None:
    """Non-empty, and parseable when it claims to be JSON.

    A zero-byte or unparseable "original" is not an original — it is a broken
    download that would be discovered only on the day someone needed to revert.
    """
    if path.stat().st_size == 0:
        raise QcStoreError(f"{path.name}: snapshot is empty")
    if path.suffix.lower() == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise QcStoreError(f"{path.name}: snapshot does not parse: {e}") from e


def snapshot_dir(tag: str, *, root=None) -> Path:
    return _root(root) / "orig" / tag


def snapshot(tag: str, *, gh: Gh | None = None, root=None,
             names=None, commit=True) -> dict:
    """Every text asset of `tag` into `_qc/orig/<tag>/`, with a manifest.

    Idempotent and one-way: a snapshot that already exists is never rewritten.
    If the live asset has moved since it was taken, or the file on disk has,
    that is a SnapshotConflict — there is exactly one untouched copy of what
    was published and code does not get to choose which version it is. The
    caller decides (almost always: the chapter was re-uploaded by another tool
    and needs a fresh QC pass under a new snapshot directory).

    Assets that appeared on the release after the snapshot are added; nothing
    that exists is touched.

    `commit` is True (commit the result by explicit path, §5.5), False (do not
    run git at all) or a runner callable for the tests. A snapshot taken into a
    `root` outside the repository is never committed whatever this says.
    """
    gh = gh or Gh()
    root = _root(root)
    dest = snapshot_dir(tag, root=root)
    manifest_path = dest / "manifest.json"
    prior = read_json(manifest_path)
    entries: dict[str, dict] = {}
    if prior:
        entries = {e["name"]: dict(e) for e in prior.get("assets", [])}

    live = {a.name: a for a in gh.list_assets(tag)
            if is_text_asset(a.name) and (names is None or a.name in names)}
    if not live:
        raise QcStoreError(f"{tag}: no text assets on the release to snapshot")

    for name, a in sorted(live.items()):
        path = dest / name
        old = entries.get(name)
        if old and path.exists():
            on_disk = sha256_file(path)
            if on_disk != old["sha256"]:
                raise SnapshotConflict(
                    f"{tag}/{name}: the snapshot on disk hashes {on_disk[:8]},"
                    f" the manifest says {old['sha256'][:8]} — the pristine "
                    f"copy was modified locally")
            if live_sha256(gh, tag, name, a) != old["sha256"]:
                raise SnapshotConflict(
                    f"{tag}/{name}: the live asset changed since the snapshot "
                    f"({old['sha256'][:8]} → {a.sha256 or '?'}). Refusing to "
                    f"overwrite the original.")
            continue
        got = fetch(tag, dest, gh=gh, names=[name])[name]
        if old and got["sha256"] != old["sha256"]:
            raise SnapshotConflict(
                f"{tag}/{name}: the snapshot file was missing and the live "
                f"asset now hashes {got['sha256'][:8]}, not the recorded "
                f"{old['sha256'][:8]} — the original is gone, not re-fetchable")
        try:
            _verify_snapshot_file(got["path"])
        except QcStoreError:
            # Leave nothing behind that a later run would mistake for a good
            # original; the manifest is written only from what verified.
            got["path"].unlink(missing_ok=True)
            raise
        entries[name] = {"name": name, "sha256": got["sha256"],
                         "asset_id": got["asset_id"], "size": got["size"],
                         "fetched_at": now_iso()}

    manifest = {
        "tag": tag, "repo": gh.repo,
        "created_at": (prior or {}).get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "assets": [entries[n] for n in sorted(entries)],
    }
    write_json_atomic(manifest_path, manifest)
    # §2.5 / §5.5: the originals go into git before anything is swapped, by
    # explicit path, and only after every file above verified — a snapshot
    # committed before the manifest agrees with it is a record of nothing.
    # A git failure is loud and does not raise: the files are already on disk
    # and the caller's chapter has not started yet.
    if commit is not False:
        manifest["commit"] = commit_snapshot(tag, root=root, runner=commit
                                             if callable(commit) else None)
    return manifest


def snapshot_manifest(tag: str, *, root=None) -> dict | None:
    return read_json(snapshot_dir(tag, root=root) / "manifest.json")


# ── journal ─────────────────────────────────────────────────────────────────
# Spec §5.5. Linear: resume() hands back the first one not done. Re-judging
# after a fix is a clear() of the judged_* steps, not a branch — the run has
# one place it can be at, and the report has to be able to say where.
STEPS = (
    "fetched", "linted", "bundle_built",
    "judged_A", "judged_B", "judged_C",
    "fixes_applied", "rejudged",
    "upload_pending", "uploaded", "verdict",
)
# The last step: marking it IS the definition of "this chapter is finished".
FINAL_STEP = STEPS[-1]
# Steps that happen only when the run takes a branch most chapters do not.
# `fixes_applied` needs a judged chapter, `rejudged` a Tier-2 patch (Tier 2 is
# off by default), `upload_pending`/`uploaded` an asset that actually changed —
# a chapter judged clean marks none of the four and is finished all the same.
# resume() reports one of these only while the run could still reach it; once a
# LATER step is done the branch was not taken and pointing at it would be a lie
# — which is the step name _pause_on_quota puts in the owner's alert.
OPTIONAL_STEPS = frozenset({"fixes_applied", "rejudged",
                            "upload_pending", "uploaded"})


class Journal:
    """The per-chapter progress file, `_qc/<tag>/journal.json`.

    Exists because a backlog run is 24-51 hours of serial agy time (spec §6)
    across quota lockouts of up to 27 hours, on a laptop that sleeps. Anything
    not written down between two agy calls is paid for twice.
    """

    def __init__(self, tag: str, *, root=None):
        self.tag = tag
        self.root = _root(root)
        self.path = self.root / tag / "journal.json"
        self.data = read_json(self.path) or {
            "tag": tag, "created_at": now_iso(), "updated_at": now_iso(),
            "steps": {}, "uploads": {},
        }
        self.data.setdefault("steps", {})
        self.data.setdefault("uploads", {})

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        write_json_atomic(self.path, self.data)

    # -- steps -------------------------------------------------------------
    def _check(self, step: str) -> str:
        if step not in STEPS:
            raise QcStoreError(f"unknown step {step!r}; expected one of "
                               f"{', '.join(STEPS)}")
        return step

    def mark(self, step: str, **detail) -> dict:
        self.data["steps"][self._check(step)] = {
            "at": now_iso(), **({"detail": detail} if detail else {})}
        self.save()
        return self.data["steps"][step]

    def clear(self, *steps: str) -> None:
        """Un-do steps so they run again — the re-judge after a fix, or a step
        whose output was thrown away. Explicit, so a resume can never silently
        skip work that was invalidated."""
        for s in steps:
            self.data["steps"].pop(self._check(s), None)
        self.save()

    def done(self, step: str) -> bool:
        return self._check(step) in self.data["steps"]

    def finished(self) -> bool:
        """The chapter reached its verdict. Steps a clean chapter never takes
        (OPTIONAL_STEPS) are not required for this, and never were: `verdict`
        is written last, after the upload, by the only code that writes it."""
        return self.done(FINAL_STEP)

    def resume(self) -> str | None:
        """The next step to run, or None when the chapter is finished.

        A step that was skipped rather than left undone is not "next": the run
        is already past it. Without that, a chapter judged clean — no Tier-2
        round, no changed asset — finished with `verdict` marked and reported
        `rejudged` for ever, so nothing could ask the journal whether a chapter
        was done and `_pause_on_quota` wrote that same name into state.json and
        into the owner's phone alert.

        A cleared step is still returned even when later steps are done: that
        is what clear() means (the re-judge after a fix), and it is why this
        cannot simply be "the step after the last completed one".
        """
        if self.finished():
            return None
        done = self.data["steps"]
        for i, s in enumerate(STEPS):
            if s in done:
                continue
            if s in OPTIONAL_STEPS and any(t in done for t in STEPS[i + 1:]):
                continue
            return s
        return None

    # -- open uploads ------------------------------------------------------
    def upload_record(self, name: str) -> dict | None:
        return self.data["uploads"].get(name)

    def set_upload(self, name: str, **fields) -> dict:
        rec = dict(self.data["uploads"].get(name) or {})
        rec.update(fields)
        rec["at"] = now_iso()
        self.data["uploads"][name] = rec
        self.save()
        return rec

    def drop_upload(self, name: str) -> None:
        self.data["uploads"].pop(name, None)
        self.save()

    def pending_uploads(self) -> list[str]:
        return sorted(n for n, r in self.data["uploads"].items()
                      if r.get("stage") not in (None, "done"))


# ── global run state ────────────────────────────────────────────────────────
RUN_STATES = ("in_progress", "paused_quota", "upload_pending", "stale")


def state_path(*, root=None) -> Path:
    return _root(root) / "state.json"


def load_state(*, root=None) -> dict:
    return read_json(state_path(root=root), default={"runs": {}}) or {"runs": {}}


def get_run_state(tag: str, *, root=None) -> dict | None:
    return load_state(root=root).get("runs", {}).get(tag)


def set_run_state(tag: str, state: str, *, root=None, **fields) -> dict:
    """Record where one chapter stands, globally.

    `paused_quota` carries resume_after and step because a quota stop is the
    common case, not the exception: spec §4 forbids sleeping in-process for
    more than 30 minutes and agy's reset can be ~27 hours out, so the run exits
    and a later invocation has to know both when it may try again and what it
    was about to do.
    """
    if state not in RUN_STATES:
        raise QcStoreError(f"unknown run state {state!r}; expected one of "
                           f"{', '.join(RUN_STATES)}")
    if state == "paused_quota":
        for required in ("resume_after", "step"):
            if not fields.get(required):
                raise QcStoreError(
                    f"paused_quota needs {required}: without it the run cannot"
                    f" tell when to retry or what to retry")
    data = load_state(root=root)
    data.setdefault("runs", {})[tag] = {"state": state, "at": now_iso(),
                                        **fields}
    data["updated_at"] = now_iso()
    write_json_atomic(state_path(root=root), data)
    return data["runs"][tag]


def clear_run_state(tag: str, *, root=None) -> None:
    data = load_state(root=root)
    if data.get("runs", {}).pop(tag, None) is not None:
        data["updated_at"] = now_iso()
        write_json_atomic(state_path(root=root), data)


# ── safe upload ─────────────────────────────────────────────────────────────
# Stage names, in order. They are journal values, so they are also what a
# resume reconciles against; do not renumber them casually.
UPLOAD_STAGES = ("checked", "temp_uploaded", "temp_verified",
                 "orig_renamed", "promoted", "done")

_PRE_QC_RX = re.compile(r"\.pre-qc-(\d{8})(?:-(\d+))?$")


def _latest_pre_qc(name: str, assets) -> str | None:
    """The most recent `<name>.pre-qc-…` on the release, or None.

    Sorted on the parsed (date, sequence), not on the string: YYYYMMDD happens
    to sort correctly, the same-day sequence does not — `…-10` comes before
    `…-2`. A rollback that picked the wrong sibling would republish an older
    version of the chapter under its real name and call it a restore, which is
    the one failure this module is not allowed to have.
    """
    best, best_key = None, None
    for n in assets:
        if not n.startswith(f"{name}.pre-qc-"):
            continue
        m = _PRE_QC_RX.search(n)
        if not m:
            continue
        key = (m.group(1), int(m.group(2) or 1))
        if best_key is None or key > best_key:
            best, best_key = n, key
    return best


def temp_asset_name(name: str, sha256: str) -> str:
    """`<name>.qc-<sha8>` — the name the corrected bytes land under first.

    The sha8 is in the name so a resume can tell ITS temp asset from one left
    by an earlier, different attempt at the same chapter: same name, different
    content, and promoting the wrong one publishes a correction nobody judged.
    """
    return f"{name}.qc-{sha256[:8]}"


def pre_qc_name(name: str, taken=(), *, today=None) -> str:
    """`<name>.pre-qc-<YYYYMMDD>`, with a suffix if that is taken.

    A second QC pass on the same chapter on the same day would otherwise try to
    rename onto an existing asset name; GitHub answers 422 and the swap stops
    with the original already halfway aside.
    """
    day = (today or datetime.now()).strftime("%Y%m%d")
    base = f"{name}.pre-qc-{day}"
    if base not in set(taken):
        return base
    n = 2
    while f"{base}-{n}" in set(taken):
        n += 1
    return f"{base}-{n}"


def _assets_by_name(gh: Gh, tag: str) -> dict[str, Asset]:
    return {a.name: a for a in gh.list_assets(tag)}


def _verify_remote(gh: Gh, tag: str, name: str, expect_sha: str) -> None:
    """Download the asset back and compare. The only proof that what GitHub
    stored is what we sent — an upload that reported success and truncated the
    body is exactly the failure this whole module is insurance against."""
    stage = Path(tempfile.mkdtemp(prefix="qc-verify-"))
    try:
        got = sha256_file(gh.download(tag, name, stage / "asset.bin"))
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if got != expect_sha:
        raise GhError(f"{tag}/{name}: read back {got[:8]}, expected "
                      f"{expect_sha[:8]}")


def safe_upload(tag: str, name: str, local_path, judged_sha256: str, *,
                gh: Gh | None = None, journal: Journal | None = None,
                root=None, today=None) -> dict:
    """Publish corrected bytes over `name` without the original ever being at
    risk. Spec §5.5. Raises Stale when the live asset moved since it was judged.

    Sequence, journalled at every stage:
      checked        the live asset still hashes judged_sha256
      temp_uploaded  the new bytes are on the release as `<name>.qc-<sha8>`
      temp_verified  downloaded back, hash matches
      orig_renamed   the live asset is now `<name>.pre-qc-<date>`
      promoted       the temp asset is now `<name>`
      done           `<name>` downloaded back, hash matches
    """
    gh = gh or Gh()
    journal = journal or Journal(tag, root=root)
    local_path = Path(local_path)
    new_sha = sha256_file(local_path)

    assets = _assets_by_name(gh, tag)
    live = assets.get(name)
    if live is None:
        # A missing `<name>` with a `.pre-qc-` sibling is not a missing asset,
        # it is a swap that stopped between the two renames. Saying "stale"
        # there sends the owner to re-judge a chapter whose only problem is an
        # unfinished upload, and a second safe_upload would never complete it.
        half = _latest_pre_qc(name, assets)
        if half:
            raise UploadUnresolved(
                f"{tag}/{name}: the asset is mid-swap — the original is under "
                f"{half}. Finish or undo it with resume_upload(), not a "
                f"fresh safe_upload().")
        raise Stale(f"{tag}: no asset named {name} to replace")
    live_sha = live_sha256(gh, tag, name, live)
    if live_sha != judged_sha256:
        set_run_state(tag, "stale", root=root, asset=name,
                      judged_sha256=judged_sha256, live_sha256=live_sha)
        raise Stale(f"{tag}/{name}: live asset hashes {live_sha[:8]}, the "
                    f"verdict was made on {judged_sha256[:8]}. Re-judge before"
                    f" uploading.")
    if new_sha == judged_sha256:
        # Nothing changed. Uploading would still work, but it would rename a
        # perfectly good asset aside for no reason and put a spurious
        # "pre-qc" entry in front of the owner.
        journal.set_upload(name, stage="done", note="identical, nothing to do",
                           judged_sha256=judged_sha256, new_sha256=new_sha)
        return {"tag": tag, "name": name, "stage": "done", "changed": False,
                "new_sha256": new_sha}

    tmp = temp_asset_name(name, new_sha)
    set_run_state(tag, "upload_pending", root=root, asset=name)
    # Start the record FROM SCRATCH. set_upload merges, and a chapter can be
    # swapped twice (a fix, then a revert). Inheriting the finished swap's
    # pre_qc_name made the second one rename the original onto a name that was
    # already taken; GitHub answers 422 and the swap stops with the temp asset
    # published and nothing promoted. Found by the second-pass test, 2026-09-22.
    journal.drop_upload(name)
    journal.set_upload(name, stage="checked", temp_name=tmp,
                       judged_sha256=judged_sha256, new_sha256=new_sha,
                       local_path=str(local_path))
    journal.mark("upload_pending", asset=name)
    return _swap(gh, tag, name, local_path, new_sha, journal,
                 root=root, today=today)


def _swap(gh: Gh, tag: str, name: str, local_path: Path, new_sha: str,
          journal: Journal, *, root=None, today=None) -> dict:
    """Forward half of the swap, from wherever the release currently stands."""
    rec = journal.upload_record(name) or {}
    tmp = rec.get("temp_name") or temp_asset_name(name, new_sha)
    assets = _assets_by_name(gh, tag)

    if tmp not in assets:
        gh.upload(tag, local_path, tmp)
        assets = _assets_by_name(gh, tag)
        if tmp not in assets:
            raise GhError(f"{tag}: uploaded {tmp} but the release does not "
                          f"list it")
    journal.set_upload(name, stage="temp_uploaded", temp_name=tmp,
                       new_sha256=new_sha)

    _verify_remote(gh, tag, tmp, new_sha)
    journal.set_upload(name, stage="temp_verified")

    if name in assets:
        pre = rec.get("pre_qc_name") or pre_qc_name(name, assets, today=today)
        gh.rename_asset(assets[name].asset_id, pre)
        journal.set_upload(name, stage="orig_renamed", pre_qc_name=pre)
        assets = _assets_by_name(gh, tag)
    else:
        # Already renamed aside by an earlier attempt; the record knows which.
        pre = rec.get("pre_qc_name")

    gh.rename_asset(assets[tmp].asset_id, name)
    journal.set_upload(name, stage="promoted")

    _verify_remote(gh, tag, name, new_sha)
    journal.set_upload(name, stage="done", pre_qc_name=pre)
    if not journal.pending_uploads():
        journal.mark("uploaded")
        clear_run_state(tag, root=root)
    return {"tag": tag, "name": name, "stage": "done", "changed": True,
            "new_sha256": new_sha, "pre_qc_name": pre, "temp_name": tmp}


def resume_upload(tag: str, name: str, *, gh: Gh | None = None,
                  journal: Journal | None = None, root=None,
                  local_path=None, rollback: bool = False,
                  today=None) -> dict:
    """Finish, or undo, a swap that stopped part-way.

    Decides from the release's own asset list, not from the journal's stage:
    the stage that is missing is precisely the one whose write never happened.
    The journal supplies the two things the release cannot — which temp name is
    ours and what the corrected bytes hash to.
    """
    gh = gh or Gh()
    journal = journal or Journal(tag, root=root)
    rec = journal.upload_record(name)
    if not rec:
        raise UploadUnresolved(f"{tag}/{name}: no upload record to resume")
    new_sha = rec.get("new_sha256")
    judged_sha = rec.get("judged_sha256")
    tmp = rec.get("temp_name") or (temp_asset_name(name, new_sha)
                                   if new_sha else None)
    local = Path(local_path or rec.get("local_path") or "")

    assets = _assets_by_name(gh, tag)
    live = assets.get(name)
    temp_asset = assets.get(tmp) if tmp else None
    pre_name = rec.get("pre_qc_name")
    pre = assets.get(pre_name) if pre_name else None
    if pre is None:                       # the record may predate the rename
        pre_name = _latest_pre_qc(name, assets)
        pre = assets.get(pre_name) if pre_name else None

    # 1. Already finished: `<name>` holds the corrected bytes.
    if live is not None and new_sha and live_sha256(gh, tag, name, live) == new_sha:
        if temp_asset is not None:
            gh.delete_asset(temp_asset.asset_id)      # our own leftover
        journal.set_upload(name, stage="done", pre_qc_name=pre_name)
        if not journal.pending_uploads():
            journal.mark("uploaded")
            clear_run_state(tag, root=root)
        return {"tag": tag, "name": name, "stage": "done", "changed": True,
                "resumed": "already complete", "new_sha256": new_sha}

    # 2. The original is still in place — nothing published has moved yet, so
    #    a rollback here is free and a roll-forward is the ordinary path.
    if live is not None:
        if rollback:
            if temp_asset is not None:
                gh.delete_asset(temp_asset.asset_id)
            journal.drop_upload(name)
            clear_run_state(tag, root=root)
            return {"tag": tag, "name": name, "stage": "rolled_back",
                    "changed": False}
        if not local.exists():
            raise UploadUnresolved(
                f"{tag}/{name}: cannot roll forward without the corrected file"
                f" ({local or 'no path recorded'})")
        return _swap(gh, tag, name, local, new_sha, journal, root=root,
                     today=today)

    # From here `<name>` does not exist: the first rename went through.
    if pre is None:
        raise UploadUnresolved(
            f"{tag}: neither {name} nor a {name}.pre-qc-* asset is on the "
            f"release. Nothing here deletes assets, so this was not us — "
            f"restore from _qc/orig/{tag}/ by hand.")

    # 3. Rollback after the original was moved aside: put it back.
    if rollback:
        if temp_asset is not None:
            gh.delete_asset(temp_asset.asset_id)
        gh.rename_asset(pre.asset_id, name)
        journal.drop_upload(name)
        clear_run_state(tag, root=root)
        return {"tag": tag, "name": name, "stage": "rolled_back",
                "changed": False, "restored_from": pre_name}

    # 4. Roll forward: promote the temp, re-uploading it first if the promote
    #    is what died (the temp is gone and `<name>` was never created).
    if temp_asset is None:
        if not local.exists():
            # The corrected bytes are unreachable, but the original is safe
            # under pre-qc. Put it back rather than leave the chapter without
            # the asset the app reads.
            gh.rename_asset(pre.asset_id, name)
            journal.drop_upload(name)
            clear_run_state(tag, root=root)
            where = str(local) or "no path recorded"
            raise UploadUnresolved(
                f"{tag}/{name}: the corrected file is gone ({where}); "
                f"restored the original from {pre_name}. Re-run the fix for "
                f"this chapter.")
        gh.upload(tag, local, tmp)
    journal.set_upload(name, stage="temp_uploaded", temp_name=tmp,
                       pre_qc_name=pre_name)
    _verify_remote(gh, tag, tmp, new_sha)
    journal.set_upload(name, stage="temp_verified")
    assets = _assets_by_name(gh, tag)
    gh.rename_asset(assets[tmp].asset_id, name)
    journal.set_upload(name, stage="promoted")
    _verify_remote(gh, tag, name, new_sha)
    journal.set_upload(name, stage="done", pre_qc_name=pre_name)
    if not journal.pending_uploads():
        journal.mark("uploaded")
        clear_run_state(tag, root=root)
    return {"tag": tag, "name": name, "stage": "done", "changed": True,
            "resumed": "promoted", "new_sha256": new_sha,
            "pre_qc_name": pre_name, "judged_sha256": judged_sha}


# ── the machine has to stay awake ───────────────────────────────────────────
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


@contextlib.contextmanager
def keep_awake():
    """Stop Windows suspending the machine for the duration of a QC run.

    One agy call can take 25 minutes with no keyboard or mouse activity, and
    chapter 56's bulk run died because the laptop slept for nine hours
    mid-chapter and the NotebookLM session expired under it
    (bulk_run.py:501-522). Here the cost would be worse: a sleep between the
    two renames of a swap leaves a release with the asset moved aside and not
    yet replaced.

    ES_SYSTEM_REQUIRED only, not ES_DISPLAY_REQUIRED — the run does not need
    the screen on, and keeping it on overnight is its own nuisance. The flag is
    per-THREAD, so this must wrap the thread that does the waiting. A no-op off
    Windows.
    """
    handle = None
    if sys.platform == "win32":
        try:
            import ctypes
            handle = ctypes.windll.kernel32
            handle.SetThreadExecutionState.restype = ctypes.c_uint
            handle.SetThreadExecutionState(
                ctypes.c_uint(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED))
        except Exception as e:                        # noqa: BLE001
            print(f"[i] could not hold the machine awake: {e}", flush=True)
            handle = None
    try:
        yield
    finally:
        if handle is not None:
            with contextlib.suppress(Exception):
                import ctypes
                handle.SetThreadExecutionState(ctypes.c_uint(_ES_CONTINUOUS))


def wait_for_network(*, gh: Gh | None = None, poll_s: int = 600,
                     max_wait_s: int = 12 * 3600) -> bool:
    """Block until GitHub answers again. True if it did within max_wait_s.

    Spec §5.5 says to reuse bulk_run.wait_for_network, and this does the same
    job with a different probe on purpose: bulk_run probes NotebookLM, and
    importing it raises SystemExit unless NOTEBOOKLM_PROFILE is set
    (bulk_run.py:62-67). The QC pass never calls NotebookLM — it talks to
    GitHub and to agy — so it would be refusing to start over a credential it
    does not use, and waiting on a service whose health says nothing about
    whether an upload will land.
    """
    gh = gh or Gh()
    deadline = time.time() + max_wait_s
    while True:
        try:
            gh.list_releases()
            return True
        except GhError as e:
            if time.time() + poll_s > deadline:
                print(f"[!] GitHub still unreachable: {e}", flush=True)
                return False
            print(f"[i] GitHub unreachable; next check in {poll_s // 60}m",
                  flush=True)
            time.sleep(poll_s)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _cmd_chapters(a) -> int:
    gh = None if a.offline else Gh()
    chs, orphans = chapters(gh=gh)
    for c in chs:
        mark = ("released" if c.released else
                "DRAFT" if c.draft else
                "-" if c.released is False else "?")
        print(f"{c.index:3}  {mark:<9} {c.tag}")
    if gh is None:
        print(f"\n{len(chs)} chapters; release state not checked (--offline)")
    else:
        print(f"\n{sum(1 for c in chs if c.released)}/{len(chs)} released")
    if orphans:
        print("orphan release tags (not produced by the schedule):")
        for t in orphans:
            print(f"  {t}")
    return 0


def _cmd_snapshot(a) -> int:
    man = snapshot(a.tag)
    print(f"{a.tag}: {len(man['assets'])} asset(s) in "
          f"{snapshot_dir(a.tag)}")
    for e in man["assets"]:
        print(f"  {e['sha256'][:8]}  {e['size']:>8}  {e['name']}")
    return 0


def _cmd_commit(a) -> int:
    """The §5.10 paths of one chapter into git, by explicit path.

    Here because the orchestrator's verdict step is not the only thing that
    writes them — qc_revert marks errata withdrawn, and a run that was killed
    between the verdict and the commit leaves them on disk only.
    """
    results = [commit_snapshot(a.tag), commit_verdict(a.tag)]
    for r in results:
        print(f"  {r['reason']}: {', '.join(r['paths']) or '(no paths)'}")
    return 0 if any(r["committed"] for r in results) else 1


def _cmd_status(a) -> int:
    state = load_state()
    runs = state.get("runs", {})
    tags = [a.tag] if a.tag else sorted(runs)
    if not tags:
        print("no runs recorded")
    for t in tags:
        r = runs.get(t)
        extra = json.dumps({k: v for k, v in (r or {}).items() if k != "state"},
                           ensure_ascii=False) if r else ""
        print(f"{t}: {r['state'] if r else 'no run state'}  {extra}".rstrip())
        j = Journal(t)
        if j.path.exists():
            print(f"  next step: {j.resume() or 'complete'}")
            for n in j.pending_uploads():
                rec = j.upload_record(n)
                print(f"  upload pending: {n} at stage {rec.get('stage')}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("chapters", help="schedule tags and release state")
    p.add_argument("--offline", action="store_true",
                   help="do not ask GitHub which tags are released")
    p.set_defaults(fn=_cmd_chapters)
    p = sub.add_parser("snapshot", help="copy a release's text assets to _qc/orig")
    p.add_argument("tag")
    p.set_defaults(fn=_cmd_snapshot)
    p = sub.add_parser("status", help="run states and open uploads")
    p.add_argument("tag", nargs="?")
    p.set_defaults(fn=_cmd_status)
    p = sub.add_parser("commit", help="§5.10's paths for one chapter into git")
    p.add_argument("tag")
    p.set_defaults(fn=_cmd_commit)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except QcStoreError as e:
        print(f"[STOP] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
