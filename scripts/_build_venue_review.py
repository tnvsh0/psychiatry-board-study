#!/usr/bin/env python3
"""
Build the venue-review page — the artifact the user judges the Sketchy v2 work
from. Regenerate with:  python scripts/_build_venue_review.py

Reads  _proto/web/rows.json, _proto/web/{final,control}.b64
Writes _proto/web/review.html
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
WEB = ROOT / "_proto" / "web"


def _load(name, default):
    p = ROOT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


# ── assemble the judging dataset ───────────────────────────────────────────
sym = _load("schedule/symbols.json", {"scenes": {}})
scenes = set(sym.get("scenes", {}))
sch = {d["index"]: d for d in _load("schedule/schedule.json", {"days": []})["days"]}
venues = {v["index"]: v for v in _load("_venues_partial.json", [])}
verdicts = {v["index"]: v for v in _load("_verdicts_partial.json", [])}

rows = []
for i in sorted(sch):
    day, v, ver = sch[i], venues.get(i), verdicts.get(i)
    ours = sorted(set(day.get("symbols_needed", [])) & scenes)
    kept = []
    if v and ours:
        blob = f"{v['place']} {v['sign_text']} {v['pun_explanation']}".lower()
        kept = [s for s in ours
                if any(w.lower() in blob for w in s.split("_") if len(w) > 3)]
    rows.append({
        "i": i, "he": day["title_he"], "en": day["title_en"],
        "type": day["type"], "phase": day["phase"],
        "sign": (v or {}).get("sign_text"), "place": (v or {}).get("place"),
        "activity": (v or {}).get("activity"),
        "anchor": (v or {}).get("anchor_structure"),
        "der": (v or {}).get("derivation"), "conf": (v or {}).get("confidence"),
        "why": (v or {}).get("pun_explanation"), "props": (v or {}).get("stock_props", []),
        "ours": ours, "kept": kept,
        "verdict": (ver or {}).get("verdict"),
        "vreason": (ver or {}).get("reason"),
        "valt": (ver or {}).get("better_alternative"),
    })

WEB.mkdir(parents=True, exist_ok=True)
(WEB / "rows.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

img_final = (WEB / "final.b64").read_text()
img_control = (WEB / "control.b64").read_text()

done = [r for r in rows if r["sign"]]
rejected = [r for r in rows if r["verdict"] == "REJECT"]
accepted = [r for r in rows if r["verdict"] == "ACCEPT"]
unreviewed = [r for r in rows if r["sign"] and not r["verdict"]]
conflicts = [r for r in done if r["ours"] and not r["kept"]]
preserved = [r for r in done if r["ours"] and r["kept"]]

DER_HE = {
    "PHONETIC_PUN": "משחק מילים",
    "ACRONYM_EMBED": "ראשי תיבות מוטמעים",
    "SPATIAL_ISOMORPHISM": "מבנה מרחבי",
    "LITERAL_PICTURE": "תמונה מילולית",
}

HTML = """<title>Sketchy v2 — תוצאות לשיפוט</title>
<style>
  :root {
    color-scheme: light dark;
    --ground:#E7EAEC; --surface:#FBFCFC; --sunk:#DDE2E5;
    --ink:#141A21; --ink-2:#3C4652; --muted:#69747F; --line:#C6CDD3;
    --reg-green:#00863A; --reg-magenta:#B01271; --reg-cyan:#0B6E86;
    --plate:#F6F4EC; --plate-ink:#1A1A18; --plate-edge:#B9B4A3;
    --shadow:0 1px 0 rgba(20,26,33,.05), 0 6px 20px -12px rgba(20,26,33,.35);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --ground:#101418; --surface:#171C22; --sunk:#0B0E11;
      --ink:#E6EBEF; --ink-2:#B3BDC6; --muted:#7E8A95; --line:#2B333B;
      --reg-green:#3ED17C; --reg-magenta:#F26CB4; --reg-cyan:#4CC2DC;
      --plate:#2A2822; --plate-ink:#F0EDE3; --plate-edge:#4A463C;
      --shadow:0 1px 0 rgba(0,0,0,.4), 0 6px 20px -12px rgba(0,0,0,.8);
    }
  }
  :root[data-theme="dark"] {
    --ground:#101418; --surface:#171C22; --sunk:#0B0E11;
    --ink:#E6EBEF; --ink-2:#B3BDC6; --muted:#7E8A95; --line:#2B333B;
    --reg-green:#3ED17C; --reg-magenta:#F26CB4; --reg-cyan:#4CC2DC;
    --plate:#2A2822; --plate-ink:#F0EDE3; --plate-edge:#4A463C;
    --shadow:0 1px 0 rgba(0,0,0,.4), 0 6px 20px -12px rgba(0,0,0,.8);
  }
  :root[data-theme="light"] {
    --ground:#E7EAEC; --surface:#FBFCFC; --sunk:#DDE2E5;
    --ink:#141A21; --ink-2:#3C4652; --muted:#69747F; --line:#C6CDD3;
    --reg-green:#00863A; --reg-magenta:#B01271; --reg-cyan:#0B6E86;
    --plate:#F6F4EC; --plate-ink:#1A1A18; --plate-edge:#B9B4A3;
    --shadow:0 1px 0 rgba(20,26,33,.05), 0 6px 20px -12px rgba(20,26,33,.35);
  }

  * { box-sizing:border-box; }
  body {
    margin:0; direction:rtl; background:var(--ground); color:var(--ink);
    font-family:"Segoe UI","Noto Sans Hebrew","Arial Hebrew",Arial,system-ui,sans-serif;
    font-size:16px; line-height:1.6; -webkit-font-smoothing:antialiased;
  }
  .wrap { max-width:1180px; margin:0 auto; padding:32px 20px 96px; }

  /* ── masthead ─────────────────────────────────────────────── */
  header { border-bottom:2px solid var(--ink); padding-bottom:18px; margin-bottom:30px; }
  .kicker {
    font-size:12px; letter-spacing:.16em; text-transform:uppercase;
    color:var(--muted); font-weight:600; margin:0 0 6px;
  }
  h1 { font-size:clamp(26px,4vw,38px); line-height:1.15; margin:0 0 8px; text-wrap:balance; letter-spacing:-.01em; }
  .dek { margin:0; color:var(--ink-2); max-width:62ch; }

  h2 {
    font-size:13px; letter-spacing:.14em; text-transform:uppercase; font-weight:700;
    color:var(--muted); margin:44px 0 14px; padding-bottom:8px; border-bottom:1px solid var(--line);
  }

  /* ── tally ────────────────────────────────────────────────── */
  .tally { display:grid; grid-template-columns:repeat(auto-fit,minmax(126px,1fr)); gap:1px;
           background:var(--line); border:1px solid var(--line); border-radius:3px; overflow:hidden; }
  .tally div { background:var(--surface); padding:13px 14px; }
  .tally b { display:block; font-size:26px; font-weight:700; line-height:1.1;
             font-variant-numeric:tabular-nums; letter-spacing:-.02em; }
  .tally span { font-size:12px; color:var(--muted); }
  .t-green b { color:var(--reg-green); } .t-mag b { color:var(--reg-magenta); }
  .t-cyan b { color:var(--reg-cyan); }

  /* ── renders ──────────────────────────────────────────────── */
  .shots { display:grid; grid-template-columns:1fr; gap:20px; }
  @media (min-width:900px){ .shots { grid-template-columns:1fr 1fr; } }
  figure { margin:0; background:var(--surface); border:1px solid var(--line);
           border-radius:3px; overflow:hidden; box-shadow:var(--shadow); }
  figure img { display:block; width:100%; height:auto; }
  figcaption { padding:12px 14px; font-size:14px; color:var(--ink-2); border-top:1px solid var(--line); }
  figcaption strong { display:block; color:var(--ink); font-size:13px;
                      letter-spacing:.08em; text-transform:uppercase; margin-bottom:4px; }

  /* ── controls ─────────────────────────────────────────────── */
  .controls { position:sticky; top:0; z-index:5; background:var(--ground);
              padding:12px 0; border-bottom:1px solid var(--line);
              display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
  .controls input[type=search] {
    flex:1 1 200px; min-width:160px; padding:8px 11px; font:inherit; font-size:14px;
    background:var(--surface); color:var(--ink); border:1px solid var(--line); border-radius:3px;
  }
  .chip { font:inherit; font-size:13px; padding:7px 12px; cursor:pointer;
          background:var(--surface); color:var(--ink-2);
          border:1px solid var(--line); border-radius:999px; }
  .chip[aria-pressed="true"] { background:var(--ink); color:var(--ground); border-color:var(--ink); }
  .chip:focus-visible, input:focus-visible, summary:focus-visible {
    outline:2px solid var(--reg-cyan); outline-offset:2px; }

  /* ── chapter rows ─────────────────────────────────────────── */
  .list { display:flex; flex-direction:column; gap:1px; background:var(--line);
          border:1px solid var(--line); border-radius:3px; overflow:hidden; margin-top:14px; }
  details { background:var(--surface); }
  details[open] { background:var(--surface); }
  summary { display:grid; grid-template-columns:auto 1fr auto; gap:8px 14px;
            align-items:center; padding:12px 14px; cursor:pointer; list-style:none; }
  summary::-webkit-details-marker { display:none; }
  summary:hover { background:var(--sunk); }
  .num { font-variant-numeric:tabular-nums; font-size:13px; color:var(--muted);
         font-weight:700; min-width:2.6em; }
  .titles { min-width:0; }
  .he { font-weight:600; }
  .en { font-size:12.5px; color:var(--muted); direction:ltr; text-align:right; unicode-bidi:isolate; }
  .flags { display:flex; gap:6px; align-items:center; flex-wrap:wrap; justify-content:flex-start; }

  /* the venue sign, treated as painted lettering on a plate */
  .sign {
    direction:ltr; unicode-bidi:isolate; display:inline-block;
    background:var(--plate); color:var(--plate-ink);
    border:1.5px solid var(--plate-edge); border-radius:2px;
    padding:5px 11px; font-family:ui-monospace,"Consolas","SF Mono",monospace;
    font-size:13px; font-weight:700; letter-spacing:.11em; text-transform:uppercase;
    white-space:nowrap; box-shadow:inset 0 0 0 1px rgba(255,255,255,.35);
  }
  .sign.none { background:transparent; border-style:dashed; color:var(--muted);
               font-weight:400; letter-spacing:.04em; }

  .tag { font-size:11px; letter-spacing:.07em; text-transform:uppercase; font-weight:700;
         padding:3px 8px; border-radius:2px; border:1px solid currentColor; white-space:nowrap; }
  .t-strong { color:var(--reg-green); }
  .t-work   { color:var(--reg-cyan); }
  .t-clash  { color:var(--reg-magenta); background:color-mix(in srgb,var(--reg-magenta) 12%,transparent); }
  .t-keep   { color:var(--reg-green); background:color-mix(in srgb,var(--reg-green) 12%,transparent); }

  .body { padding:2px 14px 18px; border-top:1px dashed var(--line); }
  .body dl { display:grid; grid-template-columns:auto 1fr; gap:6px 14px; margin:14px 0 0; }
  .body dt { font-size:11.5px; letter-spacing:.09em; text-transform:uppercase;
             color:var(--muted); font-weight:700; padding-top:3px; }
  .body dd { margin:0; color:var(--ink-2); }
  .props { display:flex; flex-wrap:wrap; gap:5px; margin:0; padding:0; list-style:none; }
  .props li { font-size:12.5px; background:var(--sunk); border:1px solid var(--line);
              border-radius:2px; padding:2px 7px; color:var(--ink-2); }
  .clash-note { margin-top:14px; padding:11px 13px; border-radius:3px;
                border:1px solid var(--reg-magenta);
                background:color-mix(in srgb,var(--reg-magenta) 8%,transparent); font-size:14px; }
  .clash-note b { color:var(--reg-magenta); }
  code { font-family:ui-monospace,"Consolas",monospace; font-size:.9em;
         background:var(--sunk); padding:1px 5px; border-radius:2px; direction:ltr;
         unicode-bidi:isolate; display:inline-block; }
  .empty { padding:26px 14px; color:var(--muted); background:var(--surface); text-align:center; }
  footer { margin-top:52px; padding-top:16px; border-top:1px solid var(--line);
           font-size:13px; color:var(--muted); }
  @media (prefers-reduced-motion:reduce){ *{ transition:none!important; animation:none!important; } }
</style>

<div class="wrap">
  <header>
    <p class="kicker">Sketchy v2 · שלב יישום</p>
    <h1>העולמות והמספרים — לשיפוטך</h1>
    <p class="dek">שתי תוצרות לבדיקה: הרכבת המספרים בקוד על רינדור אמיתי, וכל __DONE__ העולמות. ביקורת אדוורסרית עברה על __REVIEWED__ מהם ופסלה __REJ__. שום דבר עדיין לא נכתב לתוך <code>schedule/</code> — זו טיוטה בלבד.</p>
  </header>

  <div class="tally">
    <div><b>__DONE__</b><span>עולמות נכתבו</span></div>
    <div class="t-green"><b>__ACC__</b><span>עברו ביקורת</span></div>
    <div class="t-mag"><b>__REJ__</b><span>נפסלו</span></div>
    <div><b>__UNREV__</b><span>טרם נבדקו</span></div>
    <div class="t-mag"><b>__CLASH__</b><span>מתנגשים עם הסמלים שלנו</span></div>
    <div class="t-green"><b>__KEEP__</b><span>שימרו את שלנו</span></div>
  </div>

  <h2>הפרוטוטייפ — שתי גרסאות של אותה סצנה</h2>
  <div class="shots">
    <figure>
      <img src="data:image/jpeg;base64,__IMG_FINAL__" alt="רינדור עם מספרים שהורכבו בקוד">
      <figcaption><strong>מספרים מורכבים בקוד</strong>
        המודל צייר לוחות ריקים בצבע־מפתח; הקוד איתר אותם וכתב עליהם
        <span class="sign">70% AGAIN</span> <span class="sign">40-60% GENES</span>
        <span class="sign">4-8% TEENS</span>. הספרות נכונות מעצם הבנייה.
        פגם: ה־70% נחת על מיכל המשאית ולא על כרטיס החזור.</figcaption>
    </figure>
    <figure>
      <img src="data:image/jpeg;base64,__IMG_CONTROL__" alt="רינדור שבו המודל כתב את המספרים">
      <figcaption><strong>המודל כתב בעצמו</strong>
        הפעם המספרים יצאו נכונים — אבל קטנים, וליד־ם צצו
        <span class="sign">WARES ODKUEF</span> ונייר קרוע מג׳וברש.
        הכשל אקראי, ולכן ההרכבה קונה ודאות ולא ניצחון.</figcaption>
    </figure>
  </div>

  <h2>144 העולמות</h2>
  <div class="controls">
    <input type="search" id="q" placeholder="חיפוש: שם פרק, שלט, הסבר…" aria-label="חיפוש">
    <button class="chip" data-f="all" aria-pressed="true">הכל</button>
    <button class="chip" data-f="reject" aria-pressed="false">נפסלו</button>
    <button class="chip" data-f="clash" aria-pressed="false">מתנגשים</button>
    <button class="chip" data-f="unrev" aria-pressed="false">טרם נבדקו</button>
    <button class="chip" data-f="workable" aria-pressed="false">שמישים בלבד</button>
  </div>
  <div class="list" id="list"></div>

  <footer>
    נבנה מ־<code>_venues_partial.json</code>. הצלב מול <code>schedule/symbols.json</code>
    (17 סצנות שהמצאנו) — פרק מסומן כמתנגש כשהעולם החדש לא מזכיר סצנה קיימת שהפרק זקוק לה.
  </footer>
</div>

<script>
const ROWS = __ROWS__;
const DER = __DER__;
const list = document.getElementById('list');
const q = document.getElementById('q');
let filter = 'all';

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function card(r){
  const clash = r.sign && r.ours.length && !r.kept.length;
  const keep  = r.sign && r.kept.length;
  const flags = [];
  if (r.verdict === 'REJECT') flags.push('<span class="tag t-clash">נפסל</span>');
  else if (r.verdict === 'ACCEPT') flags.push('<span class="tag t-strong">עבר</span>');
  else if (r.sign) flags.push('<span class="tag t-work">לא נבדק</span>');
  if (clash) flags.push('<span class="tag t-clash">מתנגש</span>');
  if (keep)  flags.push('<span class="tag t-keep">שימר</span>');

  const sign = r.sign
    ? `<span class="sign">${esc(r.sign)}</span>`
    : `<span class="sign none">ממתין</span>`;

  const detail = r.sign ? `
    <dl>
      <dt>מנגנון</dt><dd>${esc(DER[r.der] || r.der || '')}</dd>
      <dt>למה</dt><dd>${esc(r.why)}</dd>
      <dt>המקום</dt><dd>${esc(r.place)}</dd>
      <dt>מה כולם עושים</dt><dd>${esc(r.activity)}</dd>
      <dt>עוגן</dt><dd>${esc(r.anchor)}</dd>
      <dt>מלאי</dt><dd><ul class="props">${
        (r.props||[]).map(p=>`<li>${esc(String(p).split(/[(,]/)[0].trim())}</li>`).join('')
      }</ul></dd>
    </dl>` : '<p style="color:var(--muted);margin:14px 0 0">טרם נכתב — ההרצה נקטעה במגבלת שימוש.</p>';

  const note = clash ? `<div class="clash-note"><b>סצנות שלנו שהפרק זקוק להן ולא הוזכרו:</b>
      ${r.ours.map(o=>`<code>${esc(o)}</code>`).join(' ')} —
      אלה סמלי־קבע (קולטן, גרעין), לא עולמות. הם אמורים להופיע כאביזרים
      <em>בתוך</em> העולם, לא במקומו.</div>` : '';

  const rej = r.verdict === 'REJECT' ? `<div class="clash-note">
      <b>הביקורת פסלה:</b> ${esc(r.vreason)}
      ${r.valt ? `<br><br><b>הצעה חלופית:</b> ${esc(r.valt)}` : ''}</div>` : '';

  return `<details><summary>
      <span class="num">${r.i}</span>
      <span class="titles"><span class="he">${esc(r.he)}</span><br>
        <span class="en">${esc(r.en)}</span></span>
      <span class="flags">${sign}${flags.join('')}</span>
    </summary><div class="body">${detail}${rej}${note}</div></details>`;
}

function render(){
  const term = q.value.trim().toLowerCase();
  const out = ROWS.filter(r=>{
    const clash = r.sign && r.ours.length && !r.kept.length;
    if (filter==='clash'    && !clash) return false;
    if (filter==='reject'   && r.verdict!=='REJECT') return false;
    if (filter==='unrev'    && (!r.sign || r.verdict)) return false;
    if (filter==='workable' && r.conf!=='workable') return false;
    if (!term) return true;
    return [r.he,r.en,r.sign,r.why,r.place].join(' ').toLowerCase().includes(term);
  });
  list.innerHTML = out.length ? out.map(card).join('')
    : '<p class="empty">אין תוצאות</p>';
}

document.querySelectorAll('.chip').forEach(b=>b.addEventListener('click',()=>{
  filter = b.dataset.f;
  document.querySelectorAll('.chip').forEach(x=>
    x.setAttribute('aria-pressed', String(x===b)));
  render();
}));
q.addEventListener('input', render);
render();
</script>
"""

out = (
    HTML.replace("__ROWS__", json.dumps(rows, ensure_ascii=False))
    .replace("__DER__", json.dumps(DER_HE, ensure_ascii=False))
    .replace("__IMG_FINAL__", img_final)
    .replace("__IMG_CONTROL__", img_control)
    .replace("__DONE__", str(len(done)))
    .replace("__REVIEWED__", str(len(accepted) + len(rejected)))
    .replace("__ACC__", str(len(accepted)))
    .replace("__REJ__", str(len(rejected)))
    .replace("__UNREV__", str(len(unreviewed)))
    .replace("__CLASH__", str(len(conflicts)))
    .replace("__KEEP__", str(len(preserved)))
)
(WEB / "review.html").write_text(out, encoding="utf-8")
print(f"wrote {WEB/'review.html'}  ({len(out)//1024} KB)")
print(f"  venues={len(done)}/144  accept={len(accepted)}  reject={len(rejected)}  "
      f"unreviewed={len(unreviewed)}  clash={len(conflicts)}  preserved={len(preserved)}")
