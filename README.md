# CrawlIQ — AI Website Auditor

**Telegram Bot:** [@aiqa_agent_bot](https://t.me/aiqa_agent_bot)

AI-powered website audit tool. Crawls any site and generates a structured report covering SEO, accessibility, content quality, performance, and technical issues — with optional AI summary.

## Features

- **Crawler** — Playwright headless browser, up to 600 pages, real-time progress, JS rendering detection
- **Analyzer** — 20+ checks across SEO, accessibility, content, performance, and URL quality
- **Indexability** — noindex detection, canonicalized-away pages
- **Performance** — load time per page, crawl depth, redirect chains, JS-dependent pages
- **Open Graph & Schema** — og:title/image presence, JSON-LD / Schema.org detection
- **AI Summary** — Groq (Llama3) or Gemini generates an actionable summary; hidden if no key set
- **Export** — HTML, Excel (multi-sheet), CSV
- **Frontend** — Dark-mode UI, SEO score, filterable page table, exclude URL patterns
- **Telegram Bot** — Send a URL, get a full report with live progress updates

## Checks

| Category | Checks |
|----------|--------|
| Indexability | Noindex meta, canonicalized-away pages |
| SEO | Missing/duplicate title, title length, missing/duplicate meta description, meta length, missing H1, multiple H1, missing canonical, duplicate titles |
| Accessibility | Missing HTML lang, images without alt text, buttons without accessible label |
| Content | Thin content (<200 words), empty pages |
| Performance | Slow pages (>3s), avg load time, deep pages (>3 clicks), redirects, JS-dependent pages |
| Open Graph | Missing og:title or og:image |
| Structured Data | Missing JSON-LD / Schema.org |
| URL Quality | Uppercase letters, underscores, path too long |
| Technical | Broken images, non-200 status, robots.txt, sitemap.xml |

## Quick Start (local)

```bash
cd backend

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Edit .env — add GROQ_API_KEY or GEMINI_API_KEY (optional)
# Add TELEGRAM_BOT_TOKEN (optional)

python main.py
```

Open http://localhost:8000

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | No | Groq API key for Llama3 AI summary — [console.groq.com](https://console.groq.com) (free) |
| `GEMINI_API_KEY` | No | Gemini API key fallback — [aistudio.google.com](https://aistudio.google.com) (free) |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token from @BotFather |
| `QA_API_BASE` | No | Internal API URL for bot (default: `http://localhost:$PORT/api`) |
| `PORT` | No | Server port (default: 8000) |

> **Never commit `.env` to git.** Use `.env.example` as a template.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analyze` | Start analysis job |
| GET | `/api/status/{job_id}` | Poll job status & result |
| POST | `/api/cancel/{job_id}` | Cancel running job |
| GET | `/api/export/{job_id}/html` | Download HTML report |
| GET | `/api/export/{job_id}/excel` | Download Excel report (multi-sheet) |
| GET | `/api/export/{job_id}/csv` | Download CSV (page details) |
| GET | `/api/health` | Health check |

### POST /api/analyze

```json
{
  "url": "https://example.com",
  "max_pages": 600,
  "exclude_patterns": ["/tag/", "/page/", "/author/"]
}
```

## Architecture

```
Frontend (HTML/JS)  ←→  Telegram Bot
         \               /
          FastAPI (async REST)
               |
        SiteCrawler (Playwright + httpx JS check)
               |
        PageAnalyzer (BeautifulSoup)
               |
        AI Agent (Groq → Gemini → none)
               |
        Exporter (HTML / Excel / CSV)
```

## Stack

- **Python 3.12** + **FastAPI** — async API with background jobs
- **Playwright** — headless Chromium crawling
- **httpx** — plain HTTP fetch for JS rendering comparison
- **BeautifulSoup4** — HTML parsing
- **Groq / Gemini** — optional AI summaries via httpx
- **openpyxl** — Excel export
- **Docker** — containerized deployment on Railway
