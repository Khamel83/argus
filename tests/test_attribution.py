"""Tests for Shapley attribution — rrf_attribution and shapley_sample."""

import math

import pytest

from argus.attribution.shapley import rrf_attribution, shapley_sample

RRF_K = 60


class TestRrfAttribution:
    def test_single_provider_equals_rrf_score(self):
        attr = rrf_attribution({"duckduckgo": 0}, k=RRF_K)
        expected = 1.0 / (RRF_K + 0 + 1)
        assert math.isclose(attr["duckduckgo"], expected)

    def test_values_sum_to_total_score(self):
        provider_ranks = {"duckduckgo": 0, "brave": 2, "tavily": 5}
        attr = rrf_attribution(provider_ranks, k=RRF_K)
        expected_total = sum(1.0 / (RRF_K + rank + 1) for rank in provider_ranks.values())
        assert math.isclose(sum(attr.values()), expected_total)

    def test_higher_rank_means_higher_contribution(self):
        attr = rrf_attribution({"a": 0, "b": 9}, k=RRF_K)
        assert attr["a"] > attr["b"]

    def test_empty_returns_empty(self):
        assert rrf_attribution({}) == {}

    def test_attribution_keys_match_providers(self):
        providers = {"brave": 1, "exa": 3, "duckduckgo": 0}
        attr = rrf_attribution(providers, k=RRF_K)
        assert set(attr.keys()) == set(providers.keys())


class TestShaplySample:
    def test_additive_game_matches_individual_contributions(self):
        """For an additive v(S) = sum of individual values, Shapley == individual."""
        weights = {"a": 2.0, "b": 1.0, "c": 3.0}

        def v(coalition):
            return sum(weights[p] for p in coalition)

        phi = shapley_sample(list(weights), v, n_samples=1000, seed=42)

        for player, w in weights.items():
            assert math.isclose(phi[player], w, abs_tol=0.1), (
                f"Expected {player}={w}, got {phi[player]}"
            )

    def test_values_sum_to_grand_coalition(self):
        players = ["a", "b", "c"]
        weights = {"a": 1.0, "b": 2.0, "c": 3.0}

        def v(coalition):
            return sum(weights[p] for p in coalition)

        phi = shapley_sample(players, v, n_samples=512, seed=0)
        assert math.isclose(sum(phi.values()), v(frozenset(players)), abs_tol=0.01)

    def test_dummy_player_gets_zero(self):
        """A player that adds nothing to every coalition gets Shapley value 0."""
        def v(coalition):
            return 1.0 if "a" in coalition else 0.0

        phi = shapley_sample(["a", "b"], v, n_samples=512, seed=7)
        assert math.isclose(phi["b"], 0.0, abs_tol=1e-9)
        assert math.isclose(phi["a"], 1.0, abs_tol=1e-9)

    def test_symmetric_players_get_equal_values(self):
        """Two interchangeable players get equal Shapley values."""
        def v(coalition):
            return float(len(coalition))

        phi = shapley_sample(["x", "y"], v, n_samples=512, seed=1)
        assert math.isclose(phi["x"], phi["y"], abs_tol=1e-9)

    def test_empty_players_returns_empty(self):
        assert shapley_sample([], lambda s: 0.0) == {}

    def test_reproducible_with_seed(self):
        players = ["p1", "p2", "p3"]
        def v(s): return float(len(s) ** 2)
        phi1 = shapley_sample(players, v, n_samples=100, seed=99)
        phi2 = shapley_sample(players, v, n_samples=100, seed=99)
        assert phi1 == phi2


class TestRrfAttributionIntegration:
    """Integration: ranking.py correctly populates score_attribution."""

    def test_ranking_populates_attribution_when_requested(self):
        from argus.broker.ranking import reciprocal_rank_fusion
        from argus.models import SearchResult

        ddg_result = SearchResult(url="https://example.com", title="Ex", snippet="")
        brave_result = SearchResult(url="https://example.com", title="Ex", snippet="")

        provider_results = {
            "duckduckgo": [ddg_result],
            "brave": [brave_result],
        }

        merged = reciprocal_rank_fusion(provider_results, compute_attribution=True)
        assert len(merged) == 1
        r = merged[0]
        assert "duckduckgo" in r.score_attribution
        assert "brave" in r.score_attribution
        assert math.isclose(sum(r.score_attribution.values()), r.score, abs_tol=1e-10)

    def test_no_attribution_when_not_requested(self):
        from argus.broker.ranking import reciprocal_rank_fusion
        from argus.models import SearchResult

        result = SearchResult(url="https://example.com", title="Ex", snippet="")
        merged = reciprocal_rank_fusion({"ddg": [result]}, compute_attribution=False)
        assert merged[0].score_attribution == {}


def test_extract_request_accepts_caller():
    from argus.api.schemas import ExtractRequest

    req = ExtractRequest(url="https://example.com/a", caller="clio-intake-extract")
    assert req.caller == "clio-intake-extract"


def test_workflow_requests_accept_caller():
    from argus.api.schemas import (
        BuildResearchPackWorkflowRequest,
        SearchAndSummarizeWorkflowRequest,
    )

    a = SearchAndSummarizeWorkflowRequest(query="q", caller="clio-workflows")
    b = BuildResearchPackWorkflowRequest(topic="t", caller="hermes")
    assert a.caller == "clio-workflows"
    assert b.caller == "hermes"


def test_workflow_service_tags_internal_searches_with_caller():
    import inspect

    from argus.workflows.service import WorkflowService

    sig = inspect.signature(WorkflowService.__init__)
    assert "caller" in sig.parameters
