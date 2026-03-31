"""Tests for broker: policies, ranking, dedupe, cache, health, budget, router."""

import pytest

from argus.models import SearchMode, ProviderName, SearchResult


# --- Policies ---

class TestPolicies:
    def test_recovery_order(self):
        from argus.broker.policies import get_provider_order
        order = get_provider_order(SearchMode.RECOVERY)
        assert order[0] == ProviderName.CACHE
        assert ProviderName.SEARXNG in order
        assert ProviderName.BRAVE in order

    def test_discovery_order(self):
        from argus.broker.policies import get_provider_order
        order = get_provider_order(SearchMode.DISCOVERY)
        assert order[0] == ProviderName.CACHE

    def test_grounding_order(self):
        from argus.broker.policies import get_provider_order
        order = get_provider_order(SearchMode.GROUNDING)
        assert ProviderName.BRAVE in order
        assert ProviderName.SERPER in order
        assert ProviderName.SEARXNG in order

    def test_research_order(self):
        from argus.broker.policies import get_provider_order
        order = get_provider_order(SearchMode.RESEARCH)
        assert ProviderName.TAVILY in order
        assert ProviderName.EXA in order

    def test_override_providers(self):
        from argus.broker.policies import resolve_routing
        override = [ProviderName.BRAVE, ProviderName.SERPER]
        result = resolve_routing(SearchMode.DISCOVERY, override)
        assert result == override

    def test_no_override_uses_policy(self):
        from argus.broker.policies import resolve_routing
        result = resolve_routing(SearchMode.DISCOVERY, None)
        assert ProviderName.CACHE in result


# --- Ranking ---

class TestRanking:
    def test_rrf_basic(self):
        from argus.broker.ranking import reciprocal_rank_fusion
        provider_results = {
            "provider_a": [
                SearchResult(url="https://a.com/1", title="A1", snippet="", provider=ProviderName.BRAVE),
                SearchResult(url="https://a.com/2", title="A2", snippet="", provider=ProviderName.BRAVE),
            ],
            "provider_b": [
                SearchResult(url="https://b.com/1", title="B1", snippet="", provider=ProviderName.SERPER),
                SearchResult(url="https://a.com/1", title="A1", snippet="", provider=ProviderName.SERPER),
            ],
        }
        merged = reciprocal_rank_fusion(provider_results)
        # https://a.com/1 appears in both providers, should rank higher
        assert merged[0].url == "https://a.com/1"
        assert merged[0].score > 0

    def test_rrf_single_provider(self):
        from argus.broker.ranking import reciprocal_rank_fusion
        results = [
            SearchResult(url="https://a.com", title="A", snippet=""),
            SearchResult(url="https://b.com", title="B", snippet=""),
        ]
        merged = reciprocal_rank_fusion({"p": results})
        assert len(merged) == 2
        assert merged[0].url == "https://a.com"  # rank 1

    def test_rrf_empty(self):
        from argus.broker.ranking import reciprocal_rank_fusion
        merged = reciprocal_rank_fusion({})
        assert merged == []


# --- Dedupe ---

class TestDedupe:
    def test_dedupes_same_url(self):
        from argus.broker.dedupe import dedupe_results
        results = [
            SearchResult(url="https://example.com", title="A", snippet=""),
            SearchResult(url="https://example.com", title="B", snippet=""),
        ]
        deduped = dedupe_results(results)
        assert len(deduped) == 1

    def test_dedupes_www_prefix(self):
        from argus.broker.dedupe import dedupe_results
        results = [
            SearchResult(url="https://example.com/page", title="A", snippet=""),
            SearchResult(url="https://www.example.com/page", title="B", snippet=""),
        ]
        deduped = dedupe_results(results)
        assert len(deduped) == 1

    def test_dedupes_trailing_slash(self):
        from argus.broker.dedupe import dedupe_results
        results = [
            SearchResult(url="https://example.com/", title="A", snippet=""),
            SearchResult(url="https://example.com", title="B", snippet=""),
        ]
        deduped = dedupe_results(results)
        assert len(deduped) == 1

    def test_keeps_distinct_urls(self):
        from argus.broker.dedupe import dedupe_results
        results = [
            SearchResult(url="https://a.com", title="A", snippet=""),
            SearchResult(url="https://b.com", title="B", snippet=""),
        ]
        deduped = dedupe_results(results)
        assert len(deduped) == 2


# --- URL Normalization ---

class TestUrlNormalization:
    def test_normalizes_case(self):
        from argus.broker.dedupe import normalize_url
        result = normalize_url("https://EXAMPLE.com")
        assert result == "https://example.com/"

    def test_strips_tracking_params(self):
        from argus.broker.dedupe import normalize_url
        url = "https://example.com/page?utm_source=fb&ref=abc&id=1"
        normalized = normalize_url(url)
        assert "utm_" not in normalized
        assert "ref=" not in normalized
        assert "id=1" in normalized

    def test_strips_www(self):
        from argus.broker.dedupe import normalize_url
        result = normalize_url("https://www.example.com")
        assert result == "https://example.com/"

    def test_sorts_query_params(self):
        from argus.broker.dedupe import normalize_url
        url = normalize_url("https://example.com?b=2&a=1")
        assert url == "https://example.com/?a=1&b=2"


# --- Cache ---

class TestCache:
    def test_put_and_get(self):
        from argus.broker.cache import SearchCache
        from argus.models import SearchResponse
        cache = SearchCache(ttl_hours=1)
        resp = SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[])
        cache.put("test", SearchMode.DISCOVERY, resp)
        assert cache.get("test", SearchMode.DISCOVERY) is resp

    def test_cache_miss(self):
        from argus.broker.cache import SearchCache
        cache = SearchCache()
        assert cache.get("nonexistent", SearchMode.DISCOVERY) is None

    def test_different_modes_separate(self):
        from argus.broker.cache import SearchCache
        from argus.models import SearchResponse
        cache = SearchCache()
        r1 = SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[])
        r2 = SearchResponse(query="test", mode=SearchMode.GROUNDING, results=[])
        cache.put("test", SearchMode.DISCOVERY, r1)
        cache.put("test", SearchMode.GROUNDING, r2)
        assert cache.get("test", SearchMode.DISCOVERY) is r1
        assert cache.get("test", SearchMode.GROUNDING) is r2

    def test_case_insensitive(self):
        from argus.broker.cache import SearchCache
        from argus.models import SearchResponse
        cache = SearchCache()
        resp = SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[])
        cache.put("Test", SearchMode.DISCOVERY, resp)
        assert cache.get("test", SearchMode.DISCOVERY) is resp

    def test_clear(self):
        from argus.broker.cache import SearchCache
        from argus.models import SearchResponse
        cache = SearchCache()
        cache.put("test", SearchMode.DISCOVERY, SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[]))
        cache.clear()
        assert cache.size() == 0


# --- Health ---

class TestHealth:
    def test_initial_state(self):
        from argus.broker.health import HealthTracker
        from argus.models import ProviderName
        h = HealthTracker()
        status = h.get_status(ProviderName.BRAVE)
        assert status is None

    def test_success_resets_failures(self):
        from argus.broker.health import HealthTracker
        from argus.models import ProviderName
        h = HealthTracker(failure_threshold=2)
        h.record_failure(ProviderName.BRAVE)
        h.record_failure(ProviderName.BRAVE)
        h.record_success(ProviderName.BRAVE)
        health = h.get_health(ProviderName.BRAVE)
        assert health.consecutive_failures == 0

    def test_degraded_after_threshold(self):
        from argus.broker.health import HealthTracker
        from argus.models import ProviderName, ProviderStatus
        h = HealthTracker(failure_threshold=3)
        for _ in range(3):
            h.record_failure(ProviderName.BRAVE)
        # At threshold, cooldown is applied -> temporarily_disabled
        assert h.get_status(ProviderName.BRAVE) == ProviderStatus.TEMPORARILY_DISABLED

    def test_degraded_when_cooldown_expires(self):
        from argus.broker.health import HealthTracker, ProviderHealth
        from argus.models import ProviderName, ProviderStatus
        h = HealthTracker(failure_threshold=2, cooldown_minutes=60)
        h.record_failure(ProviderName.BRAVE)
        h.record_failure(ProviderName.BRAVE)
        # Manually expire the cooldown
        health = h.get_health(ProviderName.BRAVE)
        health.disabled_until = 0  # expired
        # Failures still >= threshold, cooldown expired -> degraded
        assert h.get_status(ProviderName.BRAVE) == ProviderStatus.DEGRADED

    def test_cooldown_applied_after_threshold(self):
        from argus.broker.health import HealthTracker
        from argus.models import ProviderName, ProviderStatus
        h = HealthTracker(failure_threshold=2, cooldown_minutes=60)
        h.record_failure(ProviderName.BRAVE)
        h.record_failure(ProviderName.BRAVE)
        # After threshold, cooldown should be applied
        health = h.get_health(ProviderName.BRAVE)
        assert health.disabled_until is not None
        assert health.is_in_cooldown() is True

    def test_all_status(self):
        from argus.broker.health import HealthTracker
        from argus.models import ProviderName
        h = HealthTracker()
        h.record_failure(ProviderName.BRAVE)
        all_status = h.get_all_status()
        assert ProviderName.BRAVE in all_status
        assert all_status[ProviderName.BRAVE]["consecutive_failures"] == 1


# --- Budget ---

class TestBudgets:
    def test_no_budget_unlimited(self):
        from argus.broker.budgets import BudgetTracker
        from argus.models import ProviderName
        b = BudgetTracker()
        assert b.is_budget_exhausted(ProviderName.BRAVE) is False

    def test_set_budget_and_track(self):
        from argus.broker.budgets import BudgetTracker
        from argus.models import ProviderName
        b = BudgetTracker()
        b.set_budget(ProviderName.BRAVE, 1.0)
        b.record_usage(ProviderName.BRAVE, 0.5)
        assert b.get_remaining_budget(ProviderName.BRAVE) == 0.5
        assert b.is_budget_exhausted(ProviderName.BRAVE) is False

    def test_budget_exhausted(self):
        from argus.broker.budgets import BudgetTracker
        from argus.models import ProviderName
        b = BudgetTracker()
        b.set_budget(ProviderName.BRAVE, 1.0)
        b.record_usage(ProviderName.BRAVE, 1.0)
        assert b.is_budget_exhausted(ProviderName.BRAVE) is True

    def test_usage_count(self):
        from argus.broker.budgets import BudgetTracker
        from argus.models import ProviderName
        b = BudgetTracker()
        b.record_usage(ProviderName.BRAVE)
        b.record_usage(ProviderName.BRAVE)
        assert b.get_usage_count(ProviderName.BRAVE) == 2

    def test_check_status(self):
        from argus.broker.budgets import BudgetTracker
        from argus.models import ProviderName, ProviderStatus
        b = BudgetTracker()
        assert b.check_status(ProviderName.BRAVE) is None
        b.set_budget(ProviderName.BRAVE, 0.5)
        b.record_usage(ProviderName.BRAVE, 1.0)
        assert b.check_status(ProviderName.BRAVE) == ProviderStatus.BUDGET_EXHAUSTED


# --- Router ---

class TestRouter:
    def test_create_broker(self):
        from argus.broker.router import create_broker
        from argus.models import ProviderName
        # Reset config singleton so env doesn't interfere
        import argus.config as cfg_mod
        cfg_mod._config = None
        broker = create_broker()
        assert ProviderName.SEARXNG in broker._providers
        assert ProviderName.BRAVE in broker._providers
        assert len(broker._providers) == 7  # 5 live + 2 stubs
