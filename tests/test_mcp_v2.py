"""Explicit version-two MCP tool and release-contract tests."""

from __future__ import annotations

import hashlib
import json
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


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


def _success_envelope(result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "contract_version": "2.0",
        "outcome": "success",
        "request_id": "request-mcp-v2",
        "result": result or {"accepted": True},
        "error": None,
    }


def _outcome_envelope(outcome: str) -> dict[str, Any]:
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
            "status": 503,
            "detail": "Safe bounded failure",
            "instance": "urn:argus:request:request-mcp-v2",
            "code": outcome,
            "retryable": False,
            "retry_after_seconds": None,
        },
    }


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

    assert await client.request_v2(
        "/api/v2/search",
        payload={"query": "bounded"},
    ) == envelope
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
    from mcp.server.fastmcp import FastMCP

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

    mcp = FastMCP("v2-contract")
    register_v2_tools(
        mcp,
        Backend(),
        caller_identity=lambda: "principal",
        caller_token=lambda: "scoped",
    )
    registered = {
        tool.name: tool
        for tool in await mcp.list_tools()
        if tool.name.endswith("_v2")
    }

    assert tuple(registered) == V2_NAMES
    for name, tool in registered.items():
        expected = MCP_V2_TOOL_DESCRIPTOR["schemas"][name]
        assert hashlib.sha256(
            json.dumps(
                tool.inputSchema,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest() == expected["input_sha256"]
        assert hashlib.sha256(
            json.dumps(
                tool.outputSchema,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest() == expected["output_sha256"]

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
    assert result.structuredContent == envelope
    assert result.isError is False
    assert result.content[0].type == "text"
    assert "canonical" in result.content[0].text


@pytest.mark.asyncio
async def test_schema_invalid_v2_call_is_bounded_and_never_reaches_http():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

    from argus.mcp.v2_tools import register_v2_tools

    sentinel = "REJECTED-" + ("secret" * 100)

    class Backend:
        async def search_web_v2(self, **_kwargs):
            pytest.fail("HTTP execution must not run")

    mcp = FastMCP("v2-errors")
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

    assert MCP_V2_TOOL_DESCRIPTOR["transport_version"] == "2025-11-25"
    assert MCP_V2_TOOL_DESCRIPTOR["tool_contract_version"] == "2.0"
    assert tuple(MCP_V2_TOOL_DESCRIPTOR["tools"]) == V2_NAMES
    with pytest.raises(TypeError):
        MCP_V2_TOOL_DESCRIPTOR["tools"] = ()

    validate_complete_mcp_registration(
        MCP_TRANSPORT_DESCRIPTOR,
        MCP_V2_TOOL_DESCRIPTOR,
    )
    for field in ("transport_version", "tool_contract_version", "tools", "schemas"):
        mutated = deepcopy(
            _plain(MCP_V2_TOOL_DESCRIPTOR)
        )
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
    from mcp.server.fastmcp import FastMCP

    from argus.capabilities import (
        MCP_TRANSPORT_DESCRIPTOR,
        CapabilityManifestError,
        validate_complete_mcp_registration,
    )
    from argus.mcp.v2_tools import (
        actual_v2_tool_registration,
        register_v2_tools,
    )

    mcp = FastMCP("v2-registration")
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
    assert rendered.structuredContent == envelope


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

    assert rendered.structuredContent == envelope
    assert rendered.isError is (
        outcome not in {"success", "degraded", "empty"}
    )
    if outcome in {"degraded", "empty"}:
        assert f"Argus outcome: {outcome}" in rendered.content[0].text
