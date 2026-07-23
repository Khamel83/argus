"""Bounded archive.ph recovery fallback owned by the execution authority."""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

import httpx

from argus.extraction.trafilatura_result import normalize_trafilatura_result


async def try_archive_ph(url: str) -> dict | None:
    """Return one normalized archive result, or no result."""
    archive_url = f"https://archive.ph/newest/{quote_plus(url)}"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(
            archive_url,
            headers={"User-Agent": "ArgusRecovery/1.0"},
        )
    if response.status_code != 200:
        return None
    if (
        "does not have an archive" in response.text
        or "was not archived" in response.text
    ):
        return None

    import trafilatura

    extracted = await asyncio.to_thread(
        trafilatura.bare_extraction,
        response.text,
    )
    normalized = normalize_trafilatura_result(extracted)
    if normalized is None or len(normalized.text) <= 200:
        return None
    return {
        "url": str(response.url),
        "title": normalized.title or "",
        "snippet": normalized.text[:200],
        "domain": "archive.ph",
        "score": 0.8,
    }
