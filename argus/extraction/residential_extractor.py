"""
Residential extraction — sends extraction requests to remote services over Tailscale.

Routes browser-based extraction through residential IPs (homelab/macmini) to bypass
datacenter IP blocks. Supports multiple endpoints with automatic failover and
per-endpoint circuit breakers with TTL recovery.

Enable by setting ARGUS_RESIDENTIAL_ENDPOINTS (comma-separated URLs) or the legacy
ARGUS_RESIDENTIAL_EXTRACTOR_URL (single endpoint, backward compatible).
"""

import time
from typing import Optional

import httpx

from argus.extraction.cookies import load_editthiscookie_json, get_cookie_path
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.residential")

import os

_endpoints = os.getenv("ARGUS_RESIDENTIAL_ENDPOINTS", "")
_legacy_url = os.getenv("ARGUS_RESIDENTIAL_EXTRACTOR_URL", "")
RESIDENTIAL_ENDPOINTS = [u.strip() for u in _endpoints.split(",") if u.strip()] if _endpoints else ([_legacy_url] if _legacy_url else [])
RESIDENTIAL_TIMEOUT = int(os.getenv("ARGUS_RESIDENTIAL_EXTRACTOR_TIMEOUT_SECONDS", "30"))
CIRCUIT_BREAKER_COOLDOWN = 60.0


def _shared_secret() -> str:
    return os.getenv("ARGUS_RESIDENTIAL_SHARED_SECRET", "").strip()


class _EndpointHealth:
    """Per-endpoint circuit breaker with TTL recovery."""

    def __init__(self):
        self._unhealthy_until: dict[str, float] = {}

    def is_healthy(self, url: str) -> bool:
        until = self._unhealthy_until.get(url)
        if until is None:
            return True
        if time.monotonic() > until:
            del self._unhealthy_until[url]
            return True
        return False

    def mark_unhealthy(self, url: str, cooldown: float = CIRCUIT_BREAKER_COOLDOWN):
        self._unhealthy_until[url] = time.monotonic() + cooldown

    def mark_healthy(self, url: str):
        self._unhealthy_until.pop(url, None)


_endpoint_health = _EndpointHealth()


def _is_configured() -> bool:
    return bool(RESIDENTIAL_ENDPOINTS)


def _load_cookies_for_domain(domain: str) -> Optional[list[dict]]:
    """Load cookies for a domain if available."""
    if not domain:
        return None
    cookie_path = get_cookie_path(domain)
    if cookie_path is None:
        return None
    try:
        cookies = load_editthiscookie_json(cookie_path)
        return cookies if cookies else None
    except Exception as e:
        logger.debug("Failed to load cookies for %s: %s", domain, e)
        return None


async def _try_endpoint(url: str, endpoint: str, cookies: Optional[list[dict]], domain: Optional[str]) -> Optional[ExtractedContent]:
    """Try a single endpoint. Returns ExtractedContent on success, None on failure."""
    body: dict = {"url": url}
    secret = _shared_secret()
    if cookies:
        body["cookies"] = cookies
    if domain:
        body["domain"] = domain

    try:
        async with httpx.AsyncClient(timeout=RESIDENTIAL_TIMEOUT) as client:
            resp = await client.post(
                f"{endpoint}/extract",
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {secret}",
                },
            )

        if resp.status_code == 200:
            _endpoint_health.mark_healthy(endpoint)
            data = resp.json()
            return ExtractedContent(
                url=data.get("url", url),
                title=data.get("title", ""),
                text=data.get("text", ""),
                author=data.get("author", ""),
                date=data.get("date"),
                word_count=data.get("word_count", 0),
                extractor=ExtractorName.RESIDENTIAL,
            )

        if resp.status_code == 503:
            _endpoint_health.mark_unhealthy(endpoint)
            logger.warning("Residential service %s returned 503 — cooling down", endpoint)
            return None

        _endpoint_health.mark_unhealthy(endpoint)
        logger.warning("Residential service %s returned %d", endpoint, resp.status_code)
        return None

    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        _endpoint_health.mark_unhealthy(endpoint)
        logger.debug("Residential endpoint %s unreachable", endpoint)
        return None
    except Exception as e:
        logger.debug("Residential extraction failed for endpoint %s: %s", endpoint, e)
        return None


async def extract_residential(url: str, domain: str = "") -> ExtractedContent:
    """Extract content via remote residential service with failover."""
    if not _is_configured():
        return ExtractedContent(url=url, error="residential: not configured")
    if not _shared_secret():
        return ExtractedContent(url=url, error="residential: shared secret not configured")

    cookies = _load_cookies_for_domain(domain) if domain else None

    last_error = "residential: all endpoints unavailable"
    for endpoint in RESIDENTIAL_ENDPOINTS:
        if not _endpoint_health.is_healthy(endpoint):
            logger.debug("Skipping unhealthy endpoint %s", endpoint)
            continue

        result = await _try_endpoint(url, endpoint, cookies, domain or None)
        if result is not None:
            return result
        last_error = f"residential: endpoint {endpoint} failed"

    return ExtractedContent(url=url, error=last_error)


def reset_reachability():
    """Reset all endpoint health status (e.g. for health check recovery)."""
    _endpoint_health._unhealthy_until.clear()
