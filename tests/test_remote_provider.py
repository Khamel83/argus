import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from argus.acquisition.errors import AcquisitionFailure, AcquisitionFailureCode
from argus.acquisition.guarded import GuardedAcquisitionError
from argus.acquisition.models import CredentialPolicy, OperationClass, OriginProfile
from argus.models import ProviderName, SearchQuery, SearchMode
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
            {
                "url": "https://yahoo.com/r1",
                "title": "R1",
                "snippet": "s1",
                "domain": "yahoo.com",
                "provider": "yahoo",
                "score": 0.5,
                "raw_rank": 0,
                "metadata": {},
            }
        ],
        "trace": {
            "provider": "yahoo",
            "status": "success",
            "results_count": 1,
            "latency_ms": 120,
            "error": None,
        },
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
    assert trace.error == "provider_unavailable: guarded acquisition is unavailable"
    assert trace.http_status is None
    assert results == []


@pytest.mark.asyncio
async def test_remote_provider_401_returns_error_trace():
    from argus.broker.remote_provider import RemoteProviderClient

    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.content = b""
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "401", request=MagicMock(), response=mock_response
        )
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_http

        results, trace = await client.search(_make_query())

    assert trace.status == "error"
    assert trace.error == "remote egress returned HTTP 401"
    assert trace.http_status == 401
    assert results == []


@pytest.mark.asyncio
async def test_remote_provider_uses_guarded_authenticated_service_boundary():
    from argus.broker.remote_provider import RemoteProviderClient

    node = _make_node("http://worker.internal:8273/")
    client = RemoteProviderClient(ProviderName.YAHOO, node)
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "results": [],
        "trace": {"status": "empty", "results_count": 0},
    }

    with patch(
        "argus.broker.remote_provider.guarded_http_request",
        new=AsyncMock(return_value=response),
    ) as guarded:
        results, trace = await client.search(_make_query())

    assert results == []
    assert trace.status == "empty"
    kwargs = guarded.await_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["profile"] is OriginProfile.AUTHENTICATED_CONTENT
    assert kwargs["credential_policy"] is CredentialPolicy.ORIGIN_SCOPED
    assert kwargs["operation_class"] is OperationClass.DIRECT_HTTP
    assert kwargs["caller_principal"] == "remote-provider"
    assert kwargs["trusted_service_origin"] == "http://worker.internal:8273/"
    assert kwargs["follow_redirects"] is False
    assert kwargs["headers"]["Authorization"] == "Bearer s"
    assert kwargs["json_body"]["provider"] == "yahoo"


@pytest.mark.asyncio
async def test_remote_provider_preserves_typed_guard_failure_without_secret_text():
    from argus.broker.remote_provider import RemoteProviderClient

    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)
    failure = AcquisitionFailure(
        code=AcquisitionFailureCode.AUTHENTICATION_REJECTED,
        safe_reason="worker authentication was rejected",
        retryable=False,
        request_id="remote-yahoo-attempt",
    )

    with patch(
        "argus.broker.remote_provider.guarded_http_request",
        new=AsyncMock(side_effect=GuardedAcquisitionError(failure)),
    ):
        results, trace = await client.search(_make_query())

    assert results == []
    assert trace.status == "error"
    assert trace.error == "authentication_rejected: worker authentication was rejected"
    assert "Bearer" not in trace.error


@pytest.mark.asyncio
async def test_remote_provider_bounds_untrusted_worker_trace_fields():
    from argus.broker.remote_provider import RemoteProviderClient

    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)
    response = MagicMock()
    response.status_code = 200
    response.content = b"{}"
    response.json.return_value = {
        "results": [],
        "trace": {
            "status": ["success"],
            "results_count": 10_000_001,
            "error": "Authorization: Bearer should-not-cross-boundary",
        },
    }

    with patch("httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=response)
        MockClient.return_value = mock_http

        results, trace = await client.search(_make_query())

    assert results == []
    assert trace.status == "error"
    assert trace.results_count == 0
    assert trace.error is None


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
