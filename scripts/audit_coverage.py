#!/usr/bin/env python3
"""Report which parts of the textbooks the generated chapters have actually cited.

The curriculum claims to cover the books; this checks it. For every chapter that
has a published Release, it reads the "מפת המקורות" table at the top of
study_guide.md, accumulates the cited printed pages per book, and maps them onto
the real chapter boundaries from schedule/book_map.json. What comes out is the
list of book chapters that no study guide has touched.

Two deliberate limits, so the output is not read as stronger than it is:

  * Only chapters with a Release count. Chapters not yet generated are reported
    separately as "not yet generated", never as a coverage gap — those are two
    very different problems.
  * Synopsis has no printed page numbers (reflowed edition), so it is audited by
    chapter name matching, not by page.

Usage:
    python scripts/audit_coverage.py                  # fetch guides from Releases
    python scripts/audit_coverage.py --local DIR      # read study_guide.md from DIR
    python scripts/audit_coverage.py --json           # machine-readable
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# The report contains Hebrew and box-drawing glyphs; force UTF-8 so it does not
# die on a legacy console codepage (Windows cp1255) when run outside the VM.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:  # pragma: no cover
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = REPO_ROOT / "schedule" / "schedule.json"
BOOK_MAP = REPO_ROOT / "schedule" / "book_map.json"
GH_REPO = os.environ.get("GH_REPO", "tnvsh0/psychiatry-board-study")

# Study-guide table headers -> book_map keys. The header text is written by a
# language model, so match loosely on a distinctive substring.
HEADER_ALIASES = [
    ("dsm", "DSM-5-TR"),
    ("dulcan", "Dulcan"),
    ("stahl c&a", "Stahl C&A"),
    ("stahl pediatric", "Stahl C&A"),
    ("stahl essential", "Stahl Essential"),
    ("synopsis", "Synopsis"),
]


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-")
    return text.lower()[:60]


def book_for_header(header: str) -> str | None:
    h = header.lower()
    for needle, key in HEADER_ALIASES:
        if needle in h:
            return key
    return None


def parse_source_map(md: str) -> dict[str, set[int]]:
    """Pull {book: {printed pages}} out of the guide's source-map table."""
    rows = [ln for ln in md.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 3:
        return {}

    def cells(row: str) -> list[str]:
        return [c.strip().strip("*").strip() for c in row.strip().strip("|").split("|")]

    headers = cells(rows[0])
    out: dict[str, set[int]] = {}
    for row in rows[2:]:
        c = cells(row)
        if set("".join(c)) <= set("-–— :"):  # separator row
            continue
        for i, header in enumerate(headers[1:], start=1):
            key = book_for_header(header)
            if key is None or i >= len(c):
                continue
            # A chapter-reference column carries no page information.
            if "פרק" in header or key == "Synopsis":
                continue
            for m in re.finditer(r"(\d+)\s*[-–—]\s*(\d+)|(\d+)", c[i]):
                if m.group(1):
                    a, b = int(m.group(1)), int(m.group(2))
                    if a <= b and b - a < 500:
                        out.setdefault(key, set()).update(range(a, b + 1))
                else:
                    out.setdefault(key, set()).add(int(m.group(3)))
    return out


def released_tags() -> set[str]:
    r = subprocess.run(
        ["gh", "release", "list", "--repo", GH_REPO, "--limit", "500", "--json", "tagName"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"[!] gh release list failed: {r.stderr.strip()[:200]}", file=sys.stderr)
        return set()
    return {x["tagName"] for x in json.loads(r.stdout)}


def fetch_guide(tag: str) -> str | None:
    r = subprocess.run(
        ["gh", "release", "view", tag, "--repo", GH_REPO, "--json", "assets"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    names = {a["name"] for a in json.loads(r.stdout).get("assets", [])}
    asset = "study_guide.md" if "study_guide.md" in names else (
        "report.md" if "report.md" in names else None)
    if asset is None:
        return None
    r = subprocess.run(
        ["gh", "release", "download", tag, "--repo", GH_REPO,
         "--pattern", asset, "--output", "-"],
        capture_output=True, text=True,
    )
    return r.stdout if r.returncode == 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", help="read study_guide.md files from this dir tree instead of Releases")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    if not BOOK_MAP.exists():
        print(f"ERROR: {BOOK_MAP} missing. Run scripts/build_book_map.py first.", file=sys.stderr)
        return 2

    days = json.loads(SCHEDULE.read_text(encoding="utf-8"))["days"]
    books = json.loads(BOOK_MAP.read_text(encoding="utf-8"))["books"]

    cited: dict[str, set[int]] = {}
    generated, missing = [], []

    if args.local:
        guides = {p.parent.name: p.read_text(encoding="utf-8", errors="replace")
                  for p in Path(args.local).rglob("study_guide.md")}
        for d in days:
            key = f"day-{d['index']:03d}"
            if key in guides:
                generated.append(d["index"])
                for b, pages in parse_source_map(guides[key]).items():
                    cited.setdefault(b, set()).update(pages)
            else:
                missing.append(d["index"])
    else:
        tags = released_tags()
        for d in days:
            tag = f"study-{d['index']:03d}-{slugify(d['title_en'])}"
            if tag not in tags:
                missing.append(d["index"])
                continue
            md = fetch_guide(tag)
            if md is None:
                missing.append(d["index"])
                continue
            generated.append(d["index"])
            for b, pages in parse_source_map(md).items():
                cited.setdefault(b, set()).update(pages)

    report: dict[str, object] = {
        "chapters_total": len(days),
        "chapters_generated": len(generated),
        "chapters_not_generated": missing,
        "books": {},
    }

    for key, book in books.items():
        chapters = book["chapters"]
        if not book.get("has_printed_pages"):
            report["books"][key] = {
                "auditable": False,
                "reason": "edition has no printed page numbers; cite by chapter",
                "chapter_count": len(chapters),
            }
            continue
        seen = cited.get(key, set())
        untouched, touched = [], 0
        for c in chapters:
            rng = c.get("pages")
            if not rng:
                continue
            lo, hi = rng
            if any(p in seen for p in range(lo, hi + 1)):
                touched += 1
            else:
                untouched.append({"title": c["title"], "pages": rng})
        auditable = touched + len(untouched)

        # A citation past the book's last page cannot have been read off the
        # book — it was invented. This is how the phantom "Stahl Pediatric"
        # column was caught (it cited p.612 of a 525-page book), so the audit
        # keeps watching for the same failure rather than trusting the fix.
        last = max((c["pages"][1] for c in chapters if c.get("pages")), default=0)
        impossible = sorted(p for p in seen if p > last)

        report["books"][key] = {
            "auditable": True,
            "chapters_touched": touched,
            "chapters_total": auditable,
            "pages_cited": len(seen),
            "last_page": last,
            "impossible_pages": impossible,
            "untouched": untouched,
        }

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"Chapters generated: {len(generated)}/{len(days)}")
    if missing:
        print(f"Not yet generated : {len(missing)} "
              f"(coverage below reflects only the {len(generated)} that exist)")
    print()
    for key, r in report["books"].items():
        if not r["auditable"]:
            print(f"── {key}: not auditable by page — {r['reason']}")
            continue
        print(f"── {key}: {r['chapters_touched']}/{r['chapters_total']} chapters cited, "
              f"{r['pages_cited']} distinct pages")
        if r["impossible_pages"]:
            print(f"     !! FABRICATED: {len(r['impossible_pages'])} page(s) cited past "
                  f"p.{r['last_page']}, the book's last page: {r['impossible_pages'][:12]}")
        for c in r["untouched"]:
            print(f"     UNCITED  pp.{c['pages'][0]}-{c['pages'][1]}  {c['title']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
