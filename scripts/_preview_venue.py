#!/usr/bin/env python3
"""
Render a VENUE preview — the world only, with no medical content in it yet.

The 144 venues in _venues_partial.json are text specs. You cannot judge a
mnemonic world from prose, so this renders the place itself: the punned sign,
the inanimate diagonal anchor, the one shared ambient activity, ~18 same-scale
figures, and the stock-prop inventory, under the Sketchy Spec v2 composition
rules. No facts, no numbers, no legend — those come from the real pipeline.

The question a preview answers is narrow and useful: does this read as a
Sketchy page, does the sign land, and is the place mundane-but-rich enough to
hang thirty facts on later.

Usage (on the VM, where GEMINI_API_KEY lives):
    python3 _preview_venue.py --chapters 4,29,60,5 --size 2K
    python3 _preview_venue.py --sample            # a spread across the set
Outputs to _proto/venues/ch<NN>.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gemini_image as gi  # noqa: E402

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_proto" / "venues"

# A spread chosen to expose different things: a preserved course symbol, a
# corpus venue reproduced verbatim, a strong fresh pun, a flagged clash, a
# spatial-isomorphism foundation chapter, one of three colliding bowling
# alleys, a critic-rejected venue, and the hardest chapter type we have.
SAMPLE = [4, 29, 60, 5, 2, 37, 103, 143]

STYLE = """Flat digital cel illustration in the SketchyMedical manner: crisp
constant-width ink outlines over flat local-colour fills with soft airbrush
gradient shading. Outlines are dark desaturated versions of the colour they
enclose — warm brown around skin and wood, deep grey-blue around cloth — and
pure black only on genuinely black objects. The room shell (sky, back wall,
distant ground) carries no outline at all, only soft gradient, while every prop
in front of it is fully inked. Clean unmixed local colour; never combine low
value with low saturation.

Bright, deadpan, matter-of-fact register. The palette comes from the setting
itself, never from any mood or subject matter.

Aspect ratio 16:9 landscape. Every character has the correct number of hands,
fingers and limbs — no extra, duplicate, floating or disembodied hands anywhere.
"""


def build_prompt(v: dict) -> str:
    props = "\n".join(f"  - {p}" for p in v.get("stock_props", [])[:15])
    return f"""{STYLE}
THE PLACE
{v['place']}

THE ONE SHARED ACTIVITY — every single person in the frame is doing this, each
in their own way. Nobody interacts with anybody else; nothing causes anything
else; there is no story and no event unfolding.
{v['activity']}

THE ANCHOR STRUCTURE — this is the DOMINANT element of the picture and it is
INANIMATE. It cuts the frame on a diagonal and divides it into zones. It is the
largest thing in the image.
{v['anchor_structure']}

NO PROTAGONIST. No figure is larger, more central or more important than the
others. About 18 people at comparable scale, none taller than roughly
two-fifths of the frame height, with visible variety of age, build, skin tone
and clothing.

PACK THE FRAME edge to edge with the ordinary contents of this place, so it
reads as a real working location rather than a diagram. No vignette, no edge
darkening — the frame borders are as bright as the centre. No empty breathing
space.

THINGS THIS PLACE CONTAINS — draw as many as fit naturally:
{props}

TEXT IN THE PICTURE
The only text that must be legible is the place's own name sign, hand-lettered
and clearly readable, integrated into the world as a real sign would be:
"{v['sign_text']}"
Every other piece of paper, notice, board, poster and label in the scene is
illegible squiggle-lines. NO numerals appear anywhere in the picture.
"""


def render_one(v: dict, size: str) -> bool:
    from google.genai import types

    client = gi._get_client(None)
    if client is None:
        print("[!] no GEMINI_API_KEY", file=sys.stderr)
        return False

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"ch{v['index']:03d}.png"
    cfg = types.GenerateContentConfig(
        image_config=types.ImageConfig(aspect_ratio="16:9", image_size=size)
    )
    prompt = build_prompt(v)
    print(f"--- ch{v['index']:>3} \"{v['sign_text']}\" ({len(prompt)} chars) ---")
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=gi.IMAGE_MODEL, contents=prompt, config=cfg
            )
            if gi._save_image_from_response(resp, out):
                print(f"    [ok] {out.name}")
                return True
            print(f"    [!] attempt {attempt+1}: no image data", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"    [!] attempt {attempt+1}: {e}", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", help="comma-separated chapter indices")
    ap.add_argument("--sample", action="store_true", help=f"render {SAMPLE}")
    ap.add_argument("--size", default="2K", choices=["1K", "2K", "4K"])
    a = ap.parse_args()

    venues = {v["index"]: v for v in
              json.loads((ROOT / "_venues_partial.json").read_text(encoding="utf-8"))}

    if a.sample:
        want = SAMPLE
    elif a.chapters:
        want = [int(x) for x in a.chapters.split(",") if x.strip()]
    else:
        ap.print_help()
        return 1

    ok = 0
    for i in want:
        v = venues.get(i)
        if not v:
            print(f"[skip] chapter {i} has no venue")
            continue
        ok += render_one(v, a.size)
    print(f"\n{ok}/{len(want)} rendered -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
