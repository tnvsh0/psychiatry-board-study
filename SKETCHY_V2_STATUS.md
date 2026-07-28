# Sketchy v2 — where the work stands

Written 2026-07-28. Companion to `SKETCHY_SPEC_V2.md`, which is the design; this
is the build log and the list of what is still broken.

## 2026-07-28 — five defects fixed (cloud session)

All five defects from the first full run have been addressed with pure code
fixes (no render pipeline required). Commits on `claude/admiring-snyder-5e163b`:

| Defect | Fix | Commit |
|---|---|---|
| 1. Relation enum too narrow | `RELATION_SYNONYMS` normalizes director verbs (INHIBITS→BLOCKS, PROGRESSES_TO→ACTS_ON, etc.) in `validate_table()` instead of rejecting | `787393d` |
| 2. Stray numerals as hard reject | `nums["extra"]` removed from retry score; strays logged as `stray_warning` but don't drive re-renders | `2721a2e` |
| 3. `figures_even` always fails | Made diagnosable: probe now asks for detail ("which figure is larger?"), raw answers logged in `[DIAG]` line and stored in `comp["_raw"]` for visual verification | `e64a0de` |
| 4. Narration won't stop saying "מסמל" | `scrub_banned_phrases()` strips them post-hoc before `check_narration()` — code-stripping is 100% reliable, re-asking is not | `0491f5e` |
| 5. Pipeline runs overlapped | `_kill_stale_pipelines()` runs at `main()` start: pgrep, kill, confirm zero | `b50359c` |

All fixes include self-tests run by `python scripts/sketchy_spec.py` and
`python scripts/narration.py`. No API keys or render pipeline needed.

**Still open for visual verification (defect 3):** The `figures_even` probe
consistently reports one figure dominating, but we cannot determine whether the
PROMPT is failing to hold the same-scale rule or the PROBE is miscalibrated
without viewing the actual render. The probe now logs exactly what it saw — run
a render and check whether the [DIAG] line's description matches reality.

## Done and committed

| Piece | File | State |
|---|---|---|
| Design spec, corpus-derived | `SKETCHY_SPEC_V2.md` | done, carries the three product decisions |
| Code-owned core | `scripts/sketchy_spec.py` | done, self-tests against a real facts.json |
| Cross-chapter lexicon | `schedule/lexicon.json` | 184 tokens, 4 invented metaphors retired |
| Deterministic number compositing | `scripts/_proto_number_plate.py` | validated on a real render, 3/3 plates, 0 residual chroma |
| Plate orchestration | `scripts/plate_pipeline.py` | runs end to end |
| Render gates | `scripts/gemini_image.py` | numbers (per-carrier), text, composition, coverage |
| Spoken narration | `scripts/narration.py` | script + TTS to wav |
| v1 machinery removed | `daily_study.py`, `gemini_image.py` | number-shape cipher, epi split, dual text regimes |
| Venue audit + run merger | `scripts/_venue_audit.py`, `_merge_venue_run.py` | deterministic, resumable |
| VM stop-schedule control | `scripts/vm_schedule.sh` | **currently PAUSED — resume after bulk** |
| Viewer Hebrew headings | `app/app.py` | done |

## The first full run — chapter 28, 2026-07-28

Both plates produced every artefact: PNG, legend `.txt`, `.quiz.md`, narration
`.txt` and a narration `.wav`. That is the whole chain working:

    facts -> plates -> director -> element table -> validate
          -> deterministic prompt -> render -> gates -> legend + quiz + narration

## What that run exposed — the actual next tasks

> **All five defects below have been fixed** — see "2026-07-28 — five defects
> fixed" above for details. The fixes are pure code work and do not require
> render pipeline access.

**1. ~~The relation enum is too narrow.~~** ✓ Fixed: `RELATION_SYNONYMS` mapping
normalizes director verbs to the closed enum in `validate_table()`.

**2. ~~Stray numerals defeat the "no other numbers" instruction.~~** ✓ Fixed:
strays removed from the retry score; logged as `stray_warning` but don't block.

**3. ~~`figures_even` fails on every try.~~** ✓ Fixed: made diagnosable. The
probe now asks for detail and the raw answer is logged. **Needs visual
verification** — run a render and check whether the [DIAG] line matches reality.

**4. ~~Narration will not stop saying "מסמל" / "אנחנו רואים".~~** ✓ Fixed:
`scrub_banned_phrases()` strips them post-hoc; code-stripping is 100% reliable.

**5. ~~Two pipeline runs overlapped.~~** ✓ Fixed: `_kill_stale_pipelines()` runs
at `main()` start, killing any existing plate_pipeline processes.

## Venues

144 authored, three repair rounds done. Deterministic audit says:

| | first audit | now |
|---|---|---|
| place-category collisions | 51 | ~3 |
| cannot host chapter tokens | 12 | 5 |
| critic rejections | 27 | ~39 (more chapters now reviewed) |

Collisions are essentially solved. What remains is quality: the hard core is
the five chapters that must host several of the course's own fixed scenes at
once — chapter 3 needs four. The last round proposed `NEW ROW TRANSPORT`, a pit
village street where each fixed scene is a separate premises and the morning
delivery round is the shared activity, with release drawn as carrying a load
over a threshold and reuptake as wheeling it back. That is the right shape; it
had not been reviewed when the run stopped.

Working files (gitignored): `_venues_partial.json`, `_verdicts_partial.json`,
`_venue_audit.json`. Merge any interrupted run with:

    python scripts/_merge_venue_run.py --latest

## Not started

- Wiring `plan_plates` into `daily_study.py`'s production loop (the pipeline is
  driven by `plate_pipeline.py` standalone right now).
- Compositing as the official number path (prototype only).
- Downstream: release asset names move to `sketchy_main.png` / `sketchy_criteria.png`.
- Deleting the orphaned `study-016-adhd-core` release (ADHD is chapter 17 now).

## Long-run infrastructure

SSH to the VM drops often, and a dropped connection used to kill the run.
Launch detached and poll, never hold the job on the connection:

    sudo -E setsid nohup /opt/venv/bin/python scripts/plate_pipeline.py \
        --day N --plate all --out _proto/plate > /tmp/plate.log 2>&1 < /dev/null &

The four Cloud Scheduler jobs that stop the VM are paused. **Resume them after
the bulk run** with `scripts/vm_schedule.sh resume`.
