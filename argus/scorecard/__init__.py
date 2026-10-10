"""Deterministic scorecard tooling and bounded execution admission.

The scorecard compiler consumes frozen evidence; live execution callers use the
queue module to admit complete workloads before initializing ephemeral
resources.
"""

from .bundle import BundleError, verify_bundle, write_bundle
from .competitive import CompetitiveVerdict, evaluate_competitive
from .corpus import load_corpus, validate_corpus
from .queue import (
    AdmissionExpired,
    AdmissionLease,
    CapacityDenied,
    QueueClosed,
    QueueFull,
    QueueItemStatus,
    QueueStatus,
    RetryLimitExceeded,
    RetryPolicy,
    ScorecardExecutionQueue,
    ScorecardJob,
)
from .stability import StabilityVerdict, evaluate_stability

__all__ = (
    "AdmissionExpired",
    "AdmissionLease",
    "BundleError",
    "CapacityDenied",
    "CompetitiveVerdict",
    "QueueClosed",
    "QueueFull",
    "QueueItemStatus",
    "QueueStatus",
    "RetryLimitExceeded",
    "RetryPolicy",
    "ScorecardExecutionQueue",
    "ScorecardJob",
    "StabilityVerdict",
    "evaluate_competitive",
    "evaluate_stability",
    "load_corpus",
    "validate_corpus",
    "verify_bundle",
    "write_bundle",
)
