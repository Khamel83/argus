"""Exact transport-neutral validation for the HTTP-v2 wire envelope."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .outcomes import (
    AcceptedOperation,
    CanonicalOutcome,
    OperationError,
    is_success_like,
)

_OUTCOMES = Literal[
    "success",
    "degraded",
    "empty",
    "invalid_request",
    "authentication_rejected",
    "policy_rejected",
    "timeout",
    "persistence_failed",
    "providers_failed",
    "extraction_failed",
    "unready",
]


class V2Problem(BaseModel):
    """Exact public problem member of the HTTP-v2 envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    retryable: bool
    retry_after_seconds: int | None


class V2Envelope(BaseModel):
    """Exact stable outer envelope shared by HTTP and MCP v2."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: Literal["2.0"]
    outcome: _OUTCOMES
    request_id: str
    result: dict[str, Any] | None
    error: V2Problem | None

    @model_validator(mode="after")
    def validate_contract_invariants(self):
        outcome = CanonicalOutcome(self.outcome)
        error = (
            None
            if self.error is None
            else OperationError(
                outcome=outcome,
                operation_began=False,
                **self.error.model_dump(),
            )
        )
        AcceptedOperation(
            contract_version=self.contract_version,
            outcome=outcome,
            request_id=self.request_id,
            result=self.result,
            error=error,
            operation_began=False,
        )
        return self


def validate_v2_envelope(
    envelope: object,
    *,
    http_status: int | None = None,
) -> V2Envelope:
    """Validate one exact envelope and, when known, its actual HTTP status."""

    validated = V2Envelope.model_validate(envelope)
    if http_status is None:
        return validated
    if isinstance(http_status, bool) or not isinstance(http_status, int):
        raise ValueError("HTTP status must be an integer")
    outcome = CanonicalOutcome(validated.outcome)
    expected_status = (
        200
        if is_success_like(outcome)
        else validated.error.status
        if validated.error is not None
        else None
    )
    if http_status != expected_status:
        raise ValueError("HTTP status does not match the v2 envelope outcome")
    return validated
