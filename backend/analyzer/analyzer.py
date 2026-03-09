import re
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class PageReport:
    url: str
    status_code: int
    error: str | None = None

    # SEO
    has_title: bool = False
    title: str = ""
    has_meta_description: bool = False
    meta_description: str = ""
    has_h1: bool = False
    h1_count: int = 0
    has_canonical: bool = False

    # Accessibility
    html_has_lang: bool = False
    images_total: int = 0
    images_missing_alt: int = 0
    broken_images: list[str] = field(default_factory=list)
    buttons_missing_label: int = 0

    # Content
    word_count: int = 0
    is_empty: bool = False

    # Links
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

    # HTML lang attribute
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang", "").strip():
        report.html_has_lang = True

    # Title
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        report.has_title = True
        report.title = title_tag.get_text(strip=True)

    # Meta description
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta and meta.get("content", "").strip():
        report.has_meta_description = True
        report.meta_description = meta["content"].strip()

    # Canonical
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical and canonical.get("href", "").strip():
        report.has_canonical = True

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
        if src is not None and src.strip() == "":
            report.broken_images.append("(empty src)")

    # Buttons without accessible label
    for btn in soup.find_all("button"):
        has_text = bool(btn.get_text(strip=True))
        has_aria = bool(btn.get("aria-label", "").strip() or btn.get("aria-labelledby", "").strip())
        if not has_text and not has_aria:
            report.buttons_missing_label += 1

    # Word count (body text only)
    body = soup.find("body")
    if body:
        for tag in body(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = body.get_text(separator=" ")
    else:
        text = ""
    words = [w for w in text.split() if w.strip()]
    report.word_count = len(words)
    report.is_empty = report.word_count == 0

    # Links
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

    valid = [r for r in page_reports if not r.has_error]
    error_pages = [r for r in page_reports if r.has_error]

    # SEO
    missing_title       = [r for r in valid if not r.has_title]
    missing_meta        = [r for r in valid if not r.has_meta_description]
    missing_h1          = [r for r in valid if not r.has_h1]
    multiple_h1         = [r for r in valid if r.h1_count > 1]
    missing_canonical   = [r for r in valid if not r.has_canonical]

    # Accessibility
    missing_lang        = [r for r in valid if not r.html_has_lang]
    missing_alt_pages   = [r for r in valid if r.images_missing_alt > 0]
    broken_img_pages    = [r for r in valid if r.broken_images]
    buttons_unlabeled   = [r for r in valid if r.buttons_missing_label > 0]

    # Content
    thin_content        = [r for r in valid if 0 < r.word_count < 200]
    empty_pages         = [r for r in valid if r.is_empty]

    # Duplicate titles
    title_counts: dict[str, list[str]] = {}
    for r in valid:
        if r.title:
            title_counts.setdefault(r.title, []).append(r.url)
    duplicate_title_urls = [url for urls in title_counts.values() if len(urls) > 1 for url in urls]

    # Technical
    non_200 = [r for r in valid if r.status_code not in (200, 0)]

    return {
        "total_pages": total,
        "pages_crawled": len(valid),
        "error_pages": len(error_pages),
        "issues": {
            # SEO
            "missing_title": {
                "count": len(missing_title),
                "urls": [r.url for r in missing_title],
            },
            "missing_meta_description": {
                "count": len(missing_meta),
                "urls": [r.url for r in missing_meta],
            },
            "missing_h1": {
                "count": len(missing_h1),
                "urls": [r.url for r in missing_h1],
            },
            "multiple_h1": {
                "count": len(multiple_h1),
                "urls": [r.url for r in multiple_h1],
            },
            "missing_canonical": {
                "count": len(missing_canonical),
                "urls": [r.url for r in missing_canonical],
            },
            "duplicate_titles": {
                "count": len(duplicate_title_urls),
                "urls": duplicate_title_urls[:20],
            },
            # Accessibility
            "missing_html_lang": {
                "count": len(missing_lang),
                "urls": [r.url for r in missing_lang],
            },
            "missing_alt_tags": {
                "count": sum(r.images_missing_alt for r in missing_alt_pages),
                "pages": [r.url for r in missing_alt_pages],
            },
            "broken_images": {
                "count": sum(len(r.broken_images) for r in broken_img_pages),
                "pages": [r.url for r in broken_img_pages],
            },
            "buttons_missing_label": {
                "count": sum(r.buttons_missing_label for r in buttons_unlabeled),
                "pages": [r.url for r in buttons_unlabeled],
            },
            # Content
            "thin_content_under_200_words": {
                "count": len(thin_content),
                "urls": [r.url for r in thin_content],
            },
            "empty_pages": {
                "count": len(empty_pages),
                "urls": [r.url for r in empty_pages],
            },
            # Technical
            "non_200_status": {
                "count": len(non_200),
                "pages": [{"url": r.url, "status": r.status_code} for r in non_200],
            },
        },
        "content_coverage": {
            "avg_word_count": int(
                sum(r.word_count for r in valid) / max(len(valid), 1)
            ),
            "pct_thin_content": round(len(thin_content) / max(len(valid), 1) * 100, 1),
        },
        "page_details": [
            {
                "url": r.url,
                "status_code": r.status_code,
                "title": r.title,
                "has_title": r.has_title,
                "has_meta_description": r.has_meta_description,
                "has_canonical": r.has_canonical,
                "has_h1": r.has_h1,
                "h1_count": r.h1_count,
                "html_has_lang": r.html_has_lang,
                "images_total": r.images_total,
                "images_missing_alt": r.images_missing_alt,
                "buttons_missing_label": r.buttons_missing_label,
                "word_count": r.word_count,
                "error": r.error,
            }
            for r in page_reports
        ],
    }
