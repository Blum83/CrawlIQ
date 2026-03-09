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

MAX_CONCURRENT = 5  # max parallel browser pages

_DEFAULT_META = {
    "robots_txt_exists": False,
    "sitemap_exists": False,
    "sitemap_urls": [],
    "robots_disallows": [],
}


class SiteCrawler:
    def __init__(self, base_url: str, max_pages: int = 50, on_progress=None, exclude_patterns: list[str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.domain = urlparse(base_url).netloc
        self.max_pages = max_pages
        self.visited: set[str] = set()
        self.pages: list[dict] = []
        self._sem: asyncio.Semaphore | None = None
        self._on_progress = on_progress  # optional callback(crawled: int)
        self._exclude = [p.strip() for p in (exclude_patterns or []) if p.strip()]

    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.domain

    def _is_excluded(self, url: str) -> bool:
        path = urlparse(url).path
        return any(pat in path for pat in self._exclude)

    def _normalize(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    async def fetch_meta_files(self) -> dict:
        """Fetch robots.txt and sitemap.xml from the base domain."""
        parsed = urlparse(self.base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        result = {
            "robots_txt_exists": False,
            "sitemap_exists": False,
            "sitemap_urls": [],
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
                        disallows = []
                        for line in r.text.splitlines():
                            line = line.strip()
                            if line.lower().startswith("disallow:"):
                                val = line[len("disallow:"):].strip()
                                if val:
                                    disallows.append(val)
                        result["robots_disallows"] = disallows
                except Exception:
                    pass

                # sitemap.xml
                try:
                    r = await client.get(f"{base}/sitemap.xml")
                    if r.status_code == 200:
                        result["sitemap_exists"] = True
                        try:
                            root = ET.fromstring(r.text)
                            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                            urls = [loc.text.strip() for loc in root.findall(".//sm:loc", ns) if loc.text]
                            result["sitemap_urls"] = urls[:5]
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        return result

    async def crawl(self) -> tuple[list[dict], dict]:
        meta_files = await self.fetch_meta_files()

        self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (compatible; AIQABot/1.0)"
            )
            await self._crawl_page(context, self.base_url, depth=0)
            await browser.close()
        return self.pages, meta_files

    async def _crawl_page(self, context, url: str, depth: int = 0):
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
                if _HTTPX_AVAILABLE:
                    try:
                        async with httpx.AsyncClient(
                            timeout=10,
                            follow_redirects=True,
                            headers={"User-Agent": "Googlebot/2.1"},
                        ) as client:
                            plain_r = await client.get(url)
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

        tasks = [self._crawl_page(context, link, depth=depth + 1) for link in links[:10]]
        await asyncio.gather(*tasks)
