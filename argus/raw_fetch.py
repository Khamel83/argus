"""Bounded, browser-rendered raw fetches for authenticated compatibility callers."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import inspect
import json
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse

from tld import get_fld

from argus.api.schemas import FetchRawRequest, FetchRawResponse
from argus.extraction.playwright_extractor import _get_browser
from argus.extraction.ssrf import is_safe_url

_MAX_RESPONSE_BYTES = 5_000_000
_SETTLE_WINDOW_MS = 1500
_CLEANUP_TIMEOUT_SECONDS = 0.5
_DNS_TIMEOUT_SECONDS = 2.0
_ALLOWED_TARGET_SITES = {"seatgeek.com", "example.test"}
_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _actual_egress() -> str:
    value = os.environ.get("ARGUS_EGRESS_TYPE", "unknown").strip().lower()
    return value if value in {"residential", "datacenter"} else "unknown"


def _is_non_network_browser_url(url: str) -> bool:
    return urlparse(url).scheme.lower() in {"about", "blob", "data"}


def _site(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return ""
    try:
        return get_fld(hostname, fix_protocol=True) or hostname
    except Exception:
        # Local/test suffixes may not be in the public-suffix data.  They still
        # need a bounded same-site comparison rather than a cross-site match.
        labels = hostname.split(".")
        return ".".join(labels[-2:]) if len(labels) > 1 else hostname


def _contains_listing_or_inventory(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if "listing" in normalized or "inventory" in normalized:
                return True
            if _contains_listing_or_inventory(child):
                return True
    elif isinstance(value, list):
        return any(_contains_listing_or_inventory(item) for item in value)
    return False


async def _safe_network_url(url: str, source_site: str) -> tuple[bool, str]:
    """Validate DNS off-loop and confine browser traffic to the caller's site."""
    if _site(url) != source_site or source_site not in _ALLOWED_TARGET_SITES:
        return False, "cross-site host blocked"
    try:
        safe, reason = await asyncio.wait_for(
            asyncio.to_thread(is_safe_url, url),
            timeout=_DNS_TIMEOUT_SECONDS,
        )
        if not safe:
            return safe, reason
        if source_site == "example.test":
            # IANA-reserved fixture site; it cannot resolve in normal operation.
            return True, ""
        parsed = urlparse(url)
        resolved = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
            ),
            timeout=_DNS_TIMEOUT_SECONDS,
        )
        for _family, _socktype, _proto, _canonname, sockaddr in resolved:
            try:
                address = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                return False, "DNS returned an invalid address"
            if not address.is_global:
                return False, f"non-public IP blocked:{address}"
        return True, ""
    except socket.gaierror:
        return False, "DNS resolution failed"
    except TimeoutError:
        return False, "DNS validation timed out"


async def _inventory_json_body(responses: list[Any], source_url: str) -> str | None:
    source_site = _site(source_url)
    for response in responses:
        content_type = (getattr(response, "headers", {}) or {}).get("content-type", "")
        response_url = getattr(response, "url", "")
        status = getattr(response, "status", 0)
        if (
            not source_site
            or _site(response_url) != source_site
            or not 200 <= status < 300
            or "json" not in content_type.lower()
        ):
            continue
        try:
            body = await response.body()
        except Exception:
            continue
        if not body or len(body) > _MAX_RESPONSE_BYTES:
            continue
        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if _contains_listing_or_inventory(payload):
            return text
    return None


def _error(
    error: str,
    *,
    http_status: int | None,
    final_url: str = "",
    elapsed_ms: int = 0,
) -> FetchRawResponse:
    return FetchRawResponse(
        status="error",
        http_status=http_status,
        final_url=final_url,
        egress_used=_actual_egress(),
        elapsed_ms=elapsed_ms,
        error=error,
    )


def _unsafe_request_error(
    blocked_requests: list[tuple[str, str, str]],
    *,
    final_url: str = "",
) -> FetchRawResponse | None:
    if not blocked_requests:
        return None
    resource_type, _url, reason = blocked_requests[0]
    code = "unsafe_redirect" if resource_type == "document" else "unsafe_subresource"
    return _error(f"{code}:{reason}", http_status=400, final_url=final_url)


async def _fetch_raw_inner(
    request: FetchRawRequest,
    *,
    started: float,
) -> FetchRawResponse:
    source_site = _site(request.url)
    safe, reason = await _safe_network_url(request.url, source_site)
    if not safe:
        return _error(f"unsafe_url:{reason}", http_status=400)

    actual_egress = _actual_egress()
    if request.egress != "unknown" and request.egress != actual_egress:
        return _error(
            (f"egress_unavailable:requested={request.egress}:actual={actual_egress}"),
            http_status=503,
        )

    browser = await _get_browser()
    if browser is None:
        return _error("browser_unavailable", http_status=503)

    context = None
    page = None
    responses: list[Any] = []
    blocked_requests: list[tuple[str, str, str]] = []
    try:
        context_options: dict[str, Any] = {
            "user_agent": _CHROME_USER_AGENT,
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-US",
            "service_workers": "block",
        }
        if request.headers:
            context_options["extra_http_headers"] = request.headers
        context = await browser.new_context(**context_options)

        async def guard_public_requests(route) -> None:
            request_url = route.request.url
            resource_type = getattr(route.request, "resource_type", "other")
            if _is_non_network_browser_url(request_url):
                await route.continue_()
                return
            request_safe, request_reason = await _safe_network_url(
                request_url, source_site
            )
            if not request_safe:
                blocked_requests.append((resource_type, request_url, request_reason))
                await route.abort()
                return
            await route.continue_()

        await context.route("**/*", guard_public_requests)

        async def block_web_socket(web_socket) -> None:
            blocked_requests.append(("websocket", web_socket.url, "websocket blocked"))
            await web_socket.close(code=1008, reason="network policy")

        route_web_socket = getattr(context, "route_web_socket", None)
        if not callable(route_web_socket):
            return _error(
                "browser_capability_missing:route_web_socket",
                http_status=503,
            )
        installed = route_web_socket("**/*", block_web_socket)
        if inspect.isawaitable(installed):
            await installed
        page = await context.new_page()
        page.on("response", responses.append)
        timeout_ms = request.timeout_seconds * 1000
        try:
            document = await page.goto(
                request.url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
        except Exception:
            blocked_error = _unsafe_request_error(blocked_requests)
            if blocked_error is not None:
                return blocked_error
            raise

        blocked_error = _unsafe_request_error(blocked_requests)
        if blocked_error is not None:
            return blocked_error
        if document is None:
            return _error("upstream_response_missing", http_status=502)

        final_url = getattr(page, "url", "") or getattr(document, "url", request.url)
        safe, reason = await _safe_network_url(final_url, source_site)
        if not safe:
            return _error(
                f"unsafe_redirect:{reason}",
                http_status=400,
                final_url=final_url,
            )

        # Keep a real bounded capture window open for timer-triggered inventory
        # XHRs. ``networkidle`` may return early and is not a minimum wait.
        await asyncio.sleep(_SETTLE_WINDOW_MS / 1000)

        blocked_error = _unsafe_request_error(
            blocked_requests,
            final_url=final_url,
        )
        if blocked_error is not None:
            return blocked_error

        document_status = getattr(document, "status", None)
        if not isinstance(document_status, int):
            return _error(
                "upstream_status_unavailable", http_status=502, final_url=final_url
            )
        if document_status < 200 or document_status >= 300:
            return _error(
                f"upstream_status:{document_status}",
                http_status=document_status,
                final_url=final_url,
            )

        body = await _inventory_json_body(responses, final_url)
        extractor = "same_site_json" if body is not None else "raw_html"
        if body is None:
            body = await page.content()
        blocked_error = _unsafe_request_error(
            blocked_requests,
            final_url=final_url,
        )
        if blocked_error is not None:
            return blocked_error
        if not body or not body.strip():
            return _error("empty_body", http_status=502, final_url=final_url)
        if len(body.encode("utf-8")) > _MAX_RESPONSE_BYTES:
            return _error("body_too_large", http_status=502, final_url=final_url)

        return FetchRawResponse(
            status="ok",
            http_status=document_status,
            body=body,
            final_url=final_url,
            body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            render_mode_used="browser",
            extractor_used=extractor,
            egress_used=actual_egress,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            from_cache=False,
        )
    except Exception as exc:
        if (
            isinstance(exc, asyncio.TimeoutError)
            or type(exc).__name__ == "TimeoutError"
        ):
            return _error(
                "browser_timeout",
                http_status=504,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        return _error(
            f"browser_error:{type(exc).__name__}",
            http_status=502,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        if page is not None:
            try:
                await asyncio.wait_for(page.close(), timeout=_CLEANUP_TIMEOUT_SECONDS)
            except Exception:
                pass
        if context is not None:
            try:
                await asyncio.wait_for(
                    context.close(), timeout=_CLEANUP_TIMEOUT_SECONDS
                )
            except Exception:
                pass


async def fetch_raw(request: FetchRawRequest) -> FetchRawResponse:
    """Navigate with Argus's managed browser without entering the extractor chain."""
    started = time.monotonic()
    try:
        async with asyncio.timeout(request.timeout_seconds):
            return await _fetch_raw_inner(request, started=started)
    except TimeoutError:
        return _error(
            "browser_timeout",
            http_status=504,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        return _error(
            f"browser_error:{type(exc).__name__}",
            http_status=502,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
