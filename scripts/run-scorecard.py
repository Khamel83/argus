#!/usr/bin/env python3
"""Run diagnostic scorecard lanes without retrieval or execution authority."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter_ns
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from argus.scorecard.architecture import find_architecture_exceptions  # noqa: E402
from argus.scorecard.bundle import derive_generation, write_bundle  # noqa: E402
from argus.scorecard.competitive import classify_pair  # noqa: E402
from argus.scorecard.corpus import load_corpus  # noqa: E402
from argus.scorecard.stability import (  # noqa: E402
    HARD_GATES,
    evaluate_stability,
    load_frozen_stability,
)
from argus.hermetic_scorecard import (  # noqa: E402
    execute_authority_gate_contracts,
    execute_extraction_fixture,
    execute_search_fixture,
    execute_surface_fixture,
    load_expected_observations,
)
from argus.models import ProviderName  # noqa: E402
from argus.providers.fixture_harness import run_fixture_case_summaries  # noqa: E402


def _hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _git_identity(repository_root: Path) -> str:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("unable to resolve immutable candidate git identity") from exc
    return commit


def _live_configuration(corpus: dict[str, Any]) -> dict[str, object]:
    """Describe the protected live procedure without performing it."""
    return {
        "schema": "scorecard-live-configuration-v2",
        "owner": "Task 16/P1",
        "execution": "not_performed_by_task_15",
        "diagnostic_only": True,
        "can_authorize_deployment": False,
        "pr_safe": False,
        "cases": [
            *(
                {"case_id": case["id"], "mode": case["mode"]}
                for case in corpus["search_intents"]
            ),
            *(
                {"case_id": case["id"], "mode": "extraction"}
                for case in corpus["live_extractions"]
            ),
        ],
        "synchronized_identity_requirements": {
            "maximum_window_seconds": 900,
            "fields": [
                "baseline_commit",
                "candidate_commit",
                "baseline_image_digest",
                "candidate_image_digest",
                "sanitized_config_sha256",
                "corpus_hashes",
                "topology",
                "profile",
                "provider_snapshot_sha256",
            ],
        },
        "evaluator": {
            "pinned_model_required": True,
            "pinned_prompt_sha256_required": True,
            "pinned_settings_sha256_required": True,
            "orders": ["forward", "reverse"],
        },
        "free_profile": {
            "profile_id": "scheduled-free",
            "automatic": True,
            "billable_provider_calls": False,
            "maximum_tier": 0,
            "consumer": "Task 16/P1",
        },
        "budgeted_profile": {
            "profile_id": "scorecard-budgeted",
            "automatic": False,
            "immutable_authorization_required": True,
            "receipt_fields": [
                "schema",
                "receipt_id",
                "run_id",
                "generation",
                "permitted_providers",
                "maximum_tier",
                "call_count_cap",
                "cost_or_credit_cap",
                "one_time_credit_providers",
                "issued_at",
            ],
            "receipt_reuse": "rejected",
            "one_time_credit_providers": "disabled_unless_individually_named",
            "consumer": "Task 16/P1",
        },
    }


def _evaluate_corpus(
    corpus: dict[str, Any],
    expected_observations: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    artifacts: dict[str, Any] = {}
    all_matched = True
    for case in corpus["search_intents"]:
        actual = execute_search_fixture(case["hermetic_input"])
        expected = expected_observations["searches"][case["id"]]
        matched = actual == expected
        all_matched &= matched
        artifacts[f"searches/{case['id']}.json"] = {
            "schema": "normalized-search-fixture-v1",
            "case_id": case["id"],
            "mode": case["mode"],
            "profile": "hermetic",
            "input": {
                "query": case["hermetic_input"]["query"],
                "raw_fixture_id": case["id"],
            },
            "actual": actual,
            "expected": expected,
            "matched": matched,
        }
    for case in corpus["hermetic_extractions"]:
        actual = execute_extraction_fixture(case["hermetic_input"])
        expected = expected_observations["extractions"][case["id"]]
        matched = actual == expected
        all_matched &= matched
        artifacts[f"extractions/{case['id']}.json"] = {
            "schema": "normalized-extraction-fixture-v1",
            "case_id": case["id"],
            "profile": "hermetic",
            "input": {
                "raw_fixture_id": case["hermetic_input"]["fixture"],
                "content_type": case["hermetic_input"]["content_type"],
            },
            "actual": actual,
            "expected": expected,
            "matched": matched,
        }
    return artifacts, all_matched


def run_hermetic(
    output: Path,
    *,
    fixtures_root: Path,
    repository_root: Path,
) -> tuple[Path, str]:
    """Evaluate only frozen documents and publish the diagnostic bundle."""
    corpus_path = fixtures_root / "corpus.json"
    stability_path = fixtures_root / "stability-evidence.json"
    stability_expected_path = fixtures_root / "stability-expected.json"
    expected_path = fixtures_root / "hermetic-expected.json"
    started_at = datetime.now(timezone.utc)
    timer_started = perf_counter_ns()
    corpus = load_corpus(corpus_path)
    expected_observations = load_expected_observations(expected_path)
    if set(expected_observations["searches"]) != {
        case["id"] for case in corpus["search_intents"]
    } or set(expected_observations["extractions"]) != {
        case["id"] for case in corpus["hermetic_extractions"]
    }:
        raise ValueError("independent expected observations are not corpus-closed")
    provider_contracts = run_fixture_case_summaries(ProviderName.DUCKDUCKGO)
    if not all(
        provider_contracts[name]["golden_output_validated"]
        for name in ("success", "empty", "error", "malformed")
    ):
        raise ValueError("raw provider fixture contract failed")
    evaluator_expectations = {
        "win.json": "candidate_win",
        "loss.json": "baseline_win",
        "tie.json": "tie",
        "conflict.json": "ordering_conflict",
        "catastrophic.json": "catastrophic_regression",
        "malformed.json": "malformed",
        "unavailable.json": "unavailable",
    }
    for name, expected in evaluator_expectations.items():
        raw_pair = json.loads((fixtures_root / "evaluator" / name).read_text())
        raw_pair.update(pair_id="discovery-01", mode="discovery")
        if classify_pair(raw_pair).classification != expected:
            raise ValueError(f"raw evaluator fixture failed: {name}")
    artifacts, corpus_matched = _evaluate_corpus(corpus, expected_observations)
    if not corpus_matched:
        raise ValueError(
            "raw corpus execution does not match expected normalized evidence"
        )
    exceptions = find_architecture_exceptions(repository_root)
    surface_inputs = [
        {"outcome": outcome, "code": None}
        for outcome in (
            "success",
            "degraded",
            "empty",
            "timeout",
            "policy_rejected",
            "authentication_rejected",
            "providers_failed",
        )
    ]
    surface_cases = [
        {"case_id": f"surface-{index:02d}", **execute_surface_fixture(raw)}
        for index, raw in enumerate(surface_inputs, 1)
    ]
    search_artifacts = [
        artifact
        for relative, artifact in artifacts.items()
        if relative.startswith("searches/")
    ]
    extraction_artifacts = {
        artifact["case_id"]: artifact
        for relative, artifact in artifacts.items()
        if relative.startswith("extractions/")
    }
    provider_cases = ("success", "empty", "error", "malformed")
    authority_contracts = execute_authority_gate_contracts()
    provenance_artifacts = [
        artifact for artifact in artifacts.values() if "actual" in artifact
    ]
    observed_contracts = {
        "authentication": authority_contracts["authentication"],
        "caller_attribution": authority_contracts["caller_attribution"],
        "surface_equivalence": {"cases": surface_cases},
        "normalized_result_integrity": {
            "case_count": len(search_artifacts),
            "matched_case_count": sum(
                artifact["matched"] is True for artifact in search_artifacts
            ),
        },
        "universal_provenance": {
            "evidence_count": len(provenance_artifacts),
            "provenance_complete_count": sum(
                artifact["actual"]["provenance_complete"] is True
                for artifact in provenance_artifacts
            ),
        },
        "provider_traces": {
            "required_cases": list(provider_cases),
            "validated_cases": [
                name
                for name in provider_cases
                if provider_contracts[name]["golden_output_validated"]
            ],
        },
        "partial_search": {
            "successful_observations": provider_contracts["success"]["observations"],
            "failed_provider_outcome": provider_contracts["error"]["failure"],
        },
        "empty_search": {
            "observation_count": provider_contracts["empty"]["observations"],
            "outcome": provider_contracts["empty"]["failure"],
        },
        "search_evidence_floor": {
            "case_count": len(search_artifacts),
            "matched_case_count": sum(
                artifact["matched"] is True for artifact in search_artifacts
            ),
        },
        "extraction_success": {
            "required_case_ids": ["static"],
            "matched_case_ids": (
                ["static"] if extraction_artifacts["static"]["matched"] else []
            ),
        },
        "degraded_extraction": {
            "required_case_ids": ["paywall"],
            "matched_case_ids": (
                ["paywall"] if extraction_artifacts["paywall"]["matched"] else []
            ),
        },
        "extraction_failure": {
            "required_case_ids": ["malformed", "timeout", "unsupported"],
            "matched_case_ids": [
                case_id
                for case_id in ("malformed", "timeout", "unsupported")
                if extraction_artifacts[case_id]["matched"]
            ],
        },
        "durable_acceptance": authority_contracts["durable_acceptance"],
        "persistence_isolation": authority_contracts["persistence_isolation"],
        "provider_readiness": {
            "provider": "duckduckgo",
            "success_failure": provider_contracts["success"]["failure"],
            "fixture_validated": provider_contracts["success"][
                "golden_output_validated"
            ],
        },
        "mode_availability": {
            "observed_modes": sorted(
                {artifact["mode"] for artifact in search_artifacts}
            )
        },
        "policy_truth": {
            "error_fixture_outcome": provider_contracts["error"]["failure"],
            "expected_policy_outcome": "rate_limited",
        },
        "cache_eligibility": authority_contracts["cache_eligibility"],
        "cache_isolation": authority_contracts["cache_isolation"],
        "bounded_completion": {
            "completed_within_limit": (perf_counter_ns() - timer_started)
            <= 120_000_000_000,
            "limit_ms": 120_000,
        },
    }
    if set(observed_contracts) | {"recovery_authority", "evidence_bundle"} != set(
        HARD_GATES
    ):
        raise ValueError("hermetic gate observations are not closed")
    profile_evidence = load_frozen_stability(
        stability_path,
        expected_path=stability_expected_path,
        observed_contracts=observed_contracts,
    )
    stability = evaluate_stability(
        profile_evidence,
        architecture_exceptions=exceptions,
    )

    provider_snapshot = {
        "schema": "normalized-provider-snapshot-v1",
        "profile": "hermetic",
        "providers": [
            {
                "provider": "duckduckgo",
                "tier": 0,
                "fixture_contract_version": provider_contracts["success"][
                    "provider_contract_version"
                ],
                "status": "fixture_verified",
            }
        ],
    }
    provider_hash = sha256(
        (
            json.dumps(provider_snapshot, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    ).hexdigest()
    corpus_hashes = {
        "corpus.json": _hash_file(corpus_path),
        "stability-evidence.json": _hash_file(stability_path),
        "stability-expected.json": _hash_file(stability_expected_path),
        "hermetic-expected.json": _hash_file(expected_path),
        **{
            f"evaluator/{path.name}": _hash_file(path)
            for path in sorted((fixtures_root / "evaluator").glob("*.json"))
        },
    }
    evaluator_hash = sha256(
        "".join(
            corpus_hashes[path]
            for path in sorted(corpus_hashes)
            if path.startswith("evaluator/")
        ).encode()
    ).hexdigest()
    sanitized_config_sha256 = sha256(
        b"autoload=false;secret-resolution=false;network=none"
    ).hexdigest()
    dimensions = {
        "corpus_hashes": corpus_hashes,
        "evaluator": {
            "model": "frozen-paired-evaluator-fixtures-v1",
            "prompt_sha256": evaluator_hash,
            "settings_sha256": sha256(b"deterministic:no-network:v1").hexdigest(),
        },
        "topology": {"egress": "hermetic", "machine": "local-fixture"},
        "profile": "hermetic",
        "provider_snapshot_sha256": provider_hash,
        "sanitized_config_sha256": sanitized_config_sha256,
    }
    generation = derive_generation(dimensions)
    commit = _git_identity(repository_root)
    elapsed_ms = max(1, (perf_counter_ns() - timer_started) // 1_000_000)
    finished_at = datetime.now(timezone.utc)
    payload = {
        "run_id": f"hermetic-{generation[:16]}",
        "generation": generation,
        "candidate_identity": {
            "generation": generation,
            "commit": commit,
            "image_digest": None,
            "sanitized_config_sha256": sanitized_config_sha256,
            "dimensions": dimensions,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        },
        "corpus": {
            "version": corpus["version"],
            "hashes": corpus_hashes,
            "hermetic_search_case_ids": [
                case["id"] for case in corpus["search_intents"]
            ],
            "hermetic_extraction_case_ids": [
                case["id"] for case in corpus["hermetic_extractions"]
            ],
            "competitive_case_ids": [
                *(case["id"] for case in corpus["search_intents"]),
                *(case["id"] for case in corpus["live_extractions"]),
            ],
            "dimensions": dimensions,
            "generation": generation,
        },
        "provider_snapshot": provider_snapshot,
        "surface_equivalence": {
            "schema": "surface-equivalence-v1",
            "status": profile_evidence["free"]["surface_equivalence"]["status"],
            "cases": surface_cases,
        },
        "timing_receipts": {
            "schema": "normalized-timing-receipts-v1",
            "operations": [
                {
                    "operation_id": "frozen-fixture-evaluation",
                    "wall_ms": elapsed_ms,
                    "component_ms": elapsed_ms,
                    "timeout_source": "none",
                    "cache_ms": 0,
                }
            ],
        },
        "persistence_receipts": {
            "schema": "normalized-persistence-receipts-v1",
            "status": "not_applicable",
            "reason": "hermetic evaluation performs no persistence I/O",
            "receipts": [],
        },
        "artifacts": {
            "hermetic-summary.json": {
                "schema": "hermetic-summary-v1",
                "search_cases": len(corpus["search_intents"]),
                "extraction_cases": len(corpus["hermetic_extractions"]),
                "provider_execution": "hermetic_adapter_contract",
                "stability_verdict": stability.verdict,
            },
            **artifacts,
        },
    }
    return (
        write_bundle(output, lane="hermetic", stability=stability, payload=payload),
        stability.verdict,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("hermetic", "live-config"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--fixtures-root",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "scorecard",
    )
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        if args.lane == "live-config":
            configuration = _live_configuration(
                load_corpus(args.fixtures_root / "corpus.json")
            )
            encoded = json.dumps(configuration, sort_keys=True, indent=2) + "\n"
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
            print(
                f"wrote live competitive configuration to {args.output}; "
                "no live execution performed"
            )
            return 0
        output, verdict = run_hermetic(
            args.output,
            fixtures_root=args.fixtures_root,
            repository_root=args.repository_root,
        )
    except (OSError, ValueError) as exc:
        print(f"scorecard fixture/configuration error: {exc}", file=sys.stderr)
        return 2
    print(f"hermetic stability verdict: {verdict}; verified bundle: {output}")
    return 0 if verdict == "stable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
