"""
Telegram bot for AI QA Agent.
Commands:
  /start   - welcome
  /analyze <url> - start QA analysis
  /help    - usage
"""

import os
import asyncio
import sys
import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API_BASE = os.getenv("QA_API_BASE") or f"http://localhost:{os.getenv('PORT') or '8000'}/api"

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
print(f"[Bot] API_BASE = {API_BASE}", flush=True)


async def tg_get(method: str, params: dict = None):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{TG_API}/{method}", params=params or {}, timeout=30)
        return r.json()


async def tg_post(method: str, json: dict):
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{TG_API}/{method}", json=json, timeout=30)
        return r.json()


async def send_message(chat_id: int, text: str, parse_mode: str = "Markdown"):
    """Send message, splitting into multiple if it exceeds Telegram's 4096 char limit."""
    limit = 4000  # leave headroom for safety
    if len(text) <= limit:
        await tg_post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": parse_mode})
        return

    # Split by lines, accumulate chunks
    lines = text.split("\n")
    chunk = []
    chunk_len = 0
    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if chunk_len + line_len > limit and chunk:
            await tg_post("sendMessage", {"chat_id": chat_id, "text": "\n".join(chunk), "parse_mode": parse_mode})
            chunk = []
            chunk_len = 0
        chunk.append(line)
        chunk_len += line_len
    if chunk:
        await tg_post("sendMessage", {"chat_id": chat_id, "text": "\n".join(chunk), "parse_mode": parse_mode})


async def start_qa_job(url: str) -> str:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(f"{API_BASE}/analyze", json={"url": url, "max_pages": 600}, timeout=15)
                r.raise_for_status()
                return r.json()["job_id"]
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(3)
            else:
                raise


async def poll_job(job_id: str, chat_id: int, timeout: int = 3600) -> dict:
    progress_interval = 5  # send update every 5 seconds
    elapsed = 0
    last_progress = -1

    async with httpx.AsyncClient() as client:
        while elapsed < timeout:
            await asyncio.sleep(3)
            elapsed += 3

            r = await client.get(f"{API_BASE}/status/{job_id}", timeout=10)
            data = r.json()

            if data["status"] in ("done", "error"):
                return data

            # Send progress update every 30s
            if elapsed % progress_interval == 0:
                crawled = data.get("progress", 0)
                total = data.get("total", 0)
                if crawled != last_progress:
                    last_progress = crawled
                    pct = int(crawled / 600 * 100) if crawled else 0
                    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                    msg = f"⏳ Still crawling... `{crawled}/600` pages `[{bar}]`"
                    try:
                        await send_message(chat_id, msg)
                    except Exception:
                        pass

    return {"status": "error", "error": "Timeout"}


def format_report(url: str, result: dict) -> str:
    issues = result.get("issues", {})
    cov = result.get("content_coverage", {})

    def cnt(key):
        return issues.get(key, {}).get("count", 0)

    # counts
    missing_title  = cnt("missing_title")
    meta           = cnt("missing_meta_description")
    h1             = cnt("missing_h1")
    multi_h1       = cnt("multiple_h1")
    canonical      = cnt("missing_canonical")
    dup_titles     = cnt("duplicate_titles")
    lang           = cnt("missing_html_lang")
    alt            = cnt("missing_alt_tags")
    buttons        = cnt("buttons_missing_label")
    broken         = cnt("broken_images")
    thin           = cnt("thin_content_under_200_words")
    empty          = cnt("empty_pages")
    non200         = cnt("non_200_status")
    errors         = result.get("error_pages", 0)

    total_issues = missing_title + meta + h1 + multi_h1 + canonical + dup_titles + lang + alt + buttons + broken + thin + empty + non200

    def fmt(val, warn=1, crit=5):
        if val == 0:   return f"✅ `{val}`"
        if val < crit: return f"⚠️ `{val}`"
        return f"🔴 `{val}`"

    pages = result.get("pages_crawled", 1) or 1
    score = max(0, 100 - int(total_issues / pages * 100))
    if score >= 80:   health = f"🟢 Good ({score}/100)"
    elif score >= 50: health = f"🟡 Needs work ({score}/100)"
    else:             health = f"🔴 Poor ({score}/100)"

    lines = [
        f"🔍 *QA Report*",
        f"🌐 {url}",
        f"",
        f"📊 *Overview*",
        f"• Pages crawled: `{result.get('pages_crawled', 0)}`  |  Errors: `{errors}`",
        f"• Avg word count: `{cov.get('avg_word_count', 0)}` words/page",
        f"• Thin content pages: `{thin}` ({cov.get('pct_thin_content', 0)}%)",
        f"• Overall health: {health}",
        f"",
        f"🐛 *Issues* — total: `{total_issues}`",
        f"",
        f"*SEO*",
        f"• Title missing:         {fmt(missing_title)}",
        f"• Meta desc missing:     {fmt(meta)}  pages",
        f"• H1 missing:            {fmt(h1)}  pages",
        f"• Multiple H1:           {fmt(multi_h1)}",
        f"• Canonical missing:     {fmt(canonical, 5, 20)}",
        f"• Duplicate titles:      {fmt(dup_titles)}",
        f"",
        f"*Accessibility*",
        f"• HTML lang missing:     {fmt(lang, 1, 3)}",
        f"• Images without alt:    {fmt(alt, 1, 10)}",
        f"• Buttons w/o label:     {fmt(buttons)}",
        f"",
        f"*Content & Technical*",
        f"• Thin content pages:    {fmt(thin, 3, 10)}",
        f"• Empty pages:           {fmt(empty)}",
        f"• Broken images:         {fmt(broken, 1, 10)}",
        f"• Non-200 pages:         {fmt(non200)}",
    ]

    def url_list(key, sub="urls"):
        urls = issues.get(key, {}).get(sub, [])[:10]
        if not urls:
            return []
        return [f"  `{u.get('url', u) if isinstance(u, dict) else u}`" for u in urls]

    non200_pages = issues.get("non_200_status", {}).get("pages", [])[:10]
    non200_items = [f"  `{p.get('url', '')}` — *{p.get('status', '?')}*" for p in non200_pages]

    for label, items in [
        ("📝 *Pages without title:*",            url_list("missing_title")),
        ("📝 *Pages without meta description:*", url_list("missing_meta_description")),
        ("📝 *Pages without H1:*",               url_list("missing_h1")),
        ("📝 *Pages with multiple H1:*",         url_list("multiple_h1")),
        ("📝 *Duplicate titles:*",               url_list("duplicate_titles")),
        ("🚫 *Non-200 pages:*",                  non200_items),
    ]:
        if items:
            lines += ["", label] + items

    ai_summary = result.get("ai_summary", "")
    if ai_summary:
        summary_lines = ai_summary.strip().split("\n")[:15]
        lines += ["", "🤖 *AI Summary:*", "```", "\n".join(summary_lines), "```"]

    return "\n".join(lines)


async def handle_update(update: dict):
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return

    if text.startswith("/start") or text.startswith("/help"):
        await send_message(chat_id, (
            "👋 *AI QA Agent Bot*\n\n"
            "Send me a URL to analyze:\n"
            "`/analyze https://example.com`\n\n"
            "I'll crawl the site and generate a full QA report."
        ))
        return

    if text.startswith("/analyze"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await send_message(chat_id, "Usage: `/analyze https://example.com`")
            return

        url = parts[1].strip()
        if not url.startswith("http"):
            url = "https://" + url

        await send_message(chat_id, f"⏳ Starting analysis of `{url}`\\.\\.\\.", parse_mode="MarkdownV2")

        try:
            job_id = await start_qa_job(url)
            await send_message(chat_id, f"🕷 Crawling up to 200 pages\\.\\.\\. I'll send updates every 30s\\.", parse_mode="MarkdownV2")
            job = await poll_job(job_id, chat_id)

            if job["status"] == "error":
                await send_message(chat_id, f"❌ Error: {job.get('error', 'Unknown')}")
            else:
                report = format_report(url, job["result"])
                await send_message(chat_id, report)
        except Exception as e:
            await send_message(chat_id, f"❌ Failed: {e}")
        return

    # Plain URL without command
    if text.startswith("http") or "." in text:
        url = text if text.startswith("http") else "https://" + text
        await send_message(chat_id, f"⏳ Analyzing `{url}`\\.\\.\\.", parse_mode="MarkdownV2")
        try:
            job_id = await start_qa_job(url)
            job = await poll_job(job_id, chat_id)
            if job["status"] == "error":
                await send_message(chat_id, f"❌ Error: {job.get('error', 'Unknown')}")
            else:
                report = format_report(url, job["result"])
                await send_message(chat_id, report)
        except Exception as e:
            await send_message(chat_id, f"❌ Failed: {e}")
        return

    await send_message(chat_id, "Send me a URL or use `/analyze https://example.com`")


async def run_bot():
    print("Bot started. Polling for updates...")
    offset = 0
    while True:
        try:
            data = await tg_get("getUpdates", {"offset": offset, "timeout": 30, "limit": 10})
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                asyncio.create_task(handle_update(update))
        except Exception as e:
            print(f"Polling error: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_bot())
