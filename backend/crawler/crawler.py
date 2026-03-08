import asyncio
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

MAX_CONCURRENT = 5  # max parallel browser pages


class SiteCrawler:
    def __init__(self, base_url: str, max_pages: int = 50):
        self.base_url = base_url.rstrip("/")
        self.domain = urlparse(base_url).netloc
        self.max_pages = max_pages
        self.visited: set[str] = set()
        self.pages: list[dict] = []
        self._sem: asyncio.Semaphore | None = None

    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.domain

    def _normalize(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    async def crawl(self) -> list[dict]:
        self._sem = asyncio.Semaphore(MAX_CONCURRENT)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (compatible; AIQABot/1.0)"
            )
            await self._crawl_page(context, self.base_url)
            await browser.close()
        return self.pages

    async def _crawl_page(self, context, url: str):
        normalized = self._normalize(url)
        if normalized in self.visited or len(self.visited) >= self.max_pages:
            return
        self.visited.add(normalized)

        async with self._sem:
            try:
                page = await context.new_page()
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                status_code = response.status if response else 0
                html = await page.content()
                await page.close()
            except Exception as e:
                self.pages.append({"url": url, "error": str(e), "html": "", "status_code": 0})
                return

        self.pages.append({
            "url": url,
            "html": html,
            "status_code": status_code,
            "error": None,
        })

        soup = BeautifulSoup(html, "html.parser")
        links = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            full_url = urljoin(url, href)
            norm = self._normalize(full_url)
            if (
                self._is_same_domain(full_url)
                and norm not in self.visited
                and not any(full_url.endswith(ext) for ext in [".pdf", ".jpg", ".png", ".zip", ".css", ".js"])
            ):
                links.append(full_url)

        tasks = [self._crawl_page(context, link) for link in links[:10]]
        await asyncio.gather(*tasks)
