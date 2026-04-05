"""
Wayback Machine Extraction - Fetch content from the Internet Archive.

Two-step approach:
1. Check availability via Wayback Availability API
2. If available, fetch the archived content

Rate limited to 6 requests/minute per Wayback guidelines.
"""

import asyncio
import time
from typing import Optional

import httpx

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.wayback")

AVAILABILITY_URL = "https://archive.org/wayback/available"
WAYBACK_CONTENT_PREFIX = "https://web.archive.org/web"

# Rate limiting: 6 requests per minute
_min_interval = 10.0  # seconds between requests
_last_request_time = 0.0
_lock = None


def _get_lock():
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def _rate_limit():
    """Enforce minimum interval between Wayback requests."""
    global _last_request_time
    async with _get_lock():
        now = time.monotonic()
        wait = _min_interval - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_time = time.monotonic()


async def _check_availability(url: str) -> Optional[str]:
    """
    Check if URL is archived in Wayback Machine.

    Returns:
        Wayback URL if available, None otherwise
    """
    await _rate_limit()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(AVAILABILITY_URL, params={"url": url})
            resp.raise_for_status()
            data = resp.json()

        if data.get("archived_snapshots"):
            snapshot = data["archived_snapshots"].get("closest")
            if snapshot and snapshot.get("status") == "200" and snapshot.get("url"):
                return snapshot["url"]

        return None
    except Exception as e:
        logger.debug("Wayback availability check failed for %s: %s", url[:60], e)
        return None


async def _fetch_archived(wayback_url: str) -> str:
    """Fetch content from a Wayback Machine snapshot URL."""
    await _rate_limit()

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(wayback_url, headers={"User-Agent": "Argus/1.0"})
        resp.raise_for_status()
        return resp.text


async def _extract_wayback(url: str) -> ExtractedContent:
    """
    Extract content from the Wayback Machine.

    Args:
        url: Original URL to look up in the archive

    Returns:
        ExtractedContent with archived text, or error if not available
    """
    try:
        # Step 1: Check availability
        wayback_url = await _check_availability(url)
        if not wayback_url:
            return ExtractedContent(url=url, error="wayback: not archived")

        logger.info("Wayback found archive for %s: %s", url[:60], wayback_url[:80])

        # Step 2: Fetch archived content
        html = await _fetch_archived(wayback_url)

        # Extract text from HTML using trafilatura
        import trafilatura
        loop = asyncio.get_event_loop()
        downloaded = await loop.run_in_executor(
            None, lambda: trafilatura.fetch_url(wayback_url)
        )
        if not downloaded:
            # Fallback: use the raw HTML we already fetched
            downloaded = html

        extracted = await loop.run_in_executor(
            None, lambda: trafilatura.bare_extraction(downloaded)
        )

        if not extracted or not extracted.get("text"):
            return ExtractedContent(url=url, error="wayback: extraction returned no text")

        text = extracted["text"]
        return ExtractedContent(
            url=url,
            title=extracted.get("title", ""),
            text=text,
            author=extracted.get("author", ""),
            date=extracted.get("date"),
            word_count=len(text.split()),
            extractor=ExtractorName.WAYBACK,
        )
    except Exception as e:
        logger.debug("Wayback extraction failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"wayback: {e}")


async def extract_wayback(url: str) -> ExtractedContent:
    """Public entry point for Wayback extraction."""
    return await _extract_wayback(url)
