"""
Residential extraction service — runs on homelab/macmini.

Exposes a single endpoint (POST /extract) that takes a URL and extracts content
using the local extraction chain on a residential IP. Designed to be deployed
as a systemd service — set and forget.

Usage:
    python -m argus.extraction.residential_service
    # or
    ARGUS_BIND_HOST=100.113.216.27 ARGUS_PORT=8123 python -m argus.extraction.residential_service

Endpoints:
    POST /extract  {"url": "https://..."}  → ExtractedContent JSON
    GET  /health                              → {"status": "ok", ...}
"""

import asyncio
import importlib.util
import ipaddress
import os
import random
import shutil
import socket
import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from argus.config import get_config

app = FastAPI(title="Argus Residential Extractor", version="1.0.0")

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
    extractor: str = ""
    error: str | None = None


@app.get("/health")
async def health():
    config = get_config()
    crawl4ai_enabled = os.getenv("ARGUS_CRAWL4AI_ENABLED", "").lower() in ("1", "true")
    return {
        "status": "ok",
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


from argus.extraction.rate_limit import DomainRateLimiter

_worker_domain_limiter = DomainRateLimiter(max_requests=5, window_seconds=60)


@app.post("/extract")
async def extract(req: ExtractRequest, request: Request):
    global _request_count
    config = get_config()
    secret = config.residential.shared_secret
    if not secret:
        raise HTTPException(status_code=503, detail="residential shared secret not configured")

    client_host = request.client.host if request.client else None
    if not _client_allowed(client_host):
        raise HTTPException(status_code=403, detail="caller not allowed")

    auth_header = request.headers.get("authorization", "")
    if auth_header != f"Bearer {secret}":
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

    # ... (rest of extract function logic should similarly check final URL)

    # Try obscura (stealth CLI)
    obscura_path = shutil.which("obscura")
    if obscura_path:
        result = await _extract_obscura(url, config.residential.timeout_seconds)
        if result.get("text") and len(result["text"].split()) >= 50:
            return ExtractResponse(url=url, extractor="obscura", **result)

    # Try playwright (full browser)
    if _check_playwright():
        result = await _extract_playwright(url, config.residential.timeout_seconds, cookies)
        if result.get("text") and len(result["text"].split()) >= 100:
            return ExtractResponse(url=url, extractor="playwright", **result)

    # Try crawl4ai if enabled
    crawl4ai_enabled = os.getenv("ARGUS_CRAWL4AI_ENABLED", "").lower() in ("1", "true")
    if crawl4ai_enabled:
        result = await _extract_crawl4ai(url)
        if result.get("text") and len(result["text"].split()) >= 50:
            return ExtractResponse(url=url, extractor="crawl4ai", **result)

    # Return best result even if short
    if result and result.get("text"):
        return ExtractResponse(url=url, extractor=result.get("extractor", "unknown"), **result)

    raise HTTPException(status_code=503, detail="all extractors failed on residential side")


def _check_playwright() -> bool:
    return importlib.util.find_spec("playwright.async_api") is not None


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Basic SSRF protection — block private/internal IPs. Standalone (no argus dependency)."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"blocked scheme: {parsed.scheme}"

    hostname = parsed.hostname
    if not hostname:
        return False, "no hostname"

    blocked_names = {"localhost", "metadata.google.internal", "metadata", "169.254.169.254"}
    if hostname.lower() in blocked_names:
        return False, f"blocked hostname: {hostname}"

    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return False, f"private IP: {hostname}"
    except ValueError:
        pass  # hostname, not IP — resolve and check
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for family, _, _, _, sockaddr in resolved[:2]:
                ip = ipaddress.ip_address(sockaddr[0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False, f"resolves to private IP: {sockaddr[0]}"
        except socket.gaierror:
            pass  # DNS failure — let the extractor handle it

    return True, ""


async def _extract_trafilatura(url: str, cookies: list[dict] | None = None) -> dict:
    try:
        import trafilatura
        import httpx
        loop = asyncio.get_event_loop()
        ua = random.choice(_USER_AGENTS)
        headers = {"User-Agent": ua}
        if cookies:
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies if "name" in c and "value" in c)
            if cookie_str:
                headers["Cookie"] = cookie_str

        # Get final URL
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            final_url = str(resp.url)
            downloaded = resp.text

        if not downloaded:
            return {"error": "trafilatura: failed to fetch"}
        extracted = await loop.run_in_executor(None, trafilatura.bare_extraction, downloaded)
        if not extracted or not extracted.get("text"):
            return {"error": "trafilatura: no content"}
        text = extracted["text"]
        return {
            "url": final_url,
            "title": extracted.get("title", ""),
            "text": text,
            "author": extracted.get("author", ""),
            "date": extracted.get("date"),
            "word_count": len(text.split()),
        }
    except Exception as e:
        return {"error": f"trafilatura: {e}"}


async def _extract_obscura(url: str, timeout: int) -> dict:
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
    try:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            if cookies:
                context = await browser.new_context()
                await context.add_cookies(cookies)
                page = await context.new_page()
            else:
                context = None
                page = await browser.new_page()
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
            if context:
                await context.close()
            await browser.close()
            if not text or len(text.split()) < 100:
                return {"error": "playwright: too little content"}
            return {
                "url": final_url,
                "title": title or "",
                "text": text,
                "word_count": len(text.split()),
            }
        finally:
            await pw.stop()
    except Exception as e:
        return {"error": f"playwright: {e}"}


async def _extract_crawl4ai(url: str) -> dict:
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
