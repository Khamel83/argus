"""
Provider adapter contract.

All provider adapters must implement this interface.
Provider-specific response shapes must never leak outside adapters.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
import json
import time
from typing import Mapping

from argus.broker.planning import RetrievalPlan
from argus.broker.provider_evidence import ProviderSearchBatch
from argus.broker.provider_evidence import (
    ProviderRequestEvidence,
    ProviderResponseEvidence,
    ProviderFailure,
    RedirectChildEvidence,
    QueryRelation,
    FailureCategory,
    EgressType,
    failure_batch,
    query_hash,
    skipped_batch,
    with_response_timing,
    attempt_timeout_seconds,
)
from argus.providers.normalization import normalize_provider_response
from argus.providers.normalization import classify_provider_failure_response
from argus.providers.normalization import provider_response_indicates_failure
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
    async def search(self, query: SearchQuery) -> ProviderSearchBatch:
        """Execute a search and return one normalized bounded evidence batch."""
        ...

    def _request_evidence(
        self,
        query: SearchQuery,
        *,
        timeout_seconds: float | None = None,
        provider_request_material: str | None = None,
        query_relation: QueryRelation = QueryRelation.EXACT,
        redirect_children: tuple[RedirectChildEvidence, ...] = (),
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
            provider_query_hash=query_hash(
                provider_request_material
                if provider_request_material is not None
                else query.query
            ),
            query_relation=query_relation,
            freshness_translation=translation,
            timeout_seconds=timeout_seconds,
            attempt_id=str(
                query.metadata.get("_provider_attempt_id")
                or f"{self.name.value}-attempt"
            ),
            redirect_children=redirect_children,
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
        request_evidence: ProviderRequestEvidence | None = None,
        http_status: int | None = None,
        response_headers: dict[str, object] | None = None,
        egress: EgressType | None = None,
        machine: str | None = None,
    ) -> ProviderSearchBatch:
        observed_at = datetime.now(timezone.utc)
        configured_egress, configured_machine = self._provenance()
        batch = normalize_provider_response(
            self.name,
            payload,
            max_results=query.max_results,
            request_evidence=request_evidence
            or self._request_evidence(
                query, timeout_seconds=self._attempt_timeout(query)
            ),
            http_status=http_status,
            response_headers=response_headers,
            observed_at=observed_at,
            egress=egress or configured_egress,
            machine=machine if machine is not None else configured_machine,
        )
        return with_response_timing(
            batch,
            latency_ms=int((time.monotonic() - started_at) * 1000),
        )

    def _failure_batch(
        self,
        error: BaseException,
        *,
        started_at: float,
        request_evidence: ProviderRequestEvidence | None = None,
        observed_status: int | None = None,
    ) -> ProviderSearchBatch:
        return failure_batch(
            self.name,
            error,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            request_evidence=request_evidence,
            observed_status=observed_status,
        )

    def _response_failure_batch(
        self,
        response: object,
        *,
        started_at: float,
        request_evidence: ProviderRequestEvidence | None = None,
    ) -> ProviderSearchBatch | None:
        """Classify one native HTTP response inside the provider boundary."""
        status = getattr(response, "status_code", None)
        body: object = {}
        try:
            candidate = response.json()
            if isinstance(candidate, dict):
                body = candidate
        except Exception:
            pass
        if not isinstance(status, int):
            return None
        if not provider_response_indicates_failure(self.name, status, body):
            return None
        failure = classify_provider_failure_response(
            self.name,
            {
                "transport": {
                    "status_code": status,
                    "headers": self._response_headers(response),
                },
                "body": body,
            },
        )
        return failure_batch(
            self.name,
            failure,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            request_evidence=request_evidence,
        )

    def _typed_failure_batch(
        self,
        category: FailureCategory,
        summary: str,
        *,
        started_at: float,
        request_evidence: ProviderRequestEvidence | None = None,
        observed_status: int | None = None,
    ) -> ProviderSearchBatch:
        observed_at = datetime.now(timezone.utc)
        return ProviderSearchBatch(
            provider=self.name,
            provider_contract_version="2026-07-27-v1",
            request_evidence=request_evidence or ProviderRequestEvidence(),
            response_evidence=ProviderResponseEvidence(
                latency_ms=int((time.monotonic() - started_at) * 1000),
                http_status=observed_status,
                observed_at=observed_at,
            ),
            failure=ProviderFailure(
                category,
                self.name,
                http_status=observed_status,
                summary=summary,
                observed_at=observed_at,
            ),
        )

    def _skipped_batch(self, summary: str) -> ProviderSearchBatch:
        return skipped_batch(self.name, summary)

    def _provenance(self) -> tuple[EgressType, str | None]:
        raw_egress = getattr(self._config, "egress_type", "unknown")
        try:
            egress = (
                raw_egress
                if isinstance(raw_egress, EgressType)
                else EgressType(str(raw_egress))
            )
        except ValueError:
            egress = EgressType.UNKNOWN
        machine = getattr(self._config, "machine", None)
        return egress, machine if isinstance(machine, str) else None

    @staticmethod
    def _canonical_request_material(value: object) -> str:
        """Stable secret-free representation of the actual provider request."""
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _response_headers(response: object) -> dict[str, object]:
        headers = getattr(response, "headers", None)
        return dict(headers) if isinstance(headers, Mapping) else {}

    @staticmethod
    def _response_status(response: object | None) -> int | None:
        status = getattr(response, "status_code", None)
        return status if type(status) is int else None
