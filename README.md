# Morning News Bot v3.2

毎朝7時（JST）に、**海外6本＋国内7本＝13本のニュース**を Gmail で配信するボット。
朝刊本体は読み物として、添付MP3（7〜10分）は通勤・ランニング中の聴取用。

---

## v3 から v3.2 への変更点

| 機能 | v3 | v3.2 |
|---|---|---|
| 海外ニュース朝刊 | 6本（メイン5本＋スポット1本） | 6本（変更なし） |
| **国内ニュース朝刊** | ❌ なし | **7本（深掘り3本＋見出し4本）** ← NEW |
| **重複排除ロジック** | ❌ なし | **海外で扱った話題を国内から除外** ← NEW |
| ラジオ台本 | 海外3〜4本のみ、3〜5分 | **海外6本＋国内深掘り3本=9本、7〜10分** ← 拡張 |
| HTML メール | 海外朝刊1セクション | **2セクション統合（SECTION 1: 海外 / SECTION 2: 国内）** ← NEW |

---

## コスト見積もり

| 項目 | v3 月コスト | v3.2 月コスト |
|---|---|---|
| 朝刊本体（Gemini 2.5 Pro × 2回／日） | $1.5〜$4.5 | **$3〜$9** |
| ラジオ台本（Claude Sonnet 4.6） | $0.5〜$1.5 | **$1〜$3** |
| 音声生成（OpenAI TTS） | $1〜$2 | **$2〜$4** |
| **合計** | **$3〜$8** | **$6〜$16（約900〜2,400円/月）** |

日経電子版（月4,277円）の **約半額〜70%程度** で、海外＋国内合計13本+音声付き。

---

## Claude Code経由のインストール手順（v3 から v3.2 への移行）

### 1. このZIPをローカルに解凍

```bash
unzip morning-news-bot-v3.2.zip
```

### 2. Claude Code でリポジトリのクローンディレクトリに移動

ローカルにクローン済みのリポジトリディレクトリで Claude Code を起動：

```bash
cd ~/path/to/morning-news-bot
claude
```

### 3. Claude Code に以下を依頼

ZIPの中身を統合してmainにpushしてもらう例：

```
~/Downloads/morning-news-bot-v3.2/ にあるv3.2のファイルを、このリポジトリに統合してください。

具体的には：
1. morning_news.py を v3.2 の内容で完全置換
2. requirements.txt を v3.2 の内容で完全置換
3. .github/workflows/morning-news.yml を v3.2 の内容で完全置換
4. README.md は更新せず、現状維持（または v3.2 README をコピー）
5. SKILL.md はそのまま残す
6. 変更内容を確認して、適切なコミットメッセージで commit してください
7. main ブランチにpushしてください

なお、GEMINI_API_KEY/ANTHROPIC_API_KEY/OPENAI_API_KEY/GMAIL_ADDRESS/GMAIL_APP_PASSWORD/RECIPIENT_EMAIL のSecretsは既に登録済みです。
```

Claude Codeが差分を表示し、確認してからcommit & push。

### 4. 動作確認

GitHub Actions の **Run workflow** で手動実行 → 数分待つ → メール受信を確認。

ログで以下が出ていれば成功：
```
[INFO] [Step 1/4] Generating global morning news...
[INFO] Generated XXXX chars of global news markdown
[INFO] [Step 2/4] Generating domestic morning news...
[INFO] Excluding X topics from global edition       ← 重複排除が効いた証拠
[INFO] Generated XXXX chars of domestic news markdown
[INFO] [Step 3/4] Generating extended radio script...
[INFO] Generated XXXX chars of script               ← 3,500〜5,000字程度のはず
[INFO] [Step 4/4] Generating audio with OpenAI TTS...
[INFO] Audio saved: morning_news_YYYYMMDD.mp3 (X.XX MB)  ← 6〜10MB
[INFO] Email sent to ***
```

---

## 新しい Secrets は必要？

**いいえ、追加なし**。v3.0 で登録した6つのSecrets（GEMINI、ANTHROPIC、OPENAI、GMAIL_ADDRESS、GMAIL_APP_PASSWORD、RECIPIENT_EMAIL）がそのまま使えます。

---

## トラブルシューティング

### ❌ 国内朝刊だけ生成失敗、海外は届く

ログで `[ERROR] Failed to generate domestic news:` を確認。
原因の多くは Gemini API の一時的なエラー。翌日の自動実行で復活します。
頻発する場合は `morning_news.py` の `temperature` を 0.7 → 0.5 に下げると安定します。

### ❌ 海外と国内で同じ話題が重複している

`extract_global_headlines` 関数の正規表現が見出しを正しく抽出できていない可能性。
ログの `[INFO] Excluding X topics from global edition` で X が **0** または **少なすぎる** 場合は、
海外朝刊のフロントページのフォーマットが想定と違っている。
Gemini に「フロントページの ① ② ③ 形式を厳密に守る」指示を追加すれば解決。

### ❌ ラジオ台本が長すぎる/短すぎる

`SCRIPT_SYSTEM_PROMPT` の「文字数は 3,500〜5,000字程度」を
具体的な数字に変えてください（例：「4,000〜4,500字程度」）。

### ⚠️ メールが20MBを超えてGmail制限に引っかかる

7〜10分の音声は通常6〜10MB程度なので問題ないはず。
万一超える場合は `TTS_MODEL = "tts-1"` に下げると音声容量が約半分に。

---

## カスタマイズ余地

すべて `morning_news.py` の上部定数または各 SYSTEM_PROMPT で調整可能：

| 調整したいこと | 場所 | 対処 |
|---|---|---|
| 国内ニュースの本数を変える（例: 9本に） | `DOMESTIC_NEWS_SYSTEM_PROMPT` | 「深掘り3本＋見出し4本」を「深掘り4本＋見出し5本」に |
| 国内のテーマ範囲を変える | `DOMESTIC_NEWS_SYSTEM_PROMPT` | 「優先度高/中」のリストを編集 |
| ラジオ台本で取り上げる本数を変える | `SCRIPT_SYSTEM_PROMPT` | 「海外6本」「国内深掘り3本」を変更 |
| 音声時間を変える | `SCRIPT_SYSTEM_PROMPT` | 「7〜10分」「3,500〜5,000字」を変更 |
| コスト下げる（さらに） | 上部定数 | `NEWS_MODEL = "gemini-2.5-flash"` |

---

## ファイル構成

```
morning-news-bot/
├── morning_news.py              ← v3.2 で更新
├── requirements.txt             ← 変更なし（v3と同じ）
├── README.md                    ← v3.2 README（このファイル）
├── SKILL.md                     ← 変更なし
└── .github/
    └── workflows/
        └── morning-news.yml     ← timeout を 15分→20分 に拡張
```
