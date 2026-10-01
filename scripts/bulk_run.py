#!/usr/bin/env python3
"""Produce every chapter, in study order, cleaning up as it goes.

Design notes that are load-bearing:

STUDY ORDER, NOT TRACK ORDER. The owner studies chapter 1 forward and is
already behind the calendar, so the queue is the schedule's own order: the
material always arrives ahead of where they are, and a run cut short leaves a
contiguous block of finished chapters rather than a scattering.

CLEAN UP AFTER EACH CHAPTER. The old pipeline wrote every artifact to
_generated/day-NNN/ and never deleted anything. Three chapters filled 670MB and
the shared VM disk hit 98% mid-run. Once a chapter's release exists on GitHub,
the local copy is a duplicate — so it goes, and the run needs room for one
chapter at a time instead of 141.

PACE THE GENERATIONS. The podcast project measured NotebookLM failing silently
at roughly 19-21 back-to-back generations: the call returns, the artifact never
appears, and the gap is only discovered later. We produce ~7 per chapter, so
without pacing the wall is hit on chapter three.

RESUMABLE. State is a JSON file keyed by chapter, so a usage limit, a dropped
connection or a reboot costs the current chapter and nothing else.
"""
from __future__ import annotations

import argparse
import json
import shutil
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daily_study import slugify  # noqa: E402  one source of truth for tags

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "_bulk" / "state.json"
GENERATED = ROOT / "_generated"

# The same profile daily_study.py will use, so the preflight check and the work
# cannot disagree about which account is being tested.
#
# Defaulting this to "" was a live hazard, not a convenience. An empty profile
# sends every notebooklm call — including `login` — at the `default` profile,
# which belongs to another account running other projects on this machine; a
# login there overwrites their session. It cost nothing to notice the first
# time only because auth_ok() stopped the run before try_relogin() fired.
# There is no correct anonymous default, so demand one.
PROFILE = os.environ.get("NOTEBOOKLM_PROFILE", "").strip()
if not PROFILE:
    raise SystemExit(
        "NOTEBOOKLM_PROFILE is not set. Refusing to run: an empty profile\n"
        "targets `default`, which is another account's session.\n"
        "    NOTEBOOKLM_PROFILE=bulk python scripts/bulk_run.py")

# Must be the same repo daily_study.py publishes to.
GH_REPO = os.environ.get("GH_REPO", "tnvsh0/psychiatry-board-study")

# Seconds between chapters. The generations inside one chapter are already
# spaced by daily_study; this is the gap between chapters, which is what keeps
# the hourly generation count under the ceiling the podcast project hit.
CHAPTER_GAP_S = 45

# How long a freshly minted session must survive before another re-mint is
# allowed. Shorter than a session's real life (hours), longer than the few
# minutes a chapter takes to die on a profile that is actually gone.
RELOGIN_COOLDOWN_S = 20 * 60


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"done": [], "failed": {}, "started_at": None}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def refreshed_since(tag: str, t0: datetime) -> bool:
    """Did this run actually replace the chapter's study material?

    For a first run, `released(tag)` already answers that: no release before,
    a release after. For --rerun it cannot — the old release is there before
    the run starts, so a regeneration that aborted would read as done. The
    study guide is the asset every successful run re-uploads and the one the
    owner studies through, so its timestamp is the test.
    """
    r = subprocess.run(["gh", "release", "view", tag, "--repo", GH_REPO,
                        "--json", "assets"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        return False
    try:
        assets = json.loads(r.stdout or "{}").get("assets", [])
    except Exception:
        return False
    for a in assets:
        if a.get("name") == "study_guide.md" and a.get("updatedAt"):
            when = datetime.fromisoformat(a["updatedAt"].replace("Z", "+00:00"))
            return when >= t0
    return False


def released(tag: str) -> bool:
    """Does this chapter's release exist? Two things ride on the answer.

    A false negative marks a chapter that actually succeeded as failed AND
    skips its sweep, so the disk keeps filling. Without --repo, gh resolves the
    repository from the working directory, while daily_study.py publishes to
    GH_REPO - so an override, or a run started from elsewhere, would have the
    two halves asking about different repositories and never agreeing.
    """
    # A DRAFT counts as existing to `gh release view`, which returns 0 for it.
    # A peer project had five QC-rejected episodes sit unpublished since July
    # because its sweep asked whether the release existed rather than whether
    # it was published, and walked past them every week. Ask for the state.
    r = subprocess.run(["gh", "release", "view", tag, "--repo", GH_REPO,
                        "--json", "tagName,isDraft"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    if r.returncode != 0:
        return False
    try:
        return not json.loads(r.stdout).get("isDraft", False)
    except Exception:               # noqa: BLE001 — unparseable is not proof
        return False


# Kept when a chapter's artifacts are swept. facts.json is the ONLY input a
# plate render needs, and deleting it is what stripped chapters 27-41 of the
# ability to be drawn later: they were built with SKIP_PLATES during an image
# lockout, released, swept, and their facts went with them. It is ~5KB against
# a video.mp4 of 34MB, so it was never what filled the disk.
SWEEP_KEEP = ("facts.json",)


def sweep_local(day_index: int) -> int:
    """Delete this chapter's local artifacts once its release exists.

    Everything here also exists in the GitHub release, so the local copy is a
    duplicate — except that a duplicate is what a later pass reads. Keep the
    small file that makes a chapter re-renderable and delete the rest.
    """
    d = GENERATED / f"day-{day_index:03d}"
    if not d.exists():
        return 0
    mb = 0.0
    for f in sorted(d.rglob("*"), key=lambda x: -len(x.parts)):
        if f.is_file():
            if f.name in SWEEP_KEEP:
                continue
            mb += f.stat().st_size / (1024 * 1024)
            f.unlink(missing_ok=True)
        elif f.is_dir() and not any(f.iterdir()):
            f.rmdir()
    return int(mb)


# A failure that is really "the session died" looks exactly like a chapter
# failure from out here, and there are 141 chapters to fail.
AUTH_MARKERS = ("Authentication expired or invalid",
                "Run 'notebooklm login'",
                "accounts.google.com",
                "AuthExpired",            # daily_study's fast-fail
                "session is dead")
MAX_CONSECUTIVE_FAILURES = 3

# The machine's connection failing, as opposed to NotebookLM refusing. Seen on
# 2026-09-15: chapters 57, 58 and 59 each died on "certificate verify failed:
# Hostname mismatch" — something on the network path answering for Google —
# and the three-in-a-row guard stopped a queue with 88 chapters left. By the
# next morning both hosts verified cleanly on Google Trust Services certs and
# the only thing needed was to start again. These wait instead of stopping.
NETWORK_MARKERS = ("CERTIFICATE_VERIFY_FAILED",
                   "getaddrinfo failed",
                   "Temporary failure in name resolution",
                   "ConnectError",
                   "ConnectTimeout",
                   "Network is unreachable",
                   "No route to host")
NETWORK_POLL_S = 10 * 60
NETWORK_MAX_WAIT_S = 12 * 3600

# NotebookLM's chat going quiet is not a chapter problem and no chapter after
# this one will fare better. Stop the whole queue on the first sighting.
CHAT_DOWN_MARKERS = ("ChatUnavailable", "No parseable chunks",
                     "wire format may have changed")

# No chapter is ever worth this long. Chapter 2 lost its NotebookLM session
# partway through and kept going for 566 minutes — `ask` waits 900s an attempt,
# three attempts a call, several calls a chapter, all of them against a dead
# session. daily_study now fails fast on that specific cause; this is the
# backstop for every cause nobody has met yet.
# Raised from 75 minutes after chapter 11 hit the cap doing honest work: three
# plates, four render tries each, all of them legitimate. The cap exists to
# catch a hang, not to interrupt a long chapter, and the final quality-model
# pass added to every plate makes chapters longer still.
CHAPTER_TIMEOUT_S = int(os.environ.get("CHAPTER_TIMEOUT_S", 130 * 60))


class _SkipHealth(Exception):
    """The Gemini health check does not apply to this run."""


def auth_ok() -> tuple[bool, str]:
    """One cheap read before committing to hours of work.

    Google revoked this project's sessions mid-session once, server-side, while
    the stored cookies were still valid locally for another 399 days - so
    checking the cookie file proves nothing and only a real call does. Without
    this the runner would spend two hours marking all 141 chapters failed at
    45-second intervals and leave a retry list with no information in it.
    """
    p = subprocess.run(["notebooklm", "-p", PROFILE, "list"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode == 0 and not any(m in out for m in AUTH_MARKERS):
        return True, ""
    if any(m in out for m in AUTH_MARKERS):
        return False, ("NotebookLM rejected the stored session. Re-authenticate:\n"
                       f"    notebooklm -p {PROFILE} login")
    return False, f"notebooklm list failed rc={p.returncode}: {out.strip()[:200]}"


def try_relogin() -> bool:
    """Re-mint the session without a human, and say whether it worked.

    The stored NotebookLM cookies expire every few hours, but the persistent
    browser profile still holds a valid GOOGLE session, and `notebooklm login`
    uses it to mint new ones silently — the owner ran it and was never asked to
    sign in. That only holds while the profile survives, which is exactly why
    --fresh is not used here: it deletes the profile, and then a person really
    is required.

    So an expired session is recoverable in-process. Before this, it cost the
    run: the queue stopped and waited for someone to notice.
    """
    print("  [i] session expired — re-minting from the browser profile...",
          flush=True)
    for attempt in range(2):
        try:
            r = subprocess.run(
                ["notebooklm", "-p", PROFILE, "login"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600)
            out = " ".join(((r.stdout or "") + " " + (r.stderr or "")).split())
        except subprocess.TimeoutExpired:
            out = "login timed out after 600s"
        # SAY what happened. The first version threw the output away and
        # reported a bare "FAILED — a person is needed", so when chapter 22
        # stopped the queue there was nothing to explain why, and a manual
        # `notebooklm login` from a terminal then worked on the first try.
        # Discarding the error was the same mistake nb_run had made, repeated
        # two days after fixing it there.
        ok, _ = auth_ok()
        if ok:
            print("  [i] re-auth succeeded", flush=True)
            return True
        print(f"  [i] re-auth attempt {attempt + 1}/2 failed: {out[:220]}",
              flush=True)
        if attempt == 0:
            time.sleep(20)
    print("  [i] re-auth FAILED — a person is needed", flush=True)
    return False


def run_chapter(day_index: int, log_dir: Path) -> tuple[bool, str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"ch{day_index:03d}.log"
    started = time.time()
    # The child writes Hebrew chapter titles into this log. On Windows a
    # redirected stdout gets the locale codec (cp1255), which cannot encode
    # them, and the chapter dies on UnicodeEncodeError partway through. Each
    # script also reconfigures its own streams; this covers anything they call.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8:replace"
    # Audio and infographics are the two artifacts NotebookLM caps per day. Two
    # chapters' worth was enough to start getting RateLimitError on both while
    # video, quiz and flashcards kept working. Leaving them on the critical
    # path caps the whole run at three or four artifacts a day, which finishes
    # the course well after the exam. The bulk run produces everything else;
    # backfill_audio.py fills the audio in a few a day, earliest chapter first.
    env.setdefault("SKIP_QUOTA_ARTIFACTS", "1")
    # Draft rungs on Flash, the frame that ships on Pro. The owner chose this on
    # 2026-08-23: ~$85 across 141 chapters rather than ~$227, on the reasoning
    # that most renders in the ladder are discarded anyway.
    env.setdefault("SKETCHY_LADDER_MODEL", "gemini-3.1-flash-image-preview")
    # 2026-08-27: every render moves to the Antigravity CLI's own free quota.
    # The trade the owner accepted is resolution — agy has no size parameter,
    # so plates render at 1376x768 rather than 2K — against a bill that goes to
    # zero. Their reasoning was that the reverse mistake is the expensive one:
    # spending the money first and only then discovering the free route worked.
    #
    # 1K is only viable because the plates were split three ways the same day;
    # sketchy_spec drops its density budget when it sees this variable, so the
    # two changes must travel together.
    env.setdefault("SKETCHY_ENGINE", "agy")
    timed_out = False
    with log.open("w", encoding="utf-8") as fh:
        try:
            p = subprocess.run(
                [sys.executable, "-u", str(ROOT / "scripts" / "daily_study.py"),
                 "--day", str(day_index)],
                stdout=fh, stderr=subprocess.STDOUT, cwd=str(ROOT), env=env,
                timeout=CHAPTER_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            p = subprocess.CompletedProcess(args=[], returncode=124)
    mins = (time.time() - started) / 60
    body = log.read_text(encoding="utf-8", errors="replace")
    tail = body[-400:]
    note = f"{mins:.1f}min rc={p.returncode} :: {tail[-160:].strip()}"
    if timed_out:
        note = f"TIMEOUT after {mins:.0f}min :: " + tail[-140:].strip()
    if p.returncode != 0 and any(m in body for m in NETWORK_MARKERS):
        note = "NETWORK :: " + note
    elif p.returncode != 0 and any(m in body for m in AUTH_MARKERS):
        note = "AUTH :: " + note
    return p.returncode == 0, note


def wait_for_network(max_wait_s: int = NETWORK_MAX_WAIT_S) -> bool:
    """Block until NotebookLM answers again. True if it did in time.

    A network fault is not a chapter fault and not an account fault: nothing
    about the chapter needs changing, and no human needs to sign in. What it
    needs is time. auth_ok() is the test because it is a real call on the real
    profile — it passes only once DNS, TLS and the session all work together.
    """
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        ok, _ = auth_ok()
        if ok:
            return True
        # An outage can end with the session expired under it, and auth_ok()
        # cannot tell that apart from a network that is still down. Re-minting
        # is silent and harmless when the network is back, and fails fast
        # when it is not.
        try_relogin()
        if auth_ok()[0]:
            return True
        print(f"  [i] network still down; next check in "
              f"{NETWORK_POLL_S // 60}m", flush=True)
        time.sleep(NETWORK_POLL_S)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=144)
    ap.add_argument("--limit", type=int, help="stop after N chapters this session")
    ap.add_argument("--retry-failed", action="store_true",
                    help="second-chance pass over chapters that failed earlier")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rerun", help="comma-separated chapters to REGENERATE even "
                    "though their release exists (replaces the published assets)")
    a = ap.parse_args()
    rerun = ({int(x) for x in a.rerun.replace(" ", "").split(",") if x}
             if a.rerun else set())

    sched = json.loads((ROOT / "schedule" / "schedule.json").read_text(encoding="utf-8"))
    days = {d["index"]: d for d in sched["days"]}
    st = load_state()
    st["started_at"] = st.get("started_at") or datetime.now(timezone.utc).isoformat()

    queue = [i for i in range(a.start, a.end + 1) if i in days]
    if rerun:
        queue = [i for i in queue if i in rerun]
    elif a.retry_failed:
        queue = [i for i in queue if str(i) in st["failed"]]
    else:
        queue = [i for i in queue if i not in st["done"]]

    print(f"queue: {len(queue)} chapter(s){' (retry pass)' if a.retry_failed else ''}")
    if a.dry_run:
        for i in queue[:20]:
            print(f"  {i:3} {days[i]['title_he']}")
        return 0

    ok, why = auth_ok()
    # The same silent re-mint the loop uses mid-run. On 2026-09-17 a restart
    # stopped here, before chapter one, on a session that `notebooklm login`
    # then renewed without anyone signing in — the run asked a person for
    # something it could have done itself.
    if not ok and why.startswith("NotebookLM rejected"):
        ok = try_relogin()
    if not ok:
        print(f"\n[STOP] {why}", file=sys.stderr)
        return 2
    print("auth OK")

    # Chapter 29 walked into a Gemini 503 storm and spent forty minutes on a
    # plate whose gates never answered, narration that never generated and a
    # director that returned nothing - all of it billable. Wait for the weather
    # rather than paying to work through it.
    # With plates deferred, no chapter step reaches agy: the plate loop is the
    # only caller, and it stops at the SKIP_PLATES gate before the director.
    # On 2026-09-17 this check stopped a text-only queue with 87 chapters left
    # because agy answered 0/3 — for a service not one of those chapters used.
    skip_plates = os.environ.get("SKIP_PLATES", "").strip().lower() not in (
        "", "0", "false", "no")
    try:
        if skip_plates:
            raise _SkipHealth
        import gemini_health
        n, asked, note = gemini_health.check()
        if n < gemini_health.HEALTHY_AT:
            print(f"\n[STOP] Gemini answered {n}/{asked} probes"
                  + (f" ({note})" if note else "")
                  + ".\n  Wait for it to recover:  "
                  "python scripts/gemini_health.py --wait 60", file=sys.stderr)
            return 3
        print(f"gemini OK ({n}/{asked} probes)\n")
    except _SkipHealth:
        print("gemini check skipped (SKIP_PLATES: no chapter step uses agy)\n")
    except Exception as e:
        print(f"[!] could not check Gemini health: {e}", file=sys.stderr)

    n, consecutive, last_relogin = 0, 0, 0.0
    for idx in queue:
        if a.limit and n >= a.limit:
            print(f"stopping: reached --limit {a.limit}")
            break
        day = days[idx]
        # Import the real one rather than reimplementing it. These two had
        # drifted apart and disagreed on 92 of the 144 chapters: slugify
        # collapses a run of non-alphanumerics to a single dash, the copy here
        # emitted one dash per character, so "GABA / glutamate balance" became
        # study-007-gaba---glutamate-balance and released() looked up a tag
        # that does not exist. Every affected chapter would have been marked
        # FAILED after succeeding, and never swept.
        tag = f"study-{idx:03d}-{slugify(day.get('title_en', ''))}"

        # --rerun is the one way past this. It exists because the owner
        # approved regenerating chapters whose release is incomplete or wrong
        # (1, 3, 10, 11 missing a third of their material; 9, 18, 34 quizzes in
        # English), and the only other route — calling daily_study --day
        # directly — has none of this runner's re-login: on 2026-10-01 four of
        # seven such re-runs died on one expired session.
        if released(tag) and idx not in rerun:
            print(f"[{idx:3}] already released — sweeping local copy")
            freed = sweep_local(idx)
            if idx not in st["done"]:
                st["done"].append(idx)
            st["failed"].pop(str(idx), None)
            save_state(st)
            continue

        print(f"[{idx:3}] {day['title_he'][:40]} — generating...", flush=True)
        t0 = datetime.now(timezone.utc)
        ok, note = run_chapter(idx, ROOT / "_bulk" / "logs")
        # Wait out a network outage and give the chapter its turn again,
        # instead of letting the outage fail three chapters and stop the run.
        # Once per chapter: a chapter that keeps dying on the network after
        # the connection has demonstrably come back is a chapter problem.
        if not ok and note.startswith("NETWORK ::"):
            print(f"[{idx:3}] network failure — waiting for NotebookLM to "
                  f"answer again", flush=True)
            if not wait_for_network():
                st["failed"][str(idx)] = note
                save_state(st)
                print(f"\n[STOP] chapter {idx}: the network did not come back "
                      f"within {NETWORK_MAX_WAIT_S // 3600}h. Check the "
                      f"connection, then resume:\n"
                      f"    python scripts/bulk_run.py", file=sys.stderr)
                return 2
            print(f"[{idx:3}] network is back — retrying", flush=True)
            ok, note = run_chapter(idx, ROOT / "_bulk" / "logs")
        if ok and idx in rerun and not refreshed_since(tag, t0):
            ok = False
            note = (f"{note} :: REGENERATION DID NOT LAND — the release's "
                    f"study_guide.md is older than this run, so the published "
                    f"material is still the old one")
        if ok and released(tag):
            freed = sweep_local(idx)
            st["done"].append(idx)
            st["failed"].pop(str(idx), None)
            consecutive = 0
            print(f"[{idx:3}] DONE  {note.split('::')[0].strip()} — freed {freed}MB")
        else:
            # Keep the local artifacts of a failed chapter: they are the only
            # evidence of what went wrong, and sweeping them would erase it.
            st["failed"][str(idx)] = note
            consecutive += 1
            print(f"[{idx:3}] FAIL  {note}")
        save_state(st)
        n += 1

        # A revoked session, an exhausted quota or a broken build fails every
        # chapter identically. Marking 141 of them failed at 45s apart destroys
        # the retry list and teaches nobody anything, so stop and say why.
        if any(m in note for m in CHAT_DOWN_MARKERS):
            probe = f"notebooklm{' -p ' + PROFILE if PROFILE else ''} ask 'Say OK.' --json"
            print(f"\n[STOP] chapter {idx}: NotebookLM's chat is returning empty "
                  f"streams, on every notebook. Nothing downstream can be built "
                  f"until it answers again.\n  Check with:  {probe}",
                  file=sys.stderr)
            return 3
        if note.startswith("AUTH ::"):
            # Recoverable without a person, as long as the browser profile is
            # intact. Retry this chapter once rather than ending the run.
            # A once-per-run latch was wrong. NotebookLM sessions expire every
            # few hours and a full queue runs for days, so the second expiry
            # always found the latch spent: chapter 53 died 1.5 hours after
            # chapter 48 had used it, and the run stopped saying the session
            # "could not be re-minted" — which was never tried. The latch was
            # really guarding against a re-login LOOP, so guard against that
            # instead: re-minting twice inside the cooldown means the fresh
            # session died immediately, and that is a dead profile.
            if time.time() - last_relogin > RELOGIN_COOLDOWN_S and try_relogin():
                last_relogin = time.time()
                consecutive = 0
                st["failed"].pop(str(idx), None)
                save_state(st)
                print(f"[{idx:3}] retrying after re-auth", flush=True)
                t0 = datetime.now(timezone.utc)
                ok, note = run_chapter(idx, ROOT / "_bulk" / "logs")
                # The same --rerun guard as the main path. This branch had its
                # own copy of the success test, so a guard added only above
                # would be skipped by exactly the chapters an expired session
                # interrupts — four of the seven re-runs on 2026-10-01.
                if ok and idx in rerun and not refreshed_since(tag, t0):
                    ok = False
                    note = (f"{note} :: REGENERATION DID NOT LAND — "
                            f"study_guide.md is older than this run")
                if ok and released(tag):
                    freed = sweep_local(idx)
                    st["done"].append(idx)
                    save_state(st)
                    print(f"[{idx:3}] DONE  after re-auth — freed {freed}MB")
                    time.sleep(CHAPTER_GAP_S)
                    continue
                st["failed"][str(idx)] = note
                save_state(st)
            # Before ending the run on "the session is dead", ASK whether it
            # is. Chapter 56 stopped a queue with 88 chapters left while a
            # plain `list` on the same profile answered fine: the machine had
            # slept for nine hours mid-chapter, the session expired under it,
            # the re-mint worked, and the retry — started while the network was
            # still coming back — failed too. Two auth failures inside the
            # cooldown is the shape of a dead profile, but it is not proof, and
            # the cheap read that settles it already exists.
            alive, _ = auth_ok()
            if not alive:
                # Say WHICH it was. "could not be re-minted" was printed for a
                # chapter where the re-mint was never attempted, which sent the
                # owner to re-authenticate a session that was already fine.
                why = ("the re-mint did not take"
                       if time.time() - last_relogin > RELOGIN_COOLDOWN_S
                       else f"it was re-minted under "
                            f"{RELOGIN_COOLDOWN_S // 60}m ago and died again")
                print(f"\n[STOP] chapter {idx} failed on authentication and "
                      f"{why}. Sign in and resume:\n"
                      f"    notebooklm -p {PROFILE} login\n"
                      f"    python scripts/bulk_run.py", file=sys.stderr)
                return 2
            st["failed"][str(idx)] = note
            save_state(st)
            consecutive += 1
            print(f"[{idx:3}] auth failed twice, but the session answers now "
                  f"— left failed, moving on", flush=True)
            # Falls through to the consecutive-failure guard below: if this is
            # really systemic it will still stop the run, three chapters in.
        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            print(f"\n[STOP] {consecutive} chapters failed in a row — this is "
                  f"systemic, not per-chapter. Read "
                  f"_bulk/logs/ch{idx:03d}.log before resuming.", file=sys.stderr)
            return 2
        time.sleep(CHAPTER_GAP_S)

    print(f"\ndone={len(st['done'])}  failed={len(st['failed'])}")
    if st["failed"]:
        print("failed chapters:", ", ".join(sorted(st["failed"], key=int)))
        print("re-run them with:  python scripts/bulk_run.py --retry-failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
