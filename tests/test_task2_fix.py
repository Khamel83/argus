"""Regression coverage for Task 2 workflow, clock, and cache seams."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from argus.api import routes_workflows
from argus.api.schemas import BuildResearchPackWorkflowRequest
from argus.contracts import AcceptedOperation, CanonicalOutcome
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.workflows.models import WorkflowKind
from argus.workflows.service import WorkflowService


class _RecordingOperations:
    def __init__(self):
        self.search_requests = []
        self.site_requests = []
        self.compose_kwargs = []

    @staticmethod
    def _accepted(request_id: str):
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={
                "results": (),
                "composition_receipt_ref": f"composition-{request_id}",
                "artifacts": (),
            },
            error=None,
        )

    async def search(self, request, *, principal, request_id):
        del principal
        self.search_requests.append(request)
        return self._accepted(request_id)

    async def acquire_site(self, request, *, principal, request_id):
        del principal
        self.site_requests.append(request)
        return self._accepted(request_id)

    async def compose_workflow(self, retrieval, **kwargs):
        del retrieval
        self.compose_kwargs.append(kwargs)
        return self._accepted(kwargs["request_id"])


@pytest.mark.asyncio
async def test_workflow_operation_and_composition_requests_retain_free_only(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    operations = _RecordingOperations()
    service = WorkflowService(operations)
    run = service._create_run(WorkflowKind.BUILD_RESEARCH_PACK, "topic")
    run.metadata["free_only"] = True

    await service._operation_search(
        run, query="topic", mode="research", max_results=8
    )
    await service._operation_acquire_site(
        run,
        url="https://docs.example.com",
        soft_page_limit=1,
        hard_page_limit=1,
    )
    await service._compose_search_documents(
        run,
        operations._accepted("retrieval"),
        max_results=0,
        section="empty",
        role="source",
        source_type="web",
    )

    assert operations.search_requests[0].free_only is True
    assert operations.site_requests[0].free_only is True
    assert operations.compose_kwargs[0]["free_only"] is True


@pytest.mark.asyncio
async def test_build_research_pack_start_and_sync_paths_forward_free_only(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))
    service = WorkflowService(SimpleNamespace())
    captured = []

    async def execute(run_id, handler, **kwargs):
        del run_id, handler
        captured.append(kwargs)

    monkeypatch.setattr(service, "_execute_run", execute)
    await service.start_build_research_pack(topic="topic", free_only=True)
    await asyncio.sleep(0)

    async def implementation(run, **kwargs):
        del run
        captured.append(kwargs)

    monkeypatch.setattr(service, "_build_research_pack_impl", implementation)
    await service.build_research_pack(topic="topic", free_only=True)

    assert [entry["free_only"] for entry in captured] == [True, True]


@pytest.mark.asyncio
async def test_build_research_pack_route_forwards_free_only(monkeypatch):
    captured = {}

    class FakeWorkflows:
        async def start_build_research_pack(self, **kwargs):
            captured.update(kwargs)
            return captured

    monkeypatch.setattr(routes_workflows, "_to_response", lambda run: run)
    request = SimpleNamespace(
        state=SimpleNamespace(caller_identity="principal"),
        app=SimpleNamespace(state=SimpleNamespace()),
    )

    await routes_workflows.build_research_pack(
        BuildResearchPackWorkflowRequest(topic="topic", free_only=True),
        request,
        FakeWorkflows(),
    )

    assert captured["free_only"] is True


@pytest.mark.asyncio
async def test_accepted_cache_reload_uses_utc_receipt_age_and_no_second_extractor(
    monkeypatch, tmp_path
):
    from argus.config import reset_config
    from argus.extraction import extractor as extraction_module
    from argus.persistence.search_ledger import create_search_ledger_repository

    monkeypatch.setenv("ARGUS_RESIDENTIAL_POLICY", "off")
    monkeypatch.setenv("ARGUS_JINA_ENABLED", "false")
    monkeypatch.setenv("ARGUS_CRAWL4AI_ENABLED", "false")
    monkeypatch.setenv("ARGUS_YOU_CONTENTS_ENABLED", "false")
    reset_config()
    extraction_module._accepted_cache.clear()

    observed_utc = datetime.now(timezone.utc)
    pacific_clock = observed_utc.astimezone(ZoneInfo("America/Los_Angeles"))
    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'restart-cache.db'}",
        clock=lambda: pacific_clock,
    )
    content = ExtractedContent(
        url="https://example.com/restart-cache",
        title="Restart cache",
        text="durable restart cache content " * 50,
        word_count=150,
        extractor=ExtractorName.TRAFILATURA,
    )
    trafilatura = AsyncMock(return_value=content)
    monkeypatch.setattr(extraction_module, "_extract_trafilatura", trafilatura)

    first = await extraction_module.extract_url(
        content.url,
        caller="task2-fix",
        repository=repository,
        use_evidence_authority=True,
        request_id="task2-fix-first",
    )
    extraction_module._accepted_cache.clear()
    from argus.extraction.outcomes import CacheOutcome

    short_window_identity = replace(
        extraction_module._accepted_cache_identity(
            content.url,
            "default",
            True,
        ),
        freshness_window_seconds=5,
    )
    short_window_decision = extraction_module._accepted_cache.decide(
        short_window_identity,
        acceptance_repository=repository,
        now=datetime.now(timezone.utc),
    )
    assert short_window_decision.outcome is CacheOutcome.HIT_ELIGIBLE
    assert short_window_decision.age_seconds <= 5
    second = await extraction_module.extract_url(
        content.url,
        caller="task2-fix",
        free_only=True,
        repository=repository,
        use_evidence_authority=True,
        request_id="task2-fix-second",
    )

    assert first.text == second.text
    assert second.cache_hit is True
    # The first extraction may spend a few seconds exhausting completeness
    # fallbacks; UTC normalization must keep the durable age in that small
    # elapsed-time range rather than shifting by the PDT offset.
    assert second.accepted_execution_evidence.cache_age_seconds <= 5
    assert trafilatura.await_count == 1
