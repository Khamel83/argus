"""Shared, typed failure categories for Argus boundaries.

Deep modules can return their native failure objects.  This module gives the
HTTP, MCP, and CLI presenters one closed vocabulary and one status mapping.
It does not expose provider responses, URLs, credentials, or tracebacks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .outcomes import CanonicalOutcome, OperationError, http_status_for


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SECRET_MARKER = re.compile(
    r"(?:authorization\s*:|bearer\s+|cookie\s*:|set-cookie\s*:|"
    r"(?:api[_-]?key|credential|password|secret|signature|token)\s*[=:])",
    re.IGNORECASE,
)


class FailureCode(str, Enum):
    """The minimum stable failure vocabulary shared by all transports."""

    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_REJECTED = "authentication_rejected"
    POLICY_REJECTED = "policy_rejected"
    ACQUISITION_BLOCKED = "acquisition_blocked"
    BROWSER_POLICY_UNAVAILABLE = "browser_policy_unavailable"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_UNREADY = "provider_unready"
    PROVIDERS_FAILED = "providers_failed"
    EXTRACTION_FAILED = "extraction_failed"
    SPEND_DENIED = "spend_denied"
    CHARGE_UNCERTAIN = "charge_uncertain"
    PERSISTENCE_FAILED = "persistence_failed"
    OWNERSHIP_DENIED = "ownership_denied"
    WORKFLOW_OWNER_UNAVAILABLE = "workflow_owner_unavailable"
    WORKFLOW_ARTIFACT_NOT_PUBLISHED = "workflow_artifact_not_published"
    RECOVERY_NOT_VERIFIED = "recovery_not_verified"
    DELIVERY_PENDING = "delivery_pending"
    DELIVERY_FAILED = "delivery_failed"


@dataclass(frozen=True, slots=True)
class FailureSpec:
    """Transport mapping for one stable failure code."""

    code: FailureCode
    outcome: CanonicalOutcome
    status: int
    default_retryable: bool
    operation_began: bool


_SPECS = {
    FailureCode.INVALID_REQUEST: FailureSpec(
        FailureCode.INVALID_REQUEST,
        CanonicalOutcome.INVALID_REQUEST,
        422,
        False,
        False,
    ),
    FailureCode.AUTHENTICATION_REJECTED: FailureSpec(
        FailureCode.AUTHENTICATION_REJECTED,
        CanonicalOutcome.AUTHENTICATION_REJECTED,
        401,
        False,
        False,
    ),
    FailureCode.POLICY_REJECTED: FailureSpec(
        FailureCode.POLICY_REJECTED,
        CanonicalOutcome.POLICY_REJECTED,
        403,
        False,
        False,
    ),
    FailureCode.ACQUISITION_BLOCKED: FailureSpec(
        FailureCode.ACQUISITION_BLOCKED,
        CanonicalOutcome.POLICY_REJECTED,
        403,
        False,
        True,
    ),
    FailureCode.BROWSER_POLICY_UNAVAILABLE: FailureSpec(
        FailureCode.BROWSER_POLICY_UNAVAILABLE,
        CanonicalOutcome.UNREADY,
        503,
        False,
        True,
    ),
    FailureCode.TIMEOUT: FailureSpec(
        FailureCode.TIMEOUT,
        CanonicalOutcome.TIMEOUT,
        504,
        True,
        True,
    ),
    FailureCode.PROVIDER_UNAVAILABLE: FailureSpec(
        FailureCode.PROVIDER_UNAVAILABLE,
        CanonicalOutcome.UNREADY,
        503,
        True,
        True,
    ),
    FailureCode.PROVIDER_UNREADY: FailureSpec(
        FailureCode.PROVIDER_UNREADY,
        CanonicalOutcome.UNREADY,
        503,
        True,
        True,
    ),
    FailureCode.PROVIDERS_FAILED: FailureSpec(
        FailureCode.PROVIDERS_FAILED,
        CanonicalOutcome.PROVIDERS_FAILED,
        502,
        True,
        True,
    ),
    FailureCode.EXTRACTION_FAILED: FailureSpec(
        FailureCode.EXTRACTION_FAILED,
        CanonicalOutcome.EXTRACTION_FAILED,
        502,
        True,
        True,
    ),
    FailureCode.SPEND_DENIED: FailureSpec(
        FailureCode.SPEND_DENIED,
        CanonicalOutcome.UNREADY,
        503,
        False,
        True,
    ),
    FailureCode.CHARGE_UNCERTAIN: FailureSpec(
        FailureCode.CHARGE_UNCERTAIN,
        CanonicalOutcome.UNREADY,
        503,
        False,
        True,
    ),
    FailureCode.PERSISTENCE_FAILED: FailureSpec(
        FailureCode.PERSISTENCE_FAILED,
        CanonicalOutcome.PERSISTENCE_FAILED,
        503,
        True,
        True,
    ),
    FailureCode.OWNERSHIP_DENIED: FailureSpec(
        FailureCode.OWNERSHIP_DENIED,
        CanonicalOutcome.POLICY_REJECTED,
        403,
        False,
        True,
    ),
    FailureCode.WORKFLOW_OWNER_UNAVAILABLE: FailureSpec(
        FailureCode.WORKFLOW_OWNER_UNAVAILABLE,
        CanonicalOutcome.UNREADY,
        503,
        True,
        True,
    ),
    FailureCode.WORKFLOW_ARTIFACT_NOT_PUBLISHED: FailureSpec(
        FailureCode.WORKFLOW_ARTIFACT_NOT_PUBLISHED,
        CanonicalOutcome.UNREADY,
        409,
        True,
        True,
    ),
    FailureCode.RECOVERY_NOT_VERIFIED: FailureSpec(
        FailureCode.RECOVERY_NOT_VERIFIED,
        CanonicalOutcome.UNREADY,
        503,
        False,
        True,
    ),
    FailureCode.DELIVERY_PENDING: FailureSpec(
        FailureCode.DELIVERY_PENDING,
        CanonicalOutcome.UNREADY,
        202,
        True,
        True,
    ),
    FailureCode.DELIVERY_FAILED: FailureSpec(
        FailureCode.DELIVERY_FAILED,
        CanonicalOutcome.UNREADY,
        502,
        False,
        True,
    ),
}


@dataclass(frozen=True, slots=True)
class FailureRecord:
    """Bounded causal failure evidence used before transport presentation."""

    code: FailureCode
    safe_reason: str
    request_id: str
    operation_id: str | None = None
    release_identity: str | None = None
    evidence_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        code = FailureCode(self.code)
        object.__setattr__(self, "code", code)
        _bounded_text("safe_reason", self.safe_reason, 1024)
        _bounded_text("request_id", self.request_id, 128)
        if self.operation_id is not None:
            _bounded_text("operation_id", self.operation_id, 128)
        if self.release_identity is not None:
            _bounded_text("release_identity", self.release_identity, 128)
        if len(self.evidence_references) > 16:
            raise ValueError("evidence_references must contain at most 16 values")
        for reference in self.evidence_references:
            _bounded_text("evidence_reference", reference, 256)


def _bounded_text(name: str, value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be non-empty and bounded")
    if not value.isprintable() or _SECRET_MARKER.search(value):
        raise ValueError(f"{name} contains unsafe detail")
    return value


def failure_spec(code: FailureCode | str) -> FailureSpec:
    """Return the closed transport mapping for ``code``."""

    try:
        return _SPECS[FailureCode(code)]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"unknown failure code: {code!r}") from error


def failure_codes() -> tuple[FailureCode, ...]:
    """Return all codes in deterministic order for contract tests."""

    return tuple(sorted(_SPECS, key=lambda item: item.value))


def operation_error_for(
    record: FailureRecord,
    *,
    operation_began: bool | None = None,
    retryable: bool | None = None,
    retry_after_seconds: int | None = None,
) -> OperationError:
    """Convert bounded failure evidence to the existing operation envelope."""

    spec = failure_spec(record.code)
    began = spec.operation_began if operation_began is None else operation_began
    detail = record.safe_reason
    return OperationError(
        outcome=spec.outcome,
        type=f"urn:argus:problem:{spec.code.value}",
        title=spec.code.value.replace("_", " ").title(),
        status=http_status_for(spec.outcome, spec.code.value),
        detail=detail,
        instance=f"urn:argus:request:{record.request_id}",
        code=spec.code.value,
        retryable=spec.default_retryable if retryable is None else retryable,
        retry_after_seconds=retry_after_seconds,
        operation_began=began,
    )


__all__ = [
    "FailureCode",
    "FailureRecord",
    "FailureSpec",
    "failure_codes",
    "failure_spec",
    "operation_error_for",
]
