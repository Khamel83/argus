"""Deterministic competitive procedure over paired, blinded evaluator outputs."""

from __future__ import annotations

from dataclasses import dataclass
from math import comb
from typing import Any, Mapping, Sequence

from .stability import StabilityVerdict


DECISIVE = frozenset({"candidate_win", "baseline_win"})
INCONCLUSIVE = frozenset({"ordering_conflict", "malformed", "unavailable"})
VALID = DECISIVE | INCONCLUSIVE | {"tie", "catastrophic_regression"}


@dataclass(frozen=True)
class PairVerdict:
    pair_id: str
    mode: str
    classification: str


@dataclass(frozen=True)
class CompetitiveVerdict:
    verdict: str
    pairs: tuple[PairVerdict, ...]
    consistent_pairs: int
    candidate_wins: int
    baseline_wins: int
    p_value: float | None
    reason: str


def _classify(pair: Mapping[str, Any]) -> PairVerdict:
    pair_id = str(pair.get("pair_id", "unknown"))
    mode = str(pair.get("mode", "unknown"))
    forward, reverse = pair.get("forward"), pair.get("reverse")
    outcomes = {forward, reverse}
    if "catastrophic_regression" in outcomes:
        classification = "catastrophic_regression"
    elif not outcomes <= VALID or "malformed" in outcomes:
        classification = "malformed"
    elif "unavailable" in outcomes:
        classification = "unavailable"
    elif forward == reverse and forward in {"candidate_win", "baseline_win", "tie"}:
        classification = str(forward)
    else:
        classification = "ordering_conflict"
    return PairVerdict(pair_id, mode, classification)


def exact_one_sided_sign_test(wins: int, losses: int) -> float:
    """Return P[X >= wins] for a fair-binomial exact one-sided sign test."""
    total = wins + losses
    if total == 0:
        return 1.0
    return sum(comb(total, successes) for successes in range(wins, total + 1)) / (
        2**total
    )


def evaluate_competitive(
    stability: StabilityVerdict,
    pairs: Sequence[Mapping[str, Any]],
) -> CompetitiveVerdict:
    """Apply the accepted ordered procedure without scoring speed or spend."""
    classified = tuple(_classify(pair) for pair in pairs)
    candidate_wins = sum(pair.classification == "candidate_win" for pair in classified)
    baseline_wins = sum(pair.classification == "baseline_win" for pair in classified)
    consistent = sum(
        pair.classification in DECISIVE or pair.classification == "tie"
        for pair in classified
    )

    def result(
        verdict: str, reason: str, p_value: float | None = None
    ) -> CompetitiveVerdict:
        return CompetitiveVerdict(
            verdict,
            classified,
            consistent,
            candidate_wins,
            baseline_wins,
            p_value,
            reason,
        )

    if stability.verdict != "stable":
        return result("unstable", "candidate stability is not stable")
    if any(pair.classification == "catastrophic_regression" for pair in classified):
        return result("not_competitive", "catastrophic regression")
    for mode in ("discovery", "grounding", "recovery", "research"):
        mode_pairs = [pair for pair in classified if pair.mode == mode]
        if sum(pair.classification == "baseline_win" for pair in mode_pairs) > sum(
            pair.classification == "candidate_win" for pair in mode_pairs
        ):
            return result("not_competitive", f"{mode} regressed")
    if consistent < 20:
        return result("inconclusive", "fewer than 20 consistent pairs")
    if candidate_wins + baseline_wins < 8:
        return result("inconclusive", "fewer than 8 decisive pairs")
    candidate_p = exact_one_sided_sign_test(candidate_wins, baseline_wins)
    if candidate_p < 0.05:
        return result("competitive", "candidate sign test passed", candidate_p)
    baseline_p = exact_one_sided_sign_test(baseline_wins, candidate_wins)
    if baseline_p < 0.05:
        return result("not_competitive", "baseline sign test passed", baseline_p)
    return result("inconclusive", "sign test was not decisive", candidate_p)
