"""Tests for retrieval workflows."""

from types import SimpleNamespace

import pytest

from argus.extraction.models import ExtractedContent, ExtractorName
from argus.models import SearchMode, SearchResponse, SearchResult
from argus.contracts import AcceptedOperation, CanonicalOutcome


class StubEvidenceGateway:
    def __init__(self):
        self.outcomes = {}
        self.compositions = []

    def load_accepted_extraction_outcome(self, run_id):
        return self.outcomes.get(run_id)

    def accept_retrieval_composition(self, view, composition, requirement):
        self.compositions.append((view, composition, requirement))
        return SimpleNamespace(receipt_ref=f"composition-{len(self.compositions)}")


class StubAcceptedOperations:
    def __init__(self, gateway):
        self.gateway = gateway
        self._counter = 0
        self.compose_calls = []
        self.callers = []

    def _search_operation(self, request_id, results):
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={
                "results": results,
                "acceptance_receipt": {"receipt_ref": f"receipt-{request_id}"},
            },
            error=None,
        )

    async def search(self, request, *, principal, request_id):
        self.callers.append((principal, request.caller))
        if "official docs" in request.query:
            results = ({"url": "https://docs.example.com", "title": "Example Docs"},)
        elif request.query.startswith("https://"):
            results = ({"url": request.query, "title": "Captured"},)
        else:
            results = ({"url": "https://blog.example.net/guide", "title": "Guide"},)
        return self._search_operation(request_id, results)

    async def recover(self, request, *, principal, request_id):
        self.callers.append((principal, None))
        return self._search_operation(
            request_id,
            ({"url": "https://archive.example.com/post", "title": "Recovered Post"},),
        )

    async def acquire_site(self, request, *, principal, request_id):
        operation = await self.search(
            SimpleNamespace(
                query=request.url,
                mode="discovery",
                max_results=request.hard_page_limit,
                providers=None,
                free_only=False,
                caller=request.caller,
                session_id=None,
                include_attribution=False,
            ),
            principal=principal,
            request_id=request_id,
        )
        from urllib.parse import urlparse

        root_host = urlparse(request.url).netloc
        result = dict(operation.result)
        result["results"] = tuple(
            item
            for item in result["results"]
            if urlparse(str(item["url"])).netloc == root_host
        )
        return AcceptedOperation(
            outcome=operation.outcome,
            request_id=operation.request_id,
            result=result,
            error=operation.error,
        )

    async def extract(self, request, *, principal, request_id):
        from tests.test_extraction_composition import _link

        self._counter += 1
        accepted = _link(f"cluster-{self._counter}").accepted_outcome
        self.gateway.outcomes[accepted.extraction_run_id] = accepted
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={"extraction_run_id": accepted.extraction_run_id},
            error=None,
        )

    async def compose_workflow(
        self,
        retrieval,
        *,
        max_results,
        principal,
        request_id,
        selection_urls=None,
        **kwargs,
    ):
        self.compose_calls.append(
            {
                "selection_urls": selection_urls,
                "max_results": max_results,
                **kwargs,
            }
        )
        results = retrieval.result["results"]
        selected = (
            tuple(
                next(item for item in results if item["url"] == url)
                for url in selection_urls
            )
            if selection_urls
            else results[:max_results]
        )
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={
                "composition_receipt_ref": f"composition-{request_id}",
                "composition_outcome": "success",
                "links": tuple(
                    {
                        "result_cluster_ref": f"cluster-{ordinal}",
                        "artifact_disposition": "usable",
                    }
                    for ordinal, _result in enumerate(selected)
                ),
                "artifacts": tuple(
                    {
                        "url": result["url"],
                        "title": result["title"],
                        "text": "word " * 120,
                        "word_count": 120,
                        "disposition": "usable",
                        "extractor": "trafilatura",
                    }
                    for result in selected
                ),
            },
            error=None,
        )


def _service():
    from argus.workflows import WorkflowService

    gateway = StubEvidenceGateway()
    return WorkflowService(StubAcceptedOperations(gateway)), gateway


class StubBroker:
    async def search(self, query):
        if query.mode == SearchMode.RECOVERY:
            return SearchResponse(
                query=query.query,
                mode=query.mode,
                results=[
                    SearchResult(
                        url="https://archive.example.com/post",
                        title="Recovered Post",
                        snippet="Recovered",
                    ),
                    SearchResult(
                        url="https://backup.example.com/post",
                        title="Backup Post",
                        snippet="Backup",
                    ),
                ],
                total_results=2,
                search_run_id="recover-search",
            )

        if "official docs" in query.query:
            return SearchResponse(
                query=query.query,
                mode=query.mode,
                results=[
                    SearchResult(
                        url="https://docs.example.com",
                        title="Example Docs",
                        snippet="Official",
                    ),
                ],
                total_results=1,
                search_run_id="official-search",
            )

        return SearchResponse(
            query=query.query,
            mode=query.mode,
            results=[
                SearchResult(
                    url="https://blog.example.net/guide",
                    title="Guide",
                    snippet="How-to",
                ),
                SearchResult(
                    url="https://notes.example.org/reference",
                    title="Reference",
                    snippet="Reference",
                ),
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

    service, _ = _service()
    result = await service.recover_article(url="https://dead.example.com/post")

    assert result.status.value == "completed"
    assert result.report_path is not None
    assert result.manifest_path is not None
    assert result.metadata["recovered_url"] == "https://archive.example.com/post"
    assert (tmp_path / "data" / "workflows" / "runs" / f"{result.run_id}.json").exists()
    assert service._accepted_operations.compose_calls == [
        {
            "selection_urls": ("https://archive.example.com/post",),
            "max_results": 1,
        }
    ]


@pytest.mark.asyncio
async def test_get_run_loads_persisted_state_after_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))

    first_service, _ = _service()
    created = await first_service.recover_article(url="https://dead.example.com/post")

    restarted_service, _ = _service()
    loaded = restarted_service.get_run(created.run_id)

    assert loaded is not None
    assert loaded.run_id == created.run_id
    assert loaded.status.value == "completed"
    assert loaded.report_path == created.report_path
    assert loaded.documents[0].url == "https://archive.example.com/post"


@pytest.mark.asyncio
async def test_capture_site_creates_current_research_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))

    service, _ = _service()

    result = await service.capture_site(
        url="https://site.example.com", soft_page_limit=3, hard_page_limit=5
    )

    assert result.status.value == "completed"
    assert result.metadata["captured_pages"] >= 1
    current_dir = tmp_path / "data" / "docs" / "research" / "sites" / "site-example-com"
    assert current_dir.exists()
    assert (current_dir / "SUMMARY.md").exists()
    assert len(service._accepted_operations.compose_calls) == 1


@pytest.mark.asyncio
async def test_site_capture_composes_all_ranked_same_site_pages_once(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    service, _ = _service()
    original_search = service._accepted_operations.search

    async def multi_page_search(request, *, principal, request_id):
        if request.query == "https://site.example.com":
            return service._accepted_operations._search_operation(
                request_id,
                (
                    {"url": "https://site.example.com", "title": "Home"},
                    {"url": "https://site.example.com/docs", "title": "Docs"},
                    {"url": "https://unrelated.example.net", "title": "Unrelated"},
                ),
            )
        return await original_search(
            request, principal=principal, request_id=request_id
        )

    service._accepted_operations.search = multi_page_search
    result = await service.capture_site(
        url="https://site.example.com", soft_page_limit=3, hard_page_limit=5
    )

    assert result.status.value == "completed"
    assert {document.url for document in result.documents} == {
        "https://site.example.com",
        "https://site.example.com/docs",
    }
    assert len(service._accepted_operations.compose_calls) == 1
    assert service._accepted_operations.compose_calls[0]["selection_urls"] == (
        "https://site.example.com",
        "https://site.example.com/docs",
    )
    assert len(result.metadata["composition"]["links"]) == 2


@pytest.mark.asyncio
async def test_workflow_search_uses_authenticated_run_caller(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    service, _ = _service()

    await service.capture_site(
        url="https://site.example.com",
        soft_page_limit=1,
        hard_page_limit=1,
        caller_identity="maya",
        caller_label="research-ui",
    )

    assert service._accepted_operations.callers == [("maya", "research-ui")]


@pytest.mark.asyncio
async def test_partial_artifact_is_marked_in_document_citation_and_report(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    service, _ = _service()
    original_compose = service._accepted_operations.compose_workflow

    async def partial_compose(*args, **kwargs):
        operation = await original_compose(*args, **kwargs)
        result = dict(operation.result)
        artifacts = [dict(item) for item in result["artifacts"]]
        artifacts[0]["disposition"] = "partial"
        result["artifacts"] = tuple(artifacts)
        return AcceptedOperation(
            outcome=CanonicalOutcome.DEGRADED,
            request_id=operation.request_id,
            result=result,
            error=None,
        )

    service._accepted_operations.compose_workflow = partial_compose
    result = await service.capture_site(
        url="https://site.example.com", soft_page_limit=1, hard_page_limit=1
    )

    assert result.documents[0].metadata["artifact_disposition"] == "partial"
    assert result.citations[0].artifact_disposition == "partial"
    assert "[PARTIAL CONTENT]" in result.summary_sections[1].body
    from pathlib import Path

    assert "PARTIAL CONTENT" in Path(result.report_path).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_build_research_pack_populates_docs_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))

    service, _ = _service()

    result = await service.build_research_pack(
        topic="Example SDK", max_research_pages=2
    )

    assert result.status.value == "completed"
    docs_cache_dir = tmp_path / "data" / "docs" / "cache" / "docs-example-com"
    assert docs_cache_dir.exists()
    assert (docs_cache_dir / "README.md").exists()
    index_text = (tmp_path / "data" / "docs" / "cache" / ".index.md").read_text(
        encoding="utf-8"
    )
    assert "| docs-example-com | https://docs.example.com |" in index_text
    assert [citation.id for citation in result.citations] == ["S1", "S2"]
    assert len({document.id for document in result.documents}) == len(result.documents)
    assert all(
        call["selection_urls"] is not None
        for call in service._accepted_operations.compose_calls
        if call["max_results"] == 1
    )


@pytest.mark.asyncio
async def test_search_and_summarize_workflow(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("ARGUS_GATEWAY_URL", "https://gateway.example.com")
    monkeypatch.setenv("ARGUS_GATEWAY_KEY", "fake-key")

    import httpx

    # Mock gateway HTTP client POST response
    class FakeResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json_data = json_data

        def json(self):
            return self._json_data

        @property
        def text(self):
            return "ok"

    async def fake_post(self, url, headers=None, json=None):
        return FakeResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": "This is a synthesized summary answer referencing [Source 1]."
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    service, _ = _service()
    result = await service.search_and_summarize(
        query="what is search workflow", max_search_results=2
    )

    assert result.status.value == "completed"
    assert result.report_path is not None
    assert result.manifest_path is not None
    assert len(result.summary_sections) == 1
    assert result.summary_sections[0].heading == "Answer"
    assert "synthesized summary answer" in result.summary_sections[0].body
