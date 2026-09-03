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
from .failures import (
    FailureCode,
    FailureRecord,
    FailureSpec,
    failure_codes,
    failure_spec,
    operation_error_for,
)
from .identity import EvidenceIdentity, ReleaseIdentity, SchemaIdentity

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
    "EvidenceIdentity",
    "FailureCode",
    "FailureRecord",
    "FailureSpec",
    "ReleaseIdentity",
    "SchemaIdentity",
    "failure_codes",
    "failure_spec",
    "operation_error_for",
]
