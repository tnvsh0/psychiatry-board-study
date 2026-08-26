#!/usr/bin/env python3
"""Decide which facts deserve a picture and which only deserve a sentence.

The pipeline had no such step, and chapter 20 shows what that costs. Its
facts.json holds 86 items and every one of them was eligible to become a drawn
element, so the frame filled with ladders for "severity level 1 / 2 / 3" and
props for "Criterion A: persistent deficits in social communication" — while
epilepsy at 8.6%, ADHD at 28% and the 16p11.2 CNV competed for what was left.

That is backwards. Nobody sits a board exam and forgets that autism involves
social communication impairment; that IS autism. A mnemonic is for the
ARBITRARY — a figure, a receptor subtype, a gene, a cutoff, a drug that happens
to be first-line — the things with no logical hook to hang on.

    python scripts/fact_triage.py --day 20
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent

# Strip only the CITATION, never the parenthetical that holds it. facts.json
# writes "8.6% (שכיחות של אפילפסיה קומורבידית, Dulcan p.140)" — the referent
# and the source share one bracket. A regex that ate the whole bracket made
# every key_numbers entry look like a context-free digit, and I nearly deleted
# a set of the most examinable facts in the chapter on the strength of it.
_CITE = re.compile(r"\s*,?\s*(?:DSM(?:-5-TR)?|Dulcan|StahlNeuro|StahlKids|Stahl"
                   r"|Syllabus|Synopsis)[^,)\]]{0,24}?"
                   r"(?:p\.?|pp\.?|עמ')\s*[\d,\s\-–]{1,18}")

# Receptors, transporters, enzymes and loci, captured WITH their subtype. The
# subtype is the whole fact: 5-HT2A is not 5-HT1A and D3 is not D2, and a
# mnemonic that blurs them teaches the wrong thing with full confidence.
RECEPTOR = re.compile(
    r"\b(5-?HT|D|H|M|alpha|α|beta|β|NMDA|AMPA|GABA|mGlu|CB|MT|V|OX)"
    r"[-\s]?(\d[A-Za-z]?)\b|\b(CYP\d[A-Z]\d+)\b|\b(SERT|NET|DAT|VMAT2?)\b"
    r"|\b(\d{1,2}[pq]\d{1,2}(?:\.\d+)?)\b")

# A real quantity: a number that measures something.
QUANTITY = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|mg|ml|mEq|שעות|שעה|ימים|יום|"
                      r"שבועות|שבוע|חודשים|חודש|שנים|שנה|hours?|days?|weeks?|"
                      r"months?|years?)|\d+\s*[:/]\s*\d+|\bפי\s*\d+")

# An ORDINAL dressed as a number. "Severity level 1 / 2 / 3" is a list, not a
# datum — the digits carry no information a reader could get wrong.
ORDINAL = re.compile(r"(רמת?\s*(?:חומרה|תפקוד)|שלב|דרגה|type|level|stage|grade)"
                     r"\s*[:\-]?\s*\d", re.I)

# Latin drug/eponym/scale names: capitalised words that are not sentence starts.
PROPER = re.compile(r"\b[A-Z][a-z]{3,}(?:ine|ol|am|ate|one|ide|pram|zine|"
                    r"pine|done|tide|mab|nib)\b|\b[A-Z]{2,6}-?\d*\b")


def strip_citation(text: str) -> str:
    """Remove the source, keep the meaning, and leave no empty bracket behind."""
    t = _CITE.sub("", text or "")
    return re.sub(r"\s*\(\s*\)", "", t).strip()


def score_fact(text: str) -> tuple[int, list[str]]:
    """How much does this fact need a picture? Higher = draw it."""
    t = _CITE.sub(" ", text or "")
    why, score = [], 0

    r = RECEPTOR.search(t)
    if r:
        score += 5; why.append(f"receptor/locus {r.group(0).strip()}")
    if QUANTITY.search(t):
        score += 4; why.append("a measured quantity")
    if PROPER.search(t):
        score += 2; why.append("a proper name")
    # bare digits that are not ordinals still count, but for less
    if not QUANTITY.search(t) and re.search(r"\d", t) and not ORDINAL.search(t):
        score += 1; why.append("a bare figure")
    if ORDINAL.search(t):
        score -= 3; why.append("an ordinal label, not a datum")
    # a long Hebrew clause with no identifier at all is a definition
    if score <= 0 and len(t) > 30:
        why.append("definitional restatement")
    return score, why


DRAW_THRESHOLD = 3


def receptors_in(text: str) -> list[str]:
    """Every receptor, transporter, enzyme or locus named, WITH its subtype.

    The subtype is not a detail, it is the entire fact: 5-HT2A is not 5-HT1A
    and D3 is not D2, and a picture that blurs them teaches the wrong thing
    with the full authority of a mnemonic — which is worse than teaching
    nothing, because a mnemonic is designed to be hard to unlearn.

    Anything this returns must reach the plate and the legend character for
    character. It is never abbreviated, never rounded to its family, and never
    dropped by the degradation ladder.
    """
    return [m.group(0).strip() for m in RECEPTOR.finditer(text or "")]


def triage(facts: dict) -> dict:
    """Split a facts dict into what to draw and what to leave in the legend."""
    def leaves(o, p=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from leaves(v, f"{p}.{k}" if p else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from leaves(v, f"{p}[{i}]")
        else:
            yield p, str(o)

    draw, tell = [], []
    for path, text in leaves(facts):
        if not text.strip() or text.strip() == "לא צוין":
            continue
        # A figure with no sentence around it teaches nothing. The generic
        # fact schema asks for "key_numbers", and the model fills it with bare
        # values that restate a fact already stated in full elsewhere: "8.6%",
        # "9 חודשים". Drawn, they are a digit on a plate with no referent —
        # the exact failure the TAG system exists to prevent.
        words = len([w for w in re.sub(r"[\d%.,:\-–()]+", " ", text).split()
                     if len(w) > 1])
        if words < 2:
            continue
        s, why = score_fact(text)
        (draw if s >= DRAW_THRESHOLD else tell).append(
            {"path": path, "text": text, "score": s, "why": why})
    # facts.json states the same figure twice — once inside a sentence and once
    # as a bare number in its own field — so "8.6%" and "28%" each arrived as
    # two separate elements competing for two separate carriers. Keep the
    # richest phrasing of each figure and drop the bare restatement.
    seen, deduped = {}, []
    for f in sorted(draw, key=lambda x: (-x["score"], -len(x["text"]))):
        key = tuple(sorted(re.findall(r"\d+(?:[.,]\d+)?", f["text"])))
        if key and key in seen:
            seen[key]["merged"] = seen[key].get("merged", 0) + 1
            continue
        if key:
            seen[key] = f
        deduped.append(f)
    deduped.sort(key=lambda f: -f["score"])
    return {"draw": deduped, "tell": tell}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--facts", help="path to a facts.json (default: the day's)")
    a = ap.parse_args()
    p = Path(a.facts) if a.facts else ROOT / "_generated" / f"day-{a.day:03d}" / "facts.json"
    if not p.exists():
        raise SystemExit(f"no facts at {p}")
    r = triage(json.loads(p.read_text(encoding="utf-8")))
    print(f"DRAW ({len(r['draw'])}) — these carry something forgettable\n")
    for f in r["draw"]:
        print(f"  [{f['score']}] {strip_citation(f['text'])[:92]}")
        print(f"        {', '.join(f['why'])}")
    print(f"\nLEGEND ONLY ({len(r['tell'])}) — true, but nobody forgets them\n")
    for f in r["tell"][:14]:
        print(f"  [{f['score']}] {strip_citation(f['text'])[:92]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
