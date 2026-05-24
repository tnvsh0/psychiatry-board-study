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

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import streamlit as st

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

# Fallback palette if file missing
_DEFAULT_PALETTE_ENTRY = {"name": "Unknown", "hex": "#808080", "prompt": ""}


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
    """Fetch a text file (used for sketchy captions). Empty string on failure."""
    try:
        r = requests.get(url, timeout=15)
        if r.ok:
            return r.text
    except Exception:
        pass
    return ""


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


@st.cache_data(ttl=300)
def get_release_assets(tag: str) -> dict | None:
    """Fetch release assets for a given day tag. Returns {asset_name: url}."""
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    r = requests.get(f"{API_BASE}/releases/tags/{tag}", headers=headers, timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    return {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}


def slugify(text: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower()[:60]


def day_tag(day: dict) -> str:
    return f"study-{day['index']:03d}-{slugify(day.get('title_en', ''))}"


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
        st.markdown("### 🎙 פודקאסט")
        st.audio(audio_url)
        st.caption(f"[הורד MP3]({audio_url})")

    # Video
    video_url = assets.get("video.mp4")
    if video_url:
        st.markdown("### 🎬 וידאו")
        st.video(video_url)
        st.caption(f"[הורד MP4]({video_url})")

    # Sketchy scenes (Gemini) — primary visual mnemonic + Hebrew narrative caption
    sketchy_images = sorted(k for k in assets if k.startswith("sketchy_") and k.endswith(".png"))
    if sketchy_images:
        st.markdown("### 🎨 סצנות Sketchy")
        for name in sketchy_images:
            aspect = name.replace("sketchy_", "").replace(".png", "")
            caption_name = f"sketchy_{aspect}.txt"
            caption_url = assets.get(caption_name)

            with st.container():
                img_col, txt_col = st.columns([1, 1])
                with img_col:
                    st.markdown(f"**{aspect}**")
                    st.image(assets[name], use_container_width=True)
                with txt_col:
                    if caption_url:
                        caption_text = fetch_text(caption_url)
                        if caption_text:
                            st.markdown(
                                f'<div dir="rtl" style="text-align: right; '
                                f'font-size: 1.0em; line-height: 1.65; padding: 6px 4px;">'
                                f'{caption_text}</div>',
                                unsafe_allow_html=True,
                            )
                        else:
                            st.caption(f"(לא הצלחתי לטעון את הקפסולה)")
                            st.markdown(f"[פתח טקסט]({caption_url})")
                    else:
                        st.caption("(אין קפסולת הסבר לתמונה זו)")
                st.divider()

    # Standard infographics (NotebookLM) — structured reference
    infographics = sorted(k for k in assets if k.startswith("infographic_") and k.endswith(".png"))
    if infographics:
        st.markdown("### 🖼 אינפוגרפיקות (NotebookLM)")
        cols = st.columns(min(len(infographics), 3))
        for i, name in enumerate(infographics):
            with cols[i % len(cols)]:
                aspect = name.replace("infographic_", "").replace(".png", "")
                st.markdown(f"**{aspect}**")
                st.image(assets[name])

    # Study guide — prefer the new focused study_guide.md (from ask),
    # fall back to legacy report.md (from old generate report)
    sg_url = assets.get("study_guide.md") or assets.get("report.md")
    sg_label = "📖 מדריך לימוד ממוקד (עם הפניות לעמודי המקור)" if assets.get("study_guide.md") else "📖 מדריך לימוד (legacy)"
    if sg_url:
        with st.expander(sg_label, expanded=True):
            try:
                r = requests.get(sg_url, timeout=15)
                if r.ok:
                    # Render with explicit RTL wrapper so Hebrew flows correctly
                    st.markdown(
                        f'<div dir="rtl" style="text-align: right; font-size: 1.05em; line-height: 1.7;">\n\n{r.text}\n\n</div>',
                        unsafe_allow_html=True,
                    )
                    st.divider()
                    st.caption(f"[הורד MD]({sg_url})")
                else:
                    st.markdown(f"[פתח study guide]({sg_url})")
            except Exception:
                st.markdown(f"[פתח study guide]({sg_url})")

    # Flashcards
    flashcards_url = assets.get("flashcards.json")
    if flashcards_url:
        with st.expander("🃏 כרטיסיות שינון", expanded=False):
            try:
                r = requests.get(flashcards_url, timeout=15)
                if r.ok:
                    cards = r.json()
                    # Try to detect structure
                    if isinstance(cards, dict) and "flashcards" in cards:
                        cards = cards["flashcards"]
                    elif isinstance(cards, dict) and "cards" in cards:
                        cards = cards["cards"]
                    if isinstance(cards, list):
                        st.write(f"**{len(cards)} כרטיסיות**")
                        for i, c in enumerate(cards):
                            q = c.get("question") or c.get("front") or c.get("q", "")
                            a = c.get("answer") or c.get("back") or c.get("a", "")
                            aspect = c.get("aspect", "")
                            with st.expander(f"כרטיסיה {i+1}: {q[:70]}" + (f" ({aspect})" if aspect else "")):
                                st.markdown(f"**ש:** {q}")
                                st.markdown(f"**ת:** {a}")
                                # Pair with infographic if aspect matches
                                if aspect and infographics:
                                    matching = [n for n in infographics if aspect in n]
                                    if matching:
                                        st.image(assets[matching[0]], caption=f"קישור ויזואלי: {aspect}")
                    else:
                        st.json(cards)
                else:
                    st.markdown(f"[פתח flashcards.json]({flashcards_url})")
            except Exception as e:
                st.error(f"שגיאת טעינת כרטיסיות: {e}")
                st.markdown(f"[פתח flashcards.json]({flashcards_url})")

    # Quiz
    quiz_url = assets.get("quiz.json")
    if quiz_url:
        with st.expander("❓ קוויז", expanded=False):
            try:
                r = requests.get(quiz_url, timeout=15)
                if r.ok:
                    quiz = r.json()
                    if isinstance(quiz, dict) and "questions" in quiz:
                        quiz = quiz["questions"]
                    if isinstance(quiz, list):
                        st.write(f"**{len(quiz)} שאלות**")
                        for i, q in enumerate(quiz):
                            qtext = q.get("question") or q.get("q", "")
                            choices = q.get("choices") or q.get("options") or []
                            correct = q.get("correct") or q.get("answer", "")
                            expl = q.get("explanation") or q.get("rationale", "")
                            st.markdown(f"**שאלה {i+1}**: {qtext}")
                            if isinstance(choices, list):
                                user_pick = st.radio(
                                    "תשובה",
                                    choices,
                                    key=f"quiz_d{idx}_q{i}",
                                    label_visibility="collapsed",
                                )
                                if st.button("בדוק", key=f"check_d{idx}_q{i}"):
                                    if user_pick == correct or (isinstance(correct, int) and choices.index(user_pick) == correct):
                                        st.success("✅ נכון!")
                                    else:
                                        st.error(f"❌ שגיאה. התשובה: {correct}")
                                    if expl:
                                        st.info(f"💡 {expl}")
                            st.divider()
                else:
                    st.markdown(f"[פתח quiz.json]({quiz_url})")
            except Exception as e:
                st.error(f"שגיאת טעינת קוויז: {e}")
                st.markdown(f"[פתח quiz.json]({quiz_url})")

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
    except Exception as e:
        st.error(f"שגיאת טעינה: {e}")
        return

    # Header
    st.title("📚 קורס שלב ב' — פסיכיאטריית ילד ומתבגר")
    st.caption("מבוסס על: DSM-5-TR · Dulcan's Textbook · Stahl's Essential + Pediatric · הסילבוס הרשמי")

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
