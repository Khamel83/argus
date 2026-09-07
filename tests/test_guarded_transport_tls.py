"""Hermetic tests for direct transport origin preservation and pinning."""

from __future__ import annotations

import gzip
import socket

import pytest

from argus.acquisition.dns import ResolvedAddress
from argus.acquisition.transport import (
    AddressPolicyError,
    PinnedRequest,
    PinnedTransport,
    TransportDispatchError,
    TransportRequest,
    TransportPolicyError,
    UnsupportedAddressPinning,
)
from argus.acquisition.models import CredentialPolicy, LogicalOrigin, OriginProfile


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


class WireSocket:
    def __init__(self, wire: bytes):
        self.wire = wire
        self.offset = 0

    def recv(self, size: int) -> bytes:
        if self.offset >= len(self.wire):
            return b""
        chunk = self.wire[self.offset : self.offset + min(size, 7)]
        self.offset += len(chunk)
        return chunk


class SendingWireSocket:
    def __init__(self, response: bytes):
        self.response = response
        self.offset = 0
        self.sent = []
        self.connected_to = None
        self.timeout = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, address) -> None:
        self.connected_to = address

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv(self, size: int) -> bytes:
        if self.offset >= len(self.response):
            return b""
        chunk = self.response[self.offset : self.offset + min(size, 7)]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


def _pinned_request() -> PinnedRequest:
    origin = LogicalOrigin("https", "public.example", 443)
    return PinnedRequest(
        url=origin.origin + "/search",
        method="GET",
        headers=(),
        body=b"",
        timeout=10.0,
        logical_origin=origin,
        dial_address=ResolvedAddress("93.184.216.34", 443),
        tls_server_name=origin.hostname,
        host_header=origin.hostname,
        authority=origin.hostname,
    )


@pytest.mark.parametrize(
    ("body", "expected_length"),
    [(b"", 0), ("π".encode("utf-8"), 2)],
)
def test_socket_dispatcher_frames_post_body_with_exact_byte_length(
    monkeypatch, body, expected_length
):
    import argus.acquisition.transport as transport_module

    socket_instance = SendingWireSocket(
        b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"
    )
    monkeypatch.setattr(
        transport_module.socket,
        "socket",
        lambda *args, **kwargs: socket_instance,
    )
    origin = LogicalOrigin("http", "public.example", 80)
    prepared = PinnedRequest(
        url=origin.origin + "/submit",
        method="POST",
        headers=(("Content-Type", "application/json"),),
        body=body,
        timeout=10.0,
        logical_origin=origin,
        dial_address=ResolvedAddress("93.184.216.34", 80),
        tls_server_name=origin.hostname,
        host_header=origin.hostname,
        authority=origin.hostname,
    )

    response = transport_module._SocketDispatcher().send(prepared)

    assert response.status_code == 204
    assert socket_instance.connected_to == ("93.184.216.34", 80)
    assert socket_instance.closed is True
    wire_head, separator, wire_body = socket_instance.sent[0].partition(
        b"\r\n\r\n"
    )
    assert separator
    assert wire_head.startswith(b"POST /submit HTTP/1.1\r\n")
    content_lengths = [
        line
        for line in wire_head.split(b"\r\n")
        if line.lower().startswith(b"content-length:")
    ]
    assert content_lengths == [f"Content-Length: {expected_length}".encode()]
    assert wire_body == body


@pytest.mark.parametrize(
    "framing_header", [("Content-Length", "1"), ("Transfer-Encoding", "chunked")]
)
def test_transport_rejects_caller_request_framing_before_resolution(
    framing_header,
):
    resolver = SequenceResolver(["93.184.216.34"])
    recorder = Recorder()
    transport = PinnedTransport(resolver=resolver, dispatcher=recorder)

    with pytest.raises(ValueError, match="framing"):
        transport.request(
            request_for(
                "http://public.example/submit",
                method="POST",
                headers={framing_header[0]: framing_header[1]},
                body=b"x",
            )
        )

    assert resolver.calls == 0
    assert recorder.requests == []


def test_socket_dispatcher_rejects_direct_request_framing_override_before_connect(
    monkeypatch,
):
    import argus.acquisition.transport as transport_module

    socket_calls = []
    monkeypatch.setattr(
        transport_module.socket,
        "socket",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )
    origin = LogicalOrigin("http", "public.example", 80)
    prepared = PinnedRequest(
        url=origin.origin + "/submit",
        method="POST",
        headers=(("Transfer-Encoding", "chunked"),),
        body=b"x",
        timeout=10.0,
        logical_origin=origin,
        dial_address=ResolvedAddress("93.184.216.34", 80),
        tls_server_name=origin.hostname,
        host_header=origin.hostname,
        authority=origin.hostname,
    )

    with pytest.raises(ValueError, match="framing"):
        transport_module._SocketDispatcher().send(prepared)

    assert socket_calls == []


def test_socket_dispatcher_decodes_chunked_gzip_response():
    from argus.acquisition.transport import _SocketDispatcher

    payload = b"<html><body>Yahoo result</body></html>"
    compressed = gzip.compress(payload)
    wire_body = (
        f"{len(compressed):x};provider=yahoo\r\n".encode()
        + compressed
        + b"\r\n0\r\nX-Provider: yahoo\r\n\r\n"
    )
    wire = (
        b"HTTP/1.1 200 OK\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Content-Encoding: gzip\r\n"
        b"Connection: close\r\n\r\n" + wire_body
    )

    response = _SocketDispatcher._read_response(WireSocket(wire), _pinned_request())

    assert response.status_code == 200
    assert response.body == payload
    assert response.get_header("content-encoding") == "gzip"


def test_socket_dispatcher_rejects_malformed_chunked_response():
    from argus.acquisition.transport import _SocketDispatcher

    wire = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nnot-a-chunk\r\n"

    with pytest.raises(TransportDispatchError, match="chunked"):
        _SocketDispatcher._read_response(WireSocket(wire), _pinned_request())
