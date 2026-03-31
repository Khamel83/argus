"""
Reciprocal Rank Fusion (RRF) for merging results across providers.
"""

from argus.models import SearchResult


# RRF constant — lower k means more weight on early ranks
RRF_K = 60


def reciprocal_rank_fusion(
    provider_results: dict[str, list[SearchResult]],
    k: int = RRF_K,
) -> list[SearchResult]:
    """Merge results from multiple providers using Reciprocal Rank Fusion.

    Each result gets a score of sum(1 / (k + rank)) across all providers.
    Returns results sorted by fused score descending.
    """
    scores: dict[str, float] = {}  # url -> fused score
    seen: dict[str, SearchResult] = {}  # url -> best result

    for provider, results in provider_results.items():
        for rank, result in enumerate(results):
            rrf_score = 1.0 / (k + rank + 1)  # 1-indexed rank
            url = result.url

            if url not in scores:
                scores[url] = 0.0
                seen[url] = result

            scores[url] += rrf_score

    # Sort by fused score descending
    sorted_urls = sorted(scores.keys(), key=lambda u: scores[u], reverse=True)

    merged = []
    for url in sorted_urls:
        result = seen[url]
        result.score = scores[url]
        merged.append(result)

    return merged
