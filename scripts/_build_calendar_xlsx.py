#!/usr/bin/env python3
"""Render schedule/study_calendar.json into a formatted, RTL .xlsx tracker.

One-off deliverable generator (needs openpyxl). Two sheets: a weekly overview
and a per-chapter checklist with live progress formulas. Re-run after
build_study_calendar.py if the plan changes.

Usage:  python scripts/_build_calendar_xlsx.py [out.xlsx]
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

REPO = Path(__file__).resolve().parents[1]
CAL = json.loads((REPO / "schedule" / "study_calendar.json").read_text(encoding="utf-8"))
DAYS = {d["index"]: d for d in json.loads(
    (REPO / "schedule" / "schedule.json").read_text(encoding="utf-8"))["days"]}
_rg_path = REPO / "schedule" / "reading_guide.json"
READING = (json.loads(_rg_path.read_text(encoding="utf-8"))["chapters"]
           if _rg_path.exists() else {})
_wq_path = REPO / "schedule" / "weekly_questions.json"
QUESTIONS = (json.loads(_wq_path.read_text(encoding="utf-8"))["weeks"]
             if _wq_path.exists() else [])
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "schedule" / "study_calendar.xlsx"

FONT = "Arial"
NAVY = "2F5496"
LEARN = "EAF1FB"   # light blue
REVIEW = "FCE8D6"  # light orange
EXAM = "FADBD8"    # light red
HEB_MON = {1: "ינו", 2: "פבר", 3: "מרץ", 4: "אפר", 5: "מאי", 6: "יונ",
           7: "יול", 8: "אוג", 9: "ספט", 10: "אוק", 11: "נוב", 12: "דצמ"}
DENS_HE = {"high": "גבוהה", "medium": "בינונית", "low": "נמוכה"}
WD_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
HFONT = Font(name=FONT, bold=True, color="FFFFFF", size=11)
CFONT = Font(name=FONT, size=11)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center", wrap_text=True)


def fmt(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {HEB_MON[d.month]}"


def phase_name(p: int) -> str:
    return next((d.get("phase_name") for d in DAYS.values()
                 if d["phase"] == p and d.get("phase_name")), f"Phase {p}")


def reading_cell(idx: int) -> str:
    """Compose a compact multi-line reading pointer for one chapter."""
    entry = READING.get(str(idx), {}).get("reading", {})
    lines = [f"{book}: {val}" for book, val in entry.items() if book != "note"]
    if entry.get("note"):
        lines.append("ℹ " + entry["note"])
    return "\n".join(lines)


def style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = HFONT
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = CENTER
        cell.border = BORDER


def build_weekly(ws):
    ws.sheet_view.rightToLeft = True
    headers = ["שבוע", "סוג", "מתאריך", "עד תאריך", "פרקים",
               "מס' פרקים", "עומס", "תחומים / מוקד", "הושלם"]
    ws.append(headers)
    style_header(ws, 1, len(headers))

    for wk in CAL["weeks"]:
        if wk["kind"] == "learning":
            chs = wk["chapters"]
            row = [wk["week"], "למידה", fmt(wk["start"]), fmt(wk["end"]),
                   f"{chs[0]}–{chs[-1]}", len(chs), wk["weighted_load"],
                   wk["phases"], ""]
            fill = LEARN
        else:
            row = [wk["week"], "חזרה", fmt(wk["start"]), fmt(wk["end"]),
                   "—", "—", "—",
                   f'{wk["title_he"]} — {wk["note"]}', ""]
            fill = REVIEW
        ws.append(row)
        r = ws.max_row
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = CFONT
            cell.border = BORDER
            cell.fill = PatternFill("solid", fgColor=fill)
            cell.alignment = RIGHT if c == 8 else CENTER

    # Exam-week taper row
    fs = CAL["final_stretch"]
    ws.append(["בחינה", "—", fmt(fs["start"]), fmt(fs["exam"]),
               "—", "—", "—", f'שבוע הבחינה — {fs["note"]}', ""])
    r = ws.max_row
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=r, column=c)
        cell.font = Font(name=FONT, size=11, bold=True)
        cell.border = BORDER
        cell.fill = PatternFill("solid", fgColor=EXAM)
        cell.alignment = RIGHT if c == 8 else CENTER

    widths = [7, 8, 10, 10, 10, 10, 8, 60, 9]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    dv = DataValidation(type="list", formula1='"✓,"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"I2:I{ws.max_row}")


def build_chapters(ws):
    ws.sheet_view.rightToLeft = True

    headers = ["פרק", "נושא", "פאזה", "צפיפות", "שבוע", "מדריך קריאה (עמודים בספרים)", "הושלם"]
    done_col = get_column_letter(len(headers))   # 'G' — the ✓ column
    ch_col = get_column_letter(1)                 # 'A' — chapter number
    first, last = 4, 3 + len(DAYS)                # data rows 4..147

    # Progress summary band (live formulas over the checklist below).
    ws["A1"] = "התקדמות:"
    ws["A1"].font = Font(name=FONT, bold=True, size=12)
    ws["B1"] = "הושלמו"
    ws["C1"] = f'=COUNTIF({done_col}{first}:{done_col}{last},"✓")'
    ws["D1"] = "מתוך"
    ws["E1"] = f"=COUNTA({ch_col}{first}:{ch_col}{last})"
    ws["F1"] = "אחוז"
    ws["G1"] = "=IF(E1=0,0,C1/E1)"
    ws["G1"].number_format = "0.0%"
    for col in ("B", "C", "D", "E", "F", "G"):
        ws[f"{col}1"].font = Font(name=FONT, bold=True, size=12)
        ws[f"{col}1"].alignment = CENTER

    ws.append([])              # row 2 spacer
    ws.append(headers)         # row 3
    style_header(ws, 3, len(headers))

    # chapter -> week map
    ch_week = {ch: wk["week"] for wk in CAL["weeks"]
               for ch in wk.get("chapters", [])}

    for idx in sorted(DAYS):
        d = DAYS[idx]
        ws.append([idx, d["title_he"], f'{d["phase"]}. {phase_name(d["phase"])}',
                   DENS_HE.get(d.get("estimated_density", "medium"), "בינונית"),
                   ch_week.get(idx, ""), reading_cell(idx), ""])
        r = ws.max_row
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = CFONT
            cell.border = BORDER
            cell.alignment = RIGHT if c in (2, 6) else CENTER

    widths = [6, 42, 30, 9, 7, 52, 9]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A4"

    dv = DataValidation(type="list", formula1='"✓,"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{done_col}{first}:{done_col}{last}")


def build_questions(ws):
    ws.sheet_view.rightToLeft = True
    letters = "א ב ג ד ה".split()
    headers = ["שבוע", "#", "שאלה", "א", "ב", "ג", "ד", "תשובה", "הסבר", "מקור"]
    ws.append(headers)
    style_header(ws, 1, len(headers))

    top = Alignment(horizontal="right", vertical="top", wrap_text=True)
    for wk in QUESTIONS:
        for i, q in enumerate(wk["questions"], start=1):
            opts = q["options"] + [""] * (4 - len(q["options"]))
            ans = letters[q["answer"]] if q["answer"] < len(letters) else "?"
            ws.append([wk["week"], i, q["q"], opts[0], opts[1], opts[2], opts[3],
                       ans, q.get("explanation", ""), q.get("source", "")])
            r = ws.max_row
            fill = LEARN if wk["week"] % 2 else "FFFFFF"
            for c in range(1, len(headers) + 1):
                cell = ws.cell(row=r, column=c)
                cell.font = CFONT
                cell.border = BORDER
                cell.alignment = CENTER if c in (1, 2, 8) else top
                if c in (1, 2):
                    cell.fill = PatternFill("solid", fgColor=fill)
                if c == 8:  # answer — highlight
                    cell.font = Font(name=FONT, bold=True, size=11, color="1F6B2E")

    widths = [6, 4, 50, 20, 20, 20, 20, 8, 55, 20]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def main() -> int:
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "לוח שבועי"
    build_weekly(ws1)
    build_chapters(wb.create_sheet("פירוט פרקים"))
    if QUESTIONS:
        build_questions(wb.create_sheet("שאלות תרגול"))
    wb.save(OUT)
    print(f"Wrote {OUT}  ({sum(len(w['questions']) for w in QUESTIONS)} questions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
