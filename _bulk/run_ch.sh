#!/usr/bin/env bash
# One chapter, end to end, on the bulk profile. Times every phase so the 141-
# chapter schedule is measured rather than guessed.
set -u
cd "$(dirname "$0")/.."
export NOTEBOOKLM_PROFILE=bulk
export SKETCHY_IMAGE_SIZE=2K
# Draft rungs on Flash, the shipped frame on Pro. Owner's call, 2026-08-23:
# ~$85 across 141 chapters instead of ~$227.
export SKETCHY_LADDER_MODEL=gemini-3.1-flash-image-preview
set -a; . ./.env; set +a
DAY="${1:-29}"
echo "=== chapter $DAY | started $(date -u +%H:%M:%S) UTC ==="
python -u scripts/daily_study.py --day "$DAY" 2>&1
echo "=== finished $(date -u +%H:%M:%S) UTC ==="
