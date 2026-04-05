"""
Content extraction: trafilatura primary, Jina Reader fallback.

Hybrid strategy: try trafilatura (local, fast, free) first.
If it returns empty or fails, fall back to Jina Reader API.
Results are cached in memory and SQLite to avoid re-extracting the same URL.
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
_JINA_SYNC_INTERVAL = 10
_TOKENS_PER_WORD = 1.3


class ContentExtractor:
    def __init__(
        self,
        *,
        cache_ttl_seconds: int | None = None,
        domain_rate_limit: int | None = None,
        domain_window_seconds: int | None = None,
    ):
        cache_ttl = cache_ttl_seconds or int(os.getenv("ARGUS_EXTRACTION_CACHE_TTL_HOURS", "168")) * 3600
        self._cache = TTLCache(
            ttl_seconds=cache_ttl,
            key_fn=extraction_cache_key,
            skip_fn=lambda content: bool(content.error),
        )
        self._cache_ttl = cache_ttl
        self._domain_limiter = DomainRateLimiter(
            max_requests=domain_rate_limit or int(os.getenv("ARGUS_EXTRACTION_DOMAIN_RATE_LIMIT", "10")),
            window_seconds=domain_window_seconds or int(os.getenv("ARGUS_EXTRACTION_DOMAIN_WINDOW_SECONDS", "60")),
        )
        self._jina_call_count = 0
        self._jina_accumulated_tokens = 0
        from argus.broker.budget_persistence import BudgetStore
        self._store = BudgetStore()

    @property
    def cache(self) -> TTLCache:
        return self._cache

    def _get_sqlite_cached(self, url: str) -> ExtractedContent | None:
        try:
            row = self._store.get_extraction(extraction_cache_key(url), self._cache_ttl)
            if row is None:
                return None
            content = ExtractedContent(
                url=url, title=row["title"], text=row["text"],
                author=row["author"], date=row["date"],
                word_count=row["word_count"],
                extractor=ExtractorName(row["extractor"]) if row["extractor"] else None,
            )
            self._cache.put(url, value=content)
            return content
        except Exception as e:
            logger.debug("SQLite cache miss (error): %s", e)
            return None

    def _save_to_sqlite(self, url: str, content: ExtractedContent) -> None:
        try:
            self._store.put_extraction(
                extraction_cache_key(url), content.title, content.text,
                content.author, content.date, content.word_count,
                content.extractor.value if content.extractor else "",
            )
        except Exception as e:
            logger.debug("Failed to persist extraction to SQLite: %s", e)

    def _track_jina_usage(self, word_count: int) -> None:
        self._jina_accumulated_tokens += int(word_count * _TOKENS_PER_WORD)
        self._jina_call_count += 1

        if self._jina_call_count % _JINA_SYNC_INTERVAL != 0:
            return

        try:
            current = self._store.get_service_credit("jina")
            if current is not None:
                new_balance = current - self._jina_accumulated_tokens
                self._store.set_service_credit("jina", new_balance)
                logger.info(
                    "Jina token balance synced: %,.0f → %,.0f (%d calls, ~%d tokens)",
                    current, new_balance, self._jina_call_count, self._jina_accumulated_tokens,
                )
                self._jina_accumulated_tokens = 0
        except Exception as e:
            logger.warning("Failed to sync Jina token balance: %s", e)

    async def extract(self, url: str, domain: str | None = None) -> ExtractedContent:
        # In-memory cache
        cached = self._cache.get(url)
        if cached is not None:
            logger.debug("Extraction cache hit (memory) for %s", url[:60])
            return cached

        # SQLite cache
        cached = self._get_sqlite_cached(url)
        if cached is not None:
            logger.debug("Extraction cache hit (sqlite) for %s", url[:60])
            return cached

        # Domain rate limit
        allowed, retry_after = self._domain_limiter.is_allowed(url)
        if not allowed:
            return ExtractedContent(url=url, error=f"domain rate limit exceeded, retry after {retry_after}s")

        # For paywall domains with cookies, try authenticated extraction first.
        # Auto-detect domain from URL if not explicitly provided.
        if not domain:
            from argus.extraction.cookies import needs_auth
            from urllib.parse import urlparse
            if needs_auth(url):
                hostname = urlparse(url).hostname or ""
                parts = hostname.split(".")
                domain = ".".join(parts[-2:]) if len(parts) >= 2 else hostname

        if domain:
            try:
                from argus.extraction.auth_extractor import extract_authenticated

                auth_result = await extract_authenticated(url, domain)
                if auth_result and auth_result.text and not auth_result.error:
                    self._cache.put(url, value=auth_result)
                    self._save_to_sqlite(url, auth_result)
                    return auth_result
            except Exception as e:
                logger.debug("Auth extraction not available: %s", e)

        # Try trafilatura
        try:
            result = await _extract_trafilatura(url)
            if result.text and not result.error:
                logger.info("Extracted %s via trafilatura (%d words)", url[:60], result.word_count)
                self._cache.put(url, value=result)
                self._save_to_sqlite(url, result)
                return result
            logger.debug("Trafilatura returned no content for %s: %s", url[:60], result.error)
        except Exception as e:
            logger.warning("Trafilatura failed for %s: %s", url[:60], e)

        # Fallback to Jina
        try:
            result = await _extract_jina(url)
            if result.text and not result.error:
                logger.info("Extracted %s via Jina fallback (%d words)", url[:60], result.word_count)
                self._cache.put(url, value=result)
                self._save_to_sqlite(url, result)
                self._track_jina_usage(result.word_count)
                return result
            logger.warning("Jina returned no content for %s: %s", url[:60], result.error)
            return ExtractedContent(url=url, error=f"jina: {result.error}")
        except Exception as e:
            logger.error("Jina fallback failed for %s: %s", url[:60], e)
            return ExtractedContent(url=url, error=f"all extractors failed: {e}")


# Lazy extractor: avoids triggering SQLite/BudgetStore on import.
_default_extractor: ContentExtractor | None = None


def get_extractor() -> ContentExtractor:
    """Get or create the default extractor instance (lazy init)."""
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = ContentExtractor()
    return _default_extractor


def get_extraction_cache() -> TTLCache:
    return get_extractor().cache


async def extract_url(url: str, domain: str | None = None) -> ExtractedContent:
    return await get_extractor().extract(url, domain=domain)


# --- Private extraction functions (stateless) ---

async def _extract_trafilatura(url: str) -> ExtractedContent:
    import trafilatura
    loop = asyncio.get_event_loop()
    downloaded = await loop.run_in_executor(None, trafilatura.fetch_url, url)
    if not downloaded:
        return ExtractedContent(url=url, error="trafilatura: failed to fetch URL")
    extracted = await loop.run_in_executor(None, trafilatura.bare_extraction, downloaded)
    text = getattr(extracted, "text", None) if extracted else None
    if not text:
        return ExtractedContent(url=url, error="trafilatura: no content extracted")
    return ExtractedContent(
        url=url,
        title=getattr(extracted, "title", "") or "",
        text=text,
        author=getattr(extracted, "author", "") or "",
        date=getattr(extracted, "date", None),
        word_count=len(text.split()),
        extractor=ExtractorName.TRAFILATURA,
    )


async def _extract_jina(url: str) -> ExtractedContent:
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
    lines = text.split("\n", 1)
    title = lines[0].lstrip("# ").strip() if lines else ""
    body = lines[1].strip() if len(lines) > 1 else text
    return ExtractedContent(url=url, title=title, text=body, word_count=len(body.split()), extractor=ExtractorName.JINA)
