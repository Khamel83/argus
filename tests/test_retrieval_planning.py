"""Contract tests for deterministic retrieval planning."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import date, datetime, timezone
from inspect import Parameter, signature
from typing import Callable

import pytest

from argus.broker.accepted import execution_cohort
from argus.broker.planning import (
    DomainConstraints,
    ExecutionPolicySnapshot,
    FreshnessRelative,
    FreshnessWindow,
    InvalidRetrievalPlan,
    RetrievalControls,
    resolve_plan,
)
from argus.contracts.outcomes import CanonicalOutcome
from argus.models import ProviderName, SearchMode, SearchQuery

UTC_NOW = datetime(2026, 7, 27, 23, 59, tzinfo=timezone.utc)
DEFAULT_ORDER = (
    ProviderName.SEARXNG,
    ProviderName.DUCKDUCKGO,
    ProviderName.YAHOO,
    ProviderName.GITHUB,
    ProviderName.BRAVE,
    ProviderName.EXA,
    ProviderName.TAVILY,
    ProviderName.LINKUP,
    ProviderName.PARALLEL,
    ProviderName.SERPER,
    ProviderName.YOU,
    ProviderName.SEARCHAPI,
    ProviderName.VALYU,
)


@dataclass(frozen=True)
class PlanInput:
    query: SearchQuery
    controls: RetrievalControls = RetrievalControls()
    attribution: bool = False
    policy: ExecutionPolicySnapshot = ExecutionPolicySnapshot()
    now: datetime = UTC_NOW


def _input(**query_changes) -> PlanInput:
    values = {
        "query": "  cafe\u0301 \t status ",
        "mode": SearchMode.DISCOVERY,
        "max_results": 10,
    }
    values.update(query_changes)
    return PlanInput(SearchQuery(**values))


def _plan(case: PlanInput):
    return resolve_plan(
        case.query,
        case.controls,
        case.attribution,
        case.policy,
        lambda: case.now,
    )


def _pair_for_identity_vector(name: str) -> tuple[PlanInput, PlanInput]:
    base = _input()
    if name == "whitespace-only query difference":
        return base, _input(query="café status")
    if name == "query case difference":
        return base, _input(query="Café status")
    if name == "caller/caller-label difference":
        return base, _input(caller="maya", metadata={"caller_label": "lane-a"})
    if name == "attempt scope difference":
        return (
            _input(metadata={"attempt_scope": "one"}),
            _input(metadata={"attempt_scope": "two"}),
        )
    if name == "free versus budgeted profile":
        return base, _input(free_only=True)
    if name == "caller tier-cap difference":
        return base, replace(
            base,
            policy=replace(base.policy, effective_max_provider_tier=1),
        )
    if name == "provider health/balance difference":
        return (
            _input(metadata={"provider_health": "healthy", "balance": 100}),
            _input(metadata={"provider_health": "down", "balance": 0}),
        )
    if name == "result limit difference":
        return base, _input(max_results=11)
    if name in {
        "cross-tier explicit-provider permutation",
        "cross-tier explicit providers",
    }:
        return (
            _input(
                providers=[
                    ProviderName.SERPER,
                    ProviderName.BRAVE,
                    ProviderName.YAHOO,
                ]
            ),
            _input(
                providers=[
                    ProviderName.BRAVE,
                    ProviderName.YAHOO,
                    ProviderName.SERPER,
                ]
            ),
        )
    if name in {
        "same-tier explicit-provider order difference",
        "same-tier explicit providers",
    }:
        return (
            _input(providers=[ProviderName.TAVILY, ProviderName.BRAVE]),
            _input(providers=[ProviderName.BRAVE, ProviderName.TAVILY]),
        )
    if name == "domain input order difference":
        return (
            replace(
                base,
                controls=replace(
                    base.controls,
                    domains=DomainConstraints(
                        include=("BÜCHER.example", "example.com")
                    ),
                ),
            ),
            replace(
                base,
                controls=replace(
                    base.controls,
                    domains=DomainConstraints(
                        include=("example.com", "xn--bcher-kva.example")
                    ),
                ),
            ),
        )
    if name == "freshness resolved date difference":
        controls = replace(
            base.controls,
            freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY),
        )
        return (
            replace(base, controls=controls),
            replace(
                base, controls=controls, now=datetime(2026, 7, 28, tzinfo=timezone.utc)
            ),
        )
    if name == "attribution presentation difference":
        return base, replace(base, attribution=True)
    if name == "shorter deadline":
        return base, replace(base, controls=replace(base.controls, deadline_ms=60_000))
    if name == "force revalidation":
        from argus.broker.planning import RevalidationMode

        return base, replace(
            base,
            controls=replace(
                base.controls,
                revalidation=RevalidationMode.FORCE,
            ),
        )
    version_fields = {
        "execution/presentation-only complete-plan schema bump": "plan_schema_version",
        "cache-identity schema bump": "cache_identity_schema_version",
        "query-normalization version bump": "query_normalization_version",
        "routing version bump": "routing_policy_version",
        "freshness version bump": "freshness_policy_version",
        "domain version bump": "domain_policy_version",
        "ranking version bump": "ranking_policy_version",
        "result-normalization version bump": "result_normalization_version",
        "spend-policy version bump": "spend_policy_version",
        "organization-policy version bump": "organization_policy_version",
    }
    if name in version_fields:
        value: object = (
            2
            if version_fields[name]
            in {"plan_schema_version", "cache_identity_schema_version"}
            else "2"
        )
        return base, replace(
            base,
            policy=replace(base.policy, **{version_fields[name]: value}),
        )
    if name == "deployment SHA or unrelated config change":
        return (
            _input(metadata={"deployment_sha": "aaa", "unrelated": "x"}),
            _input(metadata={"deployment_sha": "bbb", "unrelated": "y"}),
        )
    if name == "unknown metadata difference":
        return (
            _input(metadata={"future_field": {"a": 1}}),
            _input(metadata={"future_field": {"a": 2}}),
        )
    raise AssertionError(f"unhandled vector {name}")


IDENTITY_VECTORS = [
    ("whitespace-only query difference", True, True),
    ("query case difference", False, False),
    ("caller/caller-label difference", True, True),
    ("attempt scope difference", True, True),
    ("free versus budgeted profile", False, True),
    ("caller tier-cap difference", False, True),
    ("provider health/balance difference", True, True),
    ("result limit difference", False, False),
    ("cross-tier explicit-provider permutation", True, True),
    ("same-tier explicit-provider order difference", False, False),
    ("cross-tier explicit providers", True, True),
    ("same-tier explicit providers", False, False),
    ("domain input order difference", True, True),
    ("freshness resolved date difference", False, False),
    ("attribution presentation difference", False, True),
    ("shorter deadline", False, True),
    ("force revalidation", False, True),
    ("execution/presentation-only complete-plan schema bump", False, True),
    ("cache-identity schema bump", False, False),
    ("query-normalization version bump", False, False),
    ("routing version bump", False, False),
    ("freshness version bump", False, False),
    ("domain version bump", False, False),
    ("ranking version bump", False, False),
    ("result-normalization version bump", False, False),
    ("spend-policy version bump", False, True),
    ("organization-policy version bump", False, True),
    ("deployment SHA or unrelated config change", True, True),
    ("unknown metadata difference", True, True),
]


@pytest.mark.parametrize(
    ("name", "same_plan_id", "same_cache_fingerprint"),
    IDENTITY_VECTORS,
    ids=[row[0] for row in IDENTITY_VECTORS],
)
def test_adr_0002_identity_vectors(
    name: str,
    same_plan_id: bool,
    same_cache_fingerprint: bool,
) -> None:
    left_input, right_input = _pair_for_identity_vector(name)
    left = _plan(left_input)
    right = _plan(right_input)

    assert (left.plan_id == right.plan_id) is same_plan_id
    assert (left.cache_fingerprint == right.cache_fingerprint) is same_cache_fingerprint
    if name == "organization-policy version bump":
        assert execution_cohort(left) != execution_cohort(right)

    if name.startswith("cross-tier"):
        expected = (
            ProviderName.YAHOO,
            ProviderName.BRAVE,
            ProviderName.SERPER,
        )
        assert left.explicit_providers == expected
        assert right.explicit_providers == expected
        assert left.candidate_providers == expected
        assert right.candidate_providers == expected
    if name.startswith("same-tier"):
        assert left.explicit_providers == tuple(left_input.query.providers or ())
        assert right.explicit_providers == tuple(right_input.query.providers or ())


def test_fixed_canonical_json_hashes_do_not_drift() -> None:
    base = _plan(_input())
    explicit = _plan(
        _input(
            providers=[
                ProviderName.SERPER,
                ProviderName.BRAVE,
                ProviderName.YAHOO,
            ]
        )
    )

    assert (
        base.plan_id
        == "2639b22d379703732af0b2ae5e550546cfbe2e98d0dfe8cd58a3f30d7d0aa3f6"
    )
    assert (
        base.cache_fingerprint
        == "a54a2d1811d8fbbc6393644ff308a10f87ee35ca113eb4217d59d8389227e89a"
    )
    assert (
        explicit.plan_id
        == "ec4f506c6d803b3e886042a4b2645afd48f91e86c128cd70b81a2541d435023b"
    )
    assert (
        explicit.cache_fingerprint
        == "4e2ce538a8906a1abcfe3ef26a38954295550778e54660fbd0de03925e40edf6"
    )


def test_default_plan_has_exact_normalization_and_routing() -> None:
    plan = _plan(_input())

    assert plan.normalized_query == "café status"
    assert plan.intent is SearchMode.DISCOVERY
    assert plan.candidate_providers == DEFAULT_ORDER
    assert plan.freshness == FreshnessWindow(
        max_cache_age_seconds=604_800,
    )
    assert plan.provider_phase_budget_ms == 115_000


@pytest.mark.parametrize(
    ("relative", "start", "max_age"),
    [
        (FreshnessRelative.DAY, date(2026, 7, 27), 86_400),
        (FreshnessRelative.WEEK, date(2026, 7, 21), 604_800),
        (FreshnessRelative.MONTH, date(2026, 6, 28), 604_800),
        (FreshnessRelative.YEAR, date(2025, 7, 28), 604_800),
    ],
)
def test_relative_freshness_uses_one_injected_utc_clock(
    relative: FreshnessRelative,
    start: date,
    max_age: int,
) -> None:
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return UTC_NOW

    plan = resolve_plan(
        _input().query,
        RetrievalControls(freshness=FreshnessWindow(requested_relative=relative)),
        False,
        ExecutionPolicySnapshot(),
        clock,
    )

    assert calls == 1
    assert plan.freshness == FreshnessWindow(
        requested_relative=relative,
        start_date=start,
        end_date=date(2026, 7, 27),
        max_cache_age_seconds=max_age,
    )


def test_domains_are_idna_normalized_deduplicated_and_sorted() -> None:
    plan = _plan(
        replace(
            _input(),
            controls=RetrievalControls(
                domains=DomainConstraints(
                    include=("BÜCHER.example", "example.com", "example.com"),
                    exclude=("Ads.Example",),
                )
            ),
        )
    )

    assert plan.domains == DomainConstraints(
        include=("example.com", "xn--bcher-kva.example"),
        exclude=("ads.example",),
    )


def test_controls_and_plan_are_immutable() -> None:
    controls = RetrievalControls()
    plan = _plan(_input())

    with pytest.raises(FrozenInstanceError):
        controls.deadline_ms = 10  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.deadline_ms = 10  # type: ignore[misc]


def test_collection_snapshots_do_not_retain_mutable_source_lists() -> None:
    include = ["example.com"]
    allowed = [ProviderName.BRAVE]

    domains = DomainConstraints(include=include)  # type: ignore[arg-type]
    policy = ExecutionPolicySnapshot(allowed_providers=allowed)  # type: ignore[arg-type]
    include.append("mutated.example")
    allowed.append(ProviderName.SERPER)

    assert domains.include == ("example.com",)
    assert policy.allowed_providers == (ProviderName.BRAVE,)


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: DomainConstraints(include={"example.com"}),  # type: ignore[arg-type]
        lambda: ExecutionPolicySnapshot(
            allowed_providers={ProviderName.BRAVE}  # type: ignore[arg-type]
        ),
    ],
)
def test_collection_snapshots_reject_non_sequence_containers(
    constructor: Callable[[], object],
) -> None:
    with pytest.raises(TypeError):
        constructor()


def test_non_string_domain_element_reaches_invalid_request_path() -> None:
    with pytest.raises(InvalidRetrievalPlan) as caught:
        resolve_plan(
            _input().query,
            RetrievalControls(
                domains=DomainConstraints(include=["example.com", 7])  # type: ignore[list-item]
            ),
            False,
            ExecutionPolicySnapshot(),
            UTC_NOW,
        )

    assert caught.value.outcome is CanonicalOutcome.INVALID_REQUEST


def test_unknown_allowed_provider_reaches_invalid_request_path() -> None:
    with pytest.raises(InvalidRetrievalPlan) as caught:
        resolve_plan(
            _input().query,
            RetrievalControls(),
            False,
            ExecutionPolicySnapshot(
                allowed_providers=[ProviderName.BRAVE, "future"]  # type: ignore[list-item]
            ),
            UTC_NOW,
        )

    assert caught.value.outcome is CanonicalOutcome.INVALID_REQUEST


@pytest.mark.parametrize(
    "query",
    [
        SearchQuery(query=" \t\n"),
        SearchQuery(query="q", max_results=True),
        SearchQuery(query="q", max_results=1.5),  # type: ignore[arg-type]
        SearchQuery(query="q", max_results=0),
        SearchQuery(query="q", max_results=51),
        SearchQuery(query="q", providers=[ProviderName.CACHE]),
        SearchQuery(query="q", mode="future"),  # type: ignore[arg-type]
        SearchQuery(query="q", providers=["future"]),  # type: ignore[list-item]
    ],
)
def test_invalid_query_controls_return_invalid_request(query: SearchQuery) -> None:
    with pytest.raises(InvalidRetrievalPlan) as caught:
        resolve_plan(
            query,
            RetrievalControls(),
            False,
            ExecutionPolicySnapshot(),
            UTC_NOW,
        )

    assert caught.value.outcome is CanonicalOutcome.INVALID_REQUEST


@pytest.mark.parametrize(
    "freshness",
    [
        FreshnessWindow(
            requested_relative=FreshnessRelative.DAY,
            start_date=date(2026, 7, 1),
        ),
        FreshnessWindow(
            start_date=date(2026, 7, 28),
            end_date=date(2026, 7, 27),
        ),
        FreshnessWindow(start_date="2026-99-99"),  # type: ignore[arg-type]
        FreshnessWindow(requested_relative="future"),  # type: ignore[arg-type]
    ],
)
def test_invalid_freshness_returns_invalid_request(
    freshness: FreshnessWindow,
) -> None:
    with pytest.raises(InvalidRetrievalPlan):
        _plan(
            replace(
                _input(),
                controls=RetrievalControls(freshness=freshness),
            )
        )


@pytest.mark.parametrize(
    "domain",
    [
        "https://example.com",
        "example.com:443",
        "example.com/path",
        "example.com?q=1",
        "*.example.com",
        "example.com.",
        "127.0.0.1",
        "2001:db8::1",
        "[2001:db8::1]",
        "１２７.０.０.１",
        "①②⑦.⓪.⓪.①",
        "",
    ],
)
def test_invalid_domain_constraints_return_invalid_request(domain: str) -> None:
    with pytest.raises(InvalidRetrievalPlan):
        _plan(
            replace(
                _input(),
                controls=RetrievalControls(
                    domains=DomainConstraints(include=(domain,))
                ),
            )
        )


def test_overlapping_domains_return_invalid_request() -> None:
    with pytest.raises(InvalidRetrievalPlan):
        _plan(
            replace(
                _input(),
                controls=RetrievalControls(
                    domains=DomainConstraints(
                        include=("BÜCHER.example",),
                        exclude=("xn--bcher-kva.example",),
                    )
                ),
            )
        )


@pytest.mark.parametrize(
    "controls",
    [
        RetrievalControls(deadline_ms=120_001),
        RetrievalControls(deadline_ms=True),
        RetrievalControls(safe_search="future"),  # type: ignore[arg-type]
        RetrievalControls(revalidation="future"),  # type: ignore[arg-type]
    ],
)
def test_invalid_typed_controls_return_invalid_request(
    controls: RetrievalControls,
) -> None:
    with pytest.raises(InvalidRetrievalPlan):
        _plan(replace(_input(), controls=controls))


@pytest.mark.parametrize(
    "policy",
    [
        ExecutionPolicySnapshot(egress_preference="future"),  # type: ignore[arg-type]
        ExecutionPolicySnapshot(effective_max_provider_tier=True),
        ExecutionPolicySnapshot(routing_policy_version=""),
        ExecutionPolicySnapshot(routing_policy_version="\n"),
        ExecutionPolicySnapshot(routing_policy_version="x" * 65),
    ],
)
def test_invalid_policy_snapshot_returns_invalid_request(
    policy: ExecutionPolicySnapshot,
) -> None:
    with pytest.raises(InvalidRetrievalPlan):
        _plan(replace(_input(), policy=policy))


def test_bounds_are_checked_before_normalization() -> None:
    overlong_query = "é" * 8_193
    overlong_domain = "a" * 1_025
    too_many_providers = [ProviderName.BRAVE] * 33
    too_many_domains = ("example.com",) * 65

    for case in (
        _input(query=overlong_query),
        _input(providers=too_many_providers),
        replace(
            _input(),
            controls=RetrievalControls(
                domains=DomainConstraints(include=(overlong_domain,))
            ),
        ),
        replace(
            _input(),
            controls=RetrievalControls(
                domains=DomainConstraints(include=too_many_domains)
            ),
        ),
    ):
        with pytest.raises(InvalidRetrievalPlan):
            _plan(case)


def test_deadline_scaffolding_never_exceeds_120_seconds() -> None:
    plan = _plan(
        replace(
            _input(),
            controls=RetrievalControls(deadline_ms=6_000),
        )
    )

    assert plan.deadline_ms == 6_000
    assert plan.provider_phase_budget_ms == 1_000
    assert plan.deadline_ms <= 120_000


def test_provider_executor_requires_a_retrieval_plan_argument() -> None:
    from argus.broker.execution import ProviderExecutor

    parameter = signature(ProviderExecutor.execute).parameters["plan"]

    assert parameter.default is Parameter.empty


@pytest.mark.asyncio
async def test_provider_executor_unconditionally_validates_the_plan() -> None:
    from argus.broker.execution import ProviderExecutor

    executor = ProviderExecutor.__new__(ProviderExecutor)

    with pytest.raises(TypeError, match="validated retrieval plan is required"):
        await executor.execute(
            SearchQuery("query"),
            [],
            plan=None,
            operation_deadline=130.0,
            provider_phase_deadline=125.0,
        )


@pytest.mark.asyncio
async def test_broker_resolves_plan_before_cache_and_passes_deadlines() -> None:
    from argus.broker.router import SearchBroker
    from argus.models import SearchResponse

    events: list[tuple] = []

    class Pipeline:
        def get_cached(self, query, run_id, *, plan, compute_attribution=False):
            events.append(("cache", plan, query))
            return None

        def build_response(
            self,
            query,
            provider_results,
            traces,
            *,
            plan,
            **kwargs,
        ):
            events.append(("build", plan, query))
            return SearchResponse(query=query.query, mode=query.mode, results=[])

    class Executor:
        async def execute(
            self,
            query,
            provider_order,
            *,
            plan,
            operation_deadline,
            provider_phase_deadline,
        ):
            from argus.broker.execution import ProviderExecutionOutcome

            events.append(
                (
                    "execute",
                    plan,
                    query,
                    operation_deadline,
                    provider_phase_deadline,
                )
            )
            return ProviderExecutionOutcome([], {}, 0)

    broker = SearchBroker.__new__(SearchBroker)
    broker._config = type(
        "Config",
        (),
        {
            "residential": type("Residential", (), {"policy": "off"})(),
            "node": type("Node", (), {"egress_type": "unknown"})(),
            "caller_tier_caps": {},
        },
    )()
    broker._pipeline = Pipeline()
    broker._executor = Executor()
    broker._utc_clock = lambda: UTC_NOW
    broker._monotonic_clock = lambda: 10.0

    await broker.search(SearchQuery("query"))

    assert [event[0] for event in events] == ["cache", "execute", "build"]
    cache_plan = events[0][1]
    execute = events[1]
    assert cache_plan is execute[1] is events[2][1]
    assert execute[3] == 130.0
    assert execute[4] == 125.0


@pytest.mark.asyncio
async def test_broker_invalid_request_stops_before_cache_or_execution() -> None:
    from argus.broker.router import SearchBroker

    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError(f"invalid request reached {name}")

    broker = SearchBroker.__new__(SearchBroker)
    broker._config = type(
        "Config",
        (),
        {
            "residential": type("Residential", (), {"policy": "off"})(),
            "node": type("Node", (), {"egress_type": "unknown"})(),
            "caller_tier_caps": {},
        },
    )()
    broker._pipeline = Forbidden()
    broker._executor = Forbidden()
    broker._utc_clock = lambda: UTC_NOW
    broker._monotonic_clock = lambda: 10.0

    with pytest.raises(InvalidRetrievalPlan) as caught:
        await broker.search(SearchQuery(" ", max_results=0))

    assert caught.value.outcome is CanonicalOutcome.INVALID_REQUEST
