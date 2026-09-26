#!/usr/bin/env python3
"""Every free check over every chapter as PUBLISHED, in minutes rather than hours.

`qc_run --all --dry-run` also fetches, snapshots, plans the judge's calls and
builds every bundle: 3.7 minutes a chapter, nine hours for the corpus, measured
2026-09-26. The §5.4 checks need none of that — `_qc/orig/` already holds the
released bytes, committed by the snapshot (§2.5). This runs the same
`qc_lint.lint_chapter` over them and takes about ten minutes.

It reads the bytes BEFORE hygiene, which is the whole point. qc_run lints the
cleaned text, because §5.9 requires the judge to read cleaned text and the spans
a verdict quotes must be the spans of the final material. That answers "what
still needs the judge or the owner" and cannot answer "what is published right
now": the first sweep reported 34 LaTeX flags in 18 chapters where the releases
carry 925 in 126, because the repair had already run in memory — and under
--dry-run or --no-upload that repair never reaches GitHub.

Writes `_qc/lint_only.json`: per chapter its counts, blocking checks and skipped
checks, plus corpus totals. Nothing is fetched, judged, fixed or uploaded, and it
does not touch the per-chapter `lint.json` a real run owns.

    python scripts/qc_lint_sweep.py
    python scripts/qc_lint_sweep.py --books      # also the two PDF-backed checks
    python scripts/qc_lint_sweep.py --chapters 1,3,19
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import qc_lint  # noqa: E402
import qc_store  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_qc" / "lint_only.json"
ORIG = REPO / "_qc" / "orig"
TEXT_SUFFIXES = (".md", ".json", ".txt")


def chapter_dirs(only: set[int] | None) -> list[Path]:
    if not ORIG.is_dir():
        raise SystemExit(f"no snapshots in {ORIG}; run the QC over a chapter first")
    out = []
    for d in sorted(p for p in ORIG.iterdir() if p.is_dir()):
        if only is not None:
            head = d.name.split("-")
            try:
                if int(head[1]) not in only:
                    continue
            except (IndexError, ValueError):
                continue
        out.append(d)
    return out


def assets_of(d: Path) -> dict:
    """name -> text, or None for a binary. The shape lint_chapter expects: a
    binary asset's presence is what the completeness check reads, never its
    bytes."""
    return {p.name: (p.read_text(encoding="utf-8", errors="replace")
                     if p.suffix in TEXT_SUFFIXES else None)
            for p in sorted(d.iterdir()) if p.is_file()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books", action="store_true",
                    help="also run impossible_citation and value_unsourced "
                         "(needs the PDFs; several minutes a chapter)")
    ap.add_argument("--chapters", help="comma-separated indexes")
    a = ap.parse_args()
    only = ({int(x) for x in a.chapters.replace(" ", "").split(",") if x}
            if a.chapters else None)

    per: dict[str, dict] = {}
    totals: collections.Counter = collections.Counter()
    fired: collections.Counter = collections.Counter()
    blocked: list[str] = []
    dirs = chapter_dirs(only)
    for i, d in enumerate(dirs, 1):
        assets = assets_of(d)
        if not assets:
            continue
        try:
            res = qc_lint.lint_chapter(d.name, assets,
                                       use_books=True if a.books else False)
        except Exception as exc:                            # noqa: BLE001
            # Loud, not swallowed: a chapter that could not be linted must not
            # read as a chapter with nothing wrong (§2.3).
            print(f"[{i}/{len(dirs)}] {d.name}: COULD NOT LINT — {exc}",
                  flush=True)
            per[d.name] = {"error": str(exc)[:300]}
            continue
        counts = res.get("counts") or {}
        per[d.name] = {"counts": counts,
                       "blocking": res.get("blocking") or [],
                       "units": res.get("units"),
                       "skipped": res.get("skipped") or []}
        totals += collections.Counter(counts)
        for check in counts:
            fired[check] += 1
        if res.get("blocking"):
            blocked.append(d.name)
        print(f"[{i}/{len(dirs)}] {d.name}: {sum(counts.values())} flags"
              + (f"  BLOCKING {','.join(res['blocking'])}"
                 if res.get("blocking") else ""), flush=True)

    qc_store.write_json_atomic(OUT, {
        "measured": "released bytes, before hygiene",
        "books": bool(a.books),
        "chapters": per,
        "totals": dict(totals),
        "fired_in": dict(fired),
        "blocked": blocked,
    })
    print(f"\n{len(per)} chapters · {sum(totals.values())} flags · "
          f"{len(blocked)} with a blocking check")
    for check, n in totals.most_common():
        mark = "  [BLOCKING]" if qc_lint.CHECKS.get(check, {}).get("blocking") else ""
        print(f"  {n:>5}  in {fired[check]:>3} chapters  {check}{mark}")
    if not a.books:
        print("  (impossible_citation and value_unsourced not run; --books)")
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
