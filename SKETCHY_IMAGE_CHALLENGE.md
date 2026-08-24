# The Sketchy-Image Challenge — Handoff & Brain-Dump

> **Purpose.** This document is written for a *more capable* AI model. It summarizes
> a working educational-content pipeline and then goes deep on its hardest, still-
> unsolved sub-problem: generating **SketchyMedical-style mnemonic illustrations**,
> and specifically the **epidemiology** image. It is deliberately honest about the
> ~13 failed/partial iterations so you can see the full search space and (hopefully)
> propose a genuinely better approach rather than repeating what we already tried.
>
> Audience: an AI being asked to "take this one step further." Assume you can read
> Python, prompt-engineer, and reason about generative-image behavior. Be opinionated.

---

## 1. What the project is (1-paragraph context)

An automated study course for the Israeli psychiatry board exam (child & adolescent
focus): **126 "chapters"**, each producing a study bundle — Hebrew audio podcast,
whiteboard video, Markdown study guide (with textbook page refs), info-dense
infographics, flashcards, quiz, a structured `facts.json`, and the hard part:
**Sketchy-style mnemonic scenes**. Clinical artifacts are made by **Google
NotebookLM** (it holds the textbooks: DSM-5-TR, Dulcan, Stahl, Kaplan & Sadock,
syllabus). The mnemonic images are made by **Gemini image models**. Everything is
uploaded to a GitHub Release per chapter; a Streamlit app (deployed on Cloud Run)
renders each chapter for study.

The *clinical* content pipeline works well. **This document is only about the
images.**

---

## 2. What "Sketchy-style" means (the target)

SketchyMedical / SketchyMicro / SketchyPharm teach by encoding every fact of a topic
into ONE bizarre, themed, hand-painted scene, so the facts are recalled by walking
through a memorable picture. We have the real reference PDF (287 pages). Studying
actual pages, the recurring formula is:

- **ONE unified, bizarre, themed WORLD**, usually born from a phonetic pun on the
  topic's name (Strep *pyogenes* → "Pie Genies" bakery; *Leishmania donovani* →
  "Desert Mama" desert revival; *Pseudomonas* → an opera house).
- **ONE coherent SITUATION with a central, dominant anchor** that the *whole cast is
  inside* — a medieval joust where everyone is part of the tournament; a desert
  preacher with the gathering around him; a diva center-stage with the show around
  her. It is **busy but composed and readable**: a few large clear elements arranged
  around a dominant center, NOT a democratic scatter of equal-size characters.
- **Every element BELONGS to that world AND encodes a fact** simultaneously (a pie
  under glass belongs in a bakery AND means "encapsulated"). Nothing is "pasted on."
- A separate **numbered legend** maps each visible element → its fact.

That last visual property — **busy-but-composed, one dominant anchor, everything a
participant in one situation** — is the thing our generations keep failing to hit.

---

## 3. The image pipeline (how a Sketchy image is currently produced)

Per chapter, per "aspect" (criteria / epidemiology / etiology / treatment / …):

```
facts.json (extracted once from the textbooks via NotebookLM, STRICT JSON:
   {criteria{clusters,thresholds,subtypes}, epidemiology{prevalence,sex_ratio,
    onset_age_typical,heritability,comorbidities[],prognosis}, etiology{...},
    treatment{...}, high_yield[]})
        │  (each aspect's image is built ONLY from its slice → no cross-bleeding)
        ▼
SCENE DIRECTOR  (text model: gemini-2.5-flash)
   A prompt converts {facts slice + symbol vocabulary + anchor "world"} into:
     scene = a PURELY VISUAL description (NO clinical terms, so the image model has
             nothing to mislabel), and
     key   = a numbered map "visible element → fact + why", used later by the caption.
        ▼
IMAGE MODEL  (gemini-3-pro-image-preview, a.k.a. "Nano Banana Pro";
              swappable via env SKETCHY_IMAGE_MODEL; older flash = gemini-3.1-flash-
              image-preview). Draws ONLY the clean scene.
        ▼
CAPTION  (multimodal gemini-2.5-flash): two-stage —
   (A) VISUAL INVENTORY: "list ONLY what you literally see" (grounds it in the real
       render, not the intended scene);
   (B) NARRATIVE CAPTION in Hebrew that describes what is actually visible and weaves
       in the clinical meaning, plus states precise figures the key flags as
       "EXACT FIGURES (state in caption)".
```

Key design choices that DO work and should be kept:
- **`facts.json` as the single source of truth**, sliced per aspect → no bleeding
  between a chapter's images.
- **Director outputs a clinically-clean scene** so the image model can't stamp
  clinical words as labels.
- **Caption reads the ACTUAL rendered image** (multimodal), not the intended scene.
- **Criteria images have a COVERAGE-GUARANTEED retry loop** (`check_coverage`):
  after each render, a vision pass checks whether *every* diagnostic criterion has a
  distinct visible hook; if some are missing it re-renders (≤3×) emphasizing the
  missing ones and keeps the most-complete render. This reliably solves
  "all N criteria must appear."
- A small util `scripts/regen_image.py --day N --label X --upload` regenerates ONE
  image, and `scripts/_recaption.py` re-captions an existing image (cheap prompt
  testing without paying for a new render).

Things that work *visually* today (for contrast with the failure below):
- **Etiology** images: e.g. a garage building the "ADHD car" (engine = brain
  chemistry, wiring = genetics, fuel = environment). A real, coherent story. Users
  consistently rate these "excellent."
- **Criteria** images: e.g. a chaotic driving test where a reckless driver embodies
  the hyperactivity cluster and a distracted pedestrian the inattention cluster, on
  a road forking to 'SCHOOL'/'HOME' (the ">=2 settings" rule). Completeness enforced
  by the retry loop.
- **Comorbidity-as-companions**: fixed symbols (anxiety = trembling figure clutching
  a scared white rabbit; depression = a small dark rain cloud raining over a slumped
  head; ODD/CD = a scowling kid tearing a "RULES" paper; specific learning disorder =
  jumbled scrambled letters around the head). These read well and are reused.

---

## 4. THE UNSOLVED PROBLEM — the epidemiology image

### 4.1 Why epidemiology is uniquely hard
Criteria/etiology have a NATURAL story (symptoms happen during a test; causes
assemble a machine). **Epidemiology is a list of ~6–8 numeric facts** with no innate
narrative:

- prevalence (e.g. ADHD ~5% children, ~2.5% adults)
- sex ratio (ADHD ~2:1 M; but autism 4:1 M, anorexia ~10:1 F, depression 2:1 F)
- age of onset (a specific age/window)
- heritability / familial risk (ADHD ~74%)
- comorbidities AND their rates (ODD ~50%, anxiety ~25–35%, SLD ~20%, depression ~11%, DMDD 4–16%)
- course / prognosis / persistence (ADHD ~60% persist to adulthood)

The user's non-negotiables, learned over many rounds:
1. **The numbers ARE the high-yield content** — they must be *memorable in the
   image*, not silently dropped.
2. They must **not be arbitrary** — a number "stuck on" a prop ("50% OFF" on a
   shirt, a giant numeral a character holds, a "statistics board") is rejected as
   meaningless. The number must be *enacted* / *belong* / *be part of the action*.
3. The image must be **ONE coherent STORY/SITUATION**, not "a few characters and a
   few symbols with no connection" (a "tableau of vignettes").
4. It must **not be cluttered**.

These four are in genuine tension and that tension is the whole problem.

### 4.2 The iteration log (what we tried → what happened)

> Honest record. Each row is a real attempt, in order. "Model" = gemini-3-pro-image-
> preview unless noted. We judged early rounds by the CAPTION (a mistake — see 4.3),
> later rounds by downloading and *viewing* the actual PNG.

1. **Rigid "epidemiology meta-symbols"** (a fixed vocabulary: sex ratio = tug-of-war,
   prevalence = crowd, comorbidity = conjoined twins wearing the comorbid disorder's
   symbol, prognosis = weather forecaster, heritability = family+DNA, plus a 0–9
   "number-peg" shape code: 0=ring,1=candle,2=swan,…,5=starfish,7=boomerang,…).
   → **Disconnected symbol-board.** A weather-forecaster desk has no reason to exist
   in a DMV; the pieces don't relate. The number-pegs (a starfish for 5) felt
   arbitrary ("why is there a starfish?").
2. **Comorbidity redesign** (one conjoined-twin pair + the comorbid disorders as
   items they wear/hold, instead of many twin pairs). Helped that one symbol, but the
   scene was still a board.
3. **Drop the rigid meta-symbols; encode each fact "scene-native"** (like criteria).
   → Better comorbidity/prognosis, but the model **still built a "DMV STATISTICS"
   board and hung number-shape pegs on it.**
4. **Qualitative only — banish all numbers to the caption.** → Clean, but the user
   (correctly) rejected it: "what data does this even contain? The numbers are what I
   need to remember."
5. **Numbers must stay, but "ride on" an element already in the scene** (each
   comorbidity companion carries its own rate; heritability rides on a family; etc.).
   → Still scattered pegs + clutter; the model loves a stats wall.
6. **Fully release the rigid peg vocabulary** (removed it from the legend, the global
   NUMBER_RULES, and the symbol index). → cleaner.
7. **KEY METHODOLOGY BUG FOUND.** The user asked "where did the swans/boomerangs even
   come from — did we feed that?" We had cleaned the *director* of pegs, but the
   **CAPTION prompt still contained the full peg code** ("swan=2, boomerang=7,
   starfish=5…"). So the multimodal caption model was **pattern-matching ambiguous
   shapes into pegs and HALLUCINATING swans/boomerangs that were not in the image.**
   We were tuning against contaminated captions. Fix: de-peg the caption; and from
   then on, **download and look at the actual PNG** (via PyMuPDF/`gh release
   download` + image read), never trust the caption alone.
8. **Prescriptive exact-count enactment**: convert each % to N-of-M and *enact* it
   (2:1 = exactly 2 boys vs 1 girl; 74% = 3 of 4 of a family; onset = candles =
   exact age), CLUSTER several facts onto one small cast, and use a **"digit-object"**
   for a number that can't become a clean fraction. → Partial: the family/onset
   enacted, but the model **still reverted to big digit cut-outs** ("2"-shaped key,
   giant "5" on paper) and stayed busy.
9. **Clean, from-scratch director prompt** for epidemiology only (no accumulated
   cruft, no 32K symbol index; the generic director had ballooned to ~32,000 chars).
   → Much cleaner BUT **too empty / no data**: in stripping the prompt we deleted the
   story/BELONGING soul, so it became a few disconnected characters again, and it
   *dropped the numbers*.
10. **Sharpen the digit-object**: the digits must be the **structural shape of a real
    in-action object** (a '5' lying down IS a go-kart wheel, a '7' IS its frame → a
    go-kart "that won't pass its driving test" = ~75%), NOT a numeral printed on / held
    up. → This *worked* for ONE hero number (a clearly-readable go-kart made of 7+5,
    being driven). But asking for ~8 such objects just re-scatters them.
11. **General rule, diagnosis-agnostic**: image *enacts* only the cleanly-enactable
    parameters (sex ratio = relative group sizes, onset = candles, heritability =
    family N-of-M, course = visible trajectory, comorbidity = fixed-symbol
    companions); ALL precise %s go to the caption (the key ends with an "EXACT FIGURES
    (state in caption)" line and the caption is instructed to state them); at most ONE
    hero digit-object. → The caption now reliably carries the exact figures. The image
    was cleaner. BUT the user said the *scene* was still "a few characters and symbols
    with no plot — not a story," and that "2 boys vs 1 girl standing apart is a
    terrible way to encode 2:1 (who counts characters?)" — it should be a
    **relationship/contest** (a tug-of-war), not a headcount.
12. **Restore the STORY soul** (re-inject SKETCHY_PHILOSOPHY + a "#1 rule: ONE bizarre
    story you can tell" + "enact each fact through the unfolding event/relationships").
    → Real improvement: a kid driving a go-kart built from 7+5 recklessly through the
    DMV under a "NO RECKLESS DRIVING" sign — there's a central action. But the user
    re-emphasized: it's **not point-interactions, it's that EVERYTHING must be part of
    ONE flowing plot.**
13. **"ONE situation everyone participates in"** (after actually viewing real Sketchy
    pages: joust/opera/desert-revival). Sharpened the rule to forbid "separate people
    each doing their own thing in a shared room." → Achieved *unity* (a chaotic DMV
    driving-track everyone is in) but **swung back to cluttered/chaotic** — a busy
    melee, not the *composed* busyness of real Sketchy.

### 4.3 The core observations (the actual learnings)

- **The model oscillates between two failure modes** and rarely lands between them:
  - *sparse & disconnected* (a "tableau of vignettes": a few characters each holding
    a symbol, no relationship), and
  - *unified but chaotic & cluttered* (a busy melee that's hard to parse).
  It does **not** reproduce real Sketchy's *"busy but COMPOSED and readable"* —
  i.e. **one large dominant central anchor with a few clearly-arranged supporting
  elements**. Our prompts say "one situation, central anchor" but the renders come
  out as democratic equal-size scatter. (This may be the single most important lever
  we have NOT cracked: forcing a dominant focal composition.)
- **Numbers split cleanly into two classes:**
  - *Enactable* — ratios (relative group sizes), clean fractions (N of M of a group),
    ages (candles). These work and are memorable when staged as a *relationship*, not
    a headcount.
  - *Un-enactable precise %s* (5%, 11%, 33%, 4–16%). Every attempt to put these IN the
    image becomes arbitrary (a numeral on a prop / a peg / a held cut-out). The ONE
    technique that worked for a *single* such number is the **"digits ARE the
    structure of a real object that's part of the action"** (the 7+5 go-kart). It does
    not scale past ~1 hero per image.
  - Current compromise: enact the enactable; put precise %s in the caption (+ planned
    narration + the separate info-dense infographic).
- **Caption ≠ image.** Trust only the rendered pixels. A multimodal caption primed
  with a symbol vocabulary will hallucinate those symbols onto ambiguous shapes.
- **Prompt entropy is real.** Iterating by accretion produced a 32K-char prompt full
  of contradictions; a clean rewrite helped but accidentally deleted essential
  principles (the story soul). There's a balance between "clean" and "complete."
- **What good looks like** (works today): etiology (assemble-a-machine story),
  criteria (a test where symptoms are the natural beats + a coverage retry loop).
  Epidemiology lacks an innate story, which is exactly why it's hard.

### 4.4 Current state of the epidemiology prompt
`scripts/daily_study.py` → `build_epidemiology_director()` (routed to from
`build_director_instruction` when `aspect == "epidemiology"`). It currently contains:
`SKETCHY_PHILOSOPHY` (bizarre-world + belonging) + a "#1 rule: ONE situation everyone
participates in" + "enact each fact as a relationship within the event" + the fixed
comorbidity symbols (`COMMON_COMORBIDITY_SYMBOLS`) + "precise %s → caption" + "at most
ONE hero digit-object whose digits are an object's structure in the action" +
"composition: 4–6 chars, every one a participant, no bystanders, no stats board."
~7K chars. Criteria/etiology still use the generic `build_director_instruction`.

---

## 5. Where we want fresh thinking

We are NOT asking you to keep tweaking the same prompt. We're asking: **is there a
fundamentally better way?** Concrete open questions:

1. **Composition control.** How do we make a text-to-image model produce *"busy but
   composed"* — ONE large dominant central anchor + a few arranged supporting
   elements (real Sketchy), instead of democratic scatter or chaos? Options we
   haven't tried: explicit layout/region prompting; a two-pass "compose then render";
   generating a rough layout/composition sketch first and using it as a reference/
   structural image; aspect-ratio or "single hero subject" constraints; negative
   prompting for "crowd/clutter".
2. **Precise numbers.** Is there a memorable, non-arbitrary, *general* way to encode
   ~5 precise percentages in one scene? Or is the right answer architectural —
   accept that the IMAGE carries only enactable facts + ONE hero number, and the
   precise data lives in the caption/narration/infographic? Is there a clever single
   device (a meaningful "fill"/proportion gauge that's diegetic, a population the
   viewer can subitize, the digit-object) that scales better than we found?
3. **Pipeline architecture.** Is text-director → image-model → caption the right
   shape? Alternatives: iterative self-critique (render → vision-critique against the
   four rules → re-render, like our criteria coverage loop but for "story unity" and
   "composition"); few-shot conditioning on the actual Sketchy reference pages;
   editing/inpainting to add facts to a clean base; a model that natively does
   reference-guided generation.
4. **Generalization.** Our last prompt is diagnosis-agnostic (the 6 universal
   epidemiology parameters, handled by enact-or-caption). Does that hold for the hard
   cases (anorexia 10:1, schizophrenia onset/suicide, autism 4:1)? What breaks?
5. **The "story" problem in general.** For aspects with no innate narrative
   (epidemiology), how do you reliably manufacture ONE coherent bizarre plot that all
   facts are participants in — automatically, per chapter, at scale (126 chapters)?

---

## 6. Technical pointers (for whoever continues)

- **Repo:** private GitHub `tnvsh0/psychiatry-board-study`. Generation runs on a GCE
  VM (`weekly-review-vm`, has NotebookLM auth + textbook PDFs + `GEMINI_API_KEY`).
  Streamlit deployed on Cloud Run.
- **Key files:**
  - `scripts/daily_study.py` — orchestrator + ALL the prompt builders
    (`SKETCHY_PHILOSOPHY`, `build_director_instruction`, `build_epidemiology_director`,
    `COMMON_COMORBIDITY_SYMBOLS`, `_ASPECT_STRUCTURE`, `NUMBER_RULES`, `extract_facts`).
  - `scripts/gemini_image.py` — model calls (`run_scene_director`,
    `generate_image`, `generate_sketchy_directed` with the criteria coverage-retry,
    `check_coverage`, two-stage `generate_caption` + `_visual_inventory`,
    `CAPTION_INSTRUCTION`, `IMAGE_MODEL`).
  - `scripts/regen_image.py` / `scripts/_recaption.py` — cheap single-image iteration.
  - `schedule/symbols.json` — the curated symbol codex (drug/receptor puns, etc.).
  - `schedule/schedule.json` — the 126 chapters; day 16 (ADHD) has a curated
    `anchor_world` ("a chaotic DRIVING TEST / DMV") used as the test bed.
- **Models:** image = `gemini-3-pro-image-preview` (Nano Banana Pro), text =
  `gemini-2.5-flash`. ~$0.13/image (Pro). The flash image model
  (`gemini-3.1-flash-image-preview`) is cheaper and has a free tier.
- **The reference:** `עותק של Sketchy.pdf` (287 pp) — render pages with PyMuPDF
  (`fitz`) since `pdftoppm`/poppler is absent. Pages 6 (Strep pyogenes joust), 25
  (Pseudomonas opera), 70 (Leishmania "Desert Mama") are good exemplars of the
  busy-but-composed, one-situation ideal.
- **Iterate cheaply:** edit prompt → `regen_image.py --day 16 --label epidemiology
  --upload` on the VM → `gh release download … --pattern sketchy_epidemiology.png` →
  **view the PNG** (don't trust the caption). The criteria/etiology labels are
  `dsm_criteria_inattention`, `dsm_criteria_hyperactivity-impulsivity`, `etiology`.

---

## 7. TL;DR for the advanced model

We built a solid pipeline; etiology and criteria images are genuinely good. The
**epidemiology image** is the holdout. After ~13 honest iterations the residual,
crisply-stated problems are:

1. **Composition:** the image model won't produce Sketchy's *busy-but-composed, one-
   dominant-anchor* look — it gives us either disconnected vignettes or a chaotic
   melee. **This is probably the highest-value thing to crack.**
2. **Precise numbers:** enactable numbers (ratios, fractions, ages) work as staged
   *relationships*; arbitrary precise %s do not, except as ONE "digits-are-an-object"
   hero. We currently route the rest to the caption/narration/infographic.
3. **Story at scale:** manufacturing ONE coherent bizarre plot that every fact
   participates in, automatically, for a data-list aspect like epidemiology, across
   126 chapters.

If you can propose a better *architecture* (composition control; render→critique→
re-render loops; reference-conditioned generation; a cleaner numeric-encoding theory)
rather than another prompt tweak, that's the win we're looking for. Be bold.
