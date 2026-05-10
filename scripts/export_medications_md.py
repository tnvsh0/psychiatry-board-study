#!/usr/bin/env python3
"""Export schedule/medications.json to a readable Markdown list by class."""
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "schedule" / "medications.json"
DEST = ROOT / "schedule" / "medications_list.md"

data = json.loads(SRC.read_text(encoding="utf-8"))
meds = data["medications"]


def family(cls: str) -> str:
    """Group classes into broader families for readable Markdown sections."""
    c = cls.lower()
    if "ssri" in c: return "1. Antidepressants — SSRI"
    if "snri" in c: return "2. Antidepressants — SNRI"
    if "tca" in c or "tricyclic" in c: return "3. Antidepressants — TCA"
    if "maoi" in c: return "4. Antidepressants — MAOI"
    if "ndri" in c: return "5. Antidepressants — Atypical (NDRI / SARI / Multimodal / etc.)"
    if "alpha 2 antagonist" in c: return "5. Antidepressants — Atypical (NDRI / SARI / Multimodal / etc.)"
    if "atypical" in c and "antidepressant" in c: return "5. Antidepressants — Atypical (NDRI / SARI / Multimodal / etc.)"
    if "sari" in c: return "5. Antidepressants — Atypical (NDRI / SARI / Multimodal / etc.)"
    if "spari" in c or "multimodal" in c: return "5. Antidepressants — Atypical (NDRI / SARI / Multimodal / etc.)"
    if "neuroactive steroid" in c: return "6. Antidepressants — Novel / Rapid-acting"
    if "rapid-acting" in c or "anesthetic" in c: return "6. Antidepressants — Novel / Rapid-acting"
    if "first-generation" in c and "antipsychotic" in c: return "7. Antipsychotics — First-generation"
    if "second-generation" in c and "antipsychotic" in c: return "8. Antipsychotics — Second-generation"
    if "5-ht2a inverse" in c: return "8. Antipsychotics — Second-generation"
    if "long-acting injectable" in c: return "9. Antipsychotics — Long-acting injectable"
    if "mood stabilizer" in c: return "10. Mood Stabilizers / Anticonvulsants in psychiatry"
    if "benzodiazepine" in c: return "11. Benzodiazepines"
    if "anxiolytic" in c: return "12. Other Anxiolytics"
    if "sleep aid" in c or "dora" in c or "z-drug" in c or "chronobiotic" in c: return "13. Sleep aids / Hypnotics"
    if "non-stimulant" in c: return "15. ADHD — Non-stimulants"
    if "stimulant" in c: return "14. ADHD — Stimulants"
    if "addiction" in c: return "16. Addiction Treatment"
    if "anticholinergic" in c or "antihistamine" in c and "eps" in c: return "17. EPS / Movement Disorders Treatment"
    if "vmat2" in c: return "17. EPS / Movement Disorders Treatment"
    if "α1" in c or "alpha" in c.replace("alpha 2", "") or "adrenergic" in c: return "18. Adrenergic agents (PTSD / akathisia / agitation)"
    if "β-blocker" in c or "beta" in c or "blocker" in c: return "18. Adrenergic agents (PTSD / akathisia / agitation)"
    if "wake-promoting" in c: return "19. Wake-promoting agents"
    if "5-ht2a antagonist" in c: return "20. Other (cyproheptadine etc.)"
    return "99. Uncategorized"


groups = defaultdict(list)
for m in meds:
    fam = family(m.get("drug_class", ""))
    groups[fam].append(m)

lines = [
    "# רשימת תרופות (Medications inventory)",
    "",
    f"סה\"כ: **{len(meds)}** תרופות",
    "",
    "מקור: מחולץ אוטומטית מ-Stahl + Stahl Pediatric ב-NotebookLM, עם השלמות ידניות (`_source: user-supplement-2026-05-10`).",
    "",
    "---",
    "",
]

for fam in sorted(groups.keys(), key=lambda s: int(s.split(".")[0])):
    section_meds = groups[fam]
    lines.append(f"## {fam}  ({len(section_meds)})")
    lines.append("")
    for m in section_meds:
        name = m.get("generic_name", "?")
        brands = m.get("brand_examples", [])
        brand_str = f" — *{', '.join(brands[:3])}*" if brands else ""
        cls = m.get("drug_class", "")
        # If drug_class adds more detail than the family heading, show it
        cls_short = re.sub(r".*— ?", "", cls).strip()
        cls_extra = f" — {cls_short}" if cls_short and cls_short.lower() != cls.lower() else ""
        notes = m.get("_notes") or ""
        notes_short = notes[:140] + ("…" if len(notes) > 140 else "") if notes else ""
        lines.append(f"- **{name}**{brand_str}{cls_extra}")
        if notes_short:
            lines.append(f"  - {notes_short}")
    lines.append("")

DEST.write_text("\n".join(lines), encoding="utf-8")
# ASCII-safe console summary
print(f"Wrote {DEST.name}: {len(meds)} meds across {len(groups)} families")
for fam in sorted(groups.keys(), key=lambda s: int(s.split(".")[0])):
    fam_ascii = fam.encode('ascii', 'replace').decode()
    print(f"  {fam_ascii:60} {len(groups[fam])}")
