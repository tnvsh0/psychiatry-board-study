#!/usr/bin/env python3
"""Render an exam-style practice-question booklet to a Hebrew RTL PDF.

Reads a per-week question file (same schema as weekly_questions.json:
{"weeks":[{"week","topic","items":[{"type","stem","sub","points","source"}]}]})
and produces a two-part booklet: the questions (with room to answer), then a
model-answer key. Built with PyMuPDF's Story engine, which does its own bidi and
flows RTL Hebrew across pages; the Hebrew glyphs come from Arial via an archive.

Usage:
    python scripts/build_question_booklet.py [in.json] [out.pdf]
Defaults: schedule/question_bank.json -> schedule/question_booklet.pdf
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import fitz

REPO = Path(__file__).resolve().parents[1]
IN = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "schedule" / "question_bank.json"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "schedule" / "question_booklet.pdf"
FONT_DIR = r"C:\Windows\Fonts"
EXAM = "25 בנובמבר 2026"

CSS = """
@font-face { font-family: heb; src: url(arial.ttf); }
@font-face { font-family: heb; src: url(arialbd.ttf); font-weight: bold; }
* { font-family: heb; }
body { font-size: 11px; color: #1a1a1a; }
h1 { font-size: 26px; color: #2F5496; text-align: center; margin: 0 0 6px 0; }
.sub { text-align: center; color: #555; font-size: 13px; margin-bottom: 2px; }
h2 { font-size: 15px; color: #ffffff; background: #2F5496; padding: 5px 10px;
     margin: 16px 0 8px 0; }
.item { margin: 0 0 11px 0; padding: 8px 11px; border: 1px solid #cbd5e1;
        border-right: 4px solid #5B7B9A; background: #f7f9fc; }
.badge { font-size: 9px; color: #ffffff; background: #5B7B9A; padding: 1px 7px;
         border-radius: 8px; }
.badge.disc { background: #8a6d3b; }
.stem { font-weight: bold; margin: 5px 0; }
.q { margin: 2px 8px 2px 0; text-align: right; }
.ans { font-size: 9px; color: #888; margin-top: 5px; }
.key { margin: 0 0 10px 0; padding: 6px 11px; border-right: 3px solid #1F6B2E;
       background: #f2f8f4; }
.pt { color: #1F4E3D; font-size: 10.5px; margin: 2px 8px 2px 0; text-align: right; }
.src { color: #888; font-size: 9px; margin-top: 3px; }
.note { color: #555; font-size: 11px; text-align: center; margin: 10px 40px; }
"""


def esc(s: str) -> str:
    return html.escape(str(s))


def build_html(data: dict) -> str:
    weeks = data["weeks"]
    n_items = sum(len(w["items"]) for w in weeks)
    P = ['<html><body dir="rtl">']

    # cover
    P.append('<h1>מחברת שאלות תרגול — שלב ב\'</h1>')
    P.append('<div class="sub">פסיכיאטריה של הילד והמתבגר</div>')
    P.append(f'<div class="sub">{n_items} שאלות בפורמט הבחינה · לפי שבועות הלימוד · '
             f'מועד הבחינה {esc(EXAM)}</div>')
    P.append('<div class="note">כל שאלה היא בפורמט שלב ב\': וינייטה קלינית או שאלת '
             '"דון/תאר/הסבר" עם תת-שאלות פתוחות. ענה/י בעל-פה או בכתב, ואז בדוק/י '
             'מול מפתח נקודות המפתח שבסוף המחברת.</div>')

    # questions
    for w in weeks:
        P.append(f'<h2>שבוע {w["week"]} — {esc(w.get("topic",""))}</h2>')
        for i, it in enumerate(w["items"], start=1):
            cls = "disc" if it["type"] == "discussion" else ""
            label = "דיון" if it["type"] == "discussion" else "וינייטה"
            P.append('<div class="item">')
            P.append(f'<span class="badge {cls}">{label}</span> '
                     f'<b>שבוע {w["week"]} · שאלה {i}</b>')
            P.append(f'<div class="stem">{esc(it["stem"])}</div>')
            for s in it.get("sub", []):
                P.append(f'<div class="q">• {esc(s)}</div>')
            P.append('<div class="ans">_______________________________________________'
                     '_______________________________</div>')
            P.append("</div>")

    # answer key
    P.append('<h2>מפתח נקודות לתשובה</h2>')
    for w in weeks:
        P.append(f'<h2>שבוע {w["week"]} — נקודות מפתח</h2>')
        for i, it in enumerate(w["items"], start=1):
            P.append('<div class="key">')
            P.append(f'<b>שאלה {i}:</b> {esc(it["stem"])}')
            for p in it.get("points", []):
                P.append(f'<div class="pt">◾ {esc(p)}</div>')
            if it.get("source"):
                P.append(f'<div class="src">מקור: {esc(it["source"])}</div>')
            P.append("</div>")

    P.append("</body></html>")
    return "\n".join(P)


def main() -> int:
    data = json.loads(IN.read_text(encoding="utf-8"))
    story = fitz.Story(html=build_html(data), user_css=CSS, archive=fitz.Archive(FONT_DIR))

    writer = fitz.DocumentWriter(str(OUT))
    media = fitz.paper_rect("a4")
    frame = media + (52, 46, -52, -50)
    more = 1
    while more:
        dev = writer.begin_page(media)
        more, _ = story.place(frame)
        story.draw(dev)
        writer.end_page()
    writer.close()

    # page numbers
    doc = fitz.open(str(OUT))
    for i, page in enumerate(doc, start=1):
        page.insert_text(fitz.Point(media.width / 2 - 12, media.height - 30),
                         f"{i} / {doc.page_count}", fontsize=8, color=(0.5, 0.5, 0.5))
    doc.saveIncr()
    doc.close()

    print(f"Wrote {OUT}  ({sum(len(w['items']) for w in data['weeks'])} items, "
          f"{fitz.open(str(OUT)).page_count} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
