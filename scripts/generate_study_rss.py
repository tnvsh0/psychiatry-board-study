#!/usr/bin/env python3
"""Build the course's private podcast feed and publish it to Cloud Storage.

WHY NOT A GITHUB REPO. The original plan was a second, public GitHub repo. Two
things killed it. The course repo is PRIVATE, so its release assets need an
Authorization header and no podcast app will ever fetch them - the audio would
have to be copied somewhere public regardless. And a public GitHub repo is
listed on the owner's profile and indexed by GitHub search, which fails the
actual requirement: a feed that is not published and not discoverable, but that
the owner can paste into AntennaPod.

THE PRIVACY MODEL. Objects live under a 32-character random prefix in a bucket
granted `roles/storage.legacyObjectReader` to allUsers. That role is
objects.get WITHOUT objects.list: anyone who knows an exact object name can
read it, nobody can enumerate the bucket to discover the prefix. The obvious
grant, `objectViewer`, also carries list and would defeat the whole thing. This
is the same shape commercial private-podcast services use, and it is what makes
the feed work in AntennaPod / Pocket Casts / Apple Podcasts. (Spotify cannot
subscribe to an arbitrary RSS URL at all, so it is out regardless.)

The pattern is lifted from the book-podcasts project's vm/setup_buckets.sh,
which is in production. This uses its OWN bucket, not that one: the two
projects should not be able to break each other.

Usage:
    python scripts/generate_study_rss.py --setup       # one-time: create bucket
    python scripts/generate_study_rss.py               # sync audio + rebuild feed
    python scripts/generate_study_rss.py --local-only  # build XML, upload nothing
    python scripts/generate_study_rss.py --url         # print the feed URL
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "_feed"
STATE = WORK / "published.json"

GH_REPO = os.environ.get("GH_REPO", "tnvsh0/psychiatry-board-study")
GCP_PROJECT = os.environ.get("FEED_GCP_PROJECT", "psych-research-agent")
BUCKET = os.environ.get("FEED_BUCKET", "psych-study-media")
REGION = os.environ.get("FEED_REGION", "me-west1")

FEED_TITLE = "פסיכיאטריה של הילד והמתבגר — קורס לבורדים"
FEED_DESC_HE = (
    "סיכומי פרקים מוקראים לקראת בחינת הבורד בפסיכיאטריה של הילד והמתבגר, "
    "לפי סדר הלימוד. כל פרק מבוסס על ספרי הלימוד של הקורס."
)
AI_DISCLOSURE = (
    "הפודקאסט נוצר אוטומטית באמצעות בינה מלאכותית על בסיס ספרי לימוד. "
    "התוכן עלול להכיל אי-דיוקים או המצאות ואינו תחליף למקור. "
    "אין להסתמך עליו לקבלת החלטות קליניות."
)


def secret() -> str:
    """The unguessable path prefix. Without it there is no privacy at all."""
    s = os.environ.get("FEED_SECRET", "").strip()
    if len(s) < 24:
        raise SystemExit(
            "FEED_SECRET is not set, or is too short to be unguessable.\n"
            "Generate one and put it in .env:\n"
            '  python -c "import secrets;print(secrets.token_urlsafe(24))"')
    return s


def base_url() -> str:
    return f"https://storage.googleapis.com/{BUCKET}/{secret()}"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"episodes": {}}


def save_state(s: dict) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def load_schedule() -> list[dict]:
    sched = json.loads((ROOT / "schedule" / "schedule.json").read_text(encoding="utf-8"))
    return sched["days"]


def released_chapters() -> dict[int, dict]:
    """Chapters whose GitHub release carries a real audio.mp3.

    Reads asset SIZE, not just its presence: a zero-byte or truncated upload is
    exactly the kind of thing that would otherwise reach the feed as a silent
    0-second episode.
    """
    r = run(["gh", "release", "list", "--repo", GH_REPO, "--limit", "300",
             "--json", "tagName,createdAt"])
    if r.returncode != 0:
        raise SystemExit(f"gh release list failed: {(r.stderr or r.stdout)[:300]}")
    out = {}
    for rel in json.loads(r.stdout or "[]"):
        tag = rel["tagName"]
        if not tag.startswith("study-"):
            continue
        try:
            idx = int(tag.split("-")[1])
        except (IndexError, ValueError):
            continue
        a = run(["gh", "release", "view", tag, "--repo", GH_REPO, "--json", "assets"])
        if a.returncode != 0:
            print(f"  [!] {tag}: could not read assets; skipping", file=sys.stderr)
            continue
        audio = next((x for x in json.loads(a.stdout or "{}").get("assets", [])
                      if x["name"].lower().endswith(".mp3") and x["size"] > 100_000), None)
        if audio:
            out[idx] = {"tag": tag, "size": audio["size"], "name": audio["name"],
                        "created": rel["createdAt"]}
    return out


def sync_audio(chapters: dict[int, dict], state: dict) -> dict[int, dict]:
    """Copy each chapter's audio into the bucket, once."""
    WORK.mkdir(parents=True, exist_ok=True)
    for idx, ch in sorted(chapters.items()):
        key = str(idx)
        done = state["episodes"].get(key)
        if done and done.get("size") == ch["size"]:
            ch["url"] = done["url"]
            continue
        local = WORK / f"day-{idx:03d}.mp3"
        print(f"  downloading {ch['tag']} ...")
        d = run(["gh", "release", "download", ch["tag"], "--repo", GH_REPO,
                 "--pattern", ch["name"], "--output", str(local), "--clobber"])
        if d.returncode != 0 or not local.exists():
            print(f"  [!] {ch['tag']}: download failed; skipping", file=sys.stderr)
            continue
        if local.stat().st_size != ch["size"]:
            print(f"  [!] {ch['tag']}: downloaded {local.stat().st_size} bytes, "
                  f"expected {ch['size']}; skipping", file=sys.stderr)
            local.unlink(missing_ok=True)
            continue
        remote = f"gs://{BUCKET}/{secret()}/audio/day-{idx:03d}.mp3"
        u = run(["gcloud", "storage", "cp", str(local), remote,
                 "--content-type=audio/mpeg", "--project", GCP_PROJECT])
        if u.returncode != 0:
            print(f"  [!] {ch['tag']}: upload failed: "
                  f"{(u.stderr or '')[:200]}", file=sys.stderr)
            local.unlink(missing_ok=True)
            continue
        ch["url"] = f"{base_url()}/audio/day-{idx:03d}.mp3"
        state["episodes"][key] = {"size": ch["size"], "url": ch["url"],
                                  "tag": ch["tag"], "created": ch["created"]}
        local.unlink(missing_ok=True)     # 47MB each; do not keep 141 of them
        print(f"  [OK] day {idx:03d} -> bucket")
    save_state(state)
    return {i: c for i, c in chapters.items() if c.get("url")}


def build_feed(chapters: dict[int, dict], days: list[dict]) -> Path:
    from feedgen.feed import FeedGenerator
    by_index = {d["index"]: d for d in days}
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.title(FEED_TITLE)
    fg.link(href=f"{base_url()}/feed.xml", rel="self")
    fg.description(f"{FEED_DESC_HE}\n\n{AI_DISCLOSURE}")
    fg.language("he")
    fg.podcast.itunes_author("קורס פרטי")
    fg.podcast.itunes_explicit("no")
    # The course is read in order, so players should offer oldest-first.
    fg.podcast.itunes_type("serial")
    fg.podcast.itunes_category("Health & Fitness", "Medicine")
    fg.podcast.itunes_summary(f"{FEED_DESC_HE}\n\n{AI_DISCLOSURE}")
    fg.podcast.itunes_block("yes")     # ask directories not to list it
    for idx, ch in sorted(chapters.items()):
        day = by_index.get(idx, {})
        fe = fg.add_entry()
        # A stable id, so re-uploading audio never duplicates an episode in a
        # subscriber's app.
        fe.id(f"study-{idx:03d}")
        fe.guid(f"study-{idx:03d}", permalink=False)
        fe.title(f"יום {idx:03d}: {day.get('title_he', ch['tag'])}")
        aspects = ", ".join(day.get("aspects", [])[:6])
        fe.description(f"{day.get('title_he','')}\n{aspects}\n\n{AI_DISCLOSURE}")
        fe.enclosure(ch["url"], str(ch["size"]), "audio/mpeg")
        fe.podcast.itunes_episode(idx)
        fe.pubDate(datetime.fromisoformat(
            ch["created"].replace("Z", "+00:00")).astimezone(timezone.utc))
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / "feed.xml"
    fg.rss_file(str(out), pretty=True)
    return out


def setup_bucket() -> None:
    """One-time infrastructure. Read the grant comment before changing it."""
    print(f"Creating gs://{BUCKET} in {GCP_PROJECT} ({REGION}) ...")
    r = run(["gcloud", "storage", "buckets", "create", f"gs://{BUCKET}",
             "--project", GCP_PROJECT, "--location", REGION,
             "--uniform-bucket-level-access"])
    if r.returncode != 0 and "already exists" not in (r.stderr or ""):
        raise SystemExit(f"bucket create failed: {(r.stderr or '')[:400]}")
    # legacyObjectReader = objects.get WITHOUT objects.list. Anyone who knows an
    # exact object name reads it; nobody can enumerate the bucket to find the
    # secret prefix. objectViewer would ALSO grant list and defeat the model.
    print(">>> granting allUsers legacyObjectReader (read-if-you-know-the-name)")
    r = run(["gcloud", "storage", "buckets", "add-iam-policy-binding",
             f"gs://{BUCKET}", "--member=allUsers",
             "--role=roles/storage.legacyObjectReader", "--project", GCP_PROJECT])
    if r.returncode != 0:
        raise SystemExit(f"iam binding failed: {(r.stderr or '')[:400]}")
    cors = WORK / "cors.json"
    WORK.mkdir(parents=True, exist_ok=True)
    cors.write_text(json.dumps([{"origin": ["*"], "method": ["GET", "HEAD"],
                                 "responseHeader": ["Content-Type", "Range"],
                                 "maxAgeSeconds": 3600}]), encoding="utf-8")
    run(["gcloud", "storage", "buckets", "update", f"gs://{BUCKET}",
         "--cors-file", str(cors), "--project", GCP_PROJECT])
    print(f"[OK] gs://{BUCKET} ready")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="create the bucket (one time)")
    ap.add_argument("--local-only", action="store_true", help="build XML, upload nothing")
    ap.add_argument("--url", action="store_true", help="print the feed URL and exit")
    a = ap.parse_args()

    if a.setup:
        setup_bucket()
        return 0
    if a.url:
        print(f"{base_url()}/feed.xml")
        return 0

    days = load_schedule()
    chapters = released_chapters()
    print(f"{len(chapters)} chapter(s) have publishable audio")
    if not chapters:
        print("nothing to publish yet")
        return 0

    if a.local_only:
        for i, c in chapters.items():
            c["url"] = f"{base_url()}/audio/day-{i:03d}.mp3"
        ready = chapters
    else:
        ready = sync_audio(chapters, load_state())

    feed = build_feed(ready, days)
    print(f"[OK] {feed} ({feed.stat().st_size} bytes, {len(ready)} episodes)")
    if not a.local_only:
        u = run(["gcloud", "storage", "cp", str(feed),
                 f"gs://{BUCKET}/{secret()}/feed.xml",
                 "--content-type=application/rss+xml", "--project", GCP_PROJECT,
                 "--cache-control=no-cache, max-age=60"])
        if u.returncode != 0:
            raise SystemExit(f"feed upload failed: {(u.stderr or '')[:300]}")
        print(f"[OK] feed live at {base_url()}/feed.xml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
