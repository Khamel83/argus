"""Bounded sitemap and internal-link acquisition for accepted workflows."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(str(href))


def domain_root(hostname: str) -> str:
    host = hostname.lower().lstrip("www.")
    parts = [part for part in host.split(".") if part]
    return host if len(parts) <= 2 else ".".join(parts[-2:])


def _same_site(url: str, root_domain: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"} and domain_root(parsed.netloc) == root_domain
    )


def looks_like_html(url: str) -> bool:
    return Path(urlparse(url).path).suffix.lower() not in {
        ".css",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".pdf",
        ".png",
        ".svg",
        ".webp",
        ".xml",
        ".zip",
    }


def _normalized(url: str) -> str:
    parsed = urlparse(url)
    return (
        parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
        )
        .geturl()
        .rstrip("/")
    )


def site_url_score(url: str, root_url: str) -> int:
    parsed = urlparse(url)
    base = urlparse(root_url)
    path = parsed.path.lower()
    score = 1
    if parsed.path in {"", "/"}:
        score += 4
    if parsed.netloc == base.netloc:
        score += 2
    for keyword in (
        "api",
        "docs",
        "download",
        "features",
        "getting-started",
        "guide",
        "install",
        "pricing",
        "reference",
        "tutorial",
    ):
        if keyword in path:
            score += 3
    depth = len([part for part in parsed.path.split("/") if part])
    if depth <= 2:
        score += 2
    elif depth >= 5:
        score -= 1
    if any(skip in path for skip in ("/tag/", "/author/", "/page/", "/category/")):
        score -= 3
    if parsed.query:
        score -= 1
    return score


async def fetch_site_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "ArgusSiteCapture/1.0"})
        response.raise_for_status()
        return response.text


async def discover_site_urls(root_url: str, *, fetcher, hard_limit: int) -> list[str]:
    """Discover same-site URLs from sitemap and bounded internal-link traversal."""
    root_domain = domain_root(urlparse(root_url).netloc)
    discovered: dict[str, str] = {_normalized(root_url): root_url}
    sitemap_url = urljoin(root_url.rstrip("/") + "/", "/sitemap.xml")
    try:
        sitemap_text = await fetcher(sitemap_url)
        sitemap = ElementTree.fromstring(sitemap_text)
        for location in sitemap.findall(".//{*}loc"):
            candidate = (location.text or "").strip()
            if (
                candidate
                and _same_site(candidate, root_domain)
                and looks_like_html(candidate)
            ):
                discovered[_normalized(candidate)] = candidate
    except Exception:
        pass

    queue = [root_url]
    visited: set[str] = set()
    while queue and len(visited) < min(hard_limit, 25):
        current = queue.pop(0)
        normalized = _normalized(current)
        if normalized in visited:
            continue
        visited.add(normalized)
        try:
            html = await fetcher(current)
        except Exception:
            continue
        parser = _LinkParser()
        parser.feed(html)
        for href in parser.links:
            candidate = urljoin(current, href)
            if not _same_site(candidate, root_domain) or not looks_like_html(candidate):
                continue
            candidate_normalized = _normalized(candidate)
            discovered[candidate_normalized] = candidate
            if candidate_normalized not in visited and len(queue) < hard_limit:
                queue.append(candidate)
        if len(discovered) >= hard_limit * 2:
            break
    return list(discovered.values())
