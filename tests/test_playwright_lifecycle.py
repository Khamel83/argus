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
    monkeypatch.setattr(extractor, "_browser_start_count", 0, raising=False)
    monkeypatch.setattr(extractor, "_remote_connection_count", 0, raising=False)
    monkeypatch.setattr(extractor, "_browser_runtime_state", "unknown", raising=False)
    monkeypatch.setattr(
        extractor,
        "_browser_runtime_reason",
        "not_observed_since_restart",
        raising=False,
    )
    yield
    await extractor.close_browser()


@pytest.mark.asyncio
async def test_failed_launch_stops_started_runtime_and_clears_singletons(
    monkeypatch, caplog
):
    """A missing Chromium binary must not leave Playwright's driver running."""
    import argus.extraction.playwright_extractor as extractor
    import playwright.async_api

    runtime = MagicMock()
    runtime.stop = AsyncMock()
    secret = "wss://browser.example/devtools?token=super-secret"
    runtime.chromium.launch = AsyncMock(side_effect=RuntimeError(secret))
    playwright_factory = MagicMock()
    playwright_factory.return_value.start = AsyncMock(return_value=runtime)
    monkeypatch.setattr(playwright.async_api, "async_playwright", playwright_factory)

    assert await extractor._get_browser() is None

    runtime.stop.assert_awaited_once()
    assert extractor._browser is None
    assert extractor._playwright_instance is None
    assert extractor.browser_capability_status()["runtime_state"] == "degraded"
    assert secret not in caplog.text
    assert "super-secret" not in caplog.text


@pytest.mark.asyncio
async def test_unavailable_browser_is_not_restarted_until_explicit_reset(monkeypatch):
    """A known missing browser remains unavailable until an operator resets it."""
    import argus.extraction.playwright_extractor as extractor
    import playwright.async_api

    failed_runtime = MagicMock()
    failed_runtime.stop = AsyncMock()
    failed_runtime.chromium.launch = AsyncMock(
        side_effect=RuntimeError("Chromium executable missing")
    )
    recovered_browser = MagicMock()
    recovered_browser.is_connected.return_value = True
    recovered_runtime = MagicMock()
    recovered_runtime.stop = AsyncMock()
    recovered_runtime.chromium.launch = AsyncMock(return_value=recovered_browser)
    playwright_factory = MagicMock()
    playwright_factory.return_value.start = AsyncMock(
        side_effect=[failed_runtime, recovered_runtime]
    )
    monkeypatch.setattr(playwright.async_api, "async_playwright", playwright_factory)

    assert await extractor._get_browser() is None
    assert await extractor._get_browser() is None
    assert playwright_factory.return_value.start.await_count == 1

    await extractor.reset_browser()
    assert extractor.browser_capability_status()["runtime_state"] == "unknown"

    assert await extractor._get_browser() is recovered_browser
    assert playwright_factory.return_value.start.await_count == 2
    assert extractor.browser_capability_status()["runtime_state"] == "healthy"


@pytest.mark.asyncio
async def test_browser_restart_metric_counts_successful_relaunches(monkeypatch):
    import argus.extraction.playwright_extractor as extractor
    import playwright.async_api

    first_browser = MagicMock()
    first_browser.is_connected.return_value = False
    first_browser.close = AsyncMock()
    second_browser = MagicMock()
    second_browser.is_connected.return_value = True
    second_browser.close = AsyncMock()
    first_runtime = MagicMock()
    first_runtime.stop = AsyncMock()
    first_runtime.chromium.launch = AsyncMock(return_value=first_browser)
    second_runtime = MagicMock()
    second_runtime.stop = AsyncMock()
    second_runtime.chromium.launch = AsyncMock(return_value=second_browser)
    playwright_factory = MagicMock()
    playwright_factory.return_value.start = AsyncMock(
        side_effect=[first_runtime, second_runtime]
    )
    monkeypatch.setattr(playwright.async_api, "async_playwright", playwright_factory)

    assert await extractor._get_browser() is first_browser
    assert await extractor._get_browser() is second_browser

    assert extractor.browser_capability_status()["process_restarts"] == 1


@pytest.mark.asyncio
async def test_remote_cdp_connection_is_not_counted_as_owned_browser_restart(
    monkeypatch,
):
    import argus.extraction.playwright_extractor as extractor
    import playwright.async_api

    browser = MagicMock()
    browser.is_connected.return_value = True
    runtime = MagicMock()
    runtime.stop = AsyncMock()
    runtime.chromium.connect_over_cdp = AsyncMock(return_value=browser)
    playwright_factory = MagicMock()
    playwright_factory.return_value.start = AsyncMock(return_value=runtime)
    monkeypatch.setattr(playwright.async_api, "async_playwright", playwright_factory)
    monkeypatch.setattr(extractor, "OBSCURA_CDP_URL", "ws://redacted.invalid")

    assert await extractor._get_browser() is browser

    status = extractor.browser_capability_status()
    assert status["process_restarts"] == 0
    assert status["remote_connections"] == 1


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
    assert runtime.chromium.launch.await_args.kwargs["chromium_sandbox"] is True
    assert "--no-sandbox" not in runtime.chromium.launch.await_args.kwargs.get(
        "args", []
    )


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
async def test_missing_browser_smoke_leaves_no_playwright_driver_child(
    monkeypatch, tmp_path
):
    """A real missing-browser launch has no surviving Playwright driver child."""
    import argus.extraction.playwright_extractor as extractor

    before = _playwright_driver_children()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-browser"))

    assert await asyncio.wait_for(extractor._get_browser(), timeout=10) is None

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and _playwright_driver_children() - before:
        await asyncio.sleep(0.05)
    assert _playwright_driver_children() == before


def test_browser_status_reports_manifest_capability_and_loaded_state(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    browser = MagicMock()
    browser.is_connected.return_value = True
    monkeypatch.setattr(extractor, "_browser", browser)
    monkeypatch.setattr(
        extractor,
        "inspect_playwright_browser_capability",
        MagicMock(
            return_value={
                "declared": True,
                "available": True,
                "sandbox_required": True,
                "playwright_version": "1.58.0",
                "revision": "1208",
            }
        ),
    )

    status = extractor.browser_capability_status()

    assert status["declared"] is True
    assert status["available"] is True
    assert status["loaded"] is True
    assert status["loaded_source"] == "local_chromium"
    assert status["sandboxed"] is True
    assert status["matches_declared"] is True


def test_local_browser_status_measures_only_argus_chromium_descendants(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    browser = MagicMock()
    browser.is_connected.return_value = True
    monkeypatch.setattr(extractor, "_browser", browser)
    monkeypatch.setattr(extractor.os, "getpid", lambda: 100)
    monkeypatch.setattr(
        extractor.subprocess,
        "run",
        MagicMock(
            return_value=MagicMock(
                returncode=0,
                stdout=(
                    "101 100 100 chromium\n"
                    "102 101 50 chrome-helper\n"
                    "103 100 999 node\n"
                    "201 200 500 chromium\n"
                ),
            )
        ),
    )
    monkeypatch.setattr(
        extractor,
        "inspect_playwright_browser_capability",
        MagicMock(return_value={"declared": True, "available": True}),
    )

    status = extractor.browser_capability_status()

    assert status["processes"] == 2
    assert status["memory_bytes"] == 150 * 1024


def test_browser_status_does_not_misreport_obscura_as_local_sandbox(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    browser = MagicMock()
    browser.is_connected.return_value = True
    monkeypatch.setattr(extractor, "_browser", browser)
    monkeypatch.setattr(extractor, "_using_obscura_cdp", True)
    monkeypatch.setattr(
        extractor,
        "inspect_playwright_browser_capability",
        MagicMock(
            return_value={
                "declared": True,
                "available": True,
                "sandbox_required": True,
            }
        ),
    )

    status = extractor.browser_capability_status()

    assert status["loaded_source"] == "obscura_cdp"
    assert status["sandboxed"] is False
    assert status["matches_declared"] is False


def test_loaded_browser_without_declared_manifest_is_reported_as_mismatch(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    browser = MagicMock()
    browser.is_connected.return_value = True
    monkeypatch.setattr(extractor, "_browser", browser)
    monkeypatch.setattr(extractor, "_using_obscura_cdp", False)
    monkeypatch.setattr(
        extractor,
        "inspect_playwright_browser_capability",
        MagicMock(
            return_value={
                "declared": False,
                "available": False,
                "sandbox_required": True,
            }
        ),
    )

    status = extractor.browser_capability_status()

    assert status["loaded_source"] == "local_chromium"
    assert status["matches_declared"] is False


def test_declared_but_missing_unloaded_browser_is_a_mismatch(monkeypatch):
    import argus.extraction.playwright_extractor as extractor

    monkeypatch.setattr(extractor, "_browser", None)
    monkeypatch.setattr(
        extractor,
        "inspect_playwright_browser_capability",
        MagicMock(
            return_value={
                "declared": True,
                "available": False,
                "sandbox_required": True,
                "degraded_reason": "browser_artifact_unavailable",
            }
        ),
    )

    status = extractor.browser_capability_status()

    assert status["loaded"] is False
    assert status["matches_declared"] is False
    assert status["processes"] == 0
    assert status["memory_bytes"] == 0
    assert status["process_restarts"] == 0
    assert status["metrics_source"] == "process_memory_since_start"
