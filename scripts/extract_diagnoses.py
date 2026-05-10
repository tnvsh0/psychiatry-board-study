#!/usr/bin/env python3
"""
Phase 0.2a — Extract psychiatric DIAGNOSES from Dulcan + DSM.

Asks NotebookLM (filtered to specific source IDs) for a structured list of
every disorder/diagnosis covered, with DSM category, page reference, and
clinical notes useful for scheduling.

Usage:
    extract_diagnoses.py <NOTEBOOK_ID> <DSM_SOURCE_ID> <DULCAN_SOURCE_ID>

Source IDs (Board-Exam study material notebook):
  DSM-5-TR     : 38aac4e5-ba7f-4e06-ad03-b97acb427d92
  Dulcan 3e    : 8948e9fc-1b07-4fdf-86bf-df5aa1c81615
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "schedule"
OUT_FILE = OUT_DIR / "diagnoses.json"

PROMPT = """From the DSM-5-TR and Dulcan's Textbook of Child & Adolescent
Psychiatry sources in this notebook, list EVERY psychiatric diagnosis
covered, with focus on those relevant to child & adolescent psychiatry.

Output JSON ONLY, no prose:

{
  "diagnoses": [
    {
      "name_en": "Attention-Deficit/Hyperactivity Disorder",
      "name_he": "הפרעת קשב וריכוז",
      "dsm_category": "Neurodevelopmental Disorders",
      "icd_code": "F90.x",
      "dsm_page": 59,
      "dulcan_chapter": "Chapter 24",
      "dulcan_page": 412,
      "key_subtypes": ["Combined", "Predominantly Inattentive", "Predominantly Hyperactive"],
      "comorbidities_common": ["ODD", "Anxiety", "Learning Disorders"],
      "complexity": "high"
    }
  ]
}

Cover ALL DSM categories that touch child & adolescent psychiatry:
- Neurodevelopmental Disorders (ADHD, ASD, ID, communication, learning, motor, tic)
- Schizophrenia spectrum (early-onset)
- Bipolar (pediatric forms)
- Depressive (MDD, persistent depressive, DMDD, PMDD)
- Anxiety (separation, social, GAD, panic, agoraphobia, specific phobia, selective mutism)
- OCD-related (OCD, BDD, hoarding, trichotillomania, excoriation)
- Trauma- & stressor-related (PTSD, acute stress, adjustment, RAD, DSED)
- Dissociative, Somatic Symptom & related
- Feeding & Eating (anorexia, bulimia, BED, ARFID, pica, rumination)
- Elimination (enuresis, encopresis)
- Sleep-Wake (insomnia, hypersomnia, narcolepsy, breathing-related, circadian, parasomnias, restless legs)
- Disruptive/Impulse-control/Conduct (ODD, conduct disorder, intermittent explosive, kleptomania, pyromania)
- Substance-related & Addictive (in adolescents)
- Personality (especially BPD onset in adolescence)
- Gender Dysphoria
- Paraphilic, sexual dysfunctions (only if relevant to adolescents)

Set "complexity" to "low" / "medium" / "high" based on how much study material
each typically requires (criteria + epidemiology + treatment + nuance).
Mark diagnoses that need MULTIPLE study days as "very_high".

Be thorough. Aim for 40–80 entries."""


def run_notebooklm(args, timeout=600):
    return subprocess.run(["/opt/venv/bin/notebooklm"] + args,
                          capture_output=True, text=True, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("notebook_id")
    ap.add_argument("dsm_source_id")
    ap.add_argument("dulcan_source_id")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Extract diagnoses from notebook {args.notebook_id} ===")
    print(f"  Filtering to: DSM ({args.dsm_source_id[:8]}) + Dulcan ({args.dulcan_source_id[:8]})")

    print("[1/2] Switching to notebook...")
    r = run_notebooklm(["use", args.notebook_id], timeout=30)
    if r.returncode != 0:
        print(f"FAIL: {r.stderr[:300]}")
        return 1

    print("[2/2] Asking for diagnoses (3-8 min)...")
    r = run_notebooklm([
        "ask", PROMPT,
        "--json",
        "-s", args.dsm_source_id,
        "-s", args.dulcan_source_id,
        "--save-as-note", "--note-title", "Diagnoses inventory (script)",
    ], timeout=900)
    if r.returncode != 0:
        print(f"FAIL: {r.stderr[:300] or r.stdout[:300]}")
        return 1

    try:
        resp = json.loads(r.stdout)
        answer = resp.get("answer", "").strip()
    except Exception as e:
        print(f"FAIL parse: {e}")
        print(r.stdout[:500])
        return 1

    m = re.search(r"```(?:json)?\s*(\{[\s\S]+?\})\s*```", answer)
    inner = m.group(1) if m else answer
    try:
        data = json.loads(inner)
    except Exception as e:
        print(f"WARNING: not valid JSON ({e}). Saved raw to diagnoses_raw.txt")
        (OUT_DIR / "diagnoses_raw.txt").write_text(answer, encoding="utf-8")
        return 1

    output = {
        "notebook_id": args.notebook_id,
        "extracted_via": "notebooklm ask -s DSM -s Dulcan",
        **data,
    }
    OUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    n = len(data.get("diagnoses", []))
    print(f"\n✓ Saved: {OUT_FILE}  ({n} diagnoses)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
