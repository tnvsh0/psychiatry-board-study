#!/usr/bin/env python3
"""Gemini QC judge for a generated chapter.

Adapted from the book-podcasts project, with the change this project forces:
there, one episode is built from one chapter slice, so the judge can be handed
the exact source and told "anything not in here is a discrepancy". Here a
chapter is a TOPIC surveyed across six whole textbooks, and handing the judge
600MB of PDF is not possible. So the bar is different and deliberately weaker:

  - HALLUCINATION of specifics is still catchable without the source. A drug
    dose, a percentage, a page citation or a DSM threshold that is wrong is
    wrong against the field, and a judge that knows psychiatry catches it.
  - COVERAGE is checkable against the chapter's declared aspects.
  - What we CANNOT check is "is this sentence supported by our six books",
    because the judge cannot read them. That limitation is stated in the
    verdict rather than pretended away.

Page citations get their own pass because they are this pipeline's known
failure mode: a day-16 study guide once cited "Stahl Pediatric pp. 600-612" in
a 525-page book, and nothing noticed.

Usage:
    python scripts/qc_review.py --day 29
    python scripts/qc_review.py --day 29 --audio _generated/day-029/audio.mp3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gemini_image import _gc  # noqa: E402  shared transient-retry wrapper

ROOT = Path(__file__).resolve().parent.parent
QC_MODEL = os.environ.get("QC_MODEL", "gemini-2.5-flash")

def _book_pages():
    """Last PRINTED page of each book, measured, never guessed.

    These were hardcoded from general knowledge and two were wrong. Stahl
    Essential was recorded as 550 pages against a real last page of 623, so the
    check would have reported a correct citation to page 600 as invented - the
    exact failure this file exists to prevent, pointed the wrong way.

    The numbers are PRINTED page numbers, the ones a reader sees on the page.
    That is not a preference: NotebookLM indexes a flat text stream with no
    page structure, so the only page numbers it can ever see are the folios
    printed in the running headers, and a PDF viewer's page index is not
    available to it even in principle. Both sides of this comparison therefore
    speak the same numbering. See scripts/measure_folios.py.
    """
    try:
        books = json.loads((ROOT / "schedule" / "drive_books.json")
                           .read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for label, b in books.items():
        n = b.get("max_printed_page")
        if n:
            out[label] = n
            out[label.split(" &")[0].split(" (")[0]] = n
    return out


BOOK_PAGES = _book_pages()

JUDGE_SYSTEM = (
    "You are a meticulous medical-education QC reviewer for a Hebrew psychiatry "
    "board-exam course. You are given a chapter's generated study material. "
    "Judge it as an expert psychiatrist would. Be strict about factual accuracy: "
    "flag any claim, number, drug dose, threshold or conclusion that is wrong or "
    "implausible for the field. Flag anything that reads as invented. You are "
    "NOT given the course's source textbooks, so do not claim a statement is "
    "unsupported merely because you cannot see the source — judge whether it is "
    "WRONG. Reply with ONLY a JSON object."
)

VERDICT_SHAPE = (
    'Reply with ONLY this JSON:\n'
    '{\n'
    '  "verdict": "ok" | "review" | "problem",\n'
    '  "accuracy": 0-100,\n'
    '  "coverage": 0-100,\n'
    '  "errors": [{"claim": "...", "why": "...", "severity": "high"|"medium"|"low"}],\n'
    '  "missing_aspects": ["..."],\n'
    '  "notes": "one or two sentences"\n'
    '}\n'
    'verdict "problem" means something is factually wrong and must be regenerated.'
)


def check_page_citations(text: str) -> list[dict]:
    """Flag a cited page that cannot exist. Arithmetic, not opinion.

    Deliberately conservative about WHICH book. Chapters cite "Stahl p. 761"
    without saying which Stahl, and the notebook holds two: the Prescriber's
    Guide for Children and Adolescents (502 printed pages) and the
    Neuroscientific Basis (623). Resolving a bare "Stahl" to the smaller one
    flagged 117 of chapter 2's 134 citations. A page is only impossible if it
    exceeds EVERY book the name could mean, so an ambiguous name is checked
    against the largest candidate.
    """
    out = []
    pat = re.compile(
        r"(DSM(?:-5(?:-TR)?)?|Dulcan|Stahl(?:'s)?(?:\s+(?:Essential|Pediatric|C&A))?"
        r"|Synopsis|Kaplan|Syllabus)"
        r"[^0-9\n]{0,24}?(\d{2,4})(?:\s*[-–]\s*(\d{2,4}))?", re.I)
    for m in pat.finditer(text or ""):
        name = re.sub(r"\s+", " ", m.group(1).strip()).lower().replace("'s", "")
        cands = [v for k, v in BOOK_PAGES.items()
                 if k.lower() == name or k.lower().startswith(name)
                 or name.startswith(k.lower().split()[0])]
        if not cands:
            continue
        limit = max(cands)              # ambiguous name -> the most forgiving book
        for g in (m.group(2), m.group(3)):
            if g and int(g) > limit:
                out.append({"claim": m.group(0).strip(),
                            "why": f"no book called {m.group(1).strip()} here runs "
                                   f"past page {limit}",
                            "severity": "high"})
    return out


def judge_chapter(day_dir: Path, day: dict) -> dict | None:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("[!] google-genai not installed")
        return None
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("[!] GEMINI_API_KEY not set")
        return None
    client = genai.Client(api_key=key)

    parts, listed = [], []
    for name in ("study_guide.md", "facts.json", "quiz.json", "flashcards.json"):
        f = day_dir / name
        if f.exists():
            parts.append(f"\n\n===== {name} =====\n"
                         + f.read_text(encoding="utf-8", errors="replace")[:60000])
            listed.append(name)
    if not parts:
        print(f"[!] nothing to judge in {day_dir}")
        return None

    prompt = (
        f"CHAPTER: {day.get('title_he')} ({day.get('title_en')})\n"
        f"ASPECTS THIS CHAPTER MUST COVER: {', '.join(day.get('aspects', []))}\n"
        f"\nMATERIAL ({', '.join(listed)}):" + "".join(parts)
        + "\n\n" + VERDICT_SHAPE
    )
    try:
        resp = _gc(client, 
            model=QC_MODEL, contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=JUDGE_SYSTEM,
                response_mime_type="application/json",
                # thinking tokens count toward max_output_tokens on 2.5
                thinking_config=types.ThinkingConfig(thinking_budget=4096),
                max_output_tokens=8192, temperature=0.2,
            ),
        )
        txt = (resp.text or "").strip()
        a, b = txt.find("{"), txt.rfind("}")
        if a == -1:
            print("[!] judge returned no JSON")
            return None
        v = json.loads(txt[a:b + 1])
    except Exception as e:
        print(f"[!] judge failed: {e}")
        return None

    # arithmetic page check runs regardless of what the judge said
    page_errs = check_page_citations("".join(parts))
    if page_errs:
        v.setdefault("errors", []).extend(page_errs)
        v["verdict"] = "problem"
    v["source_limitation"] = (
        "judged without the six source textbooks; catches wrong facts and "
        "impossible citations, not unsupported-but-plausible ones")
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", type=int, required=True)
    a = ap.parse_args()
    sched = json.loads((ROOT / "schedule" / "schedule.json").read_text(encoding="utf-8"))
    day = next((d for d in sched["days"] if d["index"] == a.day), None)
    if not day:
        raise SystemExit(f"day {a.day} not in schedule")
    day_dir = ROOT / "_generated" / f"day-{a.day:03d}"
    v = judge_chapter(day_dir, day)
    if not v:
        return 1
    (day_dir / "qc.json").write_text(json.dumps(v, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps(v, ensure_ascii=False, indent=1)[:1200])
    return 0 if v.get("verdict") != "problem" else 2


if __name__ == "__main__":
    raise SystemExit(main())
