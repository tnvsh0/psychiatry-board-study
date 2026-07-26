#!/usr/bin/env python3
"""
Deterministic venue audit — computes exactly which of the 144 venues must be
re-authored, so the (expensive, rate-limited) repair run only touches those.

Three independent findings, all computed in code, no model involved:

  1. COLLISION  two chapters occupy the same kind of real-world place. The
                first authoring run batched twelve chapters at a time, so no
                agent could see the others; it produced six launderettes and
                three bowling alleys. Spec §R1 now requires global uniqueness.
  2. REJECTED   the adversarial critic rejected the venue outright.
  3. HOSTING    the chapter needs one of the course's 17 hand-built scenes
                (Aa Hotel, Raphe tapestry, DOPA-MINE...) and the venue never
                mentions it. Spec §R9: those are lexicon tokens that must live
                *inside* the venue, so the venue has to be able to host them.

Within a collision group the venue with the strongest claim keeps the place;
the rest are queued for re-authoring. Claim strength = critic ACCEPT, then
declared confidence, then derivation (a phonetic pun beats a literal picture),
then the lower chapter index as a stable tie-break.

Usage:  python scripts/_venue_audit.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Place categories, most specific first — the first match wins, so "laundry"
# is tested before the catch-all "shop". Ordering matters more than coverage:
# a venue that matches nothing is simply unique, which is the safe default.
CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("laundrette",   ("laundromat", "launderette", "laundry", "dry clean", "dry-clean")),
    ("bowling",      ("bowling", "duckpin", "ten-pin", "tenpin")),
    ("swimming",     ("swimming pool", "municipal pool", "lido", "baths")),
    ("garden",       ("garden centre", "garden center", "nursery", "polytunnel", "potting")),
    ("self-storage", ("self-storage", "self storage", "storage facility", "storage depot")),
    ("bakery",       ("bakery", "baker's", "bread")),
    ("delicatessen", ("delicatessen", "deli counter", "butcher")),
    ("letterpress",  ("letterpress", "type case", "printing press")),
    ("fire-station", ("fire station", "fire brigade")),
    ("vehicle-test", ("vehicle test", "test station", "test centre", "mot ")),
    ("car-park",     ("park-and-ride", "park and ride", "multi-storey", "car park")),
    ("car-repair",   ("brake", "tyre", "tire", "speed shop", "salvage", "car wash",
                      "repair workshop", "garage")),
    ("bus-depot",    ("bus depot", "coach depot", "bus interchange", "transit depot")),
    ("railway",      ("railway", "terminus", "platform", "sleeper")),
    ("filling",      ("filling station", "petrol", "fuel", "charging forecourt")),
    ("sawmill",      ("sawmill", "log deck", "timber yard")),
    ("mine",         ("colliery", "coal mine", "pithead", "quarry", "open-pit")),
    ("ski",          ("ski resort", "ski lift", "piste")),
    ("courtroom",    ("courtroom", "county court", "court in session")),
    ("school",       ("school", "classroom", "gymnasium", "elementary")),
    ("hotel",        ("hotel", "motel", "guest house")),
    ("hospital",     ("clinic", "surgery", "pharmacy", "dispensary", "ward")),
    ("sorting",      ("sorting depot", "sorting office", "parcel depot", "delivery office")),
    ("travel",       ("travel agency", "travel agent")),
    ("tailor",       ("alterations", "tailoring", "upholstery", "seamstress")),
    ("brewery",      ("brewery", "bottling", "winery", "cellar")),
    ("market",       ("farmers' market", "market stall", "produce")),
    ("hardware",     ("hardware", "ironmonger", "builders' merchant", "diy store")),
    ("dancehall",    ("ballroom", "dance hall", "waltz")),
    ("fairground",   ("fairground", "midway", "funfair", "carnival")),
    ("zoo",          ("zoo", "aquarium", "panda house")),
    ("boatyard",     ("boatyard", "hard-standing", "marina")),
    ("substation",   ("substation", "boiler", "power", "electrical works", "transmitter")),
    ("junkshop",     ("junk shop", "second-hand", "secondhand", "variety store", "clearance")),
]

DERIVATION_RANK = {
    "PHONETIC_PUN": 3, "ACRONYM_EMBED": 3,
    "LITERAL_PICTURE": 2, "SPATIAL_ISOMORPHISM": 2,
}
CONFIDENCE_RANK = {"strong": 3, "workable": 2, "weak": 1}


# Where the naming clause ends and the moment begins.
_MOMENT = re.compile(
    r"[:;—–]"
    r"|\s+(?:on|at|during|in|mid|after|before|open|wedged|next\s+to|between)\b"
    r"|,",
    re.I,
)


def place_head(place: str) -> str:
    """The kind of place, with the scene-setting stripped off.

    Every authored `place` names the venue type and then describes the moment:
    "A high-street bakery at the Saturday-morning rush: flour dust in the
    window light…". Matching keywords against the whole string called an
    aquarium a school ("…during a class visit") and a shoe shop a school
    ("…the back-to-school fitting rush") — the word was in the scenery, not in
    the venue type. Cutting at the first colon was not enough; the moment
    usually starts at a preposition. Cut there instead.
    """
    return _MOMENT.split(place, maxsplit=1)[0][:70].lower()


def categorise(v: dict) -> str:
    """The kind of real-world place this venue is.

    Deliberately ignores `sign_text`: the sign is a pun, not a place type.
    Matching it made OLD SCHOOL AQUARIUM a school.
    """
    head = place_head(v.get("place", ""))
    for name, keys in CATEGORIES:
        if any(k in head for k in keys):
            return name
    return f"unique:{v['index']}"


def claim_strength(v: dict, verdict: dict | None) -> tuple:
    """Higher sorts first — this venue keeps the place."""
    return (
        1 if (verdict or {}).get("verdict") == "ACCEPT" else 0,
        CONFIDENCE_RANK.get(v.get("confidence"), 0),
        DERIVATION_RANK.get(v.get("derivation"), 0),
        -v["index"],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write the audit to this path")
    a = ap.parse_args()

    venues = {v["index"]: v for v in
              json.loads((ROOT / "_venues_partial.json").read_text(encoding="utf-8"))}
    verdicts = {v["index"]: v for v in
                json.loads((ROOT / "_verdicts_partial.json").read_text(encoding="utf-8"))}
    sym = json.loads((ROOT / "schedule/symbols.json").read_text(encoding="utf-8"))
    scenes = set(sym.get("scenes", {}))
    sch = {d["index"]: d for d in
           json.loads((ROOT / "schedule/schedule.json").read_text(encoding="utf-8"))["days"]}

    # ── 1. collisions ──────────────────────────────────────────────────────
    groups: dict[str, list[int]] = defaultdict(list)
    for i, v in venues.items():
        groups[categorise(v)].append(i)

    collisions, loses_place = {}, {}
    for cat, idxs in sorted(groups.items()):
        if cat.startswith("unique:") or len(idxs) < 2:
            continue
        ranked = sorted(idxs, key=lambda i: claim_strength(venues[i], verdicts.get(i)),
                        reverse=True)
        collisions[cat] = ranked
        for i in ranked[1:]:
            loses_place[i] = (cat, ranked[0])

    # ── 2. critic rejections ───────────────────────────────────────────────
    rejected = {i for i, v in verdicts.items() if v.get("verdict") == "REJECT"}
    unreviewed = {i for i in venues if i not in verdicts}

    # ── 3. cannot host the chapter's required lexicon tokens ───────────────
    cannot_host = {}
    for i, v in venues.items():
        need = sorted(set(sch[i].get("symbols_needed", [])) & scenes)
        if not need:
            continue
        blob = f"{v['place']} {v['sign_text']} {v['pun_explanation']}".lower()
        missing = [s for s in need
                   if not any(w.lower() in blob for w in s.split("_") if len(w) > 3)]
        if missing:
            cannot_host[i] = missing

    redo = sorted(set(loses_place) | rejected | set(cannot_host))

    # ── report ─────────────────────────────────────────────────────────────
    print("=" * 72)
    print("PLACE-CATEGORY COLLISIONS (global uniqueness is now required)")
    print("=" * 72)
    for cat, ranked in sorted(collisions.items(), key=lambda kv: -len(kv[1])):
        print(f"\n[{cat}] {len(ranked)} chapters — ch{ranked[0]} keeps it")
        for i in ranked:
            v, mark = venues[i], "KEEP " if i == ranked[0] else "REDO "
            ver = (verdicts.get(i) or {}).get("verdict", "—")
            print(f"  {mark} ch{i:>3} {v['sign_text'][:34]:36} "
                  f"{v['confidence']:9} {ver}")

    print("\n" + "=" * 72)
    print("CANNOT HOST THE CHAPTER'S OWN LEXICON TOKENS")
    print("=" * 72)
    for i, missing in sorted(cannot_host.items()):
        print(f"  ch{i:>3} {venues[i]['sign_text'][:30]:32} needs {', '.join(missing)}")

    print("\n" + "=" * 72)
    print("TALLY")
    print("=" * 72)
    print(f"  collision losers   {len(loses_place):>4}")
    print(f"  critic rejections  {len(rejected):>4}")
    print(f"  cannot host tokens {len(cannot_host):>4}")
    print(f"  ── unique chapters to re-author: {len(redo)}")
    print(f"  still never reviewed by a critic: {len(unreviewed)}")

    if a.json:
        payload = {
            "redo": redo,
            "collisions": collisions,
            "loses_place": {str(k): v for k, v in loses_place.items()},
            "rejected": sorted(rejected),
            "cannot_host": {str(k): v for k, v in cannot_host.items()},
            "unreviewed": sorted(unreviewed),
            "taken_categories": {c: r[0] for c, r in collisions.items()},
        }
        Path(a.json).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
