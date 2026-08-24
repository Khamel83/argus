"""Explicit version-two MCP tool and release-contract tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx
import pytest


V2_NAMES = (
    "search_web_v2",
    "recover_url_v2",
    "expand_links_v2",
    "extract_content_v2",
)
V1_SCHEMA_DIGESTS = {
    "search_web": (
        "9a9a980419b13d9c9c3862376b631c38a9aeea0f29f50ecf5b42055707903249",
        "30a5b813599d72ad35d1e428b7f151186c6cb52b5b1c6ebb1220d1222a5afbc9",
    ),
    "recover_url": (
        "8388f2e1661488071cea18b5a67e2d7d80f796466fb467f9e3b3f9dd20e0dc18",
        "a3820f7ee164918ce2f5edc08dc88b615e4d64e0344cf86668b9b841f7c88c6f",
    ),
    "expand_links": (
        "0f2dd39b7fd78cd131d7b9c30f0e080c8d1d08564e0d6a66638e75140e63e504",
        "a8964ce04df285f5191d38a8e2b3a99f0d58be9d04296f00e193bcdf0e14dd7e",
    ),
    "extract_content": (
        "e732ac17d12cb41d979366b714d213622887aac448c1b90e310d3e72f8668f9b",
        "3192284eacf752ff4cf63ad79f16b54c03251529ff8186a00d3a5ab082a6ffd7",
    ),
}


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


def _schema_digest(schema):
    return hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _capture_real_mcp_server(monkeypatch, backend, *, standalone=False):
    from mcp.server import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    import argus.mcp.server as server

    created = []

    def factory(*args, **kwargs):
        instance = MCPServer(*args, **kwargs)
        instance.run = lambda **_kwargs: None

        # v2 moved transport_security off the constructor and onto
        # streamable_http_app(), so inject it there instead.
        build_app = instance.streamable_http_app

        def app_with_test_host(**app_kwargs):
            app_kwargs.setdefault(
                "transport_security",
                TransportSecuritySettings(
                    enable_dns_rebinding_protection=True,
                    allowed_hosts=["testserver"],
                ),
            )
            return build_app(**app_kwargs)

        instance.streamable_http_app = app_with_test_host
        created.append(instance)
        return instance

    monkeypatch.setattr("mcp.server.MCPServer", factory)
    if standalone:
        from argus.development_mcp_server import serve_development_mcp

        monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
        monkeypatch.setenv("ARGUS_ENV", "development")
        monkeypatch.setenv("ARGUS_MCP_STANDALONE", "true")
        monkeypatch.setattr(
            "argus.development_mcp_adapter.build_development_mcp_backend",
            lambda: backend,
        )
        serve_development_mcp(transport="stdio")
    else:
        monkeypatch.setattr(server, "build_mcp_backend", lambda: backend)
        server.serve_mcp(transport="stdio")
    return created[0]


def _success_envelope(result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "contract_version": "2.0",
        "outcome": "success",
        "request_id": "request-mcp-v2",
        "result": result or {"accepted": True},
        "error": None,
    }


def _outcome_envelope(outcome: str) -> dict[str, Any]:
    from argus.contracts import CanonicalOutcome, http_status_for

    if outcome in {"success", "degraded", "empty"}:
        envelope = _success_envelope()
        envelope["outcome"] = outcome
        return envelope
    return {
        "contract_version": "2.0",
        "outcome": outcome,
        "request_id": "request-mcp-v2",
        "result": None,
        "error": {
            "type": f"urn:argus:problem:{outcome}",
            "title": outcome.replace("_", " ").title(),
            "status": http_status_for(CanonicalOutcome(outcome), outcome),
            "detail": "Safe bounded failure",
            "instance": "urn:argus:request:request-mcp-v2",
            "code": outcome,
            "retryable": False,
            "retry_after_seconds": None,
        },
    }


def _partial_failure_envelope() -> dict[str, Any]:
    envelope = _outcome_envelope("providers_failed")
    envelope["result"] = {
        "query": "partial failure",
        "mode": "research",
        "results": [
            {
                "title": "Accepted partial evidence",
                "url": "https://example.com/partial",
                "snippet": "Evidence retained before the provider floor failed",
            }
        ],
        "traces": [],
        "total_results": 1,
        "cached": False,
        "budget_warnings": [],
        "session_id": None,
    }
    return envelope


@dataclass(frozen=True)
class _Selection:
    contract_version: str | None
    base_path: str | None
    outcome: str


class _FakeAuthorityClient:
    def __init__(self, envelope: dict[str, Any]):
        self.envelope = envelope
        self.calls: list[tuple[str, Any]] = []

    async def resolve_http_contract(self, deployment_id, clock, *, refresh=False):
        self.calls.append(("discover", (deployment_id, refresh, clock())))
        return _Selection("2.0", "/api/v2", "ready")

    async def request_v2(self, path, *, payload, token=None):
        self.calls.append(("post", (path, payload, token)))
        return self.envelope


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "arguments", "path", "payload"),
    (
        (
            "search_web_v2",
            {
                "query": "canonical",
                "mode": "research",
                "max_results": 5,
                "session_id": "session-1",
                "include_attribution": True,
                "free_only": True,
                "caller_label": "maya",
                "caller_identity": "principal",
                "token": "scoped",
            },
            "/api/v2/search",
            {
                "query": "canonical",
                "mode": "research",
                "max_results": 5,
                "session_id": "session-1",
                "include_attribution": True,
                "free_only": True,
                "caller": "maya",
            },
        ),
        (
            "recover_url_v2",
            {
                "url": "https://example.com/gone",
                "title": "Gone",
                "domain": "example.com",
                "caller_label": "maya",
                "caller_identity": "principal",
                "token": "scoped",
            },
            "/api/v2/recover-url",
            {
                "url": "https://example.com/gone",
                "title": "Gone",
                "domain": "example.com",
            },
        ),
        (
            "expand_links_v2",
            {
                "query": "canonical",
                "context": "primary sources",
                "caller_label": "maya",
                "caller_identity": "principal",
                "token": "scoped",
            },
            "/api/v2/expand",
            {"query": "canonical", "context": "primary sources"},
        ),
        (
            "extract_content_v2",
            {
                "url": "https://example.com/article",
                "domain": "example.com",
                "caller_label": "maya",
                "caller_identity": "principal",
                "token": "scoped",
            },
            "/api/v2/extract",
            {
                "url": "https://example.com/article",
                "domain": "example.com",
                "caller": "maya",
            },
        ),
    ),
)
async def test_v2_adapter_discovers_before_one_exact_post_without_v1_fallback(
    method,
    arguments,
    path,
    payload,
):
    from argus.mcp.http_adapter import HttpMcpAdapter

    envelope = _success_envelope()
    client = _FakeAuthorityClient(envelope)
    adapter = HttpMcpAdapter(client)

    result = await getattr(adapter, method)(**arguments)

    assert result == envelope
    assert [kind for kind, _ in client.calls] == ["discover", "post"]
    assert client.calls[1] == ("post", (path, payload, "scoped"))


@pytest.mark.asyncio
async def test_v2_adapter_does_not_post_when_discovery_is_unready():
    from argus.mcp.http_adapter import HttpMcpAdapter

    client = _FakeAuthorityClient(_success_envelope())

    async def unready(*_args, **_kwargs):
        client.calls.append(("discover", None))
        return _Selection(None, None, "unready")

    client.resolve_http_contract = unready

    result = await HttpMcpAdapter(client).search_web_v2(query="never execute")

    assert client.calls == [("discover", None)]
    assert result["contract_version"] == "2.0"
    assert result["outcome"] == "unready"
    assert result["result"] is None
    assert result["error"]["code"] == "unready"
    assert result["error"]["detail"] == "Argus HTTP contract discovery is unavailable"


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", (-1, 0))
async def test_shared_authority_reader_accepts_chunked_body_through_11_mib(offset):
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient

    bound = 11 * 1024 * 1024
    envelope = _success_envelope({"padding": ""})
    encoded = json.dumps(envelope, separators=(",", ":")).encode()
    padding = bound + offset - len(encoded)
    envelope["result"]["padding"] = "x" * padding
    encoded = json.dumps(envelope, separators=(",", ":")).encode()
    assert len(encoded) == bound + offset
    stream = _ChunkStream([encoded[:1024], encoded[1024:]])

    async def handler(_request):
        return httpx.Response(200, stream=stream)

    client = HttpAuthorityClient(
        AuthorityClientConfig("https://authority.example", "token"),
        transport=httpx.MockTransport(handler),
    )

    assert (
        await client.request_v2(
            "/api/v2/search",
            payload={"query": "bounded"},
        )
        == envelope
    )
    assert stream.yielded == 2
    assert stream.closed is True


@pytest.mark.asyncio
async def test_shared_authority_reader_aborts_one_byte_over_11_mib_before_parse():
    from argus.authority import (
        AuthorityClientConfig,
        AuthorityRequestError,
        HttpAuthorityClient,
    )

    bound = 11 * 1024 * 1024
    stream = _ChunkStream(
        [
            b"{" + (b"x" * (bound - 1)),
            b"x",
            b"this chunk must not be consumed",
        ]
    )

    async def handler(_request):
        return httpx.Response(200, stream=stream)

    client = HttpAuthorityClient(
        AuthorityClientConfig("https://authority.example", "token"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AuthorityRequestError, match="size limit"):
        await client.request_v2(
            "/api/v2/search",
            payload={"query": "bounded"},
        )

    assert stream.yielded == 2
    assert stream.closed is True


@pytest.mark.asyncio
async def test_v2_tools_register_exact_schemas_and_return_exact_envelope():
    from mcp.server import MCPServer

    from argus.capabilities import MCP_V2_TOOL_DESCRIPTOR
    from argus.mcp.v2_tools import register_v2_tools

    class Backend:
        async def search_web_v2(self, **_kwargs):
            return _success_envelope(
                {
                    "query": "canonical",
                    "mode": "discovery",
                    "results": [],
                    "traces": [],
                    "total_results": 0,
                    "cached": False,
                    "budget_warnings": [],
                    "session_id": None,
                }
            )

    mcp = MCPServer("v2-contract")
    register_v2_tools(
        mcp,
        Backend(),
        caller_identity=lambda: "principal",
        caller_token=lambda: "scoped",
    )
    registered = {
        tool.name: tool for tool in await mcp.list_tools() if tool.name.endswith("_v2")
    }

    assert tuple(registered) == V2_NAMES
    for name, tool in registered.items():
        expected = MCP_V2_TOOL_DESCRIPTOR["schemas"][name]
        assert (
            hashlib.sha256(
                json.dumps(
                    tool.input_schema,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            == expected["input_sha256"]
        )
        assert (
            hashlib.sha256(
                json.dumps(
                    tool.output_schema,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            == expected["output_sha256"]
        )

    result = await mcp.call_tool(
        "search_web_v2",
        {"query": "canonical"},
    )
    envelope = _success_envelope(
        {
            "query": "canonical",
            "mode": "discovery",
            "results": [],
            "traces": [],
            "total_results": 0,
            "cached": False,
            "budget_warnings": [],
            "session_id": None,
        }
    )
    assert result.structured_content == envelope
    assert result.is_error is False
    assert result.content[0].type == "text"
    assert "canonical" in result.content[0].text


@pytest.mark.asyncio
async def test_schema_invalid_v2_call_is_bounded_and_never_reaches_http():
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    from argus.mcp.v2_tools import register_v2_tools

    sentinel = "REJECTED-" + ("secret" * 100)

    class Backend:
        async def search_web_v2(self, **_kwargs):
            pytest.fail("HTTP execution must not run")

    mcp = MCPServer("v2-errors")
    register_v2_tools(
        mcp,
        Backend(),
        caller_identity=lambda: "principal",
        caller_token=lambda: "scoped",
    )

    with pytest.raises(ToolError) as raised:
        await mcp.call_tool(
            "search_web_v2",
            {"query": sentinel, "max_results": 51},
        )

    message = str(raised.value)
    assert sentinel not in message
    assert len(message) <= 256
    assert "input schema" in message.lower()


def test_complete_mcp_release_descriptor_is_immutable_and_fail_closed():
    from argus.capabilities import (
        MCP_TRANSPORT_DESCRIPTOR,
        MCP_V2_TOOL_DESCRIPTOR,
        validate_complete_mcp_registration,
    )

    assert MCP_V2_TOOL_DESCRIPTOR["transport_version"] == "2026-07-28"
    assert MCP_V2_TOOL_DESCRIPTOR["tool_contract_version"] == "2.0"
    assert tuple(MCP_V2_TOOL_DESCRIPTOR["tools"]) == V2_NAMES
    with pytest.raises(TypeError):
        MCP_V2_TOOL_DESCRIPTOR["tools"] = ()

    validate_complete_mcp_registration(
        MCP_TRANSPORT_DESCRIPTOR,
        MCP_V2_TOOL_DESCRIPTOR,
    )
    for field in ("transport_version", "tool_contract_version", "tools", "schemas"):
        mutated = deepcopy(_plain(MCP_V2_TOOL_DESCRIPTOR))
        mutated[field] = "mismatch"
        with pytest.raises(Exception, match="release manifest"):
            validate_complete_mcp_registration(
                MCP_TRANSPORT_DESCRIPTOR,
                mutated,
            )


def test_http_manifest_independently_suppresses_v2_suffix_on_mcp_drift():
    from argus.capabilities import (
        MCP_V2_TOOL_DESCRIPTOR,
        http_capability_manifest,
    )

    registrations = {
        "accepted_service",
        "legacy_presenter",
        "v2_presenter",
        "v2_routes",
        "transport_security",
    }
    supported = http_capability_manifest(
        evidence_enabled=True,
        registrations=registrations,
    ).as_dict()
    assert supported["mcp_contract"] == {
        "transport": "streamable-http",
        "endpoint": "/mcp",
        "argus_tool_contract_versions": ["1", "2.0"],
        "version_2_tool_suffix": "_v2",
    }

    mismatched = _plain(MCP_V2_TOOL_DESCRIPTOR)
    mismatched["schemas"]["search_web_v2"]["input_sha256"] = "0" * 64
    suppressed = http_capability_manifest(
        evidence_enabled=True,
        registrations=registrations,
        mcp_tool_registration=mismatched,
    ).as_dict()
    assert suppressed["mcp_contract"] == {
        "transport": "streamable-http",
        "endpoint": "/mcp",
        "argus_tool_contract_versions": ["1"],
    }


def test_actual_bound_tool_schema_drift_refuses_complete_registration():
    from mcp.server import MCPServer

    from argus.capabilities import (
        MCP_TRANSPORT_DESCRIPTOR,
        CapabilityManifestError,
        validate_complete_mcp_registration,
    )
    from argus.mcp.v2_tools import (
        actual_v2_tool_registration,
        register_v2_tools,
    )

    mcp = MCPServer("v2-registration")
    register_v2_tools(
        mcp,
        object(),
        caller_identity=lambda: "principal",
        caller_token=lambda: None,
    )
    validate_complete_mcp_registration(
        MCP_TRANSPORT_DESCRIPTOR,
        actual_v2_tool_registration(mcp),
    )

    mcp._tool_manager._tools.pop("extract_content_v2")
    with pytest.raises(CapabilityManifestError, match="release manifest"):
        validate_complete_mcp_registration(
            MCP_TRANSPORT_DESCRIPTOR,
            actual_v2_tool_registration(mcp),
        )


def test_v2_text_rendering_is_bounded_in_utf8_bytes_without_changing_envelope():
    from argus.mcp.v2_tools import _result

    envelope = _success_envelope(
        {
            "url": "https://example.com/article",
            "title": "Article",
            "text": "🧭" * (64 * 1024),
        }
    )

    rendered = _result("extract_content_v2", envelope)

    assert len(rendered.content[0].text.encode("utf-8")) <= 64 * 1024
    assert rendered.structured_content == envelope


@pytest.mark.parametrize(
    "outcome",
    (
        "success",
        "degraded",
        "empty",
        "invalid_request",
        "authentication_rejected",
        "policy_rejected",
        "timeout",
        "persistence_failed",
        "providers_failed",
        "extraction_failed",
        "unready",
    ),
)
def test_v2_is_error_is_false_only_for_success_degraded_and_empty(outcome):
    from argus.mcp.v2_tools import _result

    envelope = _outcome_envelope(outcome)
    rendered = _result("search_web_v2", envelope)

    assert rendered.structured_content == envelope
    assert rendered.is_error is (outcome not in {"success", "degraded", "empty"})
    if outcome in {"degraded", "empty"}:
        assert f"Argus outcome: {outcome}" in rendered.content[0].text


def test_v2_failure_preserves_partial_evidence_and_labels_bounded_text():
    from argus.mcp.v2_tools import _result

    envelope = _partial_failure_envelope()

    rendered = _result("search_web_v2", envelope)

    assert rendered.structured_content == envelope
    assert rendered.is_error is True
    assert "failure outcome: providers_failed" in rendered.content[0].text
    assert "Accepted partial evidence" in rendered.content[0].text
    assert len(rendered.content[0].text.encode("utf-8")) <= 64 * 1024


def test_standalone_development_registers_only_usable_v1_tools(monkeypatch):
    from argus.development_mcp_adapter import LocalMcpAdapter

    registered = _capture_real_mcp_server(
        monkeypatch,
        LocalMcpAdapter(object()),
        standalone=True,
    )
    names = {tool.name for tool in registered._tool_manager.list_tools()}
    assert {
        "search_web",
        "recover_url",
        "expand_links",
        "extract_content",
        "valyu_answer",
        "capture_site",
        "build_research_pack",
        "read_pack_file",
    }.issubset(names)
    assert not any(name.endswith("_v2") for name in names)


@pytest.mark.asyncio
async def test_production_build_tool_forwards_targeted_request_and_principal(
    monkeypatch,
):
    from mcp.server.auth.provider import AccessToken

    from argus.mcp.http_adapter import HttpMcpAdapter
    import argus.mcp.server as server

    observed = {}

    class RecordingBackend(HttpMcpAdapter):
        def __init__(self):
            pass

        async def build_research_pack(self, topic, **kwargs):
            observed["topic"] = topic
            observed.update(kwargs)
            return "safe-start"

        async def search_health(self, **_kwargs):
            return "healthy"

        async def search_budgets(self, **_kwargs):
            return "budgets"

    monkeypatch.setattr(
        server,
        "_mcp_access_token",
        lambda: AccessToken(
            token="protected-token",
            client_id="authenticated-principal",
            scopes=["mcp"],
        ),
    )
    registered = _capture_real_mcp_server(monkeypatch, RecordingBackend())
    target = {
        "name": "Example",
        "source_prefixes": ["https://example.com/docs/"],
        "requirements": [
            {"claim_class": "capabilities", "query": "what it does"}
        ],
    }

    _called = await registered.call_tool(
        "build_research_pack",
        {
            "topic": "Targeted research",
            "research_targets": [target],
            "free_only": True,
            "caller": "tonight-acceptance-v3",
            "response_format": "json",
        },
    )
    content, structured = _called.content, _called.structured_content

    assert content[0].text == "safe-start"
    assert structured == {"result": "safe-start"}
    assert observed == {
        "topic": "Targeted research",
        "official_url": None,
        "max_research_pages": 40,
        "research_targets": [target],
        "free_only": True,
        "response_format": "json",
        "caller_label": "tonight-acceptance-v3",
        "caller_identity": "authenticated-principal",
        "token": "protected-token",
    }

    tool = next(
        tool
        for tool in registered._tool_manager.list_tools()
        if tool.name == "build_research_pack"
    )
    assert {
        "research_targets",
        "free_only",
    }.issubset(tool.parameters["properties"])


@pytest.mark.asyncio
async def test_standalone_build_tool_validates_and_forwards_targeted_request(
    monkeypatch,
):
    from argus import development_mcp_tools as tools

    observed = {}

    class FakeService:
        def __init__(self, *_args, **kwargs):
            observed["service_caller"] = kwargs["caller"]

        async def build_research_pack(self, **kwargs):
            observed["request"] = kwargs
            return object()

    monkeypatch.setattr(tools, "WorkflowService", FakeService)
    monkeypatch.setattr(
        tools,
        "_serialize_workflow",
        lambda _result: "standalone-result",
    )
    target = {
        "name": "Example",
        "source_prefixes": ["https://example.com/docs/"],
        "requirements": [
            {"claim_class": "capabilities", "query": "what it does"}
        ],
    }

    result = await tools.build_research_pack(
        object(),
        "Targeted research",
        research_targets=[target],
        free_only=True,
        caller_identity="authenticated-principal",
        caller_label="tonight-acceptance-v3",
    )

    assert result == "standalone-result"
    assert observed == {
        "service_caller": "authenticated-principal",
        "request": {
            "topic": "Targeted research",
            "official_url": None,
            "max_research_pages": 40,
            "research_targets": [target],
            "free_only": True,
            "caller_identity": "authenticated-principal",
            "caller_label": "tonight-acceptance-v3",
        },
    }


@pytest.mark.parametrize("standalone_value", (None, "false", "0"))
def test_direct_development_launcher_rejects_without_explicit_opt_in(
    monkeypatch,
    standalone_value,
):
    from argus.authority import AuthorityConfigurationError
    from argus.development_mcp_server import serve_development_mcp

    monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
    monkeypatch.setenv("ARGUS_ENV", "development")
    if standalone_value is None:
        monkeypatch.delenv("ARGUS_MCP_STANDALONE", raising=False)
    else:
        monkeypatch.setenv("ARGUS_MCP_STANDALONE", standalone_value)
    monkeypatch.setattr(
        "argus.development_mcp_adapter.build_development_mcp_backend",
        lambda: pytest.fail("rejected launch must not construct a broker"),
    )

    with pytest.raises(AuthorityConfigurationError, match="standalone"):
        serve_development_mcp()


def test_direct_development_launcher_accepts_explicit_opt_in(monkeypatch):
    from argus.development_mcp_server import serve_development_mcp

    backend = object()
    observed = {}
    monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setenv("ARGUS_MCP_STANDALONE", "true")
    monkeypatch.setattr(
        "argus.development_mcp_adapter.build_development_mcp_backend",
        lambda: backend,
    )
    monkeypatch.setattr(
        "argus.mcp.server.serve_mcp",
        lambda **kwargs: observed.update(kwargs),
    )

    serve_development_mcp(
        transport="streamable-http",
        host="127.0.0.1",
        port=9001,
    )

    assert observed["backend"] is backend
    assert observed["transport"] == "streamable-http"
    assert observed["host"] == "127.0.0.1"
    assert observed["port"] == 9001
    assert callable(observed["additional_registration"])


@pytest.mark.parametrize("standalone_value", (None, "false"))
@pytest.mark.parametrize("injection", ("local-backend", "development-registration"))
def test_direct_serve_mcp_rejects_injected_development_before_listener_work(
    monkeypatch,
    standalone_value,
    injection,
):
    from argus.authority import AuthorityConfigurationError
    from argus.mcp.http_adapter import HttpMcpAdapter
    import argus.mcp.server as server

    class GuardedLocalBackend:
        def __getattribute__(self, name):
            pytest.fail(f"rejected backend must not be used: {name}")

    monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
    monkeypatch.setenv("ARGUS_AUTOLOAD_DOTENV", "false")
    monkeypatch.setenv("ARGUS_ENV", "development")
    if standalone_value is None:
        monkeypatch.delenv("ARGUS_MCP_STANDALONE", raising=False)
    else:
        monkeypatch.setenv("ARGUS_MCP_STANDALONE", standalone_value)
    monkeypatch.setattr(
        "mcp.server.MCPServer",
        lambda *_args, **_kwargs: pytest.fail(
            "rejected injection must not construct a listener"
        ),
    )

    if injection == "local-backend":
        kwargs = {"backend": GuardedLocalBackend()}
    else:
        kwargs = {
            "backend": object.__new__(HttpMcpAdapter),
            "additional_registration": lambda *_args, **_kwargs: pytest.fail(
                "rejected development registration must not run"
            ),
        }

    with pytest.raises(AuthorityConfigurationError, match="standalone"):
        server.serve_mcp(transport="stdio", **kwargs)


def test_direct_serve_mcp_accepts_injected_development_with_explicit_opt_in(
    monkeypatch,
):
    import argus.mcp.server as server

    observed = {}
    backend = object()

    class FakeMCPServer:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self):
            return lambda function: function

        def run(self, **kwargs):
            observed["run"] = kwargs

    def register(_mcp, registered_backend, *, caller_identity):
        observed["backend"] = registered_backend
        observed["caller_identity"] = caller_identity

    monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
    monkeypatch.setenv("ARGUS_AUTOLOAD_DOTENV", "false")
    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setenv("ARGUS_MCP_STANDALONE", "true")
    monkeypatch.setattr("mcp.server.MCPServer", FakeMCPServer)

    server.serve_mcp(
        transport="stdio",
        backend=backend,
        additional_registration=register,
    )

    assert observed["backend"] is backend
    assert callable(observed["caller_identity"])
    assert observed["run"] == {"transport": "stdio"}


@pytest.mark.parametrize("standalone_value", (None, "false"))
def test_direct_development_backend_builder_rejects_without_opt_in(
    monkeypatch,
    standalone_value,
):
    from argus.authority import AuthorityConfigurationError
    from argus.development_mcp_adapter import build_development_mcp_backend

    monkeypatch.delenv("ARGUS_AUTHORITY_URL", raising=False)
    monkeypatch.setenv("ARGUS_ENV", "development")
    if standalone_value is None:
        monkeypatch.delenv("ARGUS_MCP_STANDALONE", raising=False)
    else:
        monkeypatch.setenv("ARGUS_MCP_STANDALONE", standalone_value)
    monkeypatch.setattr(
        "argus.broker.router.create_broker",
        lambda: pytest.fail("rejected builder must not construct a broker"),
    )

    with pytest.raises(AuthorityConfigurationError, match="standalone"):
        build_development_mcp_backend()


@pytest.mark.asyncio
async def test_real_v1_registrations_replay_exact_schema_and_result_goldens(
    monkeypatch,
):
    from argus.mcp.http_adapter import HttpMcpAdapter

    expected = {
        "search_web": "frozen-search",
        "recover_url": "frozen-recovery",
        "expand_links": "frozen-expansion",
        "extract_content": "frozen-extraction",
    }

    class ReplayBackend(HttpMcpAdapter):
        def __init__(self):
            pass

        async def search_web(self, **_kwargs):
            return expected["search_web"]

        async def recover_url(self, *_args, **_kwargs):
            return expected["recover_url"]

        async def expand_links(self, *_args, **_kwargs):
            return expected["expand_links"]

        async def extract_content(self, *_args, **_kwargs):
            return expected["extract_content"]

        async def search_health(self, **_kwargs):
            return "healthy"

        async def search_budgets(self, **_kwargs):
            return "budgets"

    registered = _capture_real_mcp_server(monkeypatch, ReplayBackend())
    tools = {tool.name: tool for tool in registered._tool_manager.list_tools()}
    arguments = {
        "search_web": {"query": "canonical"},
        "recover_url": {"url": "https://example.com/gone"},
        "expand_links": {"query": "canonical"},
        "extract_content": {"url": "https://example.com/article"},
    }

    for name, text in expected.items():
        input_digest, output_digest = V1_SCHEMA_DIGESTS[name]
        assert _schema_digest(tools[name].parameters) == input_digest
        assert _schema_digest(tools[name].output_schema) == output_digest
        _called = await registered.call_tool(
            name,
            arguments[name],
        )
        content = _called.content
        structured_content = _called.structured_content
        assert content[0].model_dump(exclude_none=True) == {
            "type": "text",
            "text": text,
        }
        assert structured_content == {"result": text}


def test_real_listener_schema_rejection_is_bounded_and_never_calls_backend(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from argus.mcp.http_adapter import HttpMcpAdapter

    sentinel = "REJECTED-" + ("private" * 100)

    class CountingBackend(HttpMcpAdapter):
        def __init__(self):
            self.v2_calls = 0

        async def search_web_v2(self, **_kwargs):
            self.v2_calls += 1
            pytest.fail("schema-invalid calls must not reach the backend")

        async def search_health(self, **_kwargs):
            return "healthy"

        async def search_budgets(self, **_kwargs):
            return "budgets"

    backend = CountingBackend()
    registered = _capture_real_mcp_server(monkeypatch, backend)
    with TestClient(registered.streamable_http_app()) as client:
        initialized = client.post(
            "/mcp",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "fixture", "version": "1"},
                },
            },
        )
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "mcp-session-id": initialized.headers["mcp-session-id"],
            "mcp-protocol-version": "2025-11-25",
        }
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search_web_v2",
                    "arguments": {
                        "query": sentinel,
                        "max_results": 51,
                    },
                },
            },
        )

    event_data = next(
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    )
    result = json.loads(event_data)["result"]
    assert response.status_code == 200
    assert result["isError"] is True
    assert "structuredContent" not in result
    assert sentinel not in result["content"][0]["text"]
    assert len(result["content"][0]["text"].encode()) <= 256
    assert backend.v2_calls == 0


def test_missing_pinned_sdk_tool_manager_fails_closed():
    from argus.capabilities import CapabilityManifestError
    from argus.mcp.v2_tools import (
        actual_v2_tool_registration,
        register_v2_tools,
    )

    class ManagerlessMcp:
        def tool(self):
            return lambda function: function

    mcp = ManagerlessMcp()
    with pytest.raises(CapabilityManifestError, match="tool manager"):
        register_v2_tools(
            mcp,
            object(),
            caller_identity=lambda: "principal",
            caller_token=lambda: None,
        )
    with pytest.raises(CapabilityManifestError, match="tool manager"):
        actual_v2_tool_registration(mcp)


@pytest.mark.parametrize(
    "envelope",
    (
        {
            "contract_version": "2.0",
            "outcome": "success",
            "request_id": "request-invariant",
            "result": None,
            "error": None,
        },
        {
            "contract_version": "2.0",
            "outcome": "empty",
            "request_id": "request-invariant",
            "result": {"accepted": True},
            "error": {
                "type": "urn:argus:problem:unready",
                "title": "Unready",
                "status": 503,
                "detail": "Invalid mixed envelope",
                "instance": "urn:argus:request:request-invariant",
                "code": "unready",
                "retryable": False,
                "retry_after_seconds": None,
            },
        },
        {
            "contract_version": "2.0",
            "outcome": "unready",
            "request_id": "request-invariant",
            "result": None,
            "error": None,
        },
    ),
)
def test_mcp_v2_rejects_http_envelopes_that_violate_result_error_invariants(
    envelope,
):
    from pydantic import ValidationError

    from argus.mcp.v2_tools import _result

    with pytest.raises(ValidationError, match="outcome"):
        _result("search_web_v2", envelope)


def test_real_mcp_tool_presents_partial_failure_evidence_end_to_end(monkeypatch):
    from fastapi.testclient import TestClient

    from argus.mcp.http_adapter import HttpMcpAdapter

    envelope = _partial_failure_envelope()

    class PartialFailureBackend(HttpMcpAdapter):
        def __init__(self):
            pass

        async def search_web_v2(self, **_kwargs):
            return envelope

        async def search_health(self, **_kwargs):
            return "healthy"

        async def search_budgets(self, **_kwargs):
            return "budgets"

    registered = _capture_real_mcp_server(monkeypatch, PartialFailureBackend())
    with TestClient(registered.streamable_http_app()) as client:
        initialized = client.post(
            "/mcp",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "fixture", "version": "1"},
                },
            },
        )
        response = client.post(
            "/mcp",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
                "mcp-session-id": initialized.headers["mcp-session-id"],
                "mcp-protocol-version": "2025-11-25",
            },
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search_web_v2",
                    "arguments": {"query": "partial failure"},
                },
            },
        )

    event_data = next(
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    )
    result = json.loads(event_data)["result"]
    assert response.status_code == 200
    assert result["structuredContent"] == envelope
    assert result["isError"] is True
    assert "failure outcome: providers_failed" in result["content"][0]["text"]
    assert "Accepted partial evidence" in result["content"][0]["text"]


def _mutate_leaf(document, path):
    mutated = deepcopy(document)
    target = mutated
    for part in path[:-1]:
        target = target[part]
    leaf = target[path[-1]]
    if isinstance(leaf, bool):
        target[path[-1]] = not leaf
    elif isinstance(leaf, int):
        target[path[-1]] = leaf + 1
    else:
        target[path[-1]] = f"{leaf}-mutated"
    return mutated


def _leaf_paths(value, prefix=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _leaf_paths(child, (*prefix, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaf_paths(child, (*prefix, index))
    else:
        yield prefix


def test_http_and_mcp_independently_reject_every_release_descriptor_mutation(
    tmp_path,
):
    from argus.capabilities import (
        MCP_RELEASE_DESCRIPTOR_PATH,
        MCP_TRANSPORT_DESCRIPTOR,
        MCP_V2_TOOL_DESCRIPTOR,
        CapabilityManifestError,
        http_capability_manifest,
        validate_complete_mcp_registration,
    )

    registrations = {
        "accepted_service",
        "legacy_presenter",
        "v2_presenter",
        "v2_routes",
        "transport_security",
    }
    release = json.loads(MCP_RELEASE_DESCRIPTOR_PATH.read_text())
    validate_complete_mcp_registration(
        MCP_TRANSPORT_DESCRIPTOR,
        MCP_V2_TOOL_DESCRIPTOR,
    )

    for index, path in enumerate(_leaf_paths(release)):
        candidate = tmp_path / f"mutated-{index}.json"
        candidate.write_text(json.dumps(_mutate_leaf(release, path)))
        capability = http_capability_manifest(
            evidence_enabled=True,
            registrations=registrations,
            release_descriptor_path=candidate,
        ).as_dict()
        assert capability["mcp_contract"]["argus_tool_contract_versions"] == ["1"], path
        with pytest.raises(
            CapabilityManifestError,
            match="release .*descriptor",
        ):
            validate_complete_mcp_registration(
                MCP_TRANSPORT_DESCRIPTOR,
                MCP_V2_TOOL_DESCRIPTOR,
                release_descriptor_path=candidate,
            )

    http_process = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path
from argus.capabilities import http_capability_manifest

registrations = {
    "accepted_service",
    "legacy_presenter",
    "v2_presenter",
    "v2_routes",
    "transport_security",
}
valid = http_capability_manifest(
    evidence_enabled=True,
    registrations=registrations,
).as_dict()
assert valid["mcp_contract"]["argus_tool_contract_versions"] == ["1", "2.0"]
for candidate in sorted(Path(sys.argv[1]).glob("mutated-*.json")):
    manifest = http_capability_manifest(
        evidence_enabled=True,
        registrations=registrations,
        release_descriptor_path=candidate,
    ).as_dict()
    assert manifest["mcp_contract"]["argus_tool_contract_versions"] == ["1"]
""",
            str(tmp_path),
        ],
        cwd=MCP_RELEASE_DESCRIPTOR_PATH.parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert http_process.returncode == 0, http_process.stderr

    mcp_process = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from pathlib import Path
from mcp.server import MCPServer
from argus.capabilities import (
    CapabilityManifestError,
    MCP_TRANSPORT_DESCRIPTOR,
    validate_complete_mcp_registration,
)
from argus.mcp.v2_tools import (
    actual_v2_tool_registration,
    register_v2_tools,
)

mcp = MCPServer("release-validation")
register_v2_tools(
    mcp,
    object(),
    caller_identity=lambda: "principal",
    caller_token=lambda: None,
)
registration = actual_v2_tool_registration(mcp)
validate_complete_mcp_registration(MCP_TRANSPORT_DESCRIPTOR, registration)
for candidate in sorted(Path(sys.argv[1]).glob("mutated-*.json")):
    try:
        validate_complete_mcp_registration(
            MCP_TRANSPORT_DESCRIPTOR,
            registration,
            release_descriptor_path=candidate,
        )
    except CapabilityManifestError:
        continue
    raise AssertionError(f"accepted mutated descriptor: {candidate}")
""",
            str(tmp_path),
        ],
        cwd=MCP_RELEASE_DESCRIPTOR_PATH.parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert mcp_process.returncode == 0, mcp_process.stderr


@pytest.mark.parametrize(
    ("artifact_state", "artifact_content", "expected_error"),
    (
        ("missing", None, "unavailable"),
        ("malformed", "{", "malformed"),
        (
            "structurally-incomplete",
            json.dumps(
                {
                    "descriptor_version": 1,
                    "transport_version": "2025-11-25",
                    "transport": {},
                    "tool_contract_version": "2.0",
                    "tools": [],
                }
            ),
            "transport fields are missing",
        ),
        ("hash-tampered", None, "digest does not match"),
    ),
)
def test_fresh_process_import_survives_invalid_packaged_release_descriptor(
    tmp_path,
    artifact_state,
    artifact_content,
    expected_error,
):
    from argus.capabilities import MCP_RELEASE_DESCRIPTOR_PATH

    isolated = tmp_path / artifact_state
    package = isolated / "argus"
    mcp_package = package / "mcp"
    mcp_package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (mcp_package / "__init__.py").write_text("")
    (package / "capabilities.py").write_text(
        (MCP_RELEASE_DESCRIPTOR_PATH.parents[1] / "capabilities.py").read_text()
    )
    descriptor = mcp_package / "release_descriptor.json"
    if artifact_state == "hash-tampered":
        tampered = json.loads(MCP_RELEASE_DESCRIPTOR_PATH.read_text())
        tampered["tool_contract_version"] = "2.0-tampered"
        descriptor.write_text(json.dumps(tampered))
    elif artifact_content is not None:
        descriptor.write_text(artifact_content)

    process = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import argus.capabilities as capabilities

registrations = {
    "accepted_service",
    "legacy_presenter",
    "v2_presenter",
    "v2_routes",
    "transport_security",
}
manifest = capabilities.http_capability_manifest(
    evidence_enabled=True,
    registrations=registrations,
).as_dict()
assert manifest["http_contracts"][-1]["version"] == "2.0"
assert manifest["mcp_contract"]["argus_tool_contract_versions"] == ["1"]
assert "version_2_tool_suffix" not in manifest["mcp_contract"]
try:
    capabilities.validate_mcp_transport_registration({})
except capabilities.CapabilityManifestError as error:
    assert sys.argv[1] in str(error), (sys.argv[1], str(error))
else:
    raise AssertionError("MCP startup accepted an invalid packaged descriptor")
""",
            expected_error,
        ],
        cwd=isolated,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
