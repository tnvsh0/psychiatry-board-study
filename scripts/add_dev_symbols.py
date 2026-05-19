#!/usr/bin/env python3
"""Add developmental-psychology symbols (Batch 6) to symbols.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYM = ROOT / "schedule" / "symbols.json"

NEW_SYMBOLS = {
    "_BATCH_6_developmental_foundations": "═══════════════════════════════════════════",
    "developmental_ladder": {
        "category": "Generic developmental milestones",
        "scene": "(standalone)",
        "symbol": "A vertical LADDER with 5 rungs, each labeled with a milestone domain (motor, language, cognitive, social-emotional, adaptive)",
        "mnemonic_link": "Ladder = climbing developmental stages, one rung per domain.",
        "function_depiction": "Missing rungs = delay. Skipping = uneven development. Falling off = regression.",
        "hebrew_explanation": "סולם התפתחותי — סולם אנכי עם 5 שלבי-אבני-דרך (מוטוריקה, שפה, קוגניציה, חברתי-רגשי, אדפטיבי). חסרון שלב = עיכוב התפתחותי. רגרסיה = נפילה משלב."
    },
    "erikson_8_stages": {
        "category": "Erik Erikson — 8 psychosocial crises across lifespan",
        "scene": "(standalone)",
        "symbol": "A grand STAIRCASE with 8 steps, each labeled with the crisis pair. Different aged figures climb each step.",
        "mnemonic_link": "8 stages of life = 8 steps. Each step = a crisis to resolve.",
        "function_depiction": "Successful resolution = step glows; failure = step cracked. Crises 1-5 are pediatric: Trust (0-1y), Autonomy (1-3y), Initiative (3-6y), Industry (6-12y), Identity (12-18y).",
        "hebrew_explanation": "8 שלבי Erikson — מדרגות, כל שלב = משבר פסיכוסוציאלי. 1-5 רלוונטים לפדיאטריה: Trust-vs-Mistrust (0-1), Autonomy (1-3), Initiative (3-6), Industry (6-12), Identity (12-18). פתרון מוצלח = המדרגה זוהרת."
    },
    "piaget_4_stages": {
        "category": "Jean Piaget — 4 stages of cognitive development",
        "scene": "(standalone)",
        "symbol": "Four children of increasing age side-by-side, each holding the object that defines their stage: sensorimotor baby with peekaboo toy, preoperational toddler with conservation cup, concrete-operational child with abacus, formal-operational teen with algebra book",
        "mnemonic_link": "Each child = one stage. Object in hand = the cognitive ability achieved.",
        "function_depiction": "Sensorimotor (0-2y) = object permanence. Preoperational (2-7y) = symbolic play, egocentrism. Concrete (7-11y) = conservation, reversibility. Formal (11+) = abstract reasoning.",
        "hebrew_explanation": "4 שלבי Piaget — 4 ילדים בגילים שונים. Sensorimotor (0-2, object permanence), Preoperational (2-7, sym play + אגוצנטריות), Concrete operational (7-11, שימור), Formal operational (11+, חשיבה מופשטת)."
    },
    "attachment_4_styles": {
        "category": "Mary Ainsworth — 4 attachment styles (Strange Situation)",
        "scene": "(standalone)",
        "symbol": "Four reunion scenes side-by-side. Secure: child runs to parent, calms. Anxious-ambivalent: child clings + resists. Avoidant: child ignores parent. Disorganized: child shows contradictory behavior, freezes",
        "mnemonic_link": "Reunion behaviors define the 4 styles.",
        "function_depiction": "Secure (~60%) = best outcome. Insecure styles raise risk for psychopathology. Disorganized = strongest predictor of borderline traits.",
        "hebrew_explanation": "4 סגנונות התקשרות — 4 סצנות איחוד הורה-ילד. Secure (~60%): רץ, נרגע. Anxious-ambivalent: דבק + מתנגד. Avoidant: מתעלם. Disorganized: סותר/קופא. Disorganized = predictor חזק של BPD."
    },
    "thomas_chess_temperament": {
        "category": "Thomas & Chess — 3 temperament types",
        "scene": "(standalone)",
        "symbol": "Three babies in cribs: 'Easy' baby smiling and regular; 'Difficult' baby crying with irregular patterns; 'Slow-to-warm-up' baby curious-cautious",
        "mnemonic_link": "Three cribs = three temperaments. 9 dimensions cluster into these.",
        "function_depiction": "Easy ~40% — regular, positive. Difficult ~10% — irregular, intense. Slow-to-warm ~15% — withdraws then adapts. Goodness-of-fit matters more than the type.",
        "hebrew_explanation": "3 סוגי טמפרמנט (Thomas & Chess). Easy ~40% (סדיר, חיובי). Difficult ~10% (לא סדיר, אינטנסיבי). Slow-to-warm-up ~15% (נסוג ואז מתאקלם). Goodness-of-fit הורה-ילד > הסוג עצמו."
    },
    "brazelton_still_face": {
        "category": "Still-Face paradigm (Tronick) — early mother-infant interaction",
        "scene": "(standalone)",
        "symbol": "Mother and 4-month-old baby face-to-face. Three frames: (1) playful engagement, (2) mother freezes neutral 'still face', (3) baby protests, looks away, distress",
        "mnemonic_link": "The still face = withdrawal of contingent response, infant distress.",
        "function_depiction": "Demonstrates: infants require contingent affective response. Foundation for attunement, repair, emotional regulation roots.",
        "hebrew_explanation": "Still-Face paradigm (Tronick) — אם-תינוק 4 חודשים מול-מול. 3 פריימים: משחק → אם 'קופאת' → התינוק מתעצב. מדגים: תינוקות זקוקים לתגובה רגשית מותאמת. בסיס ל-attunement + repair."
    },
    "winnicott_holding": {
        "category": "Winnicott — holding + transitional object + good-enough mother",
        "scene": "(standalone)",
        "symbol": "Mother holding infant in her arms with a TEDDY BEAR nearby. A glowing AURA around them = 'holding environment'. Label: 'good-enough mother'.",
        "mnemonic_link": "Teddy = transitional object. Aura = holding. Imperfect mother = good-enough.",
        "function_depiction": "Transitional object bridges symbiosis-to-separateness. Good-enough = optimally frustrating, necessary for developing self.",
        "hebrew_explanation": "Winnicott — אם מחזיקה תינוק, דובון לידם, הילה זוהרת = 'holding environment'. הדובון = אובייקט מעבר. 'good-enough mother' = לא מושלם, מתסכל-באופן-אופטימלי."
    },
    "klein_positions": {
        "category": "Melanie Klein — paranoid-schizoid + depressive positions",
        "scene": "(standalone)",
        "symbol": "Two paintings side-by-side. Left: a baby with a SPLIT good-vs-bad breast (paranoid-schizoid, ~0-6 months). Right: an older baby reconciling whole-object ambivalence (depressive position, 6+ months).",
        "mnemonic_link": "Split-world (PS) to whole-world (D).",
        "function_depiction": "Failure to integrate = persistence of splitting, BPD-like dynamics.",
        "hebrew_explanation": "Klein — שתי ציורים. שמאל: שד split (good/bad) = paranoid-schizoid (~0-6 חודשים). ימין: אובייקט שלם עם ambivalence = depressive (6+ חודשים). כישלון אינטגרציה = splitting BPD."
    },
    "anna_freud_defenses": {
        "category": "Anna Freud — defense mechanisms hierarchy",
        "scene": "(standalone)",
        "symbol": "A pyramid with 3 levels: PRIMITIVE (denial, projection, splitting), NEUROTIC (repression, displacement, intellectualization), MATURE (sublimation, humor, altruism)",
        "mnemonic_link": "Pyramid = hierarchy of defense maturity.",
        "function_depiction": "BPD = primitive; OCPD = neurotic-intellectualization; healthy = mature.",
        "hebrew_explanation": "Anna Freud — פירמידת מנגנוני הגנה. PRIMITIVE (denial, projection, splitting — BPD), NEUROTIC (repression, displacement, intellectualization — OCPD), MATURE (sublimation, humor, altruism)."
    },
    "kohut_self_psychology": {
        "category": "Heinz Kohut — Self Psychology",
        "scene": "(standalone)",
        "symbol": "Three child-parent dyads: (1) Mirror — parent reflects grandiosity; (2) Idealizing — child looks up to powerful parent; (3) Twinship — child and like-other engage as same-kind",
        "mnemonic_link": "Three selfobject needs.",
        "function_depiction": "Empathic failure to narcissistic vulnerability. Therapy = reparative selfobject experience.",
        "hebrew_explanation": "Kohut — 3 דיאדות. Mirroring, Idealizing, Twinship. כישלון אמפתיה → פגיעות נרציסטית. טיפול = חוויה selfobject מתקנת."
    },
    "fonagy_mentalization": {
        "category": "Fonagy — mentalization / reflective function",
        "scene": "(standalone)",
        "symbol": "Two thought bubbles linked between a parent and child — parent imagines child's mental state, child learns to imagine others' minds",
        "mnemonic_link": "Mind in mind. Linked bubbles = reflective bridge.",
        "function_depiction": "Pre-mentalistic modes: psychic-equivalence, pretend-mode. BPD = fragmented. MBT builds the capacity.",
        "hebrew_explanation": "Fonagy mentalization — בועות מחשבה מקושרות בין הורה לילד. Pre-mentalistic: psychic-equivalence, pretend-mode. BPD = mentalization מקוטעת. MBT = בניית היכולת."
    },
    "developmental_red_flags": {
        "category": "Cross-cutting developmental red flags",
        "scene": "(standalone)",
        "symbol": "A traffic-light style RED FLAG poster: each age has missed milestones (e.g., 9mo no babbling, 12mo no joint attention, 18mo no words, 24mo no 2-word phrases, 36mo no symbolic play, regression at any age)",
        "mnemonic_link": "Red flags = emergencies, never wait-and-see.",
        "function_depiction": "Loss of skills = always concerning (Rett, autism, neurodegenerative). No joint attention at 12mo = early ASD marker.",
        "hebrew_explanation": "דגלים אדומים התפתחותיים — לכל גיל מילסטונים שהחמיצו. 9 חוד: אין מלמול. 12 חוד: אין joint attention. 18 חוד: אין מילים. 24 חוד: אין משפטי 2 מילים. 36 חוד: אין משחק סמלי. רגרסיה בכל גיל = מטריד."
    }
}


def main():
    d = json.loads(SYM.read_text(encoding="utf-8"))
    before = len(d["symbols"])
    d["symbols"].update(NEW_SYMBOLS)
    d["version"] = 5
    SYM.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    after = len(d["symbols"])
    print(f"Symbols: {before} -> {after}  (added {after - before})")


if __name__ == "__main__":
    main()
