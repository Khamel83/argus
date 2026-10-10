"""Deterministic scorecard evaluation and guarded workload admission.

The evaluator consumes frozen evidence.  The coordinator admits an external
workload only after fail-closed host-capacity checks; it does not perform
retrieval or authorize a runtime/deployment action.
"""

from .bundle import BundleError, verify_bundle, write_bundle
from .competitive import CompetitiveVerdict, evaluate_competitive
from .corpus import load_corpus, validate_corpus
from .stability import StabilityVerdict, evaluate_stability
from .admission import (
    AdmissionDecision,
    CapacityLimits,
    CapacitySnapshot,
    ScorecardAdmissionPolicy,
    SystemCapacityProbe,
)
from .coordinator import AdmittedWorkload, ScorecardCoordinator

__all__ = (
    "AdmittedWorkload",
    "AdmissionDecision",
    "BundleError",
    "CapacityLimits",
    "CapacitySnapshot",
    "CompetitiveVerdict",
    "ScorecardAdmissionPolicy",
    "ScorecardCoordinator",
    "StabilityVerdict",
    "SystemCapacityProbe",
    "evaluate_competitive",
    "evaluate_stability",
    "load_corpus",
    "validate_corpus",
    "verify_bundle",
    "write_bundle",
)
