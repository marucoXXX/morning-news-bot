"""
Morning News Bot
================
毎朝、海外の主要紙から「朝刊」を生成してGmail配信する。

処理の流れ：
1. Claude API（claude-opus-4-7）に web_search ツールを与え、
   SKILL.mdの指示に基づいて朝刊5本＋国内未報道スポット1本を生成させる
2. 生成された Markdown 形式の朝刊を HTML に変換
3. Gmail SMTP で指定の宛先にメール送信

実行はGitHub Actionsから（毎日JST 7:00）。
ローカルテストも可能：環境変数を設定して `python morning_news.py` で動く。
"""

import os
import re
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import markdown


# ============================================================
# 設定
# ============================================================
MODEL = "claude-opus-4-7"
MAX_TOKENS = 16000
JST = timezone(timedelta(hours=9))


# ============================================================
# 朝刊生成プロンプト（SKILL.mdの内容を圧縮した版）
# ============================================================
SYSTEM_PROMPT = """あなたは「Global News Sharer（朝刊モード）」スキルを実行する戦略コンサルタントです。
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

{記事1の本文（背景／ポイント／日本への示唆／批判的コメント／ソース）}

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

- 必ず web_search ツールを使って最新情報を取得すること（推測で書かない）
- 1記事あたり 2〜3 回の web_search で十分。合計で 12〜18 回程度を目安に
- 検索結果のファクトに基づいて書く。創作・誇張禁止
- スポットの未報道判定は必ず日本語サイト指定検索を 2 回以上実行
- 出力は完全な Markdown 形式で、上記構造を厳密に守る
- 確認・前置きなしに、いきなり朝刊本体を出力する"""


USER_PROMPT_TEMPLATE = """今日（{date_label}）の朝刊を生成してください。

過去24〜72時間の海外主要紙ヘッドラインから、メイン5本＋国内未報道スポット1本を選定し、
SKILL指示に従って完全な朝刊Markdownを出力してください。

web_searchツールを使って最新情報を取得しながら進めてください。"""


# ============================================================
# Claude API呼び出し
# ============================================================
def generate_morning_news(api_key: str) -> str:
    """Claude APIで朝刊Markdownを生成する。

    Returns:
        Markdown形式の朝刊本文
    """
    client = anthropic.Anthropic(api_key=api_key)

    today = datetime.now(JST)
    date_label = today.strftime("%Y-%m-%d %a")

    user_prompt = USER_PROMPT_TEMPLATE.format(date_label=date_label)

    print(f"[INFO] Generating morning news for {date_label}...", file=sys.stderr)
    print(f"[INFO] Model: {MODEL}", file=sys.stderr)

    # web_search ツールを有効化してエージェント的に実行
    # max_uses は web_search の呼び出し上限（コスト管理のため）
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 25,
            }
        ],
        messages=[
            {"role": "user", "content": user_prompt}
        ],
    )

    # レスポンスから text ブロックを抽出して連結
    text_parts = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)

    markdown_body = "\n".join(text_parts)
    print(f"[INFO] Generated {len(markdown_body)} chars of markdown", file=sys.stderr)
    return markdown_body


# ============================================================
# Markdown → HTML 変換（メール用）
# ============================================================
def markdown_to_email_html(md_text: str, date_label: str) -> str:
    """朝刊MarkdownをメールHTML（インラインスタイル）に変換する。

    Gmailは <style> タグを多くの場合除去するので、すべてインラインで指定する。
    """
    # 標準のmarkdownライブラリでHTML変換
    html_body = markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists", "nl2br"],
    )

    # インラインスタイル付きのテンプレート
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
    </div>
    <div style="font-size:15px;color:#222;">
      {_inject_inline_styles(html_body)}
    </div>
    <div style="margin-top:40px;padding-top:16px;border-top:1px solid #ddd;font-size:12px;color:#888;text-align:center;">
      Generated by Claude (Anthropic API) · GitHub Actions による自動配信
    </div>
  </div>
</body>
</html>"""
    return email_html


def _inject_inline_styles(html: str) -> str:
    """生成されたHTMLの各タグに、Gmail対応のインラインスタイルを注入する。"""
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
def send_email_via_gmail(
    sender: str,
    app_password: str,
    recipient: str,
    subject: str,
    html_body: str,
    plain_body: str,
) -> None:
    """Gmail SMTP（587 / STARTTLS）でHTMLメールを送信する。"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    # text/plain と text/html の両方を入れて、HTMLが描画できないクライアントでも読める
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    print(f"[INFO] Connecting to smtp.gmail.com:587...", file=sys.stderr)
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, app_password)
        server.send_message(msg)
    print(f"[INFO] Email sent to {recipient}", file=sys.stderr)


# ============================================================
# メインエントリポイント
# ============================================================
def main() -> int:
    # 環境変数から設定を取得
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    gmail_address = os.environ.get("GMAIL_ADDRESS")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient_email = os.environ.get("RECIPIENT_EMAIL")

    missing = [
        name for name, value in [
            ("ANTHROPIC_API_KEY", api_key),
            ("GMAIL_ADDRESS", gmail_address),
            ("GMAIL_APP_PASSWORD", gmail_app_password),
            ("RECIPIENT_EMAIL", recipient_email),
        ] if not value
    ]
    if missing:
        print(f"[ERROR] Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    # 朝刊生成
    today = datetime.now(JST)
    date_label = today.strftime("%Y-%m-%d %a")

    try:
        markdown_body = generate_morning_news(api_key)
    except Exception as e:
        print(f"[ERROR] Failed to generate news: {e}", file=sys.stderr)
        # 失敗時はエラーメールを送る
        markdown_body = f"# 朝刊生成エラー\n\n本日の朝刊生成に失敗しました。\n\nエラー: `{e}`\n\nGitHub Actions のログを確認してください。"

    # メール変換
    html = markdown_to_email_html(markdown_body, date_label)
    subject = f"📰 海外ニュース朝刊 / {date_label}"

    # Plain版（HTMLが効かないクライアント向け）はMarkdownそのまま
    send_email_via_gmail(
        sender=gmail_address,
        app_password=gmail_app_password,
        recipient=recipient_email,
        subject=subject,
        html_body=html,
        plain_body=markdown_body,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
