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

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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

# NotebookLM caps CREATE_ARTIFACT per day, and it does not cap every type
# equally. In one day of testing, audio and infographics both began returning
# RateLimitError while video, quiz and flashcards kept working — two chapters'
# worth of artifacts was enough.
#
# That is fatal for a 141-chapter run if those two sit on the critical path: at
# three or four a day the course lands long after the exam. So a bulk run skips
# them and backfill_audio fills them in afterwards, a few a day, earliest
# chapter first. Everything else — the study guide, the facts, the Sketchy
# plates, the narration, the quiz, the flashcards, the video — is unbounded and
# still generated in line.
SKIP_QUOTA_ARTIFACTS = os.environ.get(
    "SKIP_QUOTA_ARTIFACTS", "").strip().lower() not in ("", "0", "false", "no")

# Build a chapter with no pictures. The free image allowance runs out well
# before the day does, and a chapter is mostly not pictures: chapter 26 shipped
# 22 artifacts — the study guide, the quiz, the flashcards, the video, and a
# full legend for every plate — beside 2 PNGs. The slow, serial, separately
# limited part is pulling the facts out of NotebookLM, and that has nothing to
# do with the image quota, so a multi-day image lockout is exactly when to do
# it. Plates are then rendered in a later pass from the saved facts.json.
#
# Skipping is better than letting them fail: the director costs a text call per
# plate, and a table built now is discarded anyway, because the later pass
# regenerates the table and the legend together with the image so the two
# cannot drift apart.
SKIP_PLATES = os.environ.get(
    "SKIP_PLATES", "").strip().lower() not in ("", "0", "false", "no")

# Six textbooks cannot honestly answer a chapter brief in a paragraph, so a
# short extract means the notebook was not ready or the query failed.
#
# This lived INSIDE generate_day, which made it a local variable for the whole
# function — and the retry added above it then read it before the assignment
# line ran. Chapters 15, 16 and 17 all died on UnboundLocalError, and a syntax
# check cannot see it because the name is perfectly valid until that path
# executes. A constant belongs at module scope.
MIN_EXTRACT_CHARS = 2000

NOTEBOOKLM_BIN = os.environ.get("NOTEBOOKLM_BIN", "/opt/venv/bin/notebooklm")
# Fall back to bare 'notebooklm' (in PATH) if the absolute default doesn't exist
# — lets the script run on Windows / non-VM envs without setup.
if not Path(NOTEBOOKLM_BIN).exists():
    NOTEBOOKLM_BIN = "notebooklm"
WAIT_INDEX_S = 60
SCOPE_INDEX_WAIT_S = 60  # wait for per-aspect scope sources to index before scoped infographic gen
POLL_INTERVAL_S = 60
# Chapter 29 spent 60 of its 109 minutes here, waiting for a video, a quiz and
# a set of flashcards that never arrived, then published without them anyway.
# An hour of dead time per chapter is 141 hours across the course, paid for
# nothing. Video, quiz and flashcards normally land within a few minutes; when
# they do not, the useful thing is to record the task ids and collect them
# later, not to sit on the connection.
ARTIFACT_MAX_WAIT_S = int(os.environ.get("ARTIFACT_MAX_WAIT_S", 20 * 60))

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

# The book list in the prompt and the books actually in the notebook must
# describe the same set. A book named in a prompt but missing from the notebook
# does not produce an error - NotebookLM invents plausible page numbers for it,
# which is how a day-16 study guide came to cite "Stahl Pediatric pp. 600-612"
# in a book whose last printed page is 502. That used to be an INVARIANT
# comment asking a human to keep two constants in step. It is now built from
# one source: BOOK_BLURBS is keyed by the same labels as drive_books.json, and
# a book disabled there disappears from the prompt in the same breath.
BOOK_BLURBS = {
 "Dulcan": "**Dulcan's Textbook of Child and Adolescent Psychiatry 3e** — ספר הליבה. ברירת המחדל לכל נושא קליני בילדים ונוער.",
 "DSM-5-TR": "**DSM-5-TR** — קריטריונים אבחנתיים מדויקים.",
 "StahlNeuro": "**StahlNeuro** = Stahl's Essential Psychopharmacology 5e (Neuroscientific Basis) — מנגנון נוירוביולוגי, קולטנים, מסלולים. צטט אותו בשם המדויק **StahlNeuro**, לעולם לא \"Stahl\" לבדו.",
 "StahlKids": "**StahlKids** = Stahl's Prescriber's Guide — Children and Adolescents. צטט אותו בשם המדויק **StahlKids**, לעולם לא \"Stahl\" לבדו. מינון, טיטרציה, תופעות לוואי וניטור ספציפיים לילדים ונוער. מונוגרפיות לפי תרופה — רלוונטי רק כשהנושא כולל תרופה שמופיעה בו.",
 "Syllabus": "**הסילבוס הרשמי של המועצה המדעית** — מגדיר מה נדרש לבחינה.",
 "Kaplan & Sadock Synopsis": "**Kaplan & Sadock's Synopsis** — מקור השלמה בלבד. השתמש בו אך ורק כאשר נושא שנדרש לבחינה בילדים ונוער חסר ב-Dulcan (למשל הפרעות אישיות, שאין להן פרק ב-Dulcan). אל תייבא ממנו תוכן של פסיכיאטריה של מבוגרים שאינו רלוונטי לילדים ונוער (דמנציה/אלצהיימר, פסיכיאטריה גריאטרית, תפקוד מיני של מבוגרים וכו').",
}

BOOK_ORDER = ["Dulcan", "DSM-5-TR", "StahlNeuro", "StahlKids",
              "Syllabus", "Kaplan & Sadock Synopsis"]


def books_directive(day_index=None):
    """The prompt's source list, built from the books actually in the notebook.

    `day_index` matters for the Synopsis. It is no longer attached as a book —
    only the required-literature slices for THIS chapter go in — so naming it
    for a chapter that has no slice would tell the model it has a source it
    does not have, which does not raise: it makes NotebookLM invent citations.
    That is the failure this module already carries a comment about, and
    listing an unattached Synopsis would have reintroduced it for the hundred
    chapters outside the examiners' 39.

    Called without a day it keeps the old behaviour, which is what the
    non-chapter prompts want.
    """
    books = load_drive_books()
    if day_index is not None:
        books = {k: v for k, v in books.items()
                 if not (v.get("slices_only") and not synopsis_slices_for(day_index))}
    lines = [BOOK_BLURBS[k] for k in BOOK_ORDER if k in books and k in BOOK_BLURBS]
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1))
    ranges = ", ".join(
        f"{k} עד {books[k]['max_printed_page']}"
        for k in BOOK_ORDER if k in books and books[k].get("max_printed_page"))
    synopsis = ""
    if "Kaplan & Sadock Synopsis" in books:
        synopsis = ('\n- ל-Synopsis **אין מספרי עמודים** במהדורה שבמחברת. צטט אותו '
                    'לפי פרק בלבד: "(Synopsis ch.19 Personality Disorders)". '
                    'אל תמציא לו מספר עמוד.')
    return f"""המקורות במחברת, לפי סדר עדיפות:
{numbered}

כללי ציטוט מחייבים:
- אחרי כל טענה משמעותית ציין עמוד מקור בסוגריים: "(DSM p.68)", "(Dulcan p.173)", "(StahlKids p.301)", "(StahlNeuro p.147)".
- **מספר העמוד הוא זה שמודפס על העמוד עצמו** (בכותרת העליונה או התחתונה), לא מספר הגיליון בתוכנת קריאת PDF. השניים אינם זהים: לקבצים יש שערים ותוכן עניינים לפני עמוד 1, ולכן מספר ה-PDF גדול יותר. צטט תמיד לפי המספר המודפס.
- טווחי העמודים המודפסים בספרים שבמחברת: {ranges}. עמוד מחוץ לטווח הוא בהכרח שגוי.{synopsis}
- **שני ספרי Stahl שונים במחברת**: StahlNeuro (מנגנון, עד עמ' 623) ו-StahlKids (מינונים לילדים, עד עמ' 502). ציטוט "Stahl" בלי הסיומת הוא שגוי — הוא לא מזהה איזה ספר, ואי אפשר לאמת אותו.
- אל תמציא עמודים באף מקור. אם לא מצאת — כתוב "(מקור: הקשר רחב)"
"""


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
    (see BOOK_BLURBS), NOT from the cartoon symbols.
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

{books_directive(day["index"])}

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
**בעברית** — הווינייטה, האופציות וההסבר. באנגלית רק שמות תרופות גנריים, קולטנים
(D2, 5-HT2A), מונחים קליניים (EPS, NMS, SSRI) וקיצורי DSM/ICD.
רמה: בחינת שלב ב'. דגש על:
- vignettes קליניים (מקרה אמיתי בן 3-5 שורות) שדורשים אינטגרציה
- אבחנה מבדלת
- בחירה בטיפול אופטימלי
- ניהול סיבוכים נדירים אבל חשובים
- תופעות לוואי + אינטראקציות תרופתיות

**התשובה הנכונה — קריטי**: פזר אותה בין ארבע האופציות. במידה שהתשובה הנכונה היא
תמיד הראשונה, מי שמסמן את הראשונה בכל שאלה מקבל ציון מלא בלי לדעת כלום, והקוויז
מפסיק למדוד ידע. אל תהפוך את התשובה הנכונה לאופציה הארוכה ביותר: הסבר מפורט שייך
לשדה ההסבר, לא לאופציה. המסיחים צריכים להיות באורך דומה ולהיות שגויים מסיבה קלינית,
לא מפני שהם קצרים או מעורפלים.

**איסור מוחלט**: אל תשאל על סמלים חזותיים של ה-Sketchy. אלו שאלות-בחינה קליניות,
לא חידות-מנמוניקה."""


# ── notebooklm CLI helpers ──────────────────────────────────────────────────
# Which notebooklm profile to drive. The bulk run uses a SEPARATE Google
# account from the shared VM so its daily generation quota is its own; the
# profile is how that account is selected, and forgetting it silently drives the
# other project's account instead.
NOTEBOOKLM_PROFILE = os.environ.get("NOTEBOOKLM_PROFILE", "").strip()


# NotebookLM streams a chat answer as cumulative frames: every frame carries the
# whole answer so far plus its citations, so bytes on the wire grow with the
# SQUARE of the answer. A 3,000-6,000-word extract with "lots" of citations
# fits for most chapters and overflows the client's 256MB buffer on
# drug-dense ones — FGA, SGA and Clozapine (58-60) failed in a row on it, and
# 42, 46 and 49 had failed on it before passing on a retry near the edge.
RESPONSE_TOO_LARGE = ("RPCResponseTooLargeError", "RPC response exceeded")


class AuthExpired(RuntimeError):
    """The NotebookLM session died. Nothing downstream can succeed."""


class ChatUnavailable(RuntimeError):
    """NotebookLM's chat is returning empty streams. Retrying will not help."""


def nb_run(args, timeout=120, retries=2):
    """Run the CLI, retrying a run that comes back empty.

    The CLI occasionally exits 0 having written nothing at all — seen once on
    `create`, where the caller then died on `r.stdout.strip()` because stdout
    was None. Over 141 chapters an intermittent empty result is a certainty,
    and the peer project lost whole episodes to exactly this class of silent
    failure, so retry here rather than at every call site.
    """
    # The comment above this module's NOTEBOOKLM_PROFILE has warned about the
    # empty default since it was written, and a background run dropped the
    # variable anyway and aimed the whole queue at `default`. A warning in a
    # comment is not a guard, so refuse here — this is the one funnel every
    # NotebookLM call passes through, and the scripts that only render plates
    # import this module without ever reaching it.
    if not NOTEBOOKLM_PROFILE:
        raise RuntimeError(
            "NOTEBOOKLM_PROFILE is not set; an empty profile drives the "
            "`default` account, which belongs to another project. "
            "Set NOTEBOOKLM_PROFILE=bulk before any notebooklm call.")
    cmd = [NOTEBOOKLM_BIN, "-p", NOTEBOOKLM_PROFILE]
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
        # The CLI tells us when a write's outcome is unknown: the RPC timed
        # out, and its idempotency probe found the source WAS created server
        # side. Retrying that is how the Kaplan & Sadock attach ended up with
        # three copies in one notebook, two of them wedged in `error`. An
        # unconfirmed write is for the caller to reconcile against the source
        # list, never for this layer to repeat.
        if "UNCONFIRMED_WRITE" in r.stdout or '"unconfirmed": true' in r.stdout:
            print(f"    [!] notebooklm {args[0]}: write unconfirmed, NOT retrying "
                  f"(would duplicate)", file=sys.stderr)
            return r
        # A quota is not a blip. Audio generation is capped per day, and
        # hammering the cap three times in two seconds spends the budget on
        # nothing and can push the limiter further out. Fail immediately and
        # let the caller record that this chapter still owes an artifact.
        blob = r.stdout + r.stderr
        # An answer too large to buffer is a property of the QUESTION, not a
        # blip. Chapter 58 asked three times and each attempt streamed the full
        # 256MB before the guard fired — minutes apiece, identical outcome.
        # The caller has to ask a smaller question; see RESPONSE_TOO_LARGE.
        if any(m in blob for m in RESPONSE_TOO_LARGE):
            print(f"    [!] notebooklm {args[0]}: response over the size cap, "
                  f"NOT retrying (the same question overflows again)",
                  file=sys.stderr)
            return r
        if "RateLimit" in blob or "RESOURCE_EXHAUSTED" in blob or "quota" in blob.lower():
            print(f"    [!] notebooklm {args[0]}: rate limited, NOT retrying",
                  file=sys.stderr)
            return r
        # A dead session does not come back inside a retry loop, and retrying
        # into it is expensive: `ask` waits 900s per attempt, three attempts per
        # call, several calls per chapter. Chapter 2 lost its session partway
        # through and ground on for 566 MINUTES before failing — nine and a half
        # hours of calls that could not have succeeded. Fail immediately; the
        # caller aborts the chapter and bulk_run stops the queue.
        if any(m in blob for m in ("Authentication expired or invalid",
                                   "Run 'notebooklm login'")):
            raise AuthExpired(f"notebooklm {args[0]}: session is dead")
        # The chat endpoint answering with an empty stream. Seen after a heavy
        # day: `ask` failed on every notebook on this account, including ones
        # that had answered an hour earlier, while create, source list and
        # source add-text all kept working. Whether that is Google throttling
        # chat or the wire format having changed, hammering it is wrong either
        # way — chapters 12, 13 and 14 each retried this eighteen times and
        # spent fifteen minutes apiece doing it.
        if "No parseable chunks" in blob or "wire format may have changed" in blob:
            raise ChatUnavailable(
                f"notebooklm {args[0]}: chat returned an empty stream")
        if attempt < retries:
            # Print WHAT it said, not just how long it was. Chapters 12, 13 and
            # 14 each failed on the same 203-byte error repeated eighteen times,
            # and because only the byte count was logged, nobody could see what
            # it was — three chapters lost to an error that never once named
            # itself.
            body = " ".join((r.stdout or r.stderr or "").split())
            print(f"    [!] notebooklm {args[0]} rc={r.returncode}: "
                  f"{body[:200]}; retrying ({attempt + 2}/{retries + 1})",
                  file=sys.stderr)
            time.sleep(8 * (attempt + 1))
    return last


def use_notebook(nb_id):
    os.environ["_CURRENT_NB"] = str(nb_id or "")
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
        # BOTH streams. The CLI prints "Authentication expired or invalid" to
        # STDOUT, so quoting stderr alone turned a dead session into a bare
        # "rc=2": chapters 4, 5 and 6 all died of expired auth and none of
        # their logs said so. Only the three-in-a-row circuit breaker caught
        # it, and it could not say why it had stopped.
        detail = ((r.stdout or "") + " " + (r.stderr or "")).strip()
        raise RuntimeError(f"could not list notebooks: rc={r.returncode} "
                           f"{detail[:300]}")
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
    epidemiology images. Epidemiology is now one aspect of the chapter's
    profile plate — see sketchy_spec.plan_plates for the criteria / profile /
    management split that replaces per-aspect jobs wholesale."""
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


# The six textbooks live in the owner's Drive and are attached to every
# per-day notebook BY REFERENCE. The mapping is by Drive FILE ID, not by
# filename and not by search string:
#
#   - filenames are whatever the owner's downloads were called
#     ("Emailing Dulcans Textbook of Child and Adolescent Psychiatry, 3e.pdf"),
#     so the tidy names this file used to assume matched nothing;
#   - `source add-research --from drive` looked like it worked and did not. It
#     attached the drive.google.com URL as a WEB PAGE, NotebookLM's crawler
#     fetched it unauthenticated, and every "textbook" was 544 characters of
#     "Sign in to continue to Google Drive". Six green [OK] lines, status
#     "ready", and a Google login form as the source of a psychiatry course.
#
# `source add-drive <file-id> --mime-type pdf` attaches the real document:
# type "pdf", drive_file_id set, and the notebook answers with correct page
# numbers out of the book itself.
DRIVE_BOOKS_PATH = REPO_ROOT / "schedule" / "drive_books.json"


def load_drive_books(include_disabled=False):
    """The textbooks to attach. A book with enabled=false is skipped entirely.

    One switch, because a book that cannot be attached must also vanish from
    the prompt: naming a source the notebook does not have is what produces
    invented page numbers.
    """
    with open(DRIVE_BOOKS_PATH, encoding="utf-8") as fh:
        books = json.load(fh)
    if include_disabled:
        return books
    return {k: v for k, v in books.items() if v.get("enabled", True)}


SYNOPSIS_SLICES = REPO_ROOT / "schedule" / "synopsis_slices"


def synopsis_slices_for(day_index):
    """The Synopsis extracts this chapter is required to be taught from.

    The examiners' own list (P024-2026) names 14 Synopsis chapters that Dulcan
    under-covers, and a peer session cut each into its own PDF and mapped them
    to curriculum chapters. That map is authoritative, and it is wider than the
    one I inferred from the reading guide: I had chapters 12-14 needing the
    Synopsis, the official list has eleven already-built chapters needing it.

    Attaching the exact slice beats attaching the book. Each is 31-257 pages
    and under 4MB, so it uploads from disk in seconds — the objection to local
    attachment was the whole book's 42MB per notebook, which does not apply
    here — and the notebook holds only the material the chapter is meant to
    cite, instead of 3,768 pages of mostly adult psychiatry.
    """
    man = SYNOPSIS_SLICES / "manifest.json"
    if not man.exists():
        return []
    try:
        slices = json.loads(man.read_text(encoding="utf-8")).get("slices", [])
    except Exception:                # noqa: BLE001
        return []
    out = []
    for sl in slices:
        if day_index in (sl.get("curriculum") or []):
            f = SYNOPSIS_SLICES / sl["file"]
            if f.exists():
                out.append((f, sl.get("ref", "?"), sl.get("pages")))
            else:
                print(f"    [!!] Synopsis slice missing on disk: {sl['file']} "
                      f"(regenerate with scripts/build_synopsis_slices.py)")
    return out


def attach_synopsis_slices(nb_id, day_index, attached, existing=None):
    """Attach this chapter's Synopsis extracts, if it has any.

    `existing` maps a slice title to a source id already in the notebook. A
    resumed chapter must not re-upload what is already there — that is how the
    Kaplan attach ended up with three copies in one notebook — but it must
    still register them in `attached`, or the index wait and the rename walk
    straight past sources the chapter is about to cite.
    """
    wanted = synopsis_slices_for(day_index)
    if not wanted:
        return 0
    existing = existing or {}
    got = 0
    for path, ref, pages in wanted:
        title = f"Synopsis ch.{ref}"
        if title in existing:
            attached[existing[title]] = title
            got += 1
            print(f"    [=] {title} already attached")
            continue
        r = nb_run(["source", "add", str(path), "--title", title,
                    "--mime-type", "application/pdf", "-n", nb_id, "--json"],
                   timeout=600)
        sid = None
        if r.returncode == 0:
            try:
                sid = (json.loads(r.stdout[r.stdout.index("{"):])
                       .get("source") or {}).get("id")
            except Exception:        # noqa: BLE001
                pass
        if sid:
            attached[sid] = title
            got += 1
            print(f"    [OK] {title} ({pages}pp)")
        else:
            print(f"    [!!] {title}: not attached")
    return got


def add_textbook_pdfs_to_day_notebook(nb_id, only=None):
    """Attach all six textbooks to the per-day notebook by Drive reference.

    Returns {source_id: label} for the books that attached as genuine Drive
    PDFs. The caller MUST treat a short map as fatal: a book named in the
    prompt but missing from the notebook does not error, it makes NotebookLM
    invent page numbers.

    The id map is what lets a failed index be repaired without guessing. Two of
    the six books are Stahl titles, so matching a NotebookLM source title back
    to a Drive file by name can pick the wrong one, and re-attaching the wrong
    book is worse than not repairing at all.
    """
    books = load_drive_books()
    if only is not None:
        books = {k: v for k, v in books.items() if k in only}
    attached = {}
    for label, meta in books.items():
        # Listed in the prompt, attached as per-chapter slices instead — see
        # attach_synopsis_slices. It stays in the book list so the citation
        # rules still name it; it must not be attached whole.
        if meta.get("slices_only"):
            continue
        # A book may live on disk instead of in Drive. The Synopsis has to:
        # at 3,768 pages NotebookLM refuses to index it, silently, with no
        # reason given — and the largest book that DOES index is Dulcan at
        # 1,677. Split into three parts of 1,256 it goes through, and
        # `source add` takes the local file directly, so nothing needs
        # uploading to Drive at all.
        # A book may be several files. The Synopsis has to be: at 3,768 pages
        # NotebookLM refuses to index it — `error`, no reason given, on two
        # accounts and two uploads — while the largest book that DOES index is
        # Dulcan at 1,677. In thirds it goes through.
        drive_parts = meta.get("drive_parts") or []
        if drive_parts:
            ids = []
            for i, fid in enumerate(drive_parts, 1):
                title = f"{label} ({i}/{len(drive_parts)})"
                rp = nb_run(["source", "add-drive", fid, title, "-n", nb_id,
                             "--mime-type", "pdf", "--json"], timeout=300)
                sid = None
                if rp.returncode == 0:
                    try:
                        sp = (json.loads(rp.stdout[rp.stdout.index("{"):])
                              .get("source") or {})
                        if sp.get("type") == "pdf" and sp.get("drive_file_id"):
                            sid = sp.get("id")
                    except Exception:
                        pass
                if sid:
                    ids.append(sid)
                else:
                    print(f"    [!!] {label} part {i}: not attached")
            got = len(ids)
            # ALL OR NOTHING. Registering the parts that did land would let a
            # two-thirds-missing Synopsis count as the book being present —
            # the caller only checks that every book is represented, and the
            # prompt would go on naming a source the notebook mostly lacks,
            # which is the arrangement that makes NotebookLM invent citations.
            if got == len(drive_parts):
                for sid in ids:
                    attached[sid] = label
            # Every part, or the book does not count. A half-attached textbook
            # is the worst case: the prompt names it, the notebook holds some
            # of it, and the gap is invisible to every check downstream.
            print(f"    [{'OK' if got == len(drive_parts) else '!!'}] {label} "
                  f"({got}/{len(drive_parts)} parts)")
            continue

        parts = meta.get("local_parts") or ([meta["local_path"]]
                                            if meta.get("local_path") else [])
        if parts:
            # ONE BOOK, SEVERAL FILES. The Synopsis is 3,768 pages and
            # NotebookLM will not index it — `error`, no reason given, on two
            # accounts and two uploads — while the largest book that does index
            # is Dulcan at 1,677. In thirds it goes through. `source add` takes
            # the local file, so none of this has to reach Drive.
            got = 0
            for i, rel in enumerate(parts, 1):
                path = rel if os.path.isabs(rel) else str(REPO_ROOT / rel)
                if not os.path.exists(path):
                    print(f"    [!!] {label} part {i}: missing at {path}")
                    continue
                title = label if len(parts) == 1 else f"{label} ({i}/{len(parts)})"
                rp = nb_run(["source", "add", path, "--title", title,
                             "--mime-type", "application/pdf",
                             "-n", nb_id, "--json"], timeout=900)
                sid = None
                if rp.returncode == 0:
                    try:
                        sid = (json.loads(rp.stdout[rp.stdout.index("{"):])
                               .get("source") or {}).get("id")
                    except Exception:
                        pass
                if sid:
                    attached[sid] = label
                    got += 1
                else:
                    print(f"    [!!] {label} part {i}: not attached")
            # A part-attached book counts as present only if EVERY part landed.
            # A half-attached textbook is the worst case: the prompt names it,
            # the notebook has some of it, and the gap is invisible.
            print(f"    [{'OK' if got == len(parts) else '!!'}] {label} "
                  f"({got}/{len(parts)} parts)")
            continue

        r = nb_run(["source", "add-drive", meta["drive_file_id"], label,
                    "-n", nb_id, "--mime-type", "pdf", "--json"], timeout=300)
        ok = False
        if r.returncode == 0:
            try:
                data = json.loads(r.stdout[r.stdout.index("{"):])
                src = data.get("source") or {}
                # A real Drive PDF has type "pdf" and a drive_file_id. A source
                # that came back as a web_page is the login-page failure above.
                # A local upload has no drive_file_id, and demanding one
                # would reject the very books that can only be attached that
                # way. The failure this check exists to catch — a Drive URL
                # ingested as a web page of "Sign in to continue" — cannot
                # happen on an upload, so type alone is the right test there.
                ok = src.get("type") == "pdf" and bool(src.get("drive_file_id"))
                if not ok:
                    print(f"    [!!] {label}: attached as {src.get('type')!r}, "
                          f"not a Drive PDF")
            except Exception as e:
                print(f"    [!!] {label}: could not parse response: {e}")
        else:
            print(f"    [!!] {label}: rc={r.returncode} {(r.stderr or '')[:120]}")
        if ok:
            # RENAME IT TO OUR LABEL. NotebookLM titles a Drive source with the
            # Drive FILENAME, overwriting the title we attach it with once
            # indexing finishes — which is the whole reason _names_book below
            # has to guess a book from distinctive words in its filename, and
            # why "2 of 5 books" was reported forever on a notebook that was
            # answering perfectly well.
            #
            # The rename sticks, so the mismatch can be removed at its source
            # rather than compensated for downstream. It is best-effort: a
            # source that keeps its Drive name still works, because
            # _names_book still accepts the filename.
            print(f"    [OK] {label}")
            attached[src.get("id")] = label
    return attached


def rename_sources_to_labels(nb_id, attached):
    """Retitle each source to our label — AFTER indexing, not before.

    Renaming right after attaching looks like it works and does not: the title
    survives until indexing finishes, and NotebookLM then overwrites it with
    the Drive FILENAME. My first version did exactly that, and the test that
    passed was run against a source that was already `ready`, so it measured
    the one condition under which the bug cannot appear.

    Why bother: _names_book below has to recognise a book from distinctive
    words in its filename, behind a hardcoded stopword list, purely because the
    notebook does not call the books what we call them. Getting the titles
    right removes the reason that function exists.

    Best-effort — a source that keeps its Drive name still works.
    """
    done, bad = 0, []
    for sid, label in (attached or {}).items():
        try:
            # ASSERT ON THE STATE, NOT THE ATTACH. `source wait` exits 0=ready,
            # 1=failed, 2=timeout, and the first version of this threw that
            # away — which is precisely the Kaplan failure it was written after:
            # add-drive returns rc=0 with type=pdf while indexing goes to
            # `error`, so a book can be attached, absent, and reported as fine.
            # A peer project lost a 30-minute episode to the same shape, from
            # sources that were empty, and its QC judge passed it because a
            # judge cannot see that there was nothing there.
            w = nb_run(["source", "wait", sid, "-n", nb_id], timeout=900)
            if w.returncode != 0:
                bad.append(label)
                continue
            r = nb_run(["source", "rename", sid, label, "-n", nb_id], timeout=120)
            done += (r.returncode == 0)
        except Exception as e:      # noqa: BLE001
            bad.append(f"{label} ({str(e)[:40]})")
    if done:
        print(f"    [i] retitled {done}/{len(attached)} sources to our labels")
    if bad:
        raise RuntimeError(
            "these sources never reached a usable state, so the notebook does "
            "not hold the books the prompt will name: " + ", ".join(sorted(set(bad))))
    return done


def _names_book(answer: str, label: str) -> bool:
    """Does this answer refer to that book, however it chooses to name it?

    NotebookLM shows a source under its DRIVE FILENAME, not under the label we
    attach it with, so matching on the label alone became impossible the moment
    the two Stahl books were renamed StahlNeuro and StahlKids: nothing in the
    notebook is called either of those. The probe then counted 2 of 5 books
    forever and every chapter died on a notebook that was answering perfectly
    well — 1,739 characters of it.

    So a book counts as named if the answer uses our label OR any distinctive
    word from its real filename.
    """
    a = (answer or "").lower()
    book = load_drive_books().get(label, {})
    needles = {label.lower(), label.lower().split()[0]}
    for w in re.findall(r"[a-z]{6,}", (book.get("drive_title") or "").lower()):
        if w not in ("essential", "psychopharmacology", "textbook", "emailing"):
            needles.add(w)          # neuroscientific, prescribers, adolescent...
    return any(n in a for n in needles if len(n) > 3)


def wait_until_answerable(nb_id, books=None, tries=6, first_wait=45):
    """Block until the notebook can actually ANSWER from its books.

    `ready` is not queryable. Chapter 29's clean run attached five textbooks,
    watched all five report `ready`, asked its extraction question immediately
    and got 1,185 characters back. The same notebook, asked the same kind of
    question a few minutes later, returned 7,300 characters of correctly cited
    content. Nothing had changed but time.

    So the status flag is not the thing to trust - the capability is. This asks
    a cheap question whose answer must name several of the books, and waits
    until it does. Without it the MIN_EXTRACT_CHARS gate turns a settle lag
    into a dead chapter, and before that gate existed it turned into a chapter
    built on a paragraph.
    """
    books = books or list(load_drive_books())
    q = ("Name each source document in this notebook and give one specific fact "
         "with its printed page number from each. Be brief.")
    wait = first_wait
    for attempt in range(tries):
        r = nb_run(["ask", q, "--json"], timeout=300)
        answer = ""
        if r.returncode == 0:
            try:
                answer = json.loads(r.stdout).get("answer", "") or ""
            except Exception:
                answer = ""
        named = sum(1 for b in books if _names_book(answer, b))
        if len(answer) >= 800 and named >= max(2, len(books) - 2):
            print(f"    notebook is answering ({len(answer)} chars, "
                  f"{named}/{len(books)} books named)")
            return True
        print(f"    not answering yet ({len(answer)} chars, {named}/{len(books)} "
              f"books named); waiting {wait}s", flush=True)
        if attempt == tries - 1:
            break
        time.sleep(wait)
        wait = min(int(wait * 1.6), 240)
    return False


def _list_sources(nb_id):
    """Every source in the notebook, or None if we could not find out.

    The distinction is the whole point, and the first version threw it away.
    On failure the CLI prints {"error": true, ...} to STDOUT and exits 1; that
    parses perfectly, so `.get("sources", [])` returned an empty LIST rather
    than None - "the notebook has no sources" instead of "I could not read the
    notebook". Both callers test `is None`, so both were defeated at once: the
    resume path concluded every book was missing and re-attached all six on top
    of the six already there, and the indexing wait concluded that all zero
    sources had finished and returned True.

    `source list --json` is more fragile than it looks. It makes a second RPC
    (GET_NOTEBOOK) purely to fill in a cosmetic title field, so it can exit 1
    while the source listing itself succeeded - a persistent, every-chapter
    failure rather than a blip.
    """
    r = nb_run(["source", "list", "-n", nb_id, "--json"], timeout=60)
    if r.returncode != 0:
        print(f"  [!] source list failed rc={r.returncode} for {nb_id}",
              file=sys.stderr)
        return None
    try:
        data = json.loads(r.stdout)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("error"):
        print(f"  [!] source list returned an error object for {nb_id}",
              file=sys.stderr)
        return None
    if "sources" not in data:
        return None
    return data["sources"]


def wait_for_sources_indexed(nb_id, attached=None, timeout=900,
                             interval=15, repair=True):
    """Block until every source in the notebook has finished processing.

    A freshly attached Drive PDF is not queryable for a while. Extracting
    before indexing completes does not error - it returns a near-empty answer,
    which is how chapter 29 produced 845 characters of "source material" from
    six textbooks and would have built its whole plate set on nothing.

    Polls the notebook as a WHOLE rather than waiting on each source in turn.
    The per-source wait was serial, so six books cost up to six timeouts back
    to back, and it could not see the one status that matters: `error` is
    terminal, and the first run spent its entire 420s budget waiting for a
    Kaplan & Sadock that had already failed. A failed source is retried once by
    detaching and re-attaching it, because a big PDF losing its first indexing
    attempt is a thing that will happen across 141 chapters and is not a reason
    to lose the chapter.
    """
    deadline = time.time() + timeout
    retried = set()
    while True:
        sources = _list_sources(nb_id)
        if sources is None:
            print("  [!] could not list sources to wait on", file=sys.stderr)
            return False
        if not sources:
            # "Every source finished indexing" is vacuously true of a notebook
            # with no sources, and returning True here would certify an empty
            # notebook as ready to answer questions about six textbooks.
            print(f"  [!] {nb_id} has no sources at all", file=sys.stderr)
            return False

        pending = [s for s in sources if (s.get("status") or "").lower()
                   not in ("ready", "error", "failed")]
        broken = [s for s in sources if (s.get("status") or "").lower()
                  in ("error", "failed")]

        for src in broken:
            sid, title = src.get("id"), (src.get("title") or "?")
            book = _book_for_source(sid, title, attached)
            if not repair or sid in retried or not book:
                continue
            retried.add(sid)
            print(f"    RETRY    {title[:44]} (indexing failed, re-attaching)")
            # -y is not optional: without it the CLI prompts, reads EOF from a
            # detached run and exits 2. The delete then "succeeds" silently and
            # the re-attach below leaves a duplicate instead of a replacement.
            rm = nb_run(["source", "delete", sid, "-n", nb_id, "-y"], timeout=90)
            if rm.returncode != 0:
                print(f"    [!!] could not detach the failed source "
                      f"(rc={rm.returncode}); not re-attaching, it would "
                      f"duplicate", file=sys.stderr)
                continue
            # Go through the attach helper rather than calling the CLI raw: it
            # is the thing that checks the source came back as a real Drive PDF
            # rather than a web_page, which is how six Google login pages once
            # entered a notebook reporting status "ready". Firing and forgetting
            # here would delete a book and then not notice it never came back.
            got = add_textbook_pdfs_to_day_notebook(nb_id, only=[book["label"]])
            if not got:
                print(f"    [!!] {book['label']}: re-attach failed; the book is "
                      f"now MISSING from {nb_id}", file=sys.stderr)
                return False
            attached = dict(attached or {})
            attached.update(got)
            pending.append(src)  # give the fresh copy a chance this round

        if not pending:
            still = [(s.get("title") or "?")[:44] for s in sources
                     if (s.get("status") or "").lower() != "ready"]
            for s in sources:
                print(f"    {(s.get('status') or '?'):8} {(s.get('title') or '?')[:44]}")
            if still:
                print(f"  [!] {len(still)} source(s) never indexed: {', '.join(still)}",
                      file=sys.stderr)
                return False
            return True

        if time.time() >= deadline:
            for s in sources:
                print(f"    {(s.get('status') or '?'):8} {(s.get('title') or '?')[:44]}")
            print(f"  [!] indexing timed out after {timeout}s with "
                  f"{len(pending)} source(s) still processing", file=sys.stderr)
            return False
        time.sleep(interval)


def _norm(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def _book_for_source(sid, title, attached=None):
    """Resolve a notebook source back to its drive_books entry.

    Prefers the id map recorded when the book was attached. Falls back to an
    exact drive_title prefix comparison, never a label guess: "Stahl Pediatric"
    and "Stahl Essential" share every word a loose match would key on.
    """
    books = load_drive_books()
    if attached and sid in attached:
        label = attached[sid]
        if label in books:
            return {**books[label], "label": label}
    t = _norm(title)
    if not t:
        return None
    # NotebookLM keeps the display name we pass on some sources and the Drive
    # filename on others, so both have to be checked. Labels are exact: the
    # Syllabus arrives titled "Syllabus" while its file is
    # "cylabusKidsandAdolePsychiatry2022V2_0.pdf", and matching only the
    # filename made an already-attached book look missing and attach twice.
    for lbl, b in books.items():
        if _norm(lbl) == t:
            return {**b, "label": lbl}
    if len(t) < 10:
        return None
    # Compare as much of the title as both sides have. The two Stahl titles
    # agree for 32 characters and only then diverge, so a short window matches
    # both and the ambiguity guard below throws the repair away.
    hits = []
    for lbl, b in books.items():
        d = _norm(b.get("drive_title"))
        if not d:
            continue
        n = min(len(d), len(t), 40)
        if n >= 10 and d[:n] == t[:n]:
            hits.append({**b, "label": lbl})
    return hits[0] if len(hits) == 1 else None


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


# The fact schema used to be one fixed disorder-shaped template - criteria,
# epidemiology, etiology, treatment - handed to every chapter with a line
# asking the model to "omit sections that don't apply". It does not omit them.
# Chapter 29 is "SSRIs, in depth", with eight declared aspects and none of them
# diagnostic; it came back with the DSM criteria for major depression and the
# epidemiology of depression, because those are what the template asked for.
# The mnemonic plates are built from these keys, so that chapter's criteria
# plate would have taught MDD criteria in a venue punning on SSRIs.
#
# Only 61 of the 144 chapters are disorder-shaped. The other 83 - 28 drug and
# pharmacology chapters, 55 on development, examination, ethics and the rest -
# were all being asked the same four disorder questions.
#
# So the schema is now BUILT FROM the chapter's own declared aspects, in code,
# rather than chosen by the model. The curriculum already says what each
# chapter covers; this makes the extraction ask for exactly that. 161 distinct
# aspects appear across the course, so the well-known ones get a rich shape and
# everything else gets a generic one rather than being left out.
_RICH_SECTIONS = {
    "criteria": ('"criteria": {"clusters": [{"name": "<cluster>", "items": '
                 '["<symptom>", "..."]}], "thresholds": "<e.g. >=5/9 symptoms, '
                 '>=2 weeks>", "subtypes": ["<presentation>", "..."]}'),
    "epidemiology": ('"epidemiology": {"prevalence": "<e.g. ~5% children>", '
                     '"sex_ratio": "<e.g. 2:1 M:F>", "onset_age_typical": '
                     '"<peak onset>", "heritability": "<e.g. ~74%>", '
                     '"comorbidities": ["<e.g. ODD ~50%>"], "prognosis": '
                     '"<e.g. ~60% persist>"}'),
    "etiology": ('"etiology": {"genetic": "<short>", "neurobiology": "<short - '
                 'receptors, regions, circuits>", "environmental": "<short>"}'),
    "treatment": ('"treatment": {"first_line": ["<drug or therapy + key '
                  'detail>"], "second_line": ["<...>"], "key_side_effects": '
                  '["<...>"], "monitoring": "<short>"}'),
    "drug_class": ('"drug_class": {"members": ["<drug + the one detail that '
                   'separates it>"], "mechanism": "<receptor/transporter '
                   'level>", "kinetics": ["<half-life, CYP, linearity>"], '
                   '"onset_of_effect": "<short>"}'),
}

# Which rich section each well-known aspect asks for. Anything not here gets
# the generic shape keyed by the aspect's own name.
_ASPECT_SECTION = {
    "dsm_criteria": "criteria", "diagnostic_criteria": "criteria",
    "dsm_general_criteria": "criteria", "criterion_A": "criteria",
    "epidemiology": "epidemiology",
    "etiology": "etiology", "etiology_genetic": "etiology",
    "genetic_basis": "etiology", "genetics": "etiology",
    "treatment_overview": "treatment", "treatment_first_line": "treatment",
    "treatment_second_line": "treatment", "management": "treatment",
    "drug_class_overview": "drug_class", "drug_mechanism": "drug_class",
}


def build_facts_schema(aspects):
    """(schema_text, allowed_keys) for this chapter's declared aspects."""
    parts, allowed, seen = [], [], set()
    for a in aspects:
        sec = _ASPECT_SECTION.get(a)
        if sec:
            if sec in seen:
                continue
            seen.add(sec)
            parts.append("  " + _RICH_SECTIONS[sec])
            allowed.append(sec)
        else:
            if a in seen:
                continue
            seen.add(a)
            parts.append(f'  "{a}": {{"items": ["<concrete fact, with the '
                         f'number if the sources give one>", "..."], '
                         f'"key_numbers": ["<value + what it counts>", "..."]}}')
            allowed.append(a)
    parts.append('  "high_yield": ["<exam pearl>", "<exam pearl>", "..."]')
    allowed.append("high_yield")
    return "{\n" + ",\n".join(parts) + "\n}", allowed


def extract_facts(day, notebook_id):
    """Extract STRUCTURED facts (the source of truth for the Sketchy images)
    from the per-day notebook as JSON. Each aspect's image is later built ONLY
    from its slice of this dict — guaranteeing high resolution and zero
    bleeding between images. Returns a dict (possibly partial) or {}."""
    title_he = day.get("title_he", day.get("title_en", ""))
    aspects = day.get("aspects", [])

    shape, allowed = build_facts_schema(aspects)
    schema_prompt = f"""Based ONLY on the sources in this notebook, extract the high-yield facts about: {title_he}

{books_directive(day["index"])}

Return STRICT JSON (no prose, no markdown fences) with this exact shape. Fill every field from the sources; if a field is genuinely not in the sources, use the string "לא צוין". Keep values SHORT and concrete (numbers, names) — this drives mnemonic image generation.

Every key below is about **{title_he}** itself. Do not substitute facts about a
disorder that this chapter's subject happens to treat, or about a broader topic
it belongs to. Use exactly these keys and no others.

{shape}

Output ONLY the JSON object."""

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
    except Exception as e:
        print(f"  [!] facts JSON parse failed: {e}. Raw head: {answer[:160]}")
        return {}
    if not isinstance(facts, dict):
        return {}
    # Keep only what this chapter asked for. The old prompt asked the model to
    # omit irrelevant sections and it simply did not - chapter 29 returned the
    # DSM criteria for major depression under a schema that never requested
    # them. Every unrequested key becomes legend rows and a mnemonic plate, so
    # dropping them here is the difference between a chapter about SSRIs and a
    # chapter about depression wearing an SSRI venue.
    bad = _impossible_citations(json.dumps(facts, ensure_ascii=False))
    if bad:
        eg = ", ".join(dict.fromkeys(b["claim"] for b in bad))[:120]
        print(f"  [!] facts.json cites {len(bad)} page(s) that cannot exist "
              f"({eg}); they will reach the legend", file=sys.stderr)
    extra = [k for k in facts if k not in allowed]
    if extra:
        print(f"  [!] dropping {len(extra)} section(s) this chapter did not ask "
              f"for: {', '.join(extra)}", file=sys.stderr)
        facts = {k: v for k, v in facts.items() if k in allowed}
    return facts


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

    def prompt_for(group, words):
        part = ("" if group == aspects else
                "\n\nThis is ONE PART of a larger extract; other parts cover the "
                "remaining aspects. Cover ONLY the aspects listed here, and do "
                "not open with an overview of the whole topic.")
        return f"""Extract from the sources in this notebook ALL the comprehensive material relevant to today's study topic. Be thorough — this extract will be the SOLE source for a focused study notebook.{part}

{books_directive(day["index"])}

Topic: {title_he}

Focus: {focus}

Aspects to cover comprehensively:
{', '.join(group)}

Output as a long, well-structured Markdown document in Hebrew (with English drug/receptor/concept names as they appear in source). Include:
- Page references from each source (DSM p.NNN, Dulcan p.NNN, Stahl p.NNN); Synopsis by chapter only
- Direct citations / paraphrases (lots — this is the source material)
- All clinically relevant detail (criteria, epidemiology, etiology, treatment, monitoring, special considerations for pediatric population)
- Cross-references between sources where they agree/disagree

Length: long and thorough — {words}. This is study material, not a summary."""

    def ask(group, words):
        """(answer, overflowed). answer is None on any failure."""
        r = nb_run(["ask", prompt_for(group, words), "--json"], timeout=600)
        if r.returncode != 0:
            blob = (r.stdout or "") + (r.stderr or "")
            if any(m in blob for m in RESPONSE_TOO_LARGE):
                return None, True
            print(f"  [!] extraction ask failed: {r.stderr[:200]}")
            return None, False
        try:
            return json.loads(r.stdout).get("answer", ""), False
        except Exception as e:
            print(f"  [!] extraction parse failed: {e}")
            return None, False

    def ask_split(group):
        """Halve the group until each part's answer fits under the cap.

        Halving the length of an answer roughly quarters the bytes streamed
        for it, so one split is almost always enough; recursion only covers
        the aspect that is dense on its own. A single aspect that still
        overflows has nothing left to split and fails the extract.
        """
        words = "1500-3000 words" if len(group) > 1 else "1000-2000 words"
        ans, over = ask(group, words)
        if ans is not None:
            return [ans]
        if not over:
            return None
        if len(group) < 2:
            # One aspect, still over the cap: nothing left to split by aspect,
            # but the length is still there to shrink. Chapter 3 (the
            # neurotransmitter overview, with Synopsis ch.33 attached) failed
            # its 2026-10-01 re-run this way at every split down to one aspect,
            # which aborted the whole chapter and left week 1 without its quiz
            # and cards. A shorter extract for one dense aspect is worth more
            # than no chapter: the day notebook also carries the textbooks, so
            # the guide and the quiz are not limited to what this returns.
            for shorter in ("600-1000 words", "350-600 words"):
                print(f"    [i] one aspect still over the cap; asking for "
                      f"{shorter}", file=sys.stderr)
                ans, over = ask(group, shorter)
                if ans is not None:
                    return [ans]
                if not over:
                    return None
            return None
        mid = (len(group) + 1) // 2
        print(f"    [i] part of {len(group)} aspects still over the cap; "
              f"splitting {mid}+{len(group) - mid}", file=sys.stderr)
        left = ask_split(group[:mid])
        right = ask_split(group[mid:]) if left is not None else None
        return None if right is None else left + right

    # Switch to main notebook
    use_notebook(main_notebook_id)
    print("  Extracting comprehensive content from main notebook (this may take 3-5 min)...")
    ans, over = ask(aspects, "3000-6000 words")
    if ans is not None or not over or len(aspects) < 2:
        return ans
    # The whole-chapter answer is too large to receive. The content is not the
    # problem — its size on the wire is — so ask for the same material in
    # parts and join them. Only chapters that overflow pay for this.
    mid = (len(aspects) + 1) // 2
    print(f"  [i] extract over the response cap; asking in parts "
          f"({mid}+{len(aspects) - mid} aspects)")
    left = ask_split(aspects[:mid])
    right = ask_split(aspects[mid:]) if left is not None else None
    if right is None:
        print("  [!] extraction failed even in parts")
        return None
    parts = left + right
    print(f"  [i] extract assembled from {len(parts)} parts "
          f"({sum(len(p) for p in parts)} chars)")
    return "\n\n---\n\n".join(parts)


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


# A study guide that DESCRIBES a study guide instead of being one. Chapter 29
# produced this the first time the notebook held six real textbooks instead of
# a short extract: 3,693 characters, one page citation, opening "הכנתי עבורך"
# and announcing that the material was "available in the Studio panel under
# ssri-study-guide.md". No such note existed - `note list` returned zero. The
# model reported writing a file it never wrote, and every existing check
# passed, because the answer was non-empty, in Hebrew, and even carried a
# citation. Chapters 1 and 28, generated before the books were attached, are
# 13,417 and 16,955 characters with 73 and 83 citations.
_GUIDE_RECEIPT_PHRASES = (
    "פאנל הסטודיו", "בפאנל הסטודיו", "בסטודיו", "Studio panel",
    "הכנתי עבורך", "יצרתי עבורך", "הכנתי לך", "יצרתי לך",
    "זמין עבורך", "זמין כעת", "זמין לך", "נמצא כעת",
)
MIN_GUIDE_CHARS = 6000
MIN_GUIDE_CITATIONS = 12


# A citation past the end of a book is arithmetic, not opinion, and the prompt
# already states every book's printed range and says a page outside it is wrong.
# Chapter 2 produced 111 of them anyway - "Stahl p. 761, 934, 1006, 1229" in a
# book whose last printed page is 623, measured two independent ways: 620
# distinct folios found in the indexed text, and 1,854,842 characters, which is
# 2,977 per page across 623 pages. Told and ignored, so it is checked in code.
# The tolerance is small rather than zero: one stray number inside a sentence
# can look like a citation, and a chapter is not worth re-asking over that.
MAX_IMPOSSIBLE_CITATIONS = 2


def _impossible_citations(text: str) -> list:
    try:
        from qc_review import check_page_citations
        return check_page_citations(text)
    except Exception:
        return []


def _study_guide_defects(text: str) -> list:
    """Reasons this answer is not a usable study guide. Empty list = fine."""
    out = []
    bad = _impossible_citations(text)
    if len(bad) > MAX_IMPOSSIBLE_CITATIONS:
        eg = ", ".join(dict.fromkeys(b["claim"] for b in bad))[:160]
        out.append(f"{len(bad)} citations point at pages that do not exist "
                   f"in the book they name (e.g. {eg}) — use only the printed "
                   f"page ranges given, and write \"(מקור: הקשר רחב)\" when you "
                   f"cannot find a page")
    said = [p for p in _GUIDE_RECEIPT_PHRASES if p in text]
    if said:
        out.append("it announces a file instead of containing the guide "
                   f"({', '.join(said[:3])})")
    if len(text) < MIN_GUIDE_CHARS:
        out.append(f"only {len(text)} chars, expected at least {MIN_GUIDE_CHARS}")
    cites = re.findall(r"[\[(][^\])]{0,40}?\bp\.?\s?\d{1,4}[^\])]{0,20}[\])]", text)
    # "(מקור: הקשר רחב)" is the honest answer the prompt already offers when a
    # claim is spread across the books rather than sitting on one page, and it
    # counts toward the floor. Chapters 2 and 16 — neuroanatomy basics,
    # principles of paediatric psychopharmacology — are broad overviews whose
    # material genuinely is not on twelve findable pages, and demanding twelve
    # citations left them one choice: invent, or fail three times and be
    # dropped. Both invented. A gate that makes honesty impossible is not a
    # quality gate, it is a pressure to fabricate.
    honest = len(re.findall(r"הקשר רחב", text))
    if len(cites) + honest < MIN_GUIDE_CITATIONS:
        out.append(f"only {len(cites)} page citations"
                   + (f" plus {honest} broad-context markers" if honest else "")
                   + f", expected {MIN_GUIDE_CITATIONS} between them — cite a page "
                   f"where you can find one and write (מקור: הקשר רחב) where you "
                   f"honestly cannot, but never invent a page")
    return out


def _guide_kept_from_earlier_attempt(path) -> bool:
    """True when a guide an EARLIER attempt wrote is on disk and is still one.

    Chapter 78 was refused a release twice while a complete 26,500-char guide
    sat in its directory: `ask_study_guide` returning False set the caller's
    `study_guide_path` to None, and the release guard then reported the file it
    had just stopped looking at as missing. NotebookLM had answered with a
    studio announcement on this run and with the real guide on an earlier one,
    so the run that threw the work away was the run that had it.

    Kept only when it passes `_study_guide_defects` — the same test this run's
    answers failed. That is strictly stronger than the existence check the
    guard does, so nothing a receipt could do gets easier here.
    """
    path = Path(path) if path else None
    if not (path and path.exists() and path.stat().st_size):
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"  [!] could not read the earlier study guide: {e}")
        return False
    defects = _study_guide_defects(text)
    if defects:
        print(f"  [X] a study guide from an earlier attempt is on disk but is "
              f"not usable either: {'; '.join(defects)[:200]}")
        return False
    when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d/%m %H:%M")
    print(f"  [OK] study_guide.md ({path.stat().st_size / 1024:.1f} KB) — "
          f"this run's answers were rejected; keeping the guide written "
          f"{when}, which passes the same check", flush=True)
    return True


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
| נושא משנה | DSM-5-TR עמ' | Dulcan עמ' | StahlNeuro עמ' | StahlKids עמ' | Synopsis פרק | סילבוס עמ' |

עמודת Synopsis מכילה **שם/מספר פרק בלבד**, לא עמודים, ורק כשבאמת נעשה בו שימוש
להשלמת פער ב-Dulcan. אם לא נעשה בו שימוש — סמן "—".
עמודת StahlKids רלוונטית רק לנושאים שכוללים תרופה מתוך 33 המונוגרפיות שבספר.

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

**קריטי — כתוב את המדריך עצמו כאן, בגוף התשובה**:
- אל תיצור קובץ, פתק, או פריט בפאנל הסטודיו. אל תשמור שום דבר בשום מקום.
- אל תתאר מה המדריך מכיל ואל תפרט מה "יופיע" בו. כתוב את התוכן המלא עצמו:
  את הטבלאות המלאות, את הקריטריונים המלאים, את המספרים המלאים.
- התשובה שלך היא המדריך. אם היא מתארת מדריך במקום להיות אחד — היא שגויה.
- אל תפתח בפנייה אליי ("הכנתי עבורך", "המדריך זמין"). התחל ישירות בכותרת.

{books_directive(day["index"])}"""

    def asked(text):
        """(answer, overflowed). answer is None when the ask failed."""
        r = nb_run(["ask", text, "--json"], timeout=900)
        if r.returncode != 0:
            if any(m in (r.stdout or "") + (r.stderr or "")
                   for m in RESPONSE_TOO_LARGE):
                return None, True
            print(f"  [!] ask study_guide failed: {r.stderr[:200]}")
            return None, False
        try:
            return json.loads(r.stdout).get("answer", ""), False
        except Exception as e:
            print(f"  [!] parse study_guide failed: {e}")
            return None, False

    # Chapter 58 (FGA) lost its study guide — and with it the release — to
    # RPCResponseTooLargeError: NotebookLM streams an answer as cumulative
    # frames, so a 2,000-4,000-word guide cited on every line crosses the
    # 256MB buffer on a drug-dense chapter. The guide is already organized as
    # one section per aspect, so it can be asked for in contiguous runs of
    # sections and joined without changing what the learner reads. Chapters
    # that fit never take this path, and still get the prompt above verbatim.
    def part_prompt(lo, hi, extra):
        first, last = lo == 0, hi == len(aspects)
        outline = []
        if first:
            outline += [f"# מדריך לימוד יום זה: {title_he}", "",
                        "## 0. מפת המקורות (CRITICAL — page references)",
                        "טבלה: לכל אחד מהסעיפים של הנושא כולו — "
                        + ", ".join(aspects)
                        + " — ציין באיזה ספר ובאיזה עמוד נמצא החומר.",
                        "| נושא משנה | DSM-5-TR עמ' | Dulcan עמ' | StahlNeuro עמ' "
                        "| StahlKids עמ' | Synopsis פרק | סילבוס עמ' |",
                        "", "## 1. מבוא + הגדרה"]
        outline += [f"## {2 + lo + i}. {a}" for i, a in enumerate(aspects[lo:hi])]
        if last:
            outline += ["## נקודות high-yield לבחינה", "## סיכום מבני"]
        role = ("זה החלק הראשון. אל תכתוב נקודות high-yield או סיכום — "
                "הם בחלק האחרון." if first and not last else
                "זה החלק האחרון. אל תכתוב כותרת ראשית, מבוא או מפת מקורות — "
                "הם כבר נכתבו בחלק הראשון." if last and not first else
                "זה חלק אמצעי. אל תכתוב כותרת ראשית, מבוא, מפת מקורות, "
                "נקודות high-yield או סיכום.")
        return f"""כתוב חלק אחד ממדריך לימוד מקיף בעברית, שנכתב בכמה חלקים כי הוא ארוך מכדי להישלח בתשובה אחת.

נושא היום: {title_he}

מיקוד: {focus}

**{role}** כתוב רק את הסעיפים שבמבנה הזה, במלואם, באותה מספור:

{chr(10).join(outline)}

**הוראות מחייבות**:
- שפה: עברית לכל הסבר. אנגלית רק לשמות תרופות גנריים, קולטנים (D2, 5-HT2A...), מונחים קליניים (EPS, NMS, SSRI), וקיצורי DSM/ICD.
- **לכל פיסת מידע ציין עמוד מקור בסוגריים**: "(DSM p.347)" או "(Dulcan ch.7 p.412)".
- אורך: 1200-2500 מילים לחלק הזה. ממוקד מהותית בסעיפים שלו.
- אסמכתאות: מבוסס אך ורק על המקורות במחברת.

**קריטי — כתוב את התוכן עצמו כאן, בגוף התשובה**:
- אל תיצור קובץ, פתק, או פריט בפאנל הסטודיו.
- אל תתאר מה הסעיפים מכילים. כתוב את התוכן המלא: טבלאות, קריטריונים, מספרים.
- אל תפתח בפנייה אליי ("הכנתי עבורך"). התחל ישירות בסעיף הראשון של החלק.

{books_directive(day["index"])}{extra}"""

    # The runs that fit, learned on the first attempt and reused on a retry.
    # Every overflow streams the full 256MB before it is known to be one, so
    # rediscovering the split on each of three attempts would spend minutes
    # re-asking ranges already proven too large.
    runs_that_fit = []

    def ask_parts(lo, hi, extra):
        """Sections for aspects[lo:hi], halving a run that still overflows."""
        ans, over = asked(part_prompt(lo, hi, extra))
        if ans:
            runs_that_fit.append((lo, hi))
            return [ans]
        if not over or hi - lo < 2:
            return None
        mid = (lo + hi + 1) // 2
        print(f"    [i] guide sections {lo + 2}-{hi + 1} still over the cap; "
              f"halving", file=sys.stderr)
        left = ask_parts(lo, mid, extra)
        right = ask_parts(mid, hi, extra) if left is not None else None
        return None if right is None else left + right

    answer, bad, in_parts = "", [], False
    for attempt in range(3):
        reasons = ("" if attempt == 0 else
                   "\n\nהתשובה הקודמת שלך נדחתה מהסיבות הבאות:\n"
                   + "\n".join(f"- {b}" for b in bad))
        if not in_parts:
            answer, over = asked(prompt + (reasons and reasons
                + "\nכתוב עכשיו את המדריך המלא עצמו, בגוף התשובה, מהכותרת ועד הסוף."))
            if answer is None and over and len(aspects) >= 2:
                in_parts = True
                print("  [i] study guide over the response cap; writing it "
                      "in parts", flush=True)
            elif answer is None:
                return False
        if in_parts:
            known = sorted(runs_that_fit)
            runs_that_fit.clear()
            if known:
                parts = []
                for lo, hi in known:
                    got = ask_parts(lo, hi, reasons)
                    if not got:
                        parts = None
                        break
                    parts += got
            else:
                mid = (len(aspects) + 1) // 2
                left = ask_parts(0, mid, reasons)
                right = ask_parts(mid, len(aspects), reasons) if left else None
                parts = left + right if right else None
            if not parts:
                print("  [!] study guide failed even in parts")
                return False
            answer = "\n\n".join(parts)
            print(f"  [i] study guide assembled from {len(parts)} parts "
                  f"({len(answer)} chars)", flush=True)
        if not answer:
            return False
        bad = _study_guide_defects(answer)
        if not bad:
            break
        print(f"  [!] study guide attempt {attempt + 1}/3 rejected: "
              f"{'; '.join(bad)}", file=sys.stderr)
    if bad:
        # Do NOT write it. The caller treats a missing study guide as fatal,
        # which is right: a receipt on disk passes every existence check
        # downstream and ships to the learner as the chapter's study material.
        print("  [!!] study guide never came back as a guide; not writing it",
              file=sys.stderr)
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

    stuck_note = None
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
        # Keep the ids. A timed-out artifact is usually still being built on
        # NotebookLM's side, so it is recoverable later — but only if we wrote
        # down what to collect. Chapter 29 lost a video, a quiz and a set of
        # flashcards this way and there was nothing afterwards to say they had
        # ever been asked for.
        try:
            pend_file = REPO_ROOT / "_bulk" / "pending_artifacts.json"
            pend_file.parent.mkdir(parents=True, exist_ok=True)
            book = json.loads(pend_file.read_text(encoding="utf-8")) if pend_file.exists() else {}
            book.update({aid: {"kind": kind,
                               "notebook": os.environ.get("_CURRENT_NB", ""),
                               "day": os.environ.get("_CURRENT_DAY", "")}
                         for aid, kind in pending.items()})
            pend_file.write_text(json.dumps(book, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
            print(f"  [i] recorded {len(pending)} unfinished artifact(s) in "
                  f"{pend_file.name} for later collection")
        except Exception as e:
            print(f"  [!] could not record pending artifacts: {e}", file=sys.stderr)
        for aid, kind in pending.items():
            results[aid] = (kind, "timeout", None)
    return results


def rebalance_quiz_file(path, tag: str) -> bool:
    """Move each quiz key off option A, in place, before the quiz is published.

    The prompt asks NotebookLM to vary the key and it does not: chapter 38's
    regenerated quiz (2026-09-26) put the correct answer on A in all seven
    items, the same 91%-on-A pattern measured across the 141 released quizzes.
    Asking harder is not a fix, so the pipeline does it — the same
    qc_fix.rebalance_keys that was published to the backlog under the owner's
    §5.12 exception, which is what lets it run here without a verdict.

    Fail-closed in the safe direction: if the result does not keep every item's
    single correct answer and its option set unchanged, the file is left as
    NotebookLM wrote it. An unbalanced quiz is a worse test; a quiz whose
    correct answer moved to the wrong option is a wrong one.
    """
    path = Path(path)
    try:
        import qc_fix   # local: only the publish path needs it
        import qc_lint
        raw = path.read_text(encoding="utf-8")
        before = json.loads(raw)
        after = qc_fix.rebalance_keys(json.loads(raw), tag)
        a = qc_lint._quiz_item_list(before)
        b = qc_lint._quiz_item_list(after)

        def keys(items):
            out = []
            for it in items:
                opts = qc_lint._options(it)
                out.append((sorted(json.dumps(o, sort_keys=True, ensure_ascii=False)
                                   for o in opts),
                            [o.get("text") for o in opts
                             if isinstance(o, dict) and o.get("isCorrect")]))
            return out

        if len(a) != len(b) or keys(a) != keys(b):
            print("  [!] quiz rebalance did not preserve the answers; "
                  "publishing it unbalanced rather than wrong", flush=True)
            return False
        if json.dumps(after, sort_keys=True) == json.dumps(before, sort_keys=True):
            return True
        path.write_text(json.dumps(after, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print("  [i] quiz answer order rebalanced (content unchanged)", flush=True)
        return True
    except Exception as exc:                                  # noqa: BLE001
        print(f"  [!] quiz rebalance skipped: {exc}", flush=True)
        return False


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


def _fresh(path, since):
    """Was this file written by the CURRENT run?

    Existence is not enough. bulk_run keeps a failed chapter's directory on
    purpose, so a retry pass finds the previous attempt's files still there.
    """
    try:
        return path.exists() and path.stat().st_mtime >= since
    except OSError:
        return False


def _judged_assets(tag: str) -> set[str]:
    """The asset names a QC verdict for this chapter already covers.

    QC_REVIEW_SPEC §2.5 — "never lose an asset; uploads never use --clobber" —
    and §5.12, which closes it: "every uploader refuses unless the chapter's
    verdict covers that asset's sha256". Until §5.12's per-sha256 gate exists,
    this is the cheap half of it: a `--clobber` over an asset a verdict was
    written about destroys the judged bytes with no pre-QC copy and silently
    invalidates the sha256 the verdict (and the app's qc.json) names.

    Read straight off disk, not through qc_store: this module runs on machines
    that carry neither PyMuPDF nor the QC tree, and a missing verdict must cost
    nothing. Anything unreadable means "no verdict" — the gate is a guard on
    top of the old behaviour, never a new way for a release to fail.
    """
    try:
        root = Path(os.environ.get("QC_DIR") or (REPO_ROOT / "_qc"))
        verdict = json.loads(
            (root / str(tag) / "verdict.json").read_text(encoding="utf-8"))
        if verdict.get("status") == "unjudged":
            return set()          # nothing was checked, so nothing is at risk
        return {str(n) for n in
                {**(verdict.get("inputs") or {}),
                 **(verdict.get("outputs") or {})}}
    except Exception:                                        # noqa: BLE001
        return set()


def upload_release(tag, title, notes_md, assets):
    """Create or update a GitHub Release with all assets.

    Returns {"url": "<release_url>", "assets": [...]} on success,
    or {"url": "", "assets": []} on failure (with errors printed).

    An asset a QC verdict already covers is NOT overwritten — see
    `_judged_assets`. It is skipped and named, and the rest of the release
    still uploads.
    """
    env = os.environ.copy()
    if not env.get("GH_TOKEN"):
        # Every call here is `gh`, and gh authenticates from its own keyring.
        # Demanding GH_TOKEN threw away a whole chapter's work at the last
        # step - 33 minutes of generation, 95MB of audio - while `gh auth
        # status` was reporting a valid token with repo scope the entire time.
        # Ask gh for it rather than requiring a second copy in .env.
        t = subprocess.run(["gh", "auth", "token"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if t.returncode == 0 and t.stdout.strip():
            env["GH_TOKEN"] = t.stdout.strip()
        else:
            print("  [FAIL] no GitHub credentials — set GH_TOKEN or run "
                  "`gh auth login`, then re-run.")
            return {"url": "", "assets": []}

    # Check if release exists
    r = subprocess.run(
        ["gh", "release", "view", tag, "--repo", GITHUB_REPO],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if r.returncode == 0:
        # Release exists — upload each asset with --clobber, except the ones a
        # QC verdict already covers (§5.12).
        judged = _judged_assets(tag)
        upload_failures = 0
        for a in assets:
            if Path(a).name in judged:
                print(f"  [SKIP] {Path(a).name}: a QC verdict covers these "
                      f"bytes — refusing to --clobber the judged asset. "
                      f"Re-run qc_run for {tag} after the change, or clear "
                      f"_qc/{tag}/verdict.json if the verdict is stale.")
                continue
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
        out = json.loads(rv.stdout)
    except Exception as e:
        print(f"  [FAIL] could not parse gh release view output: {e}")
        return {"url": "", "assets": []}

    # The read-back is the only place that can tell whether the upload actually
    # landed, so compare it against what we asked for. Upload failures used to
    # be counted, printed and then thrown away: a release carrying one asset
    # out of seven still returned a success URL, bulk_run saw the release
    # exist, marked the chapter DONE and swept the local directory. The
    # chapter is then permanently incomplete and its only copy is gone.
    want = {Path(a).name for a in assets}
    have = {x.get("name") for x in out.get("assets", [])}
    lost = sorted(want - have)
    if lost:
        print(f"  [FAIL] release {tag} is missing {len(lost)}/{len(want)} "
              f"asset(s): {', '.join(lost)}")
        return {"url": "", "assets": out.get("assets", []), "missing": lost}
    return out


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
    os.environ["_CURRENT_DAY"] = str(day_index)
    # Anything older than this instant belongs to a previous attempt.
    run_started = time.time() - 1

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
        # Which books are actually present, by identity rather than by count.
        # Counting was wrong on exactly the case that matters: a notebook left
        # with 6 of an expected 7 sources after a failed extraction would be
        # "short", and the retrofit re-attached all six books on top of the six
        # already there.
        sources = _list_sources(day_nb_id)
        if sources is None:
            # "Could not determine" used to shrug and continue. It then
            # generated audio, video and a study guide against a notebook with
            # NO SOURCES: every artifact would have been invented whole. If we
            # cannot confirm the books are attached, stop.
            raise RuntimeError(
                f"could not list sources in notebook {day_nb_id}; refusing to "
                f"generate against a notebook whose contents are unknown")
        present, attached = set(), {}
        for src in sources:
            book = _book_for_source(src.get("id"), src.get("title"))
            if book:
                present.add(book["label"])
                attached[src.get("id")] = book["label"]
        # A slices_only book is never attached whole — attach_synopsis_slices
        # puts in the chapter's own extracts further down, and the label stays
        # in the book list only so the citation rules keep naming it. Counting
        # it as missing here asked add_textbook_pdfs_to_day_notebook for a book
        # it is built to skip, so it returned 0 of 1 and the short-attach guard
        # below killed the chapter. This is the path EVERY existing notebook
        # takes, which is every resumed and every --retry-failed chapter: 23
        # died here after its first attempt had already spent 132 minutes.
        # The fresh-notebook check at the end of this function excludes them
        # correctly; this one was left behind when the Synopsis moved to slices.
        missing = [lbl for lbl, meta in load_drive_books().items()
                   if lbl not in present and not meta.get("slices_only")]
        if missing:
            print(f"  Notebook is missing {len(missing)} book(s): "
                  f"{', '.join(missing)}. Attaching...")
            got = add_textbook_pdfs_to_day_notebook(day_nb_id, only=missing)
            attached.update(got)
            # The fresh-notebook path refuses to continue on a short attach;
            # this path did not, and it is the one every --retry-failed re-run
            # goes through. A book named in the prompt but absent from the
            # notebook does not raise - NotebookLM invents page numbers for it.
            if len(got) < len(missing):
                raise RuntimeError(
                    f"only {len(got)}/{len(missing)} missing textbooks attached "
                    f"to {day_nb_id} ({', '.join(sorted(set(missing) - set(got.values())))} "
                    f"still absent). Refusing to generate: the prompts name "
                    f"every book, and a missing one becomes invented citations.")
        else:
            print(f"  All {len(present)} textbooks already attached.")
        # The fresh-notebook path attaches this chapter's Synopsis extracts;
        # this one did not, so a chapter resumed against an existing notebook
        # had the Synopsis named in its prompt and absent from its sources —
        # the exact shape that makes NotebookLM invent page numbers. 39 of the
        # 144 chapters require a slice. Pass what is already there so a resume
        # registers those sources instead of uploading a second copy.
        have_slices = {src.get("title"): src.get("id") for src in sources
                       if (src.get("title") or "").startswith("Synopsis ch.")}
        attach_synopsis_slices(day_nb_id, day["index"], attached,
                               existing=have_slices)
        # Always re-check indexing, even when nothing was added: a source can
        # sit in `error` from a previous run, and that is repairable here.
        print("  Waiting for textbooks to index...")
        if not wait_for_sources_indexed(day_nb_id, attached=attached):
            raise RuntimeError(
                f"not every source in {day_nb_id} finished indexing; refusing "
                f"to extract from a notebook that cannot answer yet")
        use_notebook(day_nb_id)
        # Retitle now, not at attach time: indexing overwrites the title with
        # the Drive filename, so a rename issued earlier is silently undone.
        rename_sources_to_labels(day_nb_id, attached)
        print("  Checking the notebook can answer from them...")
        if not wait_until_answerable(day_nb_id):
            raise RuntimeError(
                f"{day_nb_id} reports every source ready but still cannot "
                f"answer from them; refusing to extract")
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
        attached = add_textbook_pdfs_to_day_notebook(day_nb_id)
        attach_synopsis_slices(day_nb_id, day["index"], attached)
        # COUNT BOOKS, NOT SOURCES. The Synopsis is attached as three parts, so
        # source count stopped being a proxy for book count the moment it went
        # in: five books plus one surviving Synopsis part is six sources and
        # six books required, and this check would have waved it through with
        # two thirds of a textbook missing and the prompt still naming it.
        n_pdfs = len(set(attached.values()))
        # Books that arrive as per-chapter slices are not expected here: a
        # chapter with no required Synopsis extract attaches none, and demanding
        # one would fail every chapter outside the examiners' 39.
        need = len([b for b in load_drive_books().values()
                    if not b.get("slices_only")])
        # A book named in the prompt but absent from the notebook does not
        # raise — NotebookLM quietly invents plausible page numbers for it.
        # That already shipped once (a day-16 study guide cited "Stahl
        # Pediatric pp. 600-612" in a 525-page book). Refuse to build a chapter
        # on an incomplete notebook.
        if n_pdfs < need:
            raise RuntimeError(
                f"only {n_pdfs}/{len(load_drive_books())} textbooks attached to "
                f"notebook {day_nb_id}. Refusing to generate: the prompts name "
                f"all six books, and a missing one becomes invented citations.")

        # 4. Wait for the books to finish indexing BEFORE asking anything of
        #    them. This wait used to sit AFTER the extraction, which was
        #    harmless when extraction came from a long-lived main notebook and
        #    silently destructive once it came from a notebook whose sources
        #    were attached seconds earlier.
        print("  Waiting for textbooks to index...")
        if not wait_for_sources_indexed(day_nb_id, attached=attached):
            raise RuntimeError(
                f"not every source in {day_nb_id} finished indexing; refusing "
                f"to extract from a notebook that cannot answer yet")

        # 4b. `ready` is not the same as queryable. Verify the notebook can
        #     actually answer from the books before asking it anything that
        #     matters.
        use_notebook(day_nb_id)
        print("  Checking the notebook can answer from them...")
        if not wait_until_answerable(day_nb_id):
            raise RuntimeError(
                f"{day_nb_id} reports every source ready but still cannot "
                f"answer from them; refusing to extract")

        # 5. Extract this chapter's facts FROM THE DAY NOTEBOOK, which now
        #    holds the books themselves.
        extract_md = extract_day_content_from_main(day, symbol_block, day_nb_id)
        # Chapters 12 and 13 both died on a 748- and a 1,056-character extract
        # from a notebook that had just proved it could answer. One short answer
        # is not a broken notebook, it is one short answer — ask again before
        # writing the chapter off, since everything after this depends on it.
        if extract_md and len(extract_md) < MIN_EXTRACT_CHARS:
            print(f"  [!] extract came back at {len(extract_md)} chars; asking "
                  f"once more", file=sys.stderr)
            time.sleep(30)
            retry = extract_day_content_from_main(day, symbol_block, day_nb_id)
            if retry and len(retry) > len(extract_md):
                extract_md = retry
        if not extract_md:
            print("[!] Could not extract content from the day notebook. Aborting.")
            return None
        # Six textbooks cannot honestly answer a chapter brief in a paragraph.
        # A tiny extract means the notebook was not ready or the query failed,
        # and facts.json drives every plate downstream.
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
    task_ids["audio"] = (None if SKIP_QUOTA_ARTIFACTS
                         else start_audio(prompt_audio(day, symbol_block, legend_block)))
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
    elif _guide_kept_from_earlier_attempt(study_guide_path):
        pass
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

    if SKIP_QUOTA_ARTIFACTS:
        print("Skipping per-aspect infographics (quota-bound; backfill fills them "
              "in later).")
        infographic_aspects = []
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

        if venue is not None and SKIP_PLATES:
            print("")
            print("[i] SKIP_PLATES: the plates are deferred to a later render "
                  "pass; everything else in this chapter is still built.")
        elif venue is not None:
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
                gemini_images.append(
                    (plate.name, png, txt if _fresh(txt, run_started) else None))
                # A plate without its legend is not a study asset, and the quiz
                # and the narration are generated from that same legend.
                #
                # Freshness, not existence. A failed chapter's directory is
                # deliberately KEPT as evidence, so on a --retry-failed pass
                # the previous attempt's legend, quiz and narration are still
                # sitting there. Collecting whatever exists would ship the old
                # run's legend beside the new run's image - a caption that
                # describes a picture nobody is looking at, which is the exact
                # defect this course was rebuilt to fix.
                for extra in (day_dir / f"sketchy_{plate.name}.quiz.md",
                              day_dir / f"narration_{plate.name}.txt",
                              day_dir / f"narration_{plate.name}.wav"):
                    if _fresh(extra, run_started):
                        plate_extras.append(extra)
                    elif extra.exists():
                        print(f"    [!] {extra.name} is left over from an "
                              f"earlier attempt; not shipping it")
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
            if base_kind == "quiz":
                rebalance_quiz_file(out_path, f"study-{day_index:03d}")
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

    # "At least one file" is not a chapter. Publishing a release is what marks
    # this chapter permanently done: bulk_run sees the tag exist, records it in
    # `done`, and deletes the local directory. A release carrying only, say,
    # one PNG would retire the chapter forever with no study guide and no facts
    # behind it, and the sweep would take the evidence with it.
    essential = {"study_guide.md": study_guide_path, "facts.json": facts_path}
    absent = [n for n, p in essential.items() if not (p and Path(p).exists())]
    if absent:
        raise RuntimeError(
            f"chapter {day_index} is missing {', '.join(absent)}; refusing to "
            f"publish a release that would mark it done. Its local files are "
            f"left in place as evidence.")

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
    # An empty url means the release did not fully land. Returning it anyway
    # let the process exit 0, and bulk_run then treats an existing-but-partial
    # release as a finished chapter and sweeps the local copy.
    if not release_info.get("url"):
        raise RuntimeError(
            f"release {tag} did not publish completely"
            + (f" (missing: {', '.join(release_info['missing'])})"
               if release_info.get("missing") else "")
            + ". Local files kept.")
    print(f"Release: {release_info['url']}")

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
        result = generate_day(args.day, schedule, symbols, output_dir,
                              dry_run=args.dry_run)
        # generate_day returns None on three failures (the day is not in the
        # schedule, the extraction aborted, nothing downloaded) and on the
        # dry-run preview. The process used to exit 0 on all of them, and
        # bulk_run reads rc 0 as success: chapter 3's 2026-10-01 re-run printed
        # "Could not extract content ... Aborting." and exited 0. On a first
        # run the missing release caught it; on a re-run the OLD release is
        # still there, so the abort would have been recorded as done and its
        # evidence swept.
        if result is None and not args.dry_run:
            sys.exit(1)
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
