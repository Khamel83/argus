"""Compatibility adapter for the guarded public-address acquisition policy."""

from __future__ import annotations

from argus.acquisition.guarded import guarded_url_policy


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
    safe, reason = guarded_url_policy(url)
    if safe:
        return True, ""
    if reason.startswith("internal hostname blocked:"):
        return False, f"Internal hostname blocked: {reason.partition(':')[2].strip()}"
    if reason == "DNS resolution failed or returned no answers":
        return False, reason
    return False, _legacy_reason(reason)
