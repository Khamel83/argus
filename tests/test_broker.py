"""Tests for broker: policies, ranking, dedupe, cache, health, budget, router."""

from dataclasses import dataclass

import pytest

from argus.models import ProviderName, ProviderStatus, ProviderTrace, SearchMode, SearchQuery, SearchResult


@dataclass
class StubProvider:
    name: ProviderName
    results: list[SearchResult] | None = None
    trace: ProviderTrace | None = None
    available: bool = True
    raise_error: Exception | None = None

    def __post_init__(self):
        if self.results is None:
            self.results = []
        if self.trace is None:
            self.trace = ProviderTrace(
                provider=self.name,
                status="success",
                results_count=len(self.results),
            )
        self.calls = 0

    def is_available(self) -> bool:
        return self.available

    def status(self) -> ProviderStatus:
        return ProviderStatus.ENABLED if self.available else ProviderStatus.DISABLED_BY_CONFIG

    async def search(self, query: SearchQuery):
        self.calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        return list(self.results), ProviderTrace(
            provider=self.trace.provider,
            status=self.trace.status,
            results_count=len(self.results),
            latency_ms=self.trace.latency_ms,
            error=self.trace.error,
            budget_remaining=self.trace.budget_remaining,
        )


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

    def test_tier_sorting_free_first(self):
        """Tier 0 (SearXNG) should always come before tier 1+ providers."""
        from argus.broker.policies import get_provider_order
        for mode in SearchMode:
            order = get_provider_order(mode)
            # CACHE is index 0, SearXNG (tier 0) should be index 1
            assert order[1] == ProviderName.SEARXNG, f"{mode}: expected SearXNG at position 1, got {order[1]}"

    def test_tier_sorting_monthly_before_onetime(self):
        """Tier 1 (monthly) should always come before tier 3 (one-time)."""
        from argus.broker.policies import get_provider_order
        for mode in SearchMode:
            order = get_provider_order(mode)
            searxng_idx = order.index(ProviderName.SEARXNG)
            # Find first tier-1 and first tier-3 provider
            from argus.broker.budgets import PROVIDER_TIERS
            first_monthly = None
            first_onetime = None
            for p in order[searxng_idx + 1:]:
                tier = PROVIDER_TIERS.get(p, 99)
                if tier == 1 and first_monthly is None:
                    first_monthly = p
                if tier == 3 and first_onetime is None:
                    first_onetime = p
            if first_monthly and first_onetime:
                assert order.index(first_monthly) < order.index(first_onetime), (
                    f"{mode}: monthly {first_monthly} should come before one-time {first_onetime}"
                )

    def test_override_providers_sorted_by_tier(self):
        """Override provider lists should also be tier-sorted."""
        from argus.broker.policies import resolve_routing
        # Serper (tier 3) before Brave (tier 1) in override -> should reorder
        override = [ProviderName.SERPER, ProviderName.BRAVE]
        result = resolve_routing(SearchMode.DISCOVERY, override)
        assert result == [ProviderName.BRAVE, ProviderName.SERPER]

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

    def test_daily_pace_calculation(self):
        from argus.broker.budgets import BudgetTracker
        from argus.models import ProviderName
        b = BudgetTracker()
        b.set_budget(ProviderName.BRAVE, 3000.0)
        b.record_usage(ProviderName.BRAVE)
        # 3000 budget, 1 used → remaining 2999, pace = 2999/30 ≈ 100/day
        assert b.daily_pace(ProviderName.BRAVE) > 99
        assert b.daily_pace(ProviderName.BRAVE) < 101
        assert not b.is_over_pace(ProviderName.BRAVE)

    def test_over_pace_detection(self):
        from argus.broker.budgets import BudgetTracker
        from argus.models import ProviderName
        b = BudgetTracker()
        b.set_budget(ProviderName.BRAVE, 30.0)
        # Use all 30 today → remaining 0, pace = 0/day → over pace
        for _ in range(30):
            b.record_usage(ProviderName.BRAVE)
        assert b.is_over_pace(ProviderName.BRAVE)
        assert b.used_today(ProviderName.BRAVE) == 30

    def test_unlimited_provider_never_over_pace(self):
        from argus.broker.budgets import BudgetTracker
        from argus.models import ProviderName
        b = BudgetTracker()
        # No budget set → unlimited
        assert b.daily_pace(ProviderName.BRAVE) == float("inf")
        assert not b.is_over_pace(ProviderName.BRAVE)
        assert b.used_today(ProviderName.BRAVE) == 0


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
        assert len(broker._providers) == 11  # 5 live + 2 stubs + parallel + linkup + duckduckgo + valyu

    @pytest.mark.asyncio
    async def test_free_providers_always_queried(self, monkeypatch):
        """Tier 0 (SearXNG, DuckDuckGo) are always queried regardless of results."""
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        searxng = StubProvider(
            name=ProviderName.SEARXNG,
            results=[SearchResult(url="https://example.com/1", title="Result 1", snippet="ok", provider=ProviderName.SEARXNG)],
        )
        ddg = StubProvider(
            name=ProviderName.DUCKDUCKGO,
            results=[SearchResult(url="https://example.com/2", title="Result 2", snippet="ok", provider=ProviderName.DUCKDUCKGO)],
        )
        paid = StubProvider(
            name=ProviderName.BRAVE,
            results=[SearchResult(url="https://backup.com", title="Backup", snippet="backup", provider=ProviderName.BRAVE)],
        )

        broker = SearchBroker(
            providers={ProviderName.SEARXNG: searxng, ProviderName.DUCKDUCKGO: ddg, ProviderName.BRAVE: paid},
        )

        response = await broker.search(
            SearchQuery(
                query="always use free",
                mode=SearchMode.DISCOVERY,
                max_results=5,
                providers=[ProviderName.SEARXNG, ProviderName.DUCKDUCKGO, ProviderName.BRAVE],
            )
        )

        assert searxng.calls == 1
        assert ddg.calls == 1
        # Brave has no budget set, so it's unlimited — should also be queried
        assert paid.calls == 1
        assert response.total_results == 3

    @pytest.mark.asyncio
    async def test_paid_provider_skipped_when_over_pace(self, monkeypatch):
        """Paid providers are skipped when today's usage exceeds daily pace."""
        import time
        from argus.broker.budgets import BudgetTracker
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        free = StubProvider(
            name=ProviderName.SEARXNG,
            results=[SearchResult(url="https://example.com", title="Free", snippet="ok", provider=ProviderName.SEARXNG)],
        )
        paid = StubProvider(
            name=ProviderName.BRAVE,
            results=[SearchResult(url="https://backup.com", title="Paid", snippet="ok", provider=ProviderName.BRAVE)],
        )

        budget = BudgetTracker()
        broker = SearchBroker(
            providers={ProviderName.SEARXNG: free, ProviderName.BRAVE: paid},
            budget_tracker=budget,
        )
        # Budget of 300, but burn 200 today → pace is 100/30≈3.3/day → way over
        budget.set_budget(ProviderName.BRAVE, 300.0)
        for _ in range(200):
            budget.record_usage(ProviderName.BRAVE)

        response = await broker.search(
            SearchQuery(
                query="over pace",
                mode=SearchMode.DISCOVERY,
                providers=[ProviderName.SEARXNG, ProviderName.BRAVE],
            )
        )

        assert free.calls == 1
        assert paid.calls == 0
        assert response.total_results == 1
        assert "over pace" in response.traces[-1].error
        assert response.budget_warnings

    @pytest.mark.asyncio
    async def test_paid_provider_used_when_under_pace(self, monkeypatch):
        """Paid providers are queried when budget pace is healthy."""
        from argus.broker.budgets import BudgetTracker
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        free = StubProvider(
            name=ProviderName.SEARXNG,
            results=[SearchResult(url="https://example.com", title="Free", snippet="ok", provider=ProviderName.SEARXNG)],
        )
        paid = StubProvider(
            name=ProviderName.BRAVE,
            results=[SearchResult(url="https://backup.com", title="Paid", snippet="ok", provider=ProviderName.BRAVE)],
        )

        budget = BudgetTracker()
        broker = SearchBroker(
            providers={ProviderName.SEARXNG: free, ProviderName.BRAVE: paid},
            budget_tracker=budget,
        )
        # Set budget AFTER broker construction (constructor reads from config)
        budget.set_budget(ProviderName.BRAVE, 3000.0)
        for _ in range(10):
            budget.record_usage(ProviderName.BRAVE)

        response = await broker.search(
            SearchQuery(
                query="under pace",
                mode=SearchMode.DISCOVERY,
                providers=[ProviderName.SEARXNG, ProviderName.BRAVE],
            )
        )

        assert free.calls == 1
        assert paid.calls == 1
        assert response.total_results == 2

    @pytest.mark.asyncio
    async def test_one_time_credits_conserved_when_over_pace(self, monkeypatch):
        """Tier 3 (one-time) providers are more aggressively conserved."""
        from argus.broker.budgets import BudgetTracker
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        free = StubProvider(
            name=ProviderName.SEARXNG,
            results=[SearchResult(url="https://example.com", title="Free", snippet="ok", provider=ProviderName.SEARXNG)],
        )
        onetime = StubProvider(
            name=ProviderName.SERPER,
            results=[SearchResult(url="https://one.com", title="One", snippet="ok", provider=ProviderName.SERPER)],
        )

        budget = BudgetTracker()
        broker = SearchBroker(
            providers={ProviderName.SEARXNG: free, ProviderName.SERPER: onetime},
            budget_tracker=budget,
        )
        # Set budget AFTER broker construction (constructor reads from config)
        budget.set_budget(ProviderName.SERPER, 100.0)
        for _ in range(10):
            budget.record_usage(ProviderName.SERPER)

        response = await broker.search(
            SearchQuery(
                query="conserve one-time",
                mode=SearchMode.DISCOVERY,
                providers=[ProviderName.SEARXNG, ProviderName.SERPER],
            )
        )

        assert free.calls == 1
        assert onetime.calls == 0
        assert "one-time credits conserved" in response.traces[-1].error

    @pytest.mark.asyncio
    async def test_search_skips_budget_exhausted_provider(self, monkeypatch):
        from argus.broker.budgets import BudgetTracker
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        backup = StubProvider(
            name=ProviderName.SERPER,
            results=[SearchResult(url="https://fallback.com", title="Fallback", snippet="ok")],
        )

        exhausted_budget = BudgetTracker()
        broker = SearchBroker(
            providers={
                ProviderName.BRAVE: StubProvider(name=ProviderName.BRAVE, results=[]),
                ProviderName.SERPER: backup,
            },
            budget_tracker=exhausted_budget,
        )
        broker.budget_tracker.set_budget(ProviderName.BRAVE, 1.0)
        broker.budget_tracker.record_usage(ProviderName.BRAVE, 1.0)

        response = await broker.search(
            SearchQuery(
                query="budget exhausted",
                mode=SearchMode.DISCOVERY,
                providers=[ProviderName.BRAVE, ProviderName.SERPER],
            )
        )

        assert response.traces[0].provider == ProviderName.BRAVE
        assert response.traces[0].error == "budget exhausted"
        assert backup.calls == 1

    @pytest.mark.asyncio
    async def test_search_handles_provider_exception_and_continues(self, monkeypatch):
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        failing = StubProvider(
            name=ProviderName.SEARXNG,
            raise_error=RuntimeError("boom"),
        )
        backup = StubProvider(
            name=ProviderName.BRAVE,
            results=[SearchResult(url="https://backup.com", title="Backup", snippet="ok")],
        )
        broker = SearchBroker(
            providers={
                ProviderName.SEARXNG: failing,
                ProviderName.BRAVE: backup,
            },
        )

        response = await broker.search(
            SearchQuery(
                query="exception path",
                mode=SearchMode.DISCOVERY,
                providers=[ProviderName.SEARXNG, ProviderName.BRAVE],
            )
        )

        assert response.traces[0].status == "error"
        assert "boom" in response.traces[0].error
        assert backup.calls == 1

    @pytest.mark.asyncio
    async def test_persistence_failure_is_non_fatal(self, monkeypatch):
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.persist_search",
            lambda query_text, mode, response: (_ for _ in ()).throw(RuntimeError("persist failed")),
        )

        primary = StubProvider(
            name=ProviderName.SEARXNG,
            results=[SearchResult(url="https://example.com", title="Result", snippet="ok")],
        )
        broker = SearchBroker(providers={ProviderName.SEARXNG: primary})

        response = await broker.search(
            SearchQuery(
                query="persist fail",
                mode=SearchMode.DISCOVERY,
                providers=[ProviderName.SEARXNG],
            )
        )

        assert response.total_results == 1
        assert response.results[0].url == "https://example.com"
