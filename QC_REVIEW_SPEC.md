# QC review — specification v2

v1 (2026-09-18 morning) was reviewed adversarially from four lenses — grounding
validity, fix policy, operations, completeness — with measurements on the real books
and releases. v2 adopts the 36 changes the review produced (14 blocking) and the
owner's decisions of the same day. The review's scripts and numbers are in the session
scratchpad `spec_review/`; the headline measurements are quoted where they decide a rule.

**Owner decision, 2026-09-18:** no new chapters until every released chapter has been
judged against the textbooks and its wrong material handled. After the backlog, the
same mechanism is a fail-closed gate on every new chapter, before publication.

**Owner decisions on policy, 2026-09-18:**

1. He studies in the **app** (Streamlit). Every correction and mark must appear there.
2. **Note first, rewrite later.** A verified error is shown at once as a note with the
   textbook quote and page. Rewriting an item starts only after calibration shows a
   verified false-positive rate of 1–2% or lower.
3. **Conflicts are shown, not resolved.** When books disagree with each other, or a book
   looks outdated against newer literature, show both sources with pages and flag it to
   him. Never auto-fix.
4. **סיכומי כרטיסיות שלב ב is a legitimate source**, and anything grounded in it must
   say so explicitly.

## 1. Why

Nothing in the pipeline has ever read the materials for correctness. What a sample of 17
released chapters found, **after re-adjudication against the full text of the books**:

- Quiz keys and rationales: of 9 suspected errors, **3 are contradicted by the books**
  (042 Q2 catatonia, DSM p.135 "three (or more)"; 038 Q9 CAMS arms, Dulcan p.330-331;
  043 Q1 OCD insight, DSM p.266, with an ambiguous stem). **4 are supported by the books**
  (018 Q3 MTA height, StahlKids p.3 — possibly outdated vs Swanson 2017, owner review;
  036 Q10 carbamazepine–lithium, Dulcan p.771; 019 atomoxetine onset — a **source
  conflict**, StahlKids p.69 "2–8 weeks" vs p.74 "8–12 weeks"; 042 Q5 buspirone, Dulcan
  p.235 vs p.332). 2 are item-logic or lint problems (035 Q1, 042 Q3). The first count
  of "9 errors" came from a reviewer using its own knowledge — the failure this design
  exists to prevent.
- Key position A 98 / B 31 / C 3 / D 0 over 132 single-answer items; always-A scores 74%.
  The key is also the unique longest option in 61%.
- 17 of 42 items in releases since 2026-09-14 are types the app grades wrongly or not at
  all; multiple_select is graded with one radio button.
- The page gate reads only the first page after a book name; `(StahlNeuro p. 289, 902)`
  passes in a 623-page book.
- Hygiene: chatbot sign-offs in 8/12 guides, escaped LaTeX in 8/14, leaked labels,
  NotebookLM `[n]` markers, Arabic letters fused into Hebrew words.

## 2. Principles

1. **A judge must prove it read the sources and the material.** Measured 2026-09-18:
   an agy run (v1.2.5) that never received its PDF returned status SUCCESS with confident
   JSON, an invented "verbatim" quote, and ruled a card **supported**. On one version
   (1.2.6), backslash and forward-slash paths both attached 4/4 — the failure was agy's,
   not the path's. So ingestion is proven on every call, never assumed (§6.3).
2. **Verbatim on both sides, verified in code.** Every claim of support or contradiction
   carries a quote from the material and a quote from the source; code finds both.
3. **Fail closed.** No verified verdict → `unjudged`, never approved. An open verified
   error, conflict or unverified-high → `needs_owner`, and the queue does not resume.
4. **Mark, never hide.** The learner always sees the item, with the textbook quote.
5. **Never lose an asset.** Originals are snapshotted and committed before any change;
   uploads never use `--clobber`.

## 3. Scope

Judged in v1: `study_guide.md`, `quiz.json`, `flashcards.json`, `facts.json`, and the
released plate **legends** (`sketchy_*.txt`), which the app renders and which carry
"numbers for the exam". Later, once: the practice-question keys (`weekly_questions.json`,
`question_bank.json`).

Not judged in v1: video (measure 3 first — v2; badge says "video: not checked"),
`*.quiz.md` and narration (the app does not render them), NotebookLM Studio copies (he
studies in the app; noted in the report). No new plates.

Chapters: the tags computed from `schedule.json` with the app's `day_tag`/`slugify` that
have a published release. The orphan `study-016-adhd-core` is listed for the owner and
excluded. 58, 62, 68 and 72 (failed or interrupted) enter only through the gate.

## 4. Engine

- agy only; `gemini-3.1-pro-high` passed explicitly on every call. No paid API.
- `AGY_CLI_DISABLE_AUTO_UPDATE=1` for every agy subprocess; the agy version is recorded
  on every call; if it changes mid-run, run the atomoxetine known-answer probe (StahlKids
  p.69, "2–8 weeks") and stop if it fails.
- One agy call at a time. Local preparation for the next chapter may run in parallel.
  Concurrency 2 only after calibration shows no 429 at 1.
- `--print-timeout 25m`, subprocess timeout 2400 s, and on timeout kill the whole process
  tree (`taskkill /T /F`).
- **Quota** is detected only from the JSON envelope's status/error, stderr, and a
  `RESOURCE_EXHAUSTED` line in that run's `~/.gemini/antigravity-cli/log/cli-*.log` —
  never from a "429" substring in stdout or the reply (the 042 quiz cites "StahlNeuro
  p. 429"). Parse both `Resets in XhYmZs` and `reset after XhYmZs`. On a quota stop:
  persist `paused_quota{resume_after, step}`, one ntfy with the reset time, exit. No
  in-process sleep over 30 minutes. A quota stop never consumes a content retry.
- The material is never inline. Rules and schema are inline and under 10,000 characters;
  assert `len(subprocess.list2cmdline(argv)) < 30000` before launching.
- After validation, delete `brain/<cid>` and `conversations/<cid>.db` (the disk is 91%
  full; agy's store is 2.9 GB).

## 5. Modules and interfaces

Flat `scripts/`. Every module importable and testable offline (agy and gh faked).

### 5.1 `qc_books.py` — built 2026-09-18

- DSM-5-TR and Dulcan are reflowed: printed page N = the text between inline page-number
  lines N and the next, found as the longest chain of standalone number lines increasing
  in steps ≤ 10; unnumbered pages fold into the previous one. Measured: DSM 1049 printed
  pages, 0 gaps; Dulcan 1109, 47 unnumbered folded.
- StahlNeuro, StahlKids: edge folio + constant offset 15 (agreement 98.5%, 99.6%).
  Syllabus: offset −1 (98.3%). Kartisiot (סיכומי כרטיסיות שלב ב): printed = PDF page,
  offset −1. Synopsis: no printed pages — per-chapter slices.
- API: `book(key)` → `page_text(n)`, `pdf_pages(n) -> list[int]`, `has_page(n)`,
  `max_printed`, `pages()`; `normalize_label(raw)` (bare "Stahl" → `"Stahl?"`);
  `search(pattern, keys)`; `synopsis_slices()`.
- Text cached in `_qc/cache/books/` (gitignored — it is the books' text).
- `--check` runs known-page probes (StahlKids 69/74, DSM 135, Dulcan 330/771); all pass.

### 5.2 `qc_norm.py` — normalization and verification

`norm(s)`: NFKC; delete U+00AD, U+200E/F, U+2066–2069; strip `$`, `\(`, `\)`;
`\text{x}`→x, `\%`→%, drop other LaTeX commands; strip markdown (`**`, `__`, backticks,
leading `#`, table `|`); casefold; any run of non-alphanumerics **between two digits**
becomes one boundary `|` (so "2–8" ≠ "28"); delete every other non-alphanumeric (spaces,
hyphens, dashes, quotes, bullets, geresh, gershayim, maqaf).

- `norm_map(s) -> (normalized, offsets)` mapping each normalized char to its raw index,
  for locating spans in raw text.
- `find_quote(quote, text, min_len=30)` → `exact` | `fuzzy` (in-order token match ≥ 0.9,
  for two-column pages) | `None`, with the raw span. A quote with an ellipsis is split and
  its pieces must match in order. A source quote is matched against printed pages
  p−1..p+1 concatenated, page-number lines removed.
- `locked_values(s)` → numbers, ranges, doses, durations, percentages, criteria counts,
  with kind and normalized value; used to check that a finding disputes a real value.
- Tests: the 14 must-match and 4 must-fail cases of `spec_review/norm2.py`, plus Hebrew
  with `$…$`, `**`, maqaf, geresh; "2–8" vs "28" and "2-12".

### 5.3 `qc_cite.py` — citations

- Every citation in every unit: `(DSM p.347)`, `(Dulcan ch.7 p.412)`, **every** page in a
  list (`StahlKids p.141, p.243, p.711`), ranges `pp. 12-15`, Hebrew `עמ' 506`, table
  cells under a column that names a book, `Synopsis ch.N`, `Kaplan & Sadock p.33.2`
  (a section label, never a page), `כרטיסיות עמ' N`.
- Years are never pages (`DSM-5-TR (2022)`, `DSM-5 בשנת 2013`).
- Bare "Stahl p.N": resolved by which Stahl book has page N and the chapter's topic;
  otherwise flagged `ambiguous`.
- `parse(text, unit)` → `[{book, printed, section, raw, unit, span}]`; `impossible` =
  page beyond `max_printed` or unmappable.
- Replaces `qc_review.check_page_citations` at `daily_study.py:2075`; `qc_review.py`'s
  source-less judge is retired once `qc_judge` is live.

### 5.4 `qc_lint.py` — deterministic, free

Per chapter, on the released bytes:
- Quiz schema and types (`multiple_choice`, `multiple_select`, `fill_in_the_blank`,
  `short_answer`, untyped, unknown); exactly one key for MC; ≥ 2 options; empty fields;
  cards with empty front/back.
- Key position distribution; key longest by > 30%.
- Impossible citations via `qc_cite`, in every artifact.
- Locked value not on its cited page ±1 and not in the cited book (via `qc_books.search`
  within the key-term window) — passed to the judge as a priority, never a verdict.
- Hygiene: chatbot sign-offs (`האם תרצה`, `אשמח ל`, `מדריך זה כולל את מלוא`), escaped
  LaTeX, leaked labels (`General Instruction Filter`, `Source Summary`), `[n]` markers,
  Arabic letters, English-only stems.
- Legends: "מספרים לבחינה" chips that equal page numbers cited in the same legend.
- Completeness per asset (a missing video is a backfill item, never blocks approval).
- Duplicates within a chapter; cross-chapter near-duplicates (token Jaccard ≥ 0.5 on
  stem/front) as a global report.

### 5.5 `qc_store.py` — releases, snapshots, journal, safe upload

- `chapters()` from `schedule.json` with the app's `day_tag`/`slugify`.
- `fetch(tag)` → always `gh release download` (never `_generated/`), recording each
  asset's sha256 and asset id.
- `snapshot(tag)`: before the first change, every text asset into `_qc/orig/<tag>/`,
  verified (non-empty, parses), sha256 + asset id recorded, committed by explicit path.
- Journal per chapter: `fetched → linted → bundle_built → judged_A → judged_B → judged_C
  → fixes_applied → rejudged → upload_pending → uploaded → verdict`, atomic writes
  (tmp + `os.replace`), resume from the last completed step. Run states: `in_progress`,
  `paused_quota`, `upload_pending`, `stale` (a live sha256 differs from the verdict's).
- **Safe upload** (never `--clobber`): (1) confirm the live asset still has the judged
  sha256, else `stale`; (2) upload as `<name>.qc-<sha8>`, download back, compare sha256;
  (3) rename the live asset to `<name>.pre-qc-<date>` (`gh api -X PATCH
  repos/{o}/{r}/releases/assets/{id} -f name=…`), rename the temp to `<name>`, verify;
  (4) journal each stage as `upload_pending`; resume finishes the swap or rolls back.
- `qc_revert.py <tag> [--fix <id> | --all]`: rebuild from `_qc/orig` plus the fixes still
  active, upload the same way, mark the errata `withdrawn`.
- Keep the machine awake (`SetThreadExecutionState`); reuse `bulk_run.wait_for_network`.
  Circuit breaker: 3 consecutive chapters with a failed canary or parse → STOP + ntfy.

### 5.6 `agy_attach.py` — built; upgrades

- Each call in a fresh directory **outside the repo** (`%TEMP%/qc/<tag>/<call-id>/`)
  holding exactly `bundle.pdf` and `artifacts.md`; cwd is that directory; `@` refs point
  into it (absolute). Never edit agy's global settings.
- After each call, required: (a) a file under `brain/<cid>/.tempmediaStorage/` whose
  sha256 equals `bundle.pdf`'s; (b) the tool steps in `conversations/<cid>.db` contain no
  call other than viewing the two files (no run_command, write_to_file, find_by_name,
  list_dir …). Either failure discards the verdict and retries.
- Quota, auto-update, timeouts and process-tree kill as in §4. `db_bytes` and tokens are
  logged, never gates.
- `validate(data)` stays the hook where the judge's canary and coverage checks run.

### 5.7 `qc_sources.py` — one bundle per call

- Pages for a chapter: every cited printed page ±1; the reading-guide ranges
  (`reading_guide.json`); the chapter's Synopsis slices; and **full-text search hits**:
  for each claim's locked values, every printed page of every book where the value
  occurs within the claim's key-term window (the drug, disorder or trial named on the
  same page). A bare value match is not enough (StahlKids repeats "may take 2–8 weeks"
  on 12 pages). Every page carries its reason: `cited | guide | search | synopsis`.
- **Never drop pages.** When a chapter exceeds the per-call page limit (measured in
  calibration: 120 / 180 / 250), split into several calls — first by book, then by unit
  ranges — each with its own bundle and canary. A unit is judged in the call that holds
  its cited pages and its search hits.
- `bundle.pdf` merges the call's pages (for reflowed books, every PDF page a printed page
  touches) behind an index page (bundle page → book, printed pages). Every page is
  stamped `<book> p.<printed page(s)> · <code>`, code = 6 random base32 characters, fresh
  per call, stored only in the manifest. Kartisiot pages are stamped with its Hebrew name.
- `artifacts.md`: one block per unit — `### Q<n>`, `### C<n>`, `### G:<section heading>`,
  `### F:<facts key>`, `### L:<legend file>:<row>` — each holding the unit's text verbatim
  from the released asset, and one random nonce line at a random position in its last
  10%. Quiz blocks include type, stem, every option with its key flag and rationale, and
  the hint.
- Manifest: each book file's sha256, printed → PDF pages, reasons, codes, the nonce.
  Gitignored with the bundle (`_qc/src/`, `_qc/work/`).

### 5.8 `qc_judge.py` — the judge

Calls per chapter (split further when the page limit requires):
- **A** — quiz + cards. Per type: multiple_choice — a verdict on the key and on each
  distractor; multiple_select — per option; fill_in_the_blank — bestAnswer, each accepted
  answer correct and specific, the stem has one answer; short_answer — modelAnswer and
  requiredAttributes correct, every listed misconception actually wrong; every type — the
  hint is correct and does not give the answer away. Cards: the back is correct; one fact
  per card; on topic.
- **B** — study guide + facts, per section: clinical errors; locked values against the
  source; internal contradictions; content that belongs to another chapter.
- **C** — legends, per row.
- Synopsis-heavy chapters (≥ 20% of book citations, or Synopsis as the only textbook —
  013, 070) judge the Synopsis in its own call(s).

Rules (ported from `psychiatry-book-podcasts/scripts/qc_book_review.py:36-121`):
- The bundle is the ground truth in both directions. The judge's own knowledge never
  penalizes.
- Locked values: numbers, doses, durations, percentages, rates, effect sizes, criteria
  counts and thresholds, trial names and arms, drug names in a claim.
- Not a discrepancy: paraphrase, Hebrew translation, pedagogical framing, mnemonics, a
  correct fact the source states on another bundle page, missing coverage, formatting.
- Severity: `high` = a learner acting on it would be clinically misled or would answer a
  board question wrong; `medium` = inaccurate but not decisive; `low` = style.
- When Kartisiot is the source, the finding and any note say so by name.

Output schema:

    {canary: [{page, code}], nonce,
     coverage: [{unit, status: supported|contradicted|unsupported|source_conflict|not_checkable,
                 echo, evidence_code?, source_quote?}],
     findings: [{id, unit, kind: contradicted|unsupported|source_conflict, severity, category,
                 said, evidence_code, source_quote, second_evidence_code?, second_quote?,
                 problem, correction}]}   (findings ≤ 40; coverage uncapped)

Verification in code, all of it local and free:
1. **Canary**: the prompt names k ≥ 4 bundle pages by (book, printed page) — at least one
   per book and one in the last 10% — and every returned code must match; the nonce must
   match. Any miss discards the verdict; retry (max 3 content attempts), then `unjudged`.
2. **Coverage**: exactly one row per unit in artifacts.md; each `echo` (≥ 30 normalized
   characters, or the whole unit) occurs in its unit. Missing or unmatched → discard,
   retry, then `unjudged`. Every `supported` row on a locked value carries a source quote
   and evidence code, and all of them are verified. A row that fails counts as unchecked.
   Approval requires unchecked locked-value units ≤ a threshold set in calibration
   (start: 0).
3. **Findings** by kind:
   - `contradicted`: source_quote verified on the page of its evidence code; for a locked
     value, the disputed value is in `said`, a different value of the same type is in
     `source_quote`, and `correction` uses the source's value. A finding whose quote holds
     the same value as `said` is dropped. The recorded page is where code found the quote.
     Only `contradicted` may be high/medium and reach `qc_fix`.
   - `unsupported`: code searches the full text of every book for the value within the
     key-term window. Found → finding dropped, page logged as evidence. Not found → owner
     queue; never auto-fixed.
   - `source_conflict`: two verified quotes with different values → owner queue; both
     quotes shown; never auto-fixed.
   - Anything failing verification is `unverified`: never acted on, never shown. An
     unverified finding claimed `high` puts the chapter in `needs_owner`.
4. Dedup; drop findings whose `said` and `source_quote` say the same thing (port
   `_clean_discrepancies`).

### 5.9 `qc_fix.py` — two tiers

Acts only on verified `contradicted` findings of severity high/medium.

- **Before any change**, every quiz item and card gets a stable `id` (sha1 of its original
  stem or front); `fixes.json` references ids, never indexes.
- **Tier 1 — always, deterministic, no LLM:** embed in the item, card or legend row
  `qc: {kind: content, date, was, source: {book, printed_page, quote}, severity,
  status: active|withdrawn}`; in the guide, a footnote next to the span. Nothing is
  rewritten. For `source_conflict`, both quotes. For Kartisiot, the source is named.
- **Tier 2 — rewrite**, enabled only after calibration shows verified false positives
  ≤ 2% on ≥ 200 source-confirmed-correct units, and only when: (a) the item's original
  value appears neither on its cited page nor within the key-term window anywhere in any
  book (if it does → `source_conflict`, Tier 1 with both quotes); (b) a second
  independent judge call (fresh conversation, same bundle) returns the same corrected
  value; (c) the patch checks pass. During calibration and the first 10 backlog chapters
  every Tier-2 patch waits for the owner.
- Patch checks: the diff touches only the fields named (quiz: stem, options, isCorrect,
  rationale, hint); every number in the patch occurs in the source page or the original
  item; the key contains the source's locked value; the duplicate lint still passes.
  One rewrite call per artifact type per chapter; then **one full-chapter re-judge** (A, B,
  C, canary). At most one retry per finding (≤ 2 rounds). A re-judge finding on a patched
  unit reverts the patch, keeps Tier 1, and marks the unit `needs_owner`.
- Guide spans: located on the normalized text through the offset map; replaced only on
  **exactly one** match inside the anchored section, else Tier 1 + `needs_owner`. Table
  rows: escape `|`, no newlines, column count unchanged. facts.json edited on parsed values.
- After every locked-value correction, search every artifact of the chapter (guide incl.
  high-yield and summary, facts, quiz, cards, legends) and every other chapter for the old
  value next to the same entity; attach every hit to the same finding.
- **Presentation fixes** (kind `presentation`, no app marker): key position — sort options
  by sha1(text), rotate the key onto a planned position drawn from shuffled blocks
  [0,1,2,3] per chapter (idempotent; ≤ 3 keys per position per 10); skip or pin
  all/none-of-the-above. Hygiene clean-up, run **before** judging so spans quote the
  final text. Longest-option rewrites only on items already corrected for content.
- Corrected facts.json is also written to `_generated/day-NNN/facts.json`; plates whose
  source fact changed are marked `stale_plate`; the chapter's video gets a note that it
  may state the uncorrected version.
- Chapters built by older pipelines (001, 003, 008–014, 028, 032) are judged and fixed in
  place like any other. Only missing assets are generated (quiz and cards for 003, 010,
  011; cards for 024; facts for 001), staged locally, judged, then published, with the
  chapter's verified findings passed in as a CORRECTIONS list. Missing Synopsis content
  becomes a separate "Synopsis supplement" asset. A full rebuild needs the owner's
  approval per chapter.

Statuses: `approved`; `fixed`; `needs_owner`; `needs_regeneration`; `unjudged`. A
`needs_owner` chapter does not count as passed for resuming the queue.

### 5.10 `qc_run.py` — orchestrator, errata, notifications

- For each chapter: fetch → snapshot → hygiene → lint → sources → judge A/B/C → Tier 1 →
  (Tier 2 when enabled) → re-judge → safe upload → verdict; journaled per §5.5.
- `_qc/<tag>/verdict.json`: status per asset; input/output sha256 and asset ids; the
  bundle manifest; agy version, model, prompt hash; raw response path; canary requested
  vs returned; coverage counts; findings by kind and verification; fixes with the fixer
  and re-judge conversation ids; card_id old → new; timestamps.
- Each release gets a `qc.json` asset (status, per-asset sha256, errata) — the app reads
  `main` and releases, never this branch.
- `_qc/REPORT.md` in Hebrew, also uploaded where the app can read it.
- ntfy: (1) calibration go/no-go with its numbers and the ETA; (2) an immediate alert on
  any STOP naming the one action needed; (3) a daily digest (n/68, ETA, highs, held);
  (4) per studied chapter: "N תיקונים — מה היה שגוי → הנכון (מקור עמ' N)"; (5) one notice
  that answer order was rebalanced.
- Global pass after the backlog: every fixed `said` → its near-duplicates in other
  chapters are patched consistently or re-judged; pairs logged in the report.
- Committed by explicit path only: `_qc/<tag>/{verdict.json, fixes.json}`, `_qc/orig/`,
  `_qc/REPORT.md`, `_qc/state.json`. Gitignored: `_qc/cache/`, `_qc/src/`, `_qc/work/`,
  any `pages.json`.

### 5.11 App (`app/app.py`, deployed from `main`)

- Grading before or with the backlog: multiple_select all-or-nothing with all keys shown;
  fill_in_the_blank matched after normalization; short_answer self-graded against
  modelAnswer and requiredAttributes (no runtime LLM).
- A mark shows the verified quote and printed page (two for a conflict; the source named
  for Kartisiot). A choice matching the source is not graded wrong against a disputed key.
  A card's note shows on its back. Unverified findings are never shown.
- A card with `qc.kind == content` dated after its state's last_seen (or with no state)
  is due now and shows old → new. QC never writes `flashcard_progress.json` (the app PUTs
  its whole snapshot; the last writer wins).
- A "תיקונים" page lists the errata by chapter: "the released material said", now,
  book, page, quote, severity, date, status.
- Chapter badge: "נבדק מול ספרי הלימוד ב-<date>, N תיקונים", with a per-asset line
  ("וידאו: לא נבדק").
- These changes reach the owner only once merged to `main`; that merge is his decision.

### 5.12 Gate for new chapters (after the backlog)

- daily_study publishes a new chapter as a **draft** release; qc_run makes it public only
  on `approved`/`fixed` (confirm once that `/releases/tags/{tag}` hides drafts).
- Every uploader (daily_study, backfill_audio, backfill_infographics, regen_image,
  backfill_plates) refuses unless the chapter's verdict covers that asset's sha256.
- **One standing exception, owner's decision 2026-09-29: the answer-order rebalance.**
  `qc_fix.rebalance_keys` may be published without a verdict, through
  `scripts/qc_publish_rebalance.py` and nothing else. What forced it: measured over all
  141 released quizzes the key was option A in 864 of the 949 single-answer items (91%,
  D never), so the quiz scored recognition of "pick the first one" and not knowledge —
  and the repair was finished and verified while the judge was quota-locked for five
  days. Why it is safe to carve out and stays narrow: the repair reorders options and
  edits no content, §5.9 already classes key position as a presentation fix needing
  neither an LLM nor the owner, `_qc/orig/` holds the pristine bytes committed under
  §2.5, and the script re-derives the repair and re-verifies every item and option of
  every file at upload time — same key, same option multiset, no other field moved,
  idempotent — refusing any chapter that fails. Published 141/141 on 2026-09-29;
  read back from GitHub the key distribution is A 23 / B 24 / C 21 / D 22 over a
  14-release sample. This exception covers no other fix: hygiene, Tier 1 and Tier 2
  still wait for a verdict.
- daily_notify says "ready" only for `approved`/`fixed`.
- While judging is quota-blocked, the queue pauses.
- New chapters: generation prompt constraint + lint gate against the longest-option bias.

## 6. Calibration, before the full run

- **Gold set** listed by ID, each adjudicated by searching the full text of all books:
  label `contradicted | supported | not_in_source | item_logic | lint`, with book,
  printed page and verbatim quote. Supported-but-possibly-outdated items (MTA height) go
  to the owner list, never auto-fixed. Chapters 017–019, 035–044 are frozen until done.
- **≥ 60 seeded mutants**: single locked-value edits (number, dose, duration, criteria
  count, drug, trial arm, key swap, direction flip) in copies of source-supported units
  across every artifact type (guide, facts, every quiz type, cards, legends) and every
  book, including 005, 056, 070. Code confirms each contradicts its page first.
- **≥ 200 negatives**: source-confirmed-correct units.
- **Negative controls**: a call whose bundle.pdf is missing, and one whose artifacts.md
  is truncated, must be rejected 100%.
- Acceptance: recall on mutants + contradicted gold ≥ 89% overall, reported per artifact
  type and book with Clopper-Pearson intervals; verified false positives ≤ 2% (the gate
  for Tier 2); first-attempt canary pass rate reported (target ≥ 90%) and after retries;
  unverified rate per chapter.
- Also measured: median and p90 durations of full A/B/C calls at real sizes; attempts per
  accepted verdict; pro-high calls before the first 429; the per-call page limit (last-10%
  canary at 120/180/250 pages); bundle bytes per page by book.
- Then a go/no-go to the owner with these numbers and a revised ETA. Backlog estimate
  before calibration: 24–51 h of serial agy time, plus up to ~27 h per quota lockout.

## 7. Order

Calibration chapters first; then the chapters he studies soonest; then the rest
ascending. The content queue stays stopped until every chapter is `approved`/`fixed`.
