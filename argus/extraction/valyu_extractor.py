"""
Valyu Contents API extractor.

Extracts clean content from URLs via POST https://api.valyu.ai/v1/contents.
$0.001/URL — cheapest external extraction option. Uses same Valyu API key as search.
Gated by ARGUS_VALYU_API_KEY env var (reuses Valyu search config).
"""

import os

from argus.acquisition.guarded import guarded_http_request
from argus.config import get_config
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.logging import get_logger

logger = get_logger("extraction.valyu")

VALYU_CONTENTS_URL = "https://api.valyu.ai/v1/contents"
TIMEOUT = int(os.getenv("ARGUS_EXTRACTION_TIMEOUT_SECONDS", "15"))


async def extract_valyu_contents(url: str) -> ExtractedContent:
    """Extract content from a URL using the Valyu Contents API."""
    return ExtractedContent(
        url=url,
        error="valyu_contents disabled: durable spend reservation is required",
    )

    # Kept below for re-enablement once extraction uses the spend gateway.
    config = get_config()
    if not config.valyu.api_key:
        return ExtractedContent(url=url, error="valyu_contents: no API key configured")

    headers = {
        "X-API-Key": config.valyu.api_key,
        "Content-Type": "application/json",
    }
    body = {
        "urls": [url],
        "extract_effort": "auto",
    }

    try:
        resp = await guarded_http_request(
            VALYU_CONTENTS_URL,
            method="POST",
            json_body=body,
            headers=headers,
            profile="third_party_fetch",
            operation_class="third_party",
            caller_principal="valyu-contents",
            request_id="extract-valyu-contents",
            timeout=TIMEOUT,
            target_url=url,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            return ExtractedContent(url=url, error=f"valyu_contents: {data.get('error', 'unknown error')}")

        results = data.get("results", [])
        if not results:
            return ExtractedContent(url=url, error="valyu_contents: no results")

        page = results[0]
        if page.get("status") != "success":
            return ExtractedContent(url=url, error=f"valyu_contents: {page.get('error', 'extraction failed')}")

        text = page.get("content", "")
        if not text or len(text.strip()) < 50:
            return ExtractedContent(url=url, error="valyu_contents: content too short")

        # Record usage: $0.001 per URL
        try:
            from argus.broker.budgets import BudgetTracker
            from argus.models import ProviderName
            import os

            tracker = BudgetTracker(persist_path=os.environ.get("ARGUS_BUDGET_DB_PATH"))
            try:
                tracker.record_usage(ProviderName.VALYU, cost=0.001)
            finally:
                tracker.close()
        except Exception as e:
            logger.warning("Failed to record Valyu extraction usage: %s", e)

        return ExtractedContent(
            url=url,
            title=page.get("title", ""),
            text=text.strip(),
            author="",
            date=page.get("publication_date") or None,
            word_count=len(text.split()),
            extractor=ExtractorName.VALYU_CONTENTS,
        )
    except Exception as e:
        logger.warning("Valyu contents extraction failed for %s: %s", url[:60], e)
        return ExtractedContent(url=url, error=f"valyu_contents: {e}")
