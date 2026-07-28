"""
WolframAlpha LLM API provider.

Returns computed answers, not a list of URLs. Best for factual queries,
calculations, unit conversions, and definitions — things web search is bad at.

Free tier: 2,000 calls/month. Requires ARGUS_WOLFRAM_API_KEY (or WOLFRAM_APP_ID).
Get a key at https://developer.wolframalpha.com/
"""

import time
from urllib.parse import quote_plus, urlparse

import httpx

from argus.config import ProviderConfig
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    SearchQuery,
)
from argus.providers.base import BaseProvider
from argus.broker.provider_evidence import ProviderSearchBatch

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

    async def search(self, query: SearchQuery) -> ProviderSearchBatch:
        start = time.monotonic()

        params = {
            "appid": self._config.api_key,
            "input": query.query,
            "maxchars": 1000,
        }
        self._freshness_params(query)
        request_evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
            provider_request_material=self._canonical_request_material(
                {key: value for key, value in params.items() if key != "appid"}
            ),
        )

        resp = None
        try:
            async with httpx.AsyncClient(
                timeout=self._attempt_timeout(query)
            ) as client:
                resp = await client.get(WOLFRAM_LLM_API, params=params)

            # 501 = Wolfram can't compute this query. Not a provider failure.
            if resp.status_code == 501:
                return self._normalized_batch(
                    {"empty": True},
                    query,
                    started_at=start,
                    request_evidence=request_evidence,
                    http_status=resp.status_code,
                    response_headers=self._response_headers(resp),
                )

            native_failure = self._response_failure_batch(
                resp, started_at=start, request_evidence=request_evidence
            )

            if native_failure is not None:
                return native_failure
            text = resp.text.strip()

            if not text:
                return self._normalized_batch(
                    {"empty": True},
                    query,
                    started_at=start,
                    request_evidence=request_evidence,
                    http_status=resp.status_code,
                    response_headers=self._response_headers(resp),
                )

            return self._normalized_batch(
                {
                    "answer": text,
                    "query_url": WOLFRAM_QUERY_URL.format(quote_plus(query.query)),
                    "title": f"Wolfram|Alpha: {query.query}",
                },
                query,
                started_at=start,
                request_evidence=request_evidence,
                http_status=resp.status_code,
                response_headers=self._response_headers(resp),
            )

        except Exception as e:
            logger.warning("Wolfram search failed: %s", type(e).__name__)
            return self._failure_batch(
                e,
                started_at=start,
                request_evidence=request_evidence,
                observed_status=self._response_status(resp),
            )

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            return urlparse(url).netloc
        except Exception:
            return ""
