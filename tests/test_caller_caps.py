"""Tests for per-caller provider tier caps in ProviderExecutor."""

import pytest

from argus.broker.budgets import BudgetTracker
from argus.broker.execution import ProviderExecutor, caller_tier_cap
from argus.broker.health import HealthTracker
from argus.models import ProviderName, SearchMode, SearchQuery, SearchResult, ProviderTrace


class _StubProvider:
    def __init__(self, name: ProviderName):
        self._name = name
        self.calls = 0

    def is_available(self) -> bool:
        return True

    async def search(self, query):
        self.calls += 1
        return [
            SearchResult(
                url="https://example.com",
                title="t",
                snippet="s",
                domain="example.com",
                provider=self._name.value,
            )
        ], ProviderTrace(provider=self._name, status="success", results_count=1)


def _executor(caps: dict[str, int]) -> ProviderExecutor:
    providers = {
        ProviderName.DUCKDUCKGO: _StubProvider(ProviderName.DUCKDUCKGO),  # tier 0
        ProviderName.SERPER: _StubProvider(ProviderName.SERPER),          # tier 3
    }
    return ProviderExecutor(
        providers=providers,
        health_tracker=HealthTracker(),
        budget_tracker=BudgetTracker(persist_path=None),
        caller_tier_caps=caps,
    )


class TestCallerTierCapHelper:
    def test_no_caller_means_no_cap(self):
        assert caller_tier_cap("", {"clio*": 1}) is None

    def test_no_caps_means_no_cap(self):
        assert caller_tier_cap("clio-lane-b", {}) is None

    def test_fnmatch_pattern_matches(self):
        assert caller_tier_cap("clio-lane-b", {"clio*": 1}) == 1

    def test_exact_match(self):
        assert caller_tier_cap("hermes", {"hermes": 1}) == 1

    def test_non_matching_caller_uncapped(self):
        assert caller_tier_cap("interactive-cli", {"clio*": 1}) is None

    def test_most_restrictive_wins(self):
        assert caller_tier_cap("clio-x", {"clio*": 1, "clio-x": 0}) == 0


class TestExecutorEnforcement:
    @pytest.mark.asyncio
    async def test_capped_caller_skips_tier3_provider(self):
        executor = _executor({"clio*": 1})
        query = SearchQuery(
            query="q", mode=SearchMode.DISCOVERY, max_results=10, caller="clio-lane-b"
        )
        outcome = await executor.execute(
            query, [ProviderName.DUCKDUCKGO, ProviderName.SERPER]
        )
        serper_traces = [t for t in outcome.traces if t.provider == ProviderName.SERPER]
        assert serper_traces and serper_traces[0].status == "skipped"
        assert "caller tier cap" in (serper_traces[0].error or "")
        assert executor._providers[ProviderName.DUCKDUCKGO].calls == 1
        assert executor._providers[ProviderName.SERPER].calls == 0

    @pytest.mark.asyncio
    async def test_uncapped_caller_reaches_tier3(self):
        executor = _executor({"clio*": 1})
        query = SearchQuery(
            query="q", mode=SearchMode.DISCOVERY, max_results=50, caller="someone-else"
        )
        outcome = await executor.execute(query, [ProviderName.SERPER])
        serper_traces = [t for t in outcome.traces if t.provider == ProviderName.SERPER]
        assert serper_traces and serper_traces[0].status != "skipped"
        assert executor._providers[ProviderName.SERPER].calls == 1
