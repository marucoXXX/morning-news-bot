"""
Morning News Bot (Phase 1: Audio Attachment)
=============================================
毎朝、海外の主要紙から「朝刊」を生成してGmail配信する。
さらに、朝刊をラジオ番組風の台本に変換してOpenAI TTSで音声化し、
MP3としてメールに添付する。

処理の流れ：
1. Claude API（claude-opus-4-7）で朝刊Markdown生成（web_search使用）
2. Claude API（claude-sonnet-4-6）でラジオ台本生成（音声向けに口語調・3-5分）
3. OpenAI TTS でMP3生成
4. Gmail SMTP でHTMLメール + MP3添付で送信

実行はGitHub Actionsから（毎日JST 7:00）。
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
from openai import OpenAI


# ============================================================
# 設定
# ============================================================
NEWS_MODEL = "claude-sonnet-4-6"        # 朝刊生成（コスト重視で4/29にOPUSからSONNETへ変更）
SCRIPT_MODEL = "claude-sonnet-4-6"      # 台本変換（軽量モデルで十分）
TTS_MODEL = "tts-1-hd"                  # OpenAI TTS（品質重視）
TTS_VOICE = "shimmer"                   # 落ち着いた女性ボイス（他: alloy, echo, fable, onyx, nova）
TTS_SPEED = 1.0                         # 0.25〜4.0、1.0が標準

MAX_TOKENS_NEWS = 16000
MAX_TOKENS_SCRIPT = 8000
JST = timezone(timedelta(hours=9))


# ============================================================
# プロンプト1: 朝刊生成（Phase 1から流用）
# ============================================================
NEWS_SYSTEM_PROMPT = """あなたは「Global News Sharer（朝刊モード）」スキルを実行する戦略コンサルタントです。
海外の主要紙・メディアから今日の朝刊を組み立てる役割を持ちます。

# あなたの仕事

毎朝、以下の構成で朝刊を1つ生成してください：

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

アグリゲーター（techstartups.com等）よりも一次メディアを優先。
ペイウォール記事も検索結果スニペットで十分なファクトが取れる。

# 各記事のフォーマット

各記事は以下の4セクション、約300〜400字：

```
{国旗 or 🔍} **{番号 or "国内未報道スポット |"} {見出し}**

**🌍 背景**
{3〜4文。なぜ今この話か、業界文脈。重要キーワードは **太字**}

**🔑 ポイント**
- {1文。具体的な数字・固有名詞}
- {1文}
- {1文}
（3〜4個）

**🇯🇵 日本への示唆**
{3〜4文。具体的な日本企業名・業界名を含める。抽象論NG}

**🤨 批判的コメント**
{2〜3文。ポジショントーク・バイアス・見落とされがちな観点}

**🔗 ソース**: {URL1} | {URL2}
```

スポット記事は末尾に：
`_※ 主要日本メディア（日経・ITmedia等）で確認した範囲では、本件の日本語報道は見当たらず_`

# 通貨併記ルール（必須）

海外通貨の金額には必ず日本円換算を併記：
- $1 = 約150円
- €1 = 約170円
- £1 = 約195円

例: `$33B（約5兆円）` `$39M（約60億円）` `€500M（約840億円）`

# 出力形式

以下の構造で出力してください（必ずこの順番、必ずこのMarkdown形式）：

```
# 朝刊（{YYYY-MM-DD ddd}）

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

## ② {見出し2}

{記事2の本文}

...（5本まで）

---

## 🔍 国内未報道スポット | {スポット見出し}

{スポット記事の本文}

---

以上、本日の朝刊5本＋国内未報道スポット1本でした。
```

# 重要な制約

- 必ず web_search ツールを使って最新情報を取得すること
- 1記事あたり 2〜3 回の web_search で十分。合計で 12〜18 回程度を目安に
- 確認・前置きなしに、いきなり朝刊本体を出力する"""


# ============================================================
# プロンプト2: ラジオ台本生成（NEW）
# ============================================================
SCRIPT_SYSTEM_PROMPT = """あなたは経済・テック専門のラジオパーソナリティです。
朝の通勤・ランニング中の聞き手に向けて、海外ニュース朝刊を「3〜5分のラジオ番組台本」に変換します。

# 番組コンセプト

- 番組タイトル：「海外ニュース朝刊」
- 想定リスナー：日本のビジネスパーソン、通勤・運動中
- 時間：3〜5分（音声で読み上げて）
- 雰囲気：落ち着いた知的なトーン、押し付けがましくない、聞き手目線

# 台本の構成

1. **オープニング（10秒程度）**
   - 「おはようございます。海外ニュース朝刊、{日付}です。今朝のトピックは{テーマ要約}」のような短い導入
   - 全6本の見出しは紹介しない（聞き手が混乱する）。代わりに「3つの大きな話題」のように要約

2. **メイン記事の紹介（各記事30〜45秒）**
   - 朝刊の5本＋スポット1本のうち、**最も重要な3〜4本のみ取り上げる**（全6本入れると長すぎる）
   - 選定基準：日本のビジネスパーソンへの示唆が大きい順
   - 各記事の構成：
     a. 背景の一言サマリー（1文）
     b. 何が起きたか（2〜3文、数字や固有名詞は1〜2個に絞る）
     c. 日本への示唆（1〜2文、最重要）
   - 「批判的コメント」は省略（音声では複雑になる）

3. **クロージング（10秒程度）**
   - 「以上、本日の海外ニュース朝刊でした。詳細はメール本文をご確認ください。良い1日を」のような締め

# 音声化のための書き方ルール

絶対に守ってください：

- **絵文字・記号・URLは一切含めない**（読み上げで「マルイチ」「ハートマーク」と読まれる）
- **箇条書き・見出し記号も使わない**（プレーンな段落だけ）
- **数字は適切に変換**：
  - `$33B` → 「330億ドル、日本円で約5兆円」
  - `+582%` → 「プラス582パーセント」
  - `2026年4月` → 「ニーゼロニーロク年4月」ではなく「2026年4月」のまま
- **英語の固有名詞**は読みやすく：
  - `OpenAI` → 「オープン・エーアイ」
  - `Anthropic` → 「アンソロピック」
  - `DeepSeek` → 「ディープシーク」
  - `Tim Cook` → 「ティム・クック」
- **専門用語は解説を一言加える**：
  - 「LLM、つまり大規模言語モデル」
  - 「DCF、つまり割引キャッシュフロー法」
- **長い文を避ける**：1文あたり40〜60字程度を目安に
- **段落の間は必ず1行空ける**（音声合成エンジンが間を取りやすい）
- **「、」「。」を意識的に多用**（自然な間が生まれる）

# 出力形式

以下のフォーマットでプレーンテキストを出力（Markdownや絵文字は一切使わない）：

```
おはようございます。海外ニュース朝刊、{日付}です。

{テーマ要約の導入文}

{記事1の紹介、約30〜45秒分}

{記事2の紹介}

{記事3の紹介}

{必要なら記事4の紹介}

以上、本日の海外ニュース朝刊でした。詳細はメール本文をご確認ください。良い1日を。
```

# 重要

- 確認・前置きなしに、いきなり台本本体を出力する
- 文字数は **1500〜2400字程度**（読み上げで3〜5分）"""


# ============================================================
# Step 1: 朝刊Markdown生成
# ============================================================
def generate_morning_news(api_key: str) -> str:
    """Claude APIで朝刊Markdownを生成する。"""
    client = anthropic.Anthropic(api_key=api_key)
    today = datetime.now(JST)
    date_label = today.strftime("%Y-%m-%d %a")

    print(f"[INFO] [Step 1/3] Generating morning news for {date_label}...", file=sys.stderr)
    print(f"[INFO] Model: {NEWS_MODEL}", file=sys.stderr)

    response = client.messages.create(
        model=NEWS_MODEL,
        max_tokens=MAX_TOKENS_NEWS,
        system=NEWS_SYSTEM_PROMPT,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 25,
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"今日（{date_label}）の朝刊を生成してください。\n\n"
                    "過去24〜72時間の海外主要紙ヘッドラインから、"
                    "メイン5本＋国内未報道スポット1本を選定し、"
                    "SKILL指示に従って完全な朝刊Markdownを出力してください。"
                ),
            }
        ],
    )

    text_parts = [b.text for b in response.content if b.type == "text"]
    markdown_body = "\n".join(text_parts)
    print(f"[INFO] Generated {len(markdown_body)} chars of markdown", file=sys.stderr)
    return markdown_body


# ============================================================
# Step 2: ラジオ台本に変換（NEW）
# ============================================================
def generate_radio_script(api_key: str, news_markdown: str) -> str:
    """朝刊Markdownをラジオ番組風の台本に変換する。"""
    client = anthropic.Anthropic(api_key=api_key)
    today = datetime.now(JST)
    date_label = today.strftime("%Y年%m月%d日 %a曜日")

    print(f"[INFO] [Step 2/3] Generating radio script...", file=sys.stderr)
    print(f"[INFO] Model: {SCRIPT_MODEL}", file=sys.stderr)

    response = client.messages.create(
        model=SCRIPT_MODEL,
        max_tokens=MAX_TOKENS_SCRIPT,
        system=SCRIPT_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"以下の朝刊Markdown（日付: {date_label}）を、ラジオ番組風の台本に変換してください。\n\n"
                    f"---\n\n{news_markdown}\n\n---\n\n"
                    "音声化するため、絵文字・記号・URL・箇条書き記号を一切使わない、"
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
# Step 3: OpenAI TTS で音声生成（NEW）
# ============================================================
def generate_audio_with_openai(api_key: str, script: str, output_path: str) -> str:
    """OpenAI TTS APIで台本をMP3に変換する。

    OpenAI TTSは1リクエストあたり最大4096文字まで。
    台本が長い場合は分割して生成し、後で結合する。

    Returns:
        生成されたMP3ファイルのパス
    """
    print(f"[INFO] [Step 3/3] Generating audio with OpenAI TTS...", file=sys.stderr)
    print(f"[INFO] TTS model: {TTS_MODEL}, voice: {TTS_VOICE}", file=sys.stderr)

    client = OpenAI(api_key=api_key)

    # 4000字を超える場合は段落単位で分割
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
    """TTS用にテキストを分割する。段落（空行）境界で分割し、自然な間を保つ。"""
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        # 現在のチャンクに追加してもmax_charsを超えないなら追加
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
# Markdown → HTML 変換（メール用）
# ============================================================
def markdown_to_email_html(md_text: str, date_label: str, has_audio: bool) -> str:
    """朝刊MarkdownをメールHTMLに変換する。"""
    html_body = markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists", "nl2br"],
    )

    audio_notice = ""
    if has_audio:
        audio_notice = """
    <div style="background:#fff8e7;border-left:4px solid #f0a500;padding:12px 16px;margin:0 0 24px 0;font-size:14px;color:#444;">
      🎧 <strong>音声版が添付されています</strong>（MP3, 約3〜5分）。通勤・ランニング中の聴取にどうぞ。
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
      <div style="font-size:22px;font-weight:bold;margin-top:4px;">海外ニュース朝刊 / {date_label}</div>
    </div>{audio_notice}
    <div style="font-size:15px;color:#222;">
      {_inject_inline_styles(html_body)}
    </div>
    <div style="margin-top:40px;padding-top:16px;border-top:1px solid #ddd;font-size:12px;color:#888;text-align:center;">
      Generated by Claude (Anthropic API) + OpenAI TTS · GitHub Actions による自動配信
    </div>
  </div>
</body>
</html>"""
    return email_html


def _inject_inline_styles(html: str) -> str:
    """生成HTMLにインラインスタイルを注入。"""
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
# Gmail SMTP送信（音声添付対応）
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
    """Gmail SMTPでHTMLメール（+ MP3添付オプション）を送信。"""
    # 添付ありの場合は mixed、なしの場合は alternative
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
    # アプリパスワードに混入する可能性のある空白類を全除去
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
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    gmail_address = os.environ.get("GMAIL_ADDRESS")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient_email = os.environ.get("RECIPIENT_EMAIL")

    # 必須変数チェック
    required = {
        "ANTHROPIC_API_KEY": anthropic_key,
        "GMAIL_ADDRESS": gmail_address,
        "GMAIL_APP_PASSWORD": gmail_app_password,
        "RECIPIENT_EMAIL": recipient_email,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print(f"[ERROR] Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    # OPENAI_API_KEY は無い場合「音声なしで送信」モードで継続
    audio_enabled = bool(openai_key)
    if not audio_enabled:
        print("[WARN] OPENAI_API_KEY not set. Sending without audio attachment.", file=sys.stderr)

    today = datetime.now(JST)
    date_label = today.strftime("%Y-%m-%d %a")

    # ----------------------------------------------------------------
    # Step 1: 朝刊Markdown生成
    # ----------------------------------------------------------------
    try:
        markdown_body = generate_morning_news(anthropic_key)
    except Exception as e:
        print(f"[ERROR] Failed to generate news: {e}", file=sys.stderr)
        markdown_body = f"# 朝刊生成エラー\n\n本日の朝刊生成に失敗しました。\n\nエラー: `{e}`\n\nGitHub Actions のログを確認してください。"
        # 朝刊生成失敗時は音声もスキップ
        audio_enabled = False

    # ----------------------------------------------------------------
    # Step 2 & 3: 台本生成 + 音声生成（オプション）
    # ----------------------------------------------------------------
    audio_path = None
    if audio_enabled:
        try:
            script = generate_radio_script(anthropic_key, markdown_body)
            audio_path = f"morning_news_{today.strftime('%Y%m%d')}.mp3"
            generate_audio_with_openai(openai_key, script, audio_path)
        except Exception as e:
            print(f"[ERROR] Audio generation failed: {e}", file=sys.stderr)
            print("[WARN] Falling back to email-only delivery.", file=sys.stderr)
            audio_path = None

    # ----------------------------------------------------------------
    # Step 4: メール送信
    # ----------------------------------------------------------------
    has_audio = audio_path is not None
    html = markdown_to_email_html(markdown_body, date_label, has_audio=has_audio)
    audio_emoji = "🎧" if has_audio else "📰"
    subject = f"{audio_emoji} 海外ニュース朝刊 / {date_label}"

    send_email_with_audio(
        sender=gmail_address,
        app_password=gmail_app_password,
        recipient=recipient_email,
        subject=subject,
        html_body=html,
        plain_body=markdown_body,
        audio_path=audio_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
