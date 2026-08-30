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
import re
import sys
from pathlib import Path
from typing import Optional

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


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

# Optional cheaper model for the DRAFT rungs of the retry ladder. Most renders
# in a chapter are thrown away: chapter 29's first plate took four tries to go
# from score 4 to score 1, so three Pro images at ~$0.134 were paid for and
# discarded. With this set, every try except the last uses the cheap model to
# find the composition and the last uses IMAGE_MODEL for the frame that ships.
# Unset by default — this changes what the learner sees, so it is the owner's
# call, not a silent optimisation.
LADDER_MODEL = os.environ.get("SKETCHY_LADDER_MODEL", "").strip()


def model_for_try(attempt: int, max_tries: int) -> str:
    """Which image model this rung of the ladder should use."""
    if ENGINE == "agy":
        # The agy tool exposes no model choice, so returning a Gemini model id
        # here would write a name into the plate report that nothing actually
        # ran — and the report is what a later question about quality gets
        # answered from.
        return "agy:generate_image"
    if LADDER_MODEL and attempt < max_tries - 1:
        return LADDER_MODEL
    return IMAGE_MODEL
CAPTION_MODEL = "gemini-2.5-flash"  # text-only (director/caption/coverage) — cheap, leave as is

# Render size. v1 never set this and silently took the default 1376x768, where a
# glyph at the corpus's own relative size lands around 24px — right at the
# legibility floor before any model garbling, on plates that now carry 22-34
# elements and up to 18 lettered strings. 2K doubles both axes; because the
# hybrid split also cuts renders per chapter from five-to-seven down to two, the
# cost per chapter still falls.
IMAGE_SIZE = os.environ.get("SKETCHY_IMAGE_SIZE", "2K")  # 1K | 2K | 4K
IMAGE_ASPECT = "16:9"

# Which engine actually draws. "api" bills every render, including the two or
# three the degradation ladder throws away to find a composition; "agy" spends
# the Antigravity CLI's own free allowance instead.
#
# The trade is resolution, and it is not negotiable: agy exposes no size
# parameter, so 16:9 is 1376x768 and that is the ceiling on EVERY free route to
# these models — the CLI, the 500/day flash-image tier, the Gemini app. 2K and
# 4K are paid only.
#
# 1K was abandoned once because a glyph landed at ~24px on plates carrying
# 22-34 elements. The three-way plate split is what makes it viable again: a
# third of the elements on the same canvas is three times the glyph size.
# DEFAULT IS agy. It was "api", which is how the renders moved to the free
# quota while the director, the gates and the narration quietly stayed billed —
# and how a project whose pictures cost nothing still hit a monthly spending
# cap. The owner asked for agy and got half of it; the default is the half that
# was missing. Reaching the paid API is now a deliberate act, not an omission.
ENGINE = os.environ.get("SKETCHY_ENGINE", "agy").strip().lower()
if ENGINE == "agy":
    # Anything downstream that reasons about resolution should be told the
    # truth rather than the value someone set in the environment.
    IMAGE_SIZE = "1K"


def _image_config():
    """GenerateContentConfig pinning aspect ratio and resolution, or None."""
    try:
        from google.genai import types
    except ImportError:
        return None
    return types.GenerateContentConfig(
        image_config=types.ImageConfig(
            aspect_ratio=IMAGE_ASPECT, image_size=IMAGE_SIZE
        )
    )


_STYLE_BASE = """Create a single full-color illustration in SketchyMedical style — a COMPLETE PLACE, not an infographic and not a poster.

The image MUST be:
- A full environment filling the entire frame, edge to edge. NO blank or white background, NO vignette, NO edge darkening — the frame borders are as bright as the centre.
- ONE CONTINUOUS SCENE — a single unified space, one perspective, one light source. If the topic has parts, render them as a single CUTAWAY / CROSS-SECTION connected by doorways, stairs and walkways, distinguished by contents rather than by frames or borders.
- 12-25 human figures at comparable scale, with visible variety of age, build, skin tone and clothing. No figure exceeds roughly two-fifths of the frame height. The DOMINANT element is an INANIMATE structure that cuts the frame on a diagonal — a wall, a run of desks, a terraced pit face, a conveyor — never a person. There is NO protagonist.
- ONE shared ambient activity that every figure is engaged in, each in their own way: everyone works the shift, queues, fishes, views the art. No side plot and no story between figures — but every element line in the brief names a person and what they are DOING with that prop, and that action must be visibly happening. A prop that merely sits there while its action goes undrawn is the single most common defect in this course: the same pigeonhole wall is the best thing on a plate when staff are filing into it and dead weight on the next when nobody is.
- PACKED. Roughly one ordinary non-mnemonic prop for every meaningful one, so the place reads as a real working location rather than a diagram. Take them from the ORDINARY CLUTTER list in the brief, which holds this venue's own unclaimed furniture — never from a generic stock of filler. Density is not a defect: this is a search field, not something to be parsed in a few seconds.
- Flat digital cel illustration: crisp constant-width ink outlines over flat local-colour fills with soft airbrush gradient shading. Outlines are dark desaturated versions of the colour they enclose — warm brown around skin and wood, deep grey-blue around cloth — and pure black only on genuinely black objects. The room shell (sky, back wall, distant ground) carries no outline at all, only soft gradient, while every prop in front of it is fully inked.
- Bright, deadpan, matter-of-fact register whatever the subject. The palette comes from the setting itself, never from a mood. Clean unmixed local colour; never combine low value with low saturation.
- Each concept embedded as a physical character, object, prop or action woven INTO the scene, never placed beside it like an icon. NO callout arrows, NO leader lines, NO legend boxes, NO captions, NO floating overlays.
- ANATOMY: every character has the correct number of hands, fingers and limbs. NO extra, duplicate, floating or disembodied hands/arms anywhere in the image.
- PASTICHE QUARANTINE: another artist's technique may appear only as an object inside the world — a framed canvas, a poster, a screen — never as the rendering of the world itself.

"""

# --- Text policy ----------------------------------------------------------------------
# One regime. v1 shipped two mutually exclusive ones — "mnemonic" demanded big bold
# labels while "environment" banned any word that named what an element represented —
# and the director was meanwhile told ZERO TEXT. Three contradictory instructions
# reached the model at once.
#
# The corpus settles it: hand-lettered diegetic text is its single highest-density
# device, roughly 4-15 signs per page, and on most pages several legend items ARE the
# painted string. So text is welcome; what is banned is the overlay.

_TEXT_RULES = """Text rules:
- Legible text is hand-lettered and DIEGETIC — shop signs, business logos, travel posters, T-shirt slogans, wall calendars, machine faceplates, names painted on hulls, price boards, jersey numbers. These are PROPS, not overlays, and they are the primary carrier of names, suffixes and numbers.
- HOW MANY: exactly the strings listed under TEXT TO PAINT, and no others. That list is the budget, not a floor — do not invent extra legible text to fill the picture out. Asking for a fixed 10-18 strings is what produced a plate carrying 35, most of them the names of the objects they sat on.
- A legible string rides on a surface whose REAL JOB is to display that kind of string: a price board shows a price, a scale shows a weight, a stake shows a plant name, a rack topper shows a package name. A sign whose entire content is the fact, bolted to an object that visibly already is that object, is annotation wearing a prop costume — that is the banned overlay with extra steps, however it is mounted.
- Full advertising sentences of 3-6 words over up to three lines are welcome ("BREAK THOSE BAD SLEEPING HABITS!").
- Typographic highlighting inside a word is welcome: set the target letters as enlarged or contrasting caps inside a plausible brand name.
- Every OTHER piece of paper, screen, poster and document in the scene must be illegible squiggle-lines. Legibility is itself the signal that a string is load-bearing.
- Only the strings listed under TEXT TO PAINT are guaranteed legible. Other signage may be small, angled, clipped by the frame edge or partly occluded.
- STILL FORBIDDEN: callout arrows, leader lines, numeric badges laid over the artwork, legend boxes, captions, floating explanation text.
- NO numerals anywhere in the frame except those explicitly specified. A stray decorative number competes with the load-bearing one and breaks the number check.
"""

_STYLE_TAIL = """
Composition: aspect ratio 16:9 landscape. Think: ONE illustrated page from a Sketchy Medical book — pure storybook scene.

"""


def style_prefix(text_policy: str | None = None) -> str:
    """Full image-style prefix.

    `text_policy` is accepted and ignored — the two-regime split is gone (see
    the note above). The parameter stays only so existing call sites keep
    working while they are migrated.
    """
    return _STYLE_BASE + _TEXT_RULES + _STYLE_TAIL


SKETCHY_STYLE_PREFIX = style_prefix()


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
    """Retired on the Sketchy path — always returns ''.

    phase_palette.json maps a study phase to a mood ("cool muted blues and
    gentle greys" for Mood + Anxiety) and injecting it is the proximate cause
    of the grey drowned-world renders: the palette came from the disease
    instead of from the setting. The corpus derives colour from the venue only,
    and holds one bright deadpan register however grim the pathology.

    The file is still read by the Streamlit UI for theming, so it stays.
    """
    return ""


class PaidCallBlocked(RuntimeError):
    """Something tried to reach the billed API while the engine is agy.

    Every path that matters has been moved, but "every path I found" is not
    the same as "every path". This turns the one I missed into an error that
    names itself, instead of a charge discovered on a bill.
    """


def _guard_paid(what: str) -> None:
    if ENGINE == "agy":
        raise PaidCallBlocked(
            f"{what} tried to use the paid Gemini API, but SKETCHY_ENGINE=agy. "
            f"Route it through agy_text/agy_image, or set SKETCHY_ENGINE=api "
            f"deliberately if this call is meant to be billed.")


def _get_client(api_key: Optional[str]):
    _guard_paid("_get_client")
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



# Gemini answers 503 UNAVAILABLE ("this model is currently experiencing high
# demand") in bursts. During chapter 29 it took out the narration, the element
# table director and several render probes inside a few minutes. Only
# generate_image retried; everything else treated a transient outage as a
# permanent failure and carried on with the content missing. Over 141 chapters
# that is a lot of quietly degraded material, so every call goes through here.
_TRANSIENT = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500",
              "INTERNAL", "deadline", "timeout", "Timeout")


class _AgyText:
    """The shape google-genai returns, around an agy answer.

    A shim rather than a rewrite of the six call sites: every caller already
    walks candidates -> content -> parts -> text, and the point of routing
    through _gc is that this stays the single choke point for model calls.
    """

    class _Part:
        def __init__(self, text): self.text = text

    class _Content:
        def __init__(self, text): self.parts = [_AgyText._Part(text)]

    class _Cand:
        def __init__(self, text): self.content = _AgyText._Content(text)

    def __init__(self, text: str):
        self.candidates = [_AgyText._Cand(text)]
        self.text = text


def _gc(client, **kw):
    """client.models.generate_content with backoff on transient failures.

    Under SKETCHY_ENGINE=agy a TEXT call goes to the Antigravity CLI instead.
    Moving only the renders across left the scene director on the paid API,
    and when that project hit its monthly spending cap no plate could be built
    at all — the pictures were free and useless, because the element table is
    a text call. Vision probes still need the API: agy's print mode takes no
    image, so they degrade to "could not run", which the gates already treat
    as "learned nothing" rather than as a failure.
    """
    import time as _t

    contents = kw.get("contents")
    if (ENGINE == "agy" and isinstance(contents, str)
            and kw.get("model") == CAPTION_MODEL):
        import agy_text
        answer = agy_text.ask(contents)
        if answer is None:
            raise RuntimeError("agy text returned nothing")
        return _AgyText(answer)
    last = None
    for attempt in range(4):
        try:
            _guard_paid(f"generate_content(model={kw.get('model')})")
            return client.models.generate_content(**kw)
        except Exception as e:
            last = e
            if not any(t in str(e) for t in _TRANSIENT):
                raise
            if attempt == 3:
                break
            wait = 5 * (2 ** attempt)      # 5s, 10s, 20s
            print(f"[i] gemini transient ({str(e)[:60]}); retrying in {wait}s "
                  f"({attempt + 2}/4)", file=sys.stderr)
            _t.sleep(wait)
    raise last


def generate_image(
    prompt: str,
    output_path: str | Path,
    api_key: Optional[str] = None,
    phase: Optional[int] = None,
    model: str = IMAGE_MODEL,
    text_policy: str = "mnemonic",
) -> bool:
    """Generate one SketchyMedical-style scene. Returns True on success.

    Retries a couple of times on transient image-model failures (empty
    response / network hiccup / occasional safety refusal) — important for
    long unattended bulk runs where a single transient miss shouldn't drop
    a whole chapter's image."""
    full_prompt = style_prefix() + "\n\nContent to illustrate:\n" + prompt

    if ENGINE == "agy":
        # The ladder's draft rungs are the whole reason this branch exists, so
        # a quota wall must not kill a chapter mid-run: agy_image reads the
        # reset time out of the 429 itself and waits it out.
        import agy_image
        try:
            return agy_image.generate(full_prompt, output_path,
                                      aspect=IMAGE_ASPECT,
                                      name=Path(output_path).stem or "plate")
        except agy_image.QuotaExhausted as e:
            print(f"[!] {e}", file=sys.stderr)
            return False

    client = _get_client(api_key)
    if client is None:
        return False

    cfg = _image_config()

    import time as _time
    for attempt in range(3):
        try:
            response = _gc(client, model=model, contents=full_prompt, config=cfg
            )
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
        resp = _gc(client, model=model, contents=contents)
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
        response = _gc(client, model=model, contents=contents)
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
    if client is None and ENGINE != "agy":
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
        response = _gc(client, model=model, contents=instruction)
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
    if client is None and ENGINE != "agy":
        return {}
    try:
        resp = _gc(client, model=model, contents=director_instruction)
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


def run_json_director(instruction: str, api_key: Optional[str] = None,
                      model: str = CAPTION_MODEL) -> dict:
    """Run a text prompt whose contract is 'return STRICT JSON', and parse it.

    Separate from run_scene_director because that one enforces the v1 contract:
    it requires a "scene" key and discards everything else, so a v2 element
    table came back as {} and the plate pipeline reported "output was not an
    element table" without ever having looked at what the model actually said.
    """
    client = _get_client(api_key)
    if client is None and ENGINE != "agy":
        return {}
    try:
        resp = _gc(client, model=model, contents=instruction)
        text = "".join(
            p.text
            for c in getattr(resp, "candidates", []) or []
            for p in getattr(c.content, "parts", []) or []
            if getattr(p, "text", None)
        )
        data = _loads_first_json(text)
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        print(f"[!] json director failed: {e}", file=sys.stderr)
        return {}


def _probe(client, image_path, prompt: str, model: str = CAPTION_MODEL):
    """Ask the vision model one question about a rendered image.

    Returns the answer text, or None when the probe COULD NOT RUN (no client,
    missing file, API error). None and "" mean different things: "" is a genuine
    empty answer, None means we learned nothing. Conflating them made a hiccup
    look like a failed render, which over 144 chapters would burn the whole
    retry budget re-rendering images that were fine.
    """
    if ENGINE == "agy":
        # The gates were the last thing still billed. Leaving them on the API
        # meant every render was measured on a paid call, which is how a
        # project that had moved its pictures to a free quota still hit a
        # monthly spending cap.
        import agy_text
        try:
            return agy_text.ask_about_image(image_path, prompt)
        except Exception as e:  # noqa: BLE001
            print(f"[!] agy probe failed: {str(e)[:150]}", file=sys.stderr)
            return None

    try:
        from google.genai import types
        img = Path(image_path)
        if client is None or not (img.exists() and img.stat().st_size > 0):
            return None
        contents = [prompt, types.Part.from_bytes(data=img.read_bytes(),
                                                  mime_type="image/png")]
        resp = _gc(client, model=model, contents=contents)
        return "".join(
            part.text
            for cand in getattr(resp, "candidates", []) or []
            for part in getattr(cand.content, "parts", []) or []
            if getattr(part, "text", None)
        )
    except Exception as e:  # noqa: BLE001
        print(f"[!] probe failed: {e}", file=sys.stderr)
        return None


def check_numbers(client, image_path, required: list[tuple[str, str]]) -> dict:
    """Verify each painted numeral, PER CARRIER, and reject strays.

    `required` is [(paint_string, carrier_description)].

    Deliberately not a global "list every numeral" probe: that cannot tell the
    milepost from a decoy clock, so a render that paints every required digit
    onto the WRONG object scores 100% while teaching nothing. Asking per
    carrier binds the numeral to the thing it quantifies.

    Matching is EXACT. Fuzzy matching on numerals is actively harmful — a
    render that paints '48' for '4-8' would pass, and a wrong number burned
    into a study image out-competes the correct legend at recall.

    Returns {"probed": bool, "missing": [...], "wrong": [(want, got)],
    "extra": [...]}. When `probed` is False nothing was measured, and the
    caller must NOT treat that as a failed render.
    """
    out = {"probed": False, "missing": [], "wrong": [], "extra": []}
    for paint, carrier in required:
        got = _probe(client, image_path,
                     f"Look only at the {carrier} in this image. What digit-string, "
                     f"if any, is written on it? Answer with the characters exactly "
                     f"as they appear and nothing else, or the single word NONE.")
        if got is None:
            return out
        out["probed"] = True
        got = got.strip().strip('".\'')
        if not got or got.upper() == "NONE":
            out["missing"].append(paint)
        elif got != paint:
            out["wrong"].append((paint, got))

    # Any other numeral competes with the load-bearing ones and muddies recall.
    seen = _probe(client, image_path,
                  "List every numeral or digit-string visible anywhere in this "
                  "image, verbatim, one per line. If there are none, answer NONE.")
    if seen is not None:
        wanted = {p for p, _ in required}
        for line in seen.splitlines():
            tok = line.strip().strip('-•".\'')
            if tok and tok.upper() != "NONE" and any(c.isdigit() for c in tok):
                if tok not in wanted:
                    out["extra"].append(tok)
    return out


def check_text_strings(client, image_path, required: list[str]) -> tuple[bool, list]:
    """Which required strings are NOT legible. Returns (probed, missing).

    Exact then case-folded only. No Levenshtein: a fuzzy match would let the
    legend quote a clean string the picture does not actually contain, sending
    the reader hunting for text that is not there.
    """
    if not required:
        return True, []
    seen = _probe(client, image_path,
                  "List every readable string of letters in this image, verbatim, "
                  "one per line, each in double quotes. Do not interpret or "
                  "translate. If unsure of a string, omit it.")
    if seen is None:
        return False, []
    found = {s.strip().strip('"') for s in seen.splitlines() if s.strip()}
    folded = {s.casefold() for s in found}
    return True, [r for r in required
                  if r not in found and r.casefold() not in folded]


def check_stray_numerals(client, image_path) -> tuple[bool, list]:
    """Digits painted anywhere other than the coloured plates. (probed, strays)

    Composite mode asks for BLANK chroma plates and letters the numbers itself,
    so check_numbers does not run — and nothing was left watching for numerals
    the illustrator painted anyway. It does: the verified chapter-28 render has
    two people holding placards reading "70%" and "40-60%", each a duplicate of
    a number the compositor was about to place properly. A reader cannot tell
    which of the two to trust.

    Returns ([], probed=True) when the frame is clean.
    """
    ans = _probe(client, image_path,
                 "Ignore any solid bright green, magenta or cyan rectangles in "
                 "this image. Apart from those, list every NUMBER visible "
                 "anywhere in the picture — on signs, boards, labels, placards, "
                 "clothing, anywhere — one per line, verbatim. Include "
                 "percentages, ranges and fractions. If there are none, reply "
                 "exactly NONE.")
    if ans is None:
        return False, []
    body = ans.strip()
    if body.upper().startswith("NONE"):
        return True, []
    return True, [ln.strip().strip('"').strip("-• ")
                  for ln in body.splitlines() if ln.strip()][:12]


def check_composition(client, image_path) -> dict:
    """Three coarse structural questions that answer the 'no linkage' complaint.

    Per-object presence probes read ~100% over a cluttered cartoon regardless of
    quality; these ask about the frame as a whole, which a vision model answers
    far more reliably. A key is absent when its probe could not run.

    DIAGNOSIS NOTE for `figures_even` (defect #3 in SKETCHY_V2_STATUS.md):
    This probe failed on every try in the chapter 28 run, consistently reporting
    one figure dominating. Either the prompt is not holding the same-scale rule
    OR the probe is miscalibrated. The raw answer is now logged alongside the
    boolean so whoever can view the actual render can determine which side is
    wrong. If the probe is miscalibrated (e.g., it interprets "dominating" as
    visual salience rather than size), the prompt may need rewording.
    """
    ask = {
        "anchor_is_object": "Is the single largest object in this image an "
                            "inanimate structure rather than a person? Answer YES or NO.",
        # `figures_even` uses an extended prompt to get diagnostic detail
        "figures_even": "Look at the human figures in this image. (1) Are they all "
                        "roughly the same size? (2) Is there one figure that is clearly "
                        "larger than the others? If so, describe it briefly. Answer in "
                        "the format: 'YES all similar' or 'NO, the <description> is larger'.",
        "frame_filled": "Is this image filled with content all the way to all four "
                        "edges, with no blank margin and no darkened vignette? "
                        "Answer YES or NO.",
    }
    out = {}
    raw = {}  # store raw answers for diagnosis
    for k, q in ask.items():
        ans = _probe(client, image_path, q)
        if ans is not None:
            raw[k] = ans.strip()
            out[k] = ans.strip().upper().startswith("YES")
    # log figures_even raw answer for diagnosing defect #3
    if "figures_even" in raw and not out.get("figures_even"):
        print(f"[DIAG] figures_even probe returned NO: {raw['figures_even']!r}",
              file=sys.stderr)
    out["_raw"] = raw  # include raw answers for report inspection
    # Say how many probes actually ANSWERED. The caller scores a missing key as
    # "no failure", so a probe outage used to read as a flawless frame: every
    # key absent, zero failures counted, score 0, ship on the first try. A
    # render nobody could look at must not be indistinguishable from a render
    # that passed.
    out["_probed"] = len(raw)
    out["_asked"] = len(ask)
    return out


def check_mnemonic(client, image_path, table: dict | None = None) -> dict:
    """The four questions the owner actually judges on, and the score never asked.

    The retry score is `missing strings + composition failures`. Nothing in it
    can tell a scene from a diagram, so across four drafts the ladder picks by
    a measure unrelated to quality — which is why plate quality has varied run
    to run with no visible cause.

    Every one of these is a defect the owner named, in his words:
      - "the rail does not block the postman, he is standing behind it putting
        the mail in without any trouble" — the ACTION was specified in the
        prompt and simply not drawn, and nothing checked
      - "so many labels went into the picture, this was supposed to be banned"
      - "why are there pigeons racing and empty stairs inside an office?"
      - a photocopier lettered DUAL COPIER/SCANNER on an SSRI plate, which
        teaches that SSRIs are dual-reuptake agents

    Deliberately NOT folded into the retry score yet. The stray-numeral count
    went 15 -> 0 -> 1 -> 30 across four tries when it was scored, and a noisy
    probe driving retries burns the budget re-rendering good frames. Calibrate
    against the eleven audited plates first; report until then.
    """
    els = [e for e in ((table or {}).get("elements") or [])
           if (e.get("action") or "").strip()]
    out: dict = {}

    if els:
        # Batched: one question listing the actions, rather than one probe per
        # element. Per-object presence probes read ~100% over a cluttered
        # cartoon, and thirty round trips per rung is not affordable.
        lines = "\n".join(
            f"{i}. {(e.get('actor') or 'someone')} {e['action']}"
            for i, e in enumerate(els[:20], 1))
        ans = _probe(client, image_path,
                     "Below is a list of things that are each supposed to be "
                     "VISIBLY HAPPENING in this picture — not merely present, "
                     "but actually taking place, with the person doing it.\n\n"
                     f"{lines}\n\n"
                     "For each number answer on its own line, exactly: "
                     "'<n>: HAPPENING' or '<n>: NOT'. Answer NOT when the "
                     "objects are there but the action is not being performed.")
        if ans is not None:
            done = len(re.findall(r"^\s*\d+\s*:\s*HAPPENING", ans, re.M | re.I))
            out["actions_drawn"] = done
            out["actions_asked"] = min(len(els), 20)
            out["_actions_raw"] = ans.strip()[:800]

    ans = _probe(client, image_path,
                 "Count the ANNOTATIONS in this picture: any word, phrase or "
                 "number joined to an object by a drawn line, arrow, bracket or "
                 "pointer, or floating over the artwork rather than painted on "
                 "a surface that belongs to the scene. A shop sign, a poster, a "
                 "price board or lettering on a machine is NOT an annotation. "
                 "Answer with a single number and nothing else.")
    if ans is not None:
        m = re.search(r"\d+", ans)
        out["callouts"] = int(m.group(0)) if m else 0
        out["_callouts_raw"] = ans.strip()[:200]

    # BELONGING, NOT USE. An early version asked for objects "nobody is using
    # and that this place would not contain", and the owner caught the flaw:
    # the photocopier is untouched and is still one of the most important
    # things on an SSRI plate. Whether a prop is being USED is a different
    # question, already answered by actions_drawn above; this one asks only
    # whether the object has a REASON to be in the frame — either the venue
    # would hold it anyway, or it carries a fact. Both lists are in hand, so
    # the probe is told them rather than left to guess.
    place = ((table or {}).get("venue") or {}).get("place", "this place")
    carries = [scrub for scrub in
               ((e.get("prop") or "").strip() for e in
                ((table or {}).get("elements") or [])) if scrub][:24]
    known = "\n".join(f"- {c[:70]}" for c in carries)
    ans = _probe(client, image_path,
                 f"This picture shows: {place[:200]}\n\n"
                 + (f"These objects are deliberate and belong, whether or not "
                    f"anyone is touching them:\n{known}\n\n" if known else "")
                 + "List every OTHER object in the picture that a real version "
                   "of this place would not plausibly contain. Judge only "
                   "whether it belongs here — an object nobody is touching is "
                   "fine if the place would have one. One short name per line, "
                   "nothing else. Write NONE if there are none.")
    if ans is not None:
        items = [ln.strip(" -•\t") for ln in ans.splitlines() if ln.strip()]
        items = [i for i in items if i.upper() != "NONE" and len(i) < 60]
        out["out_of_place"] = items[:12]
        out["out_of_place_count"] = len(items)

    return out


_FRAME_SCHEMA = {
    "type": "object",
    "required": ["anchor_is_object", "figures_even", "frame_filled",
                 "callouts", "out_of_place", "actions_not_drawn"],
    "properties": {
        "anchor_is_object": {"type": "boolean"},
        "figures_even": {"type": "boolean"},
        "largest_figure": {"type": "string"},
        "frame_filled": {"type": "boolean"},
        "callouts": {"type": "integer"},
        "out_of_place": {"type": "array", "items": {"type": "string"}},
        "actions_not_drawn": {"type": "array", "items": {"type": "integer"}},
    },
}


def check_frame(client, image_path, table: dict | None = None) -> tuple[dict, dict]:
    """Every question about a finished frame, in ONE model turn under agy.

    Returns (composition, mnemonic) in the shapes the two separate checks
    already return, so the caller does not care which route ran.

    Batching is not an optimisation here, it is what makes the free route
    usable at all. Measured on one plate: check_composition 185s and
    check_mnemonic 390s as separate agy turns, because each question is a
    whole agent round trip. That is ten minutes of gates per rung and forty
    per plate, which across 144 chapters is not a pipeline. One turn answering
    all six costs about a minute.
    """
    if ENGINE != "agy":
        return (check_composition(client, image_path),
                check_mnemonic(client, image_path, table))

    import agy_text
    els = [e for e in ((table or {}).get("elements") or [])
           if (e.get("action") or "").strip()][:20]
    acts = "\n".join(f"{i}. {(e.get('actor') or 'someone')} {e['action']}"
                     for i, e in enumerate(els, 1))
    place = ((table or {}).get("venue") or {}).get("place", "")
    props = [(e.get("prop") or "").strip()
             for e in ((table or {}).get("elements") or []) if e.get("prop")][:24]

    q = f"""Answer these six questions about the picture. Return JSON only.

1. anchor_is_object — is the single largest thing in the frame an inanimate
   structure rather than a person?
2. figures_even — are the human figures all roughly the same size? false if one
   is clearly larger; name it in largest_figure.
3. frame_filled — is the picture filled to all four edges, no blank margin and
   no darkened vignette?
4. callouts — how many ANNOTATIONS are there: a word, phrase or number joined to
   an object by a drawn line, arrow, bracket or pointer, or floating over the
   artwork rather than painted on a surface belonging to the scene. A shop sign,
   a poster, a price board or lettering on a machine is NOT an annotation.
5. out_of_place — {"this picture shows: " + place[:160] + ". " if place else ""}\
which objects would a real version of this place not plausibly contain? Judge
   only whether the object BELONGS; one that nobody is touching is fine if the
   place would have one. These are deliberate and belong:
{chr(10).join("     - " + p[:70] for p in props) if props else "     (none listed)"}
6. actions_not_drawn — each numbered item below is supposed to be VISIBLY
   HAPPENING, not merely present. List the numbers that are NOT happening —
   where the objects are there but nobody is performing the action.
{acts if acts else "     (none listed)"}
"""
    data = agy_text.ask_about_image_json(image_path, q, _FRAME_SCHEMA)
    if data is None:
        return {"_probed": 0, "_asked": 3, "_raw": {}}, {}

    comp = {"anchor_is_object": bool(data.get("anchor_is_object")),
            "figures_even": bool(data.get("figures_even")),
            "frame_filled": bool(data.get("frame_filled")),
            "_probed": 3, "_asked": 3,
            "_raw": {"largest_figure": str(data.get("largest_figure") or "")}}

    not_drawn = [n for n in (data.get("actions_not_drawn") or [])
                 if isinstance(n, int)]
    oop = [str(x)[:60] for x in (data.get("out_of_place") or [])][:12]
    mn = {"callouts": int(data.get("callouts") or 0),
          "out_of_place": oop, "out_of_place_count": len(oop)}
    if els:
        mn["actions_asked"] = len(els)
        mn["actions_drawn"] = max(0, len(els) - len(not_drawn))
        mn["_actions_missing"] = not_drawn
    return comp, mn


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
        resp = _gc(client, model=model, contents=contents)
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
    text_policy: str = "mnemonic",
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

    # Image prompt = visual STYLE spec + the clean SCENE.
    # The old trailing "ABSOLUTE: no written words anywhere in the image" clause is
    # gone: it contradicted the style block's own request for 10-18 legible signs,
    # so the model received both instructions at once and split the difference at
    # random. Which strings are load-bearing is now stated positively, by the
    # scene itself and by the TEXT TO PAINT block the v2 path builds.
    base_image_prompt = (
        style_prefix()
        + "\n\nSCENE TO ILLUSTRATE — draw EXACTLY this, as one cohesive scene:\n"
        + scene
    )

    client = _get_client(api_key) if required_items else None
    tries = max(1, max_image_tries) if required_items else 1
    image_prompt = base_image_prompt
    best_bytes = None
    best_missing = None  # None = not yet measured

    for attempt in range(tries):
        if not generate_image(image_prompt, output_path, api_key=api_key, phase=phase,
                              text_policy=text_policy):
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
