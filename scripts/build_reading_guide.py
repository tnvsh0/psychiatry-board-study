#!/usr/bin/env python3
"""Derive a per-chapter reading guide (book -> printed pages) from book_map.json.

WHAT THIS IS, AND IS NOT. The gold-standard page references are the per-subtopic
"מפת המקורות" tables NotebookLM writes into each study_guide.md — but those exist
only for chapters that have been generated (2 of 144 as of writing). This script
fills the gap in the meantime: a CHAPTER-LEVEL reading pointer for all 144
curriculum chapters, built by mapping each one to the textbook chapter(s) that
cover it and reading their printed page ranges out of book_map.json.

So these are "read Dulcan ch.10, pp.173-196" pointers, not "criterion A is on
p.68" pointers. DSM is cited at diagnostic-CLASS granularity (that is all the
book's embedded TOC exposes). Synopsis has no printed pages (reflowed edition)
so it is cited by chapter. Chapters with no clean textbook home — most of the
pediatric-neurology block, and the theory chapters — say so honestly rather than
point at a wrong page; their real source is the syllabus + the flashcard summary.

The MAP below is a hand-built correspondence: curriculum index -> book chapters.
It is the auditable heart of this file; when the curriculum changes, edit it.

Writes schedule/reading_guide.json.
Usage:  python scripts/build_reading_guide.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[1]
BOOK_MAP = json.loads((REPO / "schedule" / "book_map.json").read_text(encoding="utf-8"))["books"]
DAYS = json.loads((REPO / "schedule" / "schedule.json").read_text(encoding="utf-8"))["days"]
OUT = REPO / "schedule" / "reading_guide.json"

# ── correspondence: curriculum index -> book chapters ────────────────────────
# D  = Dulcan chapter numbers        SE = Stahl Essential chapter numbers
# CA = Stahl C&A monograph numbers   DSM = DSM-5-TR class-title prefixes
# SYN = Synopsis chapter numbers (cited by chapter, no page)
# note = shown when there is no clean textbook home
NEURO = "נוירולוגיה — מהסילבוס ומהכרטיסיות; אין פרק ייעודי בספרי הליבה"
THEORY = "תיאוריה — מהכרטיסיות ומ-Synopsis; אין פרק ייעודי ב-Dulcan"

MAP: dict[int, dict] = {
    1:  {"D": [1, 4, 5]},
    2:  {"D": [6], "SE": [1]},
    3:  {"SE": [1, 2]},
    4:  {"SE": [4]},
    5:  {"SE": [5]},
    6:  {"SE": [6]},
    7:  {"SE": [3, 6]},
    8:  {"D": [2]},
    9:  {"D": [3]},
    10: {"D": [4]},
    11: {"D": [5]},
    12: {"D": [2], "SYN": [32], "note": THEORY},
    13: {"SYN": [32], "note": THEORY},
    14: {"DSM": ["Personality Disorders"], "SYN": [19]},
    15: {"D": [1]},
    16: {"D": [33]},
    17: {"D": [10], "DSM": ["Neurodevelopmental"], "SE": [11]},
    18: {"D": [34], "SE": [11], "CA": [1, 2, 19, 22, 23]},
    19: {"D": [34], "SE": [11], "CA": [5, 10, 17, 6]},
    20: {"D": [8], "DSM": ["Neurodevelopmental"]},
    21: {"D": [7], "DSM": ["Neurodevelopmental"]},
    22: {"D": [9], "DSM": ["Neurodevelopmental"]},
    23: {"D": [9], "DSM": ["Neurodevelopmental"]},
    24: {"D": [20], "DSM": ["Neurodevelopmental"]},
    25: {"D": [9], "DSM": ["Neurodevelopmental"]},
    26: {"D": [7]},
    27: {"D": [7, 8]},
    28: {"D": [13], "DSM": ["Depressive"], "SE": [6, 7]},
    29: {"D": [35], "SE": [7], "CA": [8, 13, 14, 16, 26, 30]},
    30: {"D": [35], "SE": [7], "CA": [12, 33, 6, 31]},
    31: {"D": [35], "SE": [7], "CA": [9]},
    32: {"D": [13], "DSM": ["Depressive"]},
    33: {"D": [14], "DSM": ["Bipolar"], "SE": [6, 7]},
    34: {"D": [14], "DSM": ["Bipolar"]},
    35: {"D": [36], "SE": [7], "CA": [20]},
    36: {"D": [36], "SE": [7], "CA": [32]},
    37: {"D": [13, 14, 26]},
    38: {"D": [15], "DSM": ["Anxiety"], "SE": [8]},
    39: {"D": [15], "DSM": ["Anxiety"], "SE": [8]},
    40: {"D": [15], "DSM": ["Anxiety"]},
    41: {"D": [15], "DSM": ["Anxiety"]},
    42: {"SE": [8, 10]},
    43: {"D": [17], "DSM": ["Obsessive-Compulsive"], "SE": [13]},
    44: {"D": [17, 43], "SE": [13]},
    45: {"D": [17], "DSM": ["Obsessive-Compulsive"]},
    46: {"D": [17], "DSM": ["Obsessive-Compulsive"]},
    47: {"D": [16], "DSM": ["Trauma"], "SE": [8]},
    48: {"D": [16], "SE": [8]},
    49: {"D": [16], "DSM": ["Trauma"]},
    50: {"D": [16], "DSM": ["Trauma"]},
    51: {"D": [24]},
    52: {"D": [16], "DSM": ["Trauma"]},
    53: {"DSM": ["Dissociative"], "SYN": [11]},
    54: {"DSM": ["Somatic Symptom"], "SYN": [12]},
    55: {"D": [18], "DSM": ["Schizophrenia Spectrum"], "SE": [4]},
    56: {"D": [18], "SE": [4]},
    57: {"D": [18], "DSM": ["Schizophrenia Spectrum"]},
    58: {"D": [37], "SE": [5], "CA": [7, 15, 18, 27]},
    59: {"D": [37], "SE": [5], "CA": [3, 4, 11, 21, 24, 25, 28, 29]},
    60: {"D": [37], "SE": [5], "CA": [11]},
    61: {"D": [37], "SE": [5], "CA": [3]},
    62: {"D": [37], "SE": [5], "CA": [24, 28, 29]},
    63: {"D": [37], "SE": [5], "CA": [21]},
    64: {"D": [37], "SE": [5], "CA": [25, 29]},
    65: {"D": [37], "DSM": ["Medication-Induced Movement"], "SE": [5]},
    66: {"D": [37], "DSM": ["Medication-Induced Movement"]},
    67: {"D": [37, 29], "DSM": ["Medication-Induced Movement"]},
    68: {"DSM": ["Schizophrenia Spectrum"]},
    69: {"D": [29]},
    70: {"SYN": [22], "note": "ECT — Synopsis פרק 22; אין פרק ייעודי ב-Dulcan"},
    71: {"D": [22], "DSM": ["Sleep-Wake"], "SE": [10]},
    72: {"D": [22], "SE": [10]},
    73: {"D": [22], "DSM": ["Sleep-Wake"], "SE": [10]},
    74: {"D": [22], "DSM": ["Sleep-Wake"]},
    75: {"D": [19], "DSM": ["Feeding and Eating"]},
    76: {"D": [19], "DSM": ["Feeding and Eating"]},
    77: {"D": [19], "DSM": ["Feeding and Eating"]},
    78: {"D": [21], "DSM": ["Elimination"]},
    79: {"D": [22]},
    80: {"D": [19, 41]},
    81: {"D": [12], "DSM": ["Substance"]},
    82: {"D": [12], "SE": [13]},
    83: {"D": [12], "DSM": ["Substance"]},
    84: {"D": [12], "SE": [13]},
    85: {"D": [12], "DSM": ["Substance"], "SE": [13]},
    86: {"D": [12], "DSM": ["Substance"]},
    87: {"D": [12], "CA": [6]},
    88: {"D": [12], "DSM": ["Substance"]},
    89: {"D": [12], "DSM": ["Substance"]},
    90: {"D": [12], "DSM": ["Substance"], "SE": [13]},
    91: {"DSM": ["Personality Disorders"], "SYN": [19]},
    92: {"DSM": ["Personality Disorders"], "SYN": [19]},
    93: {"DSM": ["Personality Disorders"], "SYN": [19]},
    94: {"DSM": ["Personality Disorders"], "SYN": [19]},
    95: {"SYN": [19, 23], "note": "טיפול DBT ב-BPD — Synopsis; אין פרק ייעודי ב-Dulcan"},
    96: {"D": [11], "DSM": ["Personality Disorders"], "SYN": [19]},
    97: {"DSM": ["Personality Disorders"], "SYN": [19]},
    98: {"DSM": ["Personality Disorders"], "SYN": [19]},
    99: {"DSM": ["Personality Disorders"], "SYN": [19]},
    100: {"DSM": ["Personality Disorders"], "SYN": [19]},
    101: {"DSM": ["Personality Disorders"], "SYN": [19]},
    102: {"D": [31], "SYN": [28]},
    103: {"D": [31], "note": "הדין הישראלי — חקיקה מקומית; לא מכוסה בספרי הלימוד"},
    104: {"D": [27], "DSM": ["Gender Dysphoria"]},
    105: {"DSM": ["Sexual Dysfunctions", "Paraphilic"], "SYN": [16]},
    106: {"D": [11], "DSM": ["Disruptive"]},
    107: {"D": [28]},
    108: {"D": [26]},
    109: {"D": [26]},
    110: {"D": [30]},
    111: {"D": [25]},
    112: {"DSM": ["Depressive"], "note": "פסיכיאטריה פרינטלית — specifier peripartum ב-DSM; הרחבה בכרטיסיות"},
    113: {"SE": [7]},
    114: {"DSM": ["Neurocognitive"], "SE": [12], "SYN": [3]},
    115: {"D": [6], "SE": [1]},
    116: {"note": NEURO}, 117: {"note": NEURO}, 118: {"note": NEURO},
    119: {"note": NEURO}, 120: {"note": NEURO}, 121: {"note": NEURO},
    122: {"D": [7, 8], "note": "סינדרומים נוירוגנטיים — Dulcan 7-8 + הסילבוס"},
    123: {"D": [7], "note": "נוירוקוטנאיים — Dulcan 7 + הסילבוס"},
    124: {"note": NEURO}, 125: {"note": NEURO}, 126: {"note": NEURO},
    127: {"note": NEURO},
    128: {"D": [22], "SE": [10]},
    129: {"D": [29]},
    130: {"D": [48, 49]},
    131: {"D": [38], "SYN": [23]},
    132: {"D": [43], "SYN": [23]},
    133: {"SYN": [23], "note": "DBT — Synopsis פרק 23; אין פרק ייעודי ב-Dulcan"},
    134: {"D": [42]},
    135: {"D": [44]},
    136: {"D": [45, 46]},
    137: {"D": [47]},
    138: {"D": [40, 41]},
    139: {"D": [38], "SYN": [23]},
    140: {"D": [38], "SYN": [23], "note": THEORY},
    141: {"SYN": [23], "note": THEORY},
    142: {"D": [32]},
    143: {"D": [23]},
    144: {"note": "סקירת אינטגרציה — כל החומר"},
}


def leading_num(title: str):
    m = re.match(r"\s*(\d+)\b", title)
    return int(m.group(1)) if m else None


def num_index(book_key: str) -> dict[int, list]:
    out = {}
    for c in BOOK_MAP[book_key]["chapters"]:
        n = leading_num(c["title"])
        if n is not None and c.get("pages") and c["pages"][0] and c["pages"][0] >= 1:
            out[n] = c["pages"]
    return out


def merge_pages(ranges: list[list[int]]) -> str:
    pages = set()
    for lo, hi in ranges:
        pages.update(range(lo, hi + 1))
    if not pages:
        return ""
    sp = sorted(pages)
    runs, start, prev = [], sp[0], sp[0]
    for p in sp[1:]:
        if p == prev + 1:
            prev = p
        else:
            runs.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = p
    runs.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(runs)


def dsm_pages(prefix: str):
    for c in BOOK_MAP["DSM-5-TR"]["chapters"]:
        if c["title"].startswith(prefix) and c.get("pages") and c["pages"][0] >= 1:
            return c["pages"]
    return None


def ca_titles() -> dict[int, str]:
    out = {}
    for c in BOOK_MAP["Stahl C&A"]["chapters"]:
        n = leading_num(c["title"])
        if n is not None:
            out[n] = c["title"].split(None, 1)[1] if " " in c["title"] else c["title"]
    return out


def main() -> int:
    dul, se, ca = num_index("Dulcan"), num_index("StahlNeuro"), num_index("StahlKids")
    ca_name = ca_titles()

    missing = [d["index"] for d in DAYS if d["index"] not in MAP]
    if missing:
        print(f"ERROR: no reading map for chapters {missing}", file=sys.stderr)
        return 1

    guide = {}
    for d in DAYS:
        idx = d["index"]
        m = MAP[idx]
        entry: dict[str, str] = {}

        if m.get("D"):
            rs = [dul[n] for n in m["D"] if n in dul]
            chs = "+".join(str(n) for n in m["D"])
            if rs:
                entry["Dulcan"] = f"פרק {chs}, עמ' {merge_pages(rs)}"
        if m.get("DSM"):
            rs = [p for p in (dsm_pages(x) for x in m["DSM"]) if p]
            names = " · ".join(m["DSM"])
            if rs:
                entry["DSM-5-TR"] = f"{names}, עמ' {merge_pages(rs)}"
        if m.get("SE"):
            rs = [se[n] for n in m["SE"] if n in se]
            chs = "+".join(str(n) for n in m["SE"])
            if rs:
                entry["StahlNeuro"] = f"פרק {chs}, עמ' {merge_pages(rs)}"
        if m.get("CA"):
            drugs = ", ".join(ca_name.get(n, str(n)) for n in m["CA"])
            rs = [ca[n] for n in m["CA"] if n in ca]
            if rs:
                entry["StahlKids"] = f"{drugs} (עמ' {merge_pages(rs)})"
        if m.get("SYN"):
            entry["Synopsis"] = "פרק " + ", ".join(str(n) for n in m["SYN"])
        if m.get("note"):
            entry["note"] = m["note"]

        guide[str(idx)] = {
            "title_he": d["title_he"],
            "phase": d["phase"],
            "reading": entry,
        }

    OUT.write_text(json.dumps({
        "_note": ("Chapter-level reading pointers derived from book_map.json via a "
                  "hand-built curriculum->book-chapter map. Not the per-subtopic page "
                  "refs that NotebookLM produces per generated chapter — those "
                  "supersede these once a chapter's study guide exists. DSM cited by "
                  "diagnostic class; Synopsis by chapter (unpaginated edition)."),
        "chapters": guide,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    have = sum(1 for g in guide.values() if any(k != "note" for k in g["reading"]))
    note_only = sum(1 for g in guide.values()
                    if list(g["reading"]) == ["note"] or not g["reading"])
    print(f"Wrote {OUT}")
    print(f"  {have}/{len(guide)} chapters have a book page reference")
    print(f"  {note_only} are note-only (no clean textbook home)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
