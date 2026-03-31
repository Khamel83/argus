"""
URL deduplication and canonical URL normalization.
"""

from urllib.parse import urlparse

from argus.models import SearchResult


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication purposes."""
    try:
        parsed = urlparse(url)
        # Lowercase scheme and netloc
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        # Remove www prefix
        if netloc.startswith("www."):
            netloc = netloc[4:]
        # Remove trailing slash
        path = parsed.path.rstrip("/") or "/"
        # Sort query params (if present)
        query = parsed.query
        if query:
            params = sorted(query.split("&"))
            query = "&".join(params)

        # Strip common tracking params
        if query:
            params = [p for p in query.split("&") if not p.startswith(("utm_", "ref=", "fbclid", "gclid"))]
            query = "&".join(params) if params else ""

        normalized = f"{scheme}://{netloc}{path}"
        if query:
            normalized += f"?{query}"
        return normalized
    except Exception:
        return url


def extract_domain(url: str) -> str:
    """Extract the domain from a URL."""
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    """Deduplicate results by normalized URL.

    Keeps the first occurrence (highest RRF score should already be first).
    """
    seen_urls = set()
    deduped = []

    for result in results:
        normalized = normalize_url(result.url)
        if normalized not in seen_urls:
            seen_urls.add(normalized)
            if not result.domain:
                result.domain = extract_domain(result.url)
            deduped.append(result)

    return deduped
