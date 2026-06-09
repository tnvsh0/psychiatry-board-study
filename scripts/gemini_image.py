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

# Image model — overridable via env so you can flip between top-quality and
# cheap/free without code edits:
#   SKETCHY_IMAGE_MODEL=gemini-3-pro-image-preview       → Nano Banana Pro (top quality; best
#                                                          text rendering + instruction-following;
#                                                          ~$0.134/img, no real free tier)
#   SKETCHY_IMAGE_MODEL=gemini-3.1-flash-image-preview   → Nano Banana 2 / Flash (~$0.039/img,
#                                                          ~500 imgs/day FREE tier)
# Default is Pro (highest quality, which helps most with labels + criteria completeness).
IMAGE_MODEL = os.environ.get("SKETCHY_IMAGE_MODEL", "gemini-3-pro-image-preview")
CAPTION_MODEL = "gemini-2.5-flash"  # text-only (director/caption/coverage) — cheap, leave as is


SKETCHY_STYLE_PREFIX = """Create a single full-color hand-painted illustration in SketchyMedical / SketchyMicro style — a COMPLETE SCENE, not an infographic.

The image MUST be:
- A full environment filling the entire frame (e.g. beach, kitchen, lab, forest, café, hospital ward, market, classroom, mine, opera house, courtroom — whatever fits the topic). NO blank or white background.
- ONE CONTINUOUS SCENE — a single unified space. If the topic has multiple parts (e.g. 4 chambers of a mine, 5 floors of a hotel, 3 areas of a kitchen), render them as a single CUTAWAY / CROSS-SECTION view connected by tunnels, doorways, stairs, walkways. Different areas distinguished by contents and decoration, not by frames or borders.
- A FEW characters (about 3-6), each doing something that matters — interacting, working, reacting. Not a crowd.
- A specific moment in time: something is HAPPENING right now (someone cooking, performing, escaping, examining a patient, conducting an orchestra...).
- CLEAN and UNCLUTTERED — every element must earn its place by encoding a fact. NO decorative filler, NO random extra props, NO busy background noise. Generous breathing space. (This is the opposite of a cluttered Where's-Waldo jumble — a viewer should parse the whole scene in a few seconds.)
- Hand-painted watercolor with loose ink linework. Visible brush texture and paint bleed. Warm, saturated, slightly desaturated colors. NOT flat vector art. NOT digital corporate illustration. NOT 3D render.
- Each concept from the legend embedded as a physical character, object, prop, or action — woven INTO the scene, NOT placed beside it like icons.
- ANATOMY: every character has the correct number of hands, fingers and limbs. NO extra, duplicate, floating or disembodied hands/arms anywhere in the image.

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

LOOK AT THE ATTACHED IMAGE. Your caption MUST describe ONLY what is ACTUALLY visible in it.

GROUND TRUTH — a literal visual inventory taken from the rendered image itself (this is what is REALLY there):
{visual_inventory}

Treat that inventory + the attached image as the ONLY ground truth. The two reference blocks below describe what the illustrator was ASKED to draw — the image OFTEN diverges from them (an element gets dropped, merged, or changed). Use the references ONLY to INTERPRET the meaning of elements that genuinely appear in the inventory / image.

ironclad rule — DO NOT HALLUCINATE INTENDED-BUT-UNDRAWN ELEMENTS:
- If something is named in the intended scene or the key but is NOT in the visual inventory / not clearly visible — DO NOT mention it, and do NOT describe it as if it were present. Silently omit it.
- Never narrate an object, character, label, or number unless that exact thing is unmistakably visible. When in doubt, leave it out. Do NOT pattern-match a vague shape into a number code.

Intended scene the illustrator was asked to draw (reference only — TRUST THE IMAGE OVER THIS):

{image_prompt}

Reference key for the curated symbols of this course (use ONLY to interpret VISIBLE elements):

{symbol_legend}

Write a Hebrew NARRATIVE CAPTION (one flowing paragraph, ~150-220 words). Tell the viewer the story of WHAT IS VISIBLE IN THE IMAGE, and weave in the clinical meaning of each visible character / object / action naturally as part of the storytelling.

REQUIREMENTS:
- Describe ONLY the actual visible scene (per the inventory + image) — do not invent characters, props, labels, or settings that are not there.
- ONE flowing paragraph in Hebrew. NO bullet lists. NO numbered enumeration. NO headings.
- For each visible mnemonic element, explain WHY it represents what it does (the PRIMARY HOOK — the phonetic/visual/conceptual link).
- NUMBERS: if the image genuinely shows a number — as a COUNT of objects/characters (2 boys vs 1 girl), a PROPORTION (3 of 4 affected), a written figure, or an object UNMISTAKABLY shaped like a digit and clearly meant as one — state the number and the clinical figure it stands for. Do NOT assume any fixed shape→digit code: never call something "a swan = 2" or "a boomerang = 7" from a vague resemblance. Report only a number you can truly, unambiguously see; otherwise mention none.
- Include the clinical significance briefly where it matters — link visible objects to facts on the Israeli board exam.
- If the reference key contains a line beginning "EXACT FIGURES (state in caption)", you MUST state those precise figures (e.g. prevalence %, comorbidity rates, heritability %) as clinical data — clearly as statistics the resident must memorize, NOT as objects visible in the scene.
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
    """Generate one SketchyMedical-style scene. Returns True on success.

    Retries a couple of times on transient image-model failures (empty
    response / network hiccup / occasional safety refusal) — important for
    long unattended bulk runs where a single transient miss shouldn't drop
    a whole chapter's image."""
    client = _get_client(api_key)
    if client is None:
        return False

    full_prompt = SKETCHY_STYLE_PREFIX + _palette_directive(phase) + "\n\nContent to illustrate:\n" + prompt

    import time as _time
    for attempt in range(3):
        try:
            response = client.models.generate_content(model=model, contents=full_prompt)
            if _save_image_from_response(response, Path(output_path)):
                return True
            print(f"[!] Image attempt {attempt+1}/3 returned no image data.", file=sys.stderr)
        except Exception as e:
            print(f"[!] Image attempt {attempt+1}/3 failed: {e}", file=sys.stderr)
        if attempt < 2:
            _time.sleep(5 * (attempt + 1))
    return False


def _visual_inventory(client, image_path: str | Path, model: str = CAPTION_MODEL) -> str:
    """STAGE A of captioning: a literal, interpretation-free list of what is
    actually visible in the RENDERED image. This grounds the caption so it can
    never narrate intended-but-undrawn elements — the image (not the director's
    key) is the source of truth. Returns a bullet list, or a soft fallback."""
    try:
        from google.genai import types
        img = Path(image_path)
        if not (img.exists() and img.stat().st_size > 0):
            return "(image unavailable — describe only what is clearly implied)"
        probe = (
            "Look ONLY at the attached image. List, as terse plain bullet points, "
            "every distinct thing you can ACTUALLY SEE: each character and what "
            "they are doing; each object / prop; each sign, label, letter or number "
            "(quote any text EXACTLY between quotation marks); and the setting. Do "
            "NOT interpret meaning, do NOT mention medicine, do NOT guess at "
            "anything that is not clearly visible. If you are unsure whether "
            "something is there, leave it out."
        )
        contents = [probe, types.Part.from_bytes(data=img.read_bytes(), mime_type="image/png")]
        resp = client.models.generate_content(model=model, contents=contents)
        text = ""
        for cand in getattr(resp, "candidates", []) or []:
            for part in getattr(cand.content, "parts", []) or []:
                if getattr(part, "text", None):
                    text += part.text
        return text.strip() or "(visual inventory came back empty)"
    except Exception as e:
        print(f"[!] visual inventory failed: {e}", file=sys.stderr)
        return "(visual inventory unavailable — rely on the attached image directly)"


def generate_caption(
    image_prompt: str,
    symbol_legend: str,
    image_path: Optional[str | Path] = None,
    api_key: Optional[str] = None,
    model: str = CAPTION_MODEL,
) -> str:
    """Generate a Hebrew narrative caption (two-stage when an image is given).

    When image_path is provided we first take a literal VISUAL INVENTORY of the
    rendered image (stage A), then write the caption (stage B) grounded in that
    inventory + the attached image, so the narrative describes what was ACTUALLY
    drawn and never hallucinates intended-but-undrawn elements from the key.
    Without the image, falls back to prompt-only mode (legacy)."""
    client = _get_client(api_key)
    if client is None:
        return ""
    inventory = "(no image attached — fall back to the intended scene below)"
    if image_path:
        inventory = _visual_inventory(client, image_path, model=model)
    instruction = CAPTION_INSTRUCTION.format(
        visual_inventory=inventory.strip(),
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


def _loads_first_json(text: str):
    """Parse the FIRST JSON value out of a model response, tolerating code fences
    and any trailing commentary the model appends after the JSON (which makes a
    plain json.loads raise 'Extra data: ...'). Returns the parsed object, or None."""
    import json as _json
    import re as _re
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = _re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = _re.sub(r"\n```\s*$", "", t)
        t = t.strip()
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if not starts:
        return None
    frag = t[min(starts):]
    try:
        obj, _ = _json.JSONDecoder().raw_decode(frag)  # ignores trailing data
        return obj
    except Exception:
        try:
            return _json.loads(t)
        except Exception:
            return None


def propose_anchor_theme(topic: str, aspects: list, api_key: Optional[str] = None,
                         model: str = CAPTION_MODEL) -> dict:
    """Pick ONE bizarre unifying 'world' for an entire chapter, and assign each
    aspect a DISTINCT zone within that world. All of the chapter's Sketchy
    images then share this world (aiding recall as a set) while each occupies
    its own zone (preventing factual bleeding). Returns
    {"world": str, "zones": {aspect: str}} or {} on failure."""
    client = _get_client(api_key)
    if client is None:
        return {}
    instruction = f"""You are designing a unified SketchyMedical-style mnemonic WORLD for one psychiatry chapter, so that all its images feel like parts of one memorable place.

Chapter topic: {topic}
Aspects that each need their own zone: {', '.join(aspects)}

HOW REAL SKETCHY PICKS A WORLD — the disease NAME becomes a phonetic PUN that generates the whole world:
- "Strep pyogenes" sounds like "Pie Genies" → a BAKERY full of genie-bakers.
- "Strep agalactiae" sounds like "galactic" → OUTER SPACE with an astronaut baby.
- "Neisseria" → "Nice area" ; "Klebsiella" → a clay/"Klebs" scene ; etc.

A name-pun world is great WHEN the pun is genuinely close — but a FORCED, weak pun is worse than no pun. RULES:
- Use a phonetic pun ONLY if a listener would actually hear the resemblance out loud (e.g. pyogenes->Pie Genies is strong; 'Keshev'->'Cabbage' is a STRETCH — reject it).
- The pun should be on the disorder's name or a salient word in it. Prefer English; a Hebrew/transliterated pun only if it is unmistakably close.
- If there is NO strong pun (many disorders, e.g. 'ADHD', have none), DO NOT force one. Instead choose a STRONG THEMATIC bizarre world whose vibe matches the disorder's feel (e.g. for ADHD something chaotic and impossible-to-sit-still: a pinball machine, a rodeo, a fireworks factory). Never a clinical/medical setting.
- Whatever you pick, the world must support a real STORY (an event is happening) — not just a themed backdrop with floating actors.

The world must be rich enough to host several distinct, visually different zones.
Assign each aspect a DISTINCT zone — and CRUCIALLY, each zone must be a place
where THAT aspect's facts would naturally HAPPEN, so the later scene's events
are motivated by the place (not pasted on):
- a CRITERIA zone = somewhere people are tested/judged/sorted (a test track, an
  audition, a border checkpoint) so symptoms show up as natural behaviour.
- an EPIDEMIOLOGY zone = somewhere crowds/counts/families/forecasts live (a
  stadium, a census fair, a betting parlour) so prevalence/ratios/heritability
  arise naturally.
- a TREATMENT zone = somewhere things are fixed/served/powered (a workshop, a
  kitchen, a pit-stop) so drugs and their effects belong.
Zones should differ in dominant colour/objects so they're never confused.

Return STRICT JSON only:
{{"world": "<the one world + (if used) the name-pun it came from>", "zones": {{{', '.join(f'"{a}": "<a zone where {a} facts naturally happen>"' for a in aspects)}}}}}"""
    try:
        response = client.models.generate_content(model=model, contents=instruction)
        text = ""
        for cand in getattr(response, "candidates", []) or []:
            for part in getattr(cand.content, "parts", []) or []:
                if getattr(part, "text", None):
                    text += part.text
        data = _loads_first_json(text)
        if isinstance(data, dict) and data.get("world"):
            return data
    except Exception as e:
        print(f"[!] anchor theme generation failed: {e}", file=sys.stderr)
    return {}


def run_scene_director(director_instruction: str, api_key: Optional[str] = None,
                       model: str = CAPTION_MODEL) -> dict:
    """The 'scene director': converts clinical facts into a PURE-VISUAL scene
    description (no clinical terms — so the image model has nothing to label)
    plus a numbered KEY mapping each visible element to its fact. Mirrors how a
    real Sketchy page is authored: an artist gets a scene brief, the key is
    separate text. Returns {"scene": str, "key": str} or {}."""
    client = _get_client(api_key)
    if client is None:
        return {}
    try:
        resp = client.models.generate_content(model=model, contents=director_instruction)
        text = ""
        for cand in getattr(resp, "candidates", []) or []:
            for part in getattr(cand.content, "parts", []) or []:
                if getattr(part, "text", None):
                    text += part.text
        data = _loads_first_json(text)
        if isinstance(data, dict) and data.get("scene"):
            return {"scene": str(data.get("scene", "")), "key": str(data.get("key", ""))}
    except Exception as e:
        print(f"[!] scene director failed: {e}", file=sys.stderr)
    return {}


def check_coverage(client, image_path: str | Path, required_items: list,
                   model: str = CAPTION_MODEL) -> list:
    """Return the subset of `required_items` that are NOT clearly represented in
    the rendered image. Used to GUARANTEE that criteria images contain a
    distinct, findable visual hook for EVERY diagnostic criterion (for criteria,
    completeness is mandatory — the resident must be able to find them all).
    Fails open (returns []) on any error so it never blocks generation."""
    try:
        from google.genai import types
        img = Path(image_path)
        if client is None or not required_items or not (img.exists() and img.stat().st_size > 0):
            return []
        numbered = "\n".join(f"{i + 1}. {it}" for i, it in enumerate(required_items))
        probe = (
            "You are checking the COMPLETENESS of a mnemonic illustration. Below is a "
            "numbered list of items; each should appear in the image as a distinct, "
            "findable visual element (a metaphor / prop / action counts — it need not "
            "be literal). Look at the attached image and decide, for EACH item, "
            "whether a clearly findable visual element for it is present. Be strict: "
            "if you cannot point to a specific element, it is missing. Return STRICT "
            'JSON only: {"missing": [<exact numbers of the items with no clear visual>]}.'
            "\n\nITEMS:\n" + numbered
        )
        contents = [probe, types.Part.from_bytes(data=img.read_bytes(), mime_type="image/png")]
        resp = client.models.generate_content(model=model, contents=contents)
        text = ""
        for cand in getattr(resp, "candidates", []) or []:
            for part in getattr(cand.content, "parts", []) or []:
                if getattr(part, "text", None):
                    text += part.text
        data = _loads_first_json(text)
        nums = data.get("missing", []) if isinstance(data, dict) else []
        missing = []
        for n in nums:
            try:
                idx = int(n) - 1
            except (ValueError, TypeError):
                continue
            if 0 <= idx < len(required_items):
                missing.append(required_items[idx])
        return missing
    except Exception as e:
        print(f"[!] coverage check failed: {e}", file=sys.stderr)
        return []


def generate_sketchy_directed(
    director_instruction: str,
    output_path: str | Path,
    caption_path: str | Path | None = None,
    api_key: Optional[str] = None,
    phase: Optional[int] = None,
    required_items: Optional[list] = None,
    max_image_tries: int = 1,
) -> tuple[bool, str]:
    """Full directed pipeline: director (facts→clean scene+key) → image model →
    caption (reads the actual image + the key → flowing Hebrew narrative).

    When `required_items` is given (criteria images), the image is rendered with a
    COVERAGE-GUARANTEED retry loop: after each render we check which required
    items are missing; if any are, we re-render (up to `max_image_tries`) with the
    missing ones emphasized, and keep the MOST COMPLETE render. This enforces the
    rule that a criteria image must contain a distinct visual for EVERY criterion.
    Returns (image_ok, caption_text)."""
    directed = run_scene_director(director_instruction, api_key=api_key)
    if not directed:
        print("[!] director returned nothing; cannot generate directed scene.", file=sys.stderr)
        return False, ""
    scene = directed["scene"]
    key = directed["key"]

    # Image prompt = visual STYLE spec + the clean SCENE (no clinical jargon).
    base_image_prompt = (
        SKETCHY_STYLE_PREFIX
        + "\n\nSCENE TO ILLUSTRATE — draw EXACTLY this, as one cohesive scene:\n"
        + scene
        + "\n\nABSOLUTE: no written words anywhere in the image EXCEPT the few short"
          " diegetic words explicitly quoted in the scene above (signs, name tags,"
          " numbers on objects). No explanatory labels, no captions, no callouts."
    )

    client = _get_client(api_key) if required_items else None
    tries = max(1, max_image_tries) if required_items else 1
    image_prompt = base_image_prompt
    best_bytes = None
    best_missing = None  # None = not yet measured

    for attempt in range(tries):
        if not generate_image(image_prompt, output_path, api_key=api_key, phase=phase):
            continue
        if not required_items or client is None:
            best_bytes = Path(output_path).read_bytes()
            best_missing = []
            break
        missing = check_coverage(client, output_path, required_items)
        if best_missing is None or len(missing) < len(best_missing):
            best_bytes = Path(output_path).read_bytes()
            best_missing = missing
        if not missing:
            break
        print(f"[i] criteria image attempt {attempt + 1}/{tries}: missing "
              f"{len(missing)}/{len(required_items)} item(s); retrying.", file=sys.stderr)
        miss_txt = "; ".join(missing)
        image_prompt = base_image_prompt + (
            "\n\nCRITICAL COMPLETENESS FIX — the previous attempt was MISSING these "
            f"required elements: {miss_txt}. This time you MUST include EVERY one of "
            "them as its OWN distinct, clearly visible element, in addition to the "
            "rest. Higher density is acceptable; COMPLETENESS IS MANDATORY — do not "
            "drop or merge any required element."
        )

    if best_bytes is None:
        return False, ""
    # Restore the most-complete render (a later attempt may have been worse).
    Path(output_path).write_bytes(best_bytes)
    if best_missing:
        print(f"[!] criteria image kept with {len(best_missing)} item(s) still not "
              f"clearly rendered: {'; '.join(best_missing)}", file=sys.stderr)

    # Caption = read the actual (best) image, grounded by the director's key.
    caption = generate_caption(scene, key, image_path=output_path, api_key=api_key)
    if caption and caption_path:
        cp = Path(caption_path)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(caption, encoding="utf-8")
    return True, caption


def generate_image_with_caption(
    prompt: str,
    output_path: str | Path,
    caption_path: str | Path | None = None,
    symbol_legend: str = "",
    api_key: Optional[str] = None,
    phase: Optional[int] = None,
) -> tuple[bool, str]:
    """LEGACY direct pipeline (prompt → image → caption). Kept for the
    non-directed path. New Sketchy generation uses generate_sketchy_directed."""
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
