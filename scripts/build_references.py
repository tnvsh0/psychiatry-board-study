#!/usr/bin/env python3
"""Important studies and assessment tools for each chapter — none invented.

Owner's request, 2026-10-25: for every chapter, a list of the important studies
and of the questionnaires / assessment tools, each with a few Hebrew sentences on
what it is and why it matters; first from the course's own textbooks, then real
work beyond them; nothing invented, and everything reviewed before it is shown.

WHERE EACH ITEM COMES FROM, AND HOW IT IS CHECKED
  * From the books (origin "book"). NotebookLM, in the chapter's notebook with the
    textbooks attached, names the study or tool, the book and printed page, and a
    verbatim quote. The quote must be found on that page of that book (or the
    page next to it — two textbooks are reflowed ebooks) in qc_books' own text,
    and the study's name or first author must appear there. Every number in the
    Hebrew summary must appear on those pages too. Fail any one: rejected.
  * Beyond the books (origin "pubmed"). NotebookLM proposes landmark work it is
    certain of; each candidate must be found in PubMed with a matching first
    author and year (a tool: a PubMed record naming it with validation or
    psychometric terms). The citation shown is PubMed's, not the model's, and the
    Hebrew summary is written from the PubMed ABSTRACT alone — the abstract is
    added to the notebook as a source and the question is restricted to it.
    Every number in that summary must be in the abstract.
Rejected candidates are kept, with the reason, so the review is visible.

    python scripts/build_references.py --days 1,29,47          # pilot
    python scripts/build_references.py --days 1 --publish      # attach to release
    python scripts/build_references.py --all --publish
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import daily_study as ds  # noqa: E402
import qc_books  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_refs"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BOOK_KEYS = {"dsm": "DSM-5-TR", "dsm-5-tr": "DSM-5-TR", "dulcan": "Dulcan",
             "stahlneuro": "StahlNeuro", "stahl essential": "StahlNeuro",
             "stahlkids": "StahlKids", "stahl kids": "StahlKids",
             "prescriber": "StahlKids", "syllabus": "Syllabus",
             "kartisiot": "Kartisiot", "flashcards": "Kartisiot"}
NUM = re.compile(r"\d+(?:[.,]\d+)?")


# ---------------------------------------------------------------- prompts

def _ask(prompt: str, source: str | None = None, timeout: int = 600) -> str | None:
    args = ["ask", prompt, "--json"]
    if source:
        args[2:2] = ["-s", source]
    r = ds.nb_run(args, timeout=timeout)
    if r.returncode != 0:
        print(f"    [!] ask failed: {((r.stderr or '') + (r.stdout or ''))[:200]}")
        return None
    try:
        return json.loads(r.stdout).get("answer", "")
    except Exception:
        return None


def _json_block(text: str | None) -> dict:
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    raw = re.sub(r"\[\d+(?:[,\s\-–]+\d+)*\]", "", m.group(0))   # citation markers
    try:
        return json.loads(raw)
    except Exception:
        return {}


BOOK_STUDIES = """From the sources in this notebook ONLY, list the important research studies relevant to: {title}
(landmark trials, named multisite studies, important cohort studies and meta-analyses the sources actually describe).

Return STRICT JSON only, no prose: {{"items": [ ... ]}} with up to 12 objects:
  "name": the study's name or short label (e.g. "MTA", "TADS", or "Smith et al. 2010"),
  "first_author": surname of the first author if the source gives it, else "",
  "year": year if the source gives it, else "",
  "design": e.g. RCT / cohort / meta-analysis, as the source says,
  "book": which source (DSM-5-TR, Dulcan, StahlNeuro, StahlKids, Syllabus),
  "page": the printed page number where it is described,
  "quote": ONE OR TWO SENTENCES COPIED VERBATIM from that page that mention the study,
  "summary_he": 3-4 sentences in Hebrew: what was studied, in whom, what was found, and why it matters for practice or the exam. Use only what the source says.
Include only studies the sources really describe. If the sources describe none, return {{"items": []}}. Never invent a study, an author, a year or a number."""

BOOK_TOOLS = """From the sources in this notebook ONLY, list the questionnaires, rating scales, structured interviews and other assessment tools relevant to: {title}

Return STRICT JSON only, no prose: {{"items": [ ... ]}} with up to 12 objects:
  "name": full name, "abbrev": abbreviation if any,
  "measures": what it measures, "ages": age range if stated, "informant": who fills it (parent / teacher / self / clinician) if stated,
  "use": screening / diagnosis / severity / monitoring, as the source says,
  "book": which source, "page": printed page,
  "quote": ONE OR TWO SENTENCES COPIED VERBATIM from that page that mention the tool,
  "summary_he": 2-4 sentences in Hebrew: what the tool is, for whom, how it is used, and anything the source says about items, scoring or cutoffs.
Include only tools the sources really mention. Never invent a tool, an item count, a cutoff or an age range."""

EXTERNAL_STUDIES = """Beyond what these sources say: name up to 6 additional landmark, peer-reviewed studies on "{title}" in child and adolescent psychiatry that a board candidate is expected to know, and that you are CERTAIN exist.
Return STRICT JSON only: {{"items": [{{"first_author": "", "year": "", "title": "", "journal": "", "pmid": ""}}]}}.
Leave pmid empty if unsure. If you are not certain of any, return {{"items": []}}."""

EXTERNAL_TOOLS = """Beyond what these sources say: name up to 6 additional widely used, validated assessment tools (questionnaires, rating scales, structured interviews) for "{title}" in children and adolescents that you are CERTAIN exist.
Return STRICT JSON only: {{"items": [{{"name": "", "abbrev": ""}}]}}. If none, return {{"items": []}}."""

FROM_ABSTRACT = """Using ONLY the selected source (a PubMed abstract), write {n} sentences in Hebrew: {what}
Do not add anything that is not in the abstract. Return STRICT JSON only: {{"summary_he": "", "design": "", "population": ""}}."""


# ---------------------------------------------------------------- verification

def _norm(s: str) -> str:
    s = re.sub(r"[‐-―]", "-", s or "")
    s = re.sub(r"[“”\"'‘’`]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _book_key(name: str) -> str | None:
    n = (name or "").strip().lower()
    for k, v in BOOK_KEYS.items():
        if k in n:
            return v
    return name if name in qc_books.SPECS else None


def _pages_text(key: str, page) -> str:
    try:
        p = int(str(page).split("-")[0].strip())
    except (TypeError, ValueError):
        return ""
    b = qc_books.book(key)
    return " ".join(b.page_text(x) or "" for x in (p - 1, p, p + 1) if b.has_page(x))


def _quote_on_page(quote: str, text: str) -> bool:
    q, t = _norm(quote), _norm(text)
    if len(q) < 25 or not t:
        return False
    if q in t:
        return True
    m = difflib.SequenceMatcher(None, q, t, autojunk=False).find_longest_match(0, len(q), 0, len(t))
    return m.size >= 0.85 * len(q)


def _numbers_supported(summary: str, evidence: str) -> list[str]:
    ev = {n.replace(",", ".") for n in NUM.findall(evidence or "")}
    return [n for n in NUM.findall(summary or "") if n.replace(",", ".") not in ev]


def verify_book_item(it: dict, kind: str) -> tuple[bool, str]:
    key = _book_key(str(it.get("book", "")))
    if not key:
        return False, f"book not recognised: {it.get('book')!r}"
    text = _pages_text(key, it.get("page"))
    quote = str(it.get("quote", ""))
    if not (text and _quote_on_page(quote, text)):
        # The page, not the quote, is what NotebookLM most often gets wrong:
        # Dulcan and DSM are reflowed ebooks, and in the 2026-10-25 pilot most
        # rejections were real studies cited a few pages off. Search the whole
        # book for a run of the quote's words; found elsewhere means the item is
        # kept WITH THE CORRECTED PAGE — the quote still has to be in the book.
        words = re.findall(r"[A-Za-z]{3,}", quote)
        found = None
        for start in range(0, max(1, len(words) - 6), 4):
            chunk = r"\W+".join(re.escape(w) for w in words[start:start + 7])
            if len(words[start:start + 7]) < 6:
                break
            hits = qc_books.search(chunk, keys=[key], limit=3)
            if hits:
                found = hits[0][1]
                break
        if found is None:
            return False, f"the quote is not on {key} p.{it.get('page')} nor anywhere in {key}"
        candidate = _pages_text(key, found)
        if not _quote_on_page(quote, candidate):
            return False, f"the quote is not on {key} p.{it.get('page')}"
        it["page_cited"], it["page"], text = it.get("page"), found, candidate
    ids = [str(it.get(k, "")).strip() for k in
           (("name", "first_author") if kind == "studies" else ("abbrev", "name"))]
    if not any(i and _norm(i) in _norm(text) for i in ids):
        return False, f"the {'study' if kind == 'studies' else 'tool'} is not named on that page"
    bad = _numbers_supported(str(it.get("summary_he", "")), text)
    if bad:
        return False, f"numbers in the summary not on the page: {', '.join(bad[:4])}"
    it["book"] = key
    return True, "quote found on the page"


def _get(url: str) -> bytes:
    for i in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read()
        except Exception:
            time.sleep(2 + 3 * i)
    return b""


def pubmed_find(first_author: str, year: str, title: str, pmid: str = "") -> dict | None:
    """The PubMed record that matches, or None. Matching needs author AND year."""
    ids = [pmid] if pmid and pmid.isdigit() else []
    if not ids:
        words = " ".join(re.findall(r"[A-Za-z]{4,}", title or "")[:6])
        term = f"{first_author}[1au] AND {year}[dp]" + (f" AND ({words})" if words else "")
        res = json.loads(_get(f"{EUTILS}/esearch.fcgi?db=pubmed&retmode=json&retmax=5&term="
                              + urllib.parse.quote(term)) or b"{}")
        ids = res.get("esearchresult", {}).get("idlist", [])
    for pid in ids[:5]:
        s = json.loads(_get(f"{EUTILS}/esummary.fcgi?db=pubmed&retmode=json&id={pid}") or b"{}")
        rec = s.get("result", {}).get(pid)
        if not rec:
            continue
        authors = [a.get("name", "") for a in rec.get("authors", [])]
        y = (rec.get("pubdate") or "")[:4]
        if (authors and _norm(first_author) and _norm(first_author) in _norm(authors[0])
                and str(year)[:4] == y):
            if title and difflib.SequenceMatcher(None, _norm(title), _norm(rec.get("title", ""))).ratio() < 0.45:
                continue
            return {"pmid": pid, "title": rec.get("title", ""), "journal": rec.get("fulljournalname") or rec.get("source", ""),
                    "year": y, "authors": authors[:3], "first_author": authors[0]}
        time.sleep(0.4)
    return None


def pubmed_tool(name: str, abbrev: str) -> dict | None:
    label = abbrev or name
    term = (f'("{label}"[tiab]' + (f' OR "{name}"[tiab]' if abbrev and name else "") +
            ") AND (psychometric*[tiab] OR validation[tiab] OR validity[tiab] OR reliability[tiab])"
            " AND (child*[tiab] OR adolescen*[tiab])")
    res = json.loads(_get(f"{EUTILS}/esearch.fcgi?db=pubmed&retmode=json&retmax=1&sort=relevance&term="
                          + urllib.parse.quote(term)) or b"{}")
    ids = res.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return None
    s = json.loads(_get(f"{EUTILS}/esummary.fcgi?db=pubmed&retmode=json&id={ids[0]}") or b"{}")
    rec = s.get("result", {}).get(ids[0]) or {}
    if not any(_norm(x) in _norm(rec.get("title", "")) for x in (abbrev, name) if x):
        return None
    return {"pmid": ids[0], "title": rec.get("title", ""), "journal": rec.get("source", ""),
            "year": (rec.get("pubdate") or "")[:4],
            "first_author": (rec.get("authors") or [{}])[0].get("name", "")}


def pubmed_abstract(pmid: str) -> str:
    return _get(f"{EUTILS}/efetch.fcgi?db=pubmed&id={pmid}&rettype=abstract&retmode=text").decode("utf-8", "replace")


def summarise_from_abstract(nb_id: str, pmid: str, what: str, n: str) -> tuple[dict, str]:
    abstract = pubmed_abstract(pmid)
    if len(abstract) < 200:
        return {}, abstract
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(abstract)
        path = f.name
    r = ds.nb_run(["source", "add", path, "--title", f"PubMed {pmid}", "--json", "-n", nb_id], timeout=180)
    sid = None
    try:
        sid = (json.loads(r.stdout or "{}").get("source") or {}).get("id") or json.loads(r.stdout or "{}").get("id")
    except Exception:
        pass
    if not sid:
        return {}, abstract
    try:
        ds.nb_run(["source", "wait", sid, "-n", nb_id], timeout=300)
        out = _json_block(_ask(FROM_ABSTRACT.format(n=n, what=what), source=sid))
    finally:
        ds.nb_run(["source", "delete", sid, "-n", nb_id, "-y"], timeout=120)
    return out, abstract


# ---------------------------------------------------------------- per chapter

def notebook_for(day: dict) -> str:
    title = day.get("title_he", day.get("title_en", ""))
    nb_id, nb_title = ds.find_day_notebook(day["index"], title)
    if nb_id:
        return nb_id
    print(f"    building a notebook for chapter {day['index']} (its old one is gone)")
    nb_id = ds.create_day_notebook(nb_title)
    attached = ds.add_textbook_pdfs_to_day_notebook(nb_id)
    ds.attach_synopsis_slices(nb_id, day["index"], attached)
    if not ds.wait_for_sources_indexed(nb_id, attached=attached):
        raise RuntimeError("sources never finished indexing")
    ds.use_notebook(nb_id)
    ds.rename_sources_to_labels(nb_id, attached)
    if not ds.wait_until_answerable(nb_id):
        raise RuntimeError("notebook cannot answer from its sources")
    return nb_id


def build_kind(day: dict, nb_id: str, kind: str) -> dict:
    title = f"{day.get('title_en', '')} ({day.get('title_he', '')})"
    kept, rejected = [], []
    got = _json_block(_ask((BOOK_STUDIES if kind == "studies" else BOOK_TOOLS).format(title=title)))
    for it in got.get("items") or []:
        ok, why = verify_book_item(it, kind)
        it["origin"] = "book"
        (kept if ok else rejected).append({**it, "check": why})
    seen = {_norm(str(i.get("name") or i.get("abbrev") or "")) for i in kept}

    ext = _json_block(_ask((EXTERNAL_STUDIES if kind == "studies" else EXTERNAL_TOOLS).format(title=title)))
    for it in ext.get("items") or []:
        label = it.get("title") or it.get("name") or ""
        if kind == "studies":
            rec = pubmed_find(str(it.get("first_author", "")), str(it.get("year", "")),
                              str(it.get("title", "")), str(it.get("pmid", "")))
            what = "what was studied, in whom, what was found, and why it matters"
        else:
            if _norm(str(it.get("abbrev") or it.get("name"))) in seen:
                continue
            rec = pubmed_tool(str(it.get("name", "")), str(it.get("abbrev", "")))
            what = f"what the tool {it.get('abbrev') or it.get('name')} measures, in whom, and how it performed"
        if not rec:
            rejected.append({**it, "origin": "pubmed", "check": "no matching PubMed record"})
            continue
        summary, abstract = summarise_from_abstract(nb_id, rec["pmid"], what, "3-4" if kind == "studies" else "2-3")
        if not summary.get("summary_he"):
            rejected.append({**it, **rec, "origin": "pubmed", "check": "no summary from the abstract"})
            continue
        bad = _numbers_supported(summary["summary_he"], abstract)
        if bad:
            rejected.append({**it, **rec, "origin": "pubmed",
                             "check": f"numbers not in the abstract: {', '.join(bad[:4])}"})
            continue
        item = {**rec, "origin": "pubmed", "summary_he": summary["summary_he"],
                "design": summary.get("design", ""), "check": "PubMed record matched; summary from its abstract"}
        if kind == "tools":
            item.update(name=it.get("name", ""), abbrev=it.get("abbrev", ""))
        else:
            item["name"] = f"{rec['first_author'].split(' ')[0]} et al. {rec['year']}"
        kept.append(item)
        time.sleep(1)
    return {"chapter": day["index"], "title_he": day.get("title_he"), "title_en": day.get("title_en"),
            "kind": kind, "items": kept, "rejected": rejected,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def tag_of(day: dict) -> str:
    return f"study-{day['index']:03d}-{ds.slugify(day.get('title_en', ''))}"


def publish(tag: str, files: list[Path]) -> bool:
    ok = True
    for f in files:
        r = subprocess.run(["gh", "release", "upload", tag, str(f), "--repo", ds.GITHUB_REPO, "--clobber"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok &= r.returncode == 0
        if r.returncode != 0:
            print(f"    [!] upload {f.name} failed: {(r.stderr or '')[:120]}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", help="comma-separated chapters")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--publish", action="store_true", help="attach studies.json / tools.json to the release")
    ap.add_argument("--skip-done", action="store_true")
    a = ap.parse_args()
    days = {d["index"]: d for d in ds.load_schedule()["days"]}
    want = sorted(days) if a.all else [int(x) for x in (a.days or "").split(",") if x.strip()]
    for idx in want:
        day = days[idx]
        d = OUT / tag_of(day)
        if a.skip_done and (d / "studies.json").exists() and (d / "tools.json").exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        print(f"\n[{idx:3}] {day.get('title_he')}", flush=True)
        try:
            try:
                nb_id = notebook_for(day)
            except RuntimeError as exc:
                # The stored cookies expire every few hours ("CSRF token not
                # found" on 2026-10-25, before the pilot's first chapter). The
                # browser profile re-mints them without a person.
                if not any(m in str(exc) for m in ("CSRF", "session is dead", "Authentication")):
                    raise
                import backfill_items
                if not backfill_items._relogin():
                    raise
                nb_id = notebook_for(day)
            ds.use_notebook(nb_id)
            for kind in ("studies", "tools"):
                res = build_kind(day, nb_id, kind)
                (d / f"{kind}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"    {kind}: {len(res['items'])} kept "
                      f"({sum(i['origin'] == 'book' for i in res['items'])} from the books, "
                      f"{sum(i['origin'] == 'pubmed' for i in res['items'])} PubMed), "
                      f"{len(res['rejected'])} rejected", flush=True)
        except Exception as exc:                                  # noqa: BLE001
            print(f"    FAIL {exc}")
            continue
        if a.publish:
            publish(tag_of(day), [d / "studies.json", d / "tools.json"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
