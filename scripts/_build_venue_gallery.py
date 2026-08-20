#!/usr/bin/env python3
"""
Build the venue-preview gallery — eight rendered worlds, each beside its spec,
so the venues can be judged by eye instead of from prose.

Regenerate with:  python scripts/_build_venue_gallery.py
Reads  _proto/web/gal/<NNN>.b64, _venues_partial.json, schedule/*.json
Writes _proto/web/gallery.html
"""
from __future__ import annotations

import json
from pathlib import Path

import sys
# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
GAL = ROOT / "_proto" / "web" / "gal"

venues = {v["index"]: v for v in
          json.loads((ROOT / "_venues_partial.json").read_text(encoding="utf-8"))}
sch = {d["index"]: d for d in
       json.loads((ROOT / "schedule/schedule.json").read_text(encoding="utf-8"))["days"]}
verdicts = {v["index"]: v for v in
            json.loads((ROOT / "_verdicts_partial.json").read_text(encoding="utf-8"))}

# why each of these eight is in the sample — the reason it was worth rendering
WHY = {
    4:   ("שימר סמל קיים", "מכרה ה-DOPA-MINE שהמצאנו שרד — הסוכנים הגיעו לאותו רעיון בלי לדעת עליו."),
    29:  ("שחזור הקורפוס", "העולם המקורי של Sketchy, מילה במילה. מבחן שפיות: האם המודל מגיע לאותו עמוד?"),
    60:  ("פּוּן חזק וטרי", "Cloza-pine נשמע clothes-pin — כאן נבחרה מנסרה (pine). האם הפּוּן נוחת?"),
    5:   ("סומן כמתנגש", "הפרק זקוק ל-RAPHE_TAPESTRY ולסצנות נוספות שלנו, והעולם החדש לא מזכיר אותן."),
    2:   ("איזומורפיזם מרחבי", "אין פּוּן — מבנה התחנה עצמו אמור למפות את האנטומיה."),
    37:  ("התנגשות מקומות", "אחד משלושה מסלולי באולינג בקורס (37, 55, 76). זו הבעיה המערכתית."),
    103: ("הביקורת פסלה", "הרמז העברי מאויית שגוי: חוק הנוער הוא NOAR, לא NA'AR."),
    143: ("סוג הפרק הקשה", "משפטי/אתי — אין פּוּן ואין תסמינים לצייר. אם זה עובד, הכל עובד."),
}

cards = []
for i in sorted(WHY):
    v, day, ver = venues[i], sch[i], verdicts.get(i)
    b64 = (GAL / f"{i:03d}.b64").read_text()
    tag, note = WHY[i]
    props = "".join(f"<li>{p.split('(')[0].split(',')[0].strip()}</li>"
                    for p in v.get("stock_props", [])[:10])
    rej = ""
    if ver and ver.get("verdict") == "REJECT":
        rej = (f'<p class="rej"><b>הביקורת פסלה:</b> {ver["reason"][:400]}</p>')
    cards.append(f"""
  <article>
    <div class="shot"><img src="data:image/jpeg;base64,{b64}"
         alt="עולם לפרק {i}: {v['sign_text']}" loading="lazy"></div>
    <div class="meta">
      <p class="why"><span class="pin">{tag}</span> {note}</p>
      <h3><span class="ch">פרק {i}</span> {day['title_he']}</h3>
      <p class="sign-row"><span class="sign">{v['sign_text']}</span></p>
      <dl>
        <dt>הגזירה</dt><dd>{v['pun_explanation'][:300]}</dd>
        <dt>מה כולם עושים</dt><dd>{v['activity'][:200]}</dd>
        <dt>עוגן</dt><dd>{v['anchor_structure'][:200]}</dd>
      </dl>
      <ul class="props">{props}</ul>
      {rej}
    </div>
  </article>""")

HTML = f"""<title>שמונה עולמות — לשיפוט</title>
<style>
  :root {{
    color-scheme: light dark;
    --ground:#E7EAEC; --surface:#FBFCFC; --sunk:#DDE2E5;
    --ink:#141A21; --ink-2:#3C4652; --muted:#69747F; --line:#C6CDD3;
    --green:#00863A; --magenta:#B01271; --cyan:#0B6E86;
    --plate:#F6F4EC; --plate-ink:#1A1A18; --plate-edge:#B9B4A3;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --ground:#101418; --surface:#171C22; --sunk:#0B0E11;
      --ink:#E6EBEF; --ink-2:#B3BDC6; --muted:#7E8A95; --line:#2B333B;
      --green:#3ED17C; --magenta:#F26CB4; --cyan:#4CC2DC;
      --plate:#2A2822; --plate-ink:#F0EDE3; --plate-edge:#4A463C;
    }}
  }}
  :root[data-theme="dark"] {{
    --ground:#101418; --surface:#171C22; --sunk:#0B0E11;
    --ink:#E6EBEF; --ink-2:#B3BDC6; --muted:#7E8A95; --line:#2B333B;
    --green:#3ED17C; --magenta:#F26CB4; --cyan:#4CC2DC;
    --plate:#2A2822; --plate-ink:#F0EDE3; --plate-edge:#4A463C;
  }}
  :root[data-theme="light"] {{
    --ground:#E7EAEC; --surface:#FBFCFC; --sunk:#DDE2E5;
    --ink:#141A21; --ink-2:#3C4652; --muted:#69747F; --line:#C6CDD3;
    --green:#00863A; --magenta:#B01271; --cyan:#0B6E86;
    --plate:#F6F4EC; --plate-ink:#1A1A18; --plate-edge:#B9B4A3;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; direction:rtl; background:var(--ground); color:var(--ink);
    font-family:"Segoe UI","Noto Sans Hebrew","Arial Hebrew",Arial,system-ui,sans-serif;
    font-size:16px; line-height:1.62; }}
  .wrap {{ max-width:1120px; margin:0 auto; padding:32px 20px 90px; }}
  header {{ border-bottom:2px solid var(--ink); padding-bottom:18px; margin-bottom:26px; }}
  .kicker {{ font-size:12px; letter-spacing:.16em; text-transform:uppercase;
    color:var(--muted); font-weight:600; margin:0 0 6px; }}
  h1 {{ font-size:clamp(26px,4vw,36px); margin:0 0 8px; line-height:1.15; text-wrap:balance; }}
  .dek {{ margin:0; color:var(--ink-2); max-width:64ch; }}
  .lead {{ background:var(--surface); border:1px solid var(--line); border-right:3px solid var(--cyan);
    border-radius:3px; padding:14px 16px; margin:0 0 30px; color:var(--ink-2); }}
  .lead b {{ color:var(--ink); }}

  article {{ background:var(--surface); border:1px solid var(--line); border-radius:3px;
    overflow:hidden; margin-bottom:26px; }}
  .shot {{ overflow-x:auto; background:var(--sunk); }}
  .shot img {{ display:block; width:100%; height:auto; min-width:520px; }}
  .meta {{ padding:16px 18px 20px; }}
  .why {{ margin:0 0 12px; font-size:14px; color:var(--ink-2); }}
  .pin {{ display:inline-block; font-size:11px; letter-spacing:.08em; text-transform:uppercase;
    font-weight:700; color:var(--cyan); border:1px solid currentColor; border-radius:2px;
    padding:2px 7px; margin-left:8px; }}
  h3 {{ margin:0 0 10px; font-size:19px; }}
  .ch {{ color:var(--muted); font-weight:400; font-size:14px; margin-left:8px;
    font-variant-numeric:tabular-nums; }}
  .sign-row {{ margin:0 0 14px; }}
  .sign {{ direction:ltr; unicode-bidi:isolate; display:inline-block;
    background:var(--plate); color:var(--plate-ink); border:1.5px solid var(--plate-edge);
    border-radius:2px; padding:6px 13px; font-family:ui-monospace,"Consolas",monospace;
    font-size:14px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }}
  dl {{ display:grid; grid-template-columns:auto 1fr; gap:6px 14px; margin:0 0 14px; }}
  dt {{ font-size:11.5px; letter-spacing:.09em; text-transform:uppercase; color:var(--muted);
    font-weight:700; padding-top:3px; }}
  dd {{ margin:0; color:var(--ink-2); font-size:14.5px; }}
  .props {{ display:flex; flex-wrap:wrap; gap:5px; margin:0; padding:0; list-style:none; }}
  .props li {{ font-size:12.5px; background:var(--sunk); border:1px solid var(--line);
    border-radius:2px; padding:2px 7px; color:var(--ink-2); }}
  .rej {{ margin:14px 0 0; padding:11px 13px; border-radius:3px; font-size:14px;
    border:1px solid var(--magenta);
    background:color-mix(in srgb,var(--magenta) 8%,transparent); }}
  .rej b {{ color:var(--magenta); }}
  footer {{ margin-top:44px; padding-top:16px; border-top:1px solid var(--line);
    font-size:13.5px; color:var(--muted); }}
  code {{ font-family:ui-monospace,"Consolas",monospace; font-size:.9em; background:var(--sunk);
    padding:1px 5px; border-radius:2px; direction:ltr; unicode-bidi:isolate;
    display:inline-block; }}
  @media (prefers-reduced-motion:reduce) {{ * {{ transition:none!important; animation:none!important; }} }}
</style>

<div class="wrap">
  <header>
    <p class="kicker">Sketchy v2 · תצוגת עולמות</p>
    <h1>שמונה עולמות, מצוירים</h1>
    <p class="dek">אלה הם העולמות בלבד — המקום, השלט, העוגן והפעילות המשותפת.
      עדיין ללא עובדות, ללא מספרים וללא מקרא: אלה מגיעים מהצינור עצמו, שטרם נבנה.</p>
  </header>

  <p class="lead"><b>מה לשפוט כאן:</b> האם המקום נראה כמו עמוד Sketchy — משעמם, צפוף,
    מלא אביזרים שאפשר לתלות עליהם עובדות. האם שם המקום נוחת ונקרא.
    והאם יש עוגן דומם אחד שחוצה את הפריים, בלי גיבור.</p>

  {"".join(cards)}

  <footer>נבנה מ־<code>_venues_partial.json</code>. הרינדור: ננו בננה פרו ב־2752×1536,
    לפי כללי הקומפוזיציה של <code>SKETCHY_SPEC_V2.md</code>.</footer>
</div>
"""

out = ROOT / "_proto" / "web" / "gallery.html"
out.write_text(HTML, encoding="utf-8")
print(f"wrote {out} ({len(HTML)//1024} KB, {len(cards)} venues)")
