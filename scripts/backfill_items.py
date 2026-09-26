#!/usr/bin/env python3
"""Attach the quiz, flashcards or facts that a released chapter never got.

The 2026-09-25 sweep of all 144 released chapters found nine ESSENTIAL assets
absent — and seven of them are in the first week the owner studies:

    ch01  facts.json
    ch03  quiz.json, flashcards.json
    ch10  quiz.json, flashcards.json
    ch11  quiz.json, flashcards.json

Chapters 3, 10 and 11 have no quiz and no flashcards at all: a third of the
study material for a day he reaches on 2026-09-28. They predate the guard in
daily_study that now refuses to publish an incomplete chapter (§5.4's comment on
REQUIRED_ASSETS names the same chapters), so nothing will notice them again.

WHY THIS AND NOT A RE-RUN. Re-running the chapter regenerates everything and
replaces the release: a new study guide, new cards, new quiz, new page
references, for material the owner may already have read. This is additive —
it generates ONLY the missing asset with the prompt that chapter would have
used, and uploads it beside the assets already there. Nothing published is
overwritten, which is also why `--clobber` is safe here: the only name it can
collide with is the asset we just proved absent.

It needs NotebookLM, not agy, so it runs during an agy quota lockout.

Fail-closed, like everything else that can publish (§2.3): an artifact that does
not download, does not parse as JSON, or carries no items is not uploaded and
the chapter stays on the list. A placeholder on a release passes every
existence check downstream and reaches the learner as his study material.

    python scripts/backfill_items.py --dry-run     # what is missing
    python scripts/backfill_items.py --limit 4     # fill the next four
    python scripts/backfill_items.py --day 3       # one chapter
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import daily_study as ds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "_bulk" / "items"
RATE_MARKERS = ("RateLimit", "RESOURCE_EXHAUSTED", "quota", "cap")
# The three §5.4 calls "essential" that this script can generate on its own.
# study_guide.md is deliberately not here: it is asked for with `ask` in three
# validated attempts against _study_guide_defects, and a guide is the one asset
# whose absence already stops the release.
KINDS = ("quiz.json", "flashcards.json", "facts.json")
# Under this many items an artifact is a stub, not a chapter's worth. The
# generator is asked for 8-10 quiz items and a standard card set; anything at or
# below this is a failure that produced valid JSON.
MIN_ITEMS = 3


def _gh_json(args: list[str]) -> dict | list:
    r = subprocess.run(["gh", *args, "--repo", ds.GITHUB_REPO],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:2])} failed: {(r.stderr or '')[:200]}")
    return json.loads(r.stdout or "{}")


def missing_items(only_day: int | None = None) -> list[dict]:
    """Released chapters with an essential asset absent, earliest first."""
    sched = {d["index"]: d for d in ds.load_schedule()["days"]}
    out = []
    for rel in _gh_json(["release", "list", "--limit", "300",
                         "--json", "tagName"]):
        tag = rel["tagName"]
        if not tag.startswith("study-"):
            continue
        try:
            idx = int(tag.split("-")[1])
        except (IndexError, ValueError):
            continue
        if only_day is not None and idx != only_day:
            continue
        day = sched.get(idx)
        if not day or tag.split("-", 2)[-1] != ds.slugify(day.get("title_en", "")):
            continue                        # stale tag; leave it alone
        assets = _gh_json(["release", "view", tag, "--json", "assets"])
        have = {a["name"] for a in (assets or {}).get("assets", [])
                if a["size"] > 0}
        want = [k for k in KINDS if k not in have]
        if want:
            out.append({"index": idx, "tag": tag, "day": day, "want": want})
    return sorted(out, key=lambda x: x["index"])


def _items_in(path: Path, name: str) -> int:
    """How many items the downloaded artifact really carries, or -1 if broken."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        print(f"    [!] {name} does not parse: {exc}")
        return -1
    if name == "facts.json":
        # facts.json is a dict of aspects, not a list; its "items" are its keys.
        return len(data) if isinstance(data, dict) else -1
    for key in ("items", "flashcards", "cards", "questions", "quiz"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return len(data[key])
    return len(data) if isinstance(data, list) else -1


def make_one(job: dict, name: str, nb_id: str) -> tuple[bool, str]:
    """Generate, validate and attach ONE asset. (ok, note)"""
    idx, day, tag = job["index"], job["day"], job["tag"]
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / f"day-{idx:03d}-{name}"

    if name == "facts.json":
        facts = ds.extract_facts(day, nb_id)
        if not facts:
            return False, "extraction returned nothing"
        out.write_text(json.dumps(facts, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    else:
        # The prompt the chapter itself would have used, so a backfilled quiz is
        # not a different product from one made in line. The legend block is
        # empty for the same reason it is in backfill_audio: the plates are not
        # what a clinical item is about, and §5.4 forbids asking about them.
        symbol_block = ds.collect_symbol_descriptions(
            day.get("symbols_needed", []), ds.load_symbols())
        kind = "quiz" if name == "quiz.json" else "flashcards"
        prompt = (ds.prompt_quiz if kind == "quiz" else ds.prompt_flashcards)(
            day, symbol_block, "")
        task = (ds.start_quiz if kind == "quiz" else ds.start_flashcards)(prompt)
        if not task:
            return False, "generation refused"
        # {uuid: kind} in, {uuid: (kind, status, artifact)} out — and a timed-out
        # task comes back with status "timeout", not absent, so the status is
        # what decides. `if done.get(kind)` would read a timeout as a success.
        done = ds.wait_for_artifacts({task: kind})
        got = [aid for aid, (_k, status, _a) in (done or {}).items()
               if status == "completed"]
        if not got:
            state = ", ".join(sorted({s for _k, s, _a in (done or {}).values()}))
            return False, f"generation never completed ({state or 'no status'})"
        if not ds.download_artifact(kind, got[0], out) or not out.exists():
            return False, "download failed"

    n = _items_in(out, name)
    if n < MIN_ITEMS:
        out.unlink(missing_ok=True)
        return False, (f"only {n} item(s) — not uploading a stub"
                       if n >= 0 else "unusable JSON — not uploading")
    u = subprocess.run(["gh", "release", "upload", tag, str(out),
                        "--repo", ds.GITHUB_REPO, "--clobber"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if u.returncode != 0:
        return False, f"upload failed: {(u.stderr or '')[:120]}"
    return True, f"{n} items attached to {tag}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=4,
                    help="how many CHAPTERS to attempt this run")
    ap.add_argument("--day", type=int, help="just this chapter")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    jobs = missing_items(a.day)
    total = sum(len(j["want"]) for j in jobs)
    print(f"{len(jobs)} released chapter(s) missing {total} essential asset(s)")
    if not jobs:
        return 0
    if a.dry_run:
        for j in jobs:
            print(f"  {j['index']:3}  {', '.join(j['want']):<32} "
                  f"{j['day'].get('title_he', '')[:40]}")
        return 0

    filled = 0
    for j in jobs[:a.limit]:
        title = j["day"].get("title_he", j["day"].get("title_en", ""))
        print(f"\n[{j['index']:3}] {title[:40]} — {', '.join(j['want'])}",
              flush=True)
        try:
            nb_id, _ = ds.find_day_notebook(j["index"], title)
        except RuntimeError as exc:
            print(f"      FAIL could not look up the notebook: {exc}")
            continue
        if not nb_id:
            print("      FAIL no notebook for this chapter "
                  "(re-run the chapter first)")
            continue
        ds.use_notebook(nb_id)
        for name in j["want"]:
            ok, note = make_one(j, name, nb_id)
            print(f"      {'OK  ' if ok else 'FAIL'} {name}: {note}", flush=True)
            filled += ok
            if not ok and any(m.lower() in note.lower() for m in RATE_MARKERS):
                print("      daily cap reached — stopping", file=sys.stderr)
                return 0
            time.sleep(20)

    print(f"\n{filled} asset(s) attached")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
