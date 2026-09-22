#!/usr/bin/env python3
"""Adversarial bug hunt — pages the bundle drops on the way to the judge.

Offline: reads the repository's own `schedule/synopsis_slices/` and nothing
else. No agy, no gh, no network.

THE DEFECT. `qc_sources.synopsis_pages` keys every page it produces on
`(book, printed)` = `("Synopsis", i + 1)` and merges into one dict, but
`printed` is the page's position INSIDE ITS SLICE — so two slices both number
their pages from 1 and the second slice's page N collides with the first
slice's page N. `_merge` keeps the first and only appends the reason, so the
overlapping pages of the second slice never reach the bundle at all.

Five chapters of the curriculum have two Synopsis slices (14, 56, 139, 140,
141 — `schedule/synopsis_slices/manifest.json`), and for each of them a whole
slice's worth of pages is silently missing:

    day  56: 83 + 257 slice pages -> 257 entries  (83 lost)
    day  14: 62 + 216             -> 216          ( 62 lost)
    day 139: 149 + 216            -> 216          (149 lost)

The loss is invisible downstream: `_cost` counts the same collapsed set, so the
call reports a page count that matches what it carries, and the manifest hands
qc_judge `slice_file` for the WRONG slice on every colliding page — so a quote
the judge really did read on neuroscience p.12 is verified against
schizophrenia p.12 and comes back unverified.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_books                                            # noqa: E402
import qc_sources                                          # noqa: E402

MANIFEST = qc_books.SLICES_DIR / "manifest.json"


def _slices_for(day: int) -> list[dict]:
    if not MANIFEST.exists():
        return []
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [s for s in data.get("slices", [])
            if day in (s.get("curriculum") or [])
            and (qc_books.SLICES_DIR / s.get("file", "")).exists()]


def _multi_slice_days() -> list[int]:
    if not MANIFEST.exists():
        return []
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    counts: dict[int, int] = {}
    for s in data.get("slices", []):
        if not (qc_books.SLICES_DIR / s.get("file", "")).exists():
            continue
        for day in (s.get("curriculum") or []):
            counts[day] = counts.get(day, 0) + 1
    return sorted(d for d, n in counts.items() if n > 1)


class SynopsisSlicesCollide(unittest.TestCase):
    def setUp(self):
        self.days = _multi_slice_days()
        if not self.days:
            self.skipTest("no chapter on this machine has two Synopsis slices")

    def test_every_slice_page_of_a_two_slice_chapter_reaches_the_plan(self):
        for day in self.days:
            with self.subTest(day=day):
                slices = _slices_for(day)
                want = sum(int(s.get("pages") or 0) for s in slices)
                got = qc_sources.synopsis_pages(day)
                self.assertEqual(
                    len(got), want,
                    f"day {day}: {len(slices)} slices holding {want} pages "
                    f"produced {len(got)} bundle entries — "
                    f"{want - len(got)} pages of the later slice collided with "
                    f"the earlier one on ('Synopsis', page-inside-the-slice) "
                    f"and were merged away")

    def test_each_slice_keeps_its_own_pages_in_the_plan(self):
        """Not just the count: the pages that survive must come from both."""
        for day in self.days:
            with self.subTest(day=day):
                by_file: dict[str, int] = {}
                for page in qc_sources.synopsis_pages(day).values():
                    name = page.get("slice_file") or "?"
                    by_file[name] = by_file.get(name, 0) + 1
                for s in _slices_for(day):
                    self.assertEqual(
                        by_file.get(s["file"], 0), int(s.get("pages") or 0),
                        f"day {day}: slice {s['file']} has "
                        f"{s.get('pages')} pages but only "
                        f"{by_file.get(s['file'], 0)} of them are in the plan")


if __name__ == "__main__":
    unittest.main()
