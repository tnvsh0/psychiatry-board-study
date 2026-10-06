#!/usr/bin/env python3
"""The unit: the smallest piece of a chapter that gets its own verdict.

Everything downstream is keyed on this. qc_sources writes one `### <uid>` block
per unit into artifacts.md; the judge must return exactly one coverage row per
unit (spec §5.8 verification 2); qc_fix patches the artifact the unit came from.
So the uid and the rendered text are a contract, not a convenience.

WHY THE TEXT IS RENDERED HERE AND NOT BY EACH CALLER

The judge is verified by echo: a coverage row is accepted only when its `echo`
(>= 30 normalized characters) is found inside the unit's text. If the bundle
showed the judge one rendering and the verifier searched another, every row
would fail verification and every chapter would come back `unjudged`. One
function produces the text, and both sides use it.

WHY IT IS VERBATIM, AND WHY UNKNOWN FIELDS STILL GET RENDERED

The 2026-09-18 sample found errors that live in the fields a summary would drop:
042 Q2's key is wrong in one option's rationale (DSM p.135 "three (or more)"),
038 Q9's error is in a distractor, and 019's atomoxetine conflict is in a hint.
So every option, every rationale, every hint and every type-specific field is
shown as written. Anything this renderer does not recognise is dumped as JSON
rather than dropped: the quiz schema has already changed under this pipeline
once (17 of 42 items in releases since 2026-09-14 are types the app did not
know), and a silently dropped field is an unjudged claim that still reaches the
learner.

WHY faults RAISE INSTEAD OF RETURNING NOTHING

Fail closed (spec §2.3). A quiz.json that does not parse must not look like a
chapter with no quiz items — that would let an unjudged asset be approved.

WHY NO UNIT IS EXEMPT FROM JUDGING

A classifier here once marked the guide's opening "מפת המקורות" table as holding
no clinical claim, so qc_sources could leave it out of the judge calls. It was
refuted by the first chapter judged end to end: 8 of chapter 019's high-severity
errata are in that very unit — real, mappable pages, in the right book, cited
for the wrong topic, which qc_cite and qc_lint cannot see. The classifier and
every path that could exempt a unit have been removed. Every unit is judged.

    python scripts/qc_units.py <dir>            one line per unit
    python scripts/qc_units.py <dir> --uid Q3   that unit's text, as judged
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The assets judged in v1 (spec §3), in the order their units are emitted.
# Fixed rather than "whatever order the caller's dict happens to be in", because
# the bundle, the prompt hash and the verdict must be reproducible across runs.
ARTIFACT_ORDER = ("study_guide.md", "facts.json", "quiz.json", "flashcards.json")
LEGEND_RE = re.compile(r"^sketchy_[\w\-]*\.txt$", re.I)

# `## ` only. `### ` subheads stay inside their section: the judge is asked for
# a verdict per section (spec §5.8 B), and 029's nine sections carry 14 subheads
# between them — splitting on those would triple the coverage rows for nothing.
_H2_RE = re.compile(r"(?m)^##(?!#)[ \t]*(.*)$")
# "12. סימן [Eng]: claim — הסבר" — the numbered rows of a plate legend.
_LEGEND_ROW_RE = re.compile(r"(?m)^(\d{1,3})\.[ \t]+(.*)$")
_NUMBERS_CHIP = "מספרים לבחינה"

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class UnitError(ValueError):
    """An asset that cannot be turned into units. Never swallowed."""


@dataclass
class Unit:
    uid: str                    # Q3, C12, G:<heading>, F:<key path>, L:<file>:<row>
    artifact: str               # the release asset name the unit came from
    text: str                   # what the judge sees, verbatim
    stable_id: str              # survives renumbering; fixes.json keys on it
    meta: dict[str, Any] = field(default_factory=dict)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _letter(i: int) -> str:
    """A..Z, then AA, AB… — no quiz here has more than 5 options, but an index
    error in a 27-option item would silently re-label two options the same."""
    if i < 26:
        return _LETTERS[i]
    return _LETTERS[i // 26 - 1] + _LETTERS[i % 26]


def _load_json(name: str, text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnitError(f"{name}: not valid JSON ({exc})") from exc


# ---------------------------------------------------------------------------
# study_guide.md


def _guide_units(name: str, text: str) -> list[Unit]:
    """One unit per `## ` section; the title and preamble are `G:_intro`.

    The sections partition the file exactly — "".join(u.text) == text — so a
    fix located inside a section can be written back by slicing the original.
    """
    heads = list(_H2_RE.finditer(text))
    bounds = [(0, heads[0].start() if heads else len(text), None)]
    for k, m in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(text)
        bounds.append((m.start(), end, m.group(1).strip().rstrip("#").strip()))

    out: list[Unit] = []
    seen: dict[str, int] = {}
    for idx, (a, b, head) in enumerate(bounds):
        body = text[a:b]
        if head is None and not body.strip():
            continue                      # a guide that opens straight on `## `
        label = head if head is not None else "_intro"
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            # 035 and 042 both repeat a heading ("סיכום מבני"); the judge must
            # still be able to name which one it ruled on.
            label = f"{label} #{seen[label]}"
        uid = f"G:{label}"
        out.append(Unit(uid=uid, artifact=name, text=body,
                        stable_id=_sha1(uid + "\n" + body),
                        meta={"kind": "guide", "heading": head, "index": idx,
                              "span": [a, b]}))
    return out


# ---------------------------------------------------------------------------
# facts.json


def _facts_leaves(node: Any, path: str = "") -> list[tuple[str, Any]]:
    """(key path, value) for every addressable entry.

    A leaf is a scalar or a list with no container in it. `criteria.clusters` is
    a list of dicts, so it is walked into and `criteria.clusters[0].items` is the
    unit — a list of nine three-word symptoms is one claim to check, not nine,
    and qc_fix edits facts.json on parsed values at exactly this path.
    """
    if isinstance(node, dict) and node:
        out = []
        for k, v in node.items():
            out.extend(_facts_leaves(v, f"{path}.{k}" if path else str(k)))
        return out
    if isinstance(node, list) and any(isinstance(v, (dict, list)) for v in node):
        out = []
        for i, v in enumerate(node):
            out.extend(_facts_leaves(v, f"{path}[{i}]"))
        return out
    return [(path or "_root", node)]


def _facts_units(name: str, text: str) -> list[Unit]:
    data = _load_json(name, text)
    out = []
    for path, value in _facts_leaves(data):
        uid = f"F:{path}"
        # JSON, not prose: the judge's `said` has to be quotable back into the
        # file, and indent=1 keeps each Hebrew string on its own line intact.
        body = json.dumps(value, ensure_ascii=False, indent=1)
        out.append(Unit(uid=uid, artifact=name, text=body,
                        stable_id=_sha1(uid + "\n" + body),
                        meta={"kind": "facts", "path": path, "value": value}))
    return out


# ---------------------------------------------------------------------------
# quiz.json


def _quiz_items(name: str, data: Any) -> list[dict]:
    if isinstance(data, dict):
        for key in ("questions", "items", "quiz"):
            if isinstance(data.get(key), list):
                return data[key]
        raise UnitError(f"{name}: object with no question list (keys: "
                        f"{sorted(data)})")
    if isinstance(data, list):
        return data
    raise UnitError(f"{name}: expected a list of questions, got {type(data).__name__}")


def _options(item: dict) -> list[dict]:
    opts = item.get("answerOptions")
    if opts is None:
        opts = item.get("options")
    return opts if isinstance(opts, list) else []


def render_quiz(item: dict) -> str:
    """The item as the judge reads it. Every field, nothing paraphrased."""
    lines = [f"type: {item.get('type') or 'untyped'}"]
    lines.append(f"stem: {item.get('question', item.get('stem', ''))}")
    seen = {"type", "question", "stem", "answerOptions", "options", "hint"}

    for i, opt in enumerate(_options(item)):
        if not isinstance(opt, dict):
            lines.append(f"{_letter(i)}. [    ] {opt}")
            continue
        mark = "[KEY]" if opt.get("isCorrect") else "[    ]"
        lines.append(f"{_letter(i)}. {mark} {opt.get('text', '')}")
        if opt.get("rationale"):
            lines.append(f"    rationale: {opt['rationale']}")
        for k, v in opt.items():
            if k not in ("text", "isCorrect", "rationale"):
                lines.append(f"    {k}: {json.dumps(v, ensure_ascii=False)}")

    if "bestAnswer" in item:
        lines.append(f"bestAnswer: {item['bestAnswer']}")
        seen.add("bestAnswer")
    for ans in item.get("acceptableAnswers") or []:
        lines.append(f"acceptableAnswer: {ans}")
    seen.add("acceptableAnswers")
    if item.get("rationale"):
        lines.append(f"rationale: {item['rationale']}")
    seen.add("rationale")

    grading = item.get("grading")
    seen.add("grading")
    if isinstance(grading, dict):
        if grading.get("modelAnswer"):
            lines.append(f"modelAnswer: {grading['modelAnswer']}")
        if grading.get("rationale"):
            lines.append(f"rationale: {grading['rationale']}")
        for attr in grading.get("requiredAttributes") or []:
            lines.append(f"requiredAttribute: {attr}")
        errors = grading.get("errors") or {}
        for miss in (errors.get("misconceptions") or []):
            lines.append(f"misconception: {miss}")
        for bluff in (errors.get("bluffs") or []):
            lines.append(f"bluff: {bluff}")
        extra = {k: v for k, v in grading.items()
                 if k not in ("modelAnswer", "rationale", "requiredAttributes",
                              "errors")}
        extra_errors = {k: v for k, v in errors.items()
                        if k not in ("misconceptions", "bluffs")}
        if extra_errors:
            extra["errors"] = extra_errors
        for k, v in extra.items():
            lines.append(f"grading.{k}: {json.dumps(v, ensure_ascii=False)}")
    elif grading is not None:
        lines.append(f"grading: {json.dumps(grading, ensure_ascii=False)}")

    for k in ("hasAllOfTheAbove", "hasNoneOfTheAbove"):
        if k in item:
            lines.append(f"{k}: {json.dumps(item[k])}")
            seen.add(k)

    if item.get("hint"):
        lines.append(f"hint: {item['hint']}")

    # Anything this renderer has not been taught about is still a claim the
    # learner sees. Empty values are dropped so an `imageUrls: []` does not sit
    # on every unit in the bundle.
    for k, v in item.items():
        if k in seen or v in (None, "", [], {}):
            continue
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    return "\n".join(lines)


def _quiz_units(name: str, text: str) -> list[Unit]:
    items = _quiz_items(name, _load_json(name, text))
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise UnitError(f"{name}: question {i + 1} is {type(item).__name__}")
        opts = _options(item)
        body = render_quiz(item)
        uid = f"Q{i + 1}"
        # sha1 of the ORIGINAL stem (spec §5.9): fixes.json must keep pointing
        # at the right item after a hygiene pass renumbers or reorders the file.
        # A stem-less item has nothing stable to hash, and hashing "" would give
        # every such item in the chapter the same id — so it falls back to its
        # own text. (Two items with the SAME stem do share an id on purpose:
        # they are the duplicate qc_lint reports, and a fix belongs to both.)
        stem = item.get("question") or item.get("stem") or ""
        # A malformed asset must be classified, not crash the chapter. A quiz
        # whose `question` is an object reached _sha1 and raised AttributeError
        # out of hashlib, which qc_lint's UnitError-only handler did not catch,
        # so one bad item ended the whole chapter in a traceback instead of a
        # lint finding.
        if not isinstance(stem, str):
            raise UnitError(f"{name}: question {i + 1} has a "
                            f"{type(stem).__name__} stem, expected text")
        out.append(Unit(uid=uid, artifact=name, text=body,
                        stable_id=_sha1(stem) if stem else _sha1(uid + "\n" + body),
                        meta={"kind": "quiz", "index": i,
                              "type": item.get("type") or "untyped",
                              "options": [o.get("text", "") for o in opts
                                          if isinstance(o, dict)],
                              "key_indexes": [j for j, o in enumerate(opts)
                                              if isinstance(o, dict)
                                              and o.get("isCorrect")]}))
    return out


# ---------------------------------------------------------------------------
# flashcards.json


def _card_list(name: str, data: Any) -> list[dict]:
    if isinstance(data, dict):
        for key in ("cards", "flashcards"):
            if isinstance(data.get(key), list):
                return data[key]
        raise UnitError(f"{name}: object with no card list (keys: {sorted(data)})")
    if isinstance(data, list):
        return data
    raise UnitError(f"{name}: expected a list of cards, got {type(data).__name__}")


def card_side_text(value) -> str | None:
    """A flashcard side as plain text, or None if its shape is not understood.

    NotebookLM changed its flashcard format around 2026-10-01: a side used to
    be a string and is now `{"flashcardContentBlock": [{"type": "text",
    "content": "…"}]}`. Every set generated since then — 14 chapters,
    including five the owner studies in week 1 — came back that way, and both
    the app and this module read the dict as if it were the text. Only `text`
    blocks have been seen; a block of any other type is not guessed at, so the
    caller can refuse instead of dropping it silently.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("flashcardContentBlock"), list):
        parts = []
        for block in value["flashcardContentBlock"]:
            if not isinstance(block, dict) or block.get("type") != "text":
                return None
            parts.append(str(block.get("content") or ""))
        return "\n".join(parts)
    return None


def flatten_flashcards(data):
    """(data, sides changed) with every card side as plain text, in place.

    Raises UnitError on a side whose shape card_side_text does not know: a
    rewrite that dropped an image or a table block would publish a card with
    part of its answer missing, and nothing downstream could tell.
    """
    changed = 0
    for i, card in enumerate(_card_list("flashcards.json", data)):
        if not isinstance(card, dict):
            continue
        for side in ("front", "back"):
            if side not in card or isinstance(card[side], str):
                continue
            text = card_side_text(card[side])
            if text is None:
                raise UnitError(f"flashcards.json: card {i + 1} {side} has a "
                                f"shape this code does not understand: "
                                f"{json.dumps(card[side], ensure_ascii=False)[:120]}")
            card[side] = text
            changed += 1
    return data, changed


def _card_units(name: str, text: str) -> list[Unit]:
    cards = _card_list(name, _load_json(name, text))
    out = []
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            raise UnitError(f"{name}: card {i + 1} is {type(card).__name__}")
        front = card_side_text(card.get("front", ""))
        back = card_side_text(card.get("back", ""))
        if front is None or back is None:
            raise UnitError(f"{name}: card {i + 1} has a side this code does "
                            f"not understand")
        lines = [f"front: {front}", f"back: {back}"]
        for k, v in card.items():
            if k not in ("front", "back") and v not in (None, "", [], {}):
                lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        body = "\n".join(lines)
        uid = f"C{i + 1}"
        out.append(Unit(uid=uid, artifact=name, text=body,
                        stable_id=_sha1(front) if front
                        else _sha1(uid + "\n" + body),
                        meta={"kind": "card", "index": i,
                              "front": front, "back": back}))
    return out


# ---------------------------------------------------------------------------
# sketchy_*.txt — the released plate legends


def _legend_units(name: str, text: str) -> list[Unit]:
    """One unit per numbered row, plus the "מספרים לבחינה" chip line.

    Row 0 is the block above the first numbered row (title, השלט, המקום). It is
    a unit because it is prose the app renders, and because 8 of the 44 legends
    in _album/ predate the numbered format and are prose only — for those,
    each paragraph becomes a row so nothing in the asset goes unjudged.
    """
    out: list[Unit] = []

    # `row_id`, not `row`: the enclosing function has a local `row` of its own,
    # assigned further down, and scripts/_check_scope.py — the guard that exists
    # because a read-before-assignment cost chapters 15-17 — walks a function
    # together with the functions nested inside it. A parameter that shadows an
    # outer local reads there as exactly the bug it hunts, and a guard that
    # cries wolf is a guard nobody runs.
    def add(row_id: str, body: str, meta: dict) -> None:
        body = body.rstrip()
        if not body.strip():
            return
        uid = f"L:{name}:{row_id}"
        out.append(Unit(uid=uid, artifact=name, text=body,
                        stable_id=_sha1(uid + "\n" + body),
                        meta={"kind": "legend", "file": name, "row": row_id,
                              **meta}))

    # The chip block is the last two lines — "לא צויר:" (drawn-or-not, a
    # production note) and "מספרים לבחינה:" (the numbers he is told to memorise,
    # which qc_lint checks against the pages cited in the same legend).
    tail_start, chip_line = len(text), ""
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(_NUMBERS_CHIP):
            chip_line = stripped
            tail_start = min(tail_start, pos)
        elif stripped.startswith("לא צויר"):
            tail_start = min(tail_start, pos)
        pos += len(line)

    rows = [m for m in _LEGEND_ROW_RE.finditer(text) if m.start() < tail_start]
    if rows:
        add("0", text[:rows[0].start()], {"heading": True})
        seen: dict[str, int] = {}
        for k, m in enumerate(rows):
            end = rows[k + 1].start() if k + 1 < len(rows) else tail_start
            row = m.group(1)
            seen[row] = seen.get(row, 0) + 1
            if seen[row] > 1:                    # a legend that repeats "12."
                row = f"{row}#{seen[row]}"
            add(row, text[m.start():max(end, m.end())],
                {"symbol": m.group(2).split(":")[0].strip()})
    else:
        for i, para in enumerate(p for p in re.split(r"\n\s*\n", text[:tail_start])
                                 if p.strip()):
            add(str(i + 1), para, {"prose": True})

    tail = text[tail_start:].strip()
    if tail:
        uid = f"L:{name}:numbers"
        chips = [c.strip() for c in chip_line.split(":", 1)[-1].split("·")
                 if c.strip()] if chip_line else []
        out.append(Unit(uid=uid, artifact=name, text=tail,
                        stable_id=_sha1(uid + "\n" + tail),
                        meta={"kind": "legend_numbers", "file": name,
                              "row": "numbers", "chips": chips}))
    return out


# ---------------------------------------------------------------------------


def is_judged_asset(name: str) -> bool:
    """True for the assets v1 judges (spec §3). `*.quiz.md`, narration and the
    video are out of scope, and `sketchy_main.quiz.md` must not be mistaken for
    a legend — hence the anchored `.txt` match."""
    return name in ARTIFACT_ORDER or bool(LEGEND_RE.match(name))


def units_from_asset(name: str, text: str) -> list[Unit]:
    """Units of one asset. Unknown or out-of-scope asset names give []."""
    if name == "study_guide.md":
        return _guide_units(name, text)
    if name == "facts.json":
        return _facts_units(name, text)
    if name == "quiz.json":
        return _quiz_units(name, text)
    if name == "flashcards.json":
        return _card_units(name, text)
    if LEGEND_RE.match(name):
        return _legend_units(name, text)
    return []


def units(assets: dict[str, str]) -> list[Unit]:
    """Every judgeable unit of a chapter, in a fixed artifact order.

    `assets` is release asset name -> its text, exactly as downloaded.
    """
    out: list[Unit] = []
    names = [n for n in ARTIFACT_ORDER if n in assets]
    names += sorted(n for n in assets if LEGEND_RE.match(n))
    for name in names:
        out.extend(units_from_asset(name, assets[name]))
    counts = Counter(u.uid for u in out)
    dupes = [uid for uid, n in counts.items() if n > 1]
    if dupes:
        # A duplicate uid would silently merge two coverage rows into one and
        # let a unit through unjudged.
        raise UnitError(f"duplicate unit ids: {sorted(dupes)[:5]}")
    return out


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("directory", help="a directory holding a chapter's assets")
    ap.add_argument("--uid", help="print this unit's text instead of the list")
    a = ap.parse_args()
    d = Path(a.directory)
    assets = {p.name: p.read_text(encoding="utf-8") for p in sorted(d.iterdir())
              if p.is_file() and is_judged_asset(p.name)}
    if not assets:
        print(f"no judged assets in {d}")
        return 1
    us = units(assets)
    if a.uid:
        hit = next((u for u in us if u.uid == a.uid), None)
        if not hit:
            print(f"no unit {a.uid!r}")
            return 1
        print(f"# {hit.uid}  [{hit.artifact}]  {hit.stable_id[:8]}")
        print(hit.text)
        return 0
    for u in us:
        print(f"{u.uid:<44} {u.artifact:<18} {u.stable_id[:8]} {len(u.text):>6}")
    print(f"-- {len(us)} units from {len(assets)} assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
