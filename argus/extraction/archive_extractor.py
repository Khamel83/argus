"""
Archive.is Extraction - Fetch content from archive.today/archive.is/archive.ph.

Two approaches:
1. Search for existing archive: GET /newest/{url}
2. Submit URL for archiving: POST /submit/?url={url}

Rate limited to 1 request per 5 seconds.
"""

import asyncio
import re
import time
from typing import Optional

import httpx

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.archive_is")

ARCHIVE_DOMAINS = ["archive.ph", "archive.is", "archive.today"]
ARCHIVE_SUBMIT_URL = "https://archive.ph/submit"
ARCHIVE_NEWEST_URL = "https://archive.ph/newest/"

# Rate limiting: 1 request per 5 seconds
_min_interval = 5.0
_last_request_time = 0.0
_lock = None


def _get_lock():
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def _rate_limit():
    global _last_request_time
    async with _get_lock():
        now = time.monotonic()
        wait = _min_interval - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_time = time.monotonic()


async def _search_existing(url: str) -> Optional[str]:
    """
    Search for an existing archive of the URL.

    Returns:
        Archive URL if found, None otherwise
    """
    await _rate_limit()

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(f"{ARCHIVE_NEWEST_URL}{url}")
            # archive.ph redirects to the archive page if one exists
            # If the URL is the same as what we requested, no archive exists
            if resp.status_code == 200 and resp.url:
                final_url = str(resp.url)
                # If we were redirected to an archive page (contains /<id>/)
                if re.search(r'archive\.(ph|is|today)/\w+/', final_url):
                    return final_url
        return None
    except Exception as e:
        logger.debug("Archive.is search failed for %s: %s", url[:60], e)
        return None


async def _submit_and_fetch(url: str) -> Optional[str]:
    """
    Submit URL for archiving and wait for the result.

    Returns:
        Archive URL if successful, None otherwise
    """
    await _rate_limit()

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.post(
                f"{ARCHIVE_SUBMIT_URL}",
                data={"url": url},
                headers={"User-Agent": "Argus/1.0"},
            )
            if resp.status_code == 200:
                # The response usually contains the archive URL
                # Try to find it in the response body or Location header
                final_url = str(resp.url)
                if re.search(r'archive\.(ph|is|today)/\w+/', final_url):
                    return final_url

                # Check response text for archive ID
                match = re.search(r'archive\.(ph|is|today)/(\w+)/', resp.text)
                if match:
                    domain = match.group(1)
                    archive_id = match.group(2)
                    return f"https://archive.{domain}/{archive_id}/{url}"
        return None
    except Exception as e:
        logger.debug("Archive.is submit failed for %s: %s", url[:60], e)
        return None


async def _extract_archive(url: str) -> ExtractedContent:
    """
    Extract content from archive.is.

    Args:
        url: Original URL to look up

    Returns:
        ExtractedContent with archived text, or error
    """
    try:
        # Step 1: Search for existing archive
        archive_url = await _search_existing(url)

        # Step 2: If not found, submit for archiving
        if not archive_url:
            archive_url = await _submit_and_fetch(url)

        if not archive_url:
            return ExtractedContent(url=url, error="archive_is: no archive found or created")

        logger.info("Archive.is found for %s: %s", url[:60], archive_url[:80])

        # Step 3: Fetch archived content
        await _rate_limit()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(archive_url, headers={"User-Agent": "Argus/1.0"})
            resp.raise_for_status()
            html = resp.text

        # Extract text using trafilatura
        import trafilatura
        loop = asyncio.get_event_loop()

        downloaded = await loop.run_in_executor(
            None, lambda: trafilatura.fetch_url(archive_url)
        )
        if not downloaded:
            downloaded = html

        extracted = await loop.run_in_executor(
            None, lambda: trafilatura.bare_extraction(downloaded)
        )

        if not extracted or not extracted.get("text"):
            return ExtractedContent(url=url, error="archive_is: extraction returned no text")

        text = extracted["text"]
        return ExtractedContent(
            url=url,
            title=extracted.get("title", ""),
            text=text,
            author=extracted.get("author", ""),
            word_count=len(text.split()),
            extractor=ExtractorName.ARCHIVE_IS,
        )
    except Exception as e:
        logger.debug("Archive.is extraction failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"archive_is: {e}")


async def extract_archive_is(url: str) -> ExtractedContent:
    """Public entry point for Archive.is extraction."""
    return await _extract_archive(url)
