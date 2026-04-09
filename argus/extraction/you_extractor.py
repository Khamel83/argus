"""
You.com Contents API extractor.

Extracts clean markdown from any URL via POST https://ydc-index.io/v1/contents.
$1/1k pages — cheaper than Jina. Uses the same You.com API key.
Gated by ARGUS_YOU_CONTENTS_ENABLED env var.
"""

import os
from typing import Optional

import httpx

from argus.config import get_config
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.you")

YOU_CONTENTS_URL = "https://ydc-index.io/v1/contents"
TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "15"))


async def extract_you_contents(url: str) -> ExtractedContent:
    """Extract content from a URL using the You.com Contents API."""
    config = get_config()
    if not config.you.api_key:
        return ExtractedContent(url=url, error="you_contents: no API key configured")

    headers = {
        "X-API-Key": config.you.api_key,
        "Content-Type": "application/json",
    }
    body = {
        "urls": [url],
        "formats": ["markdown"],
        "crawl_timeout": min(TIMEOUT, 30),
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(YOU_CONTENTS_URL, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        if not data or not isinstance(data, list) or not data[0]:
            return ExtractedContent(url=url, error="you_contents: empty response")

        page = data[0]
        markdown = page.get("markdown", "")
        if not markdown or len(markdown.strip()) < 50:
            return ExtractedContent(url=url, error="you_contents: content too short")

        title = page.get("title", "")
        text = markdown.strip()

        return ExtractedContent(
            url=url,
            title=title,
            text=text,
            word_count=len(text.split()),
            extractor=ExtractorName.YOU_CONTENTS,
        )
    except Exception as e:
        logger.warning("You.com contents extraction failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"you_contents: {e}")
