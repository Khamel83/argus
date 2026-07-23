"""Lifecycle tests for the reusable Playwright browser."""

import asyncio
import os
import subprocess
import time
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
async def reset_playwright_state(monkeypatch):
    """Keep singleton state isolated while exercising its public lifecycle."""
    import argus.extraction.playwright_extractor as extractor

    await extractor.close_browser()
    monkeypatch.setattr(extractor, "_browser", None)
    monkeypatch.setattr(extractor, "_playwright_instance", None)
    monkeypatch.setattr(extractor, "_using_obscura_cdp", False)
    monkeypatch.setattr(extractor, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(extractor, "_browser_unavailable", False, raising=False)
    yield
    await extractor.close_browser()


@pytest.mark.asyncio
async def test_failed_launch_stops_started_runtime_and_clears_singletons(monkeypatch):
    """A missing Chromium binary must not leave Playwright's driver running."""
    import argus.extraction.playwright_extractor as extractor
    import playwright.async_api

    runtime = MagicMock()
    runtime.stop = AsyncMock()
    runtime.chromium.launch = AsyncMock(side_effect=RuntimeError("Chromium executable missing"))
    playwright_factory = MagicMock()
    playwright_factory.return_value.start = AsyncMock(return_value=runtime)
    monkeypatch.setattr(playwright.async_api, "async_playwright", playwright_factory)

    assert await extractor._get_browser() is None

    runtime.stop.assert_awaited_once()
    assert extractor._browser is None
    assert extractor._playwright_instance is None


@pytest.mark.asyncio
async def test_unavailable_browser_is_not_restarted_until_explicit_reset(monkeypatch):
    """A known missing browser remains unavailable until an operator resets it."""
    import argus.extraction.playwright_extractor as extractor
    import playwright.async_api

    failed_runtime = MagicMock()
    failed_runtime.stop = AsyncMock()
    failed_runtime.chromium.launch = AsyncMock(side_effect=RuntimeError("Chromium executable missing"))
    recovered_browser = MagicMock()
    recovered_browser.is_connected.return_value = True
    recovered_runtime = MagicMock()
    recovered_runtime.stop = AsyncMock()
    recovered_runtime.chromium.launch = AsyncMock(return_value=recovered_browser)
    playwright_factory = MagicMock()
    playwright_factory.return_value.start = AsyncMock(side_effect=[failed_runtime, recovered_runtime])
    monkeypatch.setattr(playwright.async_api, "async_playwright", playwright_factory)

    assert await extractor._get_browser() is None
    assert await extractor._get_browser() is None
    assert playwright_factory.return_value.start.await_count == 1

    await extractor.reset_browser()

    assert await extractor._get_browser() is recovered_browser
    assert playwright_factory.return_value.start.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_initialization_creates_one_runtime_and_browser(monkeypatch):
    """Concurrent requests share one serialized browser initialization."""
    import argus.extraction.playwright_extractor as extractor
    import playwright.async_api

    start_entered = asyncio.Event()
    allow_start = asyncio.Event()

    async def start_runtime():
        start_entered.set()
        await allow_start.wait()
        return runtime

    browser = MagicMock()
    browser.is_connected.return_value = True
    runtime = MagicMock()
    runtime.stop = AsyncMock()
    runtime.chromium.launch = AsyncMock(return_value=browser)
    playwright_factory = MagicMock()
    playwright_factory.return_value.start = AsyncMock(side_effect=start_runtime)
    monkeypatch.setattr(playwright.async_api, "async_playwright", playwright_factory)

    first = asyncio.create_task(extractor._get_browser())
    await start_entered.wait()
    second = asyncio.create_task(extractor._get_browser())
    await asyncio.sleep(0)
    allow_start.set()

    assert await first is browser
    assert await second is browser
    playwright_factory.return_value.start.assert_awaited_once()
    runtime.chromium.launch.assert_awaited_once()


@pytest.mark.asyncio
async def test_extraction_cleanup_is_safe_when_context_creation_fails():
    """A failure before context assignment still returns an extraction error."""
    import argus.extraction.playwright_extractor as extractor

    browser = MagicMock()
    browser.new_context = AsyncMock(side_effect=RuntimeError("context creation failed"))
    extractor._browser = browser

    result = await extractor._extract_playwright("https://example.com")

    assert result.error == "playwright: context creation failed"


@pytest.mark.asyncio
async def test_extraction_failure_closes_page_and_context():
    """Per-request resources are released when navigation fails."""
    import argus.extraction.playwright_extractor as extractor

    page = MagicMock()
    page.goto = AsyncMock(side_effect=RuntimeError("navigation failed"))
    page.close = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    extractor._browser = browser

    result = await extractor._extract_playwright("https://example.com")

    assert result.error == "playwright: navigation failed"
    page.close.assert_awaited_once()
    context.close.assert_awaited_once()


def test_application_shutdown_closes_playwright_resources(monkeypatch):
    """FastAPI lifespan invokes the extractor's production cleanup entry point."""
    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    import argus.extraction.playwright_extractor as extractor

    broker = MagicMock()
    broker._reachability.probe_all = AsyncMock()
    broker._providers = {}
    broker.budget_tracker.close = MagicMock()
    close_browser = AsyncMock()
    monkeypatch.setattr(extractor, "close_browser", close_browser)

    with TestClient(create_app(broker=broker)):
        pass

    close_browser.assert_awaited_once()


def _playwright_driver_children() -> set[int]:
    """Return this test process's Playwright driver children, if any."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        int(parts[0])
        for line in result.stdout.splitlines()
        if (parts := line.split(maxsplit=2))
        and len(parts) == 3
        and int(parts[1]) == os.getpid()
        and "playwright/driver" in parts[2]
    }


@pytest.mark.asyncio
async def test_missing_browser_smoke_leaves_no_playwright_driver_child(monkeypatch, tmp_path):
    """A real missing-browser launch has no surviving Playwright driver child."""
    import argus.extraction.playwright_extractor as extractor

    before = _playwright_driver_children()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-browser"))

    assert await asyncio.wait_for(extractor._get_browser(), timeout=10) is None

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and _playwright_driver_children() - before:
        await asyncio.sleep(0.05)
    assert _playwright_driver_children() == before
