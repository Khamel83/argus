"""
Authenticated content extraction using Playwright with cookies.

For paywall sites where we have browser cookies, this uses a headless browser
to render the full page behind authentication, then extracts text via trafilatura.
"""

import asyncio
import os
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

# 30s timeout: browser launch + page load + render + content extraction
AUTH_TIMEOUT_MS = 30_000

# SOCKS5 proxy for routing through residential IP (avoids datacenter bot detection)
AUTH_PROXY = os.getenv("ARGUS_AUTH_PROXY", "")  # e.g. socks5://127.0.0.1:2080

# Lazy-initialized Playwright resources — shared across requests
_pw = None
_browser = None
_contexts: dict[str, object] = {}  # domain → browser context


async def _ensure_browser():
    """Get or create a shared Playwright browser instance (lazy init)."""
    global _pw, _browser, _contexts
    if _browser is not None and not _browser.is_connected():
        logger.warning("Playwright browser disconnected, relaunching")
        await shutdown_browser()
    if _browser is None:
        try:
            from playwright.async_api import async_playwright
            _pw = await async_playwright().start()
            _browser = await _pw.chromium.launch(headless=True)
            logger.info("Playwright browser launched")
        except Exception as e:
            logger.error("Failed to launch Playwright: %s", e)
            _pw = None
            return None
    return _browser


async def shutdown_browser():
    """Close all browser contexts and the browser. Call on app shutdown."""
    global _pw, _browser, _contexts
    for ctx in _contexts.values():
        try:
            await ctx.close()
        except Exception:
            pass
    _contexts.clear()
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _pw:
        try:
            await _pw.stop()
        except Exception:
            pass
        _pw = None


async def _get_context(domain: str):
    """Get or create a browser context with cookies for a domain."""
    if domain in _contexts:
        ctx = _contexts[domain]
        # Check if browser is still alive
        browser = await _ensure_browser()
        if browser is None:
            return None
        try:
            # Verify context is still usable
            if ctx.pages is not None:
                return ctx
        except Exception:
            pass
        # Context is dead, remove it
        del _contexts[domain]

    browser = await _ensure_browser()
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

    cookie_path = get_cookie_path(domain)
    if cookie_path is None:
        return None

    cookies = load_editthiscookie_json(cookie_path)
    if not cookies:
        return None

    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    status_code = 0
    try:
        launch_opts = {
            "headless": True,
            "args": ["--disable-gpu", "--disable-software-rasterizer"],
        }
        if AUTH_PROXY:
            launch_opts["proxy"] = {"server": AUTH_PROXY}
        browser = await pw.chromium.launch(**launch_opts)
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            await context.add_cookies(cookies)

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
                    return None

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
                    extractor=ExtractorName.AUTH_PLAYWRIGHT,
                )

            finally:
                await page.close()
            await context.close()
        finally:
            await browser.close()
    except Exception as e:
        logger.warning("Auth extraction failed for %s: %s", url[:60], e)
        record_auth_request(domain, success=False, status_code=status_code)
        return None
    finally:
        await pw.stop()


def _extract_from_html(html: str) -> str:
    """Extract clean text from rendered HTML using readability-lxml.

    JS-rendered pages often have content in shadow DOM or React components
    that trafilatura can't parse. readability-lxml handles these well.
    """
    from readability import Document

    doc = Document(html)
    summary = doc.summary()
    if summary:
        # Strip any remaining HTML tags
        import re
        text = re.sub(r"<[^>]+>", "", summary)
        return text.strip()
    return ""
