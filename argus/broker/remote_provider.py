"""Remote provider client — delegates search to an egress worker node."""

import re
import time
from typing import List

from argus.acquisition.guarded import (
    GuardedAcquisitionError,
    GuardedHTTPStatusError,
    guarded_http_request,
    patched_httpx_client,
)
from argus.acquisition.models import CredentialPolicy, OperationClass, OriginProfile
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

_SAFE_TRACE_ERROR = re.compile(
    r"(?:authorization\s*:|bearer\s+|cookie\s*:|set-cookie\s*:|"
    r"(?:api[_-]?key|credential|password|secret|signature|token)\s*[=:])",
    re.IGNORECASE,
)


def _safe_worker_error(value: object) -> str | None:
    """Return bounded worker error text without credential material."""

    if not isinstance(value, str) or not value.isprintable():
        return None
    if _SAFE_TRACE_ERROR.search(value):
        return None
    return value[:256] or None


def _error_trace(
    provider: ProviderName,
    node: EgressNode,
    start: float,
    error: str,
    *,
    http_status: int | None = None,
) -> ProviderTrace:
    """Build a bounded trace for a failed worker request."""

    return ProviderTrace(
        provider=provider,
        status="error",
        latency_ms=int((time.monotonic() - start) * 1000),
        error=error[:256],
        egress=node.name,
        http_status=http_status,
    )


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

    async def search(
        self, query: SearchQuery
    ) -> tuple[List[SearchResult], ProviderTrace]:
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
            worker_url = f"{self._node.url.rstrip('/')}/exec"
            response = await guarded_http_request(
                worker_url,
                method="POST",
                headers=headers,
                json_body=payload,
                profile=OriginProfile.AUTHENTICATED_CONTENT,
                credential_policy=CredentialPolicy.ORIGIN_SCOPED,
                operation_class=OperationClass.DIRECT_HTTP,
                caller_principal="remote-provider",
                request_id=(
                    str(query.metadata.get("_provider_attempt_id"))
                    if query.metadata.get("_provider_attempt_id")
                    else f"remote-{self._provider.value}-attempt"
                ),
                timeout=30.0,
                trusted_service_origin=self._node.url,
                compat_client_factory=patched_httpx_client(),
                # The worker is a configured service boundary.  A redirect
                # must never turn a bearer token into a request to an
                # unconfigured origin.
                follow_redirects=False,
            )
            response.raise_for_status()
            data = response.json()
        except GuardedHTTPStatusError as exc:
            status = exc.response.status_code
            error = f"remote egress returned HTTP {status}"
            logger.warning(
                "Remote provider %s via %s failed with HTTP %s",
                self._provider.value,
                self._node.name,
                status,
            )
            return [], _error_trace(
                self._provider,
                self._node,
                start,
                error,
                http_status=status,
            )
        except GuardedAcquisitionError as exc:
            failure = exc.failure
            error = f"{failure.code.value}: {failure.safe_reason}"
            logger.warning(
                "Remote provider %s via %s failed code=%s",
                self._provider.value,
                self._node.name,
                failure.code.value,
            )
            return [], _error_trace(self._provider, self._node, start, error)
        except Exception as exc:
            # Do not copy exception text into the provider trace.  Transport
            # errors may contain a URL or an implementation-specific secret.
            logger.warning(
                "Remote provider %s via %s failed type=%s",
                self._provider.value,
                self._node.name,
                type(exc).__name__,
            )
            return [], _error_trace(
                self._provider,
                self._node,
                start,
                "remote egress response was invalid",
            )

        if not isinstance(data, dict):
            return [], _error_trace(
                self._provider,
                self._node,
                start,
                "remote egress response was invalid",
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        raw_trace = data.get("trace", {})
        if not isinstance(raw_trace, dict):
            return [], _error_trace(
                self._provider,
                self._node,
                start,
                "remote egress trace was invalid",
            )
        raw_status = raw_trace.get("status")
        status = (
            raw_status
            if isinstance(raw_status, str)
            and raw_status in {"success", "error", "empty", "skipped"}
            else "error"
        )
        reported_count = raw_trace.get("results_count")
        trace = ProviderTrace(
            provider=self._provider,
            status=status,
            results_count=(
                reported_count
                if type(reported_count) is int and 0 <= reported_count <= 1_000_000
                else 0
            ),
            latency_ms=latency_ms,
            error=_safe_worker_error(raw_trace.get("error")),
            egress=self._node.name,
        )

        results = []
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            return [], _error_trace(
                self._provider,
                self._node,
                start,
                "remote egress results were invalid",
            )
        for r in raw_results:
            if not isinstance(r, dict):
                continue
            try:
                results.append(
                    SearchResult(
                        url=r["url"],
                        title=r.get("title", ""),
                        snippet=r.get("snippet", ""),
                        domain=r.get("domain", ""),
                        provider=self._provider,
                        score=r.get("score", 0.0),
                        raw_rank=r.get("raw_rank", 0),
                        metadata=r.get("metadata", {}),
                    )
                )
            except Exception:
                continue

        return results, trace
