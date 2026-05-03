"""Workflow execution for retrieval-oriented Argus features."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from argus.broker.dedupe import normalize_url
from argus.broker.router import SearchBroker
from argus.corpus import CorpusPaths, describe_corpus_paths, get_corpus_paths, mirror_legacy_docs_cache
from argus.extraction import extract_url
from argus.logging import get_logger
from argus.models import SearchMode, SearchQuery
from argus.persistence.db import WorkflowPersistenceGateway
from argus.workflows.models import (
    CitationRef,
    StoredDocument,
    SummarySection,
    WorkflowArtifact,
    WorkflowKind,
    WorkflowResult,
    WorkflowStatus,
)
from argus.workflows.summarizer import get_summarizer

logger = get_logger("workflows")

_STATIC_SUFFIXES = {
    ".css", ".js", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".pdf", ".zip", ".gz", ".tgz", ".json", ".xml", ".txt", ".rss", ".atom",
}
_HIGH_VALUE_KEYWORDS = (
    "docs", "doc", "guide", "guides", "reference", "api", "manual",
    "tutorial", "learn", "kb", "knowledge", "blog", "article", "faq",
)


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag != "a":
            return
        for key, value in attrs:
            if key == "href" and value:
                self.links.append(value)


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    seed = f"{parsed.netloc}{parsed.path}".strip("/") or parsed.netloc or "target"
    from argus.corpus.paths import slugify
    return slugify(seed, default="target")


def _domain_root(hostname: str) -> str:
    host = hostname.lower().lstrip("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def _same_site(url: str, root_domain: str) -> bool:
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        return False
    return _domain_root(parsed.netloc) == root_domain


def _looks_like_html(url: str) -> bool:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    return suffix not in _STATIC_SUFFIXES


def _score_site_url(url: str, root_url: str) -> int:
    parsed = urlparse(url)
    base = urlparse(root_url)
    score = 1
    path = parsed.path.lower()
    if parsed.path in {"", "/"}:
        score += 4
    if parsed.netloc == base.netloc:
        score += 2
    for keyword in _HIGH_VALUE_KEYWORDS:
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


def _lead_text(text: str, limit: int = 280) -> str:
    cleaned = " ".join(part.strip() for part in text.splitlines() if part.strip())
    cleaned = cleaned[:limit].strip()
    if cleaned and not cleaned.endswith("."):
        cleaned += "..."
    return cleaned


class WorkflowService:
    """Async workflow executor with in-memory run tracking."""

    def __init__(
        self,
        broker: SearchBroker,
        *,
        corpus_paths: CorpusPaths | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ):
        self._broker = broker
        self._paths = corpus_paths or get_corpus_paths()
        self._runs: dict[str, WorkflowResult] = {}
        self._persistence = WorkflowPersistenceGateway()
        self._progress = progress_callback

    def _report(self, current: int, total: int, message: str) -> None:
        if self._progress:
            try:
                self._progress(current, total, message)
            except Exception:
                pass

    def get_paths(self) -> dict[str, Any]:
        return describe_corpus_paths()

    def get_run(self, run_id: str) -> WorkflowResult | None:
        return self._runs.get(run_id)

    def import_legacy_docs_cache(self, source_root: str) -> dict[str, Any]:
        return mirror_legacy_docs_cache(source_root, self._paths)

    async def start_recover_article(self, *, url: str, title: str | None = None, domain: str | None = None) -> WorkflowResult:
        run = self._create_run(WorkflowKind.RECOVER_ARTICLE, url)
        asyncio.create_task(self._execute_run(run.run_id, self._recover_article_impl, url=url, title=title, domain=domain))
        return run

    async def start_capture_site(
        self,
        *,
        url: str,
        soft_page_limit: int = 75,
        hard_page_limit: int = 200,
    ) -> WorkflowResult:
        run = self._create_run(WorkflowKind.CAPTURE_SITE, url)
        asyncio.create_task(
            self._execute_run(
                run.run_id,
                self._capture_site_impl,
                url=url,
                soft_page_limit=soft_page_limit,
                hard_page_limit=hard_page_limit,
            )
        )
        return run

    async def start_build_research_pack(
        self,
        *,
        topic: str,
        official_url: str | None = None,
        max_research_pages: int = 40,
    ) -> WorkflowResult:
        run = self._create_run(WorkflowKind.BUILD_RESEARCH_PACK, topic)
        asyncio.create_task(
            self._execute_run(
                run.run_id,
                self._build_research_pack_impl,
                topic=topic,
                official_url=official_url,
                max_research_pages=max_research_pages,
            )
        )
        return run

    async def recover_article(self, *, url: str, title: str | None = None, domain: str | None = None) -> WorkflowResult:
        run = self._create_run(WorkflowKind.RECOVER_ARTICLE, url)
        return await self._execute_run(run.run_id, self._recover_article_impl, url=url, title=title, domain=domain)

    async def capture_site(
        self,
        *,
        url: str,
        soft_page_limit: int = 75,
        hard_page_limit: int = 200,
    ) -> WorkflowResult:
        run = self._create_run(WorkflowKind.CAPTURE_SITE, url)
        return await self._execute_run(
            run.run_id,
            self._capture_site_impl,
            url=url,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )

    async def build_research_pack(
        self,
        *,
        topic: str,
        official_url: str | None = None,
        max_research_pages: int = 40,
    ) -> WorkflowResult:
        run = self._create_run(WorkflowKind.BUILD_RESEARCH_PACK, topic)
        return await self._execute_run(
            run.run_id,
            self._build_research_pack_impl,
            topic=topic,
            official_url=official_url,
            max_research_pages=max_research_pages,
        )

    def _create_run(self, kind: WorkflowKind, target: str) -> WorkflowResult:
        run_id = uuid.uuid4().hex[:12]
        slug = _slug_from_url(target) if target.startswith(("http://", "https://")) else _slug_from_url(f"https://{target}")
        snapshot_dir = self._paths.snapshots_dir / kind.value / slug / run_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        run = WorkflowResult(
            run_id=run_id,
            kind=kind,
            status=WorkflowStatus.PENDING,
            target=target,
            status_url=f"/api/workflows/{run_id}",
            snapshot_dir=str(snapshot_dir),
        )
        self._runs[run_id] = run
        return run

    async def _execute_run(self, run_id: str, handler, **kwargs) -> WorkflowResult:
        run = self._runs[run_id]
        run.status = WorkflowStatus.RUNNING
        run.started_at = datetime.now(tz=None)
        self._write_run_state(run)
        try:
            await handler(run, **kwargs)
            run.status = WorkflowStatus.COMPLETED
        except Exception as exc:
            logger.exception("Workflow %s failed", run.run_id)
            run.status = WorkflowStatus.FAILED
            run.error = str(exc)
        finally:
            run.finished_at = datetime.now(tz=None)
            self._write_run_state(run)
        return run

    async def _recover_article_impl(self, run: WorkflowResult, *, url: str, title: str | None, domain: str | None):
        self._report(0, 3, "Searching for recovery candidates...")
        query_parts = [url]
        if title:
            query_parts.append(title)
        if domain:
            query_parts.append(domain)
        resp = await self._broker.search(
            SearchQuery(query=" ".join(query_parts), mode=SearchMode.RECOVERY, max_results=10)
        )
        candidates = []
        seen = {normalize_url(url)}
        for result in resp.results:
            normalized = normalize_url(result.url)
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(result.url)

        if not candidates:
            raise ValueError("No recovery candidates found")

        documents, citations = await self._capture_explicit_urls(
            run,
            candidates[:8],
            section="recovered-sources",
            role="recovered_source",
            source_type="recovery_candidate",
        )
        self._report(1, 3, f"Extracted {len(documents)} candidate pages")
        if not documents:
            raise ValueError("Recovery candidates were found but none could be extracted")

        best = max(
            documents,
            key=lambda doc: (
                int(doc.metadata.get("quality_passed", False)),
                int(doc.metadata.get("is_complete", False)),
                doc.word_count,
            ),
        )
        run.metadata["recovered_url"] = best.url
        run.metadata["candidate_count"] = len(documents)
        run.metadata["search_run_id"] = resp.search_run_id

        prompt = f"Recover a dead article for {title or url}"
        sections = await get_summarizer().summarize(
            title=title or best.title or url,
            prompt=prompt,
            documents=documents,
            citations=citations,
        )
        sections.insert(
            0,
            SummarySection(
                heading="Recovered Article",
                body=(
                    f"Argus selected **{best.title or best.url}** as the best recovery candidate. "
                    f"Recovered URL: {best.url}"
                ),
                citation_ids=[best.id],
            ),
        )
        run.documents = documents
        run.citations = citations
        run.summary_sections = sections
        self._finalize_run(
            run,
            title=title or best.title or "Recovered Article",
            report_name="report.md",
        )

    async def _capture_site_impl(
        self,
        run: WorkflowResult,
        *,
        url: str,
        soft_page_limit: int,
        hard_page_limit: int,
    ):
        self._report(0, 4, "Discovering site URLs...")
        candidates = await self._discover_site_urls(url, soft_page_limit=soft_page_limit, hard_page_limit=hard_page_limit)
        self._report(1, 4, f"Discovered {len(candidates)} URLs, extracting content...")
        documents, citations = await self._capture_explicit_urls(
            run,
            candidates,
            section="site-pages",
            role="site_page",
            source_type="site_capture",
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )
        self._report(2, 4, f"Extracted {len(documents)} pages, generating summary...")
        if not documents:
            raise ValueError("Site capture did not yield any extractable pages")

        run.metadata["captured_pages"] = len(documents)
        run.metadata["candidate_urls"] = len(candidates)
        sections = await get_summarizer().summarize(
            title=url,
            prompt=f"Summarize the most important information from site {url}",
            documents=documents,
            citations=citations,
        )
        sections.insert(
            0,
            SummarySection(
                heading="Capture Scope",
                body=(
                    f"Argus stayed on-domain for `{url}` and saved {len(documents)} pages. "
                    f"The crawler used sitemap-assisted discovery and heuristic link scoring."
                ),
                citation_ids=[doc.id for doc in documents[:3]],
            ),
        )
        run.documents = documents
        run.citations = citations
        run.summary_sections = sections

        current_dir = self._paths.research_dir / "sites" / _slug_from_url(url)
        self._finalize_run(run, title=f"Site Capture: {url}", report_name="SUMMARY.md", current_dir=current_dir)

    async def _build_research_pack_impl(
        self,
        run: WorkflowResult,
        *,
        topic: str,
        official_url: str | None,
        max_research_pages: int,
    ):
        self._report(0, 4, "Discovering official documentation URL...")
        official = official_url or await self._discover_official_docs_url(topic)
        if not official:
            raise ValueError("Could not determine an official documentation URL")

        self._report(1, 4, f"Capturing official docs from {official}...")
        official_docs, official_citations = await self._capture_site_documents(
            official,
            run=run,
            section="official-docs",
            role="official_doc",
            source_type="official_docs",
            soft_page_limit=50,
            hard_page_limit=120,
        )
        self._report(2, 4, f"Captured {len(official_docs)} official docs, searching for external research...")
        research_urls = await self._discover_research_urls(topic, official_url=official, limit=max_research_pages)
        research_docs, research_citations = await self._capture_explicit_urls(
            run,
            research_urls,
            section="external-research",
            role="external_research",
            source_type="external_research",
            citation_start=len(official_citations),
            soft_page_limit=max_research_pages,
            hard_page_limit=max_research_pages,
        )
        self._report(3, 4, f"Captured {len(research_docs)} external pages, generating summary...")
        documents = official_docs + research_docs
        citations = official_citations + research_citations
        if not documents:
            raise ValueError("Research pack did not produce any saved documents")

        run.metadata["official_url"] = official
        run.metadata["official_docs_count"] = len(official_docs)
        run.metadata["external_research_count"] = len(research_docs)

        sections = await get_summarizer().summarize(
            title=topic,
            prompt=f"Build a research pack for {topic}",
            documents=documents,
            citations=citations,
        )
        sections.insert(
            0,
            SummarySection(
                heading="Pack Composition",
                body=(
                    f"Official docs were captured from {official}. "
                    f"Argus also saved {len(research_docs)} external supporting sources."
                ),
                citation_ids=[doc.id for doc in documents[:4]],
            ),
        )
        run.documents = documents
        run.citations = citations
        run.summary_sections = sections

        slug = _slug_from_url(official)
        cache_dir = self._paths.docs_cache_dir / slug
        pack_dir = self._paths.research_dir / "packs" / _slug_from_url(topic)
        self._finalize_run(
            run,
            title=f"Research Pack: {topic}",
            report_name="SUMMARY.md",
            current_dir=pack_dir,
            docs_cache_dir=cache_dir,
            docs_cache_url=official,
        )

    async def _discover_official_docs_url(self, topic: str) -> str | None:
        resp = await self._broker.search(
            SearchQuery(query=f"{topic} official docs", mode=SearchMode.DISCOVERY, max_results=8)
        )
        for result in resp.results:
            if any(keyword in result.url.lower() for keyword in ("/docs", "docs.", "/reference", "/api")):
                return result.url
        return resp.results[0].url if resp.results else None

    async def _discover_research_urls(self, topic: str, *, official_url: str, limit: int) -> list[str]:
        official_root = _domain_root(urlparse(official_url).netloc)
        resp = await self._broker.search(
            SearchQuery(
                query=f"{topic} documentation tutorial guide comparison best practices",
                mode=SearchMode.RESEARCH,
                max_results=max(limit * 2, 20),
            )
        )
        urls: list[str] = []
        seen: set[str] = set()
        for result in resp.results:
            normalized = normalize_url(result.url)
            if normalized in seen:
                continue
            seen.add(normalized)
            if _domain_root(urlparse(result.url).netloc) == official_root:
                continue
            urls.append(result.url)
            if len(urls) >= limit:
                break
        return urls

    async def _capture_site_documents(
        self,
        url: str,
        *,
        run: WorkflowResult,
        section: str,
        role: str,
        source_type: str,
        soft_page_limit: int,
        hard_page_limit: int,
    ) -> tuple[list[StoredDocument], list[CitationRef]]:
        candidates = await self._discover_site_urls(url, soft_page_limit=soft_page_limit, hard_page_limit=hard_page_limit)
        return await self._capture_explicit_urls(
            run,
            candidates,
            section=section,
            role=role,
            source_type=source_type,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )

    async def _capture_explicit_urls(
        self,
        run: WorkflowResult,
        urls: list[str],
        *,
        section: str,
        role: str,
        source_type: str,
        citation_start: int = 0,
        soft_page_limit: int | None = None,
        hard_page_limit: int | None = None,
    ) -> tuple[list[StoredDocument], list[CitationRef]]:
        documents: list[StoredDocument] = []
        citations: list[CitationRef] = []
        output_dir = Path(run.snapshot_dir) / section
        output_dir.mkdir(parents=True, exist_ok=True)

        for i, candidate_url in enumerate(urls):
            if hard_page_limit is not None and len(documents) >= hard_page_limit:
                break
            if i > 0 and i % 5 == 0:
                self._report(i, len(urls), f"Extracting page {i}/{len(urls)}: {candidate_url[:60]}")
            result = await extract_url(candidate_url)
            if result.error or not result.text:
                continue
            if result.word_count < 60:
                continue

            citation_id = f"S{citation_start + len(citations) + 1}"
            stored = self._store_document(
                output_dir,
                citation_id=citation_id,
                url=candidate_url,
                title=result.title or candidate_url,
                text=result.text,
                word_count=result.word_count,
                domain=urlparse(candidate_url).netloc.lower().lstrip("www."),
                role=role,
                source_type=source_type,
                extractor=result.extractor.value if result.extractor else None,
                egress=result.egress,
                machine=result.machine,
                metadata={
                    "lead_text": _lead_text(result.text),
                    "quality_passed": getattr(result, "quality_passed", True),
                    "is_complete": getattr(result.completeness_result, "is_complete", None),
                    "completeness_confidence": getattr(result.completeness_result, "confidence", None),
                },
            )
            documents.append(stored)
            citations.append(
                CitationRef(
                    id=stored.id,
                    title=stored.title,
                    url=stored.url,
                    artifact_path=stored.artifact_path,
                    note=source_type,
                )
            )

            if soft_page_limit is not None and len(documents) >= soft_page_limit:
                break

        documents.sort(key=lambda item: item.word_count, reverse=True)
        return documents, citations

    async def _discover_site_urls(self, root_url: str, *, soft_page_limit: int, hard_page_limit: int) -> list[str]:
        root_domain = _domain_root(urlparse(root_url).netloc)
        discovered: dict[str, tuple[str, int]] = {normalize_url(root_url): (root_url, 100)}

        sitemap_urls = await self._load_sitemap_urls(root_url)
        for url in sitemap_urls:
            if _same_site(url, root_domain) and _looks_like_html(url):
                discovered[normalize_url(url)] = (url, _score_site_url(url, root_url))

        queue: list[str] = [root_url]
        visited: set[str] = set()
        while queue and len(visited) < min(soft_page_limit, 25):
            current = queue.pop(0)
            normalized = normalize_url(current)
            if normalized in visited:
                continue
            visited.add(normalized)
            links = await self._fetch_links(current)
            for link in links:
                absolute = urljoin(current, link)
                if not _same_site(absolute, root_domain) or not _looks_like_html(absolute):
                    continue
                normalized_link = normalize_url(absolute)
                score = _score_site_url(absolute, root_url)
                previous = discovered.get(normalized_link)
                if previous is None or score > previous[1]:
                    discovered[normalized_link] = (absolute, score)
                if normalized_link not in visited and len(queue) < hard_page_limit:
                    queue.append(absolute)
            if len(discovered) >= hard_page_limit * 2:
                break

        ordered = sorted(
            discovered.values(),
            key=lambda item: (-item[1], len(urlparse(item[0]).path), item[0]),
        )
        urls = [url for url, _ in ordered[:hard_page_limit]]
        if root_url in urls:
            urls.remove(root_url)
        return [root_url] + urls

    async def _load_sitemap_urls(self, root_url: str) -> list[str]:
        parsed = urlparse(root_url)
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(sitemap_url)
                resp.raise_for_status()
            root = ET.fromstring(resp.text)
            urls = []
            for loc in root.findall(".//{*}loc"):
                if loc.text:
                    urls.append(loc.text.strip())
            return urls
        except Exception:
            return []

    async def _fetch_links(self, url: str) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "ArgusSiteCapture/1.0"})
                resp.raise_for_status()
            parser = _LinkParser()
            parser.feed(resp.text)
            return parser.links
        except Exception:
            return []

    def _store_document(
        self,
        destination: Path,
        *,
        citation_id: str,
        url: str,
        title: str,
        text: str,
        word_count: int,
        domain: str,
        role: str,
        source_type: str,
        extractor: str | None,
        egress: str | None = None,
        machine: str | None = None,
        metadata: dict[str, Any],
    ) -> StoredDocument:
        from argus.corpus.paths import slugify

        filename = f"{citation_id.lower()}-{slugify(title or url, default='document')}.md"
        artifact_path = destination / filename
        artifact_path.write_text(
            "\n".join(
                [
                    f"# {title or url}",
                    "",
                    f"- URL: {url}",
                    f"- Domain: {domain}",
                    f"- Source type: {source_type}",
                    f"- Role: {role}",
                    f"- Extractor: {extractor or 'unknown'}",
                    f"- Egress: {egress or 'unknown'}",
                    f"- Machine: {machine or 'unknown'}",
                    f"- Word count: {word_count}",
                    "",
                    text,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        metadata_path = artifact_path.with_suffix(".json")
        metadata_path.write_text(json.dumps(metadata, indent=2, default=_json_default) + "\n", encoding="utf-8")
        return StoredDocument(
            id=citation_id,
            url=url,
            title=title,
            artifact_path=str(artifact_path),
            word_count=word_count,
            domain=domain,
            role=role,
            source_type=source_type,
            extractor=extractor,
            egress=egress,
            machine=machine,
            metadata=metadata,
        )

    def _finalize_run(
        self,
        run: WorkflowResult,
        *,
        title: str,
        report_name: str,
        current_dir: Path | None = None,
        docs_cache_dir: Path | None = None,
        docs_cache_url: str | None = None,
    ) -> None:
        report_path = Path(run.snapshot_dir) / report_name
        manifest_path = Path(run.snapshot_dir) / "manifest.json"
        report_path.write_text(self._render_report(title, run), encoding="utf-8")
        run.report_path = str(report_path)
        run.manifest_path = str(manifest_path)
        run.artifacts = [
            WorkflowArtifact(kind="report", path=str(report_path), description="Human-readable workflow report"),
            WorkflowArtifact(kind="manifest", path=str(manifest_path), description="Structured workflow manifest"),
        ]
        manifest_path.write_text(
            json.dumps(self._serialize_run(run), indent=2, default=_json_default) + "\n",
            encoding="utf-8",
        )

        if current_dir is not None:
            self._replace_directory(current_dir, Path(run.snapshot_dir))
            run.metadata["current_dir"] = str(current_dir)

        if docs_cache_dir is not None:
            self._write_docs_cache_dir(docs_cache_dir, title=title, run=run)
            if docs_cache_url:
                self._update_docs_cache_index(docs_cache_dir.name, docs_cache_url, docs_cache_dir)

        self._write_run_state(run)

    def _write_docs_cache_dir(self, docs_cache_dir: Path, *, title: str, run: WorkflowResult) -> None:
        docs_cache_dir.mkdir(parents=True, exist_ok=True)
        readme = docs_cache_dir / "README.md"
        official_docs = [document for document in run.documents if document.source_type == "official_docs"]
        lines = [
            f"# {title}",
            "",
            f"> Generated by Argus workflow `{run.kind.value}`",
            "",
            "## Latest Summary",
            "",
        ]
        for section in run.summary_sections:
            lines.append(f"### {section.heading}")
            lines.append("")
            lines.append(section.body)
            lines.append("")
        if run.report_path:
            lines.append(f"Source report: {run.report_path}")
            lines.append("")
        readme.write_text("\n".join(lines), encoding="utf-8")
        sources_dir = docs_cache_dir / "sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        for document in official_docs:
            source_path = Path(document.artifact_path)
            if source_path.exists():
                shutil.copy2(source_path, sources_dir / source_path.name)

    def _update_docs_cache_index(self, slug: str, source_url: str, path: Path) -> None:
        index_path = self._paths.docs_cache_index
        existing = index_path.read_text(encoding="utf-8").splitlines()
        row = f"| {slug} | {source_url} | {datetime.now(tz=None).date().isoformat()} | {path} |"
        filtered = [line for line in existing if not line.startswith(f"| {slug} |")]
        filtered.append(row)
        index_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")

    def _replace_directory(self, destination: Path, source: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)

    def _render_report(self, title: str, run: WorkflowResult) -> str:
        lines = [
            f"# {title}",
            "",
            f"- Run ID: {run.run_id}",
            f"- Workflow: {run.kind.value}",
            f"- Target: {run.target}",
            f"- Status: {run.status.value}",
            f"- Snapshot: {run.snapshot_dir}",
            "",
            "## Summary",
            "",
        ]
        for section in run.summary_sections:
            citations = " ".join(f"[{cid}]" for cid in section.citation_ids)
            lines.append(f"### {section.heading}")
            lines.append("")
            lines.append(section.body)
            if citations:
                lines.append("")
                lines.append(f"Citations: {citations}")
            lines.append("")

        lines.extend(
            [
                "## References",
                "",
            ]
        )
        citation_map = {citation.id: citation for citation in run.citations}
        for citation_id in sorted(citation_map):
            citation = citation_map[citation_id]
            lines.append(
                f"- [{citation.id}] {citation.title} — {citation.url}\n"
                f"  Artifact: {citation.artifact_path}"
            )
        lines.append("")
        return "\n".join(lines)

    def _serialize_run(self, run: WorkflowResult) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "kind": run.kind.value,
            "status": run.status.value,
            "target": run.target,
            "created_at": run.created_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "status_url": run.status_url,
            "snapshot_dir": run.snapshot_dir,
            "report_path": run.report_path,
            "manifest_path": run.manifest_path,
            "artifacts": [asdict(artifact) for artifact in run.artifacts],
            "documents": [asdict(document) for document in run.documents],
            "citations": [asdict(citation) for citation in run.citations],
            "summary_sections": [asdict(section) for section in run.summary_sections],
            "metadata": run.metadata,
            "error": run.error,
        }

    def _write_run_state(self, run: WorkflowResult) -> None:
        payload = self._serialize_run(run)
        state_path = self._paths.workflow_runs_dir / f"{run.run_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(payload, indent=2, default=_json_default) + "\n",
            encoding="utf-8",
        )
        self._persistence.record_run_state(payload)
