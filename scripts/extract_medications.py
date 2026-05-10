#!/usr/bin/env python3
"""
Phase 0.2b — Extract psychiatric MEDICATIONS from Stahl + Stahl Children.

Source IDs (Board-Exam study material notebook):
  Stahl Essential          : 7310d4f8-f326-435f-a72f-177905393e00
  Stahl Children & Adol.   : 5b3b88cd-f508-408c-92d7-1323757e11e2
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "schedule"
OUT_FILE = OUT_DIR / "medications.json"

PROMPT = """From Stahl's Essential Psychopharmacology and Stahl's Prescriber's
Guide for Children & Adolescents, list EVERY psychiatric medication covered.

Output JSON ONLY:

{
  "medications": [
    {
      "generic_name": "Methylphenidate",
      "brand_examples": ["Ritalin", "Concerta", "Daytrana"],
      "drug_class": "Stimulant — dopamine/norepinephrine reuptake inhibitor",
      "key_indications_psych": ["ADHD"],
      "approved_for_pediatric": true,
      "primary_receptors_actions": ["DAT inhibitor", "NET inhibitor"],
      "stahl_essential_chapter": "Chapter 8",
      "stahl_essential_pages": [502, 540],
      "stahl_pediatric_pages": [123, 130],
      "complexity": "high",
      "study_pair_with_diagnosis": "ADHD"
    }
  ]
}

Cover ALL major drug classes used in pediatric psychiatry:
- Stimulants (methylphenidate, amphetamine, lisdexamfetamine)
- Non-stimulant ADHD (atomoxetine, guanfacine, clonidine)
- Antidepressants — SSRIs (fluoxetine, sertraline, escitalopram, fluvoxamine, etc.)
- Antidepressants — SNRIs (venlafaxine, duloxetine)
- Antidepressants — atypical (bupropion, mirtazapine, vilazodone)
- Antidepressants — TCAs (used in pediatric: clomipramine, imipramine)
- Antidepressants — MAOIs (rare in peds, mention briefly)
- Antipsychotics — first-gen (haloperidol, chlorpromazine — historical)
- Antipsychotics — second-gen (risperidone, aripiprazole, olanzapine, quetiapine,
  ziprasidone, paliperidone, lurasidone, asenapine, brexpiprazole, cariprazine)
- Mood stabilizers (lithium, valproate, lamotrigine, carbamazepine, oxcarbazepine)
- Anxiolytics (benzodiazepines, buspirone, hydroxyzine)
- Sleep aids (melatonin, ramelteon, suvorexant — relevant for peds where applicable)
- OCD-specific dose ranges (fluvoxamine, sertraline at high doses)
- Specialty (clonidine for sleep/aggression, naltrexone, propranolol, etc.)
- Newer/notable (esketamine — adolescents off-label, brexanolone)

Be thorough. Aim for 40–80 entries. For each medication, set
"approved_for_pediatric" only if there's an FDA pediatric indication mentioned
in Stahl. For "complexity", use low/medium/high based on study burden
(mechanism + side effects + monitoring + practical use)."""


def run_notebooklm(args, timeout=600):
    return subprocess.run(["/opt/venv/bin/notebooklm"] + args,
                          capture_output=True, text=True, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("notebook_id")
    ap.add_argument("stahl_essential_id")
    ap.add_argument("stahl_pediatric_id")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Extract medications ===")
    r = run_notebooklm(["use", args.notebook_id], timeout=30)
    if r.returncode != 0:
        print(f"FAIL: {r.stderr[:300]}")
        return 1

    print("[2/2] Asking (3-8 min)...")
    r = run_notebooklm([
        "ask", PROMPT,
        "--json",
        "-s", args.stahl_essential_id,
        "-s", args.stahl_pediatric_id,
        "--save-as-note", "--note-title", "Medications inventory (script)",
    ], timeout=900)
    if r.returncode != 0:
        print(f"FAIL: {r.stderr[:300] or r.stdout[:300]}")
        return 1

    try:
        resp = json.loads(r.stdout)
        answer = resp.get("answer", "").strip()
    except Exception as e:
        print(f"FAIL parse: {e}")
        return 1

    m = re.search(r"```(?:json)?\s*(\{[\s\S]+?\})\s*```", answer)
    inner = m.group(1) if m else answer
    try:
        data = json.loads(inner)
    except Exception as e:
        print(f"WARNING: not valid JSON ({e}). Saved raw to medications_raw.txt")
        (OUT_DIR / "medications_raw.txt").write_text(answer, encoding="utf-8")
        return 1

    output = {
        "notebook_id": args.notebook_id,
        "extracted_via": "notebooklm ask -s Stahl_essential -s Stahl_pediatric",
        **data,
    }
    OUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    n = len(data.get("medications", []))
    print(f"\n✓ Saved: {OUT_FILE}  ({n} medications)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
