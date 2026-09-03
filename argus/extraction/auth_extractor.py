"""
Authenticated content extraction using Playwright with cookies.

For paywall sites where we have browser cookies, this uses a headless browser
to render the full page behind authentication, then extracts text via trafilatura.
"""

import asyncio
import inspect
import os
from typing import Optional

from argus.acquisition.browser_policy import (
    BrowserAdmission,
    admit_browser_url,
    failure_text,
    install_browser_policy,
)
from argus.acquisition.guarded import GuardedAcquisitionError, guarded_browser_session
from argus.extraction.cookies import (
    can_authenticate,
    get_cookie_path,
    load_editthiscookie_json,
    needs_auth,
    record_auth_request,
)
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.acquisition.guarded import guarded_url_policy
from argus.extraction.trafilatura_result import normalize_trafilatura_result
from argus.logging import get_logger

logger = get_logger("extraction.auth")

# Compatibility alias; URL decisions are centralized in Guarded Acquisition.
is_safe_url = guarded_url_policy

AUTH_TIMEOUT_MS = 15_000  # 15 seconds

# Lazy-initialized Playwright browser — shared across requests
_browser = None
_contexts: dict[str, object] = {}  # domain → browser context
_playwright_instance = None


OBScura_CDP_URL = os.getenv("ARGUS_OBSCURA_CDP_URL", "")


async def _close_browser_resources() -> None:
    """Close auth contexts, browser, and Playwright driver after a hard failure."""

    global _browser, _playwright_instance
    contexts = tuple(_contexts.values())
    _contexts.clear()
    browser, playwright_instance = _browser, _playwright_instance
    _browser = None
    _playwright_instance = None

    seen: set[int] = set()
    for context in contexts:
        if id(context) in seen:
            continue
        seen.add(id(context))
        try:
            result = context.close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Failed to close auth context reason=close_failed")
    if browser is not None:
        try:
            result = browser.close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Failed to close auth browser reason=close_failed")
    if playwright_instance is not None:
        try:
            result = playwright_instance.stop()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("Failed to stop auth Playwright runtime reason=stop_failed")


async def _get_browser(admission: BrowserAdmission | None = None):
    """Get or create a shared Playwright browser instance.

    Tries Obscura CDP first (if ARGUS_OBSCURA_CDP_URL is set), then falls
    back to launching headless Chrome.
    """
    global _browser, _playwright_instance
    if admission is None:
        try:
            await guarded_browser_session(
                "https://browser-policy.invalid/",
                profile="authenticated_content",
                credential_policy="origin_scoped",
                caller_principal="authenticated-browser-runtime",
                request_id="auth-runtime",
            )
        except GuardedAcquisitionError:
            return None
        admission = await admit_browser_url(
            "https://browser-policy.invalid/",
            profile="authenticated_content",
            credential_policy="origin_scoped",
            caller_principal="authenticated-browser-runtime",
            request_id="auth-runtime",
        )
        if not isinstance(admission, BrowserAdmission):
            return None
    if _browser is not None:
        try:
            if hasattr(_browser, 'is_connected') and _browser.is_connected():
                return _browser
        except Exception:
            pass
        await _close_browser_resources()

    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        _playwright_instance = pw

        if OBScura_CDP_URL:
            try:
                _browser = await pw.chromium.connect_over_cdp(OBScura_CDP_URL)
                logger.info("Auth extractor connected to Obscura CDP")
                return _browser
            except Exception as e:
                logger.warning("Obscura CDP unavailable, falling back to Chrome: %s", e)

        _browser = await pw.chromium.launch(headless=True)
    except Exception as e:
        logger.error("Failed to launch Playwright: %s", e)
        try:
            if _playwright_instance is not None:
                await _playwright_instance.stop()
        except Exception:
            logger.debug("Failed to stop auth Playwright runtime reason=stop_failed")
        _playwright_instance = None
        _browser = None
        return None
    return _browser


async def _get_context(domain: str, url: str | None = None, admission: BrowserAdmission | None = None):
    """Get or create a browser context with cookies for a domain."""
    target_url = url or f"https://{domain}/"
    if admission is None:
        admission = await admit_browser_url(
            target_url,
            profile="authenticated_content",
            credential_policy="origin_scoped",
            caller_principal="authenticated-browser",
            request_id="auth-context",
        )
    if not isinstance(admission, BrowserAdmission):
        return None

    if domain in _contexts:
        return _contexts[domain]

    browser = await _get_browser(admission)
    if browser is None:
        return None

    cookie_path = get_cookie_path(domain)
    if cookie_path is None:
        return None

    cookies = load_editthiscookie_json(cookie_path)
    if not cookies:
        return None

    context = None
    try:
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            service_workers="block",
        )
        policy_failure = await install_browser_policy(context, admission)
        if policy_failure is not None:
            await _close_browser_resources()
            return None
        await context.add_cookies(cookies)
    except Exception:
        await _close_browser_resources()
        return None
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
        logger.warning("Cannot authenticate for %s: cookies unavailable, stale, or rate-limited", domain)
        return ExtractedContent(url=url, error=f"auth: cannot authenticate for {domain}")

    try:
        await guarded_browser_session(
            url,
            profile="authenticated_content",
            credential_policy="origin_scoped",
            caller_principal="authenticated-browser",
            request_id="auth-extract",
        )
    except GuardedAcquisitionError as exc:
        return ExtractedContent(url=url, error=f"auth: {exc.failure.code.value}")

    admission = await admit_browser_url(
        url,
        profile="authenticated_content",
        credential_policy="origin_scoped",
        caller_principal="authenticated-browser",
        request_id="auth-extract",
    )
    if not isinstance(admission, BrowserAdmission):
        return ExtractedContent(url=url, error=failure_text(admission))

    context = await _get_context(domain, url=url, admission=admission)
    if context is None:
        logger.warning("Auth context unavailable for %s: cookies may have expired", domain)
        return ExtractedContent(url=url, error=f"auth: no browser context for {domain}")

    status_code = 0
    page = None
    try:
        page = await context.new_page()
        try:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=AUTH_TIMEOUT_MS,
            )
            status_code = response.status if response else 0
            final_url = page.url
            if final_url and final_url != url:
                safe, reason = is_safe_url(final_url)
                if not safe:
                    logger.warning("Auth extraction blocked unsafe redirect for %s: %s", url[:60], reason)
                    record_auth_request(domain, success=False, status_code=status_code)
                    return None

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
                url=final_url or url,
                title=title,
                text=extracted,
                word_count=word_count,
                extractor=ExtractorName.AUTH,
            )

        finally:
            await page.close()

    except asyncio.CancelledError:
        await _close_browser_resources()
        raise
    except Exception as e:
        logger.warning("Auth extraction failed for %s: %s", url[:60], e)
        record_auth_request(domain, success=False, status_code=status_code)
        error_text = str(e).lower()
        if any(
            marker in type(e).__name__.lower() or marker in error_text
            for marker in (
                "memory",
                "oom",
                "crash",
                "targetclosed",
                "browser has been closed",
                "browser closed",
            )
        ):
            await _close_browser_resources()
        return None


def _extract_from_html(html: str) -> str:
    """Extract clean text from rendered HTML using trafilatura."""
    import trafilatura

    result = normalize_trafilatura_result(trafilatura.bare_extraction(html))
    return result.text if result is not None else ""
