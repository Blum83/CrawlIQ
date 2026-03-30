"""
AI summary providers with automatic fallback.

Priority: Groq (free, fast) → Gemini → deterministic fallback
Set one of:
  GROQ_API_KEY   — https://console.groq.com  (free tier, Llama3)
  GEMINI_API_KEY — https://aistudio.google.com (free tier)
"""

import os
import asyncio
import httpx
from abc import ABC, abstractmethod


# ─── Base ──────────────────────────────────────────────────────────────────────

class AIProvider(ABC):
    @abstractmethod
    async def summarize(self, prompt: str) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...


# ─── Groq (free, Llama3) ───────────────────────────────────────────────────────

class GroqProvider(AIProvider):
    name = "Groq/Llama3"
    MODELS = ["llama-3.3-70b-versatile", "llama3-8b-8192"]

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def summarize(self, prompt: str) -> str:
        for model in self.MODELS:
            try:
                return await self._call(prompt, model)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    await asyncio.sleep(10)
                    try:
                        return await self._call(prompt, model)
                    except Exception:
                        continue
                elif e.response.status_code in (400, 404):
                    continue
                raise
        raise RuntimeError("All Groq models failed")

    async def _call(self, prompt: str, model: str) -> str:
        client = httpx.AsyncClient(timeout=60)
        try:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        finally:
            await client.aclose()


# ─── Gemini ────────────────────────────────────────────────────────────────────

class GeminiProvider(AIProvider):
    name = "Gemini"
    MODELS = ["gemini-2.0-flash", "gemini-2.0-flash-lite"]

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def summarize(self, prompt: str) -> str:
        for model in self.MODELS:
            try:
                return await self._call(prompt, model)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    await asyncio.sleep(15)
                    try:
                        return await self._call(prompt, model)
                    except Exception:
                        continue
                elif e.response.status_code in (400, 404):
                    continue
                raise
        raise RuntimeError("All Gemini models failed")

    async def _call(self, prompt: str, model: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]


# ─── Auto-select provider ──────────────────────────────────────────────────────

def get_provider() -> AIProvider | None:
    if key := os.getenv("GROQ_API_KEY"):
        return GroqProvider(key)
    if key := os.getenv("GEMINI_API_KEY"):
        return GeminiProvider(key)
    return None


# ─── Prompt ────────────────────────────────────────────────────────────────────

def build_prompt(url: str, aggregated: dict) -> str:
    issues = aggregated.get("issues", {})
    coverage = aggregated.get("content_coverage", {})

    def cnt(key):
        return issues.get(key, {}).get("count", 0)

    return f"""You are a professional QA engineer. Analyze the following website audit data and write a concise, actionable summary.

Website: {url}
Pages crawled: {aggregated.get('pages_crawled', 0)}

SEO issues:
- Missing title: {cnt('missing_title')} pages
- Missing meta description: {cnt('missing_meta_description')} pages
- Missing H1: {cnt('missing_h1')} pages
- Multiple H1: {cnt('multiple_h1')} pages
- Missing canonical: {cnt('missing_canonical')} pages
- Duplicate titles: {cnt('duplicate_titles')} pages

Accessibility:
- Missing html lang: {cnt('missing_html_lang')} pages
- Images missing alt text: {cnt('missing_alt_tags')} images
- Buttons without label: {cnt('buttons_missing_label')}

Content:
- Thin content (<200 words): {cnt('thin_content_under_200_words')} pages
- Empty pages: {cnt('empty_pages')}
- Avg word count: {coverage.get('avg_word_count', 0)} words/page

Technical:
- Broken images: {cnt('broken_images')}
- Non-200 pages: {cnt('non_200_status')}

Write 3-5 sentences: overall assessment, top 2-3 critical issues, and one actionable recommendation. Be direct and professional."""


# ─── Deterministic fallback ────────────────────────────────────────────────────

def deterministic_summary(url: str, aggregated: dict) -> str:
    issues = aggregated.get("issues", {})

    ranked = sorted(
        [
            (issues.get(k, {}).get("count", 0), label)
            for k, label in [
                ("missing_meta_description", "Missing meta description"),
                ("missing_title",            "Missing page title"),
                ("missing_h1",               "Missing H1 tag"),
                ("multiple_h1",              "Multiple H1 tags"),
                ("missing_canonical",        "Missing canonical link"),
                ("duplicate_titles",         "Duplicate page titles"),
                ("missing_html_lang",        "Missing HTML lang attribute"),
                ("missing_alt_tags",         "Images without alt text"),
                ("buttons_missing_label",    "Buttons without accessible label"),
                ("thin_content_under_200_words", "Thin content pages"),
                ("empty_pages",             "Empty pages"),
                ("broken_images",           "Broken images"),
                ("non_200_status",          "Non-200 status pages"),
            ]
        ],
        reverse=True,
    )

    top = [(cnt, label) for cnt, label in ranked if cnt > 0][:3]
    if not top:
        return "No significant issues detected."

    lines = ["Top issues detected:"]
    for i, (cnt, label) in enumerate(top, 1):
        lines.append(f"{i}. {label} ({cnt})")
    return "\n".join(lines)


# ─── Main entry point ──────────────────────────────────────────────────────────

async def generate_ai_summary(url: str, aggregated: dict) -> str | None:
    provider = get_provider()
    if not provider:
        return None

    prompt = build_prompt(url, aggregated)
    try:
        print(f"[AI] Using {provider.name}...")
        result = await provider.summarize(prompt)
        print(f"[AI] {provider.name} succeeded")
        return result
    except Exception as e:
        print(f"[AI] {provider.name} failed: {e}")
        return deterministic_summary(url, aggregated)
