"""Admission and resource guards for browser acquisition.

Argus cannot prove browser socket policy from Playwright callbacks.  A browser
is therefore admitted only after an external policy authority supplies a
short-lived attestation.  This module keeps that boundary small and explicit:
the provider is refreshed for every admission, the attestation is checked
against the running release, and browser resources are denied when the normal
URL policy rejects them.

The attestation carries identity only.  It must not contain cookies,
credentials, headers, or network payloads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import os
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from .errors import AcquisitionFailure
from .models import (
    AcquisitionLimits,
    AcquisitionRequest,
    CredentialPolicy,
    OperationClass,
    OriginProfile,
)
from .. import __version__
from ..extraction.ssrf import is_safe_url as _default_url_checker


MAX_ATTESTATION_ID_LENGTH = 256
MAX_FAILURE_REQUEST_ID_LENGTH = 64
_BROWSER_POLICY_UNAVAILABLE = "browser connection policy is unavailable"
_NON_NETWORK_SCHEMES = frozenset({"about", "blob", "data"})
_BROWSER_RESOURCE_TYPES = frozenset(
    {
        "document",
        "xhr",
        "fetch",
        "image",
        "script",
        "font",
        "stylesheet",
        "media",
        "websocket",
        "worker",
        "sharedworker",
        "serviceworker",
        "manifest",
        "other",
    }
)


def _safe_identity(name: str, value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")
    if len(value) > MAX_ATTESTATION_ID_LENGTH:
        raise ValueError(f"{name} exceeds {MAX_ATTESTATION_ID_LENGTH} characters")
    if not value.isprintable():
        raise ValueError(f"{name} contains control characters")
    return value


def _coerce_datetime(name: str, value: object) -> datetime:
    """Convert an external expiry value to an aware UTC datetime."""

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone aware")
        return value.astimezone(timezone.utc)
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a datetime, timestamp, or ISO text")
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError(f"{name} is not a valid timestamp") from exc
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} is not valid ISO datetime text") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{name} must be timezone aware")
        return parsed.astimezone(timezone.utc)
    raise TypeError(f"{name} must be a datetime, timestamp, or ISO text")


def _now(value: object | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return _coerce_datetime("now", value)


def current_release_identity() -> str:
    """Return the release identity bound to this process.

    Deployments may bind an immutable release string through ``ARGUS_RELEASE``.
    The package version is the safe local fallback for development and tests.
    """

    value = os.environ.get("ARGUS_RELEASE", "").strip()
    return value or __version__


@dataclass(frozen=True, slots=True, init=False)
class BrowserNetworkAttestation:
    """Identity-only proof from the external browser network authority.

    ``verified`` is an assertion made by the injected authority adapter.  The
    application does not treat process health or page interception as proof.
    The aliases in ``__init__`` keep the boundary compatible with authorities
    that call the resolver field ``resolver_identity`` or expiry ``expiry``.
    """

    policy_identity: str
    resolver_address_control_identity: str
    connection_binding_identity: str
    expires_at: datetime
    release_identity: str
    verification_ref: str
    verified: bool

    def __init__(
        self,
        policy_identity: str | None = None,
        resolver_address_control_identity: str | None = None,
        connection_binding_identity: str | None = None,
        expires_at: datetime | float | int | str | None = None,
        release_identity: str | None = None,
        *,
        resolver_identity: str | None = None,
        address_control_identity: str | None = None,
        policy_id: str | None = None,
        connection_binding: str | None = None,
        connection_binding_id: str | None = None,
        expiry: datetime | float | int | str | None = None,
        release: str | None = None,
        candidate_release: str | None = None,
        candidate_release_identity: str | None = None,
        verification_ref: str = "",
        proof: str | None = None,
        proof_valid: bool | None = None,
        verified: bool = True,
    ) -> None:
        if policy_id is not None:
            if policy_identity and policy_identity != policy_id:
                raise ValueError("policy identity aliases must match")
            policy_identity = policy_id
        resolver_values = [
            value
            for value in (
                resolver_address_control_identity,
                resolver_identity,
                address_control_identity,
            )
            if value is not None
        ]
        if not resolver_values:
            raise ValueError("resolver/address-control identity is required")
        if any(value != resolver_values[0] for value in resolver_values[1:]):
            raise ValueError("resolver identity aliases must match")

        binding_values = [
            value
            for value in (
                connection_binding_identity,
                connection_binding,
                connection_binding_id,
            )
            if value is not None
        ]
        if not binding_values:
            raise ValueError("connection-binding identity is required")
        if any(value != binding_values[0] for value in binding_values[1:]):
            raise ValueError("connection-binding identity aliases must match")

        expiry_values = [value for value in (expires_at, expiry) if value is not None]
        if not expiry_values:
            raise ValueError("expiry is required")
        if len(expiry_values) == 2 and expiry_values[0] != expiry_values[1]:
            raise ValueError("expiry aliases must match")

        release_values = [
            value
            for value in (
                release_identity,
                release,
                candidate_release,
                candidate_release_identity,
            )
            if value is not None
        ]
        if not release_values:
            raise ValueError("release identity is required")
        if any(value != release_values[0] for value in release_values[1:]):
            raise ValueError("release identity aliases must match")

        if proof is not None:
            if verification_ref and verification_ref != proof:
                raise ValueError("verification reference aliases must match")
            verification_ref = proof

        if proof_valid is not None:
            if type(proof_valid) is not bool:
                raise TypeError("proof_valid must be a boolean")
            if verified is not True and verified != proof_valid:
                raise ValueError("verified aliases must match")
            verified = proof_valid
        if type(verified) is not bool:
            raise TypeError("verified must be a boolean")
        object.__setattr__(
            self,
            "policy_identity",
            _safe_identity("policy_identity", policy_identity or ""),
        )
        object.__setattr__(
            self,
            "resolver_address_control_identity",
            _safe_identity("resolver_address_control_identity", resolver_values[0]),
        )
        object.__setattr__(
            self,
            "connection_binding_identity",
            _safe_identity("connection_binding_identity", binding_values[0]),
        )
        object.__setattr__(
            self, "expires_at", _coerce_datetime("expires_at", expiry_values[0])
        )
        object.__setattr__(
            self,
            "release_identity",
            _safe_identity("release_identity", release_values[0]),
        )
        object.__setattr__(
            self,
            "verification_ref",
            _safe_identity("verification_ref", verification_ref, allow_empty=True),
        )
        object.__setattr__(self, "verified", verified)

    @property
    def resolver_identity(self) -> str:
        """Compatibility alias for the resolver/address-control identity."""

        return self.resolver_address_control_identity

    @property
    def address_control_identity(self) -> str:
        return self.resolver_address_control_identity

    @property
    def connection_binding(self) -> str:
        return self.connection_binding_identity

    @property
    def expiry(self) -> datetime:
        return self.expires_at

    @property
    def release(self) -> str:
        return self.release_identity

    def as_dict(self) -> dict[str, object]:
        """Return the identity-only, redaction-safe authority projection."""

        return {
            "policy_identity": self.policy_identity,
            "resolver_address_control_identity": self.resolver_address_control_identity,
            "connection_binding_identity": self.connection_binding_identity,
            "expires_at": self.expires_at.isoformat(),
            "release_identity": self.release_identity,
            "verification_ref": self.verification_ref,
            "verified": self.verified,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "BrowserNetworkAttestation":
        """Build an attestation from a bounded authority response mapping."""

        if not isinstance(value, Mapping):
            raise TypeError("attestation must be a mapping")
        return cls(
            policy_identity=value.get("policy_identity", ""),
            resolver_address_control_identity=value.get(
                "resolver_address_control_identity",
                value.get("resolver_identity", value.get("address_control_identity")),
            ),
            connection_binding_identity=value.get(
                "connection_binding_identity", value.get("connection_binding")
            ),
            expires_at=value.get("expires_at", value.get("expiry")),
            release_identity=value.get("release_identity", value.get("release")),
            verification_ref=value.get("verification_ref", value.get("proof", "")),
            verified=value.get("verified", True),
        )


@runtime_checkable
class BrowserAttestationProvider(Protocol):
    """External source of a fresh browser-network attestation."""

    def current(
        self,
    ) -> (
        BrowserNetworkAttestation
        | Mapping[str, object]
        | None
        | Awaitable[BrowserNetworkAttestation | Mapping[str, object] | None]
    ):
        """Return a fresh attestation, or ``None`` when unavailable."""


class UnavailableBrowserAttestationProvider:
    """Default provider.  Browser capability stays disabled by default."""

    def current(self) -> None:
        return None


_attestation_provider: BrowserAttestationProvider = (
    UnavailableBrowserAttestationProvider()
)


def set_browser_attestation_provider(
    provider: BrowserAttestationProvider | None,
) -> None:
    """Set the injected authority adapter.

    This is an explicit process-level dependency injection seam.  Passing
    ``None`` restores the fail-closed provider.
    """

    global _attestation_provider
    _attestation_provider = provider or UnavailableBrowserAttestationProvider()


def get_browser_attestation_provider() -> BrowserAttestationProvider:
    return _attestation_provider


def _request_id(request: object | None) -> str:
    value = getattr(request, "request_id", "") if request is not None else ""
    if isinstance(value, str) and value:
        return value[:MAX_FAILURE_REQUEST_ID_LENGTH]
    return "browser-policy"


def _failure(
    request: object | None,
    reason: str = _BROWSER_POLICY_UNAVAILABLE,
) -> AcquisitionFailure:
    return AcquisitionFailure.browser_policy_unavailable(
        request_id=_request_id(request),
        reason=reason,
    )


def _normalise_attestation(
    attestation: BrowserNetworkAttestation | Mapping[str, object] | object | None,
) -> BrowserNetworkAttestation | None:
    if attestation is None:
        return None
    if isinstance(attestation, BrowserNetworkAttestation):
        return attestation
    if isinstance(attestation, Mapping):
        try:
            return BrowserNetworkAttestation.from_mapping(attestation)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def validate_browser_attestation(
    attestation: BrowserNetworkAttestation | Mapping[str, object] | None,
    now: datetime | float | int | str | None = None,
    release: str | None = None,
    *,
    request_id: str = "browser-policy",
) -> None | AcquisitionFailure:
    """Validate an attestation without contacting the authority.

    Expiry is strict: an attestation at its exact expiry instant is rejected.
    The check is deliberately repeated for each browser admission.
    """

    checked = _normalise_attestation(attestation)
    if checked is None:
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=request_id,
            reason=_BROWSER_POLICY_UNAVAILABLE,
        )
    if not checked.verified:
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=request_id,
            reason="browser connection policy proof is not verifiable",
        )
    if not (
        checked.policy_identity
        and checked.resolver_address_control_identity
        and checked.connection_binding_identity
    ):
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=request_id,
            reason="browser connection policy binding is incomplete",
        )
    expected_release = release if release is not None else current_release_identity()
    if not isinstance(expected_release, str) or not expected_release:
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=request_id,
            reason="browser release identity is unavailable",
        )
    if checked.release_identity != expected_release:
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=request_id,
            reason="browser connection policy release does not match",
        )
    try:
        current = _now(now)
    except (TypeError, ValueError, OverflowError):
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=request_id,
            reason="browser connection policy expiry is invalid",
        )
    if checked.expires_at <= current:
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=request_id,
            reason="browser connection policy has expired",
        )
    return None


@dataclass(slots=True)
class _BrowserAdmissionRuntime:
    resource_count: int = 0
    blocked_resources: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BrowserAdmission:
    """Bound browser lease after connection-policy admission."""

    request: AcquisitionRequest
    attestation: BrowserNetworkAttestation
    admitted_at: datetime
    expires_at: datetime
    max_resources: int
    _runtime: _BrowserAdmissionRuntime = field(
        default_factory=_BrowserAdmissionRuntime,
        repr=False,
        compare=False,
    )

    @property
    def policy_identity(self) -> str:
        return self.attestation.policy_identity

    @property
    def resolver_address_control_identity(self) -> str:
        return self.attestation.resolver_address_control_identity

    @property
    def connection_binding_identity(self) -> str:
        return self.attestation.connection_binding_identity

    def reserve_resource(self, resource_type: str, url: str) -> bool:
        """Reserve one network resource under the request limit."""

        if self._runtime.resource_count >= self.max_resources:
            self._runtime.blocked_resources.append(
                (resource_type, url, "browser resource limit exceeded")
            )
            return False
        self._runtime.resource_count += 1
        return True

    @property
    def resource_count(self) -> int:
        return self._runtime.resource_count

    @property
    def blocked_resources(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(self._runtime.blocked_resources)


def _request_is_browser(request: object) -> bool:
    operation = getattr(request, "operation_class", None)
    if isinstance(operation, OperationClass):
        operation = operation.value
    return operation == OperationClass.BROWSER.value


def require_browser_policy(
    request: AcquisitionRequest,
    attestation: BrowserNetworkAttestation | Mapping[str, object] | None,
    *,
    now: datetime | float | int | str | None = None,
    release: str | None = None,
) -> BrowserAdmission | AcquisitionFailure:
    """Admit one browser request after checking the external attestation."""

    request_id = _request_id(request)
    if not isinstance(request, AcquisitionRequest):
        return _failure(request, "browser request contract is invalid")
    if not _request_is_browser(request):
        return _failure(request, "browser request has the wrong operation class")
    if request.profile not in {
        OriginProfile.PUBLIC_CONTENT,
        OriginProfile.AUTHENTICATED_CONTENT,
    }:
        return _failure(request, "browser request has an unsupported origin profile")
    try:
        parsed = urlsplit(request.normalized_url)
        valid_host = bool(parsed.hostname)
        parsed.port
    except ValueError:
        valid_host = False
        parsed = None
    if parsed is None or parsed.scheme not in {"http", "https"} or not valid_host:
        return _failure(request, "browser request URL is invalid")

    failure = validate_browser_attestation(
        attestation,
        now=now,
        release=release,
        request_id=request_id,
    )
    if failure is not None:
        return failure
    checked = _normalise_attestation(attestation)
    # ``validate_browser_attestation`` above guarantees this branch.  Keep the
    # defensive check because the provider is an external boundary.
    if checked is None:
        return _failure(request)
    admitted_at = _now(now)
    return BrowserAdmission(
        request=request,
        attestation=checked,
        admitted_at=admitted_at,
        expires_at=checked.expires_at,
        max_resources=request.limits.max_resource_count,
    )


async def _provider_current(
    provider: BrowserAttestationProvider | None = None,
) -> BrowserNetworkAttestation | Mapping[str, object] | None:
    selected = provider if provider is not None else get_browser_attestation_provider()
    try:
        value = selected.current()
        if inspect.isawaitable(value):
            value = await value
    except Exception:
        return None
    return value


async def admit_browser_request(
    request: AcquisitionRequest,
    *,
    provider: BrowserAttestationProvider | None = None,
    now: datetime | float | int | str | None = None,
    release: str | None = None,
) -> BrowserAdmission | AcquisitionFailure:
    """Refresh authority state, then admit a browser request.

    No attestation is cached.  A provider error or stale response fails closed
    before the caller may create a browser, CDP connection, or context.
    """

    attestation = await _provider_current(provider)
    return require_browser_policy(request, attestation, now=now, release=release)


async def admit_browser_url(
    url: str,
    *,
    profile: OriginProfile = OriginProfile.PUBLIC_CONTENT,
    credential_policy: str | CredentialPolicy = CredentialPolicy.NONE,
    caller_principal: str = "browser",
    request_id: str = "browser-policy",
    limits: AcquisitionLimits | None = None,
    provider: BrowserAttestationProvider | None = None,
    now: datetime | float | int | str | None = None,
    release: str | None = None,
) -> BrowserAdmission | AcquisitionFailure:
    """Build and admit a browser request for a legacy URL entry point."""

    request = make_browser_request(
        url,
        profile=profile,
        credential_policy=credential_policy,
        caller_principal=caller_principal,
        request_id=request_id,
        limits=limits,
    )
    return await admit_browser_request(
        request,
        provider=provider,
        now=now,
        release=release,
    )


def make_browser_request(
    url: str,
    *,
    profile: OriginProfile = OriginProfile.PUBLIC_CONTENT,
    credential_policy: str | CredentialPolicy = CredentialPolicy.NONE,
    caller_principal: str = "browser",
    request_id: str = "browser-policy",
    limits: AcquisitionLimits | None = None,
) -> AcquisitionRequest:
    """Build the explicit request used by legacy browser entry points."""

    return AcquisitionRequest(
        normalized_url=url,
        operation_class=OperationClass.BROWSER,
        profile=profile,
        credential_policy=credential_policy,
        limits=limits or AcquisitionLimits(),
        caller_principal=caller_principal,
        request_id=request_id,
    )


def failure_text(value: AcquisitionFailure) -> str:
    """Return a stable extractor error projection for a typed failure."""

    return f"{value.code.value}:{value.safe_reason}"


def _same_origin(source_url: str, target_url: str) -> bool:
    source = urlsplit(source_url)
    target = urlsplit(target_url)
    source_scheme = {"ws": "http", "wss": "https"}.get(
        source.scheme.lower(), source.scheme.lower()
    )
    target_scheme = {"ws": "http", "wss": "https"}.get(
        target.scheme.lower(), target.scheme.lower()
    )
    source_port = source.port or (
        443 if source.scheme.lower() in {"https", "wss"} else 80
    )
    target_port = target.port or (
        443 if target.scheme.lower() in {"https", "wss"} else 80
    )
    return (
        source_scheme == target_scheme
        and (source.hostname or "").lower() == (target.hostname or "").lower()
        and source_port == target_port
    )


async def _check_resource_url(
    admission: BrowserAdmission,
    url: str,
    *,
    url_checker: Callable[[str], tuple[bool, str]] = _default_url_checker,
) -> tuple[bool, str]:
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError:
        return False, "invalid browser resource URL"
    if parsed.scheme.lower() in _NON_NETWORK_SCHEMES:
        return True, ""
    if (
        parsed.scheme.lower() not in {"http", "https", "ws", "wss"}
        or not parsed.hostname
    ):
        return False, "unsupported browser resource scheme"
    if admission.request.profile is OriginProfile.AUTHENTICATED_CONTENT:
        try:
            same_origin = _same_origin(admission.request.normalized_url, url)
        except ValueError:
            same_origin = False
        if not same_origin:
            return False, "cross-origin authenticated resource blocked"
    try:
        policy_url = url
        if parsed.scheme.lower() in {"ws", "wss"}:
            policy_url = parsed._replace(
                scheme="https" if parsed.scheme.lower() == "wss" else "http"
            ).geturl()
        decision = await asyncio.wait_for(
            asyncio.to_thread(url_checker, policy_url),
            timeout=2.0,
        )
        if inspect.isawaitable(decision):
            decision = await decision
    except Exception:
        return False, "browser resource policy check failed"
    try:
        safe, reason = decision
    except (TypeError, ValueError):
        return False, "browser resource policy check failed"
    if not safe:
        return False, reason or "browser resource rejected"
    return True, ""


async def guard_browser_route(
    route: Any,
    admission: BrowserAdmission,
    *,
    url_checker: Callable[[str], tuple[bool, str]] = _default_url_checker,
) -> None:
    """Abort an unsafe browser request before Playwright dispatches it."""

    request = getattr(route, "request", None)
    url = getattr(request, "url", "")
    resource_type = str(getattr(request, "resource_type", "other") or "other").lower()
    if resource_type not in _BROWSER_RESOURCE_TYPES:
        resource_type = "other"
    if not isinstance(url, str):
        url = ""
    allowed, reason = await _check_resource_url(
        admission,
        url,
        url_checker=url_checker,
    )
    if allowed:
        if admission.reserve_resource(resource_type, url):
            continue_route = getattr(route, "continue_", None)
            if callable(continue_route):
                result = continue_route()
                if inspect.isawaitable(result):
                    await result
            return
        reason = "browser resource limit exceeded"

    admission._runtime.blocked_resources.append((resource_type, url, reason))
    abort = getattr(route, "abort", None)
    if callable(abort):
        try:
            result = abort()
            if inspect.isawaitable(result):
                await result
        except Exception:
            # A route that cannot be aborted is still never continued.  The
            # browser will fail the request rather than receive an unsafe URL.
            return


async def guard_browser_websocket(
    web_socket: Any,
    admission: BrowserAdmission,
    *,
    url_checker: Callable[[str], tuple[bool, str]] = _default_url_checker,
) -> None:
    """Close browser WebSockets unless the normal policy explicitly allows it."""

    url = getattr(web_socket, "url", "")
    allowed, reason = await _check_resource_url(
        admission,
        url if isinstance(url, str) else "",
        url_checker=url_checker,
    )
    if allowed:
        if admission.reserve_resource("websocket", url):
            return
        reason = "browser resource limit exceeded"
    admission._runtime.blocked_resources.append(
        ("websocket", url, reason or "websocket blocked")
    )
    close = getattr(web_socket, "close", None)
    if callable(close):
        try:
            result = close(code=1008, reason="network policy")
            if inspect.isawaitable(result):
                await result
        except Exception:
            return


async def install_browser_policy(
    context: Any,
    admission: BrowserAdmission,
    *,
    url_checker: Callable[[str], tuple[bool, str]] = _default_url_checker,
) -> AcquisitionFailure | None:
    """Install document and WebSocket guards before a page is created."""

    route = getattr(context, "route", None)
    if not callable(route):
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=_request_id(admission.request),
            reason="browser request interception is unavailable",
        )
    try:
        result = route(
            "**/*",
            lambda request_route: guard_browser_route(
                request_route,
                admission,
                url_checker=url_checker,
            ),
        )
        if inspect.isawaitable(result):
            await result
    except Exception:
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=_request_id(admission.request),
            reason="browser request interception could not be installed",
        )

    route_web_socket = getattr(context, "route_web_socket", None)
    if not callable(route_web_socket):
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=_request_id(admission.request),
            reason="browser WebSocket interception is unavailable",
        )
    try:
        result = route_web_socket(
            "**/*",
            lambda web_socket: guard_browser_websocket(
                web_socket,
                admission,
                url_checker=url_checker,
            ),
        )
        if inspect.isawaitable(result):
            await result
    except Exception:
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=_request_id(admission.request),
            reason="browser WebSocket interception could not be installed",
        )
    return None


__all__ = [
    "BrowserAdmission",
    "BrowserAttestationProvider",
    "BrowserNetworkAttestation",
    "UnavailableBrowserAttestationProvider",
    "admit_browser_request",
    "admit_browser_url",
    "current_release_identity",
    "failure_text",
    "guard_browser_route",
    "guard_browser_websocket",
    "get_browser_attestation_provider",
    "install_browser_policy",
    "make_browser_request",
    "require_browser_policy",
    "set_browser_attestation_provider",
    "validate_browser_attestation",
]
