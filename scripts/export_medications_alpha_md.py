#!/usr/bin/env python3
"""Export medications.json to alphabetical Markdown list (one line each)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "schedule" / "medications.json"
DEST = ROOT / "schedule" / "medications_list_alpha.md"

meds = json.loads(SRC.read_text(encoding="utf-8"))["medications"]
meds_sorted = sorted(meds, key=lambda m: m.get("generic_name", "").lower())

lines = [
    "# רשימת תרופות — סדר אלפביתי",
    "",
    f"סה\"כ: **{len(meds_sorted)}** תרופות (זהה לרשימה לפי משפחות, רק בסדר אחר).",
    "",
    "---",
    "",
]

prev_letter = ""
for m in meds_sorted:
    name = m.get("generic_name", "?")
    letter = name[0].upper()
    if letter != prev_letter:
        lines.append("")
        lines.append(f"### {letter}")
        lines.append("")
        prev_letter = letter
    brands = m.get("brand_examples", [])
    brand_str = f" *({', '.join(brands[:2])})*" if brands else ""
    cls = m.get("drug_class", "")
    lines.append(f"- **{name}**{brand_str} — {cls}")

DEST.write_text("\n".join(lines), encoding="utf-8")
print(f"Wrote {DEST.name}: {len(meds_sorted)} meds A-Z")
