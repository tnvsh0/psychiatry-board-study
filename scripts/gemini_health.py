#!/usr/bin/env python3
"""Is Gemini healthy enough to start a chapter?

Chapter 29 ran straight into a 503 storm - "this model is currently
experiencing high demand" - and spent forty minutes producing a plate whose
gates never answered, narration that never generated, and a director that
returned nothing. Every one of those calls costs money and the retry ladder
burns 2K renders while it happens.

A bulk run should not walk into that. This sends a handful of trivial prompts
at the models the pipeline actually uses and reports whether they answer.

    python scripts/gemini_health.py            # one check, exit 0 if healthy
    python scripts/gemini_health.py --wait 40  # block until healthy, up to 40 min
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import gemini_image as gi  # noqa: E402  also loads .env

PROBES = 3          # a single 503 proves nothing; a storm fails most of them
HEALTHY_AT = 2      # this many of PROBES must answer


def check(model: str | None = None) -> tuple[int, int, str]:
    """(answered, asked, note) for the text model the gates and director use.

    Under SKETCHY_ENGINE=agy this asks agy, because that is what the director
    and the gates actually run on. Left pointing at the paid API it reported on
    a service the pipeline no longer uses — and it ran at the head of EVERY
    chapter, which is how a run that costs nothing still opened with a billed
    call. The paid-call guard is what surfaced it.
    """
    if gi.ENGINE == "agy":
        import agy_text
        ok, note = 0, ""
        for i in range(PROBES):
            try:
                if (agy_text.ask(f"Reply with the single word OK ({i}).",
                                 wait_for_quota=False) or "").strip():
                    ok += 1
            except Exception as e:  # noqa: BLE001
                note = str(e)[:90]
            time.sleep(1)
        return ok, PROBES, note

    model = model or gi.CAPTION_MODEL
    client = gi._get_client(None)
    if client is None:
        return 0, PROBES, "no GEMINI_API_KEY"
    ok, note = 0, ""
    for i in range(PROBES):
        try:
            # Deliberately not through _gc: the retry wrapper would mask the
            # very condition this is trying to measure.
            r = client.models.generate_content(
                model=model, contents=f"Reply with the single word OK ({i}).")
            if (getattr(r, "text", "") or "").strip():
                ok += 1
        except Exception as e:
            note = str(e)[:90]
        time.sleep(1)
    return ok, PROBES, note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=0,
                    help="block until healthy, up to this many minutes")
    ap.add_argument("--interval", type=int, default=300,
                    help="seconds between checks while waiting")
    a = ap.parse_args()

    deadline = time.time() + a.wait * 60
    while True:
        ok, asked, note = check()
        healthy = ok >= HEALTHY_AT
        stamp = time.strftime("%H:%M:%S")
        print(f"{stamp}  {ok}/{asked} probes answered"
              + (f"  ({note})" if note else "")
              + ("  HEALTHY" if healthy else "  DEGRADED"), flush=True)
        if healthy:
            return 0
        if time.time() >= deadline:
            print("still degraded; not starting work", file=sys.stderr)
            return 1
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
