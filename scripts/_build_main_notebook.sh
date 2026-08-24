#!/usr/bin/env bash
# Build a fresh MAIN notebook in the (new) logged-in account and upload the
# course textbooks into it. Prints the new notebook id at the end so we can
# wire it into the pipeline (BOARD_STUDY_NOTEBOOK_ID / NOTEBOOK_ID).
set -u

NB=/opt/venv/bin/notebooklm
SRC=/opt/sources
TITLE="Board-Exam study material (v2)"
OUT=/tmp/main_nb_id.txt

# 6 books the pipeline uses (comprehensive intentionally omitted — 368 MB).
FILES=(
  "dsm-5-tr.pdf"
  "dulcan.pdf"
  "stahl-essential.pdf"
  "stahl-pediatric.pdf"
  "kaplan-sadock-synopsis.pdf"
  "syllabus.pdf"
)

echo "=== creating notebook: $TITLE ==="
CREATE_JSON="$("$NB" create "$TITLE" --json 2>&1)"
echo "$CREATE_JSON"
NB_ID="$(python3 -c 'import sys,json; d=json.load(sys.stdin); print((d.get("notebook") or {}).get("id") or d.get("id") or "")' <<<"$CREATE_JSON")"
if [ -z "$NB_ID" ]; then
  echo "FAILED to create notebook / parse id"; exit 1
fi
echo "NEW_NOTEBOOK_ID=$NB_ID"
echo "$NB_ID" > "$OUT"

for f in "${FILES[@]}"; do
  p="$SRC/$f"
  if [ ! -f "$p" ]; then echo "  [skip missing] $f"; continue; fi
  mb="$(du -m "$p" | cut -f1)"
  echo "=== uploading $f (${mb} MB) ... ==="
  # generous timeout: the big Stahl files need it
  timeout 1800 "$NB" source add "$p" --title "${f%.pdf}" --json -n "$NB_ID" 2>&1 | head -4
done

echo "=== final source list ==="
"$NB" source list -n "$NB_ID" --json 2>&1 | python3 -c 'import sys,json;
try:
    d=json.load(sys.stdin); print("count:", d.get("count", len(d.get("sources",[]))))
except Exception as e:
    print("parse error:", e)'
echo "=== DONE. notebook id saved to $OUT ==="
cat "$OUT"
