"""
Proactive balance checking for providers that expose credit/usage APIs.

Queries provider APIs for remaining credits and persists the results
to the budget store. Designed to run once per day.
"""

import time
from dataclasses import dataclass
from typing import Optional

import httpx

from argus.logging import get_logger
from argus.models import ProviderName

logger = get_logger("broker.balance_check")


@dataclass
class ProviderBalance:
    """Snapshot of a provider's remaining credits."""
    provider: ProviderName
    remaining: Optional[float] = None
    limit: Optional[float] = None
    used: Optional[float] = None
    unit: str = "queries"  # or "usd", "tokens"
    source: str = ""  # e.g. "api", "headers"
    raw: Optional[dict] = None
    error: Optional[str] = None


async def check_tavily(api_key: str) -> ProviderBalance:
    """GET https://api.tavily.com/usage — returns usage, limit, plan info."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.tavily.com/usage",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        key_info = data.get("key", {})
        account_info = data.get("account", {})

        usage = key_info.get("usage", 0)
        limit = key_info.get("limit", 0)
        remaining = max(0, limit - usage)

        return ProviderBalance(
            provider=ProviderName.TAVILY,
            remaining=remaining,
            limit=limit,
            used=usage,
            unit="queries",
            source="api",
            raw=data,
        )
    except Exception as e:
        logger.warning("Tavily balance check failed: %s", e)
        return ProviderBalance(provider=ProviderName.TAVILY, error=str(e))


async def check_serper(api_key: str) -> ProviderBalance:
    """Serper exposes credits in search responses. Probe with a minimal query."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": "test", "num": 1},
            )
            resp.raise_for_status()
            data = resp.json()

        credits = data.get("credits", None)
        return ProviderBalance(
            provider=ProviderName.SERPER,
            remaining=credits,
            unit="credits",
            source="response_body",
            raw={"credits": credits},
        )
    except Exception as e:
        logger.warning("Serper balance check failed: %s", e)
        return ProviderBalance(provider=ProviderName.SERPER, error=str(e))


async def check_brave(api_key: str) -> ProviderBalance:
    """Brave exposes rate-limit headers. Probe with a minimal query."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": "test", "count": 1},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
            )
            resp.raise_for_status()

        info = {}
        for hdr in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Used"):
            val = resp.headers.get(hdr)
            if val:
                info[hdr] = val

        remaining = None
        if "X-RateLimit-Remaining" in info:
            try:
                remaining = float(info["X-RateLimit-Remaining"])
            except ValueError:
                pass

        return ProviderBalance(
            provider=ProviderName.BRAVE,
            remaining=remaining,
            unit="queries",
            source="headers",
            raw=info,
        )
    except Exception as e:
        logger.warning("Brave balance check failed: %s", e)
        return ProviderBalance(provider=ProviderName.BRAVE, error=str(e))


async def check_parallel(api_key: str) -> ProviderBalance:
    """Parallel AI exposes X-RateLimit headers."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.parallel.ai/v1beta/search",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "parallel-beta": "search-extract-2025-10-10",
                },
                json={"objective": "test", "search_queries": ["test"], "max_results": 1},
            )
            resp.raise_for_status()

        info = {}
        for hdr in ("X-RateLimit-Remaining-Requests", "X-RateLimit-Limit-Requests"):
            val = resp.headers.get(hdr)
            if val:
                info[hdr] = val

        remaining = None
        if "X-RateLimit-Remaining-Requests" in info:
            try:
                remaining = float(info["X-RateLimit-Remaining-Requests"])
            except ValueError:
                pass

        return ProviderBalance(
            provider=ProviderName.PARALLEL,
            remaining=remaining,
            unit="queries",
            source="headers",
            raw=info,
        )
    except Exception as e:
        logger.warning("Parallel balance check failed: %s", e)
        return ProviderBalance(provider=ProviderName.PARALLEL, error=str(e))


async def check_linkup(api_key: str) -> ProviderBalance:
    """Linkup may expose rate-limit headers."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.linkup.so/v1/search",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                json={"q": "test", "depth": "standard", "outputType": "searchResults"},
            )
            resp.raise_for_status()

        info = {}
        for hdr in ("X-RateLimit-Remaining", "X-RateLimit-Limit", "X-Credits-Remaining"):
            val = resp.headers.get(hdr)
            if val:
                info[hdr] = val

        remaining = None
        if "X-Credits-Remaining" in info:
            try:
                remaining = float(info["X-Credits-Remaining"])
            except ValueError:
                pass
        elif "X-RateLimit-Remaining" in info:
            try:
                remaining = float(info["X-RateLimit-Remaining"])
            except ValueError:
                pass

        return ProviderBalance(
            provider=ProviderName.LINKUP,
            remaining=remaining,
            unit="queries",
            source="headers",
            raw=info,
        )
    except Exception as e:
        logger.warning("Linkup balance check failed: %s", e)
        return ProviderBalance(provider=ProviderName.LINKUP, error=str(e))


# Mapping: provider -> checker function
_CHECKERS = {
    ProviderName.TAVILY: check_tavily,
    ProviderName.SERPER: check_serper,
    ProviderName.BRAVE: check_brave,
    ProviderName.PARALLEL: check_parallel,
    ProviderName.LINKUP: check_linkup,
}


async def check_all_balances(api_keys: dict[ProviderName, str]) -> list[ProviderBalance]:
    """Check balances for all providers that have API keys configured."""
    results = []
    for provider, checker in _CHECKERS.items():
        key = api_keys.get(provider)
        if not key:
            continue
        balance = await checker(key)
        results.append(balance)
    return results


def persist_balances(
    balances: list[ProviderBalance],
    store=None,
) -> None:
    """Write balance results to the budget store."""
    if store is None:
        return
    for b in balances:
        if b.remaining is not None:
            # Use token_balances table with provider name as service
            store.set_token_balance(b.provider.value, b.remaining)
            logger.info(
                "%s: %.0f %s remaining (source: %s)",
                b.provider.value, b.remaining, b.unit, b.source,
            )
