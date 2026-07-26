#!/usr/bin/env python3
"""
PROTOTYPE — deterministic number compositing ("Path B" of SKETCHY_SPEC_V2.md §3).

The problem: the image model cannot be trusted to spell. Our own etiology render
produced the non-word "Sorceric", and a WRONG number burned into a study image
out-competes the correct legend at recall time.

The idea: never ask the model to write the number. Ask it for a BLANK sign
painted a flat, unmistakable chroma colour, on a carrier object whose real-world
job is to bear digits. Then code finds that colour, computes the sign's quad,
and perspective-warps the exact digit-string + its referent tag onto it.

Each number slot gets a DIFFERENT chroma colour, so detection is a pure colour
mask — no connected-component labelling, no OCR, no retries, and the digits are
correct by construction.

Usage (on the VM, where GEMINI_API_KEY lives):
    python3 _proto_number_plate.py --render          # stage 1: generate the plate
    python3 _proto_number_plate.py --composite       # stage 2: paint the numbers
    python3 _proto_number_plate.py --both
    python3 _proto_number_plate.py --both --control  # also render the naive
                                                     # "model writes the text"
                                                     # version for comparison
Outputs into ./_proto/ :
    raw.png        the render, with blank chroma plates
    final.png      after compositing
    control.png    the naive approach, for side-by-side judgement
    report.json    per-slot detection geometry and status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont  # noqa: E402

import gemini_image as gi  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "_proto"

# ── The number slots ───────────────────────────────────────────────────────
# Each slot: a distinct chroma key, the carrier object, the exact string to
# paint, and its referent TAG (spec §3 — "THE TAG"; p207 paints '52' with 'HT'
# beneath it on the same banner, and that tag is the only reason the numeral is
# retrievable at all).
SLOTS = [
    {
        "id": "recurrence",
        "chroma": (0, 177, 64),  # green
        "chroma_name": "PURE GREEN",
        "carrier": "the punched return ticket held up by the worker walking back down the ramp",
        "paint": "70%",
        "tag": "AGAIN",
        "plate_color": (250, 244, 226),  # warm cream once composited
        "ink": (40, 30, 25),
    },
    {
        "id": "heritability",
        "chroma": (255, 0, 255),  # magenta
        "chroma_name": "PURE MAGENTA",
        "carrier": "the door panel of the yellow haul truck parked on the ramp",
        "paint": "40-60%",
        "tag": "GENES",
        "plate_color": (247, 208, 62),  # truck-door yellow
        "ink": (35, 28, 22),
    },
    {
        "id": "prevalence_teens",
        "chroma": (0, 255, 255),  # cyan
        "chroma_name": "PURE CYAN",
        "carrier": "the depth marker plate bolted to the deeper terrace where the teenagers stand",
        "paint": "4-8%",
        "tag": "TEENS",
        "plate_color": (242, 242, 238),  # white enamel depth plate
        "ink": (28, 28, 30),
    },
]

# ── Scene brief (spec §2/§6: mundane punned venue, inanimate diagonal anchor,
#    one shared ambient activity, packed frame, no protagonist) ─────────────
SCENE = """A working open-pit stone quarry on an ordinary overcast weekday morning.

ANCHOR STRUCTURE (the dominant element in the frame, and it is INANIMATE): the
terraced pit face, running corner to corner on a diagonal and stepping down in
three broad ledges. It is the largest thing in the picture. There is NO hero and
NO central character — no single figure is larger or more important than the rest.

ONE SHARED ACTIVITY: every person in the frame is working an ordinary quarry
shift — clocking in at the pit-head sheds, walking down the ramp, pushing
barrows, hauling, riding the skip hoist. Nobody is interacting with anybody
else; each person is simply doing their own small job.

ZONES, top to bottom: the rim, where a thin line of ordinary townspeople in
coats stand looking in without descending · the shallow upper terrace, with only
a few children on it · the markedly deeper second terrace, crowded with
teenagers · the pit floor · the ramp and skip hoist along the right edge.

CAST: about 18 figures at comparable scale, none taller than roughly two-fifths
of the frame height, with visible variety of age, build and clothing. Among them:
a trembling wide-eyed worker clutching a white rabbit to his chest; a blurred
worker mid-sprint with a spinning paper pinwheel jammed into his hat band; a
scowling worker tearing a paper safety notice in half; a slumped worker sitting
against the pit wall with a brown glass bottle tipped over at his boot; a
grinning man in a hard hat with his arms flung wide, being winched upward out of
the pit on the skip hoist; a worker walking back DOWN the ramp holding up a
punched ticket.

PACK THE FRAME edge to edge. Include plenty of ordinary quarry filler that
encodes nothing — barrows, cable drums, spoil heaps, a lunch trestle, coiled
hose, stacked pallets, pigeons — roughly as much filler as meaningful detail, so
the place reads as a real working site rather than a diagram. No vignette, no
edge darkening: the frame borders are as bright as the centre.
"""

STYLE = """Flat digital cel illustration: crisp constant-width ink outlines over
flat local-colour fills with soft airbrush gradient shading. Outlines are dark
desaturated versions of the colour they enclose — warm brown around skin and
wood, deep grey-blue around cloth — and pure black only on genuinely black
objects. The room shell (sky, distant ground, the far pit wall) carries no
outline at all, only soft gradient, while every prop in front of it is fully
inked. Bright, deadpan, matter-of-fact register — the palette comes from the
setting (grey stone, yellow plant, dusty ochre, workwear blue), never from any
mood. Clean unmixed local colour; never combine low value with low saturation.

Aspect ratio 16:9 landscape. Every character has the correct number of hands,
fingers and limbs — no extra, duplicate, floating or disembodied hands anywhere.
"""


def _plate_instructions() -> str:
    lines = [
        "BLANK SIGN PLATES — read this carefully, it is the most important "
        "instruction in this brief:",
        "",
        "Three objects in this scene each carry a BLANK rectangular sign plate. "
        "Each plate is filled with ONE flat, solid, completely uniform colour — "
        "no shading, no gradient, no texture, no highlight, no shadow, no ink "
        "outline crossing its face, and absolutely NO writing, lettering, "
        "numbers, symbols or marks of any kind on it. Each plate is simply a "
        "clean flat rectangle of pure colour, large and clearly visible, facing "
        "the viewer, and never overlapped or partly hidden by anything.",
        "",
    ]
    for i, s in enumerate(SLOTS, 1):
        r, g, b = s["chroma"]
        lines.append(
            f"  {i}. On {s['carrier']}: a blank flat plate of {s['chroma_name']} "
            f"(exactly RGB {r},{g},{b}). It should read as a sign panel bolted or "
            f"printed onto that object, and it must be big enough to fill a good "
            f"part of that object's visible face."
        )
    lines += [
        "",
        "These three colours appear NOWHERE ELSE in the picture — no other green, "
        "magenta or cyan object, garment, sign or highlight anywhere in the frame.",
        "",
        "TEXT ELSEWHERE: the arched pit-head gate sign is hand-lettered "
        '"MAJOR PIT". The torn paper notice still shows the word "RULES" on the '
        'surviving half. A small sign at the head of the steps between the two '
        'terraces reads "THE DROP". Those are the ONLY three pieces of readable '
        "text in the image. Every other piece of paper, notice, board and label "
        "in the scene is illegible squiggle-lines. NO other numbers or numerals "
        "appear anywhere in the picture.",
    ]
    return "\n".join(lines)


def _control_instructions() -> str:
    """The naive approach we are trying to replace: the model writes the digits."""
    lines = [
        "TEXT TO PAINT — these strings must appear, hand-lettered, legible, "
        "each on the object named, rendered large relative to that object:",
        "",
    ]
    for i, s in enumerate(SLOTS, 1):
        lines.append(
            f'  {i}. "{s["paint"]}" with "{s["tag"]}" in smaller capitals '
            f"directly beneath it, on {s['carrier']}."
        )
    lines += [
        '  4. "MAJOR PIT" on the arched pit-head gate sign.',
        '  5. "RULES" on the surviving half of the torn paper notice.',
        '  6. "THE DROP" on a small sign at the head of the steps.',
        "",
        "Every other piece of paper, notice, board and label in the scene is "
        "illegible squiggle-lines. NO numerals other than those listed appear "
        "anywhere in the picture.",
    ]
    return "\n".join(lines)


# ── Stage 1: render ────────────────────────────────────────────────────────
def render(which: str = "plate", size: str = "2K") -> Path | None:
    client = gi._get_client(None)
    if client is None:
        print("[!] no GEMINI_API_KEY available", file=sys.stderr)
        return None

    body = _plate_instructions() if which == "plate" else _control_instructions()
    prompt = f"{STYLE}\n{SCENE}\n{body}\n"
    out = OUT / ("raw.png" if which == "plate" else "control.png")
    OUT.mkdir(parents=True, exist_ok=True)

    from google.genai import types

    cfg = types.GenerateContentConfig(
        image_config=types.ImageConfig(aspect_ratio="16:9", image_size=size)
    )
    print(f"--- rendering {which} at {size} ({len(prompt)} chars of prompt) ---")
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=gi.IMAGE_MODEL, contents=prompt, config=cfg
            )
            if gi._save_image_from_response(resp, out):
                im = Image.open(out)
                print(f"[ok] {out.name}  {im.size[0]}x{im.size[1]}")
                return out
            print(f"[!] attempt {attempt+1}: no image data", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[!] attempt {attempt+1}: {e}", file=sys.stderr)
    return None


# ── Stage 2: detect + composite ────────────────────────────────────────────
MIN_SATURATION = 0.62   # a chroma plate is near-pure colour; scene colours are not
MIN_VALUE = 90          # and not near-black
HUE_WINDOW = 22.0       # degrees either side of the target hue
MIN_PLATE_PIXELS = 400  # below this it is not a deliberate sign plate
MIN_FILL_RATIO = 0.55   # mask area / quad area — catches merged or ragged blobs


def _mask_for(arr: np.ndarray, chroma: tuple[int, int, int]) -> np.ndarray:
    """Pixels that are genuinely this chroma key.

    Match on HUE, not on channel-difference margins. An earlier version used
    ad-hoc margins like "G-R > 60 and B-R > 60" for cyan, which also matched the
    pure GREEN plate (0,177,64) — its B-R is 64. Overlapping matchers silently
    merged two plates into one nonsense quad. Hue windows cannot overlap:
    green sits at ~142 degrees, cyan at 180, magenta at 300.

    The saturation floor is what rejects ordinary scene colours: a chroma key is
    near-fully saturated (~1.0) while a dusty pink wall or a pale sky sits
    around 0.4.
    """
    a = arr.astype(np.float32)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx, mn = a.max(axis=2), a.min(axis=2)
    rng = mx - mn
    sat = np.divide(rng, np.maximum(mx, 1))

    # hue in degrees, 0-360
    safe = np.maximum(rng, 1e-6)
    hue = np.where(
        mx == r, ((g - b) / safe) % 6,
        np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4),
    ) * 60.0

    target = {
        (0, 177, 64): 141.8,   # green
        (0, 255, 255): 180.0,  # cyan
        (255, 0, 255): 300.0,  # magenta
    }.get(chroma)
    if target is None:
        raise ValueError(f"no hue defined for {chroma}")

    delta = np.abs((hue - target + 180.0) % 360.0 - 180.0)
    return (sat >= MIN_SATURATION) & (mx > MIN_VALUE) & (delta <= HUE_WINDOW)


PROJ_FLOOR = 0.15  # a column/row belongs to the plate if it holds >=15% of the peak


def _dominant_run(proj: np.ndarray) -> tuple[int, int]:
    """Longest contiguous run of above-threshold entries in a 1-D projection."""
    on = proj >= max(1.0, PROJ_FLOOR * float(proj.max()))
    best = (0, -1)
    start = None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i - 1)
            start = None
    if start is not None and len(on) - start > best[1] - best[0]:
        best = (start, len(on) - 1)
    return best


def _largest_blob(mask: np.ndarray) -> np.ndarray:
    """Isolate the plate from stray same-colour pixels elsewhere in the frame.

    Without this, one stray matching pixel in a far corner drags a corner of the
    quad across the whole image.

    Uses scipy's connected components when available. The fallback is neither a
    hand-rolled flood fill (an earlier version spun forever whenever a fill
    returned empty) nor a percentile trim (too weak once the stray exceeds the
    trimmed fraction) but row/column projections: the plate occupies one
    contiguous, high-count run on each axis — true even when it is rotated or
    perspective-skewed — while a stray produces an isolated low-count run that
    the threshold drops. Genuine large merges survive this and are caught
    instead by the fill-ratio test in `_quad_from_mask`.
    """
    if not mask.any():
        return mask
    try:
        from scipy import ndimage  # optional; exact and faster when present

        lab, n = ndimage.label(mask)
        if n > 1:
            sizes = ndimage.sum(mask, lab, range(1, n + 1))
            return lab == (int(np.argmax(sizes)) + 1)
        return mask
    except Exception:  # noqa: BLE001 — scipy absent or unusable; use the fallback
        pass

    x0, x1 = _dominant_run(mask.sum(axis=0).astype(np.float64))
    y0, y1 = _dominant_run(mask.sum(axis=1).astype(np.float64))
    if x1 < x0 or y1 < y0:
        return mask
    keep = np.zeros_like(mask)
    keep[y0:y1 + 1, x0:x1 + 1] = True
    return mask & keep


def _quad_from_mask(mask: np.ndarray) -> tuple[list[tuple[int, int]] | None, dict]:
    """Four corners of the (possibly rotated / perspective) plate.

    For a convex quad, the extremes of (x+y) and (x-y) land on its corners.
    Returned in TL, TR, BR, BL order, with a diagnostics dict.
    """
    diag: dict = {}
    mask = _largest_blob(mask)
    ys, xs = np.nonzero(mask)
    diag["blob_pixels"] = int(xs.size)
    if xs.size < MIN_PLATE_PIXELS:
        diag["reject"] = "too_small"
        return None, diag

    s, d = xs + ys, xs - ys
    pts = {
        "tl": (int(xs[np.argmin(s)]), int(ys[np.argmin(s)])),
        "br": (int(xs[np.argmax(s)]), int(ys[np.argmax(s)])),
        "bl": (int(xs[np.argmin(d)]), int(ys[np.argmin(d)])),
        "tr": (int(xs[np.argmax(d)]), int(ys[np.argmax(d)])),
    }
    quad = [pts["tl"], pts["tr"], pts["br"], pts["bl"]]

    # shoelace area of the quad vs the actual mask area: a clean rectangular
    # plate fills its own quad. A ragged or still-merged blob does not.
    ax = [p[0] for p in quad] + [quad[0][0]]
    ay = [p[1] for p in quad] + [quad[0][1]]
    area = abs(sum(ax[i] * ay[i + 1] - ax[i + 1] * ay[i] for i in range(4))) / 2
    diag["fill_ratio"] = round(float(xs.size / area), 3) if area else 0.0
    if area <= 0 or diag["fill_ratio"] < MIN_FILL_RATIO:
        diag["reject"] = "not_rectangular"
        return None, diag
    return quad, diag


GROW_PX = 4      # outward growth of the detected quad before warping the face
DILATE_PX = 5    # MaxFilter kernel (odd) covering the anti-aliased chroma rim


def _grow_quad(quad: list[tuple[int, int]], px: float) -> list[tuple[int, int]]:
    """Push each corner `px` outward along the ray from the quad's centroid."""
    cx = sum(p[0] for p in quad) / 4.0
    cy = sum(p[1] for p in quad) / 4.0
    out = []
    for x, y in quad:
        dx, dy = x - cx, y - cy
        n = (dx * dx + dy * dy) ** 0.5 or 1.0
        out.append((int(round(x + px * dx / n)), int(round(y + px * dy / n))))
    return out


def _font(px: int) -> ImageFont.FreeTypeFont:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/seguibl.ttf",
    ):
        if Path(p).exists():
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


def _render_face(w: int, h: int, slot: dict) -> Image.Image:
    """The flat sign face: plate colour, the digit string large, the tag beneath."""
    face = Image.new("RGBA", (w, h), tuple(slot["plate_color"]) + (255,))
    d = ImageDraw.Draw(face)
    ink = tuple(slot["ink"])

    # thin darker border so it still reads as a bolted-on plate
    d.rectangle([0, 0, w - 1, h - 1], outline=tuple(max(0, c - 60) for c in slot["plate_color"]),
                width=max(2, h // 40))

    # digits fill ~58% of plate height, tag ~22%, both centred
    num_px, tag_px = max(12, int(h * 0.58)), max(8, int(h * 0.22))
    fn, ft = _font(num_px), _font(tag_px)

    def _fit(text: str, font: ImageFont.FreeTypeFont, budget: int, start_px: int):
        px = start_px
        while px > 8:
            f = _font(px)
            if d.textlength(text, font=f) <= budget:
                return f
            px -= 2
        return _font(8)

    fn = _fit(slot["paint"], fn, int(w * 0.88), num_px)
    ft = _fit(slot["tag"], ft, int(w * 0.88), tag_px)

    nb, tb = d.textbbox((0, 0), slot["paint"], font=fn), d.textbbox((0, 0), slot["tag"], font=ft)
    nh, th = nb[3] - nb[1], tb[3] - tb[1]
    gap = int(h * 0.06)
    top = (h - (nh + gap + th)) // 2

    d.text(((w - (nb[2] - nb[0])) // 2 - nb[0], top - nb[1]), slot["paint"], font=fn, fill=ink)
    d.text(((w - (tb[2] - tb[0])) // 2 - tb[0], top + nh + gap - tb[1]),
           slot["tag"], font=ft, fill=ink)
    return face


def _perspective_coeffs(src: list[tuple[int, int]], dst: list[tuple[int, int]]):
    """Coefficients mapping dst -> src, as PIL's Image.PERSPECTIVE expects."""
    a = []
    for (sx, sy), (dx, dy) in zip(src, dst):
        a.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        a.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
    A = np.array(a, dtype=float)
    B = np.array([c for p in src for c in p], dtype=float)
    return np.linalg.solve(A, B).tolist()


def composite() -> dict:
    raw = OUT / "raw.png"
    if not raw.exists():
        print(f"[!] {raw} missing — run --render first", file=sys.stderr)
        return {}
    im = Image.open(raw).convert("RGB")
    arr = np.asarray(im)
    W, H = im.size
    report = {"image": f"{W}x{H}", "slots": []}
    out = im.copy()

    for slot in SLOTS:
        mask = _mask_for(arr, slot["chroma"])
        px = int(mask.sum())
        quad, diag = _quad_from_mask(mask)
        rec = {"id": slot["id"], "paint": slot["paint"], "tag": slot["tag"],
               "chroma_pixels": px, **diag}

        if quad is None:
            rec["status"] = "NOT_FOUND"
            report["slots"].append(rec)
            print(f"[miss] {slot['id']:18} chroma px={px} blob={diag.get('blob_pixels')} "
                  f"({diag.get('reject')}) — no usable plate")
            continue

        # restrict the paste alpha to the accepted blob, not every matching pixel
        mask = _largest_blob(mask)

        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        bw, bh = max(xs) - min(xs), max(ys) - min(ys)
        rec.update(quad=quad, bbox=[min(xs), min(ys), bw, bh],
                   height_pct=round(100 * bh / H, 2))

        # Render the face at generous resolution, then warp it onto the quad.
        # The quad is grown slightly outward from its centroid first: the corner
        # extremes land a few pixels inside a rotated plate's true outline, and
        # any mask pixel the warped face fails to cover would otherwise be
        # pasted as pure black (transform() leaves the outside transparent, and
        # convert("RGB") turns that into black).
        grown = _grow_quad(quad, GROW_PX)
        fw, fh = max(64, bw * 2), max(32, bh * 2)
        face = _render_face(fw, fh, slot)
        src = [(0, 0), (fw, 0), (fw, fh), (0, fh)]
        coeffs = _perspective_coeffs(src, grown)
        warped = face.transform((W, H), Image.PERSPECTIVE, coeffs,
                                resample=Image.BICUBIC)

        # Paste using the warped face's OWN alpha over the grown quad, and do
        # NOT intersect it with the colour mask. Intersecting leaves the plate's
        # anti-aliased rim behind as a 1-2px green/magenta fringe, because those
        # boundary pixels fail the saturation test and so are absent from the
        # mask. Over-covering the plate by GROW_PX instead is invisible against
        # the artwork and leaves no fringe; the fill-ratio test above is what
        # guarantees the quad really is the plate and not a merged blob.
        out.paste(warped.convert("RGB"), (0, 0), warped.split()[3])

        rec["status"] = "COMPOSITED"
        report["slots"].append(rec)
        print(f"[ok]   {slot['id']:18} '{slot['paint']}' + '{slot['tag']}'  "
              f"quad={quad}  plate height={rec['height_pct']}% of frame")

    # Cleanup pass. Any chroma left in the frame is a bright unnatural blob that
    # must never ship: an undetected plate, a stray patch the model painted
    # somewhere it was told not to, or a rim we failed to cover. Neutralise it
    # with a heavy local blur of the surrounding art so it reads as background.
    out, leftover = _scrub_chroma(out)
    report["leftover_chroma_px"] = leftover
    if leftover:
        print(f"[scrub] neutralised {leftover} leftover chroma px")

    OUT.mkdir(parents=True, exist_ok=True)
    out.save(OUT / "final.png")
    (OUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    done = sum(1 for s in report["slots"] if s.get("status") == "COMPOSITED")
    print(f"\n{done}/{len(SLOTS)} plates composited -> {OUT/'final.png'}")
    return report


def _scrub_chroma(im: Image.Image) -> tuple[Image.Image, int]:
    """Replace any remaining chroma-key pixels with blurred surrounding art."""
    arr = np.asarray(im.convert("RGB"))
    any_mask = np.zeros(arr.shape[:2], bool)
    for slot in SLOTS:
        any_mask |= _mask_for(arr, slot["chroma"])
    n = int(any_mask.sum())
    if not n:
        return im, 0
    alpha = Image.fromarray((any_mask * 255).astype(np.uint8), mode="L")
    alpha = alpha.filter(ImageFilter.MaxFilter(DILATE_PX))
    patched = im.copy()
    patched.paste(im.filter(ImageFilter.GaussianBlur(28)), (0, 0), alpha)
    return patched, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--composite", action="store_true")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--control", action="store_true",
                    help="also render the naive model-writes-the-text version")
    ap.add_argument("--size", default="2K", choices=["1K", "2K", "4K"])
    a = ap.parse_args()

    if not (a.render or a.composite or a.both or a.control):
        ap.print_help()
        return 1

    if a.render or a.both:
        if render("plate", a.size) is None:
            return 2
    if a.control:
        render("control", a.size)
    if a.composite or a.both:
        composite()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
