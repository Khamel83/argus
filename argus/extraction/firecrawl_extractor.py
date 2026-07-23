"""
Firecrawl extraction fallback.

Extracts clean markdown from URLs via POST https://api.firecrawl.dev/v1/scrape.
1 credit per page. Uses v2 API format. Best-in-class Markdown quality with JS rendering.
Gated by ARGUS_FIRECRAWL_API_KEY env var.
"""

import httpx

from argus.config import get_config
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.firecrawl")

FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1/scrape"


async def extract_firecrawl(url: str) -> ExtractedContent:
    """Extract content from a URL using the Firecrawl v2 API."""
    return ExtractedContent(
        url=url,
        error="firecrawl disabled: durable spend reservation is required",
    )

    # Kept below for re-enablement once extraction uses the spend gateway.
    config = get_config()
    api_key = config.firecrawl.api_key
    if not api_key:
        return ExtractedContent(url=url, error="firecrawl: no API key configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {"url": url}

    try:
        async with httpx.AsyncClient(timeout=config.firecrawl.timeout_seconds) as client:
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
