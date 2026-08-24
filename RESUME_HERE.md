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

## The one decision waiting on the owner

The plate renders run on Nano Banana Pro at 2K: **~$227 for 141 chapters**.
`SKETCHY_LADDER_MODEL` renders the draft rungs on Flash and only the shipped
frame on Pro: **~$85**. It is built and switched OFF, because it changes what
the learner actually looks at. Nothing else is blocked on this — the bulk run
works either way.

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
