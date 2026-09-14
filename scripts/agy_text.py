#!/usr/bin/env python3
"""Run the pipeline's TEXT calls through the Antigravity CLI's free quota.

Moving the renders to agy left the cheap half of the pipeline on the paid API,
and on 2026-08-27 that half stopped: `429 ... Your project has exceeded its
monthly spending cap`. The images were free by then and it made no difference,
because the scene director builds the element table on the text model — so no
plate could be produced at all. The gates and the narration go the same way.

agy answers text on the same account and a separate allowance from its image
tool (the 429 names the model it applies to), so this is what makes the whole
chapter free rather than only the pictures.

Two constraints carried over from agy_image, both learned expensively:
  - the prompt must be attached to the flag with `=`. Passed as a separate
    argument, the CLI takes the NEXT FLAG as the prompt and runs the wrong
    thing without saying so.
  - a 429 states its own reset time, so a caller waits exactly that long
    instead of guessing, and never waits for midnight.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

from agy_image import QuotaExhausted, parse_reset_seconds

# Windows caps a command line at 32767 characters. The largest director prompt
# measured across chapter 29's plates is 13.4k, so this leaves room while still
# failing loudly rather than letting the OS truncate a brief in silence.
MAX_PROMPT_CHARS = 24000

TIMEOUT_S = int(os.environ.get("AGY_TEXT_TIMEOUT_S", 420))


# A 429 that does not state its reset time. Most do, and the stated value is
# always preferred — but a render that gives up because the server phrased the
# refusal differently is a chapter lost to a wording. Long enough not to hammer
# a wall that lasts hours, short enough to pick the work back up.
BLIND_WAIT_S = 1200

# Which model answers questions ABOUT AN IMAGE. Empty means agy's own default,
# which is a Flash tier — and a Flash model paired with an attachment and a
# long instruction does not fail, it returns status SUCCESS with an empty
# response. Measured here on the chapter-33 criteria plate: the batched gate
# came back {"response": ""} after 46.8s, while a one-line question about the
# same image on the same default model answered correctly. A peer project saw
# the identical split across six chapters with audio attachments.
VISION_MODEL = os.environ.get("AGY_VISION_MODEL", "gemini-3.1-pro-high").strip()


def ask(prompt: str, *, wait_for_quota: bool = True,
        max_wait_s: int = 6 * 3600) -> str | None:
    """One text turn. Returns the response, or None if it could not run.

    None means "we learned nothing" — a distinction the gates depend on, since
    scoring a probe outage as a failed render would burn the retry budget on
    frames that were fine.
    """
    if len(prompt) > MAX_PROMPT_CHARS:
        print(f"[!] agy text: prompt is {len(prompt)} chars, over the "
              f"{MAX_PROMPT_CHARS} command-line budget", file=sys.stderr)
        return None

    exe = shutil.which("agy") or "agy"
    waited = 0
    while True:
        try:
            # A list argv goes straight to CreateProcess, so the prompt's
            # newlines, quotes and Hebrew need no escaping.
            proc = subprocess.run([exe, f"--print={prompt}"],
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            print(f"[!] agy text: no response in {TIMEOUT_S}s", file=sys.stderr)
            return None

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        blob = out + "\n" + err

        if "429" in blob or "exhausted your capacity" in blob:
            secs = parse_reset_seconds(blob) or BLIND_WAIT_S
            if not wait_for_quota or waited + secs > max_wait_s:
                raise QuotaExhausted(
                    f"agy text quota exhausted; resets in {secs}s", secs)
            print(f"[i] agy text quota exhausted; the server says it resets in "
                  f"{secs // 60}m — waiting.", file=sys.stderr)
            time.sleep(secs + 30)
            waited += secs + 30
            continue

        if not out:
            print(f"[!] agy text: empty response. {' '.join(err.split())[:200]}",
                  file=sys.stderr)
            return None
        return out


def ask_about_image(image_path, question: str, *, wait_for_quota: bool = True,
                    max_wait_s: int = 6 * 3600) -> str | None:
    """One vision question, on the free quota. None if it could not run.

    agy's print mode takes no attachment, so the image goes in the way the
    agent can reach it: as a file it opens with view_file. Two things make
    that work and neither is obvious.

    The directory must be added to the workspace with --add-dir. Files inside
    the workspace root get read access automatically under the default review
    mode; anything outside needs a permission headless cannot prompt for, and
    the run dies with "a tool required the read_file permission ... auto-
    denied". A render sitting in a scratchpad is outside by definition.

    And the agent must be pointed at view_file explicitly. Left to itself it
    reaches for a shell command to inspect the file, which headless denies for
    a different reason and reports as the same wall.
    """
    from pathlib import Path
    img = Path(image_path)
    if not (img.exists() and img.stat().st_size > 0):
        return None

    return _ask_image_raw(image_path, question, wait_for_quota=wait_for_quota,
                          max_wait_s=max_wait_s)


def _ask_image_raw(image_path, question: str, *, extra: list | None = None,
                   wait_for_quota: bool = True,
                   max_wait_s: int = 6 * 3600) -> str | None:
    from pathlib import Path
    img = Path(image_path)
    if not (img.exists() and img.stat().st_size > 0):
        return None

    prompt = (f"Open the image file {img.name} with the view_file tool and look "
              f"at it. Do NOT run any shell command.\n\n{question}")
    exe = shutil.which("agy") or "agy"
    # An ABSOLUTE directory. agy's shell runs in a scratch directory of its
    # own, so a relative --add-dir names somewhere else entirely — and it does
    # not fail, it goes looking, which a peer project watched end in a
    # recursive scan of the whole drive that headless then auto-denied.
    add_dir = str(img.parent.resolve())
    model = [] if not VISION_MODEL else ["--model", VISION_MODEL]
    waited = attempts = 0
    while True:
        try:
            proc = subprocess.run(
                [exe, "--add-dir", add_dir, *model, *(extra or []),
                 f"--print={prompt}"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=TIMEOUT_S, cwd=str(img.parent))
        except subprocess.TimeoutExpired:
            print(f"[!] agy vision: no response in {TIMEOUT_S}s", file=sys.stderr)
            return None

        out = (proc.stdout or "").strip()
        blob = out + "\n" + (proc.stderr or "")

        if "429" in blob or "exhausted your capacity" in blob:
            secs = parse_reset_seconds(blob) or BLIND_WAIT_S
            if not wait_for_quota or waited + secs > max_wait_s:
                raise QuotaExhausted(
                    f"agy vision quota exhausted; resets in {secs}s", secs)
            print(f"[i] agy vision quota exhausted; resets in {secs // 60}m — "
                  f"waiting.", file=sys.stderr)
            time.sleep(secs + 30)
            waited += secs + 30
            continue

        # A transient miss, not a verdict. Across eleven calibration plates two
        # came back like this — one a 503 "service is currently unavailable",
        # one a SUCCESS carrying an empty response — and each cost that plate
        # its whole measurement. The gates treat "could not run" as unverified
        # rather than as a failed render, so a silent retry here is the
        # difference between measuring nine plates and measuring eleven.
        transient = ("503" in blob or "UNAVAILABLE" in blob
                     or "currently unavailable" in blob or not out)
        if transient and attempts < 2:
            attempts += 1
            print(f"[i] agy vision: transient miss, retry {attempts}/2",
                  file=sys.stderr)
            time.sleep(8 * attempts)
            continue

        if not out or "auto-denied" in blob or "no output produced" in blob:
            print(f"[!] agy vision: {' '.join(blob.split())[:200]}", file=sys.stderr)
            return None
        return out


def ask_about_image_json(image_path, question: str, schema: dict, **kw) -> dict | None:
    """One vision turn returning validated JSON. None if it could not run.

    Every question costs a whole agent turn — about a minute, against seconds
    on the API — so asking six of them separately put ten minutes of gates on
    each rung and forty on each plate, which does not survive 144 chapters.
    One turn answering all six is the difference between free-and-usable and
    free-and-abandoned. --json-schema makes the combined answer parseable
    without guessing at prose.
    """
    import json as _json
    import tempfile
    from pathlib import Path

    fd, spath = tempfile.mkstemp(suffix=".json", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _json.dump(schema, fh)
        # --json-schema is refused unless the output format is json or
        # stream-json, so the answer arrives inside the CLI's own envelope and
        # has to be unwrapped before it can be parsed as our schema.
        ans = _ask_image_raw(image_path, question,
                             extra=["--json-schema", spath,
                                    "--output-format", "json"], **kw)
    finally:
        try:
            os.unlink(spath)
        except OSError:
            pass
    if ans is None:
        return None
    want = set((schema.get("properties") or {}).keys())

    def unwrap(node, depth: int = 0):
        """The object that answers OUR schema, wherever the envelope put it."""
        if depth > 6:
            return None
        if isinstance(node, str):
            s = node.strip()
            if s.startswith("```"):
                s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            i, j = s.find("{"), s.rfind("}")
            if i < 0 or j <= i:
                return None
            try:
                return unwrap(_json.loads(s[i:j + 1]), depth + 1)
            except Exception:
                return None
        if isinstance(node, dict):
            if want & set(node):
                return node
            for key in ("response", "result", "output", "text", "content"):
                if key in node:
                    got = unwrap(node[key], depth + 1)
                    if got is not None:
                        return got
            for v in node.values():
                got = unwrap(v, depth + 1)
                if got is not None:
                    return got
        return None

    data = unwrap(ans)
    if data is None:
        print(f"[!] agy vision: no schema object in {ans.strip()[:200]!r}",
              file=sys.stderr)
    return data


def main() -> int:
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser(description="One text turn through agy's free quota")
    ap.add_argument("--prompt-file", required=True)
    a = ap.parse_args()
    r = ask(Path(a.prompt_file).read_text(encoding="utf-8"), wait_for_quota=False)
    print(r if r is not None else "(no answer)")
    return 0 if r else 1


if __name__ == "__main__":
    raise SystemExit(main())
