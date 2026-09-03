"""Integration proof that extractors use one guarded acquisition seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from argus.acquisition import AcquisitionFailure, AcquisitionResult
from argus.acquisition.transport import TransportResponse
from argus.acquisition.guarded import (
    GuardedAcquisition,
    GuardedAcquisitionError,
    get_guarded_acquisition,
    guarded_http_request,
    make_request,
    set_guarded_acquisition,
)
from argus.acquisition.models import OperationClass, OriginProfile


@dataclass
class SpyGuardedAcquisition:
    """Small no-network transport used to prove call-site routing."""

    responses: dict[str, bytes] = field(default_factory=dict)
    requests: list[object] = field(default_factory=list)

    async def acquire(self, request):
        self.requests.append(request)
        return AcquisitionResult(
            approved_logical_origin=request.normalized_url,
            content=self.responses.get(request.normalized_url, b""),
        )

    async def open_browser_session(self, request):
        self.requests.append(request)
        return AcquisitionFailure.browser_policy_unavailable(
            request_id=request.request_id or "spy-browser"
        )


@pytest.fixture
def guarded_spy(monkeypatch):
    # The integration test uses reserved fixture hostnames.  URL safety is
    # exercised separately by the guarded DNS/transport contract tests.
    monkeypatch.setattr(
        "argus.acquisition.guarded._validate_public_target",
        lambda _url: (True, ""),
    )
    previous = get_guarded_acquisition()
    spy = SpyGuardedAcquisition()
    set_guarded_acquisition(spy)
    try:
        yield spy
    finally:
        set_guarded_acquisition(previous)


@pytest.mark.asyncio
async def test_trafilatura_uses_guarded_acquisition(monkeypatch, guarded_spy):
    from argus.extraction import extractor

    url = "https://example.test/article"
    guarded_spy.responses[url] = b"<article>fixture body</article>"

    class FakeTrafilatura:
        @staticmethod
        def bare_extraction(_body):
            return {
                "text": "fixture body with enough words",
                "title": "Fixture",
            }

    monkeypatch.setitem(__import__("sys").modules, "trafilatura", FakeTrafilatura)

    result = await extractor._extract_trafilatura(url)

    assert result.text == "fixture body with enough words"
    assert len(guarded_spy.requests) == 1
    assert guarded_spy.requests[0].normalized_url == url
    assert guarded_spy.requests[0].operation_class == "direct_http"


@pytest.mark.asyncio
async def test_jina_uses_guarded_acquisition(monkeypatch, guarded_spy):
    from argus.extraction import extractor
    monkeypatch.setattr(
        "argus.acquisition.guarded._validate_public_target",
        lambda _url: (True, ""),
    )

    url = "https://example.test/article"
    reader_url = f"{extractor.JINA_READER_URL}{url}"
    guarded_spy.responses[reader_url] = b"# Fixture\n\nA sufficiently long fixture response with additional words."

    result = await extractor._extract_jina(url)

    assert result.error is None, result.error
    assert result.extractor.value == "jina"
    assert result.title == "Fixture"
    assert len(guarded_spy.requests) == 1
    assert guarded_spy.requests[0].normalized_url == reader_url
    assert guarded_spy.requests[0].profile.value == "third_party_fetch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer caller-secret"},
        {"Cookie": "session=caller-secret"},
        {"X-API-Key": "provider-secret"},
        {"X-Argus-Caller": "internal-identity"},
    ],
)
async def test_third_party_credentials_are_rejected_before_dispatch(
    monkeypatch, guarded_spy, headers
):
    monkeypatch.setattr(
        "argus.acquisition.guarded._validate_public_target",
        lambda _url: (True, ""),
    )

    with pytest.raises(GuardedAcquisitionError) as raised:
        await guarded_http_request(
            "https://provider.example/fetch",
            headers=headers,
            profile=OriginProfile.THIRD_PARTY_FETCH,
            operation_class=OperationClass.THIRD_PARTY,
            target_url="https://source.example/article",
        )

    assert raised.value.failure.code.value == "acquisition_blocked"
    assert guarded_spy.requests == []


@pytest.mark.asyncio
async def test_browser_opener_is_not_called_before_attestation(monkeypatch):
    from argus.acquisition.browser_policy import (
        get_browser_attestation_provider,
        set_browser_attestation_provider,
    )

    monkeypatch.setattr(
        "argus.acquisition.guarded._validate_public_target",
        lambda _url: (True, ""),
    )
    previous_provider = get_browser_attestation_provider()
    set_browser_attestation_provider(None)
    opener = AsyncMock()
    acquisition = GuardedAcquisition(browser_session_opener=opener)

    try:
        result = await acquisition.open_browser_session(
            make_request(
                "https://example.test/article",
                operation_class=OperationClass.BROWSER,
                profile=OriginProfile.PUBLIC_CONTENT,
            )
        )
    finally:
        set_browser_attestation_provider(previous_provider)

    assert getattr(result, "code", None) == "browser_policy_unavailable"
    opener.assert_not_awaited()


@dataclass
class SequenceTransport:
    responses: list[TransportResponse]
    requests: list[object] = field(default_factory=list)

    def request(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def _transport_response(status, *, url, headers=None, body=b""):
    return TransportResponse(
        status_code=status,
        headers=headers,
        body=body,
        url=url,
        dial_ip="93.184.216.34",
        tls_server_name="source.example",
        host_header="source.example",
        http2_authority="source.example",
    )


@pytest.mark.asyncio
async def test_public_cross_origin_redirect_strips_credentials(monkeypatch):
    from argus.acquisition.guarded import get_guarded_acquisition

    monkeypatch.setattr(
        "argus.acquisition.guarded._validate_public_target",
        lambda _url: (True, ""),
    )
    source = "https://source.example/article"
    destination = "https://redirect.example/article"
    transport = SequenceTransport(
        [
            _transport_response(302, url=source, headers={"Location": destination}),
            _transport_response(200, url=destination, body=b"redirected content"),
        ]
    )
    previous = get_guarded_acquisition()
    set_guarded_acquisition(GuardedAcquisition(transport=transport))
    try:
        result = await guarded_http_request(
            source,
            headers={
                "Authorization": "Bearer origin-secret",
                "Cookie": "session=origin-secret",
                "X-Request-Id": "request-correlation",
            },
            profile=OriginProfile.PUBLIC_CONTENT,
        )
    finally:
        set_guarded_acquisition(previous)

    assert result.text == "redirected content"
    assert len(transport.requests) == 2
    assert "authorization" not in transport.requests[1].header_map
    assert "cookie" not in transport.requests[1].header_map
    assert transport.requests[1].header_map["X-Request-Id"] == "request-correlation"


@pytest.mark.asyncio
async def test_unsafe_target_fails_before_transport_dispatch(monkeypatch):
    from argus.acquisition.guarded import get_guarded_acquisition

    monkeypatch.setattr(
        "argus.acquisition.guarded._validate_public_target",
        lambda _url: (False, "loopback address blocked"),
    )
    transport = SequenceTransport([])
    previous = get_guarded_acquisition()
    set_guarded_acquisition(GuardedAcquisition(transport=transport))
    try:
        with pytest.raises(GuardedAcquisitionError) as raised:
            await guarded_http_request("http://127.0.0.1/private")
    finally:
        set_guarded_acquisition(previous)

    assert raised.value.failure.code.value == "acquisition_blocked"
    assert transport.requests == []
