"""Bounded DNS resolution and fail-closed public-address policy.

The resolver in this module is deliberately small and dependency-free.  It
returns only immutable dial targets.  Callers must validate the complete
returned set before they dispatch a connection; filtering an unsafe answer
out of a mixed DNS response is not safe.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import datetime as _datetime
import inspect
import ipaddress
import queue
import socket
import threading
import time
from typing import Any


DEFAULT_RESOLUTION_TIMEOUT_SECONDS = 2.0
MAX_RESOLUTION_ADDRESSES = 32
MAX_DNS_HOSTNAME_LENGTH = 2_048

# Internal service traffic may use the private networks that Docker and
# Tailscale deployments assign to configured services.  Keep this list
# explicit.  In particular, loopback, link-local, multicast, reserved, and
# carrier-grade NAT addresses are not service destinations.
_PRIVATE_SERVICE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class DNSResolutionError(RuntimeError):
    """Raised by optional resolver integrations, never exposed by default."""


@dataclass(frozen=True, slots=True, init=False)
class ResolvedAddress:
    """One immutable IP address and the port to which it may be dialled.

    ``address`` and ``ip`` are accepted as aliases because resolver libraries
    use both names.  A zero port means "use the request port" and is useful for
    resolver adapters that return only an address.
    """

    address: str
    port: int
    family: int

    def __init__(
        self,
        address: str | ipaddress._BaseAddress | None = None,
        port: int = 0,
        family: int | None = None,
        *,
        ip: str | ipaddress._BaseAddress | None = None,
    ) -> None:
        if address is None:
            address = ip
        elif ip is not None and str(address) != str(ip):
            raise ValueError("address and ip must identify the same target")
        if address is None:
            raise TypeError("address is required")
        try:
            parsed = ipaddress.ip_address(str(address))
        except ValueError as exc:
            raise ValueError("address must be a valid IP address") from exc
        if "%" in str(address):
            raise ValueError("scoped IP addresses are not supported")
        if type(port) is not int or not 0 <= port <= 65_535:
            raise ValueError("port must be an integer from 0 through 65535")
        inferred_family = socket.AF_INET if parsed.version == 4 else socket.AF_INET6
        if family is None:
            family = inferred_family
        if isinstance(family, bool) or not isinstance(family, int):
            raise TypeError("family must be an integer address family")
        family = int(family)
        object.__setattr__(self, "address", str(parsed))
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "family", family)

    @property
    def ip(self) -> str:
        """Compatibility alias for the normalized address text."""

        return self.address

    @property
    def host(self) -> str:
        """Compatibility alias used by connector implementations."""

        return self.address

    @property
    def is_ipv4(self) -> bool:
        return self.family == socket.AF_INET

    @property
    def is_ipv6(self) -> bool:
        return self.family == socket.AF_INET6

    @property
    def socket_address(self) -> tuple[Any, ...]:
        """Return the address tuple expected by a socket connector."""

        if self.is_ipv6:
            return (self.address, self.port, 0, 0)
        return (self.address, self.port)

    @property
    def dial_ip(self) -> str:
        return self.address

    def with_port(self, port: int) -> "ResolvedAddress":
        """Return this target with a request port applied."""

        if self.port == port:
            return self
        if self.port not in (0, port):
            raise ValueError("resolved address port does not match request port")
        return ResolvedAddress(self.address, port, family=self.family)


@dataclass(frozen=True, slots=True)
class AddressDecision:
    """The result of validating an entire DNS address set."""

    allowed: bool
    addresses: tuple[ResolvedAddress, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be a boolean")
        normalized = tuple(self.addresses or ())
        if any(not isinstance(item, ResolvedAddress) for item in normalized):
            raise TypeError("addresses must contain ResolvedAddress values")
        object.__setattr__(self, "addresses", normalized)
        if not isinstance(self.reason, str):
            raise TypeError("reason must be text")

    def __bool__(self) -> bool:
        return self.allowed

    @property
    def safe(self) -> bool:
        return self.allowed

    @property
    def valid(self) -> bool:
        return self.allowed

    @property
    def approved(self) -> bool:
        return self.allowed

    @property
    def approved_addresses(self) -> tuple[ResolvedAddress, ...]:
        return self.addresses if self.allowed else ()

    @property
    def safe_addresses(self) -> tuple[ResolvedAddress, ...]:
        return self.approved_addresses

    @property
    def rejection_reason(self) -> str:
        return self.reason

    @property
    def selected_address(self) -> ResolvedAddress | None:
        return self.addresses[0] if self.allowed and self.addresses else None


def _clock_value(clock: Any) -> float:
    """Read a monotonic-ish value from common injected clock shapes."""

    candidate = clock
    if candidate is None:
        candidate = time.monotonic
    if not callable(candidate):
        for attribute in ("monotonic", "time", "now"):
            value = getattr(candidate, attribute, None)
            if value is not None:
                candidate = value
                break
    try:
        value = candidate() if callable(candidate) else candidate
    except Exception:
        value = time.monotonic()
    if isinstance(value, _datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_datetime.timezone.utc)
        return value.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return time.monotonic()


def _bounded_call(operation: Callable[[], Any], timeout: float) -> tuple[bool, Any]:
    """Run a resolver operation with a daemon-thread deadline.

    A resolver supplied by a caller can block independently of the socket
    timeout.  The worker is daemonized so a stuck resolver cannot hold process
    shutdown, and the caller receives no addresses after the deadline.
    """

    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result_queue.put((True, operation()), block=False)
        except BaseException as exc:  # communicate resolver failures safely
            try:
                result_queue.put((False, exc), block=False)
            except queue.Full:
                pass

    worker = threading.Thread(target=run, name="argus-dns", daemon=True)
    worker.start()
    worker.join(max(0.0, timeout))
    if worker.is_alive():
        return False, TimeoutError("DNS resolution timed out")
    try:
        return result_queue.get_nowait()
    except queue.Empty:
        return False, DNSResolutionError("resolver returned no result")


def _bindable_call(
    method: Callable[..., Any],
    hostname: str,
    port: int,
    *,
    timeout: float | None = None,
) -> Any:
    """Call common resolver methods without assuming one concrete signature."""

    family = socket.AF_UNSPEC
    socktype = socket.SOCK_STREAM
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        signature = None

    accepts_timeout = signature is not None and any(
        name in {"timeout", "timeout_seconds"}
        for name in signature.parameters
    )
    timeout_candidates = (
        ((hostname, port, timeout), {}),
        ((hostname, port), {"timeout": timeout}),
    ) if timeout is not None and accepts_timeout else ()
    candidates = (*timeout_candidates,
        ((hostname, port, family, socktype), {}),
        ((hostname, port), {"family": family, "type": socktype}),
        ((hostname, port), {}),
        ((hostname,), {}),
    )
    if signature is not None:
        for args, kwargs in candidates:
            try:
                signature.bind(*args, **kwargs)
            except TypeError:
                continue
            return method(*args, **kwargs)
        raise TypeError("resolver has no supported address lookup signature")

    last_error: TypeError | None = None
    for args, kwargs in candidates:
        try:
            return method(*args, **kwargs)
        except TypeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise DNSResolutionError("resolver has no supported address lookup signature")


def _resolver_method(resolver: Any) -> Callable[..., Any]:
    if resolver is None:
        return socket.getaddrinfo
    if isinstance(resolver, Mapping):
        return lambda hostname, port: resolver.get(hostname, ())
    for name in ("getaddrinfo", "resolve", "lookup"):
        method = getattr(resolver, name, None)
        if callable(method):
            return method
    if callable(resolver):
        return resolver
    raise TypeError("resolver must be callable or expose getaddrinfo")


def _iter_raw_records(raw: Any) -> Iterable[Any]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes, bytearray, ResolvedAddress, Mapping)):
        return (raw,)
    try:
        return iter(raw)
    except TypeError:
        return (raw,)


def _record_parts(record: Any, requested_port: int) -> tuple[Any, int, int | None] | None:
    if isinstance(record, ResolvedAddress):
        if requested_port and record.port not in (0, requested_port):
            return None
        return record.address, requested_port, record.family
    if isinstance(record, (str, bytes, bytearray, ipaddress._BaseAddress)):
        return record, requested_port, None
    if isinstance(record, Mapping):
        address = record.get("address", record.get("ip", record.get("host")))
        if address is None:
            return None
        port = record.get("port", requested_port)
        family = record.get("family")
        if port in (None, 0):
            port = requested_port
        if requested_port and port != requested_port:
            return None
        return address, requested_port, family
    if isinstance(record, (tuple, list)):
        if len(record) >= 5 and isinstance(record[0], int):
            family = record[0]
            sockaddr = record[4]
            if not isinstance(sockaddr, (tuple, list)) or not sockaddr:
                return None
            address = sockaddr[0]
            port = sockaddr[1] if len(sockaddr) > 1 else requested_port
            if port in (None, 0):
                port = requested_port
            if requested_port and port != requested_port:
                return None
            return address, requested_port, family
        if len(record) == 2 and isinstance(record[1], int):
            if record[1] not in (0, requested_port):
                return None
            return record[0], requested_port, None
        if len(record) >= 1 and isinstance(record[0], (str, bytes, bytearray)):
            return record[0], requested_port, None
    return None


def _normalize_records(raw: Any, requested_port: int) -> tuple[ResolvedAddress, ...]:
    normalized: list[ResolvedAddress] = []
    seen: set[tuple[str, int, int]] = set()
    iterator = _iter_raw_records(raw)
    for index, record in enumerate(iterator):
        if index >= MAX_RESOLUTION_ADDRESSES:
            # Never silently truncate an answer.  A caller must validate the
            # complete set before dispatch, so an over-bound response fails.
            return ()
        parts = _record_parts(record, requested_port)
        if parts is None:
            return ()
        address, port, family = parts
        try:
            resolved = ResolvedAddress(address, port, family=family)
        except (TypeError, ValueError):
            return ()
        key = (resolved.address, resolved.port, resolved.family)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(resolved)
    return tuple(normalized)


def resolve_public_addresses(
    hostname: str,
    port: int,
    resolver: Any = None,
    clock: Any = None,
    *,
    timeout: float = DEFAULT_RESOLUTION_TIMEOUT_SECONDS,
) -> tuple[ResolvedAddress, ...]:
    """Resolve a hostname within a bounded deadline.

    The function intentionally returns an empty tuple for DNS errors, no
    answers, malformed records, and an over-bound response.  Empty output is
    not an approval: callers must pass it to :func:`validate_address_set`,
    which rejects it.  The requested port remains authoritative.
    """

    if not isinstance(hostname, str):
        return ()
    hostname = hostname.strip()
    if not hostname or len(hostname) > MAX_DNS_HOSTNAME_LENGTH:
        return ()
    if any(not character.isprintable() or character.isspace() for character in hostname):
        return ()
    if type(port) is not int or not 1 <= port <= 65_535:
        return ()
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        return ()

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return (ResolvedAddress(literal, port),)

    method = _resolver_method(resolver)
    started = _clock_value(clock)

    def operation() -> tuple[ResolvedAddress, ...]:
        raw = _bindable_call(method, hostname, port, timeout=float(timeout))
        return _normalize_records(raw, port)

    ok, value = _bounded_call(operation, float(timeout))
    elapsed = _clock_value(clock) - started
    if not ok or elapsed > float(timeout):
        return ()
    if not isinstance(value, tuple) or any(
        not isinstance(item, ResolvedAddress) for item in value
    ):
        return ()
    return value


def resolve_trusted_service_addresses(
    hostname: str,
    port: int,
    resolver: Any = None,
    clock: Any = None,
    *,
    timeout: float = DEFAULT_RESOLUTION_TIMEOUT_SECONDS,
) -> tuple[ResolvedAddress, ...]:
    """Resolve one configured private service without broadening URL policy.

    The complete DNS answer must be RFC1918 or ULA.  A mixed, unsafe, empty,
    or malformed answer returns no addresses, so callers cannot recover by
    dropping one suspicious answer.
    """

    addresses = resolve_public_addresses(
        hostname,
        port,
        resolver,
        clock,
        timeout=timeout,
    )
    decision = validate_trusted_service_address_set(addresses)
    return decision.approved_addresses if decision.allowed else ()


def _address_reason(
    address: ResolvedAddress,
    *,
    allow_private: bool = False,
    require_private: bool = False,
) -> str | None:
    try:
        parsed = ipaddress.ip_address(address.address)
    except ValueError:
        return f"invalid address blocked: {address.address}"
    if address.family not in (socket.AF_INET, socket.AF_INET6):
        return f"unsupported address family blocked: {address.address}"
    expected_family = socket.AF_INET if parsed.version == 4 else socket.AF_INET6
    if address.family != expected_family:
        return f"ambiguous address family blocked: {address.address}"
    if parsed.is_unspecified:
        return f"unspecified address blocked: {address.address}"
    if parsed.is_loopback:
        return f"loopback address blocked: {address.address}"
    if parsed.is_link_local:
        return f"link-local address blocked: {address.address}"
    if parsed.is_multicast:
        return f"multicast address blocked: {address.address}"
    if parsed.version == 4 and ipaddress.ip_address("100.64.0.0") <= parsed <= ipaddress.ip_address(
        "100.127.255.255"
    ):
        return f"carrier-grade NAT address blocked: {address.address}"
    if parsed.is_reserved:
        return f"reserved address blocked: {address.address}"
    if parsed.is_private:
        if allow_private and any(
            parsed in network for network in _PRIVATE_SERVICE_NETWORKS
        ):
            return None
        return f"private address blocked: {address.address}"
    if require_private:
        return f"trusted service address is not private: {address.address}"
    if not parsed.is_global:
        return f"non-public address blocked: {address.address}"
    return None


def _validate_address_set(
    addresses: Iterable[Any] | Any,
    *,
    allow_private: bool = False,
    require_private: bool = False,
) -> AddressDecision:
    """Approve only a complete, finite, fully public address set.

    Any unsafe, malformed, unsupported, or conflicting answer rejects the
    entire set.  The safe set is never recovered by dropping one suspicious
    dual-stack answer.
    """

    if isinstance(addresses, (str, bytes, bytearray, ResolvedAddress, Mapping)):
        values: Iterable[Any] = (addresses,)
    else:
        try:
            values = iter(addresses)
        except TypeError:
            values = (addresses,)

    normalized: list[ResolvedAddress] = []
    seen: set[tuple[str, int, int]] = set()
    ports: set[int] = set()
    for index, value in enumerate(values):
        if index >= MAX_RESOLUTION_ADDRESSES:
            return AddressDecision(False, reason="address set exceeds policy bound")
        try:
            if isinstance(value, ResolvedAddress):
                address = value
            elif isinstance(value, Mapping):
                address = ResolvedAddress(
                    value.get("address", value.get("ip", value.get("host"))),
                    int(value.get("port", 0) or 0),
                    family=value.get("family"),
                )
            elif isinstance(value, (tuple, list)):
                parts = _record_parts(value, 0)
                if parts is None:
                    raise ValueError("invalid resolver record")
                address_value, address_port, address_family = parts
                address = ResolvedAddress(
                    address_value,
                    address_port,
                    family=address_family,
                )
            else:
                address = ResolvedAddress(value)
        except (TypeError, ValueError, OverflowError):
            return AddressDecision(False, reason=f"invalid address at index {index}")
        if address.port:
            ports.add(address.port)
        reason = _address_reason(
            address,
            allow_private=allow_private,
            require_private=require_private,
        )
        if reason is not None:
            return AddressDecision(False, reason=reason)
        key = (address.address, address.port, address.family)
        if key not in seen:
            seen.add(key)
            normalized.append(address)

    if not normalized:
        return AddressDecision(
            False,
            reason=(
                "no trusted service addresses resolved"
                if require_private
                else "no public addresses resolved"
            ),
        )
    if len(ports) > 1:
        return AddressDecision(False, reason="ambiguous address ports")
    return AddressDecision(True, tuple(normalized))


def validate_address_set(addresses: Iterable[Any] | Any) -> AddressDecision:
    """Approve only a complete, finite, fully public address set."""

    return _validate_address_set(addresses)


def validate_trusted_service_address_set(
    addresses: Iterable[Any] | Any,
) -> AddressDecision:
    """Approve only a complete RFC1918 or ULA address set for a service.

    This policy is separate from :func:`validate_address_set` so a private
    address can never be approved by accident on a user-controlled URL.
    Callers must still prove the exact configured service origin and trusted
    caller before using this result.
    """

    return _validate_address_set(
        addresses,
        allow_private=True,
        require_private=True,
    )


__all__ = [
    "AddressDecision",
    "DEFAULT_RESOLUTION_TIMEOUT_SECONDS",
    "DNSResolutionError",
    "MAX_RESOLUTION_ADDRESSES",
    "ResolvedAddress",
    "resolve_public_addresses",
    "resolve_trusted_service_addresses",
    "validate_address_set",
    "validate_trusted_service_address_set",
]
