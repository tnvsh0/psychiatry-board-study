#!/usr/bin/env python3
"""
Augment schedule/diagnoses.json with items the auto-extraction missed.

User review (2026-05-10) flagged these gaps:
- Catatonia (DSM-5-TR specifier, but clinically critical — has its own
  Catatonia Disorder Due to Another Medical Condition + Unspecified Catatonia)
- Sexual Dysfunctions (whole DSM chapter)
- Paraphilic Disorders (whole DSM chapter)
- Substance Use: caffeine, hallucinogen, sedative/hypnotic/anxiolytic
- Histrionic Personality Disorder
- General Personality Disorder criteria
- Medication-Induced Movement Disorders & Emergency conditions:
  NMS, Acute Dystonia, Akathisia, Tardive Dyskinesia, Parkinsonism,
  Serotonin Syndrome, Lithium Toxicity, Discontinuation Syndromes
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
DIAG_FILE = ROOT / "schedule" / "diagnoses.json"

ADDITIONS = [
    # ── Catatonia ────────────────────────────────────────────────────────────
    {
        "name_en": "Catatonia",
        "name_he": "קטטוניה",
        "dsm_category": "Schizophrenia Spectrum and Other Psychotic Disorders",
        "icd_code": "F06.1 / F44.89 (specifier)",
        "dsm_page": 135,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": [
            "Catatonia Associated with Another Mental Disorder (specifier)",
            "Catatonic Disorder Due to Another Medical Condition",
            "Unspecified Catatonia",
        ],
        "comorbidities_common": ["Schizophrenia", "Bipolar disorder", "MDD with psychotic features", "ASD"],
        "complexity": "high",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Critical clinical diagnosis. Bush-Francis Catatonia Rating Scale; lorazepam challenge; ECT.",
    },

    # ── Sexual Dysfunctions ──────────────────────────────────────────────────
    {
        "name_en": "Sexual Dysfunctions (overview)",
        "name_he": "תפקודים מיניים (סקירה)",
        "dsm_category": "Sexual Dysfunctions",
        "icd_code": "F52.x",
        "dsm_page": 477,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": [
            "Delayed Ejaculation",
            "Erectile Disorder",
            "Female Orgasmic Disorder",
            "Female Sexual Interest/Arousal Disorder",
            "Genito-Pelvic Pain/Penetration Disorder",
            "Male Hypoactive Sexual Desire Disorder",
            "Premature (Early) Ejaculation",
            "Substance/Medication-Induced Sexual Dysfunction",
            "Other Specified / Unspecified Sexual Dysfunction",
        ],
        "comorbidities_common": ["Depression", "Relationship distress", "SSRIs side effects"],
        "complexity": "medium",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Less child-focused but exam-relevant; SSRI-induced is major topic.",
    },

    # ── Paraphilic Disorders ─────────────────────────────────────────────────
    {
        "name_en": "Paraphilic Disorders",
        "name_he": "הפרעות פרפיליות",
        "dsm_category": "Paraphilic Disorders",
        "icd_code": "F65.x",
        "dsm_page": 779,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": [
            "Voyeuristic Disorder",
            "Exhibitionistic Disorder",
            "Frotteuristic Disorder",
            "Sexual Masochism Disorder",
            "Sexual Sadism Disorder",
            "Pedophilic Disorder",
            "Fetishistic Disorder",
            "Transvestic Disorder",
            "Other Specified / Unspecified Paraphilic Disorder",
        ],
        "comorbidities_common": ["Other paraphilias", "Mood disorders", "Substance use"],
        "complexity": "medium",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Forensic + ethical implications. Pedophilic Disorder relevant to child psychiatry consultations.",
    },

    # ── Substance Use Completions ────────────────────────────────────────────
    {
        "name_en": "Caffeine-Related Disorders",
        "name_he": "הפרעות הקשורות לקפאין",
        "dsm_category": "Substance-Related and Addictive Disorders",
        "icd_code": "F15.x",
        "dsm_page": 564,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": ["Caffeine Intoxication", "Caffeine Withdrawal", "Caffeine-Induced Anxiety/Sleep Disorder"],
        "comorbidities_common": ["Anxiety disorders", "Insomnia"],
        "complexity": "low",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Note: NO Caffeine Use Disorder per DSM-5-TR (only intoxication/withdrawal).",
    },
    {
        "name_en": "Hallucinogen-Related Disorders",
        "name_he": "הפרעות הקשורות להלוצינוגנים",
        "dsm_category": "Substance-Related and Addictive Disorders",
        "icd_code": "F16.x",
        "dsm_page": 595,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": [
            "Phencyclidine Use Disorder",
            "Other Hallucinogen Use Disorder (LSD, psilocybin, peyote, etc.)",
            "Hallucinogen Persisting Perception Disorder (HPPD)",
            "Hallucinogen Intoxication / Withdrawal",
        ],
        "comorbidities_common": ["Other substance use", "Psychotic spectrum (HPPD)"],
        "complexity": "medium",
        "_source": "user-supplement-2026-05-10",
        "_notes": "HPPD = visual flashbacks long after use. Ketamine relevance for adolescent psychiatry.",
    },
    {
        "name_en": "Sedative-, Hypnotic-, or Anxiolytic-Related Disorders",
        "name_he": "הפרעות הקשורות לסדאטיביים, היפנוטיים ואנקסיוליטיים",
        "dsm_category": "Substance-Related and Addictive Disorders",
        "icd_code": "F13.x",
        "dsm_page": 644,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": [
            "Sedative/Hypnotic/Anxiolytic Use Disorder",
            "Intoxication",
            "Withdrawal (life-threatening — seizures, DTs)",
        ],
        "comorbidities_common": ["Anxiety disorders", "Insomnia", "Other substance use"],
        "complexity": "high",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Benzodiazepines + Z-drugs + barbiturates. Withdrawal can be fatal — always taper.",
    },

    # ── Histrionic PD ────────────────────────────────────────────────────────
    {
        "name_en": "Histrionic Personality Disorder",
        "name_he": "הפרעת אישיות היסטריונית",
        "dsm_category": "Personality Disorders",
        "icd_code": "F60.4",
        "dsm_page": 757,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": [],
        "comorbidities_common": ["Other PDs (especially Borderline, Narcissistic)", "MDD", "Somatic symptom disorder"],
        "complexity": "medium",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Cluster B — pattern of excessive emotionality + attention-seeking.",
    },

    # ── General Personality Disorder Criteria ────────────────────────────────
    {
        "name_en": "General Personality Disorder (master criteria)",
        "name_he": "קריטריונים כלליים להפרעת אישיות",
        "dsm_category": "Personality Disorders",
        "icd_code": None,
        "dsm_page": 734,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": [
            "A: Enduring pattern (cognition / affect / interpersonal / impulse control)",
            "B: Inflexible and pervasive across personal/social situations",
            "C: Clinically significant distress or impairment",
            "D: Stable, of long duration, onset traceable to adolescence/early adulthood",
            "E: Not better explained by another mental disorder",
            "F: Not due to substance / medical condition",
        ],
        "comorbidities_common": [],
        "complexity": "low",
        "_source": "user-supplement-2026-05-10",
        "_notes": "FOUNDATIONAL — must precede individual PD study days. Also: Cluster A/B/C overview.",
    },

    # ── Medication-Induced Movement Disorders & Emergencies ──────────────────
    {
        "name_en": "Neuroleptic Malignant Syndrome (NMS)",
        "name_he": "תסמונת ממאירה ניורולפטית",
        "dsm_category": "Medication-Induced Movement Disorders and Other Adverse Effects of Medication",
        "icd_code": "G21.0",
        "dsm_page": 803,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": [],
        "comorbidities_common": ["Antipsychotic exposure (any, but high-potency 1st-gen worse)"],
        "complexity": "very_high",
        "_source": "user-supplement-2026-05-10",
        "_notes": "EMERGENCY: hyperthermia + rigidity + autonomic instability + ↑CK. Tx: stop antipsychotic, supportive, dantrolene/bromocriptine. DDx: serotonin syndrome.",
    },
    {
        "name_en": "Serotonin Syndrome",
        "name_he": "תסמונת סרוטונרגית",
        "dsm_category": "Medication-Induced Movement Disorders and Other Adverse Effects of Medication",
        "icd_code": "T43.225A",
        "dsm_page": None,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": [],
        "comorbidities_common": ["SSRI/SNRI/MAOI exposure", "Tramadol", "Lithium combos"],
        "complexity": "very_high",
        "_source": "user-supplement-2026-05-10",
        "_notes": "EMERGENCY: clonus + hyperreflexia + agitation + autonomic + hyperthermia. Hunter criteria. Tx: stop offending agent, supportive, cyproheptadine. DDx: NMS (rigidity vs clonus).",
    },
    {
        "name_en": "Acute Dystonia",
        "name_he": "דיסטוניה אקוטית",
        "dsm_category": "Medication-Induced Movement Disorders and Other Adverse Effects of Medication",
        "icd_code": "G24.02",
        "dsm_page": 805,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": ["Oculogyric crisis", "Torticollis", "Laryngeal dystonia", "Opisthotonos"],
        "comorbidities_common": ["1st-gen antipsychotic (esp. haloperidol) within hours-days"],
        "complexity": "high",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Tx: anticholinergic IM (benztropine, diphenhydramine). Risk highest in young males.",
    },
    {
        "name_en": "Akathisia (medication-induced)",
        "name_he": "אקתיזיה (תרופתית)",
        "dsm_category": "Medication-Induced Movement Disorders and Other Adverse Effects of Medication",
        "icd_code": "G25.71",
        "dsm_page": 808,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": ["Acute", "Tardive", "Chronic"],
        "comorbidities_common": ["Antipsychotics", "SSRIs (less common)"],
        "complexity": "high",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Subjective restlessness + objective movements. Linked to suicidality. Tx: ↓dose, β-blocker (propranolol), benzo, mirtazapine, switch to lower-EPS antipsychotic.",
    },
    {
        "name_en": "Tardive Dyskinesia",
        "name_he": "טרדיב דיסקינזיה",
        "dsm_category": "Medication-Induced Movement Disorders and Other Adverse Effects of Medication",
        "icd_code": "G24.01",
        "dsm_page": 809,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": ["Orofacial", "Limb", "Truncal"],
        "comorbidities_common": ["Long-term antipsychotic exposure (esp. 1st-gen)"],
        "complexity": "high",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Often irreversible. AIMS scale screening. Tx: VMAT-2 inhibitors (valbenazine, deutetrabenazine), switch to clozapine/quetiapine.",
    },
    {
        "name_en": "Medication-Induced Parkinsonism",
        "name_he": "פרקינסוניזם תרופתי",
        "dsm_category": "Medication-Induced Movement Disorders and Other Adverse Effects of Medication",
        "icd_code": "G21.11",
        "dsm_page": 803,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": ["Bradykinesia", "Rigidity", "Tremor", "Mask-like facies"],
        "comorbidities_common": ["Antipsychotics", "Metoclopramide"],
        "complexity": "medium",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Tx: ↓antipsychotic, anticholinergic (benztropine), amantadine.",
    },
    {
        "name_en": "Lithium Toxicity",
        "name_he": "רעלת ליתיום",
        "dsm_category": "Medication-Induced Movement Disorders and Other Adverse Effects of Medication",
        "icd_code": "T43.595A",
        "dsm_page": None,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": ["Mild (1.5–2.0)", "Moderate (2.0–2.5)", "Severe (>2.5)"],
        "comorbidities_common": ["Volume depletion", "NSAIDs/ACE-I/diuretics", "Renal disease"],
        "complexity": "high",
        "_source": "user-supplement-2026-05-10",
        "_notes": "EMERGENCY at >2.5: tremor → ataxia → confusion → seizures → death. Tx: hydration, hemodialysis if severe.",
    },
    {
        "name_en": "Antidepressant Discontinuation Syndrome",
        "name_he": "תסמונת הפסקת SSRI/SNRI",
        "dsm_category": "Medication-Induced Movement Disorders and Other Adverse Effects of Medication",
        "icd_code": "T43.205A",
        "dsm_page": 813,
        "dulcan_chapter": None,
        "dulcan_page": None,
        "key_subtypes": ["FINISH: Flu-like, Insomnia, Nausea, Imbalance, Sensory disturbances, Hyperarousal"],
        "comorbidities_common": ["Abrupt SSRI/SNRI cessation (esp. paroxetine, venlafaxine)"],
        "complexity": "medium",
        "_source": "user-supplement-2026-05-10",
        "_notes": "Differentiate from relapse. Tx: re-start + slower taper. Fluoxetine has lowest risk (long t½).",
    },
]


def main():
    data = json.loads(DIAG_FILE.read_text(encoding="utf-8"))
    existing_names = {d["name_en"].lower() for d in data["diagnoses"]}

    added = 0
    for entry in ADDITIONS:
        if entry["name_en"].lower() in existing_names:
            print(f"  SKIP (already present): {entry['name_en']}")
            continue
        data["diagnoses"].append(entry)
        added += 1
        print(f"  + {entry['name_en']}")

    DIAG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"\nAdded {added} entries. Total now: {len(data['diagnoses'])}")


if __name__ == "__main__":
    main()
