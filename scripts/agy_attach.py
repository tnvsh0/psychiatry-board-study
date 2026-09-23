#!/usr/bin/env python3
"""agy with file attachments, for a judge that must PROVE it read the source.

A judge is only worth running if it read what it judges against, and agy gives
no error when it did not. Measured 2026-09-18 on three pages of Stahl's
Prescriber's Guide (C&A), printed p.69, "full therapeutic benefits may take
2-8 weeks":

    @C:\\...\\slice.pdf   agy logs "items=1, media=0" — NOTHING attached. The
                          model answered from memory with status SUCCESS and
                          confident JSON: it invented a "verbatim" quote and
                          ruled a WRONG flashcard (8-12 weeks) supported.
    @slice.pdf            not attached either (it said so, that time)
    @C:/.../slice.pdf     attached; quoted p.69 exactly; "2-8 weeks"

The same three runs left evidence on disk, and that is what this module now
checks instead of trusting the reply (QC_REVIEW_SPEC §5.6):

    conversation 0b19f38f (attached)      brain/<cid>/.tempmediaStorage/
                                          media_1789742520937.pdf, sha256
                                          identical to the PDF we passed
    conversation 3143f5bb (from memory)   no .tempmediaStorage at all, and no
                                          tool step in conversations/<cid>.db
    conversation 93c1ced2 (relative ref)  no .tempmediaStorage; the db shows it
                                          went looking — view_file into its own
                                          brain/, then find_by_name over C:\\Users

So after every call: the attached PDF's sha256 must appear under
.tempmediaStorage (ingestion proof), and the tool steps in the conversation db
must contain nothing but viewing the files we staged. Either failure discards
the answer and retries; the reply's own confidence is never evidence.

Other rules, each one a failed run first:
  - one FRESH workspace per call, outside the repo (%TEMP%/qc/<tag>/<id>/),
    holding exactly that call's files; cwd is that directory. agy resolves what
    it likes relative to cwd, and a judge that can see the repo can read the
    answer key out of it.
  - the prompt goes inline; `@prompt.txt` makes agy reach for a denied tool.
  - Pro only: Flash drops the attachment and shells out on a long instruction
    (and here it returned an empty SUCCESS on the plate vision gate).
  - tell it the files are already attached, or it sometimes runs pdftotext.
  - QUOTA IS NEVER READ OUT OF THE ANSWER. The 042 quiz cites "StahlNeuro
    p. 429"; the old `"429" in stdout` test would have called that a rate limit
    and paused the backlog on a page number. Only the JSON envelope's
    status/error, stderr, and a RESOURCE_EXHAUSTED line in this run's cli log
    count — the real thing reads
    "RESOURCE_EXHAUSTED (code 429): Individual quota reached ... Resets in
    13h33m26s." (cli-20260901_231147.log). And a bare "429" is not evidence on
    those channels either: agy echoes its argv, prompt and all, into stderr on
    an argument error, and a traceback says "line 429". "This run's cli log"
    is taken literally too — the file must have grown during the call (the
    plate pipeline writes into the same directory) and the line must be the
    call's own, not an old lockout still sitting in the tail.
  - on quota we raise at once and do not sleep: the orchestrator persists
    `paused_quota{resume_after}` and exits. A 13-hour in-process sleep is a run
    nobody can see, resume or interrupt.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agy_image import QuotaExhausted  # noqa: E402

try:  # Hebrew unit text reaches stderr through the diagnostics.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_MODEL = os.environ.get("AGY_JUDGE_MODEL", "gemini-3.1-pro-high").strip()
# §4: 25m inside agy, 2400s outside it. The outer one is for an agy that wedges
# past its own deadline — measured A/B/C calls run 30s-3min, so anything near
# either limit is a hang, not slow work.
PRINT_TIMEOUT = os.environ.get("AGY_PRINT_TIMEOUT", "25m")
SUBPROCESS_TIMEOUT_S = int(os.environ.get("AGY_ATTACH_TIMEOUT_S", 2400))
VERSION_TIMEOUT_S = 60

DEFAULT_GEMINI_ROOT = Path(
    os.environ.get("AGY_GEMINI_ROOT",
                   Path.home() / ".gemini" / "antigravity-cli"))

# CreateProcess caps a whole command line at 32,767 characters, and the failure
# mode is truncation, not an error: a judge would silently receive half its
# schema. §4 puts the ceiling at 30,000 and measures the real argv, because the
# `@` refs, the model flag and the no-tools preamble are on it too.
MAX_CMDLINE = 30000
MAX_INLINE_PROMPT = 28000

# A 429 with no stated reset time. Most state one and the stated value always
# wins; this is so a lockout phrased differently pauses for a sane interval
# instead of hammering the wall.
BLIND_WAIT_S = 1200
# The window reopens a little after the server's own countdown; waking exactly
# on it earns a second 429 and a second pause.
QUOTA_GRACE_S = 30

# agy opens its cli log when it starts, and the log directory is SHARED: the
# plate pipeline's agy_image writes `cli-*.log` beside the judge's, and §4
# allows the two to run at once. So "the newest file" is not "this call's
# file" — a log is only this call's if it was created or grew between the
# snapshot taken before the launch and the read after it. Clock granularity
# and a log flushed just before we sampled t0 need the slack.
LOG_MTIME_SLACK_S = 5
LOG_TAIL_BYTES = 262144

# Read from the envelope's status/error, stderr and the cli log ONLY. Never
# from `response`: see the module docstring on "StahlNeuro p. 429".
#
# And "429" on its own is not evidence anywhere. stderr carries the same
# hazard the reply does: agy echoes its argv — the judge prompt, unit text and
# page citations included — on an argument error, so `(StahlNeuro p. 429)`
# arrives on the channel we trust; a Python traceback says `line 429, in main`
# for the same money. Either one used to pause the whole backlog for a reset
# window parsed out of nothing. The number now counts only beside a word that
# means quota, on the same line. Every lockout measured on this account
# (2026-09-01, 2026-09-18) says RESOURCE_EXHAUSTED or "quota reached" anyway,
# and those phrases stand on their own below.
_QUOTA = re.compile(
    r"RESOURCE_EXHAUSTED"
    r"|exhausted your capacity"
    r"|quota (?:reached|exhausted|exceeded)"
    r"|exceeded its quota"
    r"|(?:quota|rate[ -]?limit|exhaust\w*|too many requests)[^\n]{0,40}\b429\b"
    r"|\b429\b[^\n]{0,40}(?:quota|rate[ -]?limit|exhaust\w*|too many requests)",
    re.I)
# Both phrasings seen in the wild: the image tool says "Your quota will reset
# after 3h45m10s", the model says "Resets in 13h33m26s." Any subset of h/m/s.
_RESET = re.compile(
    r"(?:resets?\s+in|reset\s+after)\s*"
    r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.I)

# The judge's whole job is to read the staged files. Anything else is the model
# going looking — and every observed instance was a call whose attachment had
# failed: find_by_name over C:\Users, list_dir, run_command Get-ChildItem,
# grep_search through .gemini. A verdict written after a search is a verdict
# about some other file.
ALLOWED_TOOLS = frozenset({"view_file"})
# Keys agy uses for the path argument of view_file, in the payloads observed.
_PATH_KEYS = ("AbsolutePath", "Path", "FilePath", "File")

NO_TOOLS = (
    "The files referenced above are ATTACHED to this message and already "
    "available to you. Read them directly. Do NOT run any command, shell "
    "pipeline or tool to open, convert, extract or list them — no cat, no "
    "pdftotext, no python, no Get-ChildItem. Any such call is denied and you "
    "will produce nothing.\n\n")


class QuotaPaused(QuotaExhausted):
    """The account is rate-limited; the run stops here and resumes later.

    Subclasses agy_image.QuotaExhausted so the plate pipeline's existing
    `except QuotaExhausted` still catches it, but carries what the orchestrator
    journals: an absolute wake-up time (a duration is useless in a file that
    outlives the process) and the raw line it was read from, for the report.
    """

    def __init__(self, resume_after_epoch: float, raw: str,
                 reset_seconds: int | None = None):
        self.resume_after_epoch = float(resume_after_epoch)
        self.raw = " ".join((raw or "").split())[:400]
        if reset_seconds is None:
            reset_seconds = max(0, int(round(self.resume_after_epoch - time.time())))
        when = datetime.fromtimestamp(self.resume_after_epoch).strftime("%Y-%m-%d %H:%M")
        super().__init__(
            f"agy quota exhausted; resume after {when} "
            f"(~{reset_seconds // 3600}h{reset_seconds % 3600 // 60:02d}m): {self.raw}",
            reset_seconds)


@dataclass
class AgyResult:
    data: dict | None = None          # the parsed, validated JSON answer
    text: str = ""                    # the raw `response` of the last attempt
    conversation_id: str | None = None
    duration_s: float = 0.0
    db_bytes: int | None = None       # conversation store size, a 2nd signal
    attempts: int = 0
    diagnostic: str = ""
    history: list = field(default_factory=list)   # one line per attempt
    agy_version: str = ""             # §4: recorded on every call
    tempmedia_ok: bool | None = None  # ingestion proof; None = never reached
    tempmedia: dict = field(default_factory=dict)  # staged name -> copy found?
    tool_audit: list = field(default_factory=list)  # tool names agy called
    workspace: str = ""
    argv_len: int = 0

    @property
    def ok(self) -> bool:
        return self.data is not None


# --------------------------------------------------------------------------- exe


def agy_exe() -> str:
    exe = shutil.which("agy")
    if exe:
        return exe
    for cand in (Path.home() / "AppData/Local/agy/bin/agy.exe",
                 Path.home() / ".local/bin/agy"):
        try:
            if cand.exists():
                return str(cand)
        except OSError:
            continue
    raise FileNotFoundError("agy not found on PATH")


def _launcher(exe=None) -> list[str]:
    """argv prefix for agy: the executable, or an injected launcher in tests."""
    if exe is None:
        return [agy_exe()]
    if isinstance(exe, (list, tuple)):
        return [str(x) for x in exe]
    return [str(exe)]


def agy_env() -> dict:
    """Environment for every agy subprocess.

    §4 pins auto-update off. An update landing mid-backlog swaps the binary
    under a 68-chapter run, and the version stamped on chapter 12's verdict
    would then no longer be the one that judged chapter 40 — with no record of
    where the seam is. The version is recorded per call for the same reason.
    """
    env = os.environ.copy()
    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "1"
    return env


_VERSION_CACHE: dict[str, str] = {}


def agy_version(exe=None) -> str:
    """`agy --version`, once per process per binary.

    Once: it costs a process spawn, and the backlog makes thousands of calls.
    Per binary because tests inject one. "unknown" rather than an exception —
    a version we cannot read is worth recording as unread, not worth losing a
    judged chapter over.
    """
    launcher = _launcher(exe)
    key = "\x00".join(launcher)
    if key in _VERSION_CACHE:
        return _VERSION_CACHE[key]
    try:
        p = subprocess.run(launcher + ["--version"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=VERSION_TIMEOUT_S, env=agy_env())
        out = " ".join(((p.stdout or "") + " " + (p.stderr or "")).split())
    except (OSError, subprocess.SubprocessError):
        out = ""
    _VERSION_CACHE[key] = out.strip()[:80] or "unknown"
    return _VERSION_CACHE[key]


# --------------------------------------------------------------------- workspace


def make_workspace(tag: str, call_id: str | None = None, *,
                   base: Path | str | None = None) -> Path:
    """A fresh directory for one call, outside the repo.

    Outside the repo because cwd is handed to agy, and the repo holds the
    answers: the released assets, `_qc/orig/`, the verdicts. A judge that can
    reach them can agree with them without opening a book.

    Fresh because the workspace IS the argument list — `@` refs point into it,
    and the tool audit accepts only views inside it. A directory reused between
    calls carries the previous chapter's bundle, and a stale bundle.pdf that
    happens to still be there is exactly the state where the attachment failure
    of 2026-09-18 goes unnoticed. mkdtemp appends its own suffix to <call-id>
    so a retry of the same id cannot land in the same place.
    """
    root = Path(base) if base else Path(tempfile.gettempdir())
    parent = root / "qc" / _safe_name(tag)
    parent.mkdir(parents=True, exist_ok=True)
    prefix = (_safe_name(call_id) + "-") if call_id else "call-"
    return Path(tempfile.mkdtemp(prefix=prefix, dir=parent))


def _safe_name(name: str) -> str:
    """A tag is a chapter slug; keep it a directory name and nothing else.

    Leading dots go too: a tag reaching this with `..` in it would otherwise
    become a directory called `..-..-etc`, which is legal, confusing, and one
    typo away from the separator surviving.
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(name or "call")).strip("-.")[:64] or "call"


def stage(files: Sequence, workspace: Path | str) -> list[Path]:
    """Copy exactly this call's files into the workspace; return the copies."""
    ws = Path(workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    by_name: dict[str, Path] = {}
    for f in files:
        src = Path(f).resolve()
        if src.name in by_name and by_name[src.name] != src:
            # Both would land on one path and the judge would silently read one
            # file twice — the bundle or the artifacts, never both.
            raise ValueError(f"two different files named {src.name!r}: "
                             f"{by_name[src.name]} and {src}")
        by_name[src.name] = src
        dst = ws / src.name
        if dst != src:
            shutil.copy2(src, dst)
        staged.append(dst)
    return staged


def refs(files) -> str:
    """`@` references agy will actually attach: absolute, forward slashes.

    Measured 2026-09-18: a relative `@slice.pdf` attached nothing. A backslash
    absolute path attached nothing on agy 1.2.5 and attached fine on 1.2.6 — so
    the slashes are not the fix, but they are free and they have never failed.
    """
    return " ".join("@" + Path(f).resolve().as_posix() for f in files)


# ------------------------------------------------------------------ proof on disk


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tempmedia_proof(conversation_id: str | None, files: Sequence,
                    *, gemini_root: Path | str | None = None) -> dict:
    """Did agy actually ingest what we attached? Answered by sha256, not by agy.

    agy copies an attachment into brain/<cid>/.tempmediaStorage/ as
    media_<epoch_ms>.<ext>; the copy of atomoxetine.pdf there was byte-identical
    (422,434 bytes, sha256 59158f63…) to the file we passed. When the ref did
    not take, the directory does not exist at all. That asymmetry is the whole
    ingestion proof.

    Returns {name: True|False|None}; None = a non-PDF whose copy we did not
    find. §5.6 requires the match for PDFs (the bundle) and only records it for
    the rest: artifacts.md is small enough that agy may inline it instead.
    """
    root = Path(gemini_root) if gemini_root else DEFAULT_GEMINI_ROOT
    media = root / "brain" / str(conversation_id or "") / ".tempmediaStorage"
    seen: dict[int, list[Path]] = {}
    if conversation_id:
        try:
            for p in media.rglob("*"):
                if p.is_file():
                    seen.setdefault(p.stat().st_size, []).append(p)
        except OSError:
            pass
    out: dict[str, bool | None] = {}
    for f in files:
        src = Path(f)
        required = src.suffix.lower() == ".pdf"
        try:
            want, size = sha256_file(src), src.stat().st_size
        except OSError:
            out[src.name] = False if required else None
            continue
        # Hash only the candidates of the right size: a bundle is megabytes and
        # the store is 2.9 GB.
        found = any(sha256_file(c) == want for c in seen.get(size, []))
        out[src.name] = bool(found) if (found or required) else None
    return out


def tempmedia_ok(proof: dict) -> bool:
    """Fail closed: every PDF must be accounted for (False is a hard miss)."""
    return all(v is not False for v in proof.values())


# Protobuf walk, ported from the spec review's probe over 11 real conversation
# databases (scratchpad dbprobe/). agy stores each step as an opaque blob in
# `steps(idx, step_type, status, step_payload)`; a tool call is a nested
# message whose fields 1/2/3 are the call id, the tool name and a JSON argument
# object. No .proto is published, so this reads the shape rather than a schema —
# which is why an unreadable payload is skipped, never treated as "no tools".
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    x = s = 0
    while True:
        c = buf[i]
        i += 1
        x |= (c & 0x7F) << s
        s += 7
        if not c & 0x80:
            return x, i


def _fields(buf: bytes):
    out, i = [], 0
    try:
        while i < len(buf):
            key, i = _varint(buf, i)
            fn, wt = key >> 3, key & 7
            if fn == 0:
                return None
            if wt == 0:
                v, i = _varint(buf, i)
            elif wt == 1:
                v, i = buf[i:i + 8], i + 8
            elif wt == 5:
                v, i = buf[i:i + 4], i + 4
            elif wt == 2:
                n, i = _varint(buf, i)
                v, i = buf[i:i + n], i + n
                if i > len(buf):
                    return None
            else:
                return None
            out.append((fn, wt, v))
    except IndexError:
        return None
    return out


def _walk_tool_calls(buf: bytes, out: list, depth: int = 0) -> None:
    fields = _fields(buf)
    if not fields or depth > 12:
        return
    strs: dict[int, str] = {}
    for fn, wt, v in fields:
        if wt == 2 and fn in (1, 2, 3) and fn not in strs:
            try:
                strs[fn] = v.decode("utf-8")
            except UnicodeDecodeError:
                pass
    if 1 in strs and 2 in strs and 3 in strs and _TOOL_NAME.match(strs[2]):
        try:
            args = json.loads(strs[3])
        except ValueError:
            args = None
        if isinstance(args, dict):
            out.append({"call_id": strs[1], "name": strs[2], "args": args})
            return
    for fn, wt, v in fields:
        if wt == 2 and len(v) > 4:
            _walk_tool_calls(v, out, depth + 1)


def tool_calls(conversation_id: str | None, *,
               gemini_root: Path | str | None = None) -> list[dict] | None:
    """Every tool call in one conversation. None = the db could not be read.

    The db is COPIED before opening. agy may still hold it (the CLI writes the
    final steps as the process exits), a live WAL read can block, and sqlite
    recovers a WAL by WRITING to the file it opens — this must never alter the
    owner's store. -wal and -shm travel with it or the last steps are invisible.
    """
    root = Path(gemini_root) if gemini_root else DEFAULT_GEMINI_ROOT
    db = root / "conversations" / f"{conversation_id}.db"
    if not conversation_id or not db.exists():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="qc-conv-"))
    try:
        local = tmp / db.name
        for suffix in ("", "-wal", "-shm"):
            src = db.with_name(db.name + suffix)
            if src.exists():
                shutil.copy2(src, local.with_name(local.name + suffix))
        con = sqlite3.connect(f"file:{local.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "select idx, step_payload from steps order by idx").fetchall()
        finally:
            con.close()
    except (OSError, sqlite3.Error):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    calls: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for _idx, payload in rows:
        found: list[dict] = []
        _walk_tool_calls(payload or b"", found)
        for c in found:
            # One tool call appears twice: the request step and its result.
            key = (c["call_id"], c["name"])
            if key in seen:
                continue
            seen.add(key)
            calls.append(c)
    return calls


def audit_tools(conversation_id: str | None, *, workspace: Path | str | None = None,
                gemini_root: Path | str | None = None) -> tuple[list[str], list[str]]:
    """(tool names called, problems). A non-empty problems list fails the call.

    Fail closed on an unreadable db: "we could not check" is not "it was clean".
    A view_file outside the workspace counts as a problem because that is what
    the failed-attachment runs did — 93c1ced2 read its own brain/ directory and
    then went hunting through C:\\Users for the PDF it never received.
    """
    calls = tool_calls(conversation_id, gemini_root=gemini_root)
    if calls is None:
        return [], ["no readable conversation db for the tool audit"]
    names = [c["name"] for c in calls]
    problems: list[str] = []
    ws = Path(workspace).resolve() if workspace else None
    for c in calls:
        if c["name"] not in ALLOWED_TOOLS:
            problems.append(f"forbidden tool {c['name']}")
            continue
        if ws is None:
            continue
        path = next((c["args"][k] for k in _PATH_KEYS
                     if isinstance(c["args"].get(k), str)), None)
        if path and not _inside(path, ws):
            problems.append(f"{c['name']} outside the workspace: {path[:120]}")
    return names, problems


def _inside(path: str, root: Path) -> bool:
    """Both sides through realpath + normcase before comparing.

    On Windows %TEMP% is often handed out in its 8.3 form (RUNNER~1) while the
    refs we passed agy were resolved to the long one. Comparing the two spellings
    as strings makes every view_file look like it happened outside the workspace,
    and the judge would retry a clean call three times into `unjudged`.
    """
    try:
        cand = Path(os.path.normcase(os.path.realpath(path)))
        base = Path(os.path.normcase(os.path.realpath(str(root))))
        cand.relative_to(base)
        return True
    except (ValueError, OSError):
        return False


def conversation_bytes(conversation_id: str | None, *,
                       gemini_root: Path | str | None = None) -> int | None:
    """Size of agy's store for one conversation, database plus its WAL."""
    if not conversation_id:
        return None
    root = Path(gemini_root) if gemini_root else DEFAULT_GEMINI_ROOT
    total, seen = 0, False
    for suffix in (".db", ".db-wal"):
        try:
            total += (root / "conversations" / f"{conversation_id}{suffix}").stat().st_size
            seen = True
        except OSError:
            pass
    return total if seen else None


# A conversation id is somebody else's value: it is read verbatim out of the
# agy subprocess's JSON envelope, carried through the judge's verdict and a
# JSON file on disk, and handed back here — where it used to be formatted into
# a path and given to rmtree. `..` there is the store root: brain/ for every
# conversation, the cli logs, everything agy keeps. So the id is checked as ONE
# plain path component before it is joined to anything.
#
# The rule is the shape, not a uuid pattern. The measured ids are uuid-shaped
# ("0b19f38f-…"), but agy picks the format and the day it changes it a uuid
# rule would refuse every id and leave a 2.9 GB store to fill a disk that is
# already 91% full — a check that fails closed on the wrong axis.
_CONVERSATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

# make_workspace builds exactly one shape: <base>/qc/<tag>/<call-id>-<rand>.
WORKSPACE_MARK = "qc"


def check_conversation_id(conversation_id) -> str:
    """The id as a safe single path component, or `ValueError`.

    Refuses a separator, a drive, `.`/`..`, an empty string and anything
    outside the character set agy's ids use. Public because a caller that
    would rather fail than skip the deletion can validate first and get the
    exception; `cleanup` itself refuses loudly and carries on (see there).
    """
    cid = str(conversation_id)
    drive, rest = os.path.splitdrive(cid)
    if (drive or rest != cid or cid in (".", "..")
            or os.path.basename(cid) != cid
            or not _CONVERSATION_ID.match(cid)):
        raise ValueError(
            f"not a conversation id, refusing to delete by it: {cid[:80]!r}")
    return cid


def check_workspace(workspace) -> Path:
    """The one directory `cleanup` may delete, resolved, or `ValueError`.

    The same hazard on the second argument: qc_run passes `j.workspace`, a
    string that has been through the judge's verdict and a JSON file since
    make_workspace created it, and `<ws>/..` is the workspace's PARENT —
    `%TEMP%/qc/<tag>` here, and on another machine whatever directory holds it.
    A `..` segment is refused before the filesystem is touched, and what is
    left must still look like a workspace: absolute, and at least two levels
    under a directory named `qc`, which is the only place this module ever
    creates one.
    """
    raw = str(workspace)
    parts = [p for p in re.split(r"[\\/]+", raw) if p]
    if ".." in parts or "." in parts:
        raise ValueError(
            f"a workspace path with a '..' segment points outside the "
            f"workspace, refusing to delete it: {raw[:120]!r}")
    ws = Path(raw)
    if not ws.is_absolute():
        raise ValueError(f"workspace is not an absolute path: {raw[:120]!r}")
    resolved = Path(os.path.realpath(str(ws)))
    # parents[0] is the tag directory, parents[1] the `qc` one; requiring the
    # mark at index 1 or deeper is what makes "<ws>/.." (which resolves to the
    # tag directory, or to some directory of the caller's own) fail.
    if not any(p.name == WORKSPACE_MARK for p in list(resolved.parents)[1:]):
        raise ValueError(
            f"not a make_workspace directory (expected "
            f"<base>/{WORKSPACE_MARK}/<tag>/<call-id>), refusing to delete "
            f"it: {resolved}")
    return resolved


def _refuse(exc: Exception) -> None:
    """Say out loud that a deletion was skipped, and why."""
    print(f"agy_attach.cleanup refused: {exc}", file=sys.stderr)


def cleanup(conversation_id: str | None, *, gemini_root: Path | str | None = None,
            workspace: Path | str | None = None) -> int:
    """Delete one conversation's store. Returns the bytes freed.

    Called by the orchestrator AFTER the verdict is written, never inside ask():
    the brain directory and the db are the evidence the verdict rests on, and a
    failed call has to stay on disk long enough to be looked at. It is deleted
    at all because the disk is 91% full and agy's store is 2.9 GB — the backlog
    is ~68 chapters times three calls times a bundle each.

    Neither argument is trusted (see `check_conversation_id` /
    `check_workspace`). A value that is not a conversation id, or not a
    workspace, deletes NOTHING and is reported on stderr rather than raised:
    the caller (qc_run `_judge_one`) is holding a verdict that is already
    written, its `except Exception` around this call only appends a note, and
    the cost of a refusal is disk, while the cost of the deletion this refuses
    is the owner's agy store. A caller that wants the exception calls the
    checks itself. A falsy id or workspace means "nothing to clean" — that is
    the ordinary case of a call that never got a conversation.
    """
    root = Path(gemini_root) if gemini_root else DEFAULT_GEMINI_ROOT
    freed = 0
    if conversation_id:
        try:
            cid = check_conversation_id(conversation_id)
        except ValueError as exc:
            _refuse(exc)
            cid = ""
        if cid:
            brain = root / "brain" / cid
            if _inside(str(brain), root / "brain"):
                try:
                    for p in brain.rglob("*"):
                        if p.is_file():
                            freed += p.stat().st_size
                except OSError:
                    pass
                shutil.rmtree(brain, ignore_errors=True)
            else:                       # a symlinked brain/<cid>, say
                _refuse(ValueError(f"{brain} resolves outside {root / 'brain'}"))
            for suffix in (".db", ".db-wal", ".db-shm"):
                p = root / "conversations" / f"{cid}{suffix}"
                try:
                    freed += p.stat().st_size
                    p.unlink()
                except OSError:
                    pass
    if workspace:
        try:
            ws = check_workspace(workspace)
        except ValueError as exc:
            _refuse(exc)
            ws = None
        if ws is not None:
            try:
                for p in ws.rglob("*"):
                    if p.is_file():
                        freed += p.stat().st_size
            except OSError:
                pass
            shutil.rmtree(ws, ignore_errors=True)
    return freed


# ------------------------------------------------------------------------ quota


def parse_reset_seconds(text: str) -> int | None:
    """Seconds from "Resets in 13h33m26s" or "reset after 3h45m10s"."""
    for m in _RESET.finditer(text or ""):
        if any(m.groups()):
            h, mi, s = (int(g) if g else 0 for g in m.groups())
            return h * 3600 + mi * 60 + s
    return None


def _log_key(path: Path) -> str:
    """One spelling per file, so a snapshot and a later read agree."""
    return os.path.normcase(os.path.realpath(str(path)))


def log_snapshot(*, gemini_root: Path | str | None = None) -> dict[str, int]:
    """`{log file: size}` for every `cli-*.log`, sampled BEFORE launching agy.

    This is what binds a quota line to a call. Handed back to
    `quota_log_line`, it says which files existed and how long they were, so
    the search can be restricted to the bytes this call appended — rather than
    to "the newest file", which is whichever agy process wrote last.
    """
    root = Path(gemini_root) if gemini_root else DEFAULT_GEMINI_ROOT
    out: dict[str, int] = {}
    try:
        logs = list((root / "log").glob("cli-*.log"))
    except OSError:
        return out
    for p in logs:
        try:
            out[_log_key(p)] = p.stat().st_size
        except OSError:
            continue
    return out


# A log line's own clock, when it has one: "2026-09-18T09:14:02Z ERROR ...".
# Anchored at the start of the line — a timestamp inside the message text is
# part of the message, not the time it was written. agy also writes glog lines
# ("E0901 23:12:07.484793"), which carry no year and are left unstamped.
_LOG_STAMP = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?"
    r"\s*(Z|z|[+-]\d{2}:?\d{2})?")


def _line_epoch(line: str) -> float | None:
    """Epoch seconds for a log line's own timestamp, or None."""
    m = _LOG_STAMP.match(line.lstrip())
    if not m:
        return None
    y, mo, d, h, mi, s, zone = m.groups()
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
        if zone in ("Z", "z"):
            dt = dt.replace(tzinfo=timezone.utc)
        elif zone:
            body = zone[1:].replace(":", "")
            off = timedelta(hours=int(body[:2]), minutes=int(body[2:4]))
            dt = dt.replace(tzinfo=timezone(off if zone[0] == "+" else -off))
        return dt.timestamp()           # no zone: the log is this machine's
    except (ValueError, OverflowError, OSError):
        return None


def quota_log_line(since: float, *, gemini_root: Path | str | None = None,
                   baseline: dict | None = None) -> str | None:
    """The RESOURCE_EXHAUSTED line THIS call produced, if there is one.

    agy retries a 429 internally and only the log keeps the server's own words,
    including the reset time; the JSON envelope can come back with a generic
    executor error. But this reading stops the whole backlog for the window
    printed on the line, so "that run's log" (§4) is taken literally, on two
    axes that the file's mtime alone does not give:

    - **Whose file.** The directory is shared with agy_image, which has its own
      quota, and with any agy the owner runs by hand. Only a log that was
      created or that GREW since `baseline` (and was touched during the call)
      is a candidate, and if two of them were, the call is not attributable:
      nothing is returned, because pausing a 68-chapter backlog on another
      process's rate limit is the worse error. Without a baseline the rule
      degrades to "exactly one log was touched during the call".
    - **Which line.** Inside the file the line must be this call's too: either
      its own timestamp falls in the call, or nothing newer was written after
      it (the log ends on the error, which is what a process dying on a quota
      stop leaves). A months-old lockout sitting in the tail of a file that is
      still being appended to is not this call's — and `parse_reset_seconds`
      would read its old window straight into `resume_after`.
    """
    root = Path(gemini_root) if gemini_root else DEFAULT_GEMINI_ROOT
    try:
        logs = list((root / "log").glob("cli-*.log"))
    except OSError:
        return None
    candidates: list[tuple[Path, int, int]] = []       # path, start, size
    for p in logs:
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_mtime < since - LOG_MTIME_SLACK_S:
            continue                      # untouched during the call
        start = 0
        if baseline is not None:
            was = baseline.get(_log_key(p))
            if was is not None:
                if st.st_size <= was:
                    continue              # not written to (or rotated away)
                start = was               # read only what this call appended
        candidates.append((p, start, st.st_size))
    if len(candidates) != 1:
        # 0: no log of ours. >1: two agy processes wrote at once and the line
        # cannot be attributed to the judge.
        return None
    path, start, size = candidates[0]
    start = max(start, size - LOG_TAIL_BYTES)
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, start))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None

    lines = tail.splitlines()
    stamps = [_line_epoch(line) for line in lines]
    for i in range(len(lines) - 1, -1, -1):
        if "RESOURCE_EXHAUSTED" not in lines[i]:
            continue
        own = stamps[i]
        if own is not None and own >= since - LOG_MTIME_SLACK_S:
            return lines[i].strip()       # written during this call
        floor = own if own is not None else float("-inf")
        if any(t > floor for t in stamps[i + 1:] if t is not None):
            continue                      # the log went on after it
        return lines[i].strip()
    return None


def quota_reason(envelope: dict | None, stderr: str, log_line: str | None) -> str | None:
    """The text that proves a quota stop, or None. Sources are §4's, exactly.

    `response` is deliberately absent. A quiz that cites "StahlNeuro p. 429" is
    not a rate limit, and a judge asked about lithium levels will quote numbers
    all day.
    """
    parts: list[str] = []
    if isinstance(envelope, dict):
        for key in ("status", "error", "error_message", "message"):
            v = envelope.get(key)
            if v:
                parts.append(v if isinstance(v, str)
                             else json.dumps(v, ensure_ascii=False))
    if stderr:
        parts.append(stderr)
    if log_line:
        parts.append(log_line)
    for part in parts:
        if _QUOTA.search(part):
            return part
    return None


# ------------------------------------------------------------------- the call


def _kill_argv(pid: int) -> list[str] | None:
    """Windows: kill the whole tree. agy is a launcher over a child process;
    killing the parent alone leaves the child holding the quota slot and the
    conversation db, and the next call then fails for a reason that looks
    nothing like a timeout."""
    if os.name == "nt":
        return ["taskkill", "/T", "/F", "/PID", str(pid)]
    return None


def _kill_tree(proc) -> None:
    argv = _kill_argv(proc.pid)
    if argv:
        try:
            subprocess.run(argv, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), 9)   # start_new_session put it in its own
        except (OSError, AttributeError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def extract_json(text: str) -> dict | None:
    """The JSON object in a reply, with or without a ```json fence."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def ask_with_files(prompt: str, files, *, model: str | None = None,
                   attempts: int = 3,
                   validate: Callable[[dict], str | None] | None = None,
                   workspace: Path | str | None = None,
                   tag: str = "qc", call_id: str | None = None,
                   exe=None, gemini_root: Path | str | None = None,
                   timeout_s: int | None = None,
                   print_timeout: str | None = None) -> AgyResult:
    """One judgment over attached files. `result.data` is None unless an attempt
    returned JSON that passed `validate` AND left proof on disk that agy read
    the files (§5.6).

    `validate(data)` returns None when the answer is usable, or a reason. It is
    where the caller's canary and coverage checks run — the content half of the
    proof, against text we extracted ourselves. The ingestion proof and the tool
    audit are the mechanical half, and they run FIRST: a reply written without
    the source is rejected before anyone reads how confident it sounds.

    Raises QuotaPaused the moment a quota stop is detected. It does not sleep:
    the lockout measured on this account was 13h33m, and the orchestrator's job
    is to journal `paused_quota{resume_after}`, send one ntfy and exit.
    """
    # QC_NO_AGY is set by tests/qc/__init__.py. Every agy call in this
    # mechanism is injected so tests can fake it, and one still got through:
    # the §4 probe binds lazily, so a test that changed the agy version mid-run
    # spent the owner's quota and showed up only as the suite taking 273
    # seconds instead of 35. A missed double is now an error, not an invoice.
    # `exe` given means the caller injected its own binary — that is what the
    # agy_attach tests do, and a fake exe costs nothing. The guard is about the
    # REAL one, resolved from PATH.
    if (not exe
            and os.environ.get("QC_NO_AGY", "").strip()
            not in ("", "0", "false", "no")):
        raise RuntimeError(
            "QC_NO_AGY is set: a real agy call was attempted from a context "
            "that must not make one (probably a test with an unfaked "
            f"dependency). prompt={prompt[:80]!r} files={[str(f) for f in files]}")
    files = [Path(f) for f in files]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        return AgyResult(diagnostic=f"attachment missing: {missing}")
    if len(prompt) > MAX_INLINE_PROMPT:
        raise ValueError(f"prompt is {len(prompt)} chars; attach long material "
                         f"as a file instead (limit {MAX_INLINE_PROMPT})")

    # Resolved once, here: refs() hands agy the resolved spelling, and the tool
    # audit compares what agy reports against this same one.
    ws = (Path(workspace) if workspace else make_workspace(tag, call_id)).resolve()
    staged = stage(files, ws)
    launcher = _launcher(exe)
    body = f"{refs(staged)}\n{NO_TOOLS}{prompt}"
    cmd = launcher + ["-p", body, "--model", model or DEFAULT_MODEL,
                      "--print-timeout", print_timeout or PRINT_TIMEOUT,
                      "--output-format", "json"]
    argv_len = len(subprocess.list2cmdline(cmd))
    if argv_len >= MAX_CMDLINE:
        # Not an assert: -O would drop it, and the OS failure is truncation,
        # which the judge would answer as if it had the whole schema.
        raise ValueError(
            f"agy command line is {argv_len} characters, over the "
            f"{MAX_CMDLINE} limit; the material belongs in the bundle, not in "
            f"the prompt ({len(prompt)} chars of prompt, {len(staged)} files)")

    env = agy_env()
    limit = SUBPROCESS_TIMEOUT_S if timeout_s is None else int(timeout_s)
    res = AgyResult(agy_version=agy_version(exe), workspace=str(ws),
                    argv_len=argv_len)

    while res.attempts < attempts:
        res.attempts += 1
        # Sampled per attempt, immediately before the launch: this is what
        # tells the quota reader which cli-*.log bytes belong to this call and
        # not to whatever else is talking to agy right now.
        log_before = log_snapshot(gemini_root=gemini_root)
        t0 = time.time()
        popen_kw = {} if os.name == "nt" else {"start_new_session": True}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace",
                                cwd=str(ws), env=env, **popen_kw)
        try:
            out, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=60)
            except (subprocess.SubprocessError, OSError):
                out, err = "", ""
            res.duration_s = round(time.time() - t0, 1)
            res.diagnostic = f"no reply within {limit}s; killed the process tree"
            res.history.append(f"attempt {res.attempts}: {res.diagnostic}")
            continue
        res.duration_s = round(time.time() - t0, 1)
        out, err = out or "", err or ""

        try:
            envelope = json.loads(out or "{}")
            envelope = envelope if isinstance(envelope, dict) else None
        except json.JSONDecodeError:
            envelope = None

        log_line = quota_log_line(t0, gemini_root=gemini_root,
                                  baseline=log_before)
        reason = quota_reason(envelope, err, log_line)
        if reason:
            secs = parse_reset_seconds(reason) or BLIND_WAIT_S
            res.history.append(f"attempt {res.attempts}: quota stop — {reason[:160]}")
            raise QuotaPaused(time.time() + secs + QUOTA_GRACE_S, reason, secs)

        if envelope is None:
            res.diagnostic = (f"no JSON envelope (rc={proc.returncode}): "
                              f"{' '.join((out + ' ' + err).split())[:200]}")
            res.history.append(f"attempt {res.attempts}: {res.diagnostic}")
            continue

        res.conversation_id = envelope.get("conversation_id")
        res.text = envelope.get("response") or ""
        res.db_bytes = conversation_bytes(res.conversation_id,
                                          gemini_root=gemini_root)
        status = envelope.get("status")
        if status != "SUCCESS" or not res.text.strip():
            res.diagnostic = (f"status={status}, {len(res.text)} chars: "
                              f"{' '.join((out + ' ' + err).split())[:200]}")
            res.history.append(f"attempt {res.attempts}: {res.diagnostic}")
            continue

        # Proof before content. The 2026-09-18 run that answered from memory
        # produced well-formed JSON with an invented verbatim quote; only the
        # empty .tempmediaStorage told the truth about it.
        res.tempmedia = tempmedia_proof(res.conversation_id, staged,
                                        gemini_root=gemini_root)
        res.tempmedia_ok = tempmedia_ok(res.tempmedia)
        if not res.tempmedia_ok:
            unread = [n for n, v in res.tempmedia.items() if v is False]
            res.diagnostic = f"not ingested (no sha256 match under .tempmediaStorage): {unread}"
            res.history.append(f"attempt {res.attempts}: {res.diagnostic}")
            continue

        res.tool_audit, problems = audit_tools(res.conversation_id, workspace=ws,
                                               gemini_root=gemini_root)
        if problems:
            res.diagnostic = f"tool audit failed: {'; '.join(problems)[:200]}"
            res.history.append(f"attempt {res.attempts}: {res.diagnostic}")
            continue

        data = extract_json(res.text)
        if data is None:
            res.diagnostic = f"reply is not a JSON object: {res.text[:160]!r}"
            res.history.append(f"attempt {res.attempts}: {res.diagnostic}")
            continue

        why = validate(data) if validate else None
        if why:
            res.diagnostic = f"rejected: {why}"
            res.history.append(f"attempt {res.attempts}: {res.diagnostic}")
            continue

        res.data = data
        res.diagnostic = (f"ok in {res.duration_s}s, db {res.db_bytes} bytes, "
                          f"agy {res.agy_version}"
                          + (f", attempt {res.attempts}" if res.attempts > 1 else ""))
        res.history.append(f"attempt {res.attempts}: {res.diagnostic}")
        return res
    return res
