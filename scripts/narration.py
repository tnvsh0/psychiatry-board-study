#!/usr/bin/env python3
"""
Spoken narration for a Sketchy plate — the missing video track.

Real SketchyMedical is not a picture and a legend; it is a picture, a legend,
and someone walking you through the picture out loud. Our learners have only
the first two, which is why SKETCHY_SPEC_V2.md had to invent the per-row `hook`
field: without a narrator, nothing explains WHY a prop encodes its fact.

This restores the third channel. It is deliberately NOT the legend read aloud:
the legend is a random-access index, flat and unordered on purpose, and reading
an index aloud is unlistenable. The narration is continuous speech that walks
the place — venue first, then zone by zone — and hands over each fact where the
listener's eye would already be.

Two stages, because they fail differently:
    build_narration_brief()  code-owned. What must be said, in what order,
                             with every exact figure carried through verbatim.
    write_narration()        one text call that turns the brief into flowing
                             Hebrew, under hard constraints code then checks.
    speak()                  TTS to a .wav beside the plate.

Usage:
    python narration.py --plate main --facts _view/facts.json --dry-run
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gemini_image import _gc  # noqa: E402  shared transient-retry wrapper

import sketchy_spec as spec  # noqa: E402

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TEXT_MODEL = "gemini-2.5-flash"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"          # calm, even pace; suits a study walkthrough
TTS_RATE = 24000            # the API returns 24 kHz 16-bit mono PCM


# ── stage 1: the brief (code-owned) ────────────────────────────────────────
def build_narration_brief(table: dict, plate, topic: str) -> str:
    """What the narrator must say, in order, with the figures already filled in.

    Ordering is by ZONE, not by the legend's syllabus order. The legend is
    ordered for revision; speech has to be ordered the way a person's eye
    actually crosses the picture, or the listener is thrown around the frame.
    """
    v = table["venue"]
    zones = table.get("zones") or []
    by_id = {f.id: f for f in plate.facts}

    # group elements by zone, zones in declared order, unzoned last
    order = {z: i for i, z in enumerate(zones)}
    els = sorted(table["elements"], key=lambda e: order.get(e.get("zone", ""), 99))

    lines, seen_zone = [], None
    for e in els:
        z = e.get("zone") or ""
        if z != seen_zone:
            seen_zone = z
            lines.append(f"\n  [{z or 'elsewhere'}]")
        for fid in e.get("fact_ids", []):
            f = by_id.get(fid)
            if not f:
                continue
            hook = (e.get("hook") or "").strip()
            painted = f'  painted "{e["text"]}"' if e.get("text") else ""
            lines.append(f"    - {e['cue']}{painted} :: {f.text}"
                         + (f"  [why: {hook}]" if hook else ""))

    figures = [f"{n.op}{n.paint}".strip()
               for f in plate.facts for n in f.numbers]
    figures = list(dict.fromkeys(figures))

    return f"""TOPIC: {topic}

THE PLACE: {v['place']}
THE SIGN OVER THE DOOR: "{v['sign_text']}"
WHAT EVERYONE IS DOING: {v['activity']}
THE LARGEST THING IN THE PICTURE: {table['anchor_structure']}
ZONES, in the order the eye crosses them: {' -> '.join(zones)}

WHAT IS IN THE PICTURE, zone by zone:{chr(10).join(lines)}

EVERY FIGURE THAT MUST BE SPOKEN ALOUD, verbatim:
  {' · '.join(figures) if figures else '(none)'}
"""


NARRATION_INSTRUCTION = """You are the narrator of a SketchyMedical-style study
plate, speaking Hebrew to a psychiatry resident preparing for the Israeli board
exam (שלב ב).

Write the SPOKEN script. It will be read aloud, so write for the ear.

STRUCTURE — follow it exactly:
1. Open by naming the place and the sign over its door, and say in one sentence
   why that name is the topic's name. This is the retrieval hook: on exam day
   the chapter title is the only cue, and it has to bring back this place.
2. Say in one sentence what everyone in the picture is doing, and name the one
   big structure that dominates it.
3. Then walk the zones in the order given. In each zone, hand over its items:
   name the prop as a person would point at it, state the clinical fact, and
   give the reason the prop carries that fact.
4. Close in two sentences: the place, and the two or three figures most worth
   carrying into the exam.

HOW TO SPEAK:
- Continuous flowing speech. Never "item one, item two". No numbered list, no
  bullets, no headings — this is not the legend read aloud.
- Move with the eye: "ומשם, במורד הרמפה…", "מאחורי הדלפק…". Connect one item to
  the next; do not simply concatenate sentences.
- Hebrew throughout, but keep clinical terms and drug names in English exactly
  as given (SSRI, D2, EPS, Bipolar, ODD, MDD).
- Say every figure from the list ALOUD and EXACTLY as written. A figure spoken
  wrong is worse than a figure not spoken.
- Warm, matter-of-fact, unhurried. No jokes about the pathology, no drama.
- 400-700 words. Long enough to walk the whole place, short enough to hold.

NEVER SAY: "בתמונה", "בסצנה", "באיור", "אנו רואים", "מסמל", "מייצג", "בצד ימין
של התמונה". The listener is looking at the picture — describing it back to them
wastes the only channel that can explain WHY. Point at things ("הקרס הכבד
ההוא"), then explain them.

Return the script text only. No preamble, no title, no stage directions."""


# ── stage 2: write it ──────────────────────────────────────────────────────
def _text_call(client, prompt: str, model: str = TEXT_MODEL) -> str:
    try:
        resp = _gc(client, model=model, contents=prompt)
        return "".join(
            p.text
            for c in getattr(resp, "candidates", []) or []
            for p in getattr(c.content, "parts", []) or []
            if getattr(p, "text", None)
        ).strip()
    except Exception as e:  # noqa: BLE001
        print(f"[!] narration text call failed: {e}", file=sys.stderr)
        return ""


_BANNED = re.compile(r"בתמונה|בסצנה|באיור|אנו רואים|אנחנו רואים|מסמל|מייצג|ברקע")

# Replacements for banned phrases. The model refuses to stop using these despite
# explicit instructions and re-ask loops (defect #4 in SKETCHY_V2_STATUS.md: all
# three attempts tripped the ban-list). So we scrub them post-hoc with neutral
# alternatives that preserve sentence flow.
_SCRUB_PATTERNS = [
    # "we see X" -> just "X" (the colon or prop name already introduces it)
    (re.compile(r"אנו רואים\s+"), ""),
    (re.compile(r"אנחנו רואים\s+"), ""),
    # "X symbolizes Y" -> "X — Y" (the hook format we want).
    # INFLECTIONS MATTER. The ban matches the bare stem "מסמל", which also
    # matches inside "מסמלת" and "מסמלים"; the scrub required whitespace right
    # after the stem, so every feminine and plural form slipped past the scrub
    # and then tripped the ban. The narration could not converge on a script
    # that the scrub was, by construction, unable to fix.
    (re.compile(r"\s*(?:מסמלות|מסמלים|מייצגות|מייצגים|מסמלת|מייצגת|מסמל|מייצג)"
                r"\s+(?:את\s+)?"), " — "),
    # "in the picture/scene" -> just remove (adds nothing)
    (re.compile(r"\s*בתמונה\s*"), " "),
    (re.compile(r"\s*בסצנה\s*"), " "),
    (re.compile(r"\s*באיור\s*"), " "),
    (re.compile(r"\s*ברקע\s*"), " "),
]


def scrub_banned_phrases(script: str) -> str:
    """Remove picture-description phrases that the model cannot stop using.

    The instruction says "never say מסמל / אנחנו רואים" but the model ignores
    it even after three retry loops with the failures appended. Stripping in
    code post-hoc is cheaper and 100% reliable.
    """
    for pattern, repl in _SCRUB_PATTERNS:
        script = pattern.sub(repl, script)
    # clean up double spaces left behind
    script = re.sub(r" {2,}", " ", script)
    script = re.sub(r"\s+([,.\n])", r"\1", script)
    return script.strip()


def check_narration(script: str, figures: list[str]) -> list[str]:
    """Hard problems worth a retry. Empty list == good enough to ship.

    Note: banned phrase check is now AFTER scrubbing, so it only triggers if
    scrub_banned_phrases somehow missed something (shouldn't happen).
    """
    errs = []
    for m in set(_BANNED.findall(script or "")):
        errs.append(f'describes the picture instead of explaining it: "{m}"')
    for fig in figures:
        if fig and fig not in script:
            errs.append(f"figure not spoken: {fig}")
    words = len((script or "").split())
    if words < 250:
        errs.append(f"too short to walk the place: {words} words")
    if re.search(r"^\s*\d+[.)]\s", script or "", re.M):
        errs.append("reads as a numbered list, not continuous speech")
    return errs


def write_narration(client, brief: str, figures: list[str], tries: int = 3) -> str:
    """Generate the script, re-asking on hard failures. Returns the best attempt.

    The banned-phrase scrub runs on every attempt, so retries focus on real
    issues (missing figures, wrong length) rather than re-asking the model to
    stop saying "מסמל" — it won't, and code-stripping is 100% reliable.
    """
    prompt = f"{NARRATION_INSTRUCTION}\n\n---\n\n{brief}"
    best, best_errs = "", None
    for attempt in range(tries):
        script = _text_call(client, prompt)
        if not script:
            continue
        # scrub banned phrases BEFORE checking — eliminates the endless re-ask loop
        script = scrub_banned_phrases(script)
        errs = check_narration(script, figures)
        if best_errs is None or len(errs) < len(best_errs):
            best, best_errs = script, errs
        if not errs:
            return script
        print(f"[i] narration attempt {attempt+1}/{tries}: {len(errs)} issue(s); "
              f"{errs[0]}", file=sys.stderr)
        # re-ask only covers non-phrase issues now (figures, length, list format)
        prompt = (f"{NARRATION_INSTRUCTION}\n\n---\n\n{brief}\n\n"
                  f"YOUR PREVIOUS ATTEMPT HAD THESE PROBLEMS — fix every one:\n"
                  + "\n".join(f"- {e}" for e in errs))
    return best


# ── stage 3: speak it ──────────────────────────────────────────────────────
def _write_wav(pcm: bytes, out: Path, rate: int = TTS_RATE) -> None:
    """Wrap raw 16-bit mono PCM in a WAV container."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def speak(client, script: str, out_path: str | Path,
          voice: str = TTS_VOICE) -> bool:
    """Synthesise the script to a .wav. Returns True on success."""
    try:
        from google.genai import types
    except ImportError:
        print("[!] google-genai not installed", file=sys.stderr)
        return False
    if not script.strip():
        return False
    cfg = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
    )
    try:
        resp = _gc(client, 
            model=TTS_MODEL, contents=script, config=cfg
        )
        for c in getattr(resp, "candidates", []) or []:
            for p in getattr(c.content, "parts", []) or []:
                inline = getattr(p, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    _write_wav(inline.data, Path(out_path))
                    return True
        print("[!] TTS returned no audio", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[!] TTS failed: {e}", file=sys.stderr)
    return False


# ── self-test ──────────────────────────────────────────────────────────────
def _test_scrub_banned_phrases() -> None:
    """Verify that banned phrases are stripped post-hoc."""
    cases = [
        # "we see X" -> just "X"
        ("אנחנו רואים את הקרס הכבד", "את הקרס הכבד"),
        ("אנו רואים ילד", "ילד"),
        # "X symbolizes Y" -> "X — Y"
        ("הכלב מסמל נאמנות", "הכלב — נאמנות"),
        ("העץ מייצג צמיחה", "העץ — צמיחה"),
        # "in the picture" -> removed
        ("בתמונה יש פרחים", "יש פרחים"),
        ("נמצא בסצנה", "נמצא"),
        ("באיור מופיע", "מופיע"),
        ("ברקע נראים הרים", "נראים הרים"),
    ]
    for inp, want in cases:
        got = scrub_banned_phrases(inp)
        assert got == want, f"scrub_banned_phrases({inp!r}) = {got!r}, want {want!r}"
    print("  [OK] scrub_banned_phrases removes all banned patterns")

    # combined test: a sentence with multiple banned phrases
    nasty = "אנחנו רואים בתמונה איש שמסמל תקווה"
    got = scrub_banned_phrases(nasty)
    assert "אנחנו רואים" not in got, f"missed 'אנחנו רואים' in {got!r}"
    assert "בתמונה" not in got, f"missed 'בתמונה' in {got!r}"
    assert "מסמל" not in got, f"missed 'מסמל' in {got!r}"
    assert "תקווה" in got, f"lost the payload 'תקווה' in {got!r}"
    print("  [OK] scrub_banned_phrases handles compound bans")

    # every inflection the ban can see, the scrub must be able to remove. The
    # ban matches the bare stem, so a form the scrub misses is a script that
    # can never pass, no matter how many times it is re-asked.
    bad = [t for t in ("הצנצנות מסמלות את החסר", "העובדת מסמלת את המנגנון",
                       "הקופסה מייצגת את האזהרה", "הדוור מסמל את המנגנון",
                       "הסלים מסמלים חסר", "הם מייצגים את הרשת")
           if _BANNED.search(scrub_banned_phrases(t))]
    assert not bad, f"scrub left a banned stem in: {bad}"
    print("  [OK] scrub_banned_phrases catches every inflection the ban sees")


def _demo_table(plate) -> dict:
    """A stand-in element table so the brief can be exercised without a director."""
    zones = ["הרמפה", "רצפת הבור", "השפה"]
    els = []
    for i, f in enumerate(plate.facts[:14]):
        els.append({
            "id": f"e{i}", "fact_ids": [f.id],
            "cue": f"prop {i}", "prop": f"a prop for {f.subkey}",
            "zone": zones[i % len(zones)],
            "hook": "why this prop carries this fact",
            "derivation": "LEXICON_TOKEN",
        })
    return {
        "venue": {"sign_text": "MAJOR PIT", "place": "מחצבה פתוחה",
                  "activity": "משמרת עבודה רגילה"},
        "anchor_structure": "מדרגות המחצבה החוצות את הפריים באלכסון",
        "zones": zones, "elements": els,
    }


def main() -> int:
    print("── unit tests ──")
    _test_scrub_banned_phrases()
    print()

    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", default="_view/facts.json")
    ap.add_argument("--plate", default="main", choices=["main", "criteria"])
    ap.add_argument("--topic", default="Major Depressive Disorder — MDD")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the brief; make no API calls")
    ap.add_argument("--out", default="_proto/narration")
    a = ap.parse_args()

    import json
    facts = spec.atomize(json.loads(Path(a.facts).read_text(encoding="utf-8")))
    plate = next((p for p in spec.plan_plates(facts) if p.track == a.plate), None)
    if plate is None:
        raise SystemExit(f"no {a.plate} plate for these facts")
    spec.triage_numbers(plate)

    table = _demo_table(plate)
    brief = build_narration_brief(table, plate, a.topic)
    figures = [f"{n.op}{n.paint}".strip() for f in plate.facts for n in f.numbers]
    figures = list(dict.fromkeys(figures))

    if a.dry_run:
        print(brief)
        print(f"\n[{len(brief)} chars of brief · {len(figures)} figures to speak]")
        return 0

    import gemini_image as gi
    client = gi._get_client(None)
    if client is None:
        return 2
    out = Path(a.out)
    script = write_narration(client, brief, figures)
    if not script:
        return 3
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.plate}.txt").write_text(script, encoding="utf-8")
    print(f"script: {len(script.split())} words -> {out / (a.plate + '.txt')}")
    left = check_narration(script, figures)
    print("issues:", left or "none")
    if speak(client, script, out / f"{a.plate}.wav"):
        kb = (out / f"{a.plate}.wav").stat().st_size / 1024
        print(f"audio : {kb:.0f} KB -> {out / (a.plate + '.wav')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
