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

REPO_ROOT = Path(__file__).resolve().parent.parent

# Default IDs (override via env)
NOTEBOOK_ID = os.environ.get(
    "BOARD_STUDY_NOTEBOOK_ID",
    "157ae88a-ba4c-4d76-a1fd-c551d449a782",  # Board-Exam study material
)
GITHUB_REPO = os.environ.get("GH_REPO", "tnvsh0/psychiatry-board-study")

NOTEBOOKLM_BIN = "/opt/venv/bin/notebooklm"
WAIT_INDEX_S = 60
POLL_INTERVAL_S = 60
ARTIFACT_MAX_WAIT_S = 60 * 60  # 60 min per batch (audio --length long is slower)


def slugify(text: str) -> str:
    """Make a filesystem/URL-friendly slug from English text."""
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-")
    return text.lower()[:60]


def load_schedule():
    return json.loads((REPO_ROOT / "schedule" / "schedule.json").read_text(encoding="utf-8"))


def load_symbols():
    return json.loads((REPO_ROOT / "schedule" / "symbols.json").read_text(encoding="utf-8"))


# ── Prompt builders ─────────────────────────────────────────────────────────
def collect_symbol_descriptions(symbol_keys, symbols_data):
    """Pull short Hebrew descriptions for the symbols used today."""
    lines = []
    pools = {
        **symbols_data.get("scenes", {}),
        **symbols_data.get("ions", {}),
        **symbols_data.get("symbols", {}),
        **symbols_data.get("drugs", {}),
    }
    for key in symbol_keys:
        item = pools.get(key)
        if not isinstance(item, dict):
            continue
        he = item.get("hebrew_explanation", "")
        if he:
            lines.append(f"- {key}: {he}")
    return "\n".join(lines) if lines else "(אין סמלים ייעודיים ליום זה)"


def build_day_scene(day, symbols_data):
    """A unified 'world' description for the day, shared by video + infographics
    so the visual continuity is preserved across artifacts."""
    title_he = day.get("title_he", day.get("title_en", ""))
    symbol_keys = day.get("symbols_needed", [])

    pools = {
        **symbols_data.get("scenes", {}),
        **symbols_data.get("ions", {}),
        **symbols_data.get("symbols", {}),
        **symbols_data.get("drugs", {}),
    }
    scene_parts = []
    for key in symbol_keys[:8]:  # cap at 8 to keep prompt focused
        item = pools.get(key)
        if isinstance(item, dict):
            sym = item.get("symbol", "") or item.get("scene_name", "")
            if sym:
                scene_parts.append(f"  • {key}: {sym}")

    return f"""DAY SCENE SCRIPT (unified visual world for ALL artifacts of this day —
the video animates this scene with narration; each infographic captures a still
frame of one aspect within this same scene; all characters/symbols are consistent):

Topic: {title_he}

Recurring elements in today's scene:
{chr(10).join(scene_parts) if scene_parts else '  (this day uses generic clinical visuals)'}

Visual rule: whenever a concept from the symbol legend appears, render it using
the corresponding symbol from the legend (e.g. D2 receptor → red barrier on the
mesolimbic track; ACh → wooden match; M3 → 3 dripping teacups). Same symbol
across video and infographics — the still frames must look like extracts from
the same animated world."""


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


def build_base_context(day, symbol_block, day_scene):
    """The Hebrew context block that every prompt starts with."""
    title_he = day.get("title_he", day.get("title_en", ""))
    aspects = day.get("aspects", [])
    focus = day.get("prompt_focus", "")

    return f"""נושא היום: {title_he}

מיקוד מיוחד:
{focus}

היבטים לכיסוי:
{', '.join(aspects)}

מערכת סמלים ויזואליים עקביים (Sketchymedical-style) לשימוש בכל הציורים והסרטונים — בכל פעם שמושג מופיע בתוכן, השתמש בסמל הוויזואלי המתאים מהרשימה:
{symbol_block}

{day_scene}

{BILINGUAL_DIRECTIVE}

הוראות:
- הסתמך אך ורק על המקורות במחברת (DSM-5-TR, Dulcan, Stahl Essential, Stahl Pediatric, הסילבוס). אל תמציא.
- מטרת הלימוד: מתמחה בפסיכיאטריה של הילד והמתבגר, לקראת בחינת שלב ב'."""


def prompt_audio(day, symbol_block, day_scene):
    base = build_base_context(day, symbol_block, day_scene)
    return base + """

צור פודקאסט deep-dive בעברית — כמה שיותר ארוך ומעמיק (--length long).
שיחה מקיפה בין שני מנחים על הנושא. כיסוי שיטתי + יסודי של *כל* ההיבטים
שצוינו, כולל nuances קלינים, ציטוטים מהמקור, וינייטות. דגש על משמעות
קלינית ויישום מעשי. אזכור תרופות בשם גנרי באנגלית (לא מסחרי)."""


def prompt_video(day, symbol_block, day_scene):
    base = build_base_context(day, symbol_block, day_scene)
    return base + """

צור סרטון הסבר באורך כ-10-15 דקות, סגנון whiteboard, בעברית.
**העלילה**: הסרטון מתרחש ב-DAY SCENE SCRIPT שתואר למעלה — אותם דמויות,
אותם סמלים, אותה תפאורה. ערוץ את הסיפור כך שהאינפוגרפיקות (שייוצרו בנפרד)
ייראו כתמונות-מתוך-הסרטון.

כשמופיע D2 → מחסום אדום על מסילה (לא תיאור אחר); כשמופיע ACh → גפרור-עץ
(אצית); כשמופיע M3 → 3 כוסות תה דולפות של ה-Mad Hatter. אותם סמלים גם
בסרטון וגם באינפוגרפיקות. הוסף label בולט בכל הופעה של סמל (כמו Sketchymedical)."""


def prompt_infographic(day, symbol_block, day_scene, aspect):
    base = build_base_context(day, symbol_block, day_scene)
    aspect_prompts = {
        "dsm_criteria": "התמקד אך ורק בקריטריונים של DSM-5-TR לאבחנה זו. רשימה מסודרת + דוגמאות קליניות.",
        "epidemiology": "התמקד באפידמיולוגיה: prevalence, incidence, חלוקה לפי מין/גיל, גורמי סיכון, גורמים מגנים.",
        "etiology": "התמקד באטיולוגיה ביופסיכוסוציאלית: גנטיקה, נוירוביולוגיה, סביבה, פסיכולוגיה.",
        "treatment_first_line": "התמקד בטיפול קו ראשון: תרופתי + לא-תרופתי + עוצמת ההמלצה.",
        "treatment_second_line": "התמקד בטיפולי קו שני ומתפתחים: אינדיקציות, יעילות, סיכונים.",
        "treatment_overview": "סקירת עץ-טיפול: קו 1 → קו 2 → augmentation → refractory.",
        "drug_mechanism": "התמקד במנגנון פעולה: קולטנים, עוצמות קישור, downstream effects.",
        "drug_side_effects": "התמקד בתופעות לוואי + המנגנון של כל אחת.",
        "drug_monitoring": "התמקד בבדיקות לפני התחלה + ניטור במהלך הטיפול (תדירות + ערכים).",
        "drug_practical": "התמקד בפרקטיקה: מינונים, איך להתחיל, איך לעלות/לרדת, אינטראקציות נפוצות.",
        "drug_class_overview": "סקירה השוואתית של בני המשפחה: השוואה לפי t½, מטבוליזם, אינדיקציות.",
        "indications": "התמקד באינדיקציות (FDA + off-label) + רמת הראיה.",
        "side_effects_class": "התמקד בתופעות לוואי משותפות למשפחה.",
        "monitoring": "התמקד בניטור: מה, מתי, איזה ערכים.",
        "practical_use": "התמקד בפרקטיקה: בחירה בין בני המשפחה, אופן ההתחלה.",
        "assessment_tools": "התמקד בכלי הערכה (שאלונים, סולמות) רלוונטיים.",
        "medical_mimics": "התמקד באבחנה מבדלת רפואית ובמצבים 'Due to Another Medical Condition / Substance'.",
        "core_concept": "התמקד בקונספט הליבה — מה זה ולמה זה חשוב.",
        "anatomy_or_structure": "התמקד באנטומיה/מבנה — איפה זה ממוקם, איך זה נראה.",
        "function": "התמקד בתפקוד — מה זה עושה, איך זה משפיע.",
        "clinical_relevance": "התמקד ברלוונטיות קלינית — איפה זה פוגש מטופלים.",
        "history_origin": "התמקד בהיסטוריה ומקור הטכניקה — מי פיתח, מתי, איפה.",
        "core_principles": "התמקד בעקרונות יסוד של השיטה.",
        "practice_method": "התמקד באיך זה נעשה בפועל — מבנה sessions, טכניקות.",
        "evidence": "התמקד בעדויות אמפיריות — RCTs, meta-analyses, אינדיקציות מאושרות.",
        "recognition": "התמקד בזיהוי מוקדם של המצב — סימנים אדומים.",
        "diagnostic_criteria": "התמקד בקריטריונים הדיאגנוסטיים המדויקים.",
        "differential": "התמקד באבחנה מבדלת — מה דומה, איך מבדילים.",
        "emergency_management": "התמקד בניהול המצב החירומי: גישה, תרופות, ניטור.",
    }
    aspect_he = aspect_prompts.get(aspect, f"התמקד בהיבט: {aspect}")
    return base + f"""

צור אינפוגרפיקה (תמונה אחת) בסגנון sketch-note, בעברית. {aspect_he}
**תפאורה**: אותה סצינה כמו DAY SCENE SCRIPT — האינפוגרפיקה צריכה להיראות
כמו תמונה-נייחת מתוך הסרטון של היום (אותם דמויות, אותם סמלים, אותו עולם).

ממוקד היבט אחד בלבד — לא עמוס. השתמש בסמלים מהרשימה ב-{aspect}. הוסף label
לכל סמל. כותרת ראשית: '{aspect}' בעברית-אנגלית."""


def prompt_study_guide(day, symbol_block, day_scene):
    base = build_base_context(day, symbol_block, day_scene)
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

עמודי מקור: ציון איפה כל פיסת מידע נמצאת ב-DSM/Dulcan/Stahl."""


def prompt_flashcards(day, symbol_block, day_scene):
    base = build_base_context(day, symbol_block, day_scene)
    return base + """

צור סט כרטיסיות שינון — שאלה + תשובה — לכיסוי high-yield של היום.
פורמט סטנדרטי. כמות: standard (15-20 כרטיסיות).
תייג כל כרטיסיה ב-aspect (dsm_criteria, treatment, drug_mechanism, etc.) כדי שב-UI
נוכל להציג לצידה את האינפוגרפיקה הרלוונטית של אותו aspect."""


def prompt_quiz(day, symbol_block, day_scene):
    base = build_base_context(day, symbol_block, day_scene)
    return base + """

צור קוויז של 8-10 שאלות. סוג שאלות: vignette קליני קצר + multiple choice
(4 אופציות + הסבר). רמה: בחינת שלב ב'. דגש על אבחנה מבדלת, סיבוכים נדירים,
ניהול ב-vignette. כל הסבר תשובה מתייחס לסמלים מ-DAY SCENE."""


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
        print(f"  ⚠ source add failed: {r.stderr[:200]}")
        return None
    try:
        data = json.loads(r.stdout.strip())
        return (data.get("source") or {}).get("id") or data.get("id")
    except Exception:
        return None


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
        print(f"  ⚠ extraction ask failed: {r.stderr[:200]}")
        return None
    try:
        resp = json.loads(r.stdout)
        return resp.get("answer", "")
    except Exception as e:
        print(f"  ⚠ extraction parse failed: {e}")
        return None


def start_audio(prompt):
    r = nb_run(["generate", "audio", prompt, "--format", "deep-dive",
                "--language", "he", "--length", "long", "--json"], timeout=120)
    return _extract_task_id(r, "audio")


def start_video(prompt):
    r = nb_run(["generate", "video", prompt, "--style", "whiteboard", "--language", "he", "--json"], timeout=120)
    return _extract_task_id(r, "video")


def start_infographic(prompt):
    r = nb_run(["generate", "infographic", prompt, "--style", "sketch-note", "--language", "he", "--json"], timeout=120)
    return _extract_task_id(r, "infographic")


def start_report(prompt):
    r = nb_run(["generate", "report", prompt, "--format", "study-guide", "--language", "he", "--json"], timeout=120)
    return _extract_task_id(r, "report")


def start_flashcards(prompt):
    r = nb_run(["generate", "flashcards", prompt, "--quantity", "standard", "--difficulty", "medium", "--json"], timeout=120)
    return _extract_task_id(r, "flashcards")


def start_quiz(prompt):
    r = nb_run(["generate", "quiz", prompt, "--quantity", "standard", "--difficulty", "hard", "--json"], timeout=120)
    return _extract_task_id(r, "quiz")


def _extract_task_id(r, kind):
    if r.returncode != 0:
        print(f"  ⚠ start {kind} failed (exit {r.returncode}): {r.stderr[:200]}")
        return None
    try:
        out = json.loads(r.stdout.strip())
        return out.get("task_id") or out.get("artifact_id") or out.get("id")
    except Exception as e:
        print(f"  ⚠ parse {kind} output failed: {e}; raw: {r.stdout[:200]}")
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
        print(f"  ⚠ Timed out for: {list(pending.values())}")
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
    r = nb_run(cmd[kind], timeout=300)
    return r.returncode == 0 and Path(out_path).exists() and Path(out_path).stat().st_size > 0


# ── Github Release ──────────────────────────────────────────────────────────
def upload_release(tag, title, notes_md, assets):
    """Create or update a GitHub Release with all assets."""
    env = os.environ.copy()
    # Check if release exists
    r = subprocess.run(
        ["gh", "release", "view", tag, "--repo", GITHUB_REPO],
        capture_output=True, text=True, env=env,
    )
    if r.returncode == 0:
        # Release exists — just upload assets (--clobber to replace)
        for a in assets:
            subprocess.run(
                ["gh", "release", "upload", tag, str(a), "--repo", GITHUB_REPO, "--clobber"],
                capture_output=True, text=True, env=env, timeout=300,
            )
    else:
        # Create new release
        subprocess.run(
            ["gh", "release", "create", tag] + [str(a) for a in assets] + [
                "--title", title, "--notes", notes_md, "--repo", GITHUB_REPO,
            ],
            capture_output=True, text=True, env=env, timeout=300,
        )
    # Return download URLs
    r = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets,url",
         "--jq", "{url: .url, assets: [.assets[] | {name: .name, url: .url}]}",
         "--repo", GITHUB_REPO],
        capture_output=True, text=True, env=env,
    )
    try:
        return json.loads(r.stdout)
    except Exception:
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
    day_scene = build_day_scene(day, symbols)

    if dry_run:
        print("[DRY RUN] Skipping NotebookLM API calls.")
        print("=== DAY SCENE PREVIEW ===")
        print(day_scene)
        return None

    # === Per-day notebook architecture ===
    # 1. Check if per-day notebook already exists
    day_nb_id, day_nb_title = find_day_notebook(day_index, day.get("title_he", title_en))
    if day_nb_id:
        print(f"Found existing per-day notebook: {day_nb_id} ('{day_nb_title}')")
    else:
        # 2. Extract focused content from MAIN notebook
        extract_md = extract_day_content_from_main(day, symbol_block, NOTEBOOK_ID)
        if not extract_md:
            print("⚠ Could not extract content from main notebook. Aborting.")
            return None
        print(f"  Extracted {len(extract_md)} characters of source material.")

        # 3. Create per-day notebook
        print(f"Creating per-day notebook: {day_nb_title}")
        day_nb_id = create_day_notebook(day_nb_title)
        print(f"  Created notebook: {day_nb_id}")

        # 4. Add the extracted .md as source
        source_title = f"Day {day_index} — Source material (extracted from DSM/Dulcan/Stahl)"
        src_id = add_text_source(day_nb_id, extract_md, source_title)
        if not src_id:
            print("⚠ Could not add source. Aborting.")
            return None
        print(f"  Added source: {src_id}")

        # 5. Wait for indexing (text source = fast)
        print(f"  Waiting {WAIT_INDEX_S}s for indexing...")
        time.sleep(WAIT_INDEX_S)

    # Switch to the per-day notebook for artifact generation
    use_notebook(day_nb_id)

    # Decide how many infographics based on density
    density = day.get("estimated_density", "medium")
    n_infographics = {"low": 1, "medium": 2, "high": 3}.get(density, 2)

    # Pick which aspects get their own infographic
    infographic_aspects = day.get("aspects", [])[:n_infographics]

    # Fire off all artifacts (space out launches to reduce throttling)
    task_ids = {}
    print("Starting artifacts...")
    task_ids["audio"] = start_audio(prompt_audio(day, symbol_block, day_scene))
    print(f"  audio: {task_ids['audio']}")
    time.sleep(15)
    task_ids["video"] = start_video(prompt_video(day, symbol_block, day_scene))
    print(f"  video: {task_ids['video']}")
    time.sleep(15)
    task_ids["report"] = start_report(prompt_study_guide(day, symbol_block, day_scene))
    print(f"  report: {task_ids['report']}")
    time.sleep(15)
    task_ids["flashcards"] = start_flashcards(prompt_flashcards(day, symbol_block, day_scene))
    print(f"  flashcards: {task_ids['flashcards']}")
    time.sleep(15)
    task_ids["quiz"] = start_quiz(prompt_quiz(day, symbol_block, day_scene))
    print(f"  quiz: {task_ids['quiz']}")

    for i, aspect in enumerate(infographic_aspects):
        time.sleep(15)
        tid = start_infographic(prompt_infographic(day, symbol_block, day_scene, aspect))
        task_ids[f"infographic_{aspect}"] = tid
        print(f"  infographic_{aspect}: {tid}")

    task_ids = {k: v for k, v in task_ids.items() if v}
    print(f"\n{len(task_ids)} artifacts launched. Waiting...\n")

    # Wait
    results = wait_for_artifacts({tid: kind for kind, tid in task_ids.items()})

    # Download
    assets = []
    for aid, (kind, status, art) in results.items():
        if status != "completed":
            print(f"  ✗ {kind} skipped ({status})")
            continue
        ext = {"audio": "mp3", "video": "mp4", "infographic": "png", "report": "md", "flashcards": "json", "quiz": "json"}.get(kind.split("_")[0], "bin")
        suffix = "" if kind == kind.split("_")[0] else f"_{kind.split('_', 1)[1]}"
        out_path = day_dir / f"{kind.split('_')[0]}{suffix}.{ext}"
        if download_artifact(kind.split("_")[0], aid, out_path):
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"  ✓ {out_path.name} ({size_mb:.1f} MB)")
            assets.append(out_path)
        else:
            print(f"  ✗ download failed: {kind}")

    if not assets:
        print("⚠ No assets downloaded. Aborting release.")
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
