"""Exact, bounded evidence-fusion contract from ADR 0003."""

from __future__ import annotations

import ast
import itertools
import math
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path

import pytest

from argus.broker.fusion import (
    conservative_document_key,
    fusion_order_key,
    fuse_evidence,
    project_search_results,
)
from argus.broker.planning import (
    ExecutionPolicySnapshot,
    FreshnessRelative,
    FreshnessWindow,
    RetrievalControls,
    resolve_plan,
)
from argus.broker.provider_evidence import (
    ContractConfidence,
    ControlTranslation,
    EgressType,
    FailureCategory,
    FilterStrength,
    ProviderFailure,
    ProviderRequestEvidence,
    ProviderResponseEvidence,
    ProviderSearchBatch,
    PublicationEvidence,
    PublicationPrecision,
    PublicationSource,
    ResultObservation,
    SnippetEvidence,
    SnippetKind,
    TranslationPrecision,
)
from argus.contracts.outcomes import CanonicalOutcome
from argus.models import (
    FusionPolicy,
    ProviderName,
    RRFContribution,
    RankedResultCluster,
    SearchMode,
    SearchQuery,
)

NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


def _plan(
    *,
    mode: SearchMode = SearchMode.DISCOVERY,
    limit: int = 10,
    freshness: FreshnessWindow = FreshnessWindow(),
):
    return resolve_plan(
        SearchQuery(query="bounded fusion", mode=mode, max_results=limit),
        RetrievalControls(freshness=freshness),
        True,
        ExecutionPolicySnapshot(
            freshness_policy_version="freshness-v1",
            domain_policy_version="domain-v1-psl-fixture",
            ranking_policy_version="ranking-v1",
        ),
        NOW,
    )


def _publication(
    *,
    when: datetime | None = None,
    day: date | None = date(2026, 7, 27),
    precision: PublicationPrecision = PublicationPrecision.DATE,
    source: PublicationSource = PublicationSource.PROVIDER_FIELD,
    confidence: ContractConfidence = ContractConfidence.OFFICIAL_CONTRACT,
    field: str = "published_at",
    reference: str | None = "fixture-publication-v1",
) -> PublicationEvidence:
    return PublicationEvidence(
        published_at_utc=when,
        published_date=None if when is not None else day,
        precision=precision,
        source=source,
        contract_confidence=confidence,
        raw_field_name=field,
        semantic_contract_ref=reference,
    )


def _observation(
    provider: ProviderName,
    rank: int,
    url: str,
    *,
    title: str | None = None,
    snippet: str | None = None,
    publication: PublicationEvidence | None = None,
) -> ResultObservation:
    return ResultObservation(
        provider=provider,
        provider_rank=rank,
        url=url,
        title=title or f"{provider.value}-{rank}",
        snippet=SnippetEvidence(
            snippet if snippet is not None else f"snippet-{provider.value}-{rank}",
            SnippetKind.PROVIDER_SNIPPET,
        ),
        publication=publication,
        observed_at=NOW,
        egress=EgressType.DATACENTER,
        machine="test-node",
    )


def _batch(
    provider: ProviderName,
    observations: tuple[ResultObservation, ...] = (),
    *,
    translation: ControlTranslation | None = None,
    empty: bool = False,
) -> ProviderSearchBatch:
    return ProviderSearchBatch(
        provider=provider,
        provider_contract_version="2026-07-27-v1",
        request_evidence=ProviderRequestEvidence(
            freshness_translation=translation,
        ),
        response_evidence=ProviderResponseEvidence(
            result_count=len(observations),
            observed_at=NOW,
            egress=EgressType.DATACENTER,
            machine="test-node",
        ),
        observations=observations,
        failure=(
            ProviderFailure(
                FailureCategory.EMPTY,
                provider,
                summary="recognized successful empty",
                observed_at=NOW,
            )
            if empty
            else None
        ),
    )


def _batch_with_target(
    provider: ProviderName,
    rank: int,
    url: str,
) -> ProviderSearchBatch:
    return _batch(
        provider,
        tuple(
            _observation(
                provider,
                index,
                (
                    url
                    if index == rank
                    else f"https://filler-{provider.value}.test/{index}"
                ),
            )
            for index in range(rank + 1)
        ),
    )


def _fuse(plan, *batches, policy: FusionPolicy | None = None):
    return fuse_evidence(plan, tuple(batches), policy or FusionPolicy(), lambda: NOW)


@pytest.mark.parametrize(
    ("relative", "inside_start", "outside_start"),
    [
        (FreshnessRelative.DAY, date(2026, 7, 27), date(2026, 7, 26)),
        (FreshnessRelative.WEEK, date(2026, 7, 21), date(2026, 7, 20)),
        (FreshnessRelative.MONTH, date(2026, 6, 28), date(2026, 6, 27)),
        (FreshnessRelative.YEAR, date(2025, 7, 28), date(2025, 7, 27)),
    ],
)
def test_relative_freshness_edges_are_inclusive(relative, inside_start, outside_start):
    plan = _plan(freshness=FreshnessWindow(requested_relative=relative))
    rows = (
        _observation(ProviderName.EXA, 0, "https://inside.test/start", publication=_publication(day=inside_start)),
        _observation(ProviderName.EXA, 1, "https://inside.test/end", publication=_publication(day=date(2026, 7, 27))),
        _observation(ProviderName.EXA, 2, "https://outside.test", publication=_publication(day=outside_start)),
    )

    outcome = _fuse(plan, _batch(ProviderName.EXA, rows))

    assert [cluster.representative_url for cluster in outcome.ranked_result_clusters] == [
        "https://inside.test/start",
        "https://inside.test/end",
    ]
    assert outcome.filtered_observations[-1].reason == "out_of_range"


def test_explicit_date_edges_and_timestamp_edges_are_inclusive():
    plan = _plan(
        freshness=FreshnessWindow(
            start_date=date(2026, 7, 10),
            end_date=date(2026, 7, 12),
        )
    )
    rows = (
        _observation(ProviderName.EXA, 0, "https://x.test/start", publication=_publication(when=datetime(2026, 7, 10, tzinfo=timezone.utc))),
        _observation(ProviderName.EXA, 1, "https://x.test/end", publication=_publication(when=datetime(2026, 7, 12, 23, 59, 59, 999999, tzinfo=timezone.utc))),
        _observation(ProviderName.EXA, 2, "https://x.test/late", publication=_publication(when=datetime(2026, 7, 13, tzinfo=timezone.utc))),
    )
    outcome = _fuse(plan, _batch(ProviderName.EXA, rows))
    assert len(outcome.ranked_result_clusters) == 2
    assert outcome.filtered_observations[-1].reason == "out_of_range"


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        (_publication(day=date(2026, 7, 1), precision=PublicationPrecision.MONTH), True),
        (_publication(day=date(2026, 7, 1), precision=PublicationPrecision.YEAR), True),
        (_publication(day=date(2026, 1, 1), precision=PublicationPrecision.YEAR), True),
    ],
)
def test_coarse_precision_must_fit_entire_requested_interval(claim, expected):
    plan = _plan(
        freshness=FreshnessWindow(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
    )
    outcome = _fuse(
        plan,
        _batch(ProviderName.EXA, (_observation(ProviderName.EXA, 0, "https://x.test/a", publication=claim),)),
    )
    assert bool(outcome.ranked_result_clusters) is expected


def test_year_precision_is_rejected_when_request_covers_only_one_month():
    plan = _plan(
        freshness=FreshnessWindow(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
        )
    )
    outcome = _fuse(
        plan,
        _batch(
            ProviderName.EXA,
            (
                _observation(
                    ProviderName.EXA,
                    0,
                    "https://x.test/a",
                    publication=_publication(
                        day=date(2026, 7, 1),
                        precision=PublicationPrecision.YEAR,
                    ),
                ),
            ),
        ),
    )
    assert outcome.ranked_result_clusters == ()
    assert outcome.filtered_observations[0].reason == "out_of_range"


def test_disjoint_approved_claims_for_same_document_fail_closed():
    plan = _plan(
        freshness=FreshnessWindow(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
        )
    )
    outcome = _fuse(
        plan,
        _batch(ProviderName.EXA, (_observation(ProviderName.EXA, 0, "https://x.test/a", publication=_publication(day=date(2026, 7, 1))),)),
        _batch(ProviderName.PARALLEL, (_observation(ProviderName.PARALLEL, 0, "https://x.test/a", publication=_publication(day=date(2026, 7, 20))),)),
    )
    assert outcome.ranked_result_clusters == ()
    assert {item.reason for item in outcome.filtered_observations} == {"conflicting_publication"}


@pytest.mark.parametrize(
    "claim",
    [
        _publication(confidence=ContractConfidence.UNVERIFIED),
        _publication(source=PublicationSource.RESULT_TEXT),
        _publication(field="modified_at"),
        _publication(field="indexed_at"),
        _publication(reference=None),
    ],
)
def test_unverified_modified_indexed_and_result_text_claims_never_prove_freshness(claim):
    plan = _plan(freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY))
    outcome = _fuse(
        plan,
        _batch(ProviderName.EXA, (_observation(ProviderName.EXA, 0, "https://x.test/a", publication=claim),)),
    )
    assert outcome.ranked_result_clusters == ()
    assert outcome.filtered_observations[0].reason == "freshness_unproven"


def test_widened_translation_is_exactly_post_filtered():
    widened = ControlTranslation(
        "relative_only",
        TranslationPrecision.WIDENED,
        FilterStrength.BEST_EFFORT,
        "freshness",
        "pw",
    )
    plan = _plan(freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY))
    rows = (
        _observation(ProviderName.EXA, 0, "https://x.test/current", publication=_publication()),
        _observation(ProviderName.EXA, 1, "https://x.test/stale", publication=_publication(day=date(2026, 7, 26))),
    )
    outcome = _fuse(plan, _batch(ProviderName.EXA, rows, translation=widened))
    assert [cluster.representative_url for cluster in outcome.ranked_result_clusters] == ["https://x.test/current"]
    assert outcome.filtered_observations[-1].reason == "out_of_range"


def test_strict_exact_recognized_empty_is_proven_but_other_empty_is_unproven():
    exact = ControlTranslation(
        "date_range",
        TranslationPrecision.EXACT,
        FilterStrength.STRICT_CONTRACT,
        "start_date/end_date",
        "2026-07-27/2026-07-27",
    )
    plan = _plan(freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY))
    strict = _fuse(plan, _batch(ProviderName.EXA, translation=exact, empty=True))
    weak = _fuse(plan, _batch(ProviderName.EXA))
    assert strict.outcome is CanonicalOutcome.EMPTY
    assert strict.reason == "strict_empty"
    assert weak.outcome is CanonicalOutcome.EMPTY
    assert weak.reason == "freshness_unproven"


def test_document_key_normalizes_only_safe_proven_identity():
    assert conservative_document_key("https://EXAMPLE.com:443/a/%7e/b/../c#frag") == (
        conservative_document_key("https://example.com/a/~/c")
    )
    assert conservative_document_key("http://example.com:80/a") == "http://example.com/a"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("http://example.com/a", "https://example.com/a"),
        ("https://example.com/A", "https://example.com/a"),
        ("https://example.com/a", "https://example.com/a/"),
        ("https://example.com/a?b=1&a=2", "https://example.com/a?a=2&b=1"),
        ("https://example.com/a?k=1&k=2", "https://example.com/a?k=2&k=1"),
        ("https://example.com/a?flag", "https://example.com/a?flag="),
        ("https://www.example.com/a", "https://example.com/a"),
        ("https://example.com/a?utm_source=x", "https://example.com/a"),
    ],
)
def test_document_key_preserves_weak_or_potentially_semantic_differences(left, right):
    assert conservative_document_key(left) != conservative_document_key(right)


def test_title_snippet_similarity_and_provider_canonical_hints_do_not_merge():
    plan = _plan()
    rows = (
        _observation(ProviderName.EXA, 0, "https://one.test/story", title="same", snippet="same"),
        _observation(ProviderName.EXA, 1, "https://two.test/story", title="same", snippet="same"),
    )
    outcome = _fuse(plan, _batch(ProviderName.EXA, rows))
    assert len(outcome.ranked_result_clusters) == 2
    assert outcome.duplicate_relations == ()


def test_rrf_uses_exact_fractions_one_best_contribution_per_provider():
    plan = _plan()
    outcome = _fuse(
        plan,
        _batch(
            ProviderName.EXA,
            (
                _observation(ProviderName.EXA, 0, "https://x.test/a"),
                _observation(ProviderName.EXA, 1, "https://x.test/a"),
            ),
        ),
        _batch_with_target(ProviderName.BRAVE, 2, "https://x.test/a"),
    )
    cluster = outcome.ranked_result_clusters[0]
    expected = Fraction(1, 61) + Fraction(1, 63)
    assert (cluster.score_numerator, cluster.score_denominator) == (
        expected.numerator,
        expected.denominator,
    )
    assert [(c.provider, c.provider_rank, c.numerator, c.denominator) for c in cluster.contributions] == [
        (ProviderName.BRAVE, 2, 1, 63),
        (ProviderName.EXA, 0, 1, 61),
    ]
    assert len(cluster.observations) == 3


def test_exact_order_survives_7_11_29_counterexample_and_every_provider_permutation():
    providers = (ProviderName.BRAVE, ProviderName.EXA, ProviderName.PARALLEL)
    batches = {
        providers[0]: _batch(providers[0], tuple(_observation(providers[0], rank, f"https://filler-{providers[0].value}.test/{rank}") if rank != 7 else _observation(providers[0], rank, "https://target.test/a") for rank in range(8))),
        providers[1]: _batch(providers[1], tuple(_observation(providers[1], rank, f"https://filler-{providers[1].value}.test/{rank}") if rank != 11 else _observation(providers[1], rank, "https://target.test/a") for rank in range(12))),
        providers[2]: _batch(providers[2], tuple(_observation(providers[2], rank, f"https://filler-{providers[2].value}.test/{rank}") if rank != 29 else _observation(providers[2], rank, "https://target.test/a") for rank in range(30))),
    }
    signatures = {
        tuple(
            (cluster.cluster_sort_key, cluster.score_numerator, cluster.score_denominator)
            for cluster in _fuse(_plan(limit=50), *(batches[p] for p in order)).ranked_result_clusters
        )
        for order in itertools.permutations(providers)
    }
    assert len(signatures) == 1
    target = next(item for item in next(iter(signatures)) if item[0] == "https://target.test/a")
    exact = Fraction(1, 68) + Fraction(1, 72) + Fraction(1, 90)
    assert target[1:] == (exact.numerator, exact.denominator)


def _synthetic_cluster(
    key: str,
    score: Fraction,
    best_rank: int,
    contributor_count: int,
    smallest_provider: ProviderName,
) -> RankedResultCluster:
    return RankedResultCluster(
        cluster_sort_key=key,
        observations=(),
        representative_url=key,
        representative_title=key,
        representative_snippet="",
        representative_provider=smallest_provider,
        representative_rank=best_rank,
        site_key="example.test",
        contributions=(
            RRFContribution(smallest_provider, best_rank, 1, 61 + best_rank),
        ),
        score_numerator=score.numerator,
        score_denominator=score.denominator,
        best_provider_rank=best_rank,
        contributor_count=contributor_count,
        smallest_provider=smallest_provider,
        base_rank=-1,
        output_rank=-1,
    )


def test_all_five_base_tie_breaks_have_the_exact_declared_precedence():
    high = _synthetic_cluster(
        "https://z.test", Fraction(2, 61), 9, 1, ProviderName.EXA
    )
    low = _synthetic_cluster(
        "https://a.test", Fraction(1, 61), 0, 9, ProviderName.BRAVE
    )
    assert fusion_order_key(high) < fusion_order_key(low)

    best_rank = _synthetic_cluster(
        "https://z.test", Fraction(8, 315), 2, 2, ProviderName.EXA
    )
    later_rank = _synthetic_cluster(
        "https://a.test", Fraction(8, 315), 9, 2, ProviderName.BRAVE
    )
    assert fusion_order_key(best_rank) < fusion_order_key(later_rank)

    more = _synthetic_cluster(
        "https://z.test", Fraction(5107, 84546), 0, 5, ProviderName.EXA
    )
    fewer = _synthetic_cluster(
        "https://a.test", Fraction(5107, 84546), 0, 4, ProviderName.BRAVE
    )
    assert fusion_order_key(more) < fusion_order_key(fewer)

    brave = _synthetic_cluster(
        "https://z.test", Fraction(1, 61), 0, 1, ProviderName.BRAVE
    )
    exa = _synthetic_cluster(
        "https://a.test", Fraction(1, 61), 0, 1, ProviderName.EXA
    )
    assert fusion_order_key(brave) < fusion_order_key(exa)

    key_a = _synthetic_cluster(
        "https://a.test", Fraction(1, 61), 0, 1, ProviderName.EXA
    )
    key_b = _synthetic_cluster(
        "https://b.test", Fraction(1, 61), 0, 1, ProviderName.EXA
    )
    assert fusion_order_key(key_a) < fusion_order_key(key_b)


def test_representative_selection_is_deterministic_and_keeps_fields_together():
    plan = _plan(limit=10)
    batches = (
        _batch(ProviderName.BRAVE, (
            _observation(ProviderName.BRAVE, 0, "https://score.test/high"),
            _observation(ProviderName.BRAVE, 1, "https://rank.test/b"),
            _observation(ProviderName.BRAVE, 2, "https://contributors.test/a", title="brave representative"),
            _observation(ProviderName.BRAVE, 3, "https://provider.test/b"),
            _observation(ProviderName.BRAVE, 4, "https://key.test/b"),
        )),
        _batch(ProviderName.EXA, (
            _observation(ProviderName.EXA, 0, "https://score.test/high"),
            _observation(ProviderName.EXA, 1, "https://rank.test/a"),
            _observation(ProviderName.EXA, 2, "https://contributors.test/a", title="exa representative"),
            _observation(ProviderName.EXA, 3, "https://provider.test/a"),
            _observation(ProviderName.EXA, 4, "https://key.test/a"),
        )),
    )
    outcome = _fuse(plan, *batches)
    keys = [cluster.cluster_sort_key for cluster in outcome.ranked_result_clusters]
    assert keys[0] == "https://score.test/high"
    assert keys.index("https://provider.test/b") < keys.index("https://provider.test/a")
    representative = next(c for c in outcome.ranked_result_clusters if c.cluster_sort_key == "https://contributors.test/a")
    assert representative.representative_provider is ProviderName.BRAVE
    assert representative.representative_title == "brave representative"


def test_projection_uses_representative_and_attribution_without_mutating_outcome():
    exa = _batch(
        ProviderName.EXA,
        (_observation(ProviderName.EXA, 0, "https://x.test/a"),),
    )
    brave = _batch_with_target(ProviderName.BRAVE, 1, "https://x.test/a")
    outcome = _fuse(
        _plan(),
        exa,
        brave,
    )
    before = outcome
    projected = project_search_results(outcome, include_attribution=True)
    assert outcome == before
    assert outcome.provider_batches == (exa, brave)
    assert projected[0].provider is ProviderName.EXA
    assert projected[0].metadata["egress"] == "datacenter"
    assert projected[0].metadata["machine"] == "test-node"
    assert projected[0].metadata["source_kind"] == "web_page"
    assert projected[0].metadata["observed_at"] == NOW.isoformat()
    assert math.isclose(sum(projected[0].score_attribution.values()), projected[0].score, abs_tol=1e-15)
    with pytest.raises(FrozenInstanceError):
        outcome.reason = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("mode", [SearchMode.DISCOVERY, SearchMode.GROUNDING, SearchMode.RECOVERY])
def test_non_research_modes_preserve_exact_base_order(mode):
    rows = tuple(
        _observation(ProviderName.EXA, rank, f"https://same.test/{rank}")
        for rank in range(4)
    )
    outcome = _fuse(_plan(mode=mode, limit=4), _batch(ProviderName.EXA, rows))
    assert [c.base_rank for c in outcome.ranked_result_clusters] == [0, 1, 2, 3]


def test_research_runs_coverage_fill_and_relax_with_pinned_psl_site_keys():
    rows = (
        _observation(ProviderName.EXA, 0, "https://a.news.example.co.uk/0"),
        _observation(ProviderName.EXA, 1, "https://b.example.co.uk/1"),
        _observation(ProviderName.EXA, 2, "https://c.example.co.uk/2"),
        _observation(ProviderName.EXA, 3, "https://other.test/3"),
        _observation(ProviderName.EXA, 4, "https://c.example.co.uk/4"),
    )
    outcome = _fuse(_plan(mode=SearchMode.RESEARCH, limit=5), _batch(ProviderName.EXA, rows))
    assert [c.representative_url for c in outcome.ranked_result_clusters] == [
        "https://a.news.example.co.uk/0",
        "https://other.test/3",
        "https://b.example.co.uk/1",
        "https://c.example.co.uk/2",
        "https://c.example.co.uk/4",
    ]
    assert outcome.ranked_result_clusters[0].site_key == "example.co.uk"
    assert [item.reason for item in outcome.site_diversity_trace.selections] == [
        "coverage",
        "coverage",
        "fill",
        "relax",
        "relax",
    ]


def test_pinned_psl_includes_private_suffix_rules():
    rows = (
        _observation(ProviderName.EXA, 0, "https://a.foo.github.io/0"),
        _observation(ProviderName.EXA, 1, "https://b.foo.github.io/1"),
        _observation(ProviderName.EXA, 2, "https://c.bar.github.io/2"),
    )
    outcome = _fuse(
        _plan(mode=SearchMode.RESEARCH, limit=3),
        _batch(ProviderName.EXA, rows),
    )
    assert {cluster.site_key for cluster in outcome.ranked_result_clusters} == {
        "foo.github.io",
        "bar.github.io",
    }


@pytest.mark.parametrize(
    ("limit", "expected_clusters", "expected_sites"),
    [(1, 1, 1), (2, 2, 2), (3, 3, 2), (10, 3, 2)],
)
def test_research_floor_scales_to_result_limit(limit, expected_clusters, expected_sites):
    rows = tuple(
        _observation(ProviderName.EXA, rank, f"https://site{rank}.test/{rank}")
        for rank in range(min(limit, 3))
    )
    outcome = _fuse(_plan(mode=SearchMode.RESEARCH, limit=limit), _batch(ProviderName.EXA, rows))
    assert outcome.evidence_floor_trace.required_clusters == expected_clusters
    assert outcome.evidence_floor_trace.required_sites == expected_sites
    assert outcome.evidence_floor_trace.passed is True


def test_research_floor_failure_is_visible_not_provider_failure():
    outcome = _fuse(
        _plan(mode=SearchMode.RESEARCH, limit=3),
        _batch(ProviderName.EXA, (_observation(ProviderName.EXA, 0, "https://only.test/1"),)),
    )
    assert outcome.outcome is CanonicalOutcome.EMPTY
    assert outcome.reason == "research_structural_floor_unmet"


def test_bounds_are_checked_before_any_fusion_phase():
    policy = FusionPolicy(max_total_observations=1)
    rows = (
        _observation(ProviderName.EXA, 0, "https://x.test/0"),
        _observation(ProviderName.EXA, 1, "https://x.test/1"),
    )
    outcome = _fuse(_plan(), _batch(ProviderName.EXA, rows), policy=policy)
    assert outcome.outcome is CanonicalOutcome.INVALID_REQUEST
    assert outcome.reason == "fusion_input_bound_exceeded"
    assert outcome.completed_phases == ()


@pytest.mark.parametrize("expiry_call", range(1, 9))
def test_deadline_is_checked_before_and_after_every_bounded_phase(expiry_call):
    calls = 0

    def monotonic():
        nonlocal calls
        calls += 1
        return 11.0 if calls >= expiry_call else 9.0

    outcome = _fuse(
        _plan(),
        _batch(ProviderName.EXA, (_observation(ProviderName.EXA, 0, "https://x.test/a"),)),
        policy=FusionPolicy(operation_deadline=10.0, monotonic=monotonic),
    )
    assert outcome.outcome is CanonicalOutcome.TIMEOUT
    assert outcome.reason == "fusion_deadline_expired"
    assert len(outcome.completed_phases) == max(0, (expiry_call - 1) // 2)


def test_fusion_module_has_no_network_import_or_async_function():
    path = Path(__file__).parents[1] / "argus" / "broker" / "fusion.py"
    tree = ast.parse(path.read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    assert imports.isdisjoint({"asyncio", "httpx", "requests", "aiohttp"})
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))
