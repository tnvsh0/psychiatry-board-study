"""
Psychiatry Board Study — Moodle-style course site.

Phases → Days → Per-day artifacts (audio, video, infographics, study guide,
flashcards, quiz). Tracks per-day completion. Daily ntfy reminder sends a
link to the current day's page.

Run locally:    streamlit run app.py
Deploy:         Cloud Run (Dockerfile in this dir)

Data sources:
  - schedule/schedule.json  (curriculum)
  - schedule/symbols.json   (visual legend per day)
  - schedule/progress.json  (committed back to git on mark-complete)
  - GitHub Releases for each day's artifacts
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone, date
from pathlib import Path

import requests
import streamlit as st

# Import SR engine from scripts/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from sr_engine import (  # noqa: E402
    card_id, new_card_state, update_card, is_due, collect_due_cards, stats,
    GRADE_AGAIN, GRADE_HARD, GRADE_GOOD, GRADE_EASY, GRADE_LABELS,
)

# ── Configuration ───────────────────────────────────────────────────────────
GITHUB_REPO = os.environ.get("GH_REPO", "tnvsh0/psychiatry-board-study")
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{BRANCH}"
API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"

# Local path (used when running from inside the repo)
LOCAL_ROOT = Path(__file__).resolve().parent.parent
SCHEDULE_LOCAL = LOCAL_ROOT / "schedule" / "schedule.json"
SYMBOLS_LOCAL = LOCAL_ROOT / "schedule" / "symbols.json"
PROGRESS_LOCAL = LOCAL_ROOT / "schedule" / "progress.json"
PALETTE_LOCAL = LOCAL_ROOT / "schedule" / "phase_palette.json"
SR_PROGRESS_LOCAL = LOCAL_ROOT / "schedule" / "flashcard_progress.json"

# Fallback palette if file missing
_DEFAULT_PALETTE_ENTRY = {"name": "Unknown", "hex": "#808080", "prompt": ""}


# Asset stems are pipeline-internal ("epidemiology", "dsm_criteria_sigecaps",
# "main", "criteria2"). Printing them raw put English snake_case headings in the
# middle of a Hebrew RTL page — and stems like "dsm_criteria_" rendered with a
# dangling underscore. Map what we know; humanise the rest.
_ASPECT_HE = {
    "main": "הלוח הראשי",
    "criteria": "קריטריונים",
    "dsm_criteria": "קריטריוני DSM",
    "diagnostic_criteria": "קריטריונים אבחנתיים",
    "epidemiology": "אפידמיולוגיה",
    "etiology": "אטיולוגיה",
    "treatment": "טיפול",
    "treatment_overview": "סקירת טיפול",
    "assessment_tools": "כלי הערכה",
    "medical_mimics": "חיקויים רפואיים",
    "suicidality_assessment": "הערכת אובדנות",
    "mixed_features_specifier": "מאפיינים מעורבים",
    "course": "מהלך המחלה",
    "comorbidity": "תחלואה נלווית",
    "sigecaps": "SIGECAPS",
    "high_yield": "תשואה גבוהה",
}


def sketchy_heading(stem: str) -> str:
    """A readable Hebrew heading for an asset stem."""
    s = stem.strip("_")
    if s in _ASPECT_HE:
        return _ASPECT_HE[s]
    # compound stems: "dsm_criteria_sigecaps" -> "קריטריוני DSM · SIGECAPS"
    for base, he in sorted(_ASPECT_HE.items(), key=lambda kv: -len(kv[0])):
        if s.startswith(base + "_"):
            rest = s[len(base) + 1:]
            return f"{he} · {_ASPECT_HE.get(rest, rest.replace('_', ' '))}"
    if s.startswith("main") or s.startswith("criteria"):
        n = s[-1] if s[-1].isdigit() else ""
        base = "הלוח הראשי" if s.startswith("main") else "קריטריונים"
        return f"{base} {n}".strip()
    return s.replace("_", " ")


# ── Data loading (cached) ───────────────────────────────────────────────────
def _gh_fetch_json(path: str, fallback: dict | None = None) -> dict:
    """Fetch a JSON file from the (possibly private) repo via Contents API."""
    if GITHUB_TOKEN:
        # Contents API works for private repos
        import base64
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GITHUB_TOKEN}",
        }
        r = requests.get(f"{API_BASE}/contents/{path}?ref={BRANCH}", headers=headers, timeout=15)
        if r.status_code == 200:
            content = r.json().get("content", "")
            try:
                return json.loads(base64.b64decode(content).decode("utf-8"))
            except Exception:
                pass
    # Try raw (works for public repos or if token-less)
    r = requests.get(f"{RAW_BASE}/{path}", timeout=15)
    if r.status_code == 200:
        return r.json()
    if fallback is not None:
        return fallback
    r.raise_for_status()
    return {}


@st.cache_data(ttl=300)
def load_schedule() -> dict:
    if SCHEDULE_LOCAL.exists():
        return json.loads(SCHEDULE_LOCAL.read_text(encoding="utf-8"))
    return _gh_fetch_json("schedule/schedule.json")


@st.cache_data(ttl=300)
def load_symbols() -> dict:
    if SYMBOLS_LOCAL.exists():
        return json.loads(SYMBOLS_LOCAL.read_text(encoding="utf-8"))
    return _gh_fetch_json("schedule/symbols.json")


@st.cache_data(ttl=300)
def load_palette() -> dict:
    """Phase palette (hex + prompt) per phase number 1-10."""
    if PALETTE_LOCAL.exists():
        return json.loads(PALETTE_LOCAL.read_text(encoding="utf-8"))
    return _gh_fetch_json("schedule/phase_palette.json", fallback={})


def phase_entry(phase: int, palette: dict) -> dict:
    """Lookup a palette entry safely. Strip the _comment key."""
    entry = palette.get(str(phase)) or palette.get(phase)
    if isinstance(entry, dict):
        return entry
    return dict(_DEFAULT_PALETTE_ENTRY)


@st.cache_data(ttl=300)
def fetch_text(url: str) -> str:
    """Fetch a release-asset text file with Bearer auth (private repo).
    Returns empty string on failure."""
    if not url:
        return ""
    try:
        r = requests.get(url, headers=_octet_headers(), timeout=30, allow_redirects=True)
        if r.ok:
            return r.text
    except Exception:
        pass
    return ""


@st.cache_data(ttl=30)
def load_sr_progress() -> dict:
    """Flashcard SR progress (per-card state)."""
    if SR_PROGRESS_LOCAL.exists():
        try:
            return json.loads(SR_PROGRESS_LOCAL.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return _gh_fetch_json("schedule/flashcard_progress.json", fallback={})


def _push_sr_progress_to_github_background(progress_snapshot: dict) -> None:
    """Background-thread GitHub Contents API push. Logs but never raises so
    the foreground UI thread is never blocked or crashed by network issues."""
    if not GITHUB_TOKEN:
        return
    import base64
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }
    file_path = "schedule/flashcard_progress.json"
    try:
        sha = None
        r = requests.get(f"{API_BASE}/contents/{file_path}", headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json().get("sha")
        content_str = json.dumps(progress_snapshot, ensure_ascii=False, indent=2)
        payload = {
            "message": f"Flashcard SR: review at {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "content": base64.b64encode(content_str.encode("utf-8")).decode(),
            "branch": BRANCH,
        }
        if sha:
            payload["sha"] = sha
        requests.put(f"{API_BASE}/contents/{file_path}", headers=headers, json=payload, timeout=15)
    except Exception:
        # Background — silent failure. Local file is already saved.
        pass


def save_sr_progress(progress: dict) -> bool:
    """Save flashcard SR progress. LOCAL-FIRST for instant UX, then GitHub
    push is fired in a background thread so the grade-click feels immediate.

    Returns True if the local write succeeded. GitHub sync happens
    asynchronously; on next page load the local file is the source of truth
    (and it'll already include the latest grade)."""
    # 1. Local write — fast, atomic, immediate. This is the source of truth
    #    during the session.
    try:
        SR_PROGRESS_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        SR_PROGRESS_LOCAL.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        st.warning(f"שגיאת שמירה מקומית: {e}")
        return False

    # 2. Background push to GitHub — fire and forget. The UI doesn't wait.
    if GITHUB_TOKEN:
        import threading
        # Copy the dict so the thread isn't affected by subsequent mutations.
        snapshot = dict(progress)
        t = threading.Thread(
            target=_push_sr_progress_to_github_background,
            args=(snapshot,),
            daemon=True,
        )
        t.start()

    return True


@st.cache_data(ttl=60)
def load_progress() -> dict:
    if PROGRESS_LOCAL.exists():
        return json.loads(PROGRESS_LOCAL.read_text(encoding="utf-8"))
    return _gh_fetch_json(
        "schedule/progress.json",
        fallback={"completed_days": [], "current_day_index": 1, "last_studied": None},
    )


def save_progress(progress: dict) -> bool:
    """Save progress.json via GitHub Contents API (works on Cloud Run with GH_TOKEN)."""
    if not GITHUB_TOKEN:
        # Fallback: local write (useful for local dev)
        PROGRESS_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        PROGRESS_LOCAL.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    import base64
    # Get current SHA (if file exists)
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }
    file_path = "schedule/progress.json"
    sha = None
    r = requests.get(f"{API_BASE}/contents/{file_path}", headers=headers, timeout=10)
    if r.status_code == 200:
        sha = r.json().get("sha")
    # Commit
    content_str = json.dumps(progress, ensure_ascii=False, indent=2)
    payload = {
        "message": f"Update progress: day {progress.get('current_day_index', '?')}",
        "content": base64.b64encode(content_str.encode("utf-8")).decode(),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(f"{API_BASE}/contents/{file_path}", headers=headers, json=payload, timeout=15)
    if r.status_code in (200, 201):
        return True
    st.warning(f"GitHub commit failed: {r.status_code} {r.text[:200]}")
    return False


@st.cache_data(ttl=3600)
def get_lectures() -> dict:
    """Recorded academic lectures, keyed by chapter number as a string.

    Fetched from the repo the same way the schedule is, NOT read off local
    disk. The Cloud Run image copies only app.py and sr_engine.py — everything
    under schedule/ arrives live through the Contents API — so a local-only
    read returns nothing in production and the lectures would simply never
    appear, on a page that gave no sign anything was missing.

    A lecture is filed under every chapter it is relevant to, not only its main
    one: the Tourette/ADHD/OCD talk belongs to all three.
    """
    data = _gh_fetch_json("schedule/lectures.json", fallback=None)
    if not data:
        f = LOCAL_ROOT / "schedule" / "lectures.json"
        if not f.exists():
            return {}
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return data.get("lectures_by_chapter", {})


@st.cache_data(ttl=300)
def get_release_assets(tag: str) -> dict | None:
    """Fetch release assets for a given day tag.

    Returns {asset_name: asset_api_url}. We use the GH API 'url' field rather
    than 'browser_download_url' so private-repo assets can be downloaded with
    a Bearer token via Accept: application/octet-stream.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    r = requests.get(f"{API_BASE}/releases/tags/{tag}", headers=headers, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    return {a["name"]: a["url"] for a in data.get("assets", [])}


# Cache directory for downloaded assets — keep them between page reloads
ASSET_CACHE_DIR = Path(tempfile.gettempdir()) / "psychiatry_streamlit_assets"
ASSET_CACHE_DIR.mkdir(exist_ok=True)


def _octet_headers() -> dict:
    """Bearer-auth headers for downloading a single asset binary."""
    h = {"Accept": "application/octet-stream"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_asset_local(asset_name: str, asset_url: str) -> str | None:
    """Download a release asset to a local cache file. Returns the local path
    as a string for use with st.audio / st.video / st.image. Returns None on
    failure. Cached for an hour by Streamlit; the file itself stays on disk
    until the OS cleans /tmp."""
    if not asset_url:
        return None
    cache_key = hashlib.sha1(asset_url.encode("utf-8")).hexdigest()[:16] + "_" + asset_name
    local_path = ASSET_CACHE_DIR / cache_key
    if local_path.exists() and local_path.stat().st_size > 0:
        return str(local_path)
    try:
        with requests.get(asset_url, headers=_octet_headers(), timeout=180, stream=True, allow_redirects=True) as r:
            if not r.ok:
                return None
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        return str(local_path) if local_path.stat().st_size > 0 else None
    except Exception:
        return None


def slugify(text: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower()[:60]


def day_tag(day: dict) -> str:
    return f"study-{day['index']:03d}-{slugify(day.get('title_en', ''))}"


# ── Flashcard SR rendering ─────────────────────────────────────────────────
def _extract_qa(card_raw: dict) -> tuple[str, str]:
    """Pull (question, answer) from a flashcard JSON record."""
    q = card_raw.get("front") or card_raw.get("question") or card_raw.get("q", "")
    a = card_raw.get("back") or card_raw.get("answer") or card_raw.get("a", "")
    return q, a


def render_chapter_flashcards_sr(chapter_idx: int, cards_raw: list) -> None:
    """Render the chapter's flashcards as a Spaced-Repetition session.

    On entry, computes the review queue = cards from this chapter that are
    DUE today (including new cards never seen). User reveals + grades each.
    Grades update flashcard_progress.json and recompute the queue.
    """
    sr_state = load_sr_progress()
    if not isinstance(sr_state, dict):
        sr_state = {}

    # Index cards from THIS chapter into SR state if absent
    chapter_card_index = {}  # card_id -> raw card dict
    for c in cards_raw:
        q, _ = _extract_qa(c)
        if not q:
            continue
        cid = card_id(chapter_idx, q)
        chapter_card_index[cid] = c
        if cid not in sr_state:
            sr_state[cid] = new_card_state(chapter_idx, q)

    # Build the review queue: cards from THIS chapter that are due today
    today = date.today()
    queue = [cid for cid in chapter_card_index if is_due(sr_state[cid], today)]
    # Order: new first, then by overdueness
    queue.sort(key=lambda cid: (
        int(sr_state[cid].get("review_count", 0)) > 0,   # new first
        sr_state[cid].get("due_date", ""),                # then by due-date asc
    ))

    if not queue:
        # All chapter cards are scheduled for the future
        next_due_cid = min(chapter_card_index, key=lambda c: sr_state[c].get("due_date", "9999"))
        next_date = sr_state[next_due_cid].get("due_date", "?")
        st.success(f"🎉 סיימת היום את כל הכרטיסיות של פרק זה. החזרה הבאה: {next_date}.")
        return

    # Session state per chapter
    pos_key = f"fc_pos_c{chapter_idx}"
    rev_key = f"fc_reveal_c{chapter_idx}"
    if pos_key not in st.session_state:
        st.session_state[pos_key] = 0
    if rev_key not in st.session_state:
        st.session_state[rev_key] = False

    pos = st.session_state[pos_key]
    if pos >= len(queue):
        st.success(f"🎉 סיימת את {len(queue)} הכרטיסיות שהיו בתור היום. רענן את הדף כדי להמשיך.")
        if st.button("התחל מחדש", key=f"fc_restart_c{chapter_idx}"):
            st.session_state[pos_key] = 0
            st.session_state[rev_key] = False
            st.rerun()
        return

    cid = queue[pos]
    card = sr_state[cid]
    q, a = _extract_qa(chapter_card_index[cid])
    revealed = st.session_state[rev_key]

    # Header
    rc = int(card.get("review_count", 0))
    ease = float(card.get("ease", 2.5))
    interval = int(card.get("interval_days", 0))
    new_flag = " · 🆕 כרטיסיה חדשה" if rc == 0 else f" · interval {interval}d · ease {ease:.1f}"
    st.caption(f"כרטיסיה {pos+1} / {len(queue)} בתור היום{new_flag}")

    # Card body — show question always; answer only when revealed
    st.markdown(
        f'<div dir="rtl" style="text-align: right; padding: 22px; '
        f'border: 2px solid #ccc; border-radius: 12px; '
        f'background: rgba(0,0,0,0.03); font-size: 1.15em; line-height: 1.6; '
        f'margin: 10px 0;">'
        f'<div style="color:#888; font-size:0.85em; margin-bottom:8px;">שאלה</div>'
        f'<div>{q}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if revealed:
        st.markdown(
            f'<div dir="rtl" style="text-align: right; padding: 22px; '
            f'border: 2px solid #4caf50; border-radius: 12px; '
            f'background: rgba(76,175,80,0.06); font-size: 1.10em; line-height: 1.6; '
            f'margin: 10px 0;">'
            f'<div style="color:#4caf50; font-size:0.85em; margin-bottom:8px;">תשובה</div>'
            f'<div>{a}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Controls
    if not revealed:
        if st.button("👁  חשוף תשובה", key=f"fc_reveal_btn_c{chapter_idx}", type="primary", use_container_width=True):
            st.session_state[rev_key] = True
            st.rerun()
    else:
        st.markdown("**איך זה הלך?**")
        cols = st.columns(4)
        for g, col in zip([GRADE_AGAIN, GRADE_HARD, GRADE_GOOD, GRADE_EASY], cols):
            with col:
                en, he = GRADE_LABELS[g]
                btn_type = "secondary" if g != GRADE_GOOD else "primary"
                if st.button(en + "\n" + he, key=f"fc_grade_c{chapter_idx}_g{g}", use_container_width=True, type=btn_type):
                    # Apply grade
                    sr_state[cid] = update_card(card, g)
                    if save_sr_progress(sr_state):
                        st.session_state[pos_key] = pos + 1
                        st.session_state[rev_key] = False
                        # Invalidate cache so the next load reflects the new state
                        load_sr_progress.clear()
                        st.rerun()
                    else:
                        st.error("⚠ הגרייד לא נשמר ב-GitHub. נסה שוב.")


def render_daily_review_banner(progress_sr: dict) -> None:
    """Compact banner at the top of the dashboard showing how many cards
    are due today across all chapters."""
    s = stats(progress_sr)
    if s["total"] == 0:
        return
    due = s["due_today"]
    if due > 0:
        st.info(
            f"🃏 **{due} כרטיסיות לחזרה היום** מתוך {s['total']} סה\"כ "
            f"({s['new']} חדשות · {s['mature']} בוגרות [≥21 ימים])"
        )
    else:
        st.caption(f"🃏 {s['total']} כרטיסיות במאגר ({s['mature']} בוגרות) · אין מה לחזור היום")


# ── UI components ───────────────────────────────────────────────────────────
def render_phase_header(day: dict, palette: dict) -> None:
    """Colored bar at the top of a day page indicating the phase identity."""
    phase = day.get("phase", 0)
    entry = phase_entry(phase, palette)
    color = entry.get("hex", "#808080")
    pname = entry.get("name", day.get("phase_name", ""))
    st.markdown(
        f"""<div style="
            background: linear-gradient(90deg, {color}cc, {color}33);
            border-left: 6px solid {color};
            padding: 8px 14px; margin: 0 0 12px 0; border-radius: 4px;
            color: #fff; font-weight: 600; letter-spacing: 0.5px;">
            Phase {phase} — {pname}
        </div>""",
        unsafe_allow_html=True,
    )


def render_progress_overall(schedule, progress):
    total = len(schedule["days"])
    done = len(set(progress.get("completed_days", [])))
    pct = done / total * 100 if total else 0
    st.metric("התקדמות כללית", f"{done}/{total}", f"{pct:.0f}%")
    st.progress(done / total if total else 0)


def render_phase_sidebar(schedule, progress, current_phase=None):
    """Group days by phase and show in sidebar with completion."""
    days = schedule["days"]
    completed = set(progress.get("completed_days", []))
    current = progress.get("current_day_index", 1)

    phases = defaultdict(list)
    for d in days:
        phases[d.get("phase", 0)].append(d)

    phase_names = schedule.get("metadata", {}).get("phase_structure", {})

    selected_day_idx = None

    for phase_num in sorted(phases.keys()):
        phase_days = phases[phase_num]
        phase_name = phase_names.get(str(phase_num), f"Phase {phase_num}")
        phase_done = sum(1 for d in phase_days if d["index"] in completed)
        phase_total = len(phase_days)

        with st.sidebar.expander(
            f"**Phase {phase_num}** — {phase_done}/{phase_total}",
            expanded=(phase_num == current_phase or (1 in [d["index"] for d in phase_days] and not current_phase)),
        ):
            st.caption(phase_name[:80])
            for d in phase_days:
                icon = "✅" if d["index"] in completed else ("🔵" if d["index"] == current else "⚪")
                label = f"{icon} יום {d['index']}: {d.get('title_he', d.get('title_en', ''))[:40]}"
                if st.button(label, key=f"day_btn_{d['index']}", use_container_width=True):
                    selected_day_idx = d["index"]
    return selected_day_idx


def _extract_source_map(study_guide_md: str) -> str:
    """Extract the '## 0. מפת המקורות' section from the study guide markdown.
    Returns the section body (including its table) or '' if not found."""
    if not study_guide_md:
        return ""
    lines = study_guide_md.splitlines()
    start = None
    for i, ln in enumerate(lines):
        # Match the source-map heading (allow variations)
        if ln.lstrip().startswith("#") and ("מפת המקורות" in ln or "source map" in ln.lower() or "page reference" in ln.lower()):
            start = i
            break
    if start is None:
        return ""
    # Collect until the next heading of same-or-higher level (## or #)
    body = []
    for ln in lines[start + 1:]:
        if ln.lstrip().startswith("## ") or (ln.startswith("# ") and body):
            break
        body.append(ln)
    return "\n".join(body).strip()


def _merge_pages(tokens) -> str:
    """Turn raw page tokens ('68', '68-70', '71-73', noise like '(מקור: הקשר רחב)')
    into a clean, deduped, range-merged string e.g. '35, 68-79'. Consecutive pages
    collapse into a range, no page repeats, and non-numeric notes are dropped."""
    import re
    pages: set[int] = set()
    for tok in tokens:
        for m in re.finditer(r"(\d+)\s*[-–—]\s*(\d+)|(\d+)", str(tok)):
            if m.group(1) and m.group(2):
                a, b = int(m.group(1)), int(m.group(2))
                if a <= b and (b - a) < 1000:
                    pages.update(range(a, b + 1))
            elif m.group(3):
                pages.add(int(m.group(3)))
    if not pages:
        return ""
    sp = sorted(pages)
    runs, start, prev = [], sp[0], sp[0]
    for p in sp[1:]:
        if p == prev + 1:
            prev = p
        else:
            runs.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = p
    runs.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(runs)


def _condense_source_map(table_md: str) -> dict:
    """Collapse the per-subtopic source-map table into a per-book page list.
    Returns {book_name: 'pages, ...'} — a compact reading list, not the full
    per-subtopic detail (that stays inside the study guide)."""
    import re
    rows = [ln for ln in table_md.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        return {}

    def cells(row):
        # strip leading/trailing pipe, split, strip each cell of markdown bold
        parts = [c.strip().strip("*").strip() for c in row.strip().strip("|").split("|")]
        return parts

    header = cells(rows[0])
    # column 0 = subtopic; columns 1.. = books. Clean "עמ'" suffix.
    books = [h.replace("עמ'", "").strip() for h in header[1:]]
    # rows[1] is the markdown separator (---); data starts at rows[2]
    data_rows = rows[2:]

    per_book = {b: [] for b in books}
    for row in data_rows:
        c = cells(row)
        for bi, book in enumerate(books):
            cell = c[bi + 1] if (bi + 1) < len(c) else ""
            if not cell or cell in ("-", "—", "–"):
                continue
            for tok in cell.split(","):
                tok = tok.strip()
                if tok and tok not in ("-", "—", "–"):
                    per_book[book].append(tok)

    # Expand every token to individual pages, dedupe, and re-merge consecutive
    # pages into ranges so the list is clean (no '68, 68-70'; no '71, 72, 73').
    #
    # Exception: the Synopsis edition in the notebook has no printed page
    # numbers, so its column holds chapter references ('ch.19 Personality
    # Disorders'). Running those through _merge_pages would mine the digits out
    # and render 'ch.19' as "page 19" — a citation that points nowhere. Pass
    # chapter columns through verbatim instead.
    out = {}
    for book, toks in per_book.items():
        if "פרק" in book or "ch." in book.lower():
            seen = list(dict.fromkeys(t for t in toks if t))
            if seen:
                out[book] = ", ".join(seen)
            continue
        merged = _merge_pages(toks)
        if merged:
            out[book] = merged
    return out


def _render_recommended_reading(day: dict, idx: int) -> None:
    """Show a COMPACT reading list (book → pages) at the top of the chapter
    page. The full per-subtopic detail stays inside the study guide."""
    tag = day_tag(day)
    assets = get_release_assets(tag)
    reading = {}
    if assets:
        sg_url = assets.get("study_guide.md") or assets.get("report.md")
        if sg_url:
            sg_text = fetch_text(sg_url)
            source_map = _extract_source_map(sg_text)
            if source_map:
                reading = _condense_source_map(source_map)

    if reading:
        # Build one compact line per book
        items = "".join(
            f'<div style="margin:2px 0;"><b>{book}:</b> {pages}</div>'
            for book, pages in reading.items()
        )
        st.markdown(
            '<div dir="rtl" style="text-align:right; background:rgba(91,123,154,0.10); '
            'border-right:4px solid #5B7B9A; border-radius:6px; padding:12px 16px; margin:6px 0 14px 0;">'
            '<b>📚 עמודים מומלצים לקריאה</b>'
            f'<div style="font-size:0.95em; margin-top:6px;">{items}</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        # No study guide yet — show the focus blurb
        focus = day.get("prompt_focus", "")
        if focus:
            st.markdown(
                '<div dir="rtl" style="text-align:right; background:rgba(91,123,154,0.08); '
                'border-right:4px solid #ccc; border-radius:6px; padding:10px 14px; margin:6px 0 14px 0;">'
                f'<b>🎯 מיקוד הפרק</b><br>{focus}</div>',
                unsafe_allow_html=True,
            )


def parse_legend(text: str) -> dict:
    """Split a Sketchy legend into its parts.

    The .txt has real structure — a sign line, a place line, numbered rows and
    two summary lines — and the page was injecting the whole file into one HTML
    block. HTML collapses newlines, so twenty-nine numbered rows arrived as a
    single unbroken paragraph. That is the wall of text: a rendering bug, not
    too much writing.
    """
    out = {"sign": "", "place": "", "rows": [], "undrawn": "", "figures": ""}
    for raw in (text or "").splitlines():
        line = raw.strip().replace("⁨", "").replace("⁩", "")
        if not line:
            continue
        if line.startswith("השלט:"):
            out["sign"] = line[len("השלט:"):].strip()
        elif line.startswith("המקום:"):
            out["place"] = line[len("המקום:"):].strip()
        elif line.startswith("לא צויר:"):
            out["undrawn"] = line[len("לא צויר:"):].strip()
        elif line.startswith("מספרים לבחינה:"):
            out["figures"] = line[len("מספרים לבחינה:"):].strip()
        elif re.match(r"^\d+[.)]\s", line):
            body = re.sub(r"^\d+[.)]\s*", "", line)
            cue, _, rest = body.partition(":")
            fact, _, hook = rest.partition("—")
            out["rows"].append({"cue": cue.strip(), "fact": fact.strip(),
                                "hook": hook.strip()})
    return out


def render_legend_block(text: str) -> None:
    """The legend as a readable card: the pun first, the rows behind a click."""
    lg = parse_legend(text)
    if not lg["rows"]:
        st.markdown(f'<div dir="rtl" style="text-align:right">{text}</div>',
                    unsafe_allow_html=True)
        return

    if lg["sign"]:
        st.markdown(
            f'<div dir="rtl" style="text-align:right;background:rgba(240,180,41,.12);'
            f'border-right:4px solid #f0b429;padding:10px 16px;margin:6px 0;'
            f'border-radius:4px"><b>🔑 {lg["sign"]}</b></div>',
            unsafe_allow_html=True)
    if lg["place"]:
        st.markdown(f'<div dir="rtl" style="text-align:right;opacity:.72;'
                    f'font-size:.9em;padding:2px 16px 6px">{lg["place"]}</div>',
                    unsafe_allow_html=True)
    if lg["figures"]:
        chips = "".join(
            f'<span style="display:inline-block;background:rgba(0,120,255,.13);'
            f'border-radius:12px;padding:2px 10px;margin:3px 4px 0 0;'
            f'font-weight:600;direction:ltr">{c.strip()}</span>'
            for c in lg["figures"].split("·") if c.strip())
        st.markdown(f'<div dir="rtl" style="text-align:right;padding:4px 16px 8px">'
                    f'<span style="opacity:.7;font-size:.88em">מספרים לבחינה:</span>'
                    f'<br>{chips}</div>', unsafe_allow_html=True)

    with st.expander(f"מקרא הסצנה — {len(lg['rows'])} פריטים", expanded=False):
        # The FACT reads first and the explanation is muted under it. A row
        # whose element was already explained above carries no hook at all,
        # which is what makes a shared prop visible at a glance.
        html = ['<div dir="rtl" style="text-align:right">']
        for i, r in enumerate(lg["rows"], 1):
            hook = (f'<div style="opacity:.6;font-size:.87em">{r["hook"]}</div>'
                    if r["hook"] else "")
            html.append(
                f'<div style="padding:7px 0;border-bottom:1px solid '
                f'rgba(128,128,128,.14)">'
                f'<span style="opacity:.45;font-size:.85em">{i}.</span> '
                f'<b>{r["cue"]}</b> — {r["fact"]}{hook}</div>')
        html.append("</div>")
        st.markdown("".join(html), unsafe_allow_html=True)
        if lg["undrawn"]:
            st.caption(f"לא צויר: {lg['undrawn']}")


def render_day_page(day, symbols, progress, palette):
    """The main per-day content page."""
    idx = day["index"]
    title_he = day.get("title_he", day.get("title_en", ""))
    phase = day.get("phase", 0)
    phase_name = day.get("phase_name", "")

    completed = set(progress.get("completed_days", []))
    is_done = idx in completed

    render_phase_header(day, palette)
    st.markdown(f"# יום {idx}: {title_he}")
    st.caption(f"**Type**: {day.get('type', '')} | **Density**: {day.get('estimated_density', 'medium')}")

    # Recommended reading (source pages) — pulled from the study guide's
    # source-map section so the user knows exactly what to read in the books.
    _render_recommended_reading(day, idx)

    if is_done:
        st.success("✅ סומן כהושלם")
    else:
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("✅ סמן יום זה כהושלם", type="primary"):
                completed.add(idx)
                progress["completed_days"] = sorted(completed)
                progress["last_studied"] = datetime.now(timezone.utc).isoformat()
                # Advance to next un-completed day
                next_idx = idx + 1
                while next_idx in completed:
                    next_idx += 1
                progress["current_day_index"] = next_idx
                save_progress(progress)
                st.cache_data.clear()
                st.rerun()

    # Aspects + symbols
    with st.expander("📋 היבטים + סמלים לסקירה", expanded=False):
        st.markdown(f"**Aspects**: {', '.join(day.get('aspects', []))}")
        st.markdown(f"**Symbols**: {', '.join(day.get('symbols_needed', [])) or '(אין סמלים ייעודיים)'}")
        st.markdown(f"**מיקוד**: {day.get('prompt_focus', '')}")

    # Recorded lectures — deliberately BEFORE the "nothing generated yet" exit.
    # A chapter can have a real lecture from the owner's academic course while
    # its generated material is still queued, and that lecture is worth more
    # than the "come back in an hour" notice.
    lectures = get_lectures().get(str(day["index"]), [])
    if lectures:
        with st.expander(f"🎓 הרצאות מוקלטות ({len(lectures)})", expanded=False):
            for lec in lectures:
                label = lec["title"]
                if not lec.get("primary"):
                    label += "  ·  (שייכת בעיקר לפרק אחר)"
                st.markdown(f'<div dir="rtl" style="text-align:right;'
                            f'padding-top:8px"><b>{label}</b>'
                            f'<span style="opacity:.5"> · {lec["size_mb"]} MB</span>'
                            f'</div>', unsafe_allow_html=True)
                st.video(lec["url"])

    # Artifacts
    tag = day_tag(day)
    assets = get_release_assets(tag)

    if not assets:
        st.warning(f"⏳ התוכן עדיין לא נוצר עבור יום זה. (tag={tag})")
        st.info("ה-bulk generation עדיין רץ ברקע, או שיום זה לא הוכן עדיין. חזור בעוד שעה.")
        return

    # Audio
    audio_url = assets.get("audio.mp3")
    if audio_url:
        st.markdown("#### 🎙 פודקאסט")
        local = fetch_asset_local("audio.mp3", audio_url)
        if local:
            st.audio(local)
        else:
            st.warning("לא הצלחתי לטעון את האודיו (בעיית הרשאה?).")

    # Video
    video_url = assets.get("video.mp4")
    if video_url:
        st.markdown("#### 🎬 וידאו")
        local = fetch_asset_local("video.mp4", video_url)
        if local:
            st.video(local)
        else:
            st.warning("לא הצלחתי לטעון את הוידאו (בעיית הרשאה?).")

    # Sketchy scenes (Gemini) — primary visual mnemonic + Hebrew legend
    sketchy_images = sorted(k for k in assets if k.startswith("sketchy_") and k.endswith(".png"))
    if sketchy_images:
        st.markdown("### 🎨 סצנות Sketchy")
        for name in sketchy_images:
            aspect = name.replace("sketchy_", "").replace(".png", "")
            caption_name = f"sketchy_{aspect}.txt"
            caption_url = assets.get(caption_name)

            st.markdown(f"#### {sketchy_heading(aspect)}")
            # Image — full width, centered. Limit displayed width so it doesn't
            # become huge on wide screens.
            local_img = fetch_asset_local(name, assets[name])
            if local_img:
                left, mid, right = st.columns([1, 4, 1])
                with mid:
                    st.image(local_img, use_container_width=True)
            else:
                st.warning(f"לא הצלחתי לטעון את התמונה {name}.")

            # Caption — below the image, RTL Hebrew narrative
            if caption_url:
                caption_text = fetch_text(caption_url)
                if caption_text:
                    render_legend_block(caption_text)
                else:
                    st.caption("(לא הצלחתי לטעון את המקרא)")
            else:
                st.caption("(אין מקרא לתמונה זו)")
            st.divider()

    # Standard infographics (NotebookLM) — structured reference
    infographics = sorted(k for k in assets if k.startswith("infographic_") and k.endswith(".png"))
    if infographics:
        st.markdown("#### 🖼 אינפוגרפיקות")
        cols = st.columns(min(len(infographics), 3))
        for i, name in enumerate(infographics):
            with cols[i % len(cols)]:
                aspect = name.replace("infographic_", "").replace(".png", "")
                st.markdown(f"**{sketchy_heading(aspect)}**")
                local_img = fetch_asset_local(name, assets[name])
                if local_img:
                    st.image(local_img)
                else:
                    st.caption("(לא הצלחתי לטעון)")

    # Study guide — prefer the new focused study_guide.md (from ask),
    # fall back to legacy report.md (from old generate report)
    sg_url = assets.get("study_guide.md") or assets.get("report.md")
    sg_label = "📖 מדריך לימוד ממוקד (עם הפניות לעמודי המקור)" if assets.get("study_guide.md") else "📖 מדריך לימוד (legacy)"
    if sg_url:
        with st.expander(sg_label, expanded=True):
            sg_text = fetch_text(sg_url)
            if sg_text:
                st.markdown(
                    f'<div dir="rtl" style="text-align: right; font-size: 1.05em; line-height: 1.7;">\n\n{sg_text}\n\n</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.warning("לא הצלחתי לטעון את מדריך הלימוד.")

    # Flashcards — Spaced Repetition (SM-2) with reveal + 4-grade buttons
    flashcards_url = assets.get("flashcards.json")
    if flashcards_url:
        with st.expander("🃏 כרטיסיות שינון (Spaced Repetition)", expanded=False):
            raw = fetch_text(flashcards_url)
            if not raw:
                st.warning("לא הצלחתי לטעון את הכרטיסיות.")
            else:
                try:
                    cards_raw = json.loads(raw)
                except Exception as e:
                    st.error(f"שגיאת פירוש JSON: {e}")
                    cards_raw = None
                if isinstance(cards_raw, dict):
                    cards_raw = cards_raw.get("cards") or cards_raw.get("flashcards") or cards_raw.get("items") or []
                if isinstance(cards_raw, list) and cards_raw:
                    render_chapter_flashcards_sr(idx, cards_raw)
                elif cards_raw is not None:
                    st.json(cards_raw)

    # Quiz — handles NotebookLM's answerOptions structure
    quiz_url = assets.get("quiz.json")
    if quiz_url:
        with st.expander("❓ קוויז", expanded=False):
            raw = fetch_text(quiz_url)
            if not raw:
                st.warning("לא הצלחתי לטעון את הקוויז.")
            else:
                try:
                    quiz = json.loads(raw)
                except Exception as e:
                    st.error(f"שגיאת פירוש JSON: {e}")
                    quiz = None
                # Normalize: NotebookLM returns {title, questions: [...]}
                if isinstance(quiz, dict):
                    quiz = quiz.get("questions") or quiz.get("items") or []
                if isinstance(quiz, list) and quiz:
                    st.write(f"**{len(quiz)} שאלות**")
                    for i, q in enumerate(quiz):
                        qtext = q.get("question") or q.get("q", "")
                        # NotebookLM format: answerOptions: [{text, isCorrect, rationale}]
                        ao = q.get("answerOptions") or q.get("options") or q.get("choices") or []
                        # Build a uniform [(text, is_correct, rationale)] list
                        opts = []
                        if ao and isinstance(ao[0], dict):
                            for o in ao:
                                opts.append((
                                    o.get("text") or o.get("label", ""),
                                    bool(o.get("isCorrect")),
                                    o.get("rationale") or o.get("explanation", ""),
                                ))
                        elif ao and isinstance(ao[0], str):
                            # Plain string list — separate 'correct' field
                            correct = q.get("correct") or q.get("answer", "")
                            for j, txt in enumerate(ao):
                                is_corr = (txt == correct) or (isinstance(correct, int) and j == correct)
                                opts.append((txt, is_corr, ""))

                        st.markdown(
                            f'<div dir="rtl" style="text-align:right; font-size:1.05em; '
                            f'margin: 14px 0 6px 0;"><b>שאלה {i+1}:</b> {qtext}</div>',
                            unsafe_allow_html=True,
                        )

                        if not opts:
                            st.caption("(אין אפשרויות תשובה — בדוק את מבנה ה-JSON)")
                            st.divider()
                            continue

                        labels = [o[0] for o in opts]
                        user_pick = st.radio(
                            "תשובה",
                            labels,
                            key=f"quiz_d{idx}_q{i}",
                            label_visibility="collapsed",
                            index=None,
                        )
                        if st.button("בדוק", key=f"check_d{idx}_q{i}"):
                            if user_pick is None:
                                st.warning("בחר תשובה לפני שלוחצים 'בדוק'.")
                            else:
                                chosen_idx = labels.index(user_pick)
                                chosen_text, is_correct, chosen_rationale = opts[chosen_idx]
                                # Find the correct option (for showing if user was wrong)
                                correct_tuple = next((o for o in opts if o[1]), None)
                                if is_correct:
                                    st.success("✅ נכון!")
                                    if chosen_rationale:
                                        st.info(f"💡 {chosen_rationale}")
                                else:
                                    st.error(f"❌ שגיאה. התשובה הנכונה: **{correct_tuple[0] if correct_tuple else '?'}**")
                                    if chosen_rationale:
                                        st.caption(f"למה הבחירה שלך לא נכונה: {chosen_rationale}")
                                    if correct_tuple and correct_tuple[2]:
                                        st.info(f"💡 למה זה הנכון: {correct_tuple[2]}")
                        st.divider()
                elif quiz is not None:
                    st.json(quiz)

    # Direct release link
    st.divider()
    release_url = f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}"
    st.caption(f"🔗 [GitHub Release: {tag}]({release_url})")


# ── Main app ────────────────────────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="קורס שלב ב' — פסיכיאטריית ילד ומתבגר",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Load data
    try:
        schedule = load_schedule()
        symbols = load_symbols()
        progress = load_progress()
        palette = load_palette()
        sr_progress = load_sr_progress()
    except Exception as e:
        st.error(f"שגיאת טעינה: {e}")
        return

    # Header
    st.title("📚 קורס שלב ב' — פסיכיאטריית ילד ומתבגר")
    st.caption("מבוסס על: DSM-5-TR · Dulcan's Textbook · Stahl's Essential + Pediatric · הסילבוס הרשמי")
    render_daily_review_banner(sr_progress)

    # Sidebar: overall progress + phase navigation
    with st.sidebar:
        st.markdown("### התקדמות")
        render_progress_overall(schedule, progress)
        st.divider()
        st.markdown("### ניווט")

    # Determine which day to show
    qp = st.query_params
    requested_day = int(qp.get("day", 0)) if qp.get("day") else None
    if requested_day is None:
        requested_day = progress.get("current_day_index", 1)

    selected_from_sidebar = render_phase_sidebar(
        schedule, progress,
        current_phase=next((d.get("phase") for d in schedule["days"] if d["index"] == requested_day), None),
    )

    if selected_from_sidebar:
        requested_day = selected_from_sidebar
        st.query_params["day"] = str(requested_day)

    # Find the day object
    day = next((d for d in schedule["days"] if d["index"] == requested_day), None)
    if not day:
        st.error(f"יום {requested_day} לא נמצא ב-schedule")
        return

    render_day_page(day, symbols, progress, palette)


if __name__ == "__main__":
    main()
