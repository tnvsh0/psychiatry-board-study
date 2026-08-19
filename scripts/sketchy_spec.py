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
  plan_plates()      Facts -> plates (main track + criteria track)
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
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ── plate geometry ─────────────────────────────────────────────────────────
FACTS_PER_PLATE = 30       # target density; the corpus runs 15-50 per page
PLATE_SPLIT_AT = 45        # split into a second plate only past this
MAX_PAINTED_NUMBERS = 3    # per plate. 0 is an explicitly legal outcome.
MAX_REQUIRED_STRINGS = 18  # gated legible strings, <=5 words each
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
    track: str         # "criteria" or "main" — the hybrid split
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
    r"\s*(?P<unit>%|שנים|שנה|שבועות|שבוע|חודשים|חודש|ימים|mg|g/day|mEq/L|"
    r"years?|weeks?|months?|days?|hours?)?",
    re.I,
)

_UNIT_CANON = {
    "שנים": "yr", "שנה": "yr", "years": "yr", "year": "yr",
    "שבועות": "wk", "שבוע": "wk", "weeks": "wk", "week": "wk",
    "חודשים": "mo", "חודש": "mo", "months": "mo", "month": "mo",
    "ימים": "d", "days": "d", "day": "d",
}


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


def normalize_number(text: str, fid: str = "") -> list[NumberSpec]:
    """Extract every numeric value from a fact and normalise it for painting.

    Ratios are never turned into counted people: a 1-versus-2 queue inside an
    18-figure frame is unrecoverable and no gate can verify it, which is
    exactly the shipped bug where the render drew roughly 7:1 male while the
    caption asserted 1:2. A ratio is either painted literally on one carrier
    or it stays in the legend.
    """
    out: list[NumberSpec] = []
    prio = _priority(fid)
    for m in _NUM.finditer(text):
        if _is_identifier(text, m):
            continue
        a, b, sep = m.group("a"), m.group("b"), m.group("sep")
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
        if unit in ("yr", "wk", "mo", "d") and kind in ("SCALAR", "RANGE"):
            kind = "DURATION"

        # Carry the unit into the picture. A bare "2" painted for "2 weeks"
        # recovers nothing — the reader encodes a digit with no referent, which
        # is the failure the tag exists to prevent. "%" and duration units are
        # short enough to letter; everything longer stays in the legend.
        if unit == "%":
            paint += "%"
        elif kind == "DURATION":
            paint += unit

        tag = _tag_for(fid, unit)
        if kind == "DURATION":
            tag = {"yr": "YRS", "wk": "WKS", "mo": "MOS", "d": "DAYS"}.get(unit, tag)

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
            track="criteria" if aspect == "criteria" else "main",
            priority=_priority(path),
            numbers=normalize_number(text, path),
        ))
    return out


# ── plates ─────────────────────────────────────────────────────────────────
@dataclass
class Plate:
    track: str          # "main" | "criteria"
    index: int          # 1-based within the track
    facts: list         # list[Fact]
    venue: dict = field(default_factory=dict)

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
    plates = []
    for track in ("main", "criteria"):
        sel = [f for f in facts if f.track == track]
        if not sel:
            continue
        n = 1 if len(sel) <= PLATE_SPLIT_AT else math.ceil(len(sel) / FACTS_PER_PLATE)
        per = math.ceil(len(sel) / n)
        for i in range(n):
            chunk = sel[i * per:(i + 1) * per]
            if chunk:
                plates.append(Plate(track=track, index=i + 1, facts=chunk))
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
                "required": ["id", "fact_ids", "cue", "prop", "zone", "derivation"],
                "properties": {
                    "id": {"type": "string"},
                    "fact_ids": {"type": "array", "items": {"type": "string"},
                                 "minItems": 1},
                    "cue": {"type": "string"},
                    "prop": {"type": "string"},
                    "zone": {"type": "string"},
                    "text": {"type": "string"},
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
HOOK_MAX_WORDS = 25


def validate_table(table: dict, facts: list[Fact]) -> list[str]:
    """Hard errors that must send the director back. Empty list == accept."""
    errs: list[str] = []
    els = table.get("elements") or []
    if not els:
        return ["no elements"]

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
            errs.append(f"{eid}: cue over {MAX_CUE_WORDS} words — {cue!r}")
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
            errs.append(f"{eid}: hook over {HOOK_MAX_WORDS} words: {hook!r}")
        elif _HOOK_ASSERTS.search(hook):
            errs.append(f"{eid}: hook uses מסמל/מייצג, which asserts the link "
                        f"instead of showing why it holds: {hook!r}")

        der = e.get("derivation")
        if der not in DERIVATIONS:
            errs.append(f"{eid}: derivation {der!r} is not one of {', '.join(DERIVATIONS)}")
        rel = e.get("relation")
        if rel and rel not in RELATIONS:
            # accept synonyms — the director shouldn't have to learn our vocabulary
            canonical = RELATION_SYNONYMS.get(rel.upper().replace(" ", "_"))
            if canonical:
                e["relation"] = canonical  # mutate in place so the prompt sees the right one
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
        errs.append(f"{n_metaphor}/{len(els)} free metaphors — cap is "
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


def scrub_prop_text(s: str) -> str:
    """Strip anything the illustrator might paint as a word or a numeral."""
    s = _SCRUB_PARENS.sub("", s)          # teaching notes live in parentheses
    s = _SCRUB_QUOTED.sub(r"\1", s)       # quotes read as "letter this"
    s = _SCRUB_CAPS.sub(lambda m: m.group(0).capitalize(), s)
    s = _SCRUB_DIGITS.sub("", s)          # stray numerals break the NUMBER gate
    return re.sub(r"\s{2,}", " ", s).strip(" ,;")


def build_image_prompt(table: dict, plate: Plate, style: str,
                       chroma_plates: bool = True) -> str:
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
their own way. Nobody interacts with anybody else except where a relation is
named below; nothing causes anything else; there is no story unfolding.
{v['activity']}

THE ANCHOR — the DOMINANT element of the picture, and it is INANIMATE. It cuts
the frame on a diagonal and divides it into zones. It is the largest thing in
the image. NO PROTAGONIST: no figure is larger or more important than the rest.
{table['anchor_structure']}

ZONES, and what belongs in each: {' · '.join(zones)}"""

    props = []
    for i, e in enumerate(els, 1):
        line = f"  {i}. {scrub_prop_text(e['prop'])}"
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
            plate_lines.append(
                f"  - a blank flat plate of {cname} (exactly RGB "
                f"{rgb[0]},{rgb[1]},{rgb[2]}) on {n.carrier or 'a sign panel'}"
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

HOW DRAWN — square-on to the viewer, whole and unbroken, its four corners all
visible, standing clear of everything else. Nothing overlaps it: no person, no
pipe, no rope, no beam, no other sign crosses its face or clips its corners.
These colours appear NOWHERE ELSE in the picture.
"""
    else:
        number_block = ""

    plate_reminder = ("; the blank colour plates are large boards, a fifth of the "
                      "image height, unobstructed" if number_block else "")

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

TEXT TO PAINT — these strings, and ONLY these, are legible. Every other piece
of paper, notice, board, poster and label in the scene is illegible squiggle-
lines. NO numerals appear anywhere except where a blank plate is specified.
{text_block}
{number_block}
REMINDER — {v['place']}; everyone is {v['activity'][:120]}; the largest thing in
frame is {table['anchor_structure'][:120]}; no protagonist — every person the
same size, nobody near the viewer, nobody cropped by the frame{plate_reminder}.
"""


def build_plate_director(plate: Plate, venue: dict, topic: str,
                         tokens: dict | None = None) -> str:
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
{num_lines}
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
- `triple` is [category, material, dominant colour] and must be unique across
  elements whose facts are unrelated.
- `hook` is IN HEBREW, one flowing sentence of 8-25 words that says BOTH what
  is there AND why it is the right object, woven together. Write it the way you
  would say it out loud to someone looking at the picture.
    GOOD: "כמו שבבית צביעה מתכון נמדד באחוז ממשקל הבד, כך שכיחות הדיכאון מופיעה
          באחוזים על לוח המתכונים"
    BAD (only describes): "לוח המתכונים מציג את שיעורי השכיחות"
    BAD (only explains):  "מתכון נמדד באחוז"
    BAD (a label):        "לוח מתכונים = שכיחות"
  The everyday logic of the object must be visible in the sentence — that logic
  is what makes the fact stick. Never "מסמל" or "מייצג": those assert a link
  instead of showing why it holds.
- `venue_he` and `zones_he` are the place, the shared activity and the zone
  names in Hebrew. The whole legend is read by a Hebrew speaker; leaving the
  header in English makes the page unreadable to them.

Return STRICT JSON only, matching this shape:
{{"venue": {{"sign_text": "...", "place": "...", "activity": "..."}},
 "venue_he": {{"place": "...", "activity": "..."}},
 "anchor_structure": "...",
 "zones": ["...", "..."],
 "zones_he": ["...", "..."],
 "elements": [
   {{"id": "e1", "fact_ids": ["..."], "cue": "...", "cue_he": "...",
     "prop": "...", "zone": "...", "text": "", "derivation": "LITERAL_DEPICTION",
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


def render_legend(table: dict, plate: Plate, topic: str,
                  undrawn: list[str] | None = None) -> str:
    """The .txt file: topic line, 3-sentence walkthrough, numbered rows.

    Never describes the picture. Every row is an exam fact with a findable
    verbal address and a one-clause derivation, and every figure is stated
    verbatim whether or not it reached the pixels.
    """
    els = table["elements"]
    v = table.get("venue_he") or table["venue"]
    zones = table.get("zones_he") or table.get("zones", [])
    by_id = {f.id: f for f in plate.facts}

    head = [
        topic,
        f"המקום: {v['place'].split(':')[0].strip()}. "
        f"מה כולם עושים: {v['activity'].split('.')[0].strip()}. "
        f"האזורים: {' · '.join(zones)}.",
        "",
    ]

    rows, n = [], 0
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
            rows.append(f"{n}. {cue}: {f.text}" + (f" — {hook}" if hook else ""))

    tail = []
    not_drawn = [n_.paint for f in plate.facts for n_ in f.numbers if not n_.drawn]
    if undrawn:
        tail += ["", "לא צויר: " + " · ".join(undrawn)]
    exam = [f"{n_.op}{n_.paint}".strip()
            for f in plate.facts for n_ in f.numbers]
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
def _test_relation_normalization() -> None:
    """Verify that director synonyms are normalized rather than rejected."""
    # a minimal table with one element using a synonym the director naturally reaches for
    test_facts = [Fact(id="f1", aspect="test", subkey="x", text="test fact",
                       track="main", priority=50)]
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
    print()

    p = Path(sys.argv[1] if len(sys.argv) > 1 else "_view/facts.json")
    if not p.exists():
        raise SystemExit(f"no facts file at {p}")
    _demo(p)
