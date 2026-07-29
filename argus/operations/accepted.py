"""Single execution-and-acceptance authority for transport presenters."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import Enum
from types import SimpleNamespace
from typing import Awaitable, Callable, Mapping

from argus.contracts import (
    AcceptedOperation,
    CanonicalOutcome,
    OperationError,
    http_status_for,
)
from argus.extraction import extract_url
from argus.extraction.composition import (
    AggregateArtifactFloor,
    ArtifactRequirement,
    ArtifactSelection,
    ResultExtractionLink,
    compose_retrieval_evidence,
)
from argus.extraction.outcomes import ArtifactDisposition
from argus.models import ProviderName, SearchMode, SearchQuery, SearchResult
from argus.recovery.archive_ph import try_archive_ph
from argus.operations.site_acquisition import (
    _normalized_hostname,
    _same_site,
    discover_site_urls,
    fetch_site_text,
    looks_like_html,
    site_url_score,
)


class AcceptedAuthorityConfigurationError(RuntimeError):
    """The atomic evidence authority was requested without every dependency."""


@dataclass(frozen=True, slots=True)
class _WorkflowRetrievalView:
    outcome: CanonicalOutcome
    result_cluster_refs: tuple[str, ...]
    acceptance_receipt: str
    result_projections: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class AcceptedOperationRegistration:
    planner: bool = False
    readiness: bool = False
    evidence_repository: bool = False
    extraction_finalizer: bool = False
    legacy_presenters: bool = False

    @classmethod
    def complete(cls) -> "AcceptedOperationRegistration":
        return cls(
            planner=True,
            readiness=True,
            evidence_repository=True,
            extraction_finalizer=True,
            legacy_presenters=True,
        )

    def validate(self, authority: str) -> None:
        if authority not in {"legacy", "evidence"}:
            raise AcceptedAuthorityConfigurationError(
                "ARGUS_ACCEPTED_OPERATION_AUTHORITY must be legacy or evidence"
            )
        if authority == "legacy":
            return
        missing = [
            field.name for field in fields(self) if not getattr(self, field.name)
        ]
        if missing:
            raise AcceptedAuthorityConfigurationError(
                "evidence authority registration is missing: " + ", ".join(missing)
            )

    def capability_registrations(self) -> frozenset[str]:
        self.validate("evidence")
        return frozenset(
            {
                "accepted_service",
                "legacy_presenter",
                "v2_presenter",
                "v2_routes",
                "transport_security",
            }
        )


def _operation_error(
    outcome: CanonicalOutcome,
    *,
    request_id: str,
    detail: str,
    code: str | None = None,
    status: int | None = None,
    operation_began: bool = True,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
) -> OperationError:
    code = code or outcome.value
    return OperationError(
        outcome=outcome,
        type=f"urn:argus:problem:{code}",
        title=code.replace("_", " ").title(),
        status=status or http_status_for(outcome, code),
        detail=detail,
        instance=f"urn:argus:request:{request_id}",
        code=code,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        operation_began=operation_began,
    )


def _canonical_json_value(value):
    if is_dataclass(value):
        return _canonical_json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _canonical_json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(child) for child in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _canonical_json(value) -> str:
    return json.dumps(
        _canonical_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
    )


def _workflow_projection(requirement, composition, receipt, artifacts):
    requirement_state = _canonical_json_value(requirement)
    composition_state = _canonical_json_value(composition)
    for link in composition_state["links"]:
        link.pop("accepted_outcome", None)
    receipt_state = _canonical_json_value(receipt)
    return {
        "requirement_ref": requirement.requirement_ref,
        "artifact_requirement": requirement_state,
        "links": composition_state["links"],
        "accepted_artifact_refs": composition_state["accepted_artifact_refs"],
        "degraded_artifact_refs": composition_state["degraded_artifact_refs"],
        "rejected_extraction_refs": composition_state["rejected_extraction_refs"],
        "composition_trace": composition_state["composition_trace"],
        "composition_receipt": receipt_state,
        "composition_receipt_ref": receipt_state["receipt_ref"],
        "retrieval_outcome": composition_state["retrieval_outcome"],
        "artifact_outcome": composition_state["artifact_outcome"],
        "composite_outcome": composition_state["composite_outcome"],
        "composition_outcome": composition_state["composite_outcome"],
        "artifacts": artifacts,
    }


def _workflow_requirement_ref(
    *,
    receipt_ref: str,
    refs: tuple[str, ...],
    required: frozenset[str],
    minimum_disposition: ArtifactDisposition,
    aggregate_count: int,
) -> str:
    identity = hashlib.sha256(
        _canonical_json(
            {
                "receipt_ref": receipt_ref,
                "refs": refs,
                "required_urls": tuple(sorted(required)),
                "minimum_disposition": minimum_disposition,
                "aggregate_count": aggregate_count,
            }
        ).encode()
    ).hexdigest()[:48]
    return f"workflow-{identity}"


def _search_projection(response, *, include_attribution: bool) -> dict[str, object]:
    return {
        "query": response.query,
        "mode": response.mode.value,
        "results": [
            {
                "url": result.url,
                "title": result.title,
                "snippet": result.snippet,
                "domain": result.domain,
                "provider": result.provider.value if result.provider else None,
                "score": result.score,
                "egress": (result.metadata.get("egress") if result.metadata else None),
                "machine": (
                    result.metadata.get("machine") if result.metadata else None
                ),
                "score_attribution": (
                    dict(result.score_attribution) if include_attribution else {}
                ),
            }
            for result in response.results
        ],
        "traces": [
            {
                "provider": trace.provider.value,
                "status": trace.status,
                "results_count": trace.results_count,
                "latency_ms": trace.latency_ms,
                "error": trace.error,
                "budget_remaining": trace.budget_remaining,
            }
            for trace in response.traces
        ],
        "total_results": response.total_results,
        "cached": response.cached,
        "budget_warnings": list(response.budget_warnings),
        "search_run_id": response.search_run_id,
        "session_id": None,
    }


def _search_outcome(response) -> CanonicalOutcome:
    failed = [
        trace
        for trace in response.traces
        if trace.status not in {"success", "cache", "empty"}
    ]
    if response.results:
        return CanonicalOutcome.DEGRADED if failed else CanonicalOutcome.SUCCESS
    if failed:
        return CanonicalOutcome.PROVIDERS_FAILED
    return CanonicalOutcome.EMPTY


def _extract_projection(result) -> dict[str, object]:
    completeness = result.completeness_result
    rejection = getattr(result, "rejection", None)
    if rejection is None:
        from argus.extraction.rejection import classify_extraction_rejection

        rejection = classify_extraction_rejection(result)
    rejection_projection = None
    if rejection is not None:
        rejection_projection = rejection.to_dict()
        rejection_projection["code"] = rejection.code.value
        rejection_projection["recommended_action"] = rejection.recommended_action.value
    return {
        "extraction_run_id": result.extraction_run_id,
        "url": result.url,
        "title": result.title,
        "text": result.text,
        "author": result.author,
        "date": result.date,
        "word_count": result.word_count,
        "extractor": result.extractor.value if result.extractor else None,
        "error": result.error,
        "quality_passed": getattr(result, "quality_passed", None),
        "quality_reason": getattr(result, "quality_reason", None),
        "extractors_tried": list(getattr(result, "extractors_tried", []) or []),
        "is_complete": completeness.is_complete if completeness else None,
        "completeness_confidence": completeness.confidence if completeness else None,
        "truncation_type": completeness.truncation_type if completeness else None,
        "completeness_signals": list(completeness.signals) if completeness else None,
        "recommended_action": (
            completeness.recommended_action if completeness else None
        ),
        "rejection": rejection_projection,
        "source_type": getattr(result, "source_type", None),
        "egress": getattr(result, "egress", None),
        "machine": getattr(result, "machine", None),
        "auth_used": getattr(result, "auth_used", False),
        "cookies_used": getattr(result, "cookies_used", False),
        "archive_used": getattr(result, "archive_used", False),
        "cost": getattr(result, "cost", 0.0),
    }


class AcceptedOperationService:
    """Execute an operation once, durably accept once, then freeze its facts."""

    def __init__(
        self,
        *,
        broker_provider: Callable[[], object],
        repository_provider: Callable[[], object],
        extractor: Callable[..., Awaitable[object]] | None = None,
        archive_lookup: Callable[..., Awaitable[object]] | None = None,
        session_authority=None,
        registration: AcceptedOperationRegistration | None = None,
        site_fetcher=None,
    ):
        self._broker_provider = broker_provider
        self._repository_provider = repository_provider
        self._extractor = extractor
        self._archive_lookup = archive_lookup
        self._session_authority = session_authority
        self._registration = registration or AcceptedOperationRegistration()
        self._site_fetcher = site_fetcher or fetch_site_text
        self._evidence_repository = None

    def validate_registration(self, authority: str) -> None:
        """Validate the concrete service's atomic authority registration."""
        self._registration.validate(authority)

    def validate_runtime_authority(self) -> None:
        """Bind every concrete dependency before advertising evidence support."""
        self.validate_registration("evidence")
        broker = self._broker_provider()
        if not callable(getattr(broker, "search_accepted", None)):
            raise AcceptedAuthorityConfigurationError(
                "evidence authority broker lacks accepted search execution"
            )
        if self._session_authority is None:
            raise AcceptedAuthorityConfigurationError(
                "evidence authority requires ARGUS_RETRIEVAL_SESSION_SECRET"
            )
        if self._extractor is not None and self._extractor is not extract_url:
            raise AcceptedAuthorityConfigurationError(
                "evidence authority requires the canonical extraction finalizer"
            )
        evidence_repository = self._get_evidence_repository()
        evidence_repository.accepted_count()

    @property
    def registration(self) -> AcceptedOperationRegistration:
        return self._registration

    def capability_registrations(self) -> frozenset[str]:
        return self._registration.capability_registrations()

    def _get_evidence_repository(self):
        if self._evidence_repository is None:
            from argus.persistence.evidence import SqlAlchemyEvidenceRepository

            repository = self._repository_provider()
            session_factory = getattr(repository, "session_factory", None)
            if session_factory is None:
                raise AcceptedAuthorityConfigurationError(
                    "evidence authority repository has no transactional session"
                )
            self._evidence_repository = SqlAlchemyEvidenceRepository(session_factory)
        return self._evidence_repository

    def _accepted_search_operation(
        self,
        execution,
        *,
        request_id: str,
        include_attribution: bool,
        session_id: str | None,
    ) -> AcceptedOperation:
        outcome = execution.outcome
        if execution.response is None or execution.receipt is None:
            return AcceptedOperation(
                outcome=outcome,
                request_id=request_id,
                result=None,
                error=_operation_error(
                    outcome,
                    request_id=request_id,
                    detail="Search could not be durably accepted",
                ),
            )
        result = _search_projection(
            execution.response,
            include_attribution=include_attribution,
        )
        result["session_id"] = session_id
        result["acceptance_receipt"] = {
            "receipt_ref": execution.receipt.receipt_ref,
            "accepted_at": execution.receipt.accepted_at.isoformat(),
            "acceptance_fingerprint": (execution.receipt.acceptance_fingerprint),
        }
        if getattr(execution, "session_update_failed", False):
            result["session_update"] = {
                "status": "failed",
                "reason": "session_update_failed",
            }
        return AcceptedOperation(
            outcome=outcome,
            request_id=request_id,
            result=result,
            error=(
                None
                if outcome
                in {
                    CanonicalOutcome.SUCCESS,
                    CanonicalOutcome.DEGRADED,
                    CanonicalOutcome.EMPTY,
                }
                else _operation_error(
                    outcome,
                    request_id=request_id,
                    detail="Search providers did not produce accepted evidence",
                )
            ),
        )

    async def search(
        self,
        request,
        *,
        principal: str,
        request_id: str,
        require_owned_session: bool = False,
    ) -> AcceptedOperation:
        query = SearchQuery(
            query=request.query,
            mode=SearchMode(request.mode),
            max_results=request.max_results,
            providers=(
                [ProviderName(provider) for provider in request.providers]
                if request.providers
                else None
            ),
            free_only=request.free_only,
            caller=principal,
            metadata={"caller_label": request.caller},
        )
        session_id = request.session_id
        if require_owned_session:
            authority = self._session_authority
            if authority is None:
                outcome = CanonicalOutcome.UNREADY
                return AcceptedOperation(
                    outcome=outcome,
                    request_id=request_id,
                    result=None,
                    error=_operation_error(
                        outcome,
                        request_id=request_id,
                        detail="Retrieval session authority is unavailable",
                    ),
                )
            if session_id and not authority.owns(session_id, principal):
                outcome = CanonicalOutcome.UNREADY
                return AcceptedOperation(
                    outcome=outcome,
                    request_id=request_id,
                    result=None,
                    error=_operation_error(
                        outcome,
                        request_id=request_id,
                        detail="Retrieval session was not found",
                        code="session_not_found",
                        status=404,
                        operation_began=False,
                    ),
                    operation_began=False,
                )
            if not session_id:
                session_id = authority.issue(principal)
        broker = self._broker_provider()
        if (
            require_owned_session
            and request.session_id
            and not broker.session_exists(session_id)
        ):
            outcome = CanonicalOutcome.UNREADY
            return AcceptedOperation(
                outcome=outcome,
                request_id=request_id,
                result=None,
                error=_operation_error(
                    outcome,
                    request_id=request_id,
                    detail="Retrieval session was not found",
                    code="session_not_found",
                    status=404,
                    operation_began=False,
                ),
                operation_began=False,
            )
        if self._registration == AcceptedOperationRegistration.complete():
            evidence_repository = self._get_evidence_repository()
            if session_id:
                execution, session_id = await broker.search_with_session_accepted(
                    query,
                    evidence_repository=evidence_repository,
                    session_id=session_id,
                    compute_attribution=request.include_attribution,
                )
            else:
                execution = await broker.search_accepted(
                    query,
                    evidence_repository=evidence_repository,
                    compute_attribution=request.include_attribution,
                )
                session_id = None
            return self._accepted_search_operation(
                execution,
                request_id=request_id,
                include_attribution=request.include_attribution,
                session_id=session_id,
            )
        if session_id:
            response, session_id = await broker.search_with_session(
                query,
                session_id=session_id,
                compute_attribution=request.include_attribution,
                persist_legacy=False,
            )
        else:
            response = await broker.search(
                query,
                compute_attribution=request.include_attribution,
                persist_legacy=False,
            )
            session_id = None
        return self._accept_search(
            query,
            response,
            request_id=request_id,
            include_attribution=request.include_attribution,
            session_id=session_id,
        )

    async def acquire_site(
        self,
        request,
        *,
        principal: str,
        request_id: str,
    ) -> AcceptedOperation:
        """Acquire, rank, and durably accept sitemap/internal-link site results."""
        search_request = SimpleNamespace(
            query=request.url,
            mode="discovery",
            max_results=request.hard_page_limit,
            providers=None,
            free_only=False,
            caller=request.caller,
            session_id=None,
            include_attribution=False,
        )
        base = await self.search(
            search_request,
            principal=principal,
            request_id=f"{request_id}-search",
        )
        if base.result is None:
            return AcceptedOperation(
                outcome=base.outcome,
                request_id=request_id,
                result=None,
                error=base.error,
            )
        base_receipt = base.result.get("acceptance_receipt")
        base_receipt_ref = (
            base_receipt.get("receipt_ref")
            if isinstance(base_receipt, Mapping)
            else None
        )
        if not isinstance(base_receipt_ref, str):
            return AcceptedOperation(
                outcome=CanonicalOutcome.PERSISTENCE_FAILED,
                request_id=request_id,
                result=None,
                error=_operation_error(
                    CanonicalOutcome.PERSISTENCE_FAILED,
                    request_id=request_id,
                    detail="Site acquisition search was not durably accepted",
                ),
            )

        from urllib.parse import urlparse

        root_hostname = _normalized_hostname(request.url)
        discovered = await discover_site_urls(
            request.url,
            fetcher=self._site_fetcher,
            hard_limit=request.hard_page_limit,
        )
        candidates: dict[str, dict[str, object]] = {}
        for item in base.result.get("results", ()):
            url = str(item["url"])
            if (
                root_hostname is not None
                and _same_site(url, root_hostname)
                and looks_like_html(url)
            ):
                candidates[url.rstrip("/")] = dict(item)
        for url in discovered:
            if root_hostname is not None and _same_site(url, root_hostname):
                candidates.setdefault(
                    url.rstrip("/"),
                    {"url": url, "title": url},
                )
        ranked = tuple(
            sorted(
                candidates.values(),
                key=lambda item: (
                    -site_url_score(str(item["url"]), request.url),
                    len(urlparse(str(item["url"])).path),
                    str(item["url"]),
                ),
            )[: min(request.soft_page_limit, request.hard_page_limit)]
        )
        operation_identity = hashlib.sha256(
            _canonical_json(
                {
                    "base_receipt_ref": base_receipt_ref,
                    "root_url": request.url,
                    "results": ranked,
                }
            ).encode()
        ).hexdigest()[:48]
        operation_id = f"site-acquisition-{operation_identity}"
        plan_id = f"site-plan-{operation_identity}"
        cache_fingerprint = f"site-cache-{operation_identity}"
        execution_cohort = f"site-cohort-{operation_identity}"
        contributor_refs = (f"site-attempt-{operation_identity}",)
        from argus.broker.accepted import (
            AcceptanceReceipt,
            AcceptedRetrieval,
            CacheOutcome,
            acceptance_fingerprint,
        )
        from argus.persistence.evidence import RetrievalEvidence

        fingerprint = acceptance_fingerprint(
            operation_id=operation_id,
            plan_id=plan_id,
            cache_fingerprint=cache_fingerprint,
            execution_cohort_id=execution_cohort,
            outcome=CacheOutcome.SUCCESS,
            reason="site_acquisition_accepted",
            results=ranked,
            contributor_attempt_refs=contributor_refs,
            origin_spend_usd="0",
        )
        receipt = AcceptanceReceipt(
            receipt_ref=f"receipt:{operation_id}",
            accepted_at=datetime.now(timezone.utc),
            acceptance_fingerprint=fingerprint,
        )
        accepted = AcceptedRetrieval(
            operation_id=operation_id,
            plan_id=plan_id,
            cache_fingerprint=cache_fingerprint,
            execution_cohort=execution_cohort,
            outcome=CacheOutcome.SUCCESS,
            reason="site_acquisition_accepted",
            query=request.url,
            mode="discovery",
            results=ranked,
            contributor_attempt_refs=contributor_refs,
            origin_spend_usd="0",
            acceptance_receipt=receipt,
        )
        try:
            accepted_receipt = self._get_evidence_repository().accept(
                RetrievalEvidence(
                    accepted=accepted,
                    origin_receipt_ref=base_receipt_ref,
                    cache_published=False,
                )
            )
        except Exception:
            return AcceptedOperation(
                outcome=CanonicalOutcome.PERSISTENCE_FAILED,
                request_id=request_id,
                result=None,
                error=_operation_error(
                    CanonicalOutcome.PERSISTENCE_FAILED,
                    request_id=request_id,
                    detail="Site acquisition could not be durably accepted",
                ),
            )
        return AcceptedOperation(
            outcome=CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={
                "results": ranked,
                "acceptance_receipt": {
                    "receipt_ref": accepted_receipt.receipt_ref,
                    "accepted_at": accepted_receipt.accepted_at.isoformat(),
                    "acceptance_fingerprint": (accepted_receipt.acceptance_fingerprint),
                },
            },
            error=None,
        )

    def _accept_search(
        self,
        query: SearchQuery,
        response,
        *,
        request_id: str,
        include_attribution: bool,
        session_id: str | None = None,
    ) -> AcceptedOperation:
        outcome = _search_outcome(response)
        result = _search_projection(
            response,
            include_attribution=include_attribution,
        )
        result["session_id"] = session_id
        try:
            receipt = self._repository_provider().accept(query, response)
        except Exception:
            failed = CanonicalOutcome.PERSISTENCE_FAILED
            return AcceptedOperation(
                outcome=failed,
                request_id=request_id,
                result=None,
                error=_operation_error(
                    failed,
                    request_id=request_id,
                    detail="Search could not be durably accepted",
                ),
            )
        receipt_run_id = getattr(receipt, "run_id", None)
        delivery_intent_id = getattr(receipt, "delivery_intent_id", None)
        result["acceptance_receipt"] = {
            "run_id": (
                receipt_run_id
                if isinstance(receipt_run_id, str)
                else response.search_run_id
            ),
            "delivery_intent_id": (
                delivery_intent_id if isinstance(delivery_intent_id, str) else None
            ),
        }
        return AcceptedOperation(
            outcome=outcome,
            request_id=request_id,
            result=result,
            error=(
                None
                if outcome
                in {
                    CanonicalOutcome.SUCCESS,
                    CanonicalOutcome.DEGRADED,
                    CanonicalOutcome.EMPTY,
                }
                else _operation_error(
                    outcome,
                    request_id=request_id,
                    detail="Search providers did not produce accepted evidence",
                )
            ),
        )

    async def recover(
        self,
        request,
        *,
        principal: str,
        request_id: str,
    ) -> AcceptedOperation:
        query_parts = [request.url]
        if request.title:
            query_parts.append(request.title)
        if request.domain:
            query_parts.append(request.domain)
        query = SearchQuery(
            query=" ".join(query_parts),
            mode=SearchMode.RECOVERY,
            max_results=10,
            caller=principal,
        )
        if self._registration == AcceptedOperationRegistration.complete():

            async def archive_fallback():
                archive_lookup = self._archive_lookup or try_archive_ph
                try:
                    archived = await archive_lookup(request.url)
                except Exception:
                    return None
                if not archived:
                    return None
                return SearchResult(
                    url=archived["url"],
                    title=archived["title"],
                    snippet=archived["snippet"],
                    domain=archived["domain"],
                    score=archived["score"],
                    metadata={"source_type": "archive_ph"},
                )

            execution = await self._broker_provider().search_accepted(
                query,
                evidence_repository=self._get_evidence_repository(),
                empty_fallback=archive_fallback,
            )
            return self._accepted_search_operation(
                execution,
                request_id=request_id,
                include_attribution=False,
                session_id=None,
            )
        response = await self._broker_provider().search(query, persist_legacy=False)
        if not response.results:
            try:
                archive_lookup = self._archive_lookup
                if archive_lookup is None:
                    archive_lookup = try_archive_ph
                archived = await archive_lookup(request.url)
            except Exception:
                archived = None
            if archived:
                response.results.append(
                    SearchResult(
                        url=archived["url"],
                        title=archived["title"],
                        snippet=archived["snippet"],
                        domain=archived["domain"],
                        score=archived["score"],
                        metadata={"source_type": "archive_ph"},
                    )
                )
                response.total_results = len(response.results)
        return self._accept_search(
            query,
            response,
            request_id=request_id,
            include_attribution=False,
        )

    async def expand(
        self,
        request,
        *,
        principal: str,
        request_id: str,
    ) -> AcceptedOperation:
        query_text = request.query
        if request.context:
            query_text = f"{request.query} {request.context}"
        query = SearchQuery(
            query=query_text,
            mode=SearchMode.DISCOVERY,
            max_results=15,
            caller=principal,
        )
        if self._registration == AcceptedOperationRegistration.complete():
            execution = await self._broker_provider().search_accepted(
                query,
                evidence_repository=self._get_evidence_repository(),
            )
            return self._accepted_search_operation(
                execution,
                request_id=request_id,
                include_attribution=False,
                session_id=None,
            )
        response = await self._broker_provider().search(query, persist_legacy=False)
        return self._accept_search(
            query,
            response,
            request_id=request_id,
            include_attribution=False,
        )

    async def extract(
        self,
        request,
        *,
        principal: str,
        request_id: str,
    ) -> AcceptedOperation:
        extractor = self._extractor
        if extractor is None:
            extractor = extract_url
        try:
            from argus.api.main import _HTTP_API_AUTHORITY_CAPABILITY

            kwargs = {
                "domain": request.domain,
                "mode": request.mode,
                "caller": principal,
                "repository": self._repository_provider(),
                "authority_capability": _HTTP_API_AUTHORITY_CAPABILITY,
            }
            if (
                self._registration == AcceptedOperationRegistration.complete()
                and extractor is extract_url
            ):
                kwargs.update(
                    use_evidence_authority=True,
                    request_id=request_id,
                )
            result = await extractor(request.url, **kwargs)
        except Exception:
            outcome = CanonicalOutcome.PERSISTENCE_FAILED
            return AcceptedOperation(
                outcome=outcome,
                request_id=request_id,
                result=None,
                error=_operation_error(
                    outcome,
                    request_id=request_id,
                    detail="Extraction could not be durably recorded",
                ),
            )
        projection = _extract_projection(result)
        disposition = getattr(result, "artifact_disposition", None)
        disposition_value = getattr(disposition, "value", disposition)
        accepted_outcome = getattr(result, "accepted_outcome", None)
        if isinstance(accepted_outcome, CanonicalOutcome):
            outcome = accepted_outcome
        elif result.error:
            outcome = CanonicalOutcome.EXTRACTION_FAILED
        elif disposition_value == "partial":
            outcome = CanonicalOutcome.DEGRADED
        else:
            outcome = CanonicalOutcome.SUCCESS
        return AcceptedOperation(
            outcome=outcome,
            request_id=request_id,
            result=projection,
            error=(
                None
                if outcome in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}
                else _operation_error(
                    outcome,
                    request_id=request_id,
                    detail="Extraction did not produce an accepted artifact",
                )
            ),
        )

    async def compose_workflow(
        self,
        retrieval: AcceptedOperation,
        *,
        max_results: int,
        principal: str,
        request_id: str,
        selection_urls: tuple[str, ...] | None = None,
        required_urls: tuple[str, ...] | None = None,
        allow_partial: bool = True,
        minimum_artifacts: int | None = None,
    ) -> AcceptedOperation:
        """Claim and compose one immutable receipt-bound workflow requirement."""
        receipt = (retrieval.result or {}).get("acceptance_receipt")
        receipt_ref = (
            receipt.get("receipt_ref") if isinstance(receipt, Mapping) else None
        )
        repository = self._repository_provider()
        claim_requirement_ref = None
        results = (retrieval.result or {}).get("results")
        if isinstance(receipt_ref, str) and isinstance(results, tuple):
            if selection_urls is None:
                selected = tuple(results[:max_results])
            else:
                by_url = {item.get("url"): item for item in results}
                selected = tuple(
                    by_url[url] for url in selection_urls[:max_results] if url in by_url
                )
            from argus.contracts.result_refs import accepted_result_refs

            refs = accepted_result_refs(selected)
            selected_urls = tuple(str(item["url"]) for item in selected)
            required = (
                frozenset(selected_urls[:1])
                if required_urls is None
                else frozenset(required_urls)
            )
            aggregate_count = (
                minimum_artifacts
                if type(minimum_artifacts) is int
                else (1 if refs else 0)
            )
            claim_requirement_ref = _workflow_requirement_ref(
                receipt_ref=receipt_ref,
                refs=refs,
                required=required,
                minimum_disposition=(
                    ArtifactDisposition.PARTIAL
                    if allow_partial
                    else ArtifactDisposition.USABLE
                ),
                aggregate_count=aggregate_count,
            )
        claim = getattr(repository, "workflow_composition_claim", None)
        if claim_requirement_ref is None or claim is None:
            return await self._compose_workflow_unlocked(
                retrieval,
                max_results=max_results,
                principal=principal,
                request_id=request_id,
                selection_urls=selection_urls,
                required_urls=required_urls,
                allow_partial=allow_partial,
                minimum_artifacts=minimum_artifacts,
            )
        async with claim(receipt_ref, claim_requirement_ref):
            return await self._compose_workflow_unlocked(
                retrieval,
                max_results=max_results,
                principal=principal,
                request_id=request_id,
                selection_urls=selection_urls,
                required_urls=required_urls,
                allow_partial=allow_partial,
                minimum_artifacts=minimum_artifacts,
            )

    async def _compose_workflow_unlocked(
        self,
        retrieval: AcceptedOperation,
        *,
        max_results: int,
        principal: str,
        request_id: str,
        selection_urls: tuple[str, ...] | None = None,
        required_urls: tuple[str, ...] | None = None,
        allow_partial: bool = True,
        minimum_artifacts: int | None = None,
    ) -> AcceptedOperation:
        """Compose workflow extraction evidence behind the accepted authority."""
        from argus.extraction.composition import InvalidArtifactRequirement
        from argus.persistence.search_ledger import AcceptanceConflictError
        from sqlalchemy.exc import SQLAlchemyError

        def failed(outcome: CanonicalOutcome, detail: str, result=None):
            return AcceptedOperation(
                outcome=outcome,
                request_id=request_id,
                result=result,
                error=_operation_error(
                    outcome,
                    request_id=request_id,
                    detail=detail,
                ),
            )

        if self._registration != AcceptedOperationRegistration.complete():
            return failed(
                CanonicalOutcome.UNREADY,
                "Workflow evidence authority is not active",
            )
        if retrieval.result is None:
            return failed(
                retrieval.outcome,
                "Workflow retrieval has no accepted evidence",
            )
        results = retrieval.result.get("results")
        receipt = retrieval.result.get("acceptance_receipt")
        receipt_ref = (
            receipt.get("receipt_ref") if isinstance(receipt, Mapping) else None
        )
        if (
            not isinstance(results, tuple)
            or not isinstance(receipt_ref, str)
            or not 0 <= max_results <= 200
            or type(allow_partial) is not bool
        ):
            return failed(
                CanonicalOutcome.UNREADY,
                "Workflow retrieval evidence is unavailable",
            )
        repository = self._repository_provider()
        try:
            durable_results = repository.load_accepted_retrieval_results(receipt_ref)
        except SQLAlchemyError:
            return failed(
                CanonicalOutcome.PERSISTENCE_FAILED,
                "Workflow retrieval evidence could not be loaded",
            )
        if durable_results is None:
            return failed(
                CanonicalOutcome.UNREADY,
                "Workflow retrieval evidence is unavailable",
            )
        if _canonical_json(results) != _canonical_json(durable_results):
            return failed(
                CanonicalOutcome.UNREADY,
                "Workflow retrieval projection is not receipt-bound",
            )

        if selection_urls is None:
            selected = tuple(durable_results[:max_results])
        else:
            if (
                not isinstance(selection_urls, tuple)
                or len(selection_urls) > 200
                or len(set(selection_urls)) != len(selection_urls)
                or any(not isinstance(url, str) or not url for url in selection_urls)
            ):
                return failed(
                    CanonicalOutcome.INVALID_REQUEST,
                    "Workflow result selection is invalid",
                )
            by_url = {item.get("url"): item for item in durable_results}
            if any(url not in by_url for url in selection_urls):
                return failed(
                    CanonicalOutcome.UNREADY,
                    "Requested workflow URL is absent from accepted retrieval evidence",
                )
            selected = tuple(by_url[url] for url in selection_urls[:max_results])
        from argus.contracts.result_refs import accepted_result_refs

        refs = accepted_result_refs(selected)

        selected_urls = tuple(str(item["url"]) for item in selected)
        if required_urls is None:
            required = frozenset(selected_urls[:1])
        elif (
            not isinstance(required_urls, tuple)
            or len(set(required_urls)) != len(required_urls)
            or any(url not in selected_urls for url in required_urls)
        ):
            return failed(
                CanonicalOutcome.INVALID_REQUEST,
                "Workflow required-result selection is invalid",
            )
        else:
            required = frozenset(required_urls)
        aggregate_count = 1 if refs else 0
        if minimum_artifacts is not None:
            if type(minimum_artifacts) is not int or not 0 <= minimum_artifacts <= len(
                refs
            ):
                return failed(
                    CanonicalOutcome.INVALID_REQUEST,
                    "Workflow aggregate artifact floor is invalid",
                )
            aggregate_count = minimum_artifacts
        minimum_disposition = (
            ArtifactDisposition.PARTIAL if allow_partial else ArtifactDisposition.USABLE
        )
        requirement_ref = _workflow_requirement_ref(
            receipt_ref=receipt_ref,
            refs=refs,
            required=required,
            minimum_disposition=minimum_disposition,
            aggregate_count=aggregate_count,
        )
        requirement_identity = requirement_ref.removeprefix("workflow-")
        view = _WorkflowRetrievalView(
            retrieval.outcome,
            refs,
            receipt_ref,
            tuple(selected),
        )
        requirement = ArtifactRequirement(
            requirement_ref=requirement_ref,
            selections=tuple(
                ArtifactSelection(
                    ref,
                    selected_urls[ordinal] in required,
                    minimum_disposition,
                )
                for ordinal, ref in enumerate(refs)
            ),
            aggregate_floor=AggregateArtifactFloor(
                count=aggregate_count,
                minimum_disposition=minimum_disposition,
            ),
            max_extractions=len(refs),
            deadline_ms=30_000,
            spend_policy_ref="workflow-extraction-v1",
        )

        try:
            resumed = repository.load_accepted_workflow_composition(
                receipt_ref,
                requirement.requirement_ref,
            )
        except SQLAlchemyError:
            return failed(
                CanonicalOutcome.PERSISTENCE_FAILED,
                "Workflow composition could not be loaded",
            )
        if resumed is not None:
            source = resumed["source_state"]
            composition_state = source["composition"]
            projected_artifacts = []
            selected_by_ref = dict(zip(refs, selected, strict=True))
            for link_state in composition_state["links"]:
                artifact_ref = link_state.get("artifact_ref")
                disposition = link_state.get("artifact_disposition")
                if artifact_ref is None or disposition not in {
                    ArtifactDisposition.USABLE.value,
                    ArtifactDisposition.PARTIAL.value,
                }:
                    continue
                acceptance = link_state.get("acceptance_receipt")
                extraction_receipt = (
                    acceptance.get("receipt_ref")
                    if isinstance(acceptance, dict)
                    else acceptance
                )
                typed = (
                    repository.load_extraction_outcome_by_receipt(extraction_receipt)
                    if isinstance(extraction_receipt, str)
                    else None
                )
                if typed is None or typed.artifact is None:
                    return failed(
                        CanonicalOutcome.PERSISTENCE_FAILED,
                        "Accepted workflow artifact could not be reloaded",
                    )
                item = selected_by_ref[link_state["result_cluster_ref"]]
                projected_artifacts.append(
                    {
                        "result_cluster_ref": link_state["result_cluster_ref"],
                        "artifact_ref": typed.artifact.artifact_ref,
                        "content_identity": typed.artifact.content_identity,
                        "url": item["url"],
                        "title": typed.artifact.title,
                        "text": typed.artifact.text,
                        "word_count": typed.artifact.word_count,
                        "disposition": typed.artifact_disposition.value,
                        "extractor": typed.selected_extractor,
                    }
                )
            receipt_state = {
                key: resumed[key] for key in ("receipt_ref", "accepted_at", "scope")
            }
            resumed_projection = {
                "requirement_ref": requirement.requirement_ref,
                "artifact_requirement": source["artifact_requirement"],
                "links": composition_state["links"],
                "accepted_artifact_refs": composition_state["accepted_artifact_refs"],
                "degraded_artifact_refs": composition_state["degraded_artifact_refs"],
                "rejected_extraction_refs": composition_state[
                    "rejected_extraction_refs"
                ],
                "composition_trace": composition_state["composition_trace"],
                "composition_receipt": receipt_state,
                "composition_receipt_ref": resumed["receipt_ref"],
                "retrieval_outcome": composition_state["retrieval_outcome"],
                "artifact_outcome": composition_state["artifact_outcome"],
                "composite_outcome": composition_state["composite_outcome"],
                "composition_outcome": composition_state["composite_outcome"],
                "artifacts": projected_artifacts,
            }
            outcome = CanonicalOutcome(composition_state["composite_outcome"])
            return AcceptedOperation(
                outcome=outcome,
                request_id=request_id,
                result=resumed_projection,
                error=(
                    None
                    if outcome in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}
                    else _operation_error(
                        outcome,
                        request_id=request_id,
                        detail="Workflow artifact floor was not met",
                    )
                ),
            )

        links = []
        projected_artifacts = []
        reuse_by_identity: dict[tuple[str, str], int] = {}
        for ordinal, (ref, item) in enumerate(zip(refs, selected, strict=True)):
            extraction = await self.extract(
                SimpleNamespace(url=item["url"], domain=None, mode="default"),
                principal=principal,
                request_id=f"{request_id}-{ordinal}",
            )
            typed = None
            if extraction.result is not None:
                run_id = extraction.result.get("extraction_run_id")
                if isinstance(run_id, str):
                    try:
                        typed = repository.load_accepted_extraction_outcome(run_id)
                    except SQLAlchemyError:
                        return failed(
                            CanonicalOutcome.PERSISTENCE_FAILED,
                            "Accepted extraction evidence could not be loaded",
                        )
            if typed is None:
                return failed(
                    CanonicalOutcome.PERSISTENCE_FAILED,
                    "Accepted extraction evidence is missing",
                )
            selected_url_identity = (
                "sha256:" + hashlib.sha256(str(item["url"]).encode()).hexdigest()
            )
            if typed.normalized_url_identity != selected_url_identity:
                return failed(
                    CanonicalOutcome.PERSISTENCE_FAILED,
                    "Accepted extraction evidence does not match the selected URL",
                )
            try:
                link = ResultExtractionLink.from_accepted(
                    link_ref=f"link-{requirement_identity}-{ordinal}",
                    result_cluster_ref=ref,
                    accepted_outcome=typed,
                    required=selected_urls[ordinal] in required,
                )
            except InvalidArtifactRequirement:
                return failed(
                    CanonicalOutcome.INVALID_REQUEST,
                    "Accepted extraction evidence is invalid",
                )
            if link.artifact_ref and link.artifact_identity:
                identity = (link.artifact_ref, link.artifact_identity)
                previous = reuse_by_identity.get(identity)
                if previous is not None:
                    links[previous] = ResultExtractionLink.from_accepted(
                        link_ref=links[previous].link_ref,
                        result_cluster_ref=links[previous].result_cluster_ref,
                        accepted_outcome=links[previous].accepted_outcome,
                        required=links[previous].required,
                        reuse_origin=links[previous].result_cluster_ref,
                    )
                    link = ResultExtractionLink.from_accepted(
                        link_ref=link.link_ref,
                        result_cluster_ref=link.result_cluster_ref,
                        accepted_outcome=typed,
                        required=link.required,
                        reuse_origin=links[previous].result_cluster_ref,
                    )
                else:
                    reuse_by_identity[identity] = len(links)
            links.append(link)
            artifact = typed.artifact
            if artifact is not None and typed.artifact_disposition in {
                ArtifactDisposition.USABLE,
                ArtifactDisposition.PARTIAL,
            }:
                projected_artifacts.append(
                    {
                        "result_cluster_ref": ref,
                        "artifact_ref": artifact.artifact_ref,
                        "content_identity": artifact.content_identity,
                        "url": item["url"],
                        "title": artifact.title,
                        "text": artifact.text,
                        "word_count": artifact.word_count,
                        "disposition": typed.artifact_disposition.value,
                        "extractor": typed.selected_extractor,
                    }
                )
        try:
            composition = compose_retrieval_evidence(view, tuple(links), requirement)
        except InvalidArtifactRequirement:
            return failed(
                CanonicalOutcome.INVALID_REQUEST,
                "Workflow artifact requirement is invalid",
            )
        if composition.composite_outcome is CanonicalOutcome.PERSISTENCE_FAILED:
            return failed(
                CanonicalOutcome.PERSISTENCE_FAILED,
                "Workflow composition could not be durably accepted",
            )
        try:
            accepted = repository.accept_workflow_retrieval_composition(
                view, composition, requirement
            )
        except InvalidArtifactRequirement:
            return failed(
                CanonicalOutcome.UNREADY,
                "Workflow retrieval binding proof is unavailable",
            )
        except (AcceptanceConflictError, SQLAlchemyError, ValueError):
            return failed(
                CanonicalOutcome.PERSISTENCE_FAILED,
                "Workflow composition could not be durably accepted",
            )
        projection = _workflow_projection(
            requirement,
            composition,
            accepted,
            projected_artifacts,
        )
        if composition.composite_outcome not in {
            CanonicalOutcome.SUCCESS,
            CanonicalOutcome.DEGRADED,
        }:
            return failed(
                composition.composite_outcome,
                "Workflow artifact floor was not met",
                projection,
            )
        return AcceptedOperation(
            outcome=composition.composite_outcome,
            request_id=request_id,
            result=projection,
            error=None,
        )


def create_development_accepted_operation_service(
    broker, repository=None
) -> AcceptedOperationService:
    """Build the explicit SQLite-backed authority used only by standalone tools."""
    if repository is None:
        from argus.persistence.search_ledger import create_search_ledger_repository

        repository = create_search_ledger_repository()
    return AcceptedOperationService(
        broker_provider=lambda: broker,
        repository_provider=lambda: repository,
        registration=AcceptedOperationRegistration.complete(),
    )
