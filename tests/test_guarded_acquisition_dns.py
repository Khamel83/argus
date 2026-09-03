"""Hermetic tests for the guarded DNS address policy."""

from __future__ import annotations

import socket

import pytest

from argus.acquisition.dns import (
    AddressDecision,
    ResolvedAddress,
    resolve_public_addresses,
    validate_address_set,
)


class MappingResolver:
    def __init__(self, answers=None, *, error: Exception | None = None):
        self.answers = answers or {}
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def getaddrinfo(self, hostname, port, *args, **kwargs):
        self.calls.append((hostname, port))
        if self.error is not None:
            raise self.error
        return self.answers.get(hostname, ())


def _record(address: str, port: int = 443):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))


def _decision(addresses):
    return validate_address_set(tuple(addresses))


@pytest.mark.parametrize(
    ("address", "family"),
    [
        ("93.184.216.34", socket.AF_INET),
        ("2001:4860:4860::8888", socket.AF_INET6),
    ],
)
def test_public_ipv4_and_ipv6_addresses_are_approved(address, family):
    resolver = MappingResolver({"public.example": [_record(address)]})

    resolved = resolve_public_addresses(
        "public.example", 443, resolver, lambda: 100.0
    )

    assert resolved == (ResolvedAddress(address, 443, family=family),)
    decision = _decision(resolved)
    assert isinstance(decision, AddressDecision)
    assert decision.allowed is True
    assert decision.approved_addresses == resolved


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",  # private IPv4
        "127.0.0.1",  # loopback
        "169.254.169.254",  # link-local
        "240.0.0.1",  # reserved
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "100.64.0.1",  # carrier-grade NAT
        "fc00::1",  # IPv6 unique local/private
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "2001:db8::1",  # IPv6 documentation/reserved
        "::",  # IPv6 unspecified
        "ff02::1",  # IPv6 multicast
    ],
)
def test_non_public_address_classes_are_rejected(address):
    decision = _decision((ResolvedAddress(address, 443),))

    assert decision.allowed is False
    assert decision.reason


def test_dns_error_and_no_answer_are_empty_and_fail_closed():
    failing = MappingResolver(error=socket.gaierror("temporary DNS failure"))
    empty = MappingResolver({"missing.example": []})

    assert resolve_public_addresses("broken.example", 443, failing, lambda: 1.0) == ()
    assert resolve_public_addresses("missing.example", 443, empty, lambda: 1.0) == ()
    assert validate_address_set(()) .allowed is False


def test_mixed_safe_and_unsafe_dual_stack_answers_fail_closed():
    addresses = (
        ResolvedAddress("93.184.216.34", 443),
        ResolvedAddress("2001:db8::1", 443),
    )

    decision = _decision(addresses)

    assert decision.allowed is False
    assert "private" in decision.reason


def test_reresolution_accepts_a_different_fully_safe_public_address():
    first = (ResolvedAddress("93.184.216.34", 443),)
    second = (ResolvedAddress("142.250.72.14", 443),)

    assert _decision(first).allowed is True
    refreshed = _decision(second)
    assert refreshed.allowed is True
    assert refreshed.approved_addresses == second


@pytest.mark.parametrize(
    "addresses",
    [
        (ResolvedAddress("93.184.216.34", 443), "not-an-ip"),
        (ResolvedAddress("93.184.216.34", 443), ResolvedAddress("93.184.216.34", 80)),
    ],
)
def test_ambiguous_address_sets_fail_closed(addresses):
    decision = _decision(addresses)

    assert decision.allowed is False
    assert decision.reason
