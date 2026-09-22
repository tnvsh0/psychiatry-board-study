#!/usr/bin/env python3
"""Undo a QC correction on a published chapter — one fix, or all of them.

WHY THIS EXISTS AT ALL. The judge is measured, not trusted: spec §6 accepts a
verified false-positive rate of up to 2%, on a backlog of ~68 chapters with
tens of judged units each. So some correction WILL be wrong, and the owner will
find it while studying, at which point the question is how long it takes to put
the released material back. Without this script the answer is "hand-edit JSON
and hope"; with it, the answer is one command that rebuilds from bytes nobody
ever touched.

WHY IT REBUILDS INSTEAD OF EDITING. `_qc/orig/<tag>/` holds the assets exactly
as they were published, verified and hashed before the first change
(qc_store.snapshot). Withdrawing fix 7 of 9 by editing the live file means
trusting a patch-reversal to be exact; rebuilding means replaying the 8 fixes
that are still active onto the pristine original, so the result depends on
nothing that a previous run got right.

WHY `--all` NEEDS NOTHING BUT THE SNAPSHOT. Dropping every fix is the emergency
path — the one used when the corrections themselves are suspect — so it is
deliberately the path with the fewest moving parts: upload the snapshot back,
no fix engine involved. Withdrawing a single fix has to replay the others and
therefore needs qc_fix; if qc_fix cannot be imported, this says so instead of
guessing.

The upload goes through qc_store.safe_upload, so the fixed version is itself
preserved under `<name>.pre-qc-<date>` and a revert is as reversible as the fix.

    python scripts/qc_revert.py <tag> --all --dry-run
    python scripts/qc_revert.py <tag> --fix f3a91c
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qc_store                                          # noqa: E402
from qc_store import (                                   # noqa: E402
    Gh, Journal, QcStoreError, Stale, commit_paths, live_sha256, now_iso,
    qc_root, read_json, sha256_bytes, snapshot_dir, write_json_atomic,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                        # noqa: BLE001
    pass


class RevertError(QcStoreError):
    """This revert cannot be carried out safely."""


# ── the fixes file ──────────────────────────────────────────────────────────
# Written by qc_fix.py (spec §5.9). Only these fields are read here; everything
# else is carried through untouched, so qc_fix can add to the record without
# this script needing to know.
#   id      stable identifier, referenced by --fix
#   asset   the release asset the fix is embedded in (quiz.json, ...)
#   status  active | withdrawn
def fixes_path(tag: str, *, root=None) -> Path:
    return qc_root(root) / tag / "fixes.json"


def load_fixes(tag: str, *, root=None) -> tuple[dict, list[dict]]:
    """(the document as stored, its list of fixes). A bare list is accepted —
    the file is another module's and this one should not fail on its shape."""
    doc = read_json(fixes_path(tag, root=root))
    if doc is None:
        raise RevertError(f"{fixes_path(tag, root=root)} does not exist — "
                          f"this chapter has no recorded fixes to withdraw")
    if isinstance(doc, list):
        return {"tag": tag, "fixes": doc}, doc
    fixes = doc.get("fixes")
    if not isinstance(fixes, list):
        raise RevertError(f"{fixes_path(tag, root=root)}: no 'fixes' list")
    return doc, fixes


def is_active(fix: dict) -> bool:
    return (fix.get("status") or "active") == "active"


# ── replaying the fixes that stay ───────────────────────────────────────────
# `applier` has three states and `None` is not "unset": passing None says "this
# machine has no fix engine", which is the case the module is built to refuse
# loudly (see the header). Leaving the argument out asks for qc_fix. Without
# this sentinel the two collapsed into one, `plan(applier=None)` quietly
# imported qc_fix, and the refusal the docstring promises could not happen.
_USE_QC_FIX = object()


def default_applier():
    """qc_fix's own patcher, or None when it is not importable.

    Deliberately late and optional: `--all` must keep working on a machine
    where qc_fix is broken or half-written, because that is exactly the day
    someone wants to revert everything.
    """
    try:
        import qc_fix                                    # noqa: PLC0415
    except Exception:                                    # noqa: BLE001
        return None
    fn = getattr(qc_fix, "apply_fix", None)
    if callable(fn):
        return lambda asset, text, fixes: _apply_one_by_one(fn, asset, text,
                                                            fixes)
    fn = getattr(qc_fix, "apply_fixes", None)
    if callable(fn):
        return fn
    return None


def _apply_one_by_one(fn, asset: str, text: str, fixes: list[dict]) -> str:
    for f in fixes:
        text = fn(asset, text, f)
    return text


def rebuild(tag: str, keep: list[dict], assets: list[str], *, root=None,
            applier=None) -> dict[str, bytes]:
    """asset name -> the bytes it should hold once `keep` is the whole set of
    active fixes. Reads only `_qc/orig/<tag>/`."""
    orig = snapshot_dir(tag, root=root)
    out: dict[str, bytes] = {}
    for name in assets:
        src = orig / name
        if not src.exists():
            raise RevertError(
                f"{src} is missing: there is no pristine copy of {name} to "
                f"rebuild from. Re-snapshot is NOT a fix — the live asset is "
                f"already corrected.")
        mine = [f for f in keep if f.get("asset") == name]
        if not mine:
            out[name] = src.read_bytes()
            continue
        if applier is None:
            raise RevertError(
                f"{name}: {len(mine)} fix(es) stay active and qc_fix could not"
                f" be imported to replay them. Use --all to drop every fix, or"
                f" fix the import.")
        text = src.read_text(encoding="utf-8")
        out[name] = applier(name, text, mine).encode("utf-8")
    return out


# ── plan ────────────────────────────────────────────────────────────────────
def plan(tag: str, *, withdraw_ids=None, withdraw_all: bool = False,
         gh: Gh | None = None, root=None, applier=_USE_QC_FIX) -> dict:
    """What a revert would do, computed without changing anything.

    `applier` left out means qc_fix; `applier=None` means there is no fix
    engine, and a fix that has to be replayed then raises RevertError instead
    of being silently replayed by one.
    """
    doc, fixes = load_fixes(tag, root=root)
    active = [f for f in fixes if is_active(f)]
    by_id = {str(f.get("id")): f for f in fixes}

    if withdraw_all:
        withdraw = list(active)
    else:
        withdraw = []
        for fid in withdraw_ids or []:
            f = by_id.get(str(fid))
            if f is None:
                raise RevertError(f"{tag}: no fix with id {fid!r} "
                                  f"(have: {', '.join(sorted(by_id)) or 'none'})")
            if not is_active(f):
                raise RevertError(f"{tag}: fix {fid} is already withdrawn")
            withdraw.append(f)
    if not withdraw:
        raise RevertError(f"{tag}: no active fixes to withdraw")

    drop_ids = {id(f) for f in withdraw}
    keep = [f for f in active if id(f) not in drop_ids]
    assets = sorted({f.get("asset") for f in withdraw if f.get("asset")})
    if not assets:
        raise RevertError(f"{tag}: the fixes to withdraw name no asset "
                          f"(field 'asset'), so nothing can be rebuilt")

    rebuilt = rebuild(tag, keep, assets, root=root,
                      applier=default_applier() if applier is _USE_QC_FIX
                      else applier)

    gh = gh or Gh()
    live = {a.name: a for a in gh.list_assets(tag)}
    steps = []
    for name in assets:
        a = live.get(name)
        if a is None:
            raise RevertError(f"{tag}: the release has no asset {name}")
        live_sha = live_sha256(gh, tag, name, a)
        new_sha = sha256_bytes(rebuilt[name])
        steps.append({"asset": name, "live_sha256": live_sha,
                      "new_sha256": new_sha,
                      "changed": live_sha != new_sha,
                      "bytes": len(rebuilt[name])})
    return {"tag": tag, "withdraw": withdraw, "keep": keep,
            "steps": steps, "rebuilt": rebuilt, "doc": doc, "fixes": fixes}


def print_plan(p: dict) -> None:
    print(f"{p['tag']}")
    print(f"  withdraw {len(p['withdraw'])} fix(es): "
          + ", ".join(str(f.get('id')) for f in p["withdraw"]))
    print(f"  keep     {len(p['keep'])} active fix(es)")
    for s in p["steps"]:
        verb = "upload" if s["changed"] else "unchanged, skip"
        print(f"  {s['asset']:<20} {s['live_sha256'][:8]} -> "
              f"{s['new_sha256'][:8]}  ({s['bytes']} bytes)  {verb}")


# ── apply ───────────────────────────────────────────────────────────────────
def apply_plan(p: dict, *, gh: Gh | None = None, root=None,
               today=None) -> list[dict]:
    """Upload the rebuilt assets, then mark the withdrawn errata.

    The marking happens AFTER the upload on purpose: a fixes.json that says
    `withdrawn` while the release still carries the fix would send the app's
    "תיקונים" page and the published material into disagreement, and the app
    is what the owner reads.
    """
    gh = gh or Gh()
    tag = p["tag"]
    journal = Journal(tag, root=root)
    # Gitignored (spec §5.10 lists _qc/work/): these are throwaway bytes on
    # their way to the release, and committing a copy of every reverted asset
    # would put the same content in git twice.
    staged = qc_root(root) / "work" / tag / "revert"
    staged.mkdir(parents=True, exist_ok=True)

    results = []
    for s in p["steps"]:
        name = s["asset"]
        if not s["changed"]:
            results.append({"asset": name, "stage": "unchanged",
                            "changed": False})
            continue
        local = staged / name
        local.write_bytes(p["rebuilt"][name])
        results.append(qc_store.safe_upload(
            tag, name, local, s["live_sha256"], gh=gh, journal=journal,
            root=root, today=today))

    doc, fixes = p["doc"], p["fixes"]
    withdrawn = {str(f.get("id")) for f in p["withdraw"]}
    for f in fixes:
        if str(f.get("id")) in withdrawn:
            f["status"] = "withdrawn"
            f["withdrawn_at"] = now_iso()
            f["withdrawn_by"] = "qc_revert"
    if isinstance(doc, dict):
        doc["fixes"] = fixes
        doc["updated_at"] = now_iso()
        write_json_atomic(fixes_path(tag, root=root), doc)
    else:
        write_json_atomic(fixes_path(tag, root=root), fixes)
    # §5.10 commits fixes.json by explicit path, and this is the other module
    # that writes it. A withdrawal that stayed only on disk would leave git
    # saying the fix is still active — the same disagreement the ordering above
    # exists to avoid, one layer down. Never raises; see qc_store.commit_paths.
    commit_paths([fixes_path(tag, root=root)],
                 f"qc: withdraw {len(p['withdraw'])} fix(es) on {tag}")
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tag")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fix", action="append", metavar="ID",
                   help="withdraw this fix, replay the rest (repeatable)")
    g.add_argument("--all", action="store_true", dest="all_fixes",
                   help="withdraw every active fix; restores the snapshot")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--qc-dir", help="override _qc/ (tests, dry runs)")
    a = ap.parse_args(argv)

    root = Path(a.qc_dir) if a.qc_dir else None
    try:
        p = plan(a.tag, withdraw_ids=a.fix, withdraw_all=a.all_fixes,
                 root=root)
        print_plan(p)
        if a.dry_run:
            print("\n--dry-run: nothing uploaded, nothing marked")
            return 0
        with qc_store.keep_awake():
            for r in apply_plan(p, root=root):
                print(f"  {r['asset']}: {r.get('stage')}"
                      + (f"  (original kept as {r['pre_qc_name']})"
                         if r.get("pre_qc_name") else ""))
        print("errata marked withdrawn")
        return 0
    except Stale as e:
        print(f"\n[STOP] {e}\n  The release moved since the plan was made; "
              f"re-run qc_revert.", file=sys.stderr)
        return 3
    except QcStoreError as e:
        print(f"\n[STOP] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
