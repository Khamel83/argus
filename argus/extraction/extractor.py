"""
Content extraction: trafilatura primary, Jina Reader fallback.

Hybrid strategy: try trafilatura (local, fast, free) first.
If it returns empty or fails, fall back to Jina Reader API.
Results are cached in memory to avoid re-extracting the same URL.
"""

import asyncio
import os

import httpx

from argus.core.cache import TTLCache, extraction_cache_key
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.rate_limit import DomainRateLimiter
from argus.logging import get_logger

logger = get_logger("extraction")

DEFAULT_TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "10"))
JINA_READER_URL = "https://r.jina.ai/"
JINA_API_KEY = os.getenv("ARGUS_JINA_API_KEY", "")

# Shared cache instance — lives for the process lifetime
_cache = TTLCache(
    ttl_seconds=int(os.getenv("ARGUS_EXTRACTION_CACHE_TTL_HOURS", "168")) * 3600,
    key_fn=extraction_cache_key,
    skip_fn=lambda content: bool(content.error),
)

# Shared domain rate limiter — 10 requests per minute per domain
_domain_limiter = DomainRateLimiter(
    max_requests=int(os.getenv("ARGUS_EXTRACTION_DOMAIN_RATE_LIMIT", "10")),
    window_seconds=int(os.getenv("ARGUS_EXTRACTION_DOMAIN_WINDOW_SECONDS", "60")),
)

# Token tracking state
_jina_call_count = 0
_jina_accumulated_tokens = 0
_JINA_SYNC_INTERVAL = 10  # decrement balance every N Jina calls
_TOKENS_PER_WORD = 1.3  # rough estimate: ~1.3 tokens per word


async def _extract_trafilatura(url: str) -> ExtractedContent:
    """Extract content using trafilatura (local, no API call)."""
    import trafilatura

    loop = asyncio.get_event_loop()

    downloaded = await loop.run_in_executor(None, trafilatura.fetch_url, url)
    if not downloaded:
        return ExtractedContent(url=url, error="trafilatura: failed to fetch URL")

    extracted = await loop.run_in_executor(None, trafilatura.bare_extraction, downloaded)
    if not extracted or not extracted.get("text"):
        return ExtractedContent(url=url, error="trafilatura: no content extracted")

    text = extracted["text"]
    return ExtractedContent(
        url=url,
        title=extracted.get("title", ""),
        text=text,
        author=extracted.get("author", ""),
        date=extracted.get("date"),
        word_count=len(text.split()),
        extractor=ExtractorName.TRAFILATURA,
    )


async def _extract_jina(url: str) -> ExtractedContent:
    """Extract content using Jina Reader API (external fallback)."""
    headers = {"Accept": "text/plain"}
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"

    reader_url = f"{JINA_READER_URL}{url}"

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(reader_url, headers=headers, follow_redirects=True)
        resp.raise_for_status()

    text = resp.text.strip()
    if not text or len(text) < 50:
        return ExtractedContent(url=url, error="jina: response too short or empty")

    # Jina returns markdown — first line is usually the title
    lines = text.split("\n", 1)
    title = lines[0].lstrip("# ").strip() if lines else ""
    body = lines[1].strip() if len(lines) > 1 else text

    return ExtractedContent(
        url=url,
        title=title,
        text=body,
        word_count=len(body.split()),
        extractor=ExtractorName.JINA,
    )


def get_extraction_cache() -> TTLCache:
    """Return the shared extraction cache instance."""
    return _cache


async def extract_url(url: str) -> ExtractedContent:
    """Extract clean content from a URL.

    Tries trafilatura first (local), falls back to Jina Reader (external).
    Results are cached in memory — same URL within TTL returns cached result.
    """
    # Check cache
    cached = _cache.get(url)
    if cached is not None:
        logger.debug("Extraction cache hit for %s", url[:60])
        return cached

    # Check domain rate limit
    allowed, retry_after = _domain_limiter.is_allowed(url)
    if not allowed:
        return ExtractedContent(
            url=url,
            error=f"domain rate limit exceeded, retry after {retry_after}s",
        )

    # Try trafilatura first
    try:
        result = await _extract_trafilatura(url)
        if result.text and not result.error:
            logger.info("Extracted %s via trafilatura (%d words)", url[:60], result.word_count)
            _cache.put(url, result)
            return result
        logger.debug("Trafilatura returned no content for %s: %s", url[:60], result.error)
    except Exception as e:
        logger.warning("Trafilatura failed for %s: %s", url[:60], e)

    # Fallback to Jina
    try:
        result = await _extract_jina(url)
        if result.text and not result.error:
            logger.info("Extracted %s via Jina fallback (%d words)", url[:60], result.word_count)
            _cache.put(url, result)
            _track_jina_usage(result.word_count)
            return result
        logger.warning("Jina returned no content for %s: %s", url[:60], result.error)
        return ExtractedContent(url=url, error=f"jina: {result.error}")
    except Exception as e:
        logger.error("Jina fallback failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"all extractors failed: {e}")


def _track_jina_usage(word_count: int) -> None:
    """Estimate token cost and periodically decrement the Jina balance."""
    global _jina_call_count, _jina_accumulated_tokens

    estimated_tokens = int(word_count * _TOKENS_PER_WORD)
    _jina_call_count += 1
    _jina_accumulated_tokens += estimated_tokens

    if _jina_call_count % _JINA_SYNC_INTERVAL != 0:
        return

    # Time to sync — flush accumulated tokens to the stored balance
    try:
        from argus.broker.budget_persistence import BudgetStore

        store = BudgetStore()
        current = store.get_token_balance("jina")
        if current is not None:
            new_balance = current - _jina_accumulated_tokens
            store.set_token_balance("jina", new_balance)
            logger.info(
                "Jina token balance synced: %,.0f → %,.0f (%d calls, ~%d tokens flushed)",
                current, new_balance, _jina_call_count, _jina_accumulated_tokens,
            )
            _jina_accumulated_tokens = 0
    except Exception as e:
        logger.warning("Failed to sync Jina token balance: %s", e)
