"""
Authenticated content extraction via remote service.

Calls the Mac Mini extraction service (Playwright + cookies) over Tailscale.
Falls back gracefully to trafilatura/Jina if the service is unavailable.
"""

import os
from typing import Optional

import httpx

from argus.extraction.cookies import needs_auth
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.auth")

REMOTE_URL = os.getenv("ARGUS_REMOTE_EXTRACT_URL", "")
REMOTE_KEY = os.getenv("ARGUS_REMOTE_EXTRACT_KEY", "")
REMOTE_TIMEOUT = int(os.getenv("ARGUS_REMOTE_EXTRACT_TIMEOUT", "35"))

# Lazy-initialized HTTP client
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=REMOTE_TIMEOUT)
    return _client


async def shutdown_browser():
    """No-op — browser is remote now. Kept for API compatibility."""
    pass


async def extract_authenticated(url: str, domain: str) -> Optional[ExtractedContent]:
    """Extract content via the remote Mac Mini extraction service.

    Returns None if the service is not configured, unreachable, or extraction fails.
    Caller should fall back to regular extract_url() in that case.
    """
    if not needs_auth(url):
        return None

    if not REMOTE_URL:
        return None

    # Rate limiting and stale-cookie checks still apply locally
    from argus.extraction.cookies import can_authenticate, record_auth_request
    if not can_authenticate(domain):
        return None

    client = _get_client()
    headers = {"Content-Type": "application/json"}
    if REMOTE_KEY:
        headers["x-api-key"] = REMOTE_KEY

    try:
        resp = await client.post(
            f"{REMOTE_URL}/extract",
            json={"url": url, "domain": domain},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError:
        logger.debug("Remote extract service unreachable: %s", REMOTE_URL)
        return None
    except httpx.TimeoutException:
        logger.warning("Remote extract service timed out for %s", url[:60])
        return None
    except httpx.HTTPStatusError as e:
        logger.warning("Remote extract service HTTP error: %s", e)
        return None
    except Exception as e:
        logger.warning("Remote extract failed for %s: %s", url[:60], e)
        return None

    if "error" in data:
        status_code = data.get("status_code", 0)
        logger.info("Remote extract error for %s: %s (HTTP %d)", url[:60], data["error"], status_code)
        record_auth_request(domain, success=False, status_code=status_code)
        return None

    text = data.get("text", "")
    if not text or len(text) < 200:
        logger.info("Remote extract too short for %s (%d chars)", url[:60], len(text))
        return None

    title = data.get("title", "")
    word_count = data.get("word_count", len(text.split()))
    status_code = data.get("status_code", 0)

    logger.info(
        "Remote auth extraction for %s: %d words (HTTP %d)",
        url[:60], word_count, status_code,
    )
    record_auth_request(domain, success=True, status_code=status_code)

    return ExtractedContent(
        url=url,
        title=title,
        text=text,
        word_count=word_count,
        extractor=ExtractorName.AUTH_PLAYWRIGHT,
    )
