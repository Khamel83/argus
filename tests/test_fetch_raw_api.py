"""Compatibility coverage for the bounded raw browser fetch endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


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
                sha256="a" * 64,
                render="browser",
                extractor="raw_html",
                egress="residential",
                elapsed_ms=1,
            )
        ),
    )
    client = _remote_client(monkeypatch)

    response = client.post("/api/fetch-raw", json=_request_payload(), headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_fetch_raw_rejects_unauthenticated_remote_caller(monkeypatch):
    client = _remote_client(monkeypatch)

    response = client.post("/api/fetch-raw", json=_request_payload())

    assert response.status_code == 401
    assert response.json()["error"] == "Authentication required"


def test_fetch_raw_rejects_routing_and_credential_headers(monkeypatch):
    client = _remote_client(monkeypatch)

    response = client.post(
        "/api/fetch-raw",
        json=_request_payload(headers={"Authorization": "Bearer upstream-secret"}),
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
        for callback in responses:
            callback(inventory_response)
            callback(unrelated_response)
        return page_response

    page.goto = AsyncMock(side_effect=goto)
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )
    assert result.status == "ok"
    assert result.body == '{"listings":[{"id":"listing-1"}]}'
    assert result.extractor == "same_site_json"
    assert result.final_url == "https://events.example.test/event/123"


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
    page.content = AsyncMock(return_value="<html><body>listing page</body></html>")
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    monkeypatch.setattr("argus.raw_fetch._get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr("argus.raw_fetch.is_safe_url", lambda url: (True, ""))

    result = await fetch_raw(
        FetchRawRequest(url="https://events.example.test/event/123")
    )

    assert result.status == "ok"
    assert result.body == "<html><body>listing page</body></html>"
    assert result.extractor == "raw_html"
    assert result.sha256


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
    page.content = AsyncMock(return_value="")
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
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
    page.content = AsyncMock(return_value=" \n ")
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
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
    assert result.http_status == 502
    assert result.error == "empty_body"


@pytest.mark.asyncio
async def test_raw_fetch_rejects_unsafe_redirect(monkeypatch):
    from argus.raw_fetch import FetchRawRequest, fetch_raw

    response = SimpleNamespace(
        url="http://127.0.0.1:8080/admin",
        status=200,
        headers={"content-type": "text/html"},
    )
    page = MagicMock()
    page.url = "http://127.0.0.1:8080/admin"
    page.goto = AsyncMock(return_value=response)
    page.close = AsyncMock()
    page.on = MagicMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

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
