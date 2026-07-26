# Sketchy Spec v2 — the corpus-derived redesign

Supersedes the design assumptions in `SKETCHY_IMAGE_CHALLENGE.md`.

Derived by reverse-engineering 19 pages of the real SketchyMedical corpus
(SketchyPharm Neuro/Psych pp. 143–215, SketchyPath renal pp. 272–274),
113 legend items classified individually, cross-checked against our own
chapter-28 output. Nine analysis lenses, three competing redesigns, three
judge panels, five adversarial tests. Every rule below is either marked
`[CORPUS]` (observed in the source material) or `[INVENTION]` (our
extrapolation — revisable on evidence, never defended as fidelity).

---

## 1. Root cause

Our pipeline severs fact from picture at every stage and never re-joins them.

The director is forbidden to put clinical words in the scene. The image
model never sees the facts, the legend, or the symbol codex — it receives
only a style block and a prose scene. Numbers are converted into a
shape-cipher that the caption model is then forbidden to decode. The caption
is written *from a vision probe of the render* rather than from the fact list.

So no artifact anywhere binds **fact → named visual element → rendered pixel**.
Nothing is addressable, nothing is countable, nothing is verifiable.

Real Sketchy's entire method *is* that binding: a numbered list of `cue: fact`
pairs where the cue is a unique verbal address into a picture that carries
painted words. We deleted both halves — no legend, no words in the frame.

The three complaints are three faces of one defect:

| Complaint | Actual cause |
|---|---|
| "no plot, no protagonist, no linkage" | The picture has no *index*. We chased narrative cohesion as a proxy for coherence, which forces figures to interact and caps a plate at ~6 facts. |
| "almost no numeric data" | Literally true: `daily_study.py` line ~533 says *"NEVER just write the numeral"* and the epidemiology director appends *"NO written numerals"*. |
| "the text just describes the image" | Inevitable: the caption is generated *from* the image instead of the image being generated from an enumerated fact table. |

### Where the user's own theory is contradicted by the corpus

**There is no plot and no protagonist in real Sketchy.** `[CORPUS, certain]`
Across all 19 pages: zero causal relationships between figures, zero characters
whose fate is followed. The dominant element is always an **inanimate structure**
cutting the frame on a diagonal — the castle wall, the dock, the run of desks,
the ski slope, the Starry Night canvas. Pushing toward "ONE flowing plot" is
what produced our chaotic melee: a plot forces interaction, and interaction
prevents each figure from holding its own independent fact.

What the corpus uses instead, and what actually satisfies the underlying need:

1. **A mundane venue whose NAME is a pun on the topic** — the retrieval hook.
2. **ONE ambient activity everyone shares** — everyone works the office, views
   the art, besieges the castle. One verb, thirty variations. *This* is the
   cohesion device, not a story.
3. **A numbered legend** that gives every element a verbal address.
4. **Bounded pairwise relations** where the relation *is* the mechanism.

---

## 2. The rule set

### R1 — The venue is a pun on the topic, and the pun is painted on a sign `[CORPUS]`

Never pick a setting for being bizarre, unexpected, or mood-matched. This
inverts `SKETCHY_PHILOSOPHY` principle 1, which currently demands *"somewhere
that has NOTHING to do with the medical topic"*.

| Corpus venue | Derived from |
|---|---|
| travel agency "expre**SS** t**RI**ps" | SSRI (acronym embedded as an ordered subsequence) |
| gallery "STARRY **GAZING**" | `-azine` suffix |
| "Z.Z.Z. MATTRESSES" | the three z-drugs |
| "Ben's Diner" | **benz**odiazepines |
| barbershop | **barb**iturates |
| "Carpe Drive-in" | **carba**mazepine |
| "Château Tyramine" winery | the MAOI–tyramine interaction *is* the fact |
| castle siege | wall = glomerular basement membrane (spatial isomorphism) |

Two legal derivations: **phonetic/acronym pun on the name**, or **spatial
isomorphism** where the venue's structure maps onto the anatomy or the concept.

For a *disorder* (most of our 144), spatial isomorphism and the plain-English
picture inside the term are the workhorses — the p272 `MINIMUM` device: one
morpheme of the diagnosis hand-lettered on a prop inside a world that *is* the
term's literal picture.

**Do not print the full diagnosis on the venue sign.** `[CORPUS]` Every corpus
venue sign is the pun, never the diagnosis. Printing "MAJOR DEPRESSION QUARRY"
spends the largest lettering slot on the one word already known and destroys
the pun-as-index.

**No mechanical pun validator.** A Double-Metaphone / substring test rejects
`EXPRESS TRIPS`, `ZZZ MATTRESSES`, `STARRY GAZING`, `Carpe Drive-in` and the
siege castle — i.e. nearly every venue the corpus actually uses. `schedule/venues.json`
with 144 hand-authored entries is the **primary** mechanism, not a fallback.
This is a required up-front deliverable and it is manual work.

**Place categories must be globally unique across all 144 chapters.**
`[DECISION 2026-07-26]` The first authoring run batched chapters twelve at a
time, so no agent could see the others; it produced six launderettes, three
bowling alleys, four garden centres, three self-storage depots and the identical
sign `ODDS & ENDS` on two chapters. That breaks the one thing the venue buys: if
the chapter name does not retrieve a single unique world, it retrieves nothing.
Dedup is global, not per phase — a resident revisits chapters out of order.

The one licensed exception is the corpus's **shared universe** (§R10): venues
may be neighbouring premises on one street when the adjacency itself encodes a
pharmacological relationship, and that adjacency must be declared in
`adjacent_to`. Accidental collision is not a shared universe.

### R2 — The setting is mundane; the strangeness lives in the objects `[CORPUS]`

An office, a diner, a schoolyard, a wine cellar, a quarry. Nothing floats,
no impossible physics, one perspective, one light source. A boring container
is what makes the anomalies pop; a surreal container makes nothing anomalous.

Retires `SKETCHY_PHILOSOPHY` principles 4 (*"ABSURDITY OVER FAITHFULNESS"*)
and 5 (*"viewer should LAUGH, gasp, or say WHAT?"*). Memorability comes from
pun density, not from shock.

### R3 — One inanimate anchor structure, no protagonist `[CORPUS]`

Name one structure that cuts the frame on a diagonal and splits it into 3–4
zones: a wall, a platform, a run of desks, a terraced pit face, a conveyor.
It is the largest thing in the frame.

A visually prominent *figure* is permitted — p204's dominant standing figure
is ~40% of frame height. What is banned is a **narrative** protagonist: no
figure may be described as the one the event happens to. Cap any single figure
at ~45% of frame height.

Enforce by measurement, not by keyword blocklist: require `anchor_structure`
to be inanimate, require ≥12 figure-bearing elements, and probe *"what is the
single largest object in this image, and is it a person?"*

### R4 — ONE ambient activity, shared by everyone `[CORPUS]`

Everyone is working a shift / queueing / fishing / viewing art. That shared
activity — not a plot — is what makes 25 strangers belong in one frame and
gives each odd behaviour a plausible reason to exist.

### R5 — Bounded pairwise relations are REQUIRED for mechanism facts `[CORPUS]`

The earlier "figures never interact" rule would have made the corpus
unbuildable. The corpus links symbols constantly and **the link is the fact**:

- p204 #7 — a man physically barring another from the smiley-post-it drawer = reuptake inhibition
- p199 #7 — "Barbershop next to Cab-A" = barbiturates bind GABA-A at a separate allosteric site
- p143 #23 — "Serpent blocking V-mat" = VMAT inhibition
- p212 #4 — "Snapping double rope" = D2 blockade

Rule: any fact whose verb is transitive (block / inhibit / antagonize /
displace / prevent / convert / transport / potentiate) **must** use a relation.
Closed enum: `ADJACENT_TO | BLOCKS | ACTS_ON | FALLS_FROM | CONTAINED_IN`.
Cap at ~6 relations per plate; ban causal chains spanning more than two
elements; ban a global narrative arc.

### R6 — Density: pack the frame `[CORPUS]`

22–34 discrete mnemonic elements, 12–25 human figures at comparable scale,
roughly **one fact per figure**. Include ~1:1 non-mnemonic filler so the place
reads as real. No vignette, no breathing space, no edge darkening.

This inverts `_ASPECT_STRUCTURE['dsm_criteria']`, which currently mandates
*"just a FEW characters (2–4), each embodying SEVERAL criteria"* and
*"generous empty road/sky — parseable in seconds"*. No corpus page is
parseable in seconds. **A Sketchy page is a search field, not a poster.**

Density floor: when atomized facts < 18, expand filler rather than fail
validation. A 10-element table is not an error.

### R7 — Prop-family COHESION, not collision `[CORPUS]`

Elements whose facts share a parent key **should** share a prop family —
family membership is how the corpus encodes class membership:

- four sibling travel posters ("Fly Out!", "PARROT AIR", "DESERT AIRLINE", "The City") = four SSRIs
- two sibling office machines (fax, "DUAL COPIER/SCANNER") = two SNRIs
- four rope elements on p143 all = dopamine

Restrict the collision check to elements from **unrelated** fact keys — that
is the real "chain = prevalence AND chain = heritability" bug in our shipped
`epidemiology_who.png`. Resolve a genuine collision by differentiating the
instance (single vs double rope swing), never by swapping the family.

Cue distinctiveness is declared per element as a `(category, material,
dominant_colour)` triple; reject on triple collision across unrelated keys.

### R8 — Constant register; palette from the setting, never the disease `[CORPUS]`

Bright, deadpan, comic-absurd. Derive the value key from the chosen venue.
`_palette_directive` mapping phase 3 to *"cool muted blues and gentle greys"*
is the proximate cause of the grey drowned world. Retire it from the Sketchy
path; keep `phase_palette.json` for Streamlit theming only.

Soften the medium directive rather than absolutising it: **inked props over a
soft-gradient room shell**, outlines as dark desaturated versions of the fill
they enclose, pure black only on genuinely black objects. Do not ban texture
outright — p143 is a night lake, p274 is a muddy battlefield, and p212 renders
its whole world in Van Gogh impasto *because the impasto is the mnemonic*.
Allow a per-chapter pastiche override.

### R9 — Fixed cross-chapter lexicon, versioned and append-only `[CORPUS]`

A given symbol means exactly the same fact on every page it appears, so half
the vocabulary is already owned before the new page opens. Verified recurrences:
smiley post-it = serotonin (204/205/206) · N-compass = norepinephrine ·
happy/sad theatre masks = depression (204/205/206/207/213) · red tricycle =
TCAs · mousetrap = MAOI · rope = dopamine · bee swatter = H1 blockade ·
extinguished alpha flame = α1 blockade · cogwheels = parkinsonism · twisted
streamer = torsades · long tapering flag = long half-life · "CYP·450" plate =
CYP450 induction · tarantula = teratogenic · The Scream = panic disorder.

**One-way door:** once "bee swatter = H1" ships in an early chapter, changing
the glyph in chapter 90 silently invalidates every chapter already studied.
`schedule/lexicon.json` must be versioned and append-only **before** it is
widened. Code *substitutes* the canonical pair into the element table; it does
not suggest it to a model.

**The course's own 17 scenes become lexicon tokens, not venues.**
`[DECISION 2026-07-26]` `schedule/symbols.json` already holds hand-built scenes
— the Aa Hotel (GABA-A, five floors for five subunits), the Raphe tapestry
workshop, the DOPA-MINE colliery, the H-shaped Histamine House, the NMDA VIP
door. Almost all of them are **receptors and nuclei**: fixed symbols, not
places. They are seeded into `lexicon.json` and must appear as props *inside*
whichever venue the chapter draws, never in place of it. The Aa Hotel stands
inside the GABA chapter's world; it is not the world.

Two consequences for the venue brief, both of which the first run got wrong
because it was never shown `symbols.json`:
- The brief must list the chapter's required lexicon tokens, and the venue must
  be able to physically host them.
- A venue that cannot host them is rejected, not the token.

The exception is chapter 3 (Neurotransmitters overview), where seven of our
scenes *are* the content. There the plate is a street of those seven premises,
and no new venue is invented.

Retired regardless: the invented affective metaphors (`DSM_anxiety` = a
frightened rabbit, `DSM_depressive` = a grey rain cloud). Those are the one
mechanism with zero corpus instances — see §5.

### R10 — Shared universe: adjacency IS a fact `[CORPUS]`

Ben's Diner, the barbershop and the Cab-A taxi recur across p198, p199 and
p210. p199 #7 — *"Barbershop next to Cab-A"* — encodes the separate allosteric
site purely through which building is next door. Record cross-chapter
adjacency in `venues.json` for chapters sharing a receptor or mechanism. Free
rehearsal plus a relation-encoding channel.

---

## 3. Number encoding

**Our epidemiology number rules are `[INVENTION]`.** There is not one
epidemiology page in the corpus — it is entirely pharmacology and renal
pathology. The zone maps and per-plate number budgets below have no source.
They may be right; they must be labelled so they can be revised on evidence.

### What the corpus actually does `[CORPUS]`

1. **Write the digits.** Literal Arabic numerals painted on a diegetic object.
   `35` branded on the meat cart, `52`/`53` on retired-jersey banners,
   `CYP·450` on a licence plate, `5H-TV` as a channel logo.
2. **Strip operator, unit and decimal point** — `> 3.5 grams/day` is drawn as
   the two characters `35`. They live in the printed legend.
3. **The carrier's real-world job is to bear digits** — a numbered handcart, a
   jersey, a licence plate, a calendar, a price board. Never a numeral floating
   in space.
4. **The numbered object IS the carrier of the fact**, not a label beside it.
   The `35` is on the very cart heaped with the hams that *are* the lost protein.
5. **Small integers (1–3) are counted copies of a family-constant prop** — two
   candles = α2, double rope = D2, three Z's = the three z-drugs. Never a digit.
6. **Zero digit-shaped silhouettes across 19 pages.** Shape-as-glyph is
   reserved for *letters* (α-shaped rope, V-chevron VMAT mat).
7. **Triage hard.** Typically 0–2 painted numbers per page. Six of nine pages
   surveyed paint zero. p212 holds 31 facts and contains no digit at all.
8. **A dropped number becomes a qualitative word** — "Narrow Window",
   "Low Clearance", "MINIMUM" — or a relative-quantity contrast (p272's heaped
   numbered cart vs p274's few scattered scraps for `>150 mg/day`).
9. **Ordinals never get a numeral.** "first generation" is carried by *which
   gallery you are standing in* — Van Gogh vs Dalí — never by a digit.
10. **Continuous gradients are vertical position.** p143's dose axis *is* the
    composition's vertical axis: kidney low at the waterline (D1), bugler
    mid-dock (β1), scout high in the rigging (α1). No numbers anywhere.

### The mechanism we ship

**Delete `NUMBER_SHAPES` and `NUMBER_RULES` entirely** (`daily_study.py:452`,
`:674`). The 0–9 peg table (0=ring, 2=swan, 5=starfish, 7=boomerang) has zero
corpus instances and forces the learner to memorise a second unrelated code.
Also delete the contradictory *"2 on a barrier for D2"* example, which already
contradicted the ban it sits beside.

**Stage 1 — EXTRACT** (code, deterministic). Regex numeric tokens out of the
facts slice; classify as SCALAR / RANGE / RATIO / THRESHOLD / DURATION / COUNT.

**Stage 2 — NORMALIZE.** Corrections to the naive strip rule, all forced by
adversarial testing:

- **Never strip the decimal point.** Paint `0.6-1.2`, not `06-12`.
- **Keep `%` and `yr`** where the carrier permits. `2` on a depth marker does
  not recover `2%`; the corpus's own `35` does not recover `>3.5 g/day` either,
  and that is a real weakness, not a virtue to copy.
- **Keep the unit whenever two strings on a plate would otherwise collide or
  invert** — paint `150mg` and `3.5g`, never `150` and `35`.
- **Paint-strings must be unique per plate by construction**; the gate matches
  multiset-with-position, so two required `2`s cannot both be satisfied by one
  rendered `2`.
- **DURATION routes to a counted set of labelled time units**, not a
  paint-string — p204's `1-2 months` is a wall calendar showing exactly two
  sheets, `JANUARY` and `FEBRUARY`.
- **Ordinals are never numbers.**

**Stage 3 — CARRIER + TAG.** Closed enum of carrier archetypes, all objects
whose real job is to bear digits: `JERSEY_OR_BIB · GATE_OR_PLATFORM_NUMBER ·
PRICE_OR_MENU_BOARD · LICENCE_OR_VEHICLE_DOOR · DEPTH_OR_LEVEL_MARKER ·
WALL_CALENDAR · SCOREBOARD_OR_TALLY · ROUTE_OR_LINE_SHIELD · MILEPOST ·
TICKET_STUB · WEIGHT_OR_PRESSURE_DIAL · ADDRESS_ON_A_BUILDING ·
HARD_HAT_OR_HELMET_NUMBER · CHANNEL_OR_STATION_LOGO`.

Constraints code enforces:

- **THE TAG (highest-value single rule).** Every painted digit-string carries a
  1–4 character referent tag *on the same carrier, in the same hand* — p207's
  banner is `52` with `HT` in smaller caps directly beneath, and that tag is
  the only reason the numeral is retrievable in a frame that also contains
  jersey numbers 37, 41, 45 and 07 as pure filler. Gate on the concatenated
  string.
- **Reject a carrier whose native numeric meaning competes with the unit.** A
  depth marker reading `2` reads as 2 metres. A milepost reading `5` reads as
  distance, not years.
- **Multi-number facts land on ONE carrier.** `70% within 5 years` split across
  a ticket stub and a milepost does not reassemble from the picture.
- **Never split a range across two props.**
- **MAX_PAINTED_NUMBERS = 3** per plate, and **0 is an explicitly legal
  outcome.** Six bare unitless digits in one frame is a 3–6× inflation over the
  corpus maximum, concentrated in the model's weakest capability. A number with
  no natural numbered prop is **dropped, not forced**.
- **Ban filler numerals.** Instruct that no numeral other than the required
  ones appears anywhere in the frame, so the gate is not noisy. (The corpus
  violates this — p207 is covered in decorative jersey numbers — but we cannot
  afford the ambiguity at 768 px.)
- **MAGNITUDE PARTNER.** Every painted number sits beside a prop whose sheer
  quantity redundantly shows direction, so a failed digit still yields
  "massive" vs "trace". p272's heaped cart vs p274's scattered scraps.
- **Fact provenance.** A figure resting on a single unverified extraction stays
  in the legend, where it is cheap to correct, rather than burned into pixels.

### Path B — deterministic compositing (recommended; prototype first)

The image model is bad at rendering requested strings; our own etiology render
already produced the non-word "Sorceric". **Do not ask it to spell.**

Ask instead for a **blank sign** on the carrier, rendered in a distinctive flat
chroma key (e.g. pure `#00B140` on an otherwise chroma-free palette). After
render, code segments the largest quad of that hue, perspective-warps the exact
digit string plus its tag onto it with PIL, and re-tints to a plausible sign
colour.

This **guarantees** the correct number reaches the pixels, eliminates the
retry burn, and makes ranges, decimals, units and `1:2` ratios all safe. It is
the only proposal in the whole analysis that removes the wrong-number risk
rather than mitigating it.

Ship Path A (gate + retry) now; prototype Path B on chapter 28 before bulk.
**A wrong number burned into a study image out-competes the correct legend at
recall time and is very hard to unlearn.** Exact-match gating only — fuzzy
matching on numerals is actively harmful.

### Ratios

The counted-set route (1 boy + 1 girl vs 1 boy + 2 girls inside a mandated
12–25 figure frame with 1:1 filler) is **unrecoverable and unverifiable** —
it is exactly the shipped bug where `who.png` drew roughly 7:1 male while the
caption asserted 1:2. Either paint `1:2` literally on a single tagged carrier
(safe under Path B), or mark it `drawn=false` and print it legend-only with
`[לא מצויר]`. Never claim a ratio is drawn without a count gate.

---

## 4. Text-in-image policy

**Delete `_TEXT_RULES_ENVIRONMENT` entirely and delete the two-regime
`text_policy` parameter everywhere.** Delete the trailing *"ABSOLUTE: no
written words anywhere in the image"* clause in `generate_sketchy_directed`.
Delete the `LABEL VISIBILITY RULE` (side-by-side rows, fan out like a hand of
cards) — it is the direct cause of our line-up compositions.

Replace with a single block:

> 10–18 pieces of legible hand-lettered **diegetic** text — shop signs,
> business logos, travel posters, T-shirt slogans, wall calendars, machine
> faceplates, names painted on hulls, price boards, jersey numbers. These are
> **props, not overlays**, and they are the primary carrier of names, suffixes
> and numbers. Full advertising sentences of 3–6 words over up to three lines
> are welcome ("BREAK THOSE BAD SLEEPING HABITS!", "cannot combine with other
> CoupoNS"). Redundant labels bolted to objects that visibly already are that
> object are welcome ("DUAL COPIER/SCANNER" taped to a photocopier) whenever
> the object's *name* is the mnemonic syllable. Every **other** piece of paper,
> screen and document must be illegible squiggle-lines: legibility is itself
> the signal that a string is load-bearing.
> Still forbidden: callout arrows, leader lines, numeric badges over the
> artwork, legend boxes, captions.

**TEXT TO PAINT block.** Every required string listed verbatim in quotes with
its carrier object. Cap: **18 required strings, ≤5 words each.** (p204 carries
16 guaranteed-legible strings, two of them 4–5 words; a 12/≤3 cap silently
deletes a device the corpus uses on nearly every page.)

**Any string that is the SOLE carrier of a legend row must be gated.** It may
not be demoted to the ungated "may be small, angled, clipped" pool.

**Prop descriptions leak into the picture as painted text.** `[OBSERVED
2026-07-26]` The eight venue previews were told that *only* the venue sign must
be legible and everything else must be squiggles. The model ignored that and
lettered words straight out of the prop descriptions: the DOPA-MINE gallery
mouths came back reading `Hand-board` — my own descriptive adjective, rendered
as signage — and the courtroom produced `EVIDENCE` six times plus
`HEARSAY — INADMISSIBLE` and `CORRIDOR NOTICE`.

So the model treats any noun phrase in the brief as a candidate string. Two
rules follow:
- `TEXT TO PAINT` is the sole authority on what is lettered. Every other line
  of the prompt is written to be undrawable as text — no quoted words, no
  capitalised labels, no "a sign reading…" phrasing outside that block.
- Prop descriptions get a scrub pass in code before they reach the illustrator:
  strip quotation marks, strip ALL-CAPS tokens, and drop parentheticals (which
  in `venues.json` carry the *teaching* note, e.g. "(reuptake)" — exactly the
  kind of word that must never be painted).

Not all leakage is harmful — the courtroom's `EVIDENCE` bags are plausible
diegetic signage and the bakery volunteered `90% OF OUR STOCK IS IN THE BACK`,
which happens to be a real serotonin fact. The danger is that ungated strings
compete with gated ones and that stray numerals break the NUMBER gate. Hence:
leaked *words* are tolerated, leaked *numerals* are not (§3).

Mid-word capital highlighting (`expreSS tRIps`, `EXTRA`) carries ~33 corpus
cues and lives entirely in the Latin cue — keep it. Note that no current image
model does reliable on-demand mid-word colour highlighting; request it, do not
gate it.

---

## 5. Symbol derivation

The director must classify every element by mechanism, from a closed enum, in
**priority order**. Corpus frequencies from 113 classified items:

| # | Mechanism | Share | Example |
|---|---|---|---|
| 1 | `LITERAL_DEPICTION` | **~41%** | "Anxious coworker" = GAD · "Sticking out tongue" = tardive dyskinesia · "Passed out" = orthostatic hypotension |
| 2 | `PHONETIC_PUN` | ~22% | Parrot = paroxetine · Fax machine = venlaFAXine · Halo = haloperidol |
| 3 | `LEXICON_TOKEN` | ~18% | smiley post-it = serotonin, identical on every page |
| 4 | `MORPHEME_SPLIT` | — | Tri-cycle = TCA · EXTRA + pyramid hat = extrapyramidal · mad tea party on a MUSHroom = antiMUSCarinic |
| 5 | `PRINTED_TEXT` / `PRINTED_NUMBER` | — | "MINIMUM" scroll · "35" cart · "Narrow Window" sign |
| 6 | `COUNTED_SET` | — | 2 candles = α2 · 3 Z's = three z-drugs |
| 7 | `BORROWED_ARTWORK` | — | The Scream = panic disorder — only when the artwork *already depicts the fact*, never for atmosphere |
| 8 | `ACRONYM_EMBED` | — | expre**SS** t**RI**ps — acronym letters as an ordered subsequence |
| 9 | `FREE_METAPHOR` | **≤5%, last resort** | "frayed wire" = neuropathic pain — only when the term has no drawable morpheme, and then re-used corpus-wide so the cost is paid once |

Extensions needed for chapter types the corpus does not cover `[INVENTION]`:
`RULE_ON_A_SIGN` (a deontic rule as posted regulation with its exception beside
it — legal/ethical chapters), `CONTRAST_PAIR` (two adjacent near-identical
props differing in one attribute — capacity vs competence, nephritic vs
nephrotic), `NEGATION` (the visibly absent or crossed-out prop),
`GRAPH_AS_OBJECT` (p200's hair drawn as a decay curve).

### The hard ban

**Never mint a fresh evocative metaphor for an affective or clinical state.**
`[CORPUS, certain]` Zero of 113 items use it. Where a state must be shown,
exactly three moves in order: (a) a person literally exhibiting it, (b) a fixed
corpus-wide token, (c) a famous artwork that already depicts it.

This retires from `schedule/symbols.json`:
- `DSM_anxiety :: A FRIGHTENED RABBIT` → a person visibly anxious, or the fixed token
- `DSM_depressive :: A GRAY RAIN CLOUD over a slumped figure` → the happy/sad theatre masks, identical on p204/205/206/207/213
- `epi_prevalence :: a number-peg character holding a giant "%" sign` → an abstract data-viz convention with no corpus basis
- `epi_comorbidity :: ONE pair of CONJOINED TWINS` → import the co-occurring entity's **own** glyph physically into the scene, exactly as the red tricycle (TCAs) is parked in both the SSRI office and the MAOI cellar to mean "do not combine"

Also retires the anti-pattern *"✗ Children behaving like ADHD children
(literal, no symbolic substitution)"* — literal depiction is the corpus's
largest mechanism.

Our **drug** layer is already close to correct (Alprazolam = alpaca, Lorazepam
= LORE grandma, Midazolam = King MIDAS, Fluoxetine = fluorescent OX). The
failure is one level up: the *world* is chosen for bizarreness, so correctly
punned drugs get scattered into a venue that cannot supply their props.

---

## 6. Scene structure

**Hybrid: one main plate per chapter, plus a dedicated DSM-criteria plate.**
`[DECISION 2026-07-26]`

The corpus itself puts everything on one plate — p204 carries drug identity +
mechanism + every indication + every adverse effect in 33 rows on one image.
Per-aspect splitting is what orphaned our images and forced the weak
`who` / `comes_with` epidemiology split, which was never real anyway:
`build_image_jobs` at line 1111 hands *both* halves the identical unsplit facts
slice.

We keep one deviation from the corpus, deliberately: **DSM criteria get their
own plate.** They are memorised verbatim and recited under exam conditions, so
they earn a page the reader can hold separately from the chapter's world. Every
other aspect — epidemiology, etiology, treatment, course, comorbidity — goes on
the main plate.

So per chapter: `main` plate always, `criteria` plate when the chapter has DSM
criteria. Two renders, down from five to seven.

- `plates = ceil(N / 30)` within each of the two tracks, with a split threshold
  of ~45–50 rather than 34 (p210 carries 41 entries and p144 carries 40, both
  on one plate).
- **Legend overflow uses the reprint device, not a second scene.** p272/p273
  and p214/p215 are pixel-identical images reprinted with a continuing legend
  (verified at diff 0.00). Copy the PNG in code and continue the numbering.
- **Fact → element is many-to-one.** Every fact ID maps to exactly one element
  ID; an element may be referenced by several facts. `render_legend` emits one
  row per *fact* with the shared cue repeated verbatim (p210 repeats
  "Unbalanced stack" three times). Without this, shared class effects — the
  densest device in every drug-class chapter — are rejected.

### Zoning is per chapter type, not a fixed aspect map `[INVENTION]`

Chapter type is declared in schedule metadata, never inferred:

| Type | Zones |
|---|---|
| `CLINICAL_ENTITY` | identity = the anchor · who-gets-it · criteria · course/outcome · comorbidity |
| `PHARM_CLASS` | agents = the signage wall · mechanism · indications = the workstations · adverse effects = the periphery · toxidrome + antidote = one corner |
| `ANATOMY_CIRCUIT` | the anatomical compartments themselves, named by the director |
| `DEVELOPMENTAL` | age bands, count set by the data; legend in age order |
| `RULE_BASED` | actor roles · trigger conditions · exceptions |

The director may also propose a **mechanism-driven binary zoning** validated
against a closed enum (CNS/periphery, presynaptic/postsynaptic, acute/chronic,
high-potency/low-potency) — p214's vault-vs-lobby is the corpus's strongest
compartment mnemonic and a fixed aspect→zone table cannot express it.

Zoning is a soft grouping hint the director may override on physical-plausibility
grounds: p204 demonstrably does not zone by aspect at all — it puts props where
they plausibly live in an office.

---

## 7. Legend format

**The legend is rendered by code from the element table.** Not written by a
model. `CAPTION_INSTRUCTION` and the flowing-paragraph path are deleted; the
caption model is reduced to `annotate_hooks(table)`, whose only job is to fill
each element's ≤8-word `hook`.

```
<unnumbered>  TOPIC LINE          Major Depressive Disorder — אפידמיולוגיה | MDD
<unnumbered>  WALKTHROUGH         3 sentences, ≤45 Hebrew words:
                                  המקום · מה כולם עושים · האזורים
<blank>
N. <CUE>: <FACT> — <HOOK>
...
<unnumbered>  לא צויר             deliberately triaged facts
<unnumbered>  מספרים לבחינה        every exam figure with units and operators
```

- **CUE** — a bare noun phrase naming ONE prop, ≤6 words, no verb of
  perception. **Latin script wherever the pun lives in an English word** — a
  Hebrew cue cannot address a Latin string painted in the frame, and the
  phonetic joke cannot survive translation.
- **FACT** — a telegraphic Hebrew clinical assertion, ≤20 words, review-book
  register, clinical tokens in English (SSRI, D2, EPS, Bipolar, ODD). Where the
  fact has a figure, the exact figure appears verbatim; code asserts the numeric
  token from `facts.json` is present in the row.
- **HOOK** — ≤8 words (measure the cap against Hebrew before locking it), why
  this prop encodes this fact. `[INVENTION]` — our one deliberate addition to
  the corpus grammar, because our learners have no Sketchy video track. It is
  what turns an inventory into an explanation.
- Split on the **first** colon only; the hook may contain punctuation.
- `→` compresses a causal chain into ONE row. `(e.g. …)` for rosters.
- Quotation marks in a cue mean *"these exact words are painted in the picture,
  go find them"* — applied where the string is a sentence or a number, not
  blanket (p204 quotes exactly one cue and leaves Parrot, The City, 5H-TV
  unquoted).
- Ordering: a fixed syllabus template per chapter type; chronological for
  developmental. **Never spatial order.**

### The ban-list, corrected

The naive positional-word ban was the analysis's most damaging shared error —
it would reject the corpus's own addressing grammar: *"Barbershop next to
Cab-A"*, *"Beta Bugler in the middle"*, *"EXTRA pyramid hat on the roof"*,
*"Butter in front of liver tent"*.

- **Apply the ban to the FACT half only**, never the cue. Positional phrases in
  a cue are not narration — they are the *address*.
- Whitelist clinical laterality (left renal vein), inter-element relational
  prepositions where the relation is the mnemonic, and zone nouns where the
  zone contrast *is* the fact (p214 "rope in vault" vs "rope in lobby" = CNS vs
  periphery).
- Whitelist venue stock-prop and role nouns ("Snack drawer", "Fax machine",
  "Anxious coworker").
- What stays banned is the **reader-address register**: `we see · notice · in
  the background · depicts · symbolizes · represents · תמונה · סצנה · איור ·
  ברקע · נראה · אנו רואים · מסמל · מייצג`. The colon already does that work.
- **Validate the ban-list by running it over all 19 corpus legends first.** Any
  rule that rejects more than ~5% of authentic rows is wrong.

### Auto-generated quiz `[INVENTION]`

`<plate>.quiz.md` — the same numbered list with every fact half deleted. One
string operation, and it is the only artifact anywhere that drills *retrieval*
rather than familiarity. Ship it for every plate.

---

## 8. Code changes

### New: `scripts/sketchy_spec.py`

Owns everything a model must not be trusted with.

| Function | Job |
|---|---|
| `atomize(facts_slice)` | `→ [Fact(id, aspect, text, numeric_tokens, priority)]` |
| `normalize_number(str)` | `→ [NumberSpec(kind, paint, tag, unit, op, priority)]`, three-stage |
| `ELEMENT_TABLE_SCHEMA` | JSON schema for the director contract |
| `validate_table(table, facts)` | fact-ID coverage (many-to-one), cue uniqueness, closed derivation enum, cohesion-aware family check, carrier-class distinctness, `MAX_PAINTED_NUMBERS`, inanimate anchor, ≥12 figure elements, DROP budget |
| `build_image_prompt(table)` | deterministic tiered template |
| `render_legend(table, facts)` | topic line + walkthrough + rows + `לא צויר` + `מספרים לבחינה` |
| `render_quiz(table, facts)` | fact halves deleted |
| `plan_plates(facts)` | `ceil(N/30)`, reprint on legend overflow |

The element table replaces the free-prose `{"scene","key"}` contract:

```jsonc
{
  "venue": { "sign_text": "...", "place": "...", "activity": "..." },
  "anchor_structure": "...",
  "zones": ["..."],
  "elements": [{
    "id": "e1", "fact_ids": ["f1"], "cue": "...", "prop": "...",
    "zone": "...", "text": "...", "number": { "paint": "70%", "tag": "AGAIN", "carrier": "TICKET_STUB" },
    "derivation": "PHONETIC_PUN", "triple": ["vehicle", "metal", "yellow"],
    "relates_to": "e4", "relation": "BLOCKS", "hook": "..."
  }],
  "relations_budget": 6
}
```

### `scripts/gemini_image.py`

| Target | Change |
|---|---|
| `_STYLE_BASE` (~51) | inked props over soft-gradient shell; 12–25 figures at comparable scale; pack the frame edge to edge with ~1:1 filler; one shared ambient activity; constant bright register; pastiche quarantined inside a diegetic frame. Delete *"A FEW characters (about 3-6)… Not a crowd"*, *"CLEAN and UNCLUTTERED… parse in a few seconds"*, *"A specific moment in time: something is HAPPENING right now"*. Collapse the eight watercolour negations into one positive sentence plus ≤2 negations. |
| `_TEXT_RULES_MNEMONIC` (~71) | replace with the §4 block |
| `_TEXT_RULES_ENVIRONMENT` (~91) | **delete**, with the `text_policy` parameter everywhere |
| `LABEL VISIBILITY RULE` | **delete** |
| `_palette_directive` (~167) | remove from the Sketchy `full_prompt`; comment why |
| `CAPTION_INSTRUCTION` (~121), `generate_caption` (~272) | delete; replace with `annotate_hooks(table)` |
| `propose_anchor_theme` (~353) | → `propose_venue(topic)`; consult `venues.json` first; add `ACRONYM_EMBED`; **delete** the *"if there is NO strong pun… choose a bizarre world whose vibe matches the disorder"* fallback |
| `check_coverage` (~434) | **exists already** — call it with the full cue list per plate, not only criteria. Replace yes/no presence with forced-choice discrimination (three plausible distractors from other chapters); a presence probe over a cluttered cartoon reads ~100% regardless of quality. |
| new `check_text_strings` | probe *"list every readable string, verbatim, in quotation marks"*; match in Python. **No Levenshtein under 8 characters.** If a match was fuzzy, the legend must quote what the probe actually read or mark the row `[לא מצויר]`. |
| new `check_numbers` | **per-carrier**, not global: *"what digit-string, if any, is written on the `<carrier>`? Answer with the string or NONE."* Exact match. Reject **extra** numerals too — a render with all six required strings plus a hallucinated `88` currently passes. |
| new `check_binding` | *"for each numeral visible, name the object it is painted on"*; match numeral→carrier pairs against the table. Presence-only gating returns 100% on a render that paints every digit on the wrong object. |
| new `check_composition` | three coarse, reliably-answerable probes: is there one large inanimate structure crossing the frame diagonally · are all figures roughly the same size with none dominating · is the frame filled to all four edges |
| `generate_sketchy_directed` (~480) | → `generate_plate(plate)`: director → element table → validate (≤2 re-asks) → `build_image_prompt` → render → gates → **degradation ladder** → `render_legend` → write `.txt` + `.quiz.md` |

**Tier the prompt.** TIER 1 (venue sign, anchor structure, zone layout, ambient
activity, ≤8 required strings) restated verbatim at **both head and tail**;
TIER 2 (remaining props, filler, style negations) marked explicitly optional.
Under a ~⅓ silent-drop rate a flat 60–90 item list guarantees the anchor and
zoning are lost — which is the entire fix for complaint #1.

**Degradation ladder** (retries must not be i.i.d. resampling): try 2 restates
only the missing strings · try 3 drops the lowest-priority required strings and
re-renders with a shortened list · try 4 accepts. Code marks dropped strings
`drawn=false` so `render_legend` prints them as plain rows. Keep best-of-N on
total misses. Never fail the build.

### `scripts/daily_study.py`

| Target | Change |
|---|---|
| `SKETCHY_PHILOSOPHY` (~420) | rewrite per §2. Delete principles 1, 3, 4, 5, the opening claim that the power is *"NOT in literally illustrating clinical content"*, and the anti-patterns *"✗ Children behaving like ADHD children"* and *"✗ Floating informational labels"* |
| `NUMBER_RULES` (~452), `NUMBER_SHAPES` (~674) | **delete** both and every injection site, plus *"Never a written stat"* (~939) |
| `_ASPECT_STRUCTURE` (~568) | strip to a data table: legend order template + zone name per aspect. Delete *"just a FEW characters (2-4)"*, *"NOT a crowd"*, *"generous empty road/sky"*, *"a single narratable moment"*. The epidemiology entry is **dead code** — `build_director_instruction` returns at line 842 before `structure` is read at 851 |
| `EPI_HALVES` (~690), `build_epidemiology_director` (~736) | **delete**. The `who`/`comes_with` split is prose-only (line 1111 hands both halves the identical slice), `anchor_zone` is accepted and never used, and its *"ZERO TEXT / NO written numerals"* block is the direct cause of complaint #2 |
| `COMMON_COMORBIDITY_SYMBOLS` (~651) | move verbatim into `lexicon.json`. Keep the rate-less-comorbidity rule but enforce it in **code** via atomization — every comorbidity string becomes its own Fact with its own mandatory element — not by asking the model nicely |
| `build_director_instruction` (~826) | → `build_plate_director(plate)` emitting the element-table contract. Delete the anchor_world *"encode ONLY this aspect's facts"* block, the *"invent ONE bizarre world with NO literal connection to medicine"* fallback, the *"BELONGING / a real STORY is unfolding"* doctrine, and *"FORBIDDEN: a separate CHARACTER per fact"* (the corpus is exactly one fact per figure). Delete *"NO clinical terms — the illustrator must never see insomnia or prevalence"*: the illustrator now gets a code-built prop list and a TEXT TO PAINT list that may contain clinical acronyms painted on objects |
| `build_image_jobs` (~1096), loop (~1740–1795) | → `sketchy_spec.plan_plates(facts)`. Drop `text_policy`; raise the retry budget to 4 for every plate (total renders per chapter *falls*, from ~5–7 to 1–2 plates); write `sketchy_report.json` with all gate scores |

### New data files

- **`schedule/venues.json`** — 144 hand-authored entries `{index, sign_text,
  place, activity, anchor_structure, stock_props[], adjacent_to[]}`. **Required
  up-front deliverable**, not a safety net. Budget the manual work.
- **`schedule/lexicon.json`** — `{concept_key: {cue, prop, hook, version}}`,
  versioned and append-only, with a `pending` pool the pipeline appends to.

### Downstream

File naming moves from `sketchy_<aspect>.png` to `sketchy_plate<N>.png`.
`app/app.py`, the release assets and any calendar references must be updated in
the same commit. Also fix the existing cosmetic bug: the viewer prints raw
technical asset names (`epidemiology_who`, `dsm_criteria_`) as Hebrew-page
headings.

---

## 9. Verification

Gates run in this order; **none may fail the build**.

| Gate | Probe | Match | Action on miss |
|---|---|---|---|
| NUMBER | per-carrier: *"what digit-string is on the `<carrier>`?"* | exact, multiset-with-position; reject extras | re-render naming the string + carrier |
| BINDING | *"for each numeral, name the object it is on"* | pair match vs table | re-render |
| TEXT | *"list every readable string, verbatim"* | exact → casefold; no Levenshtein <8 chars | re-render; else quote what was read or `[לא מצויר]` |
| COMPOSITION | 3 coarse structural questions | boolean | re-render |
| ELEMENT | forced-choice discrimination over cues | ≥ threshold | log |
| COUNT-PARITY | *"how many figures at the left station?"*, run twice | agreement required | **warning only** at first; annotate the legend, do not hard-reject |

Report `covered/total` per gate into `sketchy_report.json` so 144 chapters
produce a measurable quality series and a systematically miscalibrated probe
becomes visible.

**DROP budget:** hard-warn when >15% of a plate's facts return `DROP` or come
back `[לא מצויר]`. As currently specified, a legal/ethical plate that drops 70%
of its facts passes every gate and logs green.

**Resolution is the binding constraint and must be decided, not noted.**
Current renders are 1376×768. A 3.1%-of-height glyph — the measured size of
p207's `52` — is 24 px at that height, right at the legibility floor before any
model garbling. Either raise the render resolution or cap at ~22 elements and
~8 required strings. Without an explicit trade, the first v2 render will be a
smaller version of the same failure.

---

## 10. Worked example — MDD, epidemiology zone

**Venue** `[CORPUS mechanism: p272 "MINIMUM"]`. An open-pit quarry: *depression*
is literally a hollow in the ground. Gate sign hand-lettered **"MAJOR PIT"** —
one morpheme of the diagnosis on a prop, never the full diagnosis. Mundane,
physically coherent, and it ships a large stock inventory of free fact-slots:
depth markers, haul trucks, hard hats, a skip hoist, pit-head sheds, ticket
punches, a safety-notice board.

**Anchor** — the terraced pit face, corner to corner on a diagonal, inanimate,
the largest thing in frame. **Zones** top to bottom: rim (general population) ·
upper terrace (children) · lower terrace (adolescents) · pit floor
(comorbidities) · ramp and hoist (course and outcome). **Ambient activity** —
everyone is working an ordinary quarry shift. ~18 figures at comparable scale,
frame packed edge to edge with quarry filler at ~1:1.

**Painted numbers — 3, at the cap, each tagged, each on a semantically
compatible carrier:**

| String | Tag | Carrier | Fact |
|---|---|---|---|
| `70%` | `AGAIN` | punched return ticket held by a worker walking back **down** the ramp | recurrence 70% |
| `40-60%` | `GENES` | haul-truck door, four near-identical faces crammed in the cab | heritability 40–60% |
| `4-8%` | `TEENS` | depth plate on the deeper terrace, crowded with teenagers | adolescent prevalence |

Child prevalence (`2%`) is carried by **magnitude partner** instead — the
shallow upper terrace holds visibly few children against the crowded lower
terrace, the p272-heaped-cart vs p274-scattered-scraps device. It appears in
the legend and in `מספרים לבחינה`.

Sex ratio 1:2 is **`drawn=false`** and legend-only — the counted-set route is
unverifiable inside an 18-figure frame, and claiming it is drawn is exactly the
shipped bug where `who.png` drew ~7:1 male while the caption asserted 1:2.
Under Path B it becomes paintable as `1:2` + tag `M:F` on a single shift-board.

Bipolar conversion 20–40% is over the 3-number cap → the grinning hard-hatted
figure winched **up and out** of the pit (fixed mania token + direction), number
in the legend.

**Other elements** (one fact each, `LEXICON_TOKEN` unless noted): trembling
worker clutching a white rabbit = anxiety · blurred sprinting worker with a
pinwheel in his hat band = ADHD · scowling worker tearing the safety notice,
`"RULES"` still legible = ODD/CD · slumped worker with a brown bottle at his
boot = SUD · a thin line of townspeople on the rim, none descending = the
denominator · stair sign `"THE DROP"` = puberty, where both the rate and the
sex ratio change (`PRINTED_TEXT`; adjacency is the fact).

Six required strings total — `MAJOR PIT`, `70% AGAIN`, `40-60% GENES`,
`4-8% TEENS`, `RULES`, `THE DROP` — well under the 18 cap and within reach at
768 px.

**Legend as `render_legend` emits it:**

```
Major Depressive Disorder — אפידמיולוגיה | MDD
המקום: מחצבה פתוחה. מה כולם עושים: משמרת עבודה רגילה — כניסה, ירידה, שינוע, הרמה.
האזורים: השפה · המדרגות · רצפת הבור · הרמפה והמנוף.

1.  "MAJOR PIT" gate sign: הלוח עוסק ב-Major Depressive Disorder — depression = שקע בקרקע.
2.  Depth plate "4-8% TEENS": שכיחות MDD בגיל ההתבגרות 4-8% — המספר על שלט עומק: כמה עמוק יורדים.
3.  Sparse upper terrace: שכיחות בילדים ~2% — המדרגה הרדודה, ומעטים עומדים עליה.
4.  "THE DROP" stair sign: ההתבגרות היא המדרגה — בה משתנות גם השכיחות וגם חלוקת המינים.
5.  Haul-truck door "40-60% GENES": תורשתיות MDD 40-60% — ארבע פנים כמעט זהות נוסעות במשאית שנושאת את המספר.
6.  Return ticket "70% AGAIN": ~70% יחוו הישנות (recurrence) — כרטיס חזור, והוא חוזר לתוך הבור.
7.  Hard hat on the hoist: 20-40% יעברו ל-Bipolar — מונף כלפי מעלה ויוצא מהבור.
8.  White rabbit: תחלואה נלווית — הפרעות חרדה.
9.  Pinwheel in the hat band: תחלואה נלווית — ADHD.
10. Torn "RULES" board: תחלואה נלווית — ODD / Conduct Disorder.
11. Bottle at the boot: תחלואה נלווית — Substance Use Disorder.
12. Rim crowd looking in: רוב האוכלוסייה לעולם לא יורדת — המכנה.

לא צויר: יחס המינים 1:1 לפני ההתבגרות, 1:2 (M:F) אחריה. ההישנות מתרחשת תוך 5 שנים.

מספרים לבחינה: שכיחות ילדים ~2% · מתבגרים 4-8% · תורשתיות 40-60% ·
הישנות ~70% תוך 5 שנים · המרה ל-Bipolar 20-40% · יחס מינים 1:1 → 1:2 (M:F).
```

Note what the legend never does: it never says quarry, pit, terrace, left,
right, background, or "symbolizes". It never describes the picture. Every row
is an exam fact with a findable verbal address and a one-clause derivation —
and every number is stated verbatim whether or not it reached the pixels.

---

## Open risks

- **`venues.json` is 144 entries of manual work** and is a hard prerequisite.
  Without it the fallback is "the model forces a weak pun", which is the
  failure being removed.
- **One plate per chapter concentrates risk.** Mitigate with the reduced-element
  fallback (top-20 elements) after the 4th failed try, and keep the previous
  plate on disk.
- **RTL/LTR mixing** in the legend (Latin cue, Hebrew fact) will render badly in
  some viewers. Force LTR isolation marks in code; expect layout bugs in Excel
  and Streamlit.
- **Every epidemiology rule here is extrapolation** — see §3. Revisit after the
  first ten chapters ship.
- **Hook quality is a single point of failure.** If the hook model produces
  "this represents depression", the legend degrades into the inventory prose
  being replaced. Ban `represents/symbolizes/מסמל/מייצג` in the hook field and
  require the hook to name a derivation mechanism from the enum.
