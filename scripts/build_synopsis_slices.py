#!/usr/bin/env python3
"""Extract the required Synopsis chapters into small per-chapter PDF slices.

The full Kaplan & Sadock Synopsis (3768 pdf pages) is over NotebookLM's per-source
index limit, and the owner's 3-part split is equal thirds (1256 pp each) that drag
the whole adult book into every notebook. The Stage-A required-literature list
(required_literature.json) names exactly which Synopsis chapters supplement Dulcan,
so this cuts each one out on its own — 30-260 pp, indexes trivially, zero adult
noise — and attaches per curriculum chapter via the manifest.

Ranges are resolved from the Synopsis PDF's own table of contents (by the 'ref'
in required_literature.json: a dotted ref like 1.1/3.4/32.1/34.1/34.4 is a
subsection, a bare number is the whole chapter — matched on the actual TOC
chapter number, since the required doc's numbering differs by one for
"Other somatic therapies").

Outputs (all under schedule/synopsis_slices/, gitignored as copyrighted extracts):
  synopsis-<ref>-<slug>.pdf   one slice per required chapter/subsection
  synopsis-required-all.pdf   the 14 combined, for the single-source option
  manifest.json               curriculum chapter -> slice filenames (committed)

Usage:  python scripts/build_synopsis_slices.py [path-to-synopsis.pdf]
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

import fitz

REPO = Path(__file__).resolve().parents[1]
REQ = json.loads((REPO / "schedule" / "required_literature.json").read_text(encoding="utf-8"))
OUT_DIR = REPO / "schedule" / "synopsis_slices"

SLUG = {
    "1.1": "adult-patient", "3.4": "neurocognitive-gmc", "5": "schizophrenia",
    "7": "depressive", "9": "ocd", "10": "trauma", "11": "dissociative",
    "12": "somatic", "19": "personality", "20": "somatic-therapies",
    "23": "psychotherapy", "32.1": "development", "33": "neuroscience",
    "34.1/34.4": "psychosocial-personality-theory",
}


def find_synopsis() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    hits = glob.glob(str(REPO / "Text BOOKS" / "*Synopsis of Psychiatry*.pdf"))
    if not hits:
        sys.exit("Synopsis PDF not found in Text BOOKS/ — pass the path as an argument.")
    return Path(hits[0])


def span(toc, idx) -> tuple[int, int]:
    """1-based pdf page range of TOC entry idx, up to the next same-or-higher level."""
    lvl, _, start = toc[idx]
    end = None
    for j in range(idx + 1, len(toc)):
        if toc[j][0] <= lvl:
            end = toc[j][2] - 1
            break
    return start, end if end else start


def resolve(toc, synopsis_ch: int, part: str) -> tuple[int, int]:
    """Page range for a required item: a dotted 'part' is a subsection matched by
    its label; a bare part is the whole chapter matched by its TOC number."""
    if "." in part:
        for i, (_lvl, t, _p) in enumerate(toc):
            if t.strip().startswith(part + " "):
                return span(toc, i)
        raise SystemExit(f"subsection {part} not found in TOC")
    for i, (lvl, t, _p) in enumerate(toc):
        if lvl <= 2 and t.strip().startswith(f"{synopsis_ch}. "):
            return span(toc, i)
    raise SystemExit(f"chapter {synopsis_ch} not found in TOC")


def main() -> int:
    src = find_synopsis()
    doc = fitz.open(str(src))
    toc = [(lvl, t, p) for lvl, t, p in doc.get_toc()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "_note": ("Per-chapter Synopsis slices for the required-literature supplements. "
                  "The PDFs are gitignored (copyrighted extracts); regenerate locally "
                  "with scripts/build_synopsis_slices.py. 'by_curriculum' maps a "
                  "curriculum chapter index to the slice file(s) to attach to its "
                  "NotebookLM notebook."),
        "source_pdf": src.name,
        "slices": [],
        "by_curriculum": {},
        "combined": "synopsis-required-all.pdf",
    }
    all_pages: list[tuple[int, int]] = []

    for s in REQ["synopsis_supplements"]:
        ref = s["ref"]
        parts = ref.split("/") if "/" in ref else [ref]
        ranges = [resolve(toc, s["synopsis_ch"], p) for p in parts]
        total = sum(e - b + 1 for b, e in ranges)
        slug = SLUG.get(ref, f"ch{s['synopsis_ch']}")
        fname = f"synopsis-{ref.replace('/', '_').replace('.', '_')}-{slug}.pdf"

        slice_doc = fitz.open()
        for b, e in ranges:
            slice_doc.insert_pdf(doc, from_page=b - 1, to_page=e - 1)
            all_pages.append((b, e))
        slice_doc.save(str(OUT_DIR / fname))
        slice_doc.close()

        manifest["slices"].append({
            "file": fname, "ref": ref, "synopsis_ch": s["synopsis_ch"],
            "topic_he": s["topic_he"], "pages": total, "curriculum": s["curriculum"],
        })
        for c in s["curriculum"]:
            manifest["by_curriculum"].setdefault(str(c), []).append(fname)
        print(f"  {ref:9s} {total:4d} pp -> {fname}")

    # combined single-source option
    combined = fitz.open()
    for b, e in all_pages:
        combined.insert_pdf(doc, from_page=b - 1, to_page=e - 1)
    combined.save(str(OUT_DIR / "synopsis-required-all.pdf"))
    combined_pp = combined.page_count
    combined.close()

    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n{len(manifest['slices'])} slices + combined ({combined_pp} pp) -> {OUT_DIR}")
    print(f"manifest: {OUT_DIR / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
