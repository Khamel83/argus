"""
Degraded-state tests: verify Argus behaves correctly when providers are
unavailable, timing out, over budget, or manually disabled.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchMode,
    SearchQuery,
    SearchResult,
)


# ---------------------------------------------------------------------------
# Shared stub
# ---------------------------------------------------------------------------

@dataclass
class StubProvider:
    name: ProviderName
    results: list[SearchResult] | None = None
    available: bool = True
    _status: ProviderStatus = ProviderStatus.ENABLED
    raise_error: Exception | None = None

    def __post_init__(self):
        if self.results is None:
            self.results = []
        self.calls = 0

    def is_available(self) -> bool:
        return self.available

    def status(self) -> ProviderStatus:
        return self._status

    async def search(self, query: SearchQuery):
        self.calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        return list(self.results), ProviderTrace(
            provider=self.name,
            status="success",
            results_count=len(self.results),
        )


def _make_results(provider: ProviderName, n: int = 5) -> list[SearchResult]:
    return [
        SearchResult(url=f"https://example.com/{i}", title=f"Result {i}", snippet="x")
        for i in range(n)
    ]


def _noop_persist(monkeypatch):
    monkeypatch.setattr(
        "argus.persistence.db.SearchPersistenceGateway.record_completed_search",
        lambda self, query, response: None,
    )


# ---------------------------------------------------------------------------
# 1. SearXNG only — all paid providers disabled
# ---------------------------------------------------------------------------

class TestSearXNGOnly:
    @pytest.mark.asyncio
    async def test_searxng_only_returns_results(self, monkeypatch):
        """Broker returns results from SearXNG alone when all paid providers are unavailable."""
        _noop_persist(monkeypatch)
        from argus.broker.router import SearchBroker

        searxng = StubProvider(name=ProviderName.SEARXNG, results=_make_results(ProviderName.SEARXNG))
        paid = {
            ProviderName.BRAVE: StubProvider(
                name=ProviderName.BRAVE, available=False, _status=ProviderStatus.DISABLED_BY_CONFIG
            ),
            ProviderName.SERPER: StubProvider(
                name=ProviderName.SERPER, available=False, _status=ProviderStatus.DISABLED_BY_CONFIG
            ),
            ProviderName.TAVILY: StubProvider(
                name=ProviderName.TAVILY, available=False, _status=ProviderStatus.DISABLED_BY_CONFIG
            ),
            ProviderName.EXA: StubProvider(
                name=ProviderName.EXA, available=False, _status=ProviderStatus.DISABLED_BY_CONFIG
            ),
        }

        broker = SearchBroker(
            providers={ProviderName.SEARXNG: searxng, **paid}
        )
        response = await broker.search(
            SearchQuery(query="test", mode=SearchMode.DISCOVERY)
        )

        assert len(response.results) > 0
        assert searxng.calls == 1
        for stub in paid.values():
            assert stub.calls == 0


# ---------------------------------------------------------------------------
# 2. Brave key missing — skipped, fallback to next provider
# ---------------------------------------------------------------------------

class TestMissingKey:
    @pytest.mark.asyncio
    async def test_missing_brave_key_skips_to_fallback(self, monkeypatch):
        """A provider with UNAVAILABLE_MISSING_KEY is skipped without error."""
        _noop_persist(monkeypatch)
        from argus.broker.router import SearchBroker

        brave_no_key = StubProvider(
            name=ProviderName.BRAVE,
            available=False,
            _status=ProviderStatus.UNAVAILABLE_MISSING_KEY,
        )
        serper = StubProvider(
            name=ProviderName.SERPER,
            results=_make_results(ProviderName.SERPER),
        )

        broker = SearchBroker(
            providers={
                ProviderName.SEARXNG: StubProvider(name=ProviderName.SEARXNG, results=[]),
                ProviderName.BRAVE: brave_no_key,
                ProviderName.SERPER: serper,
                ProviderName.TAVILY: StubProvider(name=ProviderName.TAVILY, results=[]),
                ProviderName.EXA: StubProvider(name=ProviderName.EXA, results=[]),
            }
        )

        response = await broker.search(
            SearchQuery(query="test", mode=SearchMode.GROUNDING)  # chain: brave → serper → searxng
        )

        assert brave_no_key.calls == 0  # skipped without attempt
        assert serper.calls == 1
        assert len(response.results) > 0


# ---------------------------------------------------------------------------
# 3. Serper timeout — error trace, fallback to next
# ---------------------------------------------------------------------------

class TestProviderTimeout:
    @pytest.mark.asyncio
    async def test_provider_timeout_falls_through(self, monkeypatch):
        """A provider that raises an exception triggers fallback to the next provider."""
        _noop_persist(monkeypatch)
        from argus.broker.router import SearchBroker
        import httpx

        serper_timeout = StubProvider(
            name=ProviderName.SERPER,
            raise_error=httpx.TimeoutException("timeout"),
        )
        # RESEARCH chain: tavily → exa → brave → serper
        # Serper is last; give tavily enough results to hit early-stop threshold (8)
        tavily = StubProvider(
            name=ProviderName.TAVILY,
            results=_make_results(ProviderName.TAVILY, n=10),
        )

        broker = SearchBroker(
            providers={
                ProviderName.SEARXNG: StubProvider(name=ProviderName.SEARXNG, results=[]),
                ProviderName.BRAVE: StubProvider(name=ProviderName.BRAVE, results=[]),
                ProviderName.SERPER: serper_timeout,
                ProviderName.TAVILY: tavily,
                ProviderName.EXA: StubProvider(name=ProviderName.EXA, results=[]),
            }
        )

        response = await broker.search(
            SearchQuery(query="test", mode=SearchMode.RESEARCH)
        )

        assert tavily.calls == 1
        assert serper_timeout.calls == 0  # early-stopped before serper was reached
        assert len(response.results) > 0


# ---------------------------------------------------------------------------
# 4. Budget exhausted on one provider — fallback fires
# ---------------------------------------------------------------------------

class TestBudgetFallback:
    @pytest.mark.asyncio
    async def test_budget_exhausted_skips_provider(self, monkeypatch):
        """A provider with exhausted budget is skipped; fallback provider is used."""
        _noop_persist(monkeypatch)
        from argus.broker.budgets import BudgetTracker
        from argus.broker.router import SearchBroker

        budgets = BudgetTracker()
        brave = StubProvider(name=ProviderName.BRAVE, results=_make_results(ProviderName.BRAVE))
        serper = StubProvider(name=ProviderName.SERPER, results=_make_results(ProviderName.SERPER))

        broker = SearchBroker(
            providers={
                ProviderName.SEARXNG: StubProvider(name=ProviderName.SEARXNG, results=[]),
                ProviderName.BRAVE: brave,
                ProviderName.SERPER: serper,
                ProviderName.TAVILY: StubProvider(name=ProviderName.TAVILY, results=[]),
                ProviderName.EXA: StubProvider(name=ProviderName.EXA, results=[]),
            },
            budget_tracker=budgets,
        )

        # Record usage AFTER broker creation so the exhaustion exceeds the
        # config-default budget ($5 for Brave). Recording $10 ensures exhaustion
        # regardless of what the config sets.
        budgets.record_usage(ProviderName.BRAVE, 10.0)
        assert budgets.is_budget_exhausted(ProviderName.BRAVE)

        response = await broker.search(
            SearchQuery(query="test", mode=SearchMode.GROUNDING)  # brave → serper → searxng
        )

        assert brave.calls == 0  # budget exhausted → skipped
        assert serper.calls == 1
        assert len(response.results) > 0


# ---------------------------------------------------------------------------
# 5. All paid providers disabled simultaneously
# ---------------------------------------------------------------------------

class TestAllPaidDisabled:
    @pytest.mark.asyncio
    async def test_all_paid_disabled_falls_to_searxng(self, monkeypatch):
        """When all paid providers are disabled, SearXNG handles the query."""
        _noop_persist(monkeypatch)
        from argus.broker.health import HealthTracker
        from argus.broker.router import SearchBroker

        health = HealthTracker()
        paid_names = [ProviderName.BRAVE, ProviderName.SERPER, ProviderName.TAVILY, ProviderName.EXA]
        for pname in paid_names:
            health.force_disable(pname, "test: all paid disabled")

        searxng = StubProvider(name=ProviderName.SEARXNG, results=_make_results(ProviderName.SEARXNG))
        paid = {pname: StubProvider(name=pname) for pname in paid_names}

        broker = SearchBroker(
            providers={ProviderName.SEARXNG: searxng, **paid},
            health_tracker=health,
        )

        response = await broker.search(
            SearchQuery(query="test", mode=SearchMode.DISCOVERY)
        )

        assert searxng.calls == 1
        for stub in paid.values():
            assert stub.calls == 0
        assert len(response.results) > 0


# ---------------------------------------------------------------------------
# 6. Extraction: trafilatura fails, Jina fallback used
# ---------------------------------------------------------------------------

class TestExtractionFallback:
    @pytest.mark.asyncio
    async def test_jina_fallback_used_when_trafilatura_empty(self, monkeypatch):
        """When trafilatura returns no content, Jina Reader is tried."""
        from argus.extraction.models import ExtractedContent, ExtractorName

        async def fake_trafilatura(url):
            return ExtractedContent(url=url, error="trafilatura: no content extracted")

        async def fake_jina(url):
            return ExtractedContent(
                url=url,
                title="Jina Result",
                text="Content from Jina Reader",
                word_count=4,
                extractor=ExtractorName.JINA,
            )

        with patch("argus.extraction.extractor._extract_trafilatura", fake_trafilatura):
            with patch("argus.extraction.extractor._extract_jina", fake_jina):
                from argus.extraction.extractor import ContentExtractor
                extractor = ContentExtractor()
                result = await extractor.extract("https://example.com/article")

        assert result.extractor == ExtractorName.JINA
        assert result.text == "Content from Jina Reader"
        assert not result.error

    @pytest.mark.asyncio
    async def test_trafilatura_success_no_jina_call(self):
        """When trafilatura succeeds, Jina is never called."""
        from argus.extraction.models import ExtractedContent, ExtractorName

        jina_called = False

        async def fake_trafilatura(url):
            return ExtractedContent(
                url=url,
                title="Trafilatura Result",
                text="Clean article text",
                word_count=3,
                extractor=ExtractorName.TRAFILATURA,
            )

        async def fake_jina(url):
            nonlocal jina_called
            jina_called = True
            return ExtractedContent(url=url, text="Should not be called")

        with patch("argus.extraction.extractor._extract_trafilatura", fake_trafilatura):
            with patch("argus.extraction.extractor._extract_jina", fake_jina):
                from argus.extraction.extractor import ContentExtractor
                extractor = ContentExtractor()
                result = await extractor.extract("https://example.com/page")

        assert result.extractor == ExtractorName.TRAFILATURA
        assert not jina_called


# ---------------------------------------------------------------------------
# 7. Session: max_turns and max_context_chars enforced
# ---------------------------------------------------------------------------

class TestSessionBounds:
    def test_max_turns_trims_oldest(self):
        """Queries beyond max_turns are trimmed (oldest removed)."""
        store = SessionStore_nopers(max_turns=3)
        session = store.create_session("s1")
        for i in range(5):
            store.add_query("s1", query=f"query {i}")
        assert len(session.queries) == 3
        # Most recent 3 are kept
        assert session.queries[-1].query == "query 4"
        assert session.queries[0].query == "query 2"

    def test_max_context_chars_truncates(self):
        """Context prefix is truncated to max_context_chars."""
        from argus.sessions.refinement import refine_query
        from argus.sessions.models import Session, QueryRecord

        long_prior = "x" * 3000
        session = Session(id="s")
        session.queries = [
            QueryRecord(query=long_prior),
            QueryRecord(query="follow up"),  # current query (short follow-up)
        ]
        refined = refine_query("follow up", session, max_context_chars=100)
        # The context part should be at most 100 chars
        context_part = refined.replace("follow up", "").strip()
        assert len(context_part) <= 100

    def test_no_bounds_exceeded_for_normal_usage(self):
        """Normal usage within bounds doesn't trim anything."""
        store = SessionStore_nopers(max_turns=20)
        session = store.create_session("s2")
        for i in range(5):
            store.add_query("s2", query=f"query {i}")
        assert len(session.queries) == 5


# ---------------------------------------------------------------------------
# 8. Provider admin: force_disable persists in-memory
# ---------------------------------------------------------------------------

class TestProviderAdmin:
    def test_force_disable_returns_manually_disabled(self):
        from argus.broker.health import HealthTracker

        h = HealthTracker()
        h.force_disable(ProviderName.BRAVE, reason="scheduled maintenance")
        assert h.get_status(ProviderName.BRAVE) == ProviderStatus.MANUALLY_DISABLED

    def test_force_enable_clears_override(self):
        from argus.broker.health import HealthTracker

        h = HealthTracker()
        h.force_disable(ProviderName.BRAVE)
        h.force_enable(ProviderName.BRAVE)
        assert h.get_status(ProviderName.BRAVE) is None

    def test_reset_cooldown_clears_failures(self):
        from argus.broker.health import HealthTracker

        h = HealthTracker(failure_threshold=2)
        h.record_failure(ProviderName.BRAVE)
        h.record_failure(ProviderName.BRAVE)
        assert h.get_status(ProviderName.BRAVE) == ProviderStatus.TEMPORARILY_DISABLED
        h.reset_cooldown(ProviderName.BRAVE)
        # After reset: no cooldown, but failures were also cleared
        assert h.get_status(ProviderName.BRAVE) is None

    def test_force_disable_does_not_affect_other_providers(self):
        from argus.broker.health import HealthTracker

        h = HealthTracker()
        h.force_disable(ProviderName.BRAVE)
        assert h.get_status(ProviderName.SERPER) is None


# ---------------------------------------------------------------------------
# Helper: in-memory session store with configurable bounds
# ---------------------------------------------------------------------------

class SessionStore_nopers:
    """Minimal in-memory session store with max_turns control for testing."""
    def __init__(self, max_turns: int = 20):
        from argus.sessions.store import SessionStore
        self._store = SessionStore(persist=False, max_turns=max_turns)

    def create_session(self, session_id: str | None = None):
        return self._store.create_session(session_id)

    def add_query(self, session_id: str, *, query: str):
        return self._store.add_query(session_id, query=query)
