"""Stable, privacy-safe failures for the guarded-acquisition boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from .models import (
    MAX_EVIDENCE_REFERENCES,
    MAX_REFERENCE_LENGTH,
    MAX_REQUEST_ID_LENGTH,
)


MAX_SAFE_REASON_LENGTH = 256
_SECRET_MARKER = re.compile(
    r"(?:authorization\s*:|bearer\s+|cookie\s*:|set-cookie\s*:|"
    r"(?:api[_-]?key|credential|password|secret|signature|token)\s*[=:])",
    re.IGNORECASE,
)


class AcquisitionFailureCode(str, Enum):
    """Closed categories that may cross the acquisition boundary."""

    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_REJECTED = "authentication_rejected"
    POLICY_REJECTED = "policy_rejected"
    ACQUISITION_BLOCKED = "acquisition_blocked"
    BROWSER_POLICY_UNAVAILABLE = "browser_policy_unavailable"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDERS_FAILED = "providers_failed"
    EXTRACTION_FAILED = "extraction_failed"
    SPEND_DENIED = "spend_denied"
    CHARGE_UNCERTAIN = "charge_uncertain"
    PERSISTENCE_FAILED = "persistence_failed"
    OWNERSHIP_DENIED = "ownership_denied"
    RECOVERY_NOT_VERIFIED = "recovery_not_verified"
    DELIVERY_PENDING = "delivery_pending"
    DELIVERY_FAILED = "delivery_failed"


# This shorter name is useful to callers that refer to failure categories.
FailureCategory = AcquisitionFailureCode


def _bounded_text(name: str, value: object, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not value:
        raise ValueError(f"{name} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    if not value.isprintable():
        raise ValueError(f"{name} contains control characters")
    return value


def _references(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError("evidence_references must be a sequence")
    try:
        values = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError("evidence_references must be a sequence") from exc
    if len(values) > MAX_EVIDENCE_REFERENCES:
        raise ValueError(
            f"evidence_references exceeds {MAX_EVIDENCE_REFERENCES} entries"
        )
    normalized: list[str] = []
    for index, reference in enumerate(values):
        normalized.append(
            _bounded_text(
                f"evidence_references[{index}]",
                reference,
                MAX_REFERENCE_LENGTH,
            )
        )
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class AcquisitionFailure:
    """A bounded failure safe to present, persist, and correlate."""

    code: AcquisitionFailureCode | str
    safe_reason: str
    retryable: bool
    request_id: str
    evidence_references: tuple[str, ...] = ()
    before_browser_creation: bool = False

    def __post_init__(self) -> None:
        try:
            code = AcquisitionFailureCode(self.code)
        except (TypeError, ValueError) as exc:
            raise ValueError("code must be a stable AcquisitionFailureCode") from exc
        object.__setattr__(self, "code", code)
        reason = _bounded_text(
            "safe_reason", self.safe_reason, MAX_SAFE_REASON_LENGTH
        )
        if _SECRET_MARKER.search(reason):
            raise ValueError("safe_reason must not contain credential material")
        object.__setattr__(self, "safe_reason", reason)
        if type(self.retryable) is not bool:
            raise TypeError("retryable must be a boolean")
        object.__setattr__(
            self,
            "request_id",
            _bounded_text("request_id", self.request_id, MAX_REQUEST_ID_LENGTH),
        )
        object.__setattr__(
            self,
            "evidence_references",
            _references(self.evidence_references),
        )
        if type(self.before_browser_creation) is not bool:
            raise TypeError("before_browser_creation must be a boolean")
        if code is AcquisitionFailureCode.BROWSER_POLICY_UNAVAILABLE:
            if self.retryable:
                raise ValueError("browser policy failure cannot be retryable")
            if not self.before_browser_creation:
                raise ValueError(
                    "browser policy failure must precede browser creation"
                )

    @classmethod
    def browser_policy_unavailable(
        cls,
        *,
        request_id: str,
        reason: str = "browser connection policy is unavailable",
        evidence_references: tuple[str, ...] = (),
    ) -> "AcquisitionFailure":
        """Build the stable pre-creation browser-policy failure."""

        return cls(
            code=AcquisitionFailureCode.BROWSER_POLICY_UNAVAILABLE,
            safe_reason=reason,
            retryable=False,
            request_id=request_id,
            evidence_references=evidence_references,
            before_browser_creation=True,
        )

    @classmethod
    def acquisition_blocked(
        cls,
        *,
        request_id: str,
        reason: str = "acquisition was blocked by policy",
        evidence_references: tuple[str, ...] = (),
        retryable: bool = False,
    ) -> "AcquisitionFailure":
        """Build the stable no-dispatch acquisition-blocked failure."""

        return cls(
            code=AcquisitionFailureCode.ACQUISITION_BLOCKED,
            safe_reason=reason,
            retryable=retryable,
            request_id=request_id,
            evidence_references=evidence_references,
        )

    @property
    def reason(self) -> str:
        """Compatibility alias for the bounded safe reason."""

        return self.safe_reason

    @property
    def category(self) -> AcquisitionFailureCode:
        """Compatibility alias for the stable failure code."""

        return self.code

    @property
    def retryability(self) -> bool:
        """Compatibility alias for retryable."""

        return self.retryable

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        """Compatibility alias for evidence_references."""

        return self.evidence_references

    def as_dict(self) -> dict[str, object]:
        """Return the bounded caller-facing projection."""

        return {
            "code": self.code.value,
            "safe_reason": self.safe_reason,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "evidence_references": self.evidence_references,
            "before_browser_creation": self.before_browser_creation,
        }
