# Morning News Bot

毎朝7時（JST）に、海外の主要紙から「朝刊」を自動生成して Gmail で配信するボット。

GitHub Actions で動作するため、自分のPCを開いておく必要はありません。

---

## 構成

- **言語**: Python 3.11
- **AI**: Anthropic Claude API（claude-opus-4-7、web_search ツール付き）
- **配信**: Gmail SMTP（HTMLメール）
- **実行基盤**: GitHub Actions（無料枠で十分）

---

## 朝刊の中身

- 海外メイン5本（US 2-3本、欧州 1本、中国/アジア 1本）
- 国内未報道スポット 1本（日本のメディアでほぼ報道されていない海外ニュース）

各記事は「背景／ポイント／日本への示唆／批判的コメント」の4セクション。

---

## セットアップ手順

### 1. このリポジトリを GitHub にアップロード

1. GitHub で新規リポジトリを作成（プライベート推奨）
2. このZIPの中身をすべてリポジトリにアップロード（または `git push`）

ファイル構成：

```
morning-news-bot/
├── morning_news.py
├── requirements.txt
├── README.md
└── .github/
    └── workflows/
        └── morning-news.yml
```

### 2. 4つのシークレットを登録

GitHub のリポジトリ画面で：

**Settings → Secrets and variables → Actions → New repository secret**

以下4つを順番に登録します：

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...`（Anthropic Console で発行したAPIキー） |
| `GMAIL_ADDRESS` | 送信元のGmailアドレス（例：`yourname@gmail.com`） |
| `GMAIL_APP_PASSWORD` | Gmail のアプリパスワード16桁（スペース込みでも可） |
| `RECIPIENT_EMAIL` | 受信先のメールアドレス（送信元と同じでも可） |

#### Anthropic API キーの取得

1. https://console.anthropic.com にサインアップ
2. API Keys → Create Key
3. 表示される `sk-ant-api03-...` をコピー（**一度しか表示されない**ので注意）
4. 初回 $5 無料クレジット付き。なくなったら課金設定（Settings → Billing）

#### Gmail アプリパスワードの取得

1. https://myaccount.google.com → セキュリティ
2. **2段階認証プロセス** を有効化（既に有効ならスキップ）
3. 検索で「アプリ パスワード」 → 開く
4. アプリ名: `Morning News Bot` → 作成
5. 表示される 16桁のパスワードをコピー

⚠️ 通常のGoogleパスワードではなく、必ずアプリパスワード（16桁専用）を使ってください。

### 3. 動作確認（手動実行）

スケジュール実行を待たずに、まず手動で動かして確認します：

1. GitHub のリポジトリ画面 → **Actions** タブ
2. 左サイドバーで **Morning News Briefing** を選択
3. 右上の **Run workflow** ボタン → **Run workflow**
4. 数分待つ（だいたい3〜5分）
5. 緑のチェックマークが付けば成功 → メールBOXを確認

メールが届いていれば動作OKです。届かない場合は、Actions のログを開いてエラーを確認してください。

### 4. 毎朝7時の自動配信

シークレット4つが登録済みなら、何もしなくても毎日 JST 7:00 に自動配信されます。

GitHub Actionsの cron は UTC で動くため、`.github/workflows/morning-news.yml` の中で `cron: "0 22 * * *"`（UTC 22:00 = JST 7:00）と指定しています。

#### 配信時刻を変更する場合

`.github/workflows/morning-news.yml` の `cron` 行を編集：

| JST 配信時刻 | UTC 表記 | cron 文字列 |
|---|---|---|
| 6:00 | 21:00（前日） | `"0 21 * * *"` |
| 7:00 | 22:00（前日） | `"0 22 * * *"` |
| 8:00 | 23:00（前日） | `"0 23 * * *"` |
| 17:00（夕刊） | 8:00 | `"0 8 * * *"` |

※ GitHub Actions の cron は数分〜十数分の遅延があります（無料枠の都合）。

---

## コスト見積もり

| 項目 | 月額目安 |
|---|---|
| GitHub Actions | **無料**（無料枠 2,000分/月、朝刊1回あたり3〜5分使用） |
| Gmail SMTP | **無料** |
| Anthropic Claude API | **約 $5〜$15 / 月**（毎日朝刊1回、web_search 25回程度） |

合計：**月数百円〜2,000円程度**。

API使用量は Anthropic Console の Usage ページで監視できます。
コストが想定より高い場合は、`morning_news.py` の `max_uses` を `25 → 15` に下げると web_search 回数が抑えられます。

---

## トラブルシューティング

### ❌ メールが届かない

1. GitHub の Actions タブでワークフローのログを確認
2. ログに `[INFO] Email sent to ...` が出ていれば送信は成功 → Gmail の迷惑メールフォルダを確認
3. `Authentication failed` エラー → アプリパスワードを再発行して `GMAIL_APP_PASSWORD` を更新
4. `2-Step Verification not enabled` エラー → Google アカウントで2段階認証を有効化

### ❌ Claude API でエラー

1. `401 authentication_error` → `ANTHROPIC_API_KEY` の値を再確認
2. `400 invalid_request_error: model not found` → `morning_news.py` の `MODEL` を調整（例：`claude-sonnet-4-6` にすればコストも下がる）
3. `529 overloaded` → APIサーバーが一時的に混雑。翌朝以降は自動回復

### ⚠️ メール本文の見た目が崩れる

Gmail Web版とスマホ版で多少差があります。スマホ版で読むのが前提ならスマホで動作確認を。

### 📅 朝刊が一部の日に空になる

土日・祝日・市場休場日はニュースが少ないため、5本に満たない場合があります。SKILLの設計上、無理に埋めずに3〜4本で配信されます。

---

## カスタマイズ

### ニュース選定の方針を変えたい

`morning_news.py` の `SYSTEM_PROMPT` を編集してください。
例えば：

- AI/LLM に特化したい → 「メイン5本のうち4本以上はAI/LLM関連」を追加
- 中国比率を上げたい → 「中国 2本、US 2本、欧州 1本」に変更
- スポットを2本にしたい → スポット選定ルールの本数を変更

### モデルを変えたい

`MODEL = "claude-opus-4-7"` を `claude-sonnet-4-6` 等に変更するとコストが下がります（品質はわずかに低下）。

### 配信先を増やしたい

`RECIPIENT_EMAIL` をカンマ区切りにし、`morning_news.py` の `recipient` 変数で `split(",")` してリスト化、`recipient_emails` を `msg["To"]` にカンマ連結で渡すよう改修すれば対応可能です。

---

## ライセンス

個人利用・組織内利用は自由です。

---

## 関連

このボットは Claude のスキル「global-news-sharer」（SKILL.md）の設計をベースにしています。
スキルの全仕様は `SKILL.md` を参照してください。
