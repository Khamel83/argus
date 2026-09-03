"""
Content extraction: integrated fallback chain with quality gates.

Chain order:
  SSRF check → cache → rate limit → auth → quality gate →
  trafilatura → quality gate → crawl4ai → quality gate →
  obscura (CLI) → quality gate → playwright (CDP or Chrome) → quality gate →
  residential (Tailscale) → quality gate →
  jina → quality gate → valyu_contents → quality gate →
  firecrawl → quality gate → you_contents → quality gate →
  wayback → quality gate → archive.is lookup → quality gate → return best result

Results are cached in memory to avoid re-extracting the same URL.

Obscura (https://github.com/h4ckf0r0day/obscura): optional Rust headless browser.
  - CLI step: requires `obscura` binary on PATH
  - CDP step: requires ARGUS_OBSCURA_CDP_URL=ws://127.0.0.1:9222 (obscura serve --stealth)
    When set, Playwright connects to Obscura instead of launching Chrome, gaining
    stealth mode (navigator.webdriver=undefined, fingerprint randomization) and
    30MB vs 200MB memory footprint.
"""

import asyncio
import copy
import hashlib
import ipaddress
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

import httpx

from argus.acquisition.guarded import (
    GuardedAcquisitionError,
    guarded_http_request,
    guarded_url_policy,
    patched_httpx_client,
)
from argus.config import get_config
from argus.contracts import CanonicalOutcome
from argus.extraction.cache import ExtractionCache
from argus.extraction.completeness import assess_completeness
from argus.extraction.models import ExtractedContent, ExtractionAttempt, ExtractorName
from argus.extraction.quality_gate import QualityGate
from argus.extraction.soft_404 import is_soft_404
from argus.extraction.rate_limit import DomainRateLimiter
from argus.extraction.trafilatura_result import normalize_trafilatura_result
from argus.logging import get_logger

logger = get_logger("extraction")

# Compatibility symbol for callers that injected the historical checker. The
# policy implementation remains in Guarded Acquisition.
is_safe_url = guarded_url_policy

JINA_READER_URL = "https://r.jina.ai/"

# Shared cache — lives for the process lifetime
_cache = ExtractionCache(
    ttl_hours=int(os.getenv("ARGUS_EXTRACTION_CACHE_TTL_HOURS", "168"))
)
# Accepted extraction cache.  Entries are published only after the typed
# outcome has been durably accepted; each lookup revalidates that receipt in
# the caller's repository.
_accepted_cache = ExtractionCache()

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


def _quality_policy_version(content_type: str) -> str:
    """Return a cache policy identity for the selected quality profile."""
    return "quality-v1" if content_type == "article" else f"quality-v1-{content_type}"


def _legacy_cache_key(url: str, content_type: str) -> str:
    """Keep the legacy URL cache from crossing content-quality profiles."""
    if content_type == "article":
        return url
    return f"{url}\x00argus-content-type={content_type}"


def _accepted_cache_identity(
    url: str, mode: str, free_only: bool, content_type: str = "article"
):
    """Build the stable policy identity used before accepted extraction."""
    from argus.extraction.cache import ExtractionCacheIdentity

    return ExtractionCacheIdentity(
        normalized_url=(
            "sha256:" + hashlib.sha256(url.encode("utf-8")).hexdigest()
        ),
        mode=mode,
        access_scope="public",
        authentication_scope_fingerprint="anonymous",
        cache_policy_version="accepted-extraction-cache-v1",
        extraction_plan_version="1",
        quality_policy_version=_quality_policy_version(content_type),
        completeness_policy_version="completeness-v1",
        outcome_policy_version="extraction-outcome-v1",
        privacy_scope="public",
        partial_allowed=True,
        cache_max_age_seconds=604_800,
        profile="free" if free_only else "autonomous",
        effective_max_provider_tier=0 if free_only else 3,
        provider_restrictions=(),
        eligible_extractors=(),
        freshness_window_seconds=604_800,
        original_evidence_ref=None,
    )


def _run_quality_gate(
    content: str,
    url: str,
    extractor_name: str,
    content_type: str = "article",
) -> tuple[bool, str]:
    """Run quality gate + soft 404 check. Returns (passed, reason)."""
    if is_soft_404(content):
        return False, "soft_404"
    evaluation = _quality_gate.evaluate(
        content,
        url,
        content_type=content_type,
        extractor=extractor_name,
    )
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
    return guarded_url_policy(final_url)


def _cache_url_candidate(url: str) -> tuple[bool, str]:
    """Check URL shape before consulting the legacy cache.

    This check deliberately performs no DNS work.  A cache hit is already a
    bounded local fact and must remain usable when the current DNS authority
    is unavailable.  Network validation still runs for cache misses.
    """

    if not isinstance(url, str):
        return False, "URL must be text"
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return False, "invalid scheme"
        if not parsed.hostname:
            return False, "no hostname"
        if parsed.username is not None or parsed.password is not None:
            return False, "credentials in URL blocked"
        # Accessing ``port`` performs the standard-library's bounded syntax
        # validation and catches malformed bracketed/overflow ports.
        _ = parsed.port
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname in {"localhost", "internal", "intranet", "local"} or hostname.endswith(
            (".local", ".internal", ".corp", ".lan")
        ):
            return False, "internal hostname blocked"
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            return False, "non-public IP blocked"
        return True, ""
    except (TypeError, ValueError) as exc:
        return False, f"invalid URL: {exc}"


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
    elif result.extractor in (ExtractorName.YOUTUBE, ExtractorName.TRAFILATURA, ExtractorName.CRAWL4AI, ExtractorName.OBSCURA, ExtractorName.PLAYWRIGHT):
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

    try:
        resp = await guarded_http_request(
            url,
            headers={"User-Agent": "Argus/1.0"},
            timeout=timeout,
            caller_principal="trafilatura",
            request_id="extract-trafilatura",
            compat_client_factory=patched_httpx_client(httpx.AsyncClient),
        )
        resp.raise_for_status()
    except GuardedAcquisitionError as exc:
        return ExtractedContent(url=url, error=f"trafilatura: {exc.failure.code.value}")
    final_url = str(resp.url)

    safe, reason = _safe_final_url(url, final_url)
    if not safe:
        return ExtractedContent(url=url, error=f"ssrf_blocked_redirect: {reason}")

    downloaded = resp.text
    if not downloaded:
        return ExtractedContent(url=url, error="trafilatura: failed to fetch URL")

    extracted = await loop.run_in_executor(None, trafilatura.bare_extraction, downloaded)
    normalized = normalize_trafilatura_result(extracted)
    if normalized is None:
        return ExtractedContent(url=final_url, error="trafilatura: no content extracted")

    text = normalized.text
    return ExtractedContent(
        url=final_url,
        title=normalized.title,
        text=text,
        author=normalized.author,
        date=normalized.date,
        word_count=len(text.split()),
        extractor=ExtractorName.TRAFILATURA,
    )


async def _extract_jina(url: str, timeout: int = 10) -> ExtractedContent:
    """Extract content using Jina Reader API (external fallback)."""
    config = get_config()
    jina_key = config.jina.api_key

    headers = {"Accept": "text/plain"}
    if jina_key:
        headers["Authorization"] = f"Bearer {jina_key}"

    reader_url = f"{JINA_READER_URL}{url}"

    try:
        resp = await guarded_http_request(
            reader_url,
            headers=headers,
            timeout=timeout,
            profile="third_party_fetch",
            operation_class="third_party",
            caller_principal="jina",
            request_id="extract-jina",
            target_url=url,
            compat_client_factory=patched_httpx_client(httpx.AsyncClient),
        )
        resp.raise_for_status()
    except GuardedAcquisitionError as exc:
        return ExtractedContent(url=url, error=f"jina: {exc.failure.code.value}")

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


def project_accepted_extraction(accepted_outcome) -> ExtractedContent:
    """Return the legacy readable projection without reclassifying S3 truth."""
    from argus.extraction.outcomes import AcceptedExtractionOutcome

    if not isinstance(accepted_outcome, AcceptedExtractionOutcome):
        raise TypeError("accepted_outcome must be an AcceptedExtractionOutcome")
    return accepted_outcome.to_legacy_extracted_content()


async def _extract_url_unpersisted(
    url: str,
    domain: str = None,
    mode: str = "default",
    *,
    content_type: str = "article",
    free_only: bool = False,
    allow_legacy_cache: bool = True,
    allow_legacy_cache_writes: bool = True,
    caller: str = "",
    request_id: str = "",
    operation_id: str | None = None,
    release_identity: str = "unknown-release",
    spend_gateway=None,
    evidence_authority: bool = False,
) -> ExtractedContent:
    """Extract clean content from a URL using the integrated fallback chain.

    Modes:
      - default: standard fallback chain
      - archive_ingest: optimized for durability and provenance (Atlas-style)

    Chain:
      URL shape → cache → rate limit → SSRF → auth → QG → trafilatura → QG →
      crawl4ai → QG → obscura → QG → playwright → QG → residential → QG →
      jina → QG → valyu_contents → QG → firecrawl → QG → you_contents → QG →
      wayback → QG → archive.is lookup → QG → return best result
    """
    config = get_config()
    from argus.extraction.domain_memory import get_domain_memory
    dm = get_domain_memory()

    # The shape/internal/literal check is intentionally DNS-free.  It lets a
    # previously cached result remain usable during a fail-closed DNS outage.
    cache_candidate, cache_reason = _cache_url_candidate(url)
    if not cache_candidate:
        return ExtractedContent(url=url, error=f"ssrf_blocked: {cache_reason}")

    # Cache check
    cache_key = _legacy_cache_key(url, content_type)
    cached = _cache.get(cache_key) if allow_legacy_cache else None
    if cached is not None:
        logger.debug("Extraction cache hit for %s", url[:60])
        result = copy.deepcopy(cached)
        result.cache_hit = True
        result.cache_source_extractor = (
            result.extractor.value if result.extractor else None
        )
        result.extractors_tried = ["cache"]
        result.attempts = [
            ExtractionAttempt(
                extractor="cache",
                status="success",
                latency_ms=0,
            )
        ]
        return result

    # Rate limiting is a local decision and must run before network DNS or
    # any extractor that may issue a network request.
    allowed, retry_after = _domain_limiter.is_allowed(url)
    if not allowed:
        return ExtractedContent(
            url=url,
            error=f"domain rate limit exceeded, retry after {retry_after}s",
        )

    # SSRF check
    safe, reason = is_safe_url(url)
    if not safe:
        return ExtractedContent(url=url, error=f"ssrf_blocked: {reason}")

    # YouTube watch pages are application shells rather than article pages.
    # Route them through the free metadata/caption adapter and do not spend
    # external extractor budget on a source those extractors cannot recover.
    from argus.extraction.youtube_extractor import normalize_youtube_input

    if normalize_youtube_input(url) is not None:
        from argus.extraction.youtube_extractor import extract_youtube

        result = await extract_youtube(url)
        _populate_provenance(result)
        if not result.extractors_tried:
            result.extractors_tried = ["youtube"]
        if not result.attempts:
            result.attempts = [
                ExtractionAttempt(
                    extractor="youtube",
                    status="failed" if result.error else "success",
                    latency_ms=0,
                    failure_summary=result.error,
                )
            ]
        if not result.error:
            if allow_legacy_cache_writes:
                _cache.put(cache_key, result)
        return result

    extractors_tried = []
    attempts: list[ExtractionAttempt] = []
    policy_skipped: set[str] = set()
    causal_failures = []
    best_result = None  # Keep the best (longest) result even if quality fails
    best_quality_result = None

    def track_attempt(name: str, result: ExtractedContent | None):
        """Track which extractors were tried and keep the best result."""
        extractors_tried.append(name)
        if result is None:
            return
        _populate_provenance(result)
        nonlocal best_result
        if result.text and result.word_count > 0:
            if best_result is None or result.word_count > best_result.word_count:
                best_result = result

    def track_quality_pass(result: ExtractedContent):
        """Keep a valid-but-incomplete result while later fallbacks run."""
        nonlocal best_quality_result
        if (
            best_quality_result is None
            or result.word_count > best_quality_result.word_count
        ):
            best_quality_result = result

    async def run_attempt(name: str, extractor, *args, **kwargs):
        """Execute and normalize one attempt for durable observability."""
        started = time.perf_counter()
        try:
            result = await extractor(*args, **kwargs)
        except Exception as exc:
            attempts.append(
                ExtractionAttempt(
                    extractor=name,
                    status="failed",
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    failure_summary=type(exc).__name__,
                )
            )
            raise
        track_attempt(name, result)
        failure = result.error if result is not None else "no_result"
        if result is not None and not failure and not result.text:
            failure = "no_content"
        attempts.append(
            ExtractionAttempt(
                extractor=name,
                status="failed" if failure else "success",
                latency_ms=round((time.perf_counter() - started) * 1000),
                failure_summary=failure,
                spend=getattr(result, "spend", None) if result is not None else None,
            )
        )
        if result is not None:
            result.attempts = list(attempts)
        return result

    def record_quality_outcome(
        result: ExtractedContent, passed: bool, reason: str
    ) -> None:
        if not attempts or passed:
            return
        previous = attempts[-1]
        attempts[-1] = ExtractionAttempt(
            extractor=previous.extractor,
            status="quality_failed",
            latency_ms=previous.latency_ms,
            failure_summary=reason or "quality_gate_failed",
            spend=previous.spend,
        )
        result.attempts = list(attempts)

    def record_policy_skip(name: str, reason: str) -> None:
        """Record a policy decision without invoking the extractor helper."""
        if name in policy_skipped:
            return
        policy_skipped.add(name)
        extractors_tried.append(name)
        attempts.append(
            ExtractionAttempt(
                extractor=name,
                status="policy_skipped",
                latency_ms=0,
                failure_summary=reason,
            )
        )

    def external_policy_reason(name: str) -> str | None:
        if free_only:
            return "free_only"
        # Every billable external step in the accepted path must be fenced by
        # the durable spend authority.  Legacy extraction keeps its historic
        # compatibility behavior; evidence-bound extraction fails closed
        # before dispatch when no gateway is supplied.
        if evidence_authority and name in {
            "jina",
            "valyu_contents",
            "firecrawl",
        }:
            return "provider_unavailable"
        if evidence_authority and name == "you_contents" and spend_gateway is None:
            return "spend_authority_unavailable"
        jina_disabled = not config.jina.enabled or os.getenv(
            "ARGUS_JINA_ENABLED", ""
        ).lower() in {"0", "false", "no"}
        if name == "jina" and jina_disabled:
            return "jina_disabled"
        return None

    paid_context = None
    if spend_gateway is not None:
        from argus.extraction.spend_gateway import ExtractionOperationContext
        from argus.models import ProviderName

        paid_operation_id = operation_id or uuid.uuid4().hex
        paid_context = ExtractionOperationContext(
            operation_id=paid_operation_id,
            request_id=request_id or paid_operation_id,
            plan_id=f"extract-plan-{paid_operation_id}",
            request_hash=hashlib.sha256(url.encode("utf-8")).hexdigest(),
            caller_identity=caller or "anonymous",
            caller_label=caller or "anonymous",
            release_identity=release_identity or "unknown-release",
            free_only=free_only,
            egress=config.node.egress_type or "unknown",
            request_class="extraction",
            plan_providers=(ProviderName.YOU,),
        )

    def reserve_paid_step(name: str):
        """Reserve a billable extraction step before dispatching its adapter."""

        if name != "you_contents" or spend_gateway is None or paid_context is None:
            return None
        from argus.models import ProviderName

        account = config.you.account_fingerprint
        return spend_gateway.reserve(
            paid_context,
            ProviderName.YOU,
            account,
            0.001,
            time.monotonic() + 60,
        )

    async def invoke_paid_step(reservation, extractor, target_url: str):
        """Dispatch a reserved paid adapter and settle only observed charges."""

        from argus.extraction.models import ExtractedContent

        try:
            paid_result = await extractor(target_url)
        except Exception:
            paid_result = ExtractedContent(
                url=target_url,
                error="you_contents: provider call failed",
            )
        provider_reference = getattr(paid_result, "provider_reference", None)
        charge = getattr(paid_result, "cost", None)
        charge_known = (
            isinstance(charge, (int, float))
            and not isinstance(charge, bool)
            and charge >= 0
            and math.isfinite(float(charge))
            and float(charge) > 0
            and isinstance(provider_reference, str)
            and bool(provider_reference)
        )
        outcome = "success" if not paid_result.error and paid_result.text else "failed"
        try:
            if charge_known:
                settlement = spend_gateway.settle(
                    reservation,
                    outcome,
                    float(charge),
                    provider_reference,
                    paid_context.request_hash,
                )
            else:
                settlement = spend_gateway.mark_uncertain(
                    reservation,
                    "provider charge or reference is unresolved",
                )
        except Exception:
            try:
                settlement = spend_gateway.mark_uncertain(
                    reservation,
                    "provider settlement is unresolved",
                )
            except Exception:
                settlement = None

        if (
            settlement is not None
            and settlement.status == "settled"
            and settlement.failure is None
            and settlement.charge is not None
            and reservation.attempt_id
        ):
            from argus.extraction.outcomes import SpendEvidence

            paid_result.cost = float(settlement.charge)
            paid_result.provider_reference = settlement.provider_reference
            paid_result.spend = SpendEvidence(
                actual_usd=Decimal(str(settlement.charge)),
                reserved_usd=Decimal(str(reservation.reserved_charge)),
                spend_attempt_ref=reservation.attempt_id,
            )
            return paid_result

        failure = getattr(settlement, "failure", None) if settlement else None
        paid_result.failure = failure
        paid_result.error = (
            failure.code.value
            if failure is not None
            else "charge_uncertain"
        )
        paid_result.text = ""
        paid_result.word_count = 0
        return paid_result

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
                res_result = await run_attempt(
                    "residential", extract_residential, url, domain=domain or ""
                )
                if res_result.text and not res_result.error:
                    passed, r_reason = _run_quality_gate(
                        res_result.text,
                        url,
                        "residential",
                        content_type=content_type,
                    )
                    res_result.quality_passed = passed
                    res_result.quality_reason = r_reason if not passed else None
                    record_quality_outcome(res_result, passed, r_reason)
                    res_result.extractors_tried = list(extractors_tried)
                    if passed:
                        track_quality_pass(res_result)
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

            result = await run_attempt(
                "auth", extract_authenticated, url, domain
            )
            if result and result.text and not result.error:
                passed, reason = _run_quality_gate(
                    result.text,
                    url,
                    "auth",
                    content_type=content_type,
                )
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                record_quality_outcome(result, passed, reason)
                result.extractors_tried = list(extractors_tried)
                if passed:
                    track_quality_pass(result)
                    result.completeness_result = assess_completeness(result.text, url)
                    if not _should_continue_for_completeness(result, step=1):
                        logger.info("Extracted %s via auth (%d words)", url[:60], result.word_count)
                        if allow_legacy_cache_writes:
                            _cache.put(cache_key, result)
                        return result
        except Exception as e:
            logger.warning("Auth extraction failed for %s: %s", url[:60], e)

    # Policy-driven residential trigger (Early)
    if use_residential_early and res_policy != "off":
        res_res = await run_residential_step()
        if res_res:
            if allow_legacy_cache_writes:
                _cache.put(cache_key, res_res)
            return res_res

    # Local Extractors (Steps 2-5)
    for step_num, step_name, static_extractor in [
        (2, "trafilatura", _extract_trafilatura),
        (3, "crawl4ai", None),
        (4, "obscura", None),
        (5, "playwright", None),
    ]:
        try:
            current_extractor = static_extractor
            if step_name == "crawl4ai":
                if os.getenv("ARGUS_CRAWL4AI_ENABLED", "").lower() not in ("1", "true"):
                    continue
                from argus.extraction.crawl4ai_extractor import extract_crawl4ai as current_extractor
            elif step_name == "obscura":
                from argus.extraction.obscura_extractor import extract_obscura as current_extractor
            elif step_name == "playwright":
                from argus.extraction.playwright_extractor import extract_playwright as current_extractor

            result = await run_attempt(step_name, current_extractor, url)
            if result.text and not result.error:
                passed, reason = _run_quality_gate(
                    result.text,
                    url,
                    step_name,
                    content_type=content_type,
                )
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                record_quality_outcome(result, passed, reason)
                result.extractors_tried = list(extractors_tried)
                if passed:
                    track_quality_pass(result)
                    result.completeness_result = assess_completeness(result.text, url)
                    if not _should_continue_for_completeness(result, step=step_num):
                        logger.info("Extracted %s via %s (%d words)", url[:60], step_name, result.word_count)
                        if allow_legacy_cache_writes:
                            _cache.put(cache_key, result)
                        return result
        except Exception as e:
            logger.warning("%s failed for %s: %s", step_name.capitalize(), url[:60], e)

    # Step 6: Residential extraction (Fallback if not already tried early)
    if not use_residential_early and res_policy != "off":
        res_res = await run_residential_step()
        if res_res:
            if allow_legacy_cache_writes:
                _cache.put(cache_key, res_res)
            return res_res

    # External APIs (Steps 7-10)
    external_steps = [
        (7, "jina", _extract_jina),
        (8, "valyu_contents", None),
        (9, "firecrawl", None),
        (10, "you_contents", None),
    ]

    for step_num, step_name, static_extractor in external_steps:
        reason = external_policy_reason(step_name)
        # For archive_ingest mode, we try archive recovery before paid APIs
        if mode == "archive_ingest" and step_num == 7:
            # We'll come back to paid APIs if archives fail
            if reason is not None:
                record_policy_skip(step_name, reason)
            break
        if reason is not None:
            record_policy_skip(step_name, reason)
            continue

        try:
            current_extractor = static_extractor
            if step_name == "valyu_contents":
                from argus.extraction.valyu_extractor import extract_valyu_contents as current_extractor
            elif step_name == "firecrawl":
                from argus.extraction.firecrawl_extractor import extract_firecrawl as current_extractor
            elif step_name == "you_contents":
                if os.getenv("ARGUS_YOU_CONTENTS_ENABLED", "").lower() not in ("1", "true"):
                    continue
                from argus.extraction.you_extractor import extract_you_contents as current_extractor

            reservation = reserve_paid_step(step_name)
            if reservation is not None and not reservation.allowed:
                record_policy_skip(
                    step_name,
                    (
                        reservation.failure.code.value
                        if reservation.failure is not None
                        else "spend_denied"
                    ),
                )
                if reservation.failure is not None:
                    causal_failures.append(reservation.failure)
                continue
            if reservation is not None:
                result = await run_attempt(
                    step_name,
                    invoke_paid_step,
                    reservation,
                    current_extractor,
                    url,
                )
            else:
                result = await run_attempt(step_name, current_extractor, url)
            if result is not None and result.failure is not None:
                causal_failures.append(result.failure)
            if result.text and not result.error:
                passed, reason = _run_quality_gate(
                    result.text,
                    url,
                    step_name,
                    content_type=content_type,
                )
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                record_quality_outcome(result, passed, reason)
                result.extractors_tried = list(extractors_tried)
                if passed:
                    track_quality_pass(result)
                    result.completeness_result = assess_completeness(result.text, url)
                    if step_name == "jina":
                        _track_jina_usage(result.word_count)
                    if not _should_continue_for_completeness(result, step=step_num):
                        logger.info("Extracted %s via %s (%d words)", url[:60], step_name, result.word_count)
                        if allow_legacy_cache_writes:
                            _cache.put(cache_key, result)
                        return result
        except Exception as e:
            logger.warning("%s failed for %s: %s", step_name.capitalize(), url[:60], e)

    # Step 11 & 12: Existing-archive recovery. External archive creation is a
    # separate explicitly authorized operation and is never part of this chain.
    for step_num, step_name, extractor_func in [
        (11, "wayback", None),
        (12, "archive_is", None),
    ]:
        try:
            if step_name == "wayback":
                from argus.extraction.wayback_extractor import extract_wayback as extractor_func
            elif step_name == "archive_is":
                from argus.extraction.archive_extractor import extract_archive_is as extractor_func

            result = await run_attempt(step_name, extractor_func, url)
            if result.text and not result.error:
                passed, reason = _run_quality_gate(
                    result.text,
                    url,
                    step_name,
                    content_type=content_type,
                )
                result.quality_passed = passed
                result.quality_reason = reason if not passed else None
                record_quality_outcome(result, passed, reason)
                result.extractors_tried = list(extractors_tried)
                if passed:
                    track_quality_pass(result)
                    result.completeness_result = assess_completeness(result.text, url)
                    logger.info("Extracted %s via %s (%d words)", url[:60], step_name, result.word_count)
                    if allow_legacy_cache_writes:
                        _cache.put(cache_key, result)
                    return result
        except Exception as e:
            logger.warning("%s failed for %s: %s", step_name.capitalize(), url[:60], e)

    # If archive_ingest and we haven't tried paid APIs yet, try them now
    if mode == "archive_ingest":
        for step_num, step_name, static_extractor in external_steps:
            reason = external_policy_reason(step_name)
            if reason is not None:
                record_policy_skip(step_name, reason)
                continue
            try:
                current_extractor = static_extractor
                if step_name == "jina":
                    current_extractor = _extract_jina
                elif step_name == "valyu_contents":
                    from argus.extraction.valyu_extractor import extract_valyu_contents as current_extractor
                elif step_name == "firecrawl":
                    from argus.extraction.firecrawl_extractor import extract_firecrawl as current_extractor
                elif step_name == "you_contents":
                    if os.getenv("ARGUS_YOU_CONTENTS_ENABLED", "").lower() not in ("1", "true"):
                        continue
                    from argus.extraction.you_extractor import extract_you_contents as current_extractor

                reservation = reserve_paid_step(step_name)
                if reservation is not None and not reservation.allowed:
                    record_policy_skip(
                        step_name,
                        (
                            reservation.failure.code.value
                            if reservation.failure is not None
                            else "spend_denied"
                        ),
                    )
                    if reservation.failure is not None:
                        causal_failures.append(reservation.failure)
                    continue
                if reservation is not None:
                    result = await run_attempt(
                        step_name,
                        invoke_paid_step,
                        reservation,
                        current_extractor,
                        url,
                    )
                else:
                    result = await run_attempt(step_name, current_extractor, url)
                if result is not None and result.failure is not None:
                    causal_failures.append(result.failure)
                if result.text and not result.error:
                    passed, reason = _run_quality_gate(
                        result.text,
                        url,
                        step_name,
                        content_type=content_type,
                    )
                    result.quality_passed = passed
                    result.quality_reason = reason if not passed else None
                    record_quality_outcome(result, passed, reason)
                    result.extractors_tried = list(extractors_tried)
                    if passed:
                        track_quality_pass(result)
                        result.completeness_result = assess_completeness(result.text, url)
                        if step_name == "jina":
                            _track_jina_usage(result.word_count)
                        if not _should_continue_for_completeness(result, step=step_num):
                            logger.info("Extracted %s via %s (%d words)", url[:60], step_name, result.word_count)
                            if allow_legacy_cache_writes:
                                _cache.put(cache_key, result)
                            return result
            except Exception as e:
                logger.warning("%s failed for %s: %s", step_name.capitalize(), url[:60], e)

    # A quality-passed result may have triggered completeness fallbacks. If none
    # recovered a more complete page, preserve that quality decision rather
    # than falsely reporting that every quality gate failed.
    if best_quality_result:
        best_quality_result.quality_passed = True
        best_quality_result.quality_reason = None
        best_quality_result.extractors_tried = extractors_tried
        best_quality_result.attempts = list(attempts)
        if allow_legacy_cache_writes:
            _cache.put(cache_key, best_quality_result)
        logger.warning(
            "Completeness fallbacks exhausted for %s, returning valid incomplete "
            "content (%d words via %s)",
            url[:60],
            best_quality_result.word_count,
            best_quality_result.extractor,
        )
        return best_quality_result

    # All extractors tried — return best result even if quality failed
    if best_result:
        best_result.quality_passed = False
        best_result.quality_reason = "all_extractors_quality_failed"
        best_result.extractors_tried = extractors_tried
        best_result.attempts = list(attempts)
        if best_result.text and best_result.completeness_result is None:
            best_result.completeness_result = assess_completeness(best_result.text, url)
        if allow_legacy_cache_writes:
            _cache.put(cache_key, best_result)
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
        attempts=list(attempts),
    )
    if causal_failures:
        result.failure = causal_failures[-1]
        result.error = causal_failures[-1].code.value
    _populate_provenance(result)
    return result


async def extract_url(
    url: str,
    domain: str = None,
    mode: str = "default",
    *,
    content_type: str = "article",
    caller: str = "",
    free_only: bool = False,
    repository=None,
    authority_capability: object | None = None,
    use_evidence_authority: bool = False,
    request_id: str | None = None,
    spend_gateway=None,
    release_identity: str = "unknown-release",
    operation_id: str | None = None,
) -> ExtractedContent:
    """Extract and durably record one logical operation before returning."""
    from argus.authority import extraction_execution_allowed

    extraction_execution_allowed(authority_capability=authority_capability)
    started = time.perf_counter()
    operation_id = operation_id or uuid.uuid4().hex
    request_id = request_id or operation_id
    if repository is None and use_evidence_authority:
        from argus.persistence.search_ledger import (
            create_search_ledger_repository,
        )

        repository = create_search_ledger_repository()
    accepted_cache_decision = None
    accepted_cache_now = None
    if use_evidence_authority:
        from argus.extraction.outcomes import CacheOutcome

        cache_identity = _accepted_cache_identity(
            url,
            mode,
            free_only,
            content_type=content_type,
        )
        accepted_cache_now = datetime.now(timezone.utc)
        cache_decision = _accepted_cache.decide(
            cache_identity,
            acceptance_repository=repository,
            now=accepted_cache_now,
        )
        if cache_decision.outcome is CacheOutcome.HIT_INELIGIBLE:
            accepted_cache_decision = cache_decision
        if cache_decision.outcome is CacheOutcome.HIT_ELIGIBLE:
            origin = repository.load_extraction_outcome_by_receipt(
                cache_decision.origin_evidence.acceptance_receipt.receipt_ref
            )
            if origin is not None:
                from dataclasses import replace

                accepted = replace(origin, cache_decision=cache_decision)
                projected = accepted.to_legacy_extracted_content()
                projected.url = url
                projected.cache_hit = True
                projected.cache_source_extractor = (
                    projected.extractor.value if projected.extractor else None
                )
                projected.extractors_tried = ["cache"]
                projected.attempts = [
                    ExtractionAttempt(
                        extractor="cache",
                        status="success",
                        latency_ms=0,
                    )
                ]
                return projected
    unpersisted_kwargs = {
        "domain": domain,
        "mode": mode,
        "free_only": free_only,
        "allow_legacy_cache": not use_evidence_authority,
        # Defer legacy cache publication until the extraction ledger accepts
        # the result.  A failed persistence write must not leave an artifact
        # that appears durable on the next request.
        "allow_legacy_cache_writes": False,
        "caller": caller,
        "request_id": request_id,
        "operation_id": operation_id,
        "release_identity": release_identity,
        "spend_gateway": spend_gateway,
        "evidence_authority": use_evidence_authority,
    }
    # Keep the default call contract for injected legacy test extractors and
    # direct callers while forwarding the additive webpage profile.
    if content_type != "article":
        unpersisted_kwargs["content_type"] = content_type
    result = await _extract_url_unpersisted(url, **unpersisted_kwargs)
    if use_evidence_authority:
        projected = _finalize_accepted_extraction(
            result,
            url=url,
            mode=mode,
            caller=caller,
            free_only=free_only,
            request_id=request_id,
            operation_id=operation_id,
            release_identity=release_identity,
            spend_gateway=spend_gateway,
            latency_ms=round((time.perf_counter() - started) * 1000),
            repository=repository,
            content_type=content_type,
            cache_decision=accepted_cache_decision,
            cache_now=accepted_cache_now,
        )
        loader = getattr(repository, "load_extraction_outcome_by_receipt", None)
        if callable(loader) and projected.acceptance_receipt is not None:
            accepted = loader(projected.acceptance_receipt.receipt_ref)
            if accepted is not None:
                _accepted_cache.put(
                    _accepted_cache_identity(
                        url,
                        mode,
                        free_only,
                        content_type=content_type,
                    ),
                    accepted,
                    acceptance_repository=repository,
                )
        return projected
    if repository is None:
        from argus.persistence.search_ledger import (
            create_search_ledger_repository,
        )

        repository = create_search_ledger_repository()
    receipt = repository.record_extraction(
        url=url,
        domain=domain,
        mode=mode,
        caller=caller,
        result=result,
        latency_ms=round((time.perf_counter() - started) * 1000),
    )
    result.extraction_run_id = receipt.extraction_run_id
    _cache.put(_legacy_cache_key(url, content_type), result)
    return result


def _finalize_accepted_extraction(
    result: ExtractedContent,
    *,
    url: str,
    mode: str,
    caller: str,
    request_id: str,
    latency_ms: int,
    repository,
    free_only: bool = False,
    content_type: str = "article",
    cache_decision=None,
    cache_now: datetime | None = None,
    operation_id: str | None = None,
    release_identity: str = "unknown-release",
    spend_gateway=None,
) -> ExtractedContent:
    """Adapt bounded chain evidence into the canonical extraction finalizer."""
    from argus.extraction.finalizer import finalize_extraction
    from argus.extraction.outcomes import (
        ArtifactEvaluation,
        AttemptOutcome,
        CacheDecision,
        CacheOutcome,
        ExtractionCandidate,
        ExtractionPlan,
        ExtractionProvenance,
        ExtractionRequest,
        ExtractorDecision,
        ExtractorExecutionDecision,
        OutcomePolicy,
        RawExtractionResult,
        TerminalCause,
        TerminalCauseKind,
    )

    run_id = operation_id or uuid.uuid4().hex
    selected = result.extractor.value if result.extractor else None
    metered_extractors = frozenset(
        {"jina", "valyu_contents", "firecrawl", "you_contents"}
    )
    candidate_names = list(
        dict.fromkeys(
            [
                *(attempt.extractor for attempt in result.attempts),
                *([selected] if selected else []),
            ]
        )
    )
    def evidence_label(value: object, fallback: str) -> str:
        label = fallback if value in {None, ""} else str(value)
        if re.fullmatch(r"[a-z][a-z0-9_:-]{0,63}", label) is None:
            raise ValueError("invalid extraction evidence label")
        return label

    provenance = ExtractionProvenance(
        source_type=evidence_label(result.source_type, "normalized_text"),
        egress=evidence_label(result.egress, "unknown"),
        machine=evidence_label(result.machine, "unknown"),
    )
    policy_reasons = {}
    for attempt in result.attempts:
        if attempt.status != "policy_skipped":
            continue
        reason = attempt.failure_summary
        policy_reasons[attempt.extractor] = (
            reason
            if reason
            in {
                "free_only",
                "jina_disabled",
                "caller_tier_cap",
                "provider_unavailable",
                "spend_authority_unavailable",
                "spend_denied",
                "provider_unready",
                "charge_uncertain",
                "policy_skipped",
            }
            else "policy_skipped"
        )
    def attempt_outcome(attempt) -> AttemptOutcome:
        failure = getattr(result, "failure", None)
        failure_code = getattr(getattr(failure, "code", None), "value", None)
        if failure_code in {
            "spend_denied",
            "provider_unready",
            "charge_uncertain",
        }:
            # The closed extraction taxonomy predates the shared failure
            # taxonomy.  BALANCE_EXHAUSTED preserves the UNREADY terminal
            # mapping while the typed failure remains on the legacy result.
            return AttemptOutcome.BALANCE_EXHAUSTED
        if failure_code == "policy_rejected":
            return AttemptOutcome.PROVIDER_POLICY_REJECTED
        if failure_code == "timeout":
            return AttemptOutcome.TIMEOUT
        if selected == attempt.extractor and bool(result.text):
            return AttemptOutcome.CONTENT
        if attempt.status == "success":
            return AttemptOutcome.EMPTY
        if attempt.status == "quality_failed":
            return AttemptOutcome.PARSE_ERROR
        return AttemptOutcome.UNKNOWN_FAILURE

    def attempt_spend(attempt):
        # Free/local attempts have no spend record.  Paid attempts must carry
        # evidence attached by the gateway; the finalizer rejects a paid
        # artifact if this value is absent.
        return attempt.spend

    decisions = []
    for ordinal, attempt in enumerate(result.attempts):
        if attempt.status == "policy_skipped":
            reason = policy_reasons[attempt.extractor]
            decisions.append(
                ExtractorDecision(
                    ordinal=ordinal,
                    extractor=attempt.extractor,
                    decision=ExtractorExecutionDecision.POLICY_SKIPPED,
                    policy_rule_ref=f"extract-{reason}-v1",
                )
            )
            continue
        decisions.append(
            ExtractorDecision(
                ordinal=ordinal,
                extractor=attempt.extractor,
                decision=ExtractorExecutionDecision.INVOKED,
                attempt_outcome=attempt_outcome(attempt),
                latency_ms=max(0, attempt.latency_ms),
                provenance=provenance,
                spend=attempt_spend(attempt),
            )
        )
    if selected and not any(
        step.extractor == selected and step.attempt_outcome is AttemptOutcome.CONTENT
        for step in decisions
    ):
        decisions.append(
            ExtractorDecision(
                ordinal=len(decisions),
                extractor=selected,
                decision=ExtractorExecutionDecision.INVOKED,
                attempt_outcome=AttemptOutcome.CONTENT,
                latency_ms=0,
                provenance=provenance,
                spend=None,
            )
        )
    preflight_outcome = None
    preflight_authority_ref = None
    if not decisions and not result.text:
        error = (result.error or "").lower()
        if error.startswith("ssrf_blocked"):
            preflight_outcome = CanonicalOutcome.POLICY_REJECTED
            preflight_authority_ref = "extraction-ssrf-policy-v1"
        elif "domain rate limit" in error:
            preflight_outcome = CanonicalOutcome.UNREADY
            preflight_authority_ref = "extraction-domain-rate-limit-v1"
        else:
            preflight_outcome = CanonicalOutcome.UNREADY
            preflight_authority_ref = "extraction-preflight-unclassified-v1"
    artifact = None
    if result.text:
        completeness = result.completeness_result or assess_completeness(
            result.text, url
        )
        artifact = ArtifactEvaluation(
            artifact_ref=f"artifact-{run_id}",
            content_identity="sha256:"
            + hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
            text=result.text,
            title=result.title,
            author=result.author,
            published_date=result.date,
            word_count=result.word_count,
            quality_passed=result.quality_passed,
            is_complete=completeness.is_complete,
            completeness_confidence=Decimal(str(completeness.confidence)),
            completeness_signals=tuple(
                dict.fromkeys(
                    evidence_label(signal, "unknown_signal")
                    for signal in completeness.signals[:16]
                )
            ),
            completeness_assessment_version="completeness-v1",
            completeness_recommended_action=completeness.recommended_action,
            provenance=provenance,
        )
    terminal = None
    if artifact is None:
        if preflight_outcome is not None:
            terminal = TerminalCause(
                kind=TerminalCauseKind.PREFLIGHT,
                preflight_outcome=preflight_outcome,
                authority_ref=preflight_authority_ref,
            )
        else:
            outcomes = tuple(
                dict.fromkeys(
                    step.attempt_outcome
                    for step in decisions
                    if step.attempt_outcome is not None
                )
            ) or (AttemptOutcome.UNKNOWN_FAILURE,)
            terminal = TerminalCause(
                kind=TerminalCauseKind.CHAIN_EXHAUSTED,
                invoked_ordinals=tuple(
                    step.ordinal
                    for step in decisions
                    if step.decision is ExtractorExecutionDecision.INVOKED
                ),
                distinct_attempt_outcomes=outcomes,
            )
    plan = ExtractionPlan(
        plan_ref=f"extract-plan-{run_id}",
        normalized_url=url,
        access_scope="public",
        mode=mode,
        candidates=tuple(
            ExtractionCandidate(
                extractor=name,
                eligible=not any(
                    attempt.extractor == name
                    and attempt.status == "policy_skipped"
                    for attempt in result.attempts
                ),
                spend_class=(
                    "policy_skipped"
                    if any(
                        attempt.extractor == name
                        and attempt.status == "policy_skipped"
                        for attempt in result.attempts
                    )
                    else "metered"
                    if name in metered_extractors or result.cost > 0
                    else "free"
                ),
                policy_rule_ref=(
                    f"extract-{policy_reasons[name]}-v1"
                    if name in policy_reasons
                    else None
                ),
            )
            for name in dict.fromkeys(candidate_names)
        ),
        cache_policy_ref="accepted-extraction-cache-v1",
        extraction_plan_version="1",
        quality_policy_version=_quality_policy_version(content_type),
        completeness_policy_version="completeness-v1",
        partial_allowed=True,
        deadline_ms=120_000,
        caller=caller,
        profile="free" if free_only else "autonomous",
        privacy_scope="public",
        release_identity=release_identity or "unknown-release",
        effective_max_provider_tier=0 if free_only else 3,
        provider_restrictions=(),
        eligible_extractors=(),
        freshness_window_seconds=604_800,
        original_evidence_ref=None,
    )
    accepted = finalize_extraction(
        ExtractionRequest(
            request_id=request_id,
            extraction_run_id=run_id,
            normalized_url=url,
            access_scope="public",
            caller=caller,
            profile="free" if free_only else "autonomous",
            privacy_scope="public",
            release_identity=release_identity or "unknown-release",
        ),
        plan,
        RawExtractionResult(
            cache_decision=cache_decision
            or CacheDecision(outcome=CacheOutcome.MISS),
            steps=tuple(decisions),
            artifact=artifact,
            selected_extractor=selected if artifact is not None else None,
            terminal_cause=terminal,
            operation_latency_ms=latency_ms,
        ),
        OutcomePolicy(version="extraction-outcome-v1"),
        repository=repository,
        clock=lambda: (cache_now or datetime.now(timezone.utc)).isoformat(),
    )
    projected = accepted.to_legacy_extracted_content()
    projected.failure = getattr(result, "failure", None)
    projected.provider_reference = getattr(result, "provider_reference", None)
    return projected


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
