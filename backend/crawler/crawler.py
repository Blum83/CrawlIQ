import asyncio
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

MAX_HTTPX_CONCURRENT    = 15   # parallel httpx fetches
MAX_PLAYWRIGHT_CONCURRENT = 3  # parallel Playwright pages (RAM-heavy)
MAX_PLAYWRIGHT_SPA      = 50   # max JS-rendered pages to re-render
MAX_PLAYWRIGHT_PERF     = 10   # pages to capture performance.timing on
JS_WORD_THRESHOLD       = 30   # body words below this → likely JS-rendered


class SiteCrawler:
    def __init__(self, base_url: str, max_pages: int = 50, on_progress=None,
                 on_sitemap=None, exclude_patterns: list[str] | None = None, cancel_check=None):
        self.base_url   = base_url.rstrip("/")
        self.domain     = urlparse(base_url).netloc
        self.max_pages  = max_pages
        self.visited:   set[str]   = set()
        self.pages:     list[dict] = []
        self._page_index: dict[str, int] = {}   # normalized_url → index in self.pages
        self._on_progress  = on_progress
        self._on_sitemap   = on_sitemap
        self._cancel_check = cancel_check
        self._exclude = [p.strip() for p in (exclude_patterns or []) if p.strip()]

    # ── helpers ────────────────────────────────────────────────────────────────

    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.domain

    def _is_excluded(self, url: str) -> bool:
        path = urlparse(url).path
        return any(pat in path for pat in self._exclude)

    def _normalize(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    def _is_asset(self, url: str) -> bool:
        return any(url.lower().endswith(ext)
                   for ext in (".pdf", ".jpg", ".jpeg", ".png", ".gif",
                               ".webp", ".svg", ".zip", ".css", ".js"))

    # ── sitemap / robots ───────────────────────────────────────────────────────

    async def _parse_sitemap(self, client, sitemap_url: str, depth: int = 0,
                             _raw_store: list | None = None) -> list[str]:
        if depth > 3:
            return []
        try:
            r = await client.get(sitemap_url, timeout=15)
            if r.status_code != 200:
                return []
            if _raw_store is not None:
                _raw_store.append((sitemap_url, r.text))
            root = ET.fromstring(r.text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            child_sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)
            if child_sitemaps:
                all_urls: list[str] = []
                for loc in child_sitemaps[:20]:
                    child_urls = await self._parse_sitemap(client, loc.text.strip(), depth + 1, _raw_store)
                    all_urls.extend(child_urls)
                return all_urls
            return [loc.text.strip() for loc in root.findall(".//sm:loc", ns) if loc.text]
        except Exception:
            return []

    async def fetch_meta_files(self) -> dict:
        parsed = urlparse(self.base_url)
        base   = f"{parsed.scheme}://{parsed.netloc}"
        result = {
            "robots_txt_exists": False,
            "sitemap_exists": False,
            "sitemap_urls": [],
            "sitemap_all_urls": [],
            "robots_disallows": [],
        }
        if not _HTTPX_AVAILABLE:
            return result
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                                         headers={"User-Agent": "Googlebot/2.1"}) as client:
                # robots.txt
                try:
                    r = await client.get(f"{base}/robots.txt")
                    if r.status_code == 200 and "text" in r.headers.get("content-type", "text"):
                        result["robots_txt_exists"] = True
                        result["robots_txt_content"] = r.text
                        disallows, sitemap_hints = [], []
                        for line in r.text.splitlines():
                            line = line.strip()
                            if line.lower().startswith("disallow:"):
                                val = line[len("disallow:"):].strip()
                                if val:
                                    disallows.append(val)
                            elif line.lower().startswith("sitemap:"):
                                val = line[len("sitemap:"):].strip()
                                if val:
                                    sitemap_hints.append(val)
                        result["robots_disallows"] = disallows
                        result["_sitemap_hints"]   = sitemap_hints
                except Exception:
                    pass

                # sitemap.xml
                sitemap_candidates = [f"{base}/sitemap.xml"] + result.pop("_sitemap_hints", [])
                all_sitemap_urls: list[str] = []
                sitemap_raw_files: list[tuple[str, str]] = []
                for candidate in sitemap_candidates:
                    urls = await self._parse_sitemap(client, candidate, _raw_store=sitemap_raw_files)
                    if urls:
                        result["sitemap_exists"] = True
                        all_sitemap_urls.extend(urls)

                result["sitemap_raw_files"] = sitemap_raw_files
                seen: set[str] = set()
                unique: list[str] = []
                for u in all_sitemap_urls:
                    if u not in seen:
                        seen.add(u)
                        unique.append(u)
                result["sitemap_all_urls"] = unique
                result["sitemap_urls"]     = unique[:5]
        except Exception:
            pass
        return result

    # ── main entry ────────────────────────────────────────────────────────────

    async def crawl(self) -> tuple[list[dict], dict]:
        meta_files    = await self.fetch_meta_files()
        sitemap_seeds = [
            u for u in meta_files.get("sitemap_all_urls", [])
            if self._is_same_domain(u) and not self._is_excluded(u)
        ]

        if self._on_sitemap and sitemap_seeds:
            self._on_sitemap(min(len(sitemap_seeds), self.max_pages))

        # Phase 1 — fast httpx crawl
        await self._httpx_phase(sitemap_seeds)

        # Phase 2 — Playwright for JS pages + perf sample
        js_urls   = [p["url"] for p in self.pages if p.get("js_dependent")][:MAX_PLAYWRIGHT_SPA]
        perf_urls = [p["url"] for p in self.pages[:MAX_PLAYWRIGHT_PERF]]
        # deduplicate, preserve order
        playwright_urls: list[str] = list(dict.fromkeys(js_urls + perf_urls))

        if playwright_urls:
            await self._playwright_phase(playwright_urls)

        return self.pages, meta_files

    # ── Phase 1: httpx ────────────────────────────────────────────────────────

    async def _httpx_phase(self, sitemap_seeds: list[str]):
        queued: set[str] = set()
        active_tasks: set[asyncio.Task] = set()
        link_queue: asyncio.Queue = asyncio.Queue()

        def maybe_enqueue(url: str, depth: int):
            norm = self._normalize(url)
            if (norm not in queued
                    and norm not in self.visited
                    and not self._is_excluded(url)
                    and not self._is_asset(url)
                    and len(queued) + len(self.pages) < self.max_pages):
                queued.add(norm)
                link_queue.put_nowait((url, depth))

        maybe_enqueue(self.base_url, 0)
        for u in sitemap_seeds:
            maybe_enqueue(u, 1)

        sem = asyncio.Semaphore(MAX_HTTPX_CONCURRENT)

        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AIQABot/1.0)"},
            verify=False,
        ) as client:

            async def run_fetch(url: str, depth: int):
                async with sem:
                    new_links = await self._httpx_fetch(client, url, depth)
                    for link_url, link_depth in new_links:
                        maybe_enqueue(link_url, link_depth)

            while not link_queue.empty() or active_tasks:
                if self._cancel_check and self._cancel_check():
                    break

                while (not link_queue.empty()
                       and len(active_tasks) < MAX_HTTPX_CONCURRENT * 2
                       and len(self.pages) < self.max_pages):
                    url, depth = link_queue.get_nowait()
                    task = asyncio.create_task(run_fetch(url, depth))
                    active_tasks.add(task)
                    task.add_done_callback(active_tasks.discard)

                if active_tasks:
                    await asyncio.wait(active_tasks,
                                       return_when=asyncio.FIRST_COMPLETED,
                                       timeout=0.1)

    async def _httpx_fetch(self, client: "httpx.AsyncClient",
                           url: str, depth: int) -> list[tuple[str, int]]:
        """Fetch one URL via httpx. Returns list of (discovered_url, depth) pairs."""
        if len(self.pages) >= self.max_pages:
            return []
        norm = self._normalize(url)
        if norm in self.visited:
            return []
        self.visited.add(norm)

        try:
            t0 = time.monotonic()
            resp = await client.get(url, timeout=20)
            elapsed_ms = int((time.monotonic() - t0) * 1000)

            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type:
                return []

            final_url  = str(resp.url)
            is_redirect = self._normalize(final_url) != norm

            html = resp.text
            soup = BeautifulSoup(html, "html.parser")

            # JS detection: count visible words in body
            body = soup.find("body")
            if body:
                for tag in body(["script", "style"]):
                    tag.decompose()
                raw_text = body.get_text(separator=" ")
            else:
                raw_text = soup.get_text(separator=" ")
            word_count  = len([w for w in raw_text.split() if w.strip()])
            js_dependent = word_count < JS_WORD_THRESHOLD

            page_data = {
                "url":           url,
                "html":          html,
                "status_code":   resp.status_code,
                "error":         None,
                "load_time_ms":  elapsed_ms,   # server response time (no JS)
                "ttfb_ms":       None,          # filled by Playwright phase
                "dom_ready_ms":  None,
                "crawl_depth":   depth,
                "is_redirect":   is_redirect,
                "redirect_to":   final_url if is_redirect else "",
                "js_dependent":  js_dependent,
                "plain_word_count": word_count,
            }

            idx = len(self.pages)
            self.pages.append(page_data)
            self._page_index[norm] = idx

            if self._on_progress:
                self._on_progress(len(self.pages))

            # Always try link discovery — even JS-rendered pages often have
            # navigation <a href> tags in the raw HTML (Next.js, Nuxt, etc.)
            if depth >= 10:
                return []

            links = []
            for tag in soup.find_all("a", href=True):
                full = urljoin(url, tag["href"])
                if self._is_same_domain(full) and not self._is_asset(full):
                    links.append((full, depth + 1))

            return links[:50]

        except Exception as e:
            self.pages.append({
                "url":           url,
                "html":          "",
                "status_code":   0,
                "error":         str(e),
                "load_time_ms":  0,
                "ttfb_ms":       None,
                "dom_ready_ms":  None,
                "crawl_depth":   depth,
                "is_redirect":   False,
                "redirect_to":   "",
                "js_dependent":  False,
                "plain_word_count": 0,
            })
            if self._on_progress:
                self._on_progress(len(self.pages))
            return []

    # ── Phase 2: Playwright ────────────────────────────────────────────────────

    async def _playwright_phase(self, urls: list[str]):
        """Re-fetch JS pages + capture performance.timing for perf sample."""
        from playwright.async_api import async_playwright

        perf_set = set(urls[:MAX_PLAYWRIGHT_PERF])
        sem = asyncio.Semaphore(MAX_PLAYWRIGHT_CONCURRENT)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--mute-audio",
                    "--no-first-run",
                    "--blink-settings=imagesEnabled=false",
                ],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (compatible; AIQABot/1.0)",
                java_script_enabled=True,
            )

            tasks = [self._playwright_fetch(context, sem, url, url in perf_set) for url in urls]
            await asyncio.gather(*tasks)

            await context.close()
            await browser.close()

    async def _playwright_fetch(self, context, sem: asyncio.Semaphore,
                                url: str, capture_perf: bool):
        if self._cancel_check and self._cancel_check():
            return

        async with sem:
            try:
                page = await context.new_page()
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                html = await page.content()

                ttfb_ms = dom_ready_ms = load_time_ms = None
                if capture_perf:
                    try:
                        timing = await page.evaluate("""() => {
                            const t = performance.timing;
                            return {
                                ttfb:      t.responseStart - t.navigationStart,
                                dom_ready: t.domContentLoadedEventEnd - t.navigationStart,
                                load_time: t.loadEventEnd > 0
                                           ? t.loadEventEnd - t.navigationStart
                                           : null,
                            };
                        }""")
                        ttfb_ms      = timing.get("ttfb")
                        dom_ready_ms = timing.get("dom_ready")
                        load_time_ms = timing.get("load_time")
                    except Exception:
                        pass

                await page.close()

                norm = self._normalize(url)
                idx  = self._page_index.get(norm)

                if idx is not None:
                    entry = self.pages[idx]
                    entry["html"] = html
                    if load_time_ms is not None:
                        entry["load_time_ms"] = load_time_ms
                    if ttfb_ms is not None:
                        entry["ttfb_ms"]     = ttfb_ms
                    if dom_ready_ms is not None:
                        entry["dom_ready_ms"] = dom_ready_ms

                    # If this was a JS page, re-count words from rendered HTML
                    if entry.get("js_dependent"):
                        soup = BeautifulSoup(html, "html.parser")
                        body = soup.find("body")
                        if body:
                            for tag in body(["script", "style"]):
                                tag.decompose()
                            text = body.get_text(separator=" ")
                            entry["plain_word_count"] = len([w for w in text.split() if w.strip()])
                else:
                    # URL only in Playwright list but missed by httpx (edge case)
                    self.pages.append({
                        "url":            url,
                        "html":           html,
                        "status_code":    response.status if response else 0,
                        "error":          None,
                        "load_time_ms":   load_time_ms or 0,
                        "ttfb_ms":        ttfb_ms,
                        "dom_ready_ms":   dom_ready_ms,
                        "crawl_depth":    0,
                        "is_redirect":    False,
                        "redirect_to":    "",
                        "js_dependent":   True,
                        "plain_word_count": 0,
                    })

            except Exception:
                pass   # Keep existing httpx data if Playwright fails
