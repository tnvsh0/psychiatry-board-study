#!/usr/bin/env python3
"""Publish the answer-order rebalance to the releases, and nothing else.

WHY THIS EXISTS AS ITS OWN SCRIPT. §5.12 refuses to upload an asset unless a
judge verdict covers its sha256, and qc_run states the rule from the other side:
"an `unjudged` chapter publishes nothing, not even its hygiene clean-up". That is
the right default and it stays. But it also parked a finished, exhaustively
verified repair behind a judge that is quota-locked, on material the owner is
about to study: measured over all 141 released quizzes, the correct answer was
option A in 864 of the 949 single-answer items — 91%, D never — so answering A
without reading scored 91%.

The owner decided on 2026-09-29 to publish this one repair without a verdict. It
is the narrowest case there is: `qc_fix.rebalance_keys` reorders options and
changes no content, §5.9 already classes key position as a presentation fix that
needs neither an LLM nor the owner, and every claim below is re-checked per file
at upload time rather than trusted from the earlier run.

WHAT IT REFUSES TO DO. A chapter whose rebalance does not verify is skipped and
named, never uploaded — same direction as everything else here (§2.3). The
pristine bytes stay in `_qc/orig/`, committed (§2.5), and this writes a manifest
with both hashes so qc_revert has something to rebuild from.

    python scripts/qc_publish_rebalance.py --dry-run
    python scripts/qc_publish_rebalance.py --limit 5
    python scripts/qc_publish_rebalance.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import qc_fix  # noqa: E402
import qc_lint  # noqa: E402
import qc_store  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ORIG = REPO / "_qc" / "orig"
WORK = REPO / "_qc" / "rebalanced"
MANIFEST = REPO / "_qc" / "rebalance_published.json"
GH_REPO = "tnvsh0/psychiatry-board-study"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def verify(before, after) -> list[str]:
    """Everything that must still be true of the file being uploaded.

    Not a sample: every item, every option. The published quiz is what the owner
    is graded against, so "the rebalance was verified once, in bulk, three days
    ago" is not the standard — it is re-proved for the exact bytes that go up.
    """
    problems: list[str] = []
    a = qc_lint._quiz_item_list(before)                       # noqa: SLF001
    b = qc_lint._quiz_item_list(after)                        # noqa: SLF001
    if len(a) != len(b):
        return [f"{len(a)} items in, {len(b)} out"]
    for ia, ib in zip(a, b):
        uid = str(ia.get("id") or ia.get("question", "")[:30])
        oa = qc_lint._options(ia)                             # noqa: SLF001
        ob = qc_lint._options(ib)                             # noqa: SLF001
        if sorted(_canon(o) for o in oa) != sorted(_canon(o) for o in ob):
            problems.append(f"option set changed in {uid}")
        ka = [o for o in oa if isinstance(o, dict) and o.get("isCorrect")]
        kb = [o for o in ob if isinstance(o, dict) and o.get("isCorrect")]
        if len(ka) != len(kb):
            problems.append(f"{len(ka)} keys in, {len(kb)} out in {uid}")
        elif ka and ka[0].get("text") != kb[0].get("text"):
            problems.append(f"the correct answer changed in {uid}")
        rest_a = {k: v for k, v in ia.items() if not isinstance(v, list)}
        rest_b = {k: v for k, v in ib.items() if not isinstance(v, list)}
        if rest_a != rest_b:
            problems.append(f"a non-option field changed in {uid}")
    return problems


def released_tags() -> set[str]:
    r = subprocess.run(["gh", "release", "list", "--repo", GH_REPO,
                        "--limit", "300", "--json", "tagName"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"gh release list failed: {(r.stderr or '')[:200]}")
    return {x["tagName"] for x in json.loads(r.stdout or "[]")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--chapters", help="comma-separated indexes")
    a = ap.parse_args()
    only = ({int(x) for x in a.chapters.replace(" ", "").split(",") if x}
            if a.chapters else None)

    tags = released_tags()
    WORK.mkdir(parents=True, exist_ok=True)
    todo, skipped, unchanged = [], [], []
    for f in sorted(ORIG.glob("*/quiz.json")):
        tag = f.parent.name
        if only is not None:
            try:
                if int(tag.split("-")[1]) not in only:
                    continue
            except (IndexError, ValueError):
                continue
        raw = f.read_text(encoding="utf-8", errors="replace")
        try:
            before = json.loads(raw)
        except Exception as exc:
            skipped.append((tag, f"the snapshot does not parse: {exc}"))
            continue
        if tag not in tags:
            skipped.append((tag, "no such release"))
            continue
        # Re-derived here rather than read from the earlier run's output: the
        # rebalance is deterministic, so this both reproduces it and proves the
        # file on disk is still the file this code produces.
        after = qc_fix.rebalance_keys(json.loads(raw), tag)
        if _canon(after) == _canon(before):
            unchanged.append(tag)
            continue
        again = qc_fix.rebalance_keys(json.loads(_canon(after)), tag)
        if _canon(again) != _canon(after):
            skipped.append((tag, "not idempotent"))
            continue
        problems = verify(before, after)
        if problems:
            skipped.append((tag, "; ".join(problems[:3])))
            continue
        todo.append((tag, raw, json.dumps(after, ensure_ascii=False, indent=1)))

    print(f"{len(todo)} to publish · {len(unchanged)} already balanced · "
          f"{len(skipped)} refused")
    for tag, why in skipped:
        print(f"  REFUSED {tag}: {why}")
    if a.dry_run:
        for tag, _raw, _new in todo[:10]:
            print(f"  would upload quiz.json to {tag}")
        return 1 if skipped else 0

    published = []
    failed = []
    for i, (tag, raw, new) in enumerate(todo[: a.limit or len(todo)], 1):
        out = WORK / f"{tag}.quiz.json"
        out.write_text(new, encoding="utf-8")
        staged = out.with_name("quiz.json")
        staged.write_text(new, encoding="utf-8")
        u = subprocess.run(["gh", "release", "upload", tag, str(staged),
                            "--repo", GH_REPO, "--clobber"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        staged.unlink(missing_ok=True)
        if u.returncode != 0:
            failed.append((tag, (u.stderr or "")[:120]))
            print(f"[{i}/{len(todo)}] FAIL {tag}: {(u.stderr or '')[:100]}",
                  flush=True)
            continue
        published.append({"tag": tag, "asset": "quiz.json",
                          "sha256_before": sha(raw), "sha256_after": sha(new)})
        print(f"[{i}/{len(todo)}] {tag}", flush=True)

    qc_store.write_json_atomic(MANIFEST, {
        "what": "answer-order rebalance (qc_fix.rebalance_keys), published "
                "without a judge verdict on the owner's decision of 2026-09-29",
        "published": published,
        "unchanged": unchanged,
        "refused": [{"tag": t, "why": w} for t, w in skipped],
        "failed": [{"tag": t, "why": w} for t, w in failed],
    })
    print(f"\n{len(published)} published, {len(failed)} failed, "
          f"{len(skipped)} refused · manifest {MANIFEST}")
    return 1 if failed or skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
