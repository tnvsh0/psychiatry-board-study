#!/usr/bin/env python3
"""
Phase 0.2 — Extract every entity that needs a memorable visual symbol.

Asks NotebookLM (which has indexed the books) for a JSON inventory of all
recurring concepts that would benefit from Sketchy-style symbols. Output goes
to schedule/entities.json and feeds Phase 0.3 (symbol legend).

Usage:
    extract_entities.py <NOTEBOOK_ID>

Run on the VM (sudo -u User -E env PATH=/opt/venv/bin:$PATH ...).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "schedule"
OUT_FILE = OUT_DIR / "entities.json"

PROMPT = """Across all sources in this notebook, list every entity that
appears repeatedly and would benefit from a memorable visual symbol for
study purposes. Be VERY thorough — this is for a Sketchy-style mnemonic
system. Output JSON ONLY, no prose.

Categories to include (extract everything that appears in the books):

{
  "medications": [
    {"name": "Methylphenidate", "class": "stimulant", "indications": ["ADHD"]}
  ],
  "drug_classes": [
    {"name": "SSRIs", "examples": ["fluoxetine", "sertraline"]}
  ],
  "receptors": [
    {"name": "D2", "neurotransmitter": "dopamine", "type": "G-protein coupled"}
  ],
  "neurotransmitters": [
    {"name": "Dopamine", "key_pathways": ["mesolimbic", "nigrostriatal"]}
  ],
  "brain_regions": [
    {"name": "Prefrontal cortex", "key_functions": ["executive function"]}
  ],
  "neural_circuits": [
    {"name": "Mesolimbic pathway", "endpoints": ["VTA", "nucleus accumbens"]}
  ],
  "dsm_diagnoses": [
    {"name": "ADHD", "category": "Neurodevelopmental"}
  ],
  "assessment_tools": [
    {"name": "PHQ-9", "purpose": "depression screening"}
  ],
  "side_effect_phenomena": [
    {"name": "QT prolongation", "mechanism": "hERG channel block"}
  ],
  "psychotherapy_modalities": [
    {"name": "CBT", "core_principle": "thought-feeling-behavior triangle"}
  ],
  "developmental_concepts": [
    {"name": "Attachment styles", "examples": ["secure","avoidant","anxious","disorganized"]}
  ],
  "lab_studies_imaging": [
    {"name": "EEG", "use_case": "seizure workup"}
  ],
  "ethical_legal_concepts": [
    {"name": "Tarasoff duty", "domain": "duty to warn"}
  ]
}

For EACH entity, include the small structured fields shown above so the
symbol designer can pick a metaphor that captures the essence. Aim for
COMPLETE coverage — if something appears multiple times across books, it
needs to be in the list."""


def run_notebooklm(args: list[str], timeout: int = 300):
    cmd = ["/opt/venv/bin/notebooklm"] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("notebook_id", help="Notebook UUID")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Extract entities from notebook {args.notebook_id} ===")

    print("[1/2] Switching to notebook...")
    r = run_notebooklm(["use", args.notebook_id], timeout=30)
    if r.returncode != 0:
        print(f"FAILED: {r.stderr[:300]}")
        return 1

    print("[2/2] Asking notebook for entity inventory (this may take 2-5 min)...")
    r = run_notebooklm([
        "ask", PROMPT,
        "--json",
        "--save-as-note", "--note-title", "Entity inventory (script-generated)",
    ], timeout=900)
    if r.returncode != 0:
        print(f"FAILED: {r.stderr[:300]}")
        return 1

    try:
        resp = json.loads(r.stdout)
        answer = resp.get("answer", "").strip()
    except Exception as e:
        print(f"FAILED to parse: {e}")
        print(r.stdout[:500])
        return 1

    # Strip markdown fencing if present
    if answer.startswith("```"):
        lines = answer.split("\n")
        answer = "\n".join(lines[1:-1]) if len(lines) > 2 else answer

    try:
        entities = json.loads(answer)
    except Exception as e:
        print(f"WARNING: not valid JSON ({e}). Saved raw to entities_raw.txt")
        (OUT_DIR / "entities_raw.txt").write_text(answer, encoding="utf-8")
        return 1

    output = {
        "notebook_id": args.notebook_id,
        "extracted_via": "notebooklm ask",
        **entities,
    }
    OUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = {k: len(v) for k, v in entities.items() if isinstance(v, list)}
    print(f"\n✓ Saved: {OUT_FILE}")
    for k, n in counts.items():
        print(f"   {k}: {n}")
    print()
    print("NEXT: review schedule/entities.json with user. Adjust as needed.")
    print("Then run scripts/build_symbols.py (assists Claude in proposing symbols).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
