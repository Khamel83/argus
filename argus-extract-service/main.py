"""
Standalone authenticated extraction service for Mac Mini.

Runs Playwright with real Chrome + stealth patches + cookies from the
local browser, extracts article text via readability-lxml.

Called by Argus (on OCI) over Tailscale.

Endpoints:
    POST /extract  {url, domain} -> {title, text, word_count, status_code}
    POST /login    {domain}      -> {success, cookie_count} or {success, message}
    GET  /health   -> {status, cookie_domains}
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# --- Config ---

SERVICE_KEY = os.getenv("ARGUS_EXTRACT_SERVICE_KEY", "")
COOKIE_DIR = Path(os.getenv("ARGUS_COOKIE_DIR", "~/.config/argus/cookies")).expanduser()
AUTH_TIMEOUT_MS = int(os.getenv("ARGUS_EXTRACT_TIMEOUT_MS", "30000"))
RENDER_WAIT_MS = int(os.getenv("ARGUS_RENDER_WAIT_MS", "5000"))
MIN_CONTENT_CHARS = 200

# Real Chrome UA from the Mac Mini's installed Chrome
CHROME_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"

app = FastAPI(title="Argus Extract Service")

# --- Lazy Playwright (real Chrome + stealth) ---

_stealth_cm = None
_pw = None
_browser = None


async def _ensure_browser():
    """Lazily launch Playwright with real Chrome + stealth patches.

    Uses manual __aenter__/__aexit__ since the service needs the browser
    to persist across requests (can't use async with in a lazy-init pattern).
    """
    global _stealth_cm, _pw, _browser
    if _browser is not None:
        try:
            if _browser.is_connected():
                return _browser
        except Exception:
            pass
        await _shutdown()
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth

        stealth = Stealth()
        _stealth_cm = stealth.use_async(async_playwright())
        _pw = await _stealth_cm.__aenter__()
        _browser = await _pw.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-gpu", "--disable-blink-features=AutomationControlled"],
        )
        return _browser
    except Exception as e:
        _stealth_cm = _pw = _browser = None
        raise RuntimeError(f"Failed to launch Playwright: {e}")


async def _shutdown():
    global _stealth_cm, _pw, _browser
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _stealth_cm:
        try:
            await _stealth_cm.__aexit__(None, None, None)
        except Exception:
            pass
        _stealth_cm = None
    _pw = None


# --- Cookie loading (duplicated from argus to keep service standalone) ---

def _load_cookies(domain: str) -> list[dict]:
    """Load and sanitize EditThisCookie JSON for Playwright."""
    path = COOKIE_DIR / f"{domain}.json"
    if not path.exists():
        parts = domain.split(".")
        if len(parts) > 2:
            parent = ".".join(parts[-2:])
            path = COOKIE_DIR / f"{parent}.json"
    if not path.exists():
        return []

    with open(path) as f:
        raw = json.load(f)

    if isinstance(raw, dict):
        raw = raw.get("cookies", [raw])

    cookies = []
    for c in raw:
        if not c.get("name") or not c.get("value"):
            continue
        entry = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
        }
        if c.get("secure"):
            entry["secure"] = True
        if c.get("httpOnly"):
            entry["httpOnly"] = True
        if c.get("sameSite"):
            ss = c["sameSite"]
            if ss in ("Strict", "Lax", "None", "no_restriction", "unspecified"):
                entry["sameSite"] = "None" if ss == "no_restriction" else ("Lax" if ss == "unspecified" else ss)
            # Skip invalid sameSite values silently (Playwright rejects them)
        if c.get("expirationDate") and c["expirationDate"] > time.time():
            entry["expires"] = c["expirationDate"]
        cookies.append(entry)

    return cookies


def _list_cookie_domains() -> list[str]:
    """List domains that have cookie files."""
    if not COOKIE_DIR.exists():
        return []
    return sorted(p.stem for p in COOKIE_DIR.glob("*.json") if p.stem != "health")


# --- Credential loading ---

CREDENTIALS_PATH = Path("~/.config/argus/credentials.json").expanduser()


def _load_credentials(domain: str) -> Optional[dict]:
    """Load credentials for a domain from ~/.config/argus/credentials.json."""
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        with open(CREDENTIALS_PATH) as f:
            creds = json.load(f)
        return creds.get(domain)
    except Exception:
        return None


# --- Cookie saving helper ---

def _save_cookies(domain: str, cookies: list[dict]) -> None:
    """Save cookies for a domain to ~/.config/argus/cookies/{domain}.json."""
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    path = COOKIE_DIR / f"{domain}.json"
    with open(path, "w") as f:
        json.dump(cookies, f, indent=2)
    os.chmod(path, 0o600)


# --- Per-domain login flows ---

LOGIN_FLOWS = {
    "nytimes.com": {
        "login_url": "https://myaccount.nytimes.com/auth/login",
        "steps": [
            {"selector": 'input[data-testid="login-email-input"], input[name="email"], input[type="email"]', "value": "{email}"},
            {"click": 'button[type="submit"], button[data-testid="submit-email"]'},
            {"wait_ms": 1500},
            {"selector": 'input[data-testid="login-password-input"], input[name="password"], input[type="password"]', "value": "{password}"},
            {"click": 'button[type="submit"], button[data-testid="submit-password"]'},
        ],
        "success_check": "myaccount.nytimes.com",
    },
    "wsj.com": {
        "login_url": "https://accounts.wsj.com/login",
        "steps": [
            {"selector": 'input[name="username"], input[type="email"], #username', "value": "{email}"},
            {"selector": 'input[name="password"], input[type="password"], #password', "value": "{password}"},
            {"click": 'button[type="submit"], #submit-button'},
        ],
        "success_check": "wsj.com",
    },
    "espn.com": {
        "login_url": "https://www.espn.com/login",
        "steps": [
            {"wait_selector": 'input[name="email"], input[placeholder*="email" i]', "timeout_ms": 8000},
            {"selector": 'input[name="email"], input[placeholder*="email" i]', "value": "{email}"},
            {"selector": 'input[name="password"], input[type="password"]', "value": "{password}"},
            {"click": 'button[type="submit"], [data-testid="btn-login"]'},
        ],
        "success_check": "espn.com",
    },
    "latimes.com": {
        "login_url": "https://www.latimes.com/account/login",
        "steps": [
            {"selector": 'input[name="email"], input[type="email"]', "value": "{email}"},
            {"selector": 'input[name="password"], input[type="password"]', "value": "{password}"},
            {"click": 'button[type="submit"]'},
        ],
        "success_check": "latimes.com",
    },
    "chicagotribune.com": {
        "login_url": "https://www.chicagotribune.com/account/login",
        "steps": [
            {"selector": 'input[name="email"], input[type="email"]', "value": "{email}"},
            {"selector": 'input[name="password"], input[type="password"]', "value": "{password}"},
            {"click": 'button[type="submit"]'},
        ],
        "success_check": "chicagotribune.com",
    },
}


# --- Login executor ---

async def _run_login_flow(domain: str, creds: dict) -> dict:
    """Execute the login flow for a domain and save resulting cookies."""
    flow = LOGIN_FLOWS[domain]
    browser = await _ensure_browser()
    context = await browser.new_context(
        user_agent=CHROME_UA,
        viewport={"width": 1440, "height": 900},
        locale="en-US,en",
    )
    try:
        page = await context.new_page()
        try:
            logger.info("Login flow for %s: navigating to %s", domain, flow["login_url"])
            await page.goto(flow["login_url"], wait_until="domcontentloaded", timeout=AUTH_TIMEOUT_MS)

            for step in flow["steps"]:
                if "selector" in step and "value" in step:
                    step_type = "fill"
                    logger.info("Login flow for %s: step %s", domain, step_type)
                    value = step["value"].format(**creds)
                    selectors = [s.strip() for s in step["selector"].split(",")]
                    filled = False
                    for sel in selectors:
                        try:
                            await page.wait_for_selector(sel, timeout=5000)
                            await page.fill(sel, value)
                            filled = True
                            break
                        except Exception:
                            continue
                    if not filled:
                        logger.info("Login flow for %s: no selector matched for fill step", domain)

                elif "click" in step:
                    step_type = "click"
                    logger.info("Login flow for %s: step %s", domain, step_type)
                    selectors = [s.strip() for s in step["click"].split(",")]
                    clicked = False
                    for sel in selectors:
                        try:
                            await page.wait_for_selector(sel, timeout=5000)
                            await page.click(sel)
                            clicked = True
                            break
                        except Exception:
                            continue
                    if not clicked:
                        logger.info("Login flow for %s: no selector matched for click step", domain)

                elif "wait_ms" in step:
                    step_type = "wait_ms"
                    logger.info("Login flow for %s: step %s", domain, step_type)
                    await page.wait_for_timeout(step["wait_ms"])

                elif "wait_selector" in step:
                    step_type = "wait_selector"
                    logger.info("Login flow for %s: step %s", domain, step_type)
                    timeout_ms = step.get("timeout_ms", 5000)
                    selectors = [s.strip() for s in step["wait_selector"].split(",")]
                    for sel in selectors:
                        try:
                            await page.wait_for_selector(sel, timeout=timeout_ms)
                            break
                        except Exception:
                            continue

            # Wait for redirect/session establishment
            await page.wait_for_timeout(3000)

            current_url = page.url
            all_cookies = await context.cookies()
            base_domain = domain.lstrip(".")
            domain_cookies = [
                c for c in all_cookies
                if c.get("domain", "").lstrip(".").endswith(base_domain)
            ]

            success_check = flow.get("success_check", "")
            if len(domain_cookies) >= 3 and success_check in current_url:
                _save_cookies(domain, domain_cookies)
                logger.info("Login flow for %s: success, %d cookies saved", domain, len(domain_cookies))
                return {"success": True, "cookie_count": len(domain_cookies)}
            else:
                logger.info(
                    "Login flow for %s: may have failed — url=%s cookies=%d",
                    domain, current_url, len(domain_cookies),
                )
                return {"success": False, "message": "login may have failed — check credentials or CAPTCHA"}
        finally:
            await page.close()
    finally:
        await context.close()


# --- Text extraction ---

def _extract_text(html: str) -> str:
    """Extract clean text from rendered HTML using readability-lxml."""
    from readability import Document
    doc = Document(html)
    summary = doc.summary()
    if summary:
        return re.sub(r"<[^>]+>", "", summary).strip()
    return ""


# --- Auth middleware ---

@app.middleware("http")
async def auth_check(request: Request, call_next):
    if not SERVICE_KEY or request.url.path == "/health":
        return await call_next(request)
    provided = request.headers.get("x-api-key")
    if provided != SERVICE_KEY:
        return JSONResponse(status_code=401, content={"error": "Invalid or missing API key"})
    return await call_next(request)


# --- Endpoints ---

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "cookie_domains": _list_cookie_domains(),
    }


@app.post("/extract")
async def extract(request: Request):
    body = await request.json()
    url = body.get("url", "")
    domain = body.get("domain", "")

    if not url or not domain:
        return JSONResponse(status_code=400, content={"error": "url and domain required"})

    cookies = _load_cookies(domain)
    if not cookies:
        return JSONResponse(status_code=404, content={"error": f"no cookies for {domain}"})

    try:
        browser = await _ensure_browser()
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})

    context = await browser.new_context(
        user_agent=CHROME_UA,
        viewport={"width": 1440, "height": 900},
        locale="en-US,en",
    )

    # playwright-stealth handles JS-level evasion (webdriver, plugins, languages, etc.)
    # automatically via the context manager wrapping

    await context.add_cookies(cookies)

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
                return JSONResponse(
                    status_code=200,
                    content={"error": f"auth failed (HTTP {status_code})", "status_code": status_code},
                )

            # Wait for JS to render (paywall dismissal, content loading)
            await page.wait_for_timeout(RENDER_WAIT_MS)

            # Scroll down to trigger lazy content loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await page.wait_for_timeout(1000)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)

            html = await page.content()

            if not html:
                return JSONResponse(
                    status_code=200,
                    content={"error": "empty page", "status_code": status_code or 500},
                )

            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, _extract_text, html)

            if not text or len(text) < MIN_CONTENT_CHARS:
                return JSONResponse(
                    status_code=200,
                    content={"error": f"content too short ({len(text)} chars)", "status_code": status_code},
                )

            title = await page.title()
            word_count = len(text.split())

            return JSONResponse(content={
                "title": title,
                "text": text,
                "word_count": word_count,
                "status_code": status_code,
            })
        finally:
            await page.close()
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={"error": str(e), "status_code": status_code},
        )
    finally:
        await context.close()


@app.post("/login")
async def login(request: Request):
    body = await request.json()
    domain = body.get("domain", "")

    if not domain:
        return JSONResponse(status_code=400, content={"error": "domain required"})

    if domain not in LOGIN_FLOWS:
        return JSONResponse(status_code=404, content={"error": f"no login flow for {domain}"})

    creds = _load_credentials(domain)
    if not creds:
        return JSONResponse(status_code=404, content={"error": f"no credentials for {domain}"})

    try:
        await _ensure_browser()
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"error": str(e)})

    result = await _run_login_flow(domain, creds)
    return JSONResponse(content=result)


# --- Shutdown ---

@app.on_event("shutdown")
async def on_shutdown():
    await _shutdown()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8910)
