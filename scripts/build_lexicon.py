#!/usr/bin/env python3
"""
Build schedule/lexicon.json — the versioned, append-only cross-chapter symbol
lexicon required by SKETCHY_SPEC_V2.md §R9.

Why this file exists separately from symbols.json: a given symbol must mean
exactly the same fact on every page it appears, so half the vocabulary is
already owned before a new chapter opens. That makes it a ONE-WAY DOOR — once
"bee swatter = H1" ships in an early chapter, changing the glyph in chapter 90
silently invalidates every chapter the resident already studied. So the lexicon
is versioned, entries are append-only, and retiring one requires an explicit
`retired` record naming its replacement.

It also implements the 2026-07-26 decision that the course's own 17 hand-built
scenes are LEXICON TOKENS, not venues. The Aa Hotel, the Raphe tapestry and the
DOPA-MINE colliery are receptors and nuclei: they must appear as props INSIDE
whichever venue a chapter draws, never in place of it.

symbols.json is read, never written. Run:  python scripts/build_lexicon.py
"""
from __future__ import annotations

import json
from pathlib import Path

import sys
# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "schedule" / "symbols.json"
OUT = ROOT / "schedule" / "lexicon.json"

LEXICON_VERSION = 1

# Vocabulary lifted from the SketchyMedical corpus itself (19 pages, 113
# classified legend items). A borrowed convention costs the learner nothing to
# decode; an invented one costs a lookup every time — which is why these
# replace our own metaphors below.
CORPUS_TOKENS = {
    # molecules and receptors
    "serotonin":            ("Smiley post-it", "a yellow smiley-face sticky note", "the corpus's fixed 5-HT token — p204/205/206"),
    "norepinephrine":       ("N-compass", "a compass rose with the N arm marked", "N for norepinephrine; same glyph on every page"),
    "dopamine":             ("Rope", "a coiled hemp rope", "rope = dopamine across the corpus; count the strands for the subtype"),
    "D2_blockade":          ("Snapping double rope", "a two-strand rope snapping apart", "two strands = D2; snapping = blockade"),
    "alpha1_blockade":      ("Extinguished alpha flame", "a single guttered candle, smoke rising", "one candle = alpha-1; blown out = antagonist"),
    "H1_blockade":          ("Bee swatter", "a yellow fly-swatter mid-swing at a bee", "the bee is histamine; swatting it is the block"),
    # adverse effects and pharmacokinetics
    "parkinsonism":         ("Cogwheels", "meshed gear teeth on a wheel rim", "cogwheel rigidity, drawn as the word says"),
    "torsades":             ("Twisted streamer", "a white streamer wound into a twist", "torsades de pointes = twists"),
    "long_half_life":       ("Long tapering flag", "a very long pennant narrowing to a point", "the tail that takes forever to end"),
    "cyp450_induction":     ("CYP-450 plate", "a vehicle licence plate lettered CYP 450", "the corpus letters the enzyme onto a plate"),
    "ataxia":               ("Unbalanced stack", "a leaning stack of crates about to go", "loss of balance, drawn literally"),
    "teratogenic":          ("Tarantula", "a large hairy spider on a surface", "the corpus's fixed teratogen warning"),
    "drug_induced_lupus":   ("Lupus wolf", "a grey wolf standing in the scene", "lupus is Latin for wolf"),
    "agranulocytosis":      ("Sand timer", "an hourglass with the sand nearly out", "counts running down — the weekly blood count"),
    "neuropathic_pain":     ("Frayed wire", "an electrical cable with its strands splayed", "a nerve stripped of insulation"),
    "eps":                  ("EXTRA pyramid hat", "a folded-newspaper pyramid hat", "EXTRA-pyramidal, split into its two morphemes"),
    # clinical states — a person exhibiting it, or a borrowed artwork
    "depression_state":     ("Happy and sad masks", "the paired theatre masks on a wall", "the conventional mask pair — p204/205/206/207/213"),
    "panic_disorder":       ("The Scream", "Munch's The Scream on a screen or poster", "an artwork that already depicts the fact"),
    "anxiety_state":        ("Anxious bystander", "a person visibly tense, wringing their hands", "literal depiction — the corpus's largest mechanism"),
    "ocd_state":            ("Obsessively neat worker", "a person squaring up already-square items", "literal depiction, never an invented symbol"),
    "social_anxiety":       ("Shy guy", "a person half-hidden at the edge of a group", "literal depiction"),
    "ptsd":                 ("PT and SD dog tags", "two military dog tags", "the acronym split across two tags"),
    "siadh":                ("ADH jug", "a large water jug being poured over someone", "the acronym lettered on the jug"),
    # drug classes
    "tca_class":            ("Red tricycle", "a child's red tricycle", "TRI-cyclic, drawn as a tricycle"),
    "maoi_class":           ("Mousetrap", "a wooden snap mousetrap", "MAO ~ mouse; the trap is the irreversible inhibition"),
}

# Invented metaphors we are retiring. Across 113 corpus items, ZERO mint a
# fresh evocative metaphor for an affective state — the corpus uses a person
# exhibiting it, a fixed token, or an artwork that already depicts it.
RETIRED = {
    "DSM_anxiety": ("anxiety_state",
                    "a frightened rabbit is an invented metaphor with no corpus "
                    "precedent; draw a person who is visibly anxious"),
    "DSM_depressive": ("depression_state",
                       "a grey rain cloud is invented; the corpus uses the "
                       "happy/sad theatre masks, identically, on five pages"),
    "epi_prevalence": ("",
                       "a peg character holding a giant % sign is a data-"
                       "visualisation convention layered on the scene; "
                       "prevalence is a painted numeral on a diegetic carrier"),
    "epi_comorbidity": ("",
                        "conjoined twins encode the RELATION abstractly; import "
                        "the co-occurring entity's own glyph into the scene "
                        "instead, as the corpus parks the TCA tricycle in the "
                        "SSRI office"),
}


def main() -> int:
    src = json.loads(SRC.read_text(encoding="utf-8"))
    tokens: dict[str, dict] = {}

    # 1. the course's 17 scenes — container tokens that live INSIDE a venue
    for key, sc in src.get("scenes", {}).items():
        tokens[key] = {
            "cue": sc.get("scene_name", key).split("—")[0].strip(),
            "prop": sc.get("setting", "").strip(),
            "hook": "course-wide fixed structure; stands inside the chapter's venue",
            "kind": "SCENE_TOKEN",
            "elements": sc.get("recurring_elements", [])[:12],
            "source": "symbols.json:scenes",
            "since": LEXICON_VERSION,
        }

    # 2. the 156 receptor/molecule symbols, which already name their scene
    for key, sym in src.get("symbols", {}).items():
        if not isinstance(sym, dict) or not sym.get("symbol"):
            continue          # the file uses string values as section rulers
        tokens[key] = {
            "cue": sym["symbol"].split("—")[0].split(":")[0].strip()[:60],
            "prop": sym["symbol"].strip(),
            "hook": (sym.get("mnemonic_link") or "").strip()[:120],
            "kind": "SYMBOL",
            "scene": sym.get("scene", ""),
            "category": sym.get("category", ""),
            "source": "symbols.json:symbols",
            "since": LEXICON_VERSION,
        }

    # 3. the corpus's own vocabulary
    for key, (cue, prop, hook) in CORPUS_TOKENS.items():
        tokens[key] = {
            "cue": cue, "prop": prop, "hook": hook,
            "kind": "SYMBOL", "source": "sketchy-corpus",
            "since": LEXICON_VERSION,
        }

    retired = {}
    for key, (replacement, why) in RETIRED.items():
        retired[key] = {"replaced_by": replacement, "why": why,
                        "retired_in": LEXICON_VERSION}
        tokens.pop(key, None)

    doc = {
        "version": LEXICON_VERSION,
        "policy": (
            "APPEND-ONLY. A token's cue and prop may never be changed once "
            "shipped — a resident who studied chapter 12 must find the same "
            "glyph in chapter 90. To replace one, add the new key and record "
            "the old under `retired` with `replaced_by`."
        ),
        "usage": (
            "Code substitutes these into the element table; it does not suggest "
            "them to a model. SCENE_TOKEN entries are props INSIDE the "
            "chapter's venue, never the venue itself."
        ),
        "tokens": dict(sorted(tokens.items())),
        "retired": retired,
        "pending": {},
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    kinds: dict[str, int] = {}
    for t in tokens.values():
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    print(f"wrote {OUT}")
    print(f"  tokens : {len(tokens)}  ({kinds})")
    print(f"  retired: {len(retired)} — {', '.join(retired)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
