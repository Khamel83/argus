"""
Local Playwright Extraction - Headless browser for JS-rendered content.

Uses async Playwright with a reusable browser instance (singleton).
Gracefully degrades if playwright is not installed.
"""

import asyncio
import logging
from typing import Optional

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.playwright")

_browser = None
_playwright_instance = None
_PLAYWRIGHT_AVAILABLE = None


def _check_playwright():
    """Check if playwright is available (cached check)."""
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is None:
        try:
            import playwright.async_api  # noqa: F401
            _PLAYWRIGHT_AVAILABLE = True
        except ImportError:
            _PLAYWRIGHT_AVAILABLE = False
            logger.debug("playwright not installed — headless browser extraction disabled")
    return _PLAYWRIGHT_AVAILABLE


async def _get_browser():
    """Get or create a shared browser instance."""
    global _browser, _playwright_instance

    if _browser and _browser.is_connected():
        return _browser

    if not _check_playwright():
        return None

    try:
        from playwright.async_api import async_playwright
        _playwright_instance = await async_playwright().start()
        _browser = await _playwright_instance.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox'],
        )
        return _browser
    except Exception as e:
        logger.warning("Failed to launch Playwright browser: %s", e)
        return None


async def _extract_playwright(url: str, timeout_ms: int = 15000) -> ExtractedContent:
    """Extract content using headless Chromium.

    Args:
        url: URL to extract
        timeout_ms: Page load timeout in milliseconds

    Returns:
        ExtractedContent with text from rendered page
    """
    browser = await _get_browser()
    if not browser:
        return ExtractedContent(url=url, error="playwright: not available")

    page = None
    try:
        page = await browser.new_page()
        await page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)

        # Wait for content to settle
        await asyncio.sleep(1)

        # Try to get meaningful text
        text = await page.evaluate("""() => {
            // Remove script/style/nav/footer elements
            const els = document.querySelectorAll('script, style, nav, footer, header, aside, iframe, noscript');
            els.forEach(el => el.remove());

            // Get main content area if it exists
            const main = document.querySelector('main, article, [role="main"], .post-content, .article-body, .entry-content');
            const source = main || document.body;

            return source.innerText || source.textContent || '';
        }""")

        title = await page.title()

        if not text or len(text.strip()) < 100:
            return ExtractedContent(url=url, error="playwright: too little content after render")

        text = text.strip()
        return ExtractedContent(
            url=url,
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
            except Exception:
                pass


async def extract_playwright(url: str) -> ExtractedContent:
    """Public entry point for Playwright extraction."""
    return await _extract_playwright(url)


async def close_browser():
    """Close the shared browser instance (call on shutdown)."""
    global _browser, _playwright_instance
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright_instance:
        try:
            await _playwright_instance.stop()
        except Exception:
            pass
        _playwright_instance = None
