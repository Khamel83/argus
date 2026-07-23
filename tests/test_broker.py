"""Tests for broker: policies, ranking, dedupe, cache, health, budget, router."""

from dataclasses import dataclass

import pytest

from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchMode,
    SearchQuery,
    SearchResult,
)
from argus.providers.base import ProbeCapability


@dataclass
class StubProvider:
    probe_capability = ProbeCapability.ASYNC_NATIVE

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
        return (
            ProviderStatus.ENABLED
            if self.available
            else ProviderStatus.DISABLED_BY_CONFIG
        )

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
            credit_info=self.trace.credit_info,
        )


def test_production_broker_never_opens_legacy_sqlite_budget_store(
    monkeypatch,
    tmp_path,
):
    from unittest.mock import MagicMock

    import argus.broker.budget_persistence as budget_persistence
    import argus.authority as authority
    from argus.broker.router import SearchBroker
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_DB_URL", "postgresql://argus@example.invalid/argus")
    monkeypatch.setenv("ARGUS_BUDGET_DB_PATH", str(tmp_path / "legacy.db"))
    monkeypatch.setattr(
        budget_persistence,
        "BudgetStore",
        MagicMock(side_effect=AssertionError("legacy SQLite must not open")),
    )
    monkeypatch.setattr(authority, "broker_construction_allowed", lambda **kwargs: None)
    reset_config()
    try:
        broker = SearchBroker(providers={}, spend_repository=MagicMock())
    finally:
        reset_config()

    assert broker.budget_tracker._store is None


def _half_open_paid_executor(monkeypatch, provider, *, caller_caps=None):
    from unittest.mock import MagicMock

    from argus.broker.budgets import BudgetTracker
    from argus.broker.execution import ProviderExecutor
    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix

    now = [100.0]
    monkeypatch.setattr("argus.broker.health.time.time", lambda: now[0])
    matrix = ReachabilityMatrix(clock=lambda: now[0], failure_ttl_seconds=30)
    matrix.update_probe(
        "local",
        ProviderName.BRAVE,
        reachable=False,
        latency_ms=450,
        source="provider_execution",
    )
    health = HealthTracker(failure_threshold=1, cooldown_minutes=0.5)
    health.record_failure(ProviderName.BRAVE)
    now[0] += 31
    budgets = BudgetTracker()
    budgets.set_budget(ProviderName.BRAVE, 100.0)
    spend = MagicMock()
    spend.reserve.side_effect = lambda **kwargs: MagicMock(
        attempt_id=kwargs["idempotency_key"],
        reserved_charge=0.01,
    )
    spend.provider_summary.return_value = {"remaining": 99.0}
    executor = ProviderExecutor(
        providers={ProviderName.BRAVE: provider},
        health_tracker=health,
        budget_tracker=budgets,
        reachability=matrix,
        caller_tier_caps=caller_caps,
        spend_repository=spend,
    )
    return executor, health, matrix, spend, now


@pytest.mark.asyncio
async def test_successful_paid_execution_records_public_reachability_evidence():
    from argus.broker.budgets import BudgetTracker
    from argus.broker.execution import ProviderExecutor
    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix

    matrix = ReachabilityMatrix()
    health = HealthTracker()
    provider = StubProvider(
        name=ProviderName.BRAVE,
        trace=ProviderTrace(
            provider=ProviderName.BRAVE,
            status="success",
            latency_ms=37,
            egress="local",
        ),
    )
    executor = ProviderExecutor(
        providers={ProviderName.BRAVE: provider},
        health_tracker=health,
        budget_tracker=BudgetTracker(),
        reachability=matrix,
    )

    await executor._execute_provider(
        SearchQuery(query="paid evidence"), provider, ProviderName.BRAVE
    )

    evidence = matrix.get_all()[ProviderName.BRAVE]["probes"]["local"]
    assert evidence["reachable"] is True
    assert evidence["latency_ms"] == 37
    assert evidence["source"] == "provider_execution"
    assert health.peek_health(ProviderName.BRAVE).last_success is not None


@pytest.mark.asyncio
async def test_background_probe_refresh_establishes_health_evidence():
    from unittest.mock import AsyncMock, MagicMock

    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ProbeOutcome, ProbeRunResult
    from argus.broker.router import SearchBroker

    broker = SearchBroker.__new__(SearchBroker)
    broker._providers = {}
    broker._egress_nodes = {}
    broker._health = HealthTracker()
    broker._reachability = MagicMock()
    broker._reachability.probe_all = AsyncMock()
    broker._reachability.probe_all.return_value = ProbeRunResult(
        (ProbeOutcome(ProviderName.DUCKDUCKGO, True),)
    )

    await broker.refresh_provider_evidence()

    assert broker._health.peek_health(ProviderName.DUCKDUCKGO).last_success is not None


@pytest.mark.asyncio
async def test_refresh_health_uses_only_outcomes_from_current_probe_run():
    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix
    from argus.broker.router import SearchBroker

    now = [100.0]
    matrix = ReachabilityMatrix(clock=lambda: now[0])
    matrix.update_probe(
        "local",
        ProviderName.BRAVE,
        reachable=True,
        latency_ms=9,
        source="provider_execution",
    )
    duck = StubProvider(
        name=ProviderName.DUCKDUCKGO,
        trace=ProviderTrace(
            provider=ProviderName.DUCKDUCKGO,
            status="error",
            latency_ms=20,
        ),
    )
    broker = SearchBroker.__new__(SearchBroker)
    broker._providers = {ProviderName.DUCKDUCKGO: duck}
    broker._egress_nodes = {}
    broker._health = HealthTracker()
    broker._reachability = matrix

    await broker.refresh_provider_evidence()

    assert broker._health.peek_health(ProviderName.BRAVE) is None
    assert broker._health.peek_health(ProviderName.DUCKDUCKGO).consecutive_failures == 1

    now[0] += 1
    duck.trace = ProviderTrace(
        provider=ProviderName.DUCKDUCKGO,
        status="success",
        latency_ms=10,
    )
    await broker.refresh_provider_evidence()

    duck_health = broker._health.peek_health(ProviderName.DUCKDUCKGO)
    assert duck_health.consecutive_failures == 0
    assert duck.calls == 2


def test_provider_health_snapshot_is_frozen_and_detached():
    from dataclasses import FrozenInstanceError

    from argus.broker.budgets import BudgetTracker
    from argus.broker.health import HealthTracker
    from argus.broker.router import SearchBroker

    health = HealthTracker()
    health.record_success(ProviderName.BRAVE)
    broker = SearchBroker.__new__(SearchBroker)
    broker._providers = {ProviderName.BRAVE: StubProvider(name=ProviderName.BRAVE)}
    broker._health = health
    broker._budgets = BudgetTracker()

    frozen = health.snapshot(ProviderName.BRAVE)
    captured = broker.get_provider_status(ProviderName.BRAVE)
    health.record_failure(ProviderName.BRAVE)

    assert captured["health"]["consecutive_failures"] == 0
    assert captured["health"]["last_failure"] is None
    with pytest.raises(FrozenInstanceError):
        frozen.consecutive_failures = 99


@pytest.mark.asyncio
async def test_cancelled_probe_cannot_mutate_shared_evidence_after_shutdown():
    import asyncio
    import threading

    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix
    from argus.broker.router import SearchBroker

    entered = threading.Event()

    class BlockingProvider(StubProvider):
        probe_capability = ProbeCapability.ASYNC_NATIVE

        async def search(self, query):
            entered.set()
            await asyncio.Event().wait()

    matrix = ReachabilityMatrix()
    health = HealthTracker()
    broker = SearchBroker.__new__(SearchBroker)
    broker._providers = {
        ProviderName.DUCKDUCKGO: BlockingProvider(name=ProviderName.DUCKDUCKGO)
    }
    broker._egress_nodes = {}
    broker._health = health
    broker._reachability = matrix

    task = asyncio.create_task(broker.refresh_provider_evidence())
    loop = asyncio.get_running_loop()

    def cancel_when_entered():
        assert entered.wait(timeout=1)
        loop.call_soon_threadsafe(task.cancel)

    canceller = threading.Thread(target=cancel_when_entered)
    canceller.start()
    with pytest.raises(asyncio.CancelledError):
        await task
    canceller.join(timeout=1)
    await asyncio.sleep(0)

    assert ProviderName.DUCKDUCKGO not in matrix.get_all()
    assert health.peek_health(ProviderName.DUCKDUCKGO) is None


@pytest.mark.asyncio
async def test_paid_provider_recovers_after_bounded_reachability_half_open(monkeypatch):
    from unittest.mock import MagicMock

    from argus.broker.budgets import BudgetTracker
    from argus.broker.execution import ProviderExecutor
    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix

    now = [100.0]
    monkeypatch.setattr("argus.broker.health.time.time", lambda: now[0])
    matrix = ReachabilityMatrix(clock=lambda: now[0], failure_ttl_seconds=30)
    provider = StubProvider(
        name=ProviderName.BRAVE,
        trace=ProviderTrace(
            provider=ProviderName.BRAVE,
            status="error",
            latency_ms=450,
        ),
    )
    budgets = BudgetTracker()
    budgets.set_budget(ProviderName.BRAVE, 100.0)
    spend = MagicMock()
    spend.reserve.return_value = MagicMock(
        attempt_id="attempt-1",
        reserved_charge=0.01,
    )
    executor = ProviderExecutor(
        providers={ProviderName.BRAVE: provider},
        health_tracker=HealthTracker(
            failure_threshold=1,
            cooldown_minutes=0.5,
        ),
        budget_tracker=budgets,
        reachability=matrix,
        spend_repository=spend,
    )
    query = SearchQuery(
        query="paid recovery",
        providers=[ProviderName.BRAVE],
        caller="test",
    )

    first = await executor.execute(query, [ProviderName.BRAVE])
    immediate = await executor.execute(query, [ProviderName.BRAVE])

    assert first.traces[0].status == "error"
    assert immediate.traces[0].error == ("health: temporarily_disabled_after_failures")
    assert provider.calls == 1
    assert spend.reserve.call_count == 1

    now[0] += 31
    provider.trace = ProviderTrace(
        provider=ProviderName.BRAVE,
        status="success",
        latency_ms=25,
    )

    recovered = await executor.execute(query, [ProviderName.BRAVE])

    assert recovered.traces[0].status == "success"
    assert provider.calls == 2
    assert matrix.get_all()[ProviderName.BRAVE]["probes"]["local"]["reachable"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["free_only", "tier_cap", "free_results", "budget"])
async def test_paid_pre_call_gates_do_not_consume_half_open_claim(monkeypatch, gate):
    provider = StubProvider(name=ProviderName.BRAVE)
    caps = {"limited": 0} if gate == "tier_cap" else None
    executor, health, matrix, _, _ = _half_open_paid_executor(
        monkeypatch, provider, caller_caps=caps
    )
    query = SearchQuery(
        query="gate",
        caller="limited" if gate == "tier_cap" else "test",
        free_only=gate == "free_only",
        max_results=1,
    )
    order = [ProviderName.BRAVE]
    if gate == "free_results":
        free = StubProvider(
            name=ProviderName.DUCKDUCKGO,
            results=[
                SearchResult(
                    url="https://free.example",
                    title="free",
                    snippet="free",
                    provider=ProviderName.DUCKDUCKGO,
                )
            ],
        )
        executor._providers[ProviderName.DUCKDUCKGO] = free
        order = [ProviderName.DUCKDUCKGO, ProviderName.BRAVE]
    if gate == "budget":
        executor._should_query_paid = lambda provider, tier: (
            False,
            "over pace, conserving monthly credits",
        )

    await executor.execute(query, order)

    assert health.get_health(ProviderName.BRAVE).half_open_claimed is False
    assert matrix.peek_best_egress(ProviderName.BRAVE) == "local"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_reservation_skip_does_not_consume_half_open_claim(monkeypatch):
    from argus.persistence.provider_spend import BudgetExhaustedError

    provider = StubProvider(name=ProviderName.BRAVE)
    executor, health, matrix, spend, _ = _half_open_paid_executor(monkeypatch, provider)
    spend.reserve.side_effect = BudgetExhaustedError("exhausted")

    await executor.execute(
        SearchQuery(query="reservation", caller="test"),
        [ProviderName.BRAVE],
    )

    assert health.get_health(ProviderName.BRAVE).half_open_claimed is False
    assert matrix.peek_best_egress(ProviderName.BRAVE) == "local"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_second_claim_failure_rolls_back_health_and_no_call_reservation(
    monkeypatch,
):
    provider = StubProvider(name=ProviderName.BRAVE)
    executor, health, matrix, spend, _ = _half_open_paid_executor(monkeypatch, provider)
    matrix.claim_egress = lambda provider, egress: None

    outcome = await executor.execute(
        SearchQuery(query="claim race", caller="test"),
        [ProviderName.BRAVE],
    )

    assert outcome.traces[0].error == "half-open claim unavailable"
    assert health.get_health(ProviderName.BRAVE).half_open_claimed is False
    assert provider.calls == 0
    spend.settle.assert_called_once()
    assert spend.settle.call_args.kwargs == {
        "actual_charge": 0.0,
        "outcome": "skipped",
    }


@pytest.mark.asyncio
async def test_cancelled_paid_invocation_releases_half_open_claims(monkeypatch):
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()

    class GateProvider(StubProvider):
        async def search(self, query):
            self.calls += 1
            entered.set()
            await release.wait()
            return [], ProviderTrace(provider=self.name, status="success")

    provider = GateProvider(name=ProviderName.BRAVE)
    executor, health, matrix, _, _ = _half_open_paid_executor(monkeypatch, provider)
    first = asyncio.create_task(
        executor.execute(
            SearchQuery(query="cancel", caller="test"),
            [ProviderName.BRAVE],
        )
    )
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    release.set()
    recovered = await executor.execute(
        SearchQuery(query="recover", caller="test"),
        [ProviderName.BRAVE],
    )

    assert recovered.traces[0].status == "success"
    assert health.get_health(ProviderName.BRAVE).half_open_claimed is False
    assert matrix.get_all()[ProviderName.BRAVE]["probes"]["local"]["reachable"] is True


@pytest.mark.asyncio
async def test_exactly_one_concurrent_paid_caller_claims_half_open(monkeypatch):
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()

    class GateProvider(StubProvider):
        async def search(self, query):
            self.calls += 1
            entered.set()
            await release.wait()
            return [], ProviderTrace(provider=self.name, status="success")

    provider = GateProvider(name=ProviderName.BRAVE)
    executor, _, _, _, _ = _half_open_paid_executor(monkeypatch, provider)
    first = asyncio.create_task(
        executor.execute(
            SearchQuery(query="first", caller="test"),
            [ProviderName.BRAVE],
        )
    )
    await entered.wait()
    second = await executor.execute(
        SearchQuery(query="second", caller="test"),
        [ProviderName.BRAVE],
    )

    assert second.traces[0].status == "skipped"
    assert provider.calls == 1

    release.set()
    assert (await first).traces[0].status == "success"


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
            assert order[1] == ProviderName.SEARXNG, (
                f"{mode}: expected SearXNG at position 1, got {order[1]}"
            )

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
            for p in order[searxng_idx + 1 :]:
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

    def test_parallel_recurring_credit_routes_before_one_time_providers(self):
        from argus.broker.budgets import PROVIDER_TIERS
        from argus.broker.policies import resolve_routing

        assert PROVIDER_TIERS[ProviderName.PARALLEL] == 1
        result = resolve_routing(
            SearchMode.RESEARCH,
            [ProviderName.SERPER, ProviderName.PARALLEL],
        )
        assert result == [ProviderName.PARALLEL, ProviderName.SERPER]

    def test_no_override_uses_policy(self):
        from argus.broker.policies import resolve_routing

        result = resolve_routing(SearchMode.DISCOVERY, None)
        assert ProviderName.CACHE in result

    def test_registered_providers_are_routed_or_non_routed(self):
        """Every documented provider should be registered and routed unless explicitly special."""
        from argus.broker.policies import get_provider_order
        from argus.broker.router import create_broker

        broker = create_broker()
        routed = set()
        for mode in SearchMode:
            routed.update(get_provider_order(mode))

        non_routed = {ProviderName.CACHE}
        missing = set(broker._providers) - routed - non_routed
        assert missing == set()


# --- Ranking ---


class TestRanking:
    def test_rrf_basic(self):
        from argus.broker.ranking import reciprocal_rank_fusion

        provider_results = {
            "provider_a": [
                SearchResult(
                    url="https://a.com/1",
                    title="A1",
                    snippet="",
                    provider=ProviderName.BRAVE,
                ),
                SearchResult(
                    url="https://a.com/2",
                    title="A2",
                    snippet="",
                    provider=ProviderName.BRAVE,
                ),
            ],
            "provider_b": [
                SearchResult(
                    url="https://b.com/1",
                    title="B1",
                    snippet="",
                    provider=ProviderName.SERPER,
                ),
                SearchResult(
                    url="https://a.com/1",
                    title="A1",
                    snippet="",
                    provider=ProviderName.SERPER,
                ),
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

    def test_attribution_variants_separate(self):
        from argus.broker.cache import SearchCache
        from argus.models import SearchResponse

        cache = SearchCache()
        plain = SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[])
        attributed = SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[])

        cache.put("test", SearchMode.DISCOVERY, plain)
        cache.put(
            "test",
            SearchMode.DISCOVERY,
            attributed,
            include_attribution=True,
        )

        assert cache.get("test", SearchMode.DISCOVERY) is plain
        assert (
            cache.get("test", SearchMode.DISCOVERY, include_attribution=True)
            is attributed
        )

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
        cache.put(
            "test",
            SearchMode.DISCOVERY,
            SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[]),
        )
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

    def test_cooldown_expiry_allows_one_half_open_attempt(self):
        from argus.broker.health import HealthTracker
        from argus.models import ProviderName

        h = HealthTracker(failure_threshold=2, cooldown_minutes=60)
        h.record_failure(ProviderName.BRAVE)
        h.record_failure(ProviderName.BRAVE)
        # Manually expire the cooldown
        health = h.get_health(ProviderName.BRAVE)
        health.disabled_until = 0  # expired
        assert h.peek_status(ProviderName.BRAVE) == ProviderStatus.DEGRADED
        assert h.get_status(ProviderName.BRAVE) is None
        assert h.get_status(ProviderName.BRAVE) == ProviderStatus.DEGRADED

    def test_cooldown_applied_after_threshold(self):
        from argus.broker.health import HealthTracker
        from argus.models import ProviderName

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
        # Use all 30 today → remaining 0 → over pace
        for _ in range(30):
            b.record_usage(ProviderName.BRAVE)
        assert b.is_over_pace(ProviderName.BRAVE)

    def test_heavy_day_after_empty_days_is_not_over_pace(self):
        """A single heavy day after empty days should NOT trigger over-pace."""
        from argus.broker.budgets import BudgetTracker
        import time
        from argus.models import ProviderName

        b = BudgetTracker()
        b.set_budget(ProviderName.BRAVE, 3000.0)
        now = time.time()
        # Use 200 queries today — that's a heavy day
        for i in range(200):
            b._usage[ProviderName.BRAVE].append((now, 1.0))
        # Remaining: 2800, 7-day rate: 200/7 = 28.6/day
        # Days until exhausted: 2800/28.6 = 97 days → NOT over pace
        assert not b.is_over_pace(ProviderName.BRAVE)

    def test_sustained_heavy_usage_is_over_pace(self):
        """Sustained heavy usage for a week SHOULD trigger over-pace."""
        from argus.broker.budgets import BudgetTracker
        import time
        from argus.models import ProviderName

        b = BudgetTracker()
        b.set_budget(ProviderName.BRAVE, 300.0)
        now = time.time()
        # Use 100/day for 7 days = 700 total, but budget is only 300
        for day in range(7):
            ts = now - (6 - day) * 24 * 3600
            for _ in range(100):
                b._usage[ProviderName.BRAVE].append((ts, 1.0))
        # Remaining: 0 (700 > 300) → over pace
        assert b.is_budget_exhausted(ProviderName.BRAVE)
        assert b.is_over_pace(ProviderName.BRAVE)

    def test_unlimited_provider_never_over_pace(self):
        from argus.broker.budgets import BudgetTracker
        from argus.models import ProviderName

        b = BudgetTracker()
        # No budget set → unlimited
        assert b.daily_pace(ProviderName.BRAVE) == float("inf")
        assert not b.is_over_pace(ProviderName.BRAVE)
        assert b.used_today(ProviderName.BRAVE) == 0

    def test_tier3_lifetime_tracking(self):
        """Tier-3 (one-time) credits should track usage forever, not reset after 30 days."""
        from argus.broker.budgets import BudgetTracker
        import time

        b = BudgetTracker()
        b.set_budget(ProviderName.SERPER, 8.0)
        # Record 5 usages now
        for _ in range(5):
            b.record_usage(ProviderName.SERPER)
        # Simulate 3 usages 31 days ago
        old_ts = time.time() - (31 * 24 * 3600)
        b._usage[ProviderName.SERPER] = [
            (old_ts, 1.0),
            (old_ts + 1, 1.0),
            (old_ts + 2, 1.0),
            (time.time(), 1.0),
            (time.time() + 1, 1.0),
            (time.time() + 2, 1.0),
            (time.time() + 3, 1.0),
            (time.time() + 4, 1.0),
        ]
        # Should show 8 total (3 old + 5 recent) — old entries NOT aged out
        assert b.get_monthly_usage(ProviderName.SERPER) == 8.0
        assert b.is_budget_exhausted(ProviderName.SERPER) is True

    def test_tier1_rolling_window_unaffected(self):
        """Tier-1 (monthly) credits should still use 30-day rolling window."""
        from argus.broker.budgets import BudgetTracker
        import time

        b = BudgetTracker()
        b.set_budget(ProviderName.BRAVE, 10.0)
        # Record 5 usages 31 days ago
        old_ts = time.time() - (31 * 24 * 3600)
        for _ in range(5):
            b._usage[ProviderName.BRAVE].append((old_ts, 1.0))
        # Old entries should age out — 0 usage
        assert b.get_monthly_usage(ProviderName.BRAVE) == 0.0
        assert b.is_budget_exhausted(ProviderName.BRAVE) is False

    def test_parallel_monthly_usage_ages_out_instead_of_becoming_lifetime(self):
        from argus.broker.budgets import BudgetTracker
        import time

        b = BudgetTracker()
        b.set_budget(ProviderName.PARALLEL, 5000.0)
        old_ts = time.time() - (31 * 24 * 3600)
        b._usage[ProviderName.PARALLEL].append((old_ts, 1.0))

        assert b.get_provider_tier(ProviderName.PARALLEL) == 1
        assert b.get_monthly_usage(ProviderName.PARALLEL) == 0.0
        assert b.get_usage_count(ProviderName.PARALLEL) == 0
        assert b.get_remaining_budget(ProviderName.PARALLEL) == 5000.0
        assert b.daily_pace(ProviderName.PARALLEL) == 5000.0 / 30.0

    def test_tier3_daily_pace_is_inf(self):
        """Tier-3 providers should have infinite daily pace — never over pace."""
        from argus.broker.budgets import BudgetTracker

        b = BudgetTracker()
        b.set_budget(ProviderName.SERPER, 100.0)
        assert b.daily_pace(ProviderName.SERPER) == float("inf")
        assert b.is_over_pace(ProviderName.SERPER) is False
        # Even with heavy usage today, still not "over pace"
        for _ in range(50):
            b.record_usage(ProviderName.SERPER)
        assert b.is_over_pace(ProviderName.SERPER) is False

    def test_tier3_exhaustion_is_sole_gate(self):
        """Tier-3 providers only skipped when truly exhausted."""
        from argus.broker.budgets import BudgetTracker

        b = BudgetTracker()
        b.set_budget(ProviderName.SERPER, 5.0)
        for _ in range(4):
            b.record_usage(ProviderName.SERPER)
        assert b.is_budget_exhausted(ProviderName.SERPER) is False
        b.record_usage(ProviderName.SERPER)
        assert b.is_budget_exhausted(ProviderName.SERPER) is True

    def test_tier3_reset_clears_lifetime(self):
        """Manual reset should clear all lifetime usage for tier-3."""
        from argus.broker.budgets import BudgetTracker

        b = BudgetTracker()
        b.set_budget(ProviderName.SERPER, 10.0)
        for _ in range(10):
            b.record_usage(ProviderName.SERPER)
        assert b.is_budget_exhausted(ProviderName.SERPER) is True
        cleared = b.reset_lifetime_usage(ProviderName.SERPER)
        assert cleared == 10
        assert b.is_budget_exhausted(ProviderName.SERPER) is False
        assert b.get_monthly_usage(ProviderName.SERPER) == 0.0

    @pytest.mark.asyncio
    async def test_provider_executor_records_actual_valyu_cost(self):
        from argus.broker.budgets import BudgetTracker
        from argus.broker.execution import ProviderExecutor
        from argus.broker.health import HealthTracker

        provider = StubProvider(
            name=ProviderName.VALYU,
            results=[
                SearchResult(url="https://example.com", title="Result", snippet="ok")
            ],
            trace=ProviderTrace(
                provider=ProviderName.VALYU,
                status="success",
                credit_info={"cost_usd": 0.0042},
            ),
        )
        budget = BudgetTracker()
        budget.set_budget(ProviderName.VALYU, 10.0)
        executor = ProviderExecutor(
            providers={ProviderName.VALYU: provider},
            health_tracker=HealthTracker(),
            budget_tracker=budget,
        )

        outcome = await executor.execute(
            SearchQuery(query="cost", providers=[ProviderName.VALYU]),
            [ProviderName.VALYU],
        )

        assert outcome.live_providers_used == 1
        assert budget.get_monthly_usage(ProviderName.VALYU) == 0.0042
        assert outcome.traces[0].budget_remaining == 9.9958


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
        assert len(broker._providers) == 14  # added yahoo + wolfram

    def test_create_broker_honors_disabled_duckduckgo(self, monkeypatch):
        from argus.broker.router import create_broker
        from argus.config import reset_config

        monkeypatch.setenv("ARGUS_DUCKDUCKGO_ENABLED", "false")
        reset_config()

        broker = create_broker()

        assert broker._providers[ProviderName.DUCKDUCKGO].is_available() is False

    @pytest.mark.asyncio
    async def test_free_providers_always_queried(self, monkeypatch):
        """Tier 0 providers are always queried; paid fallback runs if free results are insufficient."""
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        searxng = StubProvider(
            name=ProviderName.SEARXNG,
            results=[
                SearchResult(
                    url="https://example.com/1",
                    title="Result 1",
                    snippet="ok",
                    provider=ProviderName.SEARXNG,
                )
            ],
        )
        ddg = StubProvider(
            name=ProviderName.DUCKDUCKGO,
            results=[
                SearchResult(
                    url="https://example.com/2",
                    title="Result 2",
                    snippet="ok",
                    provider=ProviderName.DUCKDUCKGO,
                )
            ],
        )
        paid = StubProvider(
            name=ProviderName.BRAVE,
            results=[
                SearchResult(
                    url="https://backup.com",
                    title="Backup",
                    snippet="backup",
                    provider=ProviderName.BRAVE,
                )
            ],
        )

        broker = SearchBroker(
            providers={
                ProviderName.SEARXNG: searxng,
                ProviderName.DUCKDUCKGO: ddg,
                ProviderName.BRAVE: paid,
            },
        )

        response = await broker.search(
            SearchQuery(
                query="always use free",
                mode=SearchMode.DISCOVERY,
                max_results=5,
                providers=[
                    ProviderName.SEARXNG,
                    ProviderName.DUCKDUCKGO,
                    ProviderName.BRAVE,
                ],
            )
        )

        assert searxng.calls == 1
        assert ddg.calls == 1
        # Free results are below max_results, so the paid fallback is still useful.
        assert paid.calls == 1
        assert response.total_results == 3

    @pytest.mark.asyncio
    async def test_paid_provider_skipped_when_free_results_satisfy_query(
        self, monkeypatch
    ):
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        free_results = [
            SearchResult(
                url=f"https://example.com/{i}",
                title=f"Free {i}",
                snippet="ok",
                provider=ProviderName.DUCKDUCKGO,
            )
            for i in range(5)
        ]
        free = StubProvider(name=ProviderName.DUCKDUCKGO, results=free_results)
        paid = StubProvider(
            name=ProviderName.VALYU,
            results=[
                SearchResult(
                    url="https://paid.example.com",
                    title="Paid",
                    snippet="ok",
                    provider=ProviderName.VALYU,
                )
            ],
        )
        broker = SearchBroker(
            providers={ProviderName.DUCKDUCKGO: free, ProviderName.VALYU: paid}
        )

        response = await broker.search(
            SearchQuery(
                query="free is enough", mode=SearchMode.DISCOVERY, max_results=5
            )
        )

        assert free.calls == 1
        assert paid.calls == 0
        assert any(
            trace.provider == ProviderName.VALYU
            and trace.error == "free results satisfied query"
            for trace in response.traces
        )

    @pytest.mark.asyncio
    async def test_paid_provider_skipped_when_over_pace(self, monkeypatch):
        """Paid providers are skipped when today's usage exceeds daily pace."""
        from argus.broker.budgets import BudgetTracker
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        free = StubProvider(
            name=ProviderName.SEARXNG,
            results=[
                SearchResult(
                    url="https://example.com",
                    title="Free",
                    snippet="ok",
                    provider=ProviderName.SEARXNG,
                )
            ],
        )
        paid = StubProvider(
            name=ProviderName.BRAVE,
            results=[
                SearchResult(
                    url="https://backup.com",
                    title="Paid",
                    snippet="ok",
                    provider=ProviderName.BRAVE,
                )
            ],
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
            results=[
                SearchResult(
                    url="https://example.com",
                    title="Free",
                    snippet="ok",
                    provider=ProviderName.SEARXNG,
                )
            ],
        )
        paid = StubProvider(
            name=ProviderName.BRAVE,
            results=[
                SearchResult(
                    url="https://backup.com",
                    title="Paid",
                    snippet="ok",
                    provider=ProviderName.BRAVE,
                )
            ],
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
                max_results=2,
                providers=[ProviderName.SEARXNG, ProviderName.BRAVE],
            )
        )

        assert free.calls == 1
        assert paid.calls == 1
        assert response.total_results == 2

    @pytest.mark.asyncio
    async def test_one_time_credits_used_when_available(self, monkeypatch):
        """Tier 3 (one-time) providers are queried when budget remains — no pacing."""
        from argus.broker.budgets import BudgetTracker
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
            lambda self, query, response: None,
        )

        free = StubProvider(
            name=ProviderName.SEARXNG,
            results=[
                SearchResult(
                    url="https://example.com",
                    title="Free",
                    snippet="ok",
                    provider=ProviderName.SEARXNG,
                )
            ],
        )
        onetime = StubProvider(
            name=ProviderName.SERPER,
            results=[
                SearchResult(
                    url="https://one.com",
                    title="One",
                    snippet="ok",
                    provider=ProviderName.SERPER,
                )
            ],
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
                query="use one-time credits",
                mode=SearchMode.DISCOVERY,
                providers=[ProviderName.SEARXNG, ProviderName.SERPER],
            )
        )

        # Tier 3 providers should NOT be paced — only exhausted
        assert free.calls == 1
        assert onetime.calls == 1
        assert response.total_results == 2

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
            results=[
                SearchResult(url="https://fallback.com", title="Fallback", snippet="ok")
            ],
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
            results=[
                SearchResult(url="https://backup.com", title="Backup", snippet="ok")
            ],
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
            lambda query, response: (_ for _ in ()).throw(
                RuntimeError("persist failed")
            ),
        )

        primary = StubProvider(
            name=ProviderName.SEARXNG,
            results=[
                SearchResult(url="https://example.com", title="Result", snippet="ok")
            ],
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

    @pytest.mark.asyncio
    async def test_session_search_can_compute_attribution(self, monkeypatch):
        from argus.broker.router import SearchBroker

        monkeypatch.setattr(
            "argus.persistence.db.persist_search",
            lambda query, response: "run-id",
        )

        primary = StubProvider(
            name=ProviderName.SEARXNG,
            results=[
                SearchResult(
                    url="https://example.com",
                    title="Result",
                    snippet="ok",
                    provider=ProviderName.SEARXNG,
                )
            ],
        )
        broker = SearchBroker(providers={ProviderName.SEARXNG: primary})

        response, sid = await broker.search_with_session(
            SearchQuery(
                query="session attribution",
                mode=SearchMode.DISCOVERY,
                providers=[ProviderName.SEARXNG],
            ),
            session_id="attr-session",
            compute_attribution=True,
        )

        assert sid == "attr-session"
        assert response.results[0].score_attribution == {
            ProviderName.SEARXNG.value: response.results[0].score
        }


# --- FreeOnly ---


class TestFreeOnly:
    def test_search_query_free_only_defaults_false(self):
        from argus.models import SearchQuery

        q = SearchQuery(query="test")
        assert q.free_only is False

    def test_search_query_free_only_can_be_set(self):
        from argus.models import SearchQuery

        q = SearchQuery(query="test", free_only=True)
        assert q.free_only is True

    @pytest.mark.asyncio
    async def test_free_only_skips_paid_providers_even_when_results_insufficient(
        self, monkeypatch
    ):
        """free_only=True must skip tier > 0 providers regardless of result count."""
        from argus.broker.budgets import BudgetTracker
        from argus.broker.execution import ProviderExecutor
        from argus.broker.health import HealthTracker

        free_provider = StubProvider(
            name=ProviderName.DUCKDUCKGO,
            results=[
                SearchResult(
                    url="https://free.com/1",
                    title="Free 1",
                    snippet="ok",
                    provider=ProviderName.DUCKDUCKGO,
                )
            ],
        )
        paid_provider = StubProvider(
            name=ProviderName.BRAVE,
            results=[
                SearchResult(
                    url="https://paid.com/1",
                    title="Paid 1",
                    snippet="ok",
                    provider=ProviderName.BRAVE,
                )
            ],
        )

        executor = ProviderExecutor(
            providers={
                ProviderName.DUCKDUCKGO: free_provider,
                ProviderName.BRAVE: paid_provider,
            },
            health_tracker=HealthTracker(),
            budget_tracker=BudgetTracker(),
        )

        q = SearchQuery(query="test", free_only=True, max_results=10)
        outcome = await executor.execute(
            q,
            [ProviderName.DUCKDUCKGO, ProviderName.BRAVE],
        )

        assert free_provider.calls == 1
        assert paid_provider.calls == 0
        assert any(
            t.provider == ProviderName.BRAVE and t.error == "free_only mode"
            for t in outcome.traces
        )

    @pytest.mark.asyncio
    async def test_free_only_false_does_not_gate_paid_providers(self, monkeypatch):
        """free_only=False (default) must not affect paid provider execution."""
        from argus.broker.budgets import BudgetTracker
        from argus.broker.execution import ProviderExecutor
        from argus.broker.health import HealthTracker

        free_provider = StubProvider(
            name=ProviderName.DUCKDUCKGO,
            results=[
                SearchResult(
                    url="https://free.com/1",
                    title="Free 1",
                    snippet="ok",
                    provider=ProviderName.DUCKDUCKGO,
                )
            ],
        )
        paid_provider = StubProvider(
            name=ProviderName.BRAVE,
            results=[
                SearchResult(
                    url="https://paid.com/1",
                    title="Paid 1",
                    snippet="ok",
                    provider=ProviderName.BRAVE,
                )
            ],
        )

        executor = ProviderExecutor(
            providers={
                ProviderName.DUCKDUCKGO: free_provider,
                ProviderName.BRAVE: paid_provider,
            },
            health_tracker=HealthTracker(),
            budget_tracker=BudgetTracker(),
        )

        q = SearchQuery(query="test", free_only=False, max_results=10)
        await executor.execute(q, [ProviderName.DUCKDUCKGO, ProviderName.BRAVE])

        assert free_provider.calls == 1
        assert paid_provider.calls == 1


# --- Remote egress routing ---


def _make_executor(reachability=None, egress_nodes=None):
    """Build a ProviderExecutor with mocked providers for egress routing tests."""
    from argus.broker.execution import ProviderExecutor
    from argus.broker.health import HealthTracker
    from argus.broker.budgets import BudgetTracker

    mock_provider = StubProvider(
        name=ProviderName.YAHOO,
        results=[
            SearchResult(
                url="https://yahoo.com/r",
                title="R",
                snippet="s",
                provider=ProviderName.YAHOO,
            )
        ],
    )

    return (
        ProviderExecutor(
            providers={ProviderName.YAHOO: mock_provider},
            health_tracker=HealthTracker(),
            budget_tracker=BudgetTracker(),
            reachability=reachability,
            egress_nodes=egress_nodes or {},
        ),
        mock_provider,
    )


@pytest.mark.asyncio
async def test_executor_routes_to_remote_when_local_blocked():
    from argus.broker.reachability import ReachabilityMatrix
    from argus.config import EgressNode

    matrix = ReachabilityMatrix()
    matrix.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    matrix.update_probe("oci-dev", ProviderName.YAHOO, reachable=True, latency_ms=100)

    node = EgressNode(name="oci-dev", url="http://worker:8273", shared_secret="s")
    executor, local_provider = _make_executor(
        reachability=matrix,
        egress_nodes={"oci-dev": node},
    )

    fake_result = SearchResult(
        url="https://yahoo.com/r", title="R", snippet="s", provider=ProviderName.YAHOO
    )
    fake_trace = ProviderTrace(
        provider=ProviderName.YAHOO,
        status="success",
        results_count=1,
        latency_ms=90,
        egress="oci-dev",
    )

    # Patch RemoteProviderClient to return our fake result
    import argus.broker.remote_provider as rp_module

    original = rp_module.RemoteProviderClient

    class FakeRemote:
        def __init__(self, *a, **kw):
            pass

        async def search(self, q):
            return [fake_result], fake_trace

    rp_module.RemoteProviderClient = FakeRemote
    try:
        query = SearchQuery(query="test", mode=SearchMode.DISCOVERY, max_results=5)
        outcome = await executor.execute(query, [ProviderName.YAHOO])
    finally:
        rp_module.RemoteProviderClient = original

    assert outcome.live_providers_used == 1
    assert "yahoo" in outcome.provider_results
    assert local_provider.calls == 0  # local should NOT have been called


@pytest.mark.asyncio
async def test_executor_does_not_delegate_paid_provider_to_egress_worker():
    from unittest.mock import MagicMock, patch

    from argus.broker.budgets import BudgetTracker
    from argus.broker.execution import ProviderExecutor
    from argus.broker.health import HealthTracker
    from argus.broker.reachability import ReachabilityMatrix
    from argus.config import EgressNode

    matrix = ReachabilityMatrix()
    matrix.update_probe("local", ProviderName.BRAVE, reachable=False, latency_ms=0)
    matrix.update_probe("homelab", ProviderName.BRAVE, reachable=True, latency_ms=10)
    budgets = BudgetTracker()
    budgets.set_budget(ProviderName.BRAVE, 10.0)
    spend = MagicMock()
    provider = StubProvider(name=ProviderName.BRAVE, results=[])
    executor = ProviderExecutor(
        providers={ProviderName.BRAVE: provider},
        health_tracker=HealthTracker(),
        budget_tracker=budgets,
        reachability=matrix,
        egress_nodes={
            "homelab": EgressNode(
                name="homelab",
                url="http://worker:8273",
                shared_secret="s",
            )
        },
        spend_repository=spend,
    )

    with patch("argus.broker.remote_provider.RemoteProviderClient") as remote:
        outcome = await executor.execute(
            SearchQuery(query="paid", caller="maya"),
            [ProviderName.BRAVE],
        )

    assert outcome.traces[0].status == "skipped"
    assert "paid providers" in outcome.traces[0].error
    remote.assert_not_called()
    spend.reserve.assert_not_called()


@pytest.mark.asyncio
async def test_executor_skips_when_no_egress_available():
    from argus.broker.reachability import ReachabilityMatrix

    matrix = ReachabilityMatrix()
    matrix.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    # No workers registered at all

    executor, local_provider = _make_executor(reachability=matrix, egress_nodes={})

    query = SearchQuery(query="test", mode=SearchMode.DISCOVERY, max_results=5)
    outcome = await executor.execute(query, [ProviderName.YAHOO])

    assert outcome.live_providers_used == 0
    skipped = [t for t in outcome.traces if t.status == "skipped"]
    assert any("no reachable egress" in (t.error or "") for t in skipped)


@pytest.mark.asyncio
async def test_executor_uses_local_when_reachable():
    from argus.broker.reachability import ReachabilityMatrix

    matrix = ReachabilityMatrix()
    matrix.update_probe("local", ProviderName.YAHOO, reachable=True, latency_ms=50)

    executor, local_provider = _make_executor(reachability=matrix)

    fake_result = SearchResult(
        url="u", title="t", snippet="s", provider=ProviderName.YAHOO
    )
    fake_trace = ProviderTrace(
        provider=ProviderName.YAHOO, status="success", results_count=1
    )

    async def patched_search(q):
        local_provider.calls += 1
        return [fake_result], fake_trace

    local_provider.search = patched_search

    query = SearchQuery(query="test", mode=SearchMode.DISCOVERY, max_results=5)
    await executor.execute(query, [ProviderName.YAHOO])

    assert local_provider.calls == 1
