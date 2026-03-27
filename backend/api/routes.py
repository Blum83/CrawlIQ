import os
import sys
import json
import asyncio
import threading
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
import uuid

try:
    import httpx as _httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

try:
    import redis as _redis_lib
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

from crawler.crawler import SiteCrawler
from analyzer.analyzer import analyze_page, aggregate_reports
from agent.qa_agent import generate_ai_summary
from exporter.export import export_html, export_excel, export_csv

router = APIRouter()

JOB_TTL = 2 * 60 * 60  # 2 hours

# ── Job store: Redis if REDIS_URL is set, otherwise in-memory dict ────────────

_redis_client = None
_jobs_fallback: dict[str, dict] = {}


def _get_redis():
    global _redis_client
    if _redis_client is None and _REDIS_AVAILABLE:
        url = os.getenv("REDIS_URL")
        if url:
            _redis_client = _redis_lib.from_url(url, decode_responses=True)
    return _redis_client


def job_get(job_id: str) -> dict | None:
    r = _get_redis()
    if r:
        raw = r.get(f"job:{job_id}")
        return json.loads(raw) if raw else None
    return _jobs_fallback.get(job_id)


def job_set(job_id: str, data: dict):
    r = _get_redis()
    if r:
        r.setex(f"job:{job_id}", JOB_TTL, json.dumps(data))
    else:
        _jobs_fallback[job_id] = data


def job_update(job_id: str, **kwargs):
    data = job_get(job_id) or {}
    data.update(kwargs)
    job_set(job_id, data)


class AnalyzeRequest(BaseModel):
    url: str
    max_pages: int = 50
    exclude_patterns: list[str] = []


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending | running | done | error
    progress: int = 0
    total: int = 0
    estimated_total: int = 0
    result: dict | None = None
    error: str | None = None


async def run_analysis(job_id: str, url: str, max_pages: int, exclude_patterns: list[str] = []):
    job_update(job_id, status="running")

    try:
        # 0. Resolve protocol: try https first, fall back to http if needed
        url = await _resolve_protocol(url)

        # 1. Crawl (progress updated in real-time via callback)
        def on_progress(crawled: int):
            job_update(job_id, progress=crawled)

        def on_sitemap(estimated: int):
            job_update(job_id, estimated_total=estimated)

        def cancel_check() -> bool:
            return (job_get(job_id) or {}).get("status") == "cancelled"

        crawler = SiteCrawler(url, max_pages=max_pages, on_progress=on_progress, on_sitemap=on_sitemap, exclude_patterns=exclude_patterns, cancel_check=cancel_check)
        pages, meta_files = await crawler.crawl()
        job_update(job_id, total=len(pages), progress=len(pages))

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

        job_update(job_id, status="done", result=aggregated)

    except Exception as e:
        if (job_get(job_id) or {}).get("status") != "cancelled":
            job_update(job_id, status="error", error=str(e))


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

    # If no protocol given, prefix with https:// for validation; _resolve_protocol will probe both
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url

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


async def _resolve_protocol(url: str) -> str:
    """Try https first, then http. Returns the URL with the working protocol."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") if parsed.path and parsed.path != "/" else ""
    https_url = f"https://{parsed.netloc}{path}" if path else f"https://{parsed.netloc}"
    http_url  = f"http://{parsed.netloc}{path}"  if path else f"http://{parsed.netloc}"

    if not _HTTPX_AVAILABLE:
        return https_url

    for try_url in [https_url, http_url]:
        try:
            async with _httpx.AsyncClient(timeout=8, follow_redirects=True, verify=False) as client:
                r = await client.head(try_url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code < 500:
                    return try_url
        except Exception:
            continue

    return https_url  # fallback


@router.post("/analyze", response_model=JobStatus)
async def start_analysis(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    url = _validate_url(req.url)

    job_id = str(uuid.uuid4())
    initial = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "total": 0,
        "estimated_total": 0,
        "result": None,
        "error": None,
    }
    job_set(job_id, initial)

    # Use a thread with its own ProactorEventLoop so Playwright can spawn subprocesses
    t = threading.Thread(target=_run_in_proactor_thread, args=(job_id, url, req.max_pages, req.exclude_patterns), daemon=True)
    t.start()

    return JobStatus(**initial)


@router.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    job = job_get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(**job)


def _report_filename(ext: str) -> str:
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    return f"report-{ts}.{ext}"


def _get_done_result(job_id: str) -> dict:
    job = job_get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Report not ready")
    return job["result"]


@router.get("/export/{job_id}/html")
async def export_report_html(job_id: str):
    result = _get_done_result(job_id)
    html = export_html(result.get("target_url", ""), result)
    fname = _report_filename("html")
    return HTMLResponse(content=html, headers={
        "Content-Disposition": f'attachment; filename="{fname}"'
    })


@router.get("/export/{job_id}/excel")
async def export_report_excel(job_id: str):
    result = _get_done_result(job_id)
    data = export_excel(result.get("target_url", ""), result)
    fname = _report_filename("xlsx")
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/export/{job_id}/csv")
async def export_report_csv(job_id: str):
    result = _get_done_result(job_id)
    data = export_csv(result)
    fname = _report_filename("csv")
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/cancel/{job_id}")
async def cancel_job(job_id: str):
    job = job_get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "running":
        job_update(job_id, status="cancelled")
    return {"job_id": job_id, "status": (job_get(job_id) or {}).get("status")}


@router.get("/export/{job_id}/robots")
async def export_robots(job_id: str):
    result = _get_done_result(job_id)
    meta = result.get("meta_files", {})
    content = meta.get("robots_txt_content", "")
    if not content:
        raise HTTPException(status_code=404, detail="robots.txt not found for this site")
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="robots.txt"'},
    )


@router.get("/export/{job_id}/sitemap")
async def export_sitemap(job_id: str):
    result = _get_done_result(job_id)
    meta = result.get("meta_files", {})
    raw_files: list = meta.get("sitemap_raw_files", [])
    if not raw_files:
        raise HTTPException(status_code=404, detail="sitemap not found for this site")

    if len(raw_files) == 1:
        # Single sitemap — return as-is
        _url, xml_text = raw_files[0]
        return Response(
            content=xml_text.encode("utf-8"),
            media_type="application/xml",
            headers={"Content-Disposition": 'attachment; filename="sitemap.xml"'},
        )

    # Multiple sitemaps — zip them
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, (sm_url, xml_text) in enumerate(raw_files):
            from urllib.parse import urlparse as _up
            name = _up(sm_url).path.lstrip("/").replace("/", "_") or f"sitemap_{i}.xml"
            zf.writestr(name, xml_text.encode("utf-8"))
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sitemaps.zip"'},
    )


@router.get("/health")
async def health():
    return {"status": "ok"}
