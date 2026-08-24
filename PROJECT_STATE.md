# Psychiatry Board Study — Project State & Handoff

> **Purpose of this file:** a single, always-up-to-date reference for the whole
> system so work can continue in any new chat/branch without re-reading old
> conversations. Update it as things change.

Last updated: 2026-06-04

---

## 1. Quick Links & Locations (the "where is everything" index)

| What | Where |
|------|-------|
| **GitHub repo** (private) | https://github.com/tnvsh0/psychiatry-board-study |
| **Releases** (one per chapter, holds all artifacts) | https://github.com/tnvsh0/psychiatry-board-study/releases |
| → Day 1 release | https://github.com/tnvsh0/psychiatry-board-study/releases/tag/study-001-psychiatric-examination-of-child-and-adolescent |
| → ADHD release (**stale tag**) | https://github.com/tnvsh0/psychiatry-board-study/releases/tag/study-016-adhd-core — ADHD is now chapter **17**, so the bulk run will publish `study-017-adhd-core` and this tag is orphaned. Delete it once 017 exists. |
| **Streamlit app — LOCAL** | `streamlit run app/app.py` → http://localhost:8501 |
| **Streamlit app — ONLINE (Cloud Run)** | NOT DEPLOYED YET (see §9). Will be a `*.run.app` URL. |
| **Generation VM** | `weekly-review-vm`, zone `me-west1-b`, project `psych-research-agent` |
| → SSH | `gcloud compute ssh weekly-review-vm --zone=me-west1-b --project=psych-research-agent` |
| → repo on VM | `/opt/psychiatry-board-study` (does `git pull` to update) |
| → venv on VM | `/opt/venv/bin/python`, `/opt/venv/bin/notebooklm` |
| → textbook PDFs on VM | `/opt/sources/` (dsm-5-tr, dulcan, stahl-essential, **stahl-pediatric**, kaplan-sadock-synopsis, syllabus) |
| **Smoke-test images (local)** | `_test_outputs/` (e.g. `smoke_adhd_v9_inattention.png`) — gitignored |
| **Textbook PDFs (local)** | `Text BOOKS/` — gitignored |

**Secrets / env:**
- `GEMINI_API_KEY` — in `.env` (local + on VM). Used by `scripts/gemini_image.py`.
- `GH_TOKEN` — GitHub PAT for creating Releases + private-repo reads. In `.env` on VM; locally via `gh auth token`.
- Gemini billing: a Google Cloud "GenAI App Builder" trial credit exists; it covers Cloud Run + Networking (NOT the Gemini API itself — that bills normally, ~$0.04/image).

---

## 2. What This Project Is

An automated study course for the Israeli psychiatry board exam (שלב ב'), child &
adolescent focus. **144 "chapters"** (originally "days"; renamed because each is a
learning unit of variable length), organized into **10 phases** (foundations →
neurodevelopmental → mood/anxiety → … → psychotherapy).

For **each chapter** the pipeline produces:
- 🎙 Audio podcast (Hebrew, deep-dive) — via NotebookLM
- 🎬 Video (whiteboard, Hebrew) — via NotebookLM
- 📖 Study guide (Markdown, Hebrew, **with textbook page references**) — via NotebookLM `ask`
- 🖼 Infographics (info-dense, "instructional" style) — via NotebookLM
- 🎨 **Sketchy-style mnemonic scenes** (the hard part) — via **Gemini (Nano Banana 2)**
- 🃏 Flashcards (JSON) + ❓ Quiz (JSON) — via NotebookLM
- 📊 `facts.json` — structured high-yield facts (source of truth for the images)

Everything is uploaded to a **GitHub Release** (`study-NNN-slug`). A **Streamlit app**
reads the releases and presents each chapter (audio/video players, images +
Hebrew caption, study guide, flippable flashcards with **spaced repetition**, quiz).

---

## 3. Current Status

**Working & validated:**
- Full pipeline runs end-to-end on the VM (NotebookLM auth lives there).
- Day 1 and Day 16 (ADHD) have full releases.
- The **Sketchy image system** went through a long redesign (see §5) and now
  produces good results: belonging scenes, detail-per-fact, isolated facts,
  number-pegs, name-pun/curated worlds, criteria split per cluster.
- Day 16 was re-run "clean" with the latest peg fixes; its release has:
  `sketchy_dsm_criteria_inattention.png`, `sketchy_dsm_criteria_hyperactivity-impulsivity.png`,
  `sketchy_epidemiology.png`, `sketchy_etiology.png` + audio/video/study guide/flashcards/quiz/facts.json.

**EPIDEMIOLOGY IMAGE — the design theory we converged on (READ before touching it).**
The epidemiology image was the hardest problem in the project (~15 iterations, see
`SKETCHY_IMAGE_CHALLENGE.md` for the full log). It has ~6-8 numeric facts and no innate
story, so it kept coming out as either a scattered symbol-board or a cluttered melee.
The working design, now in `build_epidemiology_director()` (`daily_study.py`):
- **SPLIT INTO TWO IMAGES** (like criteria): `sketchy_epidemiology_who.png`
  (prevalence, sex ratio, onset, heritability) and `sketchy_epidemiology_comes_with.png`
  (comorbidity rates + course/persistence). ~3 figures each keeps every image a single
  composed scene while all the data is still carried. Split lives in `build_image_jobs`;
  a `half` arg ("who"/"comes_with") threads through `build_director_instruction`.
- **ONE DOMINANT HERO** (the personified patient) in the centre foreground; every fact
  is a property of the hero or something reacting to them — NOT a democratic ensemble.
- **NUMBERS = DIGIT-SHAPE OBJECTS** (an object whose *shape* looks like the digit — no
  code to learn, the resemblance is the mnemonic). Each figure is rounded to ≤2 digits.
  The objects must be **native to the chapter's world** (0=tyre, 1=cone, 5=tow hook,
  7=ramp in the ADHD driving world) — never imported. So digit-shapes are consistent
  *within* a chapter, vary *between* chapters. Exact percentages still go to the caption.
- **THE WORLD IS IMPOSED** (from `anchor_world`) and supplies BOTH the plot event and the
  digit-objects — this is what keeps the image tied to the disorder (a beach/carnival is
  "orphaned"; the viewer can't tell which disorder it is).
- **BINDING (1:1):** each figure's digit-shapes are carried by the element that
  represents its concept (family carries heritability, each companion carries its own
  rate) so the encoding is unambiguous; no object serves two figures.
- **TWO TEXT REGIMES** (`gemini_image.py` `style_prefix(text_policy=...)`): epidemiology
  uses `"environment"` — it bans labels that say what an element *represents*
  ("DEPRESSION", "WAITING BOYS") but ALLOWS ordinary world signage a place would carry
  anyway ("DRIVING SCHOOL" on the building, "L" plate). Test: "would this text be there
  if nobody were being taught?" All other aspects keep `"mnemonic"` (big legible labels).
- **ONE MOMENT** per half (`EPI_HALVES[*]["moment"]`): everything reacts to the hero in a
  single instant (a test / a pile-up), never stations spread across the frame.
Day 16's two epidemiology halves are the reference for "good." Etiology/criteria are
unchanged (they already work) and still use the generic `build_director_instruction`.

**Latest quality pass (image fidelity — this session):**
- **Image model → Nano Banana Pro by default** (`gemini-3-pro-image-preview`),
  overridable via env `SKETCHY_IMAGE_MODEL` (set `gemini-3.1-flash-image-preview`
  for the cheap Flash model w/ ~500/day free tier). Pro = best text rendering +
  instruction-following — directly helps labels + criteria completeness.
- **Caption fidelity (two-stage):** caption now takes a literal VISUAL INVENTORY
  of the rendered image first, then narrates ONLY what is actually there — no more
  describing intended-but-undrawn elements. (`gemini_image.py`)
- **Criteria completeness GUARANTEE:** a criteria image must contain a distinct
  visual for EVERY criterion. Director now demands one hook per criterion (density
  > cleanliness for criteria); `generate_sketchy_directed` runs a coverage-checked
  retry loop (≤3×, keeps the most-complete render) via `check_coverage`, wired by
  `required_items` for criteria jobs.
- **Comorbidity redesign:** ONE conjoined-twin pair + the comorbid disorders as
  worn/held/surrounding symbol-objects (NOT extra twin bodies). (`symbols.json`
  `epi_comorbidity` + the epidemiology structure note.)
- **Per-aspect SCOPED infographics:** each NotebookLM infographic is fed its OWN
  facts.json slice as a dedicated source and generated with
  `generate infographic -s <id>` — no cross-aspect bleeding/repetition.

**Pending (the next big steps):**
1. **Verify Day 16 clean set** visually (open the release assets or the Streamlit app).
2. **Full bulk run** — regenerate ALL 144 chapters on Nano Banana Pro (~$0.13/img,
   ~$67 total). **STRATEGY: spread across months** via `--bulk --start-from/--end-at
   --resume` (~12-18 chapters/month) so the Google AI Pro **$10/month Cloud credits**
   absorb the cost (≈ free). Test ONE chapter (day 16) first. (Confirm the $10/mo
   credits apply to the same billing account the server's `GEMINI_API_KEY` uses.)
3. **Deploy Streamlit to Cloud Run** (online). Dockerfile + .gcloudignore are ready (§9).

**Latest:** image-fidelity quality pass (Pro model, two-stage caption, criteria
completeness loop, comorbidity, scoped infographics). See `git log` on `main`.

---

## 4. Architecture & Pipeline

```
scripts/daily_study.py  --day N   (or --bulk)
   │  (runs on the VM; calls the notebooklm CLI + Gemini)
   ├─ 1. find/create per-day NotebookLM notebook
   ├─ 2. upload 6 textbook PDFs to it  (so study guide can cite pages)
   ├─ 3. extract a focused .md from the MAIN notebook → add as source
   ├─ 4. start NotebookLM artifacts (async): audio, video, infographics, flashcards, quiz
   ├─ 5. ask_study_guide()  → study_guide.md (with page refs)
   ├─ 6. extract_facts()    → facts.json  (STRICT JSON: criteria clusters, epidemiology, etiology, treatment, high_yield)
   ├─ 7. Sketchy images via Gemini  (see §5 — the scene-director pipeline)
   ├─ 8. wait for + download NotebookLM artifacts
   └─ 9. upload everything to a GitHub Release  study-NNN-slug
```

**Key design choice:** NotebookLM does the *clinical* artifacts (it has the
textbooks). Gemini does the *Sketchy mnemonic images* (NotebookLM can only make
template infographics, not free-form scenes). `facts.json` is the bridge —
extracted once, then each image is built from its slice.

---

## 5. The Sketchy Design System (the hard-won knowledge — READ THIS)

Real SketchyMedical reference is in `Text BOOKS/` / `_test_outputs/sketchy_ref.pdf`
(287 pages). Studying it produced these principles, now baked into the prompts:

### Core principles
1. **The NAME → a phonetic PUN → the WHOLE WORLD.** e.g. Strep *pyogenes* → "Pie
   Genies" → a bakery; *agalactiae* → "galactic" → space. The world isn't random —
   it encodes the name, which makes it memorable. (`propose_anchor_theme` in
   `gemini_image.py` prefers a real pun; if none is genuinely close, it picks a
   strong *thematic* world. Forced/weak puns like Keshev→Cabbage are rejected.)
2. **BELONGING (the soul of Sketchy).** Every element must *naturally belong* to
   the setting AND encode a fact at the same time. A pie under glass belongs in a
   bakery AND means "encapsulated". Pick a setting+event where the facts **happen
   on their own** (ADHD criteria → a chaotic DRIVING TEST: a reckless driver's
   behaviour *is* the hyperactivity symptoms). Nothing is "pasted on".
3. **DETAIL-per-fact, NOT CHARACTER-per-fact.** Each fact gets its own distinct
   visual detail/feature, but you DON'T make a separate character per fact — you
   cluster 5-6 details onto a FEW rich characters (one distracted pedestrian
   carries all 9 inattention symptoms as details: headphones=doesn't listen,
   dropping items=loses things, empty leash + "MISSING DOG" poster=forgetful…).
4. **NO explanatory text in the image.** The image is clean; only *diegetic*
   text allowed (a sign, a number on a jersey) that is itself the mnemonic. The
   explanation lives in a separate **numbered KEY = the caption** (`.txt`).
5. **The SCENE-DIRECTOR pipeline.** Mirrors how a real Sketchy page is authored:
   - a text-model "director" converts facts+symbols+world → `{scene, key}`:
     `scene` = pure-visual description (NO clinical terms, so the image model has
     nothing to label); `key` = numbered element→fact map.
   - the image model draws only the clean `scene`.
   - the caption model reads the **actual rendered image** + the key → flowing
     Hebrew narrative (multimodal — it describes what's really there).
6. **NUMBER ENCODING.** Small counts (≤4-5) = count belonging objects. Ratios =
   relative team sizes. Percentages/big numbers = **number-peg shapes** (0=ring,
   1=candle, 2=swan, 3=trident, 4=sailboat, **5=starfish/seahorse [NOT a hand]**,
   6=cherry, 7=boomerang, 8=hourglass, 9=balloon). **Pegs must BELONG too** — not
   floating symbols; concentrate threshold numbers on ONE fixture (the examiner's
   scoreboard), don't scatter them.
7. **PER-IMAGE ISOLATION (no bleeding).** Each image gets ONLY its aspect's facts.
   The full symbol bank is available as a *lookup*, but a symbol is used ONLY if
   its concept appears in *this* image's facts (cross-reference). So dopamine's
   DOPA-MINE symbol appears in etiology (dopamine is in those facts) but NOT in
   criteria. Aliases (`_SYMBOL_ALIASES`) catch concept words ("dopamine"→DOPA_MINE).
8. **Criteria split per cluster.** A criteria aspect with 2+ symptom clusters →
   one image per cluster (ADHD → inattention image + hyperactivity image), each
   with room for all 9 symptoms as details.
9. **Composition:** a FEW characters (3-6), CLEAN/uncluttered (not Where's-Waldo),
   correct anatomy (no extra/floating hands), watercolor+ink, 16:9, full-bleed.

### The 3 symbol tiers (`schedule/symbols.json`)
- **Tier 1 — domain symbols** (`scenes`, `ions`, `symbols`, `drugs`): receptors,
  drugs, brain regions, DSM-category scenes. ~238 entries. Constant across the
  course (a drug always renders the same way — Amphetamine = amp-to-11, etc.).
- **Tier 2 — epidemiology meta-symbols** (`epidemiology_meta`, 6): recurring
  markers reused in EVERY diagnosis's epidemiology image — tug-of-war=sex ratio,
  crowd=prevalence, conjoined-twins=comorbidity (the 2nd twin wears the comorbid
  disorder's own symbol), weather-forecaster=prognosis, family+DNA=heritability,
  cake=onset age.
- **Tier 3 — number pegs** (`meta_systems.number_pegs`): the 0-9 shape vocabulary.

---

## 6. Key Files

| File | Role |
|------|------|
| `scripts/daily_study.py` | **The orchestrator.** Per-chapter pipeline. Builds the scene-director prompts, calls NotebookLM + Gemini, uploads the Release. Run with `--day N` or `--bulk`. |
| `scripts/gemini_image.py` | Gemini calls: `propose_anchor_theme`, `run_scene_director`, `generate_sketchy_directed` (director→image→[coverage-retry]→caption), `generate_image` (3× retry), `check_coverage` (criteria completeness), two-stage caption (`_visual_inventory` + `CAPTION_INSTRUCTION`), `SKETCHY_STYLE_PREFIX`. Model = `gemini-3-pro-image-preview` (Nano Banana Pro) by default; override via env `SKETCHY_IMAGE_MODEL` (e.g. `gemini-3.1-flash-image-preview` = Flash/free-tier). |
| `schedule/symbols.json` | The symbol codex (3 tiers above). 156 `symbols` + 60 `drugs` + 17 `scenes` + 5 `ions` + 7 `epidemiology_meta` + `number_pegs`. |
| `schedule/schedule.json` | The 144-chapter curriculum (10 phases). Each chapter: title, aspects, symbols_needed, prompt_focus, optional `anchor_world`/`anchor_zones` (curated world override — chapter 17 (ADHD) has the driving-test world). |
| `schedule/phase_palette.json` | Per-phase color hints (UI theming + gentle image palette). |
| `app/app.py` | **The Streamlit course site.** Reads Releases (private repo, server-side fetch with GH_TOKEN), renders each chapter. Spaced-repetition flashcards. Recommended-reading box (pages per book). |
| `scripts/sr_engine.py` | SM-2 spaced-repetition algorithm (imported by app.py). |
| `scripts/build_book_map.py` | **Run once.** Extracts each textbook's chapter map + PRINTED page ranges into `schedule/book_map.json` (needs the local PDFs). Scans real page folios rather than assuming a fixed front-matter offset, because Dulcan's offset is not constant. |
| `scripts/audit_coverage.py` | **The coverage check.** Reads the "מפת המקורות" table from every generated `study_guide.md`, accumulates cited pages per book, and reports which book chapters no chapter has touched — plus any page cited *past the book's last page*, i.e. fabricated. Run after each batch. |
| `schedule/book_map.json` | Output of `build_book_map.py`; the audit runs off this, not off the 500 MB of PDFs. |
| `Dockerfile` (root) + `.gcloudignore` | Cloud Run deploy (bundles app.py + sr_engine.py). |
| `scripts/daily_notify.py` | (older) ntfy notification + progress tracking. |
| `scripts/_smoke_adhd_v4.py` | Local Gemini-only smoke test for ADHD images (hand-built facts, no NotebookLM needed). |

---

## 7. How To Run Things

```bash
# --- Generate one chapter (on the VM, where notebooklm is authed) ---
gcloud compute ssh weekly-review-vm --zone=me-west1-b --project=psych-research-agent
cd /opt/psychiatry-board-study && git pull origin main
nohup /opt/venv/bin/python scripts/daily_study.py --day 16 > /tmp/day16.log 2>&1 &
tail -f /tmp/day16.log

# --- Bulk all chapters ---
nohup /opt/venv/bin/python scripts/daily_study.py --bulk > /tmp/bulk.log 2>&1 &
#   (add --resume to skip chapters that already have a Release)

# --- Smoke-test Sketchy images locally (no NotebookLM, just Gemini) ---
python scripts/_smoke_adhd_v4.py        # writes _test_outputs/smoke_adhd_*.png + .txt

# --- Run the Streamlit app locally ---
export GH_TOKEN=$(gh auth token)
streamlit run app/app.py                # http://localhost:8501

# --- Coverage audit: are we actually covering the books? ---
python scripts/build_book_map.py     # once, locally, needs "Text BOOKS/"
python scripts/audit_coverage.py     # after each batch; add --json for CI
#   Reports per book: which chapters no study guide has cited, and any page
#   cited past the book's last page (= the model invented it).

# --- notebooklm auth (only needed on the VM, when it expires) ---
notebooklm login    # opens a browser; run interactively (not via nohup)
```

---

## 8. Open Issues / Watch-outs

- **Nano Banana 2 is a PREVIEW model** → occasional transient "no image data"
  failures. `generate_image` retries 3× with backoff. In bulk, a chapter image
  can still rarely fail (logged `[FAIL]`); re-run that chapter if so.
- **Residual text in images** — the model still sometimes adds a few labels.
  Reduced a lot but not zero; partly a model limitation.
- **Anchor world is auto-generated per chapter** for most chapters (quality
  varies). To force a good world, add `anchor_world` + `anchor_zones` to that
  chapter in `schedule.json` (see day 16 as the template).
- **NotebookLM auth expires on the VM** periodically → `notebooklm login`.
- **MSYS path mangling** on Windows git-bash: prefix gcloud paths carefully, or
  run gcloud from PowerShell. (Several scp attempts failed due to this.)
- Old per-chapter releases were generated with EARLIER (worse) image code; the
  bulk run will overwrite them with the new quality.

---

## 9. Cloud Run Deploy (ready, not yet run)

Files are prepared: root `Dockerfile` + `.gcloudignore`. To deploy:

```bash
# from repo root (local) OR from the VM repo
gcloud run deploy psychiatry-course \
  --source . --region me-west1 --project psych-research-agent \
  --allow-unauthenticated \
  --set-env-vars GH_REPO=tnvsh0/psychiatry-board-study,GH_TOKEN=<gh-token>
```
Needs Cloud Build + Artifact Registry + Cloud Run APIs enabled on the project.
The app fetches schedule/symbols/releases live from the private repo via GH_TOKEN
(nothing baked into the image). Likely covered by the GenAI App Builder credit.

---

## 10. Commit History (the Sketchy redesign arc)

Most of the recent work was iterating the Sketchy image quality. In order:
- `3fe0287` split clinical vs visual prompts
- `8e74219` aspect-scoped images + epidemiology meta-symbols + number-pegs + facts.json
- `02dbec4` constant symbols follow facts + anchor-world + anti-text
- `842c0c6` **scene-director pipeline** + name-pun worlds
- `8e8153b` the **BELONGING** principle
- `3017883` criteria as one coherent situation + image retry
- `38a4e06` cleaner composition; criteria = few characters, many symptoms each
- `246772a` per-image **isolation** (facts cross-reference) + detail-per-fact
- `4925080` per-cluster criteria images + curated anchor-world override
- `30b8266` number-pegs must **belong** (no floating pegs / stray hands)
- `5d8e7d9` + `97f2484` Cloud Run Dockerfile + .gcloudignore

(Run `git log --oneline` for the full list.)
