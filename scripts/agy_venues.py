#!/usr/bin/env python3
"""Re-author rejected venues through Antigravity instead of Claude subagents.

The two venue rounds cost about 4 million subagent tokens between them. The
work is well-specified — a fixed contract, a fixed output shape, a batch of
chapters with the exact reason each was rejected — so it does not need to run
on the expensive quota. `agy` bills to Google's.

    python scripts/agy_venues.py --dry-run
    python scripts/agy_venues.py --batch-size 5

Output goes to _bulk/venue_result_agy.json in the same shape the workflow
produced, so scripts/merge_venue_repair.py consumes it unchanged.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
AGY = shutil.which("agy") or shutil.which("agy.exe") or "agy"
MODEL = "gemini-3.7-flash-high"

CONTRACT = """You are authoring VENUES for a Hebrew psychiatry board-exam course that
teaches through SketchyMedical-style mnemonic pictures. One venue per chapter.
The venue is the world the chapter's picture is drawn in and it is the
retrieval hook: a resident who remembers the place remembers the chapter.

THE RULES:

1. A MUNDANE PLACE whose NAME is a pun on the topic. Never pick a setting for
   being bizarre or mood-matched. Real examples from the corpus:
     travel agency "expreSS tRIps"   <- SSRI, letters in order inside the name
     gallery "STARRY GAZING"         <- the -azine suffix
     "Z.Z.Z. MATTRESSES"             <- the three z-drugs
     "Ben's Diner"                   <- BENZodiazepines
     barbershop                      <- BARBiturates
     "Chateau Tyramine" winery       <- the MAOI-tyramine interaction IS the fact
     a castle siege                  <- wall = glomerular basement membrane

2. FOUR LEGAL DERIVATIONS, and you must name which one you used:
     PHONETIC_PUN        the sound of the topic hides in an ordinary name
     ACRONYM_EMBED       the letters sit in order inside ordinary words
     SPATIAL_ISOMORPHISM the place's STRUCTURE maps onto the anatomy or concept
     LITERAL_PICTURE     the world IS the plain-English picture inside the term

3. NEVER PRINT THE DIAGNOSIS ON THE SIGN. "MAJOR DEPRESSION QUARRY" is wrong:
   it spends the largest lettering slot on the one word already known.

4. THE PLACE CATEGORY MUST BE GLOBALLY UNIQUE across all 144 chapters. You are
   given every category already taken. Do not reuse one or a thin synonym.

5. THE VENUE MUST HOST THE CHAPTER'S REQUIRED LEXICON TOKENS — fixed scenes
   reused across the course. They appear INSIDE the venue. A venue that cannot
   contain them is wrong; the venue changes, never the token.

6. NO PROTAGONIST, ONE SHARED AMBIENT ACTIVITY, and the largest object in the
   frame is INANIMATE.

Return ONLY a JSON object, no prose and no markdown fence:
{"venues": [{"index": <int, unchanged>,
             "sign_text": "<the pun, UPPER CASE, not the diagnosis>",
             "place": "<2-3 deadpan sentences: an ordinary place on an ordinary day>",
             "activity": "<the ONE thing everybody present is doing>",
             "anchor_structure": "<largest INANIMATE thing, cutting the frame diagonally>",
             "derivation": "PHONETIC_PUN|ACRONYM_EMBED|SPATIAL_ISOMORPHISM|LITERAL_PICTURE",
             "pun_explanation": "<one sentence: exactly how the name encodes the topic>",
             "place_category": "<2-3 words naming the KIND of place>",
             "stock_props": ["<8-14 ordinary objects, favouring things that bear digits or lettering>"],
             "hosts_tokens": ["<for each required token, where it sits here>"],
             "confidence": "strong|workable"}]}
"""


def run_agy(prompt: str, timeout: int = 900) -> str:
    r = subprocess.run([AGY, "--model", MODEL, f"--print={prompt}"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"agy rc={r.returncode}: {(r.stderr or r.stdout)[:300]}")
    return r.stdout or ""


def parse_venues(raw: str) -> list:
    """Pull the venues array out of whatever agy wrapped it in."""
    a, b = raw.find("{"), raw.rfind("}")
    if a == -1 or b <= a:
        return []
    try:
        return json.loads(raw[a:b + 1]).get("venues", [])
    except json.JSONDecodeError:
        # a fenced block, or prose either side of it
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
        if m:
            try:
                return json.loads(m.group(1)).get("venues", [])
            except json.JSONDecodeError:
                pass
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    res = json.loads((ROOT / "_bulk" / "venue_result2.json").read_text(encoding="utf-8"))
    redo = json.loads((ROOT / "_bulk" / "venue_redo.json").read_text(encoding="utf-8"))
    V = {v["index"]: v for v in json.loads(
        (ROOT / "schedule" / "venues.json").read_text(encoding="utf-8"))["venues"]}
    S = {d["index"]: d for d in json.loads(
        (ROOT / "schedule" / "schedule.json").read_text(encoding="utf-8"))["days"]}
    LEX = json.loads((ROOT / "schedule" / "lexicon.json").read_text(encoding="utf-8"))["tokens"]

    def scene(t):
        v = LEX.get(t)
        return (str(v.get("scene") or v.get("description") or "") if isinstance(v, dict)
                else str(v or ""))[:110]

    jobs = []
    for f in res.get("flagged", []):
        i = f["index"]
        d = S.get(i, {})
        jobs.append({
            "index": i, "title_en": d.get("title_en", ""), "title_he": d.get("title_he", ""),
            "aspects": d.get("aspects", []),
            "required_tokens": {t: scene(t) for t in d.get("symbols_needed", []) if t in LEX},
            "why_the_last_attempt_was_rejected": (f.get("problems") or [])[:5],
            "rejected_sign": f.get("sign_text"),
            "rejected_place_category": f.get("place_category"),
        })
    taken = sorted({(v.get("place_category") or "") for i, v in V.items()
                    if i not in {j["index"] for j in jobs} and v.get("place_category")})

    print(f"{len(jobs)} chapter(s) to re-author via agy ({MODEL}), "
          f"{len(taken)} categories taken")
    if a.dry_run:
        for j in jobs[:10]:
            print(f"  ch{j['index']:3} {j['title_he'][:34]:36} "
                  f"{(j['why_the_last_attempt_was_rejected'] or [''])[0][:60]}")
        return 0

    out, failed = [], []
    for k in range(0, len(jobs), a.batch_size):
        batch = jobs[k:k + a.batch_size]
        ids = [j["index"] for j in batch]
        prompt = (CONTRACT
                  + "\nCATEGORIES ALREADY TAKEN — do not reuse or paraphrase any:\n"
                  + ", ".join(taken)
                  + "\n\nRE-AUTHOR THESE CHAPTERS. Each carries the reason its last "
                    "attempt was rejected; fix exactly that.\n"
                  + json.dumps(batch, ensure_ascii=False))
        t0 = time.time()
        try:
            vs = parse_venues(run_agy(prompt))
        except Exception as e:
            print(f"  chapters {ids}: FAILED {e}", file=sys.stderr)
            failed += ids
            continue
        got = {v.get("index") for v in vs}
        missing = [i for i in ids if i not in got]
        out.extend(v for v in vs if v.get("index") in ids)
        print(f"  chapters {ids}: {len(vs)} returned in {time.time()-t0:.0f}s"
              + (f"  MISSING {missing}" if missing else ""), flush=True)
        failed += missing
        # each new category is now taken, so later batches cannot collide with it
        taken.extend(v.get("place_category", "") for v in vs if v.get("place_category"))

    dest = ROOT / "_bulk" / "venue_result_agy.json"
    dest.write_text(json.dumps({"accepted": out, "flagged": [],
                                "not_returned": sorted(set(failed))},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(out)} venue(s) authored, {len(set(failed))} not returned -> {dest.name}")
    print("  merge with: python scripts/merge_venue_repair.py "
          "_bulk/venue_result_agy.json --dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
