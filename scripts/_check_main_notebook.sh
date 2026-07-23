#!/usr/bin/env bash
# Diagnose: is the pipeline's MAIN notebook reachable from the currently
# logged-in account? After switching Google accounts the old notebook id may
# belong to the previous account, which surfaces as "RPC GET_NOTEBOOK failed".
set -u

MAIN_ID="${BOARD_STUDY_NOTEBOOK_ID:-157ae88a-ba4c-4d76-a1fd-c551d449a782}"
NB=/opt/venv/bin/notebooklm

echo "=== configured MAIN notebook id ==="
echo "$MAIN_ID"

echo
echo "=== try to select it with the current account ==="
timeout 90 "$NB" use "$MAIN_ID" --json 2>&1 | head -8

echo
echo "=== notebooks this account can actually see ==="
timeout 90 "$NB" list --json 2>&1 > /tmp/_nb_list.json
python3 - <<'PY'
import json
try:
    d = json.load(open('/tmp/_nb_list.json'))
except Exception as e:
    print("could not parse list output:", e)
    raise SystemExit
nbs = d.get("notebooks", [])
print(f"count: {len(nbs)}")
for n in nbs:
    print(f"  {n.get('id')}  {n.get('title','')[:70]}")
PY
