#!/usr/bin/env python3
"""
Merge a venue-repair workflow run into the working files, then re-audit.

The repair runs keep getting cut off mid-flight by usage limits, so their
partial output has to be recoverable without re-running anything. Everything
needed is already in the run's journal.jsonl; this reads it and folds it in.

It also drops STALE VERDICTS. A critic verdict names a chapter, not a venue, so
after a chapter is re-authored its old REJECT still sits there judging a venue
that no longer exists — which queued 33 chapters for a repair they had already
had. Any verdict on a re-authored chapter that did not come from this run is
discarded.

Usage:
    python scripts/_merge_venue_run.py wf_73414844-2d1
    python scripts/_merge_venue_run.py --latest
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = Path(
    r"C:\Users\User\.claude\projects"
    r"\C--Users-User-OneDrive-Desktop-CLAUDE-notebooklm-py-main-psychiatry-board-study"
    r"--claude-worktrees-admiring-snyder-5e163b"
    r"\bf760d97-a99a-44a8-a075-4f63cfdcd94f\subagents\workflows"
)
VENUES = ROOT / "_venues_partial.json"
VERDICTS = ROOT / "_verdicts_partial.json"


def read_run(run_id: str) -> tuple[list, list]:
    j = WF / run_id / "journal.jsonl"
    if not j.exists():
        raise SystemExit(f"no journal at {j}")
    venues, verdicts = [], []
    for line in j.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "result" and isinstance(d.get("result"), dict):
            venues += d["result"].get("venues") or []
            verdicts += d["result"].get("verdicts") or []
    return venues, verdicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--latest", action="store_true")
    a = ap.parse_args()

    run_id = a.run_id
    if a.latest or not run_id:
        runs = sorted(WF.glob("wf_*"), key=lambda p: p.stat().st_mtime)
        if not runs:
            raise SystemExit("no workflow runs found")
        run_id = runs[-1].name
    print(f"merging run {run_id}")

    new_v, new_verd = read_run(run_id)
    print(f"  journal holds {len(new_v)} venues, {len(new_verd)} verdicts")
    if not new_v and not new_verd:
        print("  nothing to merge")
        return 0

    venues = {v["index"]: v for v in json.loads(VENUES.read_text(encoding="utf-8"))}
    for v in new_v:
        v["_reauthored"] = True
        venues[v["index"]] = v

    verdicts = {v["index"]: v for v in json.loads(VERDICTS.read_text(encoding="utf-8"))}
    fresh = {v["index"] for v in new_verd}
    stale = [i for i, v in venues.items()
             if v.get("_reauthored") and i in verdicts and i not in fresh]
    for i in stale:
        verdicts.pop(i)
    for v in new_verd:
        verdicts[v["index"]] = v

    VENUES.write_text(json.dumps([venues[i] for i in sorted(venues)],
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    VERDICTS.write_text(json.dumps([verdicts[i] for i in sorted(verdicts)],
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  venues now {len(venues)}; verdicts {len(verdicts)} "
          f"({len(stale)} stale dropped)")

    print()
    subprocess.run([sys.executable, str(ROOT / "scripts" / "_venue_audit.py"),
                    "--json", str(ROOT / "_venue_audit.json")], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
