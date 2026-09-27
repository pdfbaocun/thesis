# 角膜・硝子体 論文デイリー通知 (LINE)

毎朝 6:43 (JST) ごろに、PubMed から **角膜** と **硝子体** の論文を1本ずつ選び、
Gemini で日本語要約して LINE に送ります。

- 送信済みの論文は `data/sent_pmids.json` に記録され、以後は送られません
- 1本の論文が角膜・硝子体の両方に該当しても、同じ日に二重には送りません
- 直近30日の新しい論文を優先し、候補が尽きたら 90日 → 1年 → 5年 と範囲を広げます
- 症例報告・レター・コメント等は除外、抄録のある英語論文のみ

## 初期設定

### 1. LINE Messaging API
(LINE Notify は2025年3月で終了したため Messaging API を使います)

1. [LINE Developers](https://developers.line.biz/) で プロバイダー → **Messaging API チャネル** を作成
2. チャネルの「Messaging API設定」で **チャネルアクセストークン(長期)** を発行 → `LINE_CHANNEL_ACCESS_TOKEN`
3. 「チャネル基本設定」の **あなたのユーザーID** (`U` で始まる文字列) → `LINE_USER_ID`
4. 「Messaging API設定」のQRコードから、そのBotを自分のLINEで友だち追加

※ 無料プランは月200通まで。本ツールは1日1通(2論文をまとめて送信)なので月約30通です。

### 2. Gemini API キー
[Google AI Studio](https://aistudio.google.com/apikey) でAPIキーを発行 → `GEMINI_API_KEY`

### 3. GitHub Secrets に登録
リポジトリの Settings → Secrets and variables → Actions → New repository secret

| Name | 必須 | 内容 |
| --- | --- | --- |
| `LINE_CHANNEL_ACCESS_TOKEN` | ✅ | LINEチャネルアクセストークン |
| `LINE_USER_ID` | ✅ | 送信先(自分)のユーザーID |
| `GEMINI_API_KEY` | ✅ | Gemini APIキー |
| `NCBI_API_KEY` | 任意 | PubMedのレート制限緩和用 |

### 4. 動作確認
Actions タブ → **Daily cornea & vitreous papers** → Run workflow。
`dry_run` にチェックするとLINEに送らずログに出力するだけになります。

## カスタマイズ
- 送信時刻: `.github/workflows/daily-papers.yml` の `cron` (UTC表記。JST = UTC+9)
- 検索条件: `paper_bot/daily_papers.py` の `TOPICS`
- 要約の形式: 同ファイルの `SUMMARY_PROMPT`
- Geminiのモデル: 環境変数 `GEMINI_MODEL` (既定 `gemini-2.5-flash`)

## ローカル実行
```bash
pip install -r requirements.txt
export GEMINI_API_KEY=... LINE_CHANNEL_ACCESS_TOKEN=... LINE_USER_ID=...
DRY_RUN=1 python paper_bot/daily_papers.py   # LINEに送らず表示のみ
```
