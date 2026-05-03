"""
Content extraction: integrated fallback chain with quality gates.

Chain order:
  SSRF check → cache → rate limit → auth → quality gate →
  trafilatura → quality gate → crawl4ai → quality gate →
  obscura (CLI) → quality gate → playwright (CDP or Chrome) → quality gate →
  residential (Tailscale) → quality gate →
  jina → quality gate → valyu_contents → quality gate →
  firecrawl → quality gate → you_contents → quality gate →
  wayback → quality gate → archive.is → quality gate → return best result

Results are cached in memory to avoid re-extracting the same URL.

Obscura (https://github.com/h4ckf0r0day/obscura): optional Rust headless browser.
  - CLI step: requires `obscura` binary on PATH
  - CDP step: requires ARGUS_OBSCURA_CDP_URL=ws://127.0.0.1:9222 (obscura serve --stealth)
    When set, Playwright connects to Obscura instead of launching Chrome, gaining
    stealth mode (navigator.webdriver=undefined, fingerprint randomization) and
    30MB vs 200MB memory footprint.
"""

import asyncio
import os

import httpx

from argus.config import get_config
from argus.extraction.cache import ExtractionCache
from argus.extraction.completeness import assess_completeness
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.quality_gate import QualityGate
from argus.extraction.soft_404 import is_soft_404
from argus.extraction.rate_limit import DomainRateLimiter
from argus.extraction.ssrf import is_safe_url
from argus.logging import get_logger

logger = get_logger("extraction")

JINA_READER_URL = "https://r.jina.ai/"

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
    if is_soft_404(content):
        return False, "soft_404"
    evaluation = _quality_gate.evaluate(content, url, extractor=extractor_name)
    if not evaluation.passed:
        return False, evaluation.reason
    return True, ""


# Threshold for treating truncated-but-quality-passed content as "keep trying".
# Applies consistently across the chain. A quality-passed but clearly truncated
# result should not terminate fallback while there are later extractors left.
_COMPLETENESS_RETRY_CONFIDENCE = 0.85
_COMPLETENESS_RETRY_MAX_STEPS = 11


def _should_continue_for_completeness(result: ExtractedContent, step: int) -> bool:
    """Return True if we should skip returning this result and try the next extractor.

    Conditions: quality passed, but completeness assessment says it's clearly
    truncated (confidence >= threshold), AND we're still in the free-extractor
    window (step <= _COMPLETENESS_RETRY_MAX_STEPS).
    """
    if step > _COMPLETENESS_RETRY_MAX_STEPS:
        return False
    cr = result.completeness_result
    if cr is None:
        return False
    return (not cr.is_complete) and cr.confidence >= _COMPLETENESS_RETRY_CONFIDENCE


def _safe_final_url(original_url: str, final_url: str) -> tuple[bool, str]:
    """Validate a post-redirect URL before using fetched content."""
    if not final_url or final_url == original_url:
        return True, ""
    return is_safe_url(final_url)


def _populate_provenance(result: ExtractedContent):
    """Fill in provenance metadata based on extractor and current config."""
    config = get_config()
    result.machine = config.node.machine_name or None

    if result.extractor in (ExtractorName.AUTH,):
        result.source_type = "authenticated"
        result.auth_used = True
        result.cookies_used = True
        result.egress = config.node.egress_type
    elif result.extractor in (ExtractorName.RESIDENTIAL,):
        result.source_type = "residential"
        result.egress = "residential"
    elif result.extractor in (ExtractorName.TRAFILATURA, ExtractorName.CRAWL4AI, ExtractorName.OBSCURA, ExtractorName.PLAYWRIGHT):
        result.source_type = "live"
        result.egress = config.node.egress_type
    elif result.extractor in (ExtractorName.WAYBACK, ExtractorName.ARCHIVE_IS):
        result.source_type = "archive"
        result.archive_used = True
        result.egress = "datacenter" if config.node.egress_type != "residential" else "residential"
    elif result.extractor in (ExtractorName.JINA, ExtractorName.VALYU_CONTENTS, ExtractorName.FIRECRAWL, ExtractorName.YOU_CONTENTS):
        result.source_type = "paid_api"
        result.egress = "datacenter"


async def _extract_trafilatura(url: str, timeout: int = 10) -> ExtractedContent:
    """Extract content using trafilatura (local, no API call)."""
    import trafilatura

    loop = asyncio.get_event_loop()

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Argus/1.0"})
        resp.raise_for_status()
        final_url = str(resp.url)

    safe, reason = _safe_final_url(url, final_url)
    if not safe:
        return ExtractedContent(url=url, error=f"ssrf_blocked_redirect: {reason}")

    downloaded = resp.text
    if not downloaded:
        return ExtractedContent(url=url, error="trafilatura: failed to fetch URL")

    extracted = await loop.run_in_executor(None, trafilatura.bare_extraction, downloaded)
    if not extracted or not extracted.get("text"):
        return ExtractedContent(url=final_url, error="trafilatura: no content extracted")

    text = extracted["text"]
    return ExtractedContent(
        url=final_url,
        title=extracted.get("title", ""),
        text=text,
        author=extracted.get("author", ""),
        date=extracted.get("date"),
        word_count=len(text.split()),
        extractor=ExtractorName.TRAFILATURA,
    )


async def _extract_jina(url: str, timeout: int = 10) -> ExtractedContent:
    """Extract content using Jina Reader API (external fallback)."""
    config = get_config()
    jina_key = config.brave.api_key  # Wait, Jina doesn't have its own Config yet, but it's in env
    jina_key = os.getenv("ARGUS_JINA_API_KEY", "")

    headers = {"Accept": "text/plain"}
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"

    reader_url = f"{JINA_READER_URL}{url}"

    async with httpx.AsyncClient(timeout=timeout) as client:
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


async def extract_url(url: str, domain: str = None, mode: str = "default") -> ExtractedContent:
    """Extract clean content from a URL using the integrated fallback chain.

    Modes:
      - default: standard fallback chain
      - archive_ingest: optimized for durability and provenance (Atlas-style)

    Chain:
      SSRF → cache → rate limit → auth → QG → trafilatura → QG →
      crawl4ai → QG → obscura → QG → playwright → QG → residential → QG →
      jina → QG → valyu_contents → QG → firecrawl → QG → you_contents → QG →
      wayback → QG → archive.is → QG → return best result
    """
    config = get_config()
    from argus.extraction.domain_memory import get_domain_memory
    dm = get_domain_memory()

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
        _populate_provenance(result)
        nonlocal best_result
        if result.text and result.word_count > 0:
            if best_result is None or result.word_count > best_result.word_count:
                best_result = result

    # Phase 4: Residential Egress Policy
    res_policy = config.residential.policy
    use_residential_early = False
    if res_policy == "always":
        use_residential_early = True
    elif res_policy == "prefer_on_datacenter" and config.node.egress_type != "residential":
        use_residential_early = True
    elif res_policy == "prefer_for_domains" and domain and dm.should_prefer_residential(domain, "extraction"):
        use_residential_early = True
    elif mode == "archive_ingest" and config.node.egress_type != "residential":
        use_residential_early = True

    async def run_residential_step():
        if config.node.egress_type == "residential":
            return None # Already residential, local steps are already residential egress

        try:
            from argus.extraction.residential_extractor import extract_residential, _is_configured
            if _is_configured():
                res_result = await extract_residential(url, domain=domain or "")
                track_attempt("residential", res_result)
                if res_result.text and not res_result.error:
                    passed, r_reason = _run_quality_gate(res_result.text, url, "residential")
                    res_result.quality_passed = passed
                    res_result.quality_reason = r_reason if not passed else None
                    res_result.extractors_tried = list(extractors_tried)
                    if passed:
                        res_result.completeness_result = assess_completeness(res_result.text, url)
                        if not _should_continue_for_completeness(res_result, step=6):
                            logger.info("Extracted %s via residential (%d words)", url[:60], res_result.word_count)
                            if domain:
                                dm.record_residential_success(domain)
                            return res_result
                elif domain:
                    dm.record_datacenter_failure(domain, res_result.error)
        except Exception as e:
            logger.warning("Residential extraction failed for %s: %s", url[:60], e)
        return None

    # Step 1: Auth extraction (if cookies available for paywall domain)
    if domain:
        try:
            from argus.extraction.auth_extractor import extract_authenticated

            result = await extract_authenticated(url, domain)
            track_attempt("auth", result)
            if result and result.text and not result.error:
                passed, reason = _run_quality_gate(result.text, url, "auth")
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                result.extractors_tried = list(extractors_tried)
                if passed:
                    result.completeness_result = assess_completeness(result.text, url)
                    if not _should_continue_for_completeness(result, step=1):
                        logger.info("Extracted %s via auth (%d words)", url[:60], result.word_count)
                        _cache.put(url, result)
                        return result
        except Exception as e:
            logger.debug("Auth extraction not available: %s", e)

    # Policy-driven residential trigger (Early)
    if use_residential_early and res_policy != "off":
        res_res = await run_residential_step()
        if res_res:
            _cache.put(url, res_res)
            return res_res

    # Local Extractors (Steps 2-5)
    for step_num, step_name, extractor_func in [
        (2, "trafilatura", _extract_trafilatura),
        (3, "crawl4ai", None),
        (4, "obscura", None),
        (5, "playwright", None),
    ]:
        try:
            if step_name == "crawl4ai":
                if os.getenv("ARGUS_CRAWL4AI_ENABLED", "").lower() not in ("1", "true"):
                    continue
                from argus.extraction.crawl4ai_extractor import extract_crawl4ai as extractor_func
            elif step_name == "obscura":
                from argus.extraction.obscura_extractor import extract_obscura as extractor_func
            elif step_name == "playwright":
                from argus.extraction.playwright_extractor import extract_playwright as extractor_func

            result = await extractor_func(url)
            track_attempt(step_name, result)
            if result.text and not result.error:
                passed, reason = _run_quality_gate(result.text, url, step_name)
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                result.extractors_tried = list(extractors_tried)
                if passed:
                    result.completeness_result = assess_completeness(result.text, url)
                    if not _should_continue_for_completeness(result, step=step_num):
                        logger.info("Extracted %s via %s (%d words)", url[:60], step_name, result.word_count)
                        _cache.put(url, result)
                        return result
        except Exception as e:
            logger.warning("%s failed for %s: %s", step_name.capitalize(), url[:60], e)

    # Step 6: Residential extraction (Fallback if not already tried early)
    if not use_residential_early and res_policy != "off":
        res_res = await run_residential_step()
        if res_res:
            _cache.put(url, res_res)
            return res_res

    # External APIs (Steps 7-10)
    external_steps = [
        (7, "jina", _extract_jina),
        (8, "valyu_contents", None),
        (9, "firecrawl", None),
        (10, "you_contents", None),
    ]

    for step_num, step_name, extractor_func in external_steps:
        # For archive_ingest mode, we try archive recovery before paid APIs
        if mode == "archive_ingest" and step_num == 7:
            # We'll come back to paid APIs if archives fail
            break

        try:
            if step_name == "valyu_contents":
                from argus.extraction.valyu_extractor import extract_valyu_contents as extractor_func
            elif step_name == "firecrawl":
                from argus.extraction.firecrawl_extractor import extract_firecrawl as extractor_func
            elif step_name == "you_contents":
                if os.getenv("ARGUS_YOU_CONTENTS_ENABLED", "").lower() not in ("1", "true"):
                    continue
                from argus.extraction.you_extractor import extract_you_contents as extractor_func

            result = await extractor_func(url)
            track_attempt(step_name, result)
            if result.text and not result.error:
                passed, reason = _run_quality_gate(result.text, url, step_name)
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                result.extractors_tried = list(extractors_tried)
                if passed:
                    result.completeness_result = assess_completeness(result.text, url)
                    if step_name == "jina":
                        _track_jina_usage(result.word_count)
                    if not _should_continue_for_completeness(result, step=step_num):
                        logger.info("Extracted %s via %s (%d words)", url[:60], step_name, result.word_count)
                        _cache.put(url, result)
                        return result
        except Exception as e:
            logger.warning("%s failed for %s: %s", step_name.capitalize(), url[:60], e)

    # Step 11 & 12: Archive Recovery
    for step_num, step_name, extractor_func in [
        (11, "wayback", None),
        (12, "archive_is", None),
    ]:
        try:
            if step_name == "wayback":
                from argus.extraction.wayback_extractor import extract_wayback as extractor_func
            elif step_name == "archive_is":
                from argus.extraction.archive_extractor import extract_archive_is as extractor_func

            result = await extractor_func(url)
            track_attempt(step_name, result)
            if result.text and not result.error:
                passed, reason = _run_quality_gate(result.text, url, step_name)
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                result.extractors_tried = list(extractors_tried)
                if passed:
                    result.completeness_result = assess_completeness(result.text, url)
                    logger.info("Extracted %s via %s (%d words)", url[:60], step_name, result.word_count)
                    _cache.put(url, result)
                    return result
        except Exception as e:
            logger.warning("%s failed for %s: %s", step_name.capitalize(), url[:60], e)

    # If archive_ingest and we haven't tried paid APIs yet, try them now
    if mode == "archive_ingest":
        for step_num, step_name, extractor_func in external_steps:
            try:
                if step_name == "jina":
                    extractor_func = _extract_jina
                elif step_name == "valyu_contents":
                    from argus.extraction.valyu_extractor import extract_valyu_contents as extractor_func
                elif step_name == "firecrawl":
                    from argus.extraction.firecrawl_extractor import extract_firecrawl as extractor_func
                elif step_name == "you_contents":
                    if os.getenv("ARGUS_YOU_CONTENTS_ENABLED", "").lower() not in ("1", "true"):
                        continue
                    from argus.extraction.you_extractor import extract_you_contents as extractor_func

                result = await extractor_func(url)
                track_attempt(step_name, result)
                if result.text and not result.error:
                    passed, reason = _run_quality_gate(result.text, url, step_name)
                    result.quality_passed = passed
                    result.quality_reason = reason if not passed else None
                    result.extractors_tried = list(extractors_tried)
                    if passed:
                        result.completeness_result = assess_completeness(result.text, url)
                        if step_name == "jina":
                            _track_jina_usage(result.word_count)
                        if not _should_continue_for_completeness(result, step=step_num):
                            logger.info("Extracted %s via %s (%d words)", url[:60], step_name, result.word_count)
                            _cache.put(url, result)
                            return result
            except Exception as e:
                logger.warning("%s failed for %s: %s", step_name.capitalize(), url[:60], e)

    # All extractors tried — return best result even if quality failed
    if best_result:
        best_result.quality_passed = False
        best_result.quality_reason = "all_extractors_quality_failed"
        best_result.extractors_tried = extractors_tried
        if best_result.text and best_result.completeness_result is None:
            best_result.completeness_result = assess_completeness(best_result.text, url)
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
    _populate_provenance(result)
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
