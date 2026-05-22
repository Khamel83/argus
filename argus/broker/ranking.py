"""
Reciprocal Rank Fusion (RRF) for merging results across providers.
"""

from argus.attribution.shapley import rrf_attribution
from argus.models import SearchResult


# RRF constant — lower k means more weight on early ranks
RRF_K = 60


def reciprocal_rank_fusion(
    provider_results: dict[str, list[SearchResult]],
    k: int = RRF_K,
    compute_attribution: bool = False,
) -> list[SearchResult]:
    """Merge results from multiple providers using Reciprocal Rank Fusion.

    Each result gets a score of sum(1 / (k + rank)) across all providers.
    Returns results sorted by fused score descending.

    When compute_attribution is True, each result's score_attribution is
    populated with per-provider Shapley values (exact, O(n), values sum to score).
    """
    scores: dict[str, float] = {}        # url -> fused score
    seen: dict[str, SearchResult] = {}   # url -> best result
    ranks: dict[str, dict[str, int]] = {}  # url -> {provider -> 0-indexed rank}

    for provider, results in provider_results.items():
        for rank, result in enumerate(results):
            rrf_score = 1.0 / (k + rank + 1)  # 1-indexed rank
            url = result.url

            if url not in scores:
                scores[url] = 0.0
                seen[url] = result
                ranks[url] = {}

            scores[url] += rrf_score
            ranks[url][provider] = rank

    sorted_urls = sorted(scores.keys(), key=lambda u: scores[u], reverse=True)

    merged = []
    for url in sorted_urls:
        result = seen[url]
        result.score = scores[url]
        if compute_attribution:
            result.score_attribution = rrf_attribution(ranks[url], k=k)
        merged.append(result)

    return merged
