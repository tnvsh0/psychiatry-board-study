# Psychiatry Board Study Automation

Daily content-generation system for board exam prep, built on top of NotebookLM via the [`notebooklm-py`](https://github.com/teng-lin/notebooklm-py) CLI.

## Architecture

User-triggered (no daily cron). Each "Generate next day" click on the Streamlit UI causes the VM to:

1. Read the next un-generated row from `schedule/schedule.json`
2. Create a temp NotebookLM notebook `[Study-NNN] {topic}`
3. Add a focused Markdown source for that day's pages
4. In parallel on Google's servers, generate:
   - 🎙 Audio podcast (Hebrew, deep-dive)
   - 🎬 Video explainer (Hebrew, whiteboard style)
   - 🖼 1+ Infographics (Hebrew, sketch-note style, one per aspect)
   - 🃏 Flashcards (JSON)
   - ❓ Quiz (JSON)
   - 📖 Study guide (Markdown)
5. Wait, download all artifacts
6. Upload to GitHub Release `study-NNN-{topic-slug}`
7. ntfy with action-button links

**The notebook stays alive** so you can return to ask follow-up questions in the NotebookLM web UI.

## Prerequisites

- Runs on the existing `weekly-review-vm` (me-west1-b, static IP) — same VM as `psychiatry-weekly-review`, same NotebookLM session
- Requires the same `gh` CLI, `gcloud` SDK, and Python venv at `/opt/venv`

## Layout

```
scripts/
  extract_toc.py            # 0.1 — TOC from NotebookLM
  extract_entities.py       # 0.2 — entity inventory for symbols
  build_symbols.py          # 0.3 — propose symbols (Claude)
  build_schedule.py         # 0.4 — pedagogically order days
  daily_study.py            # main daily generator
schedule/
  toc.json                  # output of 0.1
  entities.json             # output of 0.2
  symbols.json              # locked after user review
  schedule.json             # locked after user review
  progress.json             # generation state
vm/
  run_daily_study.sh        # wrapper for Streamlit trigger
app/
  study_tab.py              # Streamlit UI extension
```

## Setup phase (one-time, careful, with user review)

See `docs/SETUP.md`. Highlights:

```bash
# All commands run on the VM as User (via Chrome Remote Desktop or SSH)
cd /opt/psychiatry-board-study

# 0.1
python scripts/extract_toc.py <NOTEBOOK_ID>
# REVIEW schedule/toc.json with user → add missing books to NotebookLM if needed

# 0.2
python scripts/extract_entities.py <NOTEBOOK_ID>
# REVIEW schedule/entities.json with user

# 0.3 — done in Claude conversation, output edited into schedule/symbols.json
# REVIEW schedule/symbols.json with user (every symbol)

# 0.4
python scripts/build_schedule.py
# REVIEW schedule/schedule.json with user (day-by-day pacing)

# Phase B — single-day dry run
python scripts/daily_study.py 0
# REVIEW all 7+ artifacts on GitHub Release
```

## Daily use

Click "Generate next day" in the Streamlit UI → wait ~20 min → ntfy delivers links.
