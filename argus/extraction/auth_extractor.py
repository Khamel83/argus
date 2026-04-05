"""
Authenticated content extraction using Playwright with cookies.

For paywall sites where we have browser cookies, this uses a headless browser
to render the full page behind authentication, then extracts text via trafilatura.
"""

import asyncio
from typing import Optional

from argus.extraction.cookies import (
    can_authenticate,
    get_cookie_path,
    load_editthiscookie_json,
    needs_auth,
    record_auth_request,
)
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.auth")

AUTH_TIMEOUT_MS = 15_000  # 15 seconds

# Lazy-initialized Playwright browser — shared across requests
_browser = None
_contexts: dict[str, object] = {}  # domain → browser context


class ExtractorName(str):
    """Extend with auth extractor."""
    AUTH = "auth_playwright"


AUTH_EXTRACTOR = ExtractorName("auth_playwright")


async def _get_browser():
    """Get or create a shared Playwright browser instance."""
    global _browser
    if _browser is None:
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(headless=True)
        except Exception as e:
            logger.error("Failed to launch Playwright: %s", e)
            return None, None
    return _browser


async def _get_context(domain: str):
    """Get or create a browser context with cookies for a domain."""
    if domain in _contexts:
        return _contexts[domain]

    browser = await _get_browser()
    if browser is None:
        return None

    cookie_path = get_cookie_path(domain)
    if cookie_path is None:
        return None

    cookies = load_editthiscookie_json(cookie_path)
    if not cookies:
        return None

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
    )
    await context.add_cookies(cookies)
    _contexts[domain] = context
    logger.info("Created authenticated browser context for %s", domain)
    return context


async def extract_authenticated(url: str, domain: str) -> Optional[ExtractedContent]:
    """Extract content using Playwright with cookies for a paywall domain.

    Returns None if cookies aren't available or extraction fails.
    Caller should fall back to regular extract_url() in that case.
    """
    if not needs_auth(url):
        return None

    if not can_authenticate(domain):
        return None

    context = await _get_context(domain)
    if context is None:
        return None

    status_code = 0
    try:
        page = await context.new_page()
        try:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=AUTH_TIMEOUT_MS,
            )
            status_code = response.status if response else 0

            if status_code in (401, 403):
                logger.warning("Auth failed for %s (HTTP %d)", url[:60], status_code)
                record_auth_request(domain, success=False, status_code=status_code)
                return None

            # Wait for article content to render
            await page.wait_for_timeout(3000)

            html = await page.content()
            if not html:
                record_auth_request(domain, success=False, status_code=status_code or 500)
                return None

            # Extract text from rendered HTML using trafilatura
            loop = asyncio.get_event_loop()
            extracted = await loop.run_in_executor(None, _extract_from_html, html)

            if not extracted or len(extracted) < 200:
                logger.info("Auth extract returned too little for %s (%d chars)", url[:60], len(extracted or ""))
                record_auth_request(domain, success=False, status_code=status_code)
                return None

            # Also grab the title from the page
            title = await page.title()

            word_count = len(extracted.split())
            logger.info(
                "Authenticated extraction for %s: %d words (HTTP %d)",
                url[:60], word_count, status_code,
            )
            record_auth_request(domain, success=True, status_code=status_code)

            return ExtractedContent(
                url=url,
                title=title,
                text=extracted,
                word_count=word_count,
                extractor=AUTH_EXTRACTOR,
            )

        finally:
            await page.close()

    except Exception as e:
        logger.warning("Auth extraction failed for %s: %s", url[:60], e)
        record_auth_request(domain, success=False, status_code=status_code)
        return None


def _extract_from_html(html: str) -> str:
    """Extract clean text from rendered HTML using trafilatura."""
    import trafilatura

    result = trafilatura.bare_extraction(html)
    if result and result.get("text"):
        return result["text"]
    return ""
