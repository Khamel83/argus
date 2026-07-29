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


def _exact_facts(facts: Mapping[str, Any], expected: set[str], contract: str) -> bool:
    return set(facts) == expected


def _contract_passed_unchecked(contract: str, facts: Mapping[str, Any]) -> bool:
    """Independently evaluate one exact typed hermetic gate observation."""
    if contract == "authentication":
        return (
            _exact_facts(
                facts, {"http_status", "network_calls_before_rejection"}, contract
            )
            and facts["http_status"] == 401
            and facts["network_calls_before_rejection"] == 0
        )
    if contract == "caller_attribution":
        return (
            _exact_facts(
                facts, {"request_caller_identity", "durable_caller_identity"}, contract
            )
            and facts["request_caller_identity"] == facts["durable_caller_identity"]
            and bool(facts["request_caller_identity"])
        )
    if contract == "surface_equivalence":
        expected = {
            "success": (200, False, 0, False),
            "degraded": (200, False, 0, False),
            "empty": (200, False, 0, False),
            "timeout": (504, True, 1, True),
            "policy_rejected": (403, True, 1, True),
            "authentication_rejected": (401, True, 1, True),
            "providers_failed": (502, True, 1, True),
        }
        if not _exact_facts(facts, {"cases"}, contract):
            return False
        cases = facts["cases"]
        return (
            isinstance(cases, list)
            and {
                case.get("outcome"): (
                    case.get("http_status"),
                    case.get("mcp_is_error"),
                    case.get("cli_exit"),
                    case.get("python_error"),
                )
                for case in cases
                if isinstance(case, Mapping)
            }
            == expected
        )
    if contract == "normalized_result_integrity":
        return (
            _exact_facts(facts, {"case_count", "matched_case_count"}, contract)
            and facts["case_count"] == facts["matched_case_count"]
            and facts["case_count"] > 0
        )
    if contract == "universal_provenance":
        return (
            _exact_facts(
                facts, {"evidence_count", "provenance_complete_count"}, contract
            )
            and facts["evidence_count"] == facts["provenance_complete_count"]
            and facts["evidence_count"] > 0
        )
    if contract == "provider_traces":
        return (
            _exact_facts(facts, {"required_cases", "validated_cases"}, contract)
            and facts["required_cases"] == facts["validated_cases"]
            and set(facts["required_cases"])
            == {"success", "empty", "error", "malformed"}
        )
    if contract == "partial_search":
        return (
            _exact_facts(
                facts, {"successful_observations", "failed_provider_outcome"}, contract
            )
            and facts["successful_observations"] > 0
            and facts["failed_provider_outcome"] == "rate_limited"
        )
    if contract == "empty_search":
        return (
            _exact_facts(facts, {"observation_count", "outcome"}, contract)
            and facts["observation_count"] == 0
            and facts["outcome"] == "empty"
        )
    if contract == "search_evidence_floor":
        return (
            _exact_facts(facts, {"case_count", "matched_case_count"}, contract)
            and facts["case_count"] == facts["matched_case_count"]
            and facts["case_count"] == 24
        )
    if contract in {
        "extraction_success",
        "degraded_extraction",
        "extraction_failure",
    }:
        return (
            _exact_facts(facts, {"required_case_ids", "matched_case_ids"}, contract)
            and facts["required_case_ids"] == facts["matched_case_ids"]
            and bool(facts["required_case_ids"])
        )
    if contract == "durable_acceptance":
        return (
            _exact_facts(
                facts,
                {
                    "operation_outcome",
                    "acceptance_receipt_present",
                    "publication_events",
                    "receipt_matched",
                },
                contract,
            )
            and facts["operation_outcome"] in {"success", "degraded", "empty"}
            and facts["acceptance_receipt_present"] is True
            and facts["publication_events"] == ["durable", "cache"]
            and facts["receipt_matched"] is True
        )
    if contract == "persistence_isolation":
        return _exact_facts(
            facts,
            {
                "production_adapter_rejected_db_config",
                "production_caller_rejected_broker",
                "development_sqlite_scope",
            },
            contract,
        ) and facts == {
            "production_adapter_rejected_db_config": True,
            "production_caller_rejected_broker": True,
            "development_sqlite_scope": "explicit",
        }
    if contract == "provider_readiness":
        return (
            _exact_facts(
                facts, {"provider", "success_failure", "fixture_validated"}, contract
            )
            and facts["provider"] == "duckduckgo"
            and facts["success_failure"] is None
            and facts["fixture_validated"] is True
        )
    if contract == "mode_availability":
        return _exact_facts(facts, {"observed_modes"}, contract) and set(
            facts["observed_modes"]
        ) == {"discovery", "grounding", "recovery", "research"}
    if contract == "policy_truth":
        return (
            _exact_facts(
                facts, {"error_fixture_outcome", "expected_policy_outcome"}, contract
            )
            and facts["error_fixture_outcome"]
            == facts["expected_policy_outcome"]
            == "rate_limited"
        )
    if contract == "cache_eligibility":
        return _exact_facts(
            facts,
            {"fresh_decision", "stale_decision"},
            contract,
        ) and facts == {
            "fresh_decision": "hit_eligible",
            "stale_decision": "hit_ineligible",
        }
    if contract == "cache_isolation":
        return _exact_facts(
            facts,
            {
                "wrong_policy_decision",
                "origin_spend_usd",
                "current_spend_usd",
                "current_provider_calls",
            },
            contract,
        ) and facts == {
            "wrong_policy_decision": "miss",
            "origin_spend_usd": "0.01",
            "current_spend_usd": "0",
            "current_provider_calls": 0,
        }
    if contract == "bounded_completion":
        return (
            _exact_facts(facts, {"completed_within_limit", "limit_ms"}, contract)
            and facts["completed_within_limit"] is True
            and facts["limit_ms"] == 120_000
        )
    if contract == "recovery_authority":
        return _exact_facts(
            facts, {"mode", "authority", "speculative_fallback"}, contract
        ) and facts == {
            "mode": "recovery",
            "authority": "canonical_http",
            "speculative_fallback": False,
        }
    if contract == "evidence_bundle":
        return _exact_facts(
            facts,
            {
                "required_sections",
                "checksum_algorithm",
                "secret_scan",
                "provider_native_payloads",
            },
            contract,
        ) and facts == {
            "required_sections": [
                "manifest",
                "identities",
                "corpus",
                "stability",
                "artifacts",
                "checksums",
            ],
            "checksum_algorithm": "sha256",
            "secret_scan": True,
            "provider_native_payloads": False,
        }
    return False


def _contract_passed(contract: str, facts: Mapping[str, Any]) -> bool:
    try:
        return _contract_passed_unchecked(contract, facts)
    except (KeyError, TypeError, ValueError):
        return False


def load_frozen_stability(
    path: Path,
    *,
    expected_path: Path,
    observed_contracts: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Bind independently executed production-contract observations to gates."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid frozen stability evidence: {exc}") from exc
    if not isinstance(document, Mapping) or set(document) != {"schema", "profiles"}:
        raise ValueError("frozen stability evidence must contain exact keys")
    if document["schema"] != "scorecard-stability-raw-v3":
        raise ValueError("unsupported frozen stability evidence schema")
    try:
        expected_document = json.loads(expected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid frozen stability expectations: {exc}") from exc
    if (
        not isinstance(expected_document, Mapping)
        or set(expected_document) != {"schema", "profiles"}
        or expected_document["schema"] != "scorecard-stability-expected-v1"
    ):
        raise ValueError("frozen stability expectations must contain exact keys")
    profiles = document["profiles"]
    expected_profiles = expected_document["profiles"]
    if not isinstance(profiles, Mapping) or set(profiles) != set(REQUIRED_PROFILES):
        raise ValueError("frozen stability evidence requires both profiles")
    if not isinstance(expected_profiles, Mapping) or set(expected_profiles) != set(
        REQUIRED_PROFILES
    ):
        raise ValueError("frozen stability expectations require both profiles")
    if not set(observed_contracts) <= set(HARD_GATES):
        raise ValueError("typed gate observations contain an unknown hard gate")
    if any(
        not isinstance(observation, Mapping) or not observation
        for observation in observed_contracts.values()
    ):
        raise ValueError("each contract requires a typed gate observation")
    evaluated: dict[str, dict[str, dict[str, Any]]] = {}
    for profile in REQUIRED_PROFILES:
        gates = profiles[profile]
        if not isinstance(gates, Mapping) or set(gates) != set(HARD_GATES):
            raise ValueError(f"{profile} must contain every hard gate exactly once")
        expected_gates = expected_profiles[profile]
        if not isinstance(expected_gates, Mapping) or set(expected_gates) != set(
            HARD_GATES
        ):
            raise ValueError(
                f"{profile} expectations must contain every hard gate exactly once"
            )
        evaluated[profile] = {}
        for gate in HARD_GATES:
            check = gates[gate]
            if not isinstance(check, Mapping) or set(check) != {
                "fixture_id",
                "contract",
                "raw",
            }:
                raise ValueError(f"{profile}.{gate} has invalid fixture shape")
            fixture_id = check["fixture_id"]
            if not isinstance(fixture_id, str) or not fixture_id:
                raise ValueError(f"{profile}.{gate} has invalid fixture id")
            contract = check["contract"]
            if contract != gate:
                raise ValueError(f"{profile}.{gate} has invalid contract binding")
            raw = check["raw"]
            if (
                not isinstance(raw, Mapping)
                or set(raw) != {"schema", "facts"}
                or raw["schema"] != "scorecard-gate-raw-v2"
                or not isinstance(raw["facts"], Mapping)
                or not raw["facts"]
            ):
                raise ValueError(f"{profile}.{gate} has invalid raw evidence")
            expected = expected_gates[gate]
            if (
                not isinstance(expected, Mapping)
                or set(expected) != {"passed"}
                or expected["passed"] is not True
            ):
                raise ValueError(f"{profile}.{gate} has invalid expected evidence")
            raw_passed = _contract_passed(contract, raw["facts"])
            observed_facts = observed_contracts.get(contract)
            if observed_facts is None:
                matched = raw_passed == expected["passed"]
                observation_count = len(raw["facts"])
            else:
                observed_passed = _contract_passed(contract, observed_facts)
                matched = (
                    dict(raw["facts"]) == dict(observed_facts)
                    and raw_passed
                    and observed_passed == expected["passed"]
                )
                observation_count = len(observed_facts)
            evaluated[profile][gate] = {
                "status": "pass" if matched else "fail",
                "reason": (
                    f"{profile}.{gate} independent raw contract passed"
                    if matched
                    else f"{profile}.{gate} independent raw contract failed"
                ),
                "evidence": {
                    "schema": "normalized-gate-evidence-v2",
                    "fixture_id": fixture_id,
                    "check": {
                        "kind": gate,
                        "passed": matched,
                        "observation_count": observation_count,
                    },
                },
            }
    return evaluated
