"""
Yahoo Search scraping provider.

No API key required. Fragile — Yahoo HTML structure can change.
Auto-disabled by health tracker after repeated failures.
Useful as a Tier 0 fallback for pip-only deploys when SearXNG isn't running.

lxml is available as a transitive dependency via trafilatura.
"""

import re
import time
from typing import List, Tuple
from urllib.parse import unquote, urljoin, urlparse

import httpx

from argus.config import ProviderConfig
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchResult,
    SearchQuery,
)
from argus.providers.base import BaseProvider
from argus.broker.provider_evidence import safe_redirect_request
from argus.broker.provider_evidence import FailureCategory

logger = get_logger("providers.yahoo")

YAHOO_SEARCH_URL = "https://search.yahoo.com/search"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
}


def _unwrap_yahoo_url(href: str) -> str:
    """Yahoo wraps result URLs through their redirect system. Extract the real URL."""
    if not href:
        return href
    # Pattern: /RU=https%3a%2f%2factual-url/RK= in the path
    match = re.search(r"/RU=([^/;]+)/R[KS]=", href)
    if match:
        return unquote(match.group(1))
    # Fallback: bare /RU= without following RK
    match = re.search(r"/RU=([^/;]+)", href)
    if match:
        return unquote(match.group(1))
    return href


class YahooProvider(BaseProvider):
    """Scrapes Yahoo Search. No API key required."""

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config or ProviderConfig(enabled=True, timeout_seconds=15)

    @property
    def name(self) -> ProviderName:
        return ProviderName.YAHOO

    def is_available(self) -> bool:
        return self._config.enabled

    def status(self) -> ProviderStatus:
        if not self._config.enabled:
            return ProviderStatus.DISABLED_BY_CONFIG
        return ProviderStatus.ENABLED

    async def _get_bounded(self, client, params):
        response = await client.get(YAHOO_SEARCH_URL, params=params)
        source_url = YAHOO_SEARCH_URL
        for redirect_count in range(1, 5):
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            if not isinstance(location, str):
                return response
            request = safe_redirect_request(
                source_url=source_url,
                destination_url=urljoin(source_url, location),
                headers=_HEADERS,
                redirect_count=redirect_count,
                max_redirects=3,
            )
            source_url = request.url
            response = await client.get(request.url, headers=request.headers)
        return response

    async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]:
        start = time.monotonic()

        try:
            provider_query = query.query
            freshness = self._freshness_params(query)
            qualifier = freshness.get("query_qualifier")
            if qualifier:
                start, _, end = str(qualifier).partition("..")
                if start:
                    provider_query += f" after:{start}"
                if end:
                    provider_query += f" before:{end}"
            params = {"p": provider_query, "n": min(query.max_results, 10), "ei": "UTF-8"}
            async with httpx.AsyncClient(
                timeout=self._attempt_timeout(query),
                headers=_HEADERS,
                follow_redirects=False,
            ) as client:
                resp = await self._get_bounded(client, params)
                resp.raise_for_status()

            results = self._parse(resp.text, query.max_results)

            if not results:
                return self._typed_failure_batch(
                    FailureCategory.PARSE_ERROR,
                    "Yahoo success page did not match the parser contract",
                    started_at=start,
                )

            return self._normalized_batch(
                {
                    "results": [
                        {
                            "url": item.url,
                            "title": item.title,
                            "snippet": item.snippet,
                        }
                        for item in results
                    ]
                },
                query,
                started_at=start,
            )

        except Exception as e:
            logger.warning("Yahoo search failed: %s", type(e).__name__)
            return self._failure_batch(e, started_at=start)

    def _parse(self, html_text: str, max_results: int) -> List[SearchResult]:
        try:
            from lxml import html as lxml_html
            return self._parse_lxml(lxml_html.fromstring(html_text), max_results)
        except ImportError:
            logger.debug("lxml not available, using regex fallback")
            return self._parse_regex(html_text, max_results)

    def _parse_lxml(self, tree, max_results: int) -> List[SearchResult]:
        results = []
        # Yahoo result containers: <div class="dd ... algo-sr ...">
        # Note: "dd" is a CSS class here, NOT the HTML <dd> tag.
        nodes = tree.xpath('//div[contains(@class,"algo-sr")]')[:max_results]

        for i, node in enumerate(nodes):
            # The title link is the <a> inside compTitle
            title_a = node.xpath('.//*[contains(@class,"compTitle")]//a[@href]')
            if not title_a:
                continue
            a = title_a[0]
            href = _unwrap_yahoo_url(a.get("href", ""))
            if not href.startswith("http"):
                continue

            # Title is in the <h3> inside that same <a>
            h3 = a.xpath('.//h3')
            title = h3[0].text_content().strip() if h3 else a.text_content().strip()

            # Description is in compText sibling
            desc_nodes = node.xpath('.//*[contains(@class,"compText")]')
            snippet = desc_nodes[0].text_content().strip() if desc_nodes else ""

            try:
                domain = urlparse(href).netloc
            except Exception:
                domain = ""

            results.append(SearchResult(
                url=href,
                title=title,
                snippet=snippet[:300],
                domain=domain,
                provider=self.name,
                score=0.0,
                raw_rank=i,
            ))

        return results

    def _parse_regex(self, html_text: str, max_results: int) -> List[SearchResult]:
        """Fallback parser when lxml is unavailable."""
        results = []
        pattern = re.compile(
            r'<h3[^>]*class="[^"]*title[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        for i, m in enumerate(pattern.finditer(html_text)):
            if i >= max_results:
                break
            href = _unwrap_yahoo_url(m.group(1))
            if not href.startswith("http"):
                continue
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            try:
                domain = urlparse(href).netloc
            except Exception:
                domain = ""
            results.append(SearchResult(
                url=href,
                title=title,
                snippet="",
                domain=domain,
                provider=self.name,
                score=0.0,
                raw_rank=i,
            ))
        return results
