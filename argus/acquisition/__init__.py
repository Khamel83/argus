"""Transport-neutral guarded-acquisition contract.

Concrete DNS, transport, and browser implementations are intentionally kept
outside this first contract module.  Callers depend on the typed values and
protocol below, never on a raw network or browser handle.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .errors import AcquisitionFailure, AcquisitionFailureCode, FailureCategory
from .models import (
    AcquisitionLimits,
    AcquisitionOperation,
    AcquisitionRequest,
    AcquisitionResult,
    BoundedContent,
    BoundedStream,
    CleanupReceipt,
    CredentialPolicy,
    DialAddressEvidence,
    GuardedBrowserSession,
    LogicalOrigin,
    OperationClass,
    OriginProfile,
    RedirectHop,
    RedirectTrace,
    ResourceCounts,
)


@runtime_checkable
class GuardedAcquisition(Protocol):
    """The single transport-neutral acquisition seam.

    Implementations may perform network or browser work behind this protocol.
    The contract itself exposes only bounded values and opaque session identity.
    """

    def acquire(
        self, request: AcquisitionRequest
    ) -> AcquisitionResult | AcquisitionFailure:
        """Acquire bounded content or return a stable failure."""

    def open_browser_session(
        self, request: AcquisitionRequest
    ) -> GuardedBrowserSession | AcquisitionFailure:
        """Open an admitted opaque browser session or return a failure."""


__all__ = [
    "AcquisitionFailure",
    "AcquisitionFailureCode",
    "AcquisitionLimits",
    "AcquisitionOperation",
    "AcquisitionRequest",
    "AcquisitionResult",
    "BoundedContent",
    "BoundedStream",
    "CleanupReceipt",
    "CredentialPolicy",
    "DialAddressEvidence",
    "FailureCategory",
    "GuardedAcquisition",
    "GuardedBrowserSession",
    "LogicalOrigin",
    "OperationClass",
    "OriginProfile",
    "RedirectHop",
    "RedirectTrace",
    "ResourceCounts",
]
