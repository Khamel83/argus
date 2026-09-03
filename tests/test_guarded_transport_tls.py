"""Hermetic tests for direct transport origin preservation and pinning."""

from __future__ import annotations

import socket

import pytest

from argus.acquisition.models import CredentialPolicy, OriginProfile
from argus.acquisition.transport import (
    AddressPolicyError,
    PinnedTransport,
    TransportRequest,
    TransportPolicyError,
    UnsupportedAddressPinning,
)


class SequenceResolver:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def getaddrinfo(self, hostname, port, *args, **kwargs):
        self.calls += 1
        answer = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port),
            )
            for address in answer
        ]


class Recorder:
    supports_address_pinning = True

    def __init__(self):
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return {"status_code": 200, "headers": {"content-type": "text/plain"}}


def request_for(url: str, **overrides) -> TransportRequest:
    values = {"method": "GET", "url": url, "headers": {}}
    values.update(overrides)
    return TransportRequest(**values)


def test_direct_transport_preserves_logical_origin_while_dialing_approved_ip():
    recorder = Recorder()
    resolver = SequenceResolver(["93.184.216.34"])
    transport = PinnedTransport(
        resolver=resolver,
        clock=lambda: 100.0,
        dispatcher=recorder,
    )

    response = transport.request(request_for("https://public.example:8443/a"))

    assert response.dial_ip == "93.184.216.34"
    assert response.tls_server_name == "public.example"
    assert response.host_header == "public.example:8443"
    assert response.http2_authority == "public.example:8443"
    assert recorder.requests[0].logical_origin.hostname == "public.example"
    assert recorder.requests[0].logical_origin.port == 8443
    assert recorder.requests[0].dial_address.address == "93.184.216.34"


def test_default_port_is_not_added_to_host_or_authority():
    recorder = Recorder()
    transport = PinnedTransport(
        resolver=SequenceResolver(["93.184.216.34"]),
        dispatcher=recorder,
    )

    response = transport.request(request_for("https://public.example/a"))

    assert response.host_header == "public.example"
    assert response.http2_authority == "public.example"


def test_trusted_searxng_service_can_dial_private_docker_address():
    recorder = Recorder()
    transport = PinnedTransport(
        resolver=SequenceResolver(["172.20.0.3"]),
        dispatcher=recorder,
    )

    response = transport.request(
        request_for(
            "http://searxng:8080/search",
            caller_principal="provider:searxng",
            profile=OriginProfile.AUTHENTICATED_CONTENT,
            credential_policy=CredentialPolicy.ORIGIN_SCOPED,
            trusted_service_origin="http://searxng:8080",
        )
    )

    assert response.dial_ip == "172.20.0.3"
    assert recorder.requests[0].logical_origin.hostname == "searxng"
    assert recorder.requests[0].dial_address.address == "172.20.0.3"


def test_private_target_without_exact_trusted_origin_is_rejected():
    recorder = Recorder()
    transport = PinnedTransport(
        resolver=SequenceResolver(["172.20.0.4"]),
        dispatcher=recorder,
    )

    with pytest.raises(TransportPolicyError, match="trusted service origin"):
        transport.request(
            request_for(
                "http://metadata.internal:8080/",
                caller_principal="provider:searxng",
                profile=OriginProfile.AUTHENTICATED_CONTENT,
                credential_policy=CredentialPolicy.ORIGIN_SCOPED,
                trusted_service_origin="http://searxng:8080",
            )
        )

    assert recorder.requests == []


def test_arbitrary_private_target_without_service_context_is_rejected():
    recorder = Recorder()
    transport = PinnedTransport(
        resolver=SequenceResolver(["172.20.0.4"]),
        dispatcher=recorder,
    )

    with pytest.raises(AddressPolicyError, match="private"):
        transport.request(request_for("http://metadata.internal:8080/"))

    assert recorder.requests == []


def test_private_service_target_rejects_impersonating_caller():
    recorder = Recorder()
    transport = PinnedTransport(
        resolver=SequenceResolver(["172.20.0.4"]),
        dispatcher=recorder,
    )

    with pytest.raises(TransportPolicyError, match="caller"):
        transport.request(
            request_for(
                "http://searxng:8080/search",
                caller_principal="provider:brave",
                profile=OriginProfile.AUTHENTICATED_CONTENT,
                credential_policy=CredentialPolicy.ORIGIN_SCOPED,
                trusted_service_origin="http://searxng:8080",
            )
        )

    assert recorder.requests == []


def test_trusted_service_cannot_use_loopback_dial_address():
    recorder = Recorder()
    transport = PinnedTransport(
        resolver=SequenceResolver(["127.0.0.1"]),
        dispatcher=recorder,
    )

    with pytest.raises(AddressPolicyError, match="trusted service addresses"):
        transport.request(
            request_for(
                "http://searxng:8080/search",
                caller_principal="provider:searxng",
                profile=OriginProfile.AUTHENTICATED_CONTENT,
                credential_policy=CredentialPolicy.ORIGIN_SCOPED,
                trusted_service_origin="http://searxng:8080",
            )
        )

    assert recorder.requests == []


def test_caller_cannot_override_host_authority_sni_or_dial_target():
    resolver = SequenceResolver(["93.184.216.34"])
    recorder = Recorder()
    transport = PinnedTransport(resolver=resolver, dispatcher=recorder)

    for headers in ({"Host": "evil.example"}, {":authority": "evil.example"}):
        with pytest.raises(ValueError, match="logical origin"):
            transport.request(request_for("https://public.example/a", headers=headers))

    with pytest.raises(ValueError, match="logical origin"):
        transport.request(
            request_for("https://public.example/a", tls_server_name="evil.example")
        )
    with pytest.raises(ValueError, match="dial"):
        transport.request(request_for("https://public.example/a", dial_ip="1.1.1.1"))

    assert recorder.requests == []


def test_unsafe_or_ambiguous_address_set_dispatches_zero_requests():
    recorder = Recorder()
    transport = PinnedTransport(
        resolver=SequenceResolver(["93.184.216.34"], ["127.0.0.1"]),
        dispatcher=recorder,
    )

    transport.request(request_for("https://public.example/a"))
    with pytest.raises(Exception):
        transport.request(request_for("https://public.example/a"))

    assert len(recorder.requests) == 1


def test_pool_is_rechecked_before_a_reused_connection_is_dispatched():
    recorder = Recorder()
    resolver = SequenceResolver(["93.184.216.34"], ["127.0.0.1"])
    transport = PinnedTransport(resolver=resolver, dispatcher=recorder)

    transport.request(request_for("https://public.example/a"))
    with pytest.raises(Exception):
        transport.request(request_for("https://public.example/a"))

    assert len(recorder.requests) == 1


def test_reresolution_can_switch_to_a_different_safe_address():
    recorder = Recorder()
    transport = PinnedTransport(
        resolver=SequenceResolver(["93.184.216.34"], ["142.250.72.14"]),
        dispatcher=recorder,
    )

    transport.request(request_for("https://public.example/a"))
    response = transport.request(request_for("https://public.example/a"))

    assert response.dial_ip == "142.250.72.14"
    assert [request.dial_ip for request in recorder.requests] == [
        "93.184.216.34",
        "142.250.72.14",
    ]


def test_unsupported_address_pinning_fails_before_dispatch():
    class UnsupportedRecorder:
        supports_address_pinning = False

        def send(self, request):
            raise AssertionError("must not dispatch")

    recorder = UnsupportedRecorder()
    transport = PinnedTransport(
        resolver=SequenceResolver(["93.184.216.34"]),
        dispatcher=recorder,
    )

    with pytest.raises(UnsupportedAddressPinning):
        transport.request(request_for("https://public.example/a"))
