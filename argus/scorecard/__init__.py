"""Deterministic, diagnostic-only retrieval scorecard tooling.

The scorecard consumes frozen evidence; it never executes retrieval, reserves
provider budget, or authorizes a runtime/deployment action.
"""

from .bundle import BundleError, verify_bundle, write_bundle
from .competitive import CompetitiveVerdict, evaluate_competitive
from .corpus import load_corpus, validate_corpus
from .stability import StabilityVerdict, evaluate_stability

__all__ = (
    "BundleError",
    "CompetitiveVerdict",
    "StabilityVerdict",
    "evaluate_competitive",
    "evaluate_stability",
    "load_corpus",
    "validate_corpus",
    "verify_bundle",
    "write_bundle",
)
