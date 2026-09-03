"""
Provider adapter contract.

All provider adapters must implement this interface.
Provider-specific response shapes must never leak outside adapters.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from enum import Enum
import json
import time
from typing import Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from argus.acquisition.errors import AcquisitionFailureCode
from argus.acquisition.guarded import (
    GuardedAcquisitionError,
    guarded_http_request,
    patched_httpx_client,
)
from argus.acquisition.models import CredentialPolicy, OperationClass, OriginProfile

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


class _ProviderCompatClient:
    """Preserve legacy fake-client request shapes behind the guarded seam."""

    def __init__(
        self,
        factory: Callable[..., object],
        params: Mapping[str, object],
        *args: object,
        client_headers: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
        include_follow_redirects: bool = False,
        include_client_follow_redirects: bool = False,
        include_call_timeout: bool = False,
        strip_initial_headers: bool = False,
        **kwargs: object,
    ) -> None:
        if client_headers is not None:
            kwargs["headers"] = dict(client_headers)
        if not include_client_follow_redirects:
            kwargs.pop("follow_redirects", None)
        self._client = factory(*args, **kwargs)
        self._params = dict(params)
        self._include_follow_redirects = include_follow_redirects
        self._include_client_follow_redirects = include_client_follow_redirects
        self._include_call_timeout = include_call_timeout
        self._strip_initial_headers = strip_initial_headers

    async def __aenter__(self) -> "_ProviderCompatClient":
        enter = getattr(self._client, "__aenter__", None)
        if callable(enter):
            await enter()
        return self

    async def __aexit__(self, *args: object) -> object:
        leave = getattr(self._client, "__aexit__", None)
        if callable(leave):
            return await leave(*args)
        return None

    async def _send(
        self, method: str, url: str, *args: object, **kwargs: object
    ) -> object:
        if not self._include_follow_redirects:
            kwargs.pop("follow_redirects", None)
        if not self._include_call_timeout:
            kwargs.pop("timeout", None)
        if self._strip_initial_headers and self._params:
            kwargs["headers"] = {}
        sender = getattr(self._client, method, None)
        if not callable(sender):
            sender = getattr(self._client, "request", None)
            if not callable(sender):
                raise TypeError("compatibility client has no request method")
            try:
                result = await sender(method.upper(), url, *args, **kwargs)
            except httpx.TimeoutException as exc:
                raise TimeoutError("provider compatibility request timed out") from exc
        else:
            if self._params:
                kwargs.setdefault("params", dict(self._params))
            try:
                result = await sender(url, *args, **kwargs)
            except httpx.TimeoutException as exc:
                raise TimeoutError("provider compatibility request timed out") from exc
        return self._materialize_response(result)

    @staticmethod
    def _materialize_response(response: object) -> object:
        """Give the guarded projection bytes even to loose test doubles."""

        content = getattr(response, "content", None)
        if isinstance(content, (bytes, bytearray, str)):
            return response
        try:
            text = getattr(response, "text", None)
        except Exception:
            # Keep malformed fixture responses bounded but non-decodable so
            # adapters retain their parse-error evidence classification.
            response.content = b"<malformed-provider-response>"
            return response
        if isinstance(text, str) and text:
            response.content = text.encode("utf-8")
            return response
        json_method = getattr(response, "json", None)
        if callable(json_method):
            try:
                payload = json_method()
                if payload is None and isinstance(text, str):
                    response.content = text.encode("utf-8")
                    return response
                if (
                    isinstance(payload, (Mapping, list, tuple, str, int, float, bool))
                    or payload is None
                ):
                    response.content = json.dumps(
                        payload, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
                    return response
            except Exception:
                pass
        if isinstance(text, str):
            response.content = text.encode("utf-8")
        return response

    async def get(self, url: str, *args: object, **kwargs: object) -> object:
        return await self._send("get", url, *args, **kwargs)

    async def post(self, url: str, *args: object, **kwargs: object) -> object:
        return await self._send("post", url, *args, **kwargs)

    async def request(
        self, method: str, url: str, *args: object, **kwargs: object
    ) -> object:
        return await self._send(method.lower(), url, *args, **kwargs)

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


def _provider_compat_factory(
    factory: Callable[..., object],
    params: Mapping[str, object],
    *,
    client_headers: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    include_follow_redirects: bool = False,
    include_client_follow_redirects: bool = False,
    include_call_timeout: bool = False,
    strip_initial_headers: bool = False,
) -> Callable[..., _ProviderCompatClient]:
    def create(*args: object, **kwargs: object) -> _ProviderCompatClient:
        return _ProviderCompatClient(
            factory,
            params,
            *args,
            client_headers=client_headers,
            include_follow_redirects=include_follow_redirects,
            include_client_follow_redirects=include_client_follow_redirects,
            include_call_timeout=include_call_timeout,
            strip_initial_headers=strip_initial_headers,
            **kwargs,
        )

    return create


def _append_query_params(url: str, params: Mapping[str, object]) -> str:
    """Encode provider controls into the provider endpoint URL for the guard."""

    if not params:
        return url
    parsed = urlsplit(url)
    encoded = urlencode(tuple(params.items()), doseq=True)
    query = f"{parsed.query}&{encoded}" if parsed.query else encoded
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
    )


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

    async def _provider_request(
        self,
        query: SearchQuery,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
        params: Mapping[str, object] | None = None,
        body: object = None,
        json_body: object = None,
        timeout: float | None = None,
    ) -> object:
        """Send one provider-endpoint request through guarded acquisition.

        Provider credentials are accepted only for the provider endpoint.  A
        patched client factory is deliberately discovered only through the
        explicit compatibility seam used by tests and embedders.
        """

        effective_timeout = (
            float(timeout) if timeout is not None else self._attempt_timeout(query)
        )
        client_factory = patched_httpx_client(httpx.AsyncClient)
        dispatch_url = _append_query_params(url, params or {})
        if client_factory is not None:
            compat_factory = _provider_compat_factory(
                client_factory,
                params or {},
                client_headers=headers if self.name is ProviderName.YAHOO else None,
                include_client_follow_redirects=self.name is ProviderName.YAHOO,
                include_call_timeout=self.name is ProviderName.YAHOO,
                strip_initial_headers=self.name is ProviderName.YAHOO,
            )
            dispatch_url = url
        else:
            compat_factory = client_factory
        request_id = str(
            query.metadata.get("_provider_attempt_id") or f"{self.name.value}-attempt"
        )
        return await guarded_http_request(
            dispatch_url,
            method=method,
            headers=headers,
            body=body,
            json_body=json_body,
            profile=OriginProfile.AUTHENTICATED_CONTENT,
            credential_policy=CredentialPolicy.ORIGIN_SCOPED,
            operation_class=OperationClass.DIRECT_HTTP,
            caller_principal=f"provider:{self.name.value}",
            request_id=request_id,
            timeout=effective_timeout,
            compat_client_factory=compat_factory,
            follow_redirects=self.name is not ProviderName.YAHOO,
            trusted_service_origin=(url if self.name is ProviderName.SEARXNG else None),
        )

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
        if isinstance(error, GuardedAcquisitionError):
            code = error.failure.code
            if code is AcquisitionFailureCode.TIMEOUT:
                return self._typed_failure_batch(
                    FailureCategory.TIMEOUT,
                    "provider request timed out",
                    started_at=started_at,
                    request_evidence=request_evidence,
                    observed_status=observed_status,
                )
            if code in {
                AcquisitionFailureCode.ACQUISITION_BLOCKED,
                AcquisitionFailureCode.POLICY_REJECTED,
                AcquisitionFailureCode.INVALID_REQUEST,
                AcquisitionFailureCode.AUTHENTICATION_REJECTED,
            }:
                return self._typed_failure_batch(
                    FailureCategory.POLICY_REJECTED,
                    "provider request was blocked by acquisition policy",
                    started_at=started_at,
                    request_evidence=request_evidence,
                    observed_status=observed_status,
                )
            error = RuntimeError("guarded provider request failed")
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
        if isinstance(headers, Mapping):
            return dict(headers)
        if isinstance(headers, Sequence) and not isinstance(headers, (str, bytes)):
            try:
                return dict(headers)
            except (TypeError, ValueError):
                return {}
        return {}

    @staticmethod
    def _response_status(response: object | None) -> int | None:
        status = getattr(response, "status_code", None)
        return status if type(status) is int else None
