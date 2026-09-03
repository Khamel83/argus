"""Direct transport with DNS revalidation and an IP-pinned dial target.

``LogicalOrigin`` is derived from the caller's URL and is kept intact for TLS
SNI, certificate validation, the HTTP ``Host`` header, and HTTP/2 authority.
The resolver-selected ``ResolvedAddress`` is used only for the socket dial.
No caller-supplied alternate origin or address is accepted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import inspect
import ipaddress
import socket
import ssl
from typing import Any
from urllib.parse import urlsplit

from .dns import (
    AddressDecision,
    DEFAULT_RESOLUTION_TIMEOUT_SECONDS,
    ResolvedAddress,
    resolve_public_addresses,
    validate_address_set,
)
from .models import LogicalOrigin, MAX_CONTENT_BYTES


MAX_TRANSPORT_HEADER_LENGTH = 1 * 1024 * 1024


class PinnedTransportError(RuntimeError):
    """Base class for failures before or during a pinned request."""


class TransportPolicyError(PinnedTransportError):
    """The request or address set cannot be dispatched by policy."""


class AddressPolicyError(TransportPolicyError):
    """The complete resolved address set was not safe to dispatch."""


class UnsupportedAddressPinning(TransportPolicyError):
    """The selected connector cannot prove that it dials the approved IP."""


class TransportDispatchError(PinnedTransportError):
    """The approved connector failed after policy admission."""


# Compatibility aliases used by integrations that name the capability first.
AddressPinningUnsupported = UnsupportedAddressPinning
UnsafeAddressSet = AddressPolicyError


def _bounded_header_items(headers: Any) -> tuple[tuple[str, str], ...]:
    if headers is None:
        return ()
    if isinstance(headers, Mapping):
        items = headers.items()
    else:
        try:
            items = iter(headers)
        except TypeError as exc:
            raise TypeError("headers must be a mapping or sequence") from exc
    normalized: list[tuple[str, str]] = []
    total = 0
    for item in items:
        try:
            name, value = item
        except (TypeError, ValueError) as exc:
            raise TypeError("headers must contain name/value pairs") from exc
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError("header names and values must be text")
        if not name or not name.isprintable() or any(character.isspace() for character in name):
            raise ValueError("header name is invalid")
        if not value.isprintable() or "\r" in value or "\n" in value:
            raise ValueError("header value is invalid")
        total += len(name) + len(value)
        if total > MAX_TRANSPORT_HEADER_LENGTH:
            raise ValueError("headers exceed the bounded transport limit")
        normalized.append((name, value))
    return tuple(normalized)


@dataclass(frozen=True, slots=True, init=False)
class TransportRequest:
    """A bounded caller request before address pinning."""

    url: str
    method: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    timeout: float | None
    logical_origin: LogicalOrigin | None
    dial_ip: str | None
    tls_server_name: str | None
    host_header: str | None
    authority: str | None
    server_hostname: str | None

    def __init__(
        self,
        url: str,
        method: str = "GET",
        headers: Mapping[str, str] | tuple[tuple[str, str], ...] | None = None,
        body: bytes | str | None = b"",
        timeout: float | None = None,
        *,
        content: bytes | str | None = None,
        logical_origin: LogicalOrigin | None = None,
        dial_ip: str | None = None,
        tls_server_name: str | None = None,
        host_header: str | None = None,
        authority: str | None = None,
        server_hostname: str | None = None,
    ) -> None:
        if not isinstance(url, str) or not url:
            raise ValueError("url must be non-empty text")
        if not isinstance(method, str) or not method or not method.isprintable():
            raise ValueError("method must be non-empty text")
        if any(character.isspace() for character in method):
            raise ValueError("method must not contain whitespace")
        if content is not None:
            if body not in (None, b"", ""):
                raise ValueError("body and content are mutually exclusive")
            body = content
        if body is None:
            body_bytes = b""
        elif isinstance(body, bytes):
            body_bytes = body
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
        else:
            raise TypeError("body must be bytes or text")
        if len(body_bytes) > MAX_CONTENT_BYTES:
            raise ValueError("body exceeds the bounded transport limit")
        if timeout is not None:
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or timeout <= 0
            ):
                raise ValueError("timeout must be a positive number")
            timeout = float(timeout)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "method", method.upper())
        object.__setattr__(self, "headers", _bounded_header_items(headers))
        object.__setattr__(self, "body", body_bytes)
        object.__setattr__(self, "timeout", timeout)
        object.__setattr__(self, "logical_origin", logical_origin)
        object.__setattr__(self, "dial_ip", dial_ip)
        object.__setattr__(self, "tls_server_name", tls_server_name)
        object.__setattr__(self, "host_header", host_header)
        object.__setattr__(self, "authority", authority)
        object.__setattr__(self, "server_hostname", server_hostname)

    @property
    def content(self) -> bytes:
        return self.body

    def get_header(self, name: str, default: str | None = None) -> str | None:
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return default

    @property
    def header_map(self) -> dict[str, str]:
        return dict(self.headers)

    @property
    def http2_headers(self) -> tuple[tuple[str, str], ...]:
        """Return connector headers with the logical HTTP/2 authority."""

        return tuple(
            (name, value) for name, value in self.headers if name.lower() != "host"
        ) + ((":authority", self.authority),)


@dataclass(frozen=True, slots=True)
class PinnedRequest:
    """The connector-facing request with one approved dial target."""

    url: str
    method: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    timeout: float | None
    logical_origin: LogicalOrigin
    dial_address: ResolvedAddress
    tls_server_name: str
    host_header: str
    authority: str

    @property
    def dial_ip(self) -> str:
        return self.dial_address.address

    @property
    def server_hostname(self) -> str:
        return self.tls_server_name

    @property
    def http2_authority(self) -> str:
        return self.authority

    @property
    def resolved_address(self) -> ResolvedAddress:
        return self.dial_address

    @property
    def content(self) -> bytes:
        return self.body

    @property
    def header_map(self) -> dict[str, str]:
        return dict(self.headers)


@dataclass(frozen=True, slots=True, init=False)
class TransportResponse:
    """A bounded response projection with dispatch identity evidence."""

    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    url: str
    dial_ip: str
    tls_server_name: str
    host_header: str
    http2_authority: str

    def __init__(
        self,
        status_code: int = 0,
        headers: Mapping[str, str] | tuple[tuple[str, str], ...] | None = None,
        body: bytes | str | None = b"",
        url: str = "",
        dial_ip: str = "",
        tls_server_name: str = "",
        host_header: str = "",
        http2_authority: str = "",
        *,
        content: bytes | str | None = None,
        authority: str | None = None,
    ) -> None:
        if content is not None:
            if body not in (None, b"", ""):
                raise ValueError("body and content are mutually exclusive")
            body = content
        if type(status_code) is not int or not 0 <= status_code <= 999:
            raise ValueError("status_code must be an integer from 0 through 999")
        if body is None:
            body_bytes = b""
        elif isinstance(body, bytes):
            body_bytes = body
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
        else:
            raise TypeError("body must be bytes or text")
        if len(body_bytes) > MAX_CONTENT_BYTES:
            raise ValueError("response exceeds the bounded transport limit")
        if authority is not None:
            if http2_authority and http2_authority != authority:
                raise ValueError("authority values must match")
            http2_authority = authority
        object.__setattr__(self, "status_code", status_code)
        object.__setattr__(self, "headers", _bounded_header_items(headers))
        object.__setattr__(self, "body", body_bytes)
        object.__setattr__(self, "url", url if isinstance(url, str) else str(url))
        object.__setattr__(self, "dial_ip", dial_ip)
        object.__setattr__(self, "tls_server_name", tls_server_name)
        object.__setattr__(self, "host_header", host_header)
        object.__setattr__(self, "http2_authority", http2_authority)

    @property
    def status(self) -> int:
        return self.status_code

    @property
    def content(self) -> bytes:
        return self.body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def get_header(self, name: str, default: str | None = None) -> str | None:
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return default

    @property
    def headers_dict(self) -> dict[str, str]:
        return dict(self.headers)

    @property
    def authority(self) -> str:
        return self.http2_authority


@dataclass(frozen=True, slots=True)
class ConnectionBinding:
    """Safe evidence for the address bound to a pooled connection."""

    logical_origin: LogicalOrigin
    dial_address: ResolvedAddress

    @property
    def dial_ip(self) -> str:
        return self.dial_address.address


class _ConnectionPool:
    """Small default pool ledger; it never stores a raw socket handle."""

    def __init__(self) -> None:
        self._bindings: dict[str, ConnectionBinding] = {}

    def current(self, origin: LogicalOrigin) -> ConnectionBinding | None:
        return self._bindings.get(origin.origin)

    def record(self, origin: LogicalOrigin, address: ResolvedAddress) -> None:
        self._bindings[origin.origin] = ConnectionBinding(origin, address)


def _origin_from_url(url: str) -> tuple[LogicalOrigin, Any]:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise ValueError(f"unsupported URL scheme: {parsed.scheme}")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials in URL are not allowed")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("URL has no hostname")
        port = parsed.port or (443 if scheme == "https" else 80)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid transport URL: {exc}") from exc
    return LogicalOrigin(scheme=scheme, hostname=hostname, port=port), parsed


def _format_host(hostname: str) -> str:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname
    return f"[{hostname}]" if address.version == 6 else hostname


def _host_header(origin: LogicalOrigin) -> str:
    host = _format_host(origin.hostname)
    default_port = 443 if origin.scheme == "https" else 80
    return host if origin.port == default_port else f"{host}:{origin.port}"


def _path_from_url(parsed: Any) -> str:
    path = parsed.path or "/"
    return f"{path}?{parsed.query}" if parsed.query else path


def _override_present(value: Any) -> bool:
    return value is not None and value != ""


def _normalize_request(request: Any) -> TransportRequest:
    if isinstance(request, TransportRequest):
        return request
    if isinstance(request, str):
        return TransportRequest(url=request)
    if isinstance(request, Mapping):
        url = request.get("url", request.get("normalized_url", request.get("full_url")))
        if url is None:
            raise TypeError("request must expose a URL")
        return TransportRequest(
            url=str(url),
            method=request.get("method", "GET"),
            headers=request.get("headers", {}),
            body=request.get("body", request.get("content", b"")),
            timeout=request.get("timeout"),
            logical_origin=request.get("logical_origin"),
            dial_ip=request.get("dial_ip"),
            tls_server_name=request.get("tls_server_name"),
            host_header=request.get("host_header"),
            authority=request.get("authority"),
            server_hostname=request.get("server_hostname"),
        )
    url = getattr(request, "url", None)
    if url is None:
        url = getattr(request, "normalized_url", None)
    if url is None:
        url = getattr(request, "full_url", None)
    if url is None:
        raise TypeError("request must expose a URL")
    url = str(url)
    method = getattr(request, "method", None)
    if method is None:
        get_method = getattr(request, "get_method", None)
        method = get_method() if callable(get_method) else "GET"
    headers = getattr(request, "headers", None)
    if headers is None:
        header_items = getattr(request, "header_items", None)
        headers = header_items() if callable(header_items) else {}
    body = getattr(request, "content", None)
    if body is None:
        body = getattr(request, "body", None)
    if body is None:
        body = getattr(request, "data", b"")
    return TransportRequest(
        url=url,
        method=method,
        headers=headers,
        body=body,
        timeout=getattr(request, "timeout", None),
        logical_origin=getattr(request, "logical_origin", None),
        dial_ip=getattr(request, "dial_ip", None),
        tls_server_name=getattr(request, "tls_server_name", None),
        host_header=getattr(request, "host_header", None),
        authority=getattr(request, "authority", None),
        server_hostname=getattr(request, "server_hostname", None),
    )


def _validate_caller_boundary(request: TransportRequest, origin: LogicalOrigin) -> None:
    if request.logical_origin is not None and request.logical_origin != origin:
        raise ValueError("caller cannot override logical origin")
    for value in (
        request.dial_ip,
        request.tls_server_name,
        request.host_header,
        request.authority,
        request.server_hostname,
    ):
        if _override_present(value):
            raise ValueError("caller cannot override logical origin or dial target")
    for name, _value in request.headers:
        lowered = name.lower()
        if lowered in {
            "host",
            ":authority",
            "authority",
            "x-forwarded-host",
            "forwarded",
        }:
            raise ValueError("caller cannot override logical origin headers")


def _dispatch_target(dispatcher: Any) -> Callable[..., Any]:
    if dispatcher is None:
        return _SocketDispatcher().send
    for name in (
        "send_pinned",
        "request_pinned",
        "dispatch_pinned",
        "dispatch",
        "send",
        "request",
    ):
        target = getattr(dispatcher, name, None)
        if callable(target):
            return target
    if callable(dispatcher):
        return dispatcher
    raise TypeError("dispatcher must be callable or expose send/request")


def _invoke_dispatch(target: Callable[..., Any], prepared: PinnedRequest) -> Any:
    kwargs = {
        "request": prepared,
        "prepared_request": prepared,
        "url": prepared.url,
        "method": prepared.method,
        "headers": prepared.headers,
        "body": prepared.body,
        "content": prepared.body,
        "logical_origin": prepared.logical_origin,
        "dial_address": prepared.dial_address,
        "resolved_address": prepared.dial_address,
        "dial_ip": prepared.dial_ip,
        "tls_server_name": prepared.tls_server_name,
        "server_hostname": prepared.tls_server_name,
        "host_header": prepared.host_header,
        "authority": prepared.authority,
        "http2_authority": prepared.authority,
    }
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return target(prepared)
    parameters = tuple(signature.parameters.values())
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return target(**kwargs)
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    keyword_only = [
        parameter
        for parameter in parameters
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    ]
    if len(positional) == 1 and not keyword_only:
        parameter = positional[0]
        if parameter.name in kwargs and parameter.name != "url":
            return target(**{parameter.name: kwargs[parameter.name]})
        return target(prepared)
    call_kwargs = {
        parameter.name: kwargs[parameter.name]
        for parameter in (*positional, *keyword_only)
        if parameter.name in kwargs
    }
    return target(**call_kwargs)


def _coerce_response(value: Any, prepared: PinnedRequest) -> TransportResponse:
    if isinstance(value, TransportResponse):
        return TransportResponse(
            status_code=value.status_code,
            headers=value.headers,
            body=value.body,
            url=prepared.url,
            dial_ip=prepared.dial_ip,
            tls_server_name=prepared.tls_server_name,
            host_header=prepared.host_header,
            http2_authority=prepared.authority,
        )
    if value is None:
        return TransportResponse(
            status_code=200,
            url=prepared.url,
            dial_ip=prepared.dial_ip,
            tls_server_name=prepared.tls_server_name,
            host_header=prepared.host_header,
            http2_authority=prepared.authority,
        )
    if isinstance(value, Mapping):
        status = value.get("status_code", value.get("status", 200))
        headers = value.get("headers", {})
        body = value.get("body", value.get("content", b""))
    elif isinstance(value, (bytes, str)):
        status, headers, body = 200, {}, value
    else:
        status = getattr(value, "status_code", getattr(value, "status", 200))
        headers = getattr(value, "headers", {})
        try:
            body = getattr(value, "content", b"")
        except Exception:
            body = b""
    try:
        status = int(status)
    except (TypeError, ValueError) as exc:
        raise TransportDispatchError("dispatcher returned an invalid status") from exc
    return TransportResponse(
        status_code=status,
        headers=headers,
        body=body,
        url=prepared.url,
        dial_ip=prepared.dial_ip,
        tls_server_name=prepared.tls_server_name,
        host_header=prepared.host_header,
        http2_authority=prepared.authority,
    )


def _pool_current(pool: Any, origin: LogicalOrigin) -> Any:
    if pool is None:
        return None
    if isinstance(pool, Mapping):
        return pool.get(origin.origin, pool.get(origin.hostname))
    for name in ("current", "get", "lookup", "connection_for", "get_connection"):
        method = getattr(pool, name, None)
        if not callable(method):
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return method(origin)
        for args in ((origin,), (origin.origin,), (origin.hostname, origin.port)):
            try:
                signature.bind(*args)
            except TypeError:
                continue
            return method(*args)
    return None


def _pool_connection_address(connection: Any) -> ResolvedAddress | None:
    if connection is None:
        return None
    if isinstance(connection, ConnectionBinding):
        return connection.dial_address
    candidate = connection
    for name in ("dial_address", "resolved_address", "address"):
        value = getattr(candidate, name, None)
        if value is not None:
            candidate = value
            break
    if isinstance(candidate, ResolvedAddress):
        return candidate
    for name in ("dial_ip", "ip", "host"):
        value = getattr(candidate, name, None)
        if value is not None:
            try:
                return ResolvedAddress(value, int(getattr(candidate, "port", 0) or 0))
            except (TypeError, ValueError):
                return None
    return None


def _invoke_pool_recheck(pool: Any, origin: LogicalOrigin, decision: AddressDecision) -> Any:
    if pool is None:
        return None
    for name in ("recheck", "validate", "validate_connection", "check"):
        method = getattr(pool, name, None)
        if not callable(method):
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return method(origin, decision)
        names = set(signature.parameters)
        if "decision" in names:
            return method(origin=origin, decision=decision)
        if "addresses" in names or "approved_addresses" in names:
            parameter = "addresses" if "addresses" in names else "approved_addresses"
            return method(origin=origin, **{parameter: decision.approved_addresses})
        for args in ((origin, decision), (origin, decision.approved_addresses)):
            try:
                signature.bind(*args)
            except TypeError:
                continue
            return method(*args)
    return None


def _pool_record(pool: Any, origin: LogicalOrigin, address: ResolvedAddress) -> None:
    if pool is None:
        return
    if isinstance(pool, _ConnectionPool):
        pool.record(origin, address)
        return
    for name in ("record", "put", "store", "bind"):
        method = getattr(pool, name, None)
        if not callable(method):
            continue
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            method(origin, address)
            return
        for args in ((origin, address), (origin.origin, address), (ConnectionBinding(origin, address),)):
            try:
                signature.bind(*args)
            except TypeError:
                continue
            method(*args)
            return


class _SocketDispatcher:
    supports_address_pinning = True

    def send(self, prepared: PinnedRequest) -> TransportResponse:
        origin = prepared.logical_origin
        timeout = prepared.timeout or 30.0
        sock = socket.socket(prepared.dial_address.family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            # Connecting to the literal approved address avoids a second DNS
            # lookup performed by socket.create_connection.
            sock.connect(prepared.dial_address.socket_address)
            if origin.scheme == "https":
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=prepared.tls_server_name)
            body = prepared.body
            headers = list(prepared.headers)
            if not any(name.lower() == "connection" for name, _ in headers):
                headers.append(("Connection", "close"))
            lines = [f"{prepared.method} {_path_from_url(urlsplit(prepared.url))} HTTP/1.1"]
            lines.extend(f"{name}: {value}" for name, value in headers)
            payload = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
            sock.sendall(payload)
            return self._read_response(sock, prepared)
        except (OSError, ssl.SSLError) as exc:
            raise TransportDispatchError("pinned connection failed") from exc
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _read_response(sock: socket.socket, prepared: PinnedRequest) -> TransportResponse:
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_CONTENT_BYTES:
            chunk = sock.recv(min(65_536, MAX_CONTENT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if b"\r\n\r\n" in b"".join(chunks) and len(chunks) > 1:
                # The direct transport is a bounded compatibility connector;
                # read to EOF so a close-delimited body remains available.
                continue
        wire = b"".join(chunks)
        if len(wire) > MAX_CONTENT_BYTES:
            raise TransportDispatchError("response exceeds the bounded transport limit")
        head, separator, body = wire.partition(b"\r\n\r\n")
        if not separator:
            raise TransportDispatchError("invalid HTTP response")
        lines = head.split(b"\r\n")
        try:
            status = int(lines[0].split(maxsplit=2)[1])
        except (IndexError, ValueError) as exc:
            raise TransportDispatchError("invalid HTTP status line") from exc
        response_headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            try:
                name, value = line.decode("latin-1").split(":", 1)
            except ValueError as exc:
                raise TransportDispatchError("invalid HTTP response header") from exc
            response_headers.append((name.strip(), value.strip()))
        return TransportResponse(
            status_code=status,
            headers=tuple(response_headers),
            body=body,
            url=prepared.url,
            dial_ip=prepared.dial_ip,
            tls_server_name=prepared.tls_server_name,
            host_header=prepared.host_header,
            http2_authority=prepared.authority,
        )


class PinnedTransport:
    """Resolve, validate, and dispatch one request to an approved IP."""

    def __init__(
        self,
        resolver: Any = None,
        clock: Any = None,
        dispatcher: Any = None,
        connector: Any = None,
        transport: Any = None,
        client: Any = None,
        pool: Any = None,
        dns_timeout: float = DEFAULT_RESOLUTION_TIMEOUT_SECONDS,
        supports_address_pinning: bool | None = None,
        address_pinning_supported: bool | None = None,
        sender: Any = None,
        connection_pool: Any = None,
        http_client: Any = None,
    ) -> None:
        choices = [
            item
            for item in (
                dispatcher,
                connector,
                transport,
                client,
                sender,
                http_client,
            )
            if item is not None
        ]
        if len(choices) > 1:
            raise ValueError(
                "provide only one dispatcher/connector/transport/client/sender"
            )
        self._dispatcher = choices[0] if choices else None
        self._resolver = resolver
        self._clock = clock
        self._dns_timeout = dns_timeout
        if pool is not None and connection_pool is not None and pool is not connection_pool:
            raise ValueError("pool and connection_pool must identify one pool")
        self._pool = pool if pool is not None else connection_pool
        if self._pool is None:
            self._pool = _ConnectionPool()
        if supports_address_pinning is not None and address_pinning_supported is not None:
            if supports_address_pinning != address_pinning_supported:
                raise ValueError("address pinning capability values must match")
        explicit = (
            supports_address_pinning
            if supports_address_pinning is not None
            else address_pinning_supported
        )
        if explicit is not None and type(explicit) is not bool:
            raise TypeError("supports_address_pinning must be a boolean")
        self._supports_address_pinning = (
            bool(explicit) if explicit is not None else self._detect_pinning_support(self._dispatcher)
        )

    @staticmethod
    def _detect_pinning_support(dispatcher: Any) -> bool:
        if dispatcher is None:
            return True
        for name in (
            "supports_address_pinning",
            "address_pinning_supported",
            "supports_pinning",
        ):
            value = getattr(dispatcher, name, None)
            if value is not None:
                return bool(value)
        # httpx transports expose ``handle_request`` but do not offer a
        # supported way to replace DNS while retaining TLS identity.
        if hasattr(dispatcher, "handle_request") or hasattr(
            dispatcher, "handle_async_request"
        ):
            return False
        return True

    @property
    def supports_address_pinning(self) -> bool:
        return self._supports_address_pinning

    def request(self, request: Any) -> TransportResponse:
        """Dispatch one pre-authorized request or fail before any dispatch."""

        if not self._supports_address_pinning:
            raise UnsupportedAddressPinning(
                "selected connector does not support approved-address pinning"
            )
        normalized = _normalize_request(request)
        origin, _ = _origin_from_url(normalized.url)
        _validate_caller_boundary(normalized, origin)
        addresses = resolve_public_addresses(
            origin.hostname,
            origin.port,
            self._resolver,
            self._clock,
            timeout=self._dns_timeout,
        )
        decision = validate_address_set(addresses)
        if not decision.allowed:
            raise AddressPolicyError(decision.reason or "address set rejected")
        try:
            approved = tuple(
                address.with_port(origin.port) for address in decision.approved_addresses
            )
        except ValueError as exc:
            raise AddressPolicyError("resolved address port is ambiguous") from exc
        decision = AddressDecision(True, approved)
        pooled = _pool_current(self._pool, origin)
        recheck = _invoke_pool_recheck(self._pool, origin, decision)
        if recheck is False or (
            isinstance(recheck, AddressDecision) and not recheck.allowed
        ):
            raise AddressPolicyError("pooled connection failed address recheck")
        pooled_address = _pool_connection_address(pooled)
        if pooled_address is not None:
            pool_reason = validate_address_set((pooled_address,)).reason
            if pool_reason:
                raise AddressPolicyError("pooled connection address is no longer safe")
        selected = self._select_address(approved, pooled_address)
        host_header = _host_header(origin)
        prepared_headers = tuple(normalized.headers) + (("Host", host_header),)
        prepared = PinnedRequest(
            url=normalized.url,
            method=normalized.method,
            headers=prepared_headers,
            body=normalized.body,
            timeout=normalized.timeout,
            logical_origin=origin,
            dial_address=selected,
            tls_server_name=origin.hostname,
            host_header=host_header,
            authority=host_header,
        )
        target = _dispatch_target(self._dispatcher)
        try:
            result = _invoke_dispatch(target, prepared)
            if inspect.isawaitable(result):
                raise TransportDispatchError("async dispatchers are not supported")
            response = _coerce_response(result, prepared)
        except PinnedTransportError:
            raise
        except Exception as exc:
            raise TransportDispatchError("pinned dispatch failed") from exc
        _pool_record(self._pool, origin, selected)
        return response

    @staticmethod
    def _select_address(
        addresses: tuple[ResolvedAddress, ...], pooled: ResolvedAddress | None
    ) -> ResolvedAddress:
        if pooled is not None:
            for address in addresses:
                if address.address == pooled.address and address.family == pooled.family:
                    return address
        # Resolver order is meaningful for local policy, but deterministic
        # family/address ordering prevents an ambiguous choice from changing
        # between equivalent resolver implementations.
        return sorted(addresses, key=lambda item: (item.family, item.address))[0]


__all__ = [
    "AddressPinningUnsupported",
    "AddressPolicyError",
    "ConnectionBinding",
    "LogicalOrigin",
    "PinnedRequest",
    "PinnedTransport",
    "PinnedTransportError",
    "TransportDispatchError",
    "TransportPolicyError",
    "TransportRequest",
    "TransportResponse",
    "ResolvedAddress",
    "UnsupportedAddressPinning",
    "UnsafeAddressSet",
]
