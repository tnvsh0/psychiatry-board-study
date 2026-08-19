#!/usr/bin/env python3
"""
Run one Sketchy v2 plate end to end.

    facts -> plates -> director -> element table -> validate
          -> deterministic image prompt -> render -> gates -> legend + quiz + narration

Everything structural is owned by sketchy_spec; this module is the orchestration
and the retry policy. The retries are a DEGRADATION LADDER, not resampling:
re-rolling the same prompt four times just pays four times for the same answer.
Try 2 restates only what was missed. Try 3 drops the lowest-priority required
strings so the model has fewer constraints to satisfy, and code marks those
drawn=false so the legend still carries them as plain rows. Try 4 accepts the
best render seen.

Usage:
    python plate_pipeline.py --day 28 --plate main --out _proto/plate
    python plate_pipeline.py --day 28 --dry-run      # prompts only, no API
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemini_image as gi  # noqa: E402
import narration as narr  # noqa: E402
import sketchy_spec as spec  # noqa: E402
import _proto_number_plate as comp_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MAX_TRIES = 4


def load_venue(index: int) -> dict:
    for name in ("schedule/venues.json", "_venues_partial.json"):
        p = ROOT / name
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("venues", [])
        for v in rows:
            if v.get("index") == index:
                return v
    raise SystemExit(f"no venue for chapter {index}")


def load_tokens(day: dict) -> dict:
    """The course-wide fixed symbols this chapter needs, from the lexicon."""
    p = ROOT / "schedule" / "lexicon.json"
    if not p.exists():
        return {}
    lex = json.loads(p.read_text(encoding="utf-8")).get("tokens", {})
    return {k: lex[k] for k in day.get("symbols_needed", []) if k in lex}


# Props whose real-world job is to bear digits. Ranked, because the difference
# between a good and a bad carrier is most of the mnemonic: a percentage written
# on a chalked recipe board reads as a proportion, the same characters on a dye
# pit read as nothing at all.
_CARRIER_WORDS = [
    (10, ("recipe board", "price board", "menu board", "scoreboard", "tally",
          "shade card", "chart", "slate", "notice board")),
    (8,  ("scale", "gauge", "dial", "thermometer", "meter", "weighbridge")),
    (7,  ("ticket", "docket", "label", "tag", "card", "stub", "receipt")),
    (6,  ("plate", "sign", "nameplate", "fascia", "placard", "board")),
    (5,  ("door", "van", "lorry", "truck", "cab", "locker", "crate", "sack")),
    (3,  ("calendar", "clock", "marker", "post", "banner", "jersey")),
]


def _carrier_score(prop: str) -> int:
    p = prop.lower()
    return max((w for w, keys in _CARRIER_WORDS if any(k in p for k in keys)),
               default=0)


def _short_carrier(prop: str) -> str:
    """Trim a stock-prop line to a short noun phrase naming the object.

    venues.json entries read like "recipe board chalked in percent on weight of
    goods" — a whole clause. Handed to the director as a carrier, it copied the
    clause verbatim into `cue`, which then failed the six-word limit on every
    numeric element. Cut at the first participle or preposition; the descriptive
    tail is scene-setting, not the object's name.
    """
    head = re.split(r"\s+(?:chalked|hung|stacked|slung|set|painted|bolted|"
                    r"printed|with|on|in|at|of|across|over|by|for|down|up)\b",
                    prop.strip(), maxsplit=1)[0]
    words = head.split()
    return " ".join(words[:4]) if words else prop.strip()[:40]


def _assign_carriers(plate: spec.Plate, venue: dict) -> None:
    """Give each painted number a carrier drawn from the venue's own inventory.

    The carrier must be something the place already contains for a story
    reason — a numbered prop imported only to hold a digit reads as a label —
    and its everyday job should be to bear digits. Best-scoring props go to the
    highest-priority numbers, and no two numbers share a carrier.
    """
    props = [p.split("(")[0].strip() for p in venue.get("stock_props", [])]
    ranked = sorted(props, key=_carrier_score, reverse=True)
    ranked = [_short_carrier(p) for p in ranked]
    nums = sorted((x for f in plate.facts for x in f.numbers if x.drawn),
                  key=lambda n: -n.priority)
    for i, n in enumerate(nums):
        n.carrier = ranked[i] if i < len(ranked) else "a hand-lettered sign panel"


def run_plate(day: dict, plate: spec.Plate, venue: dict, tokens: dict,
              out_dir: Path, dry_run: bool = False,
              numbers_mode: str = "composite") -> dict:
    topic = f"{day.get('title_en', '')} — {day.get('title_he', '')}"
    spec.triage_numbers(plate)
    _assign_carriers(plate, venue)

    director = spec.build_plate_director(plate, venue, topic, tokens)
    painted = [n for f in plate.facts for n in f.numbers if n.drawn]
    report = {"day": day.get("index"), "plate": plate.name,
              "facts": len(plate.facts), "tries": 0,
              "numbers_mode": numbers_mode}

    if dry_run:
        print(director)
        print("\n" + "=" * 70)
        print(f"[{len(director)} chars of director prompt · {len(plate.facts)} facts · "
              f"{sum(1 for f in plate.facts for n in f.numbers if n.drawn)} painted numbers]")
        return report

    client = gi._get_client(None)
    if client is None:
        raise SystemExit("no GEMINI_API_KEY")

    # ── director -> element table, re-asked on schema failure ──────────────
    table, errs = None, []
    for attempt in range(3):
        raw = gi.run_json_director(director if attempt == 0 else
                                   director + "\n\nYOUR LAST ANSWER WAS REJECTED:\n"
                                   + "\n".join(f"- {e}" for e in errs[:12]))
        cand = raw if isinstance(raw, dict) and raw.get("elements") else None
        if cand is None:
            errs = ["output had no `elements` array — return the element table "
                    "JSON exactly as specified, with no prose around it"]
            print(f"[i] element table attempt {attempt+1}/3: "
                  f"director returned keys {sorted(raw)[:8] if raw else '(nothing)'}",
                  file=sys.stderr)
            continue
        errs = spec.validate_table(cand, plate.facts)
        if not errs:
            table = cand
            break
        print(f"[i] element table attempt {attempt+1}/3: {len(errs)} error(s); "
              f"first: {errs[0]}", file=sys.stderr)
    if table is None:
        report["error"] = f"director never produced a valid table: {errs[:3]}"
        return report

    # ── render with the degradation ladder ─────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"sketchy_{plate.name}.png"
    required = [(n.paint, n.carrier)
                for f in plate.facts for n in f.numbers if n.drawn]
    strings = [e["text"] for e in table["elements"] if e.get("text")]
    best = None

    for attempt in range(MAX_TRIES):
        report["tries"] = attempt + 1
        prompt = spec.build_image_prompt(table, plate, gi.style_prefix(),
                                        chroma_plates=(numbers_mode == "composite"))
        if attempt >= 1 and best:
            if best["missing_all"]:
                prompt += ("\n\nTHESE WERE MISSING LAST TIME — letter them clearly:\n"
                           + "\n".join(f'  "{s}"' for s in best["missing_all"]))
            # A plate that was drawn but drawn tiny needs the opposite of "draw
            # it": it needs "draw the one you already drew, four times bigger".
            small = [r for r in best.get("plate_notes", [])
                     if r.get("reject") == "too_small_in_frame"]
            absent = [r for r in best.get("plate_notes", [])
                      if r.get("reject") != "too_small_in_frame"]
            if small:
                prompt += (
                    "\n\nLAST TIME THESE PLATES WERE FAR TOO SMALL. You drew "
                    + ", ".join("the {} plate at {:.0f}% of the image "
                                "height".format(r["name"], r["height_frac"] * 100)
                                for r in small)
                    + ". Each must be about 20% of the image height — four to "
                      "eight times bigger than you drew it: a doorway-sized "
                      "board, not a label. Move other things aside to make room.")
            if absent:
                prompt += ("\n\nTHESE BLANK PLATES WERE NOT USABLE LAST TIME — draw "
                           "each one as a large, flat, unobstructed rectangle of "
                           "exactly the named colour:\n"
                           + "\n".join(f'  - the {r["name"]} plate' for r in absent))
        if attempt == 2:
            # fewer constraints beats four rolls of the same overloaded prompt
            drop = strings[len(strings) // 2:]
            strings = strings[:len(strings) // 2]
            for e in table["elements"]:
                if e.get("text") in drop:
                    e["text"] = ""
            print(f"[i] dropping {len(drop)} lower-priority string(s) and retrying",
                  file=sys.stderr)

        if not gi.generate_image(prompt, png):
            continue

        # In composite mode the model was asked for BLANK plates, so there are
        # no digits yet — probing for them would report every number missing and
        # burn the whole retry budget. What matters at this stage is that the
        # blank plates exist and are findable, which the compositor reports
        # after the loop. In model mode the digits are the render's job, so the
        # gate is the only thing standing between a wrong number and the learner.
        if numbers_mode == "composite":
            rows = comp_mod.measure_chroma_plates(png, len(painted))
            found = sum(1 for r in rows if r["ok"])
            plate_notes = [r for r in rows if not r["ok"]]
            nums = {"probed": True, "missing": [], "wrong": [], "extra": [],
                    "blank_plates_found": found, "blank_plates_wanted": len(painted),
                    "blank_plates": rows}
            missing_nums = [n.paint for n in painted[found:]]
        else:
            plate_notes = []
            nums = gi.check_numbers(client, png, required)
            missing_nums = nums["missing"] + [w for w, _ in nums["wrong"]]
        probed_txt, miss_txt = gi.check_text_strings(client, png, strings)
        comp = gi.check_composition(client, png)
        missing_all = missing_nums + miss_txt
        # Strays (nums["extra"]) are NOT counted in the rejection score.
        # Evidence from the first full run: stray count went 15 -> 0 -> 1 -> 30
        # across four tries. The instruction works sometimes and not others, so
        # treating strays as a hard reject means the build never converges. They
        # are logged as a warning, reported, and annotated in the legend, but
        # they do not drive retries. (See defect #2 in SKETCHY_V2_STATUS.md.)
        # comp now contains `_raw` with the full probe answers for diagnosis — exclude it
        comp_fails = sum(1 for k, v in comp.items() if k != "_raw" and not v)
        score = len(missing_all) + comp_fails

        snap = {"numbers": nums, "text_missing": miss_txt, "composition": comp,
                "missing_all": missing_all, "score": score,
                "plate_notes": plate_notes,
                "stray_warning": nums["extra"],  # logged, not a rejection
                "bytes": png.read_bytes()}
        if best is None or score < best["score"]:
            best = snap
        stray_note = f", {len(nums['extra'])} stray (warning)" if nums["extra"] else ""
        print(f"[i] try {attempt+1}: {len(missing_all)} missing{stray_note}, "
              f"composition {comp}", file=sys.stderr)
        if score == 0:
            break

    if best:
        png.write_bytes(best["bytes"])
        report["gates"] = {k: v for k, v in best.items()
                           if k not in ("bytes", "plate_notes")}
        # anything the render never carried is still taught, just flagged
        for f in plate.facts:
            for n in f.numbers:
                if n.drawn and n.paint in best["missing_all"]:
                    n.drawn = False

    # ── write the numbers onto their blank plates ──────────────────────────
    # Only in `composite` mode. In `model` mode no blank plates were requested,
    # so there is nothing to find. Skipping this step while still ASKING for the
    # plates is what shipped raw magenta and green rectangles in the first
    # chapter-28 render.
    if numbers_mode == "composite":
        slots = [{"id": n.tag or n.paint, "chroma": rgb,
                  "paint": n.paint, "tag": n.tag}
                 for (_, rgb), n in zip(spec.CHROMA_KEYS, painted)]
        if slots:
            report["compositing"] = comp_mod.composite_plate(png, slots)

    # ── legend, quiz, narration ────────────────────────────────────────────
    undrawn = [f"{n.op}{n.paint}".strip()
               for f in plate.facts for n in f.numbers if not n.drawn]
    legend = spec.render_legend(table, plate, topic, undrawn=undrawn)
    (out_dir / f"sketchy_{plate.name}.txt").write_text(legend, encoding="utf-8")
    (out_dir / f"sketchy_{plate.name}.quiz.md").write_text(
        spec.render_quiz(legend), encoding="utf-8")

    brief = narr.build_narration_brief(table, plate, topic)
    figures = list(dict.fromkeys(f"{n.op}{n.paint}".strip()
                                 for f in plate.facts for n in f.numbers))
    script = narr.write_narration(client, brief, figures)
    if script:
        (out_dir / f"narration_{plate.name}.txt").write_text(script, encoding="utf-8")
        narr.speak(client, script, out_dir / f"narration_{plate.name}.wav")
        report["narration_words"] = len(script.split())

    (out_dir / f"report_{plate.name}.json").write_text(
        json.dumps({k: v for k, v in report.items()}, ensure_ascii=False,
                   indent=1, default=str), encoding="utf-8")
    return report


def _kill_stale_pipelines() -> None:
    """Kill any existing plate_pipeline processes before starting a new run.

    Defect #5 in SKETCHY_V2_STATUS.md: two pipeline runs overlapped, pgrep
    showed 7 processes at one point, and the main plate report contradicts
    the artefacts on disk. The fix: relaunch must pkill and confirm zero
    processes before starting.
    """
    import os
    import subprocess
    my_pid = os.getpid()

    # find all plate_pipeline.py processes except this one
    try:
        result = subprocess.run(
            ["pgrep", "-f", "plate_pipeline.py"],
            capture_output=True, text=True, timeout=5
        )
        pids = [int(p) for p in result.stdout.strip().split() if p]
        pids = [p for p in pids if p != my_pid]
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pids = []

    if pids:
        print(f"[!] found {len(pids)} stale plate_pipeline process(es): {pids}",
              file=sys.stderr)
        for pid in pids:
            try:
                os.kill(pid, 9)  # SIGKILL
                print(f"[i] killed PID {pid}", file=sys.stderr)
            except OSError:
                pass  # already dead

        # confirm zero remain (wait a moment for process table to update)
        import time
        time.sleep(0.5)
        try:
            result = subprocess.run(
                ["pgrep", "-f", "plate_pipeline.py"],
                capture_output=True, text=True, timeout=5
            )
            remaining = [int(p) for p in result.stdout.strip().split() if p]
            remaining = [p for p in remaining if p != my_pid]
            if remaining:
                raise SystemExit(f"[!] could not kill stale processes: {remaining}")
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass


def main() -> int:
    # guard against overlapping runs (defect #5)
    _kill_stale_pipelines()

    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--facts", default="_view/facts.json")
    ap.add_argument("--plate", choices=["main", "criteria", "all"], default="all")
    ap.add_argument("--out", default="_proto/plate")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--numbers", choices=["composite", "model"],
                    default="composite",
                    help="composite: model draws blank chroma plates and code "
                         "letters the exact digits onto them (correct by "
                         "construction). model: the illustrator writes them "
                         "itself (more natural, sometimes wrong).")
    a = ap.parse_args()

    sched = json.loads((ROOT / "schedule/schedule.json").read_text(encoding="utf-8"))
    day = next(d for d in sched["days"] if d["index"] == a.day)
    facts = spec.atomize(json.loads(Path(a.facts).read_text(encoding="utf-8")))
    venue = load_venue(a.day)
    tokens = load_tokens(day)

    print(f"chapter {a.day}: {day['title_he']}")
    print(f"  venue  : \"{venue['sign_text']}\" — {venue.get('place_category', '')}")
    print(f"  tokens : {', '.join(tokens) or '(none)'}")

    for plate in spec.plan_plates(facts):
        if a.plate != "all" and plate.track != a.plate:
            continue
        print(f"\n=== plate {plate.name} ({len(plate.facts)} facts) ===")
        r = run_plate(day, plate, venue, tokens, Path(a.out), a.dry_run,
                      numbers_mode=a.numbers)
        if not a.dry_run:
            print(json.dumps(r, ensure_ascii=False, indent=1, default=str)[:900])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
