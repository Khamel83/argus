"""Tests for retrieval workflows."""

from types import SimpleNamespace

import pytest

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.models import SearchMode, SearchResponse, SearchResult


class StubBroker:
    async def search(self, query):
        if query.mode == SearchMode.RECOVERY:
            return SearchResponse(
                query=query.query,
                mode=query.mode,
                results=[
                    SearchResult(url="https://archive.example.com/post", title="Recovered Post", snippet="Recovered"),
                    SearchResult(url="https://backup.example.com/post", title="Backup Post", snippet="Backup"),
                ],
                total_results=2,
                search_run_id="recover-search",
            )

        if "official docs" in query.query:
            return SearchResponse(
                query=query.query,
                mode=query.mode,
                results=[
                    SearchResult(url="https://docs.example.com", title="Example Docs", snippet="Official"),
                ],
                total_results=1,
                search_run_id="official-search",
            )

        return SearchResponse(
            query=query.query,
            mode=query.mode,
            results=[
                SearchResult(url="https://blog.example.net/guide", title="Guide", snippet="How-to"),
                SearchResult(url="https://notes.example.org/reference", title="Reference", snippet="Reference"),
            ],
            total_results=2,
            search_run_id="research-search",
        )


def _extract_result(url: str, title: str = "Title", words: int = 120):
    text = " ".join(["word"] * (words - 1) + ["done."])
    return ExtractedContent(
        url=url,
        title=title,
        text=text,
        word_count=words,
        extractor=ExtractorName.TRAFILATURA,
        quality_passed=True,
        completeness_result=SimpleNamespace(is_complete=True, confidence=0.99),
    )


@pytest.mark.asyncio
async def test_recover_article_writes_report(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))

    from argus.workflows import WorkflowService
    from argus.workflows import service as workflow_service_module

    async def fake_extract(url, domain=None):
        return _extract_result(url, title="Recovered Post", words=180 if "archive" in url else 120)

    monkeypatch.setattr(workflow_service_module, "extract_url", fake_extract)

    service = WorkflowService(StubBroker())
    result = await service.recover_article(url="https://dead.example.com/post")

    assert result.status.value == "completed"
    assert result.report_path is not None
    assert result.manifest_path is not None
    assert result.metadata["recovered_url"] == "https://archive.example.com/post"
    assert (tmp_path / "data" / "workflows" / "runs" / f"{result.run_id}.json").exists()


@pytest.mark.asyncio
async def test_capture_site_creates_current_research_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))

    from argus.workflows import WorkflowService
    from argus.workflows import service as workflow_service_module

    async def fake_extract(url, domain=None):
        return _extract_result(url, title=url.rsplit("/", 1)[-1] or "home", words=150)

    monkeypatch.setattr(workflow_service_module, "extract_url", fake_extract)

    service = WorkflowService(StubBroker())

    async def fake_sitemap(url):
        return [f"{url}/docs", f"{url}/reference"]

    async def fake_links(url):
        return ["/guide", "/api", "/blog/post"]

    monkeypatch.setattr(service, "_load_sitemap_urls", fake_sitemap)
    monkeypatch.setattr(service, "_fetch_links", fake_links)

    result = await service.capture_site(url="https://site.example.com", soft_page_limit=3, hard_page_limit=5)

    assert result.status.value == "completed"
    assert result.metadata["captured_pages"] >= 3
    current_dir = tmp_path / "data" / "docs" / "research" / "sites" / "site-example-com"
    assert current_dir.exists()
    assert (current_dir / "SUMMARY.md").exists()


@pytest.mark.asyncio
async def test_build_research_pack_populates_docs_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))

    from argus.workflows import WorkflowService
    from argus.workflows import service as workflow_service_module

    async def fake_extract(url, domain=None):
        title = "Official Doc" if "docs.example.com" in url else "External Guide"
        return _extract_result(url, title=title, words=140)

    monkeypatch.setattr(workflow_service_module, "extract_url", fake_extract)

    service = WorkflowService(StubBroker())

    async def fake_sitemap(url):
        return [f"{url}/intro", f"{url}/api"]

    async def fake_links(url):
        return ["/guide", "/reference"]

    monkeypatch.setattr(service, "_load_sitemap_urls", fake_sitemap)
    monkeypatch.setattr(service, "_fetch_links", fake_links)

    result = await service.build_research_pack(topic="Example SDK", max_research_pages=2)

    assert result.status.value == "completed"
    docs_cache_dir = tmp_path / "data" / "docs" / "cache" / "docs-example-com"
    assert docs_cache_dir.exists()
    assert (docs_cache_dir / "README.md").exists()
    index_text = (tmp_path / "data" / "docs" / "cache" / ".index.md").read_text(encoding="utf-8")
    assert "| docs-example-com | https://docs.example.com |" in index_text
