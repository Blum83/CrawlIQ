import sys
import asyncio
import threading
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
import uuid

from crawler.crawler import SiteCrawler
from analyzer.analyzer import analyze_page, aggregate_reports
from agent.qa_agent import generate_ai_summary
from exporter.export import export_html, export_excel, export_csv

router = APIRouter()

# In-memory job store (use Redis in production)
jobs: dict[str, dict] = {}


class AnalyzeRequest(BaseModel):
    url: str
    max_pages: int = 50
    exclude_patterns: list[str] = []


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending | running | done | error
    progress: int = 0
    total: int = 0
    result: dict | None = None
    error: str | None = None


async def run_analysis(job_id: str, url: str, max_pages: int, exclude_patterns: list[str] = []):
    jobs[job_id]["status"] = "running"

    try:
        # 1. Crawl (progress updated in real-time via callback)
        def on_progress(crawled: int):
            jobs[job_id]["progress"] = crawled

        crawler = SiteCrawler(url, max_pages=max_pages, on_progress=on_progress, exclude_patterns=exclude_patterns)
        pages, meta_files = await crawler.crawl()
        jobs[job_id]["total"] = len(pages)
        jobs[job_id]["progress"] = len(pages)

        # 2. Analyze each page
        domain = urlparse(url).netloc
        page_reports = [analyze_page(p, domain) for p in pages]

        # 3. Aggregate
        aggregated = aggregate_reports(page_reports)

        # 4. Attach meta files (robots.txt / sitemap)
        aggregated["meta_files"] = meta_files

        # 5. AI summary (optional — uses Groq or Gemini if key is set)
        aggregated["ai_summary"] = await generate_ai_summary(url, aggregated)
        aggregated["target_url"] = url

        jobs[job_id]["status"] = "done"
        jobs[job_id]["result"] = aggregated

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


def _run_in_proactor_thread(job_id: str, url: str, max_pages: int, exclude_patterns: list[str]):
    """Run analysis in a thread with its own ProactorEventLoop (Windows fix)."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_analysis(job_id, url, max_pages, exclude_patterns))
    finally:
        loop.close()


def _validate_url(url: str) -> str:
    """Validate and sanitize URL. Returns cleaned URL or raises HTTPException."""
    url = url.strip()

    # Must start with http:// or https://
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    parsed = urlparse(url)

    # Must have a real hostname
    if not parsed.netloc or "." not in parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL: missing or malformed hostname")

    # Hostname must contain only valid characters
    import re
    if not re.match(r'^[a-zA-Z0-9._\-\[\]:]+$', parsed.netloc):
        raise HTTPException(status_code=400, detail="Invalid URL: hostname contains illegal characters")

    # Block private/local networks
    hostname = parsed.hostname or ""
    blocked_hosts = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
    if hostname in blocked_hosts or hostname.startswith("192.168.") or hostname.startswith("10.") or hostname.startswith("172."):
        raise HTTPException(status_code=400, detail="Scanning local/private networks is not allowed")

    # Limit URL length
    if len(url) > 500:
        raise HTTPException(status_code=400, detail="URL is too long (max 500 characters)")

    # Return only scheme + netloc + path (strip query/fragment injections)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/") or f"{parsed.scheme}://{parsed.netloc}"
    return clean


@router.post("/analyze", response_model=JobStatus)
async def start_analysis(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    url = _validate_url(req.url)

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
    t = threading.Thread(target=_run_in_proactor_thread, args=(job_id, url, req.max_pages, req.exclude_patterns), daemon=True)
    t.start()

    return JobStatus(**jobs[job_id])


@router.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(**jobs[job_id])


@router.get("/export/{job_id}/html")
async def export_report_html(job_id: str):
    if job_id not in jobs or jobs[job_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not ready")
    result = jobs[job_id]["result"]
    html = export_html(result.get("target_url", ""), result)
    return HTMLResponse(content=html, headers={
        "Content-Disposition": f'attachment; filename="qa-report-{job_id[:8]}.html"'
    })


@router.get("/export/{job_id}/excel")
async def export_report_excel(job_id: str):
    if job_id not in jobs or jobs[job_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not ready")
    result = jobs[job_id]["result"]
    data = export_excel(result.get("target_url", ""), result)
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="qa-report-{job_id[:8]}.xlsx"'})


@router.get("/export/{job_id}/csv")
async def export_report_csv(job_id: str):
    if job_id not in jobs or jobs[job_id]["status"] != "done":
        raise HTTPException(status_code=404, detail="Report not ready")
    result = jobs[job_id]["result"]
    data = export_csv(result)
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="qa-pages-{job_id[:8]}.csv"'})


@router.get("/health")
async def health():
    return {"status": "ok"}
