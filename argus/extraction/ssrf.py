"""Compatibility adapter for the guarded public-address acquisition policy."""

from __future__ import annotations

import socket
from urllib.parse import urlparse

from argus.acquisition.dns import resolve_public_addresses, validate_address_set


_INTERNAL_HOSTNAMES = {
    "localhost",
    "internal",
    "intranet",
    "local",
}
_INTERNAL_SUFFIXES = (".local", ".internal", ".corp", ".lan")


def _internal_hostname(hostname: str) -> bool:
    normalized = hostname.lower().rstrip(".")
    return normalized in _INTERNAL_HOSTNAMES or normalized.endswith(_INTERNAL_SUFFIXES)


def _legacy_reason(reason: str) -> str:
    """Keep historical reason wording while using the new fail-closed policy."""

    prefix, _, address = reason.partition(": ")
    if prefix == "private address blocked":
        return f"Private IP blocked: {address}"
    if prefix == "loopback address blocked":
        return f"Loopback IP blocked: {address}"
    if prefix == "link-local address blocked":
        return f"Link-local IP blocked: {address}"
    if prefix == "reserved address blocked":
        return f"Reserved IP blocked: {address}"
    if prefix == "multicast address blocked":
        return f"Multicast IP blocked: {address}"
    if prefix == "unspecified address blocked":
        return f"Unspecified IP blocked: {address}"
    if prefix == "carrier-grade NAT address blocked":
        return f"Carrier-grade NAT IP blocked: {address}"
    return reason


def is_safe_url(url: str) -> tuple[bool, str]:
    """Return whether ``url`` has a fully public, bounded DNS answer set.

    This function remains a tuple-returning compatibility surface for legacy
    extractors.  It delegates address resolution and classification to the
    guarded acquisition policy and therefore fails closed on DNS errors and
    mixed safe/unsafe dual-stack answers.
    """

    if not isinstance(url, str):
        return False, "URL validation error: URL must be text"
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            return False, f"Invalid scheme: {parsed.scheme}"
        hostname = parsed.hostname
        if not hostname:
            return False, "No hostname in URL"
        if parsed.username is not None or parsed.password is not None:
            return False, "Credentials in URL blocked"
        if _internal_hostname(hostname):
            return False, f"Internal hostname blocked: {hostname}"
        port = parsed.port or (443 if scheme == "https" else 80)
        addresses = resolve_public_addresses(hostname, port, resolver=socket)
        decision = validate_address_set(addresses)
        if not decision.allowed:
            if not addresses:
                return False, "DNS resolution failed or returned no answers"
            return False, _legacy_reason(decision.reason)
        return True, ""
    except (TypeError, ValueError) as exc:
        return False, f"URL validation error: {exc}"
