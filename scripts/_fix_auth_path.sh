#!/usr/bin/env bash
# OBSOLETE as of the 0.3.4 -> 0.7.3 upgrade of /opt/venv (2026-08-03). DO NOT RUN.
#
# What it used to do: notebooklm 0.3.4 read auth from
# ~/.notebooklm/storage_state.json, but a fresh login already wrote to
# ~/.notebooklm/profiles/default/. This mirrored the profile file back to the
# legacy path so the CLI could find it.
#
# Why it is now harmful: 0.7.x reads the profile layout natively and ignores the
# legacy path entirely. Running this copies a file nothing reads and, worse,
# makes auth look repaired when it has not been touched — the same false green
# that cost the weekly-review session two full runs.
#
# The rule for 0.7.x, shared by every project on this VM:
#
#     export NOTEBOOKLM_HOME=/home/User/.notebooklm     # the ROOT
#     # NEVER  /home/User/.notebooklm/profiles/default
#
# The library appends `profiles/<name>` itself, so pointing HOME at the profile
# directory makes it write to profiles/default/profiles/default/ — a keepalive
# then refreshes a file no reader ever opens while the real one quietly ages
# out. board-study never set NOTEBOOKLM_HOME, so it was not affected by that
# bug; this script is retired for the separate reason above.
#
# To diagnose auth or CLI drift now, use scripts/_check_cli_compat.sh.
echo "Obsolete under notebooklm-py 0.7.x — this script does nothing." >&2
echo "Read the comments here, then run scripts/_check_cli_compat.sh." >&2
exit 1
