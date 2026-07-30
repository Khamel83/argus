"""Bounded, browser-rendered raw fetches for authenticated compatibility callers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Any
from urllib.parse import urlparse

from tld import get_fld

from argus.api.schemas import FetchRawRequest, FetchRawResponse
from argus.extraction.playwright_extractor import _get_browser
from argus.extraction.ssrf import is_safe_url

_MAX_RESPONSE_BYTES = 5_000_000


def _actual_egress() -> str:
    value = os.environ.get("ARGUS_EGRESS_TYPE", "unknown").strip().lower()
    return value if value in {"residential", "datacenter"} else "unknown"


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
        egress=_actual_egress(),
        elapsed_ms=elapsed_ms,
        error=error,
    )


async def fetch_raw(request: FetchRawRequest) -> FetchRawResponse:
    """Navigate with Argus's managed browser without entering the extractor chain."""
    started = time.monotonic()
    safe, reason = is_safe_url(request.url)
    if not safe:
        return _error(f"unsafe_url:{reason}", http_status=400)

    browser = await _get_browser()
    if browser is None:
        return _error("browser_unavailable", http_status=503)

    context = None
    page = None
    responses: list[Any] = []
    try:
        context_options = (
            {"extra_http_headers": request.headers} if request.headers else {}
        )
        context = await browser.new_context(**context_options)
        page = await context.new_page()
        page.on("response", responses.append)
        timeout_ms = request.timeout_seconds * 1000
        document = await page.goto(
            request.url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        if document is None:
            return _error("upstream_response_missing", http_status=502)

        final_url = getattr(page, "url", "") or getattr(document, "url", request.url)
        safe, reason = is_safe_url(final_url)
        if not safe:
            return _error(
                f"unsafe_redirect:{reason}",
                http_status=400,
                final_url=final_url,
            )

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
        if not body or not body.strip():
            return _error("empty_body", http_status=502, final_url=final_url)
        if len(body.encode("utf-8")) > _MAX_RESPONSE_BYTES:
            return _error("body_too_large", http_status=502, final_url=final_url)

        return FetchRawResponse(
            status="ok",
            http_status=document_status,
            body=body,
            final_url=final_url,
            sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            render="browser",
            extractor=extractor,
            egress=_actual_egress(),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            from_cache=False,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if (
            isinstance(exc, asyncio.TimeoutError)
            or type(exc).__name__ == "TimeoutError"
        ):
            return _error("browser_timeout", http_status=504, elapsed_ms=elapsed_ms)
        return _error(
            f"browser_error:{type(exc).__name__}",
            http_status=502,
            elapsed_ms=elapsed_ms,
        )
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
