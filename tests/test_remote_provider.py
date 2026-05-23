import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from argus.models import ProviderName, SearchQuery, SearchMode, ProviderStatus
from argus.config import EgressNode


def _make_node(url: str = "http://worker:8273", secret: str = "s") -> EgressNode:
    return EgressNode(name="test-worker", url=url, shared_secret=secret)


def _make_query() -> SearchQuery:
    return SearchQuery(query="test", mode=SearchMode.DISCOVERY, max_results=5)


@pytest.mark.asyncio
async def test_remote_provider_success():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"url": "https://yahoo.com/r1", "title": "R1", "snippet": "s1",
             "domain": "yahoo.com", "provider": "yahoo", "score": 0.5,
             "raw_rank": 0, "metadata": {}}
        ],
        "trace": {
            "provider": "yahoo", "status": "success",
            "results_count": 1, "latency_ms": 120, "error": None,
        }
    }

    with patch("httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_http

        results, trace = await client.search(_make_query())

    assert trace.status == "success"
    assert trace.egress == "test-worker"
    assert len(results) == 1
    assert results[0].url == "https://yahoo.com/r1"


@pytest.mark.asyncio
async def test_remote_provider_network_error_returns_error_trace():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)

    with patch("httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        MockClient.return_value = mock_http

        results, trace = await client.search(_make_query())

    assert trace.status == "error"
    assert "refused" in trace.error
    assert results == []


@pytest.mark.asyncio
async def test_remote_provider_401_returns_error_trace():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=mock_response)
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_http

        results, trace = await client.search(_make_query())

    assert trace.status == "error"
    assert results == []


def test_remote_provider_name_property():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)
    assert client.name == ProviderName.YAHOO


def test_remote_provider_is_available():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)
    assert client.is_available() is True
