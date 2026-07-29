#!/usr/bin/env python3
"""Run diagnostic scorecard lanes without retrieval or execution authority."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from argus.scorecard.architecture import find_architecture_exceptions  # noqa: E402
from argus.scorecard.bundle import derive_generation, write_bundle  # noqa: E402
from argus.scorecard.corpus import load_corpus  # noqa: E402
from argus.scorecard.stability import (  # noqa: E402
    evaluate_stability,
    load_frozen_stability,
)


def _hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _git_identity(repository_root: Path) -> tuple[str, str]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        timestamp = subprocess.check_output(
            ["git", "show", "-s", "--format=%cI", "HEAD"],
            cwd=repository_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("unable to resolve immutable candidate git identity") from exc
    return commit, timestamp


def _live_configuration() -> dict[str, object]:
    """Describe the protected live procedure without performing it."""
    return {
        "schema": "scorecard-live-configuration-v1",
        "execution_authority": "canonical_http",
        "diagnostic_only": True,
        "can_authorize_deployment": False,
        "pr_safe": False,
        "synchronization": [
            "baseline",
            "candidate",
            "topology",
            "profile",
            "provider_snapshot",
        ],
        "free_profile": {
            "automatic": True,
            "billable_provider_calls": False,
            "maximum_tier": 0,
        },
        "budgeted_profile": {
            "automatic": False,
            "authorization_receipt_required_before_reservation": True,
            "receipt_fields": [
                "receipt_id",
                "run_id",
                "generation",
                "permitted_providers",
                "maximum_tier",
                "call_count_cap",
                "cost_or_credit_cap",
                "one_time_credit_providers",
            ],
            "receipt_reuse": "rejected",
            "one_time_credit_providers": "disabled_unless_individually_named",
        },
    }


def _evaluate_corpus(corpus: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    artifacts: dict[str, Any] = {}
    all_matched = True
    for case in corpus["search_intents"]:
        actual = case["hermetic_input"]["normalized_evidence"]
        expected = case["expected_normalized_evidence"]
        matched = actual == expected
        all_matched &= matched
        artifacts[f"searches/{case['id']}.json"] = {
            "schema": "normalized-search-fixture-v1",
            "case_id": case["id"],
            "mode": case["mode"],
            "profile": "hermetic",
            "input": {"query": case["hermetic_input"]["query"]},
            "actual": actual,
            "expected": expected,
            "matched": matched,
        }
    for case in corpus["hermetic_extractions"]:
        actual = case["hermetic_input"]["normalized_evidence"]
        expected = case["expected_normalized_evidence"]
        matched = actual == expected
        all_matched &= matched
        artifacts[f"extractions/{case['id']}.json"] = {
            "schema": "normalized-extraction-fixture-v1",
            "case_id": case["id"],
            "profile": "hermetic",
            "input": {
                "fixture": case["hermetic_input"]["fixture"],
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
    corpus = load_corpus(corpus_path)
    profile_evidence = load_frozen_stability(stability_path)
    artifacts, corpus_matched = _evaluate_corpus(corpus)
    if not corpus_matched:
        raise ValueError(
            "frozen corpus fixture actual evidence does not match expected"
        )
    exceptions = find_architecture_exceptions(repository_root)
    stability = evaluate_stability(
        profile_evidence,
        architecture_exceptions=exceptions,
    )

    provider_snapshot = {
        "schema": "normalized-provider-snapshot-v1",
        "profile": "hermetic",
        "providers": [],
    }
    provider_hash = sha256(
        (
            json.dumps(provider_snapshot, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    ).hexdigest()
    corpus_hashes = {
        "corpus.json": _hash_file(corpus_path),
        "stability-evidence.json": _hash_file(stability_path),
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
    }
    generation = derive_generation(dimensions)
    commit, timestamp = _git_identity(repository_root)
    image_digest = os.environ.get(
        "ARGUS_SCORECARD_IMAGE_DIGEST",
        f"sha256:{sha256(f'hermetic-source:{commit}'.encode()).hexdigest()}",
    )
    payload = {
        "run_id": f"hermetic-{generation[:16]}",
        "generation": generation,
        "candidate_identity": {
            "generation": generation,
            "commit": commit,
            "image_digest": image_digest,
            "sanitized_config_sha256": sha256(
                b"autoload=false;secret-resolution=false;network=none"
            ).hexdigest(),
            "dimensions": dimensions,
            "started_at": timestamp,
            "finished_at": timestamp,
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
            "cases": [
                "success",
                "degraded",
                "empty",
                "timeout",
                "policy_rejected",
                "authentication_rejected",
                "providers_failed",
            ],
        },
        "timing_receipts": {
            "schema": "normalized-timing-receipts-v1",
            "operations": [
                {
                    "operation_id": "frozen-fixture-evaluation",
                    "wall_ms": 0,
                    "component_ms": 0,
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
                "provider_execution": "none",
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
            configuration = _live_configuration()
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
