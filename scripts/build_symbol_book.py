#!/usr/bin/env python3
"""Every symbol, pun and scene the plates use, as one readable Hebrew-first page.

The ideas live in schedule/symbols.json, schedule/lexicon.json and
schedule/venues.json, which the pipeline reads and nobody can browse. This
renders them into docs/symbol-book.html for brainstorming new scenes: one tab
per kind, Hebrew first and in full, the English folded underneath.

The Hebrew for the 144 chapter venues and the 25 corpus props did not exist in
the data; it was written for this book (2026-10-25) and lives in
schedule/symbol_book_he.json, keyed by "venue-N" / "prop-KEY".

    python scripts/build_symbol_book.py
"""
from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "symbol-book.html"
HE_FILE = REPO / "schedule" / "symbol_book_he.json"
DERIVATION_HE = {"PHONETIC_PUN": "משחק צליל", "ACRONYM_EMBED": "ראשי תיבות בשלט",
                 "LITERAL_PICTURE": "תמונה מילולית", "SPATIAL_ISOMORPHISM": "מבנה מרחבי"}


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def load(name):
    return json.loads((REPO / "schedule" / name).read_text(encoding="utf-8"))


def he_title(he: str, fallback: str) -> str:
    """The first clause of a Hebrew explanation, which names the thing."""
    head = re.split(r"\s+[—–-]\s+|\.\s|:\s", he or "", maxsplit=1)[0].strip()
    return head if 2 <= len(head) <= 70 else fallback.replace("_", " ")


def card(title, body_he, english=(), chips=(), sub="", group="", search=""):
    chip = "".join(f"<span class='chip'>{e(c)}</span>" for c in chips if c)
    eng = "".join(f"<p><b>{e(k)}</b> <span dir='ltr'>{e(v)}</span></p>" for k, v in english if v)
    eng_html = f"<details><summary>באנגלית</summary><div class='en'>{eng}</div></details>" if eng else ""
    body = "".join(f"<p>{e(p)}</p>" for p in (body_he if isinstance(body_he, (list, tuple)) else [body_he]) if p)
    return (f"<article class='card' data-g='{e(group)}' data-s='{e((title + ' ' + search).lower())}'>"
            f"<header><h3 dir='auto'>{e(title)}</h3>{f'<div class=sub dir=ltr>{e(sub)}</div>' if sub else ''}</header>"
            f"<div class='chips'>{chip}</div><div class='he'>{body}</div>{eng_html}</article>")


def main() -> int:
    s, lex, ven = load("symbols.json"), load("lexicon.json"), load("venues.json")
    venues = ven.get("venues", ven) if isinstance(ven, dict) else ven
    he = {x["id"]: x for x in json.loads(HE_FILE.read_text(encoding="utf-8"))} if HE_FILE.exists() else {}
    tabs = []

    # scenes
    cards = []
    for key, sc in s["scenes"].items():
        if not isinstance(sc, dict):
            continue
        cards.append(card(he_title(sc.get("hebrew_explanation"), sc.get("scene_name") or key),
                          [sc.get("hebrew_explanation")],
                          [("Scene", sc.get("scene_name")), ("Setting", sc.get("setting"))]
                          + [("•", r) for r in sc.get("recurring_elements") or []],
                          sub=sc.get("scene_name"), search=json.dumps(sc, ensure_ascii=False)))
    tabs.append(("scenes", "סצנות חוזרות", "העולמות שסמלים רבים גרים בהם לאורך כל הקורס.", cards, []))

    # symbols by category
    cards, cats = [], defaultdict(int)
    for key, sy in s["symbols"].items():
        if not isinstance(sy, dict):
            continue
        cat = sy.get("category") or "אחר"
        group = cat.split("(")[0].strip()
        cats[group] += 1
        cards.append(card(he_title(sy.get("hebrew_explanation"), key), [sy.get("hebrew_explanation")],
                          [("Symbol", sy.get("symbol")), ("Why", sy.get("mnemonic_link")),
                           ("In action", sy.get("function_depiction")), ("Category", cat)],
                          chips=[group, sy.get("scene") if sy.get("scene") and not str(sy.get("scene")).startswith("(") else ""],
                          sub=key, group=group, search=json.dumps(sy, ensure_ascii=False)))
    tabs.append(("symbols", "סמלים", f"{len(cards)} סמלים. אפשר לסנן לפי מערכת.", cards, sorted(cats)))

    # drugs
    cards = [card(he_title(d.get("hebrew_explanation"), name), [d.get("hebrew_explanation")],
                  [("Symbol", d.get("symbol")), ("Why", d.get("mnemonic_link")),
                   ("In action", d.get("function_depiction")), ("Appears in", ", ".join(d.get("appears_in_scenes") or []))],
                  sub=name, search=json.dumps(d, ensure_ascii=False))
             for name, d in s.get("drugs", {}).items() if isinstance(d, dict)]
    tabs.append(("drugs", "תרופות", f"{len(cards)} תרופות עם סמל משלהן.", cards, []))

    # ions + props
    cards = [card(he_title(i.get("hebrew_explanation"), name), [i.get("hebrew_explanation")],
                  [("Symbol", i.get("symbol")), ("Why", i.get("mnemonic_link"))],
                  chips=["יון"], sub=name, search=json.dumps(i, ensure_ascii=False))
             for name, i in s.get("ions", {}).items() if isinstance(i, dict)]
    for k, t in lex["tokens"].items():
        if t.get("source") != "sketchy-corpus":
            continue
        h = he.get(f"prop-{k}", {})
        cards.append(card(h.get("title_he") or k.replace("_", " "), [h.get("pun_he"), h.get("scene_he")],
                          [("Prop", t.get("prop")), ("Hook", t.get("hook"))],
                          chips=["אביזר קבוע"], sub=k, search=json.dumps(t, ensure_ascii=False)))
    tabs.append(("props", "יונים ואביזרים", "אביזרים שחוזרים בכל סצנה ונושאים תמיד אותה משמעות.", cards, []))

    # venues
    cards = []
    for v in venues:
        h = he.get(f"venue-{v.get('index')}", {})
        cards.append(card(h.get("title_he") or f"{v.get('sign_text')} — {v.get('title_en')}",
                          [h.get("pun_he"), h.get("scene_he")],
                          [("Chapter", v.get("title_en")), ("Pun", v.get("pun_explanation")),
                           ("Place", v.get("place")), ("Everyone is", v.get("activity")),
                           ("Anchor", v.get("anchor_structure"))],
                          chips=[f"פרק {v.get('index')}", DERIVATION_HE.get(v.get("derivation"), v.get("derivation"))],
                          search=json.dumps(v, ensure_ascii=False) + json.dumps(h, ensure_ascii=False)))
    tabs.append(("venues", "מקום לכל פרק", "השלט, משחק המילים והסצנה של כל אחד מ-144 הפרקים.", cards, []))

    # rules
    cards = [card("הכלל: משמעות אחת לכל סמל",
                  ["סמל חוזר (אביזר שנושא עובדה) מקבל משמעות אחת בכל הקורס, כדי שכשתראה אותו תדע מיד מה הוא אומר.",
                   "תפאורה — חלון, כיסא, עץ, קיר — חופשית לגמרי, ואפשר להשתמש בה בכל סצנה בלי מגבלה.",
                   "הסמל של פסיכוזה הוא האנטנות על הראש, לא הכובע."],
                  [("Usage", lex.get("usage"))])]
    cards += [card(k.replace("_", " "), [f"הוחלף ב: {r.get('replaced_by')}"],
                   [("Why retired", r.get("why"))], chips=["סמל שפרש"])
              for k, r in lex.get("retired", {}).items()]
    tabs.append(("rules", "כללים", "מה למדנו על מה שעובד ומה לא.", cards, []))

    nav = "".join(f"<button type='button' role='tab' data-tab='{tid}' aria-selected='{str(i == 0).lower()}'>"
                  f"{e(t)} <span>{len(c)}</span></button>" for i, (tid, t, _s, c, _g) in enumerate(tabs))
    panes = ""
    for i, (tid, title, sub, cards, groups) in enumerate(tabs):
        filt = ""
        if groups:
            filt = "<div class='filters'><button type='button' class='f on' data-f=''>הכול</button>" + "".join(
                f"<button type='button' class='f' data-f='{e(g)}'>{e(g)}</button>" for g in groups) + "</div>"
        panes += (f"<section class='pane' id='{tid}' role='tabpanel' {'' if i == 0 else 'hidden'}>"
                  f"<p class='lede2'>{e(sub)}</p>{filt}<div class='grid'>{''.join(cards)}</div></section>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(TEMPLATE.replace("__NAV__", nav).replace("__PANES__", panes), encoding="utf-8")
    print(f"{OUT} · {OUT.stat().st_size // 1024} KB · Hebrew for {len(he)} venues/props")
    return 0


TEMPLATE = """<title>ספר הסמלים</title>
<style>
/* A calm reading book: warm paper, dark ink, one teal marker. Hebrew leads in a
   readable size; the English is folded away for whoever wants it. */
:root{--paper:#f5f3ee;--sheet:#fffefb;--ink:#22252a;--muted:#6a6f76;--rule:#e4e0d6;
  --mark:#0f6e78;--mark-soft:#e1f0f1;--chip:#efece4;
  --f-d:"Secular One","Assistant",sans-serif;--f-b:"Assistant","Segoe UI",Arial,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#141618;--sheet:#1b1e21;--ink:#e8eaec;
  --muted:#a0a6ad;--rule:#2b3035;--mark:#5fc8d2;--mark-soft:#16343a;--chip:#252a2f;color-scheme:dark}}
:root[data-theme="dark"]{--paper:#141618;--sheet:#1b1e21;--ink:#e8eaec;--muted:#a0a6ad;--rule:#2b3035;
  --mark:#5fc8d2;--mark-soft:#16343a;--chip:#252a2f;color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font:17px/1.75 var(--f-b);margin:0}
.wrap{max-width:1080px;margin:0 auto;padding-inline:16px;padding-block:28px 72px}
h1{font:400 clamp(30px,5vw,44px)/1.1 var(--f-d);margin:0 0 8px}
.lede{color:var(--muted);max-width:62ch;margin:0 0 20px;font-size:16px}
.bar{position:sticky;top:env(safe-area-inset-top,0px);z-index:3;background:var(--paper);
  padding-block:10px;border-bottom:1px solid var(--rule);display:flex;flex-wrap:wrap;gap:8px;align-items:center}
[role=tab]{font:inherit;font-size:15px;border:0;background:transparent;color:var(--muted);padding:6px 12px;
  border-radius:8px;cursor:pointer}
[role=tab] span{font-size:12px;opacity:.7}
[role=tab][aria-selected=true]{background:var(--mark-soft);color:var(--mark);font-weight:600}
[role=tab]:focus-visible,.f:focus-visible,summary:focus-visible{outline:2px solid var(--mark);outline-offset:2px}
.bar input{margin-inline-start:auto;font:inherit;font-size:15px;padding:7px 14px;border:1px solid var(--rule);
  border-radius:10px;background:var(--sheet);color:var(--ink);min-width:min(260px,100%)}
.lede2{color:var(--muted);margin:22px 0 12px;font-size:15px}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 16px}
.f{font:inherit;font-size:13px;border:1px solid var(--rule);background:var(--sheet);color:var(--ink);
  padding:3px 11px;border-radius:999px;cursor:pointer}
.f.on{background:var(--mark);color:var(--sheet);border-color:var(--mark)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(440px,100%),1fr));gap:16px}
.card{background:var(--sheet);border:1px solid var(--rule);border-radius:12px;padding:18px 20px;min-width:0}
.card header{margin-bottom:6px}
.card h3{font:600 19px/1.35 var(--f-b);margin:0;text-wrap:balance;overflow-wrap:anywhere}
.card .sub{font-size:12px;color:var(--muted);margin-top:2px;text-align:right}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px}
.chip{font-size:12.5px;background:var(--chip);color:var(--muted);padding:1px 9px;border-radius:999px}
.he p{margin:0 0 10px;max-width:68ch}
.he p:last-child{margin-bottom:0}
details{margin-top:12px;border-top:1px solid var(--rule);padding-top:8px}
summary{cursor:pointer;color:var(--muted);font-size:13px}
.en{direction:ltr;text-align:left;font-size:13.5px;line-height:1.55;color:var(--muted);margin-top:6px}
.en p{margin:0 0 6px}
.en b{color:var(--ink);font-weight:600}
.card.hide{display:none}
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Assistant:wght@400;600&family=Secular+One&display=swap">
<div class="wrap" dir="rtl" lang="he">
  <h1>ספר הסמלים</h1>
  <p class="lede">כל הסמלים, משחקי המילים והסצנות שהלוחות משתמשים בהם, להמצאת סצנות חדשות.
  בכל כרטיס ההסבר בעברית מופיע קודם; המקור באנגלית מקופל מתחתיו.</p>
  <nav class="bar" role="tablist">__NAV__<input id="q" type="search" placeholder="חיפוש: סרוטונין, ליתיום, D2…" aria-label="חיפוש"></nav>
  __PANES__
</div>
<script>
const q=document.getElementById('q');let tab=document.querySelector('[role=tab]').dataset.tab,grp='';
function apply(){const t=q.value.trim().toLowerCase();const pane=document.getElementById(tab);
  pane.querySelectorAll('.card').forEach(c=>c.classList.toggle('hide',(t&&!c.dataset.s.includes(t))||(grp&&c.dataset.g!==grp)));}
document.querySelectorAll('[role=tab]').forEach(b=>b.addEventListener('click',()=>{
  tab=b.dataset.tab;grp='';document.querySelectorAll('[role=tab]').forEach(x=>x.setAttribute('aria-selected',x===b));
  document.querySelectorAll('.pane').forEach(p=>p.hidden=p.id!==tab);
  document.querySelectorAll('.f').forEach(f=>f.classList.toggle('on',f.dataset.f===''));apply();}));
document.addEventListener('click',ev=>{const f=ev.target.closest('.f');if(!f)return;grp=f.dataset.f;
  f.parentElement.querySelectorAll('.f').forEach(x=>x.classList.toggle('on',x===f));apply();});
q.addEventListener('input',apply);
</script>
"""

if __name__ == "__main__":
    raise SystemExit(main())
