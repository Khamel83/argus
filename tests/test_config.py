"""Tests for config, models, and logging."""


class TestConfig:
    def test_load_config_defaults(self):
        from argus.config import load_config
        cfg = load_config()
        assert cfg.env == "development"
        assert cfg.log_level == "INFO"
        assert cfg.cache_ttl_hours == 168
        assert cfg.searxng.enabled is True
        assert cfg.brave.enabled is True  # enabled by default per .env.example
        assert cfg.serper.enabled is True

    def test_load_config_from_env(self, monkeypatch):
        monkeypatch.setenv("ARGUS_BRAVE_API_KEY", "test-key")
        monkeypatch.setenv("ARGUS_BRAVE_ENABLED", "true")
        from argus.config import load_config
        cfg = load_config()
        assert cfg.brave.api_key == "test-key"
        assert cfg.brave.enabled is True

    def test_load_config_uses_secret_fallbacks(self):
        from argus.config import SecretsResolver, load_config

        class StubSecrets(SecretsResolver):
            def get(self, key: str) -> str:
                if key == "ARGUS_BRAVE_API_KEY":
                    return "secret-key"
                if key == "DB_URL":
                    return "sqlite:///secret.db"
                return ""

        cfg = load_config(environ={}, secrets_resolver=StubSecrets())

        assert cfg.brave.api_key == "secret-key"
        assert cfg.db_url == "sqlite:///secret.db"

    def test_get_config_singleton(self):
        from argus.config import get_config, reset_config
        reset_config()
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_force_reload_rebuilds_singleton(self, monkeypatch):
        from argus.config import get_config, reset_config

        reset_config()
        monkeypatch.setenv("ARGUS_ENV", "development")
        c1 = get_config()
        monkeypatch.setenv("ARGUS_ENV", "production")
        c2 = get_config(force_reload=True)

        assert c1 is not c2
        assert c2.env == "production"


class TestModels:
    def test_search_mode(self):
        from argus.models import SearchMode
        assert SearchMode.RECOVERY.value == "recovery"
        assert SearchMode.DISCOVERY.value == "discovery"

    def test_provider_name(self):
        from argus.models import ProviderName
        assert ProviderName.SEARXNG.value == "searxng"

    def test_provider_status(self):
        from argus.models import ProviderStatus
        assert ProviderStatus.HEALTHY.value == "healthy"
        assert ProviderStatus.BUDGET_EXHAUSTED.value == "budget_exhausted"

    def test_search_result(self):
        from argus.models import SearchResult
        r = SearchResult(url="https://example.com", title="Example", snippet="A test page")
        assert r.url == "https://example.com"
        assert r.score == 0.0
        assert r.metadata == {}

    def test_search_query(self):
        from argus.models import SearchQuery, SearchMode
        q = SearchQuery(query="test", mode=SearchMode.GROUNDING, max_results=5)
        assert q.mode == SearchMode.GROUNDING
        assert q.max_results == 5

    def test_provider_trace(self):
        from argus.models import ProviderTrace, ProviderName
        t = ProviderTrace(provider=ProviderName.BRAVE, status="success", results_count=10)
        assert t.provider == ProviderName.BRAVE

    def test_search_response(self):
        from argus.models import SearchResponse, SearchMode
        resp = SearchResponse(query="test", mode=SearchMode.DISCOVERY, results=[])
        assert resp.total_results == 0
        assert resp.cached is False


class TestLogging:
    def test_setup_logging(self):
        from argus.logging import setup_logging, get_logger
        logger = setup_logging("DEBUG")
        assert logger.name == "argus"
        child = get_logger("broker")
        assert child.name == "argus.broker"

    def test_get_logger(self):
        from argus.logging import get_logger
        log = get_logger("test")
        assert log.name == "argus.test"
