from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _production_adapter_environment(**overrides):
    environment = {
        "ARGUS_ENV": "production",
        "ARGUS_NODE_ROLE": "caller",
        "ARGUS_AUTHORITY_URL": "http://argus.internal:8000",
        "ARGUS_AUTHORITY_TOKEN": "adapter-token",
    }
    environment.update(overrides)
    return environment


@pytest.mark.parametrize(
    "name",
    [
        "ARGUS_DB_URL",
        "ARGUS_BUDGET_DB_PATH",
        "ARGUS_DATA_ROOT",
        "ARGUS_BRAVE_API_KEY",
        "ARGUS_SEARXNG_BASE_URL",
        "ARGUS_RESIDENTIAL_SHARED_SECRET",
        "ARGUS_EGRESS_SHARED_SECRET",
        "ARGUS_MAYA_CAPTURE_TOKEN",
        "PLAYWRIGHT_BROWSERS_PATH",
        "WOLFRAM_APP_ID",
        "WOLFRAM_API_KEY",
        "BRAVE_API_KEY",
        "JINA_API_KEY",
        "FIRECRAWL_API_KEY",
        "ARGUS_COOKIE_DIR",
        "ARGUS_OBSCURA_CDP_URL",
        "ARGUS_ADMIN_API_KEY",
    ],
)
def test_production_mcp_adapter_rejects_execution_authority_inputs(name):
    from argus.authority import (
        AuthorityConfigurationError,
        authority_client_config,
    )

    with pytest.raises(AuthorityConfigurationError, match=name):
        authority_client_config(
            _production_adapter_environment(**{name: "forbidden-value"}),
            adapter="mcp",
        )


def test_production_mcp_adapter_requires_authenticated_http_authority():
    from argus.authority import (
        AuthorityConfigurationError,
        authority_client_config,
    )

    environment = _production_adapter_environment()
    environment.pop("ARGUS_AUTHORITY_TOKEN")

    with pytest.raises(AuthorityConfigurationError, match="authentication"):
        authority_client_config(environment, adapter="mcp")


def test_development_standalone_requires_explicit_opt_in():
    from argus.authority import adapter_execution_mode

    assert adapter_execution_mode({"ARGUS_ENV": "development"}) == "unconfigured"
    assert (
        adapter_execution_mode(
            {
                "ARGUS_ENV": "development",
                "ARGUS_MCP_STANDALONE": "true",
            }
        )
        == "standalone"
    )
    assert adapter_execution_mode(_production_adapter_environment()) == "http"


def test_production_broker_construction_requires_explicit_api_authority(
    monkeypatch,
):
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "primary")
    reset_config()

    from argus.broker.router import create_broker

    with pytest.raises(RuntimeError, match="HTTP API execution authority"):
        create_broker()


@pytest.mark.asyncio
async def test_production_direct_extraction_requires_api_authority(
    monkeypatch,
):
    from argus.extraction import extractor

    monkeypatch.setenv("ARGUS_ENV", "production")
    unpersisted = AsyncMock()
    monkeypatch.setattr(extractor, "_extract_url_unpersisted", unpersisted)

    with pytest.raises(RuntimeError, match="HTTP API execution authority"):
        await extractor.extract_url("https://example.com")

    unpersisted.assert_not_awaited()


def test_production_api_authority_requires_primary_role(monkeypatch):
    from argus.api.main import _HTTP_API_AUTHORITY_CAPABILITY
    from argus.authority import broker_construction_allowed

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "caller")

    with pytest.raises(RuntimeError, match="primary"):
        broker_construction_allowed(authority_capability=_HTTP_API_AUTHORITY_CAPABILITY)


def test_production_authority_capability_is_not_a_forgeable_boolean(monkeypatch):
    from argus.authority import (
        broker_construction_allowed,
        extraction_execution_allowed,
    )

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "primary")

    with pytest.raises(RuntimeError, match="HTTP API execution authority"):
        broker_construction_allowed(authority_capability=True)
    with pytest.raises(RuntimeError, match="HTTP API execution authority"):
        extraction_execution_allowed(authority_capability=True)


def test_production_direct_search_broker_construction_is_rejected(monkeypatch):
    from argus.broker.router import SearchBroker

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "primary")

    with pytest.raises(RuntimeError, match="HTTP API execution authority"):
        SearchBroker({})


def test_production_worker_role_is_not_a_provider_execution_authority(monkeypatch):
    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "worker")

    from argus.worker.server import create_worker_app

    with pytest.raises(RuntimeError, match="HTTP API execution authority"):
        create_worker_app()


@pytest.mark.asyncio
async def test_http_client_propagates_authentication_and_caller_label():
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient

    observed = {}

    def handler(request):
        observed["authorization"] = request.headers["authorization"]
        observed["path"] = request.url.path
        observed["payload"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={
                "query": "authority",
                "mode": "discovery",
                "results": [],
                "traces": [],
                "total_results": 0,
                "cached": False,
            },
        )

    client = HttpAuthorityClient(
        AuthorityClientConfig("http://argus.internal:8000", "service-token"),
        transport=httpx.MockTransport(handler),
    )

    payload = await client.search(
        {
            "query": "authority",
            "mode": "discovery",
            "caller": "maya",
        },
        token="caller-token",
    )

    assert payload["query"] == "authority"
    assert observed == {
        "authorization": "Bearer caller-token",
        "path": "/api/search",
        "payload": {
            "query": "authority",
            "mode": "discovery",
            "caller": "maya",
        },
    }


@pytest.mark.asyncio
async def test_http_client_returns_bounded_failure_without_remote_body():
    from argus.authority import (
        AuthorityClientConfig,
        AuthorityRequestError,
        HttpAuthorityClient,
    )

    client = HttpAuthorityClient(
        AuthorityClientConfig("http://argus.internal:8000", "service-token"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                text="database password=must-not-leak",
            )
        ),
    )

    with pytest.raises(AuthorityRequestError) as raised:
        await client.search({"query": "authority"})

    assert raised.value.status_code == 503
    assert str(raised.value) == "Argus execution authority unavailable"
    assert "must-not-leak" not in str(raised.value)


@pytest.mark.asyncio
async def test_http_client_bounds_all_transport_failures():
    from argus.authority import (
        AuthorityClientConfig,
        AuthorityRequestError,
        HttpAuthorityClient,
    )

    class FailingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.WriteError(
                "socket detail must-not-leak",
                request=request,
            )

    client = HttpAuthorityClient(
        AuthorityClientConfig("http://argus.internal:8000", "service-token"),
        transport=FailingTransport(),
    )

    with pytest.raises(
        AuthorityRequestError,
        match="execution authority unavailable",
    ) as raised:
        await client.search({"query": "authority"})

    assert "must-not-leak" not in str(raised.value)


def test_production_http_requires_auth_even_from_loopback_and_derives_caller(
    monkeypatch,
):
    import json

    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.config import reset_config
    from argus.models import SearchMode, SearchResponse

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        json.dumps({"maya": {"token": "maya-token"}}),
    )
    reset_config()

    broker = MagicMock()
    broker.search = AsyncMock(
        return_value=SearchResponse(
            query="authority",
            mode=SearchMode.DISCOVERY,
            results=[],
        )
    )
    repository = MagicMock()
    client = TestClient(create_app(broker=broker, search_repository=repository))

    unauthenticated = client.post(
        "/api/search",
        json={"query": "authority"},
    )
    authenticated = client.post(
        "/api/search",
        headers={"Authorization": "Bearer maya-token"},
        json={"query": "authority", "caller": "maya-share"},
    )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    query = broker.search.await_args.args[0]
    assert query.caller == "maya"
    assert query.metadata == {"caller_label": "maya-share"}


def test_authenticated_capabilities_health_and_budgets_report_api_truth(
    monkeypatch,
):
    import json

    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        json.dumps({"mcp": {"token": "mcp-token"}}),
    )
    reset_config()

    broker = MagicMock()
    broker.get_provider_status.side_effect = lambda provider: {
        "provider": provider.value,
        "effective_status": "enabled",
        "consecutive_failures": 0,
    }
    broker.spend_repository.provider_summary.side_effect = (
        lambda provider, budget_limit: {
            "provider": provider.value,
            "remaining": budget_limit,
            "argus_estimated_charge": 0,
            "uncertain_charge": 0,
            "provider_snapshot": None,
        }
    )
    broker.budget_tracker.get_budget_limit.return_value = 100
    repository = MagicMock()
    client = TestClient(create_app(broker=broker, search_repository=repository))
    headers = {"Authorization": "Bearer mcp-token"}

    capabilities = client.get("/api/capabilities", headers=headers)
    health = client.get("/api/provider-health", headers=headers)
    budgets = client.get("/api/budgets", headers=headers)

    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "schema_version": "1.0",
        "execution_authority": "http-api",
        "role": "primary",
        "capabilities": {
            "search": True,
            "extraction": True,
            "recovery": True,
            "expansion": True,
            "provider_health": True,
            "budgets": True,
            "workflows": True,
        },
    }
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["providers"]["duckduckgo"]["effective_status"] == "enabled"
    assert budgets.status_code == 200
    assert budgets.json()["providers"]["duckduckgo"]["remaining"] == 100
    serialized = json.dumps(
        [capabilities.json(), health.json(), budgets.json()]
    ).lower()
    assert "token" not in serialized
    assert "password" not in serialized
    assert "db_url" not in serialized


def test_authority_truth_routes_require_authentication_in_production(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_API_KEY", "authority-token")
    reset_config()
    client = TestClient(create_app(broker=MagicMock()))

    for path in ("/api/capabilities", "/api/provider-health", "/api/budgets"):
        assert client.get(path).status_code == 401


def test_http_recovery_preserves_archive_fallback(monkeypatch):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.models import SearchMode, SearchResponse

    broker = MagicMock()
    broker.search = AsyncMock(
        return_value=SearchResponse(
            query="https://dead.example/article",
            mode=SearchMode.RECOVERY,
            results=[],
        )
    )
    monkeypatch.setattr(
        "argus.api.routes_search.try_archive_ph",
        AsyncMock(
            return_value={
                "url": "https://archive.ph/example",
                "title": "Archived article",
                "snippet": "Recovered content",
                "domain": "archive.ph",
                "score": 0.8,
            }
        ),
    )
    repository = MagicMock()
    client = TestClient(
        create_app(
            broker=broker,
            search_repository=repository,
        )
    )

    response = client.post(
        "/api/recover-url",
        json={"url": "https://dead.example/article"},
    )

    assert response.status_code == 200
    assert response.json()["total_results"] == 1
    assert response.json()["results"] == [
        {
            "url": "https://archive.ph/example",
            "title": "Archived article",
            "snippet": "Recovered content",
            "domain": "archive.ph",
            "provider": None,
            "score": 0.8,
            "egress": None,
            "machine": None,
            "score_attribution": {},
        }
    ]
    repository.accept.assert_called_once()


@pytest.mark.parametrize("path", ["/api/provider-health", "/api/budgets"])
def test_authority_truth_failures_are_bounded_and_fail_closed(
    monkeypatch,
    path,
):
    import json

    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.config import reset_config

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        json.dumps({"mcp": {"token": "mcp-token"}}),
    )
    reset_config()

    broker = MagicMock()
    broker.get_provider_status.side_effect = RuntimeError(
        "provider secret must-not-leak"
    )
    broker.spend_repository.provider_summary.side_effect = RuntimeError(
        "database password must-not-leak"
    )
    client = TestClient(
        create_app(broker=broker, search_repository=MagicMock()),
        raise_server_exceptions=False,
    )

    response = client.get(
        path,
        headers={"Authorization": "Bearer mcp-token"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Execution authority state unavailable"}
    assert "must-not-leak" not in response.text


def test_production_mcp_backend_is_http_only_and_never_constructs_broker(
    monkeypatch,
):
    from argus.mcp.http_adapter import HttpMcpAdapter
    from argus.mcp.server import build_mcp_backend

    def forbidden_broker():
        raise AssertionError("MCP must not construct a broker")

    monkeypatch.setattr(
        "argus.broker.router.create_broker",
        forbidden_broker,
    )

    backend = build_mcp_backend(_production_adapter_environment())

    assert isinstance(backend, HttpMcpAdapter)


def test_unconfigured_mcp_backend_fails_instead_of_silently_running_local():
    from argus.authority import AuthorityConfigurationError
    from argus.mcp.server import build_mcp_backend

    with pytest.raises(AuthorityConfigurationError, match="ARGUS_AUTHORITY_URL"):
        build_mcp_backend({"ARGUS_ENV": "development"})


@pytest.mark.asyncio
async def test_mcp_search_is_a_stateless_authenticated_http_translation():
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient
    from argus.mcp.http_adapter import HttpMcpAdapter

    observed = {}

    def handler(request):
        observed["authorization"] = request.headers["authorization"]
        observed["path"] = request.url.path
        observed["payload"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={
                "query": "canonical",
                "mode": "discovery",
                "results": [
                    {
                        "url": "https://example.com/canonical",
                        "title": "Canonical result",
                        "snippet": "From HTTP",
                        "domain": "example.com",
                        "provider": "duckduckgo",
                        "score": 1.0,
                        "egress": "residential",
                        "machine": "homelab",
                        "score_attribution": {},
                    }
                ],
                "traces": [
                    {
                        "provider": "duckduckgo",
                        "status": "success",
                        "results_count": 1,
                        "latency_ms": 5,
                    }
                ],
                "total_results": 1,
                "cached": False,
                "budget_warnings": ["brave nearing monthly limit"],
                "search_run_id": "run-http",
            },
        )

    client = HttpAuthorityClient(
        AuthorityClientConfig("http://argus.internal:8000", "service-token"),
        transport=httpx.MockTransport(handler),
    )
    adapter = HttpMcpAdapter(client)

    result = await adapter.search_web(
        query="canonical",
        mode="discovery",
        max_results=5,
        session_id=None,
        include_attribution=False,
        free_only=False,
        caller_label="maya-share",
        token="actual-caller-token",
    )

    assert "Canonical result" in result
    assert "brave nearing monthly limit" in result
    assert "https://example.com/canonical" in result
    assert "Egress: residential" in result
    assert observed == {
        "authorization": "Bearer actual-caller-token",
        "path": "/api/search",
        "payload": {
            "query": "canonical",
            "mode": "discovery",
            "max_results": 5,
            "include_attribution": False,
            "free_only": False,
            "caller": "maya-share",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "recover_url",
            "/api/recover-url",
            {
                "url": "https://dead.example/article",
                "title": "Article",
                "domain": "dead.example",
            },
        ),
        (
            "expand_links",
            "/api/expand",
            {"query": "topic", "context": "context"},
        ),
        (
            "extract_content",
            "/api/extract",
            {
                "url": "https://example.com/article",
                "domain": "example.com",
                "caller": "mcp",
            },
        ),
    ],
)
async def test_mcp_core_tools_use_http_authority(method, path, payload):
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient
    from argus.mcp.http_adapter import HttpMcpAdapter

    observed = {}

    def handler(request):
        observed["path"] = request.url.path
        observed["payload"] = __import__("json").loads(request.content)
        if path == "/api/extract":
            return httpx.Response(
                200,
                json={
                    "url": payload["url"],
                    "title": "Article",
                    "text": "Extracted",
                    "word_count": 1,
                    "extractor": "trafilatura",
                },
            )
        return httpx.Response(
            200,
            json={
                "query": payload.get("query", payload.get("url")),
                "mode": "recovery" if path.endswith("recover-url") else "discovery",
                "results": [],
                "traces": [],
                "total_results": 0,
                "cached": False,
            },
        )

    adapter = HttpMcpAdapter(
        HttpAuthorityClient(
            AuthorityClientConfig(
                "http://argus.internal:8000",
                "service-token",
            ),
            transport=httpx.MockTransport(handler),
        )
    )

    if method == "recover_url":
        await adapter.recover_url(
            payload["url"],
            payload["title"],
            payload["domain"],
            caller_label="mcp",
        )
    elif method == "expand_links":
        await adapter.expand_links(
            payload["query"],
            payload["context"],
            caller_label="mcp",
        )
    else:
        await adapter.extract_content(
            payload["url"],
            payload["domain"],
        )

    assert observed == {"path": path, "payload": payload}


@pytest.mark.asyncio
async def test_mcp_workflow_starts_through_http_without_local_artifacts():
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient
    from argus.mcp.http_adapter import HttpMcpAdapter

    observed = {}

    def handler(request):
        observed["path"] = request.url.path
        observed["payload"] = __import__("json").loads(request.content)
        return httpx.Response(
            200,
            json={
                "run_id": "mcp-workflow-http",
                "kind": "capture_site",
                "status": "running",
                "target": "https://docs.example",
                "snapshot_dir": "",
                "artifacts": [],
                "documents": [],
                "citations": [],
                "summary_sections": [],
                "metadata": {},
                "error": None,
            },
        )

    adapter = HttpMcpAdapter(
        HttpAuthorityClient(
            AuthorityClientConfig(
                "http://argus.internal:8000",
                "service-token",
            ),
            transport=httpx.MockTransport(handler),
        )
    )

    result = await adapter.capture_site(
        "https://docs.example",
        soft_page_limit=10,
        hard_page_limit=20,
        caller_label="maya",
    )

    assert "mcp-workflow-http" in result
    assert "running" in result
    assert observed == {
        "path": "/api/workflows/capture-site",
        "payload": {
            "url": "https://docs.example",
            "soft_page_limit": 10,
            "hard_page_limit": 20,
            "caller": "maya",
        },
    }


@pytest.mark.asyncio
async def test_mcp_server_registers_core_tools_against_selected_backend(
    monkeypatch,
):
    import argus.mcp.server as server

    class FakeFastMCP:
        instance = None

        def __init__(self, name, **kwargs):
            del name, kwargs
            self.tools = {}
            self.resources = {}
            self.ran = None
            FakeFastMCP.instance = self

        def tool(self):
            return lambda function: self.tools.setdefault(
                function.__name__,
                function,
            )

        def resource(self, uri):
            return lambda function: self.resources.setdefault(uri, function)

        def run(self, *, transport):
            self.ran = transport

    class Backend:
        async def search_web(self, **kwargs):
            self.search = kwargs
            return "from-http-authority"

        async def recover_url(self, *args, **kwargs):
            return "recovered"

        async def expand_links(self, *args, **kwargs):
            return "expanded"

        async def extract_content(self, *args, **kwargs):
            return "extracted"

        async def search_health(self, **kwargs):
            return "healthy"

        async def search_budgets(self, **kwargs):
            return "budget"

    backend = Backend()
    monkeypatch.setattr(
        "mcp.server.fastmcp.FastMCP",
        FakeFastMCP,
    )
    monkeypatch.setattr(server, "build_mcp_backend", lambda: backend)
    monkeypatch.setattr(
        server,
        "create_broker",
        lambda: (_ for _ in ()).throw(
            AssertionError("MCP must not construct a broker")
        ),
        raising=False,
    )

    server.serve_mcp(transport="stdio")
    registered = FakeFastMCP.instance
    result = await registered.tools["search_web"](
        "canonical",
        caller="maya",
    )

    assert result == "from-http-authority"
    assert backend.search["query"] == "canonical"
    assert backend.search["caller_label"] == "maya"
    assert registered.ran == "stdio"


def test_production_cli_rejects_direct_cookie_mutation(monkeypatch):
    from click.testing import CliRunner

    from argus.cli.main import cli

    monkeypatch.setenv("ARGUS_ENV", "production")

    result = CliRunner().invoke(cli, ["cookies", "health"])

    assert result.exit_code != 0
    assert "HTTP API execution authority" in result.output


def test_production_cli_search_uses_http_without_constructing_broker(
    monkeypatch,
):
    from click.testing import CliRunner

    from argus.cli.main import cli

    for name in tuple(__import__("os").environ):
        if name.startswith("ARGUS_"):
            monkeypatch.delenv(name, raising=False)
    for name, value in _production_adapter_environment().items():
        monkeypatch.setenv(name, value)
    observed = {}

    async def search(client, payload, *, token=None):
        del client
        observed["payload"] = payload
        observed["token"] = token
        return {
            "query": "cli authority",
            "mode": "discovery",
            "results": [
                {
                    "url": "https://example.com/cli",
                    "title": "CLI result",
                    "snippet": "HTTP only",
                    "provider": "duckduckgo",
                    "score": 1.0,
                    "score_attribution": {},
                    "egress": "residential",
                    "machine": "homelab",
                }
            ],
            "traces": [],
            "total_results": 1,
            "cached": False,
            "search_run_id": "cli-http-run",
        }

    monkeypatch.setattr(
        "argus.authority.HttpAuthorityClient.search",
        search,
    )
    monkeypatch.setattr(
        "argus.broker.router.create_broker",
        lambda: (_ for _ in ()).throw(
            AssertionError("CLI must not construct a production broker")
        ),
    )

    result = CliRunner().invoke(
        cli,
        ["search", "-q", "cli authority", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert "https://example.com/cli" in result.output
    assert observed == {
        "payload": {
            "query": "cli authority",
            "mode": "discovery",
            "max_results": 10,
            "include_attribution": False,
            "free_only": False,
            "caller": "cli",
        },
        "token": None,
    }


def test_production_cli_core_commands_all_use_http_authority(monkeypatch):
    from click.testing import CliRunner

    from argus.cli.main import cli

    for name in tuple(__import__("os").environ):
        if name.startswith("ARGUS_"):
            monkeypatch.delenv(name, raising=False)
    for name, value in _production_adapter_environment().items():
        monkeypatch.setenv(name, value)
    observed = []

    async def request(client, method, path, *, payload=None, token=None):
        del client, token
        observed.append((method, path, payload))
        if path == "/api/extract":
            return {
                "url": payload["url"],
                "title": "Extracted title",
                "text": "Extracted over HTTP",
                "word_count": 3,
                "extractor": "trafilatura",
                "egress": "residential",
                "machine": "homelab",
                "source_type": "local",
            }
        if path == "/api/recover-url":
            return {
                "query": payload["url"],
                "mode": "recovery",
                "results": [],
                "traces": [],
                "total_results": 0,
                "cached": False,
            }
        if path == "/api/provider-health":
            return {
                "status": "ok",
                "providers": {"duckduckgo": {"effective_status": "healthy"}},
            }
        if path == "/api/budgets":
            return {
                "providers": {
                    "duckduckgo": {
                        "remaining": 100,
                        "argus_estimated_charge": 0,
                        "uncertain_charge": 0,
                    }
                }
            }
        raise AssertionError(path)

    monkeypatch.setattr(
        "argus.authority.HttpAuthorityClient.request",
        request,
    )
    monkeypatch.setattr(
        "argus.broker.router.create_broker",
        lambda: (_ for _ in ()).throw(
            AssertionError("CLI must not construct a production broker")
        ),
    )
    runner = CliRunner()

    extracted = runner.invoke(
        cli,
        ["extract", "-u", "https://example.com/article", "--json"],
    )
    recovered = runner.invoke(
        cli,
        ["recover-url", "-u", "https://dead.example/article", "--json"],
    )
    health = runner.invoke(cli, ["health"])
    budgets = runner.invoke(cli, ["budgets"])

    assert extracted.exit_code == 0, extracted.output
    assert "Extracted over HTTP" in extracted.output
    assert recovered.exit_code == 0, recovered.output
    assert health.exit_code == 0, health.output
    assert "duckduckgo" in health.output
    assert budgets.exit_code == 0, budgets.output
    assert "remaining=100" in budgets.output
    assert [path for _, path, _ in observed] == [
        "/api/extract",
        "/api/recover-url",
        "/api/provider-health",
        "/api/budgets",
    ]


@pytest.mark.parametrize(
    ("arguments", "expected_path"),
    [
        (
            ["recover-article", "-u", "https://dead.example/article", "--json"],
            "/api/workflows/recover-article",
        ),
        (
            ["capture-site", "-u", "https://docs.example", "--json"],
            "/api/workflows/capture-site",
        ),
        (
            ["build-research-pack", "-t", "argus", "--json"],
            "/api/workflows/build-research-pack",
        ),
    ],
)
def test_production_cli_workflows_start_through_http(
    monkeypatch,
    arguments,
    expected_path,
):
    from click.testing import CliRunner

    from argus.cli.main import cli

    for name in tuple(__import__("os").environ):
        if name.startswith("ARGUS_"):
            monkeypatch.delenv(name, raising=False)
    for name, value in _production_adapter_environment().items():
        monkeypatch.setenv(name, value)
    observed = {}

    async def request(client, method, path, *, payload=None, token=None):
        del client, token
        observed.update(method=method, path=path, payload=payload)
        return {
            "run_id": "workflow-http",
            "kind": path.rsplit("/", 1)[-1],
            "status": "running",
            "target": "target",
            "snapshot_dir": "",
            "artifacts": [],
            "documents": [],
            "citations": [],
            "summary_sections": [],
            "metadata": {},
            "error": None,
        }

    monkeypatch.setattr(
        "argus.authority.HttpAuthorityClient.request",
        request,
    )
    monkeypatch.setattr(
        "argus.broker.router.create_broker",
        lambda: (_ for _ in ()).throw(
            AssertionError("CLI must not construct a production broker")
        ),
    )

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0, result.output
    assert '"run_id": "workflow-http"' in result.output
    assert observed["method"] == "POST"
    assert observed["path"] == expected_path


@pytest.mark.asyncio
async def test_http_and_mcp_share_one_broker_identity_and_durable_state(
    monkeypatch,
):
    import json

    from argus.api.main import create_app
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient
    from argus.config import reset_config
    from argus.mcp.http_adapter import HttpMcpAdapter
    from argus.models import (
        ProviderName,
        SearchMode,
        SearchResponse,
        SearchResult,
    )

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "primary")
    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        json.dumps({"mcp": {"token": "mcp-token"}}),
    )
    reset_config()

    broker = MagicMock()
    broker.search = AsyncMock(
        return_value=SearchResponse(
            query="same authority",
            mode=SearchMode.DISCOVERY,
            results=[
                SearchResult(
                    url="https://example.com/same",
                    title="Same authority",
                    snippet="one broker",
                    provider=ProviderName.DUCKDUCKGO,
                    metadata={
                        "egress": "residential",
                        "machine": "homelab",
                    },
                )
            ],
            total_results=1,
            search_run_id="shared-run",
        )
    )
    repository = MagicMock()
    app = create_app(broker=broker, search_repository=repository)
    transport = httpx.ASGITransport(app=app)

    first_mcp_process = HttpMcpAdapter(
        HttpAuthorityClient(
            AuthorityClientConfig("http://argus.internal", "mcp-token"),
            transport=transport,
        )
    )
    restarted_mcp_process = HttpMcpAdapter(
        HttpAuthorityClient(
            AuthorityClientConfig("http://argus.internal", "mcp-token"),
            transport=transport,
        )
    )

    first = await first_mcp_process.search_web(query="same authority")
    second = await restarted_mcp_process.search_web(query="same authority")

    assert "https://example.com/same" in first
    assert "https://example.com/same" in second
    assert broker.search.await_count == 2
    assert all(call.args[0].caller == "mcp" for call in broker.search.await_args_list)
    assert repository.accept.call_count == 2


@pytest.mark.asyncio
async def test_mcp_restart_reuses_http_health_budget_and_extraction_state(
    monkeypatch,
):
    import json

    from argus.api.main import create_app
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient
    from argus.config import reset_config
    from argus.extraction.models import ExtractedContent, ExtractorName
    from argus.mcp.http_adapter import HttpMcpAdapter

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "primary")
    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        json.dumps({"mcp": {"token": "mcp-token"}}),
    )
    reset_config()

    broker = MagicMock()
    broker.get_provider_status.side_effect = lambda provider: {
        "effective_status": "enabled",
    }
    broker.budget_tracker.get_budget_limit.return_value = 100
    broker.spend_repository.provider_summary.side_effect = (
        lambda provider, budget_limit: {
            "remaining": budget_limit,
            "argus_estimated_charge": 0,
            "uncertain_charge": 0,
        }
    )
    repository = MagicMock()
    extract = AsyncMock(
        return_value=ExtractedContent(
            url="https://example.com/article",
            text="shared extraction",
            word_count=2,
            extractor=ExtractorName.TRAFILATURA,
        )
    )
    monkeypatch.setattr("argus.api.routes_extract.extract_url", extract)
    transport = httpx.ASGITransport(
        app=create_app(
            broker=broker,
            search_repository=repository,
        )
    )

    def adapter():
        return HttpMcpAdapter(
            HttpAuthorityClient(
                AuthorityClientConfig(
                    "http://argus.internal",
                    "mcp-token",
                ),
                transport=transport,
            )
        )

    before_restart = adapter()
    after_restart = adapter()
    for process in (before_restart, after_restart):
        assert "duckduckgo" in await process.search_health()
        assert "remaining=100" in await process.search_budgets()
        assert "shared extraction" in await process.extract_content(
            "https://example.com/article"
        )

    assert extract.await_count == 2
    assert all(
        call.kwargs["repository"] is repository for call in extract.await_args_list
    )
    assert all(call.kwargs["caller"] == "mcp" for call in extract.await_args_list)


@pytest.mark.asyncio
async def test_mcp_restart_keeps_sessions_cooldowns_spend_and_outbox_acceptance(
    monkeypatch,
):
    import json

    from argus.api.main import create_app
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient
    from argus.config import reset_config
    from argus.mcp.http_adapter import HttpMcpAdapter
    from argus.models import SearchMode, SearchResponse

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "primary")
    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        json.dumps({"mcp": {"token": "mcp-token"}}),
    )
    reset_config()

    state = {"status": "enabled", "remaining": 100}
    broker = MagicMock()

    async def session_search(query, *, session_id, **kwargs):
        del kwargs
        return (
            SearchResponse(
                query=query.query,
                mode=SearchMode.DISCOVERY,
                results=[],
                search_run_id=f"run-{broker.search_with_session.await_count}",
            ),
            session_id,
        )

    broker.search_with_session = AsyncMock(side_effect=session_search)
    broker.get_provider_status.side_effect = lambda provider: {
        "effective_status": state["status"],
    }
    broker.budget_tracker.get_budget_limit.return_value = 100
    broker.spend_repository.provider_summary.side_effect = (
        lambda provider, budget_limit: {
            "remaining": state["remaining"],
            "argus_estimated_charge": 100 - state["remaining"],
            "uncertain_charge": 0,
        }
    )
    repository = MagicMock()
    transport = httpx.ASGITransport(
        app=create_app(
            broker=broker,
            search_repository=repository,
        )
    )

    def restarted_adapter():
        return HttpMcpAdapter(
            HttpAuthorityClient(
                AuthorityClientConfig(
                    "http://argus.internal",
                    "mcp-token",
                ),
                transport=transport,
            )
        )

    await restarted_adapter().search_web(
        query="first turn",
        session_id="durable-session",
    )
    state.update(status="temporarily_disabled_after_failures", remaining=75)
    after_restart = restarted_adapter()
    await after_restart.search_web(
        query="second turn",
        session_id="durable-session",
    )

    assert "temporarily_disabled_after_failures" in (
        await after_restart.search_health()
    )
    assert "remaining=75" in await after_restart.search_budgets()
    assert [
        call.kwargs["session_id"]
        for call in broker.search_with_session.await_args_list
    ] == ["durable-session", "durable-session"]
    # SearchLedgerRepository.accept is the transaction that also owns
    # user-visible Maya outbox intent creation.
    assert repository.accept.call_count == 2


def test_http_extraction_derives_caller_from_authentication(monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.config import reset_config
    from argus.extraction.models import ExtractedContent, ExtractorName

    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "primary")
    monkeypatch.setenv(
        "ARGUS_CALLER_CREDENTIALS_JSON",
        json.dumps({"mcp": {"token": "mcp-token"}}),
    )
    reset_config()
    extract = AsyncMock(
        return_value=ExtractedContent(
            url="https://example.com/article",
            text="content",
            word_count=1,
            extractor=ExtractorName.TRAFILATURA,
        )
    )
    monkeypatch.setattr("argus.api.routes_extract.extract_url", extract)
    client = TestClient(
        create_app(
            broker=MagicMock(),
            search_repository=MagicMock(),
        )
    )

    response = client.post(
        "/api/extract",
        headers={"Authorization": "Bearer mcp-token"},
        json={
            "url": "https://example.com/article",
            "caller": "spoofed-authority",
        },
    )

    assert response.status_code == 200
    assert extract.await_args.kwargs["caller"] == "mcp"
