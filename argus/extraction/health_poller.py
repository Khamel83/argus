"""
Background cookie health poller.

Checks health.json every ARGUS_AUTH_POLL_INTERVAL seconds (default 1800 / 30min).
For any stale domain (except manual-only domains like bloomberg.com), calls the
Mac Mini extract service's /login endpoint to refresh cookies automatically.
"""

import asyncio
import os
from typing import Optional

import httpx

from argus.extraction.cookies import _load_health, _save_health
from argus.logging import get_logger

logger = get_logger("extraction.health_poller")

POLL_INTERVAL = int(os.getenv("ARGUS_AUTH_POLL_INTERVAL", "1800"))

# Domains that require manual cookie refresh — skip auto-login.
# chicagotribune.com: Google OAuth flow, can't automate simple form-fill.
# wsj.com: DataDome CAPTCHA fires before login form even appears in headless Chrome.
MANUAL_ONLY_DOMAINS = {"chicagotribune.com", "wsj.com"}


async def refresh_domain(domain: str, remote_url: str, remote_key: str) -> dict:
    """Trigger a login flow for a single domain on the remote extract service.

    Returns {"success": bool, "message": str, "cookie_count": int (if success)}.
    """
    headers = {"Content-Type": "application/json"}
    if remote_key:
        headers["x-api-key"] = remote_key

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{remote_url}/login",
                json={"domain": domain},
                headers=headers,
            )
        data = resp.json()
    except httpx.ConnectError:
        return {"success": False, "message": f"extract service unreachable: {remote_url}"}
    except httpx.TimeoutException:
        return {"success": False, "message": "extract service login timed out"}
    except Exception as e:
        return {"success": False, "message": str(e)}

    if resp.status_code == 404:
        return {"success": False, "message": data.get("error", "no login flow or credentials")}

    if not data.get("success"):
        return {"success": False, "message": data.get("message", "login failed")}

    # Mark domain healthy in local health.json
    health = _load_health()
    entry = health.setdefault(domain, {})
    entry["status"] = "healthy"
    entry.setdefault("request_count", 0)
    _save_health(health)

    logger.info(
        "Auto-login succeeded for %s (%d cookies refreshed)",
        domain, data.get("cookie_count", 0),
    )
    return data


async def refresh_stale_domains(
    remote_url: Optional[str] = None,
    remote_key: Optional[str] = None,
) -> dict[str, dict]:
    """Refresh all stale domains. Returns {domain: result} mapping."""
    url = remote_url or os.getenv("ARGUS_REMOTE_EXTRACT_URL", "")
    key = remote_key or os.getenv("ARGUS_REMOTE_EXTRACT_KEY", "")

    if not url:
        logger.debug("No ARGUS_REMOTE_EXTRACT_URL configured — skipping health poll")
        return {}

    health = _load_health()
    stale = [
        domain for domain, data in health.items()
        if data.get("status") == "stale" and domain not in MANUAL_ONLY_DOMAINS
    ]

    if not stale:
        logger.debug("No stale domains to refresh")
        return {}

    logger.info("Auto-refreshing %d stale domain(s): %s", len(stale), ", ".join(stale))
    results = {}
    for domain in stale:
        results[domain] = await refresh_domain(domain, url, key)

    return results


async def _poll_loop():
    """Background task: poll and refresh stale cookies every POLL_INTERVAL seconds."""
    logger.info("Cookie health poller started (interval=%ds)", POLL_INTERVAL)
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            results = await refresh_stale_domains()
            if results:
                succeeded = [d for d, r in results.items() if r.get("success")]
                failed = [d for d, r in results.items() if not r.get("success")]
                if succeeded:
                    logger.info("Auto-login succeeded: %s", ", ".join(succeeded))
                if failed:
                    logger.warning(
                        "Auto-login failed (manual refresh needed): %s",
                        ", ".join(f"{d} ({results[d].get('message', '?')})" for d in failed),
                    )
        except Exception as e:
            logger.error("Health poller error: %s", e)


_poller_task: Optional[asyncio.Task] = None


def start_poller() -> None:
    """Start the background polling task. Call from FastAPI lifespan startup."""
    global _poller_task
    remote_url = os.getenv("ARGUS_REMOTE_EXTRACT_URL", "")
    if not remote_url:
        logger.debug("ARGUS_REMOTE_EXTRACT_URL not set — health poller disabled")
        return
    _poller_task = asyncio.ensure_future(_poll_loop())
    logger.info("Cookie health poller scheduled")


def stop_poller() -> None:
    """Cancel the background polling task. Call from FastAPI lifespan shutdown."""
    global _poller_task
    if _poller_task and not _poller_task.done():
        _poller_task.cancel()
        _poller_task = None
