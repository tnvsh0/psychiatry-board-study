#!/usr/bin/env python3
"""Smoke test: 3 distinct ADHD Sketchy images with the new aspect-scoped
architecture (facts.json slice + meta-symbols + number-pegs). Runs locally
via Gemini (no NotebookLM needed) using a hand-built ADHD facts dict."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daily_study import (  # noqa: E402
    load_schedule, load_symbols, build_caption_legend,
    prompt_sketchy_scene, facts_for_aspect,
)
from gemini_image import generate_image_with_caption, propose_anchor_theme  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "_test_outputs"

# Realistic ADHD facts (what extract_facts would return from the textbooks)
ADHD_FACTS = {
    "criteria": {
        "clusters": [
            {"name": "Inattention", "items": [
                "fails to give close attention to details / careless mistakes",
                "difficulty sustaining attention",
                "does not seem to listen when spoken to directly",
                "does not follow through on instructions / fails to finish",
                "difficulty organizing tasks",
                "avoids tasks requiring sustained mental effort",
                "loses things necessary for tasks",
                "easily distracted by extraneous stimuli",
                "forgetful in daily activities",
            ]},
            {"name": "Hyperactivity/Impulsivity", "items": [
                "fidgets / squirms",
                "leaves seat when remaining seated expected",
                "runs or climbs inappropriately (or restlessness)",
                "unable to play quietly",
                "'on the go' / driven by a motor",
                "talks excessively",
                "blurts out answers before question completed",
                "difficulty waiting turn",
                "interrupts or intrudes on others",
            ]},
        ],
        "thresholds": ">=6/9 symptoms in a cluster (>=5 if age 17+); several symptoms before age 12; present in >=2 settings; >=6 months",
        "subtypes": ["Predominantly Inattentive", "Predominantly Hyperactive-Impulsive", "Combined"],
    },
    "epidemiology": {
        "prevalence": "~5% children, ~2.5% adults",
        "sex_ratio": "~2:1 male:female (clinic up to 3:1)",
        "onset_age_typical": "symptoms by early childhood; impairment often by school entry",
        "heritability": "~74% — one of the most heritable psychiatric disorders",
        "comorbidities": ["ODD ~50%", "anxiety", "specific learning disorder", "depression"],
        "prognosis": "~60% persist into adulthood",
    },
    "treatment": {
        "first_line": ["Stimulants: Methylphenidate, Amphetamine/Lisdexamfetamine (most effective)"],
        "second_line": ["Atomoxetine (NRI)", "alpha-2 agonists: Guanfacine, Clonidine"],
        "key_side_effects": ["appetite suppression", "insomnia", "growth delay", "increased HR/BP"],
        "monitoring": "baseline + ongoing height, weight, HR, BP",
    },
    "high_yield": [
        "onset before age 12 is required (DSM-5-TR changed from 7)",
        "must impair in >=2 settings",
        "stimulants are first-line and most effective",
    ],
}

ASPECTS = ["dsm_criteria", "epidemiology", "treatment_first_line"]


def main():
    symbols = load_symbols()
    sched = load_schedule()
    day = next(d for d in sched["days"] if d["index"] == 16)
    caption_legend = build_caption_legend(day, symbols)
    phase = day.get("phase")

    anchor = propose_anchor_theme(day.get("title_he", "ADHD"), ASPECTS)
    world = anchor.get("world") if anchor else None
    zones = anchor.get("zones", {}) if anchor else {}
    print(f"Anchor world: {world}")
    for a, z in zones.items():
        print(f"  {a} -> {z}")

    for aspect in ASPECTS:
        af = facts_for_aspect(aspect, ADHD_FACTS)
        prompt = prompt_sketchy_scene(day, symbols, aspect, af,
                                      anchor_world=world, anchor_zone=zones.get(aspect))
        png = OUT / f"smoke_adhd_v5_{aspect}.png"
        txt = OUT / f"smoke_adhd_v5_{aspect}.txt"
        print(f"[{aspect}] prompt {len(prompt)} chars -> generating...")
        ok, cap = generate_image_with_caption(
            prompt=prompt, output_path=png, caption_path=txt,
            symbol_legend=caption_legend, phase=phase,
        )
        print(f"  {'OK' if ok else 'FAIL'} {png.name}; caption {len(cap)} chars")


if __name__ == "__main__":
    main()
