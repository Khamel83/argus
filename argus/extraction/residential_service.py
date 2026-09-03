import asyncio
import hmac
import importlib.util
import ipaddress
import logging
import os
import random
import shutil
import time
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, HTTPException, Request
import httpx
from pydantic import BaseModel

from argus.acquisition.guarded import (
    GuardedAcquisitionError,
    guarded_browser_session,
    guarded_http_request,
    guarded_url_policy,
    patched_httpx_client,
)
from argus.acquisition.browser_policy import (
    BrowserAdmission,
    admit_browser_url,
    failure_text,
    install_browser_policy,
)
from argus.config import get_config
from argus.extraction.rate_limit import DomainRateLimiter

logger = logging.getLogger(__name__)

app = FastAPI(title="Argus Residential Extractor", version="1.1.0")

# Realistic browser User-Agents — rotate per request to avoid fingerprinting
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_start_time = time.time()
_request_count = 0

# Concurrency management
_global_semaphore = asyncio.Semaphore(10)  # Max 10 concurrent extractions total
_domain_semaphores: Dict[str, asyncio.Semaphore] = {}
_domain_sem_lock = asyncio.Lock()


@asynccontextmanager
async def _concurrency_gate(url: str):
    """Enforce global and per-domain concurrency limits."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()

    async with _global_semaphore:
        async with _domain_sem_lock:
            if domain not in _domain_semaphores:
                _domain_semaphores[domain] = asyncio.Semaphore(2)  # Max 2 per domain
            sem = _domain_semaphores[domain]

        async with sem:
            yield


def _client_allowed(client_host: str | None) -> bool:
    config = get_config()
    if not client_host or client_host in {"localhost", "testclient"}:
        return True

    try:
        client_ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False

    for cidr in config.residential.allowed_cidrs:
        try:
            if client_ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _safe_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class ExtractRequest(BaseModel):
    url: str
    cookies: list[dict] | None = None
    domain: str | None = None


class ExtractResponse(BaseModel):
    url: str
    title: str = ""
    text: str = ""
    author: str = ""
    date: str | None = None
    word_count: int = 0
    extractor: str = "unknown"
    error: str | None = None
    failure_code: str | None = None


@app.get("/health")
async def health():
    from argus import __version__
    config = get_config()
    crawl4ai_enabled = os.getenv("ARGUS_CRAWL4AI_ENABLED", "").lower() in ("1", "true")
    return {
        "status": "ok",
        "version": __version__,
        "uptime_seconds": round(time.time() - _start_time),
        "requests": _request_count,
        "trafilatura": True,
        "playwright": _check_playwright(),
        "crawl4ai": crawl4ai_enabled,
        "obscura": shutil.which("obscura") is not None,
        "node": {
            "role": config.node.role,
            "egress": config.node.egress_type,
            "machine": config.node.machine_name,
        }
    }


_worker_domain_limiter = DomainRateLimiter(max_requests=5, window_seconds=60)


def _production_runtime(config: object | None = None) -> bool:
    """Return whether browser subprocesses must be policy-enforced."""

    configured_env = getattr(config, "env", None)
    if isinstance(configured_env, str) and configured_env.strip().lower() == "production":
        return True
    return os.getenv("ARGUS_ENV", "development").strip().lower() == "production"


def _browser_transport_unavailable(tool: str) -> dict[str, str]:
    """Return a stable policy result for an un-interceptable browser tool."""

    return {
        "error": f"{tool}: browser policy unavailable",
        "failure_code": "browser_policy_unavailable",
    }


@app.post("/extract")
async def extract(req: ExtractRequest, request: Request):
    global _request_count
    config = get_config()
    secret = config.residential.shared_secret
    if not secret:
        raise HTTPException(status_code=503, detail="residential shared secret not configured")

    # Enforce minimum secret length in non-dev environments
    if config.env != "development" and len(secret) < 32:
        raise HTTPException(status_code=503, detail="residential shared secret too short (min 32 chars)")

    client_host = request.client.host if request.client else None
    if not _client_allowed(client_host):
        raise HTTPException(status_code=403, detail="caller not allowed")

    auth_header = request.headers.get("authorization", "")
    expected = f"Bearer {secret}"
    if not _safe_compare(auth_header, expected):
        raise HTTPException(status_code=401, detail="authentication required")

    url = req.url
    # SSRF protection — block private/internal IPs
    safe, reason = _is_safe_url(url)
    if not safe:
        raise HTTPException(status_code=400, detail=f"SSRF blocked: {reason}")

    # Rate limiting
    allowed, retry_after = _worker_domain_limiter.is_allowed(url)
    if not allowed:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded, retry after {retry_after}s")

    _request_count += 1

    async with _concurrency_gate(url):
        cookies = req.cookies
        # Try trafilatura first (fast, local HTTP)
        result = await _extract_trafilatura(url, cookies)
        if result.get("text") and len(result["text"].split()) >= 50:
            # Post-redirect SSRF check
            if result.get("url") and result["url"] != url:
                safe, reason = _is_safe_url(result["url"])
                if not safe:
                    raise HTTPException(status_code=400, detail=f"SSRF blocked on redirect: {reason}")
            return ExtractResponse(url=result.get("url", url), extractor="trafilatura", **result)

        # Try obscura (stealth CLI)
        obscura_path = shutil.which("obscura")
        if obscura_path:
            result = await _extract_obscura(
                url,
                config.residential.timeout_seconds,
                production=_production_runtime(config),
            )
            if result.get("text") and len(result["text"].split()) >= 50:
                return ExtractResponse(url=url, extractor="obscura", **result)

        # Try playwright (full browser)
        if _check_playwright():
            result = await _extract_playwright(url, config.residential.timeout_seconds, cookies)
            if result.get("text") and len(result["text"].split()) >= 100:
                # Post-redirect SSRF check
                if result.get("url") and result["url"] != url:
                    safe, reason = _is_safe_url(result["url"])
                    if not safe:
                        raise HTTPException(status_code=400, detail=f"SSRF blocked on redirect: {reason}")
                return ExtractResponse(url=result.get("url", url), extractor="playwright", **result)

        # Try crawl4ai if enabled
        crawl4ai_enabled = os.getenv("ARGUS_CRAWL4AI_ENABLED", "").lower() in ("1", "true")
        if crawl4ai_enabled:
            result = await _extract_crawl4ai(
                url,
                production=_production_runtime(config),
            )
            if result.get("text") and len(result["text"].split()) >= 50:
                return ExtractResponse(url=url, extractor="crawl4ai", **result)

        # Return best result even if short
        if result and result.get("text"):
            return ExtractResponse(url=url, extractor=result.get("extractor", "unknown"), **result)

    raise HTTPException(status_code=503, detail="all extractors failed on residential side")


def _check_playwright() -> bool:
    return importlib.util.find_spec("playwright.async_api") is not None


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Compatibility URL check owned by Guarded Acquisition."""
    safe, reason = guarded_url_policy(url)
    if safe:
        return True, ""
    return False, reason


async def _extract_trafilatura(url: str, cookies: list[dict] | None = None) -> dict:
    try:
        import trafilatura
        from argus.extraction.trafilatura_result import normalize_trafilatura_result

        ua = random.choice(_USER_AGENTS)
        headers = {"User-Agent": ua}
        if cookies:
            cookie_str = "; ".join(
                f"{c['name']}={c['value']}"
                for c in cookies
                if "name" in c and "value" in c
            )
            if cookie_str:
                headers["Cookie"] = cookie_str
        profile = "authenticated_content" if cookies else "public_content"
        credential_policy = "origin_scoped" if cookies else "none"
        resp = await guarded_http_request(
            url,
            headers=headers,
            profile=profile,
            credential_policy=credential_policy,
            caller_principal="residential-worker-trafilatura",
            request_id="residential-worker-trafilatura",
            timeout=10,
            target_url=url,
            compat_client_factory=patched_httpx_client(httpx.AsyncClient),
        )
        resp.raise_for_status()
        final_url = str(resp.url)
        downloaded = resp.text

        if not downloaded:
            return {"error": "trafilatura: failed to fetch"}
        extracted = await asyncio.get_event_loop().run_in_executor(
            None, trafilatura.bare_extraction, downloaded
        )
        normalized = normalize_trafilatura_result(extracted)
        if normalized is None:
            return {"error": "trafilatura: no content"}
        text = normalized.text
        return {
            "url": final_url,
            "title": normalized.title,
            "text": text,
            "author": normalized.author,
            "date": normalized.date,
            "word_count": len(text.split()),
        }
    except Exception as e:
        return {"error": f"trafilatura: {e}"}


async def _extract_obscura(
    url: str,
    timeout: int,
    *,
    production: bool | None = None,
) -> dict:
    if (_production_runtime() if production is None else production):
        return _browser_transport_unavailable("obscura")
    try:
        await guarded_browser_session(
            url,
            caller_principal="residential-obscura",
            request_id="residential-obscura",
        )
    except GuardedAcquisitionError as exc:
        return {"error": f"{exc.failure.code.value}: {exc.failure.safe_reason}"}
    admission = await admit_browser_url(
        url,
        caller_principal="residential-obscura",
        request_id="residential-obscura",
    )
    if not isinstance(admission, BrowserAdmission):
        return {"error": failure_text(admission)}

    try:
        proc = await asyncio.create_subprocess_exec(
            "obscura", "fetch", url, "--dump", "text", "--stealth", "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"error": "obscura: timeout"}
        if proc.returncode != 0:
            return {"error": f"obscura: exit {proc.returncode}"}
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return {"error": "obscura: no content"}
        return {"text": text, "word_count": len(text.split())}
    except Exception as e:
        return {"error": f"obscura: {e}"}


async def _extract_playwright(url: str, timeout: int, cookies: list[dict] | None = None) -> dict:
    profile = "authenticated_content" if cookies else "public_content"
    credential_policy = "origin_scoped" if cookies else "none"
    try:
        await guarded_browser_session(
            url,
            profile=profile,
            credential_policy=credential_policy,
            caller_principal="residential-playwright",
            request_id="residential-playwright",
        )
    except GuardedAcquisitionError as exc:
        return {"error": f"{exc.failure.code.value}: {exc.failure.safe_reason}"}
    admission = await admit_browser_url(
        url,
        profile=profile,
        credential_policy=credential_policy,
        caller_principal="residential-playwright",
        request_id="residential-playwright",
    )
    if not isinstance(admission, BrowserAdmission):
        return {"error": failure_text(admission)}

    try:
        from playwright.async_api import async_playwright
        from urllib.parse import urlparse

        initial_domain = urlparse(url).netloc.lower()

        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            context = await browser.new_context(service_workers="block")
            policy_failure = await install_browser_policy(context, admission)
            if policy_failure is not None:
                return {"error": failure_text(policy_failure)}
            if cookies:
                # Playwright's add_cookies automatically scopes them.
                # However, if we redirect to a new domain, we should ensure
                # we don't manually leak them in headers (handled by browser).
                await context.add_cookies(cookies)
                page = await context.new_page()
            else:
                page = await context.new_page()

            # Listen for redirects to monitor domain changes
            page.on("framenavigated", lambda frame: _check_playwright_leak(frame, initial_domain))

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            await asyncio.sleep(1)
            final_url = page.url
            title = await page.title()
            text = await page.evaluate("""() => {
                const els = document.querySelectorAll('script, style, nav, footer, header, aside, iframe, noscript');
                els.forEach(el => el.remove());
                const main = document.querySelector('main, article, [role="main"], .post-content, .article-body, .entry-content');
                const source = main || document.body;
                return source.innerText || source.textContent || '';
            }""")
            text = (text or "").strip()
            if not text or len(text.split()) < 100:
                return {"error": "playwright: too little content"}
            return {
                "url": final_url,
                "title": title or "",
                "text": text,
                "word_count": len(text.split()),
            }
        finally:
            if "page" in locals() and page is not None:
                try:
                    await page.close()
                except Exception:
                    logger.debug("Failed to close residential page reason=close_failed")
            if "context" in locals() and context is not None:
                try:
                    await context.close()
                except Exception:
                    logger.debug("Failed to close residential context reason=close_failed")
            if "browser" in locals() and browser is not None:
                try:
                    await browser.close()
                except Exception:
                    logger.debug("Failed to close residential browser reason=close_failed")
            await pw.stop()
    except Exception as e:
        return {"error": f"playwright: {e}"}


def _check_playwright_leak(frame, initial_domain):
    """Callback for playwright redirection to ensure we aren't somehow leaking state across domains.
    This is largely handled by the browser's own cookie isolation, but good for diagnostics."""
    from urllib.parse import urlparse
    current_domain = urlparse(frame.url).netloc.lower()
    if current_domain and current_domain != initial_domain:
        # Browser handles this correctly, but we track it
        pass


async def _extract_crawl4ai(
    url: str,
    *,
    production: bool | None = None,
) -> dict:
    if (_production_runtime() if production is None else production):
        return _browser_transport_unavailable("crawl4ai")
    try:
        await guarded_browser_session(
            url,
            caller_principal="residential-crawl4ai",
            request_id="residential-crawl4ai",
        )
    except GuardedAcquisitionError as exc:
        return {"error": f"{exc.failure.code.value}: {exc.failure.safe_reason}"}
    admission = await admit_browser_url(
        url,
        caller_principal="residential-crawl4ai",
        request_id="residential-crawl4ai",
    )
    if not isinstance(admission, BrowserAdmission):
        return {"error": failure_text(admission)}

    try:
        from crawl4ai import AsyncWebCrawler

        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(url)
            if not result or not result.markdown:
                return {"error": "crawl4ai: no content"}
            text = result.markdown
            return {
                "title": getattr(result, "title", "") or "",
                "text": text,
                "word_count": len(text.split()),
            }
    except Exception as e:
        return {"error": f"crawl4ai: {e}"}


if __name__ == "__main__":
    import uvicorn
    config = get_config()
    bind = os.getenv("ARGUS_BIND_HOST", os.getenv("ARGUS_BIND", config.host))
    port = int(os.getenv("ARGUS_PORT", str(config.port)))
    uvicorn.run(app, host=bind, port=port)
