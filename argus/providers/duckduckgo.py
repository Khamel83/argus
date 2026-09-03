"""
DuckDuckGo search provider.

Scrapes DuckDuckGo's public website — no API key, no Docker, no cost.
Falls under Tier 0 (free/unlimited) alongside SearXNG.
Less reliable than SearXNG (depends on DDG's HTML not changing).
Uses the `ddgs` package: https://pypi.org/project/ddgs/
"""

import asyncio
import importlib.util
import json
import sys
from typing import List

from argus.config import ProviderConfig
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    SearchResult,
    SearchQuery,
)
from argus.providers.base import BaseProvider, ProbeCapability
from argus.broker.provider_evidence import (
    FailureCategory,
    ProviderFailure,
    ProviderSearchBatch,
)

logger = get_logger("providers.duckduckgo")


async def _terminate_and_reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


async def _cancellation_safe_reap(process: asyncio.subprocess.Process) -> None:
    cleanup = asyncio.create_task(_terminate_and_reap(process))
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
    await cleanup


class DuckDuckGoProvider(BaseProvider):
    probe_capability = ProbeCapability.ASYNC_NATIVE

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config or ProviderConfig(enabled=True)
        self._available = self._config.enabled and self._check_available()

    def _check_available(self) -> bool:
        return importlib.util.find_spec("ddgs") is not None

    @property
    def name(self) -> ProviderName:
        return ProviderName.DUCKDUCKGO

    def is_available(self) -> bool:
        return self._available

    def status(self) -> ProviderStatus:
        if not self._config.enabled:
            return ProviderStatus.DISABLED_BY_CONFIG
        if not self._available:
            return ProviderStatus.UNAVAILABLE_MISSING_KEY
        return ProviderStatus.ENABLED

    async def search(self, query: SearchQuery) -> ProviderSearchBatch:
        start = __import__("time").monotonic()
        if not self._available:
            return self._skipped_batch(
                (
                    "disabled by config"
                    if not self._config.enabled
                    else "ddgs package not installed (pip install ddgs)"
                )
            )
        freshness = self._freshness_params(query)
        request = {
            "query": query.query,
            "max_results": min(query.max_results, 20),
            "timelimit": freshness.get("timelimit"),
        }
        request_evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
            provider_request_material=self._canonical_request_material(request),
        )
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "argus.providers.ddg_worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=1_048_576,
            )
            encoded = json.dumps(request, separators=(",", ":")).encode("utf-8")
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(encoded),
                timeout=self._attempt_timeout(query),
            )
            if len(stdout) > 1_048_576:
                raise ValueError("worker response exceeded IPC bound")
            payload = json.loads(stdout.decode("utf-8"))
            worker_error = payload.get("error") if isinstance(payload, dict) else None
            worker_error_kind = (
                worker_error.get("kind")
                if isinstance(worker_error, dict)
                else None
            )
            if worker_error_kind == "rate_limit":
                failure = ProviderFailure(
                    FailureCategory.RATE_LIMITED,
                    self.name,
                    summary="duckduckgo library rate limit",
                )
                return self._failure_batch(
                    failure,
                    started_at=start,
                    request_evidence=request_evidence,
                )
            if worker_error_kind == "timeout":
                return self._failure_batch(
                    TimeoutError("duckduckgo library timeout"),
                    started_at=start,
                    request_evidence=request_evidence,
                )
            if worker_error_kind == "policy_rejected":
                return self._typed_failure_batch(
                    FailureCategory.POLICY_REJECTED,
                    "duckduckgo request was blocked by acquisition policy",
                    started_at=start,
                    request_evidence=request_evidence,
                )
            if worker_error_kind == "parse_error":
                return self._typed_failure_batch(
                    FailureCategory.PARSE_ERROR,
                    "duckduckgo response did not match the parser contract",
                    started_at=start,
                    request_evidence=request_evidence,
                )
            if worker_error_kind == "provider_unavailable":
                return self._failure_batch(
                    RuntimeError("duckduckgo guarded request unavailable"),
                    started_at=start,
                    request_evidence=request_evidence,
                )
            if process.returncode != 0 or not isinstance(payload, dict):
                raise RuntimeError("worker failed")
            if payload.get("error") is not None:
                raise RuntimeError("worker failed")
            return self._normalized_batch(
                payload,
                query,
                started_at=start,
                request_evidence=request_evidence,
            )
        except json.JSONDecodeError:
            return self._typed_failure_batch(
                FailureCategory.PARSE_ERROR,
                "DuckDuckGo worker returned malformed JSON",
                started_at=start,
                request_evidence=request_evidence,
            )
        except (asyncio.TimeoutError, TimeoutError):
            if process is not None:
                await _cancellation_safe_reap(process)
            return self._failure_batch(
                TimeoutError("duckduckgo worker deadline reached"),
                started_at=start,
                request_evidence=request_evidence,
            )
        except asyncio.CancelledError:
            if process is not None:
                await _cancellation_safe_reap(process)
            raise
        except Exception as error:
            if process is not None:
                await _cancellation_safe_reap(process)
            logger.warning("DuckDuckGo worker failed: %s", type(error).__name__)
            return self._failure_batch(
                error, started_at=start, request_evidence=request_evidence
            )

    def _normalize(self, raw_results: list) -> List[SearchResult]:
        results = []
        for i, item in enumerate(raw_results):
            url = item.get("href", "")
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("body", ""),
                    domain=self._extract_domain(url),
                    provider=self.name,
                    score=0.0,
                    raw_rank=i,
                )
            )
        return results

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            from urllib.parse import urlparse

            return urlparse(url).netloc
        except Exception:
            return ""
