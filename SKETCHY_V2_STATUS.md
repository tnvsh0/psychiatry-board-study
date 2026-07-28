# Sketchy v2 — where the work stands

Written 2026-07-28. Companion to `SKETCHY_SPEC_V2.md`, which is the design; this
is the build log and the list of what is still broken.

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

**1. The relation enum is too narrow.** The director kept reaching for domain
verbs the enum does not contain: `progresses to`, `inhibits`, `predicts`. Three
table attempts were burned on this and the `main` plate never produced a valid
table. `RELATIONS` currently holds only ADJACENT_TO / BLOCKS / ACTS_ON /
FALLS_FROM / CONTAINED_IN. Either widen it (a PROGRESSES_TO is a real
mechanism) or accept synonyms and normalise them in code. Code-side mapping is
probably right — the director should not have to learn our vocabulary.

**2. Stray numerals defeat the "no other numbers" instruction.** Across four
render tries the stray count went 15 → 0 → 1 → 30. The instruction works
sometimes and not others, so the NUMBER gate's `extra` list cannot be a hard
reject or the build never converges. Options: treat strays as a warning that
annotates the report, or move the painted numbers onto chroma plates (Path B,
already prototyped) so a stray is cosmetic rather than competing.

**3. `figures_even` fails on every try.** The composition probe consistently
reports one figure dominating. Either the prompt is not holding the same-scale
rule, or the probe is miscalibrated. Look at the render before trusting either.

**4. Narration will not stop saying "מסמל" / "אנחנו רואים".** All three
attempts tripped the ban-list. The retry appends the failures, and it still
does it. Consider stripping the phrase in code post-hoc rather than re-asking,
or rewording the instruction as a positive rule rather than a prohibition.

**5. Two pipeline runs overlapped.** `pgrep` showed 7 processes at one point,
and the `main` plate report contradicts the artefacts on disk. The relaunch
must `pkill` and confirm zero processes before starting.

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
