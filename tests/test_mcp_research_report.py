"""Production MCP adapters for remotely readable research packs."""

import json

import httpx
import pytest

from argus.authority import (
    AuthorityClientConfig,
    AuthorityRequestError,
    HttpAuthorityClient,
)
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
async def test_build_json_adapter_allowlists_safe_start_projection():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "run_id": "run-safe",
                "kind": "build-research-pack",
                "status": "running",
                "target": "Parallel",
                "created_at": "2026-08-09T01:00:00",
                "started_at": "2026-08-09T01:00:01",
                "finished_at": None,
                "status_url": "/api/workflows/run-safe",
                "snapshot_dir": "/srv/argus/snapshots/run-safe",
                "report_path": "/srv/argus/SUMMARY.md",
                "manifest_path": "/srv/argus/manifest.json",
                "artifacts": [{"path": "/srv/argus/SUMMARY.md"}],
                "documents": [{"artifact_path": "/srv/argus/source.md"}],
                "metadata": {"token": "secret"},
            },
        )

    adapter = HttpMcpAdapter(
        HttpAuthorityClient(
            AuthorityClientConfig("https://authority.example", "default-token"),
            transport=httpx.MockTransport(handler),
        )
    )

    payload = json.loads(
        await adapter.build_research_pack(
            "Example SDK", response_format="json", token="scoped-token"
        )
    )

    assert set(payload) == {
        "run_id",
        "kind",
        "status",
        "target",
        "created_at",
        "started_at",
        "finished_at",
        "status_url",
    }
    assert payload["status_url"] == "/api/workflows/run-safe/status"
    assert "/srv/argus" not in json.dumps(payload)


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


@pytest.mark.asyncio
async def test_artifact_adapter_preserves_authority_range_error():
    def handler(request):
        return httpx.Response(
            422,
            json={"detail": "Workflow artifact byte range is invalid"},
        )

    adapter = HttpMcpAdapter(
        HttpAuthorityClient(
            AuthorityClientConfig("https://authority.example", "default-token"),
            transport=httpx.MockTransport(handler),
        )
    )

    with pytest.raises(AuthorityRequestError) as raised:
        await adapter.read_workflow_artifact(
            "run-safe", artifact="report", max_bytes=1, token="scoped-token"
        )

    assert raised.value.status_code == 422


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
