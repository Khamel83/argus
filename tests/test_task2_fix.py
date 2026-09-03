"""Regression coverage for Task 2 workflow, clock, and cache seams."""

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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

    await service._operation_search(run, query="topic", mode="research", max_results=8)
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
        # Keep the window short enough to catch a local-time/PDT conversion,
        # while allowing the bounded fallback chain to finish on CI runners.
        freshness_window_seconds=60,
    )
    short_window_decision = extraction_module._accepted_cache.decide(
        short_window_identity,
        acceptance_repository=repository,
        now=datetime.now(timezone.utc),
    )
    assert short_window_decision.outcome is CacheOutcome.HIT_ELIGIBLE
    assert short_window_decision.age_seconds <= 60
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
    assert second.accepted_execution_evidence.cache_age_seconds <= 60
    assert trafilatura.await_count == 1


def test_accepted_cache_lookup_filters_url_identity_before_bounded_newest_rows(
    tmp_path,
):
    """An older matching receipt remains visible behind 128 newer misses."""
    from argus.extraction.extractor import _finalize_accepted_extraction
    from argus.persistence.search_ledger import (
        ExtractionOutcomeAcceptanceRow,
        ExtractionOutcomePlanRow,
        _canonical_json,
        _extraction_projection_state,
        acceptance_fingerprint,
        create_search_ledger_repository,
    )

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'bounded-cache-lookup.db'}",
        create_schema=True,
    )
    source_url = "https://example.com/bounded-cache-target"
    accepted_content = _finalize_accepted_extraction(
        ExtractedContent(
            url=source_url,
            title="Bounded cache target",
            text="durable target content " * 50,
            word_count=150,
            extractor=ExtractorName.TRAFILATURA,
        ),
        url=source_url,
        mode="default",
        caller="task2-fix",
        request_id="bounded-cache-target-request",
        latency_ms=1,
        repository=repository,
    )
    origin = repository.load_extraction_outcome_by_receipt(
        accepted_content.acceptance_receipt.receipt_ref
    )
    assert origin is not None
    target_identity = origin.normalized_url_identity
    assert target_identity

    base_state = _extraction_projection_state(origin)
    newer_than_target = datetime.now(timezone.utc) + timedelta(days=1)
    with repository.session_factory.begin() as session:
        for index in range(129):
            state = deepcopy(base_state)
            run_id = f"unrelated-cache-run-{index:03d}"
            plan_ref = f"unrelated-cache-plan-{index:03d}"
            plan_id = f"unrelated-cache-row-{index:03d}"
            state["extraction_run_id"] = run_id
            state["request_id"] = f"unrelated-cache-request-{index:03d}"
            state["plan_ref"] = plan_ref
            state["plan"]["plan_ref"] = plan_ref
            state["plan"]["normalized_url"] = f"https://unrelated.example/{index}"
            state["normalized_url_identity"] = "sha256:" + f"{index + 1:064x}"
            session.add(
                ExtractionOutcomePlanRow(
                    id=plan_id,
                    plan_ref=plan_ref,
                    extraction_run_id=run_id,
                    request_id=state["request_id"],
                    normalized_url=state["plan"]["normalized_url"],
                    access_scope=state["plan"]["access_scope"],
                    mode=state["plan"]["mode"],
                    plan_json=_canonical_json(state["plan"]),
                    source_fingerprint=acceptance_fingerprint(state),
                    created_at=newer_than_target,
                )
            )
            session.add(
                ExtractionOutcomeAcceptanceRow(
                    receipt_ref=f"unrelated-cache-receipt-{index:03d}",
                    plan_id=plan_id,
                    outcome=state["outcome"],
                    artifact_disposition=state["artifact_disposition"],
                    outcome_policy_version=(state["extraction_outcome_policy_version"]),
                    projection_json=_canonical_json(state),
                    acceptance_fingerprint=acceptance_fingerprint(state),
                    accepted_at=newer_than_target + timedelta(seconds=index),
                    scope=origin.acceptance_receipt.scope,
                )
            )

    matches = repository.find_extraction_outcomes_by_url_identity(
        target_identity,
        mode="default",
    )

    assert [item.acceptance_receipt.receipt_ref for item in matches] == [
        origin.acceptance_receipt.receipt_ref
    ]


@pytest.mark.parametrize("legacy_identity", ["bare", "missing"])
def test_accepted_cache_lookup_normalizes_legacy_url_hash_identity(
    tmp_path, legacy_identity
):
    """Legacy bare/missing identities still resolve the accepted URL exactly."""
    from sqlalchemy import select

    from argus.extraction.extractor import _finalize_accepted_extraction
    from argus.persistence.search_ledger import (
        ExtractionOutcomeAcceptanceRow,
        ExtractionOutcomePlanRow,
        _canonical_json,
        create_search_ledger_repository,
    )

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / f'legacy-{legacy_identity}.db'}",
        create_schema=True,
    )
    source_url = "https://example.com/identity-target?x=1"
    accepted_content = _finalize_accepted_extraction(
        ExtractedContent(
            url=source_url,
            title="Legacy identity target",
            text="legacy identity target content " * 50,
            word_count=150,
            extractor=ExtractorName.TRAFILATURA,
        ),
        url=source_url,
        mode="default",
        caller="task2-fix",
        request_id=f"legacy-{legacy_identity}-request",
        latency_ms=1,
        repository=repository,
    )
    receipt_ref = accepted_content.acceptance_receipt.receipt_ref
    target_identity = "sha256:" + hashlib.sha256(source_url.encode()).hexdigest()
    with repository.session_factory.begin() as session:
        plan = session.scalar(
            select(ExtractionOutcomePlanRow).where(
                ExtractionOutcomePlanRow.extraction_run_id
                == accepted_content.extraction_run_id
            )
        )
        acceptance = session.scalar(
            select(ExtractionOutcomeAcceptanceRow).where(
                ExtractionOutcomeAcceptanceRow.receipt_ref == receipt_ref
            )
        )
        assert plan is not None
        assert acceptance is not None
        state = json.loads(acceptance.projection_json)
        state["plan"]["normalized_url"] = source_url
        plan.normalized_url = source_url
        plan.plan_json = _canonical_json(state["plan"])
        if legacy_identity == "bare":
            state["normalized_url_identity"] = target_identity.removeprefix("sha256:")
        else:
            state.pop("normalized_url_identity")
        acceptance.projection_json = _canonical_json(state)

    matches = repository.find_extraction_outcomes_by_url_identity(
        target_identity,
        mode="default",
    )

    assert [item.acceptance_receipt.receipt_ref for item in matches] == [receipt_ref]
    assert matches[0].normalized_url_identity == target_identity


def test_accepted_cache_lookup_keeps_mode_boundary_and_rejects_sql_metacharacters(
    tmp_path,
):
    """Legacy URL hashes never cross mode or become a SQL predicate."""
    from sqlalchemy import select

    from argus.extraction.extractor import _finalize_accepted_extraction
    from argus.persistence.search_ledger import (
        ExtractionOutcomeAcceptanceRow,
        ExtractionOutcomePlanRow,
        _canonical_json,
        create_search_ledger_repository,
    )

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'legacy-boundary.db'}",
        create_schema=True,
    )
    source_url = "https://example.com/identity-target?x=1"
    accepted_content = _finalize_accepted_extraction(
        ExtractedContent(
            url=source_url,
            title="Legacy mode target",
            text="legacy mode target content " * 50,
            word_count=150,
            extractor=ExtractorName.TRAFILATURA,
        ),
        url=source_url,
        mode="archive_ingest",
        caller="task2-fix",
        request_id="legacy-mode-request",
        latency_ms=1,
        repository=repository,
    )
    receipt_ref = accepted_content.acceptance_receipt.receipt_ref
    target_identity = "sha256:" + hashlib.sha256(source_url.encode()).hexdigest()
    with repository.session_factory.begin() as session:
        plan = session.scalar(
            select(ExtractionOutcomePlanRow).where(
                ExtractionOutcomePlanRow.extraction_run_id
                == accepted_content.extraction_run_id
            )
        )
        acceptance = session.scalar(
            select(ExtractionOutcomeAcceptanceRow).where(
                ExtractionOutcomeAcceptanceRow.receipt_ref == receipt_ref
            )
        )
        assert plan is not None
        assert acceptance is not None
        state = json.loads(acceptance.projection_json)
        state["plan"]["normalized_url"] = source_url
        state.pop("normalized_url_identity")
        plan.normalized_url = source_url
        plan.plan_json = _canonical_json(state["plan"])
        acceptance.projection_json = _canonical_json(state)

    assert (
        repository.find_extraction_outcomes_by_url_identity(
            target_identity,
            mode="default",
        )
        == []
    )
    assert (
        repository.find_extraction_outcomes_by_url_identity(
            'sha256:%" OR 1=1 --',
            mode="archive_ingest",
        )
        == []
    )
