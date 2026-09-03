"""
Yahoo Search scraping provider.

No API key required. Fragile — Yahoo HTML structure can change.
Auto-disabled by health tracker after repeated failures.
Useful as a Tier 0 fallback for pip-only deploys when SearXNG isn't running.

lxml is available as a transitive dependency via trafilatura.
"""

import re
import time
from typing import List
from urllib.parse import unquote, urljoin, urlparse

import httpx  # noqa: F401 - explicit compatibility seam for adapter tests

from argus.config import ProviderConfig
from argus.logging import get_logger
from argus.models import ProviderName, ProviderStatus, SearchResult, SearchQuery
from argus.providers.base import BaseProvider
from argus.broker.provider_evidence import (
    FailureCategory,
    ProviderFailure,
    ProviderSearchBatch,
    QueryRelation,
    RedirectChildEvidence,
    safe_redirect_request,
)

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

    async def _get_bounded(
        self,
        params,
        query: SearchQuery,
    ) -> tuple[
        object | None, tuple[RedirectChildEvidence, ...], ProviderFailure | None
    ]:
        response = await self._provider_request(
            query,
            YAHOO_SEARCH_URL,
            method="GET",
            params=params,
            headers=_HEADERS,
        )
        source_url = YAHOO_SEARCH_URL
        children: list[RedirectChildEvidence] = []
        for redirect_count in range(1, 5):
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response, tuple(children), None
            location = self._response_headers(response).get("location")
            if not isinstance(location, str):
                return response, tuple(children), None
            timeout = self._attempt_timeout(query)
            try:
                request = safe_redirect_request(
                    source_url=source_url,
                    destination_url=urljoin(source_url, location),
                    headers=_HEADERS,
                    redirect_count=redirect_count,
                    parent_attempt_id=str(
                        query.metadata.get("_provider_attempt_id")
                        or f"{self.name.value}-attempt"
                    ),
                    timeout_seconds=timeout,
                    max_redirects=3,
                )
            except ProviderFailure as failure:
                return response, tuple(children), failure
            children.append(request.child_evidence)
            source_url = request.url
            response = await self._provider_request(
                query,
                request.url,
                method="GET",
                headers=request.headers,
                timeout=timeout,
            )
        raise AssertionError("bounded redirect loop is exhaustive")

    async def search(self, query: SearchQuery) -> ProviderSearchBatch:
        started_at = time.monotonic()
        provider_query = query.query
        children: tuple[RedirectChildEvidence, ...] = ()
        relation = QueryRelation.EXACT

        resp = None
        try:
            freshness = self._freshness_params(query)
            qualifier = freshness.get("query_qualifier")
            if qualifier:
                start_date, _, end_date = str(qualifier).partition("..")
                if start_date:
                    provider_query += f" after:{start_date}"
                if end_date:
                    provider_query += f" before:{end_date}"
                relation = QueryRelation.PROVIDER_REWRITE
            params = {
                "p": provider_query,
                "n": min(query.max_results, 10),
                "ei": "UTF-8",
            }
            resp, children, redirect_failure = await self._get_bounded(
                params, query
            )
            request_evidence = self._request_evidence(
                query,
                timeout_seconds=self._attempt_timeout(query),
                provider_request_material=provider_query,
                query_relation=relation,
                redirect_children=children,
            )
            if redirect_failure is not None:
                return self._typed_failure_batch(
                    redirect_failure.category,
                    redirect_failure.summary,
                    started_at=started_at,
                    request_evidence=request_evidence,
                    observed_status=self._response_status(resp),
                )
            assert resp is not None
            native_failure = self._response_failure_batch(
                resp, started_at=started_at, request_evidence=request_evidence
            )
            if native_failure is not None:
                return native_failure

            results = self._parse(resp.text, query.max_results)

            if not results:
                if re.search(r"\bno results\b", resp.text, re.IGNORECASE):
                    return self._typed_failure_batch(
                        FailureCategory.EMPTY,
                        "Yahoo returned an explicit empty result page",
                        started_at=started_at,
                        request_evidence=request_evidence,
                        observed_status=self._response_status(resp),
                    )
                return self._typed_failure_batch(
                    FailureCategory.PARSE_ERROR,
                    "Yahoo success page did not match the parser contract",
                    started_at=started_at,
                    request_evidence=request_evidence,
                    observed_status=self._response_status(resp),
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
                started_at=started_at,
                request_evidence=request_evidence,
                http_status=resp.status_code,
                response_headers=self._response_headers(resp),
            )

        except Exception as e:
            logger.warning("Yahoo search failed: %s", type(e).__name__)
            request_evidence = self._request_evidence(
                query,
                timeout_seconds=max(0.001, self._config.timeout_seconds),
                provider_request_material=provider_query,
                query_relation=relation,
                redirect_children=children,
            )
            return self._failure_batch(
                e,
                started_at=started_at,
                request_evidence=request_evidence,
                observed_status=self._response_status(resp),
            )

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
            h3 = a.xpath(".//h3")
            title = h3[0].text_content().strip() if h3 else a.text_content().strip()

            # Description is in compText sibling
            desc_nodes = node.xpath('.//*[contains(@class,"compText")]')
            snippet = desc_nodes[0].text_content().strip() if desc_nodes else ""

            try:
                domain = urlparse(href).netloc
            except Exception:
                domain = ""

            results.append(
                SearchResult(
                    url=href,
                    title=title,
                    snippet=snippet[:300],
                    domain=domain,
                    provider=self.name,
                    score=0.0,
                    raw_rank=i,
                )
            )

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
            results.append(
                SearchResult(
                    url=href,
                    title=title,
                    snippet="",
                    domain=domain,
                    provider=self.name,
                    score=0.0,
                    raw_rank=i,
                )
            )
        return results
