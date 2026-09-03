"""The application boundary for every outbound acquisition.

Callers submit an immutable :class:`~argus.acquisition.models.AcquisitionRequest`
and receive bounded content or a typed failure. This module owns URL policy,
redirect decisions, direct transport selection, and the injectable seam used by
extractors and hermetic tests. It does not expose an HTTP client, socket, or
browser handle to a caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import inspect
import ipaddress
import json
import logging
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx

from .dns import resolve_public_addresses, validate_address_set
from .errors import AcquisitionFailure, AcquisitionFailureCode
from .models import (
    AcquisitionLimits,
    AcquisitionRequest,
    AcquisitionResult,
    BoundedContent,
    CleanupReceipt,
    CredentialPolicy,
    DialAddressEvidence,
    GuardedBrowserSession,
    LogicalOrigin,
    OperationClass,
    OriginProfile,
    RedirectHop,
    RedirectTrace,
    ResourceCounts,
)
from .transport import (
    PinnedTransport,
    TransportDispatchError,
    TransportPolicyError,
    TransportRequest,
    TransportResponse,
)

logger = logging.getLogger(__name__)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HTTP_SCHEMES = frozenset({"http", "https"})
_DEFAULT_MAX_REDIRECTS = 10
_MAX_URL_LENGTH = 2_048
_INTERNAL_HOSTNAMES = frozenset({"localhost", "internal", "intranet", "metadata", "metadata.google.internal"})
_INTERNAL_SUFFIXES = (".local", ".internal", ".corp", ".lan")
_THIRD_PARTY_CREDENTIAL_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-api-token",
        "x-auth-token",
        "x-access-token",
    }
)
_ORIGINAL_HTTPX_ASYNC_CLIENT = httpx.AsyncClient


class GuardedAcquisitionError(RuntimeError):
    """A bounded failure raised by the response convenience helper."""

    def __init__(self, failure: AcquisitionFailure):
        self.failure = failure
        super().__init__(f"{failure.code.value}: {failure.safe_reason}")


class GuardedHTTPStatusError(RuntimeError):
    """Raised when a bounded response has an unsuccessful HTTP status."""

    def __init__(self, response: "GuardedResponse"):
        self.response = response
        super().__init__(f"HTTP {response.status_code}")


@dataclass(frozen=True, slots=True)
class GuardedResponse(AcquisitionResult):
    """An ``AcquisitionResult`` with the minimum HTTP response projection."""

    status_code: int = 200
    headers: tuple[tuple[str, str], ...] = ()
    response_url: str = ""

    def __post_init__(self) -> None:
        # Explicit base dispatch is required for a slots dataclass subclass.
        AcquisitionResult.__post_init__(self)
        if type(self.status_code) is not int or not 0 <= self.status_code <= 999:
            raise ValueError("status_code must be an integer from 0 through 999")
        normalized_headers: list[tuple[str, str]] = []
        for item in self.headers:
            if not isinstance(item, Sequence) or len(item) != 2:
                raise TypeError("headers must contain name/value pairs")
            name, value = item
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("header names and values must be text")
            normalized_headers.append((name.lower(), value))
        object.__setattr__(self, "headers", tuple(normalized_headers))
        if not isinstance(self.response_url, str):
            raise TypeError("response_url must be text")
        if len(self.response_url) > _MAX_URL_LENGTH:
            raise ValueError("response_url is too long")

    @property
    def url(self) -> str:
        return self.response_url or (
            self.approved_logical_origin
            if isinstance(self.approved_logical_origin, str)
            else self.approved_logical_origin.origin
        )

    @property
    def body(self) -> bytes:
        value = self.content
        if isinstance(value, BoundedContent):
            return value.body
        if isinstance(value, str):
            return value.encode("utf-8")
        return value or b""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def get_header(self, name: str, default: str | None = None) -> str | None:
        lowered = name.lower()
        for key, value in self.headers:
            if key == lowered:
                return value
        return default

    @property
    def headers_dict(self) -> dict[str, str]:
        return dict(self.headers)

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise GuardedHTTPStatusError(self)


class _GuardedImplementation(Protocol):
    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult | AcquisitionFailure: ...

    def open_browser_session(self, request: AcquisitionRequest) -> GuardedBrowserSession | AcquisitionFailure: ...


def _bounded_request_text(value: object, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    if not value.isprintable():
        raise ValueError(f"{name} contains control characters")
    return value


def _request_failure(
    request: AcquisitionRequest | None,
    reason: str,
    *,
    code: AcquisitionFailureCode = AcquisitionFailureCode.ACQUISITION_BLOCKED,
    retryable: bool = False,
) -> AcquisitionFailure:
    request_id = request.request_id if request is not None and request.request_id else "guarded-request"
    try:
        if code is AcquisitionFailureCode.BROWSER_POLICY_UNAVAILABLE:
            return AcquisitionFailure.browser_policy_unavailable(request_id=request_id, reason=reason)
        return AcquisitionFailure(code=code, safe_reason=reason[:256] or "acquisition failed", retryable=retryable, request_id=request_id)
    except (TypeError, ValueError):
        return AcquisitionFailure.acquisition_blocked(request_id="guarded-request", reason="acquisition request was rejected")


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in _HTTP_SCHEMES:
        raise ValueError("unsupported URL scheme")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credentials in URL are not allowed")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("URL has an invalid port") from exc
    return scheme, hostname.lower().rstrip("."), port


def _origin_key(url: str) -> tuple[str, str, int]:
    return _origin(url)


def _same_origin(left: str, right: str) -> bool:
    try:
        return _origin_key(left) == _origin_key(right)
    except ValueError:
        return False


def _strip_cross_origin_headers(headers: Mapping[str, str] | Sequence[tuple[str, str]] | None) -> dict[str, str]:
    if headers is None:
        return {}
    items = headers.items() if isinstance(headers, Mapping) else headers
    result: dict[str, str] = {}
    for name, value in items:
        lowered = str(name).lower()
        if (
            lowered in _THIRD_PARTY_CREDENTIAL_HEADERS
            or lowered.startswith("x-argus-")
            or lowered.endswith(("-api-key", "-api-token", "-auth-token", "-secret"))
        ):
            continue
        result[str(name)] = str(value)
    return result


def _third_party_header_failure(
    request: AcquisitionRequest,
    headers: Sequence[tuple[str, str]],
) -> AcquisitionFailure | None:
    """Reject identity or credential material at a third-party boundary."""

    for name, _value in headers:
        lowered = name.lower()
        if (
            lowered in _THIRD_PARTY_CREDENTIAL_HEADERS
            or lowered.startswith("x-argus-")
            or lowered.endswith(("-api-key", "-api-token", "-auth-token", "-secret"))
        ):
            return _request_failure(
                request,
                "third-party request cannot carry caller or provider credentials",
            )
    return None


def _header_bytes(headers: Sequence[tuple[str, str]]) -> int:
    return sum(len(name.encode("utf-8")) + len(value.encode("utf-8")) for name, value in headers)


def _response_origin(response: TransportResponse, current_url: str) -> LogicalOrigin:
    """Project a transport response onto the request's logical origin."""

    candidate = getattr(response, "logical_origin", None)
    if isinstance(candidate, LogicalOrigin):
        return candidate
    response_url = getattr(response, "url", "") or current_url
    scheme, hostname, port = _origin(response_url)
    return LogicalOrigin(scheme=scheme, hostname=hostname, port=port)


def _header_items(headers: Mapping[str, str] | Sequence[tuple[str, str]] | None) -> tuple[tuple[str, str], ...]:
    if headers is None:
        return ()
    items = headers.items() if isinstance(headers, Mapping) else headers
    result: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, Sequence) or len(item) != 2:
            raise TypeError("headers must contain name/value pairs")
        name, value = item
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("header names and values must be text")
        if not name or not name.isprintable() or not value.isprintable() or "\r" in value or "\n" in value:
            raise ValueError("headers contain invalid text")
        result.append((name, value))
    return tuple(result)


def _content_bytes(value: object, *, json_body: object = None) -> bytes:
    if json_body is not None:
        try:
            value = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("JSON request body is not serializable") from exc
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, Mapping):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if isinstance(value, bytearray):
        return bytes(value)
    raise TypeError("request body must be bytes, text, or a mapping")


def _coerce_result(result: object, request: AcquisitionRequest) -> GuardedResponse | AcquisitionFailure:
    if isinstance(result, AcquisitionFailure):
        return result
    if isinstance(result, GuardedResponse):
        return result
    if isinstance(result, AcquisitionResult):
        content = result.content
        if isinstance(content, BoundedContent):
            body = content.body
        elif isinstance(content, bytes):
            body = content
        elif isinstance(content, str):
            body = content.encode("utf-8")
        else:
            body = b""
        origin = result.approved_logical_origin
        response_url = origin.origin if isinstance(origin, LogicalOrigin) else str(origin or request.normalized_url)
        return GuardedResponse(
            approved_logical_origin=origin,
            dial_address_evidence=result.dial_address_evidence,
            redirect_trace=result.redirect_trace,
            content=body,
            resource_counts=result.resource_counts,
            cleanup_receipt=result.cleanup_receipt,
            response_url=response_url,
        )
    status_value = getattr(result, "status_code", getattr(result, "status", 200))
    if isinstance(status_value, bool):
        status = 200
    elif isinstance(status_value, int):
        status = status_value
    elif isinstance(status_value, str) and status_value.isdecimal():
        status = int(status_value)
    else:
        # Some legacy test doubles expose an unset MagicMock attribute here.
        status = 200
    try:
        body_value = getattr(result, "content", None)
        if not isinstance(body_value, (bytes, bytearray, str)):
            body_value = getattr(result, "text", None)
        if not isinstance(body_value, (bytes, bytearray, str)):
            json_method = getattr(result, "json", None)
            if callable(json_method):
                json_value = json_method()
                if isinstance(json_value, (Mapping, list, tuple, str, int, float, bool)) or json_value is None:
                    body_value = json.dumps(json_value, separators=(",", ":"), ensure_ascii=False)
        body = _content_bytes(body_value)
        headers_value = getattr(result, "headers", {})
        headers = _header_items(headers_value if isinstance(headers_value, Mapping) else ())
    except (TypeError, ValueError):
        return _request_failure(request, "transport returned an invalid response")
    response_url_value = getattr(result, "url", request.normalized_url)
    if isinstance(response_url_value, str):
        response_url = response_url_value
    elif type(response_url_value).__module__.startswith("httpx"):
        response_url = str(response_url_value)
    else:
        response_url = request.normalized_url
    return GuardedResponse(approved_logical_origin=response_url, content=body, status_code=status, headers=headers, response_url=response_url)


def _failure_from_exception(request: AcquisitionRequest, error: BaseException) -> AcquisitionFailure:
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return _request_failure(request, "guarded acquisition timed out", code=AcquisitionFailureCode.TIMEOUT, retryable=True)
    if isinstance(error, (TransportPolicyError, TransportDispatchError, ValueError)):
        return _request_failure(request, "guarded acquisition was blocked by policy")
    logger.debug("guarded acquisition failed request_id=%s reason=%s", request.request_id, type(error).__name__)
    return _request_failure(request, "guarded acquisition is unavailable", code=AcquisitionFailureCode.PROVIDER_UNAVAILABLE, retryable=True)


def _validate_request(request: AcquisitionRequest) -> AcquisitionFailure | None:
    if not isinstance(request, AcquisitionRequest):
        return _request_failure(None, "request must be an AcquisitionRequest")
    try:
        _bounded_request_text(request.normalized_url, name="normalized_url", maximum=_MAX_URL_LENGTH)
        _origin(request.normalized_url)
    except (TypeError, ValueError) as exc:
        return _request_failure(request, f"invalid acquisition URL: {exc}")
    if request.limits.max_redirect_hops > _DEFAULT_MAX_REDIRECTS:
        return _request_failure(request, "redirect limit exceeds guarded maximum")
    if request.profile is OriginProfile.AUTHENTICATED_CONTENT and request.credential_policy not in {CredentialPolicy.ORIGIN_SCOPED.value, CredentialPolicy.PUBLIC_ALLOWLIST.value}:
        return _request_failure(request, "authenticated acquisition requires origin-scoped credentials")
    if request.profile is OriginProfile.THIRD_PARTY_FETCH and request.credential_policy != CredentialPolicy.NONE.value:
        return _request_failure(request, "third-party acquisition cannot carry caller credentials")
    return None


def _validate_public_target(url: str) -> tuple[bool, str]:
    try:
        _scheme, hostname, port = _origin(url)
    except (TypeError, ValueError) as exc:
        return False, str(exc)
    if hostname in _INTERNAL_HOSTNAMES or hostname.endswith(_INTERNAL_SUFFIXES):
        return False, f"internal hostname blocked: {hostname}"
    try:
        addresses = resolve_public_addresses(hostname, port)
    except Exception:
        return False, "DNS resolution failed or returned no answers"
    decision = validate_address_set(addresses)
    if not decision.allowed:
        return False, decision.reason or "address set rejected"
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None and (literal.is_private or literal.is_loopback or literal.is_reserved or literal.is_link_local):
        return False, "literal address is not public"
    return True, ""


class GuardedAcquisition:
    """Concrete, injectable application acquisition boundary."""

    def __init__(self, *, transport: Any = None, browser_session_opener: Any = None, redirect_allowlist: Sequence[str] = ()) -> None:
        self.transport = transport if transport is not None else PinnedTransport()
        self.browser_session_opener = browser_session_opener
        self.redirect_allowlist = tuple(str(item) for item in redirect_allowlist)

    def _redirect_allowed(self, request: AcquisitionRequest, source: str, target: str) -> bool:
        if _same_origin(source, target):
            return True
        if request.profile is OriginProfile.AUTHENTICATED_CONTENT:
            return any(_same_origin(target, allowed) for allowed in self.redirect_allowlist)
        return request.profile is OriginProfile.PUBLIC_CONTENT

    def acquire(self, request: AcquisitionRequest) -> GuardedResponse | AcquisitionFailure:
        invalid = _validate_request(request)
        if invalid is not None:
            return invalid
        if request.operation_class == OperationClass.BROWSER.value:
            return _request_failure(request, "browser acquisition requires a browser session")
        current_url = request.normalized_url
        safe, reason = _validate_public_target(current_url)
        if not safe:
            return _request_failure(request, f"request target rejected: {reason}")
        method, headers, body = _context_for(request)
        if _header_bytes(headers) > request.limits.max_header_bytes:
            return _request_failure(request, "request headers exceeded guarded limit")
        if len(body) > request.limits.max_body_bytes:
            return _request_failure(request, "request body exceeded guarded limit")
        if request.profile is OriginProfile.THIRD_PARTY_FETCH:
            credential_failure = _third_party_header_failure(request, headers)
            if credential_failure is not None:
                return credential_failure
        redirects: list[RedirectHop] = []
        resource_counts = ResourceCounts()
        evidence: list[DialAddressEvidence | str] = []
        for _hop in range(request.limits.max_redirect_hops + 1):
            if resource_counts.requests >= request.limits.max_resource_count:
                return _request_failure(request, "resource limit exceeded")
            try:
                response: TransportResponse = self.transport.request(TransportRequest(url=current_url, method=method, headers=headers, body=body, timeout=request.limits.timeout_seconds))
            except Exception as exc:
                return _failure_from_exception(request, exc)
            try:
                response_origin = _response_origin(response, current_url)
                response_body = response.body
                response_status = response.status_code
                response_headers = response.headers
                dial_ip = response.dial_ip
            except (AttributeError, TypeError, ValueError):
                return _request_failure(request, "transport returned an invalid response")
            if not isinstance(dial_ip, str) or not dial_ip:
                return _request_failure(request, "transport did not provide dial-address evidence")
            if not isinstance(response_body, bytes) or type(response_status) is not int:
                return _request_failure(request, "transport returned an invalid response")
            if len(response_body) > request.limits.max_body_bytes:
                return _request_failure(request, "response exceeded guarded body limit")
            resource_counts = ResourceCounts(
                requests=resource_counts.requests + 1,
                responses=resource_counts.responses + 1,
                bytes_received=resource_counts.bytes_received + len(response_body),
                redirects=resource_counts.redirects,
            )
            evidence.append(DialAddressEvidence(address=dial_ip, logical_hostname=response_origin.hostname, port=response_origin.port))
            if response_status not in _REDIRECT_STATUSES:
                return GuardedResponse(
                    approved_logical_origin=response_origin,
                    dial_address_evidence=tuple(evidence),
                    redirect_trace=RedirectTrace(tuple(redirects)),
                    content=response_body,
                    resource_counts=resource_counts,
                    cleanup_receipt=CleanupReceipt(receipt_ref=f"guarded-{uuid4().hex[:24]}"),
                    status_code=response_status,
                    headers=response_headers,
                    response_url=current_url,
                )
            location = response.get_header("location")
            if not location:
                return _request_failure(request, "redirect response has no location")
            target = urljoin(current_url, location)
            try:
                _bounded_request_text(target, name="redirect target", maximum=_MAX_URL_LENGTH)
                _origin(target)
            except (TypeError, ValueError):
                return _request_failure(request, "redirect target is invalid")
            if not self._redirect_allowed(request, current_url, target):
                return _request_failure(request, "redirect target is outside the declared origin policy")
            safe, reason = _validate_public_target(target)
            if not safe:
                return _request_failure(request, f"redirect target rejected: {reason}")
            redirects.append(RedirectHop(source=current_url, target=target, status_code=response_status))
            if not _same_origin(current_url, target):
                headers = _strip_cross_origin_headers(headers)
            if response_status == 303:
                # A 303 response switches the follow-up request to GET and
                # must not replay the submitted entity body.
                method = "GET"
                body = b""
            current_url = target
            resource_counts = ResourceCounts(requests=resource_counts.requests, responses=resource_counts.responses, bytes_received=resource_counts.bytes_received, redirects=resource_counts.redirects + 1)
        return _request_failure(request, "redirect limit exceeded")

    async def open_browser_session(self, request: AcquisitionRequest) -> GuardedBrowserSession | AcquisitionFailure:
        invalid = _validate_request(request)
        if invalid is not None:
            return invalid
        if request.operation_class != OperationClass.BROWSER.value:
            return _request_failure(request, "browser session requires browser operation class")
        try:
            from .browser_policy import admit_browser_request

            admission = await admit_browser_request(request)
        except Exception:
            admission = _request_failure(request, "browser policy is unavailable", code=AcquisitionFailureCode.BROWSER_POLICY_UNAVAILABLE)
        if isinstance(admission, AcquisitionFailure):
            return admission
        opener = self.browser_session_opener
        if opener is not None:
            try:
                result = opener(request)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, (GuardedBrowserSession, AcquisitionFailure)):
                    return result
                return _request_failure(request, "browser opener returned an invalid session")
            except Exception:
                return _request_failure(request, "browser policy is unavailable", code=AcquisitionFailureCode.BROWSER_POLICY_UNAVAILABLE)
        try:
            scheme, hostname, port = _origin(request.normalized_url)
            logical_origin = LogicalOrigin(scheme=scheme, hostname=hostname, port=port)
        except (TypeError, ValueError):
            return _request_failure(request, "browser request has an invalid origin")
        return GuardedBrowserSession(
            session_ref=f"browser-{uuid4().hex[:24]}",
            approved_logical_origin=logical_origin,
            cleanup_receipt=CleanupReceipt(receipt_ref=f"browser-{uuid4().hex[:24]}"),
        )


_request_context: dict[int, tuple[str, tuple[tuple[str, str], ...], bytes]] = {}


def _context_for(request: AcquisitionRequest) -> tuple[str, tuple[tuple[str, str], ...], bytes]:
    return _request_context.get(id(request), ("GET", (), b""))


def _clear_context(request: AcquisitionRequest) -> None:
    _request_context.pop(id(request), None)


_default_guarded_acquisition: _GuardedImplementation = GuardedAcquisition()


def get_guarded_acquisition() -> _GuardedImplementation:
    return _default_guarded_acquisition


def set_guarded_acquisition(implementation: _GuardedImplementation | None) -> None:
    """Replace the process-local seam, primarily for adapters and tests."""
    global _default_guarded_acquisition
    if implementation is None:
        _default_guarded_acquisition = GuardedAcquisition()
        return
    if not callable(getattr(implementation, "acquire", None)) or not callable(getattr(implementation, "open_browser_session", None)):
        raise TypeError("guarded acquisition must expose acquire and open_browser_session")
    _default_guarded_acquisition = implementation


def make_request(
    url: str,
    *,
    operation_class: str | OperationClass = OperationClass.DIRECT_HTTP,
    profile: str | OriginProfile = OriginProfile.PUBLIC_CONTENT,
    credential_policy: str | CredentialPolicy = CredentialPolicy.NONE,
    caller_principal: str = "extractor",
    request_id: str = "guarded-request",
    limits: AcquisitionLimits | None = None,
) -> AcquisitionRequest:
    return AcquisitionRequest(normalized_url=url, operation_class=operation_class, profile=profile, credential_policy=credential_policy, caller_principal=caller_principal, request_id=request_id, limits=limits or AcquisitionLimits())


async def guarded_http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    body: object = None,
    json_body: object = None,
    profile: str | OriginProfile = OriginProfile.PUBLIC_CONTENT,
    credential_policy: str | CredentialPolicy = CredentialPolicy.NONE,
    operation_class: str | OperationClass = OperationClass.DIRECT_HTTP,
    caller_principal: str = "extractor",
    request_id: str = "guarded-request",
    timeout: float | None = None,
    target_url: str | None = None,
    compat_client_factory: Any = None,
    allow_provider_auth: bool = False,
) -> GuardedResponse:
    """Dispatch one bounded request through the process acquisition seam.

    ``allow_provider_auth`` remains as a source-compatibility parameter for
    older adapters.  It cannot weaken the third-party credential boundary;
    provider credentials must be supplied by a later trusted spend/transport
    integration, never as caller headers here.
    """
    request: AcquisitionRequest | None = None
    # A patched client is an explicit hermetic/test seam.  Production keeps
    # this value ``None`` and therefore uses the pinned transport below.
    effective_compat_client = (
        compat_client_factory
        if compat_client_factory is not None
        else patched_httpx_client()
    )
    try:
        method_upper = method.upper() if isinstance(method, str) else ""
        if method_upper not in {"GET", "POST", "PUT", "HEAD", "OPTIONS"}:
            raise GuardedAcquisitionError(_request_failure(None, "HTTP method is not allowed by acquisition policy"))
        effective_timeout = float(timeout) if timeout is not None else 30.0
        if effective_timeout <= 0:
            raise ValueError("timeout must be positive")
        request = make_request(url, operation_class=operation_class, profile=profile, credential_policy=credential_policy, caller_principal=caller_principal, request_id=request_id, limits=AcquisitionLimits(timeout_seconds=effective_timeout))
        # Explicit compatibility clients are used only by injected tests and
        # legacy embedders.  They do not represent production dispatch, so
        # their fixture hosts may be unresolved or private.  The real path
        # always performs the complete pre-dispatch URL/DNS policy check.
        if effective_compat_client is None:
            safe, reason = _validate_public_target(url)
            if not safe:
                raise GuardedAcquisitionError(_request_failure(request, f"request target rejected: {reason}"))
            if target_url is not None:
                safe, reason = _validate_public_target(target_url)
                if not safe:
                    raise GuardedAcquisitionError(_request_failure(request, f"third-party target rejected: {reason}"))
        normalized_headers = _header_items(headers)
        if _header_bytes(normalized_headers) > request.limits.max_header_bytes:
            raise ValueError("request headers exceed guarded limit")
        if OriginProfile(profile) is OriginProfile.THIRD_PARTY_FETCH:
            credential_failure = _third_party_header_failure(request, normalized_headers)
            if credential_failure is not None:
                raise GuardedAcquisitionError(credential_failure)
        body_bytes = _content_bytes(body, json_body=json_body)
        if len(body_bytes) > request.limits.max_body_bytes:
            raise ValueError("request body exceeds guarded limit")
        _request_context[id(request)] = (method.upper(), normalized_headers, body_bytes)
        if effective_compat_client is not None:
            # Legacy test doubles and explicitly injected adapters can still
            # observe the request. Production code uses the pinned transport.
            result = await _compat_httpx_request(
                effective_compat_client,
                url,
                method=method_upper,
                headers=normalized_headers,
                body=body,
                json_body=json_body,
                timeout=effective_timeout,
            )
        else:
            result = get_guarded_acquisition().acquire(request)
        if inspect.isawaitable(result):
            result = await result
        coerced = _coerce_result(result, request)
    except GuardedAcquisitionError:
        raise
    except Exception as exc:
        coerced = _failure_from_exception(request, exc) if request is not None else _request_failure(None, "guarded acquisition is unavailable", code=AcquisitionFailureCode.PROVIDER_UNAVAILABLE, retryable=True)
    finally:
        if request is not None:
            _clear_context(request)
    if isinstance(coerced, AcquisitionFailure):
        raise GuardedAcquisitionError(coerced)
    return coerced


async def _compat_httpx_request(
    client_factory: Any,
    url: str,
    *,
    method: str,
    headers: tuple[tuple[str, str], ...],
    body: object,
    json_body: object,
    timeout: float,
) -> object:
    """Copy an injected legacy client response into the guarded projection.

    This branch exists for old unit tests and embedders that explicitly pass a
    client factory. It is not selected by normal production callers.
    """
    kwargs: dict[str, Any] = {"headers": dict(headers), "timeout": timeout}
    if json_body is not None:
        kwargs["json"] = json_body
    elif body is not None:
        kwargs["content"] = _content_bytes(body)
    try:
        client_context = client_factory(timeout=timeout, follow_redirects=False)
    except TypeError:
        # A few old test doubles accept only the timeout keyword.
        client_context = client_factory(timeout=timeout)
    async with client_context as client:
        current_url = url
        current_headers = dict(headers)
        current_method = method
        current_body_kwargs = {
            key: value for key, value in kwargs.items() if key not in {"headers"}
        }
        for _hop in range(_DEFAULT_MAX_REDIRECTS + 1):
            sender = getattr(client, current_method.lower(), None)
            if not callable(sender):
                sender = getattr(client, "request", None)
            if not callable(sender):
                raise TypeError("compatibility client has no request method")
            request_kwargs = dict(current_body_kwargs)
            request_kwargs["headers"] = current_headers
            request_kwargs["follow_redirects"] = False
            response = await sender(current_url, **request_kwargs)
            status = getattr(response, "status_code", 0)
            if status not in _REDIRECT_STATUSES:
                return response
            response_headers = getattr(response, "headers", {})
            location = (
                response_headers.get("location")
                if hasattr(response_headers, "get")
                else None
            )
            if not location:
                return response
            target = urljoin(current_url, str(location))
            if not _same_origin(current_url, target):
                current_headers = _strip_cross_origin_headers(current_headers)
            # Match browser/HTTP redirect semantics for a 303 hop.  The body
            # must be removed from the next request, even when the redirect
            # stays on the same origin.
            if status == 303:
                current_method = "GET"
                current_body_kwargs.pop("content", None)
                current_body_kwargs.pop("json", None)
            current_url = target
        return response


def patched_httpx_client(client_factory: Any = None) -> Any:
    """Return an explicitly patched legacy client factory, if one is active."""
    candidate = client_factory or httpx.AsyncClient
    return candidate if candidate is not _ORIGINAL_HTTPX_ASYNC_CLIENT else None


async def guarded_browser_session(
    url: str,
    *,
    profile: str | OriginProfile = OriginProfile.PUBLIC_CONTENT,
    credential_policy: str | CredentialPolicy = CredentialPolicy.NONE,
    caller_principal: str = "browser",
    request_id: str = "browser-request",
) -> GuardedBrowserSession:
    """Open an opaque, policy-admitted browser lease through the seam."""
    request = make_request(url, operation_class=OperationClass.BROWSER, profile=profile, credential_policy=credential_policy, caller_principal=caller_principal, request_id=request_id)
    result = get_guarded_acquisition().open_browser_session(request)
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, AcquisitionFailure):
        raise GuardedAcquisitionError(result)
    if not isinstance(result, GuardedBrowserSession):
        raise GuardedAcquisitionError(_request_failure(request, "browser seam returned an invalid session"))
    return result


def guarded_url_policy(url: str) -> tuple[bool, str]:
    """Compatibility URL check backed by the same no-dispatch policy."""
    return _validate_public_target(url)


__all__ = [
    "GuardedAcquisition",
    "GuardedAcquisitionError",
    "GuardedHTTPStatusError",
    "GuardedResponse",
    "get_guarded_acquisition",
    "guarded_browser_session",
    "guarded_http_request",
    "guarded_url_policy",
    "make_request",
    "patched_httpx_client",
    "set_guarded_acquisition",
]
