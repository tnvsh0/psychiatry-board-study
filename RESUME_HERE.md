# Where this stands — 2026-08-21

Written when the weekly quota ran low, to make the state legible without
re-deriving it. The exam is **2026-11-25**, about 96 days out.

## 2026-08-25 — stopped here, waiting on NotebookLM

The bulk run is HALTED and nothing is running. 11 chapters are released (1, 3-11)
and the site is live at
https://psychiatry-course-690391711540.me-west1.run.app

**The blocker is not ours.** NotebookLM's chat endpoint returns an empty stream
on every notebook on this account:

    No parseable chunks in streaming chat response (4 lines scanned).
    The response was empty or the API wire format may have changed.

Reproduced with a live session, on notebooks that had answered an hour earlier,
while create / source list / source add-text all kept working. Could be chat
throttling after a heavy day, or the wire format changing — I could not tell
apart, because the decisive test needs the second account and the standing rule
is not to log into it. CLI is already newest (0.8.1).

**To resume tomorrow:**

    notebooklm -p bulk ask "Say OK." --json     # does chat answer again?
    notebooklm -p bulk login                    # if auth expired (NEVER --fresh)
    python scripts/bulk_run.py                  # picks up at chapter 12

If chat is still dead by tomorrow, it is likely a real wire-format change and
the decoder needs checking against live traffic — see docs/rpc-development.md.

Two backfills wait on the same chat endpoint and can run once it is back:

    python scripts/backfill_audio.py --limit 3
    python scripts/backfill_infographics.py --limit 2

## Render cost — decided 2026-08-23, no longer open

`SKETCHY_LADDER_MODEL` is ON: draft rungs on Flash, and the frame that ships
re-rendered on Pro. About $85 across 141 chapters instead of about $227.

The first version of this shipped whatever the ladder settled on, so a Flash
draft that scored clean on try 1 went out as the final image — the owner spotted
that chapter 6 looked better than chapter 5 without being told chapter 6 was the
only one that had reached Pro. The final pass is now unconditional and
plates_report records which model drew each frame.

    export SKETCHY_LADDER_MODEL=gemini-3.1-flash-image-preview   # if wanted

## To restart the bulk run

    notebooklm -p bulk login --fresh          # only if auth was revoked again
    python scripts/gemini_health.py           # refuses to start in a 503 storm
    NOTEBOOKLM_PROFILE=bulk python scripts/bulk_run.py

It preflights auth and Gemini, stops on an auth failure or three consecutive
chapter failures, sweeps each chapter's local files once its release exists,
and skips the two quota-capped artifacts. Podcasts come afterwards:

    NOTEBOOKLM_PROFILE=bulk python scripts/backfill_audio.py --limit 3

## What is measured, not guessed

| | |
|---|---|
| Chapter wall time | 33m (2 plates, 503 storm) / 1h49m (3 plates, 60m of it dead wait now cut to 20m) |
| Printed page ranges | DSM 1050, Dulcan 1156, Stahl Essential 623, Stahl C&A 502, Syllabus 59 |
| NotebookLM artifact cap | audio and infographics throttle after ~2 chapters/day; video, quiz, flashcards do not |
| Venue collisions | 6 found by head-noun matching, down to 4 after the merge |
| Chapters needing venue work | 47 of 144 |

## Chapter 29 is the proving run, and it is incomplete on purpose

Released at `study-029-ssris-deep-dive` with the study guide, facts, two of
three plates, their legends, quizzes and narration. Missing: plate 3 (lost to a
relation-enum bug, since fixed), quiz, flashcards, video (20-minute wait, ids
now recorded), audio and infographics (daily cap).

Its study guide is the quality bar: 26,709 characters, 177 page citations, none
of them impossible. The first attempt was 3,693 characters with one citation
and announced a file that did not exist.

## What is left, in order

1. **47 venues** need a second round — 33 the critic flagged, 12 it never
   reviewed (two of its own agents hit a session limit), 2 that lost a category
   collision. Inputs are in `_bulk/venue_redo.json`; the merger that resolves
   collisions across all 144 is `scripts/merge_venue_repair.py`.
2. **Kaplan & Sadock** is switched off in `schedule/drive_books.json`. The
   re-uploaded file downloads fine now, but NotebookLM will not index it — most
   likely over the per-source size limit at ~1500 dense pages, against 502-1156
   for the five that work. Splitting it in two would test that. It is the
   supplementary source, used only where Dulcan has no chapter.
3. **Infographics have no backfill.** The bulk run skips them and nothing fills
   them in, so a chapter built now has its Sketchy plates and no infographic.
   That is a trade made against a November exam, not a cleanup.
4. **QC judge** (`scripts/qc_review.py`) is built and calibrated but has never
   been run across a finished chapter set.

## The podcast feed is live

    https://storage.googleapis.com/psych-study-media/<FEED_SECRET>/feed.xml

`FEED_SECRET` is in `.env`. Two episodes so far. Read-if-you-know-the-name, no
bucket listing — verified: feed 200, audio 206 on a range request, bucket root
401, guessed path 404.

## The pattern worth remembering

Every defect found today was a SILENT one — a path that shrugged and continued
rather than raising. A study guide that was a receipt. A schema that answered
the wrong question for 83 chapters. Page numbers painted as clinical data. A
probe outage scoring as a clean frame. Two thirds of the course's release tags
not matching what the publisher wrote.

None of them would have failed loudly. All of them would have shipped.

## A plate per drug — agreed 2026-08-27, deliberately not started

SketchyMedical is pitched at medical students. The owner is a senior child-and-
adolescent psychiatry resident and wants, for EVERY DRUG rather than every
family: exact receptors, interactions, doses, and CYP effects in both
directions. So each drug gets its own plate on top of the family plate, reusing
the family scene's symbols and venue.

This is not a new scene to learn. It is a ZOOM into one zone of a venue the
reader already holds — chapter 29's PAIR-OX EXPEDITIONS ox statue is already
standing in that travel agency — which is how the corpus works and how a memory
palace works. One drug carries 15-20 facts, which is a well-filled plate; the
same facts inside a family plate get crushed.

Most of the machinery is already here. venues.json carries `adjacent_to` and
`hosts_tokens`, the lexicon fixes symbols course-wide through `symbols_needed`
(the symbol-continuity problem, already solved), and `Plate.track` became
open-ended when the plates split three ways, so a track per drug is the same
mechanism.

The hard part is not the drawing, it is the facts. Today's schema is
family-level (`drug_class.members`); this needs a per-drug schema and
NotebookLM answering at book resolution. **Test it on ONE drug end to end
before committing to the set.**

There are 104 entries in schedule/medications.json. At the old $0.13 an image
that was ~$40 before a single failed attempt, which is why it was never
seriously considered. On the free agy engine it is about three days of quota.
The free engine did not only cut the bill — it moved this from too expensive to
worth doing.

DEFERRED until the plate gates (check_frame: actions_drawn, callouts,
out_of_place) are shown to agree with the owner's own judgement. That
calibration is the batch of chapter 29 plates running now.

## 2026-08-28 — the pipeline is free, and the quota is the constraint

Everything now runs on the Antigravity CLI: the scene director, the renders and
the vision gates. No paid call remains in a chapter. The Gemini project is
sitting on its monthly spending cap, which no longer blocks anything except
narration audio (TTS).

**The gates were calibrated and the owner confirmed their ranking.** Against
eleven audited plates: callouts r=0.95 with the human audit, and r=-0.73
against the human coherence score, meaning the callout count PREDICTS the
judgement rather than merely correlating. out-of-place is weaker at r=0.63.
The owner reviewed chapter 29's four plates ordered by gate score and agreed
with the order. That was the condition for trusting automation, and for
starting the per-drug plates.

**The real constraint is the image quota, and it is much tighter than first
measured.** Consumption over four days: 2, 13, 35, 11 images, then a 103-hour
lockout. So there is a long-horizon cap above the ~14-per-5-hour rolling
window, and the sustained rate is about 15 images a day — not the ~67 inferred
from one window. At roughly 430 plates and ~2 renders each, the course is about
two months of quota against 89 days to the exam. Feasible, with no margin.

**What changed today because of that.** The ladder used to exit only on
`score == 0`, and the mnemonic terms added to the score made zero unreachable
(actions_drawn tops out near 70%), so every plate burned all four rungs. It now
stops at good enough, with each threshold set by what the engine delivers and
by how far each gate has earned trust — see ACTION_TARGET, MISSING_TOLERANCE,
OOP_TOLERANCE in plate_pipeline.py.

**Chapter 26 is released** and is the first chapter built end to end on the free
engine. Two of its four plates scored 1.0 — the best yet, one drawing 13 of 13
actions — and two produced no image because the quota ran out mid-chapter.
Those two need re-rendering.

**The account trap.** `~/.notebooklm/config.json` still says
`default_profile: default`, and `default` holds the personal Google account while the
project's account, the project Google account, lives in the `bulk` profile. Every
command needs `-p bulk`, and every script run needs `NOTEBOOKLM_PROFILE=bulk`,
or it silently uses the wrong account. A login without `-p bulk` also
overwrites the peer projects' session.

**Open decision for the owner:** stay free-only and accept ~2 months, or raise
the spend cap so chapters can fall through to the paid API while agy is locked.
