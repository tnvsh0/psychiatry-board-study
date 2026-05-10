#!/usr/bin/env python3
"""Parse a `notebooklm note get` text dump → extract just the JSON Content."""
import json
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
dest = Path(sys.argv[2])

text = src.read_text(encoding="utf-8")

# Find "Content:" then take the rest
m = re.search(r"^Content:\s*\n", text, re.MULTILINE)
if not m:
    print(f"No 'Content:' header found in {src}")
    sys.exit(1)

body = text[m.end():].strip()
# Strip optional ```json fences
m2 = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", body)
if m2:
    body = m2.group(1).strip()

# Use raw_decode to grab the first complete JSON object (in case there's
# trailing prose from NotebookLM after the JSON ends).
# strict=False allows unescaped newlines inside strings (notebooklm wraps
# long lines in note rendering).
decoder = json.JSONDecoder(strict=False)
try:
    data, end = decoder.raw_decode(body)
except Exception as e:
    print(f"Could not parse JSON ({e}). Trying with newline-in-string fix...")
    # Fallback: join lines that are inside an unterminated string
    fixed = re.sub(r'(?<!\\)\n(?!\s*[\}\]\,])', ' ', body)
    try:
        data, end = decoder.raw_decode(fixed)
    except Exception as e2:
        print(f"Still failed ({e2})")
        print("First 300 chars of body:")
        print(body[:300])
        sys.exit(1)

dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Saved: {dest}")

# Quick stats
for k, v in data.items():
    if isinstance(v, list):
        print(f"  {k}: {len(v)} entries")
