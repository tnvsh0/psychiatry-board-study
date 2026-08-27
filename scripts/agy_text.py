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
            secs = parse_reset_seconds(blob)
            if not (wait_for_quota and secs) or waited + secs > max_wait_s:
                raise QuotaExhausted(
                    f"agy text quota exhausted; resets in {secs}s" if secs
                    else "agy text quota exhausted", secs)
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
