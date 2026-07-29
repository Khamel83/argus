"""The live scorecard compiler consumes sealed evidence without doing I/O."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from argus.scorecard.bundle import verify_bundle
from argus.scorecard.corpus import load_corpus
from argus.scorecard.live import LiveExecutionError, write_live_execution_bundle
from argus.scorecard.residual import (
    ResidualError,
    verify_bounded_inconclusive_residual,
    write_bounded_inconclusive_residual,
)
from argus.scorecard.stability import HARD_GATES, evaluate_stability


FIXTURES = Path(__file__).parent / "fixtures" / "scorecard"


def _stability():
    profile = {
        gate: {
            "status": "pass",
            "evidence": {
                "schema": "normalized-gate-evidence-v2",
                "fixture_id": gate,
                "check": {"kind": gate, "passed": True, "observation_count": 1},
            },
        }
        for gate in HARD_GATES
    }
    return evaluate_stability(
        {"free": profile, "budgeted": profile}, architecture_exceptions=()
    )


def _surface():
    return {
        "schema": "surface-equivalence-v1",
        "status": "pass",
        "cases": [
            {
                "case_id": "success",
                "outcome": "success",
                "http_status": 200,
                "mcp_is_error": False,
                "cli_exit": 0,
                "python_error": False,
            }
        ],
    }


def _diagnostics(operation_id: str):
    return {
        "timing": {
            "operation_id": operation_id,
            "wall_ms": 5,
            "component_ms": 4,
            "timeout_source": "none",
            "cache_ms": 0,
        },
        "attempts": [
            {
                "name": "duckduckgo",
                "kind": "provider",
                "tier": 0,
                "status": "success",
                "reason": "completed",
                "result_count": 1,
                "latency_ms": 4,
            }
        ],
        "spend": {
            "provider_calls": 1,
            "reserved_usd": 0,
            "actual_usd": 0,
            "accounting_source": "authority",
            "reconciliation": "settled",
        },
        "cache": {
            "status": "miss",
            "age_ms": 0,
            "origin": "authority",
            "origin_spend_usd": 0,
            "eligible": True,
        },
        "freshness": {
            "observed_at": "2026-07-29T00:00:05Z",
            "age_seconds": 0,
            "window_seconds": 3600,
            "status": "fresh",
            "reason": "within_window",
        },
        "persistence": {
            "repository": "postgresql",
            "durable_id": f"receipt-{operation_id}",
            "status": "accepted",
        },
    }


def _sealed():
    corpus = load_corpus(FIXTURES / "corpus.json")
    provider_snapshot = {
        "schema": "normalized-provider-snapshot-v1",
        "profile": "free",
        "providers": [
            {
                "provider": "duckduckgo",
                "tier": 0,
                "fixture_contract_version": "live-v1",
                "status": "ready",
            }
        ],
    }
    operations = []
    for intent in corpus["search_intents"]:
        case_id = intent["id"]
        result = {
            "url": f"https://example.com/{case_id}",
            "title": case_id,
            "snippet": "sealed normalized evidence",
            "domain": "example.com",
            "provider": "duckduckgo",
            "score": 1.0,
            "egress": "residential",
            "machine": "homelab",
        }
        operations.append(
            {
                "case_id": case_id,
                "mode": intent["mode"],
                "request": {
                    "query": intent["live_query"],
                    "free_only": True,
                    "providers": ["duckduckgo"],
                    "caller": "scorecard-live",
                },
                "baseline": {
                    "outcome": "success",
                    "results": [result],
                    "diagnostics": _diagnostics(f"baseline-{case_id}"),
                },
                "candidate": {
                    "outcome": "success",
                    "results": [result],
                    "diagnostics": _diagnostics(f"candidate-{case_id}"),
                },
                "evaluation": {"forward": "unavailable", "reverse": "unavailable"},
            }
        )
    captures = []
    for extraction in corpus["live_extractions"]:
        case_id = extraction["id"]
        captures.append(
            {
                "case_id": case_id,
                "snapshot_id": extraction["snapshot_id"],
                "url": extraction["url"],
                "url_sha256": extraction["url_sha256"],
                "capture_sha256": sha256(f"capture:{case_id}".encode()).hexdigest(),
            }
        )
        content = {
            "url": extraction["url"],
            "title": case_id,
            "text": "sealed extracted evidence",
            "author": None,
            "date": None,
            "word_count": 3,
            "egress": "residential",
            "machine": "homelab",
            "source_type": "trafilatura",
        }
        operations.append(
            {
                "case_id": case_id,
                "mode": "extraction",
                "request": {
                    "url": extraction["url"],
                    "snapshot_id": extraction["snapshot_id"],
                    "url_sha256": extraction["url_sha256"],
                    "caller": "scorecard-live",
                },
                "baseline": {
                    "outcome": "success",
                    "content": content,
                    "diagnostics": _diagnostics(f"baseline-{case_id}"),
                },
                "candidate": {
                    "outcome": "success",
                    "content": content,
                    "diagnostics": _diagnostics(f"candidate-{case_id}"),
                },
                "evaluation": {"forward": "unavailable", "reverse": "unavailable"},
            }
        )
    return {
        "schema": "sealed-scorecard-live-execution-v1",
        "run_id": "free-live-001",
        "evaluator": {
            "status": "unavailable",
            "model": None,
            "prompt_sha256": "c" * 64,
            "settings_sha256": "d" * 64,
            "reason_code": "pinned_evaluator_not_configured",
        },
        "topology": {"egress": "residential", "machine": "homelab"},
        "sanitized_config_sha256": "f" * 64,
        "provider_snapshot": provider_snapshot,
        "baseline_identity": {
            "commit": "8" * 40,
            "image_digest": f"sha256:{'a' * 64}",
            "started_at": "2026-07-29T00:00:00Z",
            "finished_at": "2026-07-29T00:00:10Z",
        },
        "candidate_identity": {
            "commit": "9" * 40,
            "image_digest": f"sha256:{'b' * 64}",
            "started_at": "2026-07-29T00:00:01Z",
            "finished_at": "2026-07-29T00:00:11Z",
        },
        "captures": captures,
        "operations": operations,
    }


def test_sealed_live_compiler_writes_exact_offline_bundle(tmp_path):
    output = tmp_path / "live"

    write_live_execution_bundle(
        output,
        sealed=_sealed(),
        corpus=load_corpus(FIXTURES / "corpus.json"),
        stability=_stability(),
        surface_equivalence=_surface(),
    )

    manifest = verify_bundle(output)
    assert manifest["lane"] == "competitive"
    assert manifest["dimensions"]["profile"] == "free"
    assert manifest["dimensions"]["evaluator"]["status"] == "unavailable"
    assert manifest["competitive_verdict"] == "inconclusive"
    comparisons = json.loads(
        (output / "competitive" / "blinded-comparisons.json").read_text()
    )
    assert len(comparisons["pairs"]) == 28
    assert {pair["classification"] for pair in comparisons["pairs"]} == {"unavailable"}
    assert len(list((output / "artifacts" / "searches").glob("*.json"))) == 24
    assert len(list((output / "artifacts" / "extractions").glob("*.json"))) == 4


def test_sealed_live_compiler_accepts_a_genuinely_pinned_evaluator(tmp_path):
    sealed = _sealed()
    sealed["evaluator"] = {
        "status": "pinned",
        "model": "example/evaluator:free",
        "prompt_sha256": "c" * 64,
        "settings_sha256": "d" * 64,
        "reason_code": None,
    }
    for operation in sealed["operations"]:
        operation["evaluation"] = {"forward": "tie", "reverse": "tie"}

    write_live_execution_bundle(
        tmp_path / "live",
        sealed=sealed,
        corpus=load_corpus(FIXTURES / "corpus.json"),
        stability=_stability(),
        surface_equivalence=_surface(),
    )

    manifest = verify_bundle(tmp_path / "live")
    assert manifest["dimensions"]["evaluator"]["status"] == "pinned"
    assert manifest["competitive_verdict"] == "inconclusive"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda sealed: sealed["operations"].pop(),
            "exactly 28",
        ),
        (
            lambda sealed: sealed["operations"][0]["request"].update(
                {"query": "substituted query"}
            ),
            "literal live query",
        ),
        (
            lambda sealed: sealed["operations"][0]["candidate"].pop("diagnostics"),
            "diagnostics",
        ),
        (
            lambda sealed: sealed["operations"][0]["candidate"]["diagnostics"][
                "spend"
            ].update({"actual_usd": 0.01}),
            "zero spend",
        ),
        (
            lambda sealed: sealed["operations"][0]["evaluation"].update(
                {"forward": "tie"}
            ),
            "both orders unavailable",
        ),
    ],
)
def test_sealed_live_compiler_fails_closed(mutate, message, tmp_path):
    sealed = deepcopy(_sealed())
    mutate(sealed)

    with pytest.raises(LiveExecutionError, match=message):
        write_live_execution_bundle(
            tmp_path / "live",
            sealed=sealed,
            corpus=load_corpus(FIXTURES / "corpus.json"),
            stability=_stability(),
            surface_equivalence=_surface(),
        )


def _write_live_attempt(output: Path, run_id: str):
    sealed = _sealed()
    sealed["run_id"] = run_id
    if run_id.endswith("002"):
        for identity in ("baseline_identity", "candidate_identity"):
            sealed[identity]["started_at"] = "2026-07-29T00:01:00Z"
            sealed[identity]["finished_at"] = "2026-07-29T00:01:10Z"
    write_live_execution_bundle(
        output,
        sealed=sealed,
        corpus=load_corpus(FIXTURES / "corpus.json"),
        stability=_stability(),
        surface_equivalence=_surface(),
    )


def test_two_inconclusive_bundles_form_a_bounded_verified_residual(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    output = tmp_path / "residual"
    _write_live_attempt(first, "free-live-001")
    _write_live_attempt(second, "free-live-002")

    write_bounded_inconclusive_residual(
        output, first_bundle=first, second_bundle=second
    )
    receipt = verify_bounded_inconclusive_residual(
        output, first_bundle=first, second_bundle=second
    )

    assert receipt["status"] == "accepted_residual_risk"
    assert receipt["reason"] == "two_consecutive_free_profile_inconclusive"
    assert receipt["can_authorize_deployment"] is False
    assert [attempt["run_id"] for attempt in receipt["attempts"]] == [
        "free-live-001",
        "free-live-002",
    ]


def test_residual_rejects_reusing_one_execution(tmp_path):
    attempt = tmp_path / "attempt"
    _write_live_attempt(attempt, "free-live-001")

    with pytest.raises(ResidualError, match="distinct"):
        write_bounded_inconclusive_residual(
            tmp_path / "residual",
            first_bundle=attempt,
            second_bundle=attempt,
        )


def test_residual_rejects_attempts_in_reverse_chronological_order(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_sealed = _sealed()
    second_sealed = _sealed()
    first_sealed["run_id"] = "free-live-001"
    second_sealed["run_id"] = "free-live-002"
    for identity in ("baseline_identity", "candidate_identity"):
        second_sealed[identity]["started_at"] = "2026-07-28T23:59:00Z"
        second_sealed[identity]["finished_at"] = "2026-07-28T23:59:10Z"
    for output, sealed in ((first, first_sealed), (second, second_sealed)):
        write_live_execution_bundle(
            output,
            sealed=sealed,
            corpus=load_corpus(FIXTURES / "corpus.json"),
            stability=_stability(),
            surface_equivalence=_surface(),
        )

    with pytest.raises(ResidualError, match="consecutive"):
        write_bounded_inconclusive_residual(
            tmp_path / "residual",
            first_bundle=first,
            second_bundle=second,
        )
