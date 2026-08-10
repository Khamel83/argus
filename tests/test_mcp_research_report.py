"""Production MCP adapters for remotely readable research packs."""

import json

import httpx
import pytest

from argus.authority import AuthorityClientConfig, HttpAuthorityClient
from argus.mcp.http_adapter import HttpMcpAdapter


@pytest.mark.asyncio
async def test_status_adapter_forwards_scoped_token_and_renders_safe_json():
    observed = {}

    def handler(request):
        observed["authorization"] = request.headers["authorization"]
        observed["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "run_id": "run-safe",
                "kind": "build-research-pack",
                "status": "completed",
                "target": "Parallel",
                "artifacts": [],
                "citations": [],
                "source_count": 1,
                "domain_count": 1,
                "cost_state": "unavailable",
                "runtime": {"version": "1.6.3"},
            },
        )

    adapter = HttpMcpAdapter(
        HttpAuthorityClient(
            AuthorityClientConfig("https://authority.example", "default-token"),
            transport=httpx.MockTransport(handler),
        )
    )

    result = await adapter.get_workflow_status(
        "run-safe", response_format="json", token="scoped-token"
    )

    assert json.loads(result)["run_id"] == "run-safe"
    assert observed == {
        "authorization": "Bearer scoped-token",
        "path": "/api/workflows/run-safe/status",
    }


@pytest.mark.asyncio
async def test_artifact_adapter_forwards_bounds_and_renders_content():
    observed = {}

    def handler(request):
        observed["authorization"] = request.headers["authorization"]
        observed["path"] = request.url.path
        observed["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "run_id": "run-safe",
                "artifact": "report",
                "kind": "report",
                "media_type": "text/markdown; charset=utf-8",
                "total_bytes": 11,
                "offset": 4,
                "bytes_returned": 7,
                "truncated": False,
                "next_offset": None,
                "sha256": "a" * 64,
                "content": "report!",
            },
        )

    adapter = HttpMcpAdapter(
        HttpAuthorityClient(
            AuthorityClientConfig("https://authority.example", "default-token"),
            transport=httpx.MockTransport(handler),
        )
    )

    result = await adapter.read_workflow_artifact(
        "run-safe",
        artifact="report",
        offset=4,
        max_bytes=64,
        response_format="markdown",
        token="scoped-token",
    )

    assert "report!" in result
    assert "SHA-256" in result
    assert observed == {
        "authorization": "Bearer scoped-token",
        "path": "/api/workflows/run-safe/artifacts/report",
        "query": {"offset": "4", "max_bytes": "64"},
    }


def test_production_mcp_registers_remote_status_and_artifact_tools(monkeypatch):
    from argus.mcp.http_adapter import HttpMcpAdapter
    from tests.test_mcp_v2 import _capture_real_mcp_server

    backend = object.__new__(HttpMcpAdapter)
    registered = _capture_real_mcp_server(monkeypatch, backend)
    names = {tool.name for tool in registered._tool_manager.list_tools()}

    assert {
        "build_research_pack",
        "get_workflow_status",
        "read_workflow_artifact",
    }.issubset(names)
