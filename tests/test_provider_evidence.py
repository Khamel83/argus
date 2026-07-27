"""Hermetic contracts for provider evidence normalization."""

from __future__ import annotations

import asyncio
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from argus.broker.planning import FreshnessWindow
from argus.broker.provider_evidence import (
    ContractConfidence,
    EvidenceKind,
    FailureCategory,
    FilterStrength,
    LegacyProviderBatchAdapter,
    NativeScoreEvidence,
    NativeScoreSemantics,
    ProviderFailure,
    ProviderRequestEvidence,
    ProviderResponseEvidence,
    ProviderSearchBatch,
    PublicationEvidence,
    QueryRelation,
    ResultObservation,
    SnippetEvidence,
    SnippetKind,
    TranslationPrecision,
    attempt_timeout_seconds,
    classify_http_failure,
    normalize_provider_response,
    safe_redirect_request,
)
from argus.models import ProviderName, ProviderTrace, SearchResult
from argus.models import SearchQuery
from argus.config import ProviderConfig
from argus.provider_controls import (
    PROVIDER_CONTROL_CAPABILITIES,
    FreshnessControlCapability,
    RequiredControlUnsupported,
    translate_freshness,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "providers"
FAILURE_CLASSES = {"401", "402", "403", "408_504", "422", "429", "5xx"}
REGISTERED = {provider.value for provider in ProviderName if provider is not ProviderName.CACHE}


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_covers_every_registered_provider_and_contract_case():
    manifest = _load(FIXTURE_ROOT / "manifest.json")
    assert set(manifest["providers"]) == REGISTERED
    assert manifest["migration_aliases"] == {
        "exa": ["published_date"],
        "parallel": ["excerpt", "snippet"],
        "searchapi": ["organic"],
    }
    for provider, entry in manifest["providers"].items():
        assert set(entry["fixtures"]) == {
            "success",
            "empty",
            "malformed",
            "usage_rate",
            "freshness",
        }
        for fixture in entry["fixtures"].values():
            assert (FIXTURE_ROOT / provider / fixture).is_file()
        assert set(entry["failures"]) == FAILURE_CLASSES
        for failure in entry["failures"].values():
            assert set(failure) in ({"fixture"}, {"reason"})
            if "fixture" in failure:
                assert (FIXTURE_ROOT / provider / failure["fixture"]).is_file()
            else:
                assert len(failure["reason"]) >= 24


@pytest.mark.parametrize("provider_name", sorted(REGISTERED))
def test_success_empty_and_malformed_fixtures_cross_one_typed_seam(provider_name):
    manifest = _load(FIXTURE_ROOT / "manifest.json")
    fixtures = manifest["providers"][provider_name]["fixtures"]
    provider = ProviderName(provider_name)

    success = normalize_provider_response(
        provider,
        _load(FIXTURE_ROOT / provider_name / fixtures["success"]),
        max_results=10,
    )
    empty = normalize_provider_response(
        provider,
        _load(FIXTURE_ROOT / provider_name / fixtures["empty"]),
        max_results=10,
    )
    malformed = normalize_provider_response(
        provider,
        _load(FIXTURE_ROOT / provider_name / fixtures["malformed"]),
        max_results=10,
    )

    assert isinstance(success, ProviderSearchBatch)
    assert success.provider is provider
    assert success.observations or provider is ProviderName.WOLFRAM
    assert [item.provider_rank for item in success.observations] == list(
        range(len(success.observations))
    )
    assert empty.observations == ()
    assert empty.failure is not None
    assert empty.failure.category is FailureCategory.EMPTY
    assert malformed.failure is not None
    assert malformed.failure.category is FailureCategory.PARSE_ERROR


@pytest.mark.parametrize(
    ("provider", "field", "expected"),
    [
        (ProviderName.TAVILY, "request_id", "tavily-usage"),
        (ProviderName.EXA, "request_id", "exa-usage"),
        (ProviderName.PARALLEL, "request_id", "parallel-usage"),
        (ProviderName.YOU, "request_id", "you-usage"),
        (ProviderName.VALYU, "transaction_id", "valyu-usage"),
        (ProviderName.SEARCHAPI, "request_id", "searchapi-usage"),
    ],
)
def test_usage_rate_fixtures_retain_only_typed_response_evidence(
    provider, field, expected
):
    fixture = _load(FIXTURE_ROOT / provider.value / "usage_rate.json")
    batch = normalize_provider_response(provider, fixture, max_results=10)
    assert getattr(batch.response_evidence, field) == expected


@pytest.mark.parametrize(
    "provider",
    [
        ProviderName.SEARXNG,
        ProviderName.TAVILY,
        ProviderName.EXA,
        ProviderName.PARALLEL,
        ProviderName.VALYU,
    ],
)
def test_documented_publication_fixtures_produce_typed_evidence(provider):
    fixture = _load(FIXTURE_ROOT / provider.value / "freshness.json")
    batch = normalize_provider_response(provider, fixture, max_results=10)
    assert batch.observations[0].publication is not None


def test_only_inventory_authorized_migration_aliases_are_dual_read():
    exa = normalize_provider_response(
        ProviderName.EXA,
        {
            "results": [
                {
                    "url": "https://example.test/exa-alias",
                    "title": "alias",
                    "text": "fixture",
                    "published_date": "2026-07-27",
                }
            ]
        },
        max_results=1,
    )
    parallel = normalize_provider_response(
        ProviderName.PARALLEL,
        {
            "results": [
                {
                    "url": "https://example.test/parallel-alias",
                    "title": "alias",
                    "excerpt": "legacy excerpt",
                }
            ]
        },
        max_results=1,
    )
    searchapi = normalize_provider_response(
        ProviderName.SEARCHAPI,
        {
            "organic": [
                {
                    "link": "https://example.test/searchapi-alias",
                    "title": "alias",
                    "snippet": "legacy organic",
                }
            ]
        },
        max_results=1,
    )
    unapproved = normalize_provider_response(
        ProviderName.SEARXNG,
        {
            "results": [
                {
                    "url": "https://example.test/unapproved",
                    "title": "unapproved",
                    "content": "fixture",
                    "published_date": "2026-07-27",
                }
            ]
        },
        max_results=1,
    )
    assert exa.observations[0].publication is not None
    assert parallel.observations[0].snippet.primary_text == "legacy excerpt"
    assert searchapi.observations[0].url.endswith("/searchapi-alias")
    assert unapproved.observations[0].publication is None


def test_private_sentinels_are_scrubbed_and_all_projection_values_are_bounded():
    secret = "do-not-cross"
    response = ProviderResponseEvidence(
        request_id="r" * 129,
        warnings=(
            f"Authorization: Bearer {secret}",
            f"Cookie: session={secret}",
            f"/Users/person/private/{secret}",
            "w" * 400,
            "ok",
            "sixth",
        ),
        usage_count=7,
        cost_usd=0.25,
        rate_limit_reset=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    observation = ResultObservation(
        provider=ProviderName.EXA,
        provider_rank=0,
        url=f"https://example.test/article?signature={secret}&safe=yes",
        title="t" * 1_200,
        snippet=SnippetEvidence(
            primary_text="s" * 2_400,
            kind=SnippetKind.PROVIDER_HIGHLIGHT,
            highlights=("h" * 700,) * 5,
        ),
        source_kind=EvidenceKind.UNKNOWN,
        provider_source_type="mystery",
        publication=PublicationEvidence.from_raw(
            "not-a-date",
            raw_field_name="publishedDate",
            confidence=ContractConfidence.OFFICIAL_CONTRACT,
        ),
        native_score=NativeScoreEvidence.from_value(
            math.nan,
            semantics=NativeScoreSemantics.RELEVANCE,
        ),
    )
    batch = ProviderSearchBatch(
        provider=ProviderName.EXA,
        provider_contract_version="2026-07-27",
        request_evidence=ProviderRequestEvidence(
            effective_query_hash="a" * 64,
            query_relation=QueryRelation.EXACT,
        ),
        response_evidence=response,
        observations=(observation,),
    )
    rendered = json.dumps(batch.safe_log_record(), sort_keys=True)

    assert secret not in rendered
    assert "/Users/person" not in rendered
    assert len(batch.response_evidence.warnings) == 3
    assert all(len(item) <= 256 for item in batch.response_evidence.warnings)
    assert batch.response_evidence.request_id is None
    assert len(batch.observations[0].title) == 1_000
    assert batch.observations[0].snippet.truncated is True
    assert len(batch.observations[0].snippet.highlights) == 3
    assert "signature" not in batch.observations[0].url
    assert batch.observations[0].native_score is None
    assert batch.observations[0].publication is None
    assert batch.observations[0].source_kind is EvidenceKind.UNKNOWN


def test_batch_rejects_duplicate_or_noncontiguous_provider_positions():
    def item(rank: int) -> ResultObservation:
        return ResultObservation(
            provider=ProviderName.BRAVE,
            provider_rank=rank,
            url=f"https://example.test/{rank}",
            title="title",
            snippet=SnippetEvidence("snippet", SnippetKind.PROVIDER_DESCRIPTION),
        )

    with pytest.raises(ValueError, match="unique zero-based"):
        ProviderSearchBatch(
            provider=ProviderName.BRAVE,
            provider_contract_version="v1",
            observations=(item(0), item(0)),
        )
    with pytest.raises(ValueError, match="unique zero-based"):
        ProviderSearchBatch(
            provider=ProviderName.BRAVE,
            provider_contract_version="v1",
            observations=(item(1),),
        )


def test_duplicate_provider_native_positions_drop_only_diagnostic_field():
    items = tuple(
        ResultObservation(
            provider=ProviderName.SERPER,
            provider_rank=rank,
            provider_position=1,
            url=f"https://example.test/{rank}",
            title="title",
            snippet=SnippetEvidence("snippet", SnippetKind.PROVIDER_SNIPPET),
        )
        for rank in range(2)
    )
    batch = ProviderSearchBatch(
        provider=ProviderName.SERPER,
        provider_contract_version="v1",
        observations=items,
    )
    assert [item.provider_rank for item in batch.observations] == [0, 1]
    assert [item.provider_position for item in batch.observations] == [None, None]


def test_failure_classification_is_typed_bounded_and_never_serializes_raw_inputs():
    failure = classify_http_failure(
        ProviderName.EXA,
        402,
        provider_code="PAYMENT_REQUIRED",
        request_id="request-7",
        summary="credit balance exhausted",
        raw_body='{"api_key":"do-not-cross"}',
        request_url="https://api.exa.ai/search?api_key=do-not-cross",
        headers={"Authorization": "Bearer do-not-cross"},
    )
    assert failure.category is FailureCategory.BALANCE_EXHAUSTED
    assert failure.http_status == 402
    rendered = json.dumps(failure.safe_log_record(), sort_keys=True)
    assert "do-not-cross" not in rendered
    assert "api_key" not in rendered


def test_legacy_tuple_adapter_marks_missing_evidence_explicitly():
    result = SearchResult(
        url="https://example.test",
        title="Example",
        snippet="Legacy",
        provider=ProviderName.BRAVE,
        raw_rank=9,
    )
    trace = ProviderTrace(provider=ProviderName.BRAVE, status="success")
    batch = LegacyProviderBatchAdapter.from_legacy(([result], trace))

    assert isinstance(batch, ProviderSearchBatch)
    assert batch.observations[0].provider_rank == 0
    assert batch.response_evidence.evidence_missing is True


def test_deadline_timeout_is_exact_and_refuses_expired_phase():
    assert attempt_timeout_seconds(
        configured_timeout=15.0,
        provider_phase_deadline=110.0,
        monotonic=lambda: 100.0,
    ) == 10.0
    assert attempt_timeout_seconds(
        configured_timeout=5.0,
        provider_phase_deadline=110.0,
        monotonic=lambda: 100.0,
    ) == 5.0
    with pytest.raises(TimeoutError, match="deadline"):
        attempt_timeout_seconds(
            configured_timeout=5.0,
            provider_phase_deadline=100.0,
            monotonic=lambda: 100.0,
        )


@pytest.mark.asyncio
async def test_in_flight_attempt_is_cancelled_at_phase_deadline():
    cancelled = asyncio.Event()

    async def operation():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    from argus.broker.provider_evidence import run_with_attempt_deadline

    with pytest.raises(TimeoutError, match="deadline"):
        await run_with_attempt_deadline(
            operation(),
            configured_timeout=0.01,
            provider_phase_deadline=10.0,
            monotonic=lambda: 0.0,
        )
    assert cancelled.is_set()


def test_cross_origin_redirect_strips_credentials_cookies_and_signed_query():
    redirected = safe_redirect_request(
        source_url="https://api.example.test/start?signature=secret",
        destination_url="https://other.test/next?token=secret&ok=yes",
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "Accept": "application/json",
        },
        redirect_count=2,
        max_redirects=3,
    )
    assert redirected.headers == {"Accept": "application/json"}
    assert redirected.url == "https://other.test/next?ok=yes"
    assert redirected.redirect_count == 2
    with pytest.raises(ProviderFailure) as captured:
        safe_redirect_request(
            source_url="https://api.example.test",
            destination_url="https://api.example.test/four",
            headers={},
            redirect_count=4,
            max_redirects=3,
        )
    assert captured.value.category is FailureCategory.POLICY_REJECTED


def test_all_provider_control_capabilities_are_closed_and_required_controls_fail():
    assert set(PROVIDER_CONTROL_CAPABILITIES) == {
        provider for provider in ProviderName if provider is not ProviderName.CACHE
    }
    assert set(PROVIDER_CONTROL_CAPABILITIES.values()) <= set(
        FreshnessControlCapability
    )
    translation = translate_freshness(
        ProviderName.BRAVE,
        FreshnessWindow(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 27),
        ),
        required=True,
    )
    assert translation.precision is TranslationPrecision.EXACT
    assert translation.strength is FilterStrength.STRICT_CONTRACT
    with pytest.raises(RequiredControlUnsupported):
        translate_freshness(
            ProviderName.SERPER,
            FreshnessWindow(start_date=date(2026, 7, 1)),
            required=True,
        )


@pytest.mark.asyncio
async def test_exa_uses_current_casing_bounded_contents_and_phase_timeout():
    from argus.providers.exa import EXA_API_BASE, ExaProvider

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"results": []}
    query = SearchQuery(
        query="current contract",
        metadata={
            "_freshness_window": FreshnessWindow(
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 27),
            ),
            "_provider_phase_deadline": 110.0,
            "_monotonic": lambda: 100.0,
        },
    )
    with patch("argus.providers.exa.httpx.AsyncClient") as client_type:
        client = client_type.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=response)
        batch = await ExaProvider(
            ProviderConfig(enabled=True, api_key="fixture", timeout_seconds=15)
        ).search(query)

    assert isinstance(batch, ProviderSearchBatch)
    client_type.assert_called_once_with(timeout=10.0)
    _, kwargs = client.post.call_args
    assert client.post.call_args.args[0] == EXA_API_BASE
    assert kwargs["json"]["numResults"] == 10
    assert "num_results" not in kwargs["json"]
    assert kwargs["json"]["contents"]["highlights"]["maxCharacters"] == 500
    assert kwargs["json"]["startPublishedDate"] == "2026-07-01"
    assert kwargs["json"]["endPublishedDate"] == "2026-07-27"


@pytest.mark.asyncio
async def test_parallel_uses_v1_and_nested_advanced_settings():
    from argus.providers.parallel import PARALLEL_API_BASE, ParallelProvider

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"results": []}
    with patch("argus.providers.parallel.httpx.AsyncClient") as client_type:
        client = client_type.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=response)
        await ParallelProvider(
            ProviderConfig(enabled=True, api_key="fixture")
        ).search(SearchQuery(query="current contract", max_results=7))

    assert PARALLEL_API_BASE.endswith("/v1/search")
    _, kwargs = client.post.call_args
    assert kwargs["json"]["advanced_settings"]["max_results"] == 7
    assert "parallel-beta" not in kwargs["headers"]
