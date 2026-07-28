"""Exact, bounded evidence-fusion contract from ADR 0003."""

from __future__ import annotations

import ast
import itertools
import math
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path

import pytest

from argus.broker.fusion import (
    PSL_DOMAIN_POLICY_VERSION,
    PSL_SNAPSHOT_ID,
    PSL_SNAPSHOT_SHA256,
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
    TemporalClaimKind,
    TranslationPrecision,
    EvidenceKind,
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
            domain_policy_version=PSL_DOMAIN_POLICY_VERSION,
            ranking_policy_version="ranking-v1",
        ),
        NOW,
    )


def _publication(
    *,
    when: datetime | None = None,
    day: date | None = date(2026, 7, 27),
    precision: PublicationPrecision | None = None,
    source: PublicationSource = PublicationSource.PROVIDER_FIELD,
    confidence: ContractConfidence = ContractConfidence.OFFICIAL_CONTRACT,
    field: str = "published_at",
    reference: str | None = "exa-search-contract",
    parser_version: str | None = "iso8601-v1",
    claim_kind: TemporalClaimKind = TemporalClaimKind.PUBLISHED,
) -> PublicationEvidence:
    return PublicationEvidence(
        published_at_utc=when,
        published_date=None if when is not None else day,
        precision=precision
        or (
            PublicationPrecision.TIMESTAMP
            if when is not None
            else PublicationPrecision.DATE
        ),
        source=source,
        contract_confidence=confidence,
        raw_field_name=field,
        semantic_contract_ref=reference,
        parser_version=parser_version,
        claim_kind=claim_kind,
    )


def _observation(
    provider: ProviderName,
    rank: int,
    url: str,
    *,
    title: str | None = None,
    snippet: str | None = None,
    publication: PublicationEvidence | None = None,
    source_kind: EvidenceKind = EvidenceKind.WEB_PAGE,
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
        source_kind=source_kind,
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
        _batch(ProviderName.PARALLEL, (_observation(ProviderName.PARALLEL, 0, "https://x.test/a", publication=_publication(day=date(2026, 7, 20), reference="parallel-search-contract")),)),
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


@pytest.mark.parametrize(
    ("claim_kind", "field"),
    [
        (TemporalClaimKind.MODIFIED, "lastModified"),
        (TemporalClaimKind.INDEXED, "indexedAt"),
        (TemporalClaimKind.CREATED, "dateCreated"),
        (TemporalClaimKind.CRAWLED, "lastCrawled"),
    ],
)
def test_only_explicit_typed_published_claims_can_prove_freshness(
    claim_kind,
    field,
):
    plan = _plan(freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY))
    outcome = _fuse(
        plan,
        _batch(
            ProviderName.EXA,
            (
                _observation(
                    ProviderName.EXA,
                    0,
                    "https://x.test/a",
                    publication=_publication(claim_kind=claim_kind, field=field),
                ),
            ),
        ),
    )
    assert outcome.ranked_result_clusters == ()
    assert outcome.filtered_observations[0].reason == "freshness_unproven"


@pytest.mark.parametrize(
    ("plan_version", "contract_version", "reference", "parser_version"),
    [
        ("unknown-policy", "2026-07-27-v1", "exa-search-contract", "iso8601-v1"),
        ("freshness-v1", "unknown-contract", "exa-search-contract", "iso8601-v1"),
        ("freshness-v1", "2026-07-27-v1", "unknown-ref", "iso8601-v1"),
        ("freshness-v1", "2026-07-27-v1", "exa-search-contract", "unknown-parser"),
    ],
)
def test_freshness_registry_rejects_unknown_policy_contract_reference_or_parser(
    plan_version,
    contract_version,
    reference,
    parser_version,
):
    base = _plan(
        freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY)
    )
    plan = replace(base, freshness_policy_version=plan_version)
    claim = _publication(
        reference=reference,
        parser_version=parser_version,
    )
    batch = replace(
        _batch(
            ProviderName.EXA,
            (
                _observation(
                    ProviderName.EXA,
                    0,
                    "https://x.test/a",
                    publication=claim,
                ),
            ),
        ),
        provider_contract_version=contract_version,
    )
    outcome = _fuse(plan, batch)
    assert outcome.ranked_result_clusters == ()
    assert outcome.filtered_observations[0].reason == "freshness_unproven"


def test_temporal_claim_kind_and_parser_version_are_closed_and_bounded():
    with pytest.raises(TypeError):
        replace(_publication(), claim_kind="published")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        replace(_publication(), parser_version="x" * 129)


def test_fusion_rejects_authorized_unknown_precision_with_forged_timestamp():
    publication = object.__new__(PublicationEvidence)
    values = {
        "published_at_utc": NOW,
        "published_date": None,
        "precision": PublicationPrecision.UNKNOWN,
        "source": PublicationSource.PROVIDER_FIELD,
        "contract_confidence": ContractConfidence.OFFICIAL_CONTRACT,
        "raw_field_name": "publishedAt",
        "semantic_contract_ref": "exa-search-contract",
        "parser_version": "iso8601-v1",
        "claim_kind": TemporalClaimKind.PUBLISHED,
    }
    for name, value in values.items():
        object.__setattr__(publication, name, value)
    plan = _plan(
        freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY)
    )
    batch = _batch(
        ProviderName.EXA,
        (
            _observation(
                ProviderName.EXA,
                0,
                "https://x.test/forged",
                publication=publication,
            ),
        ),
    )

    outcome = _fuse(plan, batch)

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


def _exact_empty_translation(
    *,
    requested_relative: str = "day",
    resolved_start: date = date(2026, 7, 27),
    resolved_end: date = date(2026, 7, 27),
    applied_start: date = date(2026, 7, 27),
    applied_end: date = date(2026, 7, 27),
    contract_ref: str = "successful-empty-v1",
) -> ControlTranslation:
    return ControlTranslation(
        "date_range",
        TranslationPrecision.EXACT,
        FilterStrength.STRICT_CONTRACT,
        "start_date/end_date",
        "2026-07-27/2026-07-27",
        requested_relative=requested_relative,
        resolved_start_date=resolved_start,
        resolved_end_date=resolved_end,
        applied_start_date=applied_start,
        applied_end_date=applied_end,
        successful_empty_contract_ref=contract_ref,
    )


def test_strict_exact_recognized_empty_is_proven_but_other_empty_is_unproven():
    exact = _exact_empty_translation()
    plan = _plan(freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY))
    strict = _fuse(plan, _batch(ProviderName.EXA, translation=exact, empty=True))
    weak = _fuse(plan, _batch(ProviderName.EXA))
    assert strict.outcome is CanonicalOutcome.EMPTY
    assert strict.reason == "strict_empty"
    assert weak.outcome is CanonicalOutcome.EMPTY
    assert weak.reason == "freshness_unproven"


@pytest.mark.parametrize(
    "translation",
    [
        _exact_empty_translation(
            applied_start=date(1999, 1, 1),
            applied_end=date(1999, 1, 2),
        ),
        _exact_empty_translation(resolved_start=date(1999, 1, 1)),
        _exact_empty_translation(requested_relative="week"),
        _exact_empty_translation(contract_ref="unknown-empty-contract"),
    ],
)
def test_strict_empty_rejects_mismatched_windows_and_unknown_contract(translation):
    plan = _plan(freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY))
    outcome = _fuse(
        plan,
        _batch(ProviderName.EXA, translation=translation, empty=True),
    )
    assert outcome.outcome is CanonicalOutcome.EMPTY
    assert outcome.reason == "freshness_unproven"


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


def test_provider_evidence_preserves_key_only_and_empty_query_grammar_end_to_end():
    rows = (
        _observation(ProviderName.EXA, 0, "https://example.test/a?flag"),
        _observation(ProviderName.EXA, 1, "https://example.test/a?flag="),
    )
    batch = _batch(ProviderName.EXA, rows)
    assert [item.url for item in batch.observations] == [
        "https://example.test/a?flag",
        "https://example.test/a?flag=",
    ]

    outcome = _fuse(_plan(), batch)

    assert len(outcome.ranked_result_clusters) == 2
    assert {item.cluster_sort_key for item in outcome.ranked_result_clusters} == {
        "https://example.test/a?flag",
        "https://example.test/a?flag=",
    }


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


def test_ranking_trace_records_every_eligible_cluster_and_complete_ordering_values():
    rows = tuple(
        _observation(ProviderName.EXA, rank, f"https://site{rank}.test/{rank}")
        for rank in range(4)
    )
    outcome = _fuse(
        _plan(limit=2),
        _batch(ProviderName.EXA, rows),
    )
    assert len(outcome.ranked_result_clusters) == 2
    assert len(outcome.ranking_trace.records) == 4
    for index, record in enumerate(outcome.ranking_trace.records):
        assert record.base_rank == index
        assert record.score_numerator == 1
        assert record.score_denominator == 61 + index
        assert record.best_provider_rank == index
        assert record.contributor_count == 1
        assert record.smallest_provider is ProviderName.EXA
        assert record.cluster_sort_key == f"https://site{index}.test/{index}"
        assert record.contributions == (
            RRFContribution(ProviderName.EXA, index, 1, 61 + index),
        )


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


def test_computed_answer_is_typed_separate_and_satisfies_only_grounding_floor():
    answer = _observation(
        ProviderName.WOLFRAM,
        0,
        "https://www.wolframalpha.com/input?i=2%2B2",
        title="2 + 2",
        snippet="4",
        source_kind=EvidenceKind.COMPUTED_ANSWER,
    )
    grounding = _fuse(
        _plan(mode=SearchMode.GROUNDING),
        _batch(ProviderName.WOLFRAM, (answer,)),
    )
    discovery_answer = _observation(
        ProviderName.EXA,
        0,
        "https://example.test/computed",
        title="computed",
        snippet="answer",
        source_kind=EvidenceKind.COMPUTED_ANSWER,
    )
    discovery = _fuse(
        _plan(mode=SearchMode.DISCOVERY),
        _batch(ProviderName.EXA, (discovery_answer,)),
    )

    assert grounding.outcome is CanonicalOutcome.SUCCESS
    assert grounding.ranked_result_clusters == ()
    assert len(grounding.computed_answers) == 1
    assert grounding.computed_answers[0].eligible_for_grounding is True
    assert grounding.evidence_floor_trace.eligible_computed_answers == 1
    assert grounding.evidence_floor_trace.passed is True
    assert discovery.outcome is CanonicalOutcome.EMPTY
    assert discovery.computed_answers[0].eligible_for_grounding is False
    assert project_search_results(grounding) == []
    with pytest.raises(FrozenInstanceError):
        grounding.computed_answers[0].disposition = "changed"  # type: ignore[misc]


def test_freshness_scoped_computed_answer_does_not_satisfy_grounding_floor():
    answer = _observation(
        ProviderName.WOLFRAM,
        0,
        "https://www.wolframalpha.com/input?i=weather",
        snippet="answer",
        source_kind=EvidenceKind.COMPUTED_ANSWER,
    )
    outcome = _fuse(
        _plan(
            mode=SearchMode.GROUNDING,
            freshness=FreshnessWindow(requested_relative=FreshnessRelative.DAY),
        ),
        _batch(ProviderName.WOLFRAM, (answer,)),
    )
    assert outcome.outcome is CanonicalOutcome.EMPTY
    assert outcome.reason == "freshness_unproven"
    assert outcome.computed_answers[0].disposition == "freshness_ineligible"


@pytest.mark.parametrize("mode", [SearchMode.DISCOVERY, SearchMode.GROUNDING, SearchMode.RECOVERY])
def test_non_research_modes_preserve_exact_base_order(mode):
    rows = tuple(
        _observation(ProviderName.BRAVE, rank, f"https://same.test/{rank}")
        for rank in range(4)
    )
    outcome = _fuse(_plan(mode=mode, limit=4), _batch(ProviderName.BRAVE, rows))
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
    decisions = {
        item.cluster_sort_key: item
        for item in outcome.site_diversity_trace.decisions
    }
    assert len(decisions) == 5
    assert decisions["https://c.example.co.uk/2"].events == (
        "defer_soft_cap",
        "backfill_relaxed",
    )
    assert decisions["https://c.example.co.uk/2"].output_rank == 3
    assert all(item.selected_site_count_after >= 1 for item in decisions.values())
    assert outcome.site_diversity_trace.passed is True
    assert outcome.site_diversity_trace.disposition == "floor_passed"


def test_diversity_trace_records_candidates_omitted_by_result_limit():
    rows = tuple(
        _observation(ProviderName.EXA, rank, f"https://site{rank}.test/{rank}")
        for rank in range(5)
    )
    outcome = _fuse(
        _plan(mode=SearchMode.RESEARCH, limit=3),
        _batch(ProviderName.EXA, rows),
    )
    assert len(outcome.site_diversity_trace.decisions) == 5
    omitted = [
        item
        for item in outcome.site_diversity_trace.decisions
        if item.output_rank is None
    ]
    assert [item.base_rank for item in omitted] == [3, 4]
    assert all(item.events[-1] == "omit_result_limit" for item in omitted)


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
    assert outcome.site_diversity_trace.psl_snapshot == PSL_SNAPSHOT_ID
    assert (
        outcome.site_diversity_trace.psl_snapshot_sha256
        == PSL_SNAPSHOT_SHA256
    )


def test_domain_policy_must_bind_the_exact_pinned_psl_snapshot():
    plan = replace(_plan(), domain_policy_version="domain-v1-unpinned")
    outcome = _fuse(
        plan,
        _batch(
            ProviderName.EXA,
            (_observation(ProviderName.EXA, 0, "https://example.test/a"),),
        ),
    )
    assert outcome.outcome is CanonicalOutcome.INVALID_REQUEST
    assert outcome.reason == "domain_policy_unrecognized"
    assert outcome.completed_phases == ()


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


@pytest.mark.parametrize(
    ("mode", "batch"),
    [
        (
            SearchMode.GROUNDING,
            _batch(
                ProviderName.EXA,
                (
                    _observation(
                        ProviderName.EXA,
                        0,
                        "https://excluded.test/url",
                    ),
                ),
            ),
        ),
        (
            SearchMode.GROUNDING,
            replace(
                _batch(ProviderName.EXA),
                failure=ProviderFailure(
                    FailureCategory.AUTHENTICATION_REJECTED,
                    ProviderName.EXA,
                    observed_at=NOW,
                ),
            ),
        ),
        (SearchMode.GROUNDING, _batch(ProviderName.EXA, empty=True)),
        (
            SearchMode.DISCOVERY,
            _batch(
                ProviderName.WOLFRAM,
                (
                    _observation(
                        ProviderName.WOLFRAM,
                        0,
                        "https://wolfram.test/answer",
                        source_kind=EvidenceKind.COMPUTED_ANSWER,
                    ),
                ),
            ),
        ),
        (SearchMode.DISCOVERY, _batch(ProviderName.CACHE, empty=True)),
    ],
)
def test_unplanned_and_cache_batches_are_rejected_before_all_fusion_phases(
    mode,
    batch,
):
    outcome = _fuse(_plan(mode=mode), batch)

    assert outcome.outcome is CanonicalOutcome.INVALID_REQUEST
    assert outcome.reason == "fusion_provider_not_planned"
    assert outcome.completed_phases == ()
    assert outcome.provider_batches == ()
    assert outcome.filtered_observations == ()
    assert outcome.computed_answers == ()
    assert outcome.ranking_trace.records == ()
    assert outcome.site_diversity_trace.decisions == ()


def test_duplicate_provider_batches_are_rejected_with_stable_identity_error():
    outcome = _fuse(
        _plan(),
        _batch(ProviderName.EXA),
        _batch(ProviderName.EXA),
    )

    assert outcome.outcome is CanonicalOutcome.INVALID_REQUEST
    assert outcome.reason == "fusion_duplicate_provider_batch"
    assert outcome.completed_phases == ()
    assert outcome.provider_batches == ()


def test_forged_batch_provider_identity_mismatch_is_rejected_before_phases():
    batch = _batch(ProviderName.EXA)
    object.__setattr__(
        batch,
        "observations",
        (
            _observation(
                ProviderName.BRAVE,
                0,
                "https://example.test/mismatch",
            ),
        ),
    )

    outcome = _fuse(_plan(), batch)

    assert outcome.outcome is CanonicalOutcome.INVALID_REQUEST
    assert outcome.reason == "fusion_batch_identity_mismatch"
    assert outcome.completed_phases == ()
    assert outcome.provider_batches == ()


@pytest.mark.parametrize(
    "policy",
    [
        FusionPolicy(max_provider_batches=15),
        FusionPolicy(max_observations_per_provider=51),
        FusionPolicy(max_total_observations=701),
        FusionPolicy(operation_deadline=math.nan),
        FusionPolicy(operation_deadline=math.inf),
    ],
)
def test_hard_ceilings_and_finite_deadline_cannot_be_loosened(policy):
    outcome = _fuse(_plan(), policy=policy)
    assert outcome.outcome is CanonicalOutcome.INVALID_REQUEST
    assert outcome.reason == "fusion_policy_invalid"
    assert outcome.completed_phases == ()


def test_non_finite_monotonic_sample_fails_closed_as_timeout():
    outcome = _fuse(
        _plan(),
        _batch(
            ProviderName.EXA,
            (_observation(ProviderName.EXA, 0, "https://x.test/a"),),
        ),
        policy=FusionPolicy(
            operation_deadline=10.0,
            monotonic=lambda: math.nan,
        ),
    )
    assert outcome.outcome is CanonicalOutcome.TIMEOUT
    assert outcome.reason == "fusion_clock_invalid"
    assert outcome.completed_phases == ()


def test_activatable_path_requires_deadline_and_positive_final_reserve():
    missing = _fuse(_plan(), policy=FusionPolicy(activatable=True))
    no_reserve = _fuse(
        _plan(),
        policy=FusionPolicy(
            activatable=True,
            operation_deadline=10.0,
            publication_reserve_seconds=0.0,
        ),
    )
    assert (missing.outcome, missing.reason) == (
        CanonicalOutcome.INVALID_REQUEST,
        "fusion_policy_invalid",
    )
    assert (no_reserve.outcome, no_reserve.reason) == (
        CanonicalOutcome.INVALID_REQUEST,
        "fusion_policy_invalid",
    )


def test_activatable_deadline_preserves_final_publication_reserve():
    outcome = _fuse(
        _plan(),
        _batch(
            ProviderName.EXA,
            (_observation(ProviderName.EXA, 0, "https://x.test/a"),),
        ),
        policy=FusionPolicy(
            activatable=True,
            operation_deadline=10.0,
            publication_reserve_seconds=1.0,
            monotonic=lambda: 9.0,
        ),
    )
    assert outcome.outcome is CanonicalOutcome.TIMEOUT
    assert outcome.reason == "fusion_deadline_expired"
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
    assert len(outcome.completed_phases) == expiry_call // 2


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
