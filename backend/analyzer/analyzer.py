import re
from bs4 import BeautifulSoup
from dataclasses import dataclass, field


@dataclass
class PageReport:
    url: str
    status_code: int
    error: str | None = None

    # SEO
    has_meta_description: bool = False
    meta_description: str = ""
    has_h1: bool = False
    h1_count: int = 0
    title: str = ""

    # Accessibility
    images_total: int = 0
    images_missing_alt: int = 0
    broken_images: list[str] = field(default_factory=list)

    # Content
    word_count: int = 0
    links_total: int = 0
    links_external: int = 0

    # Errors
    has_error: bool = False


def analyze_page(page_data: dict, base_domain: str) -> PageReport:
    url = page_data["url"]
    html = page_data.get("html", "")
    status_code = page_data.get("status_code", 0)
    error = page_data.get("error")

    report = PageReport(url=url, status_code=status_code, error=error)

    if error or not html:
        report.has_error = True
        return report

    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_tag = soup.find("title")
    report.title = title_tag.get_text(strip=True) if title_tag else ""

    # Meta description
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta and meta.get("content", "").strip():
        report.has_meta_description = True
        report.meta_description = meta["content"].strip()

    # H1
    h1_tags = soup.find_all("h1")
    report.h1_count = len(h1_tags)
    report.has_h1 = report.h1_count > 0

    # Images
    images = soup.find_all("img")
    report.images_total = len(images)
    for img in images:
        alt = img.get("alt", None)
        if alt is None or alt.strip() == "":
            report.images_missing_alt += 1
        src = img.get("src", "")
        # Only flag truly broken: empty src (not data URIs, those are valid inline images)
        if src is not None and src.strip() == "":
            report.broken_images.append("(empty src)")

    # Word count (body text)
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    words = [w for w in text.split() if w.strip()]
    report.word_count = len(words)

    # Links
    from urllib.parse import urlparse
    all_links = soup.find_all("a", href=True)
    report.links_total = len(all_links)
    for a in all_links:
        href = a["href"]
        parsed = urlparse(href)
        if parsed.netloc and parsed.netloc != base_domain:
            report.links_external += 1

    return report


def aggregate_reports(page_reports: list[PageReport]) -> dict:
    total = len(page_reports)
    if total == 0:
        return {}

    missing_meta = [r for r in page_reports if not r.has_meta_description and not r.has_error]
    missing_h1 = [r for r in page_reports if not r.has_h1 and not r.has_error]
    broken_images_pages = [r for r in page_reports if r.broken_images]
    missing_alt_pages = [r for r in page_reports if r.images_missing_alt > 0]
    thin_content = [r for r in page_reports if r.word_count < 200 and not r.has_error]
    error_pages = [r for r in page_reports if r.has_error]
    non_200 = [r for r in page_reports if r.status_code not in (200, 0) and not r.has_error]

    return {
        "total_pages": total,
        "pages_crawled": total - len(error_pages),
        "error_pages": len(error_pages),
        "issues": {
            "missing_meta_description": {
                "count": len(missing_meta),
                "urls": [r.url for r in missing_meta],
            },
            "missing_h1": {
                "count": len(missing_h1),
                "urls": [r.url for r in missing_h1],
            },
            "broken_images": {
                "count": sum(len(r.broken_images) for r in broken_images_pages),
                "pages": [r.url for r in broken_images_pages],
            },
            "missing_alt_tags": {
                "count": sum(r.images_missing_alt for r in missing_alt_pages),
                "pages": [r.url for r in missing_alt_pages],
            },
            "thin_content_under_200_words": {
                "count": len(thin_content),
                "urls": [r.url for r in thin_content],
            },
            "non_200_status": {
                "count": len(non_200),
                "pages": [{"url": r.url, "status": r.status_code} for r in non_200],
            },
        },
        "content_coverage": {
            "avg_word_count": int(
                sum(r.word_count for r in page_reports if not r.has_error) / max(total - len(error_pages), 1)
            ),
            "pct_thin_content": round(len(thin_content) / max(total - len(error_pages), 1) * 100, 1),
        },
        "page_details": [
            {
                "url": r.url,
                "status_code": r.status_code,
                "title": r.title,
                "has_meta_description": r.has_meta_description,
                "has_h1": r.has_h1,
                "h1_count": r.h1_count,
                "images_total": r.images_total,
                "images_missing_alt": r.images_missing_alt,
                "word_count": r.word_count,
                "error": r.error,
            }
            for r in page_reports
        ],
    }
