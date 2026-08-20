#!/usr/bin/env python3
"""
daily_study.py — generate all artifacts for one study day.

Modes:
    --day N             Generate day N (1-indexed)
    --bulk              Generate ALL un-released days 1..120
    --resume            Skip days that already have a GitHub Release

For each day, uses the existing 'Board-Exam study material' NotebookLM
notebook (which contains DSM, Dulcan, Stahl×2, syllabus). The script
fires off these artifacts in parallel on Google's servers:
    1. Audio podcast (deep-dive Hebrew)
    2. Video (whiteboard style, Hebrew)
    3. Study guide (Markdown report, Hebrew)
    4. Flashcards (JSON)
    5. Quiz (JSON)
    6. Infographic per aspect (sketch-note style, Hebrew) — 1 to N

All prompts inject the day's symbols_needed from symbols.json so the
visual mnemonics stay consistent across artifacts and days.

Outputs: GitHub Release `study-{NNN}-{slug}` with all artifacts as assets.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Gemini-based image generation (Sketchymedical style) — replaces NotebookLM infographics
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from gemini_image import generate_image_with_caption as gemini_generate
    from gemini_image import propose_anchor_theme, generate_sketchy_directed
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

REPO_ROOT = Path(__file__).resolve().parent.parent

# Default IDs (override via env)
# v2 notebook, rebuilt 2026-07-23 in the current Google account after the account
# switch orphaned the original ("157ae88a…" belonged to the old account → RPC
# GET_NOTEBOOK failed). Holds the 6 course textbooks (comprehensive omitted).
NOTEBOOK_ID = os.environ.get(
    "BOARD_STUDY_NOTEBOOK_ID",
    "17546075-8a85-4998-96ff-8619238fc8a4",  # Board-Exam study material (v2)
)
GITHUB_REPO = os.environ.get("GH_REPO", "tnvsh0/psychiatry-board-study")

NOTEBOOKLM_BIN = os.environ.get("NOTEBOOKLM_BIN", "/opt/venv/bin/notebooklm")
# Fall back to bare 'notebooklm' (in PATH) if the absolute default doesn't exist
# — lets the script run on Windows / non-VM envs without setup.
if not Path(NOTEBOOKLM_BIN).exists():
    NOTEBOOKLM_BIN = "notebooklm"
WAIT_INDEX_S = 60
SCOPE_INDEX_WAIT_S = 60  # wait for per-aspect scope sources to index before scoped infographic gen
POLL_INTERVAL_S = 60
ARTIFACT_MAX_WAIT_S = 60 * 60  # 60 min per batch (audio --length long is slower)

# Source PDFs uploaded to each per-day notebook so NotebookLM can cite page
# numbers in the study guide. Configurable via env var so the path differs
# between VM (/opt/sources) and local dev (anything you choose).
SOURCE_PDF_DIR = Path(os.environ.get("SOURCE_PDF_DIR", "/opt/sources"))
SOURCE_PDF_NAMES = [
    "dsm-5-tr.pdf",
    "dulcan.pdf",
    "stahl-essential.pdf",
    "stahl-pediatric.pdf",
    "kaplan-sadock-synopsis.pdf",
    "syllabus.pdf",
    # kaplan-sadock-comprehensive.pdf intentionally omitted — 352 MB,
    # exceeds practical NotebookLM upload time for daily generation.
    # Add it manually to the main notebook if you want comprehensive coverage.
]

# INVARIANT: SOURCE_PDF_NAMES and BOOKS_DIRECTIVE must describe the same books.
# A book named in a prompt but missing from the notebook does not produce an
# error — NotebookLM invents plausible page numbers for it. That is exactly what
# happened before "stahl-pediatric.pdf" was actually added: the day-16 study
# guide cites "Stahl Pediatric pp. 600-612" in a book that is 525 pages long.
BOOKS_DIRECTIVE = """המקורות במחברת, לפי סדר עדיפות:
1. **Dulcan's Textbook of Child and Adolescent Psychiatry 3e** — ספר הליבה. ברירת המחדל לכל נושא קליני בילדים ונוער.
2. **DSM-5-TR** — קריטריונים אבחנתיים מדויקים.
3. **Stahl's Essential Psychopharmacology 5e** — מנגנון נוירוביולוגי, קולטנים, מסלולים.
4. **Stahl's Prescriber's Guide — Children and Adolescents** — מינון, טיטרציה, תופעות לוואי וניטור ספציפיים לילדים ונוער. מונוגרפיות לפי תרופה (33 תרופות, 525 עמ') — רלוונטי רק כשהנושא כולל תרופה שמופיעה בו.
5. **הסילבוס הרשמי של המועצה המדעית** — מגדיר מה נדרש לבחינה.
6. **Kaplan & Sadock's Synopsis** — מקור השלמה בלבד. השתמש בו אך ורק כאשר נושא שנדרש לבחינה בילדים ונוער חסר ב-Dulcan (למשל הפרעות אישיות, שאין להן פרק ב-Dulcan). אל תייבא ממנו תוכן של פסיכיאטריה של מבוגרים שאינו רלוונטי לילדים ונוער (דמנציה/אלצהיימר, פסיכיאטריה גריאטרית, תפקוד מיני של מבוגרים וכו').

כללי ציטוט מחייבים:
- אחרי כל טענה משמעותית ציין עמוד מקור בסוגריים: "(DSM p.68)", "(Dulcan p.173)", "(Stahl C&A p.301)".
- ל-Synopsis **אין מספרי עמודים** במהדורה שבמחברת. צטט אותו לפי פרק בלבד: "(Synopsis ch.19 Personality Disorders)". אל תמציא לו מספר עמוד.
- אל תמציא עמודים באף מקור. אם לא מצאת — כתוב "(מקור: הקשר רחב)"."""


def slugify(text: str) -> str:
    """Make a filesystem/URL-friendly slug from English text."""
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-")
    return text.lower()[:60]


def load_schedule():
    return json.loads((REPO_ROOT / "schedule" / "schedule.json").read_text(encoding="utf-8"))


def load_symbols():
    return json.loads((REPO_ROOT / "schedule" / "symbols.json").read_text(encoding="utf-8"))


def _all_symbol_pools(symbols_data):
    """Flatten every symbol category into one {key: entry} dict, including the
    new epidemiology_meta and meta_systems categories."""
    pools = {}
    for cat in ("scenes", "ions", "symbols", "drugs", "epidemiology_meta", "meta_systems"):
        for k, v in symbols_data.get(cat, {}).items():
            if k.startswith("_"):  # skip _README / _BATCH markers
                continue
            pools[k] = v
    return pools


# Inline aliases so a symbol is pulled when its CONCEPT appears in the facts
# under a different word than its key (e.g. facts say "dopamine", key is DOPA_MINE).
# Extend as needed. Keeps each image isolated to genuinely-relevant symbols.
_SYMBOL_ALIASES = {
    "DOPA_MINE": ["dopamine", "dopaminergic", "DA "],
    "prefrontal_cortex": ["prefrontal", "PFC", "frontal"],
    "DAT": ["dopamine transporter"],
    "NET": ["norepinephrine transporter", "noradrenaline transporter"],
}


def _symbols_for_aspect(symbol_keys, aspect, symbols_data):
    """Symbols EXPLICITLY tagged for this aspect (from the day's symbols_needed).
    Untagged symbols are NOT included indiscriminately anymore — that caused
    cross-aspect bleeding (e.g. DOPA_MINE leaking into the criteria image).
    Relevance now comes from the facts (see _symbols_named_in_facts) + tags."""
    pools = _all_symbol_pools(symbols_data)
    relevant = []
    for key in symbol_keys:
        item = pools.get(key)
        if not isinstance(item, dict):
            continue
        tags = item.get("aspects")
        if tags and (aspect in tags or "all" in tags):
            relevant.append(key)
    return relevant


# ── Prompt builders ─────────────────────────────────────────────────────────
def collect_symbol_descriptions(symbol_keys, symbols_data):
    """Pull short Hebrew descriptions for the symbols used today."""
    lines = []
    pools = _all_symbol_pools(symbols_data)
    for key in symbol_keys:
        item = pools.get(key)
        if not isinstance(item, dict):
            continue
        he = item.get("hebrew_explanation", "")
        if he:
            lines.append(f"- {key}: {he}")
    return "\n".join(lines) if lines else "(אין סמלים ייעודיים ליום זה)"


def build_symbol_legend(day, symbols_data):
    """Build a symbol legend block for today — the visual representation of each
    concept relevant to this day. Each image (and the video) renders these as
    independent scenes; there is NO requirement that images of the same day
    look like each other. Only the symbol identity is shared, not the framing."""
    symbol_keys = day.get("symbols_needed", [])

    pools = _all_symbol_pools(symbols_data)
    parts = []
    for key in symbol_keys[:8]:
        item = pools.get(key)
        if isinstance(item, dict):
            sym = item.get("symbol", "") or item.get("scene_name", "")
            if sym:
                parts.append(f"  - {key}: {sym}")

    if not parts:
        return "(no specific symbols for this day — use generic clinical visuals)"

    return ("Symbol legend — when any of these concepts appears in the content, "
            "depict it using the corresponding visual representation:\n"
            + "\n".join(parts))


def build_caption_legend(day, symbols_data):
    """Richer legend for the narrative caption generator — includes mnemonic
    link and Hebrew explanation per symbol so the caption can explain WHY
    each symbol represents what it does."""
    symbol_keys = day.get("symbols_needed", [])

    pools = _all_symbol_pools(symbols_data)
    blocks = []
    for key in symbol_keys[:8]:
        item = pools.get(key)
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol", "") or item.get("scene_name", "")
        mnemonic = item.get("mnemonic_link", "")
        he = item.get("hebrew_explanation", "")
        clinical = item.get("function_depiction", "")
        block = [f"- {key}:"]
        if sym:
            block.append(f"    visual: {sym}")
        if mnemonic:
            block.append(f"    hook: {mnemonic}")
        if clinical:
            block.append(f"    clinical: {clinical}")
        if he:
            block.append(f"    hebrew: {he}")
        blocks.append("\n".join(block))
    return "\n".join(blocks) if blocks else "(no specific symbol entries for this day)"


BILINGUAL_DIRECTIVE = """
LANGUAGE — bilingual (Hebrew-primary, like Israeli clinicians):
- Hebrew narration / labels for explanation, concepts, and continuous prose.
- KEEP IN ENGLISH: drug GENERIC names (Methylphenidate, Lorazepam, Clozapine,
  never brand names like Ritalin/Ativan), receptor names (D2, 5-HT2A, NMDA,
  α1, β2, GABA-A, M1), clinical acronyms (EPS, NMS, SSRI, SNRI, AUD, OCD),
  the English mnemonics from the symbol legend (Mad as a Hatter, Lucky 7, 42,
  Two of Hearts, Olympic athlete, etc.), drug classes (TCA, MAOI), and ratings
  (DSM-5-TR, ICD).
- Hebrew mnemonics from the legend stay in Hebrew (אצית for ACh, דת for DAT,
  קוטב צפוני for bipolar, דובי for dopamine, בית-ב' for GABA-B, בית-H for histamine).
"""


def build_clinical_context(day):
    """Context for CLINICAL artifacts (audio, video, study guide, flashcards, quiz).

    Deliberately omits the visual symbol legend — those are mnemonic aids
    for the IMAGES only. Clinical content must come from the textbook PDFs
    (see BOOKS_DIRECTIVE), NOT from the cartoon symbols.
    Mentioning the cartoon symbols here causes NotebookLM to generate
    flashcards/quiz items about the visual mnemonics ("what does the ninja
    mask represent?") instead of the clinical content ("at what age should
    babbling appear?")."""
    title_he = day.get("title_he", day.get("title_en", ""))
    aspects = day.get("aspects", [])
    focus = day.get("prompt_focus", "")

    return f"""נושא היום: {title_he}

מיקוד מיוחד:
{focus}

היבטים לכיסוי:
{', '.join(aspects)}

{BILINGUAL_DIRECTIVE}

הוראות חובה — תוכן קליני בלבד:
- ההוראות הללו מיועדות לחומר לימודי קליני. **אל תכתוב על הסמלים החזותיים/הציורים/האיורים** (אלה נועדו אך ורק לתמונות הליווי — הם לא נושא הלמידה).
- כל פיסת תוכן חייבת להישען על המקורות במחברת.

{BOOKS_DIRECTIVE}

- מטרת הלימוד: מתמחה בפסיכיאטריה של הילד והמתבגר לקראת בחינת שלב ב' — חייב להיות תוכן בחינתי, לא מנמוניקה ויזואלית.
- אל תמציא — אם המידע חסר במקורות, ציין זאת."""


def build_visual_context(day, symbol_block, legend_block):
    """Context for VISUAL artifacts (sketchy image generation only).

    Includes the full symbol legend because images need the visual
    mnemonics. Clinical accuracy is secondary to memorable iconography here."""
    title_he = day.get("title_he", day.get("title_en", ""))
    aspects = day.get("aspects", [])
    focus = day.get("prompt_focus", "")

    return f"""נושא היום: {title_he}

מיקוד מיוחד:
{focus}

היבטים לכיסוי:
{', '.join(aspects)}

מערכת סמלים ויזואליים עקביים (Sketchymedical-style). בכל פעם שאחד המושגים האלה מופיע — השתמש בסמל הוויזואלי המתאים:
{symbol_block}

{legend_block}

{BILINGUAL_DIRECTIVE}"""


def prompt_audio(day, symbol_block, legend_block):
    base = build_clinical_context(day)
    return base + """

צור פודקאסט deep-dive בעברית — כמה שיותר ארוך ומעמיק (--length long).
שיחה מקיפה בין שני מנחים על הנושא. כיסוי שיטתי + יסודי של *כל* ההיבטים שצוינו,
כולל nuances קלינים, ציטוטים מהמקור, וינייטות קליניות, והפניות לעמודי מקור
שצוינו לעיל. דגש על משמעות קלינית ויישום מעשי. אזכור תרופות בשם גנרי באנגלית
(לא מסחרי).

המנחים יכולים להזכיר בקצרה את המנמוניקה החזותית (תמונת ה-Sketchy של היום)
כעוגן זיכרון — אבל מרבית הפודקאסט חייבת להיות חומר קליני, לא דיון על איורים."""


def prompt_video(day, symbol_block, legend_block):
    base = build_clinical_context(day)
    return base + """

צור סרטון הסבר באורך כ-10-15 דקות, סגנון whiteboard, בעברית.
התוכן חייב להיות חומר לימוד קליני מהמקורות שבמחברת, עם הפניות לעמודים
בעקבות כל טענה מרכזית. כיסוי מקיף של *כל* ההיבטים שצוינו.

הסרטון עומד בפני עצמו ולא חייב להיראות כמו תמונות סטילס אחרות של היום.
ניתן להזכיר בקצרה את הסמלים החזותיים של היום כעוגנים, אבל תוכן הסרטון
חייב להיות קליני ולא דיון בציורים."""


_INFOGRAPHIC_ASPECT_PROMPTS = {
    "dsm_criteria": "קריטריונים DSM-5-TR — רשימה מספרית מסודרת + ספי-זמן + מספר תסמינים נדרש.",
    "epidemiology": "אפידמיולוגיה — שכיחות, היארעות, פירוט לפי מין/גיל, גורמי סיכון.",
    "etiology": "אטיולוגיה ביופסיכוסוציאלית — תרשים גנטיקה + נוירוביולוגיה + סביבה.",
    "treatment_first_line": "טיפול קו ראשון — תרופתי + לא-תרופתי, עם דרגות המלצה.",
    "treatment_second_line": "טיפולי קו שני ומתפתחים — אינדיקציות, יעילות, סיכונים.",
    "treatment_overview": "עץ-טיפול — קו 1 → קו 2 → augmentation → refractory.",
    "drug_mechanism": "מנגנון פעולה — קולטנים, עוצמות קישור, downstream effects.",
    "drug_side_effects": "תופעות לוואי + מנגנון של כל אחת.",
    "drug_monitoring": "ניטור — בדיקות לפני התחלה ובמהלך, תדירות וערכים.",
    "drug_practical": "פרקטיקה — מינונים, התחלה, titration, אינטראקציות.",
    "drug_class_overview": "סקירה משווה של בני המשפחה — t½, מטבוליזם, אינדיקציות.",
    "indications": "אינדיקציות (FDA + off-label) ורמת הראיה.",
    "side_effects_class": "תופעות לוואי משותפות למשפחת התרופות.",
    "monitoring": "ניטור — מה, מתי, איזה ערכים.",
    "practical_use": "פרקטיקה — בחירה בין בני המשפחה, אופן ההתחלה.",
    "assessment_tools": "כלי הערכה — שאלונים, סולמות (Conners, ASEBA, CGI, וכו').",
    "medical_mimics": "אבחנה מבדלת רפואית — מצבים 'Due to Another Medical Condition / Substance'.",
    "core_concept": "קונספט הליבה — מה זה ולמה זה חשוב.",
    "anatomy_or_structure": "אנטומיה/מבנה — איפה ממוקם, איך נראה.",
    "function": "תפקוד — מה זה עושה, איך זה משפיע.",
    "clinical_relevance": "רלוונטיות קלינית — איפה זה פוגש מטופלים.",
    "history_origin": "היסטוריה ומקור הטכניקה — מי פיתח, מתי, איפה.",
    "core_principles": "עקרונות יסוד של השיטה.",
    "practice_method": "מבנה הסשנים בפועל וטכניקות.",
    "evidence": "עדויות אמפיריות — RCTs, meta-analyses, אינדיקציות מאושרות.",
    "recognition": "סימנים אדומים לזיהוי מוקדם.",
    "diagnostic_criteria": "קריטריונים דיאגנוסטיים מדויקים.",
    "differential": "אבחנה מבדלת — מה דומה, איך מבדילים.",
    "emergency_management": "ניהול חירומי — גישה, תרופות, ניטור.",
}


def prompt_notebooklm_infographic(day, aspect):
    """LEGACY whole-notebook infographic prompt (fallback when no per-aspect
    scope source could be built). Prefer prompt_infographic_scoped, which is fed
    only this aspect's facts.json slice and run with `generate infographic -s`."""
    base = build_clinical_context(day)
    aspect_he = _INFOGRAPHIC_ASPECT_PROMPTS.get(aspect, f"התמקד בהיבט: {aspect}")
    return base + f"""

צור אינפוגרפיקה אינפורמטיבית-מבנית (לא בסגנון Sketchy, לא סצנה — אלא תרשים-מידע).
היבט המוקד: {aspect_he}

מבנה דרוש:
- כותרת ראשית של ההיבט
- מספרים, טבלאות, רשימות, תרשימים — מידע קליני בדחיסות גבוהה
- שמות תרופות גנריים באנגלית; שמות-משתנים קליניים באנגלית; הסבר בעברית
- עמודי מקור בסוגריים אחרי כל טענה מרכזית
- מקסימום אינפורמטיבי, מינימום קישוט

איסור: ללא דמויות-קומיקס, ללא סצנות, ללא סמלי-מנמוניקה ויזואליים. זו אינפוגרפיקה רגילה."""


def _render_scope_md(day, aspect, slice_):
    """Render one aspect's facts.json slice as a small, focused Markdown source.
    Uploaded as its OWN NotebookLM source so the aspect's infographic can be
    scoped to ONLY it (`generate infographic -s <id>`) — the same per-aspect
    isolation the Sketchy images use, now applied to the infographics."""
    title_he = day.get("title_he", day.get("title_en", ""))
    body = _facts_block_for_aspect(aspect, slice_)
    return (
        f"# {title_he} — היבט: {aspect}\n\n"
        f"מקור ממוקד להיבט **{aspect}** בלבד (slice מתוך facts.json של היום). "
        f"האינפוגרפיקה של היבט זה מסתמכת אך ורק על מקור זה — אין לכלול היבטים אחרים.\n\n"
        f"## עובדות הליבה\n\n```json\n{body}\n```\n"
    )


def prompt_infographic_scoped(day, aspect, aspect_facts):
    """Per-aspect infographic prompt, used with `generate infographic -s <scope>`
    so the infographic is built ONLY from this aspect's facts slice — no
    cross-aspect bleeding / repetition. Mirrors the Sketchy per-aspect isolation."""
    title_he = day.get("title_he", day.get("title_en", ""))
    aspect_he = _INFOGRAPHIC_ASPECT_PROMPTS.get(aspect, f"התמקד בהיבט: {aspect}")
    facts_text = _facts_block_for_aspect(aspect, aspect_facts)
    return f"""צור אינפוגרפיקה אינפורמטיבית-מבנית (לא בסגנון Sketchy, לא סצנה — תרשים-מידע) על היבט אחד בלבד.

נושא: {title_he}
ההיבט היחיד של אינפוגרפיקה זו: {aspect_he}

הסתמך אך ורק על המקור שצורף (scope) — שמכיל את עובדות ההיבט הזה בלבד. אל תכלול מידע על היבטים אחרים של הנושא (אם זו אפידמיולוגיה — לא אטיולוגיה/טיפול, וכו').

עובדות הליבה לכיסוי:
{facts_text}

מבנה דרוש:
- כותרת ראשית של ההיבט
- מספרים, טבלאות, רשימות, תרשימים — מידע קליני בדחיסות גבוהה, ממוקד בהיבט זה בלבד
- שמות תרופות/קולטנים באנגלית; הסבר בעברית
- מקסימום אינפורמטיבי, מינימום קישוט

{BILINGUAL_DIRECTIVE}

איסור: ללא דמויות-קומיקס, ללא סצנות, ללא סמלי-מנמוניקה ויזואליים, וללא היבטים אחרים. זו אינפוגרפיקה רגילה וממוקדת-היבט."""


SKETCHY_PHILOSOPHY = """A SketchyMedical page is a MUNDANE PLACE whose NAME is a pun on the topic, packed edge to edge, indexed by a numbered legend. It is a search field, not a poster - and it is not a story.

Reverse-engineered from 19 corpus pages and 113 classified legend items:

1. THE VENUE IS THE PUN, AND THE PUN IS PAINTED ON A SIGN.
   SSRIs -> a travel agency lettered "expreSS tRIps". The -azine antipsychotics -> an art
   gallery lettered "STARRY GAZING". Benzodiazepines -> "Ben's Diner". Barbiturates -> a
   BARBershop. Carbamazepine -> the "Carpe Drive-in". Nephrotic syndrome -> a castle
   siege, because the WALL is the basement membrane.
   Never choose a setting for being bizarre, unexpected or mood-matched. The exam cue is
   the chapter name: if the name does not generate the world, the name retrieves nothing.

2. THE SETTING IS BORING; THE OBJECTS ARE STRANGE.
   An office, a diner, a schoolyard, a wine cellar. Nothing floats, no impossible physics.
   A boring container is what makes the anomalies pop.

3. NO PLOT AND NO PROTAGONIST.
   Zero causal links between figures; zero characters whose fate is followed. The dominant
   element is an INANIMATE structure cutting the frame on a diagonal. What binds 25
   strangers into one frame is ONE shared ambient activity - everyone works the shift,
   views the art, besieges the castle. One verb, thirty variations.
   Pairwise relations are allowed, and REQUIRED where the fact's verb is transitive: a man
   barring another from the post-it drawer IS reuptake inhibition; a serpent across the
   ramp IS VMAT blockade. What is banned is a narrative arc.

4. DEPICT CLINICAL STATES LITERALLY - the single largest mechanism, about 41%.
   An anxious coworker is GAD. A shy guy is social anxiety. Sticking out a tongue is
   tardive dyskinesia. NEVER mint a fresh evocative metaphor for an affective state:
   across 113 items there are ZERO. Use, in order: a person exhibiting it, a fixed lexicon
   token, or a famous artwork that already depicts it.

5. TEXT IS THE HIGHEST-DENSITY DEVICE, not a last resort.
   4-15 hand-lettered diegetic signs per page, and on most pages several legend items ARE
   the painted string. Signs are props, never overlays.

6. NUMBERS ARE WRITTEN DIGITS ON OBJECTS WHOSE JOB IS TO BEAR DIGITS.
   "35" branded on the meat cart; "52" and "53" on retired-jersey banners; "CYP-450" on a
   licence plate. There is no digit-shape cipher anywhere in the corpus. Triage hard: 0-2
   painted numbers per page is the norm, and a number with no natural numbered prop is
   dropped, not forced. Every painted numeral carries a short referent tag on the same
   object, or it is just a floating digit.

7. ONE FACT PER FIGURE, AND PACK THE FRAME.
   About 30 facts across 12-25 same-scale figures plus labelled props, with roughly as
   much ordinary non-mnemonic filler as meaningful detail.

8. THE LEXICON IS FIXED AND SHARED.
   A symbol means exactly the same thing on every page it appears, so half the vocabulary
   is already owned before the chapter opens. See schedule/lexicon.json.

EXPLICIT ANTI-PATTERNS - these RUIN memorability and are FORBIDDEN:
X A setting chosen for atmosphere or shock rather than a pun, a literal picture, or a
  spatial mapping.
X A hero the scene revolves around, or a plot the viewer is meant to narrate.
X An invented metaphor for a feeling (a rain cloud for depression, a frightened rabbit
  for anxiety).
X Floating informational labels, callout arrows, chart-like overlays, legend boxes.
X Generous empty space, or a scene meant to be parsed in a few seconds.
X A digit-shape cipher, or an exact figure left out of the picture on principle."""




def _symbols_named_in_facts(aspect_facts, symbols_data):
    """Find constant symbols whose concept is NAMED in this aspect's facts, so
    the symbol follows the drug/receptor wherever it is mentioned (e.g. the
    Amphetamine AMPlifier appears in any treatment image that mentions
    Amphetamine, even if that symbol 'belongs' to another chapter)."""
    if not aspect_facts:
        return []
    import json as _json
    text = aspect_facts if isinstance(aspect_facts, str) else _json.dumps(aspect_facts, ensure_ascii=False)
    text_l = text.lower()
    pools = _all_symbol_pools(symbols_data)
    found = []
    for key, item in pools.items():
        # match the key, normalized forms, inline aliases, and any per-symbol
        # 'aliases' field, against the facts text
        candidates = {key.lower(), key.replace("_", " ").lower(),
                      key.replace("_receptor", "").replace("_", " ").lower()}
        candidates.update(a.lower() for a in _SYMBOL_ALIASES.get(key, []))
        if isinstance(item, dict):
            candidates.update(a.lower() for a in item.get("aliases", []))
        for c in candidates:
            if len(c) >= 3 and c in text_l:
                found.append(key)
                break
    return found


def _compact_symbol_index(symbols_data, exclude=None):
    """One short line per symbol across the WHOLE codex: 'key: short visual'.
    Used as a reference so the model can always reach the canonical symbol for
    ANY drug/receptor/concept that appears in the facts — never a miss."""
    exclude = set(exclude or [])
    pools = _all_symbol_pools(symbols_data)
    lines = []
    for key, item in pools.items():
        if key in exclude or not isinstance(item, dict):
            continue
        sym = (item.get("symbol") or item.get("scene_name") or "").strip()
        if not sym:
            continue
        short = sym if len(sym) <= 90 else sym[:88].rstrip() + "…"
        lines.append(f"- {key}: {short}")
    return "\n".join(lines)


def _aspect_scoped_legend(day, aspect, symbols_data, aspect_facts=None):
    """PRIORITY legend — the symbols most likely relevant to this aspect, in
    full detail. This is NOT the only access the model has (it also gets the
    full index as a lookup); it just highlights the high-confidence ones:
      (a) constant symbols whose concept is NAMED in this aspect's facts
          (with aliases) — the main driver,
      (b) symbols explicitly TAGGED for this aspect,
      (c) epidemiology meta-symbols for epi images,
      (d) the number-peg system (numbers can appear in any aspect)."""
    keys = _symbols_named_in_facts(aspect_facts, symbols_data)
    keys += _symbols_for_aspect(day.get("symbols_needed", []), aspect, symbols_data)
    # Epidemiology drops BOTH the rigid epidemiology_meta markers AND the fixed 0-9
    # number-peg vocabulary. A fixed shape reused across every chapter (starfish=5) is
    # a WEAK discriminating cue ("which scene had the starfish?") and a magnet for a
    # disconnected stats-board. The director now invents a SCENE-SPECIFIC visual for
    # each figure (per the epidemiology STRUCTURE block); other aspects keep the pegs.
    if aspect != "epidemiology":
        keys.append("number_pegs")
    # dedupe, preserve order
    seen = set()
    keys = [k for k in keys if not (k in seen or seen.add(k))]
    pools = _all_symbol_pools(symbols_data)
    blocks = []
    for key in keys:
        item = pools.get(key)
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol", "") or item.get("scene_name", "")
        hook = item.get("mnemonic_link", "")
        b = [f"- {key}:"]
        if sym:
            b.append(f"    visual: {sym}")
        if hook:
            b.append(f"    hook: {hook}")
        blocks.append("\n".join(b))
    return "\n".join(blocks) if blocks else "(no specific symbols for this aspect — invent fitting puns)"


def _facts_block_for_aspect(aspect, aspect_facts):
    """Render the structured facts for this aspect as prompt text."""
    if not aspect_facts:
        return "(facts not available — encode the standard high-yield points for this aspect)"
    if isinstance(aspect_facts, str):
        return aspect_facts
    # dict / list → readable lines
    import json as _json
    return _json.dumps(aspect_facts, ensure_ascii=False, indent=2)


# Aspect-specific structural conventions for the image.
_ASPECT_STRUCTURE = {
    "dsm_criteria": (
        "STRUCTURE — CRITERIA: COMPLETENESS IS THE #1 RULE. The image MUST contain a "
        "DISTINCT, separately findable visual hook for EVERY SINGLE criterion in the "
        "facts below — if the facts list 9 symptoms, the image shows 9 distinct "
        "hooks. NEVER drop, merge, or blur several criteria into one vague action "
        "(e.g. a generic 'reckless driver') — each criterion is its OWN specific "
        "detail the viewer can point to. Cluster a few distinct details onto each of "
        "a few rich characters WHERE IT STAYS LEGIBLE, but completeness beats "
        "cleanliness: if a character cannot clearly carry another distinct detail, "
        "ADD another figure / prop / station rather than omit a criterion. Higher "
        "density is fine here (real Sketchy criteria pages are busy on purpose) — "
        "just keep every hook separated and facing the viewer so none hides. Pick "
        "ONE situation where the symptoms naturally occur as beats of a continuous "
        "activity. WORKED EXAMPLE for ADHD: a chaotic DRIVING TEST. The reckless "
        "driver shows the Hyperactivity/Impulsivity symptoms, but EACH symptom is a "
        "separate visible beat — a smoking floored motor (driven by a motor), a hand "
        "on the horn cutting the queue (can't wait turn), lurching before the green "
        "(acts before thinking), bouncing in the seat (fidgets), climbing out the "
        "window (leaves seat), mouth mid-shout (blurts/talks excessively), etc.; the "
        "distracted pedestrian shows EACH Inattention symptom separately — papers "
        "flying unnoticed (loses things), eyes on a billboard (distracted), "
        "headphones ignoring the whistle (doesn't listen), an unread checklist "
        "(doesn't follow instructions), a tangled bag (poor organization), and so "
        "on — one distinct detail per criterion, ALL present. TWO SETTINGS: the road "
        "forks with a clear sign — 'SCHOOL' and 'HOME' (>=2 settings). THRESHOLDS as "
        "the examiner's CLIPBOARD scoreboard: the tally and >=6/9 (>=5 if 17+), an "
        "age road-sign, a duration timer — via number-pegs. Before finishing, COUNT "
        "the distinct symptom-hooks: it MUST equal the number of criteria in the facts."
    ),
    "diagnostic_criteria": (
        "STRUCTURE — CRITERIA: COMPLETENESS FIRST. Render a DISTINCT, findable visual "
        "hook for EVERY criterion in the facts (N criteria -> N hooks); never drop, "
        "merge, or blur them into a vague action. Cluster a few details per character "
        "where legible, but ADD more figures/props/stations rather than omit any — "
        "density is acceptable, completeness is mandatory. ONE coherent situation "
        "(e.g. a driving test: reckless driver = hyperactivity beats, distracted "
        "pedestrian = inattention beats, road forking to 'SCHOOL'/'HOME' = 2 "
        "settings). Examiner's clipboard-scoreboard shows the tally and the >=N "
        "threshold (pegged). Before finishing, COUNT the hooks — must equal N."
    ),
    "epidemiology": (
        "STRUCTURE — EPIDEMIOLOGY = ONE STORY WHERE EVERY NUMBER RIDES ON SOMETHING "
        "THAT ALREADY BELONGS. The figures are HIGH-YIELD and MUST be visible: "
        "prevalence, sex ratio, persistence/prognosis, heritability, AND the COMORBIDITY "
        "RATE of each comorbid disorder (e.g. ODD ~50%) — do NOT banish these to the "
        "caption. (Only minor sub-figures — an adult-vs-child split, a wide range — may "
        "move to the caption to keep the picture clean.) "
        "THE KEY TO MANY NUMBERS WITHOUT CLUTTER: every number must RIDE ON an element "
        "that is ALREADY in the scene for a story reason — never add a floating object "
        "just to carry a number, and never gather numbers onto a separate stats board. "
        "So: each COMORBIDITY companion is already present (trailing the patient, "
        "bearing that disorder's own symbol) — put ITS rate on that same companion (a "
        "badge / a jersey number / how many of them there are); HERITABILITY rides on "
        "the FAMILY (3 of 4 of them affected ~ 74%, and/or a number on their shared "
        "form); PREVALENCE rides on an entrance POSTER the office already has; SEX RATIO "
        "is simply the count of boys vs girls already playing; PERSISTENCE rides on the "
        "long queue (most are grown-ups, a sign at the exit). "
        "HOW TO REPRESENT EACH NUMBER — two tiers, the director's scene-specific choice: "
        "(A) IF IT CAN BE ENACTED, make the number a SITUATION the viewer SEES: a RATIO "
        "= relative group sizes (2 boys fighting over one toy car beside 1 calm girl = "
        "the ~2:1 sex ratio); a clean PROPORTION = a fraction of a visible group (3 of 4 "
        "of the family affected ~ 75% heritability; half the kids defiant ~ a 50% "
        "comorbidity). When the number IS the staged situation it is strongest of all. "
        "(B) IF IT CANNOT BE ENACTED (an odd percentage), do NOT just write it on a "
        "random prop — that stays arbitrary. Instead SPELL ITS DIGITS with objects whose "
        "SHAPE resembles each digit AND that belong to THIS scene, ideally fused into ONE "
        "meaningful prop. WORKED EXAMPLE (75% at a licensing office): a '5' laid as a "
        "wheel with a '7' above it as the frame = a broken go-kart that 'won't pass its "
        "test'. The digit-objects are chosen PER SCENE (a wheel here, never a fixed "
        "starfish), so they read AS the number, BELONG to the story, and form a "
        "scene-specific memory hook. A plain written number is a LAST resort, only on a "
        "sign/form a real office would actually carry. NEVER a generic reused shape, "
        "NEVER a number floating on a random object, NEVER a stats board. "
        "Keep the meta-concepts scene-native (NO weather-forecaster, NO mandatory "
        "tug-of-war): comorbidity = clingy companions with their symbols; prognosis = "
        "who is still stuck in the crowd / at the exit; heritability = a near-identical "
        "parent and child. ONE event, narratable start to finish, every key figure "
        "findable on something that already belongs to it."
    ),
}










def build_director_instruction(day, symbols_data, aspect, aspect_facts=None,
                               anchor_world=None, anchor_zone=None, half=None):
    """Build the SCENE DIRECTOR instruction (a text-model prompt).

    The director converts this aspect's clinical facts + the symbol vocabulary
    + the name-pun world into (a) a scene description and (b) a numbered KEY
    mapping each visible element to its fact. This mirrors how a real Sketchy
    page is authored: scene brief plus a separate key.

    `half` is accepted and ignored — the epidemiology who/comes_with split is
    gone. It never really existed: build_image_jobs handed BOTH halves the
    identical unsplit facts slice, so the two renders differed only by prose,
    and the branch's own "ZERO TEXT / NO written numerals" block is what
    stripped every figure out of the epidemiology images.
    """
    title_he = day.get("title_he", day.get("title_en", ""))
    scoped_legend = _aspect_scoped_legend(day, aspect, symbols_data, aspect_facts)
    full_index = _compact_symbol_index(symbols_data)
    facts_text = _facts_block_for_aspect(aspect, aspect_facts)
    structure = _ASPECT_STRUCTURE.get(aspect, "")
    # Number handling now lives entirely in sketchy_spec.normalize_number and in
    # the TEXT TO PAINT block. The old NUMBER_RULES shipped a 0-9 digit-shape
    # cipher that has zero instances across the whole corpus, alongside "NEVER
    # just write the numeral" — the precise inverse of what Sketchy does.
    number_rules = ""

    if anchor_world and anchor_zone:
        setting_block = f"""SETTING — SHARED CHAPTER WORLD (a LIGHT linking thread, not heavy sameness):
This chapter's images all live in ONE world: {anchor_world}.
THIS image is set in ONE distinct zone of it: {anchor_zone}.
The shared world is just enough for the viewer to recall the chapter's images
as a connected set. But this zone must be VISUALLY DISTINCT from the chapter's
other zones — different dominant colors, objects and action — so the images are
never confused with each other. Do NOT try to show the whole world or other
aspects here; render only THIS zone and only THIS aspect's facts."""
    else:
        setting_block = """SETTING: invent ONE bizarre, unexpected world with NO literal connection to medicine (casino, medieval joust, pirate cove, space station, candy factory...). Do NOT use a clinical setting (clinic, lecture hall, lab, pharmacy, ward, ER)."""

    return f"""{SKETCHY_PHILOSOPHY}

CHAPTER (grounding only — do NOT cram the whole chapter into this image):
{title_he}

{setting_block}

THIS IMAGE COVERS ONE ASPECT ONLY: {aspect}
Encode ONLY the facts below. Criteria-facts belong only in the criteria image,
epidemiology-facts only in the epidemiology image, etc.

FACTS TO ENCODE (this aspect only):
{facts_text}

PRIORITY SYMBOLS for this aspect (full detail — concepts named in the facts above):
{scoped_legend}

FULL SYMBOL REFERENCE INDEX — this is a LOOKUP TABLE, not a shopping list.
HOW TO USE IT (cross-reference rule):
- For each concept that actually appears in THIS image's FACTS above, check this
  index; if it has a canonical symbol, you MUST use that exact symbol (reusing
  constant symbols is the whole point of the course).
- DO NOT pull a symbol whose concept does NOT appear in this image's facts.
  NEGATIVE EXAMPLE: this is a CRITERIA image; 'dopamine' is NOT a criterion, so
  the DOPA-MINE coal-mine scene MUST NOT appear here (it belongs only in images
  whose facts mention dopamine, e.g. etiology). Likewise no drug symbols in an
  epidemiology image unless a drug is in the epidemiology facts.
- When unsure whether a concept is in the facts: it isn't — leave it out.
{full_index}

{structure}

{number_rules}

YOU ARE THE SCENE DIRECTOR. Do NOT draw. Produce an illustrator's brief + a key.

THE ONE RULE THAT MATTERS MOST — BELONGING (this is the soul of Sketchy):
Real Sketchy works because EVERY element naturally BELONGS to the setting AND
encodes a fact at the same time — nothing is pasted on. In the 'Pie Genies'
bakery, a pie under a glass dome belongs (it's a bakery display) AND encodes
'encapsulated'. A chef's hat belongs AND carries the 'M' protein.

So your job is NOT 'pick a backdrop then place fact-actors in it'. Your job is:
1. CHOOSE A SETTING + EVENT where THIS aspect's facts would HAPPEN ON THEIR OWN.
   The characters' behaviours must be MOTIVATED by the setting, not imposed.
   (Bad: cabbages randomly jumping in a courtroom — jumping has no reason there.
    Good for ADHD criteria: a chaotic DRIVING TEST — a careless driver scraping
    cones = inattention, an impatient driver jumping the light = impulsivity,
    the examiner's scorecard needing 6/9 ticks = the symptom threshold. Each
    behaviour is what people NATURALLY do there, and each also encodes a fact.)
2. Make sure a real STORY/EVENT is unfolding (a test, a heist, a race, a
   feast, a rescue) — coherent and FLOWING, with bizarre/funny/extreme beats so
   it sticks. The viewer should be able to narrate what's happening.
3. Map EACH fact to a thing that genuinely BELONGS in that setting and encodes
   it by visual/phonetic pun. If a fact won't fit the setting naturally, you
   chose the wrong setting — pick a better one. Reuse the EXACT constant symbols
   from the index for any drug/receptor/concept named in the facts.
   DETAIL-PER-FACT, NOT CHARACTER-PER-FACT (strict, both directions):
   - REQUIRED: every fact gets its OWN distinct visual representation — a
     specific detail, prop, feature or action (e.g. 'no eye contact' = a face
     pointedly looking away; 'loses things' = items dropping unnoticed from a
     bag). Do not abstract a list into a vague 'a distracted person' or a mere
     count — each item must be individually findable.
   - FORBIDDEN: a separate CHARACTER (mascot) per fact — that is overload.
     Cluster MANY details onto a FEW characters (one character can carry 5-6
     distinct detail-facts: eyes averted + headphones + dropping items +
     untied lace + glazed stare). A few rich characters, not a crowd of
     single-trait mascots.
4. Numbers: ratios = relative team sizes; small counts (<=4) = countable
   objects; percentages/big numbers = number-peg shapes as a punchline. Never a
   written stat.

CRITICAL — the scene description must be PURELY VISUAL, NO clinical terms. The
illustrator must never see 'insomnia' or 'prevalence' — only the visual (e.g.
'a driver slumped asleep at the wheel, eyes still wide open'). The ONLY words
allowed in the final image are short DIEGETIC labels that are naturally part of
an object AND double as the mnemonic (a sign, a number on a jersey) — list each
in quotes. No explanatory labels, callouts, or floating text.

OUTPUT — STRICT JSON only (no markdown fence):
{{
  "scene": "<purely-visual description (usually 3-5 sentences; but for criteria images make it AS LONG AS NEEDED to spell out a DISTINCT visual for EVERY criterion — never compress several criteria into one phrase): the SETTING, the EVENT unfolding, and each character/object/action and what it is doing within that event (so the illustrator paints a coherent moment, not a collage). Include any short quoted diegetic words. NO clinical terminology.>",
  "key": "<numbered list mapping each visible element to the clinical fact it encodes and WHY (the hook), including which number-peg encodes which figure. For the study caption, not the image.>"
}}"""


def prompt_study_guide(day, symbol_block, legend_block):
    base = build_clinical_context(day)
    return base + """

צור מדריך לימוד מפורט (study guide) בפורמט markdown, בעברית.
מבנה לוגי:
1. מבוא
2. הגדרה / רקע
3. קריטריונים לאבחנה (אם רלוונטי)
4. אפידמיולוגיה
5. אטיולוגיה (ביופסיכוסוציאלי)
6. הצגה קלינית + אבחנה מבדלת
7. גישה טיפולית (תרופתית + לא-תרופתית)
8. ניטור ומעקב
9. מה ייחודי לילדים/נוער
10. נקודות high-yield לבחינה
11. סיכום

חובה: ציון עמוד מקור אחרי כל טענה משמעותית בפורמט "(DSM p.NNN)" / "(Dulcan p.NNN)" וכו'.

איסור: אל תזכיר את תמונות ה-Sketchy או הסמלים הוויזואליים. המדריך הוא חומר קליני בלבד."""


def prompt_flashcards(day, symbol_block, legend_block):
    base = build_clinical_context(day)
    aspects = day.get("aspects", [])
    return base + f"""

צור סט כרטיסיות שינון — שאלה + תשובה — לכיסוי high-yield של היום.
כמות: standard (15-20 כרטיסיות). פורמט: front (שאלה) + back (תשובה).

כל כרטיסיה חייבת לבחון **תוכן קליני** מהמקורות:
- קריטריוני אבחנה ספציפיים (גילים, מספר תסמינים, משך, חומרה)
- אפידמיולוגיה (שכיחות, מין, גיל הופעה)
- אטיולוגיה ופתופיזיולוגיה
- טיפול קו ראשון/שני, מינונים, תופעות לוואי
- ניטור (מה, מתי, איזה ערכים)
- אבחנה מבדלת
- היבטים ייחודיים לילדים/נוער

**איסור מוחלט**: אל תכתוב כרטיסיות על הסמלים החזותיים של ה-Sketchy (אין שאלות כמו "מה מייצג הילד עם מסכת הנינג'ה?" — זה לא חומר בחינה). הכרטיסיות נבחנות בידע קליני אמיתי.

תייג כל כרטיסיה ב-aspect מתוך הרשימה הזו: {', '.join(aspects)}.
ה-aspect משמש ב-UI לקישור עם האינפוגרפיקה הרלוונטית."""


def prompt_quiz(day, symbol_block, legend_block):
    base = build_clinical_context(day)
    return base + """

צור קוויז של 8-10 שאלות מסוג vignette קליני קצר + multiple choice (4 אופציות + הסבר).
רמה: בחינת שלב ב'. דגש על:
- vignettes קליניים (מקרה אמיתי בן 3-5 שורות) שדורשים אינטגרציה
- אבחנה מבדלת
- בחירה בטיפול אופטימלי
- ניהול סיבוכים נדירים אבל חשובים
- תופעות לוואי + אינטראקציות תרופתיות

**איסור מוחלט**: אל תשאל על סמלים חזותיים של ה-Sketchy. אלו שאלות-בחינה קליניות,
לא חידות-מנמוניקה."""


# ── notebooklm CLI helpers ──────────────────────────────────────────────────
# Which notebooklm profile to drive. The bulk run uses a SEPARATE Google
# account from the shared VM so its daily generation quota is its own; the
# profile is how that account is selected, and forgetting it silently drives the
# other project's account instead.
NOTEBOOKLM_PROFILE = os.environ.get("NOTEBOOKLM_PROFILE", "").strip()


def nb_run(args, timeout=120, retries=2):
    """Run the CLI, retrying a run that comes back empty.

    The CLI occasionally exits 0 having written nothing at all — seen once on
    `create`, where the caller then died on `r.stdout.strip()` because stdout
    was None. Over 141 chapters an intermittent empty result is a certainty,
    and the peer project lost whole episodes to exactly this class of silent
    failure, so retry here rather than at every call site.
    """
    cmd = [NOTEBOOKLM_BIN]
    if NOTEBOOKLM_PROFILE:
        cmd += ["-p", NOTEBOOKLM_PROFILE]
    last = None
    for attempt in range(retries + 1):
        try:
            # encoding MUST be pinned. With text=True alone, Python decodes
            # the child's output with the Windows locale codec (cp1255 here).
            # Our notebook titles are Hebrew UTF-8, cp1255 cannot decode them,
            # and the failure is invisible: the reader thread dies, stdout comes
            # back EMPTY with returncode 0 and an empty stderr. Every "transient
            # empty result" chased in this pipeline was this.
            r = subprocess.run(cmd + args, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            print(f"    [!] notebooklm {args[0]} timed out "
                  f"({attempt + 1}/{retries + 1})", file=sys.stderr)
            last = subprocess.CompletedProcess(cmd + args, 124, "", str(e))
            continue
        # Normalise: capture_output should give strings, but a killed child can
        # leave None, and None is what turns a retryable blip into a crash.
        r.stdout = r.stdout or ""
        r.stderr = r.stderr or ""
        last = r
        if r.returncode == 0 and r.stdout.strip():
            return r
        if attempt < retries:
            print(f"    [!] notebooklm {args[0]} returned rc={r.returncode} with "
                  f"{len(r.stdout)} bytes; retrying "
                  f"({attempt + 2}/{retries + 1})", file=sys.stderr)
            time.sleep(8 * (attempt + 1))
    return last


def use_notebook(nb_id):
    r = nb_run(["use", nb_id], timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"notebooklm use failed: {r.stderr[:300]}")


def find_day_notebook(day_index, title_he):
    """Look up existing per-day notebook by exact title match."""
    expected_title = f"[Study-{day_index:03d}] {title_he}"
    r = nb_run(["list", "--json"], timeout=60)
    # A failed LOOKUP is not the same as "no such notebook". Returning
    # (None, None) here conflated them: the caller read the second value as the
    # title and went on to create a notebook literally named None. Worse, if a
    # notebook for this day already existed, a transient list failure would
    # silently create a duplicate. Raise instead — nb_run has already retried.
    if r.returncode != 0:
        raise RuntimeError(f"could not list notebooks: rc={r.returncode} "
                           f"{(r.stderr or '')[:200]}")
    try:
        nbs = json.loads(r.stdout).get("notebooks", [])
    except Exception as e:
        raise RuntimeError(f"could not parse notebook list: {e}; "
                           f"raw: {(r.stdout or '')[:200]}")
    for nb in nbs:
        if nb.get("title", "").strip() == expected_title.strip():
            return nb.get("id"), expected_title
    return None, expected_title


def create_day_notebook(title):
    """Create an empty notebook with the given title."""
    r = nb_run(["create", title, "--json"], timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"create notebook failed: {r.stderr[:300]}")
    try:
        data = json.loads(r.stdout.strip())
    except Exception as e:
        raise RuntimeError(f"create notebook parse failed: {e}; raw: {r.stdout[:300]}")
    nb_id = (data.get("notebook") or {}).get("id") or data.get("id")
    if not nb_id:
        raise RuntimeError(f"no id in create response: {r.stdout[:300]}")
    return nb_id


def add_text_source(nb_id, content, title):
    """Add inline text as a source. Returns source_id (or None)."""
    # Write to a temp .md so source add picks it up as file
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp_path = f.name
    r = nb_run(["source", "add", tmp_path, "--title", title, "--json", "-n", nb_id], timeout=180)
    Path(tmp_path).unlink(missing_ok=True)
    if r.returncode != 0:
        print(f"  [!] source add failed: {r.stderr[:200]}")
        return None
    try:
        data = json.loads(r.stdout.strip())
        return (data.get("source") or {}).get("id") or data.get("id")
    except Exception:
        return None


def add_pdf_source(nb_id, pdf_path, title=None):
    """Upload a single PDF as a source to the per-day notebook. Returns source_id or None."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"  [!] PDF not found, skipping: {pdf_path}")
        return None
    title = title or pdf_path.stem
    r = nb_run(["source", "add", str(pdf_path), "--title", title, "--json", "-n", nb_id], timeout=600)
    if r.returncode != 0:
        print(f"  [!] PDF source add failed ({pdf_path.name}): {r.stderr[:200]}")
        return None
    try:
        data = json.loads(r.stdout.strip())
        return (data.get("source") or {}).get("id") or data.get("id")
    except Exception:
        return None


def build_image_jobs(aspects, facts):
    """Return [(label, aspect, facts_slice)]. Normally one job per aspect, but a
    criteria aspect whose facts have 2+ symptom clusters is split into one job
    per cluster (e.g. ADHD criteria -> inattention image + hyperactivity image),
    each receiving only that cluster's items + the shared thresholds, so each
    criterion has room to be its own distinct visual detail.

    The epidemiology who/comes_with split is GONE. It only ever existed in prose:
    both halves were handed the identical unsplit facts slice, so the two renders
    differed by wording alone, and the branch that produced them also carried the
    "ZERO TEXT / NO written numerals" rule that stripped every figure out of the
    epidemiology images. Epidemiology is now one aspect of the chapter's main
    plate — see sketchy_spec.plan_plates for the hybrid main/criteria split that
    replaces per-aspect jobs wholesale."""
    jobs = []
    for aspect in aspects:
        slice_ = facts_for_aspect(aspect, facts)
        if aspect in ("dsm_criteria", "diagnostic_criteria") and isinstance(slice_, dict):
            crit = slice_.get("criteria") if isinstance(slice_.get("criteria"), dict) else None
            clusters = crit.get("clusters") if crit else None
            if isinstance(clusters, list) and len(clusters) >= 2:
                shared = {k: v for k, v in crit.items() if k != "clusters"}
                for n, c in enumerate(clusters, 1):
                    name = c.get("name", "cluster")
                    # slugify strips non-Latin characters, so a Hebrew cluster
                    # name ("תסמיני ליבה") collapses to "" and the asset ships
                    # as sketchy_dsm_criteria_.png. Fall back to the ordinal.
                    label = f"{aspect}_{slugify(name) or f'cluster{n}'}"
                    cf = {"criteria": {"cluster": name, "items": c.get("items", []), **shared}}
                    jobs.append((label, aspect, cf))
                continue
        jobs.append((aspect, aspect, slice_))
    return jobs


# Each book is found in Drive by a SEARCH STRING, not a filename. The filenames
# in Drive are whatever the owner's downloads were called
# ("Emailing Dulcans Textbook of Child and Adolescent Psychiatry, 3e.pdf"), and
# the tidy names this file used to assume ("dulcan.pdf") match nothing. Every
# string below was verified against the real Drive on 2026-08-20.
DRIVE_BOOK_QUERIES = [
    ("DSM-5-TR", "DSM-5-TR"),
    ("Dulcan", "Dulcan"),
    ("Kaplan & Sadock Synopsis", "Kaplan Sadock Synopsis"),
    ("Stahl Essential", "Stahl Essential Psychopharmacology"),
    ("Stahl Pediatric", "Prescriber's Guide Children Adolescents"),
    ("Syllabus", "syllabus"),
]


def add_textbook_pdfs_to_day_notebook(nb_id):
    """Attach all six textbooks to the per-day notebook BY DRIVE REFERENCE.

    The books stay whole and the notebook stays fresh: a new notebook per
    chapter (so NotebookLM cannot drift into last chapter's context) with every
    book available to it (so it can survey the topic itself instead of being
    handed our own page ranges back). Uploading the PDFs per chapter would have
    been ~600MB x 141 chapters; a Drive import is a reference and costs nothing.

    Returns the count of books actually attached. The caller MUST treat a short
    count as fatal: a book named in the prompt but missing from the notebook
    does not error, it makes NotebookLM invent page numbers.
    """
    added = 0
    for label, query in DRIVE_BOOK_QUERIES:
        r = nb_run(["source", "add-research", query, "--from", "drive",
                    "--import-all", "-n", nb_id, "--timeout", "180", "--json"],
                   timeout=300)
        ok = False
        try:
            data = json.loads(r.stdout[r.stdout.index("{"):])
            got = data.get("imported_sources") or []
            ok = bool(got)
            if ok:
                print(f"    [OK] {label}: {got[0]['title'][:58]}")
        except Exception:
            pass
        if not ok:
            print(f"    [!!] {label}: NOT attached (query {query!r})")
        added += ok
    return added


def wait_for_sources_indexed(nb_id, timeout=420):
    """Block until every source in the notebook has finished processing.

    A freshly attached Drive PDF is not queryable for a while. Extracting
    before indexing completes does not error — it returns a near-empty answer,
    which is how chapter 29 produced 845 characters of "source material" from
    six textbooks and would have built its whole plate set on nothing.
    """
    r = nb_run(["source", "list", "-n", nb_id, "--json"], timeout=60)
    try:
        sources = json.loads(r.stdout).get("sources", [])
    except Exception:
        print("  [!] could not list sources to wait on", file=sys.stderr)
        return False
    ok = True
    for src in sources:
        sid = src.get("id")
        if not sid:
            continue
        w = nb_run(["source", "wait", sid, "-n", nb_id,
                    "--timeout", str(timeout), "--interval", "5"],
                   timeout=timeout + 60)
        # exit 0 = ready, 1 = failed, 2 = timeout
        state = {0: "ready", 1: "FAILED", 2: "TIMEOUT"}.get(w.returncode, "?")
        title = (src.get("title") or sid)[:44]
        print(f"    {state:8} {title}")
        ok &= (w.returncode == 0)
    return ok


def count_notebook_sources(nb_id):
    """Return number of sources currently in the given notebook (or -1 on error)."""
    r = nb_run(["source", "list", "-n", nb_id, "--json"], timeout=60)
    if r.returncode != 0:
        return -1
    try:
        data = json.loads(r.stdout)
        return data.get("count", len(data.get("sources", [])))
    except Exception:
        return -1


def _strip_code_fence(text):
    """Strip ```json ... ``` fences a model may wrap JSON in."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


def extract_facts(day, notebook_id):
    """Extract STRUCTURED facts (the source of truth for the Sketchy images)
    from the per-day notebook as JSON. Each aspect's image is later built ONLY
    from its slice of this dict — guaranteeing high resolution and zero
    bleeding between images. Returns a dict (possibly partial) or {}."""
    title_he = day.get("title_he", day.get("title_en", ""))
    aspects = day.get("aspects", [])

    schema_prompt = f"""Based ONLY on the sources in this notebook, extract the high-yield facts about: {title_he}

{BOOKS_DIRECTIVE}

Return STRICT JSON (no prose, no markdown fences) with this exact shape. Fill every field from the sources; if a field is genuinely not in the sources, use the string "לא צוין". Keep values SHORT and concrete (numbers, names) — this drives mnemonic image generation.

{{
  "criteria": {{
    "clusters": [
      {{"name": "<cluster name e.g. Inattention>", "items": ["<symptom 1>", "<symptom 2>", "..."]}},
      {{"name": "<cluster name e.g. Hyperactivity/Impulsivity>", "items": ["..."]}}
    ],
    "thresholds": "<e.g. >=6/9 symptoms, <12yo onset, >=2 settings, >=6 months>",
    "subtypes": ["<presentation 1>", "..."]
  }},
  "epidemiology": {{
    "prevalence": "<e.g. ~5% children, ~2.5% adults>",
    "sex_ratio": "<e.g. 2:1 male:female>",
    "onset_age_typical": "<epidemiological peak onset, or לא צוין>",
    "heritability": "<e.g. ~74%, one of the most heritable>",
    "comorbidities": ["<e.g. ODD ~50%>", "<e.g. anxiety>", "..."],
    "prognosis": "<e.g. ~60% persist into adulthood>"
  }},
  "etiology": {{
    "genetic": "<short>",
    "neurobiology": "<short — receptors, regions, circuits>",
    "environmental": "<short>"
  }},
  "treatment": {{
    "first_line": ["<drug or therapy + key detail>"],
    "second_line": ["<...>"],
    "key_side_effects": ["<...>"],
    "monitoring": "<short>"
  }},
  "high_yield": ["<exam pearl 1>", "<exam pearl 2>", "..."]
}}

Only include the keys relevant to today's aspects ({', '.join(aspects)}); omit sections that don't apply (e.g., omit 'treatment' for a pure anatomy chapter). Output ONLY the JSON object."""

    use_notebook(notebook_id)
    print("  Extracting structured facts.json (this may take 1-2 min)...")
    r = nb_run(["ask", schema_prompt, "--json"], timeout=600)
    if r.returncode != 0:
        print(f"  [!] facts extraction ask failed: {r.stderr[:200]}")
        return {}
    try:
        answer = json.loads(r.stdout).get("answer", "")
    except Exception as e:
        print(f"  [!] facts ask parse failed: {e}")
        return {}
    try:
        facts = json.loads(_strip_code_fence(answer))
        if isinstance(facts, dict):
            return facts
    except Exception as e:
        print(f"  [!] facts JSON parse failed: {e}. Raw head: {answer[:160]}")
    return {}


def facts_for_aspect(aspect, facts):
    """Return the slice of facts.json relevant to a given image aspect."""
    if not isinstance(facts, dict):
        return None
    # Map aspect → which facts.json section(s) to feed the image
    mapping = {
        "dsm_criteria": ["criteria"],
        "diagnostic_criteria": ["criteria"],
        "epidemiology": ["epidemiology"],
        "etiology": ["etiology"],
        "treatment_first_line": ["treatment"],
        "treatment_second_line": ["treatment"],
        "treatment_overview": ["treatment"],
        "drug_mechanism": ["etiology", "treatment"],
        "differential": ["criteria", "high_yield"],
        "assessment_tools": ["criteria", "high_yield"],
        "medical_mimics": ["high_yield"],
    }
    sections = mapping.get(aspect)
    if not sections:
        # default: give high_yield + anything matching the aspect name
        sections = ["high_yield"]
    slice_ = {s: facts[s] for s in sections if s in facts}
    return slice_ or None


def extract_day_content_from_main(day, symbols_block, main_notebook_id):
    """Use the MAIN notebook to extract comprehensive material for this day's
    topic into a markdown file. This .md becomes the focused source for the
    per-day notebook."""
    title_he = day.get("title_he", "")
    aspects = day.get("aspects", [])
    focus = day.get("prompt_focus", "")

    extraction_prompt = f"""Extract from the sources in this notebook ALL the comprehensive material relevant to today's study topic. Be thorough — this extract will be the SOLE source for a focused study notebook.

{BOOKS_DIRECTIVE}

Topic: {title_he}

Focus: {focus}

Aspects to cover comprehensively:
{', '.join(aspects)}

Output as a long, well-structured Markdown document in Hebrew (with English drug/receptor/concept names as they appear in source). Include:
- Page references from each source (DSM p.NNN, Dulcan p.NNN, Stahl p.NNN); Synopsis by chapter only
- Direct citations / paraphrases (lots — this is the source material)
- All clinically relevant detail (criteria, epidemiology, etiology, treatment, monitoring, special considerations for pediatric population)
- Cross-references between sources where they agree/disagree

Length: long and thorough — 3000-6000 words. This is study material, not a summary."""

    # Switch to main notebook
    use_notebook(main_notebook_id)
    print("  Extracting comprehensive content from main notebook (this may take 3-5 min)...")
    r = nb_run(["ask", extraction_prompt, "--json"], timeout=600)
    if r.returncode != 0:
        print(f"  [!] extraction ask failed: {r.stderr[:200]}")
        return None
    try:
        resp = json.loads(r.stdout)
        return resp.get("answer", "")
    except Exception as e:
        print(f"  [!] extraction parse failed: {e}")
        return None


def start_audio(prompt):
    r = nb_run(["generate", "audio", prompt, "--format", "deep-dive",
                "--language", "he", "--length", "long", "--json"], timeout=120)
    return _extract_task_id(r, "audio")


def start_video(prompt):
    r = nb_run(["generate", "video", prompt, "--style", "whiteboard", "--language", "he", "--json"], timeout=120)
    return _extract_task_id(r, "video")


def start_infographic(prompt):
    # NotebookLM's native infographic style — instructional (info-dense,
    # teaching-focused). NOT sketch-note: we use Gemini Nano Banana 2 for the
    # actual Sketchy scenes, since NotebookLM's sketch-note style produces
    # template infographics rather than real scene-based mnemonics.
    r = nb_run(["generate", "infographic", prompt, "--style", "instructional", "--language", "he", "--json"], timeout=120)
    return _extract_task_id(r, "infographic")


def start_infographic_scoped(prompt, source_id):
    """Start a NotebookLM infographic scoped to a SINGLE source (the per-aspect
    facts slice uploaded by _render_scope_md), via `generate infographic -s <id>`.
    This isolates the infographic to one aspect so the day's infographics stop
    sharing the whole-notebook soup (no repetition / mixed aspects)."""
    r = nb_run(["generate", "infographic", prompt, "--style", "instructional",
                "--language", "he", "--source", source_id, "--json"], timeout=120)
    return _extract_task_id(r, "infographic")


def start_report(prompt):
    # DEPRECATED: notebooklm generate report uses a template that ignores the
    # prompt and picks its own topic (usually the largest source). We now use
    # ask_study_guide() instead, which is synchronous and honors the prompt.
    r = nb_run(["generate", "report", prompt, "--format", "study-guide", "--language", "he", "--json"], timeout=120)
    return _extract_task_id(r, "report")


def ask_study_guide(day, symbol_block, legend_block, out_path):
    """Generate a focused study guide via 'notebooklm ask' that honors the
    day's specific topic AND includes page references to source books."""
    title_he = day.get("title_he", day.get("title_en", ""))
    aspects = day.get("aspects", [])
    focus = day.get("prompt_focus", "")

    prompt = f"""צור מדריך לימוד מקיף, ממוקד ומובנה בעברית עבור הנושא של היום הזה בלבד.

נושא היום: {title_he}

מיקוד: {focus}

מבנה המדריך (מודולים מסודרים):

# מדריך לימוד יום זה: {title_he}

## 0. מפת המקורות (CRITICAL — page references)
טבלה: לכל סעיף ב-מדריך, ציין באיזה ספר וב-איזה עמוד נמצא החומר.
פורמט:
| נושא משנה | DSM-5-TR עמ' | Dulcan עמ' | Stahl Essential עמ' | Stahl C&A עמ' | Synopsis פרק | סילבוס עמ' |

עמודת Synopsis מכילה **שם/מספר פרק בלבד**, לא עמודים, ורק כשבאמת נעשה בו שימוש
להשלמת פער ב-Dulcan. אם לא נעשה בו שימוש — סמן "—".
עמודת Stahl C&A רלוונטית רק לנושאים שכוללים תרופה מתוך 33 המונוגרפיות שבספר.

## 1. מבוא + הגדרה
## 2. {aspects[0] if len(aspects) > 0 else 'קריטריונים'}
## 3. {aspects[1] if len(aspects) > 1 else 'אפידמיולוגיה'}
## 4. {aspects[2] if len(aspects) > 2 else 'אטיולוגיה'}
## 5. {aspects[3] if len(aspects) > 3 else 'טיפול'}
{chr(10).join(f'## {6+i}. {a}' for i, a in enumerate(aspects[4:]))}
## נקודות high-yield לבחינה
## סיכום מבני

**הוראות מחייבות**:
- שפה: עברית לכל הסבר. אנגלית רק לשמות תרופות גנריים, קולטנים (D2, 5-HT2A...), מונחים קליניים (EPS, NMS, SSRI), וקיצורי DSM/ICD.
- **לכל פיסת מידע ציין עמוד מקור בסוגריים**: "(DSM p.347)" או "(Dulcan ch.7 p.412)".
- אורך: 2000-4000 מילים. ממוקד מהותית בנושא היום, לא סקירה כללית.
- אסמכתאות: מבוסס אך ורק על המקורות במחברת.

{BOOKS_DIRECTIVE}"""

    r = nb_run(["ask", prompt, "--json"], timeout=900)
    if r.returncode != 0:
        print(f"  [!] ask study_guide failed: {r.stderr[:200]}")
        return False
    try:
        resp = json.loads(r.stdout)
        answer = resp.get("answer", "")
    except Exception as e:
        print(f"  [!] parse study_guide failed: {e}")
        return False
    if not answer:
        return False
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(answer, encoding="utf-8")
    return out_path.exists() and out_path.stat().st_size > 0


def start_flashcards(prompt):
    r = nb_run(["generate", "flashcards", prompt, "--quantity", "standard", "--difficulty", "medium", "--json"], timeout=120)
    return _extract_task_id(r, "flashcards")


def start_quiz(prompt):
    r = nb_run(["generate", "quiz", prompt, "--quantity", "standard", "--difficulty", "hard", "--json"], timeout=120)
    return _extract_task_id(r, "quiz")


def _extract_task_id(r, kind):
    if r.returncode != 0:
        print(f"  [!] start {kind} failed (exit {r.returncode}): {r.stderr[:200]}")
        return None
    try:
        out = json.loads(r.stdout.strip())
        return out.get("task_id") or out.get("artifact_id") or out.get("id")
    except Exception as e:
        print(f"  [!] parse {kind} output failed: {e}; raw: {r.stdout[:200]}")
        return None


def wait_for_artifacts(task_ids, max_wait=ARTIFACT_MAX_WAIT_S):
    """Poll until every task is completed or failed.
    `task_ids` is already {uuid: kind} — keep that orientation."""
    if not task_ids:
        return {}

    # Correct: iterate {uuid: kind} as (uuid, kind), keep same orientation in pending.
    pending = {tid: kind for tid, kind in task_ids.items() if tid}
    results = {}
    start = time.time()

    while pending and (time.time() - start) < max_wait:
        time.sleep(POLL_INTERVAL_S)
        elapsed = int(time.time() - start)
        r = nb_run(["artifact", "list", "--json"], timeout=30)
        try:
            arts = json.loads(r.stdout).get("artifacts", [])
        except Exception:
            continue
        for a in arts:
            aid = a.get("id")
            if aid in pending:
                status = a.get("status", "?")
                kind = pending[aid]
                print(f"  [{elapsed // 60}m] {kind} ({aid[:8]}): {status}")
                if status == "completed":
                    results[aid] = (kind, "completed", a)
                    del pending[aid]
                elif status in ("failed", "error", "unknown"):
                    results[aid] = (kind, status, a)
                    del pending[aid]

    if pending:
        print(f"  [!] Timed out for: {list(pending.values())}")
        for aid, kind in pending.items():
            results[aid] = (kind, "timeout", None)
    return results


def download_artifact(kind, artifact_id, out_path):
    cmd = {
        "audio": ["download", "audio", str(out_path), "-a", artifact_id],
        "video": ["download", "video", str(out_path), "-a", artifact_id],
        "infographic": ["download", "infographic", str(out_path), "-a", artifact_id],
        "report": ["download", "report", str(out_path), "-a", artifact_id],
        "flashcards": ["download", "flashcards", str(out_path), "-a", artifact_id],
        "quiz": ["download", "quiz", str(out_path), "-a", artifact_id],
    }
    if kind not in cmd:
        return False
    # Remove any pre-existing file so notebooklm CLI doesn't suffix with " (2)", etc.
    out_path = Path(out_path)
    if out_path.exists():
        try:
            out_path.unlink()
        except OSError:
            pass
    r = nb_run(cmd[kind], timeout=300)
    return r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


# ── Github Release ──────────────────────────────────────────────────────────
def _gh_ok(stage: str, r: subprocess.CompletedProcess) -> bool:
    """Log + return whether a gh subprocess call succeeded."""
    if r.returncode == 0:
        return True
    err = (r.stderr or r.stdout or "").strip().splitlines()
    tail = err[-1] if err else "(no error message)"
    print(f"  [FAIL] gh {stage} exit={r.returncode}: {tail}")
    return False


def upload_release(tag, title, notes_md, assets):
    """Create or update a GitHub Release with all assets.

    Returns {"url": "<release_url>", "assets": [...]} on success,
    or {"url": "", "assets": []} on failure (with errors printed).
    """
    env = os.environ.copy()
    if not env.get("GH_TOKEN"):
        print("  [FAIL] GH_TOKEN not set — cannot create/upload Release. "
              "Add GH_TOKEN to .env or environment, then re-run.")
        return {"url": "", "assets": []}

    # Check if release exists
    r = subprocess.run(
        ["gh", "release", "view", tag, "--repo", GITHUB_REPO],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if r.returncode == 0:
        # Release exists — upload each asset with --clobber
        upload_failures = 0
        for a in assets:
            ru = subprocess.run(
                ["gh", "release", "upload", tag, str(a), "--repo", GITHUB_REPO, "--clobber"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=300,
            )
            if not _gh_ok(f"release upload {Path(a).name}", ru):
                upload_failures += 1
        if upload_failures:
            print(f"  [!] {upload_failures}/{len(assets)} assets failed to upload.")
    else:
        # Create new release with all assets attached at creation
        rc = subprocess.run(
            ["gh", "release", "create", tag] + [str(a) for a in assets] + [
                "--title", title, "--notes", notes_md, "--repo", GITHUB_REPO,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600,
        )
        if not _gh_ok("release create", rc):
            return {"url": "", "assets": []}

    # Read back assets / url
    rv = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets,url",
         "--jq", "{url: .url, assets: [.assets[] | {name: .name, url: .url}]}",
         "--repo", GITHUB_REPO],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if not _gh_ok("release view (read-back)", rv):
        return {"url": "", "assets": []}
    try:
        return json.loads(rv.stdout)
    except Exception as e:
        print(f"  [FAIL] could not parse gh release view output: {e}")
        return {"url": "", "assets": []}


# ── Per-day generator ───────────────────────────────────────────────────────
def generate_day(day_index, schedule, symbols, output_dir, dry_run=False):
    days = schedule["days"]
    day = next((d for d in days if d["index"] == day_index), None)
    if not day:
        print(f"Day {day_index} not in schedule.")
        return None

    title_en = day.get("title_en", f"Day {day_index}")
    slug = slugify(title_en)
    tag = f"study-{day_index:03d}-{slug}"
    day_dir = output_dir / f"day-{day_index:03d}"
    day_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*70}")
    print(f"Day {day_index}: {day.get('title_he', title_en)}")
    print(f"Tag: {tag}")
    print(f"Aspects: {day.get('aspects', [])}")
    print(f"Symbols ({len(day.get('symbols_needed', []))}): {day.get('symbols_needed', [])[:5]}...")
    print(f"Density: {day.get('estimated_density', 'medium')}")
    print(f"{'='*70}\n")

    # Build symbol descriptions for prompts
    symbol_block = collect_symbol_descriptions(day.get("symbols_needed", []), symbols)
    legend_block = build_symbol_legend(day, symbols)
    caption_legend = build_caption_legend(day, symbols)

    if dry_run:
        print("[DRY RUN] Skipping NotebookLM + Gemini API calls.")
        print("=== SYMBOL LEGEND PREVIEW ===")
        print(legend_block)
        print("\n=== CAPTION LEGEND PREVIEW ===")
        print(caption_legend)
        return None

    # === Per-day notebook architecture ===
    # 1. Check if per-day notebook already exists
    day_nb_id, day_nb_title = find_day_notebook(day_index, day.get("title_he", title_en))
    if day_nb_id:
        print(f"Found existing per-day notebook: {day_nb_id} ('{day_nb_title}')")
        # Retrofit: if the notebook predates the PDF-wiring change, it only has
        # the extracted .md as its single source. Upload PDFs now so the study
        # guide can cite real page numbers.
        n_sources = count_notebook_sources(day_nb_id)
        # Books now arrive by Drive reference, so the old count of local PDF
        # files on disk is meaningless — expect one source per Drive query.
        min_expected = 1 + len(DRIVE_BOOK_QUERIES)
        if 0 < n_sources < min_expected:
            print(f"  Notebook has only {n_sources} source(s) (expected {min_expected}). Retrofitting with textbook PDFs...")
            added = add_textbook_pdfs_to_day_notebook(day_nb_id)
            if added > 0:
                wait_s = 180  # PDFs take longer to index than text
                print(f"  Added {added} PDF source(s). Waiting {wait_s}s for indexing...")
                time.sleep(wait_s)
        elif n_sources >= min_expected:
            print(f"  Notebook already has {n_sources} sources — skipping PDF retrofit.")
        else:
            # "Could not determine" used to shrug and continue. It then
            # generated audio, video and a study guide against a notebook with
            # NO SOURCES: every artifact would have been invented whole. If we
            # cannot confirm the books are attached, stop.
            raise RuntimeError(
                f"could not count sources in notebook {day_nb_id}; refusing to "
                f"generate against a notebook whose contents are unknown")
    else:
        # 2. Create the per-day notebook FIRST, then attach the books, then
        #    extract from it.
        #
        #    This used to extract from a single shared MAIN notebook holding
        #    every book, and only afterwards build the day's notebook. Two
        #    reasons that is gone: the main notebook belongs to whichever
        #    account created it (the bulk run uses its own account and simply
        #    cannot see it), and a notebook reused across 144 chapters
        #    accumulates context until the model starts drifting. A fresh
        #    notebook per chapter with all six books attached by Drive
        #    reference costs nothing and has neither problem.
        print(f"Creating per-day notebook: {day_nb_title}")
        day_nb_id = create_day_notebook(day_nb_title)
        print(f"  Created notebook: {day_nb_id}")

        # 3. Attach the textbooks by Drive reference so the study guide can
        #    cite real pages.
        print("  Attaching textbooks from Drive...")
        n_pdfs = add_textbook_pdfs_to_day_notebook(day_nb_id)
        # A book named in the prompt but absent from the notebook does not
        # raise — NotebookLM quietly invents plausible page numbers for it.
        # That already shipped once (a day-16 study guide cited "Stahl
        # Pediatric pp. 600-612" in a 525-page book). Refuse to build a chapter
        # on an incomplete notebook.
        if n_pdfs < len(DRIVE_BOOK_QUERIES):
            raise RuntimeError(
                f"only {n_pdfs}/{len(DRIVE_BOOK_QUERIES)} textbooks attached to "
                f"notebook {day_nb_id}. Refusing to generate: the prompts name "
                f"all six books, and a missing one becomes invented citations.")

        # 4. Wait for the books to finish indexing BEFORE asking anything of
        #    them. This wait used to sit AFTER the extraction, which was
        #    harmless when extraction came from a long-lived main notebook and
        #    silently destructive once it came from a notebook whose sources
        #    were attached seconds earlier.
        print("  Waiting for textbooks to index...")
        if not wait_for_sources_indexed(day_nb_id):
            raise RuntimeError(
                f"not every source in {day_nb_id} finished indexing; refusing "
                f"to extract from a notebook that cannot answer yet")

        # 5. Extract this chapter's facts FROM THE DAY NOTEBOOK, which now
        #    holds the books themselves.
        extract_md = extract_day_content_from_main(day, symbol_block, day_nb_id)
        if not extract_md:
            print("[!] Could not extract content from the day notebook. Aborting.")
            return None
        # Six textbooks cannot honestly answer a chapter brief in a paragraph.
        # A tiny extract means the notebook was not ready or the query failed,
        # and facts.json drives every plate downstream.
        MIN_EXTRACT_CHARS = 2000
        if len(extract_md) < MIN_EXTRACT_CHARS:
            raise RuntimeError(
                f"extract is only {len(extract_md)} chars (expected at least "
                f"{MIN_EXTRACT_CHARS}); the notebook likely was not indexed. "
                f"Refusing to build a chapter on it.")
        print(f"  Extracted {len(extract_md)} characters of source material.")
        print(f"  Attached {n_pdfs} textbook(s) by reference.")

        # 4b. Add the extracted .md as a focused-summary source.
        source_title = f"Day {day_index} — Focused extract (from main notebook)"
        src_id = add_text_source(day_nb_id, extract_md, source_title)
        if not src_id:
            print("[!] Could not add focused-extract source. Continuing with PDFs only.")
        else:
            print(f"  Added focused-extract source: {src_id}")

        # 5. Wait for indexing — PDFs need more time than text
        wait_s = max(WAIT_INDEX_S, 120 if n_pdfs > 0 else WAIT_INDEX_S)
        print(f"  Waiting {wait_s}s for indexing...")
        time.sleep(wait_s)

    # Switch to the per-day notebook for artifact generation
    use_notebook(day_nb_id)

    # Decide how many infographics based on density
    density = day.get("estimated_density", "medium")
    n_infographics = {"low": 1, "medium": 2, "high": 3}.get(density, 2)

    # Pick which aspects get their own infographic
    infographic_aspects = day.get("aspects", [])[:n_infographics]

    # Fire off the comprehensive NotebookLM artifacts first (audio, video,
    # flashcards, quiz) — these legitimately draw on the WHOLE notebook. The
    # per-aspect infographics are launched LATER (after facts.json exists), each
    # scoped to ONLY its aspect's slice so they stop repeating each other.
    task_ids = {}
    print("Starting NotebookLM artifacts...")
    task_ids["audio"] = start_audio(prompt_audio(day, symbol_block, legend_block))
    print(f"  audio: {task_ids['audio']}")
    time.sleep(15)
    task_ids["video"] = start_video(prompt_video(day, symbol_block, legend_block))
    print(f"  video: {task_ids['video']}")
    time.sleep(15)
    task_ids["flashcards"] = start_flashcards(prompt_flashcards(day, symbol_block, legend_block))
    print(f"  flashcards: {task_ids['flashcards']}")
    time.sleep(15)
    task_ids["quiz"] = start_quiz(prompt_quiz(day, symbol_block, legend_block))
    print(f"  quiz: {task_ids['quiz']}")

    # Synchronous: generate the study guide via 'ask' (NOT generate report —
    # that ignores the prompt and picks its own topic). The ask format honors
    # our day-specific prompt AND we ask for page references.
    print("\nGenerating focused study guide (ask + page refs)...")
    study_guide_path = day_dir / "study_guide.md"
    if ask_study_guide(day, symbol_block, legend_block, study_guide_path):
        size_kb = study_guide_path.stat().st_size / 1024
        print(f"  [OK] study_guide.md ({size_kb:.1f} KB)")
    else:
        print("  [X] study guide generation failed")
        study_guide_path = None

    # Extract STRUCTURED facts (source of truth for the per-aspect images).
    print("\nExtracting structured facts.json...")
    facts = extract_facts(day, day_nb_id)
    facts_path = day_dir / "facts.json"
    if facts:
        facts_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [OK] facts.json ({len(json.dumps(facts))} bytes, keys: {list(facts.keys())})")
    else:
        print("  [!] facts.json empty — images will fall back to generic per-aspect points.")
        facts_path = None

    # Per-aspect SCOPED infographics: upload each aspect's facts slice as its OWN
    # NotebookLM source, then generate that aspect's infographic scoped to ONLY
    # that source (`generate infographic -s <id>`). This isolates each
    # infographic to its aspect — no cross-aspect bleeding / repetition — exactly
    # like the Sketchy images. If facts / slice / upload / scoped-start is
    # unavailable, falls back to the old whole-notebook infographic so we never
    # lose one.
    use_notebook(day_nb_id)
    scope_source_ids = {}
    if facts:
        print("\nUploading per-aspect scope sources for infographics...")
        for aspect in infographic_aspects:
            slice_ = facts_for_aspect(aspect, facts)
            if not slice_:
                continue
            sid = add_text_source(day_nb_id, _render_scope_md(day, aspect, slice_),
                                  f"[SCOPE] {aspect}")
            if sid:
                scope_source_ids[aspect] = sid
                print(f"  scope[{aspect}] = {sid[:8]}")
        if scope_source_ids:
            print(f"  Waiting {SCOPE_INDEX_WAIT_S}s for scope sources to index...")
            time.sleep(SCOPE_INDEX_WAIT_S)

    print("Starting per-aspect infographics...")
    for aspect in infographic_aspects:
        time.sleep(15)
        sid = scope_source_ids.get(aspect)
        if sid:
            tid = start_infographic_scoped(
                prompt_infographic_scoped(day, aspect, facts_for_aspect(aspect, facts)), sid)
            note = "scoped"
        else:
            tid = start_infographic(prompt_notebooklm_infographic(day, aspect))
            note = "whole-notebook (fallback)"
        task_ids[f"infographic_{aspect}"] = tid
        print(f"  infographic_{aspect}: {tid} [{note}]")

    # Generate Sketchy-style scenes via Gemini. Each image is ASPECT-SCOPED
    # (only its slice of facts.json + only its facts-named symbols) so the day's
    # images don't bleed. A criteria aspect with 2+ symptom clusters is split
    # into one image per cluster (e.g. ADHD: inattention image + hyperactivity
    # image) so each criterion gets room to be a distinct detail.
    # ── Sketchy v2 plates ──────────────────────────────────────────────────
    # ONE PLATE PER TRACK, not one image per aspect. v1 drew a separate scene
    # for each aspect of the chapter, which is why nothing linked: the reader
    # got four unrelated pictures of the same topic and no single place to
    # remember. v2 draws the chapter's main plate and, separately, its DSM
    # criteria plate, both inside the chapter's fixed venue.
    #
    # plate_pipeline owns everything structural — the director contract, the
    # element table, the degradation ladder, the render gates, the number
    # compositing, the legend, the quiz and the narration. This loop only
    # decides that plates are wanted and collects what came back.
    gemini_images = []    # [(label, png, txt)] — the release's image assets
    plate_extras = []     # quiz + narration, which ship with their plate
    plate_reports = []
    if HAS_GEMINI:
        try:
            import plate_pipeline as pp
            import sketchy_spec as spec
        except ImportError as exc:
            pp, spec = None, None
            print(f"[!] Sketchy v2 unavailable ({exc}); no plates this run.")

        venue = None
        if pp is not None:
            try:
                venue = pp.load_venue(day_index)
            except SystemExit as exc:
                # A missing venue is a HARD stop for the day's images, never a
                # quiet fall back to the v1 per-aspect path: the whole point of
                # v2 is that a chapter is always drawn in its own fixed place,
                # and a chapter drawn somewhere else is worse than none.
                print(f"[!] {exc} — the chapter cannot be drawn without its venue.")

        if venue is not None:
            tokens = pp.load_tokens(day)
            plates = spec.plan_plates(spec.atomize(facts)) if facts else []
            if not plates:
                print("[!] no facts to draw; skipping plates.")
            else:
                sign = venue.get('sign_text', '?')
                print("")
                print(f"Generating {len(plates)} Sketchy v2 plate(s) in {sign!r}...")
            for plate in plates:
                print(f"  sketchy_{plate.name}: {len(plate.facts)} facts, directing...")
                try:
                    rep = pp.run_plate(day, plate, venue, tokens, day_dir)
                except Exception as exc:   # one bad plate must not lose the day
                    print(f"    [FAIL] sketchy_{plate.name}: {exc}")
                    plate_reports.append({"plate": plate.name, "error": str(exc)})
                    continue
                plate_reports.append(rep)
                png = day_dir / f"sketchy_{plate.name}.png"
                txt = day_dir / f"sketchy_{plate.name}.txt"
                if rep.get("error") or not png.exists():
                    print(f"    [FAIL] {rep.get('error', 'no render produced')}")
                    continue
                gates = rep.get("gates", {})
                print(f"    [OK] {png.name} "
                      f"({png.stat().st_size / (1024 * 1024):.1f} MB), "
                      f"{rep.get('tries')} tries, score {gates.get('score')}, "
                      f"composited {rep.get('compositing', {}).get('composited', 0)}"
                      f"/{rep.get('compositing', {}).get('requested', 0)} numbers")
                gemini_images.append((plate.name, png, txt if txt.exists() else None))
                # A plate without its legend is not a study asset, and the quiz
                # and the narration are generated from that same legend.
                for extra in (day_dir / f"sketchy_{plate.name}.quiz.md",
                              day_dir / f"narration_{plate.name}.txt",
                              day_dir / f"narration_{plate.name}.wav"):
                    if extra.exists():
                        plate_extras.append(extra)
            if plate_reports:
                (day_dir / "plates_report.json").write_text(
                    json.dumps(plate_reports, ensure_ascii=False, indent=1),
                    encoding="utf-8")
                plate_extras.append(day_dir / "plates_report.json")
    else:
        print("[!] gemini_image module unavailable; skipping Sketchy plates.")

    task_ids = {k: v for k, v in task_ids.items() if v}
    print(f"\n{len(task_ids)} NotebookLM artifacts launched. Waiting...\n")

    # Wait
    results = wait_for_artifacts({tid: kind for kind, tid in task_ids.items()})

    # Download all NotebookLM artifacts (incl. infographics)
    assets = []
    for aid, (kind, status, art) in results.items():
        if status != "completed":
            print(f"  [X] {kind} skipped ({status})")
            continue
        ext_map = {"audio": "mp3", "video": "mp4", "infographic": "png", "report": "md", "flashcards": "json", "quiz": "json"}
        base_kind = kind.split("_")[0]
        ext = ext_map.get(base_kind, "bin")
        suffix = "" if kind == base_kind else f"_{kind.split('_', 1)[1]}"
        out_path = day_dir / f"{base_kind}{suffix}.{ext}"
        if download_artifact(base_kind, aid, out_path):
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"  [OK] {out_path.name} ({size_mb:.1f} MB)")
            assets.append(out_path)
        else:
            print(f"  [X] download failed: {kind}")

    # Include the Sketchy v2 plates: the render, its legend, and everything
    # generated from that legend (quiz, narration script, narration audio).
    # The legend is what makes the picture teachable, so it is not optional.
    for label, png_path, txt_path in gemini_images:
        assets.append(png_path)
        if txt_path:
            assets.append(txt_path)
    assets.extend(plate_extras)

    # Include the focused study guide (from ask, not generate report)
    if study_guide_path and study_guide_path.exists():
        assets.append(study_guide_path)

    # Include the structured facts.json (source of truth + useful for the UI)
    if facts_path and facts_path.exists():
        assets.append(facts_path)

    if not assets:
        print("[!] No assets downloaded. Aborting release.")
        return None

    # Upload to GitHub Release
    title_he = day.get("title_he", title_en)
    notes = f"""# יום {day_index}: {title_he}

**שלב**: {day.get('phase')} ({day.get('phase_name', '')})
**סוג**: {day.get('type', '')}
**היבטים**: {', '.join(day.get('aspects', []))}

חבילת הלימוד היומית של מערכת ההכנה לבחינת שלב ב' בפסיכיאטריה של הילד והמתבגר.

נוצר אוטומטית מתוכן ב-NotebookLM (DSM-5-TR + Dulcan + Stahl + הסילבוס הרשמי) דרך `daily_study.py`.
"""
    print(f"\nUploading release {tag}...")
    release_info = upload_release(tag, f"📚 יום {day_index:03d}: {title_he}", notes, assets)
    print(f"Release: {release_info.get('url', '?')}")

    return release_info


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, help="Generate a specific day (1-120)")
    ap.add_argument("--bulk", action="store_true", help="Generate days 1..120")
    ap.add_argument("--start-from", type=int, default=1, help="Bulk start day")
    ap.add_argument("--end-at", type=int, default=120, help="Bulk end day")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true", help="Skip days that already have a release")
    args = ap.parse_args()

    schedule = load_schedule()
    symbols = load_symbols()

    output_dir = REPO_ROOT / "_generated"
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.day:
        generate_day(args.day, schedule, symbols, output_dir, dry_run=args.dry_run)
    elif args.bulk:
        for d in range(args.start_from, args.end_at + 1):
            # Resume: check if release already exists
            if args.resume:
                day_entry = next((x for x in schedule["days"] if x["index"] == d), None)
                if day_entry:
                    tag = f"study-{d:03d}-{slugify(day_entry.get('title_en', ''))}"
                    r = subprocess.run(["gh", "release", "view", tag, "--repo", GITHUB_REPO],
                                       capture_output=True, text=True, encoding="utf-8", errors="replace")
                    if r.returncode == 0:
                        print(f"Day {d}: release already exists ({tag}), skipping.")
                        continue
            generate_day(d, schedule, symbols, output_dir, dry_run=args.dry_run)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
