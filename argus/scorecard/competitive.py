"""Deterministic competitive procedure over paired, blinded evaluator outputs."""

from __future__ import annotations

from dataclasses import dataclass
from math import comb
from typing import Any, Mapping, Sequence

from .corpus import COMPETITIVE_CASE_MODES, SEARCH_MODES
from .stability import StabilityVerdict


DECISIVE = frozenset({"candidate_win", "baseline_win"})
INCONCLUSIVE = frozenset({"ordering_conflict", "malformed", "unavailable"})
VALID_EVALUATOR_VALUES = DECISIVE | {
    "tie",
    "catastrophic_regression",
    "malformed",
    "unavailable",
}


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


def _classify(pair: object, *, expected_id: str | None = None) -> PairVerdict:
    if not isinstance(pair, Mapping):
        return PairVerdict(expected_id or "invalid", "unknown", "malformed")
    pair_id = pair.get("pair_id")
    mode = pair.get("mode")
    if not isinstance(pair_id, str) or not pair_id:
        pair_id = expected_id or "invalid"
    expected_mode = COMPETITIVE_CASE_MODES.get(pair_id)
    if (
        not isinstance(mode, str)
        or expected_mode is None
        or mode != expected_mode
        or set(pair) - {"pair_id", "mode", "forward", "reverse", "latency_ms", "cost"}
    ):
        return PairVerdict(
            pair_id, mode if isinstance(mode, str) else "unknown", "malformed"
        )
    forward, reverse = pair.get("forward"), pair.get("reverse")
    if (
        not isinstance(forward, str)
        or not isinstance(reverse, str)
        or forward not in VALID_EVALUATOR_VALUES
        or reverse not in VALID_EVALUATOR_VALUES
        or "malformed" in (forward, reverse)
    ):
        classification = "malformed"
    elif "catastrophic_regression" in (forward, reverse):
        classification = "catastrophic_regression"
    elif "unavailable" in (forward, reverse):
        classification = "unavailable"
    elif forward == reverse and forward in DECISIVE | {"tie"}:
        classification = forward
    else:
        classification = "ordering_conflict"
    return PairVerdict(pair_id, mode, classification)


def exact_one_sided_sign_test(wins: int, losses: int) -> float:
    """Return P[X >= wins] for a fair-binomial exact one-sided sign test."""
    if (
        isinstance(wins, bool)
        or isinstance(losses, bool)
        or not isinstance(wins, int)
        or not isinstance(losses, int)
        or wins < 0
        or losses < 0
    ):
        raise ValueError("wins and losses must be nonnegative integers")
    total = wins + losses
    if total == 0:
        return 1.0
    return sum(comb(total, successes) for successes in range(wins, total + 1)) / (
        2**total
    )


def _normalize_pairs(pairs: Sequence[Mapping[str, Any]]) -> tuple[PairVerdict, ...]:
    by_id: dict[str, list[object]] = {}
    malformed_extras: list[PairVerdict] = []
    for pair in pairs:
        if not isinstance(pair, Mapping):
            malformed_extras.append(_classify(pair))
            continue
        pair_id = pair.get("pair_id")
        if not isinstance(pair_id, str) or pair_id not in COMPETITIVE_CASE_MODES:
            malformed_extras.append(_classify(pair))
            continue
        by_id.setdefault(pair_id, []).append(pair)
    normalized: list[PairVerdict] = []
    for case_id, mode in COMPETITIVE_CASE_MODES.items():
        supplied = by_id.get(case_id, [])
        if not supplied:
            normalized.append(PairVerdict(case_id, mode, "unavailable"))
        elif len(supplied) > 1:
            normalized.append(PairVerdict(case_id, mode, "malformed"))
        else:
            normalized.append(_classify(supplied[0], expected_id=case_id))
    return tuple(normalized + malformed_extras)


def evaluate_competitive(
    stability: StabilityVerdict,
    pairs: Sequence[Mapping[str, Any]],
) -> CompetitiveVerdict:
    """Apply the accepted ordered procedure without scoring speed or spend."""
    if stability.verdict != "stable":
        return CompetitiveVerdict(
            "unstable",
            (),
            0,
            0,
            0,
            None,
            "candidate stability is not stable",
        )

    classified = _normalize_pairs(pairs)
    canonical = tuple(
        pair for pair in classified if pair.pair_id in COMPETITIVE_CASE_MODES
    )
    candidate_wins = sum(pair.classification == "candidate_win" for pair in canonical)
    baseline_wins = sum(pair.classification == "baseline_win" for pair in canonical)
    consistent = sum(
        pair.classification in DECISIVE or pair.classification == "tie"
        for pair in canonical
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

    if any(pair.classification == "catastrophic_regression" for pair in canonical):
        return result("not_competitive", "catastrophic regression")
    for mode in SEARCH_MODES:
        mode_pairs = [pair for pair in canonical if pair.mode == mode]
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
