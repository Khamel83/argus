"""Pure planning primitives for receipt-bound targeted research.

The planner has no provider, persistence, or network dependencies.  It consumes
the immutable projections returned by accepted search operations and emits a
deterministic selection plan.  URL validation deliberately reuses the strict
public-HTTPS helpers from :mod:`research_targets`; accepted URL text is retained
verbatim while a canonical identity is used only for matching and dedupe.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from tld import get_fld

from argus.contracts import CanonicalOutcome
from argus.workflows.research_targets import normalize_source_prefix, prefix_matches

MAX_REQUIREMENT_SEARCH_RESULTS = 8
MAX_REQUIREMENTS = 16
MAX_PREFIX_CANDIDATES = 2
MAX_EXTERNAL_CANDIDATES = 4
MAX_EXTERNAL_PER_DOMAIN = 2
TARGET_REQUIREMENT_SEARCH_TIMEOUT_SECONDS = 30
TARGET_CANDIDATE_COMPOSITION_TIMEOUT_SECONDS = 45
TARGET_EXTERNAL_REMAINDER_TIMEOUT_SECONDS = 120
TARGET_WORKFLOW_DEADLINE_SECONDS = 540
TARGET_GLOBAL_CONCURRENCY = 5


class TargetWorkflowFailure(RuntimeError):
    """Stable failure raised by targeted planning or execution seams."""

    def __init__(
        self,
        code: str,
        *,
        requirement_ref: str | None = None,
        detail: str | None = None,
        private: bool = False,
    ) -> None:
        self.code = code
        self.requirement_ref = requirement_ref
        self.detail = detail or code
        self.private = private
        super().__init__(code)


class TargetCandidateFailure(TargetWorkflowFailure):
    """Private marker for one rejected extraction candidate."""

    def __init__(self, *, requirement_ref: str, code: str = "extraction_failed"):
        super().__init__(
            code,
            requirement_ref=requirement_ref,
            detail="target candidate did not produce a usable artifact",
            private=True,
        )


@dataclass(frozen=True, slots=True)
class PageBudgetMath:
    """Derived target/external page slots for one validated request."""

    requirement_count: int
    max_research_pages: int
    required_target_pages: int
    mandatory_external_pages: int
    optional_external_pages: int
    external_extraction_candidates: int

    @property
    def minimum_pages(self) -> int:
        return self.required_target_pages + self.mandatory_external_pages

    @property
    def external_page_slots(self) -> int:
        return self.mandatory_external_pages + self.optional_external_pages


def page_budget_math(requirement_count: int, max_research_pages: int) -> PageBudgetMath:
    """Derive page slots from the supplied request, never frozen fixture counts."""

    if (
        type(requirement_count) is not int
        or requirement_count < 0
        or requirement_count > MAX_REQUIREMENTS
    ):
        raise TargetWorkflowFailure("workflow_page_budget_exceeded")
    if type(max_research_pages) is not int or max_research_pages < 1:
        raise TargetWorkflowFailure("workflow_page_budget_exceeded")

    required_target_pages = requirement_count
    # Targeted plans require one independent secondary.  A zero-requirement
    # request is the legacy path and therefore has no targeted-secondary floor.
    mandatory_external_pages = 1 if requirement_count else 0
    available_external = max_research_pages - required_target_pages
    if available_external < mandatory_external_pages:
        raise TargetWorkflowFailure("workflow_page_budget_exceeded")
    external_page_slots = max(0, available_external)
    optional_external_pages = (
        max(0, external_page_slots - mandatory_external_pages)
        if requirement_count
        else 0
    )
    return PageBudgetMath(
        requirement_count=requirement_count,
        max_research_pages=max_research_pages,
        required_target_pages=required_target_pages,
        mandatory_external_pages=mandatory_external_pages,
        optional_external_pages=optional_external_pages,
        external_extraction_candidates=(
            min(MAX_EXTERNAL_CANDIDATES, external_page_slots)
            if requirement_count
            else 0
        ),
    )


@dataclass(frozen=True, slots=True)
class TargetRequirement:
    """One flattened requirement in caller-provided target order."""

    target_index: int
    requirement_index: int
    target_name: str
    claim_class: str
    query: str
    source_prefixes: tuple[str, ...]
    target_domain: str
    target_domains: tuple[str, ...] = ()

    @property
    def requirement_ref(self) -> str:
        return f"target-{self.target_index}-requirement-{self.requirement_index}"

    @property
    def search_request_id(self) -> str:
        return f"target-search-{self.target_index}-{self.requirement_index}"


@dataclass(frozen=True, slots=True)
class TargetSearchRequest:
    """Provider-neutral request descriptor for one accepted research search."""

    requirement_ref: str
    query: str
    mode: str = "research"
    max_results: int = MAX_REQUIREMENT_SEARCH_RESULTS
    free_only: bool = False
    caller: str = ""
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class ReceiptBoundCandidate:
    """A result retaining exact accepted URL text plus a canonical key."""

    url: str
    canonical_url: str
    title: str
    result: Mapping[str, Any]
    requirement_ref: str
    target_name: str
    registrable_domain: str


@dataclass(frozen=True, slots=True)
class RequirementSelection:
    requirement: TargetRequirement
    receipt_ref: str
    request_id: str
    candidates: tuple[ReceiptBoundCandidate, ...]
    search_index: int = 0

    @property
    def requirement_ref(self) -> str:
        return self.requirement.requirement_ref


@dataclass(frozen=True, slots=True)
class TargetResearchPlan:
    requirements: tuple[RequirementSelection, ...]
    external_candidates: tuple[ReceiptBoundCandidate, ...]
    page_math: PageBudgetMath
    search_requests: tuple[TargetSearchRequest, ...]

    @property
    def requirement_count(self) -> int:
        return len(self.requirements)

    @property
    def target_document_slots(self) -> int:
        return self.page_math.required_target_pages

    @property
    def mandatory_external(self) -> bool:
        return self.page_math.mandatory_external_pages > 0 and self.requirement_count > 0


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _registrable_domain(url_or_host: str) -> str:
    raw = str(url_or_host)
    try:
        host = urlsplit(raw).hostname or raw
    except ValueError:
        host = raw
    host = host.strip().lower().rstrip(".")
    if not host:
        return ""
    try:
        value = get_fld(host, fix_protocol=True)
    except Exception:
        value = None
    if isinstance(value, str) and value:
        return value.lower().rstrip(".")
    parts = [part for part in host.split(".") if part]
    return ".".join(parts[-2:]) if len(parts) > 1 else host


def _safe_result_url(result: Any) -> tuple[str, str] | None:
    value = _value(result, "url")
    if not isinstance(value, str) or not value:
        return None
    try:
        # normalize_source_prefix applies the strict HTTPS/public-host,
        # credential/query/fragment and SSRF-safe checks.
        canonical = normalize_source_prefix(value)
    except (TypeError, ValueError):
        return None
    return value, canonical


def _accepted_results(operation: Any) -> tuple[Mapping[str, Any], ...]:
    result = _value(operation, "result")
    values = _value(result, "results", ())
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return ()
    return tuple(value for value in values if isinstance(value, Mapping))


def accepted_receipt_ref(operation: Any) -> str | None:
    """Extract the accepted receipt identity without modifying its projection."""

    result = _value(operation, "result")
    receipt = _value(result, "acceptance_receipt")
    value = _value(receipt, "receipt_ref")
    return value if isinstance(value, str) and value else None


def _outcome_value(operation: Any) -> str:
    outcome = _value(operation, "outcome")
    value = _value(outcome, "value", outcome)
    return str(value) if value is not None else "unready"


_PRESERVED_AUTHORITY_CODES = frozenset(
    {
        "unready",
        "persistence_failed",
        "invalid_request",
        "authentication_rejected",
        "policy_rejected",
        "providers_failed",
        "contract_error",
        "accepted_contract_error",
    }
)


def accepted_failure_code(operation: Any) -> str | None:
    """Return an accepted authority code that must not gain a workflow prefix."""

    error = _value(operation, "error")
    code = _value(error, "code")
    outcome = _outcome_value(operation)
    if not code:
        code = outcome
    if isinstance(code, str) and code.startswith("workflow_composition_"):
        code = code.removeprefix("workflow_composition_")
    if outcome in {
        CanonicalOutcome.UNREADY.value,
        CanonicalOutcome.PERSISTENCE_FAILED.value,
        CanonicalOutcome.INVALID_REQUEST.value,
    }:
        return str(code)
    if str(code) in _PRESERVED_AUTHORITY_CODES:
        return str(code)
    if "contract" in str(code).lower():
        return str(code)
    return None


def derive_requirement_request_id(receipt_ref: str, requirement_ref: str) -> str:
    """Derive a bounded stable ID from the accepted receipt and requirement ref."""

    material = f"{receipt_ref}\x00{requirement_ref}".encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return f"target-{digest[:48]}"


def flatten_requirements(targets: Sequence[Any]) -> tuple[TargetRequirement, ...]:
    """Flatten target models/mappings while preserving their exact input order."""

    if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes, bytearray)):
        raise ValueError("research targets must be a sequence")
    flattened: list[TargetRequirement] = []
    for target_index, target in enumerate(targets):
        name = _value(target, "name")
        prefixes = _value(target, "source_prefixes", ())
        requirements = _value(target, "requirements", ())
        if not isinstance(name, str) or not name:
            raise ValueError("research target name is required")
        if not isinstance(prefixes, Sequence) or isinstance(
            prefixes, (str, bytes, bytearray)
        ):
            raise ValueError("research target source prefixes are required")
        canonical_prefixes: list[str] = []
        for prefix in prefixes:
            try:
                canonical_prefixes.append(normalize_source_prefix(prefix))
            except (TypeError, ValueError) as exc:
                raise ValueError("research target source prefix is unsafe") from exc
        target_domains = tuple(
            dict.fromkeys(_registrable_domain(prefix) for prefix in canonical_prefixes)
        )
        target_domain = target_domains[0] if target_domains else ""
        if not isinstance(requirements, Sequence) or isinstance(
            requirements, (str, bytes, bytearray)
        ):
            raise ValueError("research target requirements are required")
        for requirement_index, requirement in enumerate(requirements):
            claim_class = _value(requirement, "claim_class")
            query = _value(requirement, "query")
            if not isinstance(claim_class, str) or not isinstance(query, str) or not query:
                raise ValueError("research requirement claim class and query are required")
            flattened.append(
                TargetRequirement(
                    target_index=target_index,
                    requirement_index=requirement_index,
                    target_name=name,
                    claim_class=claim_class,
                    query=query,
                    source_prefixes=tuple(canonical_prefixes),
                    target_domain=target_domain,
                    target_domains=target_domains,
                )
            )
    if len(flattened) > MAX_REQUIREMENTS:
        raise TargetWorkflowFailure("workflow_page_budget_exceeded")
    return tuple(flattened)


def make_target_search_requests(
    targets: Sequence[Any], *, free_only: bool = False, caller: str = ""
) -> tuple[TargetSearchRequest, ...]:
    """Build exactly one bounded research request per supplied requirement."""

    return tuple(
        TargetSearchRequest(
            requirement_ref=requirement.requirement_ref,
            query=requirement.query,
            free_only=bool(free_only),
            caller=caller,
            request_id=requirement.search_request_id,
        )
        for requirement in flatten_requirements(targets)
    )


def _candidate(
    result: Mapping[str, Any],
    *,
    requirement: TargetRequirement,
    requirement_ref: str,
) -> ReceiptBoundCandidate | None:
    safe = _safe_result_url(result)
    if safe is None:
        return None
    exact_url, canonical = safe
    title = _value(result, "title", "")
    return ReceiptBoundCandidate(
        url=exact_url,
        canonical_url=canonical,
        title=title if isinstance(title, str) else "",
        result=result,
        requirement_ref=requirement_ref,
        target_name=requirement.target_name,
        registrable_domain=_registrable_domain(canonical),
    )


def select_prefix_candidates(
    requirement: TargetRequirement,
    operation: Any,
    *,
    global_seen: set[str] | None = None,
) -> tuple[ReceiptBoundCandidate, ...]:
    """Select at most the first two path-bound candidates in result order."""

    authority_code = accepted_failure_code(operation)
    if authority_code is not None and _outcome_value(operation) not in {
        CanonicalOutcome.SUCCESS.value,
        CanonicalOutcome.DEGRADED.value,
        CanonicalOutcome.EMPTY.value,
    }:
        raise TargetWorkflowFailure(authority_code, requirement_ref=requirement.requirement_ref)
    seen = global_seen if global_seen is not None else set()
    selected: list[ReceiptBoundCandidate] = []
    for result in _accepted_results(operation):
        candidate = _candidate(
            result,
            requirement=requirement,
            requirement_ref=requirement.requirement_ref,
        )
        if candidate is None or candidate.canonical_url in seen:
            continue
        if not any(prefix_matches(prefix, candidate.url) for prefix in requirement.source_prefixes):
            continue
        seen.add(candidate.canonical_url)
        selected.append(candidate)
        if len(selected) >= MAX_PREFIX_CANDIDATES:
            break
    return tuple(selected)


def select_external_candidates(
    operations: Sequence[Any],
    *,
    excluded_domains: set[str] | frozenset[str],
    excluded_urls: set[str] | frozenset[str] = frozenset(),
    max_pages: int,
    max_candidates: int = MAX_EXTERNAL_CANDIDATES,
    max_per_domain: int = MAX_EXTERNAL_PER_DOMAIN,
) -> tuple[ReceiptBoundCandidate, ...]:
    """Select valid independent external pages in accepted result order."""

    if max_pages <= 0 or max_candidates <= 0:
        return ()
    seen = set(excluded_urls)
    domain_counts: dict[str, int] = {}
    selected: list[ReceiptBoundCandidate] = []
    placeholder = TargetRequirement(
        target_index=0,
        requirement_index=0,
        target_name="external",
        claim_class="external",
        query="",
        source_prefixes=(),
        target_domain="",
        target_domains=(),
    )
    for operation in operations:
        authority_code = accepted_failure_code(operation)
        if authority_code is not None and _outcome_value(operation) not in {
            CanonicalOutcome.SUCCESS.value,
            CanonicalOutcome.DEGRADED.value,
            CanonicalOutcome.EMPTY.value,
        }:
            raise TargetWorkflowFailure(authority_code)
        requirement_ref = "external"
        receipt = accepted_receipt_ref(operation)
        if receipt:
            requirement_ref = f"external-{hashlib.sha256(receipt.encode()).hexdigest()[:12]}"
        for result in _accepted_results(operation):
            candidate = _candidate(
                result,
                requirement=placeholder,
                requirement_ref=requirement_ref,
            )
            if candidate is None or candidate.canonical_url in seen:
                continue
            if candidate.registrable_domain in excluded_domains:
                continue
            if domain_counts.get(candidate.registrable_domain, 0) >= max_per_domain:
                continue
            seen.add(candidate.canonical_url)
            domain_counts[candidate.registrable_domain] = (
                domain_counts.get(candidate.registrable_domain, 0) + 1
            )
            selected.append(candidate)
            if len(selected) >= min(max_pages, max_candidates):
                return tuple(selected)
    return tuple(selected)


def plan_target_research(
    targets: Sequence[Any],
    accepted_searches: Sequence[Any],
    *,
    max_research_pages: int,
    free_only: bool = False,
    caller: str = "",
) -> TargetResearchPlan:
    """Plan target and external selections from accepted searches only."""

    requirements = flatten_requirements(targets)
    page_math = page_budget_math(len(requirements), max_research_pages)
    if not isinstance(accepted_searches, Sequence) or isinstance(
        accepted_searches, (str, bytes, bytearray)
    ):
        raise ValueError("accepted searches must be a sequence")
    if len(accepted_searches) < len(requirements):
        raise TargetWorkflowFailure("workflow_required_target_unready")

    selected_requirements: list[RequirementSelection] = []
    globally_seen: set[str] = set()
    for search_index, (requirement, operation) in enumerate(
        zip(requirements, accepted_searches, strict=False)
    ):
        outcome = _outcome_value(operation)
        if outcome == CanonicalOutcome.TIMEOUT.value:
            raise TargetWorkflowFailure(
                "workflow_required_target_search_timeout",
                requirement_ref=requirement.requirement_ref,
            )
        receipt_ref = accepted_receipt_ref(operation)
        if receipt_ref is None:
            authority_code = accepted_failure_code(operation)
            raise TargetWorkflowFailure(
                authority_code or "workflow_required_target_unready",
                requirement_ref=requirement.requirement_ref,
            )
        candidates = select_prefix_candidates(
            requirement,
            operation,
            global_seen=globally_seen,
        )
        if not candidates:
            raise TargetWorkflowFailure(
                "workflow_required_target_unready",
                requirement_ref=requirement.requirement_ref,
            )
        selected_requirements.append(
            RequirementSelection(
                requirement=requirement,
                receipt_ref=receipt_ref,
                request_id=derive_requirement_request_id(
                    receipt_ref,
                    requirement.requirement_ref,
                ),
                candidates=candidates,
                search_index=search_index,
            )
        )

    excluded_domains = {
        domain
        for requirement in selected_requirements
        for domain in requirement.requirement.target_domains
        if domain
    }
    external_candidates = select_external_candidates(
        accepted_searches,
        excluded_domains=excluded_domains,
        excluded_urls=globally_seen,
        max_pages=page_math.external_extraction_candidates,
    )
    if selected_requirements and not external_candidates:
        raise TargetWorkflowFailure("workflow_external_evidence_unready")
    return TargetResearchPlan(
        requirements=tuple(selected_requirements),
        external_candidates=external_candidates,
        page_math=page_math,
        search_requests=make_target_search_requests(
            targets,
            free_only=free_only,
            caller=caller,
        ),
    )


class TargetResearchPlanner:
    """Small stateful facade over the pure planning functions."""

    def __init__(
        self,
        targets: Sequence[Any],
        *,
        max_research_pages: int,
        free_only: bool = False,
        caller: str = "",
    ) -> None:
        self.targets = tuple(targets)
        self.max_research_pages = max_research_pages
        self.free_only = bool(free_only)
        self.caller = caller

    @property
    def search_requests(self) -> tuple[TargetSearchRequest, ...]:
        return make_target_search_requests(
            self.targets,
            free_only=self.free_only,
            caller=self.caller,
        )

    def plan(self, accepted_searches: Sequence[Any]) -> TargetResearchPlan:
        return plan_target_research(
            self.targets,
            accepted_searches,
            max_research_pages=self.max_research_pages,
            free_only=self.free_only,
            caller=self.caller,
        )


# Descriptive compatibility aliases for callers/tests that prefer noun-first
# names.  They all resolve to the same pure implementation above.
TargetPlan = TargetResearchPlan
ResearchTargetPlan = TargetResearchPlan
TargetCandidate = ReceiptBoundCandidate
ResearchRequirementPlan = RequirementSelection
SelectionPlan = TargetResearchPlan
build_target_search_requests = make_target_search_requests
plan_targets = plan_target_research


__all__ = [
    "MAX_EXTERNAL_CANDIDATES",
    "MAX_EXTERNAL_PER_DOMAIN",
    "MAX_PREFIX_CANDIDATES",
    "MAX_REQUIREMENT_SEARCH_RESULTS",
    "MAX_REQUIREMENTS",
    "TARGET_CANDIDATE_COMPOSITION_TIMEOUT_SECONDS",
    "TARGET_EXTERNAL_REMAINDER_TIMEOUT_SECONDS",
    "TARGET_GLOBAL_CONCURRENCY",
    "TARGET_REQUIREMENT_SEARCH_TIMEOUT_SECONDS",
    "TARGET_WORKFLOW_DEADLINE_SECONDS",
    "PageBudgetMath",
    "ReceiptBoundCandidate",
    "ResearchRequirementPlan",
    "ResearchTargetPlan",
    "SelectionPlan",
    "TargetCandidate",
    "TargetCandidateFailure",
    "TargetPlan",
    "TargetRequirement",
    "TargetResearchPlan",
    "TargetSearchRequest",
    "TargetResearchPlanner",
    "TargetWorkflowFailure",
    "accepted_failure_code",
    "accepted_receipt_ref",
    "build_target_search_requests",
    "derive_requirement_request_id",
    "flatten_requirements",
    "make_target_search_requests",
    "page_budget_math",
    "plan_target_research",
    "plan_targets",
    "select_external_candidates",
    "select_prefix_candidates",
]
