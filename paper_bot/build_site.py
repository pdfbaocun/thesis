"""data/papers.json から、まとめサイト「今日の論文」を site/ に生成する。

外部ライブラリを使わない静的サイト(GitHub Pages で公開)。
  site/index.html          すべての論文(新しい順)
  site/cornea.html         角膜のみ
  site/vitreous.html       硝子体のみ
  site/retina.html         網膜のみ
  site/papers/<pmid>.html  論文ごとの詳細ページ
"""

from __future__ import annotations

import json
import os
import re
import shutil
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS_FILE = ROOT / "data" / "papers.json"
OUT = ROOT / "site"
SITE_NAME = "今日の論文"
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

TOPICS = {"cornea": "角膜", "vitreous": "硝子体", "retina": "網膜"}
TABS = [
    ("index.html", "すべて", None),
    ("cornea.html", "角膜", "cornea"),
    ("vitreous.html", "硝子体", "vitreous"),
    ("retina.html", "網膜", "retina"),
]

CSS = """
:root {
  --bg: #f6f5f1; --card: #ffffff; --text: #1f2328; --muted: #656d76; --line: #e3e1da;
  --accent: #0f6e6e; --cornea: #0f6e6e; --cornea-bg: #e2f1ef; --vitreous: #5b4bb7; --vitreous-bg: #ebe8f8;
  --retina: #b4561b; --retina-bg: #fbeadf;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #15171a; --card: #1e2125; --text: #e8e6e1; --muted: #9aa1a9; --line: #2e3237;
    --accent: #5cc3bb; --cornea: #5cc3bb; --cornea-bg: #1d3533; --vitreous: #a99cf0; --vitreous-bg: #2b2743;
    --retina: #f0a06a; --retina-bg: #3a2618;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN",
    "Noto Sans JP", "Yu Gothic", Meiryo, sans-serif;
  font-size: 16px; line-height: 1.75; -webkit-text-size-adjust: 100%;
}
a { color: var(--accent); }
.wrap { max-width: 720px; margin: 0 auto; padding: 0 16px 48px; }
header.site { padding: 20px 0 8px; }
header.site a { color: inherit; text-decoration: none; }
header.site h1 { margin: 0; font-size: 1.35rem; letter-spacing: .04em; }
header.site p { margin: 2px 0 0; color: var(--muted); font-size: .85rem; }
nav.tabs {
  position: sticky; top: 0; z-index: 1; display: flex; gap: 8px;
  padding: 10px 0; background: var(--bg); border-bottom: 1px solid var(--line);
}
nav.tabs a {
  flex: 1; text-align: center; padding: 8px 0; border-radius: 999px; text-decoration: none;
  color: var(--muted); border: 1px solid var(--line); font-size: .9rem; font-weight: 600;
}
nav.tabs a[aria-current="page"] { background: var(--text); color: var(--bg); border-color: var(--text); }
.day { margin: 22px 0 8px; color: var(--muted); font-size: .8rem; font-weight: 600; letter-spacing: .05em; }
.card {
  display: block; background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 14px 16px; margin-bottom: 10px; color: inherit; text-decoration: none;
}
.card:active { transform: scale(.99); }
.card h2 { margin: 6px 0 4px; font-size: 1rem; line-height: 1.55; }
.card .meta { color: var(--muted); font-size: .8rem; }
.badge {
  display: inline-block; font-size: .72rem; font-weight: 700; padding: 1px 10px; border-radius: 999px;
}
.badge.cornea { color: var(--cornea); background: var(--cornea-bg); }
.badge.vitreous { color: var(--vitreous); background: var(--vitreous-bg); }
.badge.retina { color: var(--retina); background: var(--retina-bg); }
.empty { color: var(--muted); text-align: center; padding: 48px 0; }
article h1 { font-size: 1.25rem; line-height: 1.55; margin: 12px 0 6px; }
article .orig { color: var(--muted); font-size: .85rem; margin: 0 0 4px; }
article .meta { color: var(--muted); font-size: .8rem; margin-bottom: 18px; }
article section { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 12px 16px; margin-bottom: 10px; }
article section h2 { font-size: .85rem; color: var(--accent); margin: 0 0 4px; letter-spacing: .04em; }
article section p { margin: 0; }
article section.note { border-left: 4px solid var(--accent); }
.links { display: flex; gap: 8px; flex-wrap: wrap; margin: 18px 0; }
.links a {
  flex: 1; min-width: 140px; text-align: center; padding: 10px; border-radius: 10px;
  border: 1px solid var(--line); background: var(--card); text-decoration: none; font-weight: 600;
}
.back { display: inline-block; margin-top: 16px; font-size: .9rem; }
footer { color: var(--muted); font-size: .75rem; margin-top: 32px; line-height: 1.6; }
"""


def parse_sections(summary: str) -> list[tuple[str, str]]:
    """「【見出し】本文」形式の要約を (見出し, 本文) のリストに分解する。"""
    text = summary.replace("**", "")
    parts = re.split(r"【([^】]+)】", text)
    sections = []
    for i in range(1, len(parts), 2):
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        sections.append((parts[i].strip(), body))
    if not sections and text.strip():
        sections.append(("要約", text.strip()))
    return sections


def title_ja(entry: dict) -> str:
    for heading, body in parse_sections(entry["summary"]):
        if heading.startswith("タイトル和訳") and body:
            return body.splitlines()[0]
    return entry["title"]


def authors_text(entry: dict) -> str:
    authors = entry.get("authors", [])
    text = ", ".join(authors[:3])
    return text + (" et al." if len(authors) > 3 else "")


def page(title: str, body: str, *, prefix: str, description: str = "", path: str = "") -> str:
    og = ""
    if SITE_URL:
        og = (
            f'<meta property="og:title" content="{escape(title)}">\n'
            f'<meta property="og:description" content="{escape(description)}">\n'
            f'<meta property="og:type" content="website">\n'
            f'<meta property="og:site_name" content="{SITE_NAME}">\n'
            f'<meta property="og:url" content="{SITE_URL}/{path}">\n'
        )
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}">
{og}<link rel="stylesheet" href="{prefix}style.css">
</head>
<body>
<div class="wrap">
<header class="site"><a href="{prefix}index.html"><h1>{SITE_NAME}</h1>
<p>角膜・硝子体・網膜の新着論文を毎日1本ずつ、日本語で要約</p></a></header>
{body}
<footer>要約はGoogle Geminiが論文の抄録から自動生成したものです。誤りを含む可能性があるため、診療や研究に用いる際は必ず原著をご確認ください。</footer>
</div>
</body>
</html>
"""


def render_list(entries: list[dict], current: str) -> str:
    current_attr = ' aria-current="page"'
    tabs = "".join(
        f'<a href="{href}"{current_attr if href == current else ""}>{label}</a>'
        for href, label, _ in TABS
    )
    items, last_day = [], None
    for e in entries:
        if e["sent_on"] != last_day:
            items.append(f'<div class="day">{escape(e["sent_on"])}</div>')
            last_day = e["sent_on"]
        items.append(
            f'<a class="card" href="papers/{e["pmid"]}.html">'
            f'<span class="badge {e["topic"]}">{TOPICS[e["topic"]]}</span>'
            f"<h2>{escape(title_ja(e))}</h2>"
            f'<div class="meta">{escape(e["journal"])} ({escape(e["year"])})</div></a>'
        )
    if not items:
        items.append('<p class="empty">まだ論文がありません</p>')
    return f'<nav class="tabs">{tabs}</nav>\n<main>{"".join(items)}</main>'


def render_detail(e: dict) -> str:
    sections = []
    for heading, body in parse_sections(e["summary"]):
        if heading.startswith("タイトル和訳"):
            continue
        cls = ' class="note"' if heading.startswith("ひとこと") else ""
        paragraphs = "<br>".join(escape(line) for line in body.splitlines() if line.strip())
        sections.append(f"<section{cls}><h2>{escape(heading)}</h2><p>{paragraphs}</p></section>")
    links = f'<a href="{escape(e["url"])}">PubMedで見る</a>'
    if e.get("doi"):
        links += f'<a href="https://doi.org/{escape(e["doi"])}">原著を読む(DOI)</a>'
    return f"""<article>
<span class="badge {e["topic"]}">{TOPICS[e["topic"]]}</span>
<h1>{escape(title_ja(e))}</h1>
<p class="orig">{escape(e["title"])}</p>
<div class="meta">{escape(authors_text(e))}<br>{escape(e["journal"])} ({escape(e["year"])}) ・ {escape(e["sent_on"])} 配信</div>
{"".join(sections)}
<div class="links">{links}</div>
<a class="back" href="../index.html">← 一覧に戻る</a>
</article>"""


def build() -> None:
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))["papers"] if PAPERS_FILE.exists() else []
    # 要約がまだない論文(作り直し前など)は載せない。新しい日付が上、同じ日は角膜→硝子体→網膜
    order = {key: i for i, key in enumerate(TOPICS)}
    # hidden: 的外れだった論文などをサイトから外す(送信済みの記録は残す)
    entries = [e for e in papers if e.get("summary") and not e.get("hidden")]
    entries.sort(key=lambda e: (e["sent_on"], -order.get(e["topic"], 99)), reverse=True)

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "papers").mkdir(parents=True)
    (OUT / "style.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    description = "角膜・硝子体・網膜の新着論文を毎日1本ずつ、日本語で要約"
    for href, label, topic in TABS:
        subset = [e for e in entries if topic is None or e["topic"] == topic]
        title = SITE_NAME if topic is None else f"{label}の論文 | {SITE_NAME}"
        html = page(title, render_list(subset, href), prefix="", description=description, path=href)
        (OUT / href).write_text(html, encoding="utf-8")

    for e in entries:
        sections = dict(parse_sections(e["summary"]))
        desc = next((v for k, v in sections.items() if k.startswith("結論")), "")[:120]
        html = page(
            f"{title_ja(e)} | {SITE_NAME}", render_detail(e), prefix="../",
            description=desc, path=f"papers/{e['pmid']}.html",
        )
        (OUT / "papers" / f"{e['pmid']}.html").write_text(html, encoding="utf-8")
    print(f"{len(entries)}件の論文でサイトを生成しました: {OUT}")


if __name__ == "__main__":
    build()
