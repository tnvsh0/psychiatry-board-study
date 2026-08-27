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

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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


# Big enough that an unverified render never beats a verified one and never
# reads as a clean score, small enough to stay legible in a report.
UNVERIFIED_PENALTY = 100


def run_plate(day: dict, plate: spec.Plate, venue: dict, tokens: dict,
              out_dir: Path, dry_run: bool = False,
              numbers_mode: str = "composite") -> dict:
    topic = f"{day.get('title_en', '')} — {day.get('title_he', '')}"
    spec.triage_numbers(plate)
    _assign_carriers(plate, venue)

    director = spec.build_plate_director(
        plate, venue, topic, tokens,
        composited_numbers=(numbers_mode == "composite"))
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

    out_dir.mkdir(parents=True, exist_ok=True)
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
        # Fix mechanically first, then judge. Otherwise the ladder spends its
        # whole budget re-asking for string lengths.
        # last rung: rewrite what the director would not stop writing
        # One honest chance at the hook rule, then fix it: at 10 of 34
        # elements the director is not going to be argued out of it.
        fixed = spec.normalize_table(cand, aggressive=(attempt >= 1),
                                     known_facts={f.id for f in plate.facts})
        soft: list[str] = []
        errs = spec.validate_table(cand, plate.facts, soft=soft,
                                   last_rung=(attempt == 2))
        if not errs:
            table = cand
            for note in fixed:
                print(f"[i] normalized {note}", file=sys.stderr)
            for note in soft:
                print(f"[i] accepted with warning: {note}", file=sys.stderr)
            report["table_warnings"] = fixed + soft
            break
        # print them ALL. Printing only errs[0] cost a whole render cycle:
        # the first error looked like one the normalizer already handles, so
        # the real blockers behind it were invisible.
        print(f"[i] element table attempt {attempt+1}/3: {len(errs)} error(s):",
              file=sys.stderr)
        for e in errs[:20]:
            print(f"      - {e}", file=sys.stderr)
        # Keep the rejected candidate. Each blocker costs a full render cycle
        # to discover from a log line, and the log only ever shows the errors,
        # never the answer that produced them. With the candidate on disk every
        # remaining rejection class can be found in one pass, offline.
        try:
            (out_dir / f"_rejected_{plate.name}_{attempt + 1}.json").write_text(
                json.dumps({"errors": errs, "soft": soft, "normalized": fixed,
                            "table": cand}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except Exception:
            pass  # diagnostics must never take the run down
    if table is None:
        report["error"] = f"director never produced a valid table: {errs[:3]}"
        return report

    # In composite mode the numerals belong to the compositor, so a required
    # string that carries digits is a number about to be painted TWICE — once
    # by hand where the director filed it, once on its blank plate. Drop it.
    if numbers_mode == "composite":
        for e in table["elements"]:
            txt = (e.get("text") or "").strip()
            if txt and any(c.isdigit() for c in txt):
                print(f"[i] dropped digit-bearing string {txt!r} — the "
                      f"compositor owns the numerals", file=sys.stderr)
                report.setdefault("digit_strings_dropped", []).append(txt)
                e["text"] = ""

    # ── render with the degradation ladder ─────────────────────────────────
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
            # In composite mode the NUMBERS are not the illustrator's job — the
            # compositor warps them onto the blank chroma plates afterwards.
            # Asking for them back by value ("letter '70%' clearly") is asking
            # for a hand-painted duplicate of a number that is about to be
            # composited, which is the stray-numeral defect this mode exists to
            # remove. A number whose plate went missing is chased below, as a
            # PLATE. Only text strings are lettered here.
            relettering = (best["text_missing"] if numbers_mode == "composite"
                           else best["missing_all"])
            if relettering:
                prompt += ("\n\nTHESE WERE MISSING LAST TIME — letter them clearly:\n"
                           + "\n".join(f'  "{s}"' for s in relettering))
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
            # The composition probe already says exactly what went wrong and
            # names the offender; saying it back is far more use than repeating
            # the general rule the model has already read once and ignored.
            comp_raw = (best.get("composition") or {}).get("_raw") or {}
            comp_bad = {k: v for k, v in comp_raw.items()
                        if not str(v).strip().upper().startswith("YES")}
            if comp_bad:
                said = "\n".join(f"  - {v}" for v in comp_bad.values())
                prompt += (
                    "\n\nWHAT WAS WRONG WITH THE LAST PICTURE, as reported by "
                    "someone who looked at it:\n" + said +
                    "\nFix exactly that. If a figure was called out as larger, "
                    "draw that same person at the size of everyone else, "
                    "standing further back, not near the viewer and not "
                    "reaching toward it.")
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

        model_used = gi.model_for_try(attempt, MAX_TRIES)
        if not gi.generate_image(prompt, png, model=model_used):
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
            # Which plates failed, not how many. This was `painted[found:]`,
            # using a COUNT as an OFFSET: with plates 1 and 3 drawn and 2 lost,
            # found==2 named plate 3 as missing and let plate 2 pass. The retry
            # then argued for the wrong number and the legend flagged the wrong
            # fact as undrawn.
            missing_nums = [n.paint for i, n in enumerate(painted)
                            if i >= len(rows) or not rows[i]["ok"]]
            # check_numbers does not run in this mode, so this is the only
            # thing watching for numerals the illustrator painted by hand.
            # They are duplicates of numbers the compositor is about to place,
            # and the reader has no way to know which one is authoritative.
            stray_probed, nums["extra"] = gi.check_stray_numerals(client, png)
        else:
            plate_notes = []
            nums = gi.check_numbers(client, png, required)
            missing_nums = nums["missing"] + [w for w, _ in nums["wrong"]]
            stray_probed = nums.get("probed", True)
        probed_txt, miss_txt = gi.check_text_strings(client, png, strings)
        comp = gi.check_composition(client, png)
        missing_all = missing_nums + miss_txt
        # Strays (nums["extra"]) are NOT counted in the rejection score.
        # Evidence from the first full run: stray count went 15 -> 0 -> 1 -> 30
        # across four tries. The instruction works sometimes and not others, so
        # treating strays as a hard reject means the build never converges. They
        # are logged as a warning, reported, and annotated in the legend, but
        # they do not drive retries. (See defect #2 in SKETCHY_V2_STATUS.md.)
        # comp carries `_raw`, `_probed` and `_asked` for diagnosis — not gates
        comp_fails = sum(1 for k, v in comp.items()
                         if not k.startswith("_") and not v)
        # A probe that could not RUN is not a probe that passed. Every gate here
        # reports failure as an empty result: check_text_strings returns no
        # missing strings, check_composition returns no keys. So a vision-API
        # outage scored 0 on try 1 and shipped immediately, wearing the same
        # "score 0" a genuinely clean render wears. The penalty keeps an
        # unverified frame from short-circuiting the retry loop and from
        # out-ranking a verified one that merely had a flaw.
        verified = (probed_txt and stray_probed
                    and comp.get("_probed", 0) == comp.get("_asked", 0))
        # Strays DO count on the draft rungs, and only there. The owner's
        # objection is exact: chapter 6 carries a "DA" label three times over
        # and a loose "PFC", which is noise the reader has to filter past. But
        # the old evidence still holds — stray counts went 15 -> 0 -> 1 -> 30
        # across four tries, so making them a hard reject everywhere means the
        # build never converges and the last rung ships whatever it has.
        #
        # The resolution is the owner's: reject on strays while drafts are
        # cheap, never on the expensive final. A draft that is clean except for
        # stray labels is worth one more cheap roll; the Pro frame is not worth
        # re-rolling for them, and by then the prompt has already been through
        # the ladder's corrections.
        drafting = bool(gi.LADDER_MODEL) and attempt < MAX_TRIES - 1
        stray_penalty = len(nums["extra"]) if drafting else 0
        score = (len(missing_all) + comp_fails + stray_penalty
                 + (0 if verified else UNVERIFIED_PENALTY))

        snap = {"numbers": nums, "text_missing": miss_txt, "composition": comp,
                "missing_all": missing_all, "score": score,
                "verified": verified,
                "plate_notes": plate_notes,
                "stray_warning": nums["extra"],  # logged, not a rejection
                "model": model_used,
                "prompt": prompt,
                "bytes": png.read_bytes()}
        if best is None or score < best["score"]:
            best = snap
        stray_note = (f", {len(nums['extra'])} stray"
                      + (" (counted)" if drafting and nums["extra"] else " (warning)")
                      if nums["extra"] else "")
        print(f"[i] try {attempt+1}: {len(missing_all)} missing{stray_note}, "
              f"composition {comp}", file=sys.stderr)
        if score == 0:
            break
        # Nothing measurable is wrong; the only complaint is that the probes
        # could not confirm it. Another render cannot fix a probe outage, it
        # just costs another 2K image - and rung 3 of the ladder DROPS required
        # strings, so retrying here actively makes the plate worse. Keep this
        # one, stop, and let the report carry verified=false.
        if not verified and not missing_all and not comp_fails:
            print("[i] gates unavailable and nothing found wrong — keeping this "
                  "render, marked unverified", file=sys.stderr)
            break

    # The ladder is allowed to use the cheap model to FIND the composition, but
    # the frame the learner sees must be drawn by the quality model. Without
    # this, a cheap draft that happens to score 0 on try 1 breaks the loop and
    # ships: chapter 5 went out as a two-try Flash render while chapter 6, which
    # needed all four tries, reached Pro and was visibly better. The owner spotted
    # the difference without being told which was which.
    # The agy engine has ONE model — its ladder rungs and its final frame come
    # off the same tool, so re-rendering the winner buys nothing and spends an
    # image out of a quota that only refills every five hours.
    if best and gi.ENGINE != "agy" and gi.LADDER_MODEL \
            and best.get("model") != gi.IMAGE_MODEL:
        print(f"[i] final pass on {gi.IMAGE_MODEL} (ladder settled on "
              f"{best['model']})", file=sys.stderr)
        final = png.with_suffix(".final.png")
        if gi.generate_image(best["prompt"], final, model=gi.IMAGE_MODEL):
            best = {**best, "bytes": final.read_bytes(), "model": gi.IMAGE_MODEL,
                    "final_pass": True}
            final.unlink(missing_ok=True)
        else:
            print("[i] final pass failed — keeping the ladder's render",
                  file=sys.stderr)

    if best:
        png.write_bytes(best["bytes"])
        report["model"] = best.get("model")
        report["final_pass"] = bool(best.get("final_pass"))
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
    undrawn = [spec.figure_text(n)
               for f in plate.facts for n in f.numbers if not n.drawn]
    legend = spec.render_legend(table, plate, topic, undrawn=undrawn, venue_why=venue.get('pun_explanation_he') or venue.get('pun_explanation'))
    (out_dir / f"sketchy_{plate.name}.txt").write_text(legend, encoding="utf-8")
    (out_dir / f"sketchy_{plate.name}.quiz.md").write_text(
        spec.render_quiz(legend), encoding="utf-8")

    brief = narr.build_narration_brief(table, plate, topic)
    figures = list(dict.fromkeys(spec.figure_text(n)
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
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
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
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
    ap.add_argument("--plate", choices=["criteria", "profile", "management",
                                        "main", "all"], default="all")
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
