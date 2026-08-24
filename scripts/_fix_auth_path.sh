#!/usr/bin/env bash
# One-off: the notebooklm CLI migrated to a profiles/ layout, but the installed
# CLI (0.3.4) still looks for ~/.notebooklm/storage_state.json. A fresh login
# therefore lands in profiles/default/ and the pipeline reports AUTH_REQUIRED.
# Mirror the profile's auth into the legacy path the CLI actually checks.
set -u

NB_DIR="$HOME/.notebooklm"
PROFILE_AUTH="$NB_DIR/profiles/default/storage_state.json"
LEGACY_AUTH="$NB_DIR/storage_state.json"

echo "=== source (profile) ==="
ls -la "$PROFILE_AUTH" 2>/dev/null || { echo "MISSING: $PROFILE_AUTH"; exit 1; }

if [ -f "$LEGACY_AUTH" ]; then
  cp -f "$LEGACY_AUTH" "$LEGACY_AUTH.bak.$(date +%s)"
  echo "backed up existing legacy auth"
fi

cp -f "$PROFILE_AUTH" "$LEGACY_AUTH"
chmod 600 "$LEGACY_AUTH"
echo "=== installed at legacy path ==="
ls -la "$LEGACY_AUTH"

echo "=== auth test ==="
timeout 150 /opt/venv/bin/notebooklm list --json 2>&1 | head -12
