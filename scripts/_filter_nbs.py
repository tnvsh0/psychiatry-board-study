# -*- coding: utf-8 -*-
"""Helper to filter notebook list — runs on VM after wc/list."""
import json, re, sys

# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/nbs.json"
d = json.load(open(PATH, encoding="utf-8"))
nbs = d.get("notebooks", [])
print(f"TOTAL notebooks: {len(nbs)}")
print(f"Top-level keys in response: {list(d.keys())}")
print("\n--- Non-weekly-review notebooks (likely study/personal) ---")
weekly = re.compile(r"^(פסיכיאטריה|פסיכותרפיה|התפתחות|מדעי המוח|פסיכופרמקולוגיה|סקירת)")
for n in nbs:
    t = n.get("title", "")
    if not weekly.match(t):
        print(f"  {n['id']}  |  {t[:80]}")
