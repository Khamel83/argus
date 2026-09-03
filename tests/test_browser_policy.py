"""Hermetic browser connection-policy admission and cleanup tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from argus.acquisition.browser_policy import (
    BrowserAdmission,
    BrowserNetworkAttestation,
    admit_browser_request,
    current_release_identity,
    guard_browser_route,
    install_browser_policy,
    make_browser_request,
    require_browser_policy,
    set_browser_attestation_provider,
    validate_browser_attestation,
)


@pytest.fixture(autouse=True)
def reset_provider():
    set_browser_attestation_provider(None)
    yield
    set_browser_attestation_provider(None)


def _attestation(**overrides) -> BrowserNetworkAttestation:
    values = {
        "policy_identity": "proxy-policy-v1",
        "resolver_identity": "resolver-v1",
        "connection_binding": "connection-binding-v1",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "release_identity": current_release_identity(),
    }
    values.update(overrides)
    return BrowserNetworkAttestation(**values)


def _request(**overrides):
    values = {
        "url": "https://public.example/article",
        "request_id": "browser-test",
    }
    values.update(overrides)
    return make_browser_request(**values)


def test_missing_attestation_is_rejected_before_browser_creation():
    request = _request()

    failure = require_browser_policy(request, None)

    assert failure.code == "browser_policy_unavailable"
    assert failure.before_browser_creation is True
    assert failure.request_id == request.request_id


def test_invalid_and_expired_attestations_are_rejected():
    request = _request()
    now = datetime.now(timezone.utc)

    invalid = validate_browser_attestation(
        _attestation(verified=False),
        now=now,
        release=current_release_identity(),
        request_id=request.request_id,
    )
    expired = validate_browser_attestation(
        _attestation(expires_at=now),
        now=now,
        release=current_release_identity(),
        request_id=request.request_id,
    )

    assert invalid.code == "browser_policy_unavailable"
    assert expired.code == "browser_policy_unavailable"
    assert invalid.before_browser_creation is True
    assert expired.before_browser_creation is True


def test_expiry_is_strict_at_the_boundary():
    now = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
    attestation = _attestation(expires_at=now + timedelta(seconds=1))

    assert (
        validate_browser_attestation(
            attestation,
            now=now,
            release=current_release_identity(),
        )
        is None
    )
    failure = validate_browser_attestation(
        attestation,
        now=now + timedelta(seconds=1),
        release=current_release_identity(),
    )
    assert failure.code == "browser_policy_unavailable"


@pytest.mark.asyncio
async def test_provider_is_refreshed_for_each_admission_and_stale_value_never_reused():
    current = datetime.now(timezone.utc)
    fresh = _attestation(expires_at=current + timedelta(minutes=5))
    calls = 0

    class Provider:
        async def current(self):
            nonlocal calls
            calls += 1
            return fresh

    request = _request()
    first = await admit_browser_request(request, provider=Provider(), now=current)
    second = await admit_browser_request(request, provider=Provider(), now=current)

    assert isinstance(first, BrowserAdmission)
    assert isinstance(second, BrowserAdmission)
    assert calls == 2

    class StaleProvider:
        def current(self):
            return _attestation(expires_at=current - timedelta(seconds=1))

    stale = await admit_browser_request(request, provider=StaleProvider(), now=current)
    assert stale.code == "browser_policy_unavailable"


@pytest.mark.asyncio
async def test_failed_refresh_does_not_touch_browser_factory():
    import argus.extraction.playwright_extractor as extractor

    browser_factory = AsyncMock()

    class FailedProvider:
        async def current(self):
            raise RuntimeError("authority unavailable")

    set_browser_attestation_provider(FailedProvider())
    extractor._browser = None
    extractor._playwright_instance = None
    original_get_browser = extractor._get_browser
    extractor._get_browser = browser_factory
    try:
        result = await extractor.extract_playwright("https://public.example/article")
    finally:
        extractor._get_browser = original_get_browser

    assert result.error.startswith("browser_policy_unavailable:")
    browser_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_low_level_browser_factory_is_fail_closed_without_admission(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    playwright_factory = AsyncMock()
    monkeypatch.setattr(extractor, "_check_playwright", lambda: True)
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        playwright_factory,
    )

    assert await extractor._get_browser() is None
    playwright_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_attested_external_cdp_path_is_admitted_only_with_all_bindings():
    request = _request()
    attestation = _attestation()
    admission = require_browser_policy(request, attestation)

    assert isinstance(admission, BrowserAdmission)
    assert admission.policy_identity == "proxy-policy-v1"
    assert admission.resolver_address_control_identity == "resolver-v1"
    assert admission.connection_binding_identity == "connection-binding-v1"

    # The constructor rejects an incomplete binding.  A provider that reports
    # an unverifiable value is represented by ``verified=False`` instead.
    unverifiable = _attestation(verified=False)
    rejected = require_browser_policy(request, unverifiable)
    assert rejected.code == "browser_policy_unavailable"


@pytest.mark.asyncio
async def test_resource_guard_aborts_unsafe_resources_before_dispatch():
    admission = require_browser_policy(_request(), _attestation())
    assert isinstance(admission, BrowserAdmission)
    safe_urls = {
        "document": "https://public.example/article",
        "xhr": "https://public.example/api",
        "fetch": "https://public.example/data",
        "image": "https://cdn.example/image.png",
        "script": "https://cdn.example/app.js",
        "font": "https://cdn.example/font.woff2",
        "stylesheet": "https://cdn.example/app.css",
        "media": "https://cdn.example/movie.mp4",
        "worker": "https://public.example/worker.js",
    }

    def checker(url):
        return (False, "private address") if "169.254" in url else (True, "")

    for resource_type, url in safe_urls.items():
        route = SimpleNamespace(
            request=SimpleNamespace(url=url, resource_type=resource_type),
            continue_=AsyncMock(),
            abort=AsyncMock(),
        )
        await guard_browser_route(route, admission, url_checker=checker)
        route.continue_.assert_awaited_once()
        route.abort.assert_not_awaited()

    unsafe = SimpleNamespace(
        request=SimpleNamespace(
            url="http://169.254.169.254/latest/meta-data",
            resource_type="xhr",
        ),
        continue_=AsyncMock(),
        abort=AsyncMock(),
    )
    await guard_browser_route(unsafe, admission, url_checker=checker)
    unsafe.abort.assert_awaited_once()
    unsafe.continue_.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_guard_closes_unsafe_socket_and_allows_safe_socket():
    admission = require_browser_policy(_request(), _attestation())
    assert isinstance(admission, BrowserAdmission)

    def checker(url):
        return (False, "unsafe address") if "127.0.0.1" in url else (True, "")

    unsafe = SimpleNamespace(
        url="ws://127.0.0.1/private",
        close=AsyncMock(),
    )
    await admission_guard_websocket(unsafe, admission, checker)
    unsafe.close.assert_awaited_once()

    safe = SimpleNamespace(url="wss://public.example/socket", close=AsyncMock())
    await admission_guard_websocket(safe, admission, checker)
    safe.close.assert_not_awaited()


async def admission_guard_websocket(web_socket, admission, checker):
    from argus.acquisition.browser_policy import guard_browser_websocket

    await guard_browser_websocket(web_socket, admission, url_checker=checker)


@pytest.mark.asyncio
async def test_install_browser_policy_happens_before_page_creation():
    admission = require_browser_policy(_request(), _attestation())
    assert isinstance(admission, BrowserAdmission)
    events = []

    class Context:
        def route(self, pattern, handler):
            events.append(("route", pattern, handler))

        def route_web_socket(self, pattern, handler):
            events.append(("websocket", pattern, handler))

    failure = await install_browser_policy(
        Context(), admission, url_checker=lambda _url: (True, "")
    )

    assert failure is None
    assert [event[0] for event in events] == ["route", "websocket"]


@pytest.mark.asyncio
async def test_browser_oom_closes_owned_browser_and_clears_singleton(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    class Provider:
        def current(self):
            return _attestation()

    set_browser_attestation_provider(Provider())

    browser = MagicMock()
    browser.is_connected.return_value = True
    browser.close = AsyncMock()
    browser.new_context = AsyncMock(side_effect=MemoryError("browser OOM"))
    runtime = MagicMock()
    runtime.stop = AsyncMock()
    extractor._browser = browser
    extractor._playwright_instance = runtime

    result = await extractor._extract_playwright("https://public.example/article")

    assert "browser OOM" in result.error
    browser.close.assert_awaited_once()
    runtime.stop.assert_awaited_once()
    assert extractor._browser is None
    assert extractor._playwright_instance is None


@pytest.mark.asyncio
async def test_context_creation_failure_closes_page_context_and_browser(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    class Provider:
        def current(self):
            return _attestation()

    set_browser_attestation_provider(Provider())

    browser = MagicMock()
    browser.is_connected.return_value = True
    browser.new_context = AsyncMock(side_effect=RuntimeError("context creation failed"))
    browser.close = AsyncMock()
    extractor._browser = browser

    result = await extractor._extract_playwright("https://public.example/article")

    assert result.error == "playwright: context creation failed"
    browser.close.assert_awaited_once()
    assert extractor._browser is None
