"""Bounded archive.ph recovery fallback owned by the execution authority."""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

from argus.acquisition.guarded import (
    GuardedAcquisitionError,
    guarded_http_request,
    patched_httpx_client,
)
from argus.acquisition.models import CredentialPolicy, OperationClass, OriginProfile
from argus.logging import get_logger
from argus.extraction.trafilatura_result import normalize_trafilatura_result

logger = get_logger("recovery.archive_ph")


async def try_archive_ph(url: str) -> dict | None:
    """Return one normalized archive result, or no result."""
    archive_url = f"https://archive.ph/newest/{quote_plus(url)}"
    try:
        response = await guarded_http_request(
            archive_url,
            headers={"User-Agent": "ArgusRecovery/1.0"},
            profile=OriginProfile.THIRD_PARTY_FETCH,
            credential_policy=CredentialPolicy.NONE,
            operation_class=OperationClass.THIRD_PARTY,
            caller_principal="recovery:archive-ph",
            request_id="archive-ph-recovery",
            timeout=15,
            target_url=url,
            compat_client_factory=patched_httpx_client(),
        )
    except GuardedAcquisitionError as exc:
        # Recovery is an optional fallback.  Keep the typed policy/transport
        # failure at the guarded boundary and do not expose its details in a
        # result that callers treat as an absent archive.
        logger.debug(
            "Archive.ph recovery unavailable code=%s",
            exc.failure.code.value,
        )
        return None
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
