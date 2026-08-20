#!/usr/bin/env python3
"""
Phase 0.1 — Extract a structured TOC of all books in a NotebookLM notebook.

Asks NotebookLM (which has indexed the books) for a JSON-structured table of
contents, save_as_note for traceability, and writes schedule/toc.json.

Usage:
    extract_toc.py <NOTEBOOK_ID>
    extract_toc.py --notebook-id 94cee222-...

Run on the VM (sudo -u User -E env PATH=/opt/venv/bin:$PATH ...) so that
NotebookLM auth from the VM's static IP is used.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "schedule"
OUT_FILE = OUT_DIR / "toc.json"

PROMPT = """For each source in this notebook (each is a textbook or syllabus
document), extract a structured table of contents. Output JSON ONLY (no prose
before or after) with this exact shape:

{
  "books": [
    {
      "title": "Book title as it appears",
      "source_id": "the NotebookLM source id if you can identify it, else null",
      "chapters": [
        {
          "number": "1",
          "title": "Chapter title",
          "page_start": 23,
          "page_end": 56,
          "sections": [
            {"title": "Subsection title", "page_start": 24, "page_end": 30}
          ]
        }
      ]
    }
  ],
  "syllabus_topics": [
    "Topic name as listed in the official syllabus, if a syllabus document is present"
  ]
}

Be thorough — list EVERY chapter and EVERY major section. Page numbers are
critical for the daily-study schedule that will reference them. If a source
appears to be a syllabus (not a textbook), capture all listed topics in
syllabus_topics."""


def run_notebooklm(args: list[str], timeout: int = 300) -> tuple[int, str, str]:
    cmd = ["/opt/venv/bin/notebooklm"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract TOC from a NotebookLM notebook.")
    ap.add_argument("notebook_id", nargs="?", help="Notebook UUID")
    ap.add_argument("--notebook-id", dest="notebook_id_flag", help="Notebook UUID (flag form)")
    args = ap.parse_args()

    nb_id = args.notebook_id or args.notebook_id_flag
    if not nb_id:
        print("ERROR: notebook_id required", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"=== Extract TOC from notebook {nb_id} ===")

    # 1. Switch context
    print("[1/3] Switching to notebook...")
    rc, out, err = run_notebooklm(["use", nb_id], timeout=30)
    if rc != 0:
        print(f"FAILED to use notebook: {err[:300]}")
        return 1

    # 2. List sources to confirm what's there
    print("[2/3] Listing sources in notebook...")
    rc, out, err = run_notebooklm(["source", "list", "--json"], timeout=30)
    if rc == 0:
        try:
            srcs = json.loads(out).get("sources", [])
            print(f"      Found {len(srcs)} sources:")
            for s in srcs:
                title = s.get("title", "?")[:80]
                status = s.get("status", "?")
                print(f"        - [{status}] {title}")
        except Exception as e:
            print(f"      (could not parse source list: {e})")

    # 3. Ask the notebook for the TOC, save as note + capture JSON
    print("[3/3] Asking notebook to produce TOC JSON (this may take 1-3 min)...")
    rc, out, err = run_notebooklm([
        "ask", PROMPT,
        "--json",
        "--save-as-note", "--note-title", "TOC extraction (script-generated)",
    ], timeout=600)

    if rc != 0:
        print(f"FAILED to ask: {err[:300]}")
        return 1

    try:
        resp = json.loads(out)
        answer = resp.get("answer", "")
    except Exception as e:
        print(f"FAILED to parse ask output: {e}")
        print(f"Raw: {out[:500]}")
        return 1

    # The answer should itself be JSON. Strip any markdown fencing.
    answer_stripped = answer.strip()
    if answer_stripped.startswith("```"):
        # Strip ```json ... ```
        lines = answer_stripped.split("\n")
        answer_stripped = "\n".join(lines[1:-1]) if len(lines) > 2 else answer_stripped

    try:
        toc = json.loads(answer_stripped)
    except Exception as e:
        print(f"WARNING: NotebookLM answer was not valid JSON ({e}).")
        print("Saving raw answer to schedule/toc_raw.txt for manual cleanup.")
        (OUT_DIR / "toc_raw.txt").write_text(answer, encoding="utf-8")
        return 1

    # Save with metadata
    output = {
        "notebook_id": nb_id,
        "extracted_via": "notebooklm ask --save-as-note",
        "books": toc.get("books", []),
        "syllabus_topics": toc.get("syllabus_topics", []),
        "_raw_response_preview": answer[:500],
    }
    OUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ Saved: {OUT_FILE}")
    print(f"  Books extracted: {len(output['books'])}")
    print(f"  Syllabus topics: {len(output['syllabus_topics'])}")
    print()
    print("NEXT: review schedule/toc.json with the user. Coverage check:")
    print("  - All books listed?")
    print("  - All chapters with page numbers?")
    print("  - Syllabus topics captured (if applicable)?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
