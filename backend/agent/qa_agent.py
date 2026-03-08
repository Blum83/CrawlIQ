import os
import httpx


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


async def _gemini_summary(prompt: str, api_key: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


async def _anthropic_summary(prompt: str, api_key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


async def generate_ai_summary(url: str, aggregated: dict) -> str:
    prompt = build_prompt(url, aggregated)

    gemini_key = os.getenv("GEMINI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    # Try Gemini first (free), fall back to Anthropic, then text fallback
    gemini_error = None
    if gemini_key:
        try:
            return await _gemini_summary(prompt, gemini_key)
        except Exception as e:
            gemini_error = str(e)
            print(f"[AI Agent] Gemini failed: {e}")

    if anthropic_key:
        try:
            return await _anthropic_summary(prompt, anthropic_key)
        except Exception as e:
            print(f"[AI Agent] Anthropic failed: {e}")
            errors = f"Gemini: {gemini_error}\nAnthropic: {e}" if gemini_error else str(e)
            return f"AI summary unavailable: {errors}\n\n" + _fallback_summary(url, aggregated)

    if gemini_error:
        return f"AI summary unavailable (Gemini): {gemini_error}\n\n" + _fallback_summary(url, aggregated)

    return _fallback_summary(url, aggregated)


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
