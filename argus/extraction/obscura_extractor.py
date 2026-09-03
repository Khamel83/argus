"""
Obscura headless browser extraction — subprocess CLI, stealth mode.

Calls `obscura fetch <url> --dump text --stealth --quiet` as a subprocess.
Falls back silently if the binary is not installed.

Install: https://github.com/h4ckf0r0day/obscura/releases
"""

import asyncio
import os
import shutil
from typing import Optional

from argus.acquisition.guarded import GuardedAcquisitionError, guarded_browser_session
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.obscura")

_OBSCURA_TIMEOUT = int(os.getenv("ARGUS_OBSCURA_TIMEOUT_SECONDS", "20"))

# Cached availability check — only runs `which` once per process
_obscura_available: Optional[bool] = None


def _production_runtime() -> bool:
    """Return whether this process is a production execution authority."""

    return os.getenv("ARGUS_ENV", "development").strip().lower() == "production"


def _browser_transport_unavailable(url: str) -> ExtractedContent:
    """Return a typed failure when the CLI cannot prove network policy.

    Obscura's standalone CLI owns its sockets.  Argus cannot install the
    browser request interception policy on that process, so production must
    not treat a preflight admission as proof of guarded traffic.
    """

    from argus.acquisition.errors import AcquisitionFailure

    failure = AcquisitionFailure.browser_policy_unavailable(
        request_id="obscura-extract",
        reason="obscura browser transport cannot prove guarded network policy",
    )
    return ExtractedContent(
        url=url,
        error="obscura: browser policy unavailable",
        failure=failure,
    )


def _is_available() -> bool:
    global _obscura_available
    if _obscura_available is None:
        _obscura_available = shutil.which("obscura") is not None
        if not _obscura_available:
            logger.debug("obscura binary not found — Obscura CLI extraction disabled")
    return _obscura_available


async def extract_obscura(url: str) -> ExtractedContent:
    """Extract content using Obscura headless browser (stealth mode, subprocess)."""
    if _production_runtime():
        return _browser_transport_unavailable(url)
    try:
        await guarded_browser_session(
            url,
            caller_principal="obscura",
            request_id="obscura-extract",
        )
    except GuardedAcquisitionError as exc:
        return ExtractedContent(url=url, error=f"obscura: {exc.failure.code.value}")

    if not _is_available():
        return ExtractedContent(url=url, error="obscura: binary not found")

    try:
        proc = await asyncio.create_subprocess_exec(
            "obscura", "fetch", url,
            "--dump", "text",
            "--stealth",
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_OBSCURA_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ExtractedContent(url=url, error=f"obscura: timeout after {_OBSCURA_TIMEOUT}s")

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return ExtractedContent(url=url, error=f"obscura: exit {proc.returncode}: {err[:200]}")

        text = stdout.decode("utf-8", errors="replace").strip()
        if not text or len(text) < 100:
            return ExtractedContent(url=url, error="obscura: content too short")

        return ExtractedContent(
            url=url,
            text=text,
            word_count=len(text.split()),
            extractor=ExtractorName.OBSCURA,
        )
    except Exception as e:
        logger.debug("Obscura extraction failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"obscura: {e}")
