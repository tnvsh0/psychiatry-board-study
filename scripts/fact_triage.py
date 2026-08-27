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

# IDENTIFIERS: anything whose letters and digits together ARE the name. A
# whitelist of receptor families was the first attempt and it is the wrong
# shape — it can only ever catch what I thought of, and the one it misses is
# the one that gets blurred. The rule is general instead: a token that mixes
# letters with digits, or carries a Greek letter, or is an all-caps acronym, is
# an identifier and is atomic.
#
# The subtype is not a detail, it is the entire fact. 5-HT2A is not 5-HT1A, D3
# is not D2, CYP2D6 is not CYP3A4, 16p11.2 is not 16p11 — and a picture that
# blurs any of them teaches the wrong thing with a mnemonic's authority, which
# is worse than teaching nothing because a mnemonic is built to resist
# unlearning.
IDENTIFIER = re.compile(
    r"\d{1,2}[pq]\d{1,2}(?:\.\d+)?"                     # 16p11.2, a locus
    r"|\d+-[A-Za-z]{1,6}\d*[A-Za-z]*\d*"                 # 5-HT2A, 5-HT1A
    r"|[A-Za-z]{2,12}\d+[A-Za-z]*\d*(?:\.\d+)?"         # TAAR1, SHANK3, CYP2D6, Cav1.2
    r"|(?:alpha|beta|gamma|delta|sigma|kappa|mu"
    r"|α|β|γ|δ|σ|κ|μ)-?\d[A-Za-z]?"   # alpha-2, sigma-1
    # CASE-SENSITIVE, despite the re.I on the whole pattern. These three
    # alternatives are the ones whose meaning IS the capitalisation, and under
    # re.I "\b[A-Z]{3,6}\b" matches every ordinary Latin word of three to six
    # letters — the triage reported "receptor/locus Major" and would have
    # marked half the English in the corpus as an identifier that must reach
    # the plate character for character.
    r"|(?-i:[A-Za-z]{2,8}-[A-Z0-9]\b)"                    # MAO-B, GABA-A, ADOS-2
    r"|(?-i:\b[A-Z]\d+[A-Za-z]?\b)"                    # D2, D3, H1
    r"|(?-i:\b[A-Z]{3,6}\b)",                          # SERT, NMDA, COMT
    re.I)

RECEPTOR = IDENTIFIER      # the old name, kept for callers

# A real quantity: a number that measures something.
QUANTITY = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|mg|ml|mEq|שעות|שעה|ימים|יום|"
    r"שבועות|שבוע|חודשים|חודש|שנים|שנה|hours?|days?|weeks?|months?|years?)"
    r"|\d+\s*[:/]\s*\d+|\bפי\s*\d+"
    # A DIAGNOSTIC THRESHOLD is the most forgettable number in a chapter and
    # the most examinable: "3 of 3 in A and at least 2 of 4 in B", "functional
    # language by age 5", "approved from age 6". These first scored as
    # unremarkable and went to the legend while bare percentages were drawn
    # twice over — the exact inversion this file exists to stop.
    r"|\d+\s*(?:מתוך|מ־|מ-)\s*\d+"
    r"|\b\d+\s*(?:of|/)\s*\d+\b"
    r"|(?:לפחות|לכל היותר|מעל|מתחת ל)\s*\d+"
    r"|(?:עד|מ)\s*גיל\s*\d+"
    r"|\bגיל\s*\d+")

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


# "Criterion A", "B2", "C" — the letter is not decoration, it is how the
# criterion is asked about and how a resident is expected to recite it.
CRITERION = re.compile(r"קריטריון\s*([A-Z])|criterion\s*([A-Z])", re.I)


def score_fact(text: str, path: str = "") -> tuple[int, list[str]]:
    """How much does this fact need a picture? Higher = draw it."""
    t = _CITE.sub(" ", text or "")
    why, score = [], 0

    # A diagnostic criterion is exactly what has to be memorised, letter and
    # all — which is why criteria get their own plate rather than competing
    # for space on the main one. The first version of this scored them as
    # definitional filler and sent them to the legend, which was wrong.
    if path.startswith("criteria") or CRITERION.search(t):
        score += 6
        why.append("a diagnostic criterion")

    r = RECEPTOR.search(t)
    if r:
        score += 5; why.append(f"receptor/locus {r.group(0).strip()}")
    if QUANTITY.search(t):
        score += 4; why.append("a measured quantity")
    if PROPER.search(t):
        # A drug name IS the archetypal Sketchy content — a barbershop for
        # barbiturates, Ben's Diner for benzodiazepines. Scoring it 2 put it
        # under the threshold, so "Methylphenidate, Atomoxetine, Guanfacine"
        # went to the legend as plain text while a severity ladder was drawn.
        score += 4; why.append("a proper name")
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
        s, why = score_fact(text, path)
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

