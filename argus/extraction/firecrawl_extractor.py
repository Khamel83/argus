"""
Firecrawl extraction fallback.

Extracts clean markdown from URLs via POST https://api.firecrawl.dev/v1/scrape.
1 credit per page. Uses v2 API format. Best-in-class Markdown quality with JS rendering.
Gated by ARGUS_FIRECRAWL_API_KEY env var.
"""

import os

import httpx

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.firecrawl")

FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1/scrape"
TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "30"))


async def extract_firecrawl(url: str) -> ExtractedContent:
    """Extract content from a URL using the Firecrawl v2 API."""
    api_key = os.getenv("ARGUS_FIRECRAWL_API_KEY", "")
    if not api_key:
        return ExtractedContent(url=url, error="firecrawl: no API key configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {"url": url}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(FIRECRAWL_API_URL, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if not data.get("success"):
            return ExtractedContent(url=url, error=f"firecrawl: {data.get('error', 'extraction failed')}")

        result = data.get("data", {})
        markdown = result.get("markdown", "")
        if not markdown or len(markdown.strip()) < 50:
            return ExtractedContent(url=url, error="firecrawl: content too short")

        metadata = result.get("metadata", {})
        title = metadata.get("title", "") or result.get("title", "")

        return ExtractedContent(
            url=url,
            title=title,
            text=markdown.strip(),
            author=metadata.get("author", ""),
            date=None,
            word_count=len(markdown.split()),
            extractor=ExtractorName.FIRECRAWL,
        )
    except Exception as e:
        logger.warning("Firecrawl extraction failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"firecrawl: {e}")
