import re
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class PageReport:
    url: str
    status_code: int
    error: str | None = None

    # Indexability
    is_noindex: bool = False        # <meta name="robots" content="noindex">
    is_nofollow: bool = False       # <meta name="robots" content="nofollow">
    canonical_url: str = ""         # href of canonical tag (empty if absent)
    is_canonicalized_away: bool = False  # canonical points to a different URL

    # SEO
    has_title: bool = False
    title: str = ""
    has_meta_description: bool = False
    meta_description: str = ""
    has_h1: bool = False
    h1_count: int = 0
    has_canonical: bool = False

    # Title/description quality
    title_length: int = 0
    title_too_long: bool = False    # > 60 chars
    title_too_short: bool = False   # < 10 chars (and has title)
    meta_description_length: int = 0
    meta_description_too_long: bool = False   # > 160 chars
    meta_description_too_short: bool = False  # < 70 chars (and has meta)

    # Open Graph
    has_og_title: bool = False
    has_og_description: bool = False
    has_og_image: bool = False

    # Structured data
    has_schema_org: bool = False    # JSON-LD or itemscope present

    # Headings
    h2_count: int = 0

    # URL issues
    url_has_uppercase: bool = False    # uppercase letters in path
    url_has_underscores: bool = False  # underscores in path
    url_too_long: bool = False         # path > 115 chars

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

    # Performance/technical (from crawler)
    load_time_ms: int = 0
    crawl_depth: int = 0
    is_redirect: bool = False
    redirect_to: str = ""
    js_dependent: bool = False

    # Errors
    has_error: bool = False


def _normalize_url(url: str) -> str:
    """Normalize URL for comparison: lowercase scheme+host, strip trailing slash."""
    try:
        p = urlparse(url)
        return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path}".rstrip("/")
    except Exception:
        return url.rstrip("/")


def analyze_page(page_data: dict, base_domain: str) -> PageReport:
    url = page_data["url"]
    html = page_data.get("html", "")
    status_code = page_data.get("status_code", 0)
    error = page_data.get("error")

    report = PageReport(url=url, status_code=status_code, error=error)

    # Pull crawler-provided fields
    report.load_time_ms = page_data.get("load_time_ms", 0)
    report.crawl_depth = page_data.get("crawl_depth", 0)
    report.is_redirect = page_data.get("is_redirect", False)
    report.redirect_to = page_data.get("redirect_to", "")
    report.js_dependent = page_data.get("js_dependent", False)

    # URL quality checks
    parsed_url = urlparse(url)
    url_path = parsed_url.path
    report.url_has_uppercase = bool(re.search(r"[A-Z]", url_path))
    report.url_has_underscores = "_" in url_path
    report.url_too_long = len(url_path) > 115

    if error or not html:
        report.has_error = True
        return report

    soup = BeautifulSoup(html, "html.parser")

    # HTML lang attribute
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang", "").strip():
        report.html_has_lang = True

    # Robots meta tag (noindex / nofollow)
    robots_meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    if robots_meta:
        robots_content = robots_meta.get("content", "").lower()
        report.is_noindex  = "noindex"  in robots_content
        report.is_nofollow = "nofollow" in robots_content

    # Title
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        report.has_title = True
        report.title = title_tag.get_text(strip=True)
        report.title_length = len(report.title)
        report.title_too_long = report.title_length > 60
        report.title_too_short = report.title_length < 10

    # Meta description
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta and meta.get("content", "").strip():
        report.has_meta_description = True
        report.meta_description = meta["content"].strip()
        report.meta_description_length = len(report.meta_description)
        report.meta_description_too_long = report.meta_description_length > 160
        report.meta_description_too_short = report.meta_description_length < 70

    # Canonical
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical and canonical.get("href", "").strip():
        report.has_canonical = True
        canon_href = canonical["href"].strip()
        report.canonical_url = canon_href
        # Canonicalized away: canonical points to a different URL
        norm_page   = _normalize_url(url)
        norm_canon  = _normalize_url(canon_href)
        report.is_canonicalized_away = bool(norm_canon and norm_canon != norm_page)

    # H1
    h1_tags = soup.find_all("h1")
    report.h1_count = len(h1_tags)
    report.has_h1 = report.h1_count > 0

    # H2
    report.h2_count = len(soup.find_all("h2"))

    # Open Graph
    og_title = soup.find("meta", property="og:title")
    report.has_og_title = bool(og_title and og_title.get("content", "").strip())
    og_desc = soup.find("meta", property="og:description")
    report.has_og_description = bool(og_desc and og_desc.get("content", "").strip())
    og_image = soup.find("meta", property="og:image")
    report.has_og_image = bool(og_image and og_image.get("content", "").strip())

    # Structured data (JSON-LD or itemscope)
    ld_json = soup.find("script", type="application/ld+json")
    has_itemscope = bool(soup.find(attrs={"itemscope": True}))
    report.has_schema_org = bool(ld_json or has_itemscope)

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

    # Indexability
    noindex_pages        = [r for r in valid if r.is_noindex]
    canonicalized_away   = [r for r in valid if r.is_canonicalized_away]
    non_indexable        = {r.url for r in noindex_pages} | {r.url for r in canonicalized_away}
    # SEO checks only on truly indexable pages
    indexable            = [r for r in valid if r.url not in non_indexable]

    # SEO
    missing_title       = [r for r in indexable if not r.has_title]
    missing_meta        = [r for r in valid if not r.has_meta_description]
    missing_h1          = [r for r in indexable if not r.has_h1]
    multiple_h1         = [r for r in indexable if r.h1_count > 1]
    missing_canonical   = [r for r in indexable if not r.has_canonical]

    # Title/meta quality
    title_too_long  = [r for r in indexable if r.title_too_long]
    title_too_short = [r for r in indexable if r.title_too_short]
    meta_too_long   = [r for r in indexable if r.meta_description_too_long]
    meta_too_short  = [r for r in indexable if r.meta_description_too_short]

    # Duplicate meta descriptions
    meta_desc_counts: dict[str, list[str]] = {}
    for r in indexable:
        if r.meta_description:
            meta_desc_counts.setdefault(r.meta_description, []).append(r.url)
    duplicate_meta_urls = [url for urls in meta_desc_counts.values() if len(urls) > 1 for url in urls]

    # Open Graph & Schema
    missing_og     = [r for r in indexable if not r.has_og_title or not r.has_og_image]
    missing_schema = [r for r in indexable if not r.has_schema_org]

    # URL quality
    url_issues = [r for r in valid if r.url_has_uppercase or r.url_has_underscores or r.url_too_long]

    # Accessibility (on indexable pages)
    missing_lang        = [r for r in indexable if not r.html_has_lang]
    missing_alt_pages   = [r for r in indexable if r.images_missing_alt > 0]
    broken_img_pages    = [r for r in valid if r.broken_images]
    buttons_unlabeled   = [r for r in indexable if r.buttons_missing_label > 0]

    # Content (on indexable pages)
    thin_content        = [r for r in indexable if 0 < r.word_count < 200]
    empty_pages         = [r for r in indexable if r.is_empty]

    # Duplicate titles (on indexable pages)
    title_counts: dict[str, list[str]] = {}
    for r in indexable:
        if r.title:
            title_counts.setdefault(r.title, []).append(r.url)
    duplicate_title_urls = [url for urls in title_counts.values() if len(urls) > 1 for url in urls]

    # Technical
    non_200 = [r for r in valid if r.status_code not in (200, 0)]

    # Performance
    slow_pages         = [r for r in valid if r.load_time_ms > 3000]
    avg_load_time      = int(sum(r.load_time_ms for r in valid) / max(len(valid), 1))
    deep_pages         = [r for r in valid if r.crawl_depth > 3]
    redirect_pages     = [r for r in valid if r.is_redirect]
    js_dependent_pages = [r for r in valid if r.js_dependent]

    return {
        "total_pages": total,
        "pages_crawled": total,
        "indexable_pages": len(indexable),
        "non_indexable_pages": total - len(indexable),
        "error_pages": len(error_pages),
        "indexability": {
            "noindex": {
                "count": len(noindex_pages),
                "urls": [r.url for r in noindex_pages],
            },
            "canonicalized_away": {
                "count": len(canonicalized_away),
                "urls": [{"url": r.url, "canonical": r.canonical_url} for r in canonicalized_away],
            },
        },
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
            # Title/meta quality
            "title_too_long": {
                "count": len(title_too_long),
                "urls": [r.url for r in title_too_long],
            },
            "title_too_short": {
                "count": len(title_too_short),
                "urls": [r.url for r in title_too_short],
            },
            "meta_description_too_long": {
                "count": len(meta_too_long),
                "urls": [r.url for r in meta_too_long],
            },
            "meta_description_too_short": {
                "count": len(meta_too_short),
                "urls": [r.url for r in meta_too_short],
            },
            "duplicate_meta_descriptions": {
                "count": len(duplicate_meta_urls),
                "urls": duplicate_meta_urls[:20],
            },
            # Open Graph & Schema
            "missing_og": {
                "count": len(missing_og),
                "urls": [r.url for r in missing_og],
            },
            "missing_schema": {
                "count": len(missing_schema),
                "urls": [r.url for r in missing_schema],
            },
            # URL quality
            "url_issues": {
                "count": len(url_issues),
                "urls": [r.url for r in url_issues],
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
            # Performance
            "slow_pages": {
                "count": len(slow_pages),
                "urls": [r.url for r in slow_pages],
            },
            "redirect_pages": {
                "count": len(redirect_pages),
                "urls": [{"url": r.url, "redirect_to": r.redirect_to} for r in redirect_pages],
            },
            "js_dependent_pages": {
                "count": len(js_dependent_pages),
                "urls": [r.url for r in js_dependent_pages],
            },
            "deep_pages": {
                "count": len(deep_pages),
                "urls": [r.url for r in deep_pages],
            },
        },
        "performance": {
            "avg_load_time_ms": avg_load_time,
            "slow_pages_count": len(slow_pages),
            "redirect_pages_count": len(redirect_pages),
            "js_dependent_count": len(js_dependent_pages),
            "deep_pages_count": len(deep_pages),
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
                "indexable": not r.is_noindex and not r.is_canonicalized_away and not r.has_error,
                "is_noindex": r.is_noindex,
                "is_canonicalized_away": r.is_canonicalized_away,
                "canonical_url": r.canonical_url,
                "has_canonical": r.has_canonical,
                "has_h1": r.has_h1,
                "h1_count": r.h1_count,
                "html_has_lang": r.html_has_lang,
                "images_total": r.images_total,
                "images_missing_alt": r.images_missing_alt,
                "buttons_missing_label": r.buttons_missing_label,
                "word_count": r.word_count,
                "error": r.error,
                # New fields
                "load_time_ms": r.load_time_ms,
                "crawl_depth": r.crawl_depth,
                "is_redirect": r.is_redirect,
                "redirect_to": r.redirect_to,
                "js_dependent": r.js_dependent,
                "title_length": r.title_length,
                "meta_description_length": r.meta_description_length,
                "has_og_title": r.has_og_title,
                "has_og_image": r.has_og_image,
                "has_schema_org": r.has_schema_org,
            }
            for r in page_reports
        ],
    }
