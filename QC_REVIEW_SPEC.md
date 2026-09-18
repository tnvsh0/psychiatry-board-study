# QC review — specification

Status: draft for review, 2026-09-18. Owner decision the same day: **no new chapters until
every released chapter has been judged against the textbooks and its wrong material fixed.**
After the backlog passes, the same mechanism becomes a fail-closed gate on every new chapter.

## Why

Nothing in this pipeline has ever read the study materials for correctness. A manual sample
of 17 released chapters found (verified independently):

- 9 wrong answer keys or rationales in 149 quiz items (OCD insight specifier, catatonia,
  CAMS arms, atomoxetine onset, MTA growth, calcium stem).
- Keyed option position A 98 / B 31 / C 3 / D 0 over 132 single-answer items: always
  choosing A scores 74%. The keyed option is also the longest in 84/132 (64%).
- 17 of 42 items in releases since 2026-09-14 are types the app cannot grade.
- Impossible page citations passing the existing gate (it reads only the first page after a
  book name; `(StahlNeuro p. 289, 902)` passes in a 623-page book).
- Hygiene: chatbot sign-offs in 8/12 guides, double-escaped LaTeX in 8/14, leaked internal
  labels, NotebookLM `[n]` markers in facts, Arabic letters fused into Hebrew words.

## The one lesson that shapes everything: a judge must prove it read the source

Measured 2026-09-18 on Stahl C&A printed p.69 (atomoxetine, "full therapeutic benefits may
take 2–8 weeks"):

| attachment reference | agy log | result |
|---|---|---|
| `@C:\...\slice.pdf` (backslashes) | `media=0` — not attached | quoted a sentence **not in the source**, answered "8–12 weeks", ruled the wrong flashcard **supported**. Status SUCCESS, confident JSON. |
| `@slice.pdf` (relative) | `media=0` | said honestly the file was not attached |
| `@C:/.../slice.pdf` (forward slashes) | attached | verbatim quote from p.69, "2–8 weeks" |

A judge that has not read the source does not fail — it certifies errors as correct. The
podcast project published two unjudged episodes this way. Therefore:

1. Attachments are always absolute paths with forward slashes.
2. Every judge call carries a **canary**: we choose a page in the bundle and ask for a
   verbatim quote of a specific span we can locate (e.g. "the first sentence under the
   first heading on the page printed N", or "the sentence containing <token>"). Code
   verifies the quote against the extracted text of that page. No match → the verdict is
   discarded and the call retried; after the retries, the chapter is `unjudged`.
3. Every finding must carry `said` (verbatim from the artifact) and `source_quote` (verbatim
   from the bundle). Code verifies both. A finding that fails verification is logged as
   `unverified` and never acted on. This is the noise filter, and it is also what makes
   every correction traceable to a page.
4. Fail-closed: a chapter is `approved` only with a verified verdict. Crash, empty reply,
   failed canary, quota exhaustion → `unjudged`, never "passed".

## Scope

v1 judges `study_guide.md`, `quiz.json`, `flashcards.json`, `facts.json`.
Video: measure 3 released videos with the same method before building a video judge (v2).
Plates: excluded (separate track, deferred by the owner).
Audio overview / infographics: not produced by the bulk pipeline (SKIP_QUOTA_ARTIFACTS).

## Engine and constraints

- agy only. No paid API (the key is deleted; `gemini_image._guard_paid` blocks it).
- Judge model `gemini-3.1-pro-high`. Flash returns an empty SUCCESS on long instructions
  with attachments (measured 2026-09-16 on the vision gate).
- Prompt inline (never `@prompt.txt`), preamble forbidding shell/tool use, `--output-format
  json`, `--print-timeout 20m`. Parse `response`, tolerate a ```json fence.
- 429 / "exhausted your capacity": parse the stated reset (`agy_image.parse_reset_seconds`),
  wait, resume. Never count a quota stop as a verdict.
- NotebookLM (`notebooklm -p bulk`) is used only for regenerating missing or rebuilt
  artifacts, never for judging.
- The content queue stays stopped until the backlog passes.

## Modules

Flat `scripts/` like the rest of the project. Every module importable and unit-tested
without network.

### `scripts/qc_books.py` — printed page → PDF page, and page text

- Books: `DSM-5-TR`, `Dulcan`, `StahlNeuro` (Stahl Essential 5e), `StahlKids` (Prescriber's
  Guide C&A), `Synopsis` (chapter-only, no printed pages), `Syllabus`.
  Files in `Text BOOKS/` (see `schedule/book_map.json` for names and printed ranges).
- Label normalization for every alias seen in artifacts: `DSM`, `DSM-5`, `DSM-5-TR`;
  `Dulcan`; `StahlNeuro`, `Stahl Essential`, `Stahl p.` (ambiguous → resolve by page range
  and chapter context, else flag); `StahlKids`, `Stahl C&A`, `Prescriber's Guide`;
  `Synopsis ch.N`; `Syllabus`, `סילבוס`.
- Folio index per book: scan every PDF page for its printed folio (reuse
  `build_book_map.scan_folios`), then fill gaps by piecewise-linear interpolation between
  found folios, verified monotonic. Cache to `_qc/folios/<book>.json`. Report coverage.
  Dulcan's offset drifts (46 → 113), so never a constant offset.
- `page_text(book, printed) -> str`, `pdf_index(book, printed) -> int | None`,
  `max_printed(book)`.

### `scripts/qc_cite.py` — citation parser

- Extract every citation from guide/quiz/cards/facts: `(DSM p.347)`, `(Dulcan ch.7 p.412)`,
  `StahlKids p.141, p.243, p.711` (EVERY page in a list), ranges `pp. 12-15`, Hebrew
  `עמ' 506`, table cells whose column header names a book, `Synopsis ch.N`.
- Must NOT treat years as pages (`DSM-5-TR (2022)`, `DSM-5 בשנת 2013`).
- Returns `[{book, printed, raw, where}]` and `impossible` = page beyond `max_printed` or
  unmappable.

### `scripts/qc_sources.py` — per-chapter source bundle

- Pages = every cited page ±1, plus the chapter's reading-guide ranges
  (`schedule/reading_guide.json`, Hebrew free text: `עמ' 3-16, 59-90`), plus the chapter's
  Synopsis slices (`schedule/synopsis_slices/`).
- One PDF per book under `_qc/src/<tag>/<book>.pdf`, plus `pages.json` =
  `{book: [{printed, pdf_index, text}]}` used for all verification.
- Cap per judge call (initial 120 pages; measure). Over the cap: cited pages first, then
  reading-guide pages nearest the chapter's aspects. Log what was dropped.

### `scripts/agy_attach.py` — agy with file attachments

- `ask_with_files(prompt, files, *, model, timeout, retries) -> AgyResult` with
  `text, conversation_id, duration_s, db_bytes, diagnostics`.
- `@` refs: `Path(f).resolve().as_posix()`.
- Records the conversation store size (`~/.gemini/antigravity-cli/conversations/<id>.db`)
  as a second ingestion signal; the canary is the decisive one.
- Retries: empty response, unparseable JSON, tool-permission denial. Quota: wait for reset.

### `scripts/qc_lint.py` — deterministic checks (free)

Per chapter, over local or downloaded assets:

- Schema: quiz item types (`multiple_choice`, `multiple_select`, `fill_in_the_blank`,
  `short_answer`, unknown); exactly one correct for MC; ≥2 options; empty text;
  flashcards with empty front/back.
- Key position distribution and longest-option-is-key rate (chapter and global).
- Impossible citations via `qc_cite` (all artifacts, not just the guide).
- Hygiene: chatbot sign-offs (`האם תרצה`, `אשמח ל`, completeness claims like `מדריך זה
  כולל את מלוא`), double-escaped LaTeX, leaked labels (`General Instruction Filter`,
  `Source Summary`), NotebookLM `[n]` markers, Arabic letters, English-only stems share.
- Completeness: required assets present in the release (`study_guide.md`, `quiz.json`,
  `flashcards.json`, `facts.json`, `video.mp4`).
- Within-chapter duplicate items.

### `scripts/qc_judge.py` — the LLM judge

Two calls per chapter, both with the chapter's source bundle attached:

- **A — quiz + flashcards.** Items numbered `Q1..`, `C1..`. The guide is included as
  reference text (not judged here) so contradictions between artifacts surface.
  Per item: is the keyed answer correct per the source; is any distractor also correct;
  does the rationale support the key and cite the right page; is the card's back correct;
  one fact per card; is the item on this chapter's topic.
- **B — study guide + facts.** Findings anchored to a section heading and a verbatim span.
  Clinical errors; numbers, doses, durations, percentages, criteria counts vs the source;
  internal contradictions; content that belongs to another chapter.

Rules ported from `psychiatry-book-podcasts/scripts/qc_book_review.py:36-121`, adapted to
text:

- The source bundle is the ground truth in both directions. The judge's own knowledge may
  not be used to penalize. Where the source is silent, a statement that is correct and
  consistent is at most `low`; an unsupported **locked value** is `high`.
- Locked values: numbers, doses, durations, percentages, rates, effect sizes, criteria
  counts and thresholds, study/trial names and their arms, drug names in a claim.
- Not a discrepancy: paraphrase, Hebrew translation of an English term, pedagogical framing,
  mnemonics, a correct fact the source states on a different bundle page, missing coverage
  (for quiz/cards), formatting.
- Severity: `high` = a learner acting on it would be clinically misled or would answer a
  board question wrong; `medium` = inaccurate but not decisive (wrong page, imprecise
  wording that changes meaning slightly, contradiction between two artifacts where one is
  right); `low` = style, clarity.
- Output schema: `{canary: {quote}, findings: [{id, item, severity, category, said,
  source_book, source_printed_page, source_quote, problem, correction}]}` — at most 40
  findings, most severe first.

Verification in code (after every call):

1. canary quote ∈ canary page text (normalized: whitespace, bullets, dashes, quotes) —
   else discard the whole verdict, retry (max 3), then `unjudged`.
2. per finding: `said` ⊂ artifact text; `source_quote` ⊂ bundle text of the named page ±1 —
   else `unverified`.
3. dedup; drop findings whose `said` and `source_quote` say the same thing (port
   `_clean_discrepancies`).

### `scripts/qc_fix.py` — corrections

Acts only on verified findings of severity `high` or `medium`.

- **Quiz item**: agy text call (no attachment needed: the verified `source_quote` and page
  are in the prompt) rewrites that one item in the same JSON shape — corrected key, options,
  rationale with the right page. Then a mini judge call (bundle attached, canary) on the
  patched items only. Accept only if clean.
- **Flashcard**: patch `back` only, keep `front` byte-identical so `sr_engine.card_id`
  (sha1 of chapter + front) and the owner's review history survive. If the `front` itself is
  wrong, replace the card and log the identity change.
- **Guide / facts**: replace the verbatim `said` span with a grounded correction; re-judge
  the affected section (mini call B on that section).
- **Key position bias**: deterministic seeded permutation of options per item (seed = tag +
  item index) so keys are uniform across A–D. Check rationales for letter references first.
- **Longest-option bias**: items where the key is longest by >30% → agy rewrites distractors
  to comparable length; judge verifies no distractor became correct.
- **Hygiene**: deterministic clean-up (sign-offs, LaTeX, leaked labels, `[n]`), logged.
- **Missing assets / chapters built by older pipelines** (001, 028, the Synopsis-mapped
  chapters released before 2026-09-01): `needs_regeneration` → rebuilt through the normal
  pipeline, then judged like any other chapter.
- Unsupported quiz item types: kept; the app is fixed to render and grade them.
- Every change → `_qc/<tag>/fixes.json` with before, after, finding id, source quote, page.

### `scripts/qc_run.py` — orchestrator

- Chapters: every released `study-NNN-*` (and later, every new chapter).
- Per chapter: fetch assets (local `_generated/day-NNN` or `gh release download`) →
  lint → sources → judge A, B → fix → re-judge → upload changed assets
  (`gh release upload <tag> <file> --clobber`) → verdict.
- `_qc/<tag>/verdict.json`: `status ∈ {approved, fixed, needs_regeneration, unjudged}`,
  lint counts, findings (verified/unverified), fixes, judge diagnostics (model, attempts,
  duration, conversation id, db bytes, canary).
- Resumable `_qc/state.json`; stops cleanly on quota and resumes.
- Report `_qc/REPORT.md` in Hebrew, committed; one ntfy summary per run.

### App

- Render and grade `multiple_select`, `fill_in_the_blank`, `short_answer`.
- Per chapter QC badge; per item "corrected on <date>, source p.N" marker. Mark, never hide.

### Gate for new chapters (after the backlog)

`bulk_run`: a chapter counts as done only when released **and** `approved`/`fixed`.
Judged immediately after `daily_study`; `unjudged` stays on a retry list (fail-closed).

## Calibration before the full run

Gold set: the 9 known wrong items, each adjudicated by us against the extracted page text
(a deterministic lookup, not the judge), plus 60 items from the same chapters confirmed
correct against the page text. Acceptance before running all chapters:

- canary pass rate ≥ 95% of calls (after retries);
- recall on the known errors ≥ 8/9 as verified `high`/`medium`;
- verified false positives ≤ 5% of judged items;
- median call duration and quota use recorded, full-run time estimated.

## Order of the backlog

Chapters the owner studies soonest come first, then already-studied ones: this week's
chapters, then the rest ascending. The calibration chapters (017-019, 035-044) run first.
