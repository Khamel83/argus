"""Compatibility coverage for the bounded raw browser fetch endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def allow_browser_policy(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from argus.acquisition.browser_policy import (
        BrowserNetworkAttestation,
        current_release_identity,
        set_browser_attestation_provider,
    )

    attestation = BrowserNetworkAttestation(
        policy_identity="test-policy",
        resolver_identity="test-resolver",
        connection_binding="test-binding",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        release_identity=current_release_identity(),
    )

    class Provider:
        def current(self):
            return attestation

    set_browser_attestation_provider(Provider())
    yield
    set_browser_attestation_provider(None)


def _remote_client(monkeypatch):
    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    monkeypatch.setenv("ARGUS_API_KEY", "caller-secret")
    monkeypatch.setenv("ARGUS_ALLOWED_HOSTS", "testserver")
    return TestClient(create_app(), client=("203.0.113.10", 50000))


def _request_payload(**overrides):
    payload = {
        "url": "https://events.example.test/event/123",
        "render": "browser",
        "cache": False,
        "extractors": ["raw_html"],
        "impersonate": "chrome",
        "egress": "residential",
        "timeout_seconds": 5,
        "headers": {},
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer caller-secret"},
        {"X-API-Key": "caller-secret"},
    ],
)
def test_fetch_raw_accepts_existing_caller_auth(monkeypatch, headers):
    from argus.api import routes_fetch_raw
    from argus.api.schemas import FetchRawResponse

    monkeypatch.setattr(
        routes_fetch_raw,
        "fetch_raw",
        AsyncMock(
            return_value=FetchRawResponse(
                status="ok",
                http_status=200,
                body="<html>ready</html>",
                final_url="https://events.example.test/event/123",
                body_sha256="a" * 64,
                render_mode_used="browser",
                extractor_used="raw_html",
                egress_used="residential",
                elapsed_ms=1,
            )
        ),
    )
    client = _remote_client(monkeypatch)

    response = client.post("/api/fetch-raw", json=_request_payload(), headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "http_status": 200,
        "body": "<html>ready</html>",
        "final_url": "https://events.example.test/event/123",
        "body_sha256": "a" * 64,
        "render_mode_used": "browser",
        "extractor_used": "raw_html",
        "egress_used": "residential",
        "elapsed_ms": 1,
        "from_cache": False,
        "error": None,
    }


def test_fetch_raw_rejects_unauthenticated_remote_caller(monkeypatch):
    client = _remote_client(monkeypatch)

    response = client.post("/api/fetch-raw", json=_request_payload())

    assert response.status_code == 401
    assert response.json()["error"] == "Authentication required"


@pytest.mark.parametrize(
    "blocked_header",
    [
        "Authorization",
        "X-Goog-Api-Key",
        "X-Aws-Credential",
        "X-Forwarded-For",
        "X-Custom-Route",
        "Cookie",
    ],
)
def test_fetch_raw_rejects_non_allowlisted_headers(monkeypatch, blocked_header):
    client = _remote_client(monkeypatch)

    response = client.post(
        "/api/fetch-raw",
        json=_request_payload(headers={blocked_header: "secret-or-routing-value"}),
        headers={"X-API-Key": "caller-secret"},
    )

    assert response.status_code == 422
    assert "headers" in str(response.json()).lower()


@pytest.mark.asyncio
async def test_raw_fetch_prefers_same_site_inventory_json(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    page_response = SimpleNamespace(
        url="https://events.example.test/event/123",
        status=200,
        headers={"content-type": "text/html"},
    )
    inventory_response = SimpleNamespace(
        url="https://api.example.test/inventory/123",
        status=200,
        headers={"content-type": "application/json"},
        body=AsyncMock(return_value=b'{"listings":[{"id":"listing-1"}]}'),
    )
    unrelated_response = SimpleNamespace(
        url="https://analytics.example.net/collect",
        status=200,
        headers={"content-type": "application/json"},
        body=AsyncMock(return_value=b'{"listings":[{"id":"wrong-site"}]}'),
    )
    page = MagicMock()
    page.url = "https://events.example.test/event/123"
    page.content = AsyncMock(return_value="<html>fallback</html>")
    page.close = AsyncMock()
    responses = []
    page.on.side_effect = lambda event, callback: responses.append(callback)

    async def goto(*_args, **_kwargs):
        return page_response

    async def capture_window(_delay):
        for callback in responses:
            callback(inventory_response)
            callback(unrelated_response)

    page.goto = AsyncMock(side_effect=goto)
    page.wait_for_load_state = AsyncMock()
    context = MagicMock()
    context.route = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))
    monkeypatch.setattr("argus.raw_fetch.asyncio.sleep", capture_window)

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )
    assert result.status == "ok"
    assert result.body == '{"listings":[{"id":"listing-1"}]}'
    assert result.extractor_used == "same_site_json"
    assert result.final_url == "https://events.example.test/event/123"
    page.wait_for_load_state.assert_not_awaited()
    assert hasattr(responses[0], "__dict__")


@pytest.mark.asyncio
async def test_raw_fetch_falls_back_to_nonempty_rendered_html(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    response = SimpleNamespace(
        url="https://events.example.test/event/123",
        status=200,
        headers={"content-type": "text/html"},
    )
    page = MagicMock()
    page.url = "https://events.example.test/event/123"
    page.goto = AsyncMock(return_value=response)
    page.wait_for_load_state = AsyncMock()
    page.content = AsyncMock(return_value="<html><body>listing page</body></html>")
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
    context.route = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))
    monkeypatch.setattr("argus.raw_fetch.asyncio.sleep", AsyncMock())

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )

    assert result.status == "ok"
    assert result.body == "<html><body>listing page</body></html>"
    assert result.extractor_used == "raw_html"
    assert result.body_sha256


@pytest.mark.asyncio
async def test_raw_fetch_reports_upstream_status_and_never_accepts_empty_body(
    monkeypatch,
):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    response = SimpleNamespace(
        url="https://events.example.test/event/123",
        status=503,
        headers={"content-type": "text/html"},
    )
    page = MagicMock()
    page.url = "https://events.example.test/event/123"
    page.goto = AsyncMock(return_value=response)
    page.wait_for_load_state = AsyncMock()
    page.content = AsyncMock(return_value="")
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
    context.route = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))
    monkeypatch.setattr("argus.raw_fetch.asyncio.sleep", AsyncMock())

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )

    assert result.status == "error"
    assert result.http_status == 503
    assert result.body == ""
    assert result.error == "upstream_status:503"


@pytest.mark.asyncio
async def test_raw_fetch_rejects_empty_success_body(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    response = SimpleNamespace(
        url="https://events.example.test/event/123",
        status=200,
        headers={"content-type": "text/html"},
    )
    page = MagicMock()
    page.url = "https://events.example.test/event/123"
    page.goto = AsyncMock(return_value=response)
    page.wait_for_load_state = AsyncMock()
    page.content = AsyncMock(return_value=" \n ")
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
    context.route = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))
    monkeypatch.setattr("argus.raw_fetch.asyncio.sleep", AsyncMock())

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )

    assert result.status == "error"
    assert result.http_status == 502
    assert result.error == "empty_body"


@pytest.mark.asyncio
async def test_raw_fetch_installs_guard_before_navigation_and_blocks_unsafe_redirect(
    monkeypatch,
):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    page = MagicMock()
    page.url = "https://events.example.test/event/123"
    page.close = AsyncMock()
    page.on = MagicMock()
    handlers = []
    context = MagicMock()
    context.route = AsyncMock(
        side_effect=lambda _pattern, handler: handlers.append(handler)
    )
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    async def goto(*_args, **_kwargs):
        assert handlers, "request guard must be installed before navigation"
        redirect = SimpleNamespace(
            request=SimpleNamespace(
                url="http://127.0.0.1:8080/admin",
                resource_type="document",
            ),
            abort=AsyncMock(),
            continue_=AsyncMock(),
        )
        await handlers[0](redirect)
        redirect.abort.assert_awaited_once()
        raise RuntimeError("net::ERR_FAILED")

    page.goto = AsyncMock(side_effect=goto)
    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr(
        "argus.raw_fetch.is_safe_url",
        lambda url: (False, "loopback address") if "127.0.0.1" in url else (True, ""),
    )

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )

    assert result.status == "error"
    assert result.error == "unsafe_redirect:loopback address"


@pytest.mark.asyncio
async def test_raw_fetch_blocks_unsafe_subresource_before_it_is_requested(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    response = SimpleNamespace(
        url="https://events.example.test/event/123",
        status=200,
        headers={"content-type": "text/html"},
    )
    page = MagicMock()
    page.url = "https://events.example.test/event/123"
    page.goto = AsyncMock(return_value=response)
    page.content = AsyncMock(return_value="<html>must not succeed</html>")
    page.close = AsyncMock()
    page.on = MagicMock()
    handlers = []
    context = MagicMock()
    context.route = AsyncMock(
        side_effect=lambda _pattern, handler: handlers.append(handler)
    )
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    async def capture_window(*_args, **_kwargs):
        subresource = SimpleNamespace(
            request=SimpleNamespace(
                url="http://169.254.169.254/latest/meta-data",
                resource_type="xhr",
            ),
            abort=AsyncMock(),
            continue_=AsyncMock(),
        )
        await handlers[0](subresource)
        subresource.abort.assert_awaited_once()

    page.wait_for_load_state = AsyncMock()
    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr(
        "argus.raw_fetch.is_safe_url",
        lambda url: (
            (False, "link-local address") if "169.254.169.254" in url else (True, "")
        ),
    )
    monkeypatch.setattr("argus.raw_fetch.asyncio.sleep", capture_window)

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )

    assert result.status == "error"
    assert result.error == "unsafe_subresource:link-local address"
    page.content.assert_not_awaited()


@pytest.mark.asyncio
async def test_raw_fetch_guard_allows_non_network_browser_resources(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    document = SimpleNamespace(
        url="https://events.example.test/event/123",
        status=200,
        headers={"content-type": "text/html"},
    )
    page = MagicMock()
    page.url = document.url
    page.content = AsyncMock(return_value="<html>safe resources</html>")
    page.wait_for_load_state = AsyncMock()
    page.close = AsyncMock()
    page.on = MagicMock()
    handlers = []
    context = MagicMock()
    context.route = AsyncMock(
        side_effect=lambda _pattern, handler: handlers.append(handler)
    )
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    async def goto(*_args, **_kwargs):
        for url in (
            "data:image/svg+xml;base64,PHN2Zy8+",
            "blob:https://events.example.test/id",
            "about:blank",
        ):
            resource = SimpleNamespace(
                request=SimpleNamespace(url=url, resource_type="image"),
                abort=AsyncMock(),
                continue_=AsyncMock(),
            )
            await handlers[0](resource)
            resource.continue_.assert_awaited_once()
            resource.abort.assert_not_awaited()
        return document

    page.goto = AsyncMock(side_effect=goto)
    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr(
        "argus.raw_fetch.is_safe_url",
        lambda url: (
            (True, "") if url.startswith("https://") else (False, "invalid scheme")
        ),
    )

    result = await fetch_raw(FetchRawRequest(url=document.url))

    assert result.status == "ok"


@pytest.mark.asyncio
async def test_raw_fetch_never_succeeds_after_late_unsafe_subresource(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    document = SimpleNamespace(
        url="https://events.example.test/event/123",
        status=200,
        headers={"content-type": "text/html"},
    )
    page = MagicMock()
    page.url = document.url
    page.goto = AsyncMock(return_value=document)
    page.wait_for_load_state = AsyncMock()
    page.close = AsyncMock()
    page.on = MagicMock()
    handlers = []
    context = MagicMock()
    context.route = AsyncMock(
        side_effect=lambda _pattern, handler: handlers.append(handler)
    )
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    async def content():
        late_request = SimpleNamespace(
            request=SimpleNamespace(
                url="http://127.0.0.1/private",
                resource_type="fetch",
            ),
            abort=AsyncMock(),
            continue_=AsyncMock(),
        )
        await handlers[0](late_request)
        return "<html>must not be successful</html>"

    page.content = AsyncMock(side_effect=content)
    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr(
        "argus.raw_fetch.is_safe_url",
        lambda url: (False, "loopback address") if "127.0.0.1" in url else (True, ""),
    )

    result = await fetch_raw(FetchRawRequest(url=document.url))

    assert result.status == "error"
    assert result.error == "unsafe_subresource:loopback address"


@pytest.mark.asyncio
async def test_raw_fetch_blocks_service_workers_and_websockets(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    document = SimpleNamespace(
        url="https://events.example.test/event/123",
        status=200,
        headers={"content-type": "text/html"},
    )
    page = MagicMock()
    page.url = document.url
    page.goto = AsyncMock(return_value=document)
    page.content = AsyncMock(return_value="<html>safe</html>")
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
    context.route = AsyncMock()
    websocket_handlers = []
    context.route_web_socket = AsyncMock(
        side_effect=lambda _pattern, handler: websocket_handlers.append(handler)
    )
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))
    monkeypatch.setattr("argus.raw_fetch.asyncio.sleep", AsyncMock())

    result = await fetch_raw(FetchRawRequest(url=document.url))

    assert result.status == "ok"
    assert browser.new_context.await_args.kwargs["service_workers"] == "block"
    context.route_web_socket.assert_awaited_once()
    assert websocket_handlers


@pytest.mark.asyncio
async def test_raw_fetch_fails_closed_without_websocket_routing(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    context = MagicMock()
    context.route = AsyncMock()
    context.route_web_socket = None
    context.new_page = AsyncMock()
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )

    assert result.status == "error"
    assert result.http_status == 503
    assert result.error == "browser_capability_missing:route_web_socket"
    context.new_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_raw_fetch_rejects_private_second_dns_answer(monkeypatch):
    import socket

    from argus.raw_fetch import FetchRawRequest, fetch_raw

    get_browser = AsyncMock()
    monkeypatch.setattr("argus.raw_fetch._get_browser", get_browser)
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))
    monkeypatch.setattr(
        "argus.raw_fetch.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )

    result = await fetch_raw(FetchRawRequest(url="https://seatgeek.com/event/123"))

    assert result.status == "error"
    assert result.http_status == 400
    assert result.error == "unsafe_url:non-public IP blocked:127.0.0.1"
    get_browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_raw_fetch_overall_timeout_includes_browser_acquisition(monkeypatch):
    import asyncio

    from argus.raw_fetch import FetchRawRequest, fetch_raw

    async def slow_browser():
        await asyncio.sleep(1)

    request = FetchRawRequest.model_construct(
        url="https://events.example.test/event/123",
        render="browser",
        cache=False,
        extractors=["raw_html"],
        impersonate="chrome",
        egress="unknown",
        timeout_seconds=0.01,
        headers={},
    )
    monkeypatch.setattr("argus.raw_fetch._get_browser", slow_browser)
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))

    result = await fetch_raw(request)

    assert result.status == "error"
    assert result.http_status == 504
    assert result.error == "browser_timeout"


@pytest.mark.asyncio
async def test_raw_fetch_preserves_explicit_navigation_timeout(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    page = MagicMock()
    page.goto = AsyncMock(side_effect=TimeoutError("navigation exceeded timeout"))
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
    context.route = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )

    assert result.status == "error"
    assert result.http_status == 504
    assert result.error == "browser_timeout"


@pytest.mark.asyncio
async def test_raw_fetch_uses_realistic_chrome_context_and_safe_headers(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    response = SimpleNamespace(
        url="https://www.seatgeek.com/event/123",
        status=200,
        headers={"content-type": "text/html"},
    )
    page = MagicMock()
    page.url = response.url
    page.goto = AsyncMock(return_value=response)
    page.wait_for_load_state = AsyncMock()
    page.content = AsyncMock(return_value="<html>SeatGeek</html>")
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
    context.route = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    monkeypatch.setenv("ARGUS_EGRESS_TYPE", "residential")
    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))

    result = await fetch_raw(
        FetchRawRequest(
            url=response.url,
            egress="residential",
            headers={
                "Accept": "text/html",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
    )

    assert result.status == "ok"
    context_options = browser.new_context.await_args.kwargs
    assert "Chrome/" in context_options["user_agent"]
    assert context_options["viewport"]["width"] >= 1280
    assert context_options["extra_http_headers"] == {
        "Accept": "text/html",
        "Accept-Language": "en-US,en;q=0.9",
    }
    assert result.egress_used == "residential"


@pytest.mark.asyncio
async def test_raw_fetch_rejects_unsatisfied_requested_egress(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    get_browser = AsyncMock()
    monkeypatch.setenv("ARGUS_EGRESS_TYPE", "datacenter")
    monkeypatch.setattr("argus.raw_fetch._get_browser", get_browser)
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))

    result = await fetch_raw(
        FetchRawRequest(
            url="https://seatgeek.com/event/123",
            egress="residential",
        )
    )

    assert result.status == "error"
    assert result.http_status == 503
    assert result.error == "egress_unavailable:requested=residential:actual=datacenter"
    get_browser.assert_not_awaited()


def test_fetch_raw_preserves_upstream_403_and_exact_error_shape(monkeypatch):
    from argus.api import routes_fetch_raw
    from argus.api.schemas import FetchRawResponse

    monkeypatch.setattr(
        routes_fetch_raw,
        "fetch_raw",
        AsyncMock(
            return_value=FetchRawResponse(
                status="error",
                http_status=403,
                final_url="https://seatgeek.com/event/123",
                egress_used="residential",
                elapsed_ms=8,
                error="upstream_status:403",
            )
        ),
    )
    client = _remote_client(monkeypatch)

    response = client.post(
        "/api/fetch-raw",
        json=_request_payload(url="https://seatgeek.com/event/123"),
        headers={"X-API-Key": "caller-secret"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "status": "error",
        "http_status": 403,
        "body": "",
        "final_url": "https://seatgeek.com/event/123",
        "body_sha256": "",
        "render_mode_used": "browser",
        "extractor_used": "",
        "egress_used": "residential",
        "elapsed_ms": 8,
        "from_cache": False,
        "error": "upstream_status:403",
    }
