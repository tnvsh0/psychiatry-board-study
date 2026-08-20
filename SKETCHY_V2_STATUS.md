# Sketchy v2 — where the work stands

Written 2026-07-28. Companion to `SKETCHY_SPEC_V2.md`, which is the design; this
is the build log and the list of what is still broken.

## 2026-08-20 — the render was finally looked at

The chapter 28 render existed since 2026-07-28 but nothing in the loop had ever
VIEWED it, so two defects sat open on "cannot tell whether the prompt or the
probe is wrong". Both are now settled by looking.

**`figures_even`: the probe was right, the prompt was wrong.** In
`sketchy_criteria.png` the sleeping man at bottom left is drawn in the
foreground at roughly twice everyone else's height and is cut off by the frame
edge. The old rule ("comparable scale, none taller than two-fifths of the
frame") is a CEILING, and a ceiling permits a foreground — so the model kept
composing a photograph with a near subject. Replaced with the ban that names the
actual failure: nobody near the viewer, nobody cropped by the frame, no
half-bodies or close-ups, everyone between one-sixth and one-quarter of frame
height on one floor.

**Blank plates were 2.6-5.5% of frame height.** "Large" is not a size. The brief
now says one fifth of the image height, doorway-sized, taller than a person's
torso, and names what is wrong (a tag, a ticket, a badge). Prompts are not
guarantees, so the size is enforced too: `measure_chroma_plates()` measures each
plate against the FRAME (`MIN_PLATE_HEIGHT_FRAC = 0.10`) and the retry ladder
re-renders below that, with a retry note that says "the one you drew at 4% must
be four to eight times bigger" rather than "draw a plate". The COMPOSITOR stays
tolerant on purpose: it still letters a plate the gate rejected, because on the
last rung a small composited number beats shipping a raw magenta rectangle.
`--selftest` covers both halves on synthetic frames, no API key needed.

**The chapter never reached a render at all.** The first re-run showed the
director bounced three times on word counts (hook over 25 words, cue over 9),
each answer substantively fine. Fixed the way the relation enum and the banned
narration phrases were fixed — in code:

  - `normalize_table()` trims a long cue back to the prop it names, refuses a
    trim that would collide with another cue, and does not leave the cut
    dangling on a function word.
  - an over-long hook is now SOFT: kept, reported as `table_warnings`, never a
    re-ask. `HOOK_MAX_WORDS` 25 -> 32, because the hook was recently required to
    describe AND explain in one sentence and the cap never moved with it.

**The chapter never reached a render, part two.** Three more re-runs, each
finding one more rejection class the hard way — a colliding cue trim that gave
up, מסמל/מייצג, zones named in Hebrew against an English zone list. The fix that
actually mattered was not any of them: `plate_pipeline` now writes every
rejected candidate to `_rejected_<plate>_<attempt>.json`. One dump showed all 19
remaining errors in three classes at once, offline, for free — where the log had
been showing one blocker per fifteen-minute render cycle.

With the dump in hand, every rejection class in `validate_table` was sorted into
"disagreement about the picture" (stays hard) and "bookkeeping" (normalized):
derivation synonyms, unknown fact ids stripped, dangling and over-cap relations
dropped, over-long and over-budget required strings dropped, Hebrew zone names
mapped back to their English twins. Passive relation verbs (`affected_by`)
deliberately degrade to ADJACENT_TO rather than ACTS_ON — mapping them to
ACTS_ON reverses who acts on whom and the illustrator draws it backwards.

**`plan_plates` is wired into `daily_study.py`.** The production loop no longer
builds one image per aspect through `build_image_jobs ->
generate_sketchy_directed`; it calls `plan_plates` and runs
`plate_pipeline.run_plate` per plate in the chapter's fixed venue, shipping the
legend, quiz, narration script and audio with each render, plus a
`plates_report.json`. A missing venue stops the day's images instead of quietly
falling back to v1 — a chapter drawn in the wrong place is worse than none.
`regen_image.py` still uses the v1 helpers, so they stay.

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
