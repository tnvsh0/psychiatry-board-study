#!/usr/bin/env python3
"""
Gemini image generation for SketchyMedical-style educational scenes.

Used by daily_study.py to replace NotebookLM's template-based infographic
generator with full Sketchy-style storybook scenes via Gemini 2.5 Flash
Image, plus a flowing Hebrew narrative caption per image.

Reads GEMINI_API_KEY from environment (auto-loads from repo-root .env).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional


def _load_dotenv() -> None:
    """Load KEY=value pairs from repo-root .env into os.environ (no dep)."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


REPO_ROOT = Path(__file__).resolve().parent.parent
PHASE_PALETTE_PATH = REPO_ROOT / "schedule" / "phase_palette.json"

IMAGE_MODEL = "gemini-3.1-flash-image-preview"  # Nano Banana 2 — 16:9 native, improved text rendering
CAPTION_MODEL = "gemini-2.5-flash"


SKETCHY_STYLE_PREFIX = """Create a single full-color hand-painted illustration in SketchyMedical / SketchyMicro style — a COMPLETE SCENE, not an infographic.

The image MUST be:
- A full environment filling the entire frame (e.g. beach, kitchen, lab, forest, café, hospital ward, market, classroom, mine, opera house, courtroom — whatever fits the topic). NO blank or white background.
- ONE CONTINUOUS SCENE — a single unified space. If the topic has multiple parts (e.g. 4 chambers of a mine, 5 floors of a hotel, 3 areas of a kitchen), render them as a single CUTAWAY / CROSS-SECTION view connected by tunnels, doorways, stairs, walkways. Different areas distinguished by contents and decoration, not by frames or borders.
- Multiple characters present, each doing something — interacting, working, reacting.
- A specific moment in time: something is HAPPENING right now (someone cooking, performing, escaping, examining a patient, conducting an orchestra...).
- Dense and detail-rich, like a single page from a children's storybook or a Where's Waldo scene.
- Hand-painted watercolor with loose ink linework. Visible brush texture and paint bleed. Warm, saturated, slightly desaturated colors. NOT flat vector art. NOT digital corporate illustration. NOT 3D render.
- Each concept from the legend embedded as a physical character, object, prop, or action — woven INTO the scene, NOT placed beside it like icons.

Text rules — STRICT:
- NO callout arrows. NO captions. NO legend boxes. NO floating text overlays. NO explanation text.
- ONLY diegetic text — text that physically exists as part of the environment: signs on buildings, name tags on collars, single words painted on packages or barrels, numbers on equipment, lettering on a chef's hat.
- BUT this diegetic text is CRITICAL — these labels are the primary mnemonic anchors the viewer must read at a glance. Render them PROMINENTLY and LEGIBLY: bold, large, clearly readable letters. A goat's name tag should have a big readable "K". A milk pail should have its single word painted BIG and BOLD. A storefront sign should fill its space confidently. Each label stays at 1-2 words / 1 letter / 1 number — but rendered LARGE enough to read instantly even at thumbnail size (about 200px wide).
- The mnemonic hook = the diegetic label. Treat it as the most important visual element after the character/object itself.
- LABEL VISIBILITY RULE — never let one labeled object hide another's label:
  * When multiple labeled objects appear together (a row of pails, a set of signs, several characters with name tags) — arrange them so EVERY label faces the viewer and is fully visible.
  * Prefer SIDE-BY-SIDE row layouts (objects standing next to each other across the frame) over depth-stacked rows (objects lined up going away from the camera, where the front one blocks the back ones).
  * If perspective is necessary, fan the objects out (like a hand of cards) so each label remains visible at the same angle.
  * Each label-bearing surface should face roughly toward the viewer — not turned to the side or rotated away.
  * No label may be partially obscured by another object, by shadow, or by foreground clutter.

Composition hierarchy — most prominent to least:
1. The 3-5 labeled mnemonic anchors (characters or objects bearing diegetic labels like MOO, K, '2', Delta, EXTRA, etc.) are the LARGEST elements in the scene. Place them prominently in the foreground or middle-ground. Their labels must be clearly readable at thumbnail size.
2. Supporting characters and props fill the surrounding scene to make the world feel alive and dense — but they do NOT compete in size with the labeled anchors. They are visibly smaller / less foreground.
3. Background environment (barn, sky, fields, walls, equipment, distant figures) sets the scene without distracting.

A viewer's eye should hit the labeled anchors first, then explore supporting details, then take in the environment. Density should never come at the cost of label legibility.

Composition: aspect ratio 16:9 landscape. Think: ONE illustrated page from a Sketchy Medical book — pure storybook scene.

"""


CAPTION_INSTRUCTION = """You are explaining a SketchyMedical-style educational illustration to a psychiatric resident studying for the Israeli board exam (שלב ב).

LOOK AT THE ATTACHED IMAGE. Your caption MUST describe what is ACTUALLY visible in the image — its specific characters, its specific setting, its specific labels. Do NOT invent a different scene. If the image shows a bakery, write about the bakery. If it shows a circus, write about the circus.

Original generation prompt that produced this image (context only — the actual image may differ; trust what you SEE, not what was requested):

{image_prompt}

Reference legend for the curated symbols of this course (use these to interpret what the visible elements stand for):

{symbol_legend}

Write a Hebrew NARRATIVE CAPTION (one flowing paragraph, ~150-220 words). Tell the viewer the story of WHAT IS VISIBLE IN THE IMAGE, and weave in the clinical meaning of each visible character / object / action naturally as part of the storytelling.

REQUIREMENTS:
- Describe the ACTUAL VISIBLE SCENE — do not invent characters or settings that are not in the image.
- ONE flowing paragraph in Hebrew. NO bullet lists. NO numbered enumeration. NO headings.
- For each visible mnemonic element, explain WHY it represents what it does (the PRIMARY HOOK — the phonetic/visual/conceptual link).
- NUMBER-PEGS — CRITICAL: whenever a number-shape peg appears in the image (a HAND/high-five or starfish = 5, a swan = 2, a cherry or six-shooter = 6, a candle = 1, a sailboat = 4, a boomerang = 7, a snowman/hourglass = 8, a balloon/cat = 9, a donut/ring = 0), you MUST name it in the story AND state the number it encodes AND the clinical figure it stands for — e.g. "היד שעושה high-five (5) חתומה ב-% מזכירה ששכיחות ADHD היא כ-5%". Never skip a visible peg; the whole point is to lock the number to the picture. A small wordplay on the number is a bonus, not required.
- Include the clinical significance briefly where it matters — link visible objects to facts on the Israeli board exam.
- Use English for: drug generic names, receptor names (μ, κ, δ, D2, 5-HT2A, NMDA, GABA-A, α1, β2, M3), clinical acronyms (EPS, NMS, SSRI, OCD), and any mnemonic labels visible in the image (MOO, K, EXTRA, '2', etc.).
- Hebrew explanatory prose throughout.

Begin the caption directly — no preamble, no "Here is the caption:", just the paragraph itself.
"""


def _load_palette() -> dict:
    if not PHASE_PALETTE_PATH.exists():
        return {}
    try:
        return json.loads(PHASE_PALETTE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _palette_directive(phase: Optional[int]) -> str:
    """Return a prompt line injecting the phase-specific color palette, or ''."""
    if phase is None:
        return ""
    palette = _load_palette()
    entry = palette.get(str(phase)) or palette.get(phase)
    if not isinstance(entry, dict):
        return ""
    desc = entry.get("prompt", "")
    if not desc:
        return ""
    return f"\nOverall color palette bias for this scene: {desc}.\n"


def _get_client(api_key: Optional[str]):
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("[!] GEMINI_API_KEY not set; cannot generate image.", file=sys.stderr)
        return None
    try:
        from google import genai
    except ImportError:
        print("[!] google-genai not installed. Run: pip install google-genai", file=sys.stderr)
        return None
    return genai.Client(api_key=key)


def _save_image_from_response(response, output_path: Path) -> bool:
    """Walk response parts, write the first inline image to output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for cand in getattr(response, "candidates", []) or []:
        for part in getattr(cand.content, "parts", []) or []:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                output_path.write_bytes(inline.data)
                return output_path.exists() and output_path.stat().st_size > 0
    return False


def generate_image(
    prompt: str,
    output_path: str | Path,
    api_key: Optional[str] = None,
    phase: Optional[int] = None,
    model: str = IMAGE_MODEL,
) -> bool:
    """Generate one SketchyMedical-style scene. Returns True on success."""
    client = _get_client(api_key)
    if client is None:
        return False

    full_prompt = SKETCHY_STYLE_PREFIX + _palette_directive(phase) + "\n\nContent to illustrate:\n" + prompt

    try:
        response = client.models.generate_content(model=model, contents=full_prompt)
    except Exception as e:
        print(f"[!] Image generation failed: {e}", file=sys.stderr)
        return False

    if _save_image_from_response(response, Path(output_path)):
        return True
    print("[!] Model returned no image data.", file=sys.stderr)
    return False


def generate_caption(
    image_prompt: str,
    symbol_legend: str,
    image_path: Optional[str | Path] = None,
    api_key: Optional[str] = None,
    model: str = CAPTION_MODEL,
) -> str:
    """Generate a Hebrew narrative caption.

    When image_path is provided, the actual saved image bytes are attached
    to the request so the multimodal text model describes what's ACTUALLY
    visible in the image rather than re-inventing a scene from the prompt.
    Without the image, falls back to prompt-only mode (legacy)."""
    client = _get_client(api_key)
    if client is None:
        return ""
    instruction = CAPTION_INSTRUCTION.format(
        image_prompt=image_prompt.strip(),
        symbol_legend=symbol_legend.strip() or "(no specific legend symbols provided)",
    )

    contents: list = [instruction]
    if image_path:
        try:
            from google.genai import types
            img_path = Path(image_path)
            if img_path.exists() and img_path.stat().st_size > 0:
                contents.append(types.Part.from_bytes(
                    data=img_path.read_bytes(),
                    mime_type="image/png",
                ))
        except Exception as e:
            # If image attachment fails, continue with prompt-only.
            print(f"[!] Image attachment for caption failed: {e}", file=sys.stderr)

    try:
        response = client.models.generate_content(model=model, contents=contents)
    except Exception as e:
        print(f"[!] Caption generation failed: {e}", file=sys.stderr)
        return ""
    text_parts = []
    for cand in getattr(response, "candidates", []) or []:
        for part in getattr(cand.content, "parts", []) or []:
            t = getattr(part, "text", None)
            if t:
                text_parts.append(t)
    return "".join(text_parts).strip()


def generate_image_with_caption(
    prompt: str,
    output_path: str | Path,
    caption_path: str | Path | None = None,
    symbol_legend: str = "",
    api_key: Optional[str] = None,
    phase: Optional[int] = None,
) -> tuple[bool, str]:
    """Generate image AND narrative caption. Returns (image_ok, caption_text).

    The caption model is now given the actual generated image (multimodal
    input) so the caption faithfully describes what is visible — instead
    of inventing a separate scene from the same open-ended prompt."""
    image_ok = generate_image(prompt, output_path, api_key=api_key, phase=phase)
    if not image_ok:
        return False, ""
    caption = generate_caption(
        prompt, symbol_legend,
        image_path=output_path,
        api_key=api_key,
    )
    if caption and caption_path:
        cp = Path(caption_path)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(caption, encoding="utf-8")
    return True, caption


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", help="Image content prompt")
    ap.add_argument("output", help="Output PNG path")
    ap.add_argument("--caption", help="Optional caption .txt output path")
    ap.add_argument("--legend", default="", help="Symbol legend block for the caption")
    ap.add_argument("--phase", type=int, help="Phase 1-10 for palette injection")
    args = ap.parse_args()
    if args.caption:
        ok, cap = generate_image_with_caption(
            args.prompt, args.output, args.caption,
            symbol_legend=args.legend, phase=args.phase,
        )
        if cap:
            print(f"[OK] caption ({len(cap)} chars) saved to {args.caption}")
        sys.exit(0 if ok else 1)
    else:
        ok = generate_image(args.prompt, args.output, phase=args.phase)
        sys.exit(0 if ok else 1)
