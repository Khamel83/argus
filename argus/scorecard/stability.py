"""Fail-closed stability evaluation over supplied evidence only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


HARD_GATES = (
    "authentication",
    "caller_attribution",
    "surface_equivalence",
    "normalized_result_integrity",
    "universal_provenance",
    "provider_traces",
    "partial_search",
    "empty_search",
    "search_evidence_floor",
    "extraction_success",
    "degraded_extraction",
    "extraction_failure",
    "durable_acceptance",
    "persistence_isolation",
    "provider_readiness",
    "mode_availability",
    "policy_truth",
    "cache_eligibility",
    "cache_isolation",
    "bounded_completion",
    "recovery_authority",
    "evidence_bundle",
)
REQUIRED_PROFILES = ("free", "budgeted")


@dataclass(frozen=True)
class GateVerdict:
    status: str
    reason: str


@dataclass(frozen=True)
class ProfileVerdict:
    verdict: str
    gates: Mapping[str, GateVerdict]


@dataclass(frozen=True)
class StabilityVerdict:
    verdict: str
    profiles: Mapping[str, ProfileVerdict]
    architecture_exceptions: tuple[str, ...]


def _gate_verdict(value: object) -> GateVerdict:
    if not isinstance(value, Mapping):
        return GateVerdict("missing", "required gate evidence is absent")
    status = value.get("status")
    evidence = value.get("evidence")
    if status not in {"pass", "fail", "inconclusive"}:
        return GateVerdict("missing", "gate status is missing or invalid")
    if not isinstance(evidence, Mapping) or not evidence:
        return GateVerdict("missing", "required gate evidence is absent")
    if status != "pass":
        return GateVerdict(str(status), str(value.get("reason", "gate did not pass")))
    return GateVerdict("pass", str(value.get("reason", "passed")))


def evaluate_stability(
    profile_evidence: Mapping[str, Mapping[str, Any]],
    *,
    architecture_exceptions: tuple[str, ...] | list[str],
) -> StabilityVerdict:
    """Evaluate every hard gate independently; no profile can offset another."""
    profiles: dict[str, ProfileVerdict] = {}
    for profile in REQUIRED_PROFILES:
        supplied = profile_evidence.get(profile, {})
        gates = {gate: _gate_verdict(supplied.get(gate)) for gate in HARD_GATES}
        profile_verdict = (
            "stable"
            if all(gate.status == "pass" for gate in gates.values())
            else "unstable"
        )
        profiles[profile] = ProfileVerdict(profile_verdict, gates)
    exceptions = tuple(architecture_exceptions)
    overall = (
        "stable"
        if not exceptions
        and all(profile.verdict == "stable" for profile in profiles.values())
        else "unstable"
    )
    return StabilityVerdict(overall, profiles, exceptions)
