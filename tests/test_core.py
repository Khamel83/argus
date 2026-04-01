"""Tests for core modules: TTLCache, SlidingWindowLimiter, SQLite persistence."""

import os
import tempfile
import time
import pytest


class TestTTLCache:
    def test_put_and_get(self):
        from argus.core.cache import TTLCache
        cache = TTLCache(ttl_seconds=3600, key_fn=lambda x: x)
        cache.put("key1", value="val1")
        assert cache.get("key1") == "val1"

    def test_cache_miss(self):
        from argus.core.cache import TTLCache
        cache = TTLCache(ttl_seconds=3600, key_fn=lambda x: x)
        assert cache.get("missing") is None

    def test_ttl_expiry(self):
        from argus.core.cache import TTLCache
        cache = TTLCache(ttl_seconds=0, key_fn=lambda x: x)
        cache.put("key", value="val")
        time.sleep(0.01)
        assert cache.get("key") is None

    def test_skip_fn(self):
        from argus.core.cache import TTLCache
        cache = TTLCache(ttl_seconds=3600, key_fn=lambda x: x,
                         skip_fn=lambda v: v == "skip_me")
        cache.put("k1", value="keep_me")
        cache.put("k2", value="skip_me")
        assert cache.get("k1") == "keep_me"
        assert cache.get("k2") is None

    def test_clear(self):
        from argus.core.cache import TTLCache
        cache = TTLCache(ttl_seconds=3600, key_fn=lambda x: x)
        cache.put("k", value="v")
        assert cache.size() == 1
        cache.clear()
        assert cache.size() == 0

    def test_multiple_key_parts(self):
        from argus.core.cache import TTLCache
        cache = TTLCache(ttl_seconds=3600, key_fn=lambda a, b: f"{a}:{b}")
        cache.put("a", "b", value="val")
        assert cache.get("a", "b") == "val"
        assert cache.get("a", "c") is None


class TestSlidingWindowLimiter:
    def test_allows_within_limit(self):
        from argus.core.rate_limit import SlidingWindowLimiter
        limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            allowed, _ = limiter.is_allowed("test_key")
        assert allowed is True

    def test_blocks_over_limit(self):
        from argus.core.rate_limit import SlidingWindowLimiter
        limiter = SlidingWindowLimiter(max_requests=2, window_seconds=60)
        limiter.is_allowed("key")
        limiter.is_allowed("key")
        allowed, retry_after = limiter.is_allowed("key")
        assert allowed is False
        assert retry_after > 0

    def test_separate_keys_independent(self):
        from argus.core.rate_limit import SlidingWindowLimiter
        limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60)
        limiter.is_allowed("key1")
        allowed, _ = limiter.is_allowed("key2")
        assert allowed is True

    def test_window_expires(self):
        from argus.core.rate_limit import SlidingWindowLimiter
        limiter = SlidingWindowLimiter(max_requests=1, window_seconds=0)
        limiter.is_allowed("key")
        time.sleep(0.01)
        allowed, _ = limiter.is_allowed("key")
        assert allowed is True

    def test_remaining(self):
        from argus.core.rate_limit import SlidingWindowLimiter
        limiter = SlidingWindowLimiter(max_requests=5, window_seconds=60)
        assert limiter.remaining("key") == 5
        limiter.is_allowed("key")
        assert limiter.remaining("key") == 4

    def test_clear(self):
        from argus.core.rate_limit import SlidingWindowLimiter
        limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60)
        limiter.is_allowed("key")
        limiter.clear()
        allowed, _ = limiter.is_allowed("key")
        assert allowed is True


class TestSQLitePersistence:
    def test_init_and_schema(self):
        from argus.persistence.db import init_db, _get_db_path
        with tempfile.TemporaryDirectory() as tmpdir:
            import argus.persistence.db as db_mod
            db_mod._db_path = os.path.join(tmpdir, "test.db")
            init_db()
            import sqlite3
            conn = sqlite3.connect(db_mod._db_path)
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            conn.close()
            assert "search_queries" in tables
            assert "search_runs" in tables
            assert "search_results" in tables
            assert "provider_usage" in tables

    def test_persist_and_non_fatal(self):
        from argus.persistence.db import SearchPersistenceGateway
        from argus.models import SearchQuery, SearchMode, SearchResponse, SearchResult, ProviderName

        with tempfile.TemporaryDirectory() as tmpdir:
            import argus.persistence.db as db_mod
            db_mod._db_path = os.path.join(tmpdir, "test.db")

            gw = SearchPersistenceGateway()
            query = SearchQuery(query="test query", mode=SearchMode.DISCOVERY)
            response = SearchResponse(
                query="test query", mode=SearchMode.DISCOVERY,
                results=[SearchResult(url="https://example.com", title="Test", snippet="ok",
                                     provider=ProviderName.BRAVE)],
                traces=[], total_results=1, search_run_id="test123",
            )
            run_id = gw.record_completed_search(query, response)
            assert run_id == "test123"

    def test_gateway_init_failure_is_non_fatal(self):
        from argus.persistence.db import SearchPersistenceGateway
        with tempfile.TemporaryDirectory() as tmpdir:
            import argus.persistence.db as db_mod
            db_mod._db_path = os.path.join(tmpdir, "test.db")
            gw = SearchPersistenceGateway()
            # Should not raise
            assert gw.record_completed_search(None, None) is None


class TestApiKeyAuth:
    @pytest.mark.asyncio
    async def test_health_exempt_from_auth(self):
        from argus.api.main import create_app
        from unittest.mock import MagicMock
        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})
        from fastapi.testclient import TestClient
        client = TestClient(create_app(broker=mock_broker))
        # Set api_key via monkeypatch isn't easy with TestClient,
        # but we can test that health works without a key
        resp = client.get("/api/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_auth_required_when_key_set(self):
        from argus.api.main import create_app
        from unittest.mock import MagicMock, AsyncMock
        from fastapi.testclient import TestClient
        from argus.models import SearchResponse, SearchMode

        mock_broker = MagicMock()
        mock_broker.get_provider_status = MagicMock(return_value={"effective_status": "enabled"})
        mock_broker.search = AsyncMock(return_value=SearchResponse(
            query="test", mode=SearchMode.DISCOVERY, results=[],
            total_results=0, cached=False, search_run_id="a1",
        ))

        app = create_app(broker=mock_broker)
        app.state.api_key = "test-secret-key"
        # Also set rate limiter's api_key so it doesn't rate-limit
        app.state.rate_limiter._api_key = "test-secret-key"
        client = TestClient(app)

        # No key → 401
        resp = client.post("/api/search", json={"query": "test", "mode": "discovery"})
        assert resp.status_code == 401

        # Wrong key → 401
        resp = client.post("/api/search", json={"query": "test", "mode": "discovery"},
                           headers={"x-api-key": "wrong"})
        assert resp.status_code == 401

        # Correct key → 200
        resp = client.post("/api/search", json={"query": "test", "mode": "discovery"},
                           headers={"x-api-key": "test-secret-key"})
        assert resp.status_code == 200
