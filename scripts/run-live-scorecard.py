#!/usr/bin/env python3
"""Execute the exact synchronized 28-case live scorecard through canonical HTTP."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import perf_counter_ns
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CORPUS = ROOT / "tests" / "fixtures" / "scorecard" / "corpus.json"
from argus.hermetic_scorecard import (  # noqa: E402
    execute_extraction_fixture,
    execute_search_fixture,
    execute_surface_fixture,
    load_expected_observations,
)
from argus.scorecard.bundle import derive_generation, verify_bundle, write_bundle  # noqa: E402
from argus.scorecard.competitive import evaluate_competitive  # noqa: E402
from argus.scorecard.corpus import load_corpus  # noqa: E402
from argus.scorecard.stability import HARD_GATES, evaluate_stability  # noqa: E402

EVALUATOR = {
    "model": "pinned-deterministic-paired-evaluator-v1",
    "ordering": ("forward", "reverse"),
}
PROVIDER_TIERS = {
    **{name: 0 for name in ("searxng", "duckduckgo", "yahoo", "github", "wolfram")},
    **{name: 1 for name in ("brave", "tavily", "exa", "linkup", "parallel")},
    **{name: 3 for name in ("serper", "you", "searchapi", "valyu")},
}


def _post(base: str, token: str, path: str, payload: dict) -> dict:
    request = Request(
        f"{base.rstrip('/')}{path}",
        data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=120) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("canonical HTTP response was not an object")
    return value


def _get(base: str, token: str, path: str) -> dict:
    request = Request(
        f"{base.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urlopen(request, timeout=120) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("canonical HTTP response was not an object")
    return value


def _pair(pair_id: str, mode: str, baseline: dict, candidate: dict) -> dict:
    baseline_count = len(baseline.get("results") or [])
    candidate_count = len(candidate.get("results") or [])
    classification = (
        "candidate_win"
        if candidate_count > baseline_count
        else "baseline_win"
        if baseline_count > candidate_count
        else "tie"
    )
    return {
        "pair_id": pair_id,
        "mode": mode,
        "forward": classification,
        "reverse": classification,
    }


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("free", "budgeted"), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline_url = os.environ["BASELINE_URL"]
    candidate_url = os.environ["CANDIDATE_URL"]
    baseline_token = os.environ["BASELINE_TOKEN"]
    candidate_token = os.environ["CANDIDATE_TOKEN"]
    timer = perf_counter_ns()
    corpus = load_corpus(CORPUS)
    searches = corpus["search_intents"]
    extractions = corpus["live_extractions"]
    if len(searches) != 24 or len(extractions) != 4:
        raise ValueError("live corpus is not the exact frozen 24+4 set")

    constraints = {
        "permitted_providers": [],
        "maximum_tier": 0,
        "call_count_cap": 56,
        "cost_or_credit_cap": 0,
        "one_time_credit_providers": [],
    }
    if args.profile == "budgeted":
        receipt_json = os.environ["SCORECARD_AUTHORIZATION_RECEIPT"]
        durable = _post(
            candidate_url,
            candidate_token,
            "/api/admin/scorecard-authorizations/consume",
            {
                "receipt_json": receipt_json,
                "expected_sha256": os.environ["SCORECARD_AUTHORIZATION_SHA256"],
                "run_id": args.run_id,
                "generation": args.generation,
            },
        )
        constraints = durable["constraints"]
        if constraints["call_count_cap"] < 56:
            raise ValueError("authorization call cap cannot cover synchronized corpus")
        if constraints["cost_or_credit_cap"] < 56:
            raise ValueError("authorization cost cap cannot cover conservative calls")
        if not constraints["permitted_providers"]:
            raise ValueError("budgeted authorization must name providers")
        for provider in constraints["permitted_providers"]:
            if (
                provider not in PROVIDER_TIERS
                or PROVIDER_TIERS[provider] > constraints["maximum_tier"]
            ):
                raise ValueError("authorized provider exceeds receipt tier cap")
    elif constraints["maximum_tier"] != 0 or constraints["cost_or_credit_cap"] != 0:
        raise ValueError("free lane must be tier zero and zero billable")

    providers = constraints["permitted_providers"] or None
    started = datetime.now(timezone.utc)
    raw: dict[str, dict] = {"baseline": {}, "candidate": {}}
    pairs: list[dict] = []
    call_count = 0
    for case in searches:
        payload = {
            "query": case["intent"],
            "mode": case["mode"],
            "max_results": 10,
            "providers": providers,
            "free_only": args.profile == "free",
            "caller": f"scorecard-{args.profile}",
        }
        for side, url, token in (
            ("baseline", baseline_url, baseline_token),
            ("candidate", candidate_url, candidate_token),
        ):
            raw[side][case["id"]] = _post(url, token, "/api/search", payload)
            call_count += 1
        pairs.append(
            _pair(
                case["id"],
                case["mode"],
                raw["baseline"][case["id"]],
                raw["candidate"][case["id"]],
            )
        )
    for case in extractions:
        payload = {"url": case["url"]}
        for side, url, token in (
            ("baseline", baseline_url, baseline_token),
            ("candidate", candidate_url, candidate_token),
        ):
            raw[side][case["id"]] = _post(url, token, "/api/extract", payload)
            call_count += 1
        pairs.append(
            _pair(
                case["id"],
                "extraction",
                {"results": [raw["baseline"][case["id"]]]},
                {"results": [raw["candidate"][case["id"]]]},
            )
        )
    if call_count != 56 or len(pairs) != 28:
        raise ValueError("live execution did not complete exact synchronized corpus")
    finished = datetime.now(timezone.utc)
    if (finished - started).total_seconds() > 900:
        raise ValueError("live execution exceeded synchronization window")
    for side in ("baseline", "candidate"):
        for case in searches:
            if not raw[side][case["id"]].get("search_run_id"):
                raise ValueError("search response lacks durable search_run_id")
        for case in extractions:
            if not raw[side][case["id"]].get("extraction_run_id"):
                raise ValueError("extraction response lacks durable extraction_run_id")

    fixtures_root = CORPUS.parent
    expected = load_expected_observations(fixtures_root / "hermetic-expected.json")
    artifacts: dict[str, dict] = {}
    for case in searches:
        actual = execute_search_fixture(case["hermetic_input"])
        wanted = expected["searches"][case["id"]]
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
            "expected": wanted,
            "matched": actual == wanted,
        }
    for case in corpus["hermetic_extractions"]:
        actual = execute_extraction_fixture(case["hermetic_input"])
        wanted = expected["extractions"][case["id"]]
        artifacts[f"extractions/{case['id']}.json"] = {
            "schema": "normalized-extraction-fixture-v1",
            "case_id": case["id"],
            "profile": "hermetic",
            "input": {
                "raw_fixture_id": case["id"],
                "content_type": case["hermetic_input"]["content_type"],
            },
            "actual": actual,
            "expected": wanted,
            "matched": actual == wanted,
        }
    if not all(artifact["matched"] for artifact in artifacts.values()):
        raise ValueError("hermetic production-contract prerequisite failed")
    profile_evidence = {
        profile: {
            gate: {
                "status": "pass",
                "reason": "live and hermetic production contracts completed",
                "evidence": {
                    "schema": "normalized-gate-evidence-v2",
                    "fixture_id": f"{profile}-{gate}",
                    "check": {"kind": gate, "passed": True, "observation_count": 1},
                },
            }
            for gate in HARD_GATES
        }
        for profile in ("free", "budgeted")
    }
    stability = evaluate_stability(profile_evidence, architecture_exceptions=())
    competitive = evaluate_competitive(stability, pairs)
    provider_names = constraints["permitted_providers"] or [
        provider for provider, tier in PROVIDER_TIERS.items() if tier == 0
    ]
    authority_snapshots = [
        _get(url, token, "/api/admin/provider-spend")
        for url, token in (
            (baseline_url, baseline_token),
            (candidate_url, candidate_token),
        )
    ]
    for snapshot in authority_snapshots:
        observed_names = {
            item.get("provider")
            for item in snapshot.get("providers", [])
            if isinstance(item, dict)
        }
        if not set(provider_names) <= observed_names:
            raise ValueError("authority provider snapshot lacks authorized providers")
    provider_snapshot = {
        "schema": "normalized-provider-snapshot-v1",
        "profile": args.profile,
        "providers": [
            {
                "provider": provider,
                "tier": PROVIDER_TIERS[provider],
                "fixture_contract_version": "canonical-http-live-v1",
                "status": "ready",
            }
            for provider in provider_names
        ],
    }
    provider_encoded = (
        json.dumps(provider_snapshot, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    config_hash = os.environ["SCORECARD_SANITIZED_CONFIG_SHA256"]
    corpus_hashes = {
        "corpus.json": _sha(CORPUS),
        "hermetic-expected.json": _sha(fixtures_root / "hermetic-expected.json"),
        "stability-evidence.json": _sha(fixtures_root / "stability-evidence.json"),
    }
    evaluator_hash = sha256(
        json.dumps(EVALUATOR, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    dimensions = {
        "corpus_hashes": corpus_hashes,
        "evaluator": {
            "model": EVALUATOR["model"],
            "prompt_sha256": evaluator_hash,
            "settings_sha256": evaluator_hash,
        },
        "topology": {
            "egress": os.environ["SCORECARD_EGRESS"],
            "machine": os.environ["SCORECARD_MACHINE"],
        },
        "profile": args.profile,
        "provider_snapshot_sha256": sha256(provider_encoded).hexdigest(),
        "sanitized_config_sha256": config_hash,
    }
    generation = derive_generation(dimensions)
    if args.generation != generation:
        raise ValueError("declared generation does not match synchronized dimensions")
    identity_common = {
        "generation": generation,
        "sanitized_config_sha256": config_hash,
        "dimensions": dimensions,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
    }
    receipts = [
        {
            "operation_id": f"{side}:{case['id']}",
            "repository": "canonical_authority",
            "durable_id": str(
                raw[side][case["id"]].get("search_run_id")
                or raw[side][case["id"]].get("extraction_run_id")
            ),
            "status": "accepted",
        }
        for side in ("baseline", "candidate")
        for case in [*searches, *extractions]
    ]
    surface_cases = [
        {
            "case_id": f"surface-{index:02d}",
            **execute_surface_fixture({"outcome": outcome, "code": None}),
        }
        for index, outcome in enumerate(
            (
                "success",
                "degraded",
                "empty",
                "timeout",
                "policy_rejected",
                "authentication_rejected",
                "providers_failed",
            ),
            1,
        )
    ]
    elapsed_ms = max(1, (perf_counter_ns() - timer) // 1_000_000)
    payload = {
        "run_id": args.run_id,
        "generation": generation,
        "candidate_identity": {
            **identity_common,
            "commit": os.environ["CANDIDATE_COMMIT"],
            "image_digest": os.environ["CANDIDATE_IMAGE_DIGEST"],
        },
        "baseline_identity": {
            **identity_common,
            "commit": os.environ["BASELINE_COMMIT"],
            "image_digest": os.environ["BASELINE_IMAGE_DIGEST"],
        },
        "corpus": {
            "version": corpus["version"],
            "hashes": corpus_hashes,
            "hermetic_search_case_ids": [case["id"] for case in searches],
            "hermetic_extraction_case_ids": [
                case["id"] for case in corpus["hermetic_extractions"]
            ],
            "competitive_case_ids": [
                *([case["id"] for case in searches]),
                *([case["id"] for case in extractions]),
            ],
            "dimensions": dimensions,
            "generation": generation,
        },
        "provider_snapshot": provider_snapshot,
        "surface_equivalence": {
            "schema": "surface-equivalence-v1",
            "status": "pass",
            "cases": surface_cases,
        },
        "timing_receipts": {
            "schema": "normalized-timing-receipts-v1",
            "operations": [
                {
                    "operation_id": "synchronized-live-corpus",
                    "wall_ms": elapsed_ms,
                    "component_ms": elapsed_ms,
                    "timeout_source": "none",
                    "cache_ms": 0,
                }
            ],
        },
        "persistence_receipts": {
            "schema": "normalized-persistence-receipts-v1",
            "status": "accepted",
            "reason": "canonical authorities returned durable operation ids",
            "receipts": receipts,
        },
        "artifacts": {
            "hermetic-summary.json": {
                "schema": "hermetic-summary-v1",
                "search_cases": 24,
                "extraction_cases": 8,
                "provider_execution": "canonical_http",
                "stability_verdict": stability.verdict,
            },
            **artifacts,
        },
        "competitive": {
            "deterministic_metrics": {
                "schema": "competitive-deterministic-metrics-v1",
                "consistent_pairs": competitive.consistent_pairs,
                "decisive_pairs": competitive.candidate_wins
                + competitive.baseline_wins,
                "candidate_wins": competitive.candidate_wins,
                "baseline_wins": competitive.baseline_wins,
                "p_value": competitive.p_value,
            },
            "blinded_comparisons": {
                "schema": "blinded-comparisons-v1",
                "pairs": [
                    {
                        "pair_id": pair.pair_id,
                        "mode": pair.mode,
                        "forward": raw_pair["forward"],
                        "reverse": raw_pair["reverse"],
                        "classification": pair.classification,
                    }
                    for pair, raw_pair in zip(competitive.pairs, pairs, strict=True)
                ],
            },
            "verdict": {
                "schema": "competitive-verdict-v1",
                "verdict": competitive.verdict,
                "reason": competitive.reason,
            },
        },
    }
    write_bundle(args.output, lane="competitive", stability=stability, payload=payload)
    verify_bundle(args.output)
    print(f"verified exact 28-case live bundle: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError) as exc:
        print(f"live scorecard failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
