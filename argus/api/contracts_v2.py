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


def _safe_v2_result(value):
    result = _thaw(value)
    if not isinstance(result, dict) or not isinstance(result.get("traces"), list):
        return result
    evidence = result.get("execution_evidence")
    attempts = (
        evidence.get("attempts")
        if isinstance(evidence, dict) and isinstance(evidence.get("attempts"), list)
        else []
    )
    safe_traces = []
    for index, trace in enumerate(result["traces"]):
        if not isinstance(trace, dict):
            continue
        safe = dict(trace)
        attempt = attempts[index] if index < len(attempts) else None
        if isinstance(attempt, dict):
            safe["status"] = attempt.get("status", "unclassified_failure")
            safe["error"] = attempt.get("reason_code")
        else:
            safe["status"] = "unclassified_failure"
            safe["error"] = "unclassified_failure"
        safe_traces.append(safe)
    result["traces"] = safe_traces
    return result


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
                "result": _safe_v2_result(operation.result),
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
