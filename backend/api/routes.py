import sys
import asyncio
import threading
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uuid

from crawler.crawler import SiteCrawler
from analyzer.analyzer import analyze_page, aggregate_reports
from agent.qa_agent import generate_ai_summary

router = APIRouter()

# In-memory job store (use Redis in production)
jobs: dict[str, dict] = {}


class AnalyzeRequest(BaseModel):
    url: str
    max_pages: int = 50


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending | running | done | error
    progress: int = 0
    total: int = 0
    result: dict | None = None
    error: str | None = None


async def run_analysis(job_id: str, url: str, max_pages: int):
    jobs[job_id]["status"] = "running"

    try:
        # 1. Crawl (progress updated in real-time via callback)
        def on_progress(crawled: int):
            jobs[job_id]["progress"] = crawled

        crawler = SiteCrawler(url, max_pages=max_pages, on_progress=on_progress)
        pages = await crawler.crawl()
        jobs[job_id]["total"] = len(pages)
        jobs[job_id]["progress"] = len(pages)

        # 2. Analyze each page
        domain = urlparse(url).netloc
        page_reports = [analyze_page(p, domain) for p in pages]

        # 3. Aggregate
        aggregated = aggregate_reports(page_reports)

        # 4. AI summary (optional — uses Groq or Gemini if key is set)
        aggregated["ai_summary"] = await generate_ai_summary(url, aggregated)
        aggregated["target_url"] = url

        jobs[job_id]["status"] = "done"
        jobs[job_id]["result"] = aggregated

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


def _run_in_proactor_thread(job_id: str, url: str, max_pages: int):
    """Run analysis in a thread with its own ProactorEventLoop (Windows fix)."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_analysis(job_id, url, max_pages))
    finally:
        loop.close()


@router.post("/analyze", response_model=JobStatus)
async def start_analysis(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    parsed = urlparse(req.url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "total": 0,
        "result": None,
        "error": None,
    }

    # Use a thread with its own ProactorEventLoop so Playwright can spawn subprocesses
    t = threading.Thread(target=_run_in_proactor_thread, args=(job_id, req.url, req.max_pages), daemon=True)
    t.start()

    return JobStatus(**jobs[job_id])


@router.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(**jobs[job_id])


@router.get("/health")
async def health():
    return {"status": "ok"}
