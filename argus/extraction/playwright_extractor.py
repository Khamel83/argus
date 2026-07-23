"""
Local Playwright Extraction - Headless browser for JS-rendered content.

Uses async Playwright with a reusable browser instance (singleton).

If ARGUS_OBSCURA_CDP_URL is set (e.g. ws://127.0.0.1:9222), Playwright connects
to an Obscura CDP server instead of launching headless Chrome. Obscura provides
built-in stealth (navigator.webdriver=undefined, fingerprint randomization) and
uses 30MB vs 200MB memory. Falls back to launching Chrome if connection fails.

When connected via Obscura CDP, extraction uses LP.getMarkdown for cleaner output.

Gracefully degrades if playwright is not installed.
"""

import asyncio
import importlib.util
import os

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.ssrf import is_safe_url
from argus.logging import get_logger
from argus.runtime_manifest import inspect_playwright_browser_capability

logger = get_logger("extraction.playwright")

OBSCURA_CDP_URL = os.getenv("ARGUS_OBSCURA_CDP_URL", "")

_browser = None
_playwright_instance = None
_using_obscura_cdp = False
_PLAYWRIGHT_AVAILABLE = None
_browser_unavailable = False
_browser_lock: asyncio.Lock | None = None


def _check_playwright():
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is None:
        _PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright.async_api") is not None
        if not _PLAYWRIGHT_AVAILABLE:
            logger.debug("playwright not installed — headless browser extraction disabled")
    return _PLAYWRIGHT_AVAILABLE


def _get_browser_lock() -> asyncio.Lock:
    """Create the singleton initialization lock lazily for the active loop."""
    global _browser_lock
    if _browser_lock is None:
        _browser_lock = asyncio.Lock()
    return _browser_lock


async def _close_browser_resources() -> None:
    """Release singleton resources and clear their references before awaiting."""
    global _browser, _playwright_instance, _using_obscura_cdp

    browser, playwright_instance = _browser, _playwright_instance
    _browser = None
    _playwright_instance = None
    _using_obscura_cdp = False

    if browser:
        try:
            await browser.close()
        except Exception as exc:
            logger.debug("Failed to close Playwright browser: %s", exc)
    if playwright_instance:
        try:
            await playwright_instance.stop()
        except Exception as exc:
            logger.debug("Failed to stop Playwright runtime: %s", exc)


async def _get_browser():
    """Get or create a shared browser instance.

    Tries Obscura CDP first (if ARGUS_OBSCURA_CDP_URL is set), then falls
    back to launching headless Chrome.
    """
    global _browser, _playwright_instance, _using_obscura_cdp, _browser_unavailable

    async with _get_browser_lock():
        if _browser and _browser.is_connected():
            return _browser

        if _browser:
            await _close_browser_resources()

        if _browser_unavailable or not _check_playwright():
            return None

        try:
            from playwright.async_api import async_playwright
            _playwright_instance = await async_playwright().start()

            if OBSCURA_CDP_URL:
                try:
                    _browser = await _playwright_instance.chromium.connect_over_cdp(OBSCURA_CDP_URL)
                    _using_obscura_cdp = True
                    logger.info("Connected to Obscura CDP at %s", OBSCURA_CDP_URL)
                    return _browser
                except Exception as e:
                    logger.warning(
                        "Obscura CDP connection failed (%s), falling back to Chrome: %s",
                        OBSCURA_CDP_URL, e,
                    )
                    _using_obscura_cdp = False

            _browser = await _playwright_instance.chromium.launch(
                headless=True,
                chromium_sandbox=True,
            )
            return _browser
        except Exception as e:
            logger.warning("Failed to launch Playwright browser: %s", e)
            await _close_browser_resources()
            _browser_unavailable = True
            return None


async def _extract_playwright(url: str, timeout_ms: int = 15000) -> ExtractedContent:
    """Extract content using headless browser (Obscura CDP or Chrome)."""
    browser = await _get_browser()
    if not browser:
        return ExtractedContent(url=url, error="playwright: not available")

    context = None
    page = None
    try:
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
        final_url = page.url
        if final_url and final_url != url:
            safe, reason = is_safe_url(final_url)
            if not safe:
                return ExtractedContent(url=url, error=f"playwright: unsafe redirect blocked: {reason}")
        await asyncio.sleep(1)

        title = await page.title()
        text = ""

        # Obscura CDP: use LP.getMarkdown for cleaner DOM-to-Markdown conversion
        if _using_obscura_cdp:
            try:
                cdp = await context.new_cdp_session(page)
                result = await cdp.send("LP.getMarkdown")
                text = result.get("markdown", "").strip()
                await cdp.detach()
            except Exception as e:
                logger.debug("LP.getMarkdown failed, falling back to innerText: %s", e)

        # Standard path (Chrome, or Obscura CDP fallback)
        if not text or len(text.split()) < 50:
            text = await page.evaluate("""() => {
                const els = document.querySelectorAll('script, style, nav, footer, header, aside, iframe, noscript');
                els.forEach(el => el.remove());
                const main = document.querySelector('main, article, [role="main"], .post-content, .article-body, .entry-content');
                const source = main || document.body;
                return source.innerText || source.textContent || '';
            }""")
            text = text.strip() if text else ""

        if not text or len(text.strip()) < 100:
            return ExtractedContent(url=url, error="playwright: too little content after render")

        return ExtractedContent(
            url=final_url or url,
            title=title or "",
            text=text,
            word_count=len(text.split()),
            extractor=ExtractorName.PLAYWRIGHT,
        )
    except Exception as e:
        logger.debug("Playwright extraction failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"playwright: {e}")
    finally:
        if page:
            try:
                await page.close()
            except Exception as exc:
                logger.debug("Failed to close Playwright page for %s: %s", url[:60], exc)
        if context:
            try:
                await context.close()
            except Exception as exc:
                logger.debug("Failed to close Playwright context for %s: %s", url[:60], exc)


async def extract_playwright(url: str) -> ExtractedContent:
    """Public entry point for Playwright extraction."""
    return await _extract_playwright(url)


async def close_browser():
    """Close the shared browser instance (call on shutdown).

    When using Obscura CDP, this disconnects from the Obscura server
    without stopping it — Obscura keeps running for future connections.
    """
    async with _get_browser_lock():
        await _close_browser_resources()


async def reset_browser():
    """Explicitly clear the unavailable capability and retry on a later request.

    This is deliberately not automatic: a failed local Chromium launch remains
    cached until an operator has changed the runtime environment.
    """
    global _browser_unavailable
    async with _get_browser_lock():
        await _close_browser_resources()
        _browser_unavailable = False


def browser_capability_status() -> dict[str, object]:
    """Return sanitized declared, installed, and loaded browser state."""
    status = inspect_playwright_browser_capability()
    loaded = bool(_browser and _browser.is_connected())
    loaded_source = (
        "obscura_cdp"
        if loaded and _using_obscura_cdp
        else "local_chromium"
        if loaded
        else None
    )
    status["loaded"] = loaded
    status["loaded_source"] = loaded_source
    status["sandboxed"] = loaded and loaded_source == "local_chromium"
    status["matches_declared"] = not loaded or (
        status.get("declared") is True
        and status.get("available") is True
        and loaded_source == "local_chromium"
    )
    return status
