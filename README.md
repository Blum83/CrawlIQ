# AI QA Agent

Automated website quality analysis. Crawls any website and generates a structured QA report covering SEO, accessibility, content quality, and technical issues — with optional AI summary.

## Features

- **Crawler** — Playwright headless browser, up to 200 pages, real-time progress
- **Analyzer** — SEO, accessibility, content, and technical checks (see full list below)
- **AI Summary** — Groq (Llama3) or Gemini generates an actionable summary; deterministic fallback if no key set
- **Export** — Download report as HTML, Excel (multi-sheet), or CSV
- **Frontend** — Dark-mode UI with live progress bar
- **Telegram Bot** — Send a URL, get a full report with progress updates

## Checks

| Category | Checks |
|----------|--------|
| SEO | Missing title, missing meta description, missing H1, multiple H1, missing canonical, duplicate titles |
| Accessibility | Missing HTML lang, images without alt text, buttons without accessible label |
| Content | Thin content (<200 words), empty pages, avg word count |
| Technical | Broken images, non-200 status pages, error pages |

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
| `GROQ_API_KEY` | No | Groq API key for Llama3 AI summary (free tier at console.groq.com) |
| `GEMINI_API_KEY` | No | Gemini API key fallback |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token |
| `QA_API_BASE` | No | Internal API URL for bot (default: `http://localhost:$PORT/api`) |
| `PORT` | No | Server port (default: 8000) |

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analyze` | Start analysis job |
| GET | `/api/status/{job_id}` | Poll job status & result |
| GET | `/api/export/{job_id}/html` | Download HTML report |
| GET | `/api/export/{job_id}/excel` | Download Excel report (multi-sheet) |
| GET | `/api/export/{job_id}/csv` | Download CSV (page details) |
| GET | `/api/health` | Health check |

### POST /api/analyze

```json
{ "url": "https://example.com", "max_pages": 200 }
```

### Response (when done)

```json
{
  "status": "done",
  "result": {
    "pages_crawled": 80,
    "issues": {
      "missing_meta_description": {"count": 12, "urls": [...]},
      "missing_h1": {"count": 3, "urls": [...]},
      "missing_canonical": {"count": 53, "urls": [...]},
      "duplicate_titles": {"count": 8, "urls": [...]},
      "missing_html_lang": {"count": 0, "urls": []},
      "missing_alt_tags": {"count": 230, "pages": [...]},
      "buttons_missing_label": {"count": 12, "pages": [...]},
      "thin_content_under_200_words": {"count": 7, "urls": [...]},
      "non_200_status": {"count": 1, "pages": [{"url": "...", "status": 404}]}
    },
    "content_coverage": {"avg_word_count": 950, "pct_thin_content": 8.8},
    "ai_summary": "...",
    "page_details": [...]
  }
}
```

## Architecture

```
Frontend (HTML/JS)  ←→  Telegram Bot
         \               /
          FastAPI (async REST)
               |
        SiteCrawler (Playwright)
               |
        PageAnalyzer (BeautifulSoup)
               |
        AI Agent (Groq → Gemini → fallback)
               |
        Exporter (HTML / Excel / CSV)
```

## Stack

- **Python 3.12** + **FastAPI** — async API with background jobs
- **Playwright** — headless Chromium crawling
- **BeautifulSoup4** — HTML parsing
- **Groq / Gemini** — optional AI summaries
- **openpyxl** — Excel export
- **python-telegram-bot** (httpx polling) — Telegram bot
- **Docker** — containerized deployment on Railway
