#!/usr/bin/env python3
"""
The code-owned core of the Sketchy v2 plate pipeline (see SKETCHY_SPEC_V2.md).

v1's defect was that nothing bound fact -> named visual element -> rendered
pixel. The director wrote free prose, the illustrator never saw the facts, and
the caption was written from a vision probe of the render. So nothing was
addressable, countable or verifiable.

This module owns everything a model must not be trusted with:

  atomize()          facts.json  -> a flat list of Facts with stable ids
  normalize_number() a fact's numerals -> exact strings to paint, with tags
  plan_plates()      Facts -> plates (criteria / profile / management),
                     each carrying only the facts worth drawing
  validate_table()   the director's element table -> a list of hard errors
  build_image_prompt()  element table -> the illustrator prompt, deterministic
  render_legend()    element table -> the numbered `cue: fact — hook` legend
  render_quiz()      the same list with every fact half deleted

Nothing here calls a model. Everything here is testable:  python sketchy_spec.py
runs the whole chain over a facts.json and prints what it produced.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fact_triage        # noqa: E402  — scores how forgettable a fact is

# The one thing the prop scrubber must not touch. Shared with the triage on
# purpose: a token that is examinable enough to force a fact onto the plate is
# exactly the token that must survive the trip to the illustrator intact.
# Ordinary capitalised words caught by the acronym branch keep their capitals
# too, which is the right trade — the alternative is CYP2D6 arriving as "Cypd".
RECEPTOR_TOKEN = fact_triage.IDENTIFIER
# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── plate geometry ─────────────────────────────────────────────────────────
# Density follows RESOLUTION, because legibility is the product of the two and
# nothing else. At 2K a glyph on a 30-element plate is comfortable; at 1K the
# same plate lands it near 24px, the floor before the model has garbled
# anything. Every FREE route to these models is 1K — the Antigravity CLI, the
# flash-image daily tier, the Gemini app — so choosing the free engine has to
# buy fewer elements per scene rather than smaller letters.
#
# More plates is not the cost it looks like. The complaint that started this
# was plates carrying so much that nothing on them stood out.
_TIGHT = (os.environ.get("SKETCHY_ENGINE", "api").strip().lower() == "agy"
          or os.environ.get("SKETCHY_IMAGE_SIZE", "2K").strip().upper() == "1K")

FACTS_PER_PLATE = 18 if _TIGHT else 30   # target density; corpus runs 15-50
PLATE_SPLIT_AT = 22 if _TIGHT else 45    # split into another plate past this
MIN_PLATE_FACTS = 6        # below this a track is folded, not given its own scene


# ── which plate a fact belongs on ──────────────────────────────────────────
# Three plates, per the 2026-08-27 decision: the criteria, then WHO AND WHAT
# (who gets it, why, how it shows up) and WHAT YOU DO (treat, measure, watch).
#
# This is not a cosmetic reorganisation. One plate carrying 22-34 elements is
# what forced the render up to 2K, and 2K is paid-only on every route; a third
# of the elements on the same canvas is three times the glyph size, which is
# what puts the free 1K quota back within reach. Density is the lever.
#
# The aspect vocabulary is OPEN — 161 distinct names across the 144 chapters,
# and about a hundred of them occur exactly once ("wernicke_korsakoff",
# "duty_to_warn", "conversion_oral_to_LAI"). A lookup table can only hold the
# ones I thought of, and the chapter it misses is the one that lands on the
# wrong plate without saying so. So the rule matches STEMS, and anything it
# cannot place goes to the descriptive plate and is REPORTED — an unplaceable
# aspect should be a line of output, not a silent default.

# What a clinician DOES: choose it, give it, measure it, watch for it.
_MANAGEMENT_STEMS = (
    # "medic" would be the obvious stem and it is a trap: it swallows
    # "medical_mimics", which 48 chapters carry and which is a differential —
    # what else could look like this — not a thing anyone does.
    "treat", "therap", "manage", "pharmac", "medicat", "dosin", "dosag",
    "indication", "monitor", "augment", "restraint", "escalation", "protocol",
    # "restrict" swallows "restricted_interests", which is an autism symptom.
    # "restriction" keeps means_restriction and dietary_restrictions.
    "hygiene", "planning", "prevent", "restriction", "admission", "stabiliz",
    "workup", "assessment", "tool", "scale", "screen", "interview",
    "examination", "practice", "technique", "method", "intervention",
    "discontinu", "conversion", "refeeding", "safety", "challenge",
    "side_effect", "toxicity", "overdose", "lethality", "commitment",
    "habit_reversal", "means_", "fbt", "cbt", "ciwa", "y_bocs", "lai_",
    "bush_francis", "evidence", "practical_use", "emergency",
)

# The criteria themselves — the letters and thresholds a resident recites.
_CRITERIA_STEMS = (
    "criteri", "sigecaps", "specifier", "symptom_cluster", "cluster_overview",
    "4_symptom", "threshold",
)


# "criteria" is not always the DSM's. Admission criteria are a decision about
# what to DO with a patient, and they have no lettered clusters to recite; on
# the criteria plate they would sit beside Criterion A looking like one.
_NOT_DSM_CRITERIA = ("admission", "discharge", "referral", "eligibilit")


def track_for_aspect(aspect: str) -> str:
    """'criteria' | 'management' | 'profile' for one facts.json key."""
    a = (aspect or "").strip().lower()
    if any(s in a for s in _CRITERIA_STEMS) and \
            not any(s in a for s in _NOT_DSM_CRITERIA):
        return "criteria"
    if any(s in a for s in _MANAGEMENT_STEMS):
        return "management"
    return "profile"


def unplaceable_aspects(aspects) -> list[str]:
    """Aspects that fell to the default rather than matching a stem.

    Not an error — 'anatomy' and 'clinical_presentation' belong on the profile
    plate and reach it this way. It exists so a genuinely miscategorised
    one-off is visible in the run log instead of being absorbed in silence.
    """
    return [a for a in aspects
            if not any(s in (a or "").lower()
                       for s in _CRITERIA_STEMS + _MANAGEMENT_STEMS)]
MAX_PAINTED_NUMBERS = 3    # per plate. 0 is an explicitly legal outcome.
MAX_REQUIRED_STRINGS = 11 if _TIGHT else 18   # gated strings, <=5 words each
MAX_RELATIONS = 6          # bounded pairwise relations; never a plot
# Cue length. Six was guesswork and it rejects the corpus's own grammar:
# "Alpha1 scout w/ single candle up high" is 7 words, "Prevented from picking up
# smiley face and compass balls" is 9. Capping at 6 burned re-asks on every
# plate for cues that a real Sketchy legend would print unchanged. Nine still
# forbids a sentence, which is the thing the cap is actually for.
MAX_CUE_WORDS = 9

# Chroma keys for deterministic number compositing (Path B, §3). Each painted
# number gets its own key so detection is a pure hue mask — see
# _proto_number_plate.py, which owns the detection and warping.
CHROMA_KEYS = [
    ("PURE GREEN", (0, 177, 64)),
    ("PURE MAGENTA", (255, 0, 255)),
    ("PURE CYAN", (0, 255, 255)),
]

DERIVATIONS = (
    "LITERAL_DEPICTION",   # ~41% of the corpus — a person doing the thing
    "PHONETIC_PUN",
    "LEXICON_TOKEN",
    "MORPHEME_SPLIT",
    "PRINTED_TEXT",
    "PRINTED_NUMBER",
    "COUNTED_SET",
    "BORROWED_ARTWORK",
    "ACRONYM_EMBED",
    "RULE_ON_A_SIGN",
    "CONTRAST_PAIR",
    "NEGATION",
    "GRAPH_AS_OBJECT",
    "FREE_METAPHOR",       # last resort, capped
)
FREE_METAPHOR_CAP = 0.05

RELATIONS = ("ADJACENT_TO", "BLOCKS", "ACTS_ON", "FALLS_FROM", "CONTAINED_IN")

# The director reaches for domain verbs the enum does not contain: `progresses to`,
# `inhibits`, `predicts`. Three table attempts burned on chapter 28 because of this.
# Rather than teach the director our vocabulary, accept synonyms and normalize them.
RELATION_SYNONYMS = {
    # transitive verbs the model naturally reaches for, mapped to the closest enum
    "INHIBITS": "BLOCKS", "PREVENTS": "BLOCKS", "STOPS": "BLOCKS",
    "ANTAGONIZES": "BLOCKS", "ANTAGONISES": "BLOCKS", "SUPPRESSES": "BLOCKS",
    "PROGRESSES_TO": "ACTS_ON", "LEADS_TO": "ACTS_ON", "CAUSES": "ACTS_ON",
    "CONVERTS_TO": "ACTS_ON", "TRANSFORMS": "ACTS_ON", "TRIGGERS": "ACTS_ON",
    "PREDICTS": "ACTS_ON", "INDICATES": "ACTS_ON", "PRECEDES": "ACTS_ON",
    "TRANSPORTS": "ACTS_ON", "RELEASES": "ACTS_ON", "POTENTIATES": "ACTS_ON",
    "NEXT_TO": "ADJACENT_TO", "BESIDE": "ADJACENT_TO", "NEAR": "ADJACENT_TO",
    "INSIDE": "CONTAINED_IN", "WITHIN": "CONTAINED_IN", "PART_OF": "CONTAINED_IN",
    "DROPS_FROM": "FALLS_FROM", "DESCENDS_FROM": "FALLS_FROM",
    "PRODUCES": "ACTS_ON", "MAKES": "ACTS_ON", "DRIVES": "ACTS_ON",
    "COMBINED_WITH": "ADJACENT_TO", "PAIRED_WITH": "ADJACENT_TO",
    "ACCOMPANIES": "ADJACENT_TO", "ACCOMPANIED_BY": "ADJACENT_TO",
    "OCCURS_WITH": "ADJACENT_TO", "CO_OCCURS_WITH": "ADJACENT_TO",
    "SIMILAR_TO": "ADJACENT_TO", "CONTRASTS_WITH": "ADJACENT_TO",
    "FOLLOWS": "ADJACENT_TO", "REPLACES": "ADJACENT_TO",
    "TOGETHER_WITH": "ADJACENT_TO", "COMPARED_TO": "ADJACENT_TO",
    # PASSIVE verbs name the same relation from the other end. Mapping them to
    # ACTS_ON would silently reverse who does what to whom, which the picture
    # then draws backwards, so they degrade to plain adjacency instead.
    "AFFECTED_BY": "ADJACENT_TO", "ACTED_ON_BY": "ADJACENT_TO",
    "MODULATED_BY": "ADJACENT_TO", "BLOCKED_BY": "ADJACENT_TO",
    "CAUSED_BY": "ADJACENT_TO", "PRODUCED_BY": "ADJACENT_TO",
    "RECEIVES_FROM": "ADJACENT_TO", "DEPENDS_ON": "ADJACENT_TO",
}

# The same medicine for the derivation enum. The director reaches for the word
# that describes what it did, not for the token in our list, and a whole table
# was never worth losing over the difference.
DERIVATION_SYNONYMS = {
    "LITERAL": "LITERAL_DEPICTION", "DEPICTION": "LITERAL_DEPICTION",
    "LITERAL_DEPICTION_OF_ACTION": "LITERAL_DEPICTION",
    "PUN": "PHONETIC_PUN", "SOUND_PUN": "PHONETIC_PUN",
    "HOMOPHONE": "PHONETIC_PUN", "WORDPLAY": "PHONETIC_PUN",
    "LEXICON": "LEXICON_TOKEN", "FIXED_SYMBOL": "LEXICON_TOKEN",
    "COURSE_TOKEN": "LEXICON_TOKEN",
    "MORPHEME": "MORPHEME_SPLIT", "WORD_SPLIT": "MORPHEME_SPLIT",
    "TEXT": "PRINTED_TEXT", "LABEL": "PRINTED_TEXT", "SIGN": "PRINTED_TEXT",
    "NUMBER": "PRINTED_NUMBER", "NUMERAL": "PRINTED_NUMBER",
    "PRINTED_DIGITS": "PRINTED_NUMBER",
    "COUNT": "COUNTED_SET", "COUNTING": "COUNTED_SET", "SET": "COUNTED_SET",
    "ARTWORK": "BORROWED_ARTWORK", "PAINTING": "BORROWED_ARTWORK",
    "ACRONYM": "ACRONYM_EMBED", "MNEMONIC": "ACRONYM_EMBED",
    "RULE": "RULE_ON_A_SIGN", "NOTICE": "RULE_ON_A_SIGN",
    "CONTRAST": "CONTRAST_PAIR", "COMPARISON": "CONTRAST_PAIR",
    "OPPOSITE": "CONTRAST_PAIR", "NEGATIVE": "NEGATION",
    "GRAPH": "GRAPH_AS_OBJECT", "CHART": "GRAPH_AS_OBJECT",
    "GRAPH_OBJECT": "GRAPH_AS_OBJECT",
    "METAPHOR": "FREE_METAPHOR", "ANALOGY": "FREE_METAPHOR",
}

# Objects whose real-world job is to bear digits (§3 stage 3).
CARRIERS = (
    "JERSEY_OR_BIB", "GATE_OR_PLATFORM_NUMBER", "PRICE_OR_MENU_BOARD",
    "LICENCE_OR_VEHICLE_DOOR", "DEPTH_OR_LEVEL_MARKER", "WALL_CALENDAR",
    "SCOREBOARD_OR_TALLY", "ROUTE_OR_LINE_SHIELD", "MILEPOST", "TICKET_STUB",
    "WEIGHT_OR_PRESSURE_DIAL", "ADDRESS_ON_A_BUILDING",
    "HARD_HAT_OR_HELMET_NUMBER", "CHANNEL_OR_STATION_LOGO",
)

# A carrier whose native numeric meaning fights the unit teaches the wrong
# thing: a depth marker reading "2" reads as two metres, a milepost reading
# "5" reads as distance, not years.
CARRIER_NATIVE_UNIT = {
    "DEPTH_OR_LEVEL_MARKER": "depth", "MILEPOST": "distance",
    "WALL_CALENDAR": "time", "WEIGHT_OR_PRESSURE_DIAL": "weight",
    "PRICE_OR_MENU_BOARD": "money", "ADDRESS_ON_A_BUILDING": "address",
}


# ── facts ──────────────────────────────────────────────────────────────────
@dataclass
class Fact:
    id: str            # stable address, e.g. "epidemiology.comorbidities[2]"
    aspect: str        # top-level facts.json key
    subkey: str        # second-level key
    text: str
    track: str         # "criteria" | "profile" | "management"
    priority: int      # exam yield, drives number triage
    numbers: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["numbers"] = [asdict(n) for n in self.numbers]
        return d


@dataclass
class NumberSpec:
    kind: str          # SCALAR | RANGE | RATIO | THRESHOLD | DURATION | COUNT
    paint: str         # the exact characters to render, e.g. "40-60%"
    tag: str           # 1-4 char referent, painted on the same carrier
    unit: str
    op: str            # stripped from the picture, kept for the legend
    priority: int
    drawn: bool = False
    carrier: str = ""


# Exam yield. Number triage is mandatory and mechanical: only the top
# MAX_PAINTED_NUMBERS survive into the picture, the rest stay in the legend.
_PRIORITY = [
    (r"criteria\.thresholds", 100),
    (r"epidemiology\.prevalence", 90),
    (r"epidemiology\.heritability", 80),
    (r"epidemiology\.prognosis", 70),
    (r"treatment\.monitoring", 60),
    (r"criteria\.clusters", 55),
    (r"epidemiology\.comorbidities", 50),
    (r"treatment\.first_line", 50),
    (r"epidemiology\.sex_ratio", 40),
    (r"epidemiology\.onset", 35),
    (r"high_yield", 65),
]


def _priority(fid: str) -> int:
    for pat, p in _PRIORITY:
        if re.match(pat, fid):
            return p
    return 20


# Short referent tags, so a painted numeral is not a floating digit. p207
# letters "52" with "HT" beneath it on the same banner, and that tag is the
# only reason the numeral survives a frame full of decorative jersey numbers.
_TAGS = [
    (r"prevalence", "RATE"), (r"heritability", "GENES"),
    (r"prognosis", "AGAIN"), (r"thresholds", "MIN"),
    (r"sex_ratio", "M:F"), (r"onset", "AGE"),
    (r"monitoring", "WKS"), (r"comorbid", "WITH"),
]


def _tag_for(fid: str, unit: str) -> str:
    for pat, t in _TAGS:
        if re.search(pat, fid):
            return t
    return {"%": "PCT", "yr": "YRS"}.get(unit, "N")


# Numeric token grammar. Deliberately keeps the decimal point (painting "06-12"
# for a 0.6-1.2 lithium level would teach a wrong fact) and keeps "%" where the
# carrier permits, because a bare "2" on a depth marker does not recover "2%".
_NUM = re.compile(
    r"(?P<op>>=|<=|[<>≤≥~≈]|\bעד\b)?\s*"
    r"(?P<a>\d+(?:[.,]\d+)?)"
    r"(?:\s*(?P<sep>[-–—:/]|\s+to\s+)\s*(?P<b>\d+(?:[.,]\d+)?))?"
    r"\s*(?:ה)?(?P<unit>%|שנים|שנה|שבועות|שבוע|חודשים|חודש|ימים|יום|שעות|שעה|"
    r"דקות|דקה|mg|g/day|mEq/L|"
    r"years?|weeks?|months?|days?|hours?|hrs?|mins?|minutes?)?",
    re.I,
)

_UNIT_CANON = {
    "שנים": "yr", "שנה": "yr", "years": "yr", "year": "yr",
    "שבועות": "wk", "שבוע": "wk", "weeks": "wk", "week": "wk",
    "חודשים": "mo", "חודש": "mo", "months": "mo", "month": "mo",
    "ימים": "d", "יום": "d", "days": "d", "day": "d",
    # Hours were in the regex but not in this table, so "t½ של 26 שעות" painted
    # a bare "26" tagged N. The spec is explicit that a figure without its
    # referent recovers nothing: a reader who encodes "26" has encoded a digit.
    "שעות": "h", "שעה": "h", "hours": "h", "hour": "h", "hrs": "h", "hr": "h",
    "דקות": "min", "דקה": "min", "minutes": "min", "minute": "min",
    "mins": "min", "min": "min",
}

# A publication or approval year is not a quantity. "אזהרת ה-FDA ב-2004" painted
# "2004" on a carrier, tagged N, competing for space with the doses and
# thresholds the plate exists to teach. Same family as the page-number leak: a
# number that is really a reference, dressed as data.
_YEAR_LO, _YEAR_HI = 1900, 2100


def _is_identifier(text: str, m: re.Match) -> bool:
    """True when this digit names a molecule or receptor, not a quantity.

    Psychiatry text is full of digits that are part of a NAME — 5-HT, 5-HT2A,
    D2, H1, alpha-2, CYP450, IPT-A. Left alone they were extracted as
    quantities: "חסר ב-5-HT" yielded the figure "5", which then queued for
    painting on a carrier and, worse, for the narrator to read aloud as a
    statistic. The tell is that a real quantity is bounded by a unit, a space
    or Hebrew, never welded to Latin letters.
    """
    before = text[:m.start("a")]
    after = text[m.end():]
    # welded to Latin letters on either side: P450, D2, 5-HT, 5HT2A
    if re.search(r"[A-Za-z]-?$", before):
        return True
    if re.match(r"-?[A-Za-z]", after):
        return True
    # a stripped unit still leaves the letters in `after` only when the regex
    # consumed one, so also check the raw text right after the digits
    tail = text[m.end("b") if m.group("b") else m.end("a"):]
    return bool(re.match(r"-[A-Za-z]", tail))


# Every fact carries its source, "(DSM p.183)" or "[Dulcan p.735-736]", because
# the study guide is meant to be checkable against the books. Those page numbers
# were then read as QUANTITIES. Chapter 29's criteria plate composited '183'
# under the tag MIN - the tag that means "diagnostic threshold" - so the plate
# taught that the threshold for major depression is 183, and '185' beside it as
# a bare number. The citation has to come off before the digits are counted.
_CITE = re.compile(
    r"[\[(]\s*(?:DSM(?:-5(?:-TR)?)?|Dulcan|Stahl[^\])]{0,24}|Synopsis|Kaplan"
    r"[^\])]{0,20}|סילבוס|Syllabus)[^\])]{0,30}?"
    r"(?:p\.?|pp\.?|עמ'?|ch\.?)\s*[\dIVXivx][^\])]{0,24}[\])]",
    re.IGNORECASE)


def strip_citations(text: str) -> str:
    """Drop source citations so their page numbers are not read as data."""
    return _CITE.sub(" ", text or "").strip()



def figure_text(n) -> str:
    """An operator joined to its number the way a reader expects.

    ">=5/9" is right, but the Hebrew operator is a WORD - "עד 8wk", not
    "עד8wk". The glued form reached the narration brief as a figure that then
    could not be found in the spoken script, because no narrator says it that
    way, and the check reported it as an unspoken figure on every plate that
    had one.
    """
    op = (getattr(n, "op", "") or "").strip()
    sep = " " if op and op[-1].isalpha() else ""
    return f"{op}{sep}{n.paint}".strip()


def normalize_number(text: str, fid: str = "") -> list[NumberSpec]:
    """Extract every numeric value from a fact and normalise it for painting.

    Ratios are never turned into counted people: a 1-versus-2 queue inside an
    18-figure frame is unrecoverable and no gate can verify it, which is
    exactly the shipped bug where the render drew roughly 7:1 male while the
    caption asserted 1:2. A ratio is either painted literally on one carrier
    or it stays in the legend.
    """
    text = strip_citations(text)
    out: list[NumberSpec] = []
    prio = _priority(fid)
    for m in _NUM.finditer(text):
        if _is_identifier(text, m):
            continue
        a, b, sep = m.group("a"), m.group("b"), m.group("sep")
        # a bare four-digit year, with no unit and no range partner
        if (not b and not (m.group("unit") or "").strip()
                and a.isdigit() and _YEAR_LO <= int(a) <= _YEAR_HI):
            continue
        op = (m.group("op") or "").strip()
        raw_unit = (m.group("unit") or "").strip().lower()
        unit = _UNIT_CANON.get(raw_unit, raw_unit)

        if sep and sep.strip() in (":", "/"):
            kind = "RATIO" if sep.strip() == ":" else "THRESHOLD"
            paint = f"{a}{sep.strip()}{b}"
        elif b:
            kind, paint = "RANGE", f"{a}-{b}"
        else:
            kind, paint = "SCALAR", a
        if unit in ("yr", "wk", "mo", "d", "h", "min") and kind in ("SCALAR", "RANGE"):
            kind = "DURATION"

        # Carry the unit into the picture. A bare "2" painted for "2 weeks"
        # recovers nothing — the reader encodes a digit with no referent, which
        # is the failure the tag exists to prevent. "%" and duration units are
        # short enough to letter; everything longer stays in the legend.
        if unit == "%":
            paint += "%"
        elif kind == "DURATION":
            paint += unit
        elif unit in ("mg", "meq/l", "g/day"):
            # A dose without its unit is not a dose. These are short enough to
            # letter on a carrier; anything longer stays in the legend. Compared
            # lowercase because raw_unit is folded before the lookup.
            paint += {"meq/l": "mEq/L"}.get(unit, unit)

        tag = _tag_for(fid, unit)
        if kind == "DURATION":
            tag = {"yr": "YRS", "wk": "WKS", "mo": "MOS", "d": "DAYS",
                   "h": "HRS", "min": "MIN"}.get(unit, tag)

        out.append(NumberSpec(
            kind=kind, paint=paint, tag=tag, unit=unit, op=op, priority=prio,
        ))
    return out


def _walk(node, aspect: str, subkey: str, path: str, acc: list) -> None:
    """Flatten facts.json into (path, text) leaves, preserving list indices."""
    if isinstance(node, str):
        if node.strip():
            acc.append((path, aspect, subkey, node.strip()))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk(item, aspect, subkey, f"{path}[{i}]", acc)
    elif isinstance(node, dict):
        for k, v in node.items():
            # a cluster's own name is scaffolding, not a fact to draw
            if k == "name" and "items" in node:
                continue
            _walk(v, aspect, subkey or k, f"{path}.{k}" if path else k, acc)


def atomize(facts: dict) -> list[Fact]:
    """facts.json -> one Fact per atomic assertion, with a stable id.

    One fact = one legend row. An element may carry several facts (shared class
    effects are the densest device in every drug-class chapter), but a fact may
    never be silently merged away — that is what let comorbidities without a
    percentage vanish from the epidemiology images.
    """
    leaves: list = []
    for aspect, node in facts.items():
        _walk(node, aspect, "", aspect, leaves)

    out = []
    for path, aspect, subkey, text in leaves:
        out.append(Fact(
            id=path, aspect=aspect, subkey=subkey, text=text,
            track=track_for_aspect(aspect),
            priority=_priority(path),
            numbers=normalize_number(text, path),
        ))
    return out


# ── plates ─────────────────────────────────────────────────────────────────
@dataclass
class Plate:
    track: str          # "criteria" | "profile" | "management"
    index: int          # 1-based within the track
    facts: list         # list[Fact] — the ones that get drawn
    venue: dict = field(default_factory=dict)
    # True, examinable, and nobody forgets them: "autism involves impaired
    # social communication" IS autism. Drawn, they crowd out the arbitrary
    # things a mnemonic exists for — the 16p11.2 CNV, epilepsy at 8.6%. They
    # are never discarded, only moved off the picture and into the legend.
    legend_only: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.track}{self.index if self.index > 1 else ''}"


def plan_plates(facts: list[Fact]) -> list[Plate]:
    """Split a chapter's facts into plates.

    Hybrid, per the 2026-07-26 decision: the DSM criteria get their own plate
    because they are recited verbatim under exam conditions; everything else
    shares the chapter's main plate. Within a track we only split past
    PLATE_SPLIT_AT — the corpus routinely carries 40+ items on one page, and a
    legend that overflows is reprinted against the SAME image rather than
    given a second scene.
    """
    by_track = {t: [f for f in facts if f.track == t]
                for t in ("criteria", "profile", "management")}

    # A track too thin to fill a scene does not get one. Two facts on their own
    # plate is a venue, an anchor and an ambient activity spent on two facts,
    # and it reads as an error to the learner. Fold it into the other
    # descriptive track instead — the criteria keep a lower bar, because their
    # whole reason for a separate plate is that they are recited verbatim.
    def fold(src: str, dst: str, floor: int) -> None:
        if 0 < len(by_track[src]) < floor:
            for f in by_track[src]:
                f.track = dst
            by_track[dst] += by_track[src]
            by_track[src] = []

    fold("management", "profile", MIN_PLATE_FACTS)
    fold("profile", "management", MIN_PLATE_FACTS)
    fold("criteria", "profile" if by_track["profile"] else "management", 3)

    plates = []
    for track in ("criteria", "profile", "management"):
        sel = by_track[track]
        if not sel:
            continue
        # Which of these actually need a picture. A criterion is exempt: its
        # letter and its threshold are precisely what gets recited under exam
        # conditions, so the criteria plate draws all of its facts.
        if track == "criteria":
            draw, tell = sel, []
        else:
            scored = [(f, fact_triage.score_fact(f.text, f.id)[0]) for f in sel]
            draw = [f for f, s in scored if s >= fact_triage.DRAW_THRESHOLD]
            tell = [f for f, s in scored if s < fact_triage.DRAW_THRESHOLD]
            # A plate with nothing arbitrary on it still needs to be a scene.
            # Rather than render an empty venue, fall back to drawing the lot —
            # the triage is there to rank, not to veto a whole aspect.
            if len(draw) < MIN_PLATE_FACTS:
                draw, tell = sel, []

        n = 1 if len(draw) <= PLATE_SPLIT_AT else math.ceil(len(draw) / FACTS_PER_PLATE)
        per = math.ceil(len(draw) / n)
        for i in range(n):
            chunk = draw[i * per:(i + 1) * per]
            if chunk:
                plates.append(Plate(track=track, index=i + 1, facts=chunk,
                                    # the legend-only facts ride on the first
                                    # plate of the track, where the reader is
                                    # already looking for that track's material
                                    legend_only=tell if i == 0 else []))
    return plates


def triage_numbers(plate: Plate) -> list[NumberSpec]:
    """Choose which numbers actually get painted, and mark the rest legend-only.

    The corpus paints 0-2 numerals per page and six of nine surveyed pages
    paint none at all. A number with no natural numbered prop is dropped, not
    forced: a vivid wrong number out-competes the correct legend at recall and
    is very hard to unlearn.
    """
    cands = [n for f in plate.facts for n in f.numbers
             if n.kind not in ("COUNT",)]
    # ordinals and bare small counts are never numerals in the corpus
    cands = [n for n in cands if not (n.kind == "SCALAR" and n.unit == "" and
                                      len(n.paint) == 1)]
    cands.sort(key=lambda n: (-n.priority, n.kind == "RATIO"))

    chosen, seen_paint, seen_tag = [], set(), set()
    for n in cands:
        if len(chosen) >= MAX_PAINTED_NUMBERS:
            break
        if n.paint in seen_paint:
            continue                   # unique per plate by construction, so
                                       # the gate can match position, not just
                                       # set membership
        if n.tag in seen_tag:
            # Two numerals under the same referent tag are swap-vulnerable and
            # no gate can catch a swap that teaches the rates backwards. The
            # director may override `tag` with a discriminating one (KIDS vs
            # TEENS); absent that, drop the weaker and leave it to the legend.
            continue
        seen_paint.add(n.paint)
        seen_tag.add(n.tag)
        n.drawn = True
        chosen.append(n)
    return chosen


# ── element table validation ───────────────────────────────────────────────
ELEMENT_TABLE_SCHEMA = {
    "type": "object",
    "required": ["venue", "anchor_structure", "zones", "elements"],
    "properties": {
        "venue": {
            "type": "object",
            "required": ["sign_text", "place", "activity"],
            "properties": {
                "sign_text": {"type": "string"},
                "place": {"type": "string"},
                "activity": {"type": "string"},
            },
        },
        "anchor_structure": {"type": "string"},
        "zones": {"type": "array", "items": {"type": "string"},
                  "minItems": 2, "maxItems": 5},
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "fact_ids", "cue", "prop", "zone",
                             "actor", "action", "derivation"],
                "properties": {
                    "id": {"type": "string"},
                    "fact_ids": {"type": "array", "items": {"type": "string"},
                                 "minItems": 1},
                    "cue": {"type": "string"},
                    "prop": {"type": "string"},
                    "zone": {"type": "string"},
                    # An audit of eleven plates across three eras of this
                    # pipeline found ONE property that separates every praised
                    # element from every orphan, surviving controls for venue,
                    # prop and era: whether somebody is USING it. The same
                    # pigeonhole wall is the centrepiece of one plate — staff
                    # filing into it and pulling from it — and dead weight on
                    # the next, where nobody sorts mail.
                    #
                    # Until now there was nowhere to say it. The illustrator
                    # received `prop` alone, so element 1 of the SSRI plate
                    # reached it as "Figure of a postal carrier" while the
                    # blocking — which IS allosteric inhibition, the whole
                    # mnemonic — stayed behind in `cue` and went to the legend.
                    "actor": {"type": "string",
                              "description": "who handles this prop, as one of "
                                             "the venue's own people ('a "
                                             "consultant', 'a queueing "
                                             "customer'), or NOBODY"},
                    "action": {"type": "string",
                               "description": "what they are doing with it, as "
                                              "a present participle phrase "
                                              "('barred from the post-it "
                                              "drawer by the stair rail'). This "
                                              "is what gets drawn."},
                    "text": {"type": "string"},
                    # What familiar word the painted string lands on, and what
                    # in the picture pays for the distortion. "CYPD reads as
                    # NYPD; a signwriter is halfway through repainting the N
                    # into a C." Without this a token is merely printed, which
                    # leaves the reader memorising both the fact and where it
                    # was standing.
                    "text_hook": {"type": "string"},
                    "derivation": {"type": "string", "enum": list(DERIVATIONS)},
                    "triple": {"type": "array", "items": {"type": "string"},
                               "minItems": 3, "maxItems": 3},
                    "relates_to": {"type": "string"},
                    "relation": {"type": "string", "enum": list(RELATIONS)},
                    "hook": {"type": "string"},
                },
            },
        },
    },
}

_HERO_WORDS = re.compile(r"\b(hero|protagonist|central figure|main character)\b", re.I)
_HAS_HEBREW = re.compile(r"[֐-׿]")
# The hook must carry BOTH halves — what is there and why that object is the
# right one — in one flowing sentence. An earlier version banned every
# describing verb, which was wrong: description is half the job. What stays
# banned is the lazy connector, which asserts a link instead of showing it.
_HOOK_ASSERTS = re.compile(r"\bמסמל|\bמייצג|\bמסמלת|\bמייצגת")
HOOK_MIN_WORDS = 6   # below this it degrades into a label, not a sentence
# 25 was too tight for the shape the hook is now REQUIRED to have. Since the
# legend hook must describe AND explain in one sentence, the everyday half and
# the clinical half both have to fit; the chapter 28 director produced 30-word
# hooks that were correct, readable and single-sentence, and every one of them
# was rejected. The cap is there to forbid a paragraph, and 32 still does.
HOOK_MAX_WORDS = 32


def _trim_cue(cue: str) -> str:
    """A cue names ONE prop. Cut the description the director hung off it.

    "Tally slate showing '70%' re-dyeing and '20-40%' to 'Bipolar Weaving'" is
    not a nine-word cue and never will be: it is a two-word cue with a caption
    attached. Cut at the first joint where the naming stops and the describing
    starts, then hard-trim what is left.
    """
    for joint in (",", " showing", " displaying", " reading", " marked",
                  " with ", " that ", " which ", " holding", " labelled",
                  " labeled", " bearing", " (", " — ", " - "):
        i = cue.find(joint)
        if i > 0:
            cue = cue[:i]
    words = cue.split()[:MAX_CUE_WORDS]
    # a hard cut lands mid-phrase ("...the deeper terrace where"); a cue ending
    # on a function word reads as a sentence someone forgot to finish
    tail = {"where", "the", "a", "an", "of", "in", "on", "at", "to", "with",
            "that", "which", "and", "by", "for", "from", "as", "into", "over"}
    while len(words) > 1 and words[-1].lower().strip(",;:") in tail:
        words.pop()
    return " ".join(words).strip(" ,;:-")


def _cue_candidates(cue: str) -> list[str]:
    """Short names for this prop, shortest first, none over the cap.

    The joint-cut is the best name ("Weighing scale"), but it is also the name
    most likely to be taken already, since a director that writes one long cue
    usually wrote the short version of the same prop somewhere else. So offer
    the joint-cut first and then progressively longer prefixes, which is how a
    person disambiguates too: "Weighing scale", "Weighing scale with brass".
    """
    words = cue.split()
    out, seen = [], set()
    for cand in [_trim_cue(cue)] + [" ".join(words[:n])
                                    for n in range(2, MAX_CUE_WORDS + 1)]:
        cand = cand.strip(" ,;:-")
        if cand and cand.lower() not in seen and len(cand.split()) <= MAX_CUE_WORDS:
            seen.add(cand.lower())
            out.append(cand)
    return out


# "X מסמל את Y" asserts the link instead of showing it, which is the one thing
# the hook may not do. On the last rung of the ladder the assertion is rewritten
# as an apposition — the everyday half, a dash, the clinical half — which is the
# shape the owner asked for, minus the connective that was doing the asserting.
_ASSERT_VERB = re.compile(r"\s*(מסמלים|מייצגים|מסמלת|מייצגת|מסמל|מייצג)\s+(את\s+)?")


def _deassert_hook(hook: str) -> str:
    return re.sub(r"\s{2,}", " ", _ASSERT_VERB.sub(" — ", hook, count=1)).strip()


def normalize_table(table: dict, aggressive: bool = False,
                    known_facts: set | None = None) -> list[str]:
    """Fix in code what a re-ask does not reliably fix. Returns what it changed.

    Same medicine as the relation synonyms and the banned narration phrases:
    when the director's answer is right in substance and wrong in a mechanical
    way, normalizing is 100% reliable and re-asking is not. Three attempts at
    "your cue is one word too long" is three renders' worth of budget spent on
    a string operation.
    """
    notes: list[str] = []
    els = table.get("elements") or []

    # The zone list is the director's OWN vocabulary, not a contract we handed
    # it, so a zone that is not on the list is a bookkeeping slip and not a
    # disagreement. Chapter 28 lost all three attempts to one: the director
    # wrote `zones` in English, `zones_he` in Hebrew, and then named every
    # element's zone in Hebrew. Map it back by index where the Hebrew list
    # gives the answer, and otherwise take the director at its word and add it.
    zones = list(table.get("zones") or [])
    zones_he = list(table.get("zones_he") or [])
    by_he = {h.strip().casefold(): en
             for h, en in zip(zones_he, zones) if h and en}
    known = {z.strip().casefold() for z in zones}
    for e in els:
        z = (e.get("zone") or "").strip()
        if not z or z.casefold() in known:
            continue
        if z.casefold() in by_he:
            e["zone"] = by_he[z.casefold()]
            notes.append(f"{e.get('id', '?')}: zone {z!r} mapped to "
                         f"{e['zone']!r}")
        else:
            zones.append(z)
            known.add(z.casefold())
            notes.append(f"{e.get('id', '?')}: zone {z!r} added to the list")
    table["zones"] = zones

    # A fact id the plate does not have is usually a lexicon token the director
    # filed in the wrong column (RAPHE_TAPESTRY is a fixed SYMBOL, not a fact).
    # Strip it and keep the element: the fact-coverage check below is what
    # actually guards the chapter, and it is untouched by this.
    if known_facts is not None:
        for e in els:
            cited = e.get("fact_ids") or []
            good = [f for f in cited if f in known_facts]
            if len(good) != len(cited):
                e["fact_ids"] = good
                notes.append(f"{e.get('id', '?')}: dropped unknown fact id(s) "
                             f"{[f for f in cited if f not in known_facts]}")

    # A relation that points nowhere is not a claim about the picture, it is a
    # leftover. Dropping it costs one adjacency; keeping it costs the table.
    ids = {e.get("id") for e in els}
    for e in els:
        if e.get("relation") and not e.get("relates_to"):
            e.pop("relation", None)
            notes.append(f"{e.get('id', '?')}: dropped a relation with no target")
        elif e.get("relates_to") and e["relates_to"] not in ids:
            notes.append(f"{e.get('id', '?')}: dropped relation to unknown "
                         f"element {e['relates_to']!r}")
            e.pop("relation", None)
            e.pop("relates_to", None)

    # A relation verb outside the closed set is bookkeeping, not disagreement
    # about the picture. Chapter 29's third plate was lost entirely because the
    # director answered `relation: "is not directly responsible for the
    # clinical delay caused by"` — a whole English clause where a verb belongs.
    # Three re-asks later the plate was abandoned, taking 21 facts with it.
    #
    # Dropping it is also the SAFE reading. The synonyms map near-misses onto
    # the real verbs, but an arbitrary clause can mean anything, including a
    # negation like this one, and guessing ACTS_ON from it would have the
    # illustrator draw the opposite of what was meant.
    for e in els:
        rel = (e.get("relation") or "").strip()
        if not rel:
            continue
        key = rel.upper().replace(" ", "_")
        if key in RELATIONS:
            e["relation"] = key
        elif RELATION_SYNONYMS.get(key):
            e["relation"] = RELATION_SYNONYMS[key]
        else:
            notes.append(f"{e.get('id', '?')}: dropped an unmappable relation "
                         f"{rel[:48]!r}")
            e.pop("relation", None)
            e.pop("relates_to", None)

    # Over the relation cap, keep the first MAX_RELATIONS and flatten the rest
    # to plain adjacency. The cap exists so the plate does not become a plot,
    # and the plate is no more of a plot with six relations than with nine.
    related = [e for e in els if e.get("relates_to")]
    for e in related[MAX_RELATIONS:]:
        e.pop("relation", None)
        e.pop("relates_to", None)
    if len(related) > MAX_RELATIONS:
        notes.append(f"dropped {len(related) - MAX_RELATIONS} relation(s) over "
                     f"the cap of {MAX_RELATIONS}")

    # Required strings: the ladder already drops them under pressure, so an
    # over-long or over-budget one is a string to drop, not a table to reject.
    kept = 0
    for e in els:
        txt = (e.get("text") or "").strip()
        if not txt:
            continue
        if len(txt.split()) > 5:
            notes.append(f"{e.get('id', '?')}: dropped text {txt!r} (over 5 words)")
            e["text"] = ""
        elif kept >= MAX_REQUIRED_STRINGS:
            notes.append(f"{e.get('id', '?')}: dropped text {txt!r} (over the "
                         f"cap of {MAX_REQUIRED_STRINGS})")
            e["text"] = ""
        else:
            kept += 1

    taken = {(e.get("cue") or "").strip().lower() for e in els}
    for e in els:
        cue = (e.get("cue") or "").strip()
        if cue and len(cue.split()) > MAX_CUE_WORDS:
            # A trim that collides with another element's cue must not be taken
            # -- the legend would name two different props identically -- so
            # walk out to a longer name until one is free.
            others = taken - {cue.lower()}
            short = next((c for c in _cue_candidates(cue)
                          if c.lower() not in others), None)
            if short:
                e["cue"] = short
                taken.add(short.lower())
                notes.append(f"{e.get('id', '?')}: cue trimmed to {short!r}")

        # Only on the last rung. The director gets two honest chances to write
        # a hook that shows the link rather than asserting it, because the
        # rewrite keeps both halves but cannot supply a reason that was never
        # written. Losing the chapter over it is still worse.
        hook = (e.get("hook") or "").strip()
        if aggressive and hook and _HOOK_ASSERTS.search(hook):
            e["hook"] = _deassert_hook(hook)
            notes.append(f"{e.get('id', '?')}: hook de-asserted -> {e['hook']!r}")
    return notes


def validate_table(table: dict, facts: list[Fact],
                   soft: list[str] | None = None,
                   last_rung: bool = False) -> list[str]:
    """Hard errors that must send the director back. Empty list == accept.

    `soft`, when given, collects the violations that are NOT worth another
    round trip — today, an over-long hook. A hook of 34 words is a worse legend
    row than a hook of 30; a chapter with no plate at all because its director
    was bounced three times over word counts is very much worse than either.
    """
    errs: list[str] = []
    soft_out = errs if soft is None else soft
    els = table.get("elements") or []
    if not els:
        return ["no elements"]

    # IS ANYBODY USING IT. The one property that separated every element that
    # worked from every element that failed, across eleven audited plates and
    # three eras of this pipeline. It is checked as a PROPORTION, not per
    # element: one prop standing idle is a weak row, but a table where most
    # props stand idle means the director ignored the field, and that is a
    # different failure needing a different answer. Bouncing the whole table
    # over a single element is how chapter 29's third plate was abandoned with
    # twenty-one facts still on it.
    acted = [e for e in els
             if (e.get("action") or "").strip()
             and (e.get("actor") or "").strip().upper() != "NOBODY"]
    if els and len(acted) * 2 < len(els):
        errs.append(
            f"only {len(acted)} of {len(els)} elements have somebody doing "
            f"something with the prop. An unused prop gets labelled instead of "
            f"understood — give each element an `actor` from this venue's own "
            f"people and an `action` that IS the fact, not a pose beside it.")
    for e in els:
        if not (e.get("action") or "").strip():
            soft_out.append(f"{e.get('id', '?')}: no action — the illustrator "
                            f"will draw this prop standing idle")

    # A BARE TOKEN IS NOT A MNEMONIC. "CYP2D6" painted on a monitor is the fact
    # printed in the picture: the reader still learns it by rote, and now also
    # has to remember where it was standing. It earns its place when it lands
    # on a word they already hold — CYPD is one letter off NYPD — and when
    # something in the scene pays for the letter that changed.
    #
    # Checked as a proportion, like the actor rule, and only on strings that
    # are a bare identifier with no familiar word around them. A chapter may
    # legitimately paint one naked token; a plate where most of the lettering
    # is naked tokens means the director transported the facts instead of
    # encoding them.
    lettered = [e for e in els if (e.get("text") or "").strip()]
    naked = [e for e in lettered
             if fact_triage.IDENTIFIER.fullmatch((e.get("text") or "").strip())
             and not (e.get("text_hook") or "").strip()]
    if lettered and len(naked) * 2 > len(lettered):
        errs.append(
            f"{len(naked)} of {len(lettered)} painted strings are bare "
            f"technical tokens with nothing to land on "
            f"({', '.join((e.get('text') or '') for e in naked[:5])}). Bend "
            f"each towards a word the reader already knows, keeping its "
            f"characters exact inside it, and give `text_hook` the familiar "
            f"word plus what in the scene pays for the letters that changed.")

    # every fact must be represented; an element may carry several facts
    want = {f.id for f in facts}
    got = {fid for e in els for fid in e.get("fact_ids", [])}
    for missing in sorted(want - got):
        errs.append(f"fact not represented: {missing}")
    for unknown in sorted(got - want):
        errs.append(f"element cites unknown fact id: {unknown}")

    ids, cues = set(), {}
    triples = {}
    for e in els:
        eid = e.get("id", "")
        if eid in ids:
            errs.append(f"duplicate element id: {eid}")
        ids.add(eid)

        cue = (e.get("cue") or "").strip()
        if not cue:
            errs.append(f"{eid}: empty cue")
        elif len(cue.split()) > MAX_CUE_WORDS:
            # normalize_table() shortens these first; one that survives it is
            # one whose every short form is already another element's name.
            soft_out.append(f"{eid}: cue over {MAX_CUE_WORDS} words — {cue!r}")
        elif cue.lower() in cues:
            errs.append(f"{eid}: cue collides with {cues[cue.lower()]} — {cue!r}")
        else:
            cues[cue.lower()] = eid

        # cue distinctiveness as a declared (category, material, colour) triple
        tri = tuple(x.lower() for x in (e.get("triple") or []))
        if tri:
            if tri in triples:
                errs.append(f"{eid}: same (category, material, colour) as {triples[tri]}")
            else:
                triples[tri] = eid

        # The closed enums live in ELEMENT_TABLE_SCHEMA, but nothing runs a
        # JSON-schema validator over the director's output, so they were being
        # declared and never checked. A relation of "predicts" sailed through
        # here and then crashed prompt construction on a dict lookup.
        # The legend is read by a Hebrew speaker. An English hook or cue is not
        # a style preference — it makes the row unusable to its only audience,
        # which is how the first chapter-28 legend shipped.
        if not _HAS_HEBREW.search(e.get("cue_he") or ""):
            errs.append(f"{eid}: cue_he must be in Hebrew — got {e.get('cue_he')!r}")
        hook = (e.get("hook") or "").strip()
        nw = len(hook.split())
        if not _HAS_HEBREW.search(hook):
            errs.append(f"{eid}: hook must be in Hebrew — got {hook!r}")
        elif nw < HOOK_MIN_WORDS:
            errs.append(f"{eid}: hook is a label, not a sentence ({nw} words). "
                        f"Say what is there AND why that object fits, in one "
                        f"flowing sentence: {hook!r}")
        elif nw > HOOK_MAX_WORDS:
            soft_out.append(f"{eid}: hook over {HOOK_MAX_WORDS} words "
                            f"({nw}): {hook!r}")
        elif _HOOK_ASSERTS.search(hook):
            errs.append(f"{eid}: hook uses מסמל/מייצג, which asserts the link "
                        f"instead of showing why it holds: {hook!r}")

        der = e.get("derivation")
        if der not in DERIVATIONS:
            key = str(der).upper().replace(" ", "_").replace("-", "_")
            canon = key if key in DERIVATIONS else DERIVATION_SYNONYMS.get(key)
            if canon:
                e["derivation"] = der = canon
        if der not in DERIVATIONS:
            errs.append(f"{eid}: derivation {der!r} is not one of {', '.join(DERIVATIONS)}")
        rel = e.get("relation")
        if rel and rel not in RELATIONS:
            # CASE FIRST. The synonym table holds only the non-canonical verbs,
            # so a director that wrote 'blocks' in lower case fell straight
            # through it and was rejected for using a relation that is on the
            # list — which is how chapter 28 lost an attempt to the word BLOCKS.
            key = rel.upper().replace(" ", "_").replace("-", "_")
            # accept synonyms — the director shouldn't have to learn our vocabulary
            canonical = key if key in RELATIONS else RELATION_SYNONYMS.get(key)
            if canonical:
                e["relation"] = rel = canonical  # so the prompt sees the right one
            else:
                errs.append(f"{eid}: relation {rel!r} is not one of {', '.join(RELATIONS)} "
                            f"and has no known synonym")
        if rel and not e.get("relates_to"):
            errs.append(f"{eid}: relation without relates_to")
        if e.get("relates_to") and e["relates_to"] not in {x.get("id") for x in els}:
            errs.append(f"{eid}: relates_to unknown element {e['relates_to']}")
        if e.get("number") is not None and not isinstance(e.get("number"), dict):
            errs.append(f"{eid}: number must be an object")

    n_rel = sum(1 for e in els if e.get("relates_to"))
    if n_rel > MAX_RELATIONS:
        errs.append(f"{n_rel} relations > {MAX_RELATIONS} — this is becoming a plot")

    n_metaphor = sum(1 for e in els if e.get("derivation") == "FREE_METAPHOR")
    if els and n_metaphor / len(els) > FREE_METAPHOR_CAP:
        # A real quality rule: a free metaphor is the least memorable device in
        # the corpus, so the director is sent back for it — twice. But the
        # derivation is the director's own admission about its own element, and
        # demoting the label would only hide the count. On the last rung the
        # over-cap plate ships with the count recorded, because a plate with
        # four soft elements teaches more than a chapter with no plate.
        (soft_out if last_rung else errs).append(
            f"{n_metaphor}/{len(els)} free metaphors — cap is "
            f"{FREE_METAPHOR_CAP:.0%}; use a person exhibiting the state "
            f"or a fixed lexicon token")

    anchor = table.get("anchor_structure", "")
    if _HERO_WORDS.search(anchor):
        errs.append("anchor_structure must be inanimate, not a character")

    zones = set(table.get("zones") or [])
    for e in els:
        if e.get("zone") and e["zone"] not in zones:
            errs.append(f"{e.get('id')}: zone {e['zone']!r} not in the zone list")

    strings = [e["text"] for e in els if e.get("text")]
    if len(strings) > MAX_REQUIRED_STRINGS:
        errs.append(f"{len(strings)} required strings > {MAX_REQUIRED_STRINGS}")
    for s in strings:
        if len(s.split()) > 5:
            errs.append(f"required string over 5 words: {s!r}")

    return errs


# ── prompt construction ────────────────────────────────────────────────────
# The illustrator letters any noun phrase it likes the look of: told that only
# the venue sign must be legible, it still lettered "Hand-board" (an adjective
# out of a prop description) onto the DOPA-MINE gallery mouths. So everything
# outside TEXT TO PAINT is scrubbed of anything that reads as a label.
_SCRUB_PARENS = re.compile(r"\([^)]*\)")
_SCRUB_QUOTED = re.compile(r"[\"“”'']([^\"“”'']{0,60})[\"“”'']")
_SCRUB_CAPS = re.compile(r"\b[A-Z][A-Z0-9&./+-]{2,}\b")
_SCRUB_DIGITS = re.compile(r"\d")


def _stash_identifiers(s: str) -> tuple[str, dict]:
    """Hide receptor/enzyme/locus names from the scrubber, reversibly.

    An identifier is letters AND digits welded together, so a scrubber that
    case-folds capitals and deletes numerals destroys it: CYP2D6 came out as
    "Cypd", CYP2C19 as "Cypc", 5-HT2C as "-Htc". The illustrator was painting
    those fragments faithfully, and the owner reasonably asked how anyone is
    supposed to remember them — they were not the facts any more.

    receptors_in() promises these reach the plate character for character. This
    is what makes that true on the only path that matters, the prompt.
    """
    keep: dict[str, str] = {}

    def stash(m: re.Match) -> str:
        # Lowercase letters only: the marker has to survive the caps-folding
        # and the digit-stripping that come next.
        key = "\x01" + "".join(chr(97 + int(c)) for c in str(len(keep))) + "\x02"
        keep[key] = m.group(0)
        return key

    return RECEPTOR_TOKEN.sub(stash, s), keep


def scrub_prop_text(s: str) -> str:
    """Strip anything the illustrator might paint as a word or a numeral.

    Identifiers are exempt — see _stash_identifiers.
    """
    s, keep = _stash_identifiers(s)
    s = _SCRUB_PARENS.sub("", s)          # teaching notes live in parentheses
    s = _SCRUB_QUOTED.sub(r"\1", s)       # quotes read as "letter this"
    s = _SCRUB_CAPS.sub(lambda m: m.group(0).capitalize(), s)
    s = _SCRUB_DIGITS.sub("", s)          # stray numerals break the NUMBER gate
    for key, val in keep.items():
        s = s.replace(key, val)
    return re.sub(r"\s{2,}", " ", s).strip(" ,;")


def build_image_prompt(table: dict, plate: Plate, style: str,
                       chroma_plates: bool = True,
                       venue: dict | None = None) -> str:
    """Assemble the illustrator prompt deterministically from the table.

    Tiered on purpose: under a roughly one-third silent drop rate a flat list
    of 60-90 items loses the anchor and the zoning, which is the whole of the
    user's "no linkage" complaint. TIER 1 is restated at the head and the tail.
    """
    v = table["venue"]
    els = table["elements"]
    zones = table.get("zones", [])

    tier1 = f"""THE PLACE — {v['place']}

THE ONE SHARED ACTIVITY — every person in the frame is doing this, each in
their own way. There is no story unfolding and no side plot between figures.
But each numbered element below names a person and what they are DOING with
that prop, and every one of those actions IS happening in the picture. This
line used to end "nothing causes anything else", which is the opposite
instruction, and it is why plate after plate came back as a room of props
standing idle with their names written beside them.
{v['activity']}

THE ANCHOR — the DOMINANT element of the picture, and it is INANIMATE. It cuts
the frame on a diagonal and divides it into zones. It is the largest thing in
the image. NO PROTAGONIST: no figure is larger or more important than the rest.
{table['anchor_structure']}

ZONES, and what belongs in each: {' · '.join(zones)}"""

    props = []
    for i, e in enumerate(els, 1):
        # THE ACTION LEADS. This line used to be the bare noun phrase in
        # `prop`, and the verb the director had written — the thing that IS the
        # mnemonic — never left `cue`, which only the legend reads. So the
        # illustrator was handed twenty isolated nouns, drew them standing
        # around, and lettered each with its own name to fill the text quota.
        # That is the "labels everywhere, nothing connected to the scene"
        # complaint, and it is produced here.
        actor = scrub_prop_text(e.get("actor") or "")
        action = scrub_prop_text(e.get("action") or "")
        prop = scrub_prop_text(e["prop"])
        if action and actor and actor.upper() != "NOBODY":
            line = f"  {i}. {actor} {action} — {prop}"
        elif action:
            line = f"  {i}. {prop}, {action}"
        else:
            line = f"  {i}. {prop}"
        if e.get("zone"):
            line += f" — in the {scrub_prop_text(e['zone'])}"
        if e.get("relates_to") and e.get("relation"):
            tgt = next((x for x in els if x.get("id") == e["relates_to"]), None)
            # .get, not [], so an unvalidated relation degrades to a plain
            # adjacency instead of taking the whole build down
            verb = {"BLOCKS": "physically blocking", "ACTS_ON": "acting on",
                    "ADJACENT_TO": "right beside", "FALLS_FROM": "falling from",
                    "CONTAINED_IN": "inside"}.get(e["relation"], "right beside")
            if tgt:
                line += f", {verb} {scrub_prop_text(tgt['prop'])}"
        props.append(line)

    strings = [(e["text"], scrub_prop_text(e["prop"]))
               for e in els if e.get("text")]
    text_block = "\n".join(
        f'  {i}. "{s}" — lettered on {p}' for i, (s, p) in enumerate(strings, 1)
    ) or "  (none)"

    numbers = [n for f in plate.facts for n in f.numbers if n.drawn]
    if chroma_plates and numbers:
        plate_lines = []
        for (cname, rgb), n in zip(CHROMA_KEYS, numbers):
            # NO RGB TRIPLE. The illustrator painted the words "PURE GREEN
            # (RGB 0,177,64)" onto the plate — two lines below an instruction
            # reading "absolutely NO writing, lettering, numbers or marks of
            # any kind on it", which is why the answer is not more prose. A
            # prompt that contains digits in a brief that also has a section
            # called TEXT TO PAINT will sooner or later have those digits
            # painted. The detector masks on HUE with tolerance and never
            # needed the exact value.
            plate_lines.append(
                f"  - a blank flat plate of {cname.lower()}, the most "
                f"saturated {cname.split()[-1].lower()} in the picture, "
                f"on {scrub_prop_text(n.carrier or 'a sign panel')}"
            )
        number_block = f"""
BLANK SIGN PLATES — the most important instruction in this brief.
Each of these objects carries a BLANK rectangular sign plate filled with ONE
flat, solid, completely uniform colour: no shading, no gradient, no texture, no
outline crossing its face, and absolutely NO writing, lettering, numbers or
marks of any kind on it.
{chr(10).join(plate_lines)}

HOW BIG — each plate is a LARGE BOARD, not a label. It stands about ONE FIFTH
OF THE FULL IMAGE HEIGHT tall and wider than it is tall: the size of a doorway,
a shop shutter or a big wall-mounted noticeboard, and clearly taller than the
head-to-waist of any person in the frame. A tag, a ticket, a badge, a sticker or
a small hanging sign is WRONG — if the plate is smaller than a person's torso,
the picture has to be redrawn. Three such boards in the scene is correct and
deliberate; make room for them.

HOW DRAWN — each plate is the FACE OF THE OBJECT IT SITS ON, not a poster
floating in front of the scene. Draw the object that carries it — its frame,
its posts, its bolts, its chains, its stand, the wall it is screwed to — so that
a viewer can see what holds it up and why it is there. A big cream rectangle
hanging in mid-air belongs to nothing and teaches nothing.
Within that, keep the coloured face itself clean: square-on to the viewer,
whole and unbroken, its four corners all visible, and nothing crossing it — no
person, no pipe, no rope, no beam, no other sign over its face or clipping its
corners. Frame it, mount it, bolt it; just do not cover it.
These colours appear NOWHERE ELSE in the picture.
"""
    else:
        number_block = ""

    # ORDINARY CLUTTER, from the venue's own inventory rather than a fixed list.
    # The style contract used to name "barrows, cable drums, stacked pallets,
    # pigeons, coiled hose" in every one of the 144 briefs, which is why the
    # owner asked why there were pigeons in a travel agency: we asked for them,
    # in a greenhouse and on a beach too. venues.json has hand-written
    # stock_props for all 144 places; what no element claimed is exactly what
    # ordinary background should be.
    # Scrub the stock props before offering them as clutter. venues.json was
    # hand-authored and several entries carry lettering of their own — chapter
    # 29's reads "photocopier with a DUAL COPIER/SCANNER label taped to it",
    # which three separate audits condemned as painted misteaching, since SSRIs
    # are not dual-reuptake agents. Offering that string inside a block headed
    # "carrying no fact and no legible text" is how it kept arriving.
    stock = [t for t in (plain_clutter(p) for p in
                         ((venue or {}).get("stock_props") or []))
             if len(t.split()) >= 2]
    claimed = " ".join(scrub_prop_text(e.get("prop", "")) for e in els).lower()
    # Match on a 6-character STEM, not the whole word: the venue writes "wall of
    # pigeonholes" and the element claims "the pigeonhole wall", and a plural
    # was enough to hand the illustrator the same object twice — once as the
    # mnemonic and once as background it was told carries no fact.
    claimed_stems = {w[:6] for w in re.findall(r"[a-z]{5,}", claimed)}
    spare = [p for p in stock
             if not any(w[:6] in claimed_stems
                        for w in re.findall(r"[a-z]{5,}", p.lower())[:3])]
    clutter_block = ""
    if spare:
        clutter_block = (
            "\nORDINARY CLUTTER — this venue's own furniture, carrying no fact"
            " and no legible text. Fill the gaps with these and nothing else:\n  "
            + "; ".join(spare[:12]) + "\n"
        )

    plate_reminder = ("; the blank colour plates are large boards, a fifth of the "
                      "image height, unobstructed" if number_block else "")

    n_strings = len(strings)
    return f"""{style}
{tier1}

CAST AND DENSITY
About 18 people, and EVERY ONE OF THEM IS DRAWN AT THE SAME SIZE. This is one
high, wide shot of a whole room seen from a distance — it is not a photograph
with a foreground. Therefore:
  - No person stands near the viewer. Nobody is in the front of the shot.
  - No person is cut off by the edge of the frame, and nobody is drawn as a
    half-body, a close-up, a face, or a pair of hands.
  - Nobody leans, lies, reaches or falls toward the viewer.
  - Every person is between one-sixth and one-quarter of the image height, feet
    on the same floor, heads at roughly the same level across the picture.
If one person in the finished picture is noticeably bigger than the others, the
picture is WRONG and must be redrawn — that single oversized figure is the most
common failure of this brief. Give them visible variety of age, build, skin
tone, hair and clothing: different colours and different garments on each, never
one shared outfit repeated across the cast. PACK the frame edge to edge, with
roughly as much ordinary non-mnemonic clutter as meaningful detail so the place
reads as real. No vignette, no edge darkening.

THINGS IN THIS PLACE
{chr(10).join(props)}

TEXT TO PAINT — the {n_strings} string(s) below, and ONLY these, are legible. Every
other piece of paper, notice, board, poster and label in the scene is illegible
squiggle-lines. Do not invent further legible text to fill the picture out. NO
numerals appear anywhere except where a blank plate is specified.
{text_block}
{clutter_block}{number_block}
REMINDER — {v['place']}; everyone is {v['activity'][:120]}; the largest thing in
frame is {table['anchor_structure'][:120]}; no protagonist — every person the
same size, nobody near the viewer, nobody cropped by the frame{plate_reminder}.
"""


def build_plate_director(plate: Plate, venue: dict, topic: str,
                         tokens: dict | None = None,
                         composited_numbers: bool = True) -> str:
    """The director prompt. Its output contract is the ELEMENT TABLE, not prose.

    v1 asked for a free-text scene and a loose key, which is why nothing could
    be validated: there was no stable name for any element, so no gate could
    ask whether it had been drawn. Here the director fills in a table whose
    shape code already fixed, and `validate_table` rejects it mechanically.
    """
    numbers = [n for f in plate.facts for n in f.numbers if n.drawn]
    fact_lines = "\n".join(
        f"  {f.id}  [{f.aspect}/{f.subkey}]  {f.text}" for f in plate.facts
    )
    num_lines = "\n".join(
        f'  - "{n.paint}" tagged "{n.tag}" — carrier class {n.carrier or "choose one"}'
        for n in numbers
    ) or "  (no numbers are painted on this plate)"

    # The illustrator does not letter these when they are composited, and the
    # director does not know that — so on the first real composite run it filed
    # all three numbers as required `text`, the illustrator duly painted them by
    # hand, and the picture ended up carrying each number twice: once lettered,
    # once on its blank plate. The strings are stripped in code as well; this is
    # the half that stops them being written in the first place.
    num_rule = ("\n\nDO NOT put any of these numbers, or their tags, in any "
                "element's `text` field. A separate step letters them onto "
                "blank plates after the picture is drawn, and anything you put "
                "in `text` is painted by hand as well, so the number would "
                "appear twice. `text` is for words only — never digits, never "
                "a percent sign, never a ratio." if composited_numbers else "")

    tok_lines = ""
    if tokens:
        tok_lines = "\n\nCOURSE-WIDE FIXED SYMBOLS this chapter needs. These may NOT be\n" \
                    "redesigned. Each must stand as a prop INSIDE the venue, never in place\n" \
                    "of it. Use the given cue and prop verbatim:\n" + "\n".join(
            f'  - {k}: cue "{v["cue"]}" — {v["prop"][:120]}' for k, v in tokens.items()
        )

    return f"""You are the scene director for a SketchyMedical-style study plate.

TOPIC: {topic}

THE VENUE IS FIXED — do not invent another one:
  sign over the door : "{venue['sign_text']}"
  the place          : {venue['place']}
  what everyone does : {venue['activity']}
  the anchor         : {venue.get('anchor_structure', '')}
  things it contains : {', '.join(venue.get('stock_props', [])[:15])}

THE FACTS — every one of these must be represented by exactly one element.
An element MAY carry several facts (shared class effects), but no fact may be
dropped or merged away.
{fact_lines}

NUMBERS TO PAINT — these exact characters, each with its tag on the SAME object:
{num_lines}{num_rule}
{tok_lines}

RULES
- Derive each element from the closed list, preferring the earlier ones:
  LITERAL_DEPICTION (a person actually doing it — the corpus's largest
  mechanism), PHONETIC_PUN, LEXICON_TOKEN, MORPHEME_SPLIT, PRINTED_TEXT,
  PRINTED_NUMBER, COUNTED_SET, BORROWED_ARTWORK, ACRONYM_EMBED, RULE_ON_A_SIGN,
  CONTRAST_PAIR, NEGATION, GRAPH_AS_OBJECT, FREE_METAPHOR (last resort, at most
  one element in twenty).
- NEVER invent a metaphor for a feeling. Draw a person exhibiting it, or use a
  fixed token, or borrow an artwork that already depicts it.
- Give 3 or 4 zones, named after parts of this place, and put every element in one.
- Any fact whose verb is transitive (blocks, inhibits, prevents, converts,
  transports) MUST use `relates_to` + `relation`. At most {MAX_RELATIONS} relations
  on the plate — this is a place, not a plot. No figure is the protagonist.
- `cue` is a bare noun phrase naming ONE prop, at most {MAX_CUE_WORDS} words, unique on the
  plate, in Latin script. A positional phrase in a cue is fine — it is the address.
- `cue_he` is the SAME cue in Hebrew, and it is what the learner actually reads.
  Keep clinical terms and any word genuinely painted in the picture in English
  inside it — the legend prints the English separately, in brackets.
- `text` only where a string is genuinely painted in the world, at most 5 words.
  At most {MAX_REQUIRED_STRINGS} of them.

  A PAINTED TOKEN IS NOT YET A MNEMONIC. Writing CYP2D6 on a monitor puts the
  fact in the picture and leaves the reader exactly where they started: they
  still have to learn it by rote, and now they have to learn where it was
  standing too. A string earns its place only when all three of these hold:

  (a) IT LANDS ON SOMETHING ALREADY IN THE READER'S HEAD. Bend the token
      towards a word, brand or institution they know, and keep the token's own
      characters exact inside it. CYPD is one letter off NYPD, so CYP2D6 can be
      a precinct: a CYP2D6 PRECINCT door, officers, a squad car.
  (b) THE DISTORTION IS PAID FOR IN THE SCENE. If C replaced N, something in
      the picture must say why — a signwriter halfway through repainting the N
      into a C, a cracked neon N, a stencil with the N plate missing. An
      unexplained near-miss is remembered as the original word, and then the
      reader recalls NYPD and has lost the fact.
  (c) THE ELEMENT CARRIES THE FUNCTION AND THE PARTNERS, NOT ONLY THE NAME.
      Ask what this thing DOES and what it is tied to, and put that in the
      action. An enzyme that clears drugs is a duty officer processing them;
      the drug that inhibits it potently is the one handcuffing him to his desk
      so the queue backs up; a weak inhibitor barely slows him. Now the reader
      recovers "paroxetine and fluoxetine are potent CYP2D6 inhibitors" from
      one picture instead of memorising a list.

  A bare technical token painted on a surface, with no familiar word to land
  on, no reason for its shape and nobody doing anything with it, is the defect
  this whole brief exists to prevent — however legible it is.
- `triple` is [category, material, dominant colour] and must be unique across
  elements whose facts are unrelated.
- `hook` is IN HEBREW, one flowing sentence of 8-32 words that says BOTH what
  is there AND why it is the right object, woven together. Write it the way you
  would say it out loud to someone looking at the picture.
    GOOD: "כמו שבבית צביעה מתכון נמדד באחוז ממשקל הבד, כך שכיחות הדיכאון מופיעה
          באחוזים על לוח המתכונים"
    BAD (only describes): "לוח המתכונים מציג את שיעורי השכיחות"
    BAD (only explains):  "מתכון נמדד באחוז"
    BAD (a label):        "לוח מתכונים = שכיחות"
  The everyday logic of the object must be visible in the sentence — that logic
  is what makes the fact stick.
  THE WORDS "מסמל" AND "מייצג" ARE FORBIDDEN, and they are the single most
  common reason a table is sent back. They announce that a link exists instead
  of showing why it holds, and a reader who is told a link exists does not
  remember it. Whenever you are about to write one, you already know the real
  reason — write that instead:
    BAD:  "ועדת בית קפה שמדברת בקולניות מייצגת את רשת ברירת המחדל בדיכאון"
    GOOD: "ועדת בית קפה שממשיכה לדוש באותו נושא גם כשאין מה להוסיף, בדיוק כמו
          רשת ברירת המחדל שנשארת פעילה בדיכאון וטוחנת את אותן מחשבות"
  The shape that works: the everyday thing, then "כמו ש… כך …" or "בדיוק כמו",
  then the clinical fact.
- `venue_he` and `zones_he` are the place, the shared activity and the zone
  names in Hebrew. The whole legend is read by a Hebrew speaker; leaving the
  header in English makes the page unreadable to them.

- `actor` and `action` are THE MOST IMPORTANT FIELDS IN THE TABLE, because they
  are the only ones the illustrator can act on. `prop` names the object;
  `action` is what is being DONE with it, and that is what gets drawn.
  An audit of this course's plates found exactly one property that separates
  every element that worked from every element that failed: whether somebody is
  USING it. The same wall of pigeonholes is the best thing on one plate — staff
  filing into it and pulling from it — and dead weight on the next, where
  nobody sorts mail. A prop that merely stands there gets labelled instead of
  understood, and a labelled prop is not a mnemonic.
    `actor`  one of THIS VENUE'S OWN people — "a consultant at the far desk",
             "a queueing customer". Write NOBODY only when the fact genuinely
             has no agent; at most a quarter of the plate may say NOBODY.
    `action` a present-participle phrase, and where the fact IS a mechanism the
             action must BE that mechanism, not stand near it:
      BAD:   prop "a staircase", action "standing in the office"
             — the pun is audible and nothing happens, so it arrives as a
               flight of stairs rising to nothing with a label on it
      GOOD:  prop "a mezzanine stair rail across the pigeonhole wall",
             actor "the SERT postman",
             action "barred by the rail from reaching the post-it drawer he is
               stretching for"
             — allosteric blockade IS the blocking; now it is drawn
  The prop must also have a reason to be in THIS place. A stranger who knows
  nothing about the chapter should be able to say why the object is there.
  A pun that produces an object the venue would never contain is a failure even
  when the pun is good: build the pun out of the furniture the venue already
  has, or give it a job the venue would plausibly need done.

Return STRICT JSON only, matching this shape:
{{"venue": {{"sign_text": "...", "place": "...", "activity": "..."}},
 "venue_he": {{"place": "...", "activity": "..."}},
 "anchor_structure": "...",
 "zones": ["...", "..."],
 "zones_he": ["...", "..."],
 "elements": [
   {{"id": "e1", "fact_ids": ["..."], "cue": "...", "cue_he": "...",
     "prop": "...", "actor": "...", "action": "...",
     "zone": "...", "text": "", "derivation": "LITERAL_DEPICTION",
     "triple": ["...", "...", "..."], "relates_to": "", "relation": "",
     "hook": "..."}}
 ]}}"""


# ── legend ─────────────────────────────────────────────────────────────────
# Banned in the FACT half only. Positional words in a CUE are the address, not
# narration — the corpus writes "Barbershop next to Cab-A" and "Butter in front
# of liver tent", and banning those would reject its own grammar.
_BAN_FACT = re.compile(
    r"תמונה|סצנה|איור|ברקע|בחזית|נראה|אנו רואים|מסמל|מייצג"
    r"|\bpicture\b|\bscene\b|\bdepicts?\b|\bbackground\b|\bwe see\b"
    r"|\bsymboli[sz]es\b|\brepresents\b",
    re.I,
)


def check_legend_row(cue: str, fact: str, hook: str) -> list[str]:
    errs = []
    if _BAN_FACT.search(fact):
        errs.append(f"fact half describes the picture: {fact!r}")
    if _BAN_FACT.search(hook or ""):
        errs.append(f"hook describes the picture: {hook!r}")
    if len(cue.split()) > MAX_CUE_WORDS:
        errs.append(f"cue over {MAX_CUE_WORDS} words: {cue!r}")
    return errs


# Unicode bidi isolate. Without it a Latin run inside a Hebrew line drags its
# neighbouring punctuation around — "[BLACK BOX]" renders as "]BLACK BOX[", and
# a bracketed string ending in a digit or a quote scrambles worse than that.
# FSI/PDI tells the renderer to lay the run out on its own and put it back where
# it belongs, which is what makes a mixed-direction legend readable at all.
_FSI, _PDI = "⁨", "⁩"


def _ltr(s: str) -> str:
    """Isolate a Latin run so it renders correctly inside Hebrew text."""
    return f"{_FSI}{s}{_PDI}" if s else s


# Lettering clauses inside a hand-authored stock prop. 106 of the 144 venues
# carry at least one — "photocopier with a DUAL COPIER/SCANNER label taped to
# it", "gantry crane hook stamped 5 TONNES". As background they are worse than
# useless: they spend the picture's legibility on strings that carry no fact,
# and one of them was condemned by three separate audits for teaching that
# SSRIs are dual-reuptake agents. scrub_prop_text only case-folds them, which
# leaves the instruction to letter the object intact.
# Cut from the lettering verb ONWARD only. An earlier version allowed the
# pattern to reach backwards across the phrase, so "overhead gantry crane hook
# stamped 5 TONNES" lost the crane hook as well as the lettering — 203 of the
# 421 lettered props were being deleted outright rather than cleaned.
_LETTERING_CLAUSE = re.compile(
    r"\s*(?:,\s*)?(?:and\s+)?\b(?:stamped|stencil\w*|reading|marked|labell?ed"
    r"|lettered|inscribed|titled)\b[^,;]*", re.I)
_TRAILING_LABEL = re.compile(
    r"\s*(?:,)?\s*with\s+an?\s+[^,;]*\b(?:label|sign|notice|placard|nameplate|"
    r"decal|sticker)\b[^,;]*", re.I)


def plain_clutter(text: str) -> str:
    """A stock prop with its lettering removed, for use as silent background."""
    t = _TRAILING_LABEL.sub("", text or "")
    t = _LETTERING_CLAUSE.sub("", t)
    t = re.sub(r"['\"“”][^'\"“”]{1,40}['\"“”]", "", t)      # quoted strings
    t = re.sub(r"\b[A-Z][A-Z0-9/&.-]{2,}\b", "", t)          # bare caps runs
    # "A-frame notice: NO NAKED FLAMES" must not survive as "A-frame notice: NO"
    t = re.sub(r"\s*:\s*[^,;]{0,12}$", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;-:")
    return t


def _short_cue(cue: str, keep: int = 5) -> str:
    """Enough of a cue to point at the prop again, on a repeat row.

    The bracketed Latin address goes — it was there so the reader could find
    the lettering in the picture, and by the second row they have found it.
    """
    head = re.split(r"\s*[\[(]", cue.strip(), maxsplit=1)[0].strip(" ,.–—")
    words = head.split()
    return " ".join(words[:keep]) + ("…" if len(words) > keep else "")


def render_legend(table: dict, plate: Plate, topic: str,
                  undrawn: list[str] | None = None,
                  venue_why: str | None = None) -> str:
    """The .txt file: topic line, 3-sentence walkthrough, numbered rows.

    Never describes the picture. Every row is an exam fact with a findable
    verbal address and a one-clause derivation, and every figure is stated
    verbatim whether or not it reached the pixels.
    """
    els = table["elements"]
    v = table.get("venue_he") or table["venue"]
    zones = table.get("zones_he") or table.get("zones", [])
    by_id = {f.id: f for f in plate.facts}

    # THE SIGN COMES FIRST. The venue's name is the pun, and the pun is the
    # entire retrieval hook: remember the place, remember the chapter. It was in
    # venues.json all along and the legend never printed it — chapter 6 is drawn
    # in NOR'EAST PINES BANDSTAND, where Nor'east is NE is NorEpinephrine, and a
    # reader was given the place description and the activity with no hint of
    # why that place. The owner said the scenes felt arbitrary. They were not;
    # the reason was simply never shown.
    sign = (table.get("venue") or {}).get("sign_text", "").strip()
    why = (venue_why or "").strip()
    head = [topic]
    if sign:
        head.append(f'השלט: "{_ltr(sign)}"' + (f" — {why}" if why else ""))
    head += [
        f"המקום: {v['place'].split(':')[0].strip()}. "
        f"מה כולם עושים: {v['activity'].split('.')[0].strip()}. "
        f"האזורים: {' · '.join(zones)}.",
        "",
    ]

    # An element carrying several facts used to repeat its hook on every one of
    # them. Chapter 6's legend had ten duplicated explanations across 29 rows,
    # one of them printed three times word for word — which is both most of the
    # length the owner complained about and the reason it is hard to follow: a
    # reader cannot tell a new idea from one they have already read.
    #
    # The hook belongs to the ELEMENT, so it is printed once, on that element's
    # first row. Its remaining facts hang under it as plain rows, and the reader
    # can see at a glance that they share a prop.
    rows, n, hook_shown = [], 0, set()
    for e in els:
        for fid in e.get("fact_ids", []):
            f = by_id.get(fid)
            if not f:
                continue
            n += 1
            # Hebrew cue for reading, the Latin address in brackets for finding.
            # Both are needed: many props are lettered in English in the picture
            # (BLACK BOX, 5-HT, CBT) because the terms are clinical, so a purely
            # Hebrew cue cannot point at them — but a purely Latin cue makes a
            # Hebrew page unreadable.
            cue = (e.get("cue_he") or e.get("cue") or "").strip()
            latin = (e.get("cue") or "").strip()
            if e.get("text"):
                latin = f'{latin} "{e["text"]}"' if latin else f'"{e["text"]}"'
            if latin and latin != cue:
                cue = f"{cue} [{_ltr(latin)}]"
            hook = (e.get("hook") or "").strip()
            eid = e.get("id") or cue
            if eid in hook_shown:
                # Printing the hook once fixed half of this. The CUE was still
                # reprinted in full on every fact the element carries, so an
                # element with three facts spent three thirty-word cues saying
                # the same thing — most of the length the owner objected to,
                # and the reason a reader cannot tell a new row from a repeat.
                # Later rows name the prop and stop.
                hook = ""
                cue = _short_cue(cue)
            else:
                hook_shown.add(eid)
            rows.append(f"{n}. {cue}: {f.text}" + (f" — {hook}" if hook else ""))

    tail = []
    # Facts the triage kept off the picture. They are still the chapter's
    # material and still examinable — what they are not is arbitrary, so they
    # get a sentence instead of a prop. Numbering continues from the drawn rows
    # so the reader sees one list, not a demotion.
    if getattr(plate, "legend_only", None):
        tail += ["", "בלי ציור (נובע מההגדרה, לא דורש מנמוני):"]
        for f in plate.legend_only:
            n += 1
            # The row needs an address before the colon, and not only so it
            # reads like the drawn rows: render_quiz blanks everything after
            # the first "<label>: ", so a row without one is printed to the
            # quiz with its answer still in it. These facts are undrawn, not
            # untested.
            label = _ltr((f.subkey or f.aspect or "").replace("_", " ").strip())
            tail.append(f"{n}. {label}: {f.text}" if label else f"{n}. —: {f.text}")

    if undrawn:
        tail += ["", "לא צויר: " + " · ".join(undrawn)]
    # Every figure in the chapter reaches this line, whether it was painted,
    # left to the legend, or attached to a fact the triage moved off the plate.
    exam = [f"{n_.op}{n_.paint}".strip()
            for f in list(plate.facts) + list(getattr(plate, "legend_only", []))
            for n_ in f.numbers]
    if exam:
        tail += ["", "מספרים לבחינה: " + " · ".join(dict.fromkeys(exam))]

    return "\n".join(head + rows + tail).strip() + "\n"


def render_quiz(legend: str) -> str:
    """The same numbered list with every fact half deleted — retrieval practice.

    One string operation, and it is the only artifact in the system that
    measures recall rather than familiarity.
    """
    out = []
    for line in legend.splitlines():
        m = re.match(r"^(\d+\.\s*)(.+?):\s", line)
        out.append(f"{m.group(1)}{m.group(2)}: ______" if m else line)
    return "\n".join(out) + "\n"


# ── self-test ──────────────────────────────────────────────────────────────
def _test_table_normalization() -> None:
    """Over-long cues are trimmed, not bounced; over-long hooks are accepted.

    Both rules exist because the chapter 28 director burned all three of its
    attempts on word counts and the chapter produced no plate at all.
    """
    long_cue = "Tally slate showing '70%' re-dyeing and '20-40%' to 'Bipolar Weaving'"
    t = {"elements": [{"id": "e6", "cue": long_cue}]}
    notes = normalize_table(t)
    ok = t["elements"][0]["cue"] == "Tally slate" and len(notes) == 1
    print(f"  [{'OK' if ok else 'FAIL'}] over-long cue trimmed to its prop name "
          f"-> {t['elements'][0]['cue']!r}")

    # a trim that would collide walks out to a longer free name instead
    t = {"elements": [{"id": "e6", "cue": long_cue},
                      {"id": "e7", "cue": "Tally slate"}]}
    normalize_table(t)
    got = t["elements"][0]["cue"]
    ok = (got.lower() != "tally slate" and got != long_cue
          and len(got.split()) <= MAX_CUE_WORDS)
    print(f"  [{'OK' if ok else 'FAIL'}] a colliding trim walks out to a free "
          f"name -> {got!r}")

    # no dangling function word at the cut
    cut = _trim_cue("Depth marker plate bolted to the deeper terrace where the "
                    "teenagers stand")
    ok = not cut.split()[-1].lower() in ("where", "the", "to")
    print(f"  [{'OK' if ok else 'FAIL'}] hard cut does not end on a function "
          f"word -> {cut!r}")

    # a canonical name in the wrong case is the same name
    t = {"elements": [{"id": "e1", "cue": "x", "cue_he": "א", "prop": "p",
                       "hook": "מילה " * 8, "derivation": "printed-number",
                       "relation": "blocks", "relates_to": "e1"}]}
    errs = validate_table(t, [])
    el = t["elements"][0]
    ok = (el["relation"] == "BLOCKS" and el["derivation"] == "PRINTED_NUMBER"
          and not any("is not one of" in e for e in errs))
    print(f"  [{'OK' if ok else 'FAIL'}] 'blocks' and 'printed-number' are "
          f"accepted as BLOCKS and PRINTED_NUMBER")

    # the leftovers a director leaves behind are cleared, not bounced
    t = {"zones": ["a"], "elements": [
        {"id": "e1", "cue": "x", "fact_ids": ["F1", "RAPHE_TAPESTRY"],
         "text": "one two three four five six"},
        {"id": "e2", "cue": "y", "fact_ids": ["F2"], "relation": "BLOCKS",
         "relates_to": "e99"},
        {"id": "e3", "cue": "z", "fact_ids": ["F1"], "relation": "BLOCKS"},
    ]}
    normalize_table(t, known_facts={"F1", "F2"})
    e1, e2, e3 = t["elements"]
    ok = (e1["fact_ids"] == ["F1"] and e1["text"] == ""
          and "relation" not in e2 and "relates_to" not in e2
          and "relation" not in e3)
    print(f"  [{'OK' if ok else 'FAIL'}] unknown fact ids, dangling relations "
          f"and over-long strings are cleared, not bounced")

    # a derivation the director invented maps to the enum
    t = {"elements": [{"id": "e1", "cue": "x", "cue_he": "א", "prop": "p",
                       "hook": "מילה " * 8, "derivation": "PUN"}]}
    validate_table(t, [])
    ok = t["elements"][0]["derivation"] == "PHONETIC_PUN"
    print(f"  [{'OK' if ok else 'FAIL'}] derivation PUN -> PHONETIC_PUN")

    # a Hebrew zone name is mapped back to its English twin, not rejected
    t = {"zones": ["the mixing floor", "the pit yard"],
         "zones_he": ["רצפת הערבוב", "רחבת הבורות"],
         "elements": [{"id": "e1", "zone": "רחבת הבורות"},
                      {"id": "e2", "zone": "the loading bay"}]}
    normalize_table(t)
    mapped = t["elements"][0]["zone"] == "the pit yard"
    added = "the loading bay" in t["zones"]
    print(f"  [{'OK' if mapped and added else 'FAIL'}] a Hebrew zone maps to "
          f"its twin; an unknown zone joins the list")

    # the asserting verb is rewritten only on the last rung, and only then
    asserted = "הלוח — " + "מילה " * 8 + "מסמל את המצב"
    t = {"elements": [{"id": "e1", "hook": asserted}]}
    normalize_table(t)                    # not aggressive
    kept = t["elements"][0]["hook"] == asserted
    normalize_table(t, aggressive=True)
    gone = not _HOOK_ASSERTS.search(t["elements"][0]["hook"])
    print(f"  [{'OK' if kept and gone else 'FAIL'}] מסמל survives two rounds, "
          f"then is rewritten as an apposition")

    # an over-long hook is a warning, not a rejection
    hook = "מילה " * 40
    el = {"id": "e1", "cue": "Slate", "cue_he": "לוח", "hook": hook,
          "prop": "a slate", "fact_ids": [], "derivation": "PRINTED_NUMBER"}
    soft: list[str] = []
    errs = validate_table({"elements": [el]}, [], soft=soft)
    ok = not any("hook over" in e for e in errs) and any("hook over" in x for x in soft)
    print(f"  [{'OK' if ok else 'FAIL'}] over-long hook is soft, not a rejection")


def _test_relation_normalization() -> None:
    """Verify that director synonyms are normalized rather than rejected."""
    # a minimal table with one element using a synonym the director naturally reaches for
    test_facts = [Fact(id="f1", aspect="test", subkey="x", text="test fact",
                       track="profile", priority=50)]
    table = {
        "venue": {"sign_text": "TEST", "place": "a test place", "activity": "testing"},
        "anchor_structure": "a large inanimate object",
        "zones": ["zone A", "zone B"],
        "elements": [{
            "id": "e1", "fact_ids": ["f1"], "cue": "test prop",
            "prop": "a test prop", "zone": "zone A", "derivation": "LITERAL_DEPICTION",
            "relates_to": "e2", "relation": "INHIBITS",  # synonym, not in RELATIONS
        }, {
            "id": "e2", "fact_ids": ["f1"], "cue": "another prop",
            "prop": "another prop", "zone": "zone B", "derivation": "LITERAL_DEPICTION",
        }],
    }
    errs = validate_table(table, test_facts)
    # should NOT reject because INHIBITS is a synonym for BLOCKS
    assert "INHIBITS" not in " ".join(errs), f"INHIBITS was rejected: {errs}"
    # should have been normalized to BLOCKS
    assert table["elements"][0]["relation"] == "BLOCKS", (
        f"relation not normalized: {table['elements'][0]['relation']}")
    print("  [OK] relation normalization: INHIBITS -> BLOCKS")

    # test an unknown relation still fails
    table["elements"][0]["relation"] = "MYSTIFIES"
    errs = validate_table(table, test_facts)
    assert any("MYSTIFIES" in e for e in errs), f"unknown relation MYSTIFIES not rejected: {errs}"
    print("  [OK] unknown relation MYSTIFIES rejected")


def _demo(path: Path) -> None:
    facts = atomize(json.loads(path.read_text(encoding="utf-8")))
    print(f"atomized {len(facts)} facts from {path.name}\n")

    plates = plan_plates(facts)
    for p in plates:
        drawn = triage_numbers(p)
        print(f"── plate {p.name}: {len(p.facts)} facts, "
              f"{len(drawn)}/{sum(len(f.numbers) for f in p.facts)} numbers painted")
        for n in drawn:
            print(f"     PAINT {n.paint:>8}  tag={n.tag:<6} {n.kind:<9} "
                  f"prio={n.priority}")
    print()
    print("first 12 facts:")
    for f in facts[:12]:
        nums = ",".join(n.paint for n in f.numbers) or "—"
        print(f"  [{f.track:8}] {f.id:34} p{f.priority:<3} nums={nums:<14} "
              f"{f.text[:44]}")


if __name__ == "__main__":
    import sys
    print("── unit tests ──")
    _test_relation_normalization()
    _test_table_normalization()
    print()

    p = Path(sys.argv[1] if len(sys.argv) > 1 else "_view/facts.json")
    if not p.exists():
        raise SystemExit(f"no facts file at {p}")
    _demo(p)
