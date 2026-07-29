"""Hermetic behavior for the diagnostic retrieval scorecard."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from argus.scorecard.authorization import (
    AuthorizationError,
    validate_authorization_bytes,
)
from argus.scorecard.competitive import CompetitiveInputError, evaluate_competitive
from argus.scorecard.corpus import load_corpus, validate_corpus
from argus.scorecard.stability import HARD_GATES, evaluate_stability


FIXTURES = Path(__file__).parent / "fixtures" / "scorecard"
ROOT = Path(__file__).resolve().parents[1]


def _stable_profile() -> dict[str, dict[str, object]]:
    return {
        gate: {"status": "pass", "evidence": {"fixture": gate}} for gate in HARD_GATES
    }


def _canonical_pairs(default: str = "tie") -> list[dict[str, str]]:
    corpus = load_corpus(FIXTURES / "corpus.json")
    return [
        {
            "pair_id": entry["id"],
            "mode": entry["mode"],
            "forward": default,
            "reverse": default,
        }
        for entry in corpus["search_intents"]
    ] + [
        {
            "pair_id": entry["id"],
            "mode": "extraction",
            "forward": default,
            "reverse": default,
        }
        for entry in corpus["live_extractions"]
    ]


def test_frozen_corpus_has_the_declared_search_and_extraction_coverage():
    corpus = load_corpus(FIXTURES / "corpus.json")

    validate_corpus(corpus)
    assert len(corpus["search_intents"]) == 24
    assert {entry["mode"] for entry in corpus["search_intents"]} == {
        "discovery",
        "grounding",
        "recovery",
        "research",
    }
    assert len(corpus["hermetic_extractions"]) == 8
    assert len(corpus["live_extractions"]) == 4
    assert all(entry["profiles"] for entry in corpus["search_intents"])


def test_corpus_rejects_an_intent_without_its_evidence_contract():
    corpus = json.loads((FIXTURES / "corpus.json").read_text())
    del corpus["search_intents"][0]["minimum_evidence_shape"]

    with pytest.raises(ValueError, match="exact keys"):
        validate_corpus(corpus)


@pytest.mark.parametrize(
    "mutation, message",
    (
        (lambda corpus: corpus.update({"unexpected": True}), "exact keys"),
        (
            lambda corpus: corpus["search_intents"][0].update({"unexpected": True}),
            "exact keys",
        ),
        (
            lambda corpus: corpus["search_intents"][0].update(
                {"profiles": ["free", "free"]}
            ),
            "profiles",
        ),
        (
            lambda corpus: corpus["search_intents"][0]["minimum_evidence_shape"].update(
                {"sources": True}
            ),
            "evidence",
        ),
        (
            lambda corpus: corpus["live_extractions"][0].pop("snapshot_sha256", None),
            "exact keys",
        ),
    ),
)
def test_corpus_rejects_noncanonical_shapes(mutation, message):
    corpus = json.loads((FIXTURES / "corpus.json").read_text())
    mutation(corpus)

    with pytest.raises(ValueError, match=message):
        validate_corpus(corpus)


def test_stability_evaluates_each_gate_and_free_cannot_be_masked_by_budgeted():
    free = _stable_profile()
    free["authentication"] = {"status": "fail", "evidence": {"fixture": "bad"}}

    verdict = evaluate_stability(
        {"free": free, "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )

    assert verdict.verdict == "unstable"
    assert verdict.profiles["free"].verdict == "unstable"
    assert verdict.profiles["budgeted"].verdict == "stable"
    assert verdict.profiles["free"].gates["authentication"].status == "fail"


def test_stability_fails_closed_for_missing_gate_evidence_and_direct_authority():
    free = _stable_profile()
    del free["provider_traces"]

    verdict = evaluate_stability(
        {"free": free, "budgeted": _stable_profile()},
        architecture_exceptions=("argus/api/routes_dashboard.py",),
    )

    assert verdict.verdict == "unstable"
    assert verdict.profiles["free"].gates["provider_traces"].status == "missing"
    assert verdict.architecture_exceptions == ("argus/api/routes_dashboard.py",)


def test_competitive_applies_catastrophic_and_mode_regression_before_statistics():
    stable = evaluate_stability(
        {"free": _stable_profile(), "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )

    catastrophic_pairs = _canonical_pairs("candidate_win")
    catastrophic_pairs[-1]["forward"] = "catastrophic_regression"
    catastrophic_pairs[-1]["reverse"] = "catastrophic_regression"
    catastrophic = evaluate_competitive(stable, catastrophic_pairs)
    mode_loss_pairs = _canonical_pairs()
    for pair in mode_loss_pairs:
        if pair["mode"] == "grounding":
            pair["forward"] = "baseline_win"
            pair["reverse"] = "baseline_win"
    mode_loss = evaluate_competitive(stable, mode_loss_pairs)

    assert catastrophic.verdict == "not_competitive"
    assert mode_loss.verdict == "not_competitive"


def test_competitive_requires_consistency_and_decisive_pairs():
    stable = evaluate_stability(
        {"free": _stable_profile(), "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )

    unavailable_pairs = _canonical_pairs("candidate_win")
    for pair in unavailable_pairs[:9]:
        pair["forward"] = "unavailable"
        pair["reverse"] = "unavailable"
    unavailable = evaluate_competitive(stable, unavailable_pairs)
    tied = evaluate_competitive(stable, _canonical_pairs("tie"))

    assert unavailable.verdict == "inconclusive"
    assert unavailable.consistent_pairs == 19
    assert tied.verdict == "inconclusive"


def test_competitive_uses_exact_one_sided_sign_test_and_ignores_speed_and_spend():
    stable = evaluate_stability(
        {"free": _stable_profile(), "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )
    pairs = _canonical_pairs("tie")
    for pair in pairs[:8]:
        pair["forward"] = "candidate_win"
        pair["reverse"] = "candidate_win"
    for pair in pairs:
        pair["latency_ms"] = "1"
        pair["cost"] = "999999"

    verdict = evaluate_competitive(stable, pairs)

    assert verdict.verdict == "competitive"
    assert verdict.candidate_wins == 8
    assert verdict.p_value < 0.05


def test_competitive_stability_rule_precedes_hostile_pair_parsing():
    unstable = evaluate_stability(
        {"free": {}, "budgeted": {}},
        architecture_exceptions=("argus/api/routes_dashboard.py",),
    )

    verdict = evaluate_competitive(
        unstable,
        [{"pair_id": ["hostile"], "mode": {"bad": True}, "forward": [], "reverse": {}}],
    )

    assert verdict.verdict == "unstable"
    assert verdict.pairs == ()


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "unknown", "wrong-mode"))
def test_competitive_rejects_any_noncanonical_28_pair_set(mutation):
    stable = evaluate_stability(
        {"free": _stable_profile(), "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )
    corpus = load_corpus(FIXTURES / "corpus.json")
    canonical = [
        {
            "pair_id": entry["id"],
            "mode": entry["mode"],
            "forward": "tie",
            "reverse": "tie",
        }
        for entry in corpus["search_intents"]
    ] + [
        {
            "pair_id": entry["id"],
            "mode": "extraction",
            "forward": "tie",
            "reverse": "tie",
        }
        for entry in corpus["live_extractions"]
    ]
    if mutation == "missing":
        canonical.pop()
    elif mutation == "duplicate":
        canonical[-1] = dict(canonical[0])
    elif mutation == "unknown":
        canonical[-1]["pair_id"] = "unknown-case"
    else:
        canonical[-1]["mode"] = "research"

    with pytest.raises(CompetitiveInputError):
        evaluate_competitive(stable, canonical)


@pytest.mark.parametrize(
    ("candidate_wins", "baseline_wins", "expected"),
    (
        (7, 0, "inconclusive"),
        (8, 0, "competitive"),
        (0, 8, "not_competitive"),
        (11, 5, "inconclusive"),
        (13, 3, "competitive"),
    ),
)
def test_competitive_sign_test_boundaries_and_reverse_direction(
    candidate_wins, baseline_wins, expected
):
    stable = evaluate_stability(
        {"free": _stable_profile(), "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )
    pairs = _canonical_pairs("tie")
    extraction_pairs = [pair for pair in pairs if pair["mode"] == "extraction"]
    search_pairs = [pair for pair in pairs if pair["mode"] != "extraction"]
    for pair in search_pairs[:candidate_wins]:
        pair["forward"] = "candidate_win"
        pair["reverse"] = "candidate_win"
    remaining_candidate = max(0, candidate_wins - len(search_pairs))
    for pair in extraction_pairs[:remaining_candidate]:
        pair["forward"] = "candidate_win"
        pair["reverse"] = "candidate_win"
    baseline_targets = extraction_pairs[:baseline_wins]
    if baseline_wins > len(baseline_targets):
        baseline_targets += search_pairs[
            candidate_wins : candidate_wins + baseline_wins - len(baseline_targets)
        ]
    for pair in baseline_targets:
        pair["forward"] = "baseline_win"
        pair["reverse"] = "baseline_win"

    verdict = evaluate_competitive(stable, pairs)

    assert verdict.verdict == expected
    assert verdict.candidate_wins == candidate_wins
    assert verdict.baseline_wins == baseline_wins


@pytest.mark.parametrize(
    "fixture_name, expected",
    (
        ("win.json", "candidate_win"),
        ("loss.json", "baseline_win"),
        ("tie.json", "tie"),
        ("conflict.json", "ordering_conflict"),
        ("catastrophic.json", "catastrophic_regression"),
        ("malformed.json", "malformed"),
        ("unavailable.json", "unavailable"),
    ),
)
def test_paired_evaluator_fixture_classification(fixture_name, expected):
    stable = evaluate_stability(
        {"free": _stable_profile(), "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )
    pair = json.loads((FIXTURES / "evaluator" / fixture_name).read_text())
    pair["pair_id"] = "discovery-01"
    pair["mode"] = "discovery"

    pairs = _canonical_pairs()
    pairs[0] = pair
    verdict = evaluate_competitive(stable, pairs)

    assert (
        next(
            item for item in verdict.pairs if item.pair_id == "discovery-01"
        ).classification
        == expected
    )


def test_hermetic_runner_writes_a_verified_bundle_without_live_execution(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run-scorecard.py",
            "--lane",
            "hermetic",
            "--output",
            str(tmp_path / "scorecard"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "ARGUS_AUTHORITY_URL": "https://must-not-be-called.invalid",
            "ARGUS_AUTHORITY_TOKEN": "__ARGUS_SECRET_SENTINEL__",
            "ARGUS_AUTOLOAD_DOTENV": "false",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "stable" in result.stdout
    assert (tmp_path / "scorecard" / "checksums.sha256").is_file()


def test_hermetic_runner_fails_for_broken_fixtures_before_live_execution(tmp_path):
    broken = tmp_path / "fixtures"
    broken.mkdir()
    corpus = json.loads((FIXTURES / "corpus.json").read_text())
    corpus["search_intents"][0]["hermetic_input"]["transport_outcome"] = "empty"
    (broken / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
    (broken / "stability-evidence.json").write_bytes(
        (FIXTURES / "stability-evidence.json").read_bytes()
    )
    (broken / "hermetic-expected.json").write_bytes(
        (FIXTURES / "hermetic-expected.json").read_bytes()
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run-scorecard.py",
            "--lane",
            "hermetic",
            "--fixtures-root",
            str(broken),
            "--output",
            str(tmp_path / "broken-bundle"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={
            "PATH": "",
            "ARGUS_AUTHORITY_URL": "https://must-not-be-called.invalid",
            "ARGUS_API_KEY": "__ARGUS_SECRET_SENTINEL__",
        },
    )

    assert result.returncode != 0
    assert "fixture" in result.stderr.lower()
    assert not (tmp_path / "broken-bundle").exists()


def test_hermetic_gate_mutation_fails_only_that_gate_and_exits_unstable(tmp_path):
    import shutil

    broken = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, broken)
    evidence_path = broken / "stability-evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["profiles"]["free"]["authentication"]["raw"]["samples"][0] = False
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run-scorecard.py",
            "--lane",
            "hermetic",
            "--fixtures-root",
            str(broken),
            "--output",
            str(tmp_path / "unstable-bundle"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "ARGUS_AUTOLOAD_DOTENV": "false"},
    )

    assert result.returncode == 1, result.stderr
    gates = json.loads(
        (tmp_path / "unstable-bundle" / "stability" / "gates.json").read_text()
    )
    assert gates["profiles"]["free"]["gates"]["authentication"]["status"] == "fail"
    assert all(
        verdict["status"] == "pass"
        for gate, verdict in gates["profiles"]["free"]["gates"].items()
        if gate != "authentication"
    )
    assert gates["profiles"]["budgeted"]["verdict"] == "stable"


def test_ci_runs_only_the_hermetic_lane_and_publishes_its_bundle():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "scripts/run-scorecard.py --lane hermetic" in workflow
    assert "scorecard-hermetic" in workflow
    assert "--lane live" not in workflow


def test_live_workflow_is_free_only_on_schedule_and_budgeted_is_receipt_gated():
    workflow = (ROOT / ".github" / "workflows" / "scorecard-live.yml").read_text()

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "live-config" in workflow
    assert "scheduled-free" in workflow
    assert "budgeted" in workflow
    assert "scorecard-budgeted" in workflow
    assert "scripts/run-live-scorecard.py" not in workflow
    assert "Authorization" not in workflow
    assert "secrets." not in workflow
    assert "BASELINE_TOKEN" not in workflow
    assert "CANDIDATE_TOKEN" not in workflow
    assert "curl " not in workflow
    assert "argus search" not in workflow
    assert "/api/search" not in workflow
    assert "/api/extract" not in workflow
    assert "reserve" not in workflow.lower()
    assert "consume" not in workflow.lower()
    assert "deploy" not in workflow.lower()
    assert "actions/cache" not in workflow
    run_blocks = workflow.split("run:")[1:]
    assert all(
        "${{ inputs." not in block.split("\n      -", 1)[0] for block in run_blocks
    )
    assert "diagnostic-only" in workflow
    assert "Task 16/P1" in workflow


def test_budgeted_authorization_receipt_is_exact_digest_bound():
    receipt = {
        "schema": "scorecard-budget-authorization-v1",
        "receipt_id": "receipt-001",
        "run_id": "run-001",
        "generation": "a" * 64,
        "permitted_providers": ["brave"],
        "maximum_tier": 1,
        "call_count_cap": 28,
        "cost_or_credit_cap": 1000,
        "one_time_credit_providers": [],
        "issued_at": "2026-07-29T00:00:00Z",
    }
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    digest = __import__("hashlib").sha256(encoded.encode()).hexdigest()
    validated = validate_authorization_bytes(
        encoded.encode(),
        expected_sha256=digest,
        run_id="run-001",
        generation="a" * 64,
    )
    assert validated["receipt_id"] == "receipt-001"
    with pytest.raises(AuthorizationError, match="digest"):
        validate_authorization_bytes(
            encoded.encode(),
            expected_sha256="0" * 64,
            run_id="run-001",
            generation="a" * 64,
        )


def test_budgeted_authorization_fails_closed_on_mismatch():
    receipt = {
        "schema": "scorecard-budget-authorization-v1",
        "receipt_id": "receipt-002",
        "run_id": "wrong-run",
        "generation": "b" * 64,
        "permitted_providers": ["serper"],
        "maximum_tier": 3,
        "call_count_cap": 1,
        "cost_or_credit_cap": 1,
        "one_time_credit_providers": [],
        "issued_at": "2026-07-29T00:00:00Z",
    }
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    digest = __import__("hashlib").sha256(encoded.encode()).hexdigest()

    with pytest.raises(AuthorizationError, match="run id|one-time"):
        validate_authorization_bytes(
            encoded.encode(),
            expected_sha256=digest,
            run_id="run-002",
            generation="b" * 64,
        )


@pytest.mark.parametrize("hostile", ([["serper"]], [{"provider": "serper"}], [1]))
def test_budgeted_authorization_rejects_hostile_one_time_provider_lists(hostile):
    receipt = {
        "schema": "scorecard-budget-authorization-v1",
        "receipt_id": "receipt-hostile",
        "run_id": "run-hostile",
        "generation": "a" * 64,
        "permitted_providers": ["serper"],
        "maximum_tier": 3,
        "call_count_cap": 28,
        "cost_or_credit_cap": 1,
        "one_time_credit_providers": hostile,
        "issued_at": "2026-07-29T00:00:00Z",
    }
    encoded = (
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    with pytest.raises(AuthorizationError):
        validate_authorization_bytes(
            encoded,
            expected_sha256=__import__("hashlib").sha256(encoded).hexdigest(),
            run_id="run-hostile",
            generation="a" * 64,
        )


@pytest.mark.parametrize(
    "hostile",
    (
        [["brave"]],
        [{"provider": "brave"}],
        [1],
        ["not-a-provider"],
        ["brave", "brave"],
    ),
)
def test_budgeted_authorization_rejects_hostile_permitted_provider_lists(hostile):
    receipt = {
        "schema": "scorecard-budget-authorization-v1",
        "receipt_id": "receipt-hostile-permitted",
        "run_id": "run-hostile",
        "generation": "a" * 64,
        "permitted_providers": hostile,
        "maximum_tier": 1,
        "call_count_cap": 28,
        "cost_or_credit_cap": 1,
        "one_time_credit_providers": [],
        "issued_at": "2026-07-29T00:00:00Z",
    }
    encoded = (
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    with pytest.raises(AuthorizationError):
        validate_authorization_bytes(
            encoded,
            expected_sha256=__import__("hashlib").sha256(encoded).hexdigest(),
            run_id="run-hostile",
            generation="a" * 64,
        )
