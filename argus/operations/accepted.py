"""Single execution-and-acceptance authority for transport presenters."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Awaitable, Callable

from argus.contracts import AcceptedOperation, CanonicalOutcome, OperationError
from argus.extraction import extract_url
from argus.models import ProviderName, SearchMode, SearchQuery, SearchResult
from argus.recovery.archive_ph import try_archive_ph


class AcceptedAuthorityConfigurationError(RuntimeError):
    """The atomic evidence authority was requested without every dependency."""


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
        missing = [field.name for field in fields(self) if not getattr(self, field.name)]
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
        status=status or {
            CanonicalOutcome.PERSISTENCE_FAILED: 503,
            CanonicalOutcome.PROVIDERS_FAILED: 502,
            CanonicalOutcome.EXTRACTION_FAILED: 502,
            CanonicalOutcome.TIMEOUT: 504,
            CanonicalOutcome.UNREADY: 503,
        }.get(outcome, 503),
        detail=detail,
        instance=f"urn:argus:request:{request_id}",
        code=code,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
        operation_began=operation_began,
    )


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
                "egress": (
                    result.metadata.get("egress") if result.metadata else None
                ),
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
        rejection_projection["recommended_action"] = (
            rejection.recommended_action.value
        )
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
    ):
        self._broker_provider = broker_provider
        self._repository_provider = repository_provider
        self._extractor = extractor
        self._archive_lookup = archive_lookup
        self._session_authority = session_authority
        self._registration = registration or AcceptedOperationRegistration()
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
        if not callable(self._extractor or extract_url):
            raise AcceptedAuthorityConfigurationError(
                "evidence authority extraction finalizer is unavailable"
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
            "acceptance_fingerprint": (
                execution.receipt.acceptance_fingerprint
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
                delivery_intent_id
                if isinstance(delivery_intent_id, str)
                else None
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
        if result.error:
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
