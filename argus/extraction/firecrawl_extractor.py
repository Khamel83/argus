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

FIRECRAWL_API_URL = "https://api.firecrawl.dev/v2/scrape"


def parse_firecrawl_v2_response(url: str, payload: object) -> ExtractedContent:
    """Normalize the dormant v2 response without exposing native bodies."""
    if not isinstance(payload, dict):
        return ExtractedContent(url=url, error="firecrawl: malformed response")
    if payload.get("success") is not True:
        return ExtractedContent(url=url, error="firecrawl: extraction failed")
    result = payload.get("data")
    if not isinstance(result, dict):
        return ExtractedContent(url=url, error="firecrawl: malformed response")
    markdown = result.get("markdown")
    if not isinstance(markdown, str) or len(markdown.strip()) < 50:
        return ExtractedContent(url=url, error="firecrawl: content too short")
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    status = metadata.get("statusCode")
    if (type(status) is int and status >= 400) or metadata.get("error"):
        return ExtractedContent(url=url, error="firecrawl: extraction failed")
    text = markdown.strip()
    return ExtractedContent(
        url=url,
        title=str(metadata.get("title") or result.get("title") or "")[:1_000],
        text=text,
        author=str(metadata.get("author") or "")[:256],
        date=None,
        word_count=len(text.split()),
        extractor=ExtractorName.FIRECRAWL,
    )


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
    body = {"url": url, "formats": ["markdown"]}

    try:
        async with httpx.AsyncClient(
            timeout=config.firecrawl.timeout_seconds
        ) as client:
            resp = await client.post(FIRECRAWL_API_URL, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return parse_firecrawl_v2_response(url, data)
    except Exception as e:
        logger.warning(
            "Firecrawl extraction failed for %s: %s",
            url[:60],
            type(e).__name__,
        )
        return ExtractedContent(url=url, error="firecrawl: extraction failed")
