import asyncio
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

MAX_CONCURRENT = 3  # max parallel browser pages (reduced to save RAM)

_DEFAULT_META = {
    "robots_txt_exists": False,
    "sitemap_exists": False,
    "sitemap_urls": [],
    "robots_disallows": [],
}


class SiteCrawler:
    def __init__(self, base_url: str, max_pages: int = 50, on_progress=None, exclude_patterns: list[str] | None = None, cancel_check=None):
        self.base_url = base_url.rstrip("/")
        self.domain = urlparse(base_url).netloc
        self.max_pages = max_pages
        self.visited: set[str] = set()
        self.pages: list[dict] = []
        self._sem: asyncio.Semaphore | None = None
        self._on_progress = on_progress  # optional callback(crawled: int)
        self._cancel_check = cancel_check  # optional callback() -> bool
        self._exclude = [p.strip() for p in (exclude_patterns or []) if p.strip()]

    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.domain

    def _is_excluded(self, url: str) -> bool:
        path = urlparse(url).path
        return any(pat in path for pat in self._exclude)

    def _normalize(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    async def _parse_sitemap(self, client, sitemap_url: str, depth: int = 0, _raw_store: list | None = None) -> list[str]:
        """Recursively collect all page URLs from a sitemap or sitemap index.
        _raw_store: if provided, appends (url, raw_text) tuples for each fetched sitemap."""
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

            # Sitemap index — recurse into child sitemaps
            child_sitemaps = root.findall(".//sm:sitemap/sm:loc", ns)
            if child_sitemaps:
                all_urls: list[str] = []
                for loc in child_sitemaps[:20]:  # cap at 20 child sitemaps
                    child_urls = await self._parse_sitemap(client, loc.text.strip(), depth + 1, _raw_store)
                    all_urls.extend(child_urls)
                return all_urls

            # Regular sitemap — return page URLs
            return [loc.text.strip() for loc in root.findall(".//sm:loc", ns) if loc.text]
        except Exception:
            return []

    async def fetch_meta_files(self) -> dict:
        """Fetch robots.txt and sitemap.xml from the base domain."""
        parsed = urlparse(self.base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
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
            async with httpx.AsyncClient(
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": "Googlebot/2.1"},
            ) as client:
                # robots.txt
                try:
                    r = await client.get(f"{base}/robots.txt")
                    if r.status_code == 200 and "text" in r.headers.get("content-type", "text"):
                        result["robots_txt_exists"] = True
                        result["robots_txt_content"] = r.text
                        disallows = []
                        sitemap_from_robots: list[str] = []
                        for line in r.text.splitlines():
                            line = line.strip()
                            if line.lower().startswith("disallow:"):
                                val = line[len("disallow:"):].strip()
                                if val:
                                    disallows.append(val)
                            elif line.lower().startswith("sitemap:"):
                                val = line[len("sitemap:"):].strip()
                                if val:
                                    sitemap_from_robots.append(val)
                        result["robots_disallows"] = disallows
                        result["_sitemap_hints"] = sitemap_from_robots
                except Exception:
                    pass

                # sitemap.xml — try /sitemap.xml, then any hints from robots.txt
                sitemap_candidates = [f"{base}/sitemap.xml"] + result.pop("_sitemap_hints", [])
                all_sitemap_urls: list[str] = []
                sitemap_raw_files: list[tuple[str, str]] = []
                for candidate in sitemap_candidates:
                    urls = await self._parse_sitemap(client, candidate, _raw_store=sitemap_raw_files)
                    if urls:
                        result["sitemap_exists"] = True
                        all_sitemap_urls.extend(urls)

                result["sitemap_raw_files"] = sitemap_raw_files  # list of (url, xml_text)

                # Deduplicate
                seen: set[str] = set()
                unique: list[str] = []
                for u in all_sitemap_urls:
                    if u not in seen:
                        seen.add(u)
                        unique.append(u)

                result["sitemap_all_urls"] = unique
                result["sitemap_urls"] = unique[:5]  # display only
        except Exception:
            pass

        return result

    async def crawl(self) -> tuple[list[dict], dict]:
        meta_files = await self.fetch_meta_files()

        # Pre-seed queue with same-domain sitemap URLs
        sitemap_seeds = [
            u for u in meta_files.get("sitemap_all_urls", [])
            if self._is_same_domain(u) and not self._is_excluded(u)
        ]

        self._sem = asyncio.Semaphore(MAX_CONCURRENT)

        # Single shared httpx client for all JS-detection checks
        self._http_client = None
        if _HTTPX_AVAILABLE:
            self._http_client = httpx.AsyncClient(
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": "Googlebot/2.1"},
            )

        try:
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

                # Phase 1: link-following from homepage
                await self._crawl_page(context, self.base_url, depth=0)

                # Phase 2: crawl sitemap URLs not yet discovered by link-following
                remaining = [
                    u for u in sitemap_seeds
                    if self._normalize(u) not in self.visited and len(self.visited) < self.max_pages
                ]
                if remaining:
                    tasks = [self._crawl_page(context, u, depth=1) for u in remaining]
                    await asyncio.gather(*tasks)

                await context.close()
                await browser.close()
        finally:
            if self._http_client:
                await self._http_client.aclose()

        return self.pages, meta_files

    async def _crawl_page(self, context, url: str, depth: int = 0):
        if self._cancel_check and self._cancel_check():
            return
        normalized = self._normalize(url)
        if normalized in self.visited or len(self.visited) >= self.max_pages:
            return
        if self._is_excluded(url):
            return
        self.visited.add(normalized)

        async with self._sem:
            try:
                page = await context.new_page()
                t0 = time.monotonic()
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                load_time_ms = int((time.monotonic() - t0) * 1000)
                status_code = response.status if response else 0
                final_url = page.url  # URL after any redirects

                # Detect redirect
                is_redirect = False
                redirect_to = ""
                norm_original = self._normalize(url)
                norm_final = self._normalize(final_url)
                if norm_final and norm_final != norm_original:
                    is_redirect = True
                    redirect_to = final_url

                html = await page.content()

                # JS rendering check: compare plain httpx word count vs rendered
                js_dependent = False
                plain_word_count = 0
                if self._http_client:
                    try:
                        plain_r = await self._http_client.get(url)
                        plain_soup = BeautifulSoup(plain_r.text, "html.parser")
                        plain_body = plain_soup.find("body")
                        if plain_body:
                            for tag in plain_body(["script", "style"]):
                                tag.decompose()
                            plain_text = plain_body.get_text(separator=" ")
                        else:
                            plain_text = plain_soup.get_text(separator=" ")
                        plain_word_count = len([w for w in plain_text.split() if w.strip()])

                        # Count words in rendered HTML
                        rendered_soup = BeautifulSoup(html, "html.parser")
                        rendered_body = rendered_soup.find("body")
                        if rendered_body:
                            for tag in rendered_body(["script", "style"]):
                                tag.decompose()
                            rendered_text = rendered_body.get_text(separator=" ")
                        else:
                            rendered_text = rendered_soup.get_text(separator=" ")
                        rendered_words = len([w for w in rendered_text.split() if w.strip()])

                        if rendered_words > 0 and plain_word_count / rendered_words < 0.5:
                            js_dependent = True
                    except Exception:
                        pass

                await page.close()
            except Exception as e:
                self.pages.append({
                    "url": url,
                    "error": str(e),
                    "html": "",
                    "status_code": 0,
                    "load_time_ms": 0,
                    "crawl_depth": depth,
                    "is_redirect": False,
                    "redirect_to": "",
                    "js_dependent": False,
                    "plain_word_count": 0,
                })
                return

        self.pages.append({
            "url": url,
            "html": html,
            "status_code": status_code,
            "error": None,
            "load_time_ms": load_time_ms,
            "crawl_depth": depth,
            "is_redirect": is_redirect,
            "redirect_to": redirect_to,
            "js_dependent": js_dependent,
            "plain_word_count": plain_word_count,
        })

        if self._on_progress:
            self._on_progress(len(self.pages))

        soup = BeautifulSoup(html, "html.parser")
        links = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            full_url = urljoin(url, href)
            norm = self._normalize(full_url)
            if (
                self._is_same_domain(full_url)
                and norm not in self.visited
                and not self._is_excluded(full_url)
                and not any(full_url.endswith(ext) for ext in [".pdf", ".jpg", ".png", ".zip", ".css", ".js"])
            ):
                links.append(full_url)

        tasks = [self._crawl_page(context, link, depth=depth + 1) for link in links[:50]]
        await asyncio.gather(*tasks)
