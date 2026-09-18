#!/usr/bin/env python3
"""agy with file attachments, for judges that must read a source.

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

So every reference here is an absolute path with forward slashes, and every
caller passes a `validate` that checks the answer against text we extracted
ourselves (a canary quote). A reply that fails it is retried, and after the
retries the caller gets `data=None` — never a verdict it cannot trust.

The rest is carried over from the podcast project's judge, where each rule was
a failed run first (psychiatry-book-podcasts/scripts/agy_judge.py):
  - the prompt goes inline; `@prompt.txt` makes agy reach for a denied tool
  - Pro only: Flash drops the attachment and shells out on a long instruction
    (and here it returned an empty SUCCESS on the plate vision gate)
  - tell it the files are already attached, or it sometimes runs pdftotext
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agy_image import QuotaExhausted, parse_reset_seconds  # noqa: E402

DEFAULT_MODEL = os.environ.get("AGY_JUDGE_MODEL", "gemini-3.1-pro-high").strip()
PRINT_TIMEOUT = os.environ.get("AGY_PRINT_TIMEOUT", "20m")
# --print-timeout should end the run; this is for an agy that wedges anyway.
SUBPROCESS_TIMEOUT_S = int(os.environ.get("AGY_ATTACH_TIMEOUT_S", 1800))
BLIND_WAIT_S = 1200
CONVERSATIONS = Path.home() / ".gemini" / "antigravity-cli" / "conversations"

# CreateProcess caps a whole command line at 32,767 characters. Anything long —
# a study guide, a quiz — goes in as an attachment, not into the prompt.
MAX_INLINE_PROMPT = 28000

NO_TOOLS = (
    "The files referenced above are ATTACHED to this message and already "
    "available to you. Read them directly. Do NOT run any command, shell "
    "pipeline or tool to open, convert, extract or list them — no cat, no "
    "pdftotext, no python, no Get-ChildItem. Any such call is denied and you "
    "will produce nothing.\n\n")


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

    @property
    def ok(self) -> bool:
        return self.data is not None


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


def refs(files) -> str:
    """`@` references agy will actually attach: absolute, forward slashes."""
    return " ".join("@" + Path(f).resolve().as_posix() for f in files)


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


def conversation_bytes(conversation_id: str | None) -> int | None:
    """Size of agy's store for one conversation, database plus its WAL."""
    if not conversation_id:
        return None
    total, seen = 0, False
    for suffix in (".db", ".db-wal"):
        p = CONVERSATIONS / f"{conversation_id}{suffix}"
        try:
            total += p.stat().st_size
            seen = True
        except OSError:
            pass
    return total if seen else None


def ask_with_files(prompt: str, files, *, model: str | None = None,
                   attempts: int = 3,
                   validate: Callable[[dict], str | None] | None = None,
                   wait_for_quota: bool = True,
                   max_wait_s: int = 6 * 3600) -> AgyResult:
    """One judgment over attached files. `result.data` is None unless an
    attempt returned JSON that `validate` accepted.

    `validate(data)` returns None when the answer is usable, or a reason. It is
    where the caller proves the files were read — a canary quote checked
    against our own extraction — so a confident answer written without the
    source is retried like any other failure instead of being believed.
    """
    files = [Path(f) for f in files]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        return AgyResult(diagnostic=f"attachment missing: {missing}")
    if len(prompt) > MAX_INLINE_PROMPT:
        raise ValueError(f"prompt is {len(prompt)} chars; attach long material "
                         f"as a file instead (limit {MAX_INLINE_PROMPT})")

    exe = agy_exe()
    body = f"{refs(files)}\n{NO_TOOLS}{prompt}"
    cmd = [exe, "-p", body, "--model", model or DEFAULT_MODEL,
           "--print-timeout", PRINT_TIMEOUT, "--output-format", "json"]
    # agy's own shell does not start here, but a relative path it decides to
    # resolve would; the attachments' directory is the least surprising cwd.
    cwd = str(files[0].resolve().parent) if files else None

    res, waited = AgyResult(), 0
    while res.attempts < attempts:
        res.attempts += 1
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=SUBPROCESS_TIMEOUT_S, cwd=cwd)
        except subprocess.TimeoutExpired:
            res.diagnostic = f"no reply within {SUBPROCESS_TIMEOUT_S}s"
            res.history.append(res.diagnostic)
            continue
        res.duration_s = round(time.time() - t0, 1)
        blob = (proc.stdout or "") + "\n" + (proc.stderr or "")

        if "429" in blob or "exhausted your capacity" in blob:
            secs = parse_reset_seconds(blob) or BLIND_WAIT_S
            if not wait_for_quota or waited + secs > max_wait_s:
                raise QuotaExhausted(
                    f"agy quota exhausted; resets in {secs}s", secs)
            print(f"[i] agy quota exhausted; resets in {secs // 60}m — waiting",
                  file=sys.stderr, flush=True)
            time.sleep(secs + 30)
            waited += secs + 30
            res.attempts -= 1          # a quota wait is not a failed attempt
            continue

        try:
            env = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            res.diagnostic = (f"no JSON envelope (rc={proc.returncode}): "
                              f"{' '.join(blob.split())[:200]}")
            res.history.append(res.diagnostic)
            continue

        res.conversation_id = env.get("conversation_id")
        res.text = env.get("response") or ""
        res.db_bytes = conversation_bytes(res.conversation_id)
        status = env.get("status")
        if status != "SUCCESS" or not res.text.strip():
            res.diagnostic = (f"status={status}, {len(res.text)} chars: "
                              f"{' '.join(blob.split())[:200]}")
            res.history.append(res.diagnostic)
            continue

        data = extract_json(res.text)
        if data is None:
            res.diagnostic = f"reply is not a JSON object: {res.text[:160]!r}"
            res.history.append(res.diagnostic)
            continue

        reason = validate(data) if validate else None
        if reason:
            res.diagnostic = f"rejected: {reason}"
            res.history.append(res.diagnostic)
            continue

        res.data = data
        res.diagnostic = (f"ok in {res.duration_s}s, db {res.db_bytes} bytes"
                          + (f", attempt {res.attempts}" if res.attempts > 1
                             else ""))
        res.history.append(res.diagnostic)
        return res
    return res
