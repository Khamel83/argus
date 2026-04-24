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

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.obscura")

_OBSCURA_TIMEOUT = int(os.getenv("ARGUS_OBSCURA_TIMEOUT_SECONDS", "20"))

# Cached availability check — only runs `which` once per process
_obscura_available: Optional[bool] = None


def _is_available() -> bool:
    global _obscura_available
    if _obscura_available is None:
        _obscura_available = shutil.which("obscura") is not None
        if not _obscura_available:
            logger.debug("obscura binary not found — Obscura CLI extraction disabled")
    return _obscura_available


async def extract_obscura(url: str) -> ExtractedContent:
    """Extract content using Obscura headless browser (stealth mode, subprocess)."""
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
