#!/usr/bin/env bash
# Verify that the notebooklm CLI on this box still supports every subcommand the
# board-study pipeline actually depends on.
#
# WHY: /opt/venv was upgraded 0.3.4 -> 0.7.3 by the weekly-review session on
# 2026-08-03. They verified the commands THEIR pipeline uses (list, create, use,
# source add, generate audio, artifact list, download audio, delete). Ours uses
# a different set, and the overlap is small: every `ask` call and every
# `generate` except audio is unverified against 0.7.3.
#
# `ask --json` matters most — it is how facts.json is extracted, and if its
# output shape changed the pipeline does not crash, it produces EMPTY CHAPTERS.
# That is the failure mode worth catching early: silent, and only visible weeks
# later as a chapter with no content.
#
# Run this on the VM the next time it is awake, before any bulk run:
#     bash scripts/_check_cli_compat.sh
set -u

NB="${NOTEBOOKLM_BIN:-/opt/venv/bin/notebooklm}"
PASS=0; FAIL=0

hdr() { printf '\n=== %s ===\n' "$1"; }
chk() { # chk <label> <args...>  — does this subcommand still exist and parse?
  local label="$1"; shift
  printf '  %-42s ' "$label"
  if timeout 45 "$NB" "$@" --help >/dev/null 2>&1; then
    echo "OK"; PASS=$((PASS+1))
  else
    echo "MISSING / CHANGED"; FAIL=$((FAIL+1))
  fi
}

hdr "CLI version"
"$NB" --version 2>&1 | head -2

hdr "auth (must be root, NOT profiles/default — see 0.7 profile handling)"
echo "  NOTEBOOKLM_HOME=${NOTEBOOKLM_HOME:-(unset, CLI default)}"
timeout 90 "$NB" list --json 2>&1 | head -3

hdr "subcommands this pipeline calls"
chk "use"                      use
chk "list"                     list
chk "source add"               source add
chk "source list"              source list
chk "ask            [CRITICAL]" ask
chk "generate audio"           generate audio
chk "generate video"           generate video
chk "generate infographic"     generate infographic
chk "generate report"          generate report
chk "generate flashcards"      generate flashcards
chk "generate quiz"            generate quiz
chk "artifact list"            artifact list
chk "download audio"           download audio
chk "download video"           download video
chk "download infographic"     download infographic
chk "download report"          download report

hdr "the shape that actually matters"
cat <<'NOTE'
  A subcommand existing is not the same as its OUTPUT being unchanged.
  facts.json is built from `ask --json`, so run one real extraction and look
  at the JSON before trusting a bulk run:

    /opt/venv/bin/notebooklm use <NOTEBOOK_ID>
    /opt/venv/bin/notebooklm ask 'Return STRICT JSON: {"ping":"pong"}' --json

  Expect a JSON object with the answer text reachable the same way
  daily_study.py reads it (see _extract_answer / nb_run call sites).
NOTE

hdr "result"
echo "  $PASS ok, $FAIL missing-or-changed"
[ "$FAIL" -eq 0 ] || echo "  -> do NOT start a bulk run until these are resolved"
