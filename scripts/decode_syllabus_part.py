#!/usr/bin/env python3
"""Take a notebooklm ask --json response and extract the topics list."""
import json
import re
import sys
from pathlib import Path

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

src = Path(sys.argv[1] if len(sys.argv) > 1 else "schedule/_syllabus_raw.json")
dest = Path(sys.argv[2] if len(sys.argv) > 2 else "schedule/syllabus_topics_part1.json")

text = src.read_text(encoding="utf-8")
# Skip leading "Matched: ..." line if present
if text.lstrip().startswith("Matched:"):
    text = text.split("\n", 1)[1]

resp = json.loads(text)
answer = resp.get("answer", "")
# Strip markdown json fence if present
m = re.search(r"```(?:json)?\s*(\[[\s\S]+?\])\s*```", answer)
inner = m.group(1) if m else answer

topics = json.loads(inner)
print(f"Decoded {len(topics)} topics from syllabus")

# Detect page from "[NN]" at end of each line
parsed = []
for t in topics:
    pm = re.search(r"\[(\d+)(?:,\s*\d+)*\]\s*$", t.strip())
    page = int(pm.group(1)) if pm else None
    label = re.sub(r"\s*\[(\d+)(?:,\s*\d+)*\]\s*$", "", t.strip())
    indent = len(t) - len(t.lstrip())
    parsed.append({
        "indent": indent,
        "label": label,
        "page": page,
    })

dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps({"topics": parsed}, ensure_ascii=False, indent=2),
                encoding="utf-8")
print(f"Saved: {dest}")

# Print preview
print("\n--- First 30 topics (preview) ---")
for t in parsed[:30]:
    pad = " " * t["indent"]
    pg = f" (p.{t['page']})" if t['page'] else ""
    print(f"{pad}{t['label']}{pg}")

# Print page range
pages = [t["page"] for t in parsed if t["page"]]
if pages:
    print(f"\nPage range: {min(pages)} → {max(pages)}")
