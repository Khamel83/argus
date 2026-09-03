"""Frozen values exchanged at the guarded-acquisition boundary.

This module contains policy-neutral values only.  It intentionally has no HTTP,
DNS, browser, or socket dependencies.  The later acquisition implementation
may attach those behaviours behind the protocol exported by the package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any


MAX_NORMALIZED_URL_LENGTH = 2_048
MAX_OPERATION_CLASS_LENGTH = 64
MAX_CREDENTIAL_POLICY_LENGTH = 64
MAX_CALLER_PRINCIPAL_LENGTH = 128
MAX_REQUEST_ID_LENGTH = 64
MAX_REFERENCE_LENGTH = 128
MAX_CONTENT_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_REFERENCES = 16
MAX_REDIRECT_HOPS = 100
MAX_RESOURCE_COUNT = 1_000
MAX_TIMEOUT_SECONDS = 600.0
MAX_HEADER_BYTES = 1 * 1024 * 1024


class OriginProfile(str, Enum):
    """The explicit origin and credential boundary for one acquisition."""

    PUBLIC_CONTENT = "public_content"
    AUTHENTICATED_CONTENT = "authenticated_content"
    THIRD_PARTY_FETCH = "third_party_fetch"


class CredentialPolicy(str, Enum):
    """Common credential policies accepted by an acquisition request.

    ``AcquisitionRequest`` keeps this field as a bounded string so a future
    policy can be introduced without changing the request shape.  These values
    provide a closed vocabulary for callers that do not need a custom policy.
    """

    NONE = "none"
    ORIGIN_SCOPED = "origin_scoped"
    PUBLIC_ALLOWLIST = "public_allowlist"


class OperationClass(str, Enum):
    """Common operation classes for direct, browser, and third-party work."""

    DIRECT_HTTP = "direct_http"
    BROWSER = "browser"
    THIRD_PARTY = "third_party"


# A descriptive alias used by some callers and by the later transport layer.
AcquisitionOperation = OperationClass


def _text(
    name: str,
    value: object,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    """Return bounded printable text without silently truncating identity."""

    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    if not value.isprintable():
        raise ValueError(f"{name} contains control characters")
    return value


def _sequence(value: object, *, name: str, maximum: int) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{name} must be a sequence of values")
    try:
        result = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be a sequence of values") from exc
    if len(result) > maximum:
        raise ValueError(f"{name} exceeds {maximum} entries")
    return result


def _nonnegative_integer(name: str, value: object, *, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise ValueError(f"{name} must be an integer from 0 through {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class AcquisitionLimits:
    """Finite request limits carried into every acquisition implementation."""

    max_body_bytes: int = MAX_CONTENT_BYTES
    max_resource_count: int = 100
    max_redirect_hops: int = 10
    timeout_seconds: float = 30.0
    max_header_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if (
            type(self.max_body_bytes) is not int
            or not 1 <= self.max_body_bytes <= MAX_CONTENT_BYTES
        ):
            raise ValueError(
                f"max_body_bytes must be from 1 through {MAX_CONTENT_BYTES}"
            )
        if (
            type(self.max_resource_count) is not int
            or not 1 <= self.max_resource_count <= MAX_RESOURCE_COUNT
        ):
            raise ValueError(
                f"max_resource_count must be from 1 through {MAX_RESOURCE_COUNT}"
            )
        if (
            type(self.max_redirect_hops) is not int
            or not 0 <= self.max_redirect_hops <= MAX_REDIRECT_HOPS
        ):
            raise ValueError(
                f"max_redirect_hops must be from 0 through {MAX_REDIRECT_HOPS}"
            )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or not 0 < float(self.timeout_seconds) <= MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"timeout_seconds must be finite and from 0 through {MAX_TIMEOUT_SECONDS}"
            )
        if (
            type(self.max_header_bytes) is not int
            or not 1 <= self.max_header_bytes <= MAX_HEADER_BYTES
        ):
            raise ValueError(
                f"max_header_bytes must be from 1 through {MAX_HEADER_BYTES}"
            )
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    @property
    def max_response_bytes(self) -> int:
        """Compatibility name for the bounded response body."""

        return self.max_body_bytes

    @property
    def max_decompressed_bytes(self) -> int:
        """Compatibility name for the bounded decompressed response body."""

        return self.max_body_bytes

    @property
    def max_resources(self) -> int:
        """Compatibility name for the resource count ceiling."""

        return self.max_resource_count

    @property
    def max_redirects(self) -> int:
        """Compatibility name for the redirect-hop ceiling."""

        return self.max_redirect_hops


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    """Immutable, bounded input to a guarded acquisition."""

    normalized_url: str
    operation_class: str | OperationClass
    profile: OriginProfile
    credential_policy: str | CredentialPolicy
    limits: AcquisitionLimits = field(default_factory=AcquisitionLimits)
    caller_principal: str = ""
    request_id: str = ""
    # A service origin is an explicit, caller-owned exception for private
    # network services such as the configured SearXNG authority.  It is never
    # inferred from the URL and is accepted only for the static trusted
    # service callers enforced by the guarded implementation.
    trusted_service_origin: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "normalized_url",
            _text(
                "normalized_url",
                self.normalized_url,
                maximum=MAX_NORMALIZED_URL_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "operation_class",
            _text(
                "operation_class",
                self.operation_class,
                maximum=MAX_OPERATION_CLASS_LENGTH,
            ),
        )
        try:
            profile = OriginProfile(self.profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("profile must be an explicit OriginProfile") from exc
        object.__setattr__(self, "profile", profile)
        object.__setattr__(
            self,
            "credential_policy",
            _text(
                "credential_policy",
                self.credential_policy,
                maximum=MAX_CREDENTIAL_POLICY_LENGTH,
            ),
        )
        if not isinstance(self.limits, AcquisitionLimits):
            raise TypeError("limits must be AcquisitionLimits")
        object.__setattr__(
            self,
            "caller_principal",
            _text(
                "caller_principal",
                self.caller_principal,
                maximum=MAX_CALLER_PRINCIPAL_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "request_id",
            _text(
                "request_id",
                self.request_id,
                maximum=MAX_REQUEST_ID_LENGTH,
            ),
        )
        if self.trusted_service_origin is not None:
            object.__setattr__(
                self,
                "trusted_service_origin",
                _text(
                    "trusted_service_origin",
                    self.trusted_service_origin,
                    maximum=MAX_NORMALIZED_URL_LENGTH,
                ),
            )

    @property
    def caller(self) -> str:
        """Compatibility name for the authenticated caller principal."""

        return self.caller_principal


@dataclass(frozen=True, slots=True)
class LogicalOrigin:
    """The approved TLS and HTTP logical origin identity."""

    scheme: str
    hostname: str
    port: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "scheme",
            _text("scheme", self.scheme, maximum=16).lower(),
        )
        object.__setattr__(
            self,
            "hostname",
            _text("hostname", self.hostname, maximum=MAX_NORMALIZED_URL_LENGTH),
        )
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise ValueError("port must be an integer from 1 through 65535")

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.hostname}:{self.port}"


@dataclass(frozen=True, slots=True)
class DialAddressEvidence:
    """Bounded evidence for the address selected for one connection."""

    address: str
    logical_hostname: str = ""
    port: int = 0
    lease_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", _text("address", self.address, maximum=128))
        object.__setattr__(
            self,
            "logical_hostname",
            _text(
                "logical_hostname",
                self.logical_hostname,
                maximum=MAX_NORMALIZED_URL_LENGTH,
                allow_empty=True,
            ),
        )
        if type(self.port) is not int or not 0 <= self.port <= 65_535:
            raise ValueError("port must be an integer from 0 through 65535")
        if self.lease_reference is not None:
            object.__setattr__(
                self,
                "lease_reference",
                _text(
                    "lease_reference",
                    self.lease_reference,
                    maximum=MAX_REFERENCE_LENGTH,
                ),
            )

    @property
    def approved_address(self) -> str:
        return self.address

    @property
    def dial_address(self) -> str:
        return self.address


@dataclass(frozen=True, slots=True)
class RedirectHop:
    """One already-validated redirect observation."""

    source: str
    target: str
    status_code: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source",
            _text("source", self.source, maximum=MAX_NORMALIZED_URL_LENGTH),
        )
        object.__setattr__(
            self,
            "target",
            _text("target", self.target, maximum=MAX_NORMALIZED_URL_LENGTH),
        )
        if type(self.status_code) is not int or not 300 <= self.status_code <= 399:
            raise ValueError("redirect status_code must be in range 300 through 399")

    @property
    def from_url(self) -> str:
        return self.source

    @property
    def to_url(self) -> str:
        return self.target


@dataclass(frozen=True, slots=True)
class RedirectTrace:
    """An immutable, bounded redirect trace."""

    hops: tuple[RedirectHop, ...] = ()

    def __post_init__(self) -> None:
        hops = _sequence(self.hops, name="redirect_trace", maximum=MAX_REDIRECT_HOPS)
        if any(not isinstance(hop, RedirectHop) for hop in hops):
            raise TypeError("redirect_trace entries must be RedirectHop")
        object.__setattr__(self, "hops", hops)

    def __len__(self) -> int:
        return len(self.hops)


@dataclass(frozen=True, slots=True)
class ResourceCounts:
    """Bounded counts emitted by direct or browser acquisition."""

    requests: int = 0
    responses: int = 0
    bytes_received: int = 0
    redirects: int = 0

    def __post_init__(self) -> None:
        for name in ("requests", "responses", "bytes_received", "redirects"):
            maximum = (
                MAX_CONTENT_BYTES if name == "bytes_received" else MAX_RESOURCE_COUNT
            )
            object.__setattr__(
                self,
                name,
                _nonnegative_integer(name, getattr(self, name), maximum=maximum),
            )

    @property
    def total(self) -> int:
        return self.requests


@dataclass(frozen=True, slots=True)
class CleanupReceipt:
    """Opaque proof that owned acquisition resources have a closure record."""

    receipt_ref: str = "pending"
    closed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "receipt_ref",
            _text("receipt_ref", self.receipt_ref, maximum=MAX_REFERENCE_LENGTH),
        )
        if type(self.closed) is not bool:
            raise TypeError("closed must be a boolean")

    @property
    def receipt_id(self) -> str:
        return self.receipt_ref


@dataclass(frozen=True, slots=True)
class BoundedContent:
    """Optional in-memory content projection with a hard size ceiling."""

    body: bytes
    content_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes):
            raise TypeError("body must be bytes")
        if len(self.body) > MAX_CONTENT_BYTES:
            raise ValueError("body exceeds the bounded content limit")
        if self.content_type is not None:
            object.__setattr__(
                self,
                "content_type",
                _text("content_type", self.content_type, maximum=128),
            )

    @property
    def data(self) -> bytes:
        return self.body


@dataclass(frozen=True, slots=True)
class BoundedStream:
    """Opaque provider-owned stream identity, never a live stream handle."""

    stream_ref: str
    max_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stream_ref",
            _text("stream_ref", self.stream_ref, maximum=MAX_REFERENCE_LENGTH),
        )
        if (
            type(self.max_bytes) is not int
            or not 1 <= self.max_bytes <= MAX_CONTENT_BYTES
        ):
            raise ValueError(f"max_bytes must be from 1 through {MAX_CONTENT_BYTES}")


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    """Bounded output of a successful guarded acquisition."""

    approved_logical_origin: LogicalOrigin | str = ""
    dial_address_evidence: tuple[DialAddressEvidence | str, ...] = ()
    redirect_trace: RedirectTrace | tuple[RedirectHop | str, ...] = field(
        default_factory=tuple
    )
    content: bytes | str | BoundedContent | None = None
    stream: BoundedStream | None = None
    resource_counts: ResourceCounts = field(default_factory=ResourceCounts)
    cleanup_receipt: CleanupReceipt | None = None

    def __post_init__(self) -> None:
        if isinstance(self.approved_logical_origin, LogicalOrigin):
            pass
        else:
            object.__setattr__(
                self,
                "approved_logical_origin",
                _text(
                    "approved_logical_origin",
                    self.approved_logical_origin,
                    maximum=MAX_NORMALIZED_URL_LENGTH,
                    allow_empty=True,
                ),
            )
        evidence = _sequence(
            self.dial_address_evidence,
            name="dial_address_evidence",
            maximum=MAX_EVIDENCE_REFERENCES,
        )
        for item in evidence:
            if isinstance(item, DialAddressEvidence):
                continue
            if isinstance(item, str):
                continue
            raise TypeError(
                "dial_address_evidence entries must be DialAddressEvidence or text"
            )
        object.__setattr__(self, "dial_address_evidence", evidence)

        trace = self.redirect_trace
        if isinstance(trace, RedirectTrace):
            normalized_trace: RedirectTrace | tuple[RedirectHop | str, ...] = trace
        else:
            entries = _sequence(
                trace,
                name="redirect_trace",
                maximum=MAX_REDIRECT_HOPS,
            )
            for item in entries:
                if not isinstance(item, (RedirectHop, str)):
                    raise TypeError(
                        "redirect_trace entries must be RedirectHop or text"
                    )
            normalized_trace = entries
        object.__setattr__(self, "redirect_trace", normalized_trace)

        if self.content is not None:
            if isinstance(self.content, BoundedContent):
                content_length = len(self.content.body)
            elif isinstance(self.content, bytes):
                content_length = len(self.content)
            elif isinstance(self.content, str):
                content_length = len(self.content.encode("utf-8"))
            else:
                raise TypeError(
                    "content must be bounded bytes, text, or BoundedContent"
                )
            if content_length > MAX_CONTENT_BYTES:
                raise ValueError("content exceeds the bounded content limit")
        if self.stream is not None and not isinstance(self.stream, BoundedStream):
            raise TypeError("stream must be an opaque BoundedStream")
        if self.content is not None and self.stream is not None:
            raise ValueError("content and stream are mutually exclusive")
        if not isinstance(self.resource_counts, ResourceCounts):
            raise TypeError("resource_counts must be ResourceCounts")
        if self.cleanup_receipt is not None and not isinstance(
            self.cleanup_receipt, CleanupReceipt
        ):
            raise TypeError("cleanup_receipt must be CleanupReceipt")


@dataclass(frozen=True, slots=True)
class GuardedBrowserSession:
    """Opaque browser-session lease returned after policy admission.

    The lease contains identity and cleanup evidence only.  It intentionally
    does not carry a browser, page, context, client, or socket object.
    """

    session_ref: str = ""
    approved_logical_origin: LogicalOrigin | str = ""
    resource_counts: ResourceCounts = field(default_factory=ResourceCounts)
    cleanup_receipt: CleanupReceipt | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "session_ref",
            _text(
                "session_ref",
                self.session_ref,
                maximum=MAX_REFERENCE_LENGTH,
                allow_empty=True,
            ),
        )
        if not isinstance(self.approved_logical_origin, LogicalOrigin):
            object.__setattr__(
                self,
                "approved_logical_origin",
                _text(
                    "approved_logical_origin",
                    self.approved_logical_origin,
                    maximum=MAX_NORMALIZED_URL_LENGTH,
                    allow_empty=True,
                ),
            )
        if not isinstance(self.resource_counts, ResourceCounts):
            raise TypeError("resource_counts must be ResourceCounts")
        if self.cleanup_receipt is not None and not isinstance(
            self.cleanup_receipt, CleanupReceipt
        ):
            raise TypeError("cleanup_receipt must be CleanupReceipt")
