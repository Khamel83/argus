"""Closed accepted-operation outcomes and transport-independent invariants."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import InitVar, dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Generic, TypeVar


class CanonicalOutcome(str, Enum):
    """Scorecard outcomes in their canonical compatibility order."""

    SUCCESS = "success"
    DEGRADED = "degraded"
    EMPTY = "empty"
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_REJECTED = "authentication_rejected"
    POLICY_REJECTED = "policy_rejected"
    TIMEOUT = "timeout"
    PERSISTENCE_FAILED = "persistence_failed"
    PROVIDERS_FAILED = "providers_failed"
    EXTRACTION_FAILED = "extraction_failed"
    UNREADY = "unready"


_DEFAULT_HTTP_STATUS = {
    CanonicalOutcome.SUCCESS: 200,
    CanonicalOutcome.DEGRADED: 200,
    CanonicalOutcome.EMPTY: 200,
    CanonicalOutcome.INVALID_REQUEST: 422,
    CanonicalOutcome.AUTHENTICATION_REJECTED: 401,
    CanonicalOutcome.POLICY_REJECTED: 403,
    CanonicalOutcome.TIMEOUT: 504,
    CanonicalOutcome.PERSISTENCE_FAILED: 503,
    CanonicalOutcome.PROVIDERS_FAILED: 502,
    CanonicalOutcome.EXTRACTION_FAILED: 502,
    CanonicalOutcome.UNREADY: 503,
}

_NARROW_HTTP_STATUS = {
    "malformed_request": (CanonicalOutcome.INVALID_REQUEST, 400),
    "payload_too_large": (CanonicalOutcome.INVALID_REQUEST, 413),
    "unsupported_media_type": (CanonicalOutcome.INVALID_REQUEST, 415),
    "route_not_found": (CanonicalOutcome.INVALID_REQUEST, 404),
    "idempotency_conflict": (CanonicalOutcome.INVALID_REQUEST, 409),
    "session_not_found": (CanonicalOutcome.UNREADY, 404),
    "rate_limited": (CanonicalOutcome.UNREADY, 429),
    "internal_failure": (CanonicalOutcome.UNREADY, 503),
    "misdirected_request": (CanonicalOutcome.POLICY_REJECTED, 421),
    "method_not_allowed": (CanonicalOutcome.INVALID_REQUEST, 405),
}
_ADMISSION_ONLY_CODES = frozenset(_NARROW_HTTP_STATUS)
_CANONICAL_ERROR_TITLES = {
    code: code.replace("_", " ").title()
    for code in (
        *(outcome.value for outcome in CanonicalOutcome),
        *_ADMISSION_ONLY_CODES,
    )
}

_SUCCESS_LIKE = {
    CanonicalOutcome.SUCCESS,
    CanonicalOutcome.DEGRADED,
    CanonicalOutcome.EMPTY,
}
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONTRACT_VERSION = "2.0"
_MAX_RETRY_AFTER_SECONDS = 86_400

T = TypeVar("T")


def _as_outcome(outcome: CanonicalOutcome) -> CanonicalOutcome:
    try:
        return CanonicalOutcome(outcome)
    except (TypeError, ValueError) as error:
        raise ValueError(f"unknown canonical outcome: {outcome!r}") from error


def is_success_like(outcome: CanonicalOutcome) -> bool:
    """Return whether an accepted outcome is non-error across transports."""

    return _as_outcome(outcome) in _SUCCESS_LIKE


def http_status_for(
    outcome: CanonicalOutcome,
    code: str | None = None,
) -> int:
    """Return the closed ADR 0006 status for an outcome and optional code."""

    canonical = _as_outcome(outcome)
    if code is None or code == canonical.value:
        return _DEFAULT_HTTP_STATUS[canonical]
    if not isinstance(code, str) or not _SAFE_CODE.fullmatch(code):
        raise ValueError(f"unknown error code: {code!r}")

    try:
        code_outcome = CanonicalOutcome(code)
    except ValueError:
        code_outcome = None
    if code_outcome is not None:
        raise ValueError(
            f"error code {code!r} is not valid for outcome {canonical.value!r}"
        )

    narrow = _NARROW_HTTP_STATUS.get(code)
    if narrow is None:
        raise ValueError(f"unknown error code: {code!r}")
    required_outcome, status = narrow
    if canonical is not required_outcome:
        raise ValueError(
            f"error code {code!r} is not valid for outcome {canonical.value!r}"
        )
    return status


def mcp_is_error_for(outcome: CanonicalOutcome) -> bool:
    """Return the MCP tool-result error bit for an accepted operation."""

    return not is_success_like(outcome)


def _require_bounded_text(
    name: str,
    value: str,
    *,
    maximum: int,
) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be non-empty and at most {maximum} characters")


def _freeze(value: Any, active: set[int] | None = None) -> Any:
    active = set() if active is None else active
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("result must contain acyclic JSON-compatible values")
        if not all(isinstance(key, str) for key in value):
            raise ValueError("result object keys must be strings")
        active.add(identity)
        try:
            return MappingProxyType(
                {
                    str(key): _freeze(child, active)
                    for key, child in value.items()
                }
            )
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("result must contain acyclic JSON-compatible values")
        active.add(identity)
        try:
            return tuple(_freeze(child, active) for child in value)
        finally:
            active.remove(identity)
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float) and math.isfinite(value):
        return float(value)
    raise ValueError("result leaves must be immutable JSON-compatible values")


@dataclass(frozen=True, slots=True)
class OperationError:
    """Stable, bounded problem detail for one accepted operation."""

    outcome: InitVar[CanonicalOutcome]
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    retryable: bool
    retry_after_seconds: int | None
    operation_began: InitVar[bool] = True

    def __post_init__(
        self,
        outcome: CanonicalOutcome,
        operation_began: bool,
    ) -> None:
        canonical = _as_outcome(outcome)
        if not isinstance(operation_began, bool):
            raise ValueError("operation_began must be a boolean")
        if not isinstance(self.code, str) or not _SAFE_CODE.fullmatch(self.code):
            raise ValueError("code must be a bounded stable identifier")
        if operation_began and self.code in _ADMISSION_ONLY_CODES:
            raise ValueError(
                f"{self.code!r} is admission-only after operation acceptance"
            )
        if self.type != f"urn:argus:problem:{self.code}":
            raise ValueError("type must be the stable URN for code")
        if self.title != _CANONICAL_ERROR_TITLES.get(self.code):
            raise ValueError("title must be canonical for code")
        _require_bounded_text("title", self.title, maximum=128)
        _require_bounded_text("detail", self.detail, maximum=1024)
        _require_bounded_text("instance", self.instance, maximum=128)
        request_prefix = "urn:argus:request:"
        if not self.instance.startswith(request_prefix) or not _SAFE_REQUEST_ID.fullmatch(
            self.instance.removeprefix(request_prefix)
        ):
            raise ValueError("instance must contain only a bounded request_id")
        if (
            isinstance(self.status, bool)
            or not isinstance(self.status, int)
            or self.status != http_status_for(canonical, self.code)
        ):
            raise ValueError("status must match the outcome and code")
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be a boolean")
        if self.retry_after_seconds is not None:
            if (
                isinstance(self.retry_after_seconds, bool)
                or not isinstance(self.retry_after_seconds, int)
                or self.retry_after_seconds < 0
                or self.retry_after_seconds > _MAX_RETRY_AFTER_SECONDS
            ):
                raise ValueError(
                    "retry_after_seconds must be an integer from 0 through "
                    f"{_MAX_RETRY_AFTER_SECONDS}"
                )
            if not self.retryable:
                raise ValueError(
                    "retry_after_seconds requires retryable to be true"
                )


@dataclass(frozen=True, slots=True)
class AcceptedOperation(Generic[T]):
    """Immutable transport-neutral result of one accepted Argus operation."""

    outcome: CanonicalOutcome
    request_id: str
    result: T | None
    error: OperationError | None
    contract_version: str = _CONTRACT_VERSION
    operation_began: InitVar[bool] = True

    def __post_init__(self, operation_began: bool) -> None:
        canonical = _as_outcome(self.outcome)
        object.__setattr__(self, "outcome", canonical)
        if not isinstance(operation_began, bool):
            raise ValueError("operation_began must be a boolean")
        if self.contract_version != _CONTRACT_VERSION:
            raise ValueError("contract_version must be exactly '2.0'")
        if not isinstance(self.request_id, str) or not _SAFE_REQUEST_ID.fullmatch(
            self.request_id
        ):
            raise ValueError("request_id must use the bounded safe correlation form")
        if self.result is not None:
            if not isinstance(self.result, Mapping):
                raise ValueError("result must be an object result or None")
            object.__setattr__(self, "result", _freeze(self.result))

        if is_success_like(canonical):
            if self.result is None:
                raise ValueError("success-like outcomes require an object result")
            if self.error is not None:
                raise ValueError("success-like outcomes must not have an error")
            return

        if self.error is None:
            raise ValueError("failure outcome requires an error")
        if operation_began and self.error.code in _ADMISSION_ONLY_CODES:
            raise ValueError(
                f"{self.error.code!r} is admission-only after operation acceptance"
            )
        expected_status = http_status_for(canonical, self.error.code)
        if self.error.status != expected_status:
            raise ValueError("error does not match the operation outcome")
        if self.error.instance != f"urn:argus:request:{self.request_id}":
            raise ValueError("error instance must match the operation request_id")
