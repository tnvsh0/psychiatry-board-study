#!/usr/bin/env python3
"""§6's page-limit measurement: how big a bundle does the judge still read?

The per-call page limit decides how many agy calls a chapter costs, and a
chapter costs 5-8 today. Raising 180 to 250 measured -34% of calls across six
chapters — but only if the judge still reads the END of the bigger bundle. §6
says to measure that, and qc_sources prepares it: `measure_limit` builds a
bundle of about N pages and picks three canary pages at 5%, 50% and 95% of it.
This sends those three prompts and checks the answers.

The tail code is the whole measurement. A front code that verifies while the
tail one does not is exactly a bundle that is too large: the judge started
reading and stopped, which is the failure mode the per-call limit exists for.

One agy call per size, and it waits out a quota lockout rather than failing —
the calibration run is spending the same quota, so this is written to share the
window rather than to win it.

    python scripts/qc_measure_limits.py                 120, 180, 250 on ch.19
    python scripts/qc_measure_limits.py --sizes 250,300 --chapter 29
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agy_attach  # noqa: E402
import qc_cite  # noqa: E402
import qc_sources  # noqa: E402
import qc_store  # noqa: E402
import qc_units  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_qc" / "limits"
# Chapter 19's guide is the one whose source-map unit needed 195 pages of its
# own, so it is where a higher limit has the most to prove.
DEFAULT_CHAPTER = 19
DEFAULT_SIZES = (120, 180, 250)


def assets_for(tag: str) -> dict:
    """The released text of one chapter, from the calibration cache or a fetch."""
    for base in (REPO / "_qc" / "cache" / "calibration" / tag,
                 REPO / "_qc" / "orig" / tag,
                 REPO / "_qc" / "calibration" / "qc" / "orig" / tag):
        if base.is_dir():
            out = {p.name: p.read_text(encoding="utf-8", errors="replace")
                   for p in base.iterdir()
                   if p.suffix in (".md", ".json", ".txt")}
            if out:
                return out
    raise SystemExit(f"no local copy of {tag}; run qc_calibrate --build first")


def one(tag: str, day: int, size: int, work: Path, *, model=None) -> dict:
    units = qc_units.units(assets_for(tag))
    cites = qc_cite.cites_by_unit(units)
    rec = qc_sources.measure_limit(tag=tag, day=day, units=units, cites=cites,
                                   target=size, work_dir=work / f"n{size}")
    print(f"\n[{size}] bundle {rec['bundle_pages']} pages "
          f"({rec['pdf_pages']} source), {rec['bundle_bytes'] / 1e6:.1f}MB, "
          f"canary at " + ", ".join(f"{r['book']} p.{r['printed']}"
                                    for r in rec["canary"]), flush=True)
    while True:
        try:
            res = agy_attach.ask_with_files(
                rec["prompt"], [Path(rec["bundle"]), Path(rec["artifacts"])],
                model=model or agy_attach.DEFAULT_MODEL, attempts=2)
            break
        except agy_attach.QuotaPaused as exc:
            wait = max(60, int(exc.resume_after_epoch - time.time()) + 60)
            print(f"[{size}] quota — sleeping {wait // 60}m", flush=True)
            time.sleep(wait)
    out = qc_sources.check_answer(rec, res.data or res.text or "")
    out.update(size=size, tag=tag, agy_version=getattr(res, "agy_version", ""),
               duration_s=getattr(res, "duration_s", None),
               attempts=getattr(res, "attempts", None),
               tempmedia_ok=getattr(res, "tempmedia_ok", None),
               bundle_pages=rec["bundle_pages"], bundle_bytes=rec["bundle_bytes"],
               diagnostic=getattr(res, "diagnostic", ""))
    print(f"[{size}] {out.get('verified')}/{out.get('of')} codes verified · "
          f"tail {'OK' if out.get('tail_verified') else 'FAILED'} · "
          f"nonce {'ok' if out.get('nonce_ok') else out.get('nonce_problem')} · "
          f"{'PASSES' if out.get('passes') else 'does not pass'}"
          + (f" · {out['problems']}" if out.get("problems") else ""), flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default=",".join(str(n) for n in DEFAULT_SIZES))
    ap.add_argument("--chapter", type=int, default=DEFAULT_CHAPTER)
    ap.add_argument("--model")
    a = ap.parse_args()

    _path, tag, day = qc_sources.find_chapter(str(a.chapter))
    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / "work"
    results = []
    for size in [int(s) for s in a.sizes.split(",") if s.strip()]:
        results.append(one(tag, day, size, work, model=a.model))
        qc_store.write_json_atomic(OUT / "result.json",
                                   {"chapter": day, "tag": tag,
                                    "results": results})
    print("\n=== §6 page limit ===")
    for r in results:
        print(f"  {r['size']:>4} pages ({r.get('bundle_pages')} in the bundle): "
              f"tail {'OK' if r.get('tail_verified') else 'FAILED'}  "
              f"({r.get('verified')}/{r.get('of')} codes, {r.get('duration_s')}s)")
    passing = [r["size"] for r in results if r.get("passes")]
    print(f"largest size whose LAST-10% canary verified: "
          f"{max(passing) if passing else 'none'}")
    print(f"written to {OUT / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
