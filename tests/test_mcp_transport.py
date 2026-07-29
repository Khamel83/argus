from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from fastapi.testclient import TestClient
from starlette.responses import Response


SUPPORTED_PROTOCOLS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)


class ManualClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def test_registry_session_is_opaque_bounded_and_principal_owned():
    from argus.mcp.sessions import McpSessionRegistry

    registry = McpSessionRegistry()
    session = registry.initialize("maya", "2025-11-25")

    assert 32 <= len(session.session_id) <= 128
    assert session.session_id.isascii()
    assert session.session_id.isprintable()
    assert "maya" not in session.session_id
    assert registry.lookup(session.session_id, "maya") == session
    assert registry.lookup(session.session_id, "penny") is None


def test_registry_touch_extends_exact_thirty_minute_idle_expiry():
    from argus.mcp.sessions import McpSessionRegistry

    clock = ManualClock()
    registry = McpSessionRegistry(clock=clock)
    session = registry.initialize("maya", "2025-03-26")

    clock.advance(1_799)
    assert registry.touch(session.session_id, "maya") is not None
    clock.advance(1_799)
    assert registry.lookup(session.session_id, "maya") is not None
    clock.advance(1)
    assert registry.lookup(session.session_id, "maya") is None
    assert registry.active_count == 0


def test_registry_terminate_and_process_restart_invalidate_sessions():
    from argus.mcp.sessions import McpSessionRegistry

    first_process = McpSessionRegistry()
    session = first_process.initialize("maya", "2025-11-25")

    assert first_process.terminate(session.session_id, "penny") is False
    assert first_process.lookup(session.session_id, "maya") is not None
    assert first_process.terminate(session.session_id, "maya") is True
    assert first_process.lookup(session.session_id, "maya") is None
    assert McpSessionRegistry().lookup(session.session_id, "maya") is None


def test_registry_retries_a_forced_active_id_collision():
    from argus.mcp.sessions import McpSessionRegistry

    identifiers = iter(("collision", "collision", "replacement"))
    registry = McpSessionRegistry(id_factory=lambda: next(identifiers))

    first = registry.initialize("maya", "2025-11-25")
    second = registry.initialize("penny", "2025-11-25")

    assert first.session_id == "collision"
    assert second.session_id == "replacement"
    assert registry.active_count == 2


def test_registry_concurrent_initializes_never_exceed_256():
    from argus.mcp.sessions import McpSessionCapacityError, McpSessionRegistry

    attempts = 300
    barrier = Barrier(attempts)
    registry = McpSessionRegistry()
    admitted = []
    rejected = 0
    result_lock = Lock()

    def initialize(index):
        nonlocal rejected
        barrier.wait()
        try:
            session = registry.initialize(f"principal-{index}", "2025-11-25")
        except McpSessionCapacityError:
            with result_lock:
                rejected += 1
        else:
            with result_lock:
                admitted.append(session.session_id)

    with ThreadPoolExecutor(max_workers=attempts) as executor:
        list(executor.map(initialize, range(attempts)))

    assert len(admitted) == 256
    assert len(set(admitted)) == 256
    assert rejected == attempts - 256
    assert registry.active_count == 256


def test_registry_capacity_has_no_lru_eviction_and_reclaims_expiry_atomically():
    from argus.mcp.sessions import McpSessionCapacityError, McpSessionRegistry

    clock = ManualClock()
    registry = McpSessionRegistry(max_active=2, clock=clock)
    first = registry.initialize("maya", "2025-11-25")
    clock.advance(1)
    second = registry.initialize("penny", "2025-11-25")
    assert registry.touch(first.session_id, "maya") is not None

    with pytest.raises(McpSessionCapacityError):
        registry.initialize("hermes", "2025-11-25")

    assert registry.lookup(first.session_id, "maya") is not None
    assert registry.lookup(second.session_id, "penny") is not None
    clock.advance(1_800)
    replacement = registry.initialize("hermes", "2025-11-25")
    assert registry.active_count == 1
    assert registry.lookup(replacement.session_id, "hermes") is not None


def test_registry_bounded_sweep_removes_idle_sessions_without_capacity_pressure():
    from argus.mcp.sessions import McpSessionRegistry

    clock = ManualClock()
    registry = McpSessionRegistry(max_active=10, clock=clock, sweep_limit=2)
    for index in range(5):
        registry.initialize(f"principal-{index}", "2025-11-25")
    clock.advance(1_800)

    assert registry.sweep() == 2
    assert registry.active_count == 3
    assert registry.sweep() == 2
    assert registry.active_count == 1
    assert registry.sweep() == 1
    assert registry.active_count == 0


def test_release_manifest_has_immutable_unadvertised_s9b_transport_descriptor():
    from argus.capabilities import http_capability_manifest

    manifest = http_capability_manifest(evidence_enabled=False)
    descriptor = manifest.mcp_transport

    assert descriptor["endpoint"] == "/mcp"
    assert descriptor["protocol_versions"] == SUPPORTED_PROTOCOLS
    assert descriptor["methods"] == ("POST", "GET", "DELETE", "OPTIONS")
    assert descriptor["post_content_type"] == "application/json"
    assert descriptor["post_accept"] == (
        "application/json",
        "text/event-stream",
    )
    assert descriptor["get_accept"] == "text/event-stream"
    assert descriptor["max_request_body_bytes"] == 4 * 1024 * 1024
    assert descriptor["notification_status"] == 202
    assert descriptor["session_idle_timeout_seconds"] == 30 * 60
    assert descriptor["max_active_sessions"] == 256
    assert descriptor["session_id_max_characters"] == 128
    assert descriptor["legacy_sse_paths"] == ("/sse", "/messages/")
    assert "mcp_transport" not in manifest.as_dict()
    with pytest.raises(TypeError):
        descriptor["endpoint"] = "/changed"


class FakeTransport:
    def __init__(self, session_id):
        self.mcp_session_id = session_id
        self.terminated = False

    async def terminate(self):
        self.terminated = True


class FakeSessionManager:
    def __init__(self):
        self._server_instances = {}


class FakeMcpSdkApp:
    def __init__(self, manager):
        self.manager = manager
        self.calls = []
        self.principals = []
        self._counter = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        from argus.mcp.server import _mcp_caller_identity

        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        headers = {
            key.decode().lower(): value.decode() for key, value in scope["headers"]
        }
        self.calls.append((scope["method"], scope["path"], body, headers))
        self.principals.append(_mcp_caller_identity())

        response_headers = [(b"content-type", b"application/json")]
        if b'"method":"initialize"' in body.replace(b" ", b""):
            self._counter += 1
            internal_id = f"internal-{self._counter}"
            self.manager._server_instances[internal_id] = FakeTransport(internal_id)
            response_headers.append((b"mcp-session-id", internal_id.encode()))
            status = 200
            content = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        elif scope["method"] == "DELETE":
            status = 200
            content = b""
        elif b'"method":"notifications/' in body.replace(b" ", b"") or (
            b'"result":' in body.replace(b" ", b"")
            and b'"method":' not in body.replace(b" ", b"")
        ):
            status = 202
            content = b""
        else:
            status = 200
            content = b'{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}'
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": response_headers,
            }
        )
        await send({"type": "http.response.body", "body": content})


def _streamable_client(
    *,
    clock=None,
    max_active=256,
    requires_auth=False,
    auth_config=None,
    allowed_origins=(),
):
    from argus.api.security import TransportSecurityGuard
    from argus.mcp.server import secure_mcp_transport_app

    manager = FakeSessionManager()
    downstream = FakeMcpSdkApp(manager)
    guard = TransportSecurityGuard(
        allowed_hosts=("testserver",),
        allowed_origins=allowed_origins,
        host_policy_explicit=True,
        origin_policy_explicit=True,
    )
    app = secure_mcp_transport_app(
        downstream,
        session_manager=manager,
        transport="streamable-http",
        security_guard=guard,
        requires_auth=requires_auth,
        auth_config=auth_config,
        registry_options={
            "clock": clock or __import__("time").monotonic,
            "max_active": max_active,
        },
        sweep_interval_seconds=60,
    )
    return TestClient(app), app, downstream, manager


def _initialize(client, protocol="2025-11-25", token=None):
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    return client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": protocol,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )


@pytest.mark.parametrize("protocol", SUPPORTED_PROTOCOLS)
def test_streamable_http_initializes_every_promised_protocol(protocol):
    client, app, downstream, manager = _streamable_client()

    response = _initialize(client, protocol)

    assert response.status_code == 200
    session_id = response.headers["mcp-session-id"]
    assert app.registry.lookup(session_id, "local-mcp").protocol_version == protocol
    assert session_id in manager._server_instances
    assert manager._server_instances[session_id].mcp_session_id == session_id
    assert not session_id.startswith("internal-")
    assert downstream.calls[0][1] == "/mcp"


def test_streamable_http_rejects_method_and_media_before_sdk():
    client, _, downstream, _ = _streamable_client()

    method = client.put("/mcp")
    content_type = client.post(
        "/mcp",
        content=b"{}",
        headers={
            "content-type": "text/plain",
            "accept": "application/json, text/event-stream",
        },
    )
    post_accept = client.post(
        "/mcp",
        json={},
        headers={"accept": "application/json"},
    )
    get_accept = client.get("/mcp", headers={"accept": "application/json"})
    q_zero = client.post(
        "/mcp",
        json={},
        headers={"accept": ("application/json;q=0, text/event-stream;q=0")},
    )

    assert method.status_code == 405
    assert method.headers["allow"] == "POST, GET, DELETE, OPTIONS"
    assert content_type.status_code == 415
    assert post_accept.status_code == 406
    assert get_accept.status_code == 406
    assert q_zero.status_code == 406
    assert downstream.calls == []


def test_streamable_http_enforces_four_mib_actual_and_declared_body_bound():
    client, _, downstream, _ = _streamable_client()
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }

    declared = client.post(
        "/mcp",
        content=b"{}",
        headers={**headers, "content-length": str(4 * 1024 * 1024 + 1)},
    )
    actual = client.post(
        "/mcp",
        content=b" " * (4 * 1024 * 1024 + 1),
        headers={**headers, "content-length": "2"},
    )

    assert declared.status_code == 413
    assert actual.status_code == 413
    assert downstream.calls == []


def test_streamable_http_options_is_cors_preflight_only():
    client, _, downstream, _ = _streamable_client(
        allowed_origins=("https://maya.example",)
    )

    ordinary = client.options("/mcp")
    preflight = client.options(
        "/mcp",
        headers={
            "origin": "https://maya.example",
            "access-control-request-method": "POST",
            "access-control-request-headers": (
                "authorization,content-type,mcp-protocol-version,mcp-session-id"
            ),
        },
    )

    assert ordinary.status_code == 405
    assert preflight.status_code == 204
    assert preflight.content == b""
    assert preflight.headers["access-control-allow-origin"] == "https://maya.example"
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()
    assert downstream.calls == []


def test_authenticated_streamable_http_allows_credentialless_cors_preflight():
    from argus.auth import AuthConfig

    client, _, downstream, _ = _streamable_client(
        requires_auth=True,
        auth_config=AuthConfig(
            caller_api_key="listener-token",
            admin_api_key="listener-token",
            cors_origins=(),
        ),
        allowed_origins=("https://maya.example",),
    )

    preflight = client.options(
        "/mcp",
        headers={
            "origin": "https://maya.example",
            "access-control-request-method": "POST",
            "access-control-request-headers": "authorization,content-type",
        },
    )

    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "https://maya.example"
    assert downstream.calls == []


def test_streamable_http_notification_and_response_acknowledge_202_without_body():
    client, _, _, _ = _streamable_client()
    initialized = _initialize(client)
    session_id = initialized.headers["mcp-session-id"]
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "mcp-session-id": session_id,
        "mcp-protocol-version": "2025-11-25",
    }

    notification = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    response = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 4, "result": {}},
    )

    assert notification.status_code == 202
    assert notification.content == b""
    assert response.status_code == 202
    assert response.content == b""


def test_streamable_http_session_failures_and_legacy_protocol_default():
    client, _, downstream, _ = _streamable_client()
    initialized = _initialize(client, "2025-03-26")
    session_id = initialized.headers["mcp-session-id"]
    request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    base = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }

    missing = client.post("/mcp", headers=base, json=request)
    wrong = client.post(
        "/mcp",
        headers={**base, "mcp-session-id": "wrong"},
        json=request,
    )
    legacy_default = client.post(
        "/mcp",
        headers={**base, "mcp-session-id": session_id},
        json=request,
    )
    unsupported = client.post(
        "/mcp",
        headers={
            **base,
            "mcp-session-id": session_id,
            "mcp-protocol-version": "2099-01-01",
        },
        json=request,
    )

    assert missing.status_code == 400
    assert wrong.status_code == 404
    assert legacy_default.status_code == 200
    assert unsupported.status_code == 400
    assert len(downstream.calls) == 2


def test_streamable_http_session_is_principal_owned_and_delete_terminates():
    from argus.auth import AuthConfig

    auth = AuthConfig(
        caller_api_key="",
        admin_api_key="",
        cors_origins=(),
        scoped_caller_credentials=(("maya", "maya-token"), ("penny", "penny-token")),
    )
    client, app, downstream, manager = _streamable_client(
        requires_auth=True,
        auth_config=auth,
    )
    unauthenticated = _initialize(client)
    initialized = _initialize(client, token="maya-token")
    session_id = initialized.headers["mcp-session-id"]
    base = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "mcp-session-id": session_id,
        "mcp-protocol-version": "2025-11-25",
    }
    wrong_principal = client.post(
        "/mcp",
        headers={**base, "authorization": "Bearer penny-token"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    deleted = client.delete(
        "/mcp",
        headers={
            "accept": "application/json",
            "mcp-session-id": session_id,
            "mcp-protocol-version": "2025-11-25",
            "authorization": "Bearer maya-token",
        },
    )
    after_delete = client.post(
        "/mcp",
        headers={**base, "authorization": "Bearer maya-token"},
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    )

    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
    assert initialized.status_code == 200
    assert downstream.principals == ["maya", "maya"]
    assert wrong_principal.status_code == 404
    assert deleted.status_code == 200
    assert after_delete.status_code == 404
    assert app.registry.active_count == 0
    assert session_id not in manager._server_instances


def test_streamable_http_expiry_reclaims_sdk_transport_without_capacity_pressure():
    clock = ManualClock()
    client, app, _, manager = _streamable_client(clock=clock)
    initialized = _initialize(client)
    session_id = initialized.headers["mcp-session-id"]

    clock.advance(1_800)
    response = client.post(
        "/mcp",
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "mcp-session-id": session_id,
            "mcp-protocol-version": "2025-11-25",
        },
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )

    assert response.status_code == 404
    assert app.registry.active_count == 0
    assert session_id not in manager._server_instances


def test_streamable_http_capacity_returns_503_without_evicting_existing_session():
    client, app, downstream, _ = _streamable_client(max_active=1)
    first = _initialize(client)
    second = _initialize(client)

    assert first.status_code == 200
    assert second.status_code == 503
    assert second.headers["retry-after"] == "60"
    assert app.registry.active_count == 1
    assert len(downstream.calls) == 1


def test_periodic_sweep_reclaims_expiry_without_another_http_request():
    import time

    from argus.api.security import TransportSecurityGuard
    from argus.mcp.server import secure_mcp_transport_app

    clock = ManualClock()
    manager = FakeSessionManager()
    downstream = FakeMcpSdkApp(manager)
    app = secure_mcp_transport_app(
        downstream,
        session_manager=manager,
        transport="streamable-http",
        security_guard=TransportSecurityGuard(
            allowed_hosts=("testserver",),
            allowed_origins=(),
            host_policy_explicit=True,
            origin_policy_explicit=True,
        ),
        registry_options={"clock": clock},
        sweep_interval_seconds=0.005,
    )
    session = app.registry.initialize("local-mcp", "2025-11-25")
    manager._server_instances[session.session_id] = FakeTransport(session.session_id)
    clock.advance(1_800)

    with TestClient(app):
        time.sleep(0.02)

    assert app.registry.active_count == 0
    assert session.session_id not in manager._server_instances


def test_pinned_sdk_wire_and_legacy_tool_result_shape_remain_unchanged():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    from argus.api.security import TransportSecurityGuard
    from argus.mcp.server import secure_mcp_transport_app

    mcp = FastMCP(
        "contract-fixture",
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["testserver"],
            allowed_origins=[],
        ),
    )

    @mcp.tool()
    def frozen_tool(value: str) -> str:
        return value

    sdk_app = mcp.streamable_http_app()
    app = secure_mcp_transport_app(
        sdk_app,
        session_manager=mcp.session_manager,
        transport="streamable-http",
        security_guard=TransportSecurityGuard(
            allowed_hosts=("testserver",),
            allowed_origins=(),
            host_policy_explicit=True,
            origin_policy_explicit=True,
        ),
    )
    with TestClient(app) as client:
        initialized = _initialize(client)
        session_id = initialized.headers["mcp-session-id"]
        common_headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "mcp-session-id": session_id,
            "mcp-protocol-version": "2025-11-25",
        }
        notification = client.post(
            "/mcp",
            headers=common_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        jsonrpc_response = client.post(
            "/mcp",
            headers=common_headers,
            json={"jsonrpc": "2.0", "id": 99, "result": {}},
        )
        response = client.post(
            "/mcp",
            headers=common_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "frozen_tool",
                    "arguments": {"value": "frozen-result"},
                },
            },
        )

    assert initialized.status_code == 200
    assert initialized.json()["result"]["protocolVersion"] == "2025-11-25"
    assert notification.status_code == 202
    assert notification.content == b""
    assert jsonrpc_response.status_code == 202
    assert jsonrpc_response.content == b""
    assert response.status_code == 200
    assert response.json()["result"] == {
        "content": [{"type": "text", "text": "frozen-result"}],
        "structuredContent": {"result": "frozen-result"},
        "isError": False,
    }


@pytest.mark.parametrize("protocol", SUPPORTED_PROTOCOLS)
def test_production_default_sdk_sse_response_is_not_cancelled_by_body_replay(
    protocol,
):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    from argus.api.security import TransportSecurityGuard
    from argus.mcp.server import secure_mcp_transport_app

    mcp = FastMCP(
        "default-sse-fixture",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["testserver"],
            allowed_origins=[],
        ),
    )
    sdk_app = mcp.streamable_http_app()
    app = secure_mcp_transport_app(
        sdk_app,
        session_manager=mcp.session_manager,
        transport="streamable-http",
        security_guard=TransportSecurityGuard(
            allowed_hosts=("testserver",),
            allowed_origins=(),
            host_policy_explicit=True,
            origin_policy_explicit=True,
        ),
    )

    with TestClient(app) as client:
        initialized = _initialize(client, protocol)

    assert initialized.status_code == 200
    assert initialized.headers["content-type"].startswith("text/event-stream")
    assert f'"protocolVersion":"{protocol}"'.encode() in initialized.content


def test_legacy_sse_guard_preserves_frozen_get_and_post_shapes():
    from argus.api.security import TransportSecurityGuard
    from argus.auth import AuthConfig
    from argus.mcp.server import secure_mcp_transport_app

    calls = []

    async def legacy_sdk(scope, receive, send):
        calls.append((scope["method"], scope["path"]))
        if scope["method"] == "GET":
            response = Response(
                b"event: endpoint\r\ndata: /messages/?session_id=frozen\r\n\r\n",
                status_code=200,
                headers={"cache-control": "no-cache"},
                media_type="text/event-stream",
            )
        else:
            response = Response(b"Accepted", status_code=202, media_type="text/plain")
        await response(scope, receive, send)

    auth = AuthConfig(
        caller_api_key="",
        admin_api_key="",
        cors_origins=(),
        scoped_caller_credentials=(
            ("maya", "legacy-token"),
            ("penny", "other-token"),
        ),
    )
    guard = TransportSecurityGuard(
        allowed_hosts=("testserver",),
        allowed_origins=(),
        host_policy_explicit=True,
        origin_policy_explicit=True,
    )
    app = secure_mcp_transport_app(
        legacy_sdk,
        transport="sse",
        security_guard=guard,
        requires_auth=True,
        auth_config=auth,
    )
    client = TestClient(app)

    rejected = client.get("/sse")
    get_response = client.get(
        "/sse",
        headers={"authorization": "Bearer legacy-token"},
    )
    post_response = client.post(
        "/messages/?session_id=frozen",
        content=b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
        headers={
            "authorization": "Bearer legacy-token",
            "content-type": "application/json",
        },
    )
    wrong_principal = client.post(
        "/messages/?session_id=frozen",
        content=b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
        headers={
            "authorization": "Bearer other-token",
            "content-type": "application/json",
        },
    )
    unsupported_media = client.post(
        "/messages/?session_id=frozen",
        content=b"not-json",
        headers={
            "authorization": "Bearer legacy-token",
            "content-type": "text/plain",
        },
    )
    oversized = client.post(
        "/messages/?session_id=frozen",
        content=b"{}",
        headers={
            "authorization": "Bearer legacy-token",
            "content-type": "application/json",
            "content-length": str(4 * 1024 * 1024 + 1),
        },
    )

    assert rejected.status_code == 401
    assert get_response.status_code == 200
    assert get_response.content == (
        b"event: endpoint\r\ndata: /messages/?session_id=frozen\r\n\r\n"
    )
    assert get_response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert get_response.headers["cache-control"] == "no-cache"
    assert post_response.status_code == 202
    assert post_response.content == b"Accepted"
    assert post_response.headers["content-type"] == "text/plain; charset=utf-8"
    assert wrong_principal.status_code == 404
    assert unsupported_media.status_code == 415
    assert oversized.status_code == 413
    assert calls == [("GET", "/sse"), ("POST", "/messages/")]


def test_rejected_initialize_removes_unaccounted_sdk_transport():
    from argus.api.security import TransportSecurityGuard
    from argus.mcp.server import secure_mcp_transport_app

    manager = FakeSessionManager()
    leaked = FakeTransport("internal-rejected")

    async def rejecting_sdk(scope, receive, send):
        del receive
        manager._server_instances[leaked.mcp_session_id] = leaked
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"mcp-session-id", leaked.mcp_session_id.encode()),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"jsonrpc":"2.0","error":{"code":-32602}}',
            }
        )

    app = secure_mcp_transport_app(
        rejecting_sdk,
        session_manager=manager,
        transport="streamable-http",
        security_guard=TransportSecurityGuard(
            allowed_hosts=("testserver",),
            allowed_origins=(),
            host_policy_explicit=True,
            origin_policy_explicit=True,
        ),
    )
    response = _initialize(TestClient(app))

    assert response.status_code == 400
    assert app.registry.active_count == 0
    assert manager._server_instances == {}


def test_config_loads_explicit_proxy_exposure_flag():
    from argus.mcp.server import _mcp_remote_exposed

    assert _mcp_remote_exposed({}) is False
    assert _mcp_remote_exposed({"ARGUS_MCP_REMOTE_EXPOSED": "true"}) is True


def test_env_example_documents_fixed_transport_bounds_and_proxy_exposure():
    from pathlib import Path

    example = (Path(__file__).parents[1] / ".env.example").read_text()

    assert "ARGUS_MCP_REMOTE_EXPOSED=false" in example
    assert "idle timeout: 30 minutes" in example
    assert "maximum active sessions: 256" in example
    assert "request body maximum: 4 MiB" in example


def test_network_serve_binds_sdk_app_to_validated_argus_registration(monkeypatch):
    import argus.mcp.server as server

    observed = {}

    class FakeFastMCP:
        def __init__(self, name, **kwargs):
            from types import SimpleNamespace

            observed["name"] = name
            observed["kwargs"] = kwargs
            self._session_manager = object()
            self.settings = SimpleNamespace(
                streamable_http_path="/mcp",
                sse_path="/sse",
                message_path="/messages/",
            )

        def tool(self):
            return lambda function: function

        def streamable_http_app(self):
            observed["sdk_app_built"] = True

            async def app(scope, receive, send):
                del scope, receive, send

            return app

        @property
        def session_manager(self):
            return self._session_manager

    monkeypatch.setattr("mcp.server.fastmcp.FastMCP", FakeFastMCP)
    monkeypatch.setattr(server, "build_mcp_backend", lambda: object())
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **kwargs: observed.update({"app": app, "run_kwargs": kwargs}),
    )
    monkeypatch.setenv("ARGUS_ENV", "production")
    monkeypatch.setenv("ARGUS_NODE_ROLE", "caller")
    monkeypatch.setenv("ARGUS_AUTHORITY_URL", "http://argus.internal:8000")
    monkeypatch.setenv("ARGUS_AUTHORITY_TOKEN", "authority-token")
    monkeypatch.setenv("ARGUS_API_KEY", "listener-token")
    monkeypatch.setenv("ARGUS_ALLOWED_HOSTS", "mcp.internal")
    monkeypatch.setenv("ARGUS_ALLOWED_ORIGINS", "")

    server.serve_mcp(
        transport="streamable-http",
        host="127.0.0.1",
        port=8001,
    )

    assert observed["sdk_app_built"] is True
    assert isinstance(observed["app"], server.McpTransportSecurityApp)
    assert observed["app"]._requires_auth is True
    assert observed["run_kwargs"] == {"host": "127.0.0.1", "port": 8001}
    assert "auth" not in observed["kwargs"]
    assert "token_verifier" not in observed["kwargs"]
    assert observed["kwargs"]["transport_security"].allowed_hosts == ["mcp.internal"]
    assert observed["kwargs"]["transport_security"].allowed_origins == []


def test_proxy_exposed_loopback_mcp_fails_without_complete_listener_policy(
    monkeypatch,
):
    import argus.mcp.server as server
    from argus.api.security import TransportSecurityConfigurationError

    monkeypatch.setattr(server, "build_mcp_backend", lambda: object())
    monkeypatch.setenv("ARGUS_ENV", "development")
    monkeypatch.setenv("ARGUS_MCP_REMOTE_EXPOSED", "true")
    monkeypatch.setenv("ARGUS_API_KEY", "listener-token")
    monkeypatch.delenv("ARGUS_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("ARGUS_ALLOWED_ORIGINS", raising=False)

    with pytest.raises(TransportSecurityConfigurationError, match="allowed hosts"):
        server.serve_mcp(
            transport="streamable-http",
            host="127.0.0.1",
        )


def test_listener_registration_mismatch_fails_before_opening_listener(monkeypatch):
    import argus.mcp.server as server
    from argus.capabilities import CapabilityManifestError

    monkeypatch.setattr(server, "build_mcp_backend", lambda: object())
    monkeypatch.setattr(server, "_mcp_transport_registration", lambda _mcp: {})
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *args, **kwargs: pytest.fail("listener must not open"),
    )

    with pytest.raises(CapabilityManifestError, match="does not match"):
        server.serve_mcp(transport="streamable-http")
