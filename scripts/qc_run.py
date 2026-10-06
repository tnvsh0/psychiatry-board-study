#!/usr/bin/env python3
"""Drive one chapter at a time through the whole QC pass, and survive being
interrupted anywhere in it. Spec §5.10.

WHY AN ORCHESTRATOR AT ALL. The backlog is ~68 released chapters at three agy
calls each, 24-51 hours of serial judge time (spec §6), on a laptop that sleeps,
behind a quota that locked this account out for 13h33m the one time it was
measured. Nothing in that run may be paid for twice, and nothing may be half
done: every stage writes its result to `_qc/<tag>/` and marks a step in
qc_store.Journal before the next one starts, so a resume picks up at the first
step that never completed rather than at the chapter's beginning.

WHY THE ORDER IS FETCH → SNAPSHOT → HYGIENE → LINT → SOURCES → JUDGE. Hygiene
runs before the judge because §5.9 needs the judge's spans to quote the final
text: clean the escaped LaTeX afterwards and every span a finding anchors on
moves. Lint runs after hygiene for the same reason — a `hygiene_latex` flag on
text that has already been cleaned is noise the owner has to re-read. §5.4 says
lint reads "the released bytes"; what was wrong with them is not lost, it is in
the hygiene record of the verdict, which names every change.

WHY THE JUDGE, SOURCES AND FIXER ARE INJECTED. Three reasons, in order of how
much they cost: (1) every test in tests/qc/test_qc_run.py runs offline, and an
orchestrator that imported agy at module scope could not be tested at all;
(2) qc_sources, qc_judge and qc_fix land beside this file and their names were
not frozen when it was written, so `_Lazy` binds them on first use and says
exactly what it looked for when it cannot — a wiring bug names itself instead of
crashing 40 minutes into a run; (3) `--dry-run` must reach the bundle without
ever constructing an agy call, which is only safe if the judge is a value this
module can decline to touch.

WHY IT EXITS INSTEAD OF SLEEPING. A quota stop is not an error, it is a wait of
up to ~27 hours (§4 forbids sleeping in-process for more than 30 minutes). The
run persists `paused_quota{resume_after, step}`, sends ONE ntfy with the wake-up
time and exits 4. A cron or the owner starts it again; the journal makes that
cost nothing.

    python scripts/qc_run.py --chapters 19,42
    python scripts/qc_run.py --all --dry-run          # no agy call, no upload
    python scripts/qc_run.py --chapters 35 --no-upload
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import statistics
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import qc_cite                                            # noqa: E402
import qc_lint                                            # noqa: E402
import qc_norm                                            # noqa: E402
import qc_store                                           # noqa: E402
import qc_units                                           # noqa: E402
from agy_attach import QuotaPaused                        # noqa: E402

# Windows gives a redirected stdout the locale codec (cp1255 here) and a single
# Hebrew character then raises UnicodeEncodeError. Every report line this module
# prints is Hebrew and a backlog run is always piped into a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                         # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]

# Exit codes. The caller of a 24-hour run is a cron line or a tired owner, so
# each one names a different next action:
EXIT_OK = 0          # every requested chapter reached a verdict
EXIT_ERROR = 2       # refused to start, or a chapter blew up unrecoverably
EXIT_STOP = 3        # circuit breaker: the judge is not answering usefully
EXIT_QUOTA = 4       # paused on quota; start again after `resume_after`

# Three chapters in a row whose canary or JSON parse failed means the judge
# itself is broken (a model change, an agy upgrade, a truncated schema) — not
# three unlucky chapters. Measured on 2026-09-18: when agy stopped attaching
# files it failed EVERY call the same way, so the fourth attempt would have been
# the 68th wasted one. Stop at three and say which probe to run.
CIRCUIT_BREAKER_N = 3

# §5.9: "At most one retry per finding (<= 2 rounds)". A round is plan → judge →
# patch; round 2 exists only to confirm a Tier-2 rewrite, never to keep editing.
MAX_REJUDGE_ROUNDS = 2

# The three judge calls of §5.8 and the journal step each one completes.
# There is no `judged_S` in qc_store.STEPS, so a Synopsis call (and any split of
# a call that the page limit forced) is recorded in the detail of the last
# judging step: the journal has to stay linear for resume() to mean anything.
CALL_STEP = {"A": "judged_A", "B": "judged_B", "C": "judged_C"}
LAST_JUDGE_STEP = "judged_C"

# Only these reach a REWRITE (§5.8.3: "Only `contradicted` may be high/medium
# and reach qc_fix").
FIXABLE_KINDS = ("contradicted",)
FIXABLE_SEVERITIES = ("high", "medium")
# Everything else a verified finding can be: shown to the owner, never patched.
OWNER_KINDS = ("source_conflict", "unsupported")
# Kinds that get a Tier-1 MARK as well as a place in the owner's queue.
#
# §5.9's Tier 1 is "always, deterministic, no LLM ... For `source_conflict`,
# both quotes", and §5.11 draws that mark ("two for a conflict") and stops
# grading a choice that matches the source as wrong. The owner queue is a local
# `_qc/owner_queue.json` that is never uploaded and that the app never reads, so
# routing a conflict there ALONE left §1's motivating case — 019 atomoxetine,
# StahlKids p.69 "2-8 weeks" against p.74 "8-12 weeks" — marked nowhere the
# learner could see it, which is what §2.4 ("mark, never hide") forbids.
#
# A mark is not a fix: these never reach apply_tier2, and §5.8.3's "never
# auto-fixed" is about the rewrite, not about showing both quotes. The severity
# gate is qc_fix's own (`_why_not_tier1`): a `low` is style, written down in the
# queue and embedded nowhere.
MARK_KINDS = ("source_conflict",)

# A verdict status, most urgent first. The first one that applies wins, because
# the status has exactly one job: naming the single next action.
STATUS_ORDER = ("unjudged", "needs_owner", "needs_regeneration", "fixed",
                "approved")

# §5.8.2: "Approval requires unchecked locked-value units <= a threshold set in
# calibration (start: 0)." An unchecked unit is one whose coverage row the judge
# returned but whose locked value carried no verified source quote — i.e. a
# number nobody confirmed against a book. Approving a chapter on those is the
# same silence §1 found when the first review "checked" 9 items from its own
# knowledge.
#
# This is a SECOND copy of qc_judge.MAX_UNCHECKED, and deliberately not an
# import: qc_judge is bound lazily (see the adapter below) so that `--dry-run`
# and every test here run without importing agy. The two must stay equal; the
# test `test_the_unchecked_threshold_matches_the_judge` fails if they drift.
MAX_UNCHECKED = 0

# §3: "Not judged in v1: video (measure 3 first — v2; badge says 'video: not
# checked')". Named in qc.json's per-asset map so app/quiz_logic.asset_lines
# draws the line; `not_checked` alone is a key the app never reads.
NOT_CHECKED_ASSETS = ("video.mp4",)

# The per-asset status for a chapter whose BOOK-BACKED checks never ran.
#
# §2.3: a chapter must not "look clean because a file was absent". `--no-books`
# (and a machine without `Text BOOKS/`) skips `impossible_citation` and
# `value_unsourced` — the two lint checks that open a PDF — while the judge
# still runs, so the chapter can reach `fixed` and the app then draws §5.11's
# unqualified "נבדק מול ספרי הלימוד" over it: quiz_logic.parse_qc_json reads
# `checked = status not in _NOT_CHECKED` and "fixed" is not in that set.
#
# This word IS in it, so the per-asset line reads "לא נבדק" instead of
# "נבדק, ללא תיקונים". It is deliberately one of the app's existing vocabulary
# rather than a truer word like `partially_checked`: the app is deployed from
# `main` on the owner's decision (§5.11) and must keep reading a qc.json written
# by this branch. `qc.json["skipped"]` names WHICH checks did not run, and the
# per-asset `skipped` list carries the same, for a badge that can one day say
# which half was checked.
PARTIAL_ASSET_STATUS = "skipped"

STATUS_HE = {
    "approved": "אושר",
    "fixed": "תוקן",
    "needs_owner": "ממתין להחלטת הבעלים",
    "needs_regeneration": "דורש יצירה מחדש",
    "unjudged": "לא נשפט",
}
KIND_HE = {
    "contradicted": "סותר את המקור",
    "unsupported": "לא נמצא במקור",
    "source_conflict": "סתירה בין מקורות",
}
SEVERITY_HE = {"high": "גבוהה", "medium": "בינונית", "low": "נמוכה"}

# Owner decision 4, 2026-09-18: "סיכומי כרטיסיות שלב ב is a legitimate source,
# and anything grounded in it must say so explicitly" — and §5.8's rules repeat
# it ("When Kartisiot is the source, the finding and any note say so by name").
# The Hebrew line below goes into _qc/REPORT.md and into §5.10.4's per-chapter
# ntfy, neither of which passes through the app, so the name has to be resolved
# here. A third copy of the table (qc_fix.BOOK_HE, app/quiz_logic.BOOK_HE) for
# the same reason the second exists: app/ ships without scripts/, and a fix row
# written by an older qc_fix carries no `book_he` at all.
BOOK_HE = {
    "DSM-5-TR": "DSM-5-TR",
    "Dulcan": "Dulcan's Textbook",
    "StahlNeuro": "Stahl — Essential Psychopharmacology",
    "StahlKids": "Stahl — Prescriber's Guide, ילדים ומתבגרים",
    "Synopsis": "Kaplan & Sadock — Synopsis",
    "Syllabus": "הסילבוס הרשמי",
    "Kartisiot": "סיכומי כרטיסיות שלב ב",
    "Stahl?": "Stahl (לא ברור איזה כרך)",
}

# Reasons that count as "the judge did not answer", for the circuit breaker.
#
# Deliberately not "network", "quota" or "timeout": those are the machine's
# problem and retrying them later is right. Everything here is a call that came
# back and could not be believed — §5.5 names canary and parse, §5.8.1 adds the
# two mechanical proofs and §5.8.2 the coverage gate, and all four are
# discarded, retried three times and left `unjudged` in exactly the same way.
#
# The words are the ones agy_attach actually writes into `res.diagnostic`, which
# qc_judge copies into the judgment's `reason`:
#   agy_attach.py  "not ingested (no sha256 match under .tempmediaStorage): …"
#                  "tool audit failed: …"
#                  "rejected: <the validator's reason>"  (canary | nonce |
#                                                         coverage | schema)
#                  "no JSON envelope (rc=…)", "reply is not a JSON object: …"
# Matching prose is the fallback, not the contract: a judgment that carries a
# machine-readable `reason_code` is counted on that instead (BREAKER_CODES).
_BREAKER_REASON = re.compile(
    r"canary|nonce|parse|json|schema|coverage|ingest|tool audit|rejected",
    re.I)

# The machine-readable half. qc_judge may set `reason_code` on a judgment; when
# it does, the breaker reads it and never guesses from a sentence.
BREAKER_CODES = frozenset({"canary", "nonce", "coverage", "ingestion",
                           "tool_audit", "schema", "parse"})

# §4's known-answer probe: "if [the agy version] changes mid-run, run the
# atomoxetine known-answer probe (StahlKids p.69, "2-8 weeks") and stop if it
# fails". §5.1's `--check` uses the same page, and §1's 019 conflict is the
# reason this exact number is the one worth probing.
KNOWN_ANSWER = {"book": "StahlKids", "printed": 69, "value": "2-8 weeks",
                "question": "On StahlKids printed page 69, how long does "
                            "atomoxetine take to reach its full therapeutic "
                            "effect? Quote the sentence verbatim."}

# The same topic daily_notify.py posts to, so QC alerts land where the owner
# already looks for this course. Overridable for the same reason it is there.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "psychiatry-study-tnvsh")
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh")


class NotWired(RuntimeError):
    """A collaborator module is missing or does not expose what this calls.

    Raised with the names that were tried, because the fix is a one-line alias
    in the adapter below and the message is the only place that says so.
    """


# ---------------------------------------------------------------------------
# collaborators
#
# qc_sources, qc_judge and qc_fix are siblings of this file. They are bound on
# first use rather than imported at the top so that (a) the tests inject fakes
# and never import them, (b) `--dry-run` works before qc_judge exists, and
# (c) a missing name is reported as a wiring bug with the candidates listed
# instead of an ImportError at the top of a 24-hour run.
#
# Every call is keyword-only. A collaborator may accept a subset and ignore the
# rest with **kw; a signature that rejects a documented keyword is reported as
# NotWired, not as a TypeError from somewhere deep in the stack.
_SOURCES_NAMES = ("plan_calls", "build_calls", "build_bundles", "build")
_JUDGE_NAMES = ("judge_call", "judge_chapter", "judge")
_HYGIENE_NAMES = ("apply_hygiene", "hygiene", "clean_hygiene")
_TIER1_NAMES = ("apply_tier1", "tier1", "embed_notes")
_TIER2_NAMES = ("apply_tier2", "tier2", "rewrite")
_REVERT_NAMES = ("revert_patches", "revert_tier2")
# §4's probe is one agy call against a page we hold, so it belongs to the judge
# module like every other agy call. Until qc_judge exposes one, the probe is
# NotWired — and §4 says to stop when the probe fails, which is what an
# unrunnable probe is: no evidence that the new binary still reads the books.
#
# Spelled out, never a bare `probe`: this name is called with the owner's quota
# on the other end of it, and binding it to whatever a module happens to call
# `probe` is how an unrelated function gets handed a bundle.
_PROBE_NAMES = ("known_answer_probe", "probe_known_answer", "known_answer")


def _bind(module: str, names: Sequence[str], what: str) -> Callable:
    try:
        mod = importlib.import_module(module)
    except ImportError as exc:                            # noqa: PERF203
        raise NotWired(
            f"{what}: cannot import scripts/{module}.py ({exc}). It is a "
            f"sibling of qc_run.py and must expose one of "
            f"{', '.join(names)}.") from exc
    for name in names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise NotWired(
        f"{what}: {module}.py has none of {', '.join(names)}. Add an alias "
        f"there, or a name to qc_run's adapter — the two have to agree in "
        f"exactly one place.")


class _Lazy:
    """One collaborator function, resolved the first time it is called."""

    def __init__(self, module: str, names: Sequence[str], what: str):
        self.module, self.names, self.what = module, names, what
        self._fn: Callable | None = None

    @property
    def available(self) -> bool:
        try:
            self._resolve()
            return True
        except NotWired:
            return False

    def _resolve(self) -> Callable:
        if self._fn is None:
            self._fn = _bind(self.module, self.names, self.what)
        return self._fn

    def __call__(self, **kw):
        fn = self._resolve()
        try:
            return fn(**kw)
        except TypeError as exc:
            msg = str(exc)
            if "argument" in msg and fn.__name__ in msg:
                raise NotWired(
                    f"{self.what}: {self.module}.{fn.__name__} does not accept "
                    f"{sorted(kw)} ({msg}). qc_run calls every collaborator "
                    f"with keywords only; add **kw or the missing "
                    f"parameters.") from exc
            raise


# ---------------------------------------------------------------------------
# notifications


class Ntfy:
    """ntfy.sh, with sending switched off by default.

    Off by default and not merely injectable: a test that forgot to pass a fake
    would otherwise push a Hebrew alert to the owner's phone, and the ntfy topic
    is public — anything sent there cannot be recalled. main() is the only
    place that turns it on.
    """

    def __init__(self, *, enabled: bool = False, topic: str = NTFY_TOPIC,
                 url: str = NTFY_URL):
        self.enabled, self.topic, self.url = enabled, topic, url
        self.sent: list[dict] = []

    def __call__(self, title: str, message: str, *, priority: int = 3,
                 tags: Sequence[str] = ()) -> bool:
        payload = {"topic": self.topic, "title": title, "message": message,
                   "priority": priority, "tags": list(tags)}
        self.sent.append(payload)
        if not self.enabled:
            print(f"[ntfy, לא נשלח] {title} — {message}", flush=True)
            return False
        try:
            import requests                               # lazy: tests need none
            r = requests.post(self.url, json=payload, timeout=15)
            print(f"[ntfy {r.status_code}] {title}", flush=True)
            return r.status_code == 200
        except Exception as exc:                          # noqa: BLE001
            # A failed notification must never fail the run: the verdict is
            # already on disk and the phone is a convenience.
            print(f"[!] ntfy failed: {exc}", flush=True)
            return False


# ---------------------------------------------------------------------------
# options and dependencies


@dataclass
class Options:
    tags: list[str] = field(default_factory=list)
    all_chapters: bool = False
    calibration: bool = False
    dry_run: bool = False
    no_upload: bool = False
    page_limit: int | None = None
    use_books: bool = True
    root: Path | None = None              # the _qc/ tree; None = qc_store's
    model: str | None = None
    max_rejudge: int = MAX_REJUDGE_ROUNDS
    # The release assets the judge reads; None = every judged asset. Owner's
    # decision 2026-10-01: the agy quota buys ~55 calls a week, the study guide,
    # the cards and the quiz are what he studies from, and facts.json and the
    # plate legends feed plates he will draw only after studying a chapter. The
    # assets left out are NOT judged and are reported as uncovered — the chapter
    # does not read as checked on the strength of a judge that never read them.
    scope: frozenset | None = None

    @property
    def uploads_allowed(self) -> bool:
        # Calibration never publishes: §6 runs it to measure the judge, and its
        # verdicts are made against a gold set whose "errors" are seeded.
        return not (self.dry_run or self.no_upload or self.calibration)

    def qc_root(self) -> Path:
        return qc_store.qc_root(self.root)

    def work_dir(self, tag: str) -> Path:
        # _qc/work/ is gitignored (§5.10): bundles and corrected bytes are
        # derivable, and the durable copies are the release and _qc/orig/.
        d = self.qc_root() / "work" / tag
        d.mkdir(parents=True, exist_ok=True)
        return d

    def chapter_dir(self, tag: str) -> Path:
        d = self.qc_root() / tag
        d.mkdir(parents=True, exist_ok=True)
        return d


@dataclass
class Deps:
    """Everything that talks to the outside world, in one injectable place."""
    gh: Any = None
    sources: Callable = field(
        default_factory=lambda: _Lazy("qc_sources", _SOURCES_NAMES,
                                      "bundle planner"))
    judge: Callable = field(
        default_factory=lambda: _Lazy("qc_judge", _JUDGE_NAMES, "judge"))
    hygiene: Callable = field(
        default_factory=lambda: _Lazy("qc_fix", _HYGIENE_NAMES, "hygiene pass"))
    tier1: Callable = field(
        default_factory=lambda: _Lazy("qc_fix", _TIER1_NAMES, "Tier 1 notes"))
    tier2: Callable = field(
        default_factory=lambda: _Lazy("qc_fix", _TIER2_NAMES, "Tier 2 rewrite"))
    revert: Callable = field(
        default_factory=lambda: _Lazy("qc_fix", _REVERT_NAMES, "Tier 2 revert"))
    probe: Callable = field(
        default_factory=lambda: _Lazy("qc_judge", _PROBE_NAMES,
                                      "known-answer probe"))
    notify: Callable = field(default_factory=Ntfy)
    wait_for_network: Callable = qc_store.wait_for_network
    cleanup: Callable | None = None       # agy_attach.cleanup; bound lazily
    now: Callable[[], float] = time.time

    def gh_or_default(self):
        if self.gh is None:
            self.gh = qc_store.Gh()
        return self.gh

    def agy_cleanup(self, conversation_id, workspace=None) -> int:
        if self.cleanup is not None:
            return self.cleanup(conversation_id, workspace=workspace)
        from agy_attach import cleanup as _cleanup        # lazy: tests fake it
        return _cleanup(conversation_id, workspace=workspace)


# ---------------------------------------------------------------------------
# small shared helpers


def _get(obj: Any, *names: str, default=None):
    """Read `names` off a dict or an object, first hit wins.

    qc_sources and qc_judge may hand back dataclasses or dicts; normalizing at
    this one boundary keeps every other line below indifferent to which.
    """
    for n in names:
        if isinstance(obj, dict):
            if n in obj:
                return obj[n]
        elif hasattr(obj, n):
            return getattr(obj, n)
    return default


_TRUE_WORDS = ("true", "yes", "ok", "verified", "1", "pass", "passed")


def _truthy(v) -> bool:
    """Strict truth, because `bool("false")` is True.

    A judge answers over JSON it wrote itself, and "ok": "false" / "verified":
    "no" are shapes a language model produces constantly. `ok` is the ONLY gate
    on a whole chapter — it decides `judged`, which decides whether anything is
    uploaded — so reading it has to be at least as strict as `_is_verified` is
    on a single finding, which already reads its string. A string that is not a
    yes-word is a no; anything non-string keeps ordinary truthiness.
    """
    if isinstance(v, str):
        return v.strip().lower() in _TRUE_WORDS
    return bool(v)


def _text_sha(text: str) -> str:
    return qc_store.sha256_bytes(text.encode("utf-8"))


def _read_text(path: Path) -> str:
    # read_bytes().decode(), never read_text(): read_text() translates CRLF to
    # LF, and the sha256 of the result would not be the sha256 of the asset the
    # verdict was made on. That difference would make every CRLF asset look
    # changed and send it through a swap that changes nothing.
    return path.read_bytes().decode("utf-8")


def _write_text(path: Path, text: str) -> Path:
    # write_bytes for the mirror image of the reason above: on Windows,
    # write_text() turns every "\n" into "\r\n", so the bytes uploaded would
    # not be the bytes that were judged.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _print(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# planned calls and judgments


@dataclass
class PlannedCall:
    call_id: str
    kind: str                      # A | B | C | S
    units: list[str] = field(default_factory=list)
    bundle: Path | None = None
    artifacts: Path | None = None
    pages: int = 0
    manifest: dict = field(default_factory=dict)
    raw: Any = None                # exactly what qc_sources returned

    @property
    def step(self) -> str:
        return CALL_STEP.get(self.kind, LAST_JUDGE_STEP)

    def sizes(self) -> dict:
        def size(p):
            try:
                return Path(p).stat().st_size if p else 0
            except OSError:
                return 0
        return {"bundle_bytes": size(self.bundle),
                "artifacts_bytes": size(self.artifacts)}

    def brief(self) -> str:
        s = self.sizes()
        return (f"{self.call_id} [{self.kind}] {len(self.units):>3} יחידות · "
                f"{self.pages:>3} עמודים · bundle {s['bundle_bytes'] / 1e6:.1f}MB"
                f" · artifacts {s['artifacts_bytes'] / 1024:.0f}KB")


def _as_call(obj: Any, index: int) -> PlannedCall:
    kind = str(_get(obj, "kind", "call_kind", "letter", default="A")).upper()[:1]
    call_id = str(_get(obj, "call_id", "id", "name", default=f"{kind}{index + 1}"))
    bundle = _get(obj, "bundle", "bundle_pdf", "pdf")
    artifacts = _get(obj, "artifacts", "artifacts_md", "md")
    manifest = _get(obj, "manifest", default={}) or {}
    pages = _get(obj, "pages", "page_count", "n_pages")
    if isinstance(pages, (list, tuple, set)):
        pages = len(pages)
    if pages is None:
        pages = len(manifest.get("pages") or [])
    return PlannedCall(
        call_id=call_id, kind=kind,
        units=[str(u) for u in (_get(obj, "units", "uids", default=[]) or [])],
        bundle=Path(bundle) if bundle else None,
        artifacts=Path(artifacts) if artifacts else None,
        pages=int(pages or 0), manifest=manifest, raw=obj)


@dataclass
class Judgment:
    call_id: str
    kind: str
    ok: bool = False
    reason: str = ""
    findings: list = field(default_factory=list)
    coverage: list = field(default_factory=list)
    unchecked: int = 0
    canary: dict = field(default_factory=dict)
    # §5.8.1's two MECHANICAL proofs, as the judge reported them. agy_attach
    # computes both (the sha256 of the bytes actually found under
    # `brain/<cid>/.tempmediaStorage/`, and the tool steps in
    # `conversations/<cid>.db`) and they were the only thing that told the truth
    # about the 2026-09-18 call that answered from memory. They were not carried
    # into the verdict at all, so an audit of `_qc/<tag>/verdict.json` could not
    # tell a judged chapter from one answered without the bundle.
    tempmedia_ok: Any = None            # None = the judge reported nothing
    tool_audit: Any = None
    # A machine-readable rejection class, when the judge sets one; the circuit
    # breaker prefers it to matching the prose of `reason`.
    reason_code: str = ""
    conversation_id: str | None = None
    workspace: str | None = None
    agy_version: str = ""
    model: str = ""
    prompt_sha256: str = ""
    raw_path: str | None = None
    attempts: int = 0
    units: list = field(default_factory=list)
    # The bundle THIS judgment saw, copied off the call. A resume re-plans (the
    # work tree is gitignored and may have been swept) and a re-planned bundle
    # carries a fresh nonce and fresh page codes, so a verdict that described
    # the current plan would be describing a bundle no judge ever read. §5.10
    # asks for the bundle manifest as provenance; this is where it survives.
    pages: int = 0
    manifest: dict = field(default_factory=dict)
    bundle_bytes: int = 0
    artifacts_bytes: int = 0

    def to_json(self) -> dict:
        return {k: (list(v) if isinstance(v, list) else v)
                for k, v in self.__dict__.items()}

    @classmethod
    def from_json(cls, d: dict) -> "Judgment":
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})


def _proof_failure(canary: Any, tempmedia_ok: Any) -> str:
    """"" when the judgment's own proofs are consistent with `ok`, else why not.

    §2.1 — "a judge must prove it read the sources and the material" — is
    enforced inside agy_attach and qc_judge, and the proof is then recorded in
    the judgment this module stores. It was recorded and never read: a judgment
    carrying `ok: true` beside a canary record saying `verified: false` with an
    empty `returned` list produced an APPROVED chapter, and `tempmedia_ok` was
    not among the fields read at all. One flag the judge sets about itself was
    the whole gate on 68 chapters of the owner's material.

    Only a proof that is PRESENT and says it failed refuses the judgment. A
    judgment that reports no canary at all is qc_judge's business — it runs the
    check itself and `ok` is its answer — and this adapter has nothing to
    compare. What it will not do is believe a judgment against itself.

    The words are chosen to match _BREAKER_REASON: a run whose judge keeps
    answering without the bundle must reach §5.5's STOP, not walk the backlog.
    """
    if tempmedia_ok is not None and not _truthy(tempmedia_ok):
        return ("rejected: not ingested — the judgment's own record says the "
                "bundle was not found under .tempmediaStorage "
                "(tempmedia_ok=false)")
    if isinstance(canary, dict) and canary:
        flag = canary.get("verified", canary.get("ok", canary.get("match")))
        if flag is not None and not _truthy(flag):
            return ("rejected: canary — the judgment reports its own canary as "
                    "not verified")
        requested = canary.get("requested") or ()
        if requested and not (canary.get("returned") or ()):
            return (f"rejected: canary — codes were requested for "
                    f"{len(requested)} bundle pages and none came back")
    return ""


def _as_judgment(obj: Any, call: PlannedCall) -> Judgment:
    ok = _get(obj, "ok", "verified", default=None)
    if ok is None:
        ok = _get(obj, "status", default="") in ("ok", "judged", "verified")
    ok = _truthy(ok)
    cov = _get(obj, "coverage", default=[]) or []
    canary = _get(obj, "canary", default={}) or {}
    tempmedia_ok = _get(obj, "tempmedia_ok", "ingested", default=None)
    reason = str(_get(obj, "reason", "diagnostic", "error", default="") or "")
    proof = _proof_failure(canary, tempmedia_ok)
    if ok and proof:
        ok = False
        reason = f"{proof}; the judge called this call ok" + (
            f" ({reason})" if reason else "")
    return Judgment(
        call_id=call.call_id, kind=call.kind, ok=ok,
        reason=reason,
        findings=list(_get(obj, "findings", default=[]) or []),
        coverage=list(cov),
        unchecked=int(_get(obj, "unchecked", "unchecked_locked_values",
                           default=0) or 0),
        canary=canary,
        tempmedia_ok=tempmedia_ok,
        tool_audit=_get(obj, "tool_audit", "tools", default=None),
        reason_code=str(_get(obj, "reason_code", "reject_class",
                             default="") or ""),
        conversation_id=_get(obj, "conversation_id", "cid"),
        workspace=_get(obj, "workspace"),
        agy_version=str(_get(obj, "agy_version", default="") or ""),
        model=str(_get(obj, "model", default="") or ""),
        prompt_sha256=str(_get(obj, "prompt_sha256", "prompt_hash",
                               default="") or ""),
        raw_path=(str(_get(obj, "raw_path", "raw"))
                  if _get(obj, "raw_path", "raw") else None),
        attempts=int(_get(obj, "attempts", default=0) or 0),
        units=list(call.units), pages=call.pages, manifest=call.manifest,
        **call.sizes())


# ---------------------------------------------------------------------------
# finding policy


def _finding_id(f: Any, call_id: str, i: int) -> str:
    return str(_get(f, "id", default=f"{call_id}-{i + 1}"))


def _is_verified(f: Any) -> bool:
    """Fail closed: a finding is verified only if it says so.

    §5.8.3 ends "Anything failing verification is `unverified`: never acted on,
    never shown." A judge that forgets the flag therefore gets its findings
    shown to nobody, which is the safe direction — the alternative is patching
    a chapter on an unverified claim.
    """
    return _truthy(_get(f, "verified", "verification", default=None))


def _same_value(f: Any) -> bool:
    """True when `said` and `source_quote` hold the same value.

    §5.8.4 drops these ("a finding whose quote holds the same value as `said` is
    dropped") and qc_judge already does it. Repeated here as a second net on the
    one class of finding that can reach the owner's material: it is free,
    offline and idempotent, and the 2026-09-18 sample had a judge confidently
    "correcting" 2-8 weeks to 2-8 weeks.
    """
    said = _get(f, "said", default="") or ""
    quote = _get(f, "source_quote", "quote", default="") or ""
    if not said or not quote:
        return False
    try:
        return bool(qc_norm.values_dispute(said, quote).get("same"))
    except Exception:                                     # noqa: BLE001
        return False


def _empty_classification() -> dict:
    return {"fix": [], "mark": [], "owner": [], "unverified": [], "dropped": [],
            "by_kind": {}, "by_severity": {}, "unverified_high": 0}


def classify_findings(judgments: Iterable[Judgment]) -> dict:
    """Split every finding into: to fix, to mark, to the owner, unverified,
    dropped.

    The split is orchestration policy, not re-judging: it reads the kind,
    severity and verified flag qc_judge already computed. What it adds is the
    §5.8.4 same-value drop and the §2.3 rule that an unverified `high` is not
    silence but a reason to stop the chapter.

    `fix` and `mark` are two different questions. `fix` is "may qc_fix change
    this material" and §5.8.3 answers it for `contradicted` alone. `mark` is
    "must the learner see this beside the item", which §5.9 answers with
    "always" and §5.11 draws — and for a `source_conflict` the answer is yes
    while the answer to the first question stays no. A conflict is therefore in
    `mark` AND in `owner`: the note is written now, the decision is his.
    """
    out = _empty_classification()
    for j in judgments:
        for i, f in enumerate(j.findings):
            rec = dict(f) if isinstance(f, dict) else dict(_get(f, "__dict__",
                                                                default={}))
            rec.setdefault("id", _finding_id(f, j.call_id, i))
            rec["call_id"] = j.call_id
            rec["kind"] = str(_get(f, "kind", default="") or "")
            rec["severity"] = str(_get(f, "severity", default="") or "")
            out["by_kind"][rec["kind"]] = out["by_kind"].get(rec["kind"], 0) + 1
            out["by_severity"][rec["severity"]] = \
                out["by_severity"].get(rec["severity"], 0) + 1
            if not _is_verified(f):
                out["unverified"].append(rec)
                if rec["severity"] == "high":
                    out["unverified_high"] += 1
                continue
            if _same_value(f):
                rec["dropped_because"] = "said and source_quote hold the same value"
                out["dropped"].append(rec)
                continue
            if (rec["kind"] in FIXABLE_KINDS
                    and rec["severity"] in FIXABLE_SEVERITIES):
                out["fix"].append(rec)
            elif (rec["kind"] in MARK_KINDS
                  and rec["severity"] in FIXABLE_SEVERITIES):
                # Both: §5.9 embeds the two quotes now, §5.8.3 leaves the
                # decision to the owner. Neither replaces the other.
                out["mark"].append(rec)
                out["owner"].append(rec)
            elif rec["kind"] in OWNER_KINDS or rec["severity"] == "high":
                out["owner"].append(rec)
            else:
                out["dropped"].append(rec)
    return out


def skipped_checks(lint: dict) -> list[str]:
    """The book-backed lint checks that could not run.

    qc_lint records them when `Text BOOKS/` is missing or `--no-books` was
    passed: `impossible_citation` and `value_unsourced` both need the PDFs. A
    run without them has not checked a single citation, while §5.11's badge
    would still tell the learner "נבדק מול ספרי הלימוד".
    """
    return sorted(str(_get(s, "check", default=s))
                  for s in (lint.get("skipped") or ()))


def status_holds(*, lint: dict, unchecked: int = 0, uncovered: int = 0,
                 owner_items: Sequence = (), unverified_high: int = 0,
                 agy_versions: Sequence[str] = ()) -> list[str]:
    """Every reason this chapter may not be called clean, in English, for the
    verdict's notes. Empty means nothing is holding it.

    §2.3's fail-closed rule has an edge that is easy to miss and was missed: a
    chapter must not "look clean because a file was absent". Two of these are
    gaps in the CHECK rather than defects in the material, and both were
    reaching `approved`:

    - `unchecked` (§5.8.2) — locked values the judge returned no verified
      source quote for.
    - `uncovered` (§5.8.2, "exactly one row per unit") — units no verified call
      returned a coverage row for. An empty coverage list is not evidence of
      anything; it is the shape a judge that answered from memory returns, and
      §5.8.2's echo check exists to catch exactly that.

    `skipped` is deliberately NOT here. It is a weaker hold, applied by
    chapter_status only against `approved` — see the note there.
    """
    holds = []
    if owner_items:
        holds.append(f"{len(owner_items)} findings need the owner")
    if unverified_high:
        holds.append(f"{unverified_high} unverified findings claimed high "
                     f"severity")
    if unchecked > MAX_UNCHECKED:
        holds.append(f"{unchecked} locked values were never checked against a "
                     f"source (limit {MAX_UNCHECKED})")
    if uncovered:
        holds.append(f"{uncovered} units got no coverage row from any verified "
                     f"call")
    versions = sorted({str(v) for v in agy_versions if str(v or "").strip()})
    if len(versions) > 1:
        # §4: "the agy version is recorded on every call; if it changes
        # mid-run, run the atomoxetine known-answer probe ... and stop if it
        # fails." Two binaries inside ONE chapter is the same event at its
        # smallest, and the two calls are then not comparable: the verdict says
        # `approved` on the strength of an A call one model answered and a B
        # call another did. The run-level guard probes and stops; this holds
        # the chapter that straddles the seam so the report says where it is.
        holds.append("two agy versions answered this chapter: "
                     + ", ".join(versions))
    return holds


def chapter_status(*, judged: bool, lint: dict, fixes: Sequence,
                   owner_items: Sequence, unverified_high: int,
                   unchecked: int = 0, uncovered: int = 0,
                   agy_versions: Sequence[str] = ()) -> str:
    """The one word that names the chapter's next action. §5.9's status list.

    Order matters and is not alphabetical: `unjudged` first because a chapter
    with no verified verdict has not been checked at all (§2.3 forbids calling
    it approved), then the two states a person has to clear, then `fixed`, then
    `approved`.
    """
    if not judged:
        return "unjudged"
    blocking = set(lint.get("blocking") or ())
    if status_holds(lint=lint, unchecked=unchecked, uncovered=uncovered,
                    owner_items=owner_items,
                    unverified_high=unverified_high,
                    agy_versions=agy_versions):
        return "needs_owner"
    if blocking & {"missing_asset", "asset_unparsable"}:
        return "needs_regeneration"
    if blocking:
        # A blocking lint check the judge does not fix — an unknown quiz type,
        # a second key on a single-answer item, an impossible citation. It is a
        # defect in released material, so the chapter cannot pass; it needs a
        # person, not a rewrite.
        return "needs_owner"
    if fixes:
        return "fixed"
    # Last, and only against `approved`. §2.3's sentence is that a chapter must
    # not "look clean because a file was absent", and `approved` is the only
    # status that claims cleanliness: it is what §7 lets the content queue
    # resume on and what draws an unqualified "נבדק מול ספרי הלימוד" badge. A
    # `fixed` chapter makes no such claim — it ships visible errata beside the
    # material — so a skipped citation check does not downgrade it, and a run
    # without the books still publishes the corrections it did find.
    if skipped_checks(lint):
        return "needs_owner"
    return "approved"


# ---------------------------------------------------------------------------
# the per-chapter pipeline


class ChapterRun:
    """One chapter, one journal, one verdict. Resumable at every step."""

    def __init__(self, tag: str, *, deps: Deps, opts: Options):
        self.tag = tag
        self.deps = deps
        self.opts = opts
        self.root = opts.qc_root()
        self.dir = opts.chapter_dir(tag)
        self.work = opts.work_dir(tag)
        self.journal = qc_store.Journal(tag, root=self.root)
        self.started = _now_iso()
        self.assets: dict[str, str | None] = {}     # post-hygiene text, or None
        self.inputs: dict[str, dict] = {}           # released sha/id per asset
        self.hygiene: dict = {}
        self.lint: dict = {}
        # The same checks on the bytes as published, before hygiene repairs them
        # in memory. See hygiene_and_lint for why one measurement was not enough.
        self.released_lint: dict = {}
        self.plan: list[PlannedCall] = []
        self.judgments: list[Judgment] = []
        self.fixes: list[dict] = []
        self.owner_items: list[dict] = []
        self.rounds = 0
        self.outputs: dict[str, dict] = {}
        self.notes: list[str] = []

    # -- persistence -------------------------------------------------------
    def _path(self, name: str) -> Path:
        return self.dir / name

    def _load(self, name: str, default=None):
        return qc_store.read_json(self._path(name), default=default)

    def _save(self, name: str, obj) -> Path:
        return qc_store.write_json_atomic(self._path(name), obj)

    # -- 1. fetch + snapshot ----------------------------------------------
    def fetch(self) -> None:
        """The release's own bytes, plus the pristine snapshot behind them.

        Only text is downloaded. The binary assets (audio.mp3, video.mp4, the
        plate PNGs) are recorded as present by name: qc_lint's completeness
        check needs to know they exist, and nothing here reads them — pulling a
        90MB video for 68 chapters would be an hour of transfer for a boolean.
        """
        gh = self.deps.gh_or_default()
        dest = self.work / "release"
        if self.journal.done("fetched") and (dest / ".fetched.json").exists():
            saved = qc_store.read_json(dest / ".fetched.json") or {}
            self.inputs = saved.get("inputs", {})
            self.assets = {n: (_read_text(dest / n) if v else None)
                           for n, v in saved.get("assets", {}).items()}
            return
        listing = gh.list_assets(self.tag)
        files = qc_store.fetch(self.tag, dest, gh=gh)
        assets: dict[str, str | None] = {}
        for a in listing:
            got = files.get(a.name)
            if got and qc_units.is_judged_asset(a.name):
                assets[a.name] = _read_text(Path(got["path"]))
                self.inputs[a.name] = {"sha256": got["sha256"],
                                       "asset_id": got["asset_id"],
                                       "size": got["size"]}
            else:
                assets[a.name] = None
        self.assets = assets
        # §2.5 "never lose an asset": the snapshot is taken before anything in
        # this run can write, and it refuses rather than overwrite.
        manifest = qc_store.snapshot(self.tag, gh=gh, root=self.root)
        qc_store.write_json_atomic(
            dest / ".fetched.json",
            {"inputs": self.inputs,
             "assets": {n: bool(v is not None) for n, v in assets.items()}})
        self.journal.mark("fetched", assets=sorted(assets),
                          judged=sorted(self.inputs),
                          snapshot=len(manifest.get("assets", [])))

    # -- 2. hygiene + lint -------------------------------------------------
    def hygiene_and_lint(self) -> None:
        if self.journal.done("linted") and self._load("lint.json"):
            self.lint = self._load("lint.json")
            self.released_lint = self._load("lint_released.json", default={}) or {}
            self.hygiene = self._load("hygiene.json", default={}) or {}
            for name, text in (self.hygiene.get("assets") or {}).items():
                self.assets[name] = text
            return
        # TWO measurements, because they answer different questions and the run
        # only ever recorded one of them.
        #
        # `lint_released` is the state of the BYTES THE LEARNER HAS. It runs
        # before hygiene touches anything, and the offline checks only: the
        # book-backed ones are the expensive half and a citation does not move
        # when an escape is stripped around it.
        #
        # `lint` stays what it was — the full check on the cleaned text, which is
        # what decides whether a chapter still needs the judge or the owner.
        #
        # Why this matters (measured over the 144 released snapshots on
        # 2026-09-26): the post-hygiene lint reported 34 hygiene_latex flags in
        # 18 chapters, no markers, no sign-offs and no skewed answer keys. The
        # released bytes carry 925 LaTeX contaminations in 126 chapters, 117
        # NotebookLM markers, 24 chatbot sign-offs and 53 skewed keys. Every one
        # of those had been repaired IN MEMORY before the check ran, and under
        # --dry-run or --no-upload the repair is never published — so the run
        # declared clean the very chapters whose published material was not.
        self.released_lint = qc_lint.lint_chapter(
            self.tag, dict(self.assets), use_books=False)
        self.hygiene = self._run_hygiene()
        for name, text in (self.hygiene.get("assets") or {}).items():
            self.assets[name] = text
        self.lint = qc_lint.lint_chapter(
            self.tag, self.assets, use_books=self.opts.use_books)
        self._save("hygiene.json", self.hygiene)
        self._save("lint.json", self.lint)
        self._save("lint_released.json", self.released_lint)
        self.journal.mark("linted", counts=self.lint.get("counts"),
                          blocking=self.lint.get("blocking"),
                          released_counts=self.released_lint.get("counts"),
                          hygiene=len(self.hygiene.get("changes") or []))

    def _run_hygiene(self) -> dict:
        """§5.9's presentation clean-up, before the judge reads anything.

        A missing qc_fix is not fatal here: hygiene is a cleanup, and judging
        dirty text still produces a valid verdict — it just produces spans that
        quote the dirt. So it is recorded as skipped and the run continues,
        unlike a missing judge, which stops the chapter.
        """
        try:
            res = self.deps.hygiene(tag=self.tag, assets=dict(self.assets),
                                    root=str(self.root)) or {}
        except NotWired as exc:
            self.notes.append(f"hygiene skipped: {exc}")
            return {"skipped": str(exc), "assets": {}, "changes": []}
        return {"assets": dict(_get(res, "assets", default={}) or {}),
                "changes": list(_get(res, "changes", "fixes", default=[]) or [])}

    # -- 3. sources --------------------------------------------------------
    def build_plan(self, *, round_no: int = 0) -> list[PlannedCall]:
        """One bundle per call, from qc_sources.

        Re-planned on every re-judge round rather than reused: a Tier-2 patch
        changes the unit text, so artifacts.md has to be rebuilt, and §5.7 gives
        every call a fresh nonce and fresh page codes. Reusing round 0's bundle
        would let a judge that memorised the codes pass the canary without
        reading anything.

        A RESUME also re-plans, and that is deliberate: `_qc/work/` is
        gitignored and may have been swept, so the old bundle is not reliably
        there. It costs local PDF work, never an agy call — the judgments
        already on disk are reused as they are, and each one carries the
        manifest of the bundle IT read, so the verdict's provenance still
        describes what was actually judged.
        """
        units = self.units()
        if self.opts.scope is not None:
            # Out of scope means out of the BUNDLE, never out of the chapter:
            # uncovered_units() still counts these, so publishable() holds their
            # assets and the verdict cannot call the chapter fully covered.
            left = sorted({u.artifact for u in units
                           if u.artifact not in self.opts.scope})
            units = [u for u in units if u.artifact in self.opts.scope]
            if left and round_no == 0:
                self.notes.append("לא נשפטו בכוונה (מחוץ להיקף): "
                                  + ", ".join(left))
        cites = qc_cite.cites_by_unit(units)
        out_dir = self.work / f"round{round_no}"
        out_dir.mkdir(parents=True, exist_ok=True)
        raw = self.deps.sources(tag=self.tag, units=units, cites=cites,
                                assets=dict(self.assets), work_dir=out_dir,
                                page_limit=self.opts.page_limit,
                                root=str(self.root))
        calls = [_as_call(c, i) for i, c in enumerate(raw or [])]
        self.plan = calls
        self._save("plan.json", [{"call_id": c.call_id, "kind": c.kind,
                                  "units": c.units, "pages": c.pages,
                                  "bundle": str(c.bundle) if c.bundle else None,
                                  "artifacts": (str(c.artifacts)
                                                if c.artifacts else None),
                                  **c.sizes()} for c in calls])
        if round_no == 0:
            self.journal.mark("bundle_built", calls=[c.call_id for c in calls],
                              pages=sum(c.pages for c in calls))
        return calls

    def units(self) -> list:
        text_assets = {n: t for n, t in self.assets.items() if t is not None}
        return qc_units.units(text_assets)

    # -- 4. judge ----------------------------------------------------------
    def judge_all(self, calls: Sequence[PlannedCall], *,
                  round_no: int = 0) -> list[Judgment]:
        out: list[Judgment] = []
        for call in calls:
            j = self._judge_one(call, round_no=round_no)
            out.append(j)
            self.judgments = out
            # Marked after every call, not once at the end: a quota stop raises
            # out of the NEXT call, and a journal that only learned about this
            # one at the end of the loop would report the chapter as standing
            # where it stood 25 minutes ago.
            self._mark_judge_steps(out)
            if not j.ok:
                # Stop the chapter at the first call that did not verify. The
                # remaining calls would cost 25 minutes each to produce a
                # verdict this chapter can no longer reach (§2.3: fail closed),
                # and the journal keeps what did verify.
                break
        return out

    def _judge_one(self, call: PlannedCall, *, round_no: int) -> Judgment:
        cached = self._judgment_path(call, round_no)
        # The cached file alone, deliberately, and not "cached AND the journal
        # step is done": the journal's steps are per KIND (§5.5 has judged_A,
        # not judged_A2) while the cache is per CALL, and a quota stop lands
        # between the write below and the mark in judge_all(). Trusting the
        # step there would re-run a call that already has a verified answer —
        # 25 minutes of agy time and a quota slot, which is exactly what the
        # journal exists to stop. The write is atomic, so the file existing
        # means the judgment is whole. Delete _qc/<tag>/judgments/ to force a
        # re-judge.
        if cached.exists():
            j = Judgment.from_json(qc_store.read_json(cached) or {})
            # The same proofs the fresh answer had to pass. A cache entry is
            # written only for a call that verified, so this fires on bytes
            # something else put there — an older schema, a half-written
            # resume, a hand edit — and §2.3 says a chapter must never be
            # cleared on those. Without it the gate on a resumed run is a JSON
            # field in `_qc/<tag>/judgments/` rather than a proof.
            proof = _proof_failure(j.canary, j.tempmedia_ok)
            if j.ok and proof:
                j.ok = False
                j.reason = f"cached judgment: {proof}"
                self.notes.append(
                    f"{call.call_id}: התשובה השמורה נפסלה — {proof}")
            return j
        raw = self.deps.judge(tag=self.tag, call=call.raw, plan=call,
                              model=self.opts.model, root=str(self.root),
                              work_dir=self.work, round_no=round_no)
        j = _as_judgment(raw, call)
        # ONLY a verified answer is cached. The cache exists to stop a resume
        # paying twice for a call that already answered, and a call that failed
        # its canary did not answer — it produced the confident invented JSON of
        # the 2026-09-18 incident. Caching that pinned the chapter to `unjudged`
        # for ever: every later run, including one on a fixed agy, read the
        # failure back off disk and never asked the judge again, so the only way
        # out was deleting _qc/<tag>/judgments/ by hand. §5.5 journals a failure
        # so the NEXT run picks it up, which is the opposite of remembering it.
        if j.ok:
            qc_store.write_json_atomic(cached, j.to_json())
        # §4: agy's store is 2.9GB on a disk that is 91% full, and a chapter is
        # three bundles. Delete only once the judgment is on disk — the brain
        # directory is the evidence the verdict rests on until then.
        if j.conversation_id:
            try:
                freed = self.deps.agy_cleanup(j.conversation_id,
                                              workspace=j.workspace)
                if freed:
                    _print(f"      ניקוי agy: {freed / 1e6:.0f}MB")
            except Exception as exc:                      # noqa: BLE001
                self.notes.append(f"agy cleanup failed for "
                                  f"{j.conversation_id}: {exc}")
        return j

    def _judgment_path(self, call: PlannedCall, round_no: int) -> Path:
        return self.dir / "judgments" / f"round{round_no}-{call.call_id}.json"

    def _mark_judge_steps(self, judgments: Sequence[Judgment]) -> None:
        """A judging step is done when every call PLANNED for it verified.

        Planned, not answered: a chapter with no legends has no C call at all,
        and a judged_C that never gets marked would leave resume() pointing at
        a step that has nothing to do forever. An empty plan marks nothing —
        there the chapter really is at its beginning.
        """
        if not self.plan:
            return
        planned: dict[str, list[str]] = {}
        for c in self.plan:
            planned.setdefault(CALL_STEP.get(c.kind, LAST_JUDGE_STEP),
                               []).append(c.call_id)
        verified = {j.call_id for j in judgments if j.ok}
        for step in ("judged_A", "judged_B", "judged_C"):
            ids = planned.get(step, [])
            if all(i in verified for i in ids):
                self.journal.mark(
                    step, calls=ids,
                    findings=sum(len(j.findings) for j in judgments
                                 if j.call_id in ids))

    # -- 5. fixes ----------------------------------------------------------
    def apply_tier1(self, findings: Sequence[dict]) -> list[dict]:
        """Embed the note. Nothing is rewritten (§5.9, owner decision 2)."""
        if not findings:
            return []
        try:
            res = self.deps.tier1(tag=self.tag, findings=list(findings),
                                  assets=dict(self.assets), units=self.units(),
                                  root=str(self.root)) or {}
        except NotWired as exc:
            # Without the fixer the verified error cannot be shown in the app,
            # and showing it is the whole point (§2.4 "mark, never hide"). The
            # finding therefore goes to the owner rather than quietly nowhere.
            self.notes.append(f"Tier 1 unavailable: {exc}")
            self.owner_items.extend(findings)
            return []
        for name, text in (_get(res, "assets", default={}) or {}).items():
            self.assets[name] = text
        fixes = [dict(f, tier=1) for f in
                 (_get(res, "fixes", default=[]) or [])]
        # §5.9: a Tier-1 record qc_fix could not place — an unknown unit, a
        # guide span that is not unique inside its anchored section — comes back
        # flagged `needs_owner`, with `embedded: "none"` and having changed no
        # asset at all. Counted as a fix it would report the chapter `fixed`
        # while the error it names is embedded nowhere and shown to nobody,
        # which is precisely what §2.4 ("mark, never hide") forbids. The rows
        # stay in fixes.json — that is the durable record, and the errata page
        # still shows the learner the error — but the FINDING goes to the owner
        # queue, and chapter_status holds the chapter on it.
        held = [f for f in fixes if f.get("needs_owner")]
        if held:
            # The finding rather than the fix row: the row has no `problem`,
            # and `problem` is the sentence the owner's queue shows him.
            by_id = {str(_get(f, "id", default="")): f for f in findings}
            self._add_owner([
                by_id.get(str(f.get("finding_id") or f.get("id") or ""), f)
                for f in held])
            self.notes.append(
                f"{len(held)} תיקוני Tier 1 לא שובצו ועברו לבעלים: "
                + "; ".join(f"{f.get('unit', '?')} ({f.get('reason', '')})"
                            for f in held[:5]))
        return fixes

    def _prior_fixes(self) -> list[dict]:
        doc = self._load("fixes.json", default=None)
        rows = doc.get("fixes") if isinstance(doc, dict) else doc
        return [f for f in (rows or []) if isinstance(f, dict)]

    @staticmethod
    def _fix_key(f: dict) -> str:
        return str(f.get("finding_id") or f.get("id") or "")

    def withdrawn_ids(self) -> set[str]:
        """The finding ids the owner took back, out of `_qc/<tag>/fixes.json`.

        §5.9's `withdrawn` is a DECISION, not a cache entry. qc_revert writes it
        when the owner reads an erratum and rejects it, and that file is the
        only record anywhere that he ever looked at one and said no. Re-applying
        such a finding on the next pass — a resumed backlog, a second `--all`,
        a re-run after a fix elsewhere — puts the note back on his material and
        tells him the pipeline did not listen.
        """
        return {k for f in self._prior_fixes()
                if str(f.get("status", "")).strip().lower() == "withdrawn"
                for k in (self._fix_key(f),) if k}

    def merged_fixes(self) -> list[dict]:
        """This run's fixes laid over the ones already on disk.

        fixes.json is the chapter's history, not this run's output: qc_revert
        marks rows `withdrawn` in it and qc_fix.apply_fix replays it onto the
        pristine snapshot. Rewriting it from the current run alone erased every
        withdrawal the owner had made — and because the judgment is cached, a
        second run reproduced the same finding and the file came back looking
        as though he had never refused it.

        A withdrawn row is never overwritten. Everything else is replaced by
        this run's version, which is how a re-judge corrects an earlier note.
        """
        merged: dict[str, dict] = {}
        order: list[str] = []
        for f in self._prior_fixes():
            key = self._fix_key(f)
            if key not in merged:
                order.append(key)
            merged[key] = f
        for f in self.fixes:
            key = self._fix_key(f)
            prior = merged.get(key)
            if prior and str(prior.get("status", "")).strip().lower() == \
                    "withdrawn":
                continue
            if key not in merged:
                order.append(key)
            merged[key] = f
        return [merged[k] for k in order]

    def apply_tier2(self, findings: Sequence[dict]) -> list[dict]:
        if not findings:
            return []
        try:
            res = self.deps.tier2(tag=self.tag, findings=list(findings),
                                  assets=dict(self.assets), units=self.units(),
                                  root=str(self.root), model=self.opts.model,
                                  work_dir=self.work) or {}
        except NotWired as exc:
            self.notes.append(f"Tier 2 unavailable: {exc}")
            return []
        for name, text in (_get(res, "assets", default={}) or {}).items():
            self.assets[name] = text
        return [dict(f, tier=2) for f in (_get(res, "fixes", default=[]) or [])]

    def tier2_enabled(self) -> bool:
        """§5.10: "Tier 2 stays off unless the state flag is on".

        The flag lives in _qc/state.json and is the owner's to set, after §6's
        calibration puts verified false positives at or under 2%. A CLI switch
        for it was deliberately not added: the gate is a measurement, not a
        convenience.
        """
        if self.opts.calibration or self.opts.dry_run:
            return False
        return bool(qc_store.load_state(root=self.root).get("tier2_enabled"))

    # -- 6. upload ---------------------------------------------------------
    def covered_units(self) -> set[str]:
        """Every unit id a VERIFIED call returned a coverage row for.

        Verified only: a failed call's coverage rows are part of the same
        invented JSON its findings are, and counting them would let the 2026-09-18
        failure mode — confident output from a call that received no PDF — mark
        a chapter as covered.
        """
        out: set[str] = set()
        for j in self.judgments:
            if not j.ok:
                continue
            for row in j.coverage:
                uid = str(_get(row, "unit", "uid", default="") or "")
                if uid:
                    out.add(uid)
        return out

    def uncovered_units(self) -> list[str]:
        try:
            uids = {u.uid for u in self.units()}
        except Exception:                                 # noqa: BLE001
            # An asset that stopped parsing is the lint's problem to report;
            # it must not turn the coverage gate into a crash.
            return []
        return sorted(uids - self.covered_units())

    def publishable(self, changed: dict[str, str]) -> tuple[dict, dict]:
        """Split the corrected assets into (a verdict covers it, it does not).

        §5.12 states the rule from the uploader's side — "Every uploader …
        refuses unless the chapter's verdict covers that asset's sha256" — and
        this is the same rule on the writing side. An asset none of whose units
        a verified call judged has no verdict behind it: the only thing that
        touched it was the hygiene pass, and a clean-up is not a check.

        Not hypothetical. §5.7 splits a chapter by page budget, so a call whose
        bundle would blow the limit leaves its units to another call — and a
        quota stop (13h33m, measured) can land between the two. Without this
        split, facts.json would be republished on the strength of a judge that
        never read one of its units.

        Every unit of the asset must be covered, not merely one: a partially
        judged asset is a partially checked asset, and §2.3 fails closed.
        """
        by_asset: dict[str, set[str]] = {}
        try:
            for u in self.units():
                by_asset.setdefault(u.artifact, set()).add(u.uid)
        except Exception:                                 # noqa: BLE001
            return {}, dict(changed)
        covered = self.covered_units()
        ok, held = {}, {}
        for name, text in changed.items():
            uids = by_asset.get(name) or set()
            (ok if uids and uids <= covered else held)[name] = text
        return ok, held

    def changed_assets(self) -> dict[str, str]:
        out = {}
        for name, text in self.assets.items():
            if text is None or name not in self.inputs:
                continue
            if _text_sha(text) != self.inputs[name]["sha256"]:
                out[name] = text
        return out

    def resume_uploads(self) -> dict[str, str]:
        """Finish, or undo, a swap that stopped part-way. §5.5, before anything.

        The swap is five stages and the laptop sleeps: a stop between
        `orig_renamed` and `promoted` leaves the release with
        `<name>.pre-qc-<date>` and `<name>.qc-<sha8>` and NO `<name>`, so the
        app 404s that asset for every learner. qc_store ships the recovery
        (`resume_upload`, and the `pending_uploads` record that says which name
        it is) and nothing outside its own tests ever called it: the next run
        reached `safe_upload`, which refuses a mid-swap asset with
        UploadUnresolved, and the chapter was counted as a crash instead.

        It runs before `fetch` so the release is whole before anything is
        downloaded from it, and it returns `{asset: sha256 now live}` — after a
        roll-forward the published bytes ARE the corrected ones, and a verdict
        that went on comparing them to the pre-QC sha256 would call the chapter
        stale and refuse to upload anything else in it.
        """
        pending = self.journal.pending_uploads()
        if not pending:
            return {}
        gh = self.deps.gh_or_default()
        live: dict[str, str] = {}
        for name in pending:
            local = self._corrected_bytes(name)
            res = qc_store.resume_upload(
                self.tag, name, gh=gh, journal=self.journal, root=self.root,
                local_path=local if local.exists() else None,
                # Nothing to roll forward WITH: qc_store puts the original back
                # rather than leave the release without the asset, and the next
                # pass recomputes the fix.
                rollback=not local.exists())
            stage = str(res.get("stage") or "")
            sha = str(res.get("new_sha256") or "")
            self.notes.append(
                f"העלאה שנקטעה הושלמה ({name}): {stage}"
                + (f" — {res['resumed']}" if res.get("resumed") else ""))
            _print(f"  [resume] {name}: {stage}")
            if stage == "done" and sha:
                live[name] = sha
                self.outputs[name] = {"sha256": sha, "uploaded": True,
                                      "resumed": True}
        return live

    def _corrected_bytes(self, name: str) -> Path:
        """Where this chapter's corrected `name` was written, if it is still
        there. `_qc/work/` is gitignored and may have been swept."""
        here = self.work / "out" / name
        if here.exists():
            return here
        recorded = str((self.journal.upload_record(name) or {}).get(
            "local_path") or "")
        return Path(recorded) if recorded else here

    def _adopt_live(self, live: dict[str, str]) -> None:
        """Treat a resumed asset's published bytes as the ones on the release.

        `changed_assets` compares this pass's text to `inputs[...]["sha256"]`,
        and `safe_upload` refuses (Stale) unless the live asset still hashes
        that. After a roll-forward both are the corrected sha256 — recording it
        keeps the chapter from trying to swap an asset that is already swapped,
        and keeps a repair from reading as "re-judge this chapter".
        """
        for name, sha in live.items():
            if name in self.inputs:
                self.inputs[name] = {**self.inputs[name], "sha256": sha,
                                     "resumed": True}

    def upload(self, changed: dict[str, str]) -> None:
        gh = self.deps.gh_or_default()
        for name, text in sorted(changed.items()):
            local = _write_text(self.work / "out" / name, text)
            res = qc_store.safe_upload(
                self.tag, name, local, self.inputs[name]["sha256"],
                gh=gh, journal=self.journal, root=self.root)
            self.outputs[name] = {"sha256": _text_sha(text),
                                  "uploaded": bool(res.get("changed")),
                                  "pre_qc_name": res.get("pre_qc_name")}
        self.journal.mark("uploaded", assets=sorted(changed))

    def _qc_assets(self, verdict: dict) -> dict:
        """`{asset: {status, sha256}}` — §5.10's "per-asset sha256", in the
        shape app/quiz_logic.parse_qc_json reads.

        It used to be `{asset: sha256}`, and the app reads a bare string value
        as the asset's STATUS, not its hash. Three things followed: the sha256
        never reached the app at all (so it could not tell whether the bytes it
        holds are the bytes that were judged), every asset reported itself
        "checked" because a hex digest is not in the not-checked vocabulary,
        and video.mp4 — named only in the sibling `not_checked` list, which the
        app does not read — drew no "וידאו: לא נבדק" line, leaving an unchecked
        video under a "checked" badge, which reads as a video that passed.

        Inputs first, outputs over them: a corrected asset must be listed at
        the sha256 the app will actually download, and an untouched one still
        has to be listed or the app cannot tell "checked and clean" from "never
        looked at".

        The chapter's status is stamped on an asset only when the checks that
        would justify it ran. §2.3 — "a chapter cannot look clean because a file
        was absent" — was enforced against `approved` alone, on the reasoning
        that a `fixed` chapter "makes no such claim". The app disagrees: it
        reads this status per asset, "fixed" is not in quiz_logic._NOT_CHECKED,
        and the line comes out "קוויז: נבדק, ללא תיקונים" on a chapter whose
        citation and locked-value checks never opened a book. When they were
        skipped, the asset says so (PARTIAL_ASSET_STATUS) and names which.
        """
        merged = {**verdict.get("inputs", {}), **verdict.get("outputs", {})}
        status = verdict["status"]
        skipped = skipped_checks(verdict.get("lint") or self.lint or {})
        out = {n: {"status": PARTIAL_ASSET_STATUS if skipped else status,
                   "sha256": (v or {}).get("sha256", ""),
                   **({"skipped": skipped, "chapter_status": status}
                      if skipped else {})}
               for n, v in sorted(merged.items())}
        # Unconditionally, even for a chapter that has no video: the claim is
        # about this PASS, not about the release. §3 leaves video to v2, and the
        # badge has to say so on every chapter or the silence reads as a pass.
        for name in NOT_CHECKED_ASSETS:
            out.setdefault(name, {"status": "not_checked", "sha256": ""})
        return out

    def write_qc_json(self, verdict: dict) -> Path:
        """The per-release `qc.json` the app reads (§5.10).

        Written locally always, uploaded only with the rest. A first upload is a
        plain `gh release upload` because there is nothing to swap: safe_upload
        replaces an asset that already exists, and asking it to create one would
        report the chapter stale.
        """
        doc = {
            "tag": self.tag,
            "status": verdict["status"],
            # `date` is the key app/quiz_logic.parse_qc_json reads (it also
            # takes `judged_at`). It was written only as `checked_at`, which
            # the app does not look at, so §5.11's badge rendered as
            # "נבדק מול ספרי הלימוד ב-?, N תיקונים" — a chapter that says it
            # was checked and cannot say when. `checked_at` is kept beside it:
            # it carries the time as well as the date and it is already in
            # every verdict.json on disk.
            "date": str(verdict["finished_at"])[:10],
            "checked_at": verdict["finished_at"],
            "agy_version": verdict["agy"].get("version"),
            "model": verdict["agy"].get("model"),
            "assets": self._qc_assets(verdict),
            "errata": errata_lines(verdict),
            "not_checked": list(NOT_CHECKED_ASSETS),   # §3: video is v2
            # The book-backed checks that never ran (`--no-books`, or no
            # `Text BOOKS/` on the machine). Nothing in qc.json carried this,
            # so the app could not qualify §5.11's "נבדק מול ספרי הלימוד" even
            # if it wanted to — and §2.3 is the rule that a chapter must not
            # look clean because a file was absent. Empty on a full run.
            "skipped": skipped_checks(verdict.get("lint") or self.lint or {}),
        }
        path = self._save("qc.json", doc)
        # Not published for an `unjudged` chapter: the app reads qc.json to
        # draw the "נבדק מול ספרי הלימוד" badge (§5.11), and a chapter whose
        # canary failed has not been checked against anything.
        if self.opts.uploads_allowed and verdict["status"] != "unjudged":
            gh = self.deps.gh_or_default()
            try:
                existing = gh.asset(self.tag, "qc.json")
                if existing is None:
                    gh.upload(self.tag, path, "qc.json")
                    self.outputs["qc.json"] = {"sha256": qc_store.sha256_file(path),
                                               "uploaded": True}
                else:
                    qc_store.safe_upload(self.tag, "qc.json", path,
                                         existing.sha256 or
                                         qc_store.live_sha256(gh, self.tag,
                                                              "qc.json", existing),
                                         gh=gh, journal=self.journal,
                                         root=self.root)
                    self.outputs["qc.json"] = {"sha256": qc_store.sha256_file(path),
                                               "uploaded": True}
            except qc_store.QcStoreError as exc:
                self.notes.append(f"qc.json not uploaded: {exc}")
        return path

    # -- the run -----------------------------------------------------------
    def run(self) -> dict:
        t0 = self.deps.now()
        # First, before a byte is read and before this run claims the chapter:
        # a previous pass may have died between the two renames of a swap, and
        # until that is finished or undone the release has no asset under that
        # name at all (§5.5, §2.5). qc_store clears the `upload_pending` run
        # state when it lands, so `in_progress` is set after it.
        resumed = self.resume_uploads()
        qc_store.set_run_state(self.tag, "in_progress", root=self.root)
        self.fetch()
        self._adopt_live(resumed)
        self.hygiene_and_lint()

        calls = self.build_plan(round_no=0)
        if self.opts.dry_run:
            return self._dry_run_verdict(calls, t0)

        classified = _empty_classification()
        judged = False
        patched: list[dict] = []
        if not calls:
            self.notes.append("qc_sources planned no calls for this chapter")
        while True:
            judgments = self.judge_all(calls, round_no=self.rounds)
            judged = bool(judgments) and all(j.ok for j in judgments)
            # Only the calls that verified. §5.8's canary is per call, so a
            # verified call's findings are real even when a later call failed —
            # but a FAILED call's findings are exactly the invented JSON of the
            # 2026-09-18 incident and may not be counted, shown or acted on.
            classified = classify_findings([j for j in judgments if j.ok])
            if not judged:
                break
            if self.rounds == 0:
                fixable = self._not_withdrawn(classified["fix"])
                # §5.9's Tier 1 is unconditional, and a `source_conflict` is
                # the kind it was written for ("For source_conflict, both
                # quotes"). It is marked and NOT rewritten: only `fixable`
                # reaches Tier 2 below.
                marked = self._not_withdrawn(classified.get("mark") or [])
                self.fixes.extend(self.apply_tier1(fixable + marked))
                if self.tier2_enabled():
                    patched = self.apply_tier2(fixable)
                    self.fixes.extend(patched)
                self.journal.mark("fixes_applied", n=len(self.fixes),
                                  tier2=len(patched))
            else:
                # This IS the re-judge, so the patched units have now been read
                # again. §5.9: "A re-judge finding on a patched unit reverts the
                # patch, keeps Tier 1, and marks the unit needs_owner." The
                # check has to sit here and not before the round: run on round
                # 0's judgments it would see the very findings that caused the
                # patch and revert every one of them.
                regressions = self._regressions(patched)
                if regressions:
                    self._revert(regressions)
                    self._add_owner(regressions)
                    break
            self._add_owner(classified["owner"])
            # §5.9: a re-judge exists to confirm a REWRITE. Tier 1 embeds a note
            # beside the item and changes no studied content, so re-judging
            # after it would spend 25 minutes of agy time per call re-reading
            # text the judge just approved, and could flag the note itself.
            if not patched or self.rounds + 1 >= self.opts.max_rejudge:
                break
            self.rounds += 1
            self.journal.clear("judged_A", "judged_B", "judged_C", "rejudged")
            calls = self.build_plan(round_no=self.rounds)
        if self.rounds:
            self.journal.mark("rejudged", rounds=self.rounds)

        changed = self.changed_assets()
        # An `unjudged` chapter publishes nothing, not even its hygiene
        # clean-up. §5.12 states the rule from the other side — an uploader
        # refuses unless a verdict covers that asset's sha256 — and a chapter
        # whose canary failed has no verdict. The cleaned bytes stay in
        # _qc/work/ and the next run, which will judge them, uploads them.
        # Within a judged chapter the same rule applies per ASSET: only the
        # ones every unit of which a verified call covered may go up.
        to_upload, uncovered_changed = ({}, dict(changed))
        if judged:
            to_upload, uncovered_changed = self.publishable(changed)
        if to_upload and self.opts.uploads_allowed:
            self.upload(to_upload)
            local = uncovered_changed
        else:
            local = dict(changed)
        for name, text in local.items():
            _write_text(self.work / "out" / name, text)
            self.outputs[name] = {"sha256": _text_sha(text), "uploaded": False}
        if local:
            why = ("הפרק לא נשפט" if not judged else
                   "dry-run" if self.opts.dry_run else
                   "כיול" if self.opts.calibration else
                   "no-upload" if self.opts.no_upload else
                   "אף קריאה מאומתת לא כיסתה את היחידות שלהם")
            self.notes.append(
                f"{len(local)} נכסים תוקנו מקומית ולא הועלו ({why})")

        verdict = self._verdict(classified, judged, t0)
        self._save("verdict.json", verdict)
        self._save("fixes.json", {"tag": self.tag, "updated_at": _now_iso(),
                                  "fixes": self.merged_fixes()})
        self.write_qc_json(verdict)
        self.journal.mark("verdict", status=verdict["status"])
        qc_store.clear_run_state(self.tag, root=self.root)
        return verdict

    def _not_withdrawn(self, findings: Sequence[dict]) -> list[dict]:
        """Drop the findings the owner already refused, and say so once.

        The judgment is cached, so a second run reproduces the identical
        finding; without this filter it is re-embedded and `merged_fixes` then
        has to decide between the owner's `withdrawn` row and a fresh `active`
        one. Filtering here means the question never arises.
        """
        withdrawn = self.withdrawn_ids()
        if not withdrawn:
            return list(findings)
        keep, dropped = [], []
        for f in findings:
            (dropped if str(_get(f, "id", default="")) in withdrawn
             else keep).append(f)
        if dropped:
            self.notes.append(
                f"{len(dropped)} תיקונים שהבעלים ביטל לא הוחלו מחדש: "
                + ", ".join(str(_get(f, "id", default="")) for f in dropped[:5]))
        return keep

    def _add_owner(self, items: Sequence[dict]) -> None:
        """Add to the owner queue, once per finding id.

        A re-judge round classifies the same chapter again, so an untouched
        `source_conflict` comes back a second time. The owner reads this list
        by hand; the same conflict listed twice is a chapter he checks twice.
        """
        seen = {str(_get(f, "id", default="")) for f in self.owner_items}
        for f in items:
            fid = str(_get(f, "id", default=""))
            if fid and fid in seen:
                continue
            seen.add(fid)
            self.owner_items.append(f)

    def agy_versions(self) -> list[str]:
        """Every agy version that answered a call of this chapter.

        §4 records the version "on every call" precisely so that a mid-run
        upgrade can be seen; a verdict that kept only the first one could not
        say that two binaries had answered it.
        """
        return sorted({str(j.agy_version) for j in self.judgments
                       if str(j.agy_version or "").strip()})

    def _regressions(self, patched: Sequence[dict]) -> list[dict]:
        """Verified findings in the re-judge that land on a unit we rewrote."""
        touched = {str(_get(p, "unit", "uid", default="")) for p in patched}
        touched.discard("")
        out = []
        for j in self.judgments:
            for f in j.findings:
                if _is_verified(f) and str(_get(f, "unit", default="")) in touched:
                    out.append(dict(f) if isinstance(f, dict) else {"unit":
                               _get(f, "unit")})
        return out

    def _revert(self, regressions: Sequence[dict]) -> None:
        try:
            res = self.deps.revert(tag=self.tag, findings=list(regressions),
                                   assets=dict(self.assets),
                                   root=str(self.root)) or {}
        except NotWired as exc:
            self.notes.append(f"Tier 2 revert unavailable: {exc}; the patched "
                              f"units are in the owner queue")
            return
        for name, text in (_get(res, "assets", default={}) or {}).items():
            self.assets[name] = text
        self.fixes = [f for f in self.fixes if f.get("tier") != 2]

    def _dry_run_verdict(self, calls: Sequence[PlannedCall], t0: float) -> dict:
        _print(f"  [dry-run] {len(calls)} קריאות מתוכננות:")
        for c in calls:
            _print(f"      {c.brief()}")
        total = sum(c.sizes()["bundle_bytes"] for c in calls)
        _print(f"      סה\"כ bundle: {total / 1e6:.1f}MB, "
               f"{sum(c.pages for c in calls)} עמודים")
        verdict = self._verdict(_empty_classification(), False, t0)
        verdict["dry_run"] = True
        verdict["planned_calls"] = [
            {"call_id": c.call_id, "kind": c.kind, "units": len(c.units),
             "pages": c.pages, **c.sizes()} for c in calls]
        self._save("dry_run.json", verdict)
        qc_store.clear_run_state(self.tag, root=self.root)
        return verdict

    def _verdict(self, classified: dict, judged: bool, t0: float) -> dict:
        # Only the calls that verified, for the same reason classify_findings
        # reads only those: a failed call's counts are part of the invented
        # JSON, and a chapter must never be held OR cleared on them.
        unchecked = sum(j.unchecked for j in self.judgments if j.ok)
        uncovered = self.uncovered_units() if judged else []
        versions = self.agy_versions()
        holds = status_holds(
            lint=self.lint, unchecked=unchecked, uncovered=len(uncovered),
            owner_items=self.owner_items,
            unverified_high=classified.get("unverified_high", 0),
            agy_versions=versions)
        skipped = skipped_checks(self.lint)
        if skipped and not self.fixes:
            holds.append("lint checks that never ran: " + ", ".join(skipped))
        status = chapter_status(
            judged=judged, lint=self.lint, fixes=self.fixes,
            owner_items=self.owner_items,
            unverified_high=classified.get("unverified_high", 0),
            unchecked=unchecked, uncovered=len(uncovered),
            agy_versions=versions)
        # The owner reads REPORT.md, not this function. A chapter held because a
        # check never ran looks identical to one held by a real finding unless
        # the reason is written down.
        if judged and holds:
            self.notes.extend(f"מוחזק: {h}" for h in holds
                              if f"מוחזק: {h}" not in self.notes)
        cov_rows = sum(len(j.coverage) for j in self.judgments)
        by_cov: dict[str, int] = {}
        for j in self.judgments:
            for row in j.coverage:
                s = str(_get(row, "status", default="") or "")
                by_cov[s] = by_cov.get(s, 0) + 1
        return {
            "tag": self.tag,
            "status": status,
            "calibration": self.opts.calibration,
            "started_at": self.started,
            "finished_at": _now_iso(),
            "duration_s": round(self.deps.now() - t0, 1),
            "inputs": self.inputs,
            "outputs": self.outputs,
            "hygiene": self.hygiene,
            "lint": {"counts": self.lint.get("counts", {}),
                     "blocking": self.lint.get("blocking", []),
                     "skipped": self.lint.get("skipped", []),
                     "units": self.lint.get("units", 0)},
            # What the judge actually read, when there was a judge; the plan
            # only when there was not (a dry run, or a chapter that stopped
            # before its first call).
            "bundle": ([{"call_id": j.call_id, "kind": j.kind,
                         "pages": j.pages, "units": len(j.units),
                         "manifest": j.manifest,
                         "bundle_bytes": j.bundle_bytes,
                         "artifacts_bytes": j.artifacts_bytes}
                        for j in self.judgments]
                       or [{"call_id": c.call_id, "kind": c.kind,
                            "pages": c.pages, "units": len(c.units),
                            "manifest": c.manifest, **c.sizes()}
                           for c in self.plan]),
            "judgments": [{"call_id": j.call_id, "kind": j.kind, "ok": j.ok,
                           "reason": j.reason, "reason_code": j.reason_code,
                           "attempts": j.attempts}
                          for j in self.judgments],
            "agy": {
                "version": next((j.agy_version for j in self.judgments
                                 if j.agy_version), ""),
                # Every version this chapter was answered by, not only the
                # first. §4 asks for the version "on every call", and the
                # record the spec wants is lost the moment two calls are
                # answered by two binaries — which is also exactly when it
                # matters (see `status_holds`).
                "versions": self.agy_versions(),
                "model": next((j.model for j in self.judgments if j.model),
                              self.opts.model or ""),
                "prompt_sha256": {j.call_id: j.prompt_sha256
                                  for j in self.judgments},
                # §5.8.1's mechanical proofs, per call, so an audit of this
                # file can tell a judged chapter from one answered from memory.
                "tempmedia_ok": {j.call_id: j.tempmedia_ok
                                 for j in self.judgments},
                "tool_audit": {j.call_id: j.tool_audit
                               for j in self.judgments},
                "conversations": {j.call_id: j.conversation_id
                                  for j in self.judgments},
                "attempts": {j.call_id: j.attempts for j in self.judgments},
            },
            "raw": {j.call_id: j.raw_path for j in self.judgments},
            "canary": {j.call_id: j.canary for j in self.judgments},
            "coverage": {"rows": cov_rows, "units": self.lint.get("units", 0),
                         "by_status": by_cov,
                         "uncovered_units": uncovered,
                         "unchecked_locked_values": unchecked},
            "holds": holds,
            "findings": {
                "by_kind": classified.get("by_kind", {}),
                "by_severity": classified.get("by_severity", {}),
                "verified_fixable": classified.get("fix", []),
                "owner": self.owner_items,
                "unverified": len(classified.get("unverified", [])),
                "unverified_high": classified.get("unverified_high", 0),
                "dropped": len(classified.get("dropped", [])),
            },
            "fixes": self.fixes,
            "rejudge_rounds": self.rounds,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# reports


def _source_block(f, *, second: bool = False) -> dict | None:
    """The nested `{book, printed_page, quote}` app/quiz_logic._sources reads.

    `second` picks the other side of a `source_conflict`, which §5.8 requires be
    shown with BOTH quotes (019 atomoxetine: StahlKids p.69 "2-8 weeks" against
    p.74 "8-12 weeks" — a learner shown only one of them learns the wrong half).
    """
    pre = "second_" if second else ""
    book = _get(f, f"{pre}book", default="") or ""
    page = _get(f, f"{pre}printed", f"{pre}printed_page", f"{pre}page",
                default="")
    quote = _get(f, f"{pre}quote" if second else "source_quote",
                 f"{pre}source_quote", default="") or ""
    if not (book or quote):
        return None
    return {"book": book, "printed_page": page, "quote": quote}


def book_name_he(f: Any, book: str, *, prefix: str = "") -> str:
    """The book as the owner reads it: the fix row's own `book_he` first.

    qc_fix writes `book_he` onto every fix row (`_fix_record`) and the table is
    repeated here only for a row that predates it, or one written by something
    else. An unknown key passes through unchanged — a raw label the owner can
    still read beats a silently wrong book name on a correction.
    """
    named = _get(f, f"{prefix}book_he", f"{prefix}source_name_he",
                 default="") or ""
    if named:
        return str(named)
    key = str(book or "").strip()
    return BOOK_HE.get(key, key)


def errata_lines(verdict: dict) -> list[dict]:
    """One erratum per correction, in the shape app/quiz_logic.parse_qc_json
    reads — plus the Hebrew line §5.10 asks the report for:
    "החומר שפורסם אמר → המקור אומר (ספר עמ' N)".

    WHY THE SHAPE IS THE APP'S AND NOT THIS MODULE'S. qc.json is the only thing
    that carries a verdict from the QC pass to the learner (§5.10 writes it,
    §5.11 draws it), and the owner studies in the app and nowhere else (owner
    decision 1, 2026-09-18). quiz_logic.erratum_view → qc_mark → _sources takes
    the book, the printed page and the quote out of a NESTED
    `source: {book, printed_page, quote}`, the corrected value out of
    `now`/`correction`, the asset out of `asset`, and it drops the record
    entirely unless `kind` is exactly "content".

    This function used to emit `book`, `page` and a string `source` as three
    flat siblings, with no `asset`, no `now` and no `kind`. Every erratum
    therefore reached the app with no source at all, no corrected value, and
    attached to no asset — so the per-asset line read "קוויז: נבדק, ללא
    תיקונים" on a quiz holding a verified contradiction. That is the app
    affirmatively telling the learner nothing is wrong, the exact outcome §2.4
    ("mark, never hide") and §5.11 ("a mark shows the verified quote and
    printed page") exist to prevent. Both ends were green on their own tests;
    no test put one's output into the other's input.

    The flat keys stay beside the nested ones. qc_fix duplicates them onto
    every fix row for this function (qc_fix._fix_record says so), the Hebrew
    report below reads them, and dropping them would break the errata page of
    any verdict.json already on disk.
    """
    out = []
    for f in verdict.get("fixes", []) or []:
        # §5.9: a presentation fix (the answer-order rebalance) "carries no app
        # marker" — it changed no content, so it is not an erratum. The app
        # drops it too; emitting it would inflate the badge's correction count.
        if _get(f, "kind", default="") == "presentation":
            continue
        said = _get(f, "said", "was", default="") or ""
        quote = _get(f, "source_quote", "quote", default="") or ""
        book = _get(f, "book", default="") or ""
        page = _get(f, "printed", "page", "printed_page", default="")
        book_he = book_name_he(f, book)
        second_he = book_name_he(f, _get(f, "second_book", default="") or "",
                                 prefix="second_")
        second_page = _get(f, "second_printed", "second_printed_page",
                           "second_page", default="")
        rec = {
            # -- what app/quiz_logic.parse_qc_json reads --
            # "content" always: qc_mark refuses any other kind, and the FINDING
            # kind (contradicted | source_conflict) is a different taxonomy —
            # it is kept below as `finding_kind` for the report.
            "kind": "content",
            "status": _get(f, "status", default="active") or "active",
            "date": _get(f, "date", default="") or "",
            "asset": _get(f, "asset", default="") or "",
            "id": _get(f, "id", "finding_id", default=""),
            "unit": _get(f, "unit", "uid", default=""),
            "severity": _get(f, "severity", default=""),
            "was": said,
            "now": _get(f, "correction", "now", default="") or "",
            # -- what this module's own report reads --
            "finding_kind": _get(f, "finding_kind", "kind", default=""),
            "tier": _get(f, "tier", default=1),
            "needs_owner": bool(_get(f, "needs_owner", default=False)),
            "said": said, "book": book, "page": page, "source_quote": quote,
            # Owner decision 4: the כרטיסיות is named by its Hebrew title
            # wherever it appears. The app recovers it by mapping the key
            # (quiz_logic.book_display); _qc/REPORT.md and §5.10.4's ntfy read
            # the line below, and they never pass through the app.
            "book_he": book_he,
            "he": (f"החומר שפורסם אמר \"{_trim(said)}\" → "
                   f"המקור אומר \"{_trim(quote)}\" ({book_he} עמ' {page})"
                   + (f" · המקור השני: {second_he} עמ' {second_page}"
                      if second_he else "")),
        }
        src = _source_block(f)
        if src:
            rec["source"] = src
        second = _source_block(f, second=True)
        if second:
            rec["second_source"] = second
        out.append(rec)
    return out


def rebalanced(verdict: dict) -> bool:
    """True when this chapter's answer order actually moved.

    §5.10.5 wants ONE notice that the key positions changed, and the record of
    that change is not where a first reading puts it. qc_fix.apply_hygiene
    returns its `key_position` rows in `changes` (its `fixes` key only repeats
    them), and qc_run files `changes` under the verdict's HYGIENE record —
    while `verdict["fixes"]` holds Tier-1 and Tier-2 content corrections only.
    Measured on the 019 fixture: apply_hygiene emits 2 presentation rows and 0
    of them reach `verdict["fixes"]`, so a check that read fixes alone could
    never fire, and the owner would meet a re-ordered quiz believing his memory
    of it was wrong (§1: key position was A 98 / B 31 / C 3 / D 0, so nearly
    every chapter moves something).

    `fixed` is required: rebalance_keys also logs the items it DECLINED to move
    — an option that refers to another by letter, an all/none key — and those
    change nothing the owner would notice.
    """
    rows = list(verdict.get("fixes") or [])
    rows += list((verdict.get("hygiene") or {}).get("changes") or [])
    return any(r.get("kind") == "presentation" and r.get("fixed", True)
               for r in rows)


def _trim(s: str, n: int = 160) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def load_verdicts(root: Path) -> list[dict]:
    out = []
    for p in sorted(Path(root).glob("*/verdict.json")):
        d = qc_store.read_json(p)
        if d:
            out.append(d)
    return out


def write_report(root: Path, *, verdicts: Sequence[dict] | None = None) -> Path:
    """`_qc/REPORT.md`, in Hebrew, rebuilt from every verdict on disk.

    Rebuilt rather than appended: a chapter can be judged twice (a re-judge
    after a revert), and an appended report would show the owner both answers
    with no way to tell which one is current.
    """
    root = Path(root)
    verdicts = list(verdicts if verdicts is not None else load_verdicts(root))
    verdicts.sort(key=lambda v: v.get("tag", ""))
    by_status: dict[str, int] = {}
    for v in verdicts:
        by_status[v.get("status", "?")] = by_status.get(v.get("status", "?"), 0) + 1

    lines = ["# דוח בדיקת איכות מול ספרי הלימוד", "",
             f"עודכן: {_now_iso()}  ·  {len(verdicts)} פרקים נבדקו", ""]
    lines.append("| מצב | פרקים |")
    lines.append("| :--- | ---: |")
    for st in STATUS_ORDER:
        if by_status.get(st):
            lines.append(f"| {STATUS_HE[st]} | {by_status[st]} |")
    lines.append("")

    for v in verdicts:
        st = v.get("status", "?")
        lines.append(f"## {v.get('tag')} — {STATUS_HE.get(st, st)}")
        f = v.get("findings", {}) or {}
        kinds = ", ".join(f"{KIND_HE.get(k, k)}: {n}"
                          for k, n in sorted((f.get("by_kind") or {}).items())
                          if k) or "אין"
        sev = ", ".join(f"{SEVERITY_HE.get(k, k)}: {n}"
                        for k, n in sorted((f.get("by_severity") or {}).items())
                        if k) or "אין"
        lines.append(f"- ממצאים לפי סוג: {kinds}")
        lines.append(f"- ממצאים לפי חומרה: {sev}")
        cov = v.get("coverage", {}) or {}
        lines.append(f"- כיסוי: {cov.get('rows', 0)} שורות על "
                     f"{cov.get('units', 0)} יחידות; "
                     f"ערכים שלא נבדקו: {cov.get('unchecked_locked_values', 0)}")
        blocking = (v.get("lint", {}) or {}).get("blocking") or []
        if blocking:
            lines.append("- בדיקות חוסמות: " + ", ".join(
                qc_lint.CHECKS.get(b, {}).get("he", b) for b in blocking))
        errata = errata_lines(v)
        if errata:
            lines.append(f"- {len(errata)} תיקונים:")
            for e in errata:
                lines.append(f"    - {e['unit']}: {e['he']}")
        owner = (v.get("findings", {}) or {}).get("owner") or []
        if owner:
            lines.append(f"- {len(owner)} ממצאים ממתינים להחלטת הבעלים:")
            for o in owner[:10]:
                lines.append(f"    - {_get(o, 'unit', default='')}: "
                             f"{KIND_HE.get(_get(o, 'kind', default=''), '')} — "
                             f"{_trim(_get(o, 'problem', 'said', default=''), 120)}")
        for note in v.get("notes", []) or []:
            lines.append(f"- הערה: {note}")
        lines.append("")

    path = root / "REPORT.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_bytes("\n".join(lines).encode("utf-8"))
    os.replace(tmp, path)
    return path


def update_owner_queue(root: Path, tag: str, items: Sequence[dict]) -> Path:
    """§5.10: findings needing the owner go to `_qc/owner_queue.json`.

    Keyed by (tag, finding id) so re-running a chapter updates its entries
    instead of listing the same conflict three times.
    """
    path = Path(root) / "owner_queue.json"
    doc = qc_store.read_json(path, default={"items": []}) or {"items": []}
    by_key = {(i.get("tag"), i.get("id")): i for i in doc.get("items", [])}
    for f in items:
        rec = {"tag": tag, "id": _get(f, "id", default=""),
               "unit": _get(f, "unit", default=""),
               "kind": _get(f, "kind", default=""),
               "severity": _get(f, "severity", default=""),
               "said": _get(f, "said", default=""),
               "source_quote": _get(f, "source_quote", "quote", default=""),
               "second_quote": _get(f, "second_quote", default=""),
               "book": _get(f, "book", default=""),
               "page": _get(f, "printed", "page", default=""),
               "problem": _get(f, "problem", default=""),
               "added_at": _now_iso(), "status": "open"}
        prior = by_key.get((tag, rec["id"]))
        if prior:
            rec["added_at"] = prior.get("added_at", rec["added_at"])
            rec["status"] = prior.get("status", "open")
        by_key[(tag, rec["id"])] = rec
    doc = {"updated_at": _now_iso(),
           "items": [by_key[k] for k in sorted(by_key, key=lambda k: (k[0] or "",
                                                                      k[1] or ""))]}
    qc_store.write_json_atomic(path, doc)
    return path


def digest_text(verdicts: Sequence[dict], *, total: int | None = None,
                durations: Sequence[float] = ()) -> str:
    """The daily digest of §5.10.4: n/68, ETA, highs, held."""
    done = len(verdicts)
    total = total if total is not None else done
    highs = sum((v.get("findings", {}) or {}).get("by_severity", {}).get("high", 0)
                for v in verdicts)
    held = sum(1 for v in verdicts
               if v.get("status") in ("needs_owner", "needs_regeneration",
                                      "unjudged"))
    fixes = sum(len(v.get("fixes") or []) for v in verdicts)
    eta = ""
    if durations and total > done:
        med = statistics.median(durations)
        hours = med * (total - done) / 3600
        eta = f" · נותרו ~{hours:.1f} שעות"
    return (f"{done}/{total} פרקים · {fixes} תיקונים · "
            f"{highs} ממצאים בחומרה גבוהה · {held} מוחזקים{eta}")


# ---------------------------------------------------------------------------
# §4: the agy version, and the probe that decides whether a new one may judge


def known_answer_probe(deps: Deps, *, model: str | None = None,
                       root: Path | None = None) -> dict:
    """§4's atomoxetine known-answer probe. `{ok, detail, quote}`.

    "The agy version is recorded on every call; if it changes mid-run, run the
    atomoxetine known-answer probe (StahlKids p.69, "2-8 weeks") and stop if it
    fails." Until this existed, the words appeared in exactly one place in the
    program — the Hebrew STOP alert, which told the owner to run a probe by
    hand and gave him nothing to run.

    The agy call belongs to the judge module (one call at a time, one place
    that knows how to attach a bundle), so it is `deps.probe`; what is decided
    HERE is what counts as passing, and it is decided the way every other quote
    in this mechanism is: the answer has to contain the value, found by
    qc_norm, not by a model's own report about itself.

    A probe that cannot run is a FAILED probe, not a skipped one. §2.3 fails
    closed, and "the new binary might be fine" is not evidence that it is.
    """
    want = KNOWN_ANSWER
    try:
        raw = deps.probe(book=want["book"], printed=want["printed"],
                         expect=want["value"], question=want["question"],
                         model=model, root=str(root) if root else None)
    except NotWired as exc:
        return {"ok": False, "quote": "",
                "detail": f"no known-answer probe is wired: {exc}"}
    except QuotaPaused:
        raise
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "quote": "", "detail": f"the probe failed: {exc}"}
    if raw is None:
        return {"ok": False, "quote": "", "detail": "the probe returned nothing"}
    quote = str(_get(raw, "quote", "answer", "text", "source_quote",
                     default="") or "")
    said_ok = _truthy(_get(raw, "ok", "verified", default=True))
    try:
        found = bool(qc_norm.find_quote(want["value"], quote,
                                        whole_unit=True).get("match"))
    except Exception:                                     # noqa: BLE001
        found = False
    if not said_ok:
        return {"ok": False, "quote": quote,
                "detail": f"the probe reports its own failure: "
                          f"{_get(raw, 'reason', 'detail', default='') or ''}"}
    if not found:
        return {"ok": False, "quote": quote,
                "detail": f"{want['book']} p.{want['printed']} came back "
                          f"without \"{want['value']}\": {_trim(quote, 120)!r}"}
    return {"ok": True, "quote": quote,
            "detail": f"{want['book']} p.{want['printed']} → "
                      f"\"{want['value']}\""}


def _remember_agy_version(root: Path, version: str) -> None:
    """The version the backlog is being judged by, in `_qc/state.json`.

    Across runs, not only within one: the backlog is 24-51 hours of serial
    judging spread over days and quota stops, so "mid-run" in §4 is the whole
    pass, not one invocation. Written through qc_store's own state file so
    `tier2_enabled` and the per-chapter run states survive beside it.
    """
    data = qc_store.load_state(root=root)
    if data.get("agy_version") == version:
        return
    data["agy_version"] = version
    data["updated_at"] = qc_store.now_iso()
    qc_store.write_json_atomic(qc_store.state_path(root=root), data)


def agy_version_guard(deps: Deps, opts: Options, tag: str,
                      verdict: dict) -> str:
    """"" when the run may continue, else the reason it must stop. §4.

    The upgrade this defends against is silent: `AGY_CLI_DISABLE_AUTO_UPDATE=1`
    is set on every subprocess and agy has updated itself anyway. Two versions
    over one backlog is two populations of verdict that are not comparable,
    with nothing in the report saying where the seam is.
    """
    root = opts.qc_root()
    versions = [v for v in ((verdict.get("agy") or {}).get("versions") or [])
                if str(v or "").strip()]
    if not versions:
        return ""
    baseline = str(qc_store.load_state(root=root).get("agy_version") or "")
    if not baseline:
        _remember_agy_version(root, versions[0])
        baseline = versions[0]
    changed = [v for v in versions if v != baseline]
    if not changed:
        return ""
    _print(f"  [i] גרסת agy השתנתה: {baseline} → {', '.join(changed)}; "
           f"מריץ בדיקת ידע ידועה")
    res = known_answer_probe(deps, model=opts.model, root=root)
    if res["ok"]:
        # The new binary still reads the books. Record it and go on; the seam
        # is in the report because the verdict of this chapter names both.
        _remember_agy_version(root, changed[-1])
        _print(f"  [i] הבדיקה עברה ({res['detail']}); ממשיכים עם "
               f"{changed[-1]}")
        return ""
    return (f"{tag}: agy {baseline} → {', '.join(changed)} — "
            f"{res['detail']}")


def _stop_on_agy_version(deps: Deps, why: str) -> None:
    msg = (f"{why}. הריצה נעצרה (§4). אפשר להריץ שוב את הבדיקה עם\n"
           f"    python scripts/qc_run.py --probe")
    _print(f"  [STOP] {msg}")
    deps.notify("בדיקת האיכות נעצרה — גרסת agy השתנתה", msg, priority=5,
                tags=["rotating_light"])


# ---------------------------------------------------------------------------
# the run


def resolve_tags(opts: Options, deps: Deps) -> tuple[list[str], list[str]]:
    """(tags to judge, warnings). Only released chapters, never an orphan.

    §3 excludes `study-016-adhd-core`: the chapter was renumbered and the app
    never shows that release, so judging it would spend agy time on material
    nobody studies. It is returned as a warning instead of being dropped
    silently.
    """
    gh = deps.gh_or_default()
    chapters, orphans = qc_store.chapters(gh=gh)
    warnings = [f"release without a schedule entry, skipped: {t}"
                for t in orphans]
    by_index = {c.index: c for c in chapters}
    by_tag = {c.tag: c for c in chapters}
    if opts.all_chapters:
        wanted = [c for c in chapters if c.released]
    else:
        wanted = []
        for raw in opts.tags:
            raw = raw.strip()
            if not raw:
                continue
            c = by_tag.get(raw)
            if c is None and raw.isdigit():
                c = by_index.get(int(raw))
            if c is None:
                warnings.append(f"not a chapter: {raw}")
                continue
            if not c.released:
                warnings.append(f"{c.tag}: no published release, skipped")
                continue
            wanted.append(c)
    return [c.tag for c in wanted], warnings


def run(opts: Options, deps: Deps) -> int:
    """Every requested chapter, in order. Returns the process exit code."""
    root = opts.qc_root()
    root.mkdir(parents=True, exist_ok=True)
    tags, warnings = resolve_tags(opts, deps)
    for w in warnings:
        _print(f"[i] {w}")
    if not tags:
        _print("אין פרקים לבדיקה.")
        return EXIT_ERROR

    _print(f"{len(tags)} פרקים לבדיקה"
           + (" (dry-run)" if opts.dry_run else "")
           + (" (כיול)" if opts.calibration else ""))

    verdicts: list[dict] = []
    durations: list[float] = []
    consecutive_bad = 0
    did_rebalance = False
    exit_code = EXIT_OK

    # §5.5: one agy call can run 25 minutes with no input, and a laptop that
    # sleeps between the two renames of a swap leaves a release half-swapped.
    with qc_store.keep_awake():
        for i, tag in enumerate(tags, 1):
            _print(f"\n[{i}/{len(tags)}] {tag}")
            verdict = None
            crashed = False
            try:
                verdict = ChapterRun(tag, deps=deps, opts=opts).run()
            except QuotaPaused as exc:
                _pause_on_quota(tag, exc, opts, deps)
                exit_code = EXIT_QUOTA
                break
            except qc_store.GhError as exc:
                # A network fault is not a chapter fault (bulk_run.py:314-337).
                # Wait for GitHub, then give the chapter exactly one more try —
                # a second failure is something other than the network.
                _print(f"  [!] GitHub: {exc} — ממתין לרשת")
                if not deps.wait_for_network(gh=deps.gh_or_default()):
                    _print("  [STOP] הרשת לא חזרה")
                    exit_code = EXIT_ERROR
                    break
                try:
                    verdict = ChapterRun(tag, deps=deps, opts=opts).run()
                except Exception as exc2:                 # noqa: BLE001
                    _print(f"  [!] {tag}: {exc2}")
                    traceback.print_exc()
                    crashed = True
            except NotWired as exc:
                _print(f"  [STOP] {exc}")
                return EXIT_ERROR
            except qc_store.UploadUnresolved as exc:
                # A half-finished swap is a RELEASE that needs a decision, not
                # a judge that is broken. Counting it toward §5.5's breaker
                # would stop a backlog on three chapters whose judging was
                # fine, and hide the one action that fixes it.
                _print(f"  [!] {tag}: העלאה לא הושלמה — {exc}")
                _print(f"      השלם או בטל אותה: qc_store.resume_upload("
                       f"{tag!r}, <asset>)")
                exit_code = EXIT_ERROR
                continue
            except Exception as exc:                      # noqa: BLE001
                _print(f"  [!] {tag}: {exc}")
                traceback.print_exc()
                crashed = True

            if crashed:
                # §5.5's breaker counted VERDICTS, so a chapter that raised
                # instead of reaching one was invisible to it: a judge that
                # crashes on every answer — qc_judge blowing up on a truncated
                # reply, the agy call already paid for — walked the whole
                # backlog, 68 chapters and 25 minutes each, without stopping.
                # A crash is the strongest evidence the judge is broken, so it
                # counts like a failed canary.
                exit_code = EXIT_ERROR
                consecutive_bad += 1
                if consecutive_bad >= CIRCUIT_BREAKER_N:
                    _stop_on_breaker(deps, tag, consecutive_bad)
                    exit_code = EXIT_STOP
                    break
                continue

            verdicts.append(verdict)
            durations.append(verdict.get("duration_s") or 0.0)
            _print(f"  → {STATUS_HE.get(verdict['status'], verdict['status'])}"
                   f" · {len(verdict.get('fixes') or [])} תיקונים"
                   f" · {len((verdict.get('findings') or {}).get('owner') or [])}"
                   f" לבעלים")

            owner = (verdict.get("findings") or {}).get("owner") or []
            if owner:
                update_owner_queue(root, tag, owner)
            if rebalanced(verdict):
                did_rebalance = True
            if verdict.get("fixes") and not opts.calibration:
                _notify_chapter(deps, tag, verdict)

            consecutive_bad = _breaker_count(verdict, consecutive_bad)
            if consecutive_bad >= CIRCUIT_BREAKER_N:
                _stop_on_breaker(deps, tag, consecutive_bad)
                exit_code = EXIT_STOP
                break

            # §4, last: the chapter's verdict is on disk either way, so the
            # report can say where the seam is even when the run stops here.
            try:
                why = agy_version_guard(deps, opts, tag, verdict)
            except QuotaPaused as exc:
                # The probe is an agy call like any other, and §4 forbids
                # sleeping through a lockout that was measured at 13h33m.
                _pause_on_quota(tag, exc, opts, deps)
                exit_code = EXIT_QUOTA
                break
            if why:
                _stop_on_agy_version(deps, why)
                exit_code = EXIT_STOP
                break

    if not opts.dry_run:
        # Rebuilt from every verdict on disk, not just this run's: the report is
        # the owner's single view of the backlog and a run of two chapters must
        # not shrink it to two.
        write_report(root)
    if did_rebalance:
        # §5.10.5: one notice, once, that answer order changed — otherwise the
        # owner meets a rebalanced quiz and thinks his memory of it is wrong.
        deps.notify("סדר התשובות אוזן מחדש",
                    "מיקום התשובה הנכונה שונה בחלק מהשאלות; התוכן לא שונה.",
                    tags=["shuffle"])
    if verdicts and not opts.dry_run:
        digest = digest_text(verdicts, total=len(tags), durations=durations)
        (root / "digest.txt").write_bytes(digest.encode("utf-8"))
        deps.notify("סיכום בדיקת איכות", digest, tags=["books"])
    return exit_code


def _breaker_count(verdict: dict, current: int) -> int:
    """§5.5's circuit breaker counts the failures that mean "the judge is not
    answering", and only those.

    A chapter that was judged fine but is held for the owner is not evidence
    that the judge is broken, and resetting on it is what keeps the breaker
    from firing on a run that is working.

    What counts is a call that came back and could not be believed: a failed
    canary or nonce, a reply that would not parse, a bundle that never reached
    the model, a forbidden tool call, a judge that answered about a fraction of
    the units. All five are discarded, retried three times and leave the
    chapter `unjudged`; the first two were the only ones the filter matched, so
    the 2026-09-18 incident — agy silently attaching nothing — would have run
    all 68 chapters at ~25 minutes of quota per attempt and exited 0.
    """
    if verdict.get("status") != "unjudged":
        return 0
    failed = [j for j in (verdict.get("judgments") or []) if not j.get("ok")]
    codes = {str(j.get("reason_code") or "").strip().lower() for j in failed}
    if codes & BREAKER_CODES:
        return current + 1
    reasons = " ".join(str(j.get("reason", "")) for j in failed)
    reasons += " " + " ".join(str(n) for n in (verdict.get("notes") or []))
    return current + 1 if _BREAKER_REASON.search(reasons) else current


def _pause_on_quota(tag: str, exc: QuotaPaused, opts: Options,
                    deps: Deps) -> None:
    journal = qc_store.Journal(tag, root=opts.qc_root())
    step = journal.resume() or "verdict"
    when = datetime.fromtimestamp(exc.resume_after_epoch)
    qc_store.set_run_state(tag, "paused_quota", root=opts.qc_root(),
                           resume_after=when.isoformat(timespec="seconds"),
                           resume_after_epoch=exc.resume_after_epoch,
                           step=step, raw=exc.raw)
    _print(f"  [PAUSE] מכסת agy נגמרה; ממשיכים אחרי "
           f"{when:%Y-%m-%d %H:%M} (שלב {step})")
    # ONE notification, here and nowhere else: the pause is a single event and
    # the topic is the owner's phone.
    deps.notify("בדיקת האיכות עצרה — מכסת agy",
                f"{tag}: נעצר בשלב {step}. אפשר להמשיך אחרי "
                f"{when:%H:%M %d/%m} עם\n"
                f"    python scripts/qc_run.py --chapters {tag}",
                priority=4, tags=["hourglass"])


def _stop_on_breaker(deps: Deps, tag: str, n: int) -> None:
    msg = (f"{n} פרקים ברצף ללא ודאות ({tag} אחרון). הרץ את בדיקת הידע "
           f"הידועה (StahlKids עמ' 69, \"2-8 weeks\") לפני שממשיכים:\n"
           f"    python scripts/qc_run.py --probe")
    _print(f"  [STOP] {msg}")
    deps.notify("בדיקת האיכות נעצרה — השופט לא מאמת", msg, priority=5,
                tags=["rotating_light"])


def _notify_chapter(deps: Deps, tag: str, verdict: dict) -> None:
    errata = errata_lines(verdict)
    head = errata[0]["he"] if errata else ""
    deps.notify(f"{len(errata)} תיקונים — {tag}", _trim(head, 300),
                tags=["books"])


# ---------------------------------------------------------------------------
# CLI


def build_options(a) -> Options:
    tags = []
    for chunk in (a.chapters or []):
        tags.extend(x for x in str(chunk).split(",") if x.strip())
    scope = getattr(a, "scope", None)
    return Options(
        tags=tags, all_chapters=bool(a.all), calibration=bool(a.calibration),
        dry_run=bool(a.dry_run), no_upload=bool(a.no_upload),
        page_limit=a.page_limit, use_books=not a.no_books,
        root=Path(a.qc_dir) if a.qc_dir else None, model=a.model,
        scope=parse_scope(scope))


# What `--scope study` means: the three assets the owner studies from.
STUDY_SCOPE = frozenset({"study_guide.md", "report.md", "flashcards.json",
                         "quiz.json"})


def parse_scope(value) -> frozenset | None:
    """`None` / "" = everything; "study" = STUDY_SCOPE; else a comma list of
    asset names. report.md is study_guide.md's name in the oldest chapters."""
    if not value:
        return None
    value = str(value).strip()
    if value == "study":
        return STUDY_SCOPE
    return frozenset(x.strip() for x in value.split(",") if x.strip())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chapters", action="append",
                    help="tags or schedule indexes, comma separated (19,42)")
    ap.add_argument("--all", action="store_true",
                    help="every released chapter, in schedule order")
    ap.add_argument("--calibration", action="store_true",
                    help="§6 calibration run: never uploads, Tier 2 off")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch, hygiene, lint and bundles only — no agy call,"
                         " no upload")
    ap.add_argument("--no-upload", action="store_true",
                    help="judge and fix, but keep the results local")
    ap.add_argument("--page-limit", type=int,
                    help="max printed pages per bundle (§5.7 splits above it)")
    ap.add_argument("--no-books", action="store_true",
                    help="skip the lint checks that read the PDFs")
    ap.add_argument("--probe", action="store_true",
                    help="§4's known-answer probe (StahlKids p.69, "
                         "\"2-8 weeks\") and nothing else; exit 3 if it fails")
    ap.add_argument("--model", help="override the agy model (§4: pro-high)")
    ap.add_argument("--qc-dir", help="use this _qc/ tree instead of the repo's")
    ap.add_argument("--scope",
                    help="assets to judge: 'study' (guide, flashcards, quiz) "
                         "or a comma list; default every judged asset")
    a = ap.parse_args(argv)
    if not a.all and not a.chapters and not a.probe:
        ap.error("give --chapters, --all or --probe")

    opts = build_options(a)
    if a.probe:
        # The one thing the STOP alerts tell the owner to do. Nothing is
        # fetched, judged or uploaded: it is one agy call against one page.
        res = known_answer_probe(Deps(), model=opts.model,
                                 root=opts.qc_root())
        _print(("בדיקת הידע הידועה עברה: " if res["ok"]
                else "בדיקת הידע הידועה נכשלה: ") + res["detail"])
        return EXIT_OK if res["ok"] else EXIT_STOP
    # Sending is off in the constructor and turned on here, once, for a real
    # run. A dry run stays silent: it changes nothing worth a phone alert.
    deps = Deps(notify=Ntfy(enabled=not opts.dry_run))
    return run(opts, deps)


if __name__ == "__main__":
    raise SystemExit(main())
