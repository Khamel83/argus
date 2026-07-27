"""
Crawl4AI content extractor.

Self-hosted, open-source extraction with JS rendering and LLM-aware chunking.
Install: pip install crawl4ai
Gate behind ARGUS_CRAWL4AI_ENABLED=true
"""

from collections.abc import Mapping

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.ssrf import is_safe_url
from argus.logging import get_logger

logger = get_logger("extraction.crawl4ai")


def _field(result: object, name: str, default=None):
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def normalize_crawl4ai_result(url: str, result: object) -> ExtractedContent:
    """Normalize the locked Crawl4AI result object without retaining raw fields."""
    if not result or _field(result, "success") is not True:
        return ExtractedContent(url=url, error="crawl4ai: extraction failed")

    markdown = _field(result, "markdown")
    if isinstance(markdown, Mapping):
        markdown = markdown.get("fit_markdown") or markdown.get("raw_markdown")
    elif markdown is not None and not isinstance(markdown, str):
        markdown = getattr(markdown, "fit_markdown", None) or getattr(
            markdown, "raw_markdown", None
        )
    if not isinstance(markdown, str):
        return ExtractedContent(url=url, error="crawl4ai: malformed markdown result")

    final_url = _field(result, "url") or _field(result, "redirected_url") or url
    if not isinstance(final_url, str):
        final_url = url
    if final_url != url:
        safe, reason = is_safe_url(final_url)
        if not safe:
            return ExtractedContent(
                url=url,
                error=f"crawl4ai: unsafe redirect blocked: {reason}",
            )

    text = markdown.strip()
    if not text or len(text) < 50:
        return ExtractedContent(url=url, error="crawl4ai: content too short")
    metadata = _field(result, "metadata") or {}
    title = metadata.get("title", "") if isinstance(metadata, Mapping) else ""
    return ExtractedContent(
        url=final_url,
        title=str(title)[:1_000],
        text=text,
        word_count=len(text.split()),
        extractor=ExtractorName.CRAWL4AI,
    )


async def extract_crawl4ai(url: str) -> ExtractedContent:
    """Extract content using Crawl4AI (self-hosted, no API key)."""
    try:
        from crawl4ai import AsyncWebCrawler
    except ImportError:
        return ExtractedContent(
            url=url,
            error="crawl4ai: package not installed (pip install crawl4ai)",
        )

    try:
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url)
        return normalize_crawl4ai_result(url, result)
    except Exception as e:
        logger.warning(
            "Crawl4AI extraction failed for %s: %s",
            url[:60],
            type(e).__name__,
        )
        return ExtractedContent(url=url, error="crawl4ai: extraction failed")
