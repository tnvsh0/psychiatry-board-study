"""
SM-2 (Anki-style) Spaced Repetition algorithm for the course flashcards.

Cards are identified by a stable hash of (chapter_index, question_text).
Per-card state lives in `schedule/flashcard_progress.json` and is persisted
via the same GitHub Contents API used for `progress.json`.

Card record schema (one per card_id):
    {
      "from_chapter": 5,            # source chapter index
      "ease": 2.5,                  # SM-2 ease factor (>= MIN_EASE)
      "interval_days": 7,           # days between this review and next
      "due_date": "2026-06-02",     # ISO date for next review
      "review_count": 3,            # consecutive correct reviews
      "last_grade": 2,              # 0-3 last user-grade
      "last_seen": "2026-05-26T12:00:00",
      "question_preview": "What ..."  # for debugging only
    }

Grades:
    0 = Again — failed; reset interval to 1, ease -= 0.20
    1 = Hard  — partial; interval × 1.2,    ease -= 0.15
    2 = Good  — correct; standard progression (1 / 6 / interval * ease)
    3 = Easy  — easy correct; interval × ease × 1.3, ease += 0.15
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, date

INITIAL_EASE = 2.5
MIN_EASE = 1.3

GRADE_AGAIN = 0
GRADE_HARD = 1
GRADE_GOOD = 2
GRADE_EASY = 3

GRADE_LABELS = {
    GRADE_AGAIN: ("😵 Again", "פספסתי לחלוטין"),
    GRADE_HARD: ("😬 Hard", "התקשיתי"),
    GRADE_GOOD: ("🙂 Good", "ידעתי"),
    GRADE_EASY: ("😄 Easy", "ידעתי בקלות"),
}


def card_id(chapter_index: int, question: str) -> str:
    """Stable 16-char hash for a card. Truncating to 200 chars of question
    so trailing whitespace/formatting changes don't break the identity."""
    raw = f"{chapter_index}:{(question or '').strip()[:200]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def new_card_state(chapter_index: int, question: str) -> dict:
    """Initial state for a card never seen before — due immediately (today)."""
    today = date.today().isoformat()
    return {
        "from_chapter": chapter_index,
        "ease": INITIAL_EASE,
        "interval_days": 0,
        "due_date": today,
        "review_count": 0,
        "last_grade": None,
        "last_seen": None,
        "question_preview": (question or "")[:80],
    }


def update_card(card: dict, grade: int) -> dict:
    """Apply a grade (0=Again, 1=Hard, 2=Good, 3=Easy) and return new state.

    Pure function — does not mutate the input. Always returns a new dict.
    """
    ease = float(card.get("ease", INITIAL_EASE))
    rc = int(card.get("review_count", 0))
    interval = int(card.get("interval_days", 0))

    if grade == GRADE_AGAIN:
        interval = 1
        ease = max(MIN_EASE, ease - 0.20)
        rc = 0
    elif grade == GRADE_HARD:
        interval = max(1, int(round(max(interval, 1) * 1.2)))
        ease = max(MIN_EASE, ease - 0.15)
        rc += 1
    elif grade == GRADE_GOOD:
        if rc == 0:
            interval = 1
        elif rc == 1:
            interval = 6
        else:
            interval = max(1, int(round(interval * ease)))
        rc += 1
    elif grade == GRADE_EASY:
        if rc == 0:
            interval = 4
        else:
            interval = max(1, int(round(interval * ease * 1.3)))
        ease += 0.15
        rc += 1
    else:
        # Unknown grade — leave state unchanged
        return dict(card)

    new_due = (date.today() + timedelta(days=interval)).isoformat()
    return {
        **card,
        "ease": round(ease, 3),
        "interval_days": interval,
        "due_date": new_due,
        "review_count": rc,
        "last_grade": grade,
        "last_seen": datetime.now().isoformat(timespec="seconds"),
    }


def is_due(card: dict, today: date | None = None) -> bool:
    """Whether the card is due for review today or earlier."""
    today = today or date.today()
    try:
        due = date.fromisoformat(card.get("due_date", today.isoformat()))
    except Exception:
        return True  # malformed → due
    return due <= today


def days_until_due(card: dict, today: date | None = None) -> int:
    """Negative or zero = overdue/due today. Positive = days away."""
    today = today or date.today()
    try:
        due = date.fromisoformat(card.get("due_date", today.isoformat()))
    except Exception:
        return 0
    return (due - today).days


def collect_due_cards(progress: dict, today: date | None = None) -> list[tuple[str, dict]]:
    """Return [(card_id, card_state)] for all cards due today, oldest-due first.

    Cards never seen (review_count == 0) come BEFORE overdue review cards,
    so the user works through new material before the review queue.
    """
    today = today or date.today()
    due_new = []
    due_review = []
    for cid, st in progress.items():
        if not isinstance(st, dict):
            continue
        if not is_due(st, today):
            continue
        if int(st.get("review_count", 0)) == 0:
            due_new.append((cid, st))
        else:
            due_review.append((cid, st))
    # Sort review cards by how overdue they are (most overdue first)
    due_review.sort(key=lambda x: days_until_due(x[1], today))
    return due_new + due_review


def stats(progress: dict, today: date | None = None) -> dict:
    """Aggregate stats for a Daily Review banner."""
    today = today or date.today()
    n_total = 0
    n_due_today = 0
    n_new = 0
    n_mature = 0  # interval >= 21 days
    for st in progress.values():
        if not isinstance(st, dict):
            continue
        n_total += 1
        if is_due(st, today):
            n_due_today += 1
        if int(st.get("review_count", 0)) == 0:
            n_new += 1
        if int(st.get("interval_days", 0)) >= 21:
            n_mature += 1
    return {
        "total": n_total,
        "due_today": n_due_today,
        "new": n_new,
        "mature": n_mature,
    }
