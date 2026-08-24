#!/usr/bin/env python3
"""
Add `hebrew_explanation` field to every entry in symbols.json.

The Hebrew explanation is intentionally CONCISE (2-3 sentences) covering:
1. What the symbol is (in Hebrew)
2. Why the mnemonic works
3. The key clinical function

This file is the single source of Hebrew text for the auto-generated
legend that accompanies every daily-study artifact (image/video/etc.).
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
SYM = ROOT / "schedule" / "symbols.json"

HEBREW = {
    # ── SCENES ──────────────────────────────────────────────────────────────
    "DOPA_MINE": "מכרה DOPA-MINE — סצנה ראשית לדופמין. ארבעה אגפים (מסולימבי, מסוקורטיקלי, ניגרוסטריאטלי, טוברואינפונדיבולרי). מעליות עולות = D1-family (Gs); מחסומים אדומים = D2-family (Gi); קרונות = שחרור דופמין.",
    "BUS_LINE_1A": "תחנת אוטובוס 1A. שפן רועד = חרדה. אוטובוסים שונים בקו = אגוניסטים חלקיים שונים ל-5-HT1A. אוטובוס מאחר = השהיית הופעת SSRI.",
    "TAROT_2A": "פרלור טארוט אפלולי עם שני קלפי Ace = 5-HT2A. ההיפי הקורא = פסיכדלים. שוטר עם פנס = אנטיפסיכוטי אטיפי. חדרים סמוכים: מטבח (2C), חדר-מים (5-HT3), ספרייה (5-HT6).",
    "CONVENIENCE_STORE_7": "7-Eleven פתוח 24/7. השלט 7 = קולטן 5-HT7. ה-clerk הלילי = Lurasidone שמכבה אורות = שיקום מקצב יממה.",
    "ALPHA_WOLF_PACK": "להקת זאבי אלפא ביער מושלג. זאב בודד = α1 (אמיתי, פוסט-סינפסי). שני זאבים שבהם #2 = α2 (אוטו-רצפטור, רק מתחזה לאלפא).",
    "BEETHOVEN_ORCHESTRA": "תזמורת בטהובן. שרביט #1 + תופי-לב = β1 (לב). שרביט #2 + נשיפה = β2 (ריאות). רשת דייגים בכניסה = NET, בטהובן 'דג' תווים תועים (NE) חזרה.",
    "AA_HOTEL": "מלון Aa בן 5 קומות — קולטן GABA-A. שם המלון = α-α (תת-יחידות). חיילי כלוריד ירוקים = Cl⁻ נכנסים פנימה. גדר תיל = ברביטורט. בית כנסת בית-ב' לידו = GABA-B.",
    "ASPARAGUS_VIP_DOOR": "דלת מועדון NMDA. מר אספרגוס (N-methyl-D-Aspartate) = אגוניסט. חלוק-G אפור = Glycine קו-אגוניסט. מגנט פרסה = Mg²⁺ block. אורחים מסויידים '2+' = Ca²⁺ נכנסים.",
    "DAIRY_FARM": "חוות חלב לאופיואידים. פרה 'MOO' = μ. תיש בועט 'K' = κ (דיספוריה). מטוס Delta = δ. דליי מסומנים: אנלגזיה / אופוריה / דיכוי נשימה / עצירות.",
    "WONDERLAND_TEAPARTY": "מסיבת התה של Alice במולנדרלנד — כל דמות = קולטן מוסקריני. הארנב הלבן (שעון 1 O'Clock) = M1. מלכת לבבות (2 of Hearts) = M2. ה-Mad Hatter עם 3 כוסות = M3. רעלת אנטיכולינרגית = הכל יוצא משליטה.",
    "CIGARETTE_THEATER": "תיאטרון סיגריות לקולטנים ניקוטיניים. מותג '42' = α4β2. 'Lucky 7' של סבא חכם = α7. גפרור עץ (אצית) = Acetylcholine.",
    "H_HOUSE": "בית בצורת H בן 4 קומות = קולטני היסטמין. קומה 1 (סלון נמנום) = H1. קומה 2 (מטבח H2O) = H2 (חומצת קיבה). קומה 3 (חדר עבודה) = H3 (אוטו-רצפטור). קומה 4 (גג) = H4 (חיסון).",
    "ER_GUARD_POST": "סנטרי-S1 בכניסה ל-ER (אנדופלסמטית רטיקולום) = chaperone protein sigma-1. Fluvoxamine ו-Dextromethorphan = מבקרים תכופים.",
    "RAPHE_TAPESTRY": "סדנת שטיחים פרסיים לאורך הגזע מוחי = גרעיני raphe. אורגים = נוירוני 5-HT. השם 'raphe' = 'תפר' ביוונית.",
    "LOCUS_COERULEUS_BLUEPOST": "תא טלפון משטרתי כחול = locus coeruleus ('כתם כחול' בלטינית). מוקד כל ה-NE במוח. מתפרסם לכל מקום.",
    "TUBEROMAMMILLARY_DAIRY": "פרות מימרי בהיפותלמוס הפוסטריורי = גרעין histamine. שואבות היסטמין שמדליק את המוח (ערות).",
    "MEYNERT_MYNAH": "ציפור מינה חכמה בבזאל פורברין = הגרעין הבזאלי של מיינרט (אצטיל-כולין → קליפת המוח, זיכרון).",

    # ── IONS ────────────────────────────────────────────────────────────────
    "Mg2+": "מגנט פרסה. Mg = Magnesium = מגנט (אותה אות M). חוסם NMDA במנוחה.",
    "Ca2+": "אורחים מסויידים-לבנים עם תגית '2+'. Ca = סידן = chalk (גיר). 2+ = המטען החיובי.",
    "Cl-": "חיילים ירוקים עם מגן '−'. Cl = chlorine = הגז הירוק במלחמת העולם הראשונה. נכנס לתעלת GABA-A → היפרפולריזציה.",
    "Na+": "חיילים עם גבישי מלח לבנים ו-'+'. Na = sodium = מלח. עיקרי בתעלות AMPA, nAChR.",
    "K+": "בננות צהובות עם '+' . K = potassium = banana (עשירה בקליום). זרימה החוצה → ריפולריזציה.",

    # ── BATCH 1A: DOPAMINE + SEROTONIN ───────────────────────────────────────
    "D1_receptor": "קולטן D1 — דופמין מ-D1-family (Gs, מעורר). סמל: מעלית '1↑' עולה לקומה העליונה של PFC. תפקיד: זיכרון עבודה, סימפטומים חיוביים. Gs = ↑cAMP.",
    "D5_receptor": "קולטן D5 — D1-family (Gs). סמל: מעלית '5' בספריית ההיפוקמפוס. בעיקר זיכרון. פחות קליני.",
    "D2_receptor": "קולטן D2 — D2-family (Gi, מעכב). הקולטן ה-קריטי ביותר באנטיפסיכוטיים. סמל: מחסום אדום ענק '2' במסלול מסולימבי. חסימה = פסיכוזה מטופלת; אבל גם פירמידות EPS, חלב פרולקטין, ועוד.",
    "D3_receptor": "קולטן D3 — D2-family. סמל: שלושה מחסומים בלימבי (לב). Cariprazine סלקטיבי-D3 (דיכאון בביפולר).",
    "D4_receptor": "קולטן D4 — D2-family. סמל: ארבעה ברגים על דלת שקטה (low-EPS). Clozapine עם מפתח-Z פותח שם.",
    "5-HT1A_receptor": "קולטן 5-HT1A — סרוטונין (Gi, אוטו-רצפטור + post-syn). שפן רגוע באוטובוס 1A. Buspirone, Vilazodone, Vortioxetine, Aripiprazole = אוטובוסים שונים בקו 1A (partial agonists). דחיית-הופעת SSRI דרך desensitization.",
    "5-HT2A_receptor": "קולטן 5-HT2A — סרוטונין (Gq, cortical). שולחן טארוט עם 2 קלפי Ace. אגוניסט = LSD/פסילוסיבין. אנטיפסיכוטיים אטיפיים חוסמים = מנגנון מרכזי שלהם. Pimavanserin = inverse agonist.",
    "5-HT2C_receptor": "קולטן 5-HT2C — סרוטונין (Gq, היפותלמוס/תיאבון). מטבח עם 2 שפים שומרים על צנצנת עוגיות. חסימה (Mirtazapine, Olanzapine, Quetiapine) = שודדים שמתפרצים = עליה במשקל.",
    "5-HT3_receptor": "קולטן 5-HT3 — ה-יחיד היונוטרופי בסרוטונין! שלושה שערי ברזל לוהטים זורקים מי-בחילה ירוקים. חסימה (Ondansetron, Mirtazapine, Vortioxetine) = פקקים שעוצרים בחילה.",
    "5-HT6_receptor": "קולטן 5-HT6 — סרוטונין (Gs, קוגניציה). ספרייה עם 6 תעודות PhD. חסימה (Vortioxetine) = פרוקוגניטיבי.",
    "5-HT7_receptor": "קולטן 5-HT7 — סרוטונין (Gs, מקצב יממה). 7-Eleven פתוח 24/7. חסימה (Lurasidone) = מקצב שינה-ערות משוקם.",
    "SERT": "מובל סרוטונין — מטרת SSRI/SNRI/TCA. דוור 'SERT' אוסף חבילות מהסינפסה. SSRI = שלט 'אין משלוחים' = יותר סרוטונין בסינפסה. דחיית הופעת 4-6 שבועות.",

    # ── BATCH 1B: NE + GABA + NMDA ──────────────────────────────────────────
    "alpha1_receptor": "קולטן α1 — אדרנרגי פוסט-סינפסי (Gq, מעורר). זאב אלפא אחד יוצא לציד. חסימה (Prazosin = הרגעת סיוטי PTSD; אנטיפסיכוטיים = orthostasis צד).",
    "alpha2_receptor": "קולטן α2 — אוטו-רצפטור פרה-סינפסי (Gi). שני זאבים שבהם '#2' מתחזה לאלפא וקורא לעדר חזור. Clonidine, Guanfacine = אגוניסטים שמרגיעים את העדר = ADHD, גמילת אופיואידים, יל\"ד.",
    "beta1_receptor": "קולטן β1 — אדרנרגי Gs (לב). בטהובן עם שרביט '#1' מנצח על תופי לב. חסימה (Propranolol) = לב רגוע.",
    "beta2_receptor": "קולטן β2 — אדרנרגי Gs (ריאות, שריר). בטהובן עם שרביט '#2' מנצח על כלי נשיפה. אגוניסט (Albuterol) = פתיחת דרכי אוויר; חסימה לא-סלקטיבית = ברונכוספסם.",
    "NET": "מובל נוראפינפרין — רשת דייגים (NET = רשת!) בכניסת התזמורת. אטומוקסטין, בופרופיון, SNRI, TCA = קורעים בה חורים = יותר NE בסינפסה.",
    "GABA_neurotransmitter": "GABA — המעכב הראשי. מנהל מלון Aa עם המפתח הראשי. פותח את התעלה → חיילי כלוריד ירוקים נכנסים → היפרפולריזציה.",
    "GABA_A_receptor": "קולטן GABA-A — תעלת כלוריד יונוטרופית, 5-מרית. המלון עצמו (5 קומות). BZD = מבקר שדגדג את המנהל → הדלת נפתחת תכופות. ברביטורט = פורץ-מנעולים → הדלת נשארת פתוחה זמן רב יותר.",
    "GABA_A_alpha1_subunit": "תת-יחידה α1 של GABA-A — קומה 1 'sleep lobby'. Z-drugs (Zolpidem, Eszopiclone) = מעלית אקספרס שיורדת רק לכאן = שינה.",
    "GABA_A_alpha2_alpha3_subunits": "תת-יחידות α2/α3 — קומות אמצעיות (calm lounge + group therapy). BZD רגילים מטפלים בחרדה דרכן.",
    "GABA_A_alpha5_subunit": "תת-יחידה α5 — קומה 5 'memory penthouse' עם כספת. נטרול (Midazolam פרה-ניתוחי) = שדידת הכספת = אמנזיה.",
    "GABA_B_receptor": "קולטן GABA-B — מטבוטרופי (Gi → תעלות K⁺). 'בית-ב' סמוך למלון. Baclofen מתפלל שם = ספסטיות.",
    "NMDA_receptor": "קולטן NMDA — גלוטמט יונוטרופי. דלת ברזל עם מגנט-מנעול (Mg²⁺). פתיחה דורשת 3 תנאים: גלוטמט/NMDA + Glycine + דה-פולריזציה. כשפתוח → Ca²⁺ נכנס → פלסטיות, learning.",

    # ── BATCH 1C: OPIOID + CHOLINERGIC + HISTAMINE ──────────────────────────
    "Acetylcholine": "אצטיל-כולין — גפרור עץ נצמת ('אצית' עברית = להדליק). עובר בין סצנת Wonderland (mAChR) לסצנת Cigarette Theater (nAChR).",
    "mu_opioid_receptor": "קולטן μ-opioid (Gi). פרה שאומרת 'MOO' (= μ) עם דליים: אנלגזיה / אופוריה / דיכוי נשימה / עצירות / מיוזה. Morphine, Fentanyl, Heroin = חולבים מלאים. Buprenorphine = חולב חלקי.",
    "kappa_opioid_receptor": "קולטן κ-opioid (Gi). תיש בועט עם תווית 'K'. אגוניסט = דיספוריה. חסימת κ = טרגט אנטי-דיכאוני חדש (חלק ממנגנון Buprenorphine).",
    "delta_opioid_receptor": "קולטן δ-opioid (Gi). מטוס Delta Airlines מעל החווה. אנקפלינים אנדוגניים. פחות קליני.",
    "M1_receptor": "קולטן M1 — מוסקריני (Gq, CNS, זיכרון). הארנב הלבן עם שעון '1 O'Clock'. חסימה (Atropine וכו') = Mad as a Hatter (דליריום).",
    "M2_receptor": "קולטן M2 — מוסקריני (Gi, לב). מלכת לבבות עם 2 קלפי 'Two of Hearts'. חסימה (Atropine) = ברדיקרדיה מתהפכת לטכיקרדיה.",
    "M3_receptor": "קולטן M3 — מוסקריני (Gq, בלוטות + שריר חלק). שולחן ה-Mad Hatter עם 3 כוסות תה דולפות (רוק, דמעות, זיעה). חסימה = Dry as a Bone, Hot as a Hare, Blind as a Bat.",
    "M4_receptor": "קולטן M4 — מוסקריני (Gi, סטריאטום). Tweedledee בפינה. טרגט אנטיפסיכוטי מתפתח (Xanomeline/KarXT 2024).",
    "M5_receptor": "קולטן M5 — מוסקריני (Gq, VTA reward). Tweedledum. תאוריה בהתמכרות, אין תרופות.",
    "anticholinergic_toxidrome": "תסמונת אנטיכולינרגית. כל Wonderland משתגעת: Mad as a Hatter (M1, דליריום), Red as a Beet (vasodilation), Hot as a Hare (M3 אין הזעה), Dry as a Bone (M3 יבש), Blind as a Bat (M3 אישונים מורחבים). טיפול: Physostigmine.",
    "nAChR_alpha4_beta2": "nAChR α4β2 — תעלה יונוטרופית CNS. סיגריית '42' (תשובה לחיים, ליקום, ולמה). מטרת Varenicline (אגוניסט חלקי).",
    "nAChR_alpha7": "nAChR α7 — תעלה Ca²⁺-permeable, קוגניציה. סיגריית 'Lucky 7' של סבא חכם. היפו-תפקוד בסכיזופרניה (~80% מעשנים = self-medication).",
    "H1_receptor": "קולטן H1 — היסטמין (Gq). קומה 1 בית-H (סלון נמנום). חסימה (Diphenhydramine, Mirtazapine, Quetiapine, Olanzapine) = סדציה + עליה במשקל.",
    "H2_receptor": "קולטן H2 — היסטמין (Gs, פריאטל גסטרי). קומה 2 (מטבח H2O). חסימה (Famotidine) = אנטאצידים.",
    "H3_receptor": "קולטן H3 — היסטמין (Gi, אוטו-רצפטור CNS). פרופסור נרדם על השולחן בקומה 3. Pitolisant = inverse agonist → ערות → טיפול בנרקולפסיה.",
    "H4_receptor": "קולטן H4 — היסטמין (Gi, תאי חיסון). גג בית-H עם יוני חיסון. לא פסיכיאטרי.",
    "DAT": "מובל דופמין — איש דת (רב חרדי עם פאות) במכרה DOPA-MINE אוסף עפרת דופמין שפוזרה חזרה הביתה. דת = DAT (משחק עברי). חסימה (Methylphenidate, Amphetamine, Cocaine, Bupropion) = כיסוי עיניים לרב.",
    "VMAT2": "מובל ווסיקולרי 2 — מזרון יוגה 'V-MAT 2' באולם מנוחה. מונואמינים שוכבים עליו לפני שחרור. גנבים (Valbenazine, Deutetrabenazine, Tetrabenazine) = TD treatment.",
    "sigma1_receptor": "קולטן σ1 — chaperone protein ב-ER (מצב רוח, ניאו-פרוטקציה). סנטרי 'S-1'. Fluvoxamine אגוניסט ייחודי בקרב SSRI (עוצמת OCD/חרדה). גם Dextromethorphan.",

    # ── BATCH 2: PATHWAYS + NUCLEI ──────────────────────────────────────────
    "VTA": "Ventral Tegmental Area — משרד יו\"ר ב-DOPA-MINE. מקור מסולימבי + מסוקורטיקלי. היפראקטיביות = פסיכוזה; היפו = תסמינים שליליים.",
    "substantia_nigra_pars_compacta": "Substantia Nigra — פיר 'שחור' (nigra = שחור) במכרה. מקור ניגרוסטריאטלי. ניוון = פרקינסון; חסימת D2 = פרקינסוניזם תרופתי.",
    "mesolimbic_pathway": "מסלול מסולימבי — קווי רכבת VTA→ קזינו (NAc). היפראקטיביות = סימפטומים חיוביים בסכיזופרניה. גם reward של התמכרות.",
    "mesocortical_pathway": "מסלול מסוקורטיקלי — VTA → בית-קפה אינטלקטואלי (PFC). היפואקטיביות = סימפטומים שליליים. קשה לטפל.",
    "nigrostriatal_pathway": "מסלול ניגרוסטריאטלי — substantia nigra → תא גלגלי-שיניים (סטריאטום). היפו = EPS (פירמידות), פרקינסון.",
    "tuberoinfundibular_pathway": "מסלול טוברואינפונדיבולרי — היפותלמוס → היפופיזה. DA תונית-מעכב פרולקטין. חסימת D2 = הצפת פרולקטין (galactorrhea, amenorrhea).",
    "raphe_nuclei": "גרעיני raphe (תפר במרכז הגזע מוחי) — סדנת שטיחים פרסיים. מקור כל הסרוטונין במוח. SSRI/SNRI/TCA פעולה בטרמינלים. דחיית הופעת = הלום ישן.",
    "locus_coeruleus": "Locus Coeruleus — תא טלפון משטרתי כחול ('כתם כחול'). מקור כל ה-NE במוח. היפראקטיבי בפניקה, PTSD, גמילה. Clonidine/Guanfacine = מרגיעים.",
    "tuberomammillary_nucleus": "Tuberomammillary — פרות מימרי בהיפותלמוס. מקור כל ההיסטמין במוח. ערות. Pitolisant מגביר (H3 inverse agonist).",
    "basal_nucleus_meynert": "גרעין Meynert — ציפור מינה חכמה ב-basal forebrain. מקור אצטיל-כולין לקליפת המוח. ניוון באלצהיימר.",
    "dorsal_raphe": "Dorsal raphe — תחנת האריגה העליונה. עיקר ה-5-HT לקליפת המוח. 5-HT1A autoreceptors כאן = מנגנון השהיית SSRI.",
    "pedunculopontine_nucleus": "PPN — דוושה אורגנית. ערות + REM. מטרת DBS בפרקינסון לקיפאון הליכה.",

    # ── BATCH 3: BRAIN REGIONS ──────────────────────────────────────────────
    "prefrontal_cortex": "PFC — משרד מנכ\"ל מזוגג בחזית. תפקוד ביצועי, זיכרון עבודה, עיכוב. ↓DA מסוקורטיקלי = משרד ריק = סימפטומים שליליים.",
    "anterior_cingulate_cortex": "ACC — בלש שמרחרח סתירות. ניטור קונפליקטים, ויסות רגשי. היפראקטיבי ב-OCD; היפואקטיבי בדיכאון. מטרת DBS לדיכאון עמיד.",
    "orbitofrontal_cortex": "OFC — שופט מעל הארובות (orbits = ארובות עיניים). שיפוט ערך + מוסר. פגיעה = Phineas Gage (פיזור עיכוב). OCD = שופט תקוע בלולאה.",
    "dorsolateral_prefrontal_cortex": "dlPFC — ספרן בצד המשרד. זיכרון עבודה, בקרה קוגניטיבית. מטרת rTMS לדיכאון (גירוי שמאל). היפואקטיבי בסכיזופרניה.",
    "ventromedial_prefrontal_cortex": "vmPFC — כרית מדיטציה בתחתית המשרד. ויסות רגשי, הפנמה. רכזת ב-Default Mode Network. רומינציה דיכאונית.",
    "amygdala": "אמיגדלה — אזעקה בצורת שקד (amygdala = שקד ביוונית). זיהוי איום + זיכרון רגשי. היפראקטיבית ב-PTSD/חרדה. Klüver-Bucy = פגיעה דו-צדדית.",
    "hippocampus": "היפוקמפוס — סוסון ים עם מפת מסלולים (hippocampus = סוסון ים). זיכרון דקלרטיבי + ניווט מרחבי. מצטמק בלחץ כרוני. נוירוגנזה מ-SSRI/ספורט.",
    "basal_ganglia": "Basal Ganglia — מערכת גלגלי שיניים בבסיס המוח. בקרת תנועה + הרגלים. כולל: caudate, putamen (= striatum), globus pallidus, STN, substantia nigra.",
    "striatum_caudate_putamen": "סטריאטום (caudate + putamen) — סוודר פסים שלובש פועל. ה-input של ה-basal ganglia. חסימת D2 כאן = EPS. היפר-DA = טיקים, OCD.",
    "globus_pallidus": "Globus Pallidus — באוונצ'ר חיוור עגול ביציאת מערכת הגלגלי שיניים. מטרת DBS לפרקינסון.",
    "subthalamic_nucleus": "STN — מנוע קטן אך עוצמתי מתחת לתלמוס. מטרת DBS #1 לפרקינסון. היפראקטיביות = hemiballismus.",
    "thalamus": "תלמוס — תחנת רכבת מרכזית גרנדיוזית. ממסר חושי (חוץ מריח) + מוטורי. פגיעה ב-thalamocortical oscillations = סכיזופרניה.",
    "hypothalamus": "היפותלמוס — תרמוסטט מתחת לתלמוס. שינה (SCN), תיאבון (arcuate), חום, ציר HPA.",
    "insula": "אינסולה — אי קטן בין האונה הטמפורלית לפרונטלית (insula = אי בלטינית). מודעות גופנית (interoception). היפראקטיבית בפניקה.",
    "cerebellum": "צרבלום — 'מוח קטן' בעורף (cerebellum = מוח קטן בלטינית). קואורדינציה מוטורית + תזמון קוגניטיבי. רעלת ליתיום פוגעת = אטקסיה.",
    "default_mode_network": "DMN — ועד קופי-שופ של אזורי מוח (vmPFC, PCC, angular). פעיל ב-rest = חלום-בהקיץ. היפראקטיבי בדיכאון (רומינציה).",
    "nucleus_accumbens": "Nucleus Accumbens (NAc) — קזינו עם מכונות פוקר. מרכז התגמול. כל ההתמכרויות מציפות DA כאן.",

    # ── BATCH 4: SIDE EFFECT MECHANISMS ─────────────────────────────────────
    "neuroleptic_malignant_syndrome": "NMS — תסמונת ממאירה ניורולפטית. מצב חירום: היפרתרמיה + נוקשות שרירים + אי-יציבות אוטונומית + ↑CK. גורם: אנטיפסיכוטיים (במיוחד דור 1 פוטנטיים). טיפול: הפסקת תרופה + Dantrolene/Bromocriptine + טיפול תומך. DDx: serotonin syndrome (clonus vs rigidity).",
    "serotonin_syndrome": "תסמונת סרוטונרגית — clonus + היפר-רפלקסיה + agitation + autonomic + חום. גורם: SSRI+MAOI, Tramadol+SSRI, Linezolid+SSRI. Hunter criteria. טיפול: הפסקת תרופה + Cyproheptadine. DDx: NMS (clonus = serotonin, rigidity = NMS).",
    "EPS_acute_dystonia": "דיסטוניה אקוטית — עווית שרירים פתאומית (oculogyric crisis, torticollis). תוך שעות-ימים מאנטיפסיכוטיק, במיוחד בגברים צעירים. טיפול: Benztropine IM 1-2mg או Diphenhydramine IM 25-50mg.",
    "EPS_akathisia": "אקתיזיה — תחושת אי-שקט פנימית + תנועות חיצוניות. 'A-kathisia' = 'לא-יושב' ביוונית. קשור לסיכון אובדנות. טיפול: ↓מינון, Propranolol, BZD, Mirtazapine.",
    "EPS_parkinsonism": "פרקינסוניזם תרופתי — bradykinesia + נוקשות + רעד + פנים מסיכה. דמוי פרקינסון אבל הפיך. טיפול: ↓מינון, Benztropine, Amantadine.",
    "tardive_dyskinesia": "טרדיב דיסקינזיה — תנועות פה-לסת בלתי רצוניות מאוחרות. סיכון ↑ עם שנות חשיפה לדור 1. AIMS scale screening. טיפול: מעבר ל-Clozapine/Quetiapine + VMAT2 inhibitors (Valbenazine, Deutetrabenazine).",
    "hyperprolactinemia": "היפרפרולקטינמיה — חסימת D2 במסלול טוברואינפונדיבולרי → פרולקטין ↑. סימפטומים: galactorrhea, אמנוריאה, גינקומסטיה, dysfunction מיני, אוסטיאופורוזיס. גרועים: Risperidone, Paliperidone, Haloperidol. מינימליים: Aripiprazole, Quetiapine, Clozapine.",
    "metabolic_syndrome": "תסמונת מטבולית — עליה במשקל + דיסליפידמיה + T2DM. חסימת H1 + 5-HT2C משולבת. סיכון: Olanzapine > Clozapine > Quetiapine > Risperidone > ...> Lurasidone. ניטור שנתי: גלוקוז, ליפידים, מותן.",
    "QT_prolongation": "QT הארכה — סיכון לסוג של arrhythmia (torsades de pointes). גרועים: Methadone, Ziprasidone, IV Haloperidol, TCAs, Citalopram (תלוי מינון). גורמי סיכון: היפו-K, היפו-Mg, ברדיקרדיה. ניטור: EKG בסיס + מעקב.",
    "agranulocytosis_clozapine": "אגרנולוציטוזה — דלדול נויטרופילים, אופייני ל-Clozapine. סיכון 1-2% במשך שנים. ניטור ANC חובה: שבועי × 6 ח', דו-שבועי × 6 ח', חודשי לאחר מכן. גם: Carbamazepine, Mirtazapine (נדיר).",
    "SJS_TEN": "SJS / TEN — תגובת עור חמורה (גליפת עור). תרופות בסיכון גבוה: Lamotrigine (טיטרציה איטית!), Carbamazepine (HLA-B*1502 באסיינים — בדיקה לפני התחלה), Phenobarbital, Phenytoin. תמותה גבוהה. טיפול: עצירת תרופה + ICU.",
    "SIADH_hyponatremia": "SIADH תרופתי — היפונתרמיה. תרופות: Carbamazepine, Oxcarbazepine (יותר!), SSRI (במיוחד קשישים), אנטיפסיכוטיים נדיר. סימפטומים: בלבול, כאב ראש, פרכוסים. בדיקת Na תוך שבועיים מהתחלה בקשישים.",
    "hepatotoxicity": "רעלת כבד תרופתית. גבוה: Valproate (במיוחד פדיאטרי <2 שנה, מחלות מיטוכונדריאליות — black box), Nefazodone, Disulfiram, Naltrexone (במינונים גבוהים), MAOI. ניטור LFT בסיס + תקופתי.",
    "suicidality_warning": "אזהרת FDA — סיכון אובדנות פרדוקסלי בצעירים. עליה בסיכון בחודשים 1-2 של SSRI/SNRI בגיל <25. אזהרת black-box. ליווי קרוב במשפחה. הסיכון המוחלט קטן, התועלת בדרך כלל גוברת.",
    "discontinuation_syndrome_FINISH": "תסמונת הפסקת SSRI — הפסקה חדה → היפו-סרוטונין. FINISH: Flu-like, Insomnia, Nausea, Imbalance, Sensory disturbance (electric shocks/'brain zaps'), Hyperarousal. הגרוע: Paroxetine, Venlafaxine. הקל: Fluoxetine (t½ ארוך). טיפול: חזרה + טיטרציה איטית.",
    "lithium_toxicity": "רעלת ליתיום — חלון טיפולי צר. קל (1.5-2.0): רעד, GI; בינוני (2.0-2.5): אטקסיה, בלבול; קשה (>2.5): פרכוסים, dialysis נדרש. טריגרים: התייבשות, NSAID, ACE-I/ARB, thiazides, מחלת כליה. ניטור: רמה, TSH, Cr שנתיים.",
    "lithium_hypothyroid_DI": "ליתיום → היפותירואידיזם + סוכרת תפלה נפרוגנית. היפותירואידיזם: 10-20% במשך שנים → TSH q6-12mo + Levothyroxine. נפרוגנית DI (פוליאוריה): Amiloride חוסם כניסת Li לאיסוף.",
    "priapism_trazodone": "Priapism — זיקפה מתמשכת וכואבת מחסימת α1. Trazodone הקלאסי. >4 שעות = חירום אורולוגי. גם: Risperidone, Chlorpromazine. הזהר זכרים.",
    "sexual_dysfunction_SSRI": "Dysfunction מיני מ-SSRI — 30-70%. ↓libido, anorgasmia, ejaculation מאוחר. אסטרטגיות: drug holiday (לא Fluoxetine), מעבר ל-Bupropion/Mirtazapine/Vilazodone/Vortioxetine, הוספת Bupropion/Cyproheptadine/Sildenafil.",
    "antichol_toxidrome_emergency": "תסמונת אנטיכולינרגית — מצב חירום. ראה WONDERLAND_TEAPARTY לסצנה מלאה. אובר-דוז של TCA/אנטיהיסטמין/Scopolamine. טיפול: Physostigmine + תומך.",

    # ── BATCH 5: DSM CATEGORIES ─────────────────────────────────────────────
    "DSM_neurodevelopmental": "DSM פרק נוירו-התפתחותי. סמל: גן משחקים עם טבעות עץ של אבני דרך התפתחותיות. כולל: ID, GDD, תקשורת, ASD, ADHD, SLD, תנועה (טיק, dyspraxia), stereotypic.",
    "DSM_psychotic": "DSM פרק פסיכוטי. סמל: מראה סדוקה משקפת מציאות מעוותת, קולות כגלים צבעוניים. סכיזופרניה (>6ח), סכיזואפקטיב, סכיזופרניפורם (1-6ח), Brief Psychotic (<1ח), הזיות, Catatonia. חסימת D2 מרגיעה את המראה.",
    "DSM_bipolar": "DSM פרק דו-קוטבי. סמל: see-saw בין שמש (מאניה) לענן סוער (דיכאון), קרחון קוטב צפוני ברקע. ביפולר I, II, ציקלוטימיה. סוללת Li + 'lamb-trigger' מאזנים.",
    "DSM_depressive": "DSM פרק דיכאוני. סמל: ענן גשם אפור מעל דמות שמוטה, ה-SERT דוור צובר חבילות סרוטונין שלא נמסרו. MDD (≥2ש), PDD (≥2שנה), DMDD (ילדים), PMDD. SIGECAPS.",
    "DSM_anxiety": "DSM פרק חרדה. סמל: שפן רועד (מתחנת אוטובוס 1A) מוכפל. Separation Anxiety, Selective Mutism, Phobia, Social Anxiety, Panic, Agoraphobia, GAD. אוטובוס Buspirone + SSRI/SNRI.",
    "DSM_OCD_related": "DSM פרק OCD. סמל: ידיים שוטפות בלולאה, מים מסתחררים אינסופית, מונה. OCD, BDD, Hoarding, Trichotillomania, Excoriation. SSRI במינון גבוה (Fluvoxamine, Sertraline) + Clomipramine. ERP זהב.",
    "DSM_trauma_stressor": "DSM פרק טראומה. סמל: מסגרת תמונה שבורה עם זמן מתופרר, חצי flashback אחורה. PTSD, Acute Stress, Adjustment, RAD, DSED. Prazosin לסיוטים. SSRI קו ראשון. TF-CBT, EMDR.",
    "DSM_dissociative": "DSM פרק דיסוציאטיבי. סמל: מראה מקוטעת עם מספר עצמים, שעון עם הווה מחוק. DID, Depersonalization/Derealization, אמנזיה דיסוציאטיבית. פסיכותרפיה ממוקדת-טראומה.",
    "DSM_somatic": "DSM פרק סומטי. סמל: מטופל עם סימני גוף מודגשים אבל הסטטוסקופ של הרופא לא מוצא דבר. SSD, Illness Anxiety, Conversion, Factitious. CBT עיקרי.",
    "DSM_feeding_eating": "DSM פרק אכילה. סמל: צלחת ריקה ליד סרט מדידה (Anorexia) + צלחת binge (BN, BED) + רשימה מגבילה (ARFID). AN: התמותה הגבוהה ביותר. Fluoxetine ל-BN. Topiramate ל-BED. FBT לנער/ה.",
    "DSM_elimination": "DSM פרק הפרשה. אסלה עם סימן שאלה — Enuresis (ספיגה לילית), Encopresis. Enuresis: bell-and-pad, Desmopressin, Imipramine מינון נמוך. Encopresis: בירור פדיאטרי קודם.",
    "DSM_sleep_wake": "DSM פרק שינה. ירח + שעון + מטופל עייף. Insomnia, Hypersomnolence, Narcolepsy (Pitolisant), Breathing-related, Circadian, Parasomnias, RLS. CBT-I קו ראשון ל-insomnia כרוני.",
    "DSM_sexual_dysfunction": "DSM פרק תפקודים מיניים. סמל: סמל זוגיות 'מושהה'. Delayed Ejaculation, ED, Female Orgasmic, FSIAD, Genito-Pelvic Pain, Hypoactive Desire, Premature Ejaculation, Substance-induced. SSRI גורם חשוב.",
    "DSM_gender_dysphoria": "DSM פרק דיספוריה מגדרית. סמל: דמות עם שתי השתקפויות שונות במראה — מוקצה מול חוויה. טיפול תומך, gender-affirming care.",
    "DSM_disruptive_impulse": "DSM פרק impulsive. סמל: סימן פיצוץ עם חוקים שבורים. ODD (התנגדות), CD (הסלמה), IED (כעס פתאומי), Kleptomania, Pyromania, Antisocial PD. טיפול: behavioral + parent management. לפעמים Risperidone לתוקפנות חמורה.",
    "DSM_substance_addictive": "DSM פרק חומרים. סמל: בקבוק + מזרק + סיגריה + קוביות — כולם מתכנסים לקזינו DOPA-MINE. Alcohol (Naltrexone, Acamprosate, Disulfiram), Opioid (Methadone, Buprenorphine), Tobacco (Varenicline, NRT, Bupropion), Stimulants, Cannabis, Gambling.",
    "DSM_neurocognitive": "DSM פרק נוירו-קוגניטיבי — מדולג ע\"פ בקשה (גריאטרי). Delirium עדיין רלוונטי (בלבול אקוטי): hyperactive/hypoactive/mixed; חיפוש סיבה הפיכה; אנטיפסיכוטי במינון נמוך רק להתסערות חמורה.",
    "DSM_personality_disorders": "DSM פרק אישיות. קיר 10 מסכות תיאטרון — כל PD מסכה. מעל: מסגרת 'קריטריונים כלליים A-F'. Cluster A (מוזר): Paranoid, Schizoid, Schizotypal. B (דרמטי): Antisocial, Borderline, Histrionic, Narcissistic. C (חרד): Avoidant, Dependent, OCPD.",
    "DSM_paraphilic": "DSM פרק paraphilic. סמל: שלט 'CAUTION' — עניין מיני אטיפי שגורם distress או נזק. Voyeuristic, Exhibitionistic, Frotteuristic, Masochism, Sadism, Pedophilic, Fetishistic, Transvestic. השלכות פורנזיות + אתיות. CBT + לפעמים anti-androgens.",
    "DSM_medication_induced": "DSM פרק תרופתי. בית מרקחת עם בקבוקים: NMS, Serotonin Syndrome, EPS, Akathisia, TD, Lithium toxicity, Discontinuation syndrome. זהה + טפל + מנע.",
}

# Drug Hebrew explanations
DRUG_HEBREW = {
    "Lithium": "ליתיום — סוללת Li-ion ענקית עם מד-טעינה. רוחב חלון מתח צר = חלון טיפולי 0.6-1.2 mEq/L. סוללה במתח-יתר מתפוצצת = רעלת >1.5 (רעד → אטקסיה → פרכוסים → קומה). ניטור TSH + Cr שנתיים.",
    "Valproate": "Valproate — אתלט מקצוען קירח בעמק עם עוגה גדולה. נושר שיער + עליה במשקל. רחב-spectrum (מצב רוח + נוגד פרכוסים). Hepatotoxic warning + טרטוגן (טרידה - black box פדיאטרי <2).",
    "Lamotrigine": "Lamotrigine — כבש על trigger עם פריחה אדומה על הצמר. טיטרציה איטית! Carbamazepine ↓ רמות (induces); Valproate ↑ רמות (חצי המינון). מצב רוח. סיכון SJS.",
    "Carbamazepine": "Carbamazepine — מבוך לחם שמשרטט את עצמו מחדש מדי לילה. Carb + Maze. אוטו-אינדוקציה (CYP). HLA-B*1502 אסיינים = SJS. SIADH (היפונתרמיה).",
    "Diazepam": "Diazepam — מטייל בעמק ירוק רגוע ליד נחל איטי. Dia = day-az = יום בעמק רגוע. t½ ארוך + מטבוליטים פעילים. ב-EtOH withdrawal taper.",
    "Alprazolam": "Alprazolam — אלפקה קטנה רכה עם כפפת אגרוף 'high-potency'. t½ קצר, potency גבוהה. פאניקה. רבאונד inter-dose. הימנע בנער/ה.",
    "Lorazepam": "Lorazepam — סבתא ליד האח מספרת LORE. מטבוליזם conjugation בלבד = בטוח בכבד + קשישים. Catatonia challenge. IV/IM/PO.",
    "Clonazepam": "Clonazepam — שני clones זהים הולכים עם מקלות ארוכים. t½ ארוך = פחות רבאונד מ-Alprazolam.",
    "Midazolam": "Midazolam — מלך MIDAS עם שעון עצר. כל מה שנוגע הופך מהיר. IM/IV/IN פעולה מהירה. פדיאטרי intranasal לפרכוסים.",
    "Phenobarbital": "Phenobarbital — גדר תיל ברביר סביב מלון Aa. שומר על דלתות פתוחות זמן רב יותר. מינון גבוה פותח את המלון בלי GABA → קטלני.",
    "Flumazenil": "Flumazenil — מאבטח עם FLU (מצונן), זורק BZD החוצה. אנטגוניסט תחרותי. עלול לעורר פרכוסים ב-BZD כרוני.",
    "Fluoxetine": "Fluoxetine — שור פלורסנטי שזוהר במשך שבועות. FLUO+OX. t½ ארוך = self-taper. הכי נסבל בילדים.",
    "Sertraline": "Sertraline — אביר על מסילה (SIR-on-RAIL). רחב, יציב. SSRI ראשון ל-PTSD.",
    "Paroxetine": "Paroxetine — שור בצניחת מצנח (PARA-chute). t½ קצר + אנטיכולינרגי חזק = discontinuation syndrome חמור. הכי מרדים. טרטוגן.",
    "Bupropion": "Bupropion — בומפר-קר באולם — מכוניות מתנגשות באנרגיה. NDRI מעורר. בלי dysfunction מיני, בלי סדציה. ↓סף פרכוסים. הימנע באכילה.",
    "Mirtazapine": "Mirtazapine — אדם עם MIRror, עוגה ועץ אורן. ראי = self-reflection (סדציה). עוגה = תיאבון (5-HT2C + H1). 5-HT3 antiemetic. קשישים עם תיאבון נמוך + נדודי שינה.",
    "Vortioxetine": "Vortioxetine — צב חכם עם משקפיים. רב-מודאלי: SERT + 5-HT1A agonist + 5-HT3/7 antagonist + 5-HT6 antagonist (procognitive). איטי + חכם.",
    "Methylphenidate": "Methylphenidate — שורת ילדים מרוכזים עם כובעי CH3 ומשקפי phenyl. Methyl + Phenyl + שורת focus. חוסם DAT/NET. מפעיל את כל DOPA-MINE. במינון יתר → פירמידות EPS.",
    "Amphetamine": "Amphetamine — מגבר גיטרה מוגבר ל-11 על הבמה. AMP = amplifier. חוסם DAT/NET + RELEASER. חזק יותר מ-Methylphenidate. תלות גבוהה.",
    "Atomoxetine": "Atomoxetine — שור-אטומי קטנטן רוכב על רשת NET וקורע בה חורים. ATOM+OX. NRI טהור (לא DAT). אין פוטנציאל התמכרות. אזהרת אובדנות.",
    "Clonidine": "Clonidine — שני מטפלים זהים (clones) מאכילים את הזאב #2. α2 agonist = ↓NE מרכזי. ADHD, טיקים, גמילת אופיואידים, יל\"ד. רבאונד hypertension בהפסקה חדה.",
    "Guanfacine": "Guanfacine — שומר עם פני אבן עומד מעל העדר. Guan-face = Guard-face. α2A-סלקטיבי = יותר PFC-ספציפי (פחות סדציה מ-Clonidine).",
    "Propranolol": "Propranolol — נגן מקצועי על הבמה — ידיים יציבות, בלי רעד. PRO+performer = חרדת ביצוע. β-block לא-סלקטיבי = משתיק תופי לב (β1) וגם נשיפה (β2 — ברונכוספסם!). אקתיזיה. PTSD prevention.",
    "Prazosin": "Prazosin — חייל מקבל praise על שמירת לילה ללא סיוטים. α1 antagonist. דארט מרגיע לזאב #1 = orthostasis (מנת-ראשון). PTSD nightmares. BPH.",
    "Haloperidol": "Haloperidol — מלאך עם הילה (HALO) מחזיק בובה קשיחה (EPS!). דור 1 פוטנטיק = חסימה אגרסיבית של D2 → המון פירמידות. Acute agitation IM. LAI זמין.",
    "Clozapine": "Clozapine — מפתח-Z מאסטר פותח את 4 הברגים של D4. סכיזופרניה עמידה + ↓אובדנות. אגרנולוציטוזה = WBC מתים → ניטור ANC חובה. פרכוסים + myocarditis + drooling + עליה במשקל + מטבולי.",
    "Aripiprazole": "Aripiprazole — פיצה עם רק חצי פרוסה חתוכה (A-RIP). Partial agonist של D2 + 5-HT1A; antagonist של 5-HT2A. פחות מטבולי. אקתיזיה שכיחה. LAI זמין.",
    "Cariprazine": "Cariprazine — דמות נושאת גביע 'Praise' אל הלב-לימבי. Carry+Praise. D3>D2 partial agonist. סכיזופרניה + דיכאון בביפולר + מצבים מעורבים. אקתיזיה.",
    "Lurasidone": "Lurasidone — דייג עם פיתיון בצד הסירה — לקחת עם ארוחה של 350+ kcal. Lure + side. דיכאון בביפולר + סכיזופרניה. נטל מטבולי נמוך. 5-HT7 antagonist (מכבה 7-Eleven בלילה).",
    "Quetiapine": "Quetiapine — אורן שקט (QUIET-pine) שכולם נמנמים תחתיו. הכי מרדים בדור 2 (H1 + α1 block). off-label לאינסומניה (מינון נמוך). עליה במשקל. מטבולי. דיכאון בביפולר.",
    "Olanzapine": "Olanzapine — אתלט אולימפי ישן תחת אורן, סביבו זיתים שמנים. OL+LAN+Z+PINE. הנטל המטבולי המקסימלי (משקל, דיסליפידמיה, T2DM). סדציה חזקה. Acute agitation IM (לא משולב עם BZD!). LAI = post-injection syndrome.",
    "Ketamine": "Ketamine — דארט וטרינרי 'horse tranquilizer' תקוע בדלת NMDA. Open-channel NMDA antagonist. אנטי-דיכאוני מהיר (שעות), TRD, אובדנות חריפה. דיסוסיאציה, BP↑, K-hole. IV/IM/IN.",
    "Esketamine": "Esketamine — אותו דארט-סוס עם תווית 'S' על המצח. S-enantiomer של ketamine. Intranasal, on-label ל-TRD. REMS program נדרש.",
    "Memantine": "Memantine — סבתא זקנה (Mama-tine) עם מקל — חוסמת רק את האורחים הכי גסים. Use-dependent low-affinity NMDA antagonist. ילדים off-label: תוקפנות באוטיזם, OCD.",
    "Naloxone": "Naloxone — מסמר נוקאאוט-OX (NAIL+OX+KO). אנטגוניסט μ. אינטרא-נזלי/IM/IV ל-overdose. t½ קצר — ייתכן צורך במנה חוזרת. מעורר withdrawal.",
    "Methadone": "Methadone — Don של מאפיה ב-clinic מבוקרת, מחלק חלב לפרות שלו. אגוניסט μ ארוך-טווח. פעם ביום ל-OUD maintenance. QTc.",
    "Buprenorphine": "Buprenorphine — בומפר שרק חצי מתנגש בדלת. אגוניסט μ חלקי + κ-antagonist. Ceiling effect לדיכוי נשימה. OUD maintenance. עם Naloxone (Suboxone).",
    "Disulfiram": "Disulfiram — תיש סולפר ('disulf+RAM') שמדיף ביצים מקולקלות. ALDH inhibitor. שתיית EtOH = הצטברות אצטאלדהיד = בחילה חמורה. CYP2E1/3A4 inhibitor. Hepatotoxic.",
    "Varenicline": "Varenicline — מעשן מחזיק חצי-סיגריה — לא מקבל hit מלא. α4β2 nAChR partial agonist. ↓craving + ↓nicotine reward. נצמד לסיגריית '42' ב-CIGARETTE_THEATER.",
    "Morphine": "Morphine — פסל יווני שמשנה צורה לפנים רגועות (MORPH+fine). אגוניסט μ זהב סטנדרט. אנלגזיה + אופוריה + דיכוי נשימה + עצירות + מיוזה.",
    "Fentanyl": "Fentanyl — פנטהאוז בקומה העליונה (Fent → Penthouse). פוטנטיות 100x מורפין. אנסטזיה, מדבקות לכאב כרוני. הוגנב לצח רחוב = מגיפת אובר-דוז.",
    "Oxycodone": "Oxycodone — מטופל במסכת חמצן + בייפר 'CODE-RED' (קוד נשימתי). Oxy+Code = oxygen-code. אגוניסט μ סינתטי-חצי. פוטנציאל התמכרות גבוה.",
    "Codeine": "Codeine — מגילה מקודדת מתפענחת. Prodrug → morphine דרך CYP2D6. נוגד שיעול + אנלגזיה מתונה. CYP2D6 Ultra-Rapid = רמות מורפין גבוהות = מוות פדיאטרי (FDA contraindicated <12).",
    "Tramadol": "Tramadol — חשמלית-tram מובילה בובה קטנה. אגוניסט μ חלש + SNRI (סיכון serotonin syndrome!). ↓סף פרכוסים. Prodrug דרך CYP2D6.",
    "Heroin": "Heroin — תחפושת hero מזויפת עם מזרק תקוע בקייפ (HERO-IN). דיאצטילמורפין. ליפופילי, חוצה BBB מהר → rush. מקור עיקרי למוות מ-OD.",
    "Atropine": "Atropine — תייר טרופי עם מכנסיים קצרים, כובע שמש, פיניה קולדה. A-tropine = tropic vacation. אנטגוניסט מוסקריני לא-סלקטיבי. Wonderland יוצא משליטה. שימושים: ברדיקרדיה, OP poisoning, מידריאזה.",
    "Scopolamine": "Scopolamine — פיראט עם טלסקופ (Scope). מדבקה למחלת תנועה + אמנזיה (truth serum היסטורי). Anti-emetic פוסט-ניתוחי.",
    "Benztropine": "Benztropine — מטופל עם גב כפוף ויד פרוקה (Bent+Sprain) — קובל אחרי אנטיכולינרגי. M1+H1 antagonist. קו ראשון לדיסטוניה אקוטית IM 1-2mg. פרקינסוניזם תרופתי.",
    "Trihexyphenidyl": "Trihexyphenidyl — אליל מסתובב משולש+משושה+phenyl ring. Tri+Hex+Phenyl+Idyl. אנטיכולינרגי PO לפרקינסוניזם תרופתי.",
    "Diphenhydramine": "Diphenhydramine — היידרה רב-ראשית שנושכת גם ב-Wonderland (M1) וגם בבית-H קומה 1 (H1). OTC sleep aid + anti-allergy. IM 25-50 מ\"ג לדיסטוניה אקוטית. אנטיכולינרגי בקשישים → cognitive impairment.",
    "Hydroxyzine": "Hydroxyzine — אורח ישנוני בקומה 1 של בית-H עם בועת 'Zzz'. H1 antagonist anxiolytic. חרדת ילדים, פרה-מד. NON-ADDICTIVE (אין פעולה ב-GABA).",
    "Pitolisant": "Pitolisant — שעון מעורר שצועק בקומה 3 (חדר עבודה) — מעיר את הפרופסור H3. Inverse agonist. אישור ל-narcolepsy + EDS. אלטרנטיבה לא-ממריצה ל-modafinil.",
    "Dextromethorphan": "Dextromethorphan — בקבוק סירופ שיעול עתיק עם תווית 'D' — נשתה רקריאציונלית. NMDA antagonist + σ1 agonist + נוגד שיעול. 'Robotripping' במינון גבוה. עם quinidine = pseudobulbar affect.",
    "Valbenazine": "Valbenazine — גנב שגונב את מזרון V-MAT — שוד פעם ביום. VMAT2 inhibitor. אישור ל-TD. פעם ביום.",
    "Deutetrabenazine": "Deutetrabenazine — אותו גנב V-MAT אבל בשריון דאוטריום (כפל-מימן = פינוי איטי יותר). t½ ארוך יותר מ-tetrabenazine. BID. TD + Huntington + Tourette.",
    "Tetrabenazine": "Tetrabenazine — גנב V-MAT הישן — עובד, אבל קורבנותיו הופכים מדוכאים. VMAT2 inhibitor הראשון. Huntington chorea. Black box: דיכאון/אובדנות. CYP2D6-dosed.",
    "Fluvoxamine": "Fluvoxamine — זמרת עם FLU-voice (קול מצונן) בעמדת ה-S-1, שירים אובססיביים. SSRI + σ1 agonist חזק (ייחודי בקרב SSRI — מסביר את עוצמתה ב-OCD/חרדה). קו ראשון פדיאטרי ב-OCD. מעכב CYP1A2 → ↑clozapine, theophylline, caffeine.",
}


def main():
    data = json.loads(SYM.read_text(encoding="utf-8"))

    added = 0
    skipped = 0

    # Scenes
    for k, v in data.get("scenes", {}).items():
        if k in HEBREW:
            v["hebrew_explanation"] = HEBREW[k]
            added += 1
        else:
            skipped += 1

    # Ions
    for k, v in data.get("ions", {}).items():
        if k in HEBREW:
            v["hebrew_explanation"] = HEBREW[k]
            added += 1
        else:
            skipped += 1

    # Symbols
    for k, v in data.get("symbols", {}).items():
        if not isinstance(v, dict):
            continue  # batch separator strings
        if k in HEBREW:
            v["hebrew_explanation"] = HEBREW[k]
            added += 1
        else:
            skipped += 1

    # Drugs
    for k, v in data.get("drugs", {}).items():
        if not isinstance(v, dict):
            continue
        if k in DRUG_HEBREW:
            v["hebrew_explanation"] = DRUG_HEBREW[k]
            added += 1
        else:
            skipped += 1

    SYM.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Added hebrew_explanation to {added} entries")
    print(f"Skipped {skipped} (no Hebrew text in dict yet)")


if __name__ == "__main__":
    main()
