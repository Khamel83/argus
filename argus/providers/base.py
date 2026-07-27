"""
Provider adapter contract.

All provider adapters must implement this interface.
Provider-specific response shapes must never leak outside adapters.
"""

from abc import ABC, abstractmethod
from enum import Enum
import time

from argus.broker.planning import RetrievalPlan
from argus.broker.provider_evidence import ProviderSearchBatch
from argus.broker.provider_evidence import (
    ProviderRequestEvidence,
    ProviderResponseEvidence,
    ProviderFailure,
    QueryRelation,
    FailureCategory,
    failure_batch,
    normalize_provider_response,
    query_hash,
    skipped_batch,
    with_response_timing,
    attempt_timeout_seconds,
)
from argus.broker.planning import FreshnessWindow
from argus.models import (
    ProviderName,
    ProviderStatus,
    SearchQuery,
)


class ProbeCapability(str, Enum):
    ASYNC_NATIVE = "async_native"
    BLOCKING_UNSUPPORTED = "blocking_unsupported"


class BaseProvider(ABC):
    """Abstract base for all search provider adapters."""

    probe_capability = ProbeCapability.ASYNC_NATIVE

    @property
    @abstractmethod
    def name(self) -> ProviderName:
        """Unique provider identifier."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and ready to use."""
        ...

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Return current operational status."""
        ...

    @abstractmethod
    async def search(
        self, query: SearchQuery
    ) -> ProviderSearchBatch:
        """Execute a search and return one normalized bounded evidence batch."""
        ...

    def _request_evidence(
        self,
        query: SearchQuery,
        *,
        timeout_seconds: float | None = None,
    ) -> ProviderRequestEvidence:
        translation = None
        plan = query.metadata.get("_retrieval_plan")
        freshness = query.metadata.get("_freshness_window")
        if isinstance(plan, RetrievalPlan):
            freshness = plan.freshness
        if isinstance(freshness, FreshnessWindow):
            from argus.provider_controls import translate_freshness

            translation = translate_freshness(
                self.name,
                freshness,
                required=bool(
                    freshness.requested_relative
                    or freshness.start_date
                    or freshness.end_date
                ),
            )
        return ProviderRequestEvidence(
            effective_query_hash=query_hash(query.query),
            provider_query_hash=query_hash(query.query),
            query_relation=QueryRelation.EXACT,
            freshness_translation=translation,
            timeout_seconds=timeout_seconds,
        )

    def _attempt_timeout(self, query: SearchQuery) -> float:
        deadline = query.metadata.get("_provider_phase_deadline")
        monotonic = query.metadata.get("_monotonic", time.monotonic)
        if isinstance(deadline, (int, float)) and callable(monotonic):
            return attempt_timeout_seconds(
                configured_timeout=float(self._config.timeout_seconds),
                provider_phase_deadline=float(deadline),
                monotonic=monotonic,
            )
        return float(self._config.timeout_seconds)

    def _freshness_params(self, query: SearchQuery) -> dict[str, object]:
        evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
        )
        translation = evidence.freshness_translation
        if translation is None or translation.provider_control is None:
            return {}
        value = translation.provider_value or ""
        if "/" not in translation.provider_control:
            return {translation.provider_control: value}
        start, _, end = value.partition("..")
        left, right = translation.provider_control.split("/", 1)
        output: dict[str, object] = {}
        if start:
            output[left] = start
        if end:
            output[right] = end
        return output

    def _normalized_batch(
        self,
        payload: object,
        query: SearchQuery,
        *,
        started_at: float,
    ) -> ProviderSearchBatch:
        batch = normalize_provider_response(
            self.name,
            payload,
            max_results=query.max_results,
            request_evidence=self._request_evidence(
                query,
                timeout_seconds=self._attempt_timeout(query),
            ),
        )
        return with_response_timing(
            batch,
            latency_ms=int((time.monotonic() - started_at) * 1000),
        )

    def _failure_batch(
        self, error: BaseException, *, started_at: float
    ) -> ProviderSearchBatch:
        return failure_batch(
            self.name,
            error,
            latency_ms=int((time.monotonic() - started_at) * 1000),
        )

    def _typed_failure_batch(
        self,
        category: FailureCategory,
        summary: str,
        *,
        started_at: float,
    ) -> ProviderSearchBatch:
        return ProviderSearchBatch(
            provider=self.name,
            provider_contract_version="2026-07-27-v1",
            response_evidence=ProviderResponseEvidence(
                latency_ms=int((time.monotonic() - started_at) * 1000)
            ),
            failure=ProviderFailure(
                category,
                self.name,
                summary=summary,
            ),
        )

    def _skipped_batch(self, summary: str) -> ProviderSearchBatch:
        return skipped_batch(self.name, summary)
