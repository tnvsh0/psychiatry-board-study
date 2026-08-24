#!/usr/bin/env python3
"""
One-off migration script for step 5 of the symbol-expansion project.

Transforms schedule.json:
  1. Splits 4 over-packed days into individual per-disorder days:
     - Day 21 (Learning + Communication) -> SLD + Communication Disorders
     - Day 86 (Cluster A) -> Paranoid + Schizoid + Schizotypal
     - Day 90 (Histrionic + Narcissistic) -> Histrionic + Narcissistic
     - Day 91 (Cluster C) -> Avoidant + Dependent + OCPD
  2. Renumbers subsequent days, growing 120 -> 126 days total.
  3. Updates phase_structure metadata to reflect new ranges.
  4. Updates symbols_needed across existing days to reference the new
     specific PD/ND symbols where applicable (paranoid_pd, asd, etc.)
     for better visual reuse and memory consolidation.

Run once: python scripts/_expand_schedule_step5.py
"""
import json
from pathlib import Path

import sys
# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SCHED = ROOT / "schedule" / "schedule.json"


def new_day(index, phase, phase_name, type_, title_en, title_he,
            symbols, aspects, prompt_focus, density="medium",
            diagnosis=None):
    d = {
        "aspects": aspects,
        "symbols_needed": symbols,
        "estimated_density": density,
        "index": index,
        "phase": phase,
        "phase_name": phase_name,
        "type": type_,
        "title_en": title_en,
        "title_he": title_he,
    }
    if diagnosis:
        d["diagnosis"] = diagnosis
    d["prompt_focus"] = prompt_focus
    return d


# Standard aspect sets
PD_ASPECTS = ["dsm_criteria", "epidemiology", "etiology",
              "differential", "treatment_first_line", "treatment_second_line"]
ND_ASPECTS = ["dsm_criteria", "epidemiology", "etiology",
              "assessment_tools", "treatment_first_line", "treatment_second_line",
              "differential"]


# ── New day definitions ─────────────────────────────────────────────────────
# Each as (insertion_index, new-day-objects-as-replacement-list)

def expand():
    data = json.loads(SCHED.read_text(encoding="utf-8"))
    days = data["days"]

    # Locate the four target days by current index
    by_index = {d["index"]: d for d in days}

    # === Phase 2 — split day 21 (Learning + Communication) ===
    old_21 = by_index[21]
    new_phase2 = [
        new_day(
            index=21, phase=2, phase_name="Neurodevelopmental",
            type_="diagnosis", diagnosis="Specific Learning Disorder",
            title_en="Specific Learning Disorder (SLD)",
            title_he="הפרעות למידה ספציפיות (SLD)",
            symbols=["sld", "DSM_neurodevelopmental"],
            aspects=ND_ASPECTS,
            prompt_focus="SLD: dyslexia / dyscalculia / dysgraphia. קריטריונים DSM-5-TR (≥6 חודשים למרות התערבות; הישגים מדידים מתחת לציפיות לגיל). בידול חיוני מ-IDD (ב-SLD יש IQ תקין בתחומים אחרים). ספציפיירס: קריאה / כתיבה / מתמטיקה. רמות חומרה: קל/בינוני/חמור.",
            density="medium",
        ),
        new_day(
            index=22, phase=2, phase_name="Neurodevelopmental",
            type_="diagnosis", diagnosis="Communication Disorders",
            title_en="Communication Disorders (Language, Speech, Stuttering, Social-Pragmatic)",
            title_he="הפרעות תקשורת (שפה, הפקת דיבור, גמגום, פרגמטי)",
            symbols=["communication_disorders", "DSM_neurodevelopmental"],
            aspects=ND_ASPECTS,
            prompt_focus="ארבעה תת-סוגים: Language Disorder (אוצר מילים, מבנה משפט, שיח), Speech Sound Disorder (הפקת צלילים), Childhood-Onset Fluency / Stuttering (חזרות, הארכות, חסימות), Social (Pragmatic) Communication Disorder (שימוש חברתי בתקשורת, ללא restricted/repetitive — מבדיל מ-ASD). הערכה SLP, התערבות מוקדמת.",
            density="medium",
        ),
    ]

    # === Phase 8 — three splits ===
    old_86 = by_index[86]  # Cluster A
    new_clusterA = [
        new_day(
            index=86, phase=8, phase_name="Personality + Special Populations",
            type_="diagnosis", diagnosis="Paranoid Personality Disorder",
            title_en="Paranoid Personality Disorder",
            title_he="הפרעת אישיות פרנואידית",
            symbols=["paranoid_pd", "general_PD_criteria", "DSM_personality_disorders"],
            aspects=PD_ASPECTS,
            prompt_focus="קריטריונים (≥4 מ-7): חשד בניצול ללא בסיס, ספקות נאמנות, אי-נכונות לחשוף, קריאת משמעויות נסתרות, נטירת טינה, תפיסת התקפות, חשד בבגידה זוגית. אבחנה-מבדלת: לא בזמן פסיכוזה, לא סכיזופרניה פרנואידית, לא Delusional Disorder. טיפול: פסיכותרפיה (קושי בברית טיפולית — שקיפות וגבולות ברורים), אנטיפסיכוטי במינון נמוך לסימפטומים פרנואידיים חמורים.",
            density="medium",
        ),
        new_day(
            index=87, phase=8, phase_name="Personality + Special Populations",
            type_="diagnosis", diagnosis="Schizoid Personality Disorder",
            title_en="Schizoid Personality Disorder",
            title_he="הפרעת אישיות סכיזואידית",
            symbols=["schizoid_pd", "general_PD_criteria", "DSM_personality_disorders"],
            aspects=PD_ASPECTS,
            prompt_focus="קריטריונים (≥4 מ-7): לא מעוניין בקשרים קרובים, פעילויות בודדות, חוסר עניין מיני, הנאה מועטה, אין חברים, אדישות לשבח/ביקורת, קרירות רגשית. הבדל קריטי מ-Avoidant (Schizoid לא רוצה, Avoidant פוחד). אבחנה-מבדלת מ-ASD (ב-ASD יש restricted/repetitive). טיפול: פסיכותרפיה תומכת, יחס מכבד למרחק שלהם.",
            density="medium",
        ),
        new_day(
            index=88, phase=8, phase_name="Personality + Special Populations",
            type_="diagnosis", diagnosis="Schizotypal Personality Disorder",
            title_en="Schizotypal Personality Disorder",
            title_he="הפרעת אישיות סכיזוטיפלית",
            symbols=["schizotypal_pd", "general_PD_criteria", "DSM_personality_disorders"],
            aspects=PD_ASPECTS,
            prompt_focus="קריטריונים (≥5 מ-9): ideas of reference, אמונות מאגיות (טלפתיה, סיקס סנס), חוויות תפיסה משונות, חשיבה/דיבור מעורפלים, חשדנות, אפקט לא הולם, התנהגות/מראה אקסצנטריים, אין חברים, חרדה חברתית מוגזמת ופרנואידית. מקושר גנטית לספקטרום סכיזופרני. אבחנה-מבדלת מ-Schizophrenia (אין פסיכוזה גלויה). טיפול: SSRI לחרדה, אנטיפסיכוטי במינון נמוך לפרנויה.",
            density="medium",
        ),
    ]

    # day 87 (BPD core) becomes index 89; day 88 (BPD DBT) becomes 90; day 89 (Antisocial+CD) becomes 91
    # Then split day 90 (Histrionic + Narc) into 92, 93
    # Then split day 91 (Cluster C) into 94, 95, 96
    # Then days 92-100 shift to 97-105

    new_histnarc = [
        new_day(
            index=92, phase=8, phase_name="Personality + Special Populations",
            type_="diagnosis", diagnosis="Histrionic Personality Disorder",
            title_en="Histrionic Personality Disorder",
            title_he="הפרעת אישיות היסטריונית",
            symbols=["histrionic_pd", "general_PD_criteria", "DSM_personality_disorders"],
            aspects=PD_ASPECTS,
            prompt_focus="קריטריונים (≥5 מ-8): אי-נוחות שלא במרכז תשומת לב, פיתוי לא הולם, רגש משתנה רדוד, שימוש במראה, דיבור רושמני, דרמטיזציה עצמית, סוגסטיביליות, חשיבה על קשרים כיותר אינטימיים. אבחנה-מבדלת מ-BPD (HPD רדוד; BPD = פחד נטישה עמוק + self-harm). טיפול: פסיכותרפיה דינמית; חיזוק יציבות אישיותית.",
            density="medium",
        ),
        new_day(
            index=93, phase=8, phase_name="Personality + Special Populations",
            type_="diagnosis", diagnosis="Narcissistic Personality Disorder",
            title_en="Narcissistic Personality Disorder",
            title_he="הפרעת אישיות נרציסיסטית",
            symbols=["narcissistic_pd", "general_PD_criteria", "DSM_personality_disorders"],
            aspects=PD_ASPECTS,
            prompt_focus="קריטריונים (≥5 מ-9): גרנדיוזיות, פנטזיות הצלחה, אמונה ב'מיוחד', צורך בהערצה, זכאות, ניצול בין-אישי, חוסר אמפתיה, קנאה, התנשאות. 2 תתי-סוגים: גרנדיוזי (גלוי) ופגיע (סמוי, רגיש לביקורת). קומורבידיות דיכאון לאחר 'פגיעה נרציסיסטית'. טיפול: TFP, MBT, שיקום של ה-self.",
            density="medium",
        ),
    ]

    new_clusterC = [
        new_day(
            index=94, phase=8, phase_name="Personality + Special Populations",
            type_="diagnosis", diagnosis="Avoidant Personality Disorder",
            title_en="Avoidant Personality Disorder",
            title_he="הפרעת אישיות נמנעת",
            symbols=["avoidant_pd", "general_PD_criteria", "DSM_personality_disorders"],
            aspects=PD_ASPECTS,
            prompt_focus="קריטריונים (≥4 מ-7): נמנע ממקצועות עם מגע חברתי, לא מוכן לקשר אלא אם בטוח באהבה, מצומצם באינטימיות, עסוק בביקורת/דחייה, מעוכב במצבים חדשים, רואה את עצמו כחברתית-לא-מוצלח, נמנע מסיכונים. הבדל קריטי מ-Schizoid: רוצה קשרים, פוחד. אבחנה-מבדלת מ-SAD: כרוני וחוצה. טיפול: CBT, חשיפה הדרגתית, SSRI לחרדה משמעותית.",
            density="medium",
        ),
        new_day(
            index=95, phase=8, phase_name="Personality + Special Populations",
            type_="diagnosis", diagnosis="Dependent Personality Disorder",
            title_en="Dependent Personality Disorder",
            title_he="הפרעת אישיות תלותית",
            symbols=["dependent_pd", "general_PD_criteria", "DSM_personality_disorders"],
            aspects=PD_ASPECTS,
            prompt_focus="קריטריונים (≥5 מ-8): קושי בהחלטות ללא ייעוץ, צריך שאחרים יקחו אחריות, קושי לחלוק על דעה, קושי ליזום, הולך רחוק להשגת טיפוח, אי-נוחות לבד, מחפש קשר חדש כשנפסק קודם, פחד מטיפול-עצמי. שכיח יותר בנשים בקליניקה. טיפול: פסיכותרפיה לחיזוק עצמאות, אסרטיביות, טיפול קוגניטיבי על אמונות תלות.",
            density="medium",
        ),
        new_day(
            index=96, phase=8, phase_name="Personality + Special Populations",
            type_="diagnosis", diagnosis="Obsessive-Compulsive Personality Disorder",
            title_en="Obsessive-Compulsive Personality Disorder (OCPD)",
            title_he="הפרעת אישיות אובססיבית-קומפולסיבית (OCPD)",
            symbols=["ocpd", "general_PD_criteria", "DSM_personality_disorders"],
            aspects=PD_ASPECTS,
            prompt_focus="קריטריונים (≥4 מ-8): עיסוק בפרטים/חוקים/רשימות, פרפקציוניזם, מסירות מוגזמת לעבודה, מצפוניות מוגזמת, חוסר השלכת חפצים, חוסר אצילה, חיסכון קמצני, נוקשות. הבדל קריטי מ-OCD: OCPD = ego-syntonic (חושב שכולם טועים, הוא בסדר); OCD = ego-dystonic (סובל מאובססיות). אבחנה-מבדלת מ-Hoarding (אין הצטברות אקסטרמית). טיפול: CBT לפרפקציוניזם, גמישות, עבודה על איזון עבודה-חיים.",
            density="medium",
        ),
    ]

    # Build the new days list by walking through and inserting/skipping
    new_days = []
    skip_old_indices = {21, 86, 90, 91}  # original indices to replace
    inserted_at = {21: new_phase2, 86: new_clusterA, 90: new_histnarc, 91: new_clusterC}

    next_index = 1
    for d in days:
        old_idx = d["index"]
        if old_idx in skip_old_indices:
            # Insert the replacement set
            for new in inserted_at[old_idx]:
                # Override index with running counter
                new["index"] = next_index
                new_days.append(new)
                next_index += 1
        else:
            # Renumber existing day
            d_new = dict(d)
            d_new["index"] = next_index
            new_days.append(d_new)
            next_index += 1

    total_new = len(new_days)
    print(f"Total days before: {len(days)}")
    print(f"Total days after:  {total_new}")

    # === Update symbols_needed for existing kept days to use specific symbols ===
    # Maps from (existing-symbol-to-replace) to (list-of-specifics) per topic
    # Apply by title_en match where unambiguous
    symbol_upgrades = {
        # ND days (in new numbering after +1 shift starting from day 23)
        "ADHD — core": ["adhd"],            # add adhd to existing
        "ADHD treatment: stimulants": [],   # no add
        "ADHD treatment: non-stimulants": [],
        "Autism Spectrum Disorder": ["asd"],
        "Intellectual Developmental Disorder (Intellectual Disability)": ["idd"],
        "Tic disorders + Tourette's": ["tic_disorders"],
        # BPD-related
        "Borderline Personality Disorder": ["borderline_pd", "general_PD_criteria"],
        "BPD treatment: DBT": ["borderline_pd"],
        "Antisocial PD + Conduct Disorder": ["antisocial_pd", "general_PD_criteria"],
    }

    upgrades_applied = 0
    for d in new_days:
        title = d.get("title_en", "")
        if title in symbol_upgrades:
            additions = symbol_upgrades[title]
            for sym in additions:
                if sym not in d["symbols_needed"]:
                    d["symbols_needed"].append(sym)
                    upgrades_applied += 1
            # For ASD / IDD / Tic — also drop the generic DSM_neurodevelopmental
            # since we now have specific. Keep general_PD_criteria where relevant.
            if title in ("Autism Spectrum Disorder",
                         "Intellectual Developmental Disorder (Intellectual Disability)",
                         "Tic disorders + Tourette's"):
                if "DSM_neurodevelopmental" in d["symbols_needed"]:
                    # Keep as backup pointer at end
                    d["symbols_needed"].remove("DSM_neurodevelopmental")
                    d["symbols_needed"].append("DSM_neurodevelopmental")

    print(f"Symbol upgrades applied to existing days: {upgrades_applied}")

    # === Update metadata.phase_structure ===
    # Phase 1: days 1-15 (no change)
    # Phase 2: days 16-26 (+1)
    # Phase 3: days 27-41 (+1)
    # Phase 4: days 42-51 (+1)
    # Phase 5: days 52-66 (+1)
    # Phase 6: days 67-76 (+1)
    # Phase 7: days 77-86 (+1)
    # Phase 8: days 87-106 (+1 start + 5 expansion = ends at 106)
    # Phase 9: days 107-121 (+6)
    # Phase 10: days 122-126 (+6)
    data["metadata"]["phase_structure"] = {
        "1": "Foundations (anatomy, neurotransmitters, development) — chapters 1-15",
        "2": "Neurodevelopmental — chapters 16-26",
        "3": "Mood + Anxiety — chapters 27-41",
        "4": "OCD + Trauma — chapters 42-51",
        "5": "Psychotic + Antipsychotics — chapters 52-66",
        "6": "Sleep, Eating, Elimination — chapters 67-76",
        "7": "Substance Use — chapters 77-86",
        "8": "Personality + Special Populations — chapters 87-106",
        "9": "Neurology + Cross-cutting — chapters 107-121",
        "10": "Psychotherapy + Integration — chapters 122-126",
    }
    data["metadata"]["total_days"] = total_new
    data["metadata"]["unit_label"] = "chapter"  # Conceptual rename: chapters, not 24-hour days
    data["metadata"]["build_date"] = "2026-05-26"

    # Verify phase boundaries match the days we actually placed
    for d in new_days:
        phase = d["phase"]
        idx = d["index"]
        expected_ranges = {
            1: (1, 15), 2: (16, 26), 3: (27, 41), 4: (42, 51), 5: (52, 66),
            6: (67, 76), 7: (77, 86), 8: (87, 106), 9: (107, 121), 10: (122, 126),
        }
        lo, hi = expected_ranges[phase]
        if not (lo <= idx <= hi):
            print(f"  [!] Day {idx} (phase {phase}) outside expected range {lo}-{hi}: {d.get('title_en','')}")

    data["days"] = new_days
    SCHED.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {SCHED}")
    print(f"Phase structure updated to {total_new} chapters.")


if __name__ == "__main__":
    expand()
