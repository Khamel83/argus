"""Tests for residential extraction — client and service."""

import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from argus.extraction.models import ExtractorName
from argus.config import ArgusConfig, ResidentialConfig, NodeConfig


def _mock_http_client(response_or_side_effect):
    """Create a mock httpx.AsyncClient that returns the given response."""
    mock_cls = MagicMock()
    mock_client = AsyncMock()
    if isinstance(response_or_side_effect, Exception):
        mock_client.post = AsyncMock(side_effect=response_or_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=response_or_side_effect)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_cls.return_value = mock_client
    return mock_cls


def _mock_config(endpoints=None, shared_secret="shared-secret", policy="fallback", role="primary", egress="unknown"):
    return ArgusConfig(
        node=NodeConfig(role=role, egress_type=egress),
        residential=ResidentialConfig(
            endpoints=endpoints or [],
            shared_secret=shared_secret,
            policy=policy,
        )
    )


class TestResidentialExtractor:
    """Tests for the residential extractor client (runs on oci-dev)."""

    @pytest.mark.asyncio
    async def test_not_configured(self):
        from argus.extraction.residential_extractor import extract_residential

        with patch("argus.extraction.residential_extractor.get_config", return_value=_mock_config(endpoints=[])):
            result = await extract_residential("https://example.com")
            assert result.error == "residential: not configured"

    @pytest.mark.asyncio
    async def test_shared_secret_required(self):
        from argus.extraction.residential_extractor import extract_residential

        with patch("argus.extraction.residential_extractor.get_config", return_value=_mock_config(endpoints=["http://10.0.0.1:8123"], shared_secret="")):
            result = await extract_residential("https://example.com")
            assert result.error == "residential: shared secret not configured"

    @pytest.mark.asyncio
    async def test_successful_extraction(self):
        from argus.extraction.residential_extractor import extract_residential

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "url": "https://example.com",
            "title": "Article",
            "text": "This is the article body text with enough words to be valid content.",
            "author": "John",
            "date": "2024-01-15",
            "word_count": 15,
        }

        cfg = _mock_config(endpoints=["http://10.0.0.1:8123"], shared_secret="shared-secret")
        with patch("argus.extraction.residential_extractor.get_config", return_value=cfg), \
             patch("argus.extraction.residential_extractor.httpx.AsyncClient", _mock_http_client(mock_response)):
            result = await extract_residential("https://example.com")
            assert result.text == "This is the article body text with enough words to be valid content."
            assert result.title == "Article"
            assert result.extractor == ExtractorName.RESIDENTIAL

    @pytest.mark.asyncio
    async def test_multi_endpoint_failover(self):
        """First endpoint fails, second succeeds."""
        from argus.extraction.residential_extractor import extract_residential

        fail_response = MagicMock()
        fail_response.status_code = 503

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "url": "https://example.com",
            "title": "Article",
            "text": "Fallback content from second endpoint.",
            "word_count": 8,
        }

        call_count = 0
        async def fake_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return fail_response
            return success_response

        mock_cls = MagicMock()
        mock_client = AsyncMock()
        mock_client.post = fake_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        cfg = _mock_config(endpoints=["http://10.0.0.1:8123", "http://10.0.0.2:8123"], shared_secret="shared-secret")
        with patch("argus.extraction.residential_extractor.get_config", return_value=cfg), \
             patch("argus.extraction.residential_extractor.httpx.AsyncClient", mock_cls):
            result = await extract_residential("https://example.com")
            assert result.text == "Fallback content from second endpoint."
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_service_unreachable(self):
        from argus.extraction.residential_extractor import extract_residential

        import httpx

        cfg = _mock_config(endpoints=["http://10.0.0.1:8123"], shared_secret="shared-secret")
        with patch("argus.extraction.residential_extractor.get_config", return_value=cfg), \
             patch("argus.extraction.residential_extractor.httpx.AsyncClient", _mock_http_client(httpx.ConnectError("refused"))):
            result = await extract_residential("https://example.com")
            assert "unavailable" in result.error

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_unhealthy(self):
        """Unhealthy endpoint is skipped without making a request."""
        from argus.extraction.residential_extractor import extract_residential, _endpoint_health

        _endpoint_health.mark_unhealthy("http://10.0.0.1:8123")

        cfg = _mock_config(endpoints=["http://10.0.0.1:8123"], shared_secret="shared-secret")
        with patch("argus.extraction.residential_extractor.get_config", return_value=cfg), \
             patch("argus.extraction.residential_extractor.httpx.AsyncClient") as mock_cls:
            result = await extract_residential("https://example.com")
            assert "unavailable" in result.error
            mock_cls.assert_not_called()

        _endpoint_health._unhealthy_until.clear()

    @pytest.mark.asyncio
    async def test_circuit_breaker_ttl_recovery(self):
        """Endpoint recovers after cooldown expires."""
        from argus.extraction.residential_extractor import extract_residential, _endpoint_health

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "url": "https://example.com",
            "text": "Recovered content",
            "word_count": 2,
        }

        # Mark unhealthy with very short cooldown
        _endpoint_health.mark_unhealthy("http://10.0.0.1:8123", cooldown=0.01)
        assert not _endpoint_health.is_healthy("http://10.0.0.1:8123")

        # Wait for cooldown to expire
        time.sleep(0.02)
        assert _endpoint_health.is_healthy("http://10.0.0.1:8123")

        cfg = _mock_config(endpoints=["http://10.0.0.1:8123"], shared_secret="shared-secret")
        with patch("argus.extraction.residential_extractor.get_config", return_value=cfg), \
             patch("argus.extraction.residential_extractor.httpx.AsyncClient", _mock_http_client(mock_response)):
            result = await extract_residential("https://example.com")
            assert result.text == "Recovered content"

        _endpoint_health._unhealthy_until.clear()

    @pytest.mark.asyncio
    async def test_cookie_passing(self):
        """Cookies are loaded and sent in request body when domain has cookies."""
        from argus.extraction.residential_extractor import extract_residential

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "url": "https://example.com",
            "text": "Auth content",
            "word_count": 2,
        }

        mock_cookies = [{"name": "session", "value": "abc123"}]

        cfg = _mock_config(endpoints=["http://10.0.0.1:8123"], shared_secret="shared-secret")
        with patch("argus.extraction.residential_extractor.get_config", return_value=cfg), \
             patch("argus.extraction.residential_extractor._load_cookies_for_domain", return_value=mock_cookies), \
             patch("argus.extraction.residential_extractor.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await extract_residential("https://example.com", domain="example.com")
            assert result.text == "Auth content"
            # Verify cookies were passed in request body
            call_args = mock_client.post.call_args
            body = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
            assert "cookies" in body
            assert body["cookies"] == mock_cookies
            assert call_args[1]["headers"]["Authorization"] == "Bearer shared-secret"

    @pytest.mark.asyncio
    async def test_reset_reachability(self):
        from argus.extraction.residential_extractor import reset_reachability, _endpoint_health

        _endpoint_health.mark_unhealthy("http://10.0.0.1:8123")
        assert not _endpoint_health.is_healthy("http://10.0.0.1:8123")

        reset_reachability()
        assert _endpoint_health.is_healthy("http://10.0.0.1:8123")


class TestResidentialService:
    """Tests for the standalone residential extraction service."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cfg = _mock_config()
        with patch("argus.extraction.residential_service.get_config", return_value=cfg):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/health")
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert "uptime_seconds" in data

    @pytest.mark.asyncio
    async def test_ssrf_blocks_private_ip(self):
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cfg = _mock_config(shared_secret="shared-secret")
        with patch("argus.extraction.residential_service.get_config", return_value=cfg):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/extract",
                    json={"url": "http://192.168.1.1/admin"},
                    headers={"Authorization": "Bearer shared-secret"},
                )
            assert resp.status_code == 400
            assert "SSRF" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_ssrf_blocks_localhost(self):
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cfg = _mock_config(shared_secret="shared-secret")
        with patch("argus.extraction.residential_service.get_config", return_value=cfg):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/extract",
                    json={"url": "http://localhost/admin"},
                    headers={"Authorization": "Bearer shared-secret"},
                )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_ssrf_blocks_metadata(self):
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cfg = _mock_config(shared_secret="shared-secret")
        with patch("argus.extraction.residential_service.get_config", return_value=cfg):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/extract",
                    json={"url": "http://169.254.169.254/latest/meta-data/"},
                    headers={"Authorization": "Bearer shared-secret"},
                )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_extract_requires_authentication(self):
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cfg = _mock_config(shared_secret="shared-secret")
        with patch("argus.extraction.residential_service.get_config", return_value=cfg):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post("/extract", json={"url": "https://example.com"})
                assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_extract_requires_shared_secret_configuration(self):
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cfg = _mock_config(shared_secret="")
        with patch("argus.extraction.residential_service.get_config", return_value=cfg):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post("/extract", json={"url": "https://example.com"})
                assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_extract_blocks_disallowed_callers(self):
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cfg = _mock_config(shared_secret="shared-secret")
        with patch("argus.extraction.residential_service.get_config", return_value=cfg):
            transport = ASGITransport(app=app, client=("198.51.100.10", 40000))
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/extract",
                    json={"url": "https://example.com"},
                    headers={"Authorization": "Bearer shared-secret"},
                )
                assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_extract_with_trafilatura(self):
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cfg = _mock_config(shared_secret="shared-secret")
        with patch("argus.extraction.residential_service.get_config", return_value=cfg), \
             patch("argus.extraction.residential_service._extract_trafilatura") as mock_extract, \
             patch("argus.extraction.residential_service._check_playwright", return_value=False), \
             patch("argus.extraction.residential_service.shutil.which", return_value=None):
            mock_extract.return_value = {
                "title": "Article",
                "text": " ".join(["word"] * 60),
                "author": "John",
                "date": "2024-01-15",
                "word_count": 60,
            }

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/extract",
                    json={"url": "https://example.com/article"},
                    headers={"Authorization": "Bearer shared-secret"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["extractor"] == "trafilatura"

    @pytest.mark.asyncio
    async def test_extract_passes_cookies_to_trafilatura(self):
        """Cookies in request are forwarded to trafilatura extractor."""
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cookies = [{"name": "sid", "value": "xyz"}]

        cfg = _mock_config(shared_secret="shared-secret")
        with patch("argus.extraction.residential_service.get_config", return_value=cfg), \
             patch("argus.extraction.residential_service._extract_trafilatura") as mock_extract, \
             patch("argus.extraction.residential_service._check_playwright", return_value=False), \
             patch("argus.extraction.residential_service.shutil.which", return_value=None):
            mock_extract.return_value = {
                "title": "Article",
                "text": " ".join(["word"] * 60),
                "word_count": 60,
            }

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/extract",
                    json={"url": "https://example.com", "cookies": cookies},
                    headers={"Authorization": "Bearer shared-secret"},
                )
                assert resp.status_code == 200
                # Verify cookies were passed to trafilatura
                call_args = mock_extract.call_args
                assert call_args[0][1] == cookies  # second positional arg

    @pytest.mark.asyncio
    async def test_extract_falls_back_to_playwright(self):
        from argus.extraction.residential_service import app

        from httpx import AsyncClient, ASGITransport

        cfg = _mock_config(shared_secret="shared-secret")
        with patch("argus.extraction.residential_service.get_config", return_value=cfg), \
             patch("argus.extraction.residential_service._extract_trafilatura") as mock_traf, \
             patch("argus.extraction.residential_service._extract_playwright") as mock_pw, \
             patch("argus.extraction.residential_service._check_playwright", return_value=True), \
             patch("argus.extraction.residential_service.shutil.which", return_value=None):
            mock_traf.return_value = {"error": "failed to fetch"}
            mock_pw.return_value = {
                "title": "JS Article",
                "text": " ".join(["word"] * 120),
                "word_count": 120,
            }

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/extract",
                    json={"url": "https://example.com/js-page"},
                    headers={"Authorization": "Bearer shared-secret"},
                )
                assert resp.status_code == 200
                assert resp.json()["extractor"] == "playwright"
