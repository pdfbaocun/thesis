"""角膜・硝子体の論文を毎日1本ずつ選び、日本語要約をLINEに送信する。

- 論文検索: PubMed (NCBI E-utilities)
- 要約: Google Gemini API
- 送信: LINE Messaging API (push message)
- 保存・重複防止: 選んだ論文と要約を data/papers.json に記録(まとめサイトの元データ)

使い方:
  python daily_papers.py prepare  論文を選んで要約し、data/papers.json に追加する
  python daily_papers.py send     まだLINEに送っていない論文を送る
"""

from __future__ import annotations

import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
from google import genai
from google.genai import errors as genai_errors

ROOT = Path(__file__).resolve().parent.parent
PAPERS_FILE = ROOT / "data" / "papers.json"
SITE_URL = os.environ.get("SITE_URL", "")

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

# 症例報告・コメント・訂正などは除外し、抄録のある英語論文に限定する
COMMON_FILTER = (
    " AND hasabstract AND english[la]"
    " NOT (case reports[pt] OR comment[pt] OR erratum[pt] OR editorial[pt]"
    " OR letter[pt] OR retracted publication[pt])"
)

TOPICS = [
    {
        "key": "cornea",
        "label": "角膜",
        "emoji": "👁",
        "query": (
            "(cornea[mh] OR corneal diseases[mh] OR cornea[tiab] OR corneal[tiab]"
            " OR keratoconus[tiab] OR keratitis[tiab] OR keratoplasty[tiab])"
        ),
    },
    {
        "key": "vitreous",
        "label": "硝子体",
        "emoji": "🔬",
        "query": (
            "(vitreous body[mh] OR vitrectomy[mh] OR vitreous[tiab]"
            " OR vitrectomy[tiab] OR vitreoretinal[tiab])"
        ),
    },
]

# 何日前までの論文を候補にするか。候補が尽きたら自動で範囲を広げる
SEARCH_WINDOWS_DAYS = [30, 90, 365, 1825]


def ncbi_params(extra: dict) -> dict:
    params = {"db": "pubmed", "tool": "thesis-paper-bot", **extra}
    if os.environ.get("NCBI_API_KEY"):
        params["api_key"] = os.environ["NCBI_API_KEY"]
    if os.environ.get("NCBI_EMAIL"):
        params["email"] = os.environ["NCBI_EMAIL"]
    return params


def ncbi_get(endpoint: str, params: dict) -> requests.Response:
    for attempt in range(4):
        resp = requests.get(f"{EUTILS}/{endpoint}", params=ncbi_params(params), timeout=60)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2 ** (attempt + 1))
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def search_pmids(query: str, days: int, retmax: int = 200) -> list[str]:
    resp = ncbi_get(
        "esearch.fcgi",
        {
            "term": query + COMMON_FILTER,
            "reldate": days,
            "datetype": "edat",
            "sort": "pub_date",
            "retmax": retmax,
            "retmode": "json",
        },
    )
    return resp.json()["esearchresult"]["idlist"]


def _text(elem: ET.Element | None) -> str:
    return "".join(elem.itertext()).strip() if elem is not None else ""


def parse_article(article: ET.Element) -> dict:
    citation = article.find("MedlineCitation")
    art = citation.find("Article")
    pmid = _text(citation.find("PMID"))

    abstract_parts = []
    for node in art.findall("Abstract/AbstractText"):
        label = node.get("Label")
        body = _text(node)
        abstract_parts.append(f"{label}: {body}" if label else body)

    authors = []
    for au in art.findall("AuthorList/Author"):
        last, initials = _text(au.find("LastName")), _text(au.find("Initials"))
        name = f"{last} {initials}".strip() or _text(au.find("CollectiveName"))
        if name:
            authors.append(name)

    pub_date = art.find("Journal/JournalIssue/PubDate")
    year = _text(pub_date.find("Year")) if pub_date is not None else ""
    if not year and pub_date is not None:
        year = _text(pub_date.find("MedlineDate"))[:4]

    doi = ""
    for aid in article.findall("PubmedData/ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = _text(aid)

    return {
        "pmid": pmid,
        "title": _text(art.find("ArticleTitle")),
        "journal": _text(art.find("Journal/Title")),
        "year": year,
        "authors": authors,
        "abstract": "\n".join(abstract_parts),
        "doi": doi,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def fetch_articles(pmids: list[str]) -> list[dict]:
    if not pmids:
        return []
    resp = ncbi_get("efetch.fcgi", {"id": ",".join(pmids), "retmode": "xml"})
    root = ET.fromstring(resp.content)
    by_pmid = {}
    for node in root.findall("PubmedArticle"):
        paper = parse_article(node)
        by_pmid[paper["pmid"]] = paper
    # esearch の並び順(新しい順)を保つ
    return [by_pmid[p] for p in pmids if p in by_pmid]


def pick_paper(topic: dict, exclude: set[str]) -> dict | None:
    for days in SEARCH_WINDOWS_DAYS:
        candidates = [p for p in search_pmids(topic["query"], days) if p not in exclude]
        for paper in fetch_articles(candidates[:20]):
            if paper["abstract"] and paper["title"]:
                return paper
    return None


SUMMARY_PROMPT = """あなたは眼科領域に詳しい医学論文の解説者です。
以下の論文を、眼科の大学院生・臨床医向けに日本語で要約してください。

出力形式(見出しはこのまま、Markdown記号は使わない。全体で600字程度):
【タイトル和訳】
【背景・目的】
【方法】
【主な結果】(数値があれば含める)
【結論・臨床的意義】
【ひとこと】(この論文を読む価値や注意点を1文で)

抄録に書かれていない内容は推測で補わないでください。

---
Title: {title}
Journal: {journal} ({year})
Abstract:
{abstract}
"""


def summarize(paper: dict, client: genai.Client) -> str:
    """要約に失敗したら例外を投げる(LINEには送らず、送信済みにも記録しない)。"""
    last_error: Exception | None = None
    # 混雑(503)や分単位の上限(429)は待って再試行。無料枠は1日20回なので回数は控えめにする
    waits = [30, 60, 120]
    for attempt in range(len(waits) + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL, contents=SUMMARY_PROMPT.format(**paper)
            )
            if response.text:
                return response.text.strip()
            last_error = RuntimeError("Geminiの応答が空でした")
        except genai_errors.ClientError as e:
            # 4xx(モデル名・APIキーの誤りなど)や1日の無料枠切れは再試行しても直らない
            if e.code != 429 or "PerDay" in str(e):
                raise
            last_error = e
        except (genai_errors.ServerError, requests.RequestException) as e:
            last_error = e
        print(f"Gemini要約エラー (試行{attempt + 1}): {last_error}", file=sys.stderr)
        if attempt < len(waits):
            time.sleep(waits[attempt])
    raise RuntimeError(f"Geminiでの要約に失敗しました: {last_error}")


def topic_by_key(key: str) -> dict:
    return next(t for t in TOPICS if t["key"] == key)


def format_message(entry: dict) -> str:
    topic = topic_by_key(entry["topic"])
    authors = ", ".join(entry["authors"][:3])
    if len(entry["authors"]) > 3:
        authors += " et al."
    lines = [
        f"{topic['emoji']} 今日の{topic['label']}論文 ({entry['sent_on']})",
        "",
        entry["title"],
        f"{authors}",
        f"{entry['journal']} ({entry['year']})",
        "",
        entry["summary"],
        "",
        f"PubMed: {entry['url']}",
    ]
    if entry["doi"]:
        lines.append(f"DOI: https://doi.org/{entry['doi']}")
    if SITE_URL:
        lines.append(f"サイトで見る: {SITE_URL.rstrip('/')}/papers/{entry['pmid']}.html")
    text = "\n".join(lines)
    return text[:4900]  # LINEのテキストメッセージ上限は5000文字


def send_line(messages: list[str]) -> None:
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    user_id = os.environ["LINE_USER_ID"]
    resp = requests.post(
        LINE_PUSH_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": user_id, "messages": [{"type": "text", "text": m} for m in messages]},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"LINE送信に失敗しました: {resp.status_code} {resp.text}")


def load_papers() -> list[dict]:
    if PAPERS_FILE.exists():
        return json.loads(PAPERS_FILE.read_text(encoding="utf-8"))["papers"]
    return []


def save_papers(papers: list[dict]) -> None:
    PAPERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAPERS_FILE.write_text(
        json.dumps({"papers": papers}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def make_entry(paper: dict, topic_key: str, summary: str, sent_on: str) -> dict:
    # 抄録の原文は著作権の都合で保存・公開しない
    return {
        "pmid": paper["pmid"],
        "topic": topic_key,
        "sent_on": sent_on,
        "title": paper["title"],
        "authors": paper["authors"],
        "journal": paper["journal"],
        "year": paper["year"],
        "doi": paper["doi"],
        "url": paper["url"],
        "summary": summary,
        "notified": False,
    }


def backfill(papers: list[dict], client: genai.Client) -> None:
    """要約が保存されていない論文(サイト公開前に送ったもの)の要約を作り直す。"""
    missing = [e for e in papers if not e.get("summary")]
    if not missing:
        return
    fetched = {p["pmid"]: p for p in fetch_articles([e["pmid"] for e in missing])}
    for entry in missing:
        paper = fetched.get(entry["pmid"])
        if paper is None:
            print(f"PMID {entry['pmid']} をPubMedから取得できませんでした", file=sys.stderr)
            continue
        new = make_entry(paper, entry["topic"], summarize(paper, client), entry["sent_on"])
        new["notified"] = entry.get("notified", True)
        entry.update(new)
        print(f"要約を作り直しました: {entry['pmid']}")


def today_jst() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")


def prepare() -> int:
    dry_run = os.environ.get("DRY_RUN") == "1"
    papers = load_papers()
    today = today_jst()
    client = genai.Client()  # 環境変数 GEMINI_API_KEY を使用

    backfill(papers, client)
    if os.environ.get("BACKFILL_ONLY") == "1":
        save_papers(papers)
        return 0
    # 予備の定時実行では、その日の分がすでに送信済みなら何もしない
    if os.environ.get("SKIP_IF_SENT_TODAY") == "1" and any(
        e["sent_on"] == today and e.get("notified") for e in papers
    ):
        print(f"{today} の論文は送信済みのため終了します")
        save_papers(papers)
        return 0

    exclude = {e["pmid"] for e in papers}
    new_entries = []
    for topic in TOPICS:
        paper = pick_paper(topic, exclude)
        if paper is None:
            print(f"[{topic['key']}] 未送信の論文が見つかりませんでした", file=sys.stderr)
            continue
        exclude.add(paper["pmid"])  # 角膜・硝子体の両方に該当する論文の二重送信を防ぐ
        new_entries.append(make_entry(paper, topic["key"], summarize(paper, client), today))

    if not new_entries:
        print("新しい論文がありません", file=sys.stderr)
        return 1
    if dry_run:
        print("\n\n==========\n\n".join(format_message(e) for e in new_entries))
        return 0

    papers.extend(new_entries)
    save_papers(papers)
    print(f"{len(new_entries)}件を追加しました: {[e['pmid'] for e in new_entries]}")
    return 0


def send() -> int:
    papers = load_papers()
    pending = [e for e in papers if not e.get("notified")]
    if not pending:
        print("LINEに送る論文はありません")
        return 0
    send_line([format_message(e) for e in pending])
    for entry in pending:
        entry["notified"] = True
    save_papers(papers)
    print(f"{len(pending)}件送信しました: {[e['pmid'] for e in pending]}")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare":
        sys.exit(prepare())
    if command == "send":
        sys.exit(send())
    sys.exit("使い方: python daily_papers.py [prepare|send]")
