"""Stable internal contracts shared by Argus transport presenters."""

from .outcomes import (
    AcceptedOperation,
    CanonicalOutcome,
    OperationError,
    http_status_for,
    is_success_like,
    mcp_is_error_for,
)
from .v2 import V2Envelope, V2Problem, validate_v2_envelope

__all__ = [
    "AcceptedOperation",
    "CanonicalOutcome",
    "OperationError",
    "http_status_for",
    "is_success_like",
    "mcp_is_error_for",
    "V2Envelope",
    "V2Problem",
    "validate_v2_envelope",
]
