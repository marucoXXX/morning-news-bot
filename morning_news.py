"""
Morning News Bot v3.3
======================
- v3.3 で追加: ソースURL捏造問題の修正
  - 各記事の「🔗 ソース」欄は媒体名のみ表記に変更
  - 朝刊末尾に「📚 本日参照したソース一覧」を追加（Grounding Metadataから抽出した実URLのみ）
- 海外ニュース朝刊6本（メイン5本＋国内未報道スポット1本）
- 国内ニュース朝刊7本（深掘り3本＋見出し4本）
- 重複排除ロジック（海外で扱った話題を国内から除外）
- 拡張ラジオ台本（7-10分、コンサル調、9本カバー）
- HTMLメール + MP3添付でGmail配信

処理の流れ：
1. Gemini 2.5 Pro で海外朝刊Markdown生成
2. Gemini 2.5 Pro で国内朝刊Markdown生成（海外朝刊の見出しを文脈として渡す）
3. Claude Sonnet 4.6 で拡張ラジオ台本生成（海外6本＋国内深掘り3本=9本）
4. OpenAI TTS でMP3生成
5. Gmail SMTP でHTMLメール + MP3添付で送信
"""

import os
import re
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.audio import MIMEAudio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import markdown
from google import genai
from google.genai import types
from openai import OpenAI


# ============================================================
# 設定
# ============================================================
NEWS_MODEL = "gemini-2.5-pro"
SCRIPT_MODEL = "claude-sonnet-4-6"
TTS_MODEL = "tts-1-hd"
TTS_VOICE = "shimmer"
TTS_SPEED = 1.0

MAX_TOKENS_NEWS = 16000
MAX_TOKENS_SCRIPT = 12000
JST = timezone(timedelta(hours=9))


# ============================================================
# プロンプト1: 海外朝刊生成（v3と同じ）
# ============================================================
GLOBAL_NEWS_SYSTEM_PROMPT = """あなたは「Global News Sharer（朝刊モード）」スキルを実行する戦略コンサルタントです。
海外の主要紙・メディアから今日の海外ニュース朝刊を組み立てる役割を持ちます。

# あなたの仕事

毎朝、以下の構成で海外ニュース朝刊を1つ生成してください：

1. **メイン5本**: 海外テック・ビジネスニュース
   - 地域配分: US 2-3本、欧州 1本、中国/アジア 1本
   - テック:ビジネス ≒ 3:2
   - 過去24-72時間以内
2. **国内未報道スポット1本**: 主要日本メディアで報道されていない海外テック・ビジネスニュース
   - 必ず日本語検索（site:nikkei.com、site:itmedia.co.jp等）で未報道を確認
   - 海外2媒体以上で報道されていること
   - 政治・社会・文化は対象外、事業関連のみ

# 主要ニュースソース（一次メディア優先）

US: NYT / WSJ / Bloomberg / Reuters / AP / Axios / TechCrunch / The Information / The Verge
欧州: FT / The Economist / BBC / Politico Europe
アジア: Nikkei Asia / SCMP / Caixin Global / Economic Times

アグリゲーターよりも一次メディアを優先。

# 各記事のフォーマット（4セクション、約300〜400字）

```
{国旗 or 🔍} **{番号 or "国内未報道スポット |"} {見出し}**

**🌍 背景**
{3〜4文。なぜ今この話か、業界文脈}

**🔑 ポイント**
- {1文}
- {1文}
- {1文}

**🇯🇵 日本への示唆**
{3〜4文。具体的な日本企業名・業界名}

**🤨 批判的コメント**
{2〜3文}

**🔗 ソース**: {媒体名1} | {媒体名2}
```

# ソース表記の厳守ルール（最重要）

各記事の「🔗 ソース」欄には、URLを書かないこと。
代わりに、参照した媒体名のみを記載する。

✅ 正しい例: 🔗 ソース: Bloomberg | Reuters
✅ 正しい例: 🔗 ソース: TechCrunch | The Information | FT
❌ 間違い: 🔗 ソース: https://www.bloomberg.com/news/articles/...

理由: URLを直接書くと、検索結果に存在しない架空のURLを生成してしまう
リスクがあるため、媒体名のみに統一する。実URLは末尾の参照ソース一覧で
別途まとめて提示する。

# 通貨併記ルール

海外通貨の金額には必ず日本円換算を併記：
- $1 = 約150円
- €1 = 約170円
- £1 = 約195円

例: `$33B（約5兆円）` `$39M（約60億円）`

# 出力形式

```
# 海外ニュース朝刊（{YYYY-MM-DD ddd}）

📰 **海外ニュース5本＋国内未報道スポット**

🇺🇸 **① {見出し1}**（{ソース}）
🇺🇸 **② {見出し2}**（{ソース}）
🇪🇺 **③ {見出し3}**（{ソース}）
🇨🇳 **④ {見出し4}**（{ソース}）
🌐 **⑤ {見出し5}**（{ソース}）

🔍 **国内未報道スポット**
**{スポット見出し}**（{ソース}）

---

## ① {見出し1}
{記事1の本文}

---

（5本＋スポット1本）
```

確認・前置きなしに、いきなり朝刊本体を出力する。"""


# ============================================================
# プロンプト2: 国内朝刊生成（NEW、重複排除あり）
# ============================================================
DOMESTIC_NEWS_SYSTEM_PROMPT = """あなたは日経新聞・東洋経済・ダイヤモンドのトップ編集者を兼ねた戦略コンサルタントです。
日本国内のビジネス・経済ニュースから、ビジネスパーソン向けの国内朝刊を組み立てる役割を持ちます。

# あなたの仕事

以下の構成で国内ビジネス・経済ニュース朝刊を1つ生成してください：

## メイン構成
- **深掘り3本（⭐マーク）**: 4セクション形式（背景／ポイント／日本への示唆／批判的コメント）
- **見出し4本**: 見出し＋2〜3文の要約のみ（軽量版）
- **合計7本**

## テーマ選定の方針

**ビジネス・経済中心**で構成（ビジネスパーソン向け、日経代替を意識）：

優先度高：
- 日本企業の戦略的な発表（M&A、経営判断、事業再編、決算ハイライト）
- 日銀・金融政策・マクロ経済指標
- 業界再編・規制動向
- 金融市場の重要動向（株式・為替・金利）
- 大型のテクノロジー・スタートアップニュース

優先度中：
- 政治のうち経済政策に関するもの（税制、規制、貿易）
- 国際情勢のうち日本経済への影響が大きいもの

対象外：
- 政治の党内人事・選挙報道（経済政策以外）
- 社会・事件・スポーツ・芸能

## 重複排除ルール（必須）

**海外朝刊で既に扱った話題は、国内朝刊では除外すること。**

ユーザーから「本日の海外朝刊で扱われた見出し一覧」が提供されます。
それらの話題と重複する内容は、たとえ国内企業に影響があっても、国内朝刊には含めないでください。

例：
- 海外朝刊で「Apple CEO交代」を扱っていた場合、国内朝刊で「Apple CEO交代の日本サプライヤーへの影響」は重複扱い → 除外
- 海外朝刊で「DeepSeek資金調達」を扱っていた場合、国内朝刊で「ソフトバンクGの中国AIへの言及」は重複扱い → 除外

国内朝刊は「**国内発の純粋に独立したニュース**」を選定してください。

## 主要ニュースソース

総合・経済：日経、Reuters日本版、Bloomberg日本版、東洋経済オンライン、ダイヤモンドオンライン
業界：日経電子版各業界、日経ビジネス
個別IR：各社の適時開示・IR情報

## 各記事のフォーマット

### 深掘り3本（4セクション、各約300〜400字）

```
🇯🇵 **① {見出し}**

**🌍 背景**
{3〜4文。業界文脈}

**🔑 ポイント**
- {1文}
- {1文}
- {1文}

**🇯🇵 日本への示唆**
{3〜4文。具体的な日本企業名・業界名}

**🤨 批判的コメント**
{2〜3文}

**🔗 ソース**: {媒体名1} | {媒体名2}
```

### 見出し4本（軽量版、各約100〜150字）

```
**④ {見出し}**
{2〜3文の要約。具体的な数字・固有名詞含む。末尾に参照媒体名を記載（URLは書かない）}
```

# ソース表記の厳守ルール（最重要）

各記事の「🔗 ソース」欄および見出し記事の参照表記には、URLを書かないこと。
代わりに、参照した媒体名のみを記載する。

✅ 正しい例: 🔗 ソース: 日経新聞 | Bloomberg日本版
✅ 正しい例: 🔗 ソース: 東洋経済オンライン | Reuters日本版
❌ 間違い: 🔗 ソース: https://www.nikkei.com/article/...

理由: URLを直接書くと、検索結果に存在しない架空のURLを生成してしまう
リスクがあるため、媒体名のみに統一する。実URLは末尾の参照ソース一覧で
別途まとめて提示する。

## 通貨併記ルール

外貨が出る場合は併記（$1=150円、€1=170円、£1=195円）。
円表記が中心の場合は不要。

## 出力形式

```
# 国内ニュース朝刊（{YYYY-MM-DD ddd}）

📰 **国内ビジネス・経済7本** | 深掘り3本＋見出し4本

🇯🇵 **① {見出し1}**（{ソース}）⭐深掘り
🇯🇵 **② {見出し2}**（{ソース}）⭐深掘り
🇯🇵 **③ {見出し3}**（{ソース}）⭐深掘り
🇯🇵 ④ {見出し4}
🇯🇵 ⑤ {見出し5}
🇯🇵 ⑥ {見出し6}
🇯🇵 ⑦ {見出し7}

⭐は本日深掘り解説 / それ以外は見出し＋要約のみ

---

## ① {深掘り見出し1}
{4セクション本文}

---

## ② {深掘り見出し2}
{4セクション本文}

---

## ③ {深掘り見出し3}
{4セクション本文}

---

## ④ {見出し4}
{2〜3文要約}

## ⑤ {見出し5}
{2〜3文要約}

## ⑥ {見出し6}
{2〜3文要約}

## ⑦ {見出し7}
{2〜3文要約}

---

以上、本日の国内ニュース朝刊7本でした。
```

確認・前置きなしに、いきなり朝刊本体を出力する。"""


# ============================================================
# プロンプト3: 拡張ラジオ台本生成（7-10分、9本カバー）
# ============================================================
SCRIPT_SYSTEM_PROMPT = """あなたは経済・テック専門のラジオパーソナリティです。コンサルタント顔負けの本格的なジャーナリスティック・トーンで、朝の通勤・ランニング中のビジネスパーソンに向けて、海外＋国内ニュースを「7〜10分のラジオ番組」として届けます。

# 番組コンセプト

- 番組タイトル：「海外ニュース朝刊」（国内も含めて統合番組）
- 想定リスナー：日本のビジネスパーソン、通勤・運動中
- 時間：7〜10分（読み上げ）
- 雰囲気：コンサル調の本格トーン、知的緊張感、論点を明確に

# 番組構成（必須）

## オープニング（30秒程度）
「おはようございます。海外ニュース朝刊、{日付}です。」

→ 今朝のテーマを「海外と国内、合わせて9本のニュース」と簡潔に紹介
→ キーワード3つ程度で全体像を提示
→「それでは1本目から参りましょう」で本編へ

## 本編：海外6本（各60〜90秒、合計約7分）

朝刊Markdownの海外メイン5本＋国内未報道スポット1本を**全て**取り上げる。
各記事の構成：

a. 背景の一言サマリー（1文）
b. 何が起きたか（2〜3文、数字や固有名詞は1〜2個に絞る）
c. 日本への示唆（1〜2文、最重要）
d. 批判的コメント（1文、見落とされがちな観点）

記事間に司会の繋ぎ1〜2文を入れる：
- 「続いて2本目、〜」「次は中国の話題です」「ここまでがテック、ここからビジネス」など
- 区切りには `──` を1行入れる（音声化時には自然な間として処理される）

## 本編：国内3本（深掘り分のみ、各60〜90秒、合計約3分）

朝刊Markdownの国内深掘り3本を取り上げる（見出し4本は省略）。
構成は海外記事と同じ。

海外パートから国内パートへの移行時：
「ここからは、国内ニュースに移ります。今朝の国内朝刊からは、特に重要な3本を深掘りします」

## クロージング（30秒程度）

「以上、本日の海外ニュース朝刊でした」と締め。
さらに、本日の朝刊全体を貫く「メッセージ」を1〜2文で提示：
- 例「今朝、改めて見えてきたのは、AIを軸とする資本配分の地殻変動です」
- 海外と国内のニュースを束ねる視点を提示

最後：「詳細はメール本文をご確認ください。それでは、皆様、良い1日を」

# 音声化のための書き方ルール（絶対）

- **絵文字・記号・URLは一切含めない**
- **箇条書き・見出し記号も使わない**（プレーンな段落だけ）
- **数字は適切に変換**：
  - `$33B` → 「330億ドル、日本円で約5兆円」
  - `+582%` → 「プラス582パーセント」
- **英語の固有名詞**は読みやすく：
  - `OpenAI` → 「オープン・エーアイ」
  - `Anthropic` → 「アンソロピック」
  - `DeepSeek` → 「ディープシーク」
  - `Tim Cook` → 「ティム・クック」
  - `Cohere` → 「コヒア」
  - `Aleph Alpha` → 「アレフ・アルファ」
- **長い文を避ける**：1文あたり40〜60字程度
- **段落の間は必ず1行空ける**
- **「、」「。」を意識的に多用**
- **セクション区切りには `──` を1行入れる**（音声合成エンジンが間を取りやすい）

# 出力形式（プレーンテキスト）

```
おはようございます。海外ニュース朝刊、{日付}です。

{オープニング、テーマ要約}

それでは、まず1本目から参りましょう。

──

{記事1の紹介、約60〜90秒分}

──

{記事2の紹介}

──

（海外6本続く）

──

ここからは、国内ニュースに移ります。今朝の国内朝刊からは、特に重要な3本を深掘りします。

──

{国内記事1の紹介}

──

{国内記事2の紹介}

──

{国内記事3の紹介}

──

以上、本日の海外ニュース朝刊でした。

{今朝のメインメッセージ、1〜2文}

詳細はメール本文をご確認ください。それでは、皆様、良い1日を。
```

# 重要

- 確認・前置きなしに、いきなり台本本体を出力する
- 文字数は **3,500〜5,000字程度**（読み上げで7〜10分）
- 海外6本は全て取り上げる、国内は深掘り3本のみ"""


# ============================================================
# Grounding URL 抽出 / ソース一覧追加ヘルパー
# ============================================================
def extract_grounding_urls(response) -> list[dict]:
    """Geminiレスポンスから grounding_metadata の実URL一覧を抽出する。

    Returns:
        [{"url": "https://...", "title": "..."}, ...] のリスト
    """
    urls = []
    seen = set()

    if not hasattr(response, "candidates") or not response.candidates:
        return urls

    candidate = response.candidates[0]
    if not hasattr(candidate, "grounding_metadata") or not candidate.grounding_metadata:
        return urls

    grounding_chunks = getattr(candidate.grounding_metadata, "grounding_chunks", None)
    if not grounding_chunks:
        return urls

    for chunk in grounding_chunks:
        web = getattr(chunk, "web", None)
        if web:
            url = getattr(web, "uri", None)
            title = getattr(web, "title", None) or "(タイトル不明)"
            if url and url not in seen:
                seen.add(url)
                urls.append({"url": url, "title": title})

    return urls


def append_source_list(markdown_body: str, grounding_urls: list[dict], section_title: str) -> str:
    """朝刊Markdownの末尾に「参照したソース一覧」セクションを追加する。"""
    if not grounding_urls:
        return markdown_body

    source_section = f"\n\n---\n\n## 📚 {section_title}\n\n"
    source_section += "_本朝刊の生成にあたり、Google Searchで実際に参照した記事一覧です。クリックで原典記事に飛べます。_\n\n"

    for i, item in enumerate(grounding_urls, 1):
        title = item["title"]
        if len(title) > 80:
            title = title[:77] + "..."
        source_section += f"{i}. [{title}]({item['url']})\n"

    return markdown_body + source_section


# ============================================================
# Step 1: 海外朝刊Markdown生成
# ============================================================
def generate_global_news(api_key: str) -> tuple[str, list[dict]]:
    today = datetime.now(JST)
    date_label = today.strftime("%Y-%m-%d %a")

    print(f"[INFO] [Step 1/4] Generating global morning news for {date_label}...", file=sys.stderr)
    print(f"[INFO] Model: {NEWS_MODEL} (Gemini)", file=sys.stderr)

    client = genai.Client(api_key=api_key)
    user_prompt = (
        f"今日（{date_label}）の海外ニュース朝刊を生成してください。\n\n"
        "過去24〜72時間の海外主要紙ヘッドラインから、"
        "メイン5本＋国内未報道スポット1本を選定し、完全な朝刊Markdownを出力してください。\n\n"
        "Google Searchを使って最新情報を取得しながら進めてください。"
    )

    google_search_tool = types.Tool(google_search=types.GoogleSearch())

    response = client.models.generate_content(
        model=NEWS_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=GLOBAL_NEWS_SYSTEM_PROMPT,
            tools=[google_search_tool],
            max_output_tokens=MAX_TOKENS_NEWS,
            temperature=0.7,
        ),
    )

    markdown_body = response.text or ""
    print(f"[INFO] Generated {len(markdown_body)} chars of global news markdown", file=sys.stderr)

    grounding_urls = extract_grounding_urls(response)
    print(f"[INFO] Extracted {len(grounding_urls)} grounding URLs", file=sys.stderr)

    return markdown_body, grounding_urls


# ============================================================
# Step 2: 国内朝刊Markdown生成（重複排除あり）
# ============================================================
def extract_global_headlines(global_news_md: str) -> list[str]:
    """海外朝刊のフロントページから見出し一覧を抽出する（重複排除用）。"""
    headlines = []
    # フロントページの ① ② ③ ④ ⑤ パターンと スポット の見出しを拾う
    pattern = re.compile(r"\*\*[①②③④⑤]\s*([^*]+)\*\*", re.MULTILINE)
    for match in pattern.findall(global_news_md):
        headlines.append(match.strip())

    # スポットの見出し（**国内未報道スポット | XXX** または **国内未報道スポット** の次行の見出し）
    spot_pattern = re.compile(r"🔍\s*\*\*国内未報道スポット\*\*\s*\n\s*\*\*([^*]+)\*\*")
    for match in spot_pattern.findall(global_news_md):
        headlines.append(match.strip())

    return headlines


def generate_domestic_news(api_key: str, global_headlines: list[str]) -> tuple[str, list[dict]]:
    today = datetime.now(JST)
    date_label = today.strftime("%Y-%m-%d %a")

    print(f"[INFO] [Step 2/4] Generating domestic morning news...", file=sys.stderr)
    print(f"[INFO] Model: {NEWS_MODEL} (Gemini)", file=sys.stderr)
    print(f"[INFO] Excluding {len(global_headlines)} topics from global edition", file=sys.stderr)

    headlines_text = "\n".join(f"- {h}" for h in global_headlines) if global_headlines else "（海外朝刊の見出し抽出に失敗）"

    client = genai.Client(api_key=api_key)
    user_prompt = (
        f"今日（{date_label}）の国内ニュース朝刊（ビジネス・経済中心）を生成してください。\n\n"
        f"# 本日の海外朝刊の見出し（重複排除のため）\n\n"
        f"以下の話題は本日の海外朝刊で扱われています。これらの話題と重複する内容は、"
        f"国内朝刊には含めないでください。\n\n"
        f"{headlines_text}\n\n"
        f"# 指示\n\n"
        f"国内発の独立したビジネス・経済ニュースを選定し、"
        f"深掘り3本＋見出し4本＝合計7本の構成で、完全な朝刊Markdownを出力してください。\n\n"
        f"Google Searchを使って最新情報を取得しながら進めてください。"
    )

    google_search_tool = types.Tool(google_search=types.GoogleSearch())

    response = client.models.generate_content(
        model=NEWS_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=DOMESTIC_NEWS_SYSTEM_PROMPT,
            tools=[google_search_tool],
            max_output_tokens=MAX_TOKENS_NEWS,
            temperature=0.7,
        ),
    )

    markdown_body = response.text or ""
    print(f"[INFO] Generated {len(markdown_body)} chars of domestic news markdown", file=sys.stderr)

    grounding_urls = extract_grounding_urls(response)
    print(f"[INFO] Extracted {len(grounding_urls)} grounding URLs", file=sys.stderr)

    return markdown_body, grounding_urls


# ============================================================
# Step 3: 拡張ラジオ台本生成
# ============================================================
def generate_radio_script(api_key: str, global_news_md: str, domestic_news_md: str) -> str:
    today = datetime.now(JST)
    date_label = today.strftime("%Y年%m月%d日 %a曜日")

    print(f"[INFO] [Step 3/4] Generating extended radio script...", file=sys.stderr)
    print(f"[INFO] Model: {SCRIPT_MODEL} (Anthropic)", file=sys.stderr)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=SCRIPT_MODEL,
        max_tokens=MAX_TOKENS_SCRIPT,
        system=SCRIPT_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"以下の海外朝刊と国内朝刊（日付: {date_label}）を、"
                    f"7〜10分のラジオ番組台本に変換してください。\n\n"
                    f"# 海外朝刊\n\n{global_news_md}\n\n"
                    f"# 国内朝刊\n\n{domestic_news_md}\n\n"
                    f"---\n\n"
                    "海外6本＋国内深掘り3本＝合計9本を取り上げてください。"
                    "国内の見出し4本は省略してください。"
                    "音声化のため、絵文字・記号・URL・箇条書き記号を一切使わない、"
                    "プレーンテキストの台本を出力してください。"
                ),
            }
        ],
    )

    text_parts = [b.text for b in response.content if b.type == "text"]
    script = "\n".join(text_parts)
    print(f"[INFO] Generated {len(script)} chars of script", file=sys.stderr)
    return script


# ============================================================
# Step 4: OpenAI TTS で音声生成
# ============================================================
def generate_audio_with_openai(api_key: str, script: str, output_path: str) -> str:
    print(f"[INFO] [Step 4/4] Generating audio with OpenAI TTS...", file=sys.stderr)
    print(f"[INFO] TTS model: {TTS_MODEL}, voice: {TTS_VOICE}", file=sys.stderr)

    client = OpenAI(api_key=api_key)
    chunks = _split_text_for_tts(script, max_chars=4000)
    print(f"[INFO] Script split into {len(chunks)} chunk(s)", file=sys.stderr)

    audio_bytes = bytearray()
    for i, chunk in enumerate(chunks, 1):
        print(f"[INFO]   Generating chunk {i}/{len(chunks)} ({len(chunk)} chars)...", file=sys.stderr)
        response = client.audio.speech.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=chunk,
            speed=TTS_SPEED,
            response_format="mp3",
        )
        audio_bytes.extend(response.content)

    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    file_size_mb = len(audio_bytes) / 1024 / 1024
    print(f"[INFO] Audio saved: {output_path} ({file_size_mb:.2f} MB)", file=sys.stderr)
    return output_path


def _split_text_for_tts(text: str, max_chars: int = 4000) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para) if current else para
        else:
            if current:
                chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks


# ============================================================
# Markdown → HTML 変換（海外＋国内を1通に統合）
# ============================================================
def build_email_html(global_news_md: str, domestic_news_md: str, date_label: str, has_audio: bool) -> str:
    """海外朝刊と国内朝刊を1通のHTMLメールに統合する。"""
    global_html = markdown.markdown(global_news_md, extensions=["extra", "sane_lists", "nl2br"])
    domestic_html = markdown.markdown(domestic_news_md, extensions=["extra", "sane_lists", "nl2br"])

    audio_notice = ""
    if has_audio:
        audio_notice = """
    <div style="background:#fff8e7;border-left:4px solid #f0a500;padding:12px 16px;margin:0 0 24px 0;font-size:14px;color:#444;">
      🎧 <strong>音声版が添付されています</strong>（MP3, 約7〜10分）。海外6本＋国内深掘り3本のラジオ番組です。
    </div>"""

    section_divider = """
    <div style="margin:48px 0;padding:24px 0;border-top:4px double #1a1a1a;border-bottom:4px double #1a1a1a;text-align:center;">
      <div style="font-size:13px;color:#888;letter-spacing:0.2em;">SECTION 2</div>
      <div style="font-size:18px;font-weight:bold;margin-top:4px;">🇯🇵 国内ニュース朝刊</div>
    </div>"""

    email_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>朝刊 {date_label}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:'Hiragino Sans','Yu Gothic','Meiryo',sans-serif;color:#222;line-height:1.7;">
  <div style="max-width:680px;margin:0 auto;padding:24px 16px;background:#ffffff;">

    <div style="border-bottom:3px solid #1a1a1a;padding-bottom:12px;margin-bottom:24px;">
      <div style="font-size:13px;color:#888;letter-spacing:0.1em;">MORNING NEWS BRIEFING</div>
      <div style="font-size:22px;font-weight:bold;margin-top:4px;">朝刊 / {date_label}</div>
      <div style="font-size:12px;color:#888;margin-top:4px;">海外6本＋国内7本</div>
    </div>

    {audio_notice}

    <div style="margin:24px 0 16px 0;text-align:center;">
      <div style="font-size:13px;color:#888;letter-spacing:0.2em;">SECTION 1</div>
      <div style="font-size:18px;font-weight:bold;margin-top:4px;">🌍 海外ニュース朝刊</div>
    </div>

    <div style="font-size:15px;color:#222;">
      {_inject_inline_styles(global_html)}
    </div>

    {section_divider}

    <div style="font-size:15px;color:#222;">
      {_inject_inline_styles(domestic_html)}
    </div>

    <div style="margin-top:40px;padding-top:16px;border-top:1px solid #ddd;font-size:12px;color:#888;text-align:center;">
      Generated by Gemini 2.5 Pro + Claude Sonnet 4.6 + OpenAI TTS · GitHub Actions
    </div>

  </div>
</body>
</html>"""
    return email_html


def _inject_inline_styles(html: str) -> str:
    replacements = [
        ("<h1>", '<h1 style="font-size:20px;margin:32px 0 16px 0;padding-bottom:8px;border-bottom:2px solid #1a1a1a;">'),
        ("<h2>", '<h2 style="font-size:18px;margin:28px 0 12px 0;padding:8px 12px;background:#f0f0f0;border-left:4px solid #1a1a1a;">'),
        ("<h3>", '<h3 style="font-size:16px;margin:20px 0 8px 0;color:#444;">'),
        ("<p>", '<p style="margin:12px 0;">'),
        ("<ul>", '<ul style="margin:12px 0;padding-left:24px;">'),
        ("<li>", '<li style="margin:6px 0;">'),
        ("<hr />", '<hr style="border:none;border-top:1px dashed #ccc;margin:32px 0;">'),
        ("<hr>", '<hr style="border:none;border-top:1px dashed #ccc;margin:32px 0;">'),
        ("<strong>", '<strong style="color:#1a1a1a;font-weight:bold;">'),
        ("<em>", '<em style="color:#666;font-style:italic;">'),
        ("<a ", '<a style="color:#0066cc;text-decoration:underline;" '),
        ("<code>", '<code style="background:#f4f4f4;padding:2px 6px;border-radius:3px;font-family:Menlo,monospace;font-size:13px;">'),
    ]
    for old, new in replacements:
        html = html.replace(old, new)
    return html


# ============================================================
# Gmail SMTP送信
# ============================================================
def send_email_with_audio(
    sender: str,
    app_password: str,
    recipient: str,
    subject: str,
    html_body: str,
    plain_body: str,
    audio_path: str | None = None,
) -> None:
    if audio_path:
        msg = MIMEMultipart("mixed")
        body_container = MIMEMultipart("alternative")
        msg.attach(body_container)
    else:
        msg = MIMEMultipart("alternative")
        body_container = msg

    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    body_container.attach(MIMEText(plain_body, "plain", "utf-8"))
    body_container.attach(MIMEText(html_body, "html", "utf-8"))

    if audio_path and os.path.exists(audio_path):
        with open(audio_path, "rb") as f:
            audio_part = MIMEAudio(f.read(), _subtype="mpeg")
        filename = os.path.basename(audio_path)
        audio_part.add_header(
            "Content-Disposition", "attachment", filename=filename
        )
        msg.attach(audio_part)
        size_mb = os.path.getsize(audio_path) / 1024 / 1024
        print(f"[INFO] Attached audio: {filename} ({size_mb:.2f} MB)", file=sys.stderr)

    print(f"[INFO] Connecting to smtp.gmail.com:587...", file=sys.stderr)
    cleaned_password = re.sub(r"\s+", "", app_password)
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, cleaned_password)
        server.send_message(msg)
    print(f"[INFO] Email sent to {recipient}", file=sys.stderr)


# ============================================================
# メインエントリポイント
# ============================================================
def main() -> int:
    gemini_key = os.environ.get("GEMINI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    gmail_address = os.environ.get("GMAIL_ADDRESS")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient_email = os.environ.get("RECIPIENT_EMAIL")

    required = {
        "GEMINI_API_KEY": gemini_key,
        "ANTHROPIC_API_KEY": anthropic_key,
        "GMAIL_ADDRESS": gmail_address,
        "GMAIL_APP_PASSWORD": gmail_app_password,
        "RECIPIENT_EMAIL": recipient_email,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print(f"[ERROR] Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    audio_enabled = bool(openai_key)
    if not audio_enabled:
        print("[WARN] OPENAI_API_KEY not set. Sending without audio attachment.", file=sys.stderr)

    today = datetime.now(JST)
    date_label = today.strftime("%Y-%m-%d %a")

    # Step 1: 海外朝刊
    global_urls: list[dict] = []
    try:
        global_news_md, global_urls = generate_global_news(gemini_key)
        if not global_news_md.strip():
            raise RuntimeError("Empty response from Gemini for global news")
    except Exception as e:
        print(f"[ERROR] Failed to generate global news: {e}", file=sys.stderr)
        global_news_md = f"# 海外朝刊生成エラー\n\nエラー: `{e}`"
        audio_enabled = False

    # Step 2: 国内朝刊（重複排除あり）
    # ヘッドライン抽出はソース一覧追加前のmarkdownから行う（重複排除の精度のため）
    domestic_urls: list[dict] = []
    try:
        global_headlines = extract_global_headlines(global_news_md)
        domestic_news_md, domestic_urls = generate_domestic_news(gemini_key, global_headlines)
        if not domestic_news_md.strip():
            raise RuntimeError("Empty response from Gemini for domestic news")
    except Exception as e:
        print(f"[ERROR] Failed to generate domestic news: {e}", file=sys.stderr)
        domestic_news_md = f"# 国内朝刊生成エラー\n\nエラー: `{e}`"

    # 末尾に「📚 本日参照したソース一覧」を追加（実URLのみ）
    global_news_md = append_source_list(global_news_md, global_urls, "本日参照した海外ソース")
    domestic_news_md = append_source_list(domestic_news_md, domestic_urls, "本日参照した国内ソース")

    # Step 3 & 4: 台本生成 + 音声生成
    audio_path = None
    if audio_enabled:
        try:
            script = generate_radio_script(anthropic_key, global_news_md, domestic_news_md)
            audio_path = f"morning_news_{today.strftime('%Y%m%d')}.mp3"
            generate_audio_with_openai(openai_key, script, audio_path)
        except Exception as e:
            print(f"[ERROR] Audio generation failed: {e}", file=sys.stderr)
            print("[WARN] Falling back to email-only delivery.", file=sys.stderr)
            audio_path = None

    # メール送信
    has_audio = audio_path is not None
    html = build_email_html(global_news_md, domestic_news_md, date_label, has_audio=has_audio)

    audio_emoji = "🎧" if has_audio else "📰"
    subject = f"{audio_emoji} 朝刊（海外6＋国内7） / {date_label}"

    # Plain版は両朝刊を結合
    plain_body = f"{global_news_md}\n\n\n========================================\n\n\n{domestic_news_md}"

    send_email_with_audio(
        sender=gmail_address,
        app_password=gmail_app_password,
        recipient=recipient_email,
        subject=subject,
        html_body=html,
        plain_body=plain_body,
        audio_path=audio_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
