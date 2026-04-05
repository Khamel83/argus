"""
Cookie management for authenticated extraction.

Loads EditThisCookie JSON exports, sanitizes for Playwright,
tracks health, and enforces rate limits per domain.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from argus.logging import get_logger

logger = get_logger("extraction.cookies")

COOKIE_DIR = Path(os.getenv("ARGUS_COOKIE_DIR", "~/.config/argus/cookies")).expanduser()
HEALTH_FILE = COOKIE_DIR / "health.json"

# Rate limit: max 1 authenticated request per 10s per domain
AUTH_RATE_LIMIT_SECONDS = int(os.getenv("ARGUS_AUTH_RATE_LIMIT", "10"))

# Domains that typically need authentication for full content
PAYWALL_DOMAINS = {
    "nytimes.com", "nyt.com", "nl.nytimes.com",
    "wsj.com",
    "bloomberg.com",
    "ft.com",
    "stratechery.com", "stratechery.passport.online",
    "newyorker.com",
    "theathletic.com",
    "theeconomist.com", "economist.com",
    "washingtonpost.com",
    "theinformation.com",
    "technologyreview.com",
    "platformer.news",
    "espn.com",
    "latimes.com",
    "chicagotribune.com",
}

# Track last request time per domain for rate limiting
_last_auth_request: dict[str, float] = {}


def _load_health() -> dict:
    """Load cookie health state from disk."""
    if HEALTH_FILE.exists():
        try:
            return json.loads(HEALTH_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cookie health: %s", e)
    return {}


def _save_health(health: dict) -> None:
    """Persist cookie health state to disk."""
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        HEALTH_FILE.write_text(json.dumps(health, indent=2))
    except OSError as e:
        logger.error("Failed to save cookie health: %s", e)


def get_cookie_path(domain: str) -> Optional[Path]:
    """Return the cookie file path for a domain, or None if no cookies."""
    path = COOKIE_DIR / f"{domain}.json"
    if path.exists():
        return path
    # Try stripping subdomains (e.g., nl.nytimes.com → nytimes.com)
    parts = domain.split(".")
    if len(parts) > 2:
        parent = ".".join(parts[-2:])
        parent_path = COOKIE_DIR / f"{parent}.json"
        if parent_path.exists():
            return parent_path
    return None


def needs_auth(url: str) -> bool:
    """Check if a URL's domain is known to need authentication."""
    from urllib.parse import urlparse

    hostname = urlparse(url).hostname or ""
    # Check exact match and parent domain match
    if hostname in PAYWALL_DOMAINS:
        return True
    parts = hostname.split(".")
    if len(parts) > 2:
        parent = ".".join(parts[-2:])
        if parent in PAYWALL_DOMAINS:
            return True
    return False


def can_authenticate(domain: str) -> bool:
    """Check if we have cookies and aren't rate-limited for this domain."""
    if get_cookie_path(domain) is None:
        return False

    health = _load_health()
    status = health.get(domain, {}).get("status", "healthy")
    if status == "stale":
        logger.info("Cookies for %s are stale, skipping auth", domain)
        return False

    # Rate limit check
    now = time.monotonic()
    last = _last_auth_request.get(domain, 0)
    if now - last < AUTH_RATE_LIMIT_SECONDS:
        return False

    return True


def record_auth_request(domain: str, success: bool, status_code: int = 0) -> None:
    """Record an authenticated request for health tracking and rate limiting."""
    _last_auth_request[domain] = time.monotonic()

    health = _load_health()
    entry = health.setdefault(domain, {
        "status": "healthy",
        "request_count": 0,
        "last_used": None,
        "cookies_loaded_at": None,
    })

    entry["request_count"] = entry.get("request_count", 0) + 1
    entry["last_used"] = datetime.now(timezone.utc).isoformat()
    entry["last_status_code"] = status_code

    if not success or status_code in (401, 403):
        entry["status"] = "stale"
        logger.warning(
            "Cookies for %s marked as stale (status=%d, success=%s)",
            domain, status_code, success,
        )
    else:
        entry["status"] = "healthy"

    _save_health(health)


def get_health_summary() -> dict:
    """Return cookie health status for all domains."""
    health = _load_health()
    now = datetime.now(timezone.utc)

    summary = {}
    for domain, data in health.items():
        cookie_path = get_cookie_path(domain)
        last_used = data.get("last_used")
        days_since = None
        if last_used:
            last_dt = datetime.fromisoformat(last_used)
            days_since = (now - last_dt).days

        summary[domain] = {
            "status": data.get("status", "unknown"),
            "request_count": data.get("request_count", 0),
            "last_used": last_used,
            "days_since_used": days_since,
            "has_cookies": cookie_path is not None,
            "stale_warning": days_since is not None and days_since > 30,
        }

    return summary


def load_editthiscookie_json(path: Path) -> list[dict]:
    """Load and sanitize cookies from an EditThisCookie JSON export for Playwright.

    EditThisCookie format: array of cookie objects with keys like:
    domain, name, value, path, httpOnly, secure, sameSite, expirationDate

    Playwright expects: list of dicts with name, value, domain, path, etc.
    """
    with open(path) as f:
        raw_cookies = json.load(f)

    if isinstance(raw_cookies, dict):
        # Some exports wrap in an object
        raw_cookies = raw_cookies.get("cookies", [raw_cookies])

    sanitized = []
    for cookie in raw_cookies:
        # Skip cookies without essential fields
        if not cookie.get("name") or not cookie.get("value"):
            continue

        c = {
            "name": cookie["name"],
            "value": cookie["value"],
            "domain": cookie.get("domain", ""),
            "path": cookie.get("path", "/"),
        }

        # Optional fields
        if cookie.get("secure"):
            c["secure"] = True
        if cookie.get("httpOnly"):
            c["httpOnly"] = True
        if cookie.get("sameSite"):
            # Playwright accepts "Strict", "Lax", "None"
            ss = cookie["sameSite"]
            if ss in ("Strict", "Lax", "None", "no_restriction", "unspecified"):
                c["sameSite"] = "None" if ss == "no_restriction" else ("Lax" if ss == "unspecified" else ss)
        if cookie.get("expirationDate"):
            # Only set expires if it's in the future
            exp = cookie["expirationDate"]
            if exp > time.time():
                c["expires"] = exp

        sanitized.append(c)

    logger.info("Loaded %d cookies from %s", len(sanitized), path.name)
    return sanitized
