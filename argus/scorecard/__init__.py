"""Retrieval scorecard tooling.

The evaluation paths consume frozen evidence; they never execute retrieval or
reserve provider budget.  Live scorecard workload launches are gated by a
host-wide admission coordinator so baseline and candidate runs share one
bounded Docker/network budget.
"""

from .bundle import BundleError, verify_bundle, write_bundle
from .competitive import CompetitiveVerdict, evaluate_competitive
from .corpus import load_corpus, validate_corpus
from .stability import StabilityVerdict, evaluate_stability
from .admission import (
    AdmissionResult,
    AdmissionState,
    ScorecardAdmissionCoordinator,
    ScorecardAdmissionLease,
    configured_scorecard_active_workload_limit,
)

__all__ = (
    "AdmissionResult",
    "AdmissionState",
    "BundleError",
    "CompetitiveVerdict",
    "ScorecardAdmissionCoordinator",
    "ScorecardAdmissionLease",
    "StabilityVerdict",
    "configured_scorecard_active_workload_limit",
    "evaluate_competitive",
    "evaluate_stability",
    "load_corpus",
    "validate_corpus",
    "verify_bundle",
    "write_bundle",
)
