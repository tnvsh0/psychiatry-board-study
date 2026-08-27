#!/usr/bin/env python3
"""Render plates through the Antigravity CLI's free image quota instead of the paid API.

Every render in this project has been billed to the Gemini API, and most of them
were thrown away: the degradation ladder pays for three drafts to find one
composition. `agy` has a native `generate_image` tool on the account's own
allowance, and it costs nothing.

Two things about it decide the whole design here, and both were learned the
expensive way:

1. THE QUOTA IS A ROLLING 5-HOUR WINDOW, NOT A DAY. About fourteen images, then
   `429 ... Your quota will reset after 3h45m10s`. The reset time is stated in
   the error, so this module parses it and waits exactly that long instead of
   guessing. An evening was lost to waiting for midnight, which is not when it
   resets.

2. THE REAL ERROR IS NOT IN THE STEP STATE. On 429 the agent tries to draw the
   picture itself with Pillow via `run_command`, headless auto-denies that, and
   the only thing that reaches the caller is a message about permissions. The
   429 is visible in the stream, but only if you look at the tool step's own
   text rather than at `state=ERROR`. So this module reads the whole stream and
   reports what actually happened.

There is no resolution parameter — 16:9 is 1376x768 and 1:1 is 1024x1024, and
that is the ceiling on every free route to these models. It is the reason the
plates are split three ways: a third of the elements on the same canvas is what
makes 1K legible.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BRAIN = Path(os.environ.get("AGY_BRAIN",
                            Path.home() / ".gemini" / "antigravity-cli" / "brain"))

# Windows caps a command line at 32767 characters. A full plate prompt is the
# 4.2k style prefix plus the element table, so around 15k — comfortably under,
# but a chapter with an unusually long table should fail loudly rather than be
# silently truncated by the OS.
MAX_PROMPT_CHARS = 24000

# How long one image is allowed to take. Observed: ~10s inside a ~33s agent
# turn. A 429 returns in seconds; a slow render has never exceeded a minute.
TURN_TIMEOUT_S = int(os.environ.get("AGY_TURN_TIMEOUT_S", 300))

# The agent is free to decide HOW to satisfy a request, and left to itself it
# will happily write a Python script that draws the picture with Pillow. That
# is not what we are paying for in wall-clock, and under headless permissions
# it fails anyway. The instruction has to close that door explicitly.
_ENVELOPE = """Call the generate_image tool exactly ONCE with the prompt below, then stop.

Do NOT write any file. Do NOT run any command. Do NOT draw the image yourself
with Pillow, matplotlib, SVG or any other library — if generate_image fails,
report the failure and stop. Do not retry.

ImageName: {name}
AspectRatio: {aspect}

Prompt:
{prompt}
"""


class QuotaExhausted(RuntimeError):
    """429 from the image model. `reset_seconds` is what the server itself said."""

    def __init__(self, message: str, reset_seconds: int | None):
        super().__init__(message)
        self.reset_seconds = reset_seconds


# "Your quota will reset after 3h45m10s" — the server states it, so there is no
# reason for a caller to guess a retry interval.
_RESET = re.compile(r"reset after\s+(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_reset_seconds(text: str) -> int | None:
    m = _RESET.search(text or "")
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def _stream(prompt: str, name: str, aspect: str) -> tuple[str, list[dict]]:
    """Run one agy turn. Returns (joined text of the run, parsed events)."""
    body = _ENVELOPE.format(name=name, aspect=aspect, prompt=prompt)
    if len(body) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt is {len(body)} chars, over the {MAX_PROMPT_CHARS} "
                         f"command-line budget")
    exe = shutil.which("agy") or "agy"
    # A list argv goes straight to CreateProcess: no shell, so no quoting
    # hazard from the prompt's newlines, quotes or Hebrew. The value must be
    # attached with '=' — passed as a separate argument, the CLI takes the NEXT
    # FLAG as the prompt and silently runs the wrong thing.
    cmd = [exe, "--output-format", "stream-json", f"--print={body}"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=TURN_TIMEOUT_S)
    raw = (proc.stdout or "") + (proc.stderr or "")
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    return raw, events


def _conversation_id(events: list[dict]) -> str | None:
    for e in events:
        cid = e.get("conversation_id") or (e.get("init") or {}).get("conversation_id")
        if cid:
            return cid
        for v in e.values():
            if isinstance(v, dict) and v.get("conversation_id"):
                return v["conversation_id"]
    return None


def _tool_ran(events: list[dict]) -> bool:
    for e in events:
        su = e.get("step_update") or {}
        if su.get("tool_name") == "generate_image" and su.get("state") == "DONE":
            return True
    return False


def _newest_image(conv_id: str, since: float) -> Path | None:
    """The image agy just wrote, in its own conversation folder.

    agy never writes to the working directory — the file lands in
    brain/<conversation_id>/<ImageName>_<epoch_ms>.jpg. Concluding that the
    tool 'does not really produce images' from an empty cwd was the first wrong
    turn this module exists to remember.
    """
    d = BRAIN / conv_id
    if not d.is_dir():
        return None
    imgs = [p for p in d.glob("*.jpg") if p.stat().st_mtime >= since - 5]
    imgs += [p for p in d.glob("*.png") if p.stat().st_mtime >= since - 5]
    return max(imgs, key=lambda p: p.stat().st_mtime) if imgs else None


def generate(prompt: str, output_path: str | Path, *, aspect: str = "16:9",
             name: str = "plate", wait_for_quota: bool = True,
             max_wait_s: int = 6 * 3600) -> bool:
    """Render one image to output_path. Returns True on success.

    On 429 with wait_for_quota, sleeps for exactly as long as the server said
    the window has left and tries once more.
    """
    out = Path(output_path)
    deadline_used = 0

    while True:
        t0 = time.time()
        try:
            raw, events = _stream(prompt, name, aspect)
        except subprocess.TimeoutExpired:
            print(f"[!] agy: no response in {TURN_TIMEOUT_S}s", file=sys.stderr)
            return False
        except ValueError as e:
            print(f"[!] agy: {e}", file=sys.stderr)
            return False

        if "429" in raw or "exhausted your capacity" in raw:
            secs = parse_reset_seconds(raw)
            if not (wait_for_quota and secs) or deadline_used + secs > max_wait_s:
                raise QuotaExhausted(
                    f"agy image quota exhausted; resets in {secs}s" if secs
                    else "agy image quota exhausted", secs)
            print(f"[i] agy quota exhausted; the server says it resets in "
                  f"{secs // 60}m — waiting.", file=sys.stderr)
            time.sleep(secs + 30)
            deadline_used += secs + 30
            continue

        cid = _conversation_id(events)
        if not cid:
            print("[!] agy: no conversation id in stream", file=sys.stderr)
            return False
        if not _tool_ran(events):
            # The agent answered without drawing — usually because it decided
            # to explain rather than act. Worth seeing, not worth retrying
            # blind.
            tail = " ".join(raw.split())[-300:]
            print(f"[!] agy: generate_image never completed. tail: {tail}",
                  file=sys.stderr)
            return False

        img = _newest_image(cid, t0)
        if not img:
            print(f"[!] agy: tool reported DONE but no image in {BRAIN / cid}",
                  file=sys.stderr)
            return False

        out.parent.mkdir(parents=True, exist_ok=True)
        # agy writes JPEG. The number compositing keys on flat chroma patches,
        # and JPEG's chroma subsampling softens exactly those edges, so the
        # caller is told what it is getting rather than left to discover it in
        # a failed quad detection.
        if out.suffix.lower() in (".png", ".webp"):
            try:
                from PIL import Image
                Image.open(img).convert("RGB").save(out)
            except Exception:
                shutil.copy2(img, out)
        else:
            shutil.copy2(img, out)
        return out.exists() and out.stat().st_size > 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Render one image through agy's free quota")
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--aspect", default="16:9")
    ap.add_argument("--name", default="plate")
    a = ap.parse_args()
    text = Path(a.prompt_file).read_text(encoding="utf-8")
    t0 = time.time()
    ok = generate(text, a.out, aspect=a.aspect, name=a.name, wait_for_quota=False)
    print(f"{'ok' if ok else 'FAILED'} in {time.time() - t0:.1f}s -> {a.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
