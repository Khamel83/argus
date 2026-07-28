"""
Archive.is Extraction - look up content from archive.today/archive.is/archive.ph.

Default extraction performs lookup only. Creating an external archive is a
separate, explicitly authorized irreversible action.

Rate limited to 1 request per 5 seconds.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
import time
from typing import Optional, Protocol
from urllib.parse import urlsplit

import httpx

from argus.contracts import CanonicalOutcome
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.trafilatura_result import normalize_trafilatura_result
from argus.logging import get_logger

logger = get_logger("extraction.archive_is")

ARCHIVE_DOMAINS = ["archive.ph", "archive.is", "archive.today"]
ARCHIVE_SUBMIT_URL = "https://archive.ph/submit"
ARCHIVE_NEWEST_URL = "https://archive.ph/newest/"

# Rate limiting: 1 request per 5 seconds
_min_interval = 5.0
_last_request_time = 0.0
_lock = None
_SAFE_AUTHORITY_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ArchiveCreationAuthorization:
    """One-use authority for a caller-approved external archive creation."""

    caller_policy_ref: str
    authority_receipt: str
    idempotency_key: str
    bounded_target: str
    profile: str


class ArchiveCreationPolicyRejected(RuntimeError):
    outcome = CanonicalOutcome.POLICY_REJECTED

    def __init__(self):
        super().__init__("external archive creation is not authorized")


class ArchiveCreationAuthority(Protocol):
    """Verify a receipt against the external authority that issued it."""

    def verify(self, authorization: ArchiveCreationAuthorization) -> bool: ...


class ArchiveCreationAuthorizationStore(Protocol):
    """Durable atomic consume; the tuple must remain spent across restarts."""

    def consume(
        self,
        *,
        receipt: str,
        idempotency_key: str,
        target: str,
    ) -> bool: ...


class SQLiteArchiveCreationAuthorizationStore:
    """Restart-durable atomic one-use archive authorization store."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS archive_creation_consumptions ("
                "receipt TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL, "
                "target_identity TEXT NOT NULL, "
                "PRIMARY KEY (receipt, idempotency_key, target_identity))"
            )

    def consume(self, *, receipt: str, idempotency_key: str, target: str) -> bool:
        import hashlib

        target_identity = hashlib.sha256(target.encode("utf-8")).hexdigest()
        with sqlite3.connect(self.path, timeout=5) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO archive_creation_consumptions "
                "(receipt, idempotency_key, target_identity) VALUES (?, ?, ?)",
                (receipt, idempotency_key, target_identity),
            )
            return cursor.rowcount == 1


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


async def _submit_authorized(url: str) -> Optional[str]:
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


async def _submit_and_fetch(
    url: str,
    authorization: ArchiveCreationAuthorization | None = None,
) -> Optional[str]:
    """Compatibility name that now enforces the explicit authorization seam."""
    return await create_archive(url, authorization=authorization)


def _validate_creation_authorization(
    url: str,
    authorization: ArchiveCreationAuthorization | None,
) -> None:
    if not isinstance(authorization, ArchiveCreationAuthorization):
        raise ArchiveCreationPolicyRejected()
    values = (
        authorization.caller_policy_ref,
        authorization.authority_receipt,
        authorization.idempotency_key,
    )
    if any(_SAFE_AUTHORITY_REF.fullmatch(value) is None for value in values):
        raise ArchiveCreationPolicyRejected()
    if (
        authorization.profile == "autonomous"
        or not isinstance(authorization.profile, str)
        or _SAFE_AUTHORITY_REF.fullmatch(authorization.profile) is None
        or authorization.bounded_target != url
        or len(url.encode("utf-8")) > 2048
    ):
        raise ArchiveCreationPolicyRejected()
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise ArchiveCreationPolicyRejected()


async def create_archive(
    url: str,
    *,
    authorization: ArchiveCreationAuthorization | None = None,
    authority: ArchiveCreationAuthority | None = None,
    authorization_store: ArchiveCreationAuthorizationStore | None = None,
) -> Optional[str]:
    """Create an external archive only under a fresh, bounded authority receipt."""
    _validate_creation_authorization(url, authorization)
    if authority is None or authorization_store is None:
        raise ArchiveCreationPolicyRejected()
    try:
        verified = authority.verify(authorization) is True
        consumed = (
            verified
            and authorization_store.consume(
                receipt=authorization.authority_receipt,
                idempotency_key=authorization.idempotency_key,
                target=url,
            )
            is True
        )
    except Exception as error:
        raise ArchiveCreationPolicyRejected() from error
    if not verified or not consumed:
        raise ArchiveCreationPolicyRejected()
    return await _submit_authorized(url)


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

        if not archive_url:
            return ExtractedContent(
                url=url,
                error="archive_is: no existing archive found",
            )

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

        normalized = normalize_trafilatura_result(extracted)
        if normalized is None:
            return ExtractedContent(url=url, error="archive_is: extraction returned no text")

        text = normalized.text
        return ExtractedContent(
            url=url,
            title=normalized.title,
            text=text,
            author=normalized.author,
            date=normalized.date,
            word_count=len(text.split()),
            extractor=ExtractorName.ARCHIVE_IS,
        )
    except Exception as e:
        logger.debug("Archive.is extraction failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"archive_is: {e}")


async def extract_archive_is(url: str) -> ExtractedContent:
    """Lookup-only public extraction entry point."""
    return await _extract_archive(url)
