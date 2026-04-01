"""
Content extraction: trafilatura primary, Jina Reader fallback.

Hybrid strategy: try trafilatura (local, fast, free) first.
If it returns empty or fails, fall back to Jina Reader API.
"""

import asyncio
import os

import httpx

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction")

DEFAULT_TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "10"))
JINA_READER_URL = "https://r.jina.ai/"
JINA_API_KEY = os.getenv("ARGUS_JINA_API_KEY", "")


async def _extract_trafilatura(url: str) -> ExtractedContent:
    """Extract content using trafilatura (local, no API call)."""
    import trafilatura

    loop = asyncio.get_event_loop()

    downloaded = await loop.run_in_executor(None, trafilatura.fetch_url, url)
    if not downloaded:
        return ExtractedContent(url=url, error="trafilatura: failed to fetch URL")

    extracted = await loop.run_in_executor(None, trafilatura.bare_extraction, downloaded)
    if not extracted or not extracted.get("text"):
        return ExtractedContent(url=url, error="trafilatura: no content extracted")

    text = extracted["text"]
    return ExtractedContent(
        url=url,
        title=extracted.get("title", ""),
        text=text,
        author=extracted.get("author", ""),
        date=extracted.get("date"),
        word_count=len(text.split()),
        extractor=ExtractorName.TRAFILATURA,
    )


async def _extract_jina(url: str) -> ExtractedContent:
    """Extract content using Jina Reader API (external fallback)."""
    headers = {"Accept": "text/plain"}
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"

    reader_url = f"{JINA_READER_URL}{url}"

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(reader_url, headers=headers, follow_redirects=True)
        resp.raise_for_status()

    text = resp.text.strip()
    if not text or len(text) < 50:
        return ExtractedContent(url=url, error="jina: response too short or empty")

    # Jina returns markdown — first line is usually the title
    lines = text.split("\n", 1)
    title = lines[0].lstrip("# ").strip() if lines else ""
    body = lines[1].strip() if len(lines) > 1 else text

    return ExtractedContent(
        url=url,
        title=title,
        text=body,
        word_count=len(body.split()),
        extractor=ExtractorName.JINA,
    )


async def extract_url(url: str) -> ExtractedContent:
    """Extract clean content from a URL.

    Tries trafilatura first (local), falls back to Jina Reader (external).
    """
    # Try trafilatura first
    try:
        result = await _extract_trafilatura(url)
        if result.text and not result.error:
            logger.info("Extracted %s via trafilatura (%d words)", url[:60], result.word_count)
            return result
        logger.debug("Trafilatura returned no content for %s: %s", url[:60], result.error)
    except Exception as e:
        logger.warning("Trafilatura failed for %s: %s", url[:60], e)

    # Fallback to Jina
    try:
        result = await _extract_jina(url)
        if result.text and not result.error:
            logger.info("Extracted %s via Jina fallback (%d words)", url[:60], result.word_count)
            return result
        logger.warning("Jina returned no content for %s: %s", url[:60], result.error)
        return ExtractedContent(url=url, error=f"jina: {result.error}")
    except Exception as e:
        logger.error("Jina fallback failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"all extractors failed: {e}")
