"""MCP 2026-07-28 one-shot transport contract tests."""

from __future__ import annotations

import json

import httpx
import pytest


def test_release_descriptor_advertises_both_mcp_eras():
    from argus.capabilities import MCP_TRANSPORT_DESCRIPTOR, MCP_V2_TOOL_DESCRIPTOR

    assert MCP_V2_TOOL_DESCRIPTOR["transport_version"] == "2026-07-28"
    assert "2025-11-25" in MCP_TRANSPORT_DESCRIPTOR["protocol_versions"]
    assert "2026-07-28" in MCP_TRANSPORT_DESCRIPTOR["protocol_versions"]


class _Backend:
    def __init__(self):
        self.calls = []

    async def search_web(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps({"query": kwargs["query"], "results": [], "traces": []})


def _modern_headers():
    return {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": "2026-07-28",
        "mcp-method": "tools/call",
        "mcp-name": "search_web",
    }


def _modern_call():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "search_web",
            "arguments": {"query": "stateless MCP", "max_results": 1},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {
                    "name": "argus-test",
                    "version": "1",
                },
            },
        },
    }


def _legacy_initialize():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "legacy-test", "version": "1"},
        },
    }


@pytest.mark.asyncio
async def test_mcp_2026_one_shot_call_has_no_session_and_reaches_authority():
    from mcp.server import MCPServer

    from argus.mcp.server import secure_mcp_transport_app

    backend = _Backend()
    server = MCPServer("argus")

    @server.tool()
    async def search_web(query: str, max_results: int = 10) -> str:
        return await backend.search_web(query=query, max_results=max_results)

    app = secure_mcp_transport_app(
        server.streamable_http_app(stateless_http=True, host="127.0.0.1"),
        transport="streamable-http",
        requires_auth=False,
        stateless_http=True,
    )
    transport = httpx.ASGITransport(app=app)
    async with server.session_manager.run():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as client:
            response = await client.post(
                "/mcp", headers=_modern_headers(), json=_modern_call()
            )

    assert response.status_code == 200
    assert "mcp-session-id" not in {key.lower() for key in response.headers}
    assert response.json()["result"]["isError"] is False
    assert backend.calls == [{"query": "stateless MCP", "max_results": 1}]


@pytest.mark.asyncio
async def test_mcp_2026_rejects_header_body_method_mismatch_before_authority():
    from mcp.server import MCPServer

    from argus.mcp.server import secure_mcp_transport_app

    backend = _Backend()
    server = MCPServer("argus")

    @server.tool()
    async def search_web(query: str) -> str:
        return await backend.search_web(query=query)

    app = secure_mcp_transport_app(
        server.streamable_http_app(stateless_http=True, host="127.0.0.1"),
        transport="streamable-http",
        requires_auth=False,
        stateless_http=True,
    )
    headers = {**_modern_headers(), "mcp-name": "other_tool"}
    transport = httpx.ASGITransport(app=app)
    async with server.session_manager.run():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as client:
            response = await client.post(
                "/mcp", headers=headers, json=_modern_call()
            )

    assert response.status_code == 400
    assert backend.calls == []


@pytest.mark.asyncio
async def test_mcp_v2025_initialize_remains_accepted_on_dual_era_app():
    from mcp.server import MCPServer

    from argus.mcp.server import secure_mcp_transport_app

    server = MCPServer("argus")
    app = secure_mcp_transport_app(
        server.streamable_http_app(stateless_http=True, host="127.0.0.1"),
        transport="streamable-http",
        requires_auth=False,
        stateless_http=True,
    )
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": "2025-11-25",
    }
    transport = httpx.ASGITransport(app=app)
    async with server.session_manager.run():
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as client:
            response = await client.post(
                "/mcp", headers=headers, json=_legacy_initialize()
            )

    assert response.status_code == 200
    payload = json.loads(response.text.split("data: ", 1)[1].split("\r\n", 1)[0])
    assert payload["result"]["protocolVersion"] == "2025-11-25"
