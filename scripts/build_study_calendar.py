#!/usr/bin/env python3
"""Build a WEEKLY study calendar from schedule.json, back-timed to the exam.

The board exam is 2026-11-25. Counting back in whole Monday-start weeks there
are 17 of them; this splits into 14 weeks of new material and 3 of consolidated
review, then a light final stretch in the exam week itself.

New material is packed into the 14 learning weeks by BALANCED weighted load, not
by equal chapter count: a "high" density chapter is worth more than a "low" one,
so weeks end up similar in real effort even when their chapter counts differ.
Chapters keep their curriculum order (which is the teaching order), and week
cuts snap to a phase boundary when one is near, so a week is a coherent block
rather than a phase split mid-stream.

Each learning week reserves its last study day for reviewing that week plus
whatever spaced-repetition cards are due. The three review weeks re-pass the
material in halves and then run practice exams.

Writes schedule/study_calendar.json (machine-readable, for the app) and
schedule/study_calendar.md (human-readable). Re-run after editing schedule.json.

Usage:
    python scripts/build_study_calendar.py
    python scripts/build_study_calendar.py --exam 2026-11-25 --learning-weeks 14 --study-days 5
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = REPO_ROOT / "schedule" / "schedule.json"
OUT_JSON = REPO_ROOT / "schedule" / "study_calendar.json"
OUT_MD = REPO_ROOT / "schedule" / "study_calendar.md"

DENSITY_WEIGHT = {"high": 1.4, "medium": 1.0, "low": 0.7}
HEB_MONTHS = {
    1: "ינו", 2: "פבר", 3: "מרץ", 4: "אפר", 5: "מאי", 6: "יונ",
    7: "יול", 8: "אוג", 9: "ספט", 10: "אוק", 11: "נוב", 12: "דצמ",
}


def fmt(d: date) -> str:
    return f"{d.day} {HEB_MONTHS[d.month]}"


def first_monday_on_or_after(d: date) -> date:
    return d + timedelta(days=(7 - d.weekday()) % 7)


def weight(chapter: dict) -> float:
    return DENSITY_WEIGHT.get(chapter.get("estimated_density", "medium"), 1.0)


def partition(days: list[dict], n_weeks: int) -> list[list[dict]]:
    """Split chapters (in order) into n_weeks contiguous blocks of ~equal
    weighted load, snapping a cut to a phase boundary when one is within reach."""
    total = sum(weight(c) for c in days)
    target = total / n_weeks

    weeks: list[list[dict]] = []
    i, n = 0, len(days)
    for w in range(n_weeks):
        remaining_weeks = n_weeks - w
        remaining_chapters = n - i
        # Never strand more chapters than can be covered one-per-remaining-week,
        # and always leave at least one chapter for each later week.
        max_take = remaining_chapters - (remaining_weeks - 1)
        if remaining_weeks == 1:
            weeks.append(days[i:])
            break

        acc, cut = 0.0, i
        while cut < n and acc < target and (cut - i) < max_take:
            acc += weight(days[cut])
            cut += 1
            # If a phase boundary sits within one chapter of the target, cut
            # there so weeks don't straddle two phases needlessly.
            if cut < n and acc >= target * 0.8:
                if days[cut]["phase"] != days[cut - 1]["phase"]:
                    break
        cut = max(cut, i + 1)
        weeks.append(days[i:cut])
        i = cut
    return weeks


def phase_names(chunk: list[dict], all_days: list[dict]) -> str:
    seen, names = set(), []
    for c in chunk:
        p = c["phase"]
        if p in seen:
            continue
        seen.add(p)
        nm = next((x.get("phase_name") for x in all_days
                   if x["phase"] == p and x.get("phase_name")), f"Phase {p}")
        names.append(nm)
    return " · ".join(names)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exam", default="2026-11-25")
    ap.add_argument("--today", default="2026-07-22",
                    help="anchor date; the plan starts the first Monday on/after it")
    ap.add_argument("--learning-weeks", type=int, default=14)
    ap.add_argument("--review-weeks", type=int, default=3)
    ap.add_argument("--study-days", type=int, default=5)
    args = ap.parse_args()

    exam = date.fromisoformat(args.exam)
    days = json.loads(SCHEDULE.read_text(encoding="utf-8"))["days"]
    total_weeks = args.learning_weeks + args.review_weeks
    # Start next Monday and run forward. Back-timing from the exam would strand
    # an idle week up front; starting now uses it and lands the last review week
    # exactly against the exam week, whose Mon–exam days become the light taper.
    start = first_monday_on_or_after(date.fromisoformat(args.today))
    exam_week_monday = exam - timedelta(days=exam.weekday())
    slack_days = (exam_week_monday - (start + timedelta(days=7 * total_weeks))).days
    if slack_days < 0:
        print(f"WARNING: plan overruns the exam by {-slack_days} day(s). "
              f"Reduce weeks or start earlier.", file=sys.stderr)

    chunks = partition(days, args.learning_weeks)

    weeks_out = []
    for w, chunk in enumerate(chunks):
        wk_start = start + timedelta(days=7 * w)
        wk_end = wk_start + timedelta(days=6)
        idxs = [c["index"] for c in chunk]
        weeks_out.append({
            "week": w + 1,
            "kind": "learning",
            "start": wk_start.isoformat(),
            "end": wk_end.isoformat(),
            "chapters": idxs,
            "chapter_titles_he": [c["title_he"] for c in chunk],
            "phases": phase_names(chunk, days),
            "weighted_load": round(sum(weight(c) for c in chunk), 1),
            "note": f"{args.study_days-1} ימי חומר חדש + יום חזרה שבועית",
        })

    # Review weeks: split the whole curriculum in half by phase for two passes,
    # then a practice-exam week.
    mid = days[len(days) // 2]["phase"]
    first_half = f"פאזות 1–{mid}"
    second_half = f"פאזות {mid+1}–10"
    review_plan = [
        (f"חזרה מרוכזת — חצי ראשון ({first_half})",
         "מעבר על נקודות ה-high-yield, כל הכרטיסיות שבתור, וסימון נקודות חולשה."),
        (f"חזרה מרוכזת — חצי שני ({second_half})",
         "מעבר על נקודות ה-high-yield, כל הכרטיסיות שבתור, וסימון נקודות חולשה."),
        ("מבחני תרגול + נקודות חולשה",
         "וינייטות ומבחני תרגול; חזרה ממוקדת רק לנושאים שסומנו כחלשים בשבועיים הקודמים."),
    ]
    for j, (title, note) in enumerate(review_plan[:args.review_weeks]):
        wk_start = start + timedelta(days=7 * (args.learning_weeks + j))
        wk_end = wk_start + timedelta(days=6)
        weeks_out.append({
            "week": args.learning_weeks + j + 1,
            "kind": "review",
            "start": wk_start.isoformat(),
            "end": wk_end.isoformat(),
            "chapters": [],
            "title_he": title,
            "note": note,
        })

    # The exam sits inside the week after the last review week.
    final_start = exam - timedelta(days=exam.weekday())
    calendar = {
        "exam_date": exam.isoformat(),
        "generated_from": "schedule.json",
        "start_date": start.isoformat(),
        "total_weeks": total_weeks,
        "learning_weeks": args.learning_weeks,
        "review_weeks": args.review_weeks,
        "study_days_per_week": args.study_days,
        "chapters_total": len(days),
        "final_stretch": {
            "start": final_start.isoformat(),
            "exam": exam.isoformat(),
            "note": "מנוחה וחזרה קלה בלבד — לא ללמוד חומר חדש. סקירת high-yield וכרטיסיות אדומות.",
        },
        "weeks": weeks_out,
    }
    OUT_JSON.write_text(json.dumps(calendar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- Markdown ----
    L = []
    L.append("# לוח לימוד שבועי — בחינת שלב ב'")
    L.append("")
    L.append(f"**מועד הבחינה:** {exam.day} בנובמבר 2026 (יום {['שני','שלישי','רביעי','חמישי','שישי','שבת','ראשון'][exam.weekday()]})")
    L.append(f"**תחילת הלימוד:** {fmt(start)} · **{total_weeks} שבועות** = {args.learning_weeks} למידה + {args.review_weeks} חזרה · **{args.study_days} ימי למידה בשבוע**")
    L.append(f"**סה\"כ {len(days)} פרקים.** כל שבוע מסתיים ביום חזרה על חומר השבוע + כרטיסיות בתור.")
    L.append("")
    L.append("## שבועות למידה")
    L.append("")
    L.append("| שבוע | תאריכים | פרקים | # | עומס | תחומים |")
    L.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    for wk in weeks_out:
        if wk["kind"] != "learning":
            continue
        s = date.fromisoformat(wk["start"]); e = date.fromisoformat(wk["end"])
        rng = f"{wk['chapters'][0]}–{wk['chapters'][-1]}"
        L.append(f"| **{wk['week']}** | {fmt(s)} – {fmt(e)} | {rng} | {len(wk['chapters'])} | {wk['weighted_load']} | {wk['phases']} |")
    L.append("")
    L.append("## שבועות חזרה")
    L.append("")
    L.append("| שבוע | תאריכים | מוקד |")
    L.append("| :--- | :--- | :--- |")
    for wk in weeks_out:
        if wk["kind"] != "review":
            continue
        s = date.fromisoformat(wk["start"]); e = date.fromisoformat(wk["end"])
        L.append(f"| **{wk['week']}** | {fmt(s)} – {fmt(e)} | {wk['title_he']} — {wk['note']} |")
    fs = calendar["final_stretch"]
    L.append(f"| **בחינה** | {fmt(date.fromisoformat(fs['start']))} – {fmt(exam)} | {fs['note']} |")
    L.append("")
    L.append("## פירוט פרקים לפי שבוע")
    L.append("")
    for wk in weeks_out:
        if wk["kind"] != "learning":
            continue
        s = date.fromisoformat(wk["start"]); e = date.fromisoformat(wk["end"])
        L.append(f"### שבוע {wk['week']} · {fmt(s)}–{fmt(e)} · {wk['phases']}")
        for idx, title in zip(wk["chapters"], wk["chapter_titles_he"]):
            L.append(f"- {idx}. {title}")
        L.append("")
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")
    print()
    print(f"{total_weeks} weeks from {start} to exam {exam}")
    for wk in weeks_out:
        if wk["kind"] == "learning":
            print(f"  W{wk['week']:<2} {wk['start']}  ch {wk['chapters'][0]:>3}-{wk['chapters'][-1]:<3} "
                  f"({len(wk['chapters'])} ch, load {wk['weighted_load']})  {wk['phases'][:42]}")
        else:
            print(f"  W{wk['week']:<2} {wk['start']}  REVIEW: {wk['title_he']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
