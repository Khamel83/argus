"""Pin the 2026-07-28 Streamable HTTP contract on the real Argus stateless app.

These are protocol-conformance tests, not unit tests. Most of the required
behavior is implemented by the pinned SDK rather than by Argus, so the point of
this module is to make an SDK regression fail here loudly instead of silently
changing what Argus serves to real clients.

Primary source:
https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

MODERN = "2026-07-28"


def _meta(protocol: str = MODERN) -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": protocol,
        "io.modelcontextprotocol/clientInfo": {"name": "contract", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def _body(method: str, params: dict | None = None, *, protocol: str = MODERN, rid: int = 1):
    payload = dict(params or {})
    payload["_meta"] = _meta(protocol)
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": payload}


@pytest.fixture()
def modern_client():
    from mcp.server import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    from argus.api.security import TransportSecurityGuard
    from argus.mcp.server import secure_mcp_transport_app

    mcp = MCPServer("contract-2026-07-28")

    @mcp.tool()
    def echo(value: str) -> str:
        return value

    sdk_app = mcp.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["testserver"],
            allowed_origins=[],
        ),
    )
    app = secure_mcp_transport_app(
        sdk_app,
        transport="streamable-http",
        stateless_http=True,
        security_guard=TransportSecurityGuard(
            allowed_hosts=("testserver",),
            allowed_origins=(),
            host_policy_explicit=True,
            origin_policy_explicit=True,
        ),
    )
    with TestClient(app) as client:
        yield client


def _post(client, headers, payload, method="POST"):
    base = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }
    return client.request(
        method,
        "/mcp",
        headers={**base, **headers},
        content=json.dumps(payload) if payload is not None else None,
    )


def _error_code(response):
    try:
        return (response.json().get("error") or {}).get("code")
    except ValueError:
        return None


MODERN_HEADERS = {"mcp-protocol-version": MODERN, "mcp-method": "tools/list"}


def test_modern_request_succeeds_and_mints_no_session(modern_client):
    """2026-07-28 removed protocol sessions; none may be minted or echoed."""
    response = _post(modern_client, MODERN_HEADERS, _body("tools/list"))

    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers


def test_supplied_session_id_is_ignored_and_never_echoed(modern_client):
    response = _post(
        modern_client,
        {**MODERN_HEADERS, "mcp-session-id": "client-invented-session"},
        _body("tools/list"),
    )

    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers


def test_server_discover_is_implemented(modern_client):
    """Modern servers MUST implement server/discover."""
    response = _post(
        modern_client,
        {"mcp-protocol-version": MODERN, "mcp-method": "server/discover"},
        _body("server/discover"),
    )

    assert response.status_code == 200


def test_header_body_protocol_mismatch_is_header_mismatch(modern_client):
    response = _post(
        modern_client, MODERN_HEADERS, _body("tools/list", protocol="2025-11-25")
    )

    assert response.status_code == 400
    assert _error_code(response) == -32020


@pytest.mark.parametrize(
    "headers,payload",
    [
        ({"mcp-protocol-version": MODERN}, _body("tools/list")),
        (
            {"mcp-protocol-version": MODERN, "mcp-method": "resources/list"},
            _body("tools/list"),
        ),
        (
            {"mcp-protocol-version": MODERN, "mcp-method": "tools/call"},
            _body("tools/call", {"name": "echo", "arguments": {"value": "x"}}),
        ),
        (
            {
                "mcp-protocol-version": MODERN,
                "mcp-method": "tools/call",
                "mcp-name": "not-echo",
            },
            _body("tools/call", {"name": "echo", "arguments": {"value": "x"}}),
        ),
    ],
    ids=["missing-method", "method-mismatch", "missing-name", "name-mismatch"],
)
def test_required_header_violations_are_header_mismatch(modern_client, headers, payload):
    """Missing or mismatched Mcp-Method/Mcp-Name MUST be 400 with -32020."""
    response = _post(modern_client, headers, payload)

    assert response.status_code == 400
    assert _error_code(response) == -32020


def test_unsupported_version_lists_supported_versions(modern_client):
    response = _post(
        modern_client,
        {"mcp-protocol-version": "1900-01-01", "mcp-method": "tools/list"},
        _body("tools/list", protocol="1900-01-01"),
    )

    assert response.status_code == 400
    assert _error_code(response) == -32022
    supported = response.json()["error"]["data"]["supported"]
    assert MODERN in supported, supported


def test_unknown_method_is_404_with_method_not_found(modern_client):
    """404 + -32601 is how clients tell a modern server from a legacy one."""
    response = _post(
        modern_client,
        {"mcp-protocol-version": MODERN, "mcp-method": "does/notexist"},
        _body("does/notexist"),
    )

    assert response.status_code == 404
    assert _error_code(response) == -32601


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_get_and_delete_are_method_not_allowed(modern_client, method):
    """The GET stream endpoint was removed in this revision."""
    response = _post(modern_client, MODERN_HEADERS, None, method=method)

    assert response.status_code == 405


def test_modern_body_without_protocol_header_is_not_silently_downgraded(modern_client):
    """A modern request that omits the header must fail, not fall back to legacy.

    The specification permits treating a header-less request as 2025-03-26 for
    pre-2025-06-18 clients, and the SDK does exactly that. But a body carrying
    modern `_meta` is unambiguously a modern request, and serving it under
    legacy semantics is the silent downgrade the transport contract forbids.
    """
    response = _post(modern_client, {"mcp-method": "tools/list"}, _body("tools/list"))

    assert response.status_code == 400
    assert _error_code(response) == -32020


def test_headerless_legacy_request_is_still_accepted(modern_client):
    """The anti-downgrade rule must not break genuine pre-2025-06-18 clients."""
    response = _post(
        modern_client,
        {},
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "legacy", "version": "1"},
            },
        },
    )

    assert response.status_code == 200
