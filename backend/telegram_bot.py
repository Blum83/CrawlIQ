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
API_BASE = os.getenv("QA_API_BASE", "http://localhost:8000/api")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


async def tg_get(method: str, params: dict = None):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{TG_API}/{method}", params=params or {}, timeout=30)
        return r.json()


async def tg_post(method: str, json: dict):
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{TG_API}/{method}", json=json, timeout=30)
        return r.json()


async def send_message(chat_id: int, text: str, parse_mode: str = "Markdown"):
    await tg_post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": parse_mode})


async def start_qa_job(url: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{API_BASE}/analyze", json={"url": url, "max_pages": 100}, timeout=15)
        return r.json()["job_id"]


async def poll_job(job_id: str, timeout: int = 600) -> dict:
    async with httpx.AsyncClient() as client:
        for _ in range(timeout // 3):
            await asyncio.sleep(3)
            r = await client.get(f"{API_BASE}/status/{job_id}", timeout=10)
            data = r.json()
            if data["status"] in ("done", "error"):
                return data
    return {"status": "error", "error": "Timeout"}


def format_report(url: str, result: dict) -> str:
    issues = result.get("issues", {})
    cov = result.get("content_coverage", {})

    meta   = issues.get("missing_meta_description", {}).get("count", 0)
    h1     = issues.get("missing_h1", {}).get("count", 0)
    broken = issues.get("broken_images", {}).get("count", 0)
    alt    = issues.get("missing_alt_tags", {}).get("count", 0)
    thin   = issues.get("thin_content_under_200_words", {}).get("count", 0)
    non200 = issues.get("non_200_status", {}).get("count", 0)
    errors = result.get("error_pages", 0)

    total_issues = meta + h1 + broken + alt + thin + non200

    def fmt(val, warn=1, crit=5):
        if val == 0:   return f"✅ `{val}`"
        if val < crit: return f"⚠️ `{val}`"
        return f"🔴 `{val}`"

    # Overall health score (simple)
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
        f"🐛 *Issues Found* — total: `{total_issues}`",
        f"• Meta description missing:  {fmt(meta)}  pages",
        f"• H1 tag missing:            {fmt(h1)}  pages",
        f"• Broken images:             {fmt(broken, 1, 10)}",
        f"• Images without alt text:   {fmt(alt, 1, 10)}",
        f"• Non-200 status pages:      {fmt(non200)}",
    ]

    # Top affected URLs for critical issues
    def url_list(key, sub="urls"):
        urls = issues.get(key, {}).get(sub, [])[:3]
        if not urls:
            return []
        items = []
        for u in urls:
            u_str = u.get("url", u) if isinstance(u, dict) else u
            items.append(f"  `{u_str}`")
        return items

    meta_urls = url_list("missing_meta_description")
    h1_urls   = url_list("missing_h1")

    if meta_urls:
        lines += ["", "📝 *Pages without meta description:*"] + meta_urls
    if h1_urls:
        lines += ["", "📝 *Pages without H1:*"] + h1_urls

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
            await send_message(chat_id, f"🕷 Crawling in progress\\.\\.\\. I'll ping you when done\\.", parse_mode="MarkdownV2")
            job = await poll_job(job_id)

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
            job = await poll_job(job_id)
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
