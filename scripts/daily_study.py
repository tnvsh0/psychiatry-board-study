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
ARTIFACT_MAX_WAIT_S = 45 * 60  # 45 min per batch


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


def build_base_context(day, symbol_block):
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

הוראות:
- שפה: עברית לכל הטקסט הוויזואלי, הקריינות וההסברים.
- הסתמך אך ורק על המקורות במחברת (DSM-5-TR, Dulcan, Stahl Essential, Stahl Pediatric, הסילבוס). אל תמציא.
- מטרת הלימוד: מתמחה בפסיכיאטריה של הילד והמתבגר, לקראת בחינת שלב ב'."""


def prompt_audio(day, symbol_block):
    base = build_base_context(day, symbol_block)
    return base + """

צור פודקאסט deep-dive בעברית באורך כ-20-25 דקות. שיחה מעמיקה ומרתקת בין שני
מנחים על הנושא. כיסוי שיטתי של כל ההיבטים שצוינו. דגש על משמעות קלינית
ויישום מעשי. הזכר בשם תרופות גנריות בלבד (לא שמות מסחריים)."""


def prompt_video(day, symbol_block):
    base = build_base_context(day, symbol_block)
    return base + """

צור סרטון הסבר באורך כ-10-15 דקות, סגנון whiteboard, בעברית.
חשוב: כל פעם שמופיע מושג שיש לו סמל ויזואלי ברשימה למעלה, השתמש בסמל הזה.
לדוגמה: כשמדברים על קולטן D2, הצג מחסום אדום על מסילה; כשמדברים על נוראפינפרין,
הצג זאב אלפא; כשמדברים על GABA, הצג מנהל מלון פותח שערים. הסמלים בונים זיכרון
ויזואלי עקבי. הוסף label בכל הופעה של סמל (כמו ב-Sketchymedical)."""


def prompt_infographic(day, symbol_block, aspect):
    base = build_base_context(day, symbol_block)
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
אינפוגרפיקה זו צריכה להיות ממוקדת ולא עמוסה — היבט אחד בלבד.
השתמש בסמלים מהרשימה. הוסף label לכל סמל."""


def prompt_study_guide(day, symbol_block):
    base = build_base_context(day, symbol_block)
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


def prompt_flashcards(day, symbol_block):
    base = build_base_context(day, symbol_block)
    return base + """

צור סט כרטיסיות שינון בעברית — שאלה + תשובה — לכיסוי high-yield של היום.
פורמט סטנדרטי. כמות: standard (15-20 כרטיסיות).
מינוח קליני מדויק."""


def prompt_quiz(day, symbol_block):
    base = build_base_context(day, symbol_block)
    return base + """

צור קוויז של 8-10 שאלות בעברית. סוג שאלות: vignette קליני קצר + multiple choice (4 אופציות). רמה: בחינת שלב ב'.
דגש על אבחנה מבדלת, סיבוכים נדירים, ניהול."""


# ── notebooklm CLI helpers ──────────────────────────────────────────────────
def nb_run(args, timeout=120):
    return subprocess.run([NOTEBOOKLM_BIN] + args, capture_output=True, text=True, timeout=timeout)


def use_notebook(nb_id):
    r = nb_run(["use", nb_id], timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"notebooklm use failed: {r.stderr[:300]}")


def start_audio(prompt):
    r = nb_run(["generate", "audio", prompt, "--format", "deep-dive", "--language", "he", "--json"], timeout=120)
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
    """Poll until every task is completed or failed."""
    if not task_ids:
        return {}

    pending = {tid: kind for kind, tid in task_ids.items() if tid}
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

    # Switch to main notebook
    use_notebook(NOTEBOOK_ID)

    if dry_run:
        print("[DRY RUN] Skipping NotebookLM API calls.")
        return None

    # Decide how many infographics based on density
    density = day.get("estimated_density", "medium")
    n_infographics = {"low": 1, "medium": 2, "high": 3}.get(density, 2)

    # Pick which aspects get their own infographic
    infographic_aspects = day.get("aspects", [])[:n_infographics]

    # Fire off all artifacts
    task_ids = {}
    print("Starting artifacts...")
    task_ids["audio"] = start_audio(prompt_audio(day, symbol_block))
    time.sleep(5)
    task_ids["video"] = start_video(prompt_video(day, symbol_block))
    time.sleep(5)
    task_ids["report"] = start_report(prompt_study_guide(day, symbol_block))
    time.sleep(5)
    task_ids["flashcards"] = start_flashcards(prompt_flashcards(day, symbol_block))
    time.sleep(5)
    task_ids["quiz"] = start_quiz(prompt_quiz(day, symbol_block))

    for i, aspect in enumerate(infographic_aspects):
        time.sleep(5)
        task_ids[f"infographic_{aspect}"] = start_infographic(prompt_infographic(day, symbol_block, aspect))

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
