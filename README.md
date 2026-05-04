# Morning News Bot v3（Gemini版）

毎朝7時（JST）に、海外の主要紙から「朝刊」を自動生成して **HTMLメール + MP3音声** を Gmail で配信するボット。

**v3 では朝刊生成を Gemini 2.5 Pro に変更し、コストを大幅削減**。

---

## v2 から v3 への変更点

| 機能 | v2 | v3 |
|---|---|---|
| 朝刊本体生成 | Anthropic Claude Opus 4.7 | **Google Gemini 2.5 Pro** ← 変更 |
| 朝刊本体の検索 | Anthropic web_search（Bing系） | **Google Search Grounding** ← 変更 |
| ラジオ台本生成 | Anthropic Claude Sonnet 4.6 | Anthropic Claude Sonnet 4.6（変更なし） |
| MP3生成 | OpenAI TTS | OpenAI TTS（変更なし） |
| Gmail配信 | Gmail SMTP | Gmail SMTP（変更なし） |

---

## コスト削減効果

| 項目 | v2 月コスト | v3 月コスト |
|---|---|---|
| 朝刊本体（Anthropic Opus 4.7 → Gemini 2.5 Pro） | $15〜$25 | **$1.5〜$4.5** |
| ラジオ台本（Anthropic Sonnet 4.6） | $0.5〜$1.5 | $0.5〜$1.5 |
| 音声生成（OpenAI TTS） | $1〜$2 | $1〜$2 |
| **合計** | **$17〜$28（約2,500〜4,000円）** | **$3〜$8（約450〜1,200円）** |

**約75%のコストカット**。

---

## アップグレード手順（v2 から移行する場合）

### 1. Google AI Studio で Gemini API キーを取得

1. https://aistudio.google.com にアクセス（Googleアカウントでログイン）
2. 左メニュー **「Get API key」** をクリック
3. **Create API key** → プロジェクト選択 → 作成
4. 表示される `AIzaSy...` 形式のキーを **コピーして保管**
5. **無料枠あり**（1日のリクエスト数制限内）。朝刊1回/日なら無料枠で収まる可能性が高い
6. 課金を有効化したい場合は Google Cloud Console で billing 設定

⚠️ Anthropic / OpenAI と異なり、**Gemini API は最初は無料枠のみで動作する**。クレジットカード登録は不要で始められる。

### 2. GitHub Secrets に `GEMINI_API_KEY` を追加

リポジトリ → **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `GEMINI_API_KEY` | `AIzaSy...`（Step 1で取得したキー） |

既存のSecretは変更不要：
- `ANTHROPIC_API_KEY`（Step 2のラジオ台本生成で引き続き必要）
- `OPENAI_API_KEY`
- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`
- `RECIPIENT_EMAIL`

### 3. リポジトリのファイル3つを置き換え

このZIPの中身で、既存リポジトリの以下3ファイルを **完全置換**：

- `morning_news.py`
- `requirements.txt`
- `.github/workflows/morning-news.yml`

GitHubのWeb UIで個別に編集する場合：

1. 各ファイルをリポジトリで開く
2. 鉛筆アイコン（Edit）をクリック
3. エディタ内を **全選択（Cmd+A / Ctrl+A）** → **削除**
4. 新しい内容を貼り付け
5. **Commit changes**

### 4. 動作確認

1. **Actions** タブ → **Morning News Briefing** → **Run workflow**
2. 数分待つ
3. ログを確認：
   ```
   env:
     GEMINI_API_KEY: ***       ← この行が出ていればYAML置換成功
     ANTHROPIC_API_KEY: ***
     OPENAI_API_KEY: ***
     ...

   [INFO] [Step 1/3] Generating morning news...
   [INFO] Model: gemini-2.5-pro (Gemini)        ← Gemini使用が確認できる
   [INFO] Generated XXXX chars of markdown
   [INFO] Grounding sources: XX pages referenced ← Google Search使用の証拠
   [INFO] [Step 2/3] Generating radio script...
   ...
   ```
4. メールが届くか確認

---

## さらにコストを下げたい場合

`morning_news.py` 上部の `NEWS_MODEL` を変更：

```python
NEWS_MODEL = "gemini-2.5-flash"   # Pro → Flash でさらに約1/5のコスト
```

**月コストの目安**：

| 設定 | 月コスト |
|---|---|
| `gemini-2.5-pro`（v3デフォルト） | $3〜$8 |
| `gemini-2.5-flash` | **$1〜$3（約150〜450円）** |

朝刊用途では Flash でも品質は十分です。Pro で数日試して、特に不満がなければ Flash に下げるのがおすすめ。

---

## トラブルシューティング

### ❌ `google.api_core.exceptions.PermissionDenied: 403`

Gemini APIキーが無効、またはGoogle AI Studioでキーが正しく発行されていない。

対処：
1. https://aistudio.google.com → Get API key で **新しいキーを作り直す**
2. GitHub Secrets で `GEMINI_API_KEY` を更新

### ❌ `ResourceExhausted: 429 Quota exceeded`

無料枠の1日のリクエスト上限に達した。

対処：
- 翌日まで待つ（無料枠リセット）
- Google Cloud Console で billing を有効化（有料化）し、上限を引き上げる

### ❌ メールは届くが、朝刊の品質が落ちた

Gemini 2.5 Pro は Claude Opus 4.7 より分析の深さがやや劣る場合がある。

対処：
1. `morning_news.py` の `NEWS_MODEL` を一時的に `gemini-2.5-pro` のままにし、`SCRIPT_MODEL` のレビューと比較
2. それでも不満なら、朝刊生成だけ Anthropic に戻す（`generate_morning_news` 関数を v2 のものに差し替え）

### ❌ Web検索ソースが少ない / 古い

Gemini の Google Search Grounding は Anthropic の web_search よりも、検索回数の制御が緩やか。

対処：
- ログの `Grounding sources: XX pages referenced` で参照件数を確認
- 少ない場合は `morning_news.py` の `temperature` を上げる（0.7 → 0.9）と多様な検索をするようになる

---

## なぜ Gemini が安いのか

- Gemini 2.5 Pro: Input $1.25/1M tokens, Output $5/1M tokens
- Claude Opus 4.7: Input $15/1M tokens, Output $75/1M tokens

**Inputで12倍、Outputで15倍** の差があります。朝刊1回でだいたい15K入力 + 8K出力なので、累積するとかなり大きな差に。

---

## 現状のアーキテクチャ

```
朝刊生成（情報収集と要約）       → Gemini 2.5 Pro（コスト効率）
ラジオ台本生成（軽量変換タスク）  → Claude Sonnet 4.6（日本語の自然さ）
音声生成（TTS）                 → OpenAI tts-1-hd（日本語TTS品質トップ）
```

各ステップで「最適なモデル」を使い分ける構成。
コストを最大限に削るなら、Step 2もGemini Flashに置き換え可能（さらに月$0.5ほど節約）。
