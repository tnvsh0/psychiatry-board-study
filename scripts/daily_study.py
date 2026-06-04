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
NOTEBOOK_ID = os.environ.get(
    "BOARD_STUDY_NOTEBOOK_ID",
    "157ae88a-ba4c-4d76-a1fd-c551d449a782",  # Board-Exam study material
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
    "kaplan-sadock-synopsis.pdf",
    "syllabus.pdf",
    # kaplan-sadock-comprehensive.pdf intentionally omitted — 352 MB,
    # exceeds practical NotebookLM upload time for daily generation.
    # Add it manually to the main notebook if you want comprehensive coverage.
]


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
    (DSM, Dulcan, Stahl, Synopsis, syllabus), NOT from the cartoon symbols.
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
- כל פיסת תוכן חייבת להישען על המקורות במחברת: DSM-5-TR, Dulcan's Textbook of Child and Adolescent Psychiatry, Stahl's Essential Psychopharmacology, Kaplan & Sadock's Synopsis, והסילבוס הרשמי.
- ציין הפניה לעמוד מקור בסוגריים אחרי כל טענה משמעותית (לדוגמה: "(DSM p.155)" או "(Dulcan p.245)").
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
התוכן חייב להיות חומר לימוד קליני מהמקורות (DSM, Dulcan, Stahl, Synopsis,
סילבוס), עם הפניות לעמודים בעקבות כל טענה מרכזית. כיסוי מקיף של *כל*
ההיבטים שצוינו.

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


SKETCHY_PHILOSOPHY = """The power of a SketchyMedical-style image is NOT in literally illustrating clinical content. The power is in BIZARRE, UNEXPECTED, SURPRISING METAPHOR — and in the dissonance between the setting and the medical fact.

Look at how the established symbols in our symbol legend already work:
- μ-opioid receptor → a COW with 'MOO' on her side, in a DAIRY FARM. Cows have nothing to do with opioids — that dissonance is the entire point.
- 5-HT2A receptor → a TAROT PARLOR with two Ace cards on the table. Tarot has nothing to do with serotonin.
- GABA-A receptor → an Aa HOTEL with 5 floors (α-subunits). A hotel for a receptor.
- D2 receptor → a giant RED BARRIER labeled '2' on a coal-mine train track.
- DOPA-MINE (dopamine) → a literal coal MINE called DOPA-MINE Co. Mining for dopamine ore.
- κ-opioid → a grumpy GOAT with a 'K' tag (visual + phonetic pun on kappa).
- δ-opioid → a DELTA AIRLINES plane (triangular logo = δ).
- β1 receptor → BEETHOVEN conducting with a '#1' baton in front of heart-drums (β1 = heart).

PRINCIPLES that make these scenes memorable:
1. UNEXPECTED SETTING — the scene takes place somewhere that has NOTHING to do with the medical topic (a circus, a wedding, a dairy farm, a heist, a deep-sea submarine, a medieval joust, a hot-air balloon race, an ancient temple, a Las Vegas casino, a Bollywood film set, a sushi conveyor belt, a junkyard).
2. PHONETIC OR VISUAL PUNS — each medical fact is encoded as a character or object via wordplay (μ → MOO, δ → DELTA airline triangle, β1 → Beethoven's #1, '2' on a barrier for D2).
3. NUMBERS ARE COUNTABLE OBJECTS — 6 symptoms = 6 of something (6 cupcakes, 6 dancers, 6 monks). Age 12 = 12 candles. 2 settings = 2 connected rooms.
4. ABSURDITY OVER FAITHFULNESS — a wedding cake encoding antidepressant titration is BETTER than a literal dose-escalation chart. The dissonance encodes the memory.
5. EMOTIONAL ACTIVATION — viewer should LAUGH, gasp, or say 'WHAT?' on first sight. That feeling cements the memory.

EXPLICIT ANTI-PATTERNS — these RUIN Sketchy memorability and are FORBIDDEN:
✗ A doctor's office showing patients with the disorder.
✗ A lecture hall with charts of statistics.
✗ Children behaving like ADHD children (literal, no symbolic substitution).
✗ A laboratory split into zones for biopsychosocial etiology.
✗ A pharmacy with the drug prominently displayed.
✗ A "therapy room" showing techniques.
✗ Anything that looks like a clinical illustration of the topic.
✗ Floating informational labels, callout arrows, or chart-like overlays.

The bizarre context is NOT decoration — it IS the mnemonic engine."""


NUMBER_RULES = """NUMBER ENCODING (course-wide, consistent) — and pegs MUST BELONG too:
- PREFER COUNTING BELONGING OBJECTS for small numbers (<=4-5). The number is the
  COUNT of things that already fit the scene — 2 settings = 2 road-signs; 6
  months = 6 marks on the examiner's calendar. Do NOT add a floating peg-symbol
  when a natural count works.
- RATIOS (2:1, 3:1) → relative team sizes. The viewer SEES 2-vs-1.
- ONLY for un-countable numbers (percentages, big numbers) use a NUMBER-PEG
  SHAPE — but the peg MUST be a natural object of THIS scene that happens to be
  shaped like the digit; NEVER a disembodied symbol floating in space. Shapes:
  0=ring/wheel/donut · 1=candle/pencil/pole · 2=swan/squiggle · 3=trident/
  pretzel · 4=sailboat/flag · 5=starfish/seahorse/hook · 6=cherry/golf-club ·
  7=boomerang/cliff · 8=hourglass/snowman · 9=balloon/tadpole.
- AVOID body-part pegs (a 'high-five hand' reads as a stray extra hand and
  clutters the scene) — use starfish/seahorse/hook for 5 instead.
- CONCENTRATE the threshold numbers on ONE belonging fixture (e.g. the examiner's
  scoreboard/clipboard, a single road-sign) rather than scattering peg-objects
  all over the scene. A few numbers, in one logical place.
- RANK facts ('one of the MOST heritable') → lead with the qualitative visual
  (huge vs tiny). Exact decimals live in the caption + study guide, NOT the image."""


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
    if aspect == "epidemiology":
        keys += [k for k in symbols_data.get("epidemiology_meta", {}) if not k.startswith("_")]
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
        "STRUCTURE — EPIDEMIOLOGY META-SYMBOLS: Use the recurring epidemiology "
        "meta-symbols from the legend (tug-of-war = sex ratio; crowd density = "
        "prevalence; ONE conjoined-twin pair = comorbidity, with the comorbid "
        "disorders shown as objects/symbols the twins wear/hold/are surrounded by "
        "(never extra twin bodies); weather forecaster = prognosis; family row + DNA = "
        "heritability). Encode every numeric VALUE with the number-peg system "
        "(0-9 shape pegs) as the punchline of the joke — NEVER ask the viewer to "
        "count more than ~4 objects, and NEVER just write the numeral. Ratios = "
        "relative team sizes."
    ),
}


def build_director_instruction(day, symbols_data, aspect, aspect_facts=None,
                               anchor_world=None, anchor_zone=None):
    """Build the SCENE DIRECTOR instruction (a text-model prompt).

    The director converts this aspect's clinical facts + the symbol vocabulary
    + the name-pun world into (a) a PURE-VISUAL scene description with NO
    clinical terms (so the image model has nothing to label) and (b) a numbered
    KEY mapping each visible element to its fact (used for the caption). This
    mirrors how a real Sketchy page is authored: scene brief + separate key.

    ASPECT-SCOPED + ANCHOR-THEMED: all of a chapter's images share ONE world
    (anchor_world) for recall as a set, but each occupies a DISTINCT zone
    (anchor_zone) and encodes ONLY its aspect's facts — unified, no bleeding."""
    title_he = day.get("title_he", day.get("title_en", ""))
    scoped_legend = _aspect_scoped_legend(day, aspect, symbols_data, aspect_facts)
    full_index = _compact_symbol_index(symbols_data)
    facts_text = _facts_block_for_aspect(aspect, aspect_facts)
    structure = _ASPECT_STRUCTURE.get(aspect, "")

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

{NUMBER_RULES}

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
def nb_run(args, timeout=120):
    return subprocess.run([NOTEBOOKLM_BIN] + args, capture_output=True, text=True, timeout=timeout)


def use_notebook(nb_id):
    r = nb_run(["use", nb_id], timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"notebooklm use failed: {r.stderr[:300]}")


def find_day_notebook(day_index, title_he):
    """Look up existing per-day notebook by exact title match."""
    expected_title = f"[Study-{day_index:03d}] {title_he}"
    r = nb_run(["list", "--json"], timeout=60)
    if r.returncode != 0:
        return None, None
    try:
        nbs = json.loads(r.stdout).get("notebooks", [])
    except Exception:
        return None, None
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
    criterion has room to be its own distinct visual detail."""
    jobs = []
    for aspect in aspects:
        slice_ = facts_for_aspect(aspect, facts)
        if aspect in ("dsm_criteria", "diagnostic_criteria") and isinstance(slice_, dict):
            crit = slice_.get("criteria") if isinstance(slice_.get("criteria"), dict) else None
            clusters = crit.get("clusters") if crit else None
            if isinstance(clusters, list) and len(clusters) >= 2:
                shared = {k: v for k, v in crit.items() if k != "clusters"}
                for c in clusters:
                    name = c.get("name", "cluster")
                    label = f"{aspect}_{slugify(name)}"
                    cf = {"criteria": {"cluster": name, "items": c.get("items", []), **shared}}
                    jobs.append((label, aspect, cf))
                continue
        jobs.append((aspect, aspect, slice_))
    return jobs


def add_textbook_pdfs_to_day_notebook(nb_id):
    """Upload all configured textbook PDFs to the per-day notebook so that the
    study-guide generation step can cite real page numbers. Returns the count
    of successfully added sources."""
    if not SOURCE_PDF_DIR.exists():
        print(f"  [!] SOURCE_PDF_DIR not found ({SOURCE_PDF_DIR}); skipping textbook PDFs.")
        return 0
    added = 0
    for name in SOURCE_PDF_NAMES:
        pdf = SOURCE_PDF_DIR / name
        if not pdf.exists():
            print(f"    skip (missing): {name}")
            continue
        size_mb = pdf.stat().st_size / (1024 * 1024)
        print(f"    uploading {name} ({size_mb:.1f} MB)...")
        sid = add_pdf_source(nb_id, pdf, title=pdf.stem)
        if sid:
            added += 1
            print(f"      [OK] source_id={sid[:8]}")
    return added


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

    schema_prompt = f"""Based ONLY on the sources in this notebook (DSM-5-TR, Dulcan, Stahl, Kaplan & Sadock Synopsis, syllabus), extract the high-yield facts about: {title_he}

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

    extraction_prompt = f"""Extract from this notebook (DSM-5-TR, Dulcan, Stahl Essential, Stahl Pediatric, and the syllabus) ALL the comprehensive material relevant to today's study topic. Be thorough — this extract will be the SOLE source for a focused study notebook.

Topic: {title_he}

Focus: {focus}

Aspects to cover comprehensively:
{', '.join(aspects)}

Output as a long, well-structured Markdown document in Hebrew (with English drug/receptor/concept names as they appear in source). Include:
- Page references from each source (DSM p.NNN, Dulcan p.NNN, Stahl p.NNN)
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
| נושא משנה | DSM-5-TR עמ' | Dulcan עמ' | Stahl Essential עמ' | Stahl Pediatric עמ' | סילבוס עמ' |

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
- אסמכתאות: מבוסס אך ורק על המקורות במחברת. אל תמציא עמודים — אם לא מצאת, ציין "(מקור: סילבוס/הקשר רחב)".
- הסתמך על: DSM-5-TR, Dulcan's Textbook of Child and Adolescent Psychiatry 3e, Stahl's Essential Psychopharmacology, Stahl's Children & Adolescents Prescriber's Guide, ו-הסילבוס הרשמי."""

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
        capture_output=True, text=True, env=env,
    )
    if r.returncode == 0:
        # Release exists — upload each asset with --clobber
        upload_failures = 0
        for a in assets:
            ru = subprocess.run(
                ["gh", "release", "upload", tag, str(a), "--repo", GITHUB_REPO, "--clobber"],
                capture_output=True, text=True, env=env, timeout=300,
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
            capture_output=True, text=True, env=env, timeout=600,
        )
        if not _gh_ok("release create", rc):
            return {"url": "", "assets": []}

    # Read back assets / url
    rv = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets,url",
         "--jq", "{url: .url, assets: [.assets[] | {name: .name, url: .url}]}",
         "--repo", GITHUB_REPO],
        capture_output=True, text=True, env=env,
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
        # Minimum expected: 1 (extract.md) + len(SOURCE_PDF_NAMES) PDFs
        min_expected = 1 + len([n for n in SOURCE_PDF_NAMES if (SOURCE_PDF_DIR / n).exists()])
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
            print(f"  [!] Could not determine source count; continuing without retrofit.")
    else:
        # 2. Extract focused content from MAIN notebook
        extract_md = extract_day_content_from_main(day, symbol_block, NOTEBOOK_ID)
        if not extract_md:
            print("[!] Could not extract content from main notebook. Aborting.")
            return None
        print(f"  Extracted {len(extract_md)} characters of source material.")

        # 3. Create per-day notebook
        print(f"Creating per-day notebook: {day_nb_title}")
        day_nb_id = create_day_notebook(day_nb_title)
        print(f"  Created notebook: {day_nb_id}")

        # 4a. Upload the textbook PDFs so the study guide can cite real pages.
        #     Done BEFORE the focused .md so they form the foundation.
        print(f"  Uploading textbook PDFs from {SOURCE_PDF_DIR}...")
        n_pdfs = add_textbook_pdfs_to_day_notebook(day_nb_id)
        print(f"  Added {n_pdfs} textbook PDF source(s).")

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
    gemini_images = []  # list of (label, png_path, txt_path)
    if HAS_GEMINI:
        phase = day.get("phase")
        # World override from schedule (curated) takes priority; else auto-propose.
        if day.get("anchor_world"):
            anchor_world = day["anchor_world"]
            zones = day.get("anchor_zones", {})
            print(f"  Anchor world (curated): {anchor_world}")
        else:
            anchor = propose_anchor_theme(day.get("title_he", title_en), infographic_aspects)
            anchor_world = anchor.get("world") if anchor else None
            zones = anchor.get("zones", {}) if anchor else {}
            if anchor_world:
                print(f"  Anchor world (auto): {anchor_world}")

        image_jobs = build_image_jobs(infographic_aspects, facts)
        print(f"\nGenerating {len(image_jobs)} Sketchy scene(s) via Gemini (phase {phase})...")
        for label, aspect, job_facts in image_jobs:
            png_path = day_dir / f"sketchy_{label}.png"
            txt_path = day_dir / f"sketchy_{label}.txt"
            director = build_director_instruction(
                day, symbols, aspect, job_facts,
                anchor_world=anchor_world, anchor_zone=zones.get(aspect),
            )
            # For CRITERIA images, completeness is mandatory: pass the cluster's
            # criterion items so generate_sketchy_directed runs the coverage-
            # guaranteed retry loop until every criterion has a distinct visual.
            required_items, max_tries = None, 1
            if aspect in ("dsm_criteria", "diagnostic_criteria") and isinstance(job_facts, dict):
                crit = job_facts.get("criteria")
                items = crit.get("items") if isinstance(crit, dict) else None
                if items:
                    required_items = [str(it) for it in items]
                    max_tries = 3
            print(f"  sketchy_{label}: directing scene + generating"
                  f"{f' (criteria: {len(required_items)} items, <=3 tries)' if required_items else ''}...")
            ok, caption = generate_sketchy_directed(
                director_instruction=director,
                output_path=png_path,
                caption_path=txt_path,
                phase=phase,
                required_items=required_items,
                max_image_tries=max_tries,
            )
            if ok:
                size_mb = png_path.stat().st_size / (1024 * 1024)
                cap_kb = (txt_path.stat().st_size / 1024) if txt_path.exists() else 0
                print(f"    [OK] {png_path.name} ({size_mb:.1f} MB) + caption ({cap_kb:.1f} KB)")
                gemini_images.append((label, png_path, txt_path if txt_path.exists() else None))
            else:
                print(f"    [FAIL] Gemini generation failed for sketchy_{label}")
    else:
        print("[!] gemini_image module unavailable; skipping Sketchy scenes.")

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

    # Include Gemini Sketchy scenes (image + caption .txt — both go to Release)
    for label, png_path, txt_path in gemini_images:
        assets.append(png_path)
        if txt_path:
            assets.append(txt_path)

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
                                       capture_output=True, text=True)
                    if r.returncode == 0:
                        print(f"Day {d}: release already exists ({tag}), skipping.")
                        continue
            generate_day(d, schedule, symbols, output_dir, dry_run=args.dry_run)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
