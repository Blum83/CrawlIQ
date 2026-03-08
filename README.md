# AI QA Agent

Automated website quality analysis powered by AI (Claude). Crawls any website and generates a structured QA report covering SEO, accessibility, content quality, and more.

## Features

- **Crawler** — Playwright-based headless browser crawl (respects domain boundaries)
- **Analyzer** — Checks meta descriptions, H1 tags, broken images, alt tags, word count, HTTP status codes
- **AI Summary** — Claude generates an actionable QA report from the raw data
- **Frontend** — Clean dark-mode UI with live progress tracking

## Quick Start (local)

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Set API key
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Run
python main.py
```

Open http://localhost:8000

## Quick Start (Docker)

```bash
cp backend/.env.example .env
# Add ANTHROPIC_API_KEY to .env

docker-compose up --build
```

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analyze` | Start analysis job |
| GET | `/api/status/{job_id}` | Poll job status & result |
| GET | `/api/health` | Health check |

### POST /api/analyze

```json
{
  "url": "https://example.com",
  "max_pages": 30
}
```

### Response (when done)

```json
{
  "job_id": "...",
  "status": "done",
  "result": {
    "total_pages": 42,
    "pages_crawled": 40,
    "error_pages": 2,
    "issues": {
      "missing_meta_description": {"count": 12, "urls": [...]},
      "missing_h1": {"count": 3, "urls": [...]},
      "broken_images": {"count": 2, "pages": [...]},
      "missing_alt_tags": {"count": 8, "pages": [...]},
      "thin_content_under_200_words": {"count": 13, "urls": [...]}
    },
    "content_coverage": {
      "avg_word_count": 340,
      "pct_thin_content": 30.0
    },
    "ai_summary": "...",
    "page_details": [...]
  }
}
```

## Architecture

```
Frontend (HTML/JS)
      |
FastAPI (async REST)
      |
SiteCrawler (Playwright)
      |
PageAnalyzer (BeautifulSoup)
      |
AI Agent (Claude claude-sonnet-4-6)
      |
JSON Report
```

## Stack

- **Python 3.12** + **FastAPI** — async API with background jobs
- **Playwright** — headless Chromium crawling
- **BeautifulSoup4** — HTML parsing
- **Anthropic SDK** — Claude AI summaries
- **Docker** — containerized deployment
