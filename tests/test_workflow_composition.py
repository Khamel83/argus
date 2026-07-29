"""Workflow evidence composition and rejected-content safety."""

import asyncio
import multiprocessing
from dataclasses import fields, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from argus.broker.accepted import (
    AcceptanceReceipt,
    AcceptedRetrieval,
    CacheOutcome,
)
from argus.contracts import AcceptedOperation, CanonicalOutcome
from argus.extraction.outcomes import (
    AcceptedExtractionOutcome,
    FinalizedExtractionProjection,
)
from argus.operations.accepted import (
    AcceptedOperationRegistration,
    AcceptedOperationService,
    _operation_error,
)
from argus.persistence.evidence import RetrievalEvidence, SqlAlchemyEvidenceRepository
from argus.persistence.search_ledger import create_search_ledger_repository


def _error(outcome, request_id):
    return _operation_error(
        outcome,
        request_id=request_id,
        detail="workflow composition failed",
    )


def _projection(accepted):
    values = {
        field.name: getattr(accepted, field.name)
        for field in fields(FinalizedExtractionProjection)
    }
    return FinalizedExtractionProjection(**values)


def _real_authority(tmp_path, links):
    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'workflow-authority.db'}",
        create_schema=True,
    )
    durable = {}
    for link in links:
        accepted = link.accepted_outcome
        receipt = repository.accept_extraction_outcome(_projection(accepted))
        durable[accepted.plan.normalized_url] = AcceptedExtractionOutcome.accepted(
            _projection(accepted),
            receipt,
        )

    async def extract(request, *, principal, request_id):
        accepted = durable[request.url]
        return AcceptedOperation(
            outcome=accepted.outcome,
            request_id=request_id,
            result={"extraction_run_id": accepted.extraction_run_id},
            error=(
                None
                if accepted.outcome
                in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}
                else _error(accepted.outcome, request_id)
            ),
        )

    service = AcceptedOperationService(
        broker_provider=lambda: SimpleNamespace(),
        repository_provider=lambda: repository,
        registration=AcceptedOperationRegistration.complete(),
    )
    service.extract = AsyncMock(side_effect=extract)
    return service, repository


def _accepted_retrieval(repository, results, *, receipt_ref="receipt:workflow"):
    receipt = AcceptanceReceipt(
        receipt_ref=receipt_ref,
        accepted_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        acceptance_fingerprint="a" * 64,
    )
    accepted = AcceptedRetrieval(
        operation_id=receipt_ref.removeprefix("receipt:"),
        plan_id="workflow-plan",
        cache_fingerprint="cache-workflow",
        execution_cohort="workflow-cohort",
        outcome=CacheOutcome.SUCCESS,
        reason="accepted",
        query="workflow",
        mode="discovery",
        results=tuple(results),
        contributor_attempt_refs=("attempt-workflow",),
        origin_spend_usd="0",
        acceptance_receipt=receipt,
    )
    SqlAlchemyEvidenceRepository(repository.session_factory).accept(
        RetrievalEvidence.from_accepted(accepted)
    )
    return AcceptedOperation(
        outcome=CanonicalOutcome.SUCCESS,
        request_id="retrieval-workflow",
        result={
            "results": tuple(results),
            "acceptance_receipt": {"receipt_ref": receipt_ref},
        },
        error=None,
    )


def _cross_process_compose_worker(
    database_path: str,
    extraction_run_id: str,
    extraction_counter_path: str,
    barrier,
    output,
) -> None:
    repository = create_search_ledger_repository(
        f"sqlite:///{database_path}",
        create_schema=True,
    )
    service = AcceptedOperationService(
        broker_provider=lambda: SimpleNamespace(),
        repository_provider=lambda: repository,
        registration=AcceptedOperationRegistration.complete(),
    )

    async def extract(request, *, principal, request_id):
        with open(extraction_counter_path, "a", encoding="utf-8") as counter:
            counter.write(f"{request_id}\n")
        await asyncio.sleep(0.2)
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={"extraction_run_id": extraction_run_id},
            error=None,
        )

    service.extract = extract
    retrieval = _accepted_retrieval(
        repository,
        ({"url": "https://example.com/1", "title": "Article"},),
    )
    barrier.wait()
    composed = asyncio.run(
        service.compose_workflow(
            retrieval,
            max_results=1,
            principal="process-test",
            request_id=f"process-{multiprocessing.current_process().pid}",
        )
    )
    output.put(
        (
            composed.outcome.value,
            composed.result["composition_receipt_ref"],
            composed.result["requirement_ref"],
        )
    )


@pytest.mark.asyncio
async def test_real_authority_hashes_frozen_retrieval_and_returns_full_projection(
    tmp_path,
):
    from tests.test_extraction_composition import _link

    service, repository = _real_authority(tmp_path, [_link()])
    retrieval = _accepted_retrieval(
        repository,
        (
            {
                "url": "https://example.com/1",
                "title": "Article",
                "snippet": "accepted",
                "domain": "example.com",
                "provider": "duckduckgo",
                "score": 1.0,
                "egress": "residential",
                "machine": "test-node",
                "score_attribution": {},
            },
        ),
    )

    composed = await service.compose_workflow(
        retrieval,
        max_results=1,
        principal="workflow-test",
        request_id="compose-frozen",
    )

    assert composed.outcome is CanonicalOutcome.SUCCESS
    assert composed.result["artifact_requirement"]["requirement_ref"]
    assert len(composed.result["links"]) == 1
    assert composed.result["accepted_artifact_refs"] == ("artifact-1",)
    assert composed.result["degraded_artifact_refs"] == ()
    assert composed.result["rejected_extraction_refs"] == ()
    assert composed.result["composition_trace"] == ("artifact_floor_met",)
    assert composed.result["composition_receipt"]["receipt_ref"]
    assert composed.result["retrieval_outcome"] == "success"
    assert composed.result["artifact_outcome"] == "success"
    assert composed.result["composite_outcome"] == "success"
    assert composed.result["composition_outcome"] == "success"
    assert composed.result["artifacts"][0]["artifact_ref"] == "artifact-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("links", "kwargs", "expected"),
    (
        pytest.param(
            ("success", "optional-rejection"),
            {},
            CanonicalOutcome.DEGRADED,
            id="optional-rejection-degrades",
        ),
        pytest.param(
            ("required-rejection", "success"),
            {},
            CanonicalOutcome.EXTRACTION_FAILED,
            id="required-rejection-fails",
        ),
        pytest.param(
            ("unready",),
            {},
            CanonicalOutcome.UNREADY,
            id="no-eligible-path-is-unready",
        ),
        pytest.param(
            ("partial",),
            {"allow_partial": False},
            CanonicalOutcome.EXTRACTION_FAILED,
            id="per-result-usable-floor",
        ),
        pytest.param(
            ("success", "optional-rejection"),
            {"minimum_artifacts": 2},
            CanonicalOutcome.EXTRACTION_FAILED,
            id="aggregate-floor",
        ),
        pytest.param(
            ("partial",),
            {"allow_partial": True},
            CanonicalOutcome.DEGRADED,
            id="explicit-partial-policy",
        ),
    ),
)
async def test_real_authority_enforces_required_composition_matrix(
    tmp_path,
    links,
    kwargs,
    expected,
):
    from argus.extraction.outcomes import ArtifactDisposition
    from tests.test_extraction_composition import _link

    built = []
    results = []
    for ordinal, kind in enumerate(links, start=1):
        options = {"cluster": f"cluster-{ordinal}"}
        if kind == "optional-rejection":
            options.update(
                outcome=CanonicalOutcome.EXTRACTION_FAILED,
                disposition=ArtifactDisposition.DIAGNOSTIC_ONLY,
                required=False,
            )
        elif kind == "required-rejection":
            options.update(
                outcome=CanonicalOutcome.EXTRACTION_FAILED,
                disposition=ArtifactDisposition.DIAGNOSTIC_ONLY,
            )
        elif kind == "unready":
            options.update(
                outcome=CanonicalOutcome.UNREADY,
                disposition=ArtifactDisposition.NONE,
                eligible_path=False,
                attempted=False,
            )
        elif kind == "partial":
            options.update(
                outcome=CanonicalOutcome.DEGRADED,
                disposition=ArtifactDisposition.PARTIAL,
            )
        built.append(_link(**options))
        results.append(
            {
                "url": f"https://example.com/{ordinal}",
                "title": f"Article {ordinal}",
                "snippet": "accepted",
                "domain": "example.com",
                "provider": "duckduckgo",
                "score": 1.0,
                "egress": "residential",
                "machine": "test-node",
                "score_attribution": {},
            }
        )
    service, repository = _real_authority(tmp_path, built)
    retrieval = _accepted_retrieval(repository, tuple(results))

    composed = await service.compose_workflow(
        retrieval,
        max_results=len(results),
        principal="workflow-test",
        request_id="compose-matrix",
        **kwargs,
    )

    assert composed.outcome is expected
    assert composed.result is not None
    assert len(composed.result["links"]) == len(results)
    assert composed.result["composition_receipt_ref"]


@pytest.mark.asyncio
async def test_real_authority_missing_typed_outcome_is_persistence_failed_without_link(
    tmp_path,
):
    from tests.test_extraction_composition import _link

    service, repository = _real_authority(tmp_path, [_link()])
    retrieval = _accepted_retrieval(
        repository,
        ({"url": "https://example.com/1", "title": "Article"},),
    )
    service.extract = AsyncMock(
        return_value=AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id="missing-extraction",
            result={"extraction_run_id": "missing-durable-run"},
            error=None,
        )
    )

    composed = await service.compose_workflow(
        retrieval,
        max_results=1,
        principal="workflow-test",
        request_id="compose-missing",
    )

    assert composed.outcome is CanonicalOutcome.PERSISTENCE_FAILED
    assert composed.result is None
    assert (
        repository.load_accepted_workflow_composition(
            "receipt:workflow",
            "workflow-missing",
        )
        is None
    )


@pytest.mark.asyncio
async def test_real_authority_persistence_and_extraction_exception_paths_are_typed(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy.exc import OperationalError
    from tests.test_extraction_composition import _link

    service, repository = _real_authority(tmp_path, [_link()])
    retrieval = _accepted_retrieval(
        repository,
        ({"url": "https://example.com/1", "title": "Article"},),
    )
    monkeypatch.setattr(
        repository,
        "accept_workflow_retrieval_composition",
        lambda *_args: (_ for _ in ()).throw(
            OperationalError("accept", {}, RuntimeError("write failed"))
        ),
    )
    persistence_failed = await service.compose_workflow(
        retrieval,
        max_results=1,
        principal="workflow-test",
        request_id="compose-write-failed",
    )

    extraction_path = tmp_path / "extract-exception"
    extraction_path.mkdir()
    service, repository = _real_authority(extraction_path, [_link()])
    retrieval = _accepted_retrieval(
        repository, ({"url": "https://example.com/1", "title": "Article"},)
    )

    async def raising_extractor(*_args, **_kwargs):
        raise RuntimeError("raw extractor detail must not escape")

    service._extractor = raising_extractor
    service.extract = AcceptedOperationService.extract.__get__(service)
    extraction_failed = await service.compose_workflow(
        retrieval,
        max_results=1,
        principal="workflow-test",
        request_id="compose-extractor-failed",
    )

    assert persistence_failed.outcome is CanonicalOutcome.PERSISTENCE_FAILED
    assert persistence_failed.result is None
    assert extraction_failed.outcome is CanonicalOutcome.PERSISTENCE_FAILED
    assert extraction_failed.error.detail == "Accepted extraction evidence is missing"
    assert "raw extractor detail" not in extraction_failed.error.detail


@pytest.mark.asyncio
async def test_real_authority_resumes_accepted_composition_without_reextracting(
    tmp_path,
):
    from tests.test_extraction_composition import _link

    service, repository = _real_authority(tmp_path, [_link()])
    retrieval = _accepted_retrieval(
        repository,
        ({"url": "https://example.com/1", "title": "Article"},),
    )

    first = await service.compose_workflow(
        retrieval,
        max_results=1,
        principal="workflow-test",
        request_id="compose-first",
    )
    restarted = AcceptedOperationService(
        broker_provider=lambda: SimpleNamespace(),
        repository_provider=lambda: repository,
        registration=AcceptedOperationRegistration.complete(),
    )
    restarted.extract = AsyncMock(
        side_effect=AssertionError("resume attempted a new extraction")
    )
    second = await restarted.compose_workflow(
        retrieval,
        max_results=1,
        principal="workflow-test",
        request_id="compose-resume",
    )

    assert service.extract.await_count == 1
    restarted.extract.assert_not_awaited()
    assert second.outcome is first.outcome
    assert second.result == first.result


@pytest.mark.asyncio
async def test_concurrent_same_receipt_extracts_once_and_remains_resumable(tmp_path):
    from tests.test_extraction_composition import _link

    service, repository = _real_authority(tmp_path, [_link()])
    retrieval = _accepted_retrieval(
        repository,
        ({"url": "https://example.com/1", "title": "Article"},),
    )

    first, second = await asyncio.gather(
        service.compose_workflow(
            retrieval,
            max_results=1,
            principal="caller-a",
            request_id="compose-a",
        ),
        service.compose_workflow(
            retrieval,
            max_results=1,
            principal="caller-b",
            request_id="compose-b",
        ),
    )

    assert service.extract.await_count == 1
    assert first.result == second.result
    third = await service.compose_workflow(
        retrieval,
        max_results=1,
        principal="caller-c",
        request_id="compose-c",
    )
    assert third.result == first.result
    assert service.extract.await_count == 1


def test_sqlite_composition_claim_is_cross_process_and_resumes_stably(tmp_path):
    from tests.test_extraction_composition import _link

    database_path = tmp_path / "cross-process.db"
    counter_path = tmp_path / "extractions.txt"
    repository = create_search_ledger_repository(
        f"sqlite:///{database_path}",
        create_schema=True,
    )
    accepted = _link().accepted_outcome
    repository.accept_extraction_outcome(_projection(accepted))
    _accepted_retrieval(
        repository,
        ({"url": "https://example.com/1", "title": "Article"},),
    )
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    output = context.Queue()
    processes = [
        context.Process(
            target=_cross_process_compose_worker,
            args=(
                str(database_path),
                accepted.extraction_run_id,
                str(counter_path),
                barrier,
                output,
            ),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(15)

    assert [process.exitcode for process in processes] == [0, 0]
    outcomes = [output.get(timeout=2) for _ in processes]
    assert outcomes[0] == outcomes[1]
    assert counter_path.read_text(encoding="utf-8").count("\n") == 1

    resumed = AcceptedOperationService(
        broker_provider=lambda: SimpleNamespace(),
        repository_provider=lambda: create_search_ledger_repository(
            f"sqlite:///{database_path}",
            create_schema=True,
        ),
        registration=AcceptedOperationRegistration.complete(),
    )
    resumed.extract = AsyncMock(
        side_effect=AssertionError("stable resume attempted extraction")
    )
    retrieval = _accepted_retrieval(
        repository,
        ({"url": "https://example.com/1", "title": "Article"},),
    )
    final = asyncio.run(
        resumed.compose_workflow(
            retrieval,
            max_results=1,
            principal="parent-test",
            request_id="parent-resume",
        )
    )
    assert final.result["composition_receipt_ref"] == outcomes[0][1]
    resumed.extract.assert_not_awaited()
    from argus.persistence import search_ledger

    assert search_ledger._WORKFLOW_COMPOSITION_LOCKS == {}


@pytest.mark.asyncio
async def test_cancelled_sqlite_claim_waiter_releases_process_lock_entry(tmp_path):
    from argus.persistence import search_ledger

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'cancel-claim.db'}",
        create_schema=True,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with repository.workflow_composition_claim("receipt", "requirement"):
            entered.set()
            await release.wait()

    async def waiter():
        async with repository.workflow_composition_claim("receipt", "requirement"):
            raise AssertionError("cancelled waiter entered the claim")

    holding = asyncio.create_task(holder())
    await entered.wait()
    waiting = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    waiting.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await holding
    await asyncio.sleep(0.1)

    assert search_ledger._WORKFLOW_COMPOSITION_LOCKS == {}


@pytest.mark.asyncio
async def test_cancelled_sqlite_claim_holder_releases_file_and_process_locks(tmp_path):
    from argus.persistence import search_ledger

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'cancel-holder.db'}",
        create_schema=True,
    )
    entered = asyncio.Event()

    async def holder():
        async with repository.workflow_composition_claim("receipt", "requirement"):
            entered.set()
            await asyncio.Event().wait()

    holding = asyncio.create_task(holder())
    await entered.wait()
    holding.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holding

    async with repository.workflow_composition_claim("receipt", "requirement"):
        pass
    assert search_ledger._WORKFLOW_COMPOSITION_LOCKS == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pages", "expected_url"),
    (
        pytest.param(
            {
                "https://site.example.com": "<html><body>Home</body></html>",
                "https://site.example.com/sitemap.xml": (
                    "<urlset><url><loc>https://site.example.com/sitemap-only"
                    "</loc></url></urlset>"
                ),
            },
            "https://site.example.com/sitemap-only",
            id="sitemap-only",
        ),
        pytest.param(
            {
                "https://site.example.com": (
                    '<html><body><a href="/internal-only">Internal</a></body></html>'
                ),
            },
            "https://site.example.com/internal-only",
            id="internal-link-only",
        ),
    ),
)
async def test_site_acquisition_accepts_sitemap_and_internal_link_pages(
    tmp_path, pages, expected_url
):
    from tests.test_extraction_composition import _link

    accepted_links = []
    for ordinal, url in enumerate(
        ("https://site.example.com", expected_url),
        start=1,
    ):
        accepted = _link(f"cluster-{ordinal}").accepted_outcome
        plan = replace(
            accepted.plan,
            plan_ref=f"site-plan-{ordinal}",
            normalized_url=url,
        )
        accepted_links.append(
            SimpleNamespace(
                accepted_outcome=replace(
                    accepted,
                    plan=plan,
                    plan_ref=plan.plan_ref,
                )
            )
        )
    service, repository = _real_authority(tmp_path, accepted_links)
    base = _accepted_retrieval(
        repository,
        ({"url": "https://site.example.com", "title": "Home"},),
        receipt_ref=f"receipt:{expected_url.rsplit('/', 1)[-1]}",
    )

    async def fetch(url):
        if url not in pages:
            raise OSError("not found")
        return pages[url]

    service.search = AsyncMock(return_value=base)
    service._site_fetcher = fetch
    acquired = await service.acquire_site(
        SimpleNamespace(
            url="https://site.example.com",
            soft_page_limit=10,
            hard_page_limit=20,
            caller="site-test",
        ),
        principal="site-principal",
        request_id=f"site-{expected_url.rsplit('/', 1)[-1]}",
    )

    assert acquired.outcome is CanonicalOutcome.SUCCESS
    assert expected_url in {item["url"] for item in acquired.result["results"]}
    receipt_ref = acquired.result["acceptance_receipt"]["receipt_ref"]
    durable = repository.load_accepted_retrieval_results(receipt_ref)
    assert durable == acquired.result["results"]
    selected_urls = tuple(item["url"] for item in acquired.result["results"])
    assert selected_urls == ("https://site.example.com", expected_url)
    composed = await service.compose_workflow(
        acquired,
        max_results=len(selected_urls),
        selection_urls=selected_urls,
        principal="site-principal",
        request_id=f"compose-{expected_url.rsplit('/', 1)[-1]}",
    )
    assert len(composed.result["links"]) == len(selected_urls)
    from argus.contracts.result_refs import accepted_result_refs

    assert {link["result_cluster_ref"] for link in composed.result["links"]} == set(
        accepted_result_refs(acquired.result["results"])
    )


@pytest.mark.asyncio
async def test_real_authority_verifies_exact_url_selection_and_receipt_binding(
    tmp_path,
):
    from tests.test_extraction_composition import _link

    service, repository = _real_authority(tmp_path, [_link(), _link("cluster-2")])
    results = (
        {"url": "https://example.com/1", "title": "One"},
        {"url": "https://example.com/2", "title": "Two"},
    )
    retrieval = _accepted_retrieval(repository, results)

    selected = await service.compose_workflow(
        retrieval,
        max_results=1,
        selection_urls=("https://example.com/2",),
        principal="workflow-test",
        request_id="compose-selected",
    )
    missing = await service.compose_workflow(
        retrieval,
        max_results=1,
        selection_urls=("https://example.com/missing",),
        principal="workflow-test",
        request_id="compose-missing-selection",
    )
    tampered = AcceptedOperation(
        outcome=CanonicalOutcome.SUCCESS,
        request_id="retrieval-tampered",
        result={
            "results": ({"url": "https://attacker.example", "title": "Tampered"},),
            "acceptance_receipt": {"receipt_ref": "receipt:workflow"},
        },
        error=None,
    )
    unbound = await service.compose_workflow(
        tampered,
        max_results=1,
        principal="workflow-test",
        request_id="compose-unbound",
    )

    assert selected.outcome is CanonicalOutcome.SUCCESS
    assert [artifact["url"] for artifact in selected.result["artifacts"]] == [
        "https://example.com/2"
    ]
    assert missing.outcome is CanonicalOutcome.UNREADY
    assert unbound.outcome is CanonicalOutcome.UNREADY


@pytest.mark.asyncio
async def test_legacy_retrieval_row_without_result_binding_proof_fails_closed(tmp_path):
    from sqlalchemy import update
    from argus.persistence.evidence import RetrievalEvidencePlanRow
    from tests.test_extraction_composition import _link

    service, repository = _real_authority(tmp_path, [_link()])
    retrieval = _accepted_retrieval(
        repository,
        ({"url": "https://example.com/1", "title": "Article"},),
    )
    with repository.session_factory.begin() as session:
        session.execute(update(RetrievalEvidencePlanRow).values(plan_json="{}"))

    composed = await service.compose_workflow(
        retrieval,
        max_results=1,
        principal="workflow-test",
        request_id="compose-legacy-unbound",
    )

    assert composed.outcome is CanonicalOutcome.UNREADY
    service.extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_authority_marks_shared_eligible_artifact_reuse(tmp_path):
    from tests.test_extraction_composition import _link

    shared = _link()
    service, repository = _real_authority(tmp_path, [shared])
    duplicate = {"url": "https://example.com/1", "title": "Shared"}
    retrieval = _accepted_retrieval(repository, (duplicate, duplicate))

    composed = await service.compose_workflow(
        retrieval,
        max_results=2,
        principal="workflow-test",
        request_id="compose-shared",
    )

    assert composed.outcome is CanonicalOutcome.SUCCESS
    assert len(composed.result["links"]) == 2
    assert all(link["reuse_origin"] for link in composed.result["links"])


@pytest.mark.asyncio
async def test_rejected_artifact_never_reaches_document_or_summarizer(
    monkeypatch, tmp_path
):
    """A failed composition preserves its link but cannot synthesize or deliver."""
    from argus.workflows import WorkflowService
    from argus.workflows import service as workflow_service

    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path / "data"))

    class Operations:
        async def search(self, request, *, principal, request_id):
            return AcceptedOperation(
                outcome=CanonicalOutcome.SUCCESS,
                request_id="workflow-search-2",
                result={
                    "results": (
                        {"url": "https://example.com/rejected", "title": "Rejected"},
                    ),
                    "acceptance_receipt": {"receipt_ref": "receipt-search-2"},
                },
                error=None,
            )

        async def acquire_site(self, request, *, principal, request_id):
            return await self.search(
                SimpleNamespace(),
                principal=principal,
                request_id=request_id,
            )

        async def compose_workflow(
            self, retrieval, *, max_results, principal, request_id, **_kwargs
        ):
            return AcceptedOperation(
                outcome=CanonicalOutcome.EXTRACTION_FAILED,
                request_id=request_id,
                result={
                    "requirement_ref": "workflow-required-rejection",
                    "composition_receipt_ref": "composition-rejected",
                    "composition_outcome": "extraction_failed",
                    "accepted_artifact_refs": (),
                    "degraded_artifact_refs": (),
                    "rejected_extraction_refs": ("extract-rejected",),
                    "composition_trace": ("artifact_floor_unmet",),
                    "links": (
                        {
                            "result_cluster_ref": "cluster-rejected",
                            "artifact_disposition": "diagnostic_only",
                        },
                    ),
                    "artifacts": (
                        {
                            "url": "https://example.com/rejected",
                            "title": "Rejected",
                            "text": "diagnostic content",
                            "word_count": 2,
                            "disposition": "diagnostic_only",
                            "extractor": "trafilatura",
                        },
                    ),
                },
                error=_error(CanonicalOutcome.EXTRACTION_FAILED, request_id),
            )

    class Gateway:
        def __init__(self):
            self.compositions = []

    def prohibited_summarizer(*args, **kwargs):
        raise AssertionError("rejected content reached the summarizer")

    def prohibited_constructor(*args, **kwargs):
        raise AssertionError("rejected content reached a downstream constructor")

    def prohibited_report(*args, **kwargs):
        raise AssertionError("failed composition reached report construction")

    def prohibited_delivery(*args, **kwargs):
        raise AssertionError("failed composition reached external delivery")

    gateway = Gateway()
    monkeypatch.setattr(workflow_service, "get_summarizer", prohibited_summarizer)
    monkeypatch.setattr(workflow_service, "StoredDocument", prohibited_constructor)
    monkeypatch.setattr(workflow_service, "CitationRef", prohibited_constructor)
    monkeypatch.setattr(
        workflow_service.WorkflowService, "_finalize_run", prohibited_report
    )
    monkeypatch.setattr(
        workflow_service.WorkflowService,
        "_replace_directory",
        prohibited_delivery,
    )
    result = await WorkflowService(Operations()).capture_site(
        url="https://example.com/rejected",
        soft_page_limit=1,
        hard_page_limit=1,
    )

    assert result.status.value == "failed"
    assert result.documents == []
    assert result.citations == []
    assert result.report_path is None
    assert gateway.compositions == []
    assert result.error == "workflow_composition_extraction_failed"
    assert result.metadata["failure"] == {
        "outcome": "extraction_failed",
        "code": "extraction_failed",
    }
    assert result.metadata["composition"]["outcome"] == "extraction_failed"
    assert result.metadata["composition"]["rejected_extraction_refs"] == [
        "extract-rejected"
    ]
