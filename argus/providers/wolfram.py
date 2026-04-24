"""
WolframAlpha LLM API provider.

Returns computed answers, not a list of URLs. Best for factual queries,
calculations, unit conversions, and definitions — things web search is bad at.

Free tier: 2,000 calls/month. Requires ARGUS_WOLFRAM_API_KEY (or WOLFRAM_APP_ID).
Get a key at https://developer.wolframalpha.com/
"""

import time
from urllib.parse import quote_plus, urlparse
from typing import List, Tuple

import httpx

from argus.config import ProviderConfig
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchResult,
    SearchQuery,
)
from argus.providers.base import BaseProvider

logger = get_logger("providers.wolfram")

WOLFRAM_LLM_API = "https://www.wolframalpha.com/api/v1/llm-api"
WOLFRAM_QUERY_URL = "https://www.wolframalpha.com/input?i={}"


class WolframProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.WOLFRAM

    def is_available(self) -> bool:
        return self._config.enabled and bool(self._config.api_key)

    def status(self) -> ProviderStatus:
        if not self._config.enabled:
            return ProviderStatus.DISABLED_BY_CONFIG
        if not self._config.api_key:
            return ProviderStatus.UNAVAILABLE_MISSING_KEY
        return ProviderStatus.ENABLED

    async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]:
        start = time.monotonic()

        params = {
            "appid": self._config.api_key,
            "input": query.query,
            "maxchars": 1000,
        }

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                resp = await client.get(WOLFRAM_LLM_API, params=params)

            latency_ms = int((time.monotonic() - start) * 1000)

            # 501 = Wolfram can't compute this query. Not a provider failure.
            if resp.status_code == 501:
                return [], ProviderTrace(
                    provider=self.name,
                    status="empty",
                    latency_ms=latency_ms,
                )

            resp.raise_for_status()
            text = resp.text.strip()

            if not text:
                return [], ProviderTrace(
                    provider=self.name,
                    status="empty",
                    latency_ms=latency_ms,
                )

            query_url = WOLFRAM_QUERY_URL.format(quote_plus(query.query))
            result = SearchResult(
                url=query_url,
                title=f"Wolfram|Alpha: {query.query}",
                snippet=text[:600],
                domain="wolframalpha.com",
                provider=self.name,
                score=1.0,
                raw_rank=0,
                metadata={"computed_answer": text, "wolfram_query": query.query},
            )

            return [result], ProviderTrace(
                provider=self.name,
                status="success",
                results_count=1,
                latency_ms=latency_ms,
            )

        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("Wolfram search failed: %s", e)
            return [], ProviderTrace(
                provider=self.name,
                status="error",
                latency_ms=latency_ms,
                error=str(e),
            )

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            return urlparse(url).netloc
        except Exception:
            return ""
