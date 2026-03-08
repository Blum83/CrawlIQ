import os
import asyncio
import httpx

# Gemini models to try in order (fallback to lighter model on 429)
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash-latest",
]


def build_prompt(url: str, aggregated: dict) -> str:
    issues = aggregated.get("issues", {})
    coverage = aggregated.get("content_coverage", {})

    return f"""You are a professional QA engineer. Analyze the following website audit data and generate a concise, actionable QA report.

Website: {url}
Pages crawled: {aggregated.get('pages_crawled', 0)} / {aggregated.get('total_pages', 0)}

Raw audit data:
- Missing meta descriptions: {issues.get('missing_meta_description', {}).get('count', 0)} pages
- Missing H1 tags: {issues.get('missing_h1', {}).get('count', 0)} pages
- Broken images: {issues.get('broken_images', {}).get('count', 0)}
- Missing alt tags on images: {issues.get('missing_alt_tags', {}).get('count', 0)} images across {len(issues.get('missing_alt_tags', {}).get('pages', []))} pages
- Thin content (<200 words): {issues.get('thin_content_under_200_words', {}).get('count', 0)} pages ({coverage.get('pct_thin_content', 0)}%)
- Non-200 status codes: {issues.get('non_200_status', {}).get('count', 0)} pages
- Error pages (failed to load): {aggregated.get('error_pages', 0)}
- Average word count: {coverage.get('avg_word_count', 0)} words/page

Generate a structured QA report with:
1. Executive Summary (2-3 sentences overall assessment)
2. Critical Issues (must fix)
3. Warnings (should fix)
4. Accessibility summary
5. Content quality summary
6. Top 3 recommendations

Be direct and professional. Use bullet points. Focus on actionable insights."""


async def _call_gemini(prompt: str, api_key: str, model: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


async def generate_ai_summary(url: str, aggregated: dict) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _fallback_summary(url, aggregated)

    prompt = build_prompt(url, aggregated)
    last_error = None

    for model in GEMINI_MODELS:
        try:
            print(f"[AI Agent] Trying {model}...")
            result = await _call_gemini(prompt, api_key, model)
            print(f"[AI Agent] Success with {model}")
            return result
        except httpx.HTTPStatusError as e:
            last_error = str(e)
            print(f"[AI Agent] {model} failed: {e.response.status_code}")
            if e.response.status_code == 429:
                # Rate limited — wait a bit and try next model
                await asyncio.sleep(3)
                continue
            elif e.response.status_code in (400, 404):
                # Bad model name or request — try next
                continue
            else:
                break
        except Exception as e:
            last_error = str(e)
            print(f"[AI Agent] {model} error: {e}")
            break

    return f"AI summary unavailable: {last_error}\n\n" + _fallback_summary(url, aggregated)


def _fallback_summary(url: str, aggregated: dict) -> str:
    issues = aggregated.get("issues", {})
    coverage = aggregated.get("content_coverage", {})
    lines = [
        f"## QA Report for {url}",
        f"",
        f"**Pages crawled:** {aggregated.get('pages_crawled', 0)}",
        f"",
        f"### Issues Found",
        f"- Missing meta description: {issues.get('missing_meta_description', {}).get('count', 0)} pages",
        f"- Missing H1: {issues.get('missing_h1', {}).get('count', 0)} pages",
        f"- Broken images: {issues.get('broken_images', {}).get('count', 0)}",
        f"- Missing alt tags: {issues.get('missing_alt_tags', {}).get('count', 0)} images",
        f"- Thin content: {issues.get('thin_content_under_200_words', {}).get('count', 0)} pages ({coverage.get('pct_thin_content', 0)}%)",
        f"- Non-200 pages: {issues.get('non_200_status', {}).get('count', 0)}",
    ]
    return "\n".join(lines)
