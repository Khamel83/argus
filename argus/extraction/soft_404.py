"""
Soft 404 Detection - Detect pages that return 200 but are actually error pages.

Ported from Atlas modules/ingest/robust_fetcher.py.
Integrated into the quality gate — soft-404 content is rejected.
"""

import re

# Patterns that indicate a page is actually a 404/error despite 200 status
SOFT_404_PATTERNS = [
    r"page\s*(not\s*found|doesn't\s*exist|could\s*not\s*be\s*found)",
    r"(404|not\s*found)\s*error",
    r"(this\s*)?(page|article|content)\s*(has\s*been\s*)?(deleted|removed|expired)",
    r"no\s*longer\s*available",
    r"content\s*unavailable",
    r"we\s*couldn't\s*find\s*(that|the)\s*page",
    r"sorry,?\s*we\s*can('|no)t\s*find",
    r"the\s*requested\s*(url|page|resource)\s*was\s*not\s*found",
    r"oops[!,]?\s*(page|that)?\s*not\s*found",
    r"this\s*link\s*(may\s*be\s*)?(broken|expired)",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SOFT_404_PATTERNS]


def is_soft_404(text: str) -> bool:
    """
    Detect if extracted text is a soft 404 page.

    Args:
        text: Extracted text content (not raw HTML)

    Returns:
        True if this appears to be a soft 404
    """
    if not text or len(text) < 200:
        return True

    # Check first 5000 chars where error messages appear
    check_text = text[:5000].lower()
    matches = 0
    for pattern in _COMPILED_PATTERNS:
        if pattern.search(check_text):
            matches += 1

    # Require at least 1 match — single strong pattern is enough for text content
    # (HTML version was more conservative because navigation text has false positives)
    return matches >= 1


def soft_404_check(content: str, url: str = "") -> tuple[bool, str]:
    """
    Quality-gate-compatible soft 404 check.

    Returns:
        (is_soft_404, reason) tuple — if is_soft_404 is True, reject content
    """
    if is_soft_404(content):
        return True, "soft_404: page appears to be an error/deleted page"
    return False, ""
