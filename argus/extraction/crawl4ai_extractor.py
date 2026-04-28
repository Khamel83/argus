"""
Crawl4AI content extractor.

Self-hosted, open-source extraction with JS rendering and LLM-aware chunking.
Install: pip install crawl4ai
Gate behind ARGUS_CRAWL4AI_ENABLED=true
"""

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.ssrf import is_safe_url
from argus.logging import get_logger

logger = get_logger("extraction.crawl4ai")


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

        if not result or not result.markdown:
            return ExtractedContent(url=url, error="crawl4ai: no content extracted")

        final_url = getattr(result, "url", None) or getattr(result, "redirected_url", None) or url
        if final_url != url:
            safe, reason = is_safe_url(final_url)
            if not safe:
                return ExtractedContent(url=url, error=f"crawl4ai: unsafe redirect blocked: {reason}")

        text = result.markdown.strip()
        if not text or len(text) < 50:
            return ExtractedContent(url=url, error="crawl4ai: content too short")

        return ExtractedContent(
            url=final_url,
            title=result.metadata.get("title", "") if result.metadata else "",
            text=text,
            word_count=len(text.split()),
            extractor=ExtractorName.CRAWL4AI,
        )
    except Exception as e:
        logger.warning("Crawl4AI extraction failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"crawl4ai: {e}")
