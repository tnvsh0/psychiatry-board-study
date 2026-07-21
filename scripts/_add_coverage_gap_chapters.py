#!/usr/bin/env python3
"""Insert the 18 coverage-gap chapters into schedule.json and renumber.

Each new chapter is anchored to an EXISTING chapter by title_en (stable across
renumbering) rather than by index, so this script is re-readable as a record of
intent. Indices are recomputed from position afterwards.

Gap source: COVERAGE_AUDIT.md §2 (Dulcan Parts IV/VI/VII) and §3 (topics the
traditional flashcard summary covers that the curriculum did not).
"""
import json
from pathlib import Path

SCHEDULE = Path(__file__).resolve().parents[1] / "schedule" / "schedule.json"

A = ["history_origin", "core_principles", "practice_method", "evidence", "indications"]

NEW = [
    # ── Phase 1: must precede every drug chapter ──────────────────────────
    ("Mood, affect, and temperament — clinical assessment", {
        "aspects": ["core_principles", "drug_mechanism", "practical_use", "monitoring", "side_effects_class"],
        "symbols_needed": [], "estimated_density": "high", "phase": 1,
        "phase_name": "Foundations", "type": "foundation",
        "title_en": "Principles of pediatric psychopharmacology",
        "title_he": "עקרונות פסיכופרמקולוגיה בילדים ובמתבגרים",
        "prompt_focus": "Dulcan ch.33. פרמקוקינטיקה ופרמקודינמיקה בילדים: מטבוליזם כבדי מהיר יותר, נפח פיזור שונה, T½ קצר → לעיתים מינון/ק\"ג גבוה יותר ופיצול מנות. CYP450 ואינטראקציות. מרשם off-label ו-FDA black box (אובדנות ב-SSRI מתחת לגיל 25). הסכמה מדעת של קטין ושל שני ההורים. ניטור בסיסי לפני ובמהלך טיפול. start low, go slow. היענות (adherence) ונשירה.",
    }),
    # ── Phase 4: trauma cluster ───────────────────────────────────────────
    ("Acute Stress + Adjustment Disorder", {
        "aspects": ["dsm_criteria", "epidemiology", "differential", "clinical_presentation", "treatment_overview"],
        "symbols_needed": [], "estimated_density": "medium", "phase": 4,
        "phase_name": "OCD and Trauma", "type": "diagnosis",
        "title_en": "Grief, bereavement, and Prolonged Grief Disorder",
        "title_he": "אבל, שכול והפרעת אבל ממושך",
        "prompt_focus": "Dulcan ch.16. Prolonged Grief Disorder — אבחנה חדשה ב-DSM-5-TR: בילדים ומתבגרים נדרשים ≥6 חודשים מהאובדן (במבוגרים ≥12). אבל נורמטיבי מול PGD מול MDD; ביטול ה-bereavement exclusion. הבנת מוות לפי שלב התפתחותי (סופיות, אוניברסליות, סיבתיות). אבל טראומטי. טיפול: Complicated Grief Treatment, TF-CBT, קבוצות שכול.",
    }),
    (None, {  # chained after the previous new chapter
        "aspects": ["recognition", "clinical_presentation", "risk_assessment", "management", "medical_mimics"],
        "symbols_needed": [], "estimated_density": "high", "phase": 4,
        "phase_name": "OCD and Trauma", "type": "foundation",
        "title_en": "Child abuse and neglect",
        "title_he": "התעללות והזנחה בילדים",
        "prompt_focus": "Dulcan ch.24. סוגים: פיזית, מינית, רגשית, הזנחה, ו-Factitious Disorder Imposed on Another (Munchausen by proxy). סימנים מחשידים: חבלות בדפוס לא-תואם או בגיל לא-תואם, שברים מטאפיזיאליים, abusive head trauma ודימומים ברשתית. אבחנה מבדלת רפואית חיונית: osteogenesis imperfecta, הפרעות קרישה, כתמים מונגוליים. השלכות ארוכות טווח: PTSD, BPD, מודל ה-ACEs. חובת דיווח בישראל — חוק העונשין סעיף 368ד. עקרונות ראיון ילד נפגע והימנעות משאלות מכוונות.",
    }),
    # ── Phase 5: closes the somatic-treatment gap ─────────────────────────
    ("Acute psychosis ED management", {
        "aspects": ["indications", "practice_method", "side_effects_class", "monitoring", "evidence"],
        "symbols_needed": [], "estimated_density": "medium", "phase": 5,
        "phase_name": "Psychosis and Antipsychotics", "type": "treatment_focus",
        "title_en": "ECT and other somatic therapies",
        "title_he": "ECT וטיפולים סומטיים אחרים",
        "prompt_focus": "ECT בילדים ומתבגרים. התוויות: קטטוניה, NMS, דיכאון מג'ורי עמיד, מאניה חריפה, פסיכוזה עמידה, אובדנות אקוטית עם סירוב אכילה. מנגנון משוער, פרוטוקול (unilateral מול bilateral, תדירות, מספר טיפולים), הרדמה והרפיית שרירים. תופעות לוואי: אמנזיה אנטרוגרדית ורטרוגרדית, בלבול פוסט-איקטלי. שיקולים משפטיים ואתיים בקטין בישראל. סקירה קצרה: rTMS, ketamine/esketamine, VNS, DBS.",
    }),
    # ── Phase 8: special populations + Israeli law ────────────────────────
    ("Forensic psychiatry: competency, insanity, Tarasoff", {
        "aspects": ["core_principles", "practical_use", "risk_assessment", "indications"],
        "symbols_needed": [], "estimated_density": "high", "phase": 8,
        "phase_name": "Personality + Special Populations", "type": "foundation",
        "title_en": "Israeli law in child and adolescent psychiatry",
        "title_he": "החוק הישראלי בפסיכיאטריה של הילד והמתבגר",
        "prompt_focus": "הדין הישראלי — להשלמת הפרק הקודם שעוסק בדין האמריקאי. חוק טיפול בחולי נפש התשנ\"א-1991: אשפוז קטין, הוראת אשפוז, ועדה פסיכיאטרית מחוזית. חוק הנוער (טיפול והשגחה) התש\"ך-1960. גיל הסכמה לטיפול והסכמת שני ההורים; מחלוקת בין הורים. חובת דיווח על התעללות — חוק העונשין סעיף 368ד. סודיות הקטין מול חובת יידוע הורים. אחריות פלילית מגיל 12. כשירות למתן עדות. אפוטרופסות.",
    }),
    ("Disruptive/Impulse: ODD, IED, Kleptomania, Pyromania", {
        "aspects": ["clinical_presentation", "differential", "risk_assessment", "management", "treatment_overview"],
        "symbols_needed": [], "estimated_density": "high", "phase": 8,
        "phase_name": "Personality + Special Populations", "type": "foundation",
        "title_en": "Aggression and violence in youth",
        "title_he": "אגרסיביות ואלימות בילדים ובמתבגרים",
        "prompt_focus": "Dulcan ch.28. ההבחנה המרכזית: אגרסיה תגובתית/אפקטיבית (reactive) מול פרואקטיבית/אינסטרומנטלית (proactive) — משמעות פרוגנוסטית וטיפולית שונה. הערכת סיכון לאלימות. ניהול אקוטי: de-escalation מילולי, הגבלה כימית, הגבלה פיזית ותיעודה. טיפול תרופתי: risperidone ואנטיפסיכוטים אטיפיים, מייצבי מצב רוח, אגוניסטים אלפא, וטיפול ב-ADHD כמפחית אגרסיה. הורים והתערבויות התנהגותיות. בריונות (bullying) ואלימות בבית הספר.",
    }),
    ("Self-injurious behavior (NSSI)", {
        "aspects": ["clinical_presentation", "psychiatric_relevance", "management", "core_principles"],
        "symbols_needed": [], "estimated_density": "medium", "phase": 8,
        "phase_name": "Personality + Special Populations", "type": "foundation",
        "title_en": "Family transitions: divorce, adoption, foster care",
        "title_he": "מעברים משפחתיים: גירושין, אימוץ ואומנה",
        "prompt_focus": "Dulcan ch.30. גירושין: תגובות לפי שלב התפתחותי, השפעת קונפליקט הורי מתמשך (המנבא החזק יותר מהגירושין עצמו), משמורת והסדרי שהות, ניכור הורי, תפקיד הפסיכיאטר בחוות דעת לבית משפט וגבולותיו. אימוץ: גילוי, שאלות זהות בגיל ההתבגרות, שכיחות מוגברת של הפרעות התנהגות, אימוץ בין-ארצי. אומנה: חוסר יציבות במיקום, RAD ו-DSED. גורמי חוסן משפחתיים ומגנים.",
    }),
    (None, {
        "aspects": ["core_principles", "clinical_presentation", "assessment_tools", "practical_use"],
        "symbols_needed": [], "estimated_density": "medium", "phase": 8,
        "phase_name": "Personality + Special Populations", "type": "foundation",
        "title_en": "Cultural and religious issues in child mental health",
        "title_he": "היבטים תרבותיים ודתיים בבריאות הנפש של הילד",
        "prompt_focus": "Dulcan ch.25 + DSM-5-TR Section III. Cultural Formulation Interview — מבנה ושימוש. ניבים תרבותיים למצוקה (cultural idioms of distress) והסברים תרבותיים למחלה. הבדלים בביטוי סימפטומים ובדפוסי פנייה לעזרה. אוכלוסיות בישראל: החברה החרדית, החברה הערבית, עולים מאתיופיה ומחבר העמים. סטיגמה ומחסומי נגישות. עבודה עם מנהיגות דתית. שימוש נכון במתורגמן (ולא בבן משפחה).",
    }),
    # ── Phase 9: cross-cutting clinical ───────────────────────────────────
    ("Sleep medicine intensive", {
        "aspects": ["recognition", "emergency_management", "risk_assessment", "differential", "safety_planning"],
        "symbols_needed": [], "estimated_density": "high", "phase": 9,
        "phase_name": "Neurology + Cross-cutting", "type": "foundation",
        "title_en": "Pediatric psychiatric emergencies",
        "title_he": "חירום פסיכיאטרי בילדים ובמתבגרים",
        "prompt_focus": "Dulcan ch.29. הערכה בחדר מיון: אובדנות, אלימות, אגיטציה חריפה, אפיזודה פסיכוטית ראשונה, קטטוניה. סולם ההתערבות: de-escalation מילולי ← הגבלה כימית (בחירת תרופה ומינון לפי גיל ומשקל) ← הגבלה פיזית, כולל אינדיקציות, ניטור ותיעוד. שלילה שיטתית של סיבה רפואית או טוקסית. החלטת אשפוז מול שחרור עם תוכנית בטיחות ומעורבות הורים. חירום תרופתי: NMS, תסמונת סרוטונין, דליריום אנטיכולינרגי.",
    }),
    (None, {
        "aspects": ["clinical_presentation", "psychiatric_relevance", "differential", "management", "treatment_overview"],
        "symbols_needed": [], "estimated_density": "high", "phase": 9,
        "phase_name": "Neurology + Cross-cutting", "type": "foundation",
        "title_en": "Consultation-liaison: pediatrics, oncology, chronic illness",
        "title_he": "ייעוץ-קישור: פדיאטריה, אונקולוגיה ומחלות כרוניות",
        "prompt_focus": "Dulcan ch.48-49. מודל עבודת הייעוץ במחלקת ילדים ומול רופא ילדים ראשוני. דליריום בילד מאושפז — זיהוי, אבחנה מבדלת וטיפול. היענות לטיפול במחלה כרונית (סוכרת נעורים, CF, אסתמה, מחלות מעי דלקתיות). אונקולוגיה: תגובה לאבחנה, כאב, השפעות קוגניטיביות מאוחרות של כימותרפיה והקרנות, שורדים. מחלה סופנית: שיחה על מוות עם ילד לפי גיל, ליווי המשפחה, אבל צפוי. סומטיזציה במרפאת הילדים.",
    }),
    # ── Phase 10: the psychosocial-treatment gap ──────────────────────────
    ("DBT: theory + practice", {
        "aspects": list(A), "symbols_needed": [], "estimated_density": "medium", "phase": 10,
        "phase_name": "Psychotherapy + Integration", "type": "psychotherapy",
        "title_en": "IPT-A: Interpersonal psychotherapy for adolescents",
        "title_he": "IPT-A — פסיכותרפיה בין-אישית למתבגרים",
        "prompt_focus": "Dulcan ch.42. ארבעה תחומי בעיה: אבל (grief), מחלוקת בתפקיד בין-אישי (role dispute), מעבר בתפקיד (role transition), וחסכים בין-אישיים (interpersonal deficits). מבנה של כ-12 מפגשים בשלושה שלבים. האינוונטר הבין-אישי. התאמות ייחודיות למתבגרים: מעורבות הורים, יחסי עמיתים, מעברי חיים אופייניים. בסיס הראיות ביעילות בדיכאון במתבגרים והשוואה ל-CBT.",
    }),
    (None, {
        "aspects": list(A), "symbols_needed": [], "estimated_density": "medium", "phase": 10,
        "phase_name": "Psychotherapy + Integration", "type": "psychotherapy",
        "title_en": "Motivational interviewing",
        "title_he": "ראיון מוטיבציוני",
        "prompt_focus": "Dulcan ch.44. Miller & Rollnick. רוח ה-MI: שותפות, קבלה, חמלה ועוררות (evocation). ארבעת התהליכים: engaging, focusing, evoking, planning. מיומנויות OARS (שאלות פתוחות, אישור, הקשבה משקפת, סיכום). change talk מול sustain talk; גלגול עם התנגדות במקום עימות. שלבי השינוי של Prochaska ו-DiClemente. יישום בהתמכרויות במתבגרים, בהיענות לטיפול ובהפרעות אכילה.",
    }),
    (None, {
        "aspects": ["indications", "core_principles", "practice_method", "practical_use", "evidence"],
        "symbols_needed": [], "estimated_density": "medium", "phase": 10,
        "phase_name": "Psychotherapy + Integration", "type": "psychotherapy",
        "title_en": "Levels of care: inpatient, partial hospitalization, residential, systems of care",
        "title_he": "מסגרות טיפול: אשפוז, אשפוז יום, מגורים ו-Systems of Care",
        "prompt_focus": "Dulcan ch.45-46. רצף המסגרות: אמבולטורי ← intensive outpatient ← אשפוז יום ← residential ← אשפוז מלא, והקריטריונים למעבר בין רמות. טיפול מילייה: מבנה יומי, קהילה טיפולית, token economy, ניהול משבר במחלקה, שימוש בבידוד והגבלה. Wraparound ו-Systems of Care: עקרונות, ניהול מקרה, שירותים בבית ובקהילה. תכנון שחרור ומניעת אשפוז חוזר. מבנה המערך בישראל.",
    }),
    (None, {
        "aspects": ["core_principles", "practice_method", "indications", "practical_use", "evidence"],
        "symbols_needed": [], "estimated_density": "medium", "phase": 10,
        "phase_name": "Psychotherapy + Integration", "type": "psychotherapy",
        "title_en": "School-based interventions and consultation",
        "title_he": "התערבויות וייעוץ במסגרת בית הספר",
        "prompt_focus": "Dulcan ch.47. שיתוף פעולה עם הצוות החינוכי והיועצת. התאמות והנגשה בלמידה ובבחינות. חינוך מיוחד וזכאות. רמות מניעה: אוניברסלית, סלקטיבית ומכוונת. תוכניות מניעת בריונות ותוכניות SEL (למידה חברתית-רגשית). איתור מוקדם והפניה. סודיות מול שיתוף מידע עם בית הספר. ועדת השמה וזכאות ואפיון בישראל.",
    }),
    ("Psychodynamic: Anna Freud, Klein, Kohut, Mentalization", {
        "aspects": list(A), "symbols_needed": [], "estimated_density": "high", "phase": 10,
        "phase_name": "Psychotherapy + Integration", "type": "psychotherapy",
        "title_en": "Object relations: Winnicott, Mahler, Kernberg",
        "title_he": "יחסי אובייקט: ויניקוט, מאהלר, קרנברג",
        "prompt_focus": "ויניקוט: אם טובה דיה, holding, אובייקט מעבר ותופעות מעבר, true self מול false self, המרחב הפוטנציאלי והמשחק. מאהלר: תהליך ההיפרדות-אינדיבידואציה — אוטיזם נורמלי, סימביוזה, ותת-השלבים בקיעה (differentiation), תרגול (practicing), התקרבות (rapprochement), וקביעות אובייקט. קרנברג: ארגון אישיות גבולי, פיצול כמנגנון מרכזי, זהות מפוזרת (identity diffusion), אגרסיה, וטיפול ממוקד העברה (TFP).",
    }),
    (None, {
        "aspects": ["history_origin", "core_principles", "practice_method", "indications", "evidence"],
        "symbols_needed": [], "estimated_density": "high", "phase": 10,
        "phase_name": "Psychotherapy + Integration", "type": "psychotherapy",
        "title_en": "Later theorists: Erikson, Bion, Ogden, Lacan, Spitz, Stern, relational",
        "title_he": "תיאורטיקנים נוספים: אריקסון, ביון, אוגדן, לאקאן, שפיץ, שטרן וההתייחסותיים",
        "prompt_focus": "אריקסון: שמונה השלבים הפסיכו-חברתיים, ומשבר הזהות מול בלבול תפקידים בגיל ההתבגרות. ביון: מיכל-מוכל (container-contained), reverie, אלמנטים בטא ואלפא, התפתחות החשיבה. אוגדן: העמדה האוטיסטית-מגעית, השלישי האנליטי. לאקאן: שלב המראה, והסדרים הדמיוני, הסמלי והממשי. רנה שפיץ: הוספיטליזם, דיכאון אנקליטי, רצף מחאה-ייאוש-ניתוק. דניאל שטרן: תחושות העצמי המתהוות, התכווננות רגשית (affect attunement), רגעי מפגש. הגישה הבין-אישית (Sullivan) וההתייחסותית (Mitchell): enactment והשדה הדו-אישי.",
    }),
    (None, {
        "aspects": ["core_principles", "practical_use", "indications", "evidence"],
        "symbols_needed": [], "estimated_density": "low", "phase": 10,
        "phase_name": "Psychotherapy + Integration", "type": "foundation",
        "title_en": "Telemental health for children and adolescents",
        "title_he": "בריאות נפש מרחוק לילדים ומתבגרים",
        "prompt_focus": "Dulcan ch.32. בסיס הראיות ליעילות טלה-פסיכיאטריה בילדים ומתבגרים. התאמות לבדיקה מרחוק: תצפית במשחק, שיתוף ההורה, הבטחת מרחב פרטי לילד. אתיקה, סודיות ואבטחת מידע. הסכמה מדעת ייעודית. ניהול מצב חירום מרחוק — איתור מיקום מראש ותוכנית בטיחות. סוגיות רישוי וגבולות שיפוט. אפליקציות ו-digital therapeutics כתוספת לטיפול.",
    }),
    (None, {
        "aspects": ["core_principles", "evidence", "practice_method", "practical_use"],
        "symbols_needed": [], "estimated_density": "medium", "phase": 10,
        "phase_name": "Psychotherapy + Integration", "type": "foundation",
        "title_en": "Evidence-based practice in child psychiatry",
        "title_he": "רפואה מבוססת ראיות בפסיכיאטריה של הילד",
        "prompt_focus": "Dulcan ch.23. היררכיית הראיות: מטה-אנליזה וסקירה שיטתית, RCT, מחקר עוקבה, מקרה-ביקורת, סדרת מקרים. מדדים לפירוש תוצאה: NNT, NNH, גודל אפקט (Cohen's d), רווח בר-סמך, ומשמעות סטטיסטית מול משמעות קלינית. הטיות נפוצות: הטיית בחירה, הטיית פרסום, allegiance bias. קריאה ביקורתית של מאמר טיפולי. שילוב ראיות עם ניסיון קליני והעדפת המטופל. מקורות: Cochrane, AACAP Practice Parameters.",
    }),
]


def main() -> int:
    data = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    days = data["days"]
    before = len(days)

    by_title = {d["title_en"]: d for d in days}
    anchor = None
    for anchor_title, chapter in NEW:
        if anchor_title is not None:
            if anchor_title not in by_title:
                raise SystemExit(f"anchor not found: {anchor_title!r}")
            anchor = by_title[anchor_title]
        elif anchor is None:
            raise SystemExit("chained chapter with no preceding anchor")
        days.insert(days.index(anchor) + 1, chapter)
        anchor = chapter  # chain: a None anchor means "right after the last insert"

    # Index is position, nothing else. Renumber from scratch.
    for i, d in enumerate(days, start=1):
        d["index"] = i

    # Rebuild phase_structure from the actual data rather than hand-editing it.
    ranges: dict[int, list[int]] = {}
    for d in days:
        ranges.setdefault(d["phase"], []).append(d["index"])
    names = {}
    for d in days:
        if "phase_name" in d:
            names.setdefault(d["phase"], d["phase_name"])
    data["metadata"]["total_days"] = len(days)
    data["metadata"]["phase_structure"] = {
        str(p): f"{names.get(p, '?')} — chapters {min(v)}-{max(v)}"
        for p, v in sorted(ranges.items())
    }

    # Phases must stay contiguous, or the sidebar grouping breaks.
    for p, v in sorted(ranges.items()):
        if v != list(range(min(v), max(v) + 1)):
            raise SystemExit(f"phase {p} is not contiguous: {v}")

    SCHEDULE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"chapters: {before} -> {len(days)}  (+{len(days) - before})")
    for p, v in sorted(ranges.items()):
        print(f"  phase {p}: {min(v)}-{max(v)}  ({len(v)} chapters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
