"""
Shapley value attribution for Argus.

Two functions:

  rrf_attribution  — fast, exact decomposition of an RRF score into per-provider
                     contributions. O(n) because RRF is an additive game: each
                     provider's marginal contribution is independent of the coalition.

  shapley_sample   — general-purpose Shapley estimator for non-additive
                     characteristic functions (e.g. result-set quality metrics).
                     Uses permutation sampling (Strumbelj & Kononenko, 2014).
                     Accuracy improves with n_samples; ~512 is adequate for ≤14 players.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable

RRF_K = 60  # must match argus/broker/ranking.py


def rrf_attribution(
    provider_ranks: dict[str, int],
    k: int = RRF_K,
) -> dict[str, float]:
    """Decompose an RRF score into per-provider contributions.

    Args:
        provider_ranks: mapping of provider name → 0-indexed rank for a single URL.
                        Providers that did not return the URL should be absent.
        k: RRF constant (default 60, matches broker).

    Returns:
        Dict of provider → contribution. Values sum to the result's total RRF score.

    Because RRF is a sum of independent terms (1/(k+rank+1) per provider), the
    Shapley value of each provider equals its individual contribution — no sampling
    needed.
    """
    return {provider: 1.0 / (k + rank + 1) for provider, rank in provider_ranks.items()}


def shapley_sample(
    players: Iterable[str],
    characteristic_fn: Callable[[frozenset[str]], float],
    n_samples: int = 512,
    seed: int | None = None,
) -> dict[str, float]:
    """Estimate Shapley values via permutation sampling.

    Suitable for non-additive games where re-evaluating coalitions is cheap.
    For expensive characteristic functions (e.g. re-running live searches),
    keep n_samples small or compute offline.

    Args:
        players: the set of players (e.g. provider names).
        characteristic_fn: maps a coalition (frozenset of player names) to its value.
        n_samples: number of random permutations to sample.
        seed: optional RNG seed for reproducibility.

    Returns:
        Dict of player → estimated Shapley value. Values sum to v(all players).
    """
    player_list = list(players)
    n = len(player_list)
    if n == 0:
        return {}

    rng = random.Random(seed)
    totals: dict[str, float] = {p: 0.0 for p in player_list}

    for _ in range(n_samples):
        perm = player_list[:]
        rng.shuffle(perm)
        coalition: frozenset[str] = frozenset()
        v_before = characteristic_fn(coalition)
        for player in perm:
            coalition = coalition | {player}
            v_after = characteristic_fn(coalition)
            totals[player] += v_after - v_before
            v_before = v_after

    return {p: totals[p] / n_samples for p in player_list}
