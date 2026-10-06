#!/usr/bin/env python3
"""Republish flashcards that NotebookLM returned in its new format, as plain text.

Around 2026-10-01 NotebookLM started returning each card side as
`{"flashcardContentBlock": [{"type": "text", "content": "…"}]}` instead of a
string. Every set generated since — 14 chapters, five of them in week 1 — was
published that way, and the app could not show them: it handed the dict to the
page as the question, and computed the same card id for every card in the set,
so spaced repetition would have treated a whole chapter as one card.

daily_study and backfill_items now flatten at download, so nothing new arrives
in that shape. This fixes the ones already published. It is a change of
FORMAT, not of content, and it is proved per file before anything goes up:
every flattened side must equal the join of its original text blocks exactly,
no other field of any card may change, and a side holding a block that is not
text is refused rather than dropped.

    python scripts/qc_publish_flatten.py --dry-run
    python scripts/qc_publish_flatten.py
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import qc_store  # noqa: E402
import qc_units  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
GH_REPO = "tnvsh0/psychiatry-board-study"
MANIFEST = REPO / "_qc" / "flatten_published.json"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tags() -> list[str]:
    r = subprocess.run(["gh", "release", "list", "--repo", GH_REPO, "--limit",
                        "300", "--json", "tagName"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"gh release list failed: {(r.stderr or '')[:200]}")
    return sorted(t["tagName"] for t in json.loads(r.stdout or "[]")
                  if t["tagName"].startswith("study-"))


def verify(before, after) -> list[str]:
    """Every claim the republish rests on, for every card."""
    a = qc_units._card_list("flashcards.json", before)          # noqa: SLF001
    b = qc_units._card_list("flashcards.json", after)           # noqa: SLF001
    if len(a) != len(b):
        return [f"{len(a)} cards in, {len(b)} out"]
    problems = []
    for i, (ca, cb) in enumerate(zip(a, b), 1):
        for side in ("front", "back"):
            v = ca.get(side)
            want = (v if isinstance(v, str) else
                    "\n".join(str(x.get("content") or "")
                              for x in v["flashcardContentBlock"]))
            if cb.get(side) != want:
                problems.append(f"card {i} {side} text changed")
        if {k: v for k, v in ca.items() if k not in ("front", "back")} != \
           {k: v for k, v in cb.items() if k not in ("front", "back")}:
            problems.append(f"card {i}: a field other than front/back changed")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="flatten-"))
    todo, refused, already = [], [], 0
    for tag in tags():
        d = work / tag
        d.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["gh", "release", "download", tag, "--repo", GH_REPO,
                            "-p", "flashcards.json", "-D", str(d), "--clobber"],
                           capture_output=True, text=True, encoding="utf-8")
        f = d / "flashcards.json"
        if r.returncode != 0 or not f.exists():
            continue
        raw = f.read_text(encoding="utf-8")
        before = json.loads(raw)
        try:
            after, n = qc_units.flatten_flashcards(copy.deepcopy(before))
        except qc_units.UnitError as exc:
            refused.append((tag, str(exc)))
            continue
        if n == 0:
            already += 1
            continue
        problems = verify(before, after)
        if problems:
            refused.append((tag, "; ".join(problems[:3])))
            continue
        todo.append((tag, raw, json.dumps(after, ensure_ascii=False, indent=2), n))

    print(f"{len(todo)} to republish · {already} already plain text · "
          f"{len(refused)} refused")
    for tag, why in refused:
        print(f"  REFUSED {tag}: {why}")
    if a.dry_run:
        for tag, _r, _n, n in todo:
            print(f"  would republish {tag} ({n} sides)")
        return 1 if refused else 0

    published, failed = [], []
    for tag, raw, new, n in todo:
        staged = work / tag / "upload" / "flashcards.json"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(new, encoding="utf-8")
        u = subprocess.run(["gh", "release", "upload", tag, str(staged),
                            "--repo", GH_REPO, "--clobber"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if u.returncode != 0:
            failed.append({"tag": tag, "why": (u.stderr or "")[:160]})
            print(f"  FAIL {tag}: {(u.stderr or '')[:100]}")
            continue
        published.append({"tag": tag, "sides": n, "sha256_before": sha(raw),
                          "sha256_after": sha(new)})
        print(f"  {tag}: {n} sides")
    qc_store.write_json_atomic(MANIFEST, {
        "what": "flashcards.json converted from NotebookLM's flashcardContentBlock "
                "format to plain-text sides; content unchanged",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "published": published, "failed": failed,
        "refused": [{"tag": t, "why": w} for t, w in refused],
    })
    print(f"\n{len(published)} republished, {len(failed)} failed · {MANIFEST}")
    return 1 if failed or refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
