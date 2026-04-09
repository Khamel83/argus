"""
Content extraction: integrated fallback chain with quality gates.

Chain order:
  SSRF check → cache → rate limit → auth → quality gate →
  trafilatura → quality gate → crawl4ai → quality gate →
  playwright → quality gate → jina → quality gate →
  you_contents → quality gate → wayback → quality gate →
  archive.is → quality gate → return best result

Results are cached in memory to avoid re-extracting the same URL.
"""

import asyncio
import os

import httpx

from argus.extraction.cache import ExtractionCache
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.quality_gate import QualityGate, GateResult
from argus.extraction.soft_404 import is_soft_404
from argus.extraction.rate_limit import DomainRateLimiter
from argus.extraction.ssrf import is_safe_url
from argus.logging import get_logger

logger = get_logger("extraction")

DEFAULT_TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "10"))
JINA_READER_URL = "https://r.jina.ai/"
JINA_API_KEY = os.getenv("ARGUS_JINA_API_KEY", "")

# Shared cache — lives for the process lifetime
_cache = ExtractionCache(
    ttl_hours=int(os.getenv("ARGUS_EXTRACTION_CACHE_TTL_HOURS", "168"))
)

# Shared domain rate limiter — 10 requests per minute per domain
_domain_limiter = DomainRateLimiter(
    max_requests=int(os.getenv("ARGUS_EXTRACTION_DOMAIN_RATE_LIMIT", "10")),
    window_seconds=int(os.getenv("ARGUS_EXTRACTION_DOMAIN_WINDOW_SECONDS", "60")),
)

# Shared quality gate
_quality_gate = QualityGate()

# Token tracking state
_jina_call_count = 0
_jina_accumulated_tokens = 0
_JINA_SYNC_INTERVAL = 10
_TOKENS_PER_WORD = 1.3


def _run_quality_gate(content: str, url: str, extractor_name: str) -> tuple[bool, str]:
    """Run quality gate + soft 404 check. Returns (passed, reason)."""
    # Soft 404 check first
    if is_soft_404(content):
        return False, "soft_404"

    # Quality gate
    evaluation = _quality_gate.evaluate(content, url, extractor=extractor_name)
    if not evaluation.passed:
        return False, evaluation.reason

    return True, ""


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

    if "403" in text[:200] or "Forbidden" in text[:200] or "CAPTCHA" in text[:200]:
        return ExtractedContent(url=url, error="jina: access denied (403/CAPTCHA)")

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


def get_extraction_cache() -> ExtractionCache:
    """Return the shared extraction cache instance."""
    return _cache


async def extract_url(url: str, domain: str = None) -> ExtractedContent:
    """Extract clean content from a URL using the integrated fallback chain.

    Chain:
      SSRF → cache → rate limit → auth → QG → trafilatura → QG →
      crawl4ai → QG → playwright → QG → jina → QG → wayback → QG →
      archive.is → QG → return best result (even if all quality gates failed)

    Results are cached in memory — same URL within TTL returns cached result.
    """
    # SSRF check
    safe, reason = is_safe_url(url)
    if not safe:
        return ExtractedContent(url=url, error=f"ssrf_blocked: {reason}")

    # Cache check
    cached = _cache.get(url)
    if cached is not None:
        logger.debug("Extraction cache hit for %s", url[:60])
        return cached

    # Domain rate limit
    allowed, retry_after = _domain_limiter.is_allowed(url)
    if not allowed:
        return ExtractedContent(
            url=url,
            error=f"domain rate limit exceeded, retry after {retry_after}s",
        )

    extractors_tried = []
    best_result = None  # Keep the best (longest) result even if quality fails

    def track_attempt(name: str, result: ExtractedContent):
        """Track which extractors were tried and keep the best result."""
        extractors_tried.append(name)
        nonlocal best_result
        if result.text and result.word_count > 0:
            if best_result is None or result.word_count > best_result.word_count:
                best_result = result

    # Step 1: Auth extraction (if cookies available for paywall domain)
    if domain:
        try:
            from argus.extraction.auth_extractor import extract_authenticated

            result = await extract_authenticated(url, domain)
            extractors_tried.append("auth")
            if result and result.text and not result.error:
                passed, reason = _run_quality_gate(result.text, url, "auth")
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                result.extractors_tried = list(extractors_tried)
                if passed:
                    logger.info("Extracted %s via auth (%d words)", url[:60], result.word_count)
                    _cache.put(url, result)
                    return result
                track_attempt("auth", result)
        except Exception as e:
            logger.debug("Auth extraction not available: %s", e)

    # Step 2: Trafilatura (local, fast)
    try:
        result = await _extract_trafilatura(url)
        track_attempt("trafilatura", result)
        if result.text and not result.error:
            passed, reason = _run_quality_gate(result.text, url, "trafilatura")
            result.quality_passed = passed
            result.quality_reason = reason if not passed else None
            result.extractors_tried = list(extractors_tried)
            if passed:
                logger.info("Extracted %s via trafilatura (%d words)", url[:60], result.word_count)
                _cache.put(url, result)
                return result
            logger.debug("Trafilatura content failed quality gate: %s", reason)
    except Exception as e:
        logger.warning("Trafilatura failed for %s: %s", url[:60], e)

    # Step 3: Crawl4AI (self-hosted, JS rendering)
    if os.getenv("ARGUS_CRAWL4AI_ENABLED", "").lower() in ("1", "true"):
        try:
            from argus.extraction.crawl4ai_extractor import extract_crawl4ai

            result = await extract_crawl4ai(url)
            track_attempt("crawl4ai", result)
            if result.text and not result.error:
                passed, reason = _run_quality_gate(result.text, url, "crawl4ai")
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                result.extractors_tried = list(extractors_tried)
                if passed:
                    logger.info("Extracted %s via crawl4ai (%d words)", url[:60], result.word_count)
                    _cache.put(url, result)
                    return result
                logger.debug("Crawl4AI content failed quality gate: %s", reason)
        except Exception as e:
            logger.warning("Crawl4AI failed for %s: %s", url[:60], e)

    # Step 4: Playwright (local headless browser)
    try:
        from argus.extraction.playwright_extractor import extract_playwright

        result = await extract_playwright(url)
        track_attempt("playwright", result)
        if result.text and not result.error:
            passed, reason = _run_quality_gate(result.text, url, "playwright")
            result.quality_passed = passed
            result.quality_reason = reason if not passed else None
            result.extractors_tried = list(extractors_tried)
            if passed:
                logger.info("Extracted %s via playwright (%d words)", url[:60], result.word_count)
                _cache.put(url, result)
                return result
            logger.debug("Playwright content failed quality gate: %s", reason)
    except Exception as e:
        logger.warning("Playwright failed for %s: %s", url[:60], e)

    # Step 4: Jina Reader (external API)
    try:
        result = await _extract_jina(url)
        track_attempt("jina", result)
        if result.text and not result.error:
            passed, reason = _run_quality_gate(result.text, url, "jina")
            result.quality_passed = passed
            result.quality_reason = reason if not passed else None
            result.extractors_tried = list(extractors_tried)
            if passed:
                logger.info("Extracted %s via Jina (%d words)", url[:60], result.word_count)
                _cache.put(url, result)
                _track_jina_usage(result.word_count)
                return result
            logger.debug("Jina content failed quality gate: %s", reason)
    except Exception as e:
        logger.warning("Jina failed for %s: %s", url[:60], e)

    # Step 5: You.com Contents API ($1/1k pages, cheaper than Jina)
    if os.getenv("ARGUS_YOU_CONTENTS_ENABLED", "").lower() in ("1", "true"):
        try:
            from argus.extraction.you_extractor import extract_you_contents

            result = await extract_you_contents(url)
            track_attempt("you_contents", result)
            if result.text and not result.error:
                passed, reason = _run_quality_gate(result.text, url, "you_contents")
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                result.extractors_tried = list(extractors_tried)
                if passed:
                    logger.info("Extracted %s via You.com Contents (%d words)", url[:60], result.word_count)
                    _cache.put(url, result)
                    return result
                logger.debug("You.com Contents failed quality gate: %s", reason)
        except Exception as e:
            logger.warning("You.com Contents failed for %s: %s", url[:60], e)

    # Step 6: Wayback Machine
    try:
        from argus.extraction.wayback_extractor import extract_wayback

        result = await extract_wayback(url)
        track_attempt("wayback", result)
        if result.text and not result.error:
            passed, reason = _run_quality_gate(result.text, url, "wayback")
            result.quality_passed = passed
            result.quality_reason = reason if not passed else None
            result.extractors_tried = list(extractors_tried)
            if passed:
                logger.info("Extracted %s via wayback (%d words)", url[:60], result.word_count)
                _cache.put(url, result)
                return result
            logger.debug("Wayback content failed quality gate: %s", reason)
    except Exception as e:
        logger.warning("Wayback failed for %s: %s", url[:60], e)

    # Step 6: Archive.is
    try:
        from argus.extraction.archive_extractor import extract_archive_is

        result = await extract_archive_is(url)
        track_attempt("archive_is", result)
        if result.text and not result.error:
            passed, reason = _run_quality_gate(result.text, url, "archive_is")
            result.quality_passed = passed
            result.quality_reason = reason if not passed else None
            result.extractors_tried = list(extractors_tried)
            if passed:
                logger.info("Extracted %s via archive.is (%d words)", url[:60], result.word_count)
                _cache.put(url, result)
                return result
            logger.debug("Archive.is content failed quality gate: %s", reason)
    except Exception as e:
        logger.warning("Archive.is failed for %s: %s", url[:60], e)

    # All extractors tried — return best result even if quality failed
    if best_result:
        best_result.quality_passed = False
        best_result.quality_reason = "all_extractors_quality_failed"
        best_result.extractors_tried = extractors_tried
        _cache.put(url, best_result)
        logger.warning(
            "All quality gates failed for %s, returning best (%d words via %s)",
            url[:60], best_result.word_count, best_result.extractor,
        )
        return best_result

    # Complete failure
    result = ExtractedContent(
        url=url,
        error=f"all extractors failed: tried {extractors_tried}",
        quality_passed=False,
        quality_reason="all_extractors_failed",
        extractors_tried=extractors_tried,
    )
    return result


def _track_jina_usage(word_count: int) -> None:
    """Estimate token cost and periodically decrement the Jina balance."""
    global _jina_call_count, _jina_accumulated_tokens

    estimated_tokens = int(word_count * _TOKENS_PER_WORD)
    _jina_call_count += 1
    _jina_accumulated_tokens += estimated_tokens

    if _jina_call_count % _JINA_SYNC_INTERVAL != 0:
        return

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
