"""Fail-closed stability evaluation over executable frozen evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
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
    evidence: Mapping[str, Any]


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
        return GateVerdict("missing", "required gate evidence is absent", {})
    status = value.get("status")
    evidence = value.get("evidence")
    if status not in {"pass", "fail", "inconclusive"}:
        return GateVerdict("missing", "gate status is missing or invalid", {})
    if not isinstance(evidence, Mapping) or not evidence:
        return GateVerdict("missing", "required gate evidence is absent", {})
    reason = value.get("reason", "passed" if status == "pass" else "gate did not pass")
    if not isinstance(reason, str) or not reason:
        return GateVerdict(
            "missing", "gate reason is missing or invalid", dict(evidence)
        )
    return GateVerdict(str(status), reason, dict(evidence))


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


def load_frozen_stability(
    path: Path,
    *,
    observed_contracts: Mapping[str, bool],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Bind independently executed production-contract observations to gates."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid frozen stability evidence: {exc}") from exc
    if not isinstance(document, Mapping) or set(document) != {"schema", "profiles"}:
        raise ValueError("frozen stability evidence must contain exact keys")
    if document["schema"] != "scorecard-stability-bindings-v2":
        raise ValueError("unsupported frozen stability evidence schema")
    profiles = document["profiles"]
    if not isinstance(profiles, Mapping) or set(profiles) != set(REQUIRED_PROFILES):
        raise ValueError("frozen stability evidence requires both profiles")
    evaluated: dict[str, dict[str, dict[str, Any]]] = {}
    for profile in REQUIRED_PROFILES:
        gates = profiles[profile]
        if not isinstance(gates, Mapping) or set(gates) != set(HARD_GATES):
            raise ValueError(f"{profile} must contain every hard gate exactly once")
        evaluated[profile] = {}
        for gate in HARD_GATES:
            check = gates[gate]
            if not isinstance(check, Mapping) or set(check) != {
                "fixture_id",
                "contract",
            }:
                raise ValueError(f"{profile}.{gate} has invalid fixture shape")
            fixture_id = check["fixture_id"]
            if not isinstance(fixture_id, str) or not fixture_id:
                raise ValueError(f"{profile}.{gate} has invalid fixture id")
            contract = check["contract"]
            if contract != gate or contract not in observed_contracts:
                raise ValueError(f"{profile}.{gate} has invalid contract binding")
            matched = observed_contracts[contract] is True
            evaluated[profile][gate] = {
                "status": "pass" if matched else "fail",
                "reason": (
                    "independently executed production contract passed"
                    if matched
                    else "independently executed production contract failed"
                ),
                "evidence": {
                    "schema": "normalized-gate-evidence-v2",
                    "fixture_id": fixture_id,
                    "check": {
                        "kind": gate,
                        "passed": matched,
                        "observation_count": 1,
                    },
                },
            }
    return evaluated
