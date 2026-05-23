"""Remote provider client — delegates search to an egress worker node."""

import time
from typing import List

import httpx

from argus.config import EgressNode
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchQuery,
    SearchResult,
)
from argus.providers.base import BaseProvider

logger = get_logger("broker.remote_provider")


class RemoteProviderClient(BaseProvider):
    """Implements BaseProvider by delegating to a worker node's /exec endpoint."""

    def __init__(self, provider: ProviderName, node: EgressNode) -> None:
        self._provider = provider
        self._node = node

    @property
    def name(self) -> ProviderName:
        return self._provider

    def is_available(self) -> bool:
        return True  # health tracker handles degradation

    def status(self) -> ProviderStatus:
        return ProviderStatus.ENABLED

    async def search(self, query: SearchQuery) -> tuple[List[SearchResult], ProviderTrace]:
        start = time.monotonic()
        payload = {
            "provider": self._provider.value,
            "query": query.query,
            "max_results": query.max_results,
            "mode": query.mode.value,
            "caller": query.caller,
        }
        headers = {
            "Authorization": f"Bearer {self._node.shared_secret}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._node.url}/exec",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("Remote provider %s via %s failed: %s",
                           self._provider.value, self._node.name, exc)
            return [], ProviderTrace(
                provider=self._provider,
                status="error",
                latency_ms=latency_ms,
                error=str(exc),
                egress=self._node.name,
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        raw_trace = data.get("trace", {})
        trace = ProviderTrace(
            provider=self._provider,
            status=raw_trace.get("status", "error"),
            results_count=raw_trace.get("results_count", 0),
            latency_ms=latency_ms,
            error=raw_trace.get("error"),
            egress=self._node.name,
        )

        results = []
        for r in data.get("results", []):
            try:
                results.append(SearchResult(
                    url=r["url"],
                    title=r.get("title", ""),
                    snippet=r.get("snippet", ""),
                    domain=r.get("domain", ""),
                    provider=self._provider,
                    score=r.get("score", 0.0),
                    raw_rank=r.get("raw_rank", 0),
                    metadata=r.get("metadata", {}),
                ))
            except Exception:
                continue

        return results, trace