"""Hermetic behavior for the diagnostic retrieval scorecard."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from argus.scorecard.competitive import evaluate_competitive
from argus.scorecard.corpus import load_corpus, validate_corpus
from argus.scorecard.stability import HARD_GATES, evaluate_stability


FIXTURES = Path(__file__).parent / "fixtures" / "scorecard"
ROOT = Path(__file__).resolve().parents[1]


def _stable_profile() -> dict[str, dict[str, object]]:
    return {
        gate: {"status": "pass", "evidence": {"fixture": gate}} for gate in HARD_GATES
    }


def _pairs(
    outcome: str,
    *,
    count: int = 28,
    mode: str = "research",
) -> list[dict[str, str]]:
    return [
        {
            "pair_id": f"pair-{number:02d}",
            "mode": mode,
            "forward": outcome,
            "reverse": outcome,
        }
        for number in range(count)
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

    with pytest.raises(ValueError, match="minimum_evidence_shape"):
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

    catastrophic = evaluate_competitive(
        stable,
        _pairs("candidate_win")
        + [
            {
                "pair_id": "catastrophic",
                "mode": "research",
                "forward": "catastrophic_regression",
                "reverse": "catastrophic_regression",
            }
        ],
    )
    mode_loss = evaluate_competitive(
        stable,
        _pairs("candidate_win", count=24)
        + _pairs("baseline_win", count=4, mode="grounding"),
    )

    assert catastrophic.verdict == "not_competitive"
    assert mode_loss.verdict == "not_competitive"


def test_competitive_requires_consistency_and_decisive_pairs():
    stable = evaluate_stability(
        {"free": _stable_profile(), "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )

    unavailable = evaluate_competitive(
        stable, _pairs("unavailable", count=9) + _pairs("candidate_win", count=19)
    )
    tied = evaluate_competitive(stable, _pairs("tie", count=20))

    assert unavailable.verdict == "inconclusive"
    assert unavailable.consistent_pairs == 19
    assert tied.verdict == "inconclusive"


def test_competitive_uses_exact_one_sided_sign_test_and_ignores_speed_and_spend():
    stable = evaluate_stability(
        {"free": _stable_profile(), "budgeted": _stable_profile()},
        architecture_exceptions=(),
    )
    pairs = _pairs("candidate_win", count=8) + _pairs("tie", count=20)
    for pair in pairs:
        pair["latency_ms"] = "1"
        pair["cost"] = "999999"

    verdict = evaluate_competitive(stable, pairs)

    assert verdict.verdict == "competitive"
    assert verdict.candidate_wins == 8
    assert verdict.p_value < 0.05


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

    verdict = evaluate_competitive(stable, [pair])

    assert verdict.pairs[0].classification == expected


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
    )

    assert result.returncode == 0, result.stderr
    assert "stable" in result.stdout
    assert (tmp_path / "scorecard" / "checksums.sha256").is_file()


def test_ci_runs_only_the_hermetic_lane_and_publishes_its_bundle():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "scripts/run-scorecard.py --lane hermetic" in workflow
    assert "scorecard-hermetic" in workflow
    assert "--lane live" not in workflow
