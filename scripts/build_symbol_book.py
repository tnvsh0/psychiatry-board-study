#!/usr/bin/env python3
"""Every symbol, pun and scene the plates use, as one readable page.

The ideas live in three data files the pipeline reads — schedule/symbols.json,
schedule/lexicon.json, schedule/venues.json — and nowhere a person can browse
them. This renders them into docs/symbol-book.html for brainstorming new scenes
with someone else: recurring scenes, symbols by system, drugs, ions, the
chapter venues with their sign puns, and the retired symbols with the reason.

    python scripts/build_symbol_book.py
"""
from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "symbol-book.html"
DERIVATION_HE = {"PHONETIC_PUN": "משחק צליל", "ACRONYM_EMBED": "ראשי תיבות בשלט",
                 "LITERAL_PICTURE": "תמונה מילולית", "SPATIAL_ISOMORPHISM": "מבנה מרחבי"}


def e(x) -> str:
    return html.escape("" if x is None else str(x))


def load(name):
    return json.loads((REPO / "schedule" / name).read_text(encoding="utf-8"))


def card(title, rows, he=None, tag=None, search=""):
    body = "".join(f"<div class='row'><span class='k'>{e(k)}</span><span dir='auto'>{e(v)}</span></div>"
                   for k, v in rows if v)
    he_html = f"<p class='he'>{e(he)}</p>" if he else ""
    tag_html = f"<span class='tag'>{e(tag)}</span>" if tag else ""
    return (f"<article class='card' data-s='{e((title + ' ' + search).lower())}'>"
            f"<h4 dir='auto'>{e(title)}{tag_html}</h4>{body}{he_html}</article>")


def main() -> int:
    s, lex, ven = load("symbols.json"), load("lexicon.json"), load("venues.json")
    venues = ven.get("venues", ven) if isinstance(ven, dict) else ven
    sections = []

    # 1. recurring scenes
    cards = []
    for key, sc in s["scenes"].items():
        if not isinstance(sc, dict):
            continue
        rows = [("המקום", sc.get("setting"))] + [("•", r) for r in sc.get("recurring_elements") or []]
        cards.append(card(sc.get("scene_name") or key, rows, sc.get("hebrew_explanation"),
                          key, json.dumps(sc, ensure_ascii=False)))
    sections.append(("scenes", "סצנות חוזרות", f"{len(cards)} עולמות שסמלים רבים גרים בהם", cards))

    # 2. symbols by category
    by_cat = defaultdict(list)
    for key, sy in s["symbols"].items():
        if isinstance(sy, dict):
            by_cat[sy.get("category") or "אחר"].append((key, sy))
    groups = []
    n = 0
    for cat in sorted(by_cat):
        cs = [card(key, [("סמל", sy.get("symbol")), ("למה", sy.get("mnemonic_link")),
                         ("בפעולה", sy.get("function_depiction")), ("סצנה", sy.get("scene"))],
                   sy.get("hebrew_explanation"), None, json.dumps(sy, ensure_ascii=False) + " " + cat)
              for key, sy in by_cat[cat]]
        n += len(cs)
        groups.append(f"<h3 class='cat'>{e(cat)} <small>{len(cs)}</small></h3><div class='grid'>{''.join(cs)}</div>")
    sections.append(("symbols", "סמלים לפי מערכת", f"{n} סמלים ב-{len(by_cat)} קטגוריות", groups))

    # 3. drugs
    cards = [card(name, [("סמל", d.get("symbol")), ("למה", d.get("mnemonic_link")),
                         ("בפעולה", d.get("function_depiction")),
                         ("מופיע ב", ", ".join(d.get("appears_in_scenes") or []))],
                  d.get("hebrew_explanation"), None, json.dumps(d, ensure_ascii=False))
             for name, d in s.get("drugs", {}).items() if isinstance(d, dict)]
    sections.append(("drugs", "תרופות", f"{len(cards)} תרופות עם סמל משלהן", cards))

    # 4. ions + corpus props
    cards = [card(name, [("סמל", i.get("symbol")), ("למה", i.get("mnemonic_link")),
                         ("מופיע ב", ", ".join(i.get("appears_in") or []))],
                  i.get("hebrew_explanation"), None, json.dumps(i, ensure_ascii=False))
             for name, i in s.get("ions", {}).items() if isinstance(i, dict)]
    cards += [card(k, [("אביזר", t.get("prop")), ("למה", t.get("hook"))], None, "מקורפוס Sketchy",
                   json.dumps(t, ensure_ascii=False))
              for k, t in lex["tokens"].items() if t.get("source") == "sketchy-corpus"]
    sections.append(("props", "יונים ואביזרים קבועים", f"{len(cards)} אביזרים שחוזרים בכל סצנה", cards))

    # 5. chapter venues
    cards = [card(f"{v.get('index')}. {v.get('sign_text')}",
                  [("פרק", v.get("title_en")), ("משחק המילים", v.get("pun_explanation")),
                   ("המקום", v.get("place")), ("מה כולם עושים", v.get("activity")),
                   ("מבנה מרכזי", v.get("anchor_structure"))],
                  None, DERIVATION_HE.get(v.get("derivation"), v.get("derivation")),
                  json.dumps(v, ensure_ascii=False))
             for v in venues]
    sections.append(("venues", "מקום לכל פרק", f"{len(cards)} שלטים ומשחקי מילים, אחד לכל פרק", cards))

    # 6. rules and retired
    rules = [f"<p class='rule' dir='auto'>{e(lex.get('usage'))}</p>"]
    rules += [card(k, [("הוחלף ב", r.get("replaced_by")), ("למה", r.get("why"))], None, "פרש")
              for k, r in lex.get("retired", {}).items()]
    sections.append(("rules", "כללים וסמלים שפרשו", "מה למדנו על מה עובד ומה לא", rules))

    nav = "".join(f"<a href='#{sid}'>{e(t)}</a>" for sid, t, _s, _c in sections)
    body = ""
    for sid, title, sub, items in sections:
        inner = "".join(items)
        if sid != "symbols" and sid != "rules":
            inner = f"<div class='grid'>{inner}</div>"
        body += f"<section id='{sid}'><h2>{e(title)}</h2><p class='sub'>{e(sub)}</p>{inner}</section>"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(TEMPLATE.replace("__NAV__", nav).replace("__BODY__", body), encoding="utf-8")
    print(f"{OUT} · {OUT.stat().st_size // 1024} KB")
    return 0


TEMPLATE = """<title>ספר הסמלים</title>
<style>
/* A sketchbook for inventing scenes: warm paper, pencil ink, one marker colour. */
:root{--paper:#f6f4ef;--sheet:#fffdf8;--ink:#23262b;--muted:#646a72;--rule:#e1ddd3;
  --mark:#1f6f8b;--mark-bg:#e2eff4;--f-d:"Secular One","Assistant",sans-serif;--f-b:"Assistant","Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#15171a;--sheet:#1c1f23;--ink:#e6e8ea;
  --muted:#9aa1a9;--rule:#2c3136;--mark:#6cc3e0;--mark-bg:#173039;color-scheme:dark}}
:root[data-theme="dark"]{--paper:#15171a;--sheet:#1c1f23;--ink:#e6e8ea;--muted:#9aa1a9;--rule:#2c3136;
  --mark:#6cc3e0;--mark-bg:#173039;color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font:15px/1.6 var(--f-b);margin:0}
.wrap{max-width:1180px;margin:0 auto;padding-inline:16px;padding-block:24px 60px}
h1{font:400 clamp(28px,4vw,40px)/1.1 var(--f-d);margin:0 0 6px}
.lede{color:var(--muted);max-width:70ch;margin:0 0 16px}
.top{position:sticky;top:env(safe-area-inset-top,0px);background:var(--paper);z-index:2;padding-block:10px;
  border-bottom:1px solid var(--rule);display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.top a{color:var(--mark);text-decoration:none;font-size:14px;padding:3px 10px;border-radius:999px;background:var(--mark-bg)}
.top input{margin-inline-start:auto;font:inherit;padding:6px 12px;border:1px solid var(--rule);border-radius:8px;
  background:var(--sheet);color:var(--ink);min-width:220px}
section{margin-top:34px}
h2{font:400 26px/1.2 var(--f-d);margin:0}
.sub{color:var(--muted);margin:2px 0 14px}
h3.cat{font-size:16px;margin:22px 0 8px;color:var(--mark)}
h3.cat small{color:var(--muted);font-weight:400}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px}
.card{background:var(--sheet);border:1px solid var(--rule);border-radius:8px;padding:12px 14px;min-width:0}
.card h4{margin:0 0 6px;font-size:15px;overflow-wrap:anywhere}
.tag{font-size:11px;font-weight:400;color:var(--muted);background:var(--paper);border-radius:4px;padding:1px 6px;margin-inline-start:6px}
.row{display:grid;grid-template-columns:5.5em 1fr;gap:6px;font-size:13.5px;margin-top:3px}
.row .k{color:var(--muted)}
.row span{overflow-wrap:anywhere}
.he{margin:8px 0 0;padding-top:8px;border-top:1px dashed var(--rule);font-size:13.5px}
.rule{background:var(--sheet);border:1px solid var(--rule);border-radius:8px;padding:12px 14px;white-space:pre-wrap;max-width:80ch}
.hide{display:none}
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Assistant:wght@400;600&family=Secular+One&display=swap">
<div class="wrap" dir="rtl" lang="he">
<h1>ספר הסמלים</h1>
<p class="lede">כל הסמלים, משחקי המילים והסצנות שהלוחות משתמשים בהם עד עכשיו, במקום אחד. כלל אחד חשוב לחשיבה על סצנות חדשות:
סמל חוזר מקבל משמעות אחת בכל הקורס, אבל תפאורה (חלון, כיסא, עץ) חופשית לשימוש בכל סצנה.</p>
<nav class="top">__NAV__<input id="q" type="search" placeholder="חיפוש: סרוטונין, ליתיום, D2…" aria-label="חיפוש"></nav>
__BODY__
</div>
<script>
const q=document.getElementById('q');
q.addEventListener('input',()=>{const t=q.value.trim().toLowerCase();
document.querySelectorAll('.card').forEach(c=>c.classList.toggle('hide',t&&!c.dataset.s.includes(t)));});
</script>
"""

if __name__ == "__main__":
    raise SystemExit(main())
