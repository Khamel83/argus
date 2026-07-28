"""Stable internal contracts shared by Argus transport presenters."""

from .outcomes import (
    AcceptedOperation,
    CanonicalOutcome,
    OperationError,
    http_status_for,
    is_success_like,
    mcp_is_error_for,
)

__all__ = [
    "AcceptedOperation",
    "CanonicalOutcome",
    "OperationError",
    "http_status_for",
    "is_success_like",
    "mcp_is_error_for",
]
