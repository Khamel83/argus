"""Exact version-two HTTP envelope presentation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict

from fastapi.responses import JSONResponse

from argus.contracts import (
    AcceptedOperation,
    CanonicalOutcome,
    OperationError,
    http_status_for,
)


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


class EvidenceHttpPresenter:
    """Render accepted facts without classifying or executing them."""

    def response(
        self,
        operation: AcceptedOperation,
        *,
        status_override: int | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> JSONResponse:
        error = operation.error
        status = (
            status_override
            if status_override is not None
            else 200
            if error is None
            else error.status
        )
        headers = {
            "Argus-Contract-Version": "2.0",
            "X-Request-ID": operation.request_id,
        }
        if error is not None:
            if operation.outcome is CanonicalOutcome.AUTHENTICATION_REJECTED:
                headers["WWW-Authenticate"] = "Bearer"
            if error.retry_after_seconds is not None:
                headers["Retry-After"] = str(error.retry_after_seconds)
        if extra_headers:
            headers.update(extra_headers)
        return JSONResponse(
            status_code=status,
            headers=headers,
            content={
                "contract_version": operation.contract_version,
                "outcome": operation.outcome.value,
                "request_id": operation.request_id,
                "result": _thaw(operation.result),
                "error": None if error is None else asdict(error),
            },
        )


def admission_operation(
    *,
    outcome: CanonicalOutcome,
    request_id: str,
    detail: str,
    code: str | None = None,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
) -> AcceptedOperation:
    """Build a bounded pre-execution failure with no rejected-value echo."""

    error_code = code or outcome.value
    error = OperationError(
        outcome=outcome,
        operation_began=False,
        type=f"urn:argus:problem:{error_code}",
        title=error_code.replace("_", " ").title(),
        status=http_status_for(outcome, error_code),
        detail=detail,
        instance=f"urn:argus:request:{request_id}",
        code=error_code,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
    )
    return AcceptedOperation(
        outcome=outcome,
        request_id=request_id,
        result=None,
        error=error,
        operation_began=False,
    )
