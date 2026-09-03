"""Stateless MCP protocol adapter for the Argus HTTP authority."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from http import HTTPStatus
from typing import Any
from uuid import UUID

from mcp.server.auth.provider import AccessToken
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from argus.auth import AuthConfig, remote_mcp_requires_auth
from argus.logging import get_logger, setup_logging
from argus.mcp.sessions import (
    MCP_MAX_ACTIVE_SESSIONS,
    MCP_SESSION_IDLE_TIMEOUT_SECONDS,
    McpSession,
    McpSessionCapacityError,
    McpSessionRegistry,
)

logger = get_logger("mcp.server")

_MCP_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
)
_LEGACY_DEFAULT_PROTOCOL_VERSION = "2025-03-26"
_MCP_METHODS = ("POST", "GET", "DELETE", "OPTIONS")
_MCP_MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024
_MCP_ALLOW = ", ".join(_MCP_METHODS)
_MCP_NOTIFICATION_STATUS = 202
_MCP_SESSION_ID_MAX_CHARACTERS = 128
_MCP_SWEEP_INTERVAL_SECONDS = 60
_MCP_CORS_ALLOW_HEADERS = (
    "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name, "
    "Mcp-Session-Id, Last-Event-ID, X-Request-ID"
)
_MCP_CORS_EXPOSE_HEADERS = (
    "Mcp-Session-Id, X-Request-ID, X-Argus-Deployment-ID, "
    "Argus-Contract-Version, Retry-After"
)


def _mcp_remote_exposed(environ=None) -> bool:
    values = os.environ if environ is None else environ
    return values.get("ARGUS_MCP_REMOTE_EXPOSED", "").lower() in {
        "1",
        "true",
        "yes",
    }


def _mcp_transport_registration(mcp) -> dict[str, object]:
    return {
        "endpoint": "/mcp",
        "protocol_versions": _MCP_PROTOCOL_VERSIONS,
        "methods": _MCP_METHODS,
        "post_content_type": "application/json",
        "post_accept": ("application/json", "text/event-stream"),
        "get_accept": "text/event-stream",
        "max_request_body_bytes": _MCP_MAX_REQUEST_BODY_BYTES,
        "notification_status": _MCP_NOTIFICATION_STATUS,
        "session_idle_timeout_seconds": MCP_SESSION_IDLE_TIMEOUT_SECONDS,
        "max_active_sessions": MCP_MAX_ACTIVE_SESSIONS,
        "session_id_max_characters": _MCP_SESSION_ID_MAX_CHARACTERS,
        "legacy_sse_paths": (
            "/sse",
            "/messages/",
        ),
    }


# Protocol-defined error codes from the 2026-07-28 specification.
_JSONRPC_HEADER_MISMATCH = -32020


def _jsonrpc_transport_error(
    status: int,
    message: str,
    *,
    headers: dict[str, str] | None = None,
    code: int = -32600,
) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": "server-error",
            "error": {"code": code, "message": message},
        },
        status_code=status,
        headers=headers,
    )


def _body_declares_modern_protocol(body: bytes) -> bool:
    """True when the payload carries modern per-request protocol metadata.

    A body containing ``_meta`` with ``io.modelcontextprotocol/protocolVersion``
    is unambiguously a 2026-07-28-era request. The specification lets a server
    treat a request with no ``MCP-Protocol-Version`` header as ``2025-03-26``
    for pre-2025-06-18 clients, and the SDK does so, but applying that fallback
    to a body that declares a modern version would silently downgrade a valid
    modern request.
    """
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    params = payload.get("params")
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return False
    return bool(meta.get("io.modelcontextprotocol/protocolVersion"))


def _accept_types(request: Request) -> set[str]:
    accepted = set()
    for item in request.headers.get("accept", "").split(","):
        parts = [part.strip() for part in item.split(";")]
        if not parts[0]:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.lower().startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        if quality > 0:
            accepted.add(parts[0].lower())
    return accepted


class McpTransportSecurityApp:
    """Argus admission and principal/session binding around pinned SDK apps."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        transport: str,
        security_guard,
        auth_config: AuthConfig,
        requires_auth: bool,
        session_manager=None,
        legacy_transport=None,
        registry_options: dict[str, Any] | None = None,
        sweep_interval_seconds: float = _MCP_SWEEP_INTERVAL_SECONDS,
        stateless_http: bool = False,
    ):
        self._app = app
        self._transport = transport
        self._security_guard = security_guard
        self._auth_config = auth_config
        self._requires_auth = requires_auth
        self._session_manager = session_manager
        self._legacy_transport = legacy_transport
        self._sweep_interval_seconds = sweep_interval_seconds
        self._stateless_http = stateless_http
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._legacy_actual_to_reservation: dict[str, str] = {}
        self._legacy_reservation_to_actual: dict[str, str] = {}
        self._legacy_lock = threading.Lock()
        self.registry = McpSessionRegistry(**(registry_options or {}))
        self.registry.bind_removal_callback(self._remove_transport)
        self.legacy_registry = McpSessionRegistry(**(registry_options or {}))
        self.legacy_registry.bind_removal_callback(self._remove_legacy_session)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._run_lifespan(scope, receive, send)
            return
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        rejection = await self._security_guard.rejection(request)
        if rejection is not None:
            await self._send_response(rejection, request, scope, receive, send)
            return

        if request.method == "OPTIONS" and self._is_preflight(request):
            allowed_path = (
                self._transport == "streamable-http"
                and request.url.path == "/mcp"
                or self._transport == "sse"
                and (
                    request.url.path == "/sse"
                    or request.url.path.startswith("/messages/")
                )
            )
            if allowed_path:
                await self._send_response(
                    Response(status_code=HTTPStatus.NO_CONTENT),
                    request,
                    scope,
                    receive,
                    send,
                )
                return

        principal, token = self._authenticate(request)
        if principal is None:
            response = _jsonrpc_transport_error(
                HTTPStatus.UNAUTHORIZED,
                "Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
            await self._send_response(response, request, scope, receive, send)
            return

        if self._transport == "sse":
            await self._handle_legacy_sse(
                request,
                principal,
                token,
                scope,
                receive,
                send,
            )
            return
        if self._stateless_http:
            await self._handle_stateless_streamable(
                request,
                principal,
                token,
                scope,
                receive,
                send,
            )
            return
        await self._handle_streamable(
            request,
            principal,
            token,
            scope,
            receive,
            send,
        )

    def _authenticate(self, request: Request) -> tuple[str | None, str | None]:
        authorization = request.headers.get("authorization", "")
        token = None
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip() or None
        if token is not None:
            identity = self._auth_config.identity_for_token(token)
            if identity is not None:
                return identity, token
            return None, None
        if self._requires_auth:
            return None, None
        return "local-mcp", None

    async def _handle_legacy_sse(
        self,
        request: Request,
        principal: str,
        token: str | None,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        allowed = (
            request.method == "GET"
            and request.url.path == "/sse"
            or request.method == "POST"
            and request.url.path.startswith("/messages/")
        )
        if request.method == "OPTIONS" and self._is_preflight(request):
            await self._send_response(
                Response(status_code=HTTPStatus.NO_CONTENT),
                request,
                scope,
                receive,
                send,
            )
            return
        if not allowed:
            response = _jsonrpc_transport_error(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "Method not allowed",
                headers={"Allow": "GET, POST, OPTIONS"},
            )
            await self._send_response(response, request, scope, receive, send)
            return
        bounded_receive = receive
        legacy_reservation = None
        if request.method == "GET":
            try:
                legacy_reservation = self.legacy_registry.initialize(
                    principal,
                    "legacy-sse",
                )
            except McpSessionCapacityError:
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "MCP transport session capacity exhausted",
                        headers={"Retry-After": "60"},
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
        if request.method == "POST":
            content_type = (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            )
            if content_type != "application/json":
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                        "Content-Type must be application/json",
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            body = await self._bounded_body(request, receive)
            if body is None:
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "Request body exceeds 4 MiB",
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            bounded_receive = self._replay_body(body, receive)
            legacy_session_id = request.query_params.get("session_id", "")
            with self._legacy_lock:
                reservation_id = self._legacy_actual_to_reservation.get(
                    legacy_session_id
                )
            if (
                reservation_id is None
                or self.legacy_registry.touch(
                    reservation_id,
                    principal,
                )
                is None
            ):
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.NOT_FOUND,
                        "Session not found",
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
        downstream_send = self._cors_send(request, send)
        if legacy_reservation is not None:
            downstream_send = self._legacy_binding_send(
                principal,
                legacy_reservation,
                downstream_send,
            )
        try:
            await self._call_with_principal(
                principal,
                token,
                scope,
                bounded_receive,
                downstream_send,
            )
        finally:
            if legacy_reservation is not None:
                self.legacy_registry.terminate(
                    legacy_reservation.session_id,
                    principal,
                )

    async def _handle_streamable(
        self,
        request: Request,
        principal: str,
        token: str | None,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if request.url.path != "/mcp":
            await self._send_response(
                _jsonrpc_transport_error(HTTPStatus.NOT_FOUND, "Not found"),
                request,
                scope,
                receive,
                send,
            )
            return
        if request.method == "OPTIONS":
            if self._is_preflight(request):
                response = Response(status_code=HTTPStatus.NO_CONTENT)
            else:
                response = _jsonrpc_transport_error(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "Method not allowed",
                    headers={"Allow": _MCP_ALLOW},
                )
            await self._send_response(response, request, scope, receive, send)
            return
        if request.method not in {"POST", "GET", "DELETE"}:
            await self._send_response(
                _jsonrpc_transport_error(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "Method not allowed",
                    headers={"Allow": _MCP_ALLOW},
                ),
                request,
                scope,
                receive,
                send,
            )
            return

        accept = _accept_types(request)
        if request.method == "POST" and not {
            "application/json",
            "text/event-stream",
        }.issubset(accept):
            await self._send_response(
                _jsonrpc_transport_error(
                    HTTPStatus.NOT_ACCEPTABLE,
                    "POST must accept application/json and text/event-stream",
                ),
                request,
                scope,
                receive,
                send,
            )
            return
        if request.method == "GET" and "text/event-stream" not in accept:
            await self._send_response(
                _jsonrpc_transport_error(
                    HTTPStatus.NOT_ACCEPTABLE,
                    "GET must accept text/event-stream",
                ),
                request,
                scope,
                receive,
                send,
            )
            return

        body = None
        message = None
        bounded_receive = receive
        if request.method == "POST":
            content_type = (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            )
            if content_type != "application/json":
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                        "Content-Type must be application/json",
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            body = await self._bounded_body(request, receive)
            if body is None:
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "Request body exceeds 4 MiB",
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            try:
                message = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.BAD_REQUEST,
                        "Malformed JSON-RPC request",
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            if not isinstance(message, dict):
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.BAD_REQUEST,
                        "Malformed JSON-RPC request",
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            bounded_receive = self._replay_body(body, receive)

        session_id = request.headers.get("mcp-session-id")
        is_initialize = (
            request.method == "POST"
            and session_id is None
            and message is not None
            and message.get("method") == "initialize"
        )
        if is_initialize:
            params = message.get("params")
            protocol = (
                params.get("protocolVersion") if isinstance(params, dict) else None
            )
            if protocol not in _MCP_PROTOCOL_VERSIONS:
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.BAD_REQUEST,
                        "Unsupported MCP protocol version",
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            try:
                session = self.registry.initialize(principal, protocol)
            except McpSessionCapacityError:
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "MCP transport session capacity exhausted",
                        headers={"Retry-After": "60"},
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            await self._initialize_sdk_session(
                session,
                principal,
                token,
                request,
                scope,
                bounded_receive,
                send,
            )
            return

        if session_id is None:
            await self._send_response(
                _jsonrpc_transport_error(
                    HTTPStatus.BAD_REQUEST,
                    "Mcp-Session-Id is required",
                ),
                request,
                scope,
                receive,
                send,
            )
            return
        session = self.registry.touch(session_id, principal)
        if session is None:
            await self._send_response(
                _jsonrpc_transport_error(
                    HTTPStatus.NOT_FOUND,
                    "Session not found",
                ),
                request,
                scope,
                receive,
                send,
            )
            return
        protocol = request.headers.get(
            "mcp-protocol-version",
            _LEGACY_DEFAULT_PROTOCOL_VERSION,
        )
        if (
            protocol not in _MCP_PROTOCOL_VERSIONS
            or protocol != session.protocol_version
        ):
            await self._send_response(
                _jsonrpc_transport_error(
                    HTTPStatus.BAD_REQUEST,
                    "MCP protocol version does not match the session",
                ),
                request,
                scope,
                receive,
                send,
            )
            return

        await self._call_with_principal(
            principal,
            token,
            scope,
            bounded_receive,
            self._cors_send(request, send),
        )
        if request.method == "DELETE":
            self.registry.terminate(session_id, principal)

    async def _handle_stateless_streamable(
        self,
        request: Request,
        principal: str,
        token: str | None,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Forward one 2026-07-28 exchange without creating a session."""
        if request.url.path != "/mcp":
            await self._send_response(
                _jsonrpc_transport_error(HTTPStatus.NOT_FOUND, "Not found"),
                request,
                scope,
                receive,
                send,
            )
            return
        if request.method != "POST":
            await self._send_response(
                _jsonrpc_transport_error(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    "Method not allowed",
                    headers={"Allow": "POST, OPTIONS"},
                ),
                request,
                scope,
                receive,
                send,
            )
            return
        if "mcp-protocol-version" not in request.headers:
            # The SDK would serve this under the legacy era. That is correct for
            # a genuine pre-2025-06-18 client, but a body declaring a modern
            # protocol version is a modern request, and answering it with legacy
            # semantics is a silent downgrade.
            body = await self._bounded_body(request, receive)
            if body is None:
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "Request body is too large",
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            if _body_declares_modern_protocol(body):
                await self._send_response(
                    _jsonrpc_transport_error(
                        HTTPStatus.BAD_REQUEST,
                        "MCP-Protocol-Version header is required for a request "
                        "declaring a protocol version in _meta",
                        code=_JSONRPC_HEADER_MISMATCH,
                    ),
                    request,
                    scope,
                    receive,
                    send,
                )
                return
            receive = self._replay_body(body, receive)
        await self._call_with_principal(
            principal,
            token,
            scope,
            receive,
            self._cors_send(request, send),
        )

    async def _initialize_sdk_session(
        self,
        session: McpSession,
        principal: str,
        token: str | None,
        request: Request,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        bound = False
        internal_id = None

        async def bind_send(message: Message) -> None:
            nonlocal bound, internal_id
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                rewritten = []
                for key, value in headers:
                    if key.lower() == b"mcp-session-id":
                        internal_id = value.decode()
                        if 200 <= message["status"] < 300:
                            rewritten.append((key, session.session_id.encode()))
                    else:
                        rewritten.append((key, value))
                if (
                    internal_id is not None
                    and 200 <= message["status"] < 300
                    and self._session_manager is not None
                ):
                    transport = self._session_manager._server_instances.pop(
                        internal_id,
                        None,
                    )
                    if transport is not None:
                        transport.mcp_session_id = session.session_id
                        self._session_manager._server_instances[session.session_id] = (
                            transport
                        )
                        bound = True
                message = {**message, "headers": rewritten}
            await self._cors_send(request, send)(message)

        try:
            await self._call_with_principal(
                principal,
                token,
                scope,
                receive,
                bind_send,
            )
        finally:
            if not bound:
                self.registry.terminate(session.session_id, principal)
                if internal_id is not None:
                    self._remove_transport_id(internal_id)

    async def _call_with_principal(
        self,
        principal: str,
        bearer_token: str | None,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        from mcp.server.auth.middleware.auth_context import auth_context_var
        from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

        access_token = AccessToken(
            token=bearer_token or "",
            client_id=principal,
            scopes=["mcp"],
            expires_at=int(time.time()) + 3600,
        )
        context_token = auth_context_var.set(AuthenticatedUser(access_token))
        try:
            await self._app(scope, receive, send)
        finally:
            auth_context_var.reset(context_token)

    async def _bounded_body(
        self,
        request: Request,
        receive: Receive,
    ) -> bytes | None:
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            return None
        if declared > _MCP_MAX_REQUEST_BODY_BYTES:
            return None
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > _MCP_MAX_REQUEST_BODY_BYTES:
                return None
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        return bytes(body)

    @staticmethod
    def _replay_body(body: bytes, downstream_receive: Receive) -> Receive:
        delivered = False

        async def receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await downstream_receive()

        return receive

    def _remove_transport(self, session: McpSession) -> None:
        self._remove_transport_id(session.session_id)

    def _remove_transport_id(self, session_id: str) -> None:
        if self._session_manager is None:
            return
        transport = self._session_manager._server_instances.pop(
            session_id,
            None,
        )
        if transport is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(transport.terminate())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    def _legacy_binding_send(
        self,
        principal: str,
        reservation: McpSession,
        send: Send,
    ) -> Send:
        buffered = bytearray()
        bound = False

        async def binding_send(message: Message) -> None:
            nonlocal bound
            if message["type"] == "http.response.body" and not bound:
                remaining = max(0, 4_096 - len(buffered))
                buffered.extend(message.get("body", b"")[:remaining])
                match = re.search(rb"[?&]session_id=([^&\s\r\n]+)", buffered)
                if match is not None:
                    session_id = match.group(1).decode("ascii", errors="ignore")
                    if session_id and len(session_id) <= 128:
                        with self._legacy_lock:
                            existing = self._legacy_actual_to_reservation.get(
                                session_id
                            )
                            if existing is None:
                                self._legacy_actual_to_reservation[session_id] = (
                                    reservation.session_id
                                )
                                self._legacy_reservation_to_actual[
                                    reservation.session_id
                                ] = session_id
                        bound = True
            await send(message)

        return binding_send

    def _remove_legacy_session(self, session: McpSession) -> None:
        with self._legacy_lock:
            actual_id = self._legacy_reservation_to_actual.pop(
                session.session_id,
                None,
            )
            if actual_id is not None:
                self._legacy_actual_to_reservation.pop(actual_id, None)
        if actual_id is None or self._legacy_transport is None:
            return
        try:
            sdk_session_id = UUID(hex=actual_id)
        except ValueError:
            return
        writer = self._legacy_transport._read_stream_writers.pop(
            sdk_session_id,
            None,
        )
        if writer is not None:
            self._schedule_cleanup(writer.aclose())

    def _schedule_cleanup(self, cleanup) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            cleanup.close()
            return
        task = loop.create_task(cleanup)
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _run_lifespan(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        async def periodic_sweep() -> None:
            while True:
                await asyncio.sleep(self._sweep_interval_seconds)
                self.registry.sweep()
                self.legacy_registry.sweep()

        task = asyncio.create_task(periodic_sweep())
        try:
            await self._app(scope, receive, send)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._cleanup_tasks:
                await asyncio.gather(
                    *tuple(self._cleanup_tasks),
                    return_exceptions=True,
                )

    def _is_preflight(self, request: Request) -> bool:
        return bool(
            request.headers.get("origin")
            and request.headers.get("access-control-request-method")
        )

    def _cors_send(self, request: Request, send: Send) -> Send:
        origin = request.headers.get("origin")
        allowed = origin is not None and origin in self._security_guard.allowed_origins

        async def cors_send(message: Message) -> None:
            if allowed and message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"access-control-allow-origin", origin.encode()),
                        (b"vary", b"Origin"),
                        (b"access-control-allow-methods", _MCP_ALLOW.encode()),
                        (
                            b"access-control-allow-headers",
                            _MCP_CORS_ALLOW_HEADERS.encode(),
                        ),
                        (
                            b"access-control-expose-headers",
                            _MCP_CORS_EXPOSE_HEADERS.encode(),
                        ),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        return cors_send

    async def _send_response(
        self,
        response: Response,
        request: Request,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        await response(scope, receive, self._cors_send(request, send))


def secure_mcp_transport_app(
    app: ASGIApp,
    *,
    transport: str,
    security_guard=None,
    auth_config: AuthConfig | None = None,
    requires_auth: bool = False,
    session_manager=None,
    legacy_transport=None,
    registry_options: dict[str, Any] | None = None,
    sweep_interval_seconds: float = _MCP_SWEEP_INTERVAL_SECONDS,
    stateless_http: bool = False,
) -> McpTransportSecurityApp:
    """Apply the shared Argus guard without changing pinned-SDK wire bodies."""
    from argus.api.security import TransportSecurityGuard

    return McpTransportSecurityApp(
        app,
        transport=transport,
        security_guard=security_guard or TransportSecurityGuard.from_environment(),
        auth_config=auth_config or AuthConfig.from_env(),
        requires_auth=requires_auth,
        session_manager=session_manager,
        legacy_transport=legacy_transport,
        registry_options=registry_options,
        sweep_interval_seconds=sweep_interval_seconds,
        stateless_http=stateless_http,
    )


class StaticTokenVerifier:
    """Minimal bearer-token verifier for remote MCP transports."""

    def __init__(self, auth_config: AuthConfig):
        self._auth_config = auth_config

    async def verify_token(self, token: str) -> AccessToken | None:
        identity = self._auth_config.identity_for_token(token)
        if identity is None:
            return None
        return AccessToken(
            token=token,
            client_id=identity,
            scopes=["mcp"],
            expires_at=int(time.time()) + 3600,
        )


def _mcp_access_token() -> AccessToken | None:
    from mcp.server.auth.middleware.auth_context import get_access_token

    return get_access_token()


def _mcp_caller_identity() -> str:
    access_token = _mcp_access_token()
    return access_token.client_id if access_token else "local-mcp"


def _mcp_caller_token() -> str | None:
    access_token = _mcp_access_token()
    return access_token.token if access_token else None


def build_mcp_backend(environ=None):
    """Build the production HTTP adapter without local execution authority."""
    from argus.authority import (
        AuthorityConfigurationError,
        HttpAuthorityClient,
        adapter_execution_mode,
        authority_client_config,
    )

    mode = adapter_execution_mode(environ)
    if mode == "http":
        from argus.mcp.http_adapter import HttpMcpAdapter

        return HttpMcpAdapter(
            HttpAuthorityClient(authority_client_config(environ, adapter="mcp"))
        )
    raise AuthorityConfigurationError(
        "MCP requires ARGUS_AUTHORITY_URL and authority authentication; "
        "standalone development must use the external development MCP launcher"
    )


def _require_standalone_for_injected_development(
    backend,
    additional_registration,
) -> None:
    """Reject injected local authority at the public MCP entry boundary."""

    from argus.mcp.http_adapter import HttpMcpAdapter

    local_backend = backend is not None and type(backend) is not HttpMcpAdapter
    if not local_backend and additional_registration is None:
        return

    from argus.authority import (
        AuthorityConfigurationError,
        adapter_execution_mode,
    )

    if adapter_execution_mode() != "standalone":
        raise AuthorityConfigurationError(
            "Injected MCP development authority requires standalone mode via "
            "ARGUS_MCP_STANDALONE=true"
        )


def serve_mcp(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8001,
    *,
    backend=None,
    additional_registration=None,
):
    """Start MCP as a protocol adapter over the configured execution backend."""
    _require_standalone_for_injected_development(
        backend,
        additional_registration,
    )
    try:
        from mcp.server import MCPServer
    except ImportError:
        logger.error(
            "MCP package not installed. Install with: pip install 'argus-search[mcp]'"
        )
        return

    setup_logging("INFO")
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise SystemExit(f"Unknown MCP transport: {transport}")
    backend = build_mcp_backend() if backend is None else backend
    auth_config = AuthConfig.from_env()
    from argus.api.security import TransportSecurityGuard
    from argus.config import load_config
    from argus.workflows.research_targets import ResearchTarget

    # MCPServer evaluates postponed annotations against the defining module's
    # globals while registering the nested tool.  Keep the strict model out of
    # the import-time adapter surface, then expose it only for that registration.
    globals()["ResearchTarget"] = ResearchTarget

    config = load_config()
    remotely_exposed = _mcp_remote_exposed()
    security_guard = TransportSecurityGuard.from_environment()
    is_network_transport = transport in {"sse", "streamable-http"}
    use_remote_auth = is_network_transport and (
        config.env == "production"
        or remotely_exposed
        or remote_mcp_requires_auth(transport, host)
    )
    if use_remote_auth and not auth_config.has_caller_key():
        raise SystemExit(
            "Remote MCP requires ARGUS_CALLER_CREDENTIALS_JSON or ARGUS_API_KEY."
        )
    if is_network_transport:
        security_guard.validate_startup(
            production=use_remote_auth,
            bind_host="remotely-exposed" if remotely_exposed else host,
            has_bearer_auth=auth_config.has_caller_key(),
        )

    transport_security = None
    if is_network_transport and (
        security_guard.host_policy_explicit or security_guard.origin_policy_explicit
    ):
        from mcp.server.transport_security import TransportSecuritySettings

        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(security_guard.allowed_hosts),
            allowed_origins=list(security_guard.allowed_origins),
        )
    mcp = MCPServer("argus")

    @mcp.tool()
    async def search_web(
        query: str,
        mode: str = "discovery",
        max_results: int = 10,
        session_id: str = None,
        include_attribution: bool = False,
        free_only: bool = False,
        caller: str = "mcp",
    ) -> str:
        """Search through the authenticated Argus HTTP authority."""
        return await backend.search_web(
            query=query,
            mode=mode,
            max_results=max_results,
            session_id=session_id,
            include_attribution=include_attribution,
            free_only=free_only,
            caller_label=caller,
            caller_identity=_mcp_caller_identity(),
            token=_mcp_caller_token(),
        )

    @mcp.tool()
    async def recover_url(
        url: str,
        title: str = None,
        domain: str = None,
        caller: str = "mcp",
    ) -> str:
        """Recover a dead or moved URL through HTTP."""
        return await backend.recover_url(
            url,
            title,
            domain,
            caller_label=caller,
            caller_identity=_mcp_caller_identity(),
            token=_mcp_caller_token(),
        )

    @mcp.tool()
    async def expand_links(
        query: str,
        context: str = None,
        caller: str = "mcp",
    ) -> str:
        """Expand related links through HTTP."""
        return await backend.expand_links(
            query,
            context,
            caller_label=caller,
            caller_identity=_mcp_caller_identity(),
            token=_mcp_caller_token(),
        )

    @mcp.tool()
    async def extract_content(
        url: str,
        domain: str = None,
        mode: str = "default",
        content_type: str = "article",
        free_only: bool = False,
        caller: str = "mcp",
    ) -> str:
        """Extract content through the authenticated HTTP authority."""
        return await backend.extract_content(
            url,
            domain,
            mode=mode,
            content_type=content_type,
            free_only=free_only,
            caller_label=caller,
            caller_identity=_mcp_caller_identity(),
            token=_mcp_caller_token(),
        )

    @mcp.tool()
    async def search_health() -> str:
        """Read provider health from the HTTP authority."""
        return await backend.search_health(token=_mcp_caller_token())

    @mcp.tool()
    async def search_budgets() -> str:
        """Read durable provider budgets from the HTTP authority."""
        return await backend.search_budgets(token=_mcp_caller_token())

    from argus.mcp.http_adapter import HttpMcpAdapter

    v2_registered = isinstance(backend, HttpMcpAdapter)
    if v2_registered:
        from argus.mcp.v2_tools import register_v2_tools

        register_v2_tools(
            mcp,
            backend,
            caller_identity=_mcp_caller_identity,
            caller_token=_mcp_caller_token,
        )

        @mcp.tool()
        async def recover_dead_article(
            url: str,
            title: str = None,
            domain: str = None,
            caller: str = "mcp",
        ) -> str:
            return await backend.recover_dead_article(
                url,
                title,
                domain,
                caller_label=caller,
                token=_mcp_caller_token(),
            )

        @mcp.tool()
        async def capture_site(
            url: str,
            soft_page_limit: int = 75,
            hard_page_limit: int = 200,
            caller: str = "mcp",
        ) -> str:
            return await backend.capture_site(
                url,
                soft_page_limit=soft_page_limit,
                hard_page_limit=hard_page_limit,
                caller_label=caller,
                token=_mcp_caller_token(),
            )

        @mcp.tool()
        async def build_research_pack(
            topic: str,
            official_url: str = None,
            max_research_pages: int = 40,
            research_targets: list[ResearchTarget] = None,
            free_only: bool = False,
            response_format: str = "markdown",
            caller: str = "mcp",
        ) -> str:
            return await backend.build_research_pack(
                topic,
                official_url=official_url,
                max_research_pages=max_research_pages,
                research_targets=(
                    [target.model_dump(mode="json") for target in research_targets]
                    if research_targets
                    else None
                ),
                free_only=free_only,
                response_format=response_format,
                caller_label=caller,
                caller_identity=_mcp_caller_identity(),
                token=_mcp_caller_token(),
            )

        @mcp.tool()
        async def get_workflow_status(
            run_id: str,
            response_format: str = "markdown",
        ) -> str:
            """Read safe research-pack status from the authenticated authority."""
            return await backend.get_workflow_status(
                run_id,
                response_format=response_format,
                token=_mcp_caller_token(),
            )

        @mcp.tool()
        async def read_workflow_artifact(
            run_id: str,
            artifact: str = "report",
            offset: int = 0,
            max_bytes: int = 65536,
            response_format: str = "markdown",
        ) -> str:
            """Read a bounded report or manifest slice from the authority."""
            return await backend.read_workflow_artifact(
                run_id,
                artifact=artifact,
                offset=offset,
                max_bytes=max_bytes,
                response_format=response_format,
                token=_mcp_caller_token(),
            )

    if additional_registration is not None:
        additional_registration(
            mcp,
            backend,
            caller_identity=_mcp_caller_identity,
        )

    from argus.capabilities import (
        CapabilityManifestError,
        validate_complete_mcp_registration,
        validate_mcp_transport_registration,
        validate_mcp_tool_registration,
    )

    tool_registration = None
    if v2_registered:
        from argus.mcp.v2_tools import actual_v2_tool_registration

        tool_registration = actual_v2_tool_registration(mcp)
    if transport == "stdio":
        if tool_registration is not None:
            validate_mcp_tool_registration(tool_registration)
        logger.info("Starting Argus MCP server (%s)", transport)
        mcp.run(transport=transport)
        return
    transport_registration = _mcp_transport_registration(mcp)
    if tool_registration is None:
        validate_mcp_transport_registration(transport_registration)
    else:
        validate_complete_mcp_registration(
            transport_registration,
            tool_registration,
        )
    logger.info(
        "Starting Argus MCP server (%s)%s",
        transport,
        " with auth" if use_remote_auth else "",
    )
    if transport == "streamable-http":
        sdk_app = mcp.streamable_http_app(
            stateless_http=True,
            host=host,
            transport_security=transport_security,
        )
        session_manager = None
        legacy_transport = None
    else:
        from mcp.server.sse import SseServerTransport

        sdk_app = mcp.sse_app()
        session_manager = None
        legacy_transport = next(
            (
                route.app.__self__
                for route in sdk_app.routes
                if isinstance(
                    getattr(route.app, "__self__", None),
                    SseServerTransport,
                )
            ),
            None,
        )
        if legacy_transport is None:
            raise CapabilityManifestError(
                "Pinned MCP SDK legacy SSE transport registration is unavailable"
            )
    secured_app = secure_mcp_transport_app(
        sdk_app,
        transport=transport,
        security_guard=security_guard,
        auth_config=auth_config,
        requires_auth=use_remote_auth,
        session_manager=session_manager,
        legacy_transport=legacy_transport,
        stateless_http=transport == "streamable-http",
    )

    import uvicorn

    uvicorn.run(secured_app, host=host, port=port)
