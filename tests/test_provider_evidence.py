"""Hermetic contracts for provider evidence normalization."""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from argus.broker.planning import FreshnessWindow
from argus.broker.provider_evidence import (
    ContractConfidence,
    ControlTranslation,
    EvidenceKind,
    FailureCategory,
    FilterStrength,
    LegacyProviderBatchAdapter,
    MAX_COST_USD,
    MAX_RATE_LIMIT_REMAINING,
    MAX_RETRY_AFTER_SECONDS,
    MAX_USAGE_COUNT,
    NativeScoreEvidence,
    NativeScoreSemantics,
    ProviderFailure,
    ProviderRequestEvidence,
    ProviderResponseEvidence,
    ProviderSearchBatch,
    PublicationEvidence,
    QueryRelation,
    RateLimitEvidence,
    RedirectChildEvidence,
    ResultObservation,
    SnippetEvidence,
    SnippetKind,
    TranslationPrecision,
    UsageEvidence,
    attempt_timeout_seconds,
    classify_http_failure,
    query_hash,
    safe_redirect_request,
)
from argus.providers.normalization import (
    classify_provider_failure_response,
    normalize_provider_response,
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
REGISTERED = {
    provider.value for provider in ProviderName if provider is not ProviderName.CACHE
}
EXPECTED_MAX_RATE_RESET_AHEAD_SECONDS = 366 * 24 * 60 * 60


@pytest.fixture(autouse=True)
def _prohibit_provider_contract_dns(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("provider contract tests must not use DNS")

    monkeypatch.setattr("socket.getaddrinfo", fail)
    monkeypatch.setattr("socket.create_connection", fail)


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _http_response(
    *,
    status: int = 200,
    body: object | None = None,
    text: str = "",
    headers: dict[str, str] | None = None,
):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    response.text = text
    response.json.return_value = body
    if status >= 400:
        response.raise_for_status.side_effect = __import__("httpx").HTTPStatusError(
            "native failure",
            request=MagicMock(),
            response=response,
        )
    return response


YAHOO_RESULT_HTML = """
<html><body>
  <div class="dd algo-sr">
    <div class="compTitle"><a href="https://example.test/result"><h3>Result</h3></a></div>
    <div class="compText">Fixture snippet</div>
  </div>
</body></html>
"""


@pytest.mark.asyncio
async def test_yahoo_filtered_success_preserves_monotonic_started_at():
    from argus.providers.yahoo import YahooProvider

    response = _http_response(text=YAHOO_RESULT_HTML)
    with patch("argus.providers.yahoo.httpx.AsyncClient") as client_type:
        client = client_type.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=response)
        batch = await YahooProvider(ProviderConfig(enabled=True)).search(
            SearchQuery(
                query="fixture",
                metadata={
                    "_freshness_window": FreshnessWindow(
                        start_date=date(2026, 7, 1),
                        end_date=date(2026, 7, 27),
                    )
                },
            )
        )

    assert batch.failure is None
    assert batch.observations[0].url == "https://example.test/result"
    sent = client.get.call_args_list[0].kwargs["params"]["p"]
    assert sent == "fixture after:2026-07-01 before:2026-07-27"
    assert batch.request_evidence.query_relation is QueryRelation.PROVIDER_REWRITE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "category"),
    [
        (
            _http_response(text="<html><body>consent page</body></html>"),
            FailureCategory.PARSE_ERROR,
        ),
        (_http_response(status=503), FailureCategory.PROVIDER_UNAVAILABLE),
    ],
)
async def test_yahoo_filtered_parse_and_http_failures_are_typed(response, category):
    from argus.providers.yahoo import YahooProvider

    with patch("argus.providers.yahoo.httpx.AsyncClient") as client_type:
        client = client_type.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=response)
        batch = await YahooProvider(ProviderConfig(enabled=True)).search(
            SearchQuery(
                query="fixture",
                metadata={
                    "_freshness_window": FreshnessWindow(start_date=date(2026, 7, 1))
                },
            )
        )

    assert batch.failure is not None
    assert batch.failure.category is category


@pytest.mark.asyncio
async def test_yahoo_redirect_children_strip_cross_origin_secrets_and_recompute_timeout():
    from argus.providers.yahoo import YahooProvider

    first = _http_response(
        status=302,
        headers={"location": "https://other.test/next?signature=do-not-cross&ok=yes"},
    )
    second = _http_response(text=YAHOO_RESULT_HTML)
    ticks = iter((100.0, 100.0, 102.0, 102.0, 102.0))
    with (
        patch(
            "argus.providers.yahoo._HEADERS",
            {
                "Authorization": "Bearer do-not-cross",
                "Cookie": "session=do-not-cross",
                "Accept": "text/html",
            },
        ),
        patch("argus.providers.yahoo.httpx.AsyncClient") as client_type,
    ):
        client = client_type.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=(first, second))
        batch = await YahooProvider(
            ProviderConfig(enabled=True, timeout_seconds=10)
        ).search(
            SearchQuery(
                query="fixture",
                metadata={
                    "_provider_phase_deadline": 105.0,
                    "_monotonic": lambda: next(ticks),
                    "_provider_attempt_id": "attempt-1",
                },
            )
        )

    redirected = client.get.call_args_list[1]
    assert "signature" not in redirected.args[0]
    assert redirected.kwargs["headers"] == {"Accept": "text/html"}
    assert redirected.kwargs["timeout"] == 3.0
    assert len(batch.request_evidence.redirect_children) == 1
    child = batch.request_evidence.redirect_children[0]
    assert child.parent_attempt_id == "attempt-1"
    assert child.child_index == 1
    assert child.cross_origin is True
    assert child.credentials_stripped is True


@pytest.mark.asyncio
async def test_yahoo_redirect_overflow_is_typed_and_trace_is_bounded():
    from argus.providers.yahoo import YahooProvider

    redirects = [
        _http_response(status=302, headers={"location": f"/redirect-{index}"})
        for index in range(4)
    ]
    with patch("argus.providers.yahoo.httpx.AsyncClient") as client_type:
        client = client_type.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=redirects)
        batch = await YahooProvider(ProviderConfig(enabled=True)).search(
            SearchQuery(
                query="fixture",
                metadata={"_provider_attempt_id": "attempt-overflow"},
            )
        )

    assert batch.failure is not None
    assert batch.failure.category is FailureCategory.POLICY_REJECTED
    assert len(batch.request_evidence.redirect_children) == 3
    assert client.get.await_count == 4


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
            if isinstance(fixture, str):
                assert (FIXTURE_ROOT / provider / fixture).is_file()
            else:
                assert set(fixture) == {"reason", "source"}
                assert fixture["source"].startswith(("https://", "argus/"))
                assert "#" in fixture["source"]
        assert set(entry["failures"]) == FAILURE_CLASSES
        for failure in entry["failures"].values():
            assert set(failure) in (
                {"fixture", "source", "shape_source"},
                {"reason", "source"},
            )
            if "fixture" in failure:
                assert (FIXTURE_ROOT / provider / failure["fixture"]).is_file()
                assert failure["shape_source"].startswith(("https://", "argus/"))
            else:
                assert failure["source"].startswith(("https://", "argus/"))
                assert "#" in failure["source"]
        assert set(entry["signals"]) in ({"typed"}, {"reason", "source"})
        if "reason" in entry["signals"]:
            assert entry["signals"]["source"].startswith(("https://", "argus/"))
            assert "#" in entry["signals"]["source"]
        else:
            assert entry["signals"]["typed"]


def test_manifest_failure_declarations_agree_with_cited_authority():
    manifest = _load(FIXTURE_ROOT / "manifest.json")
    for provider_name, entry in manifest["providers"].items():
        for failure_class, declaration in entry["failures"].items():
            source = declaration["source"]
            assert source.startswith(("https://", "argus/"))
            assert "#" in source
            if "fixture" in declaration:
                assert declaration["shape_source"] == source
            else:
                assert provider_name == "duckduckgo"
                assert "cannot emit" in declaration["reason"].lower()


def test_manifest_negative_declarations_cite_primary_impossibility_evidence():
    manifest = _load(FIXTURE_ROOT / "manifest.json")
    matrix = "docs/research/2026-07-27-provider-health-probe-matrix.md"
    for provider_name, entry in manifest["providers"].items():
        for failure_class, declaration in entry["failures"].items():
            if "reason" not in declaration:
                continue
            assert not declaration["source"].startswith(matrix), (
                provider_name,
                failure_class,
            )
            assert declaration["source"].startswith(("https://", "argus/"))
            assert "#" in declaration["source"]
            reason = declaration["reason"].lower()
            assert any(
                phrase in reason
                for phrase in (
                    "does not expose",
                    "cannot emit",
                    "raises only",
                    "not an http provider",
                )
            )


def test_manifest_declares_common_rate_and_timeout_cases_for_every_adapter():
    manifest = _load(FIXTURE_ROOT / "manifest.json")
    for provider_name, entry in manifest["providers"].items():
        assert set(entry["common_cases"]) == {
            "rate_limit_with_metadata",
            "rate_limit_without_metadata",
            "transport_timeout",
        }, provider_name
        for declaration in entry["common_cases"].values():
            assert declaration["kind"] in {
                "http_response",
                "library_exception",
                "transport_exception",
            }
            assert declaration["source"].startswith(("https://", "argus/"))
            assert "#" in declaration["source"]


def test_manifest_declares_all_ddgs_native_boundary_fixtures():
    entry = _load(FIXTURE_ROOT / "manifest.json")["providers"]["duckduckgo"]
    assert entry["native_boundary"] == {
        "success": "native-success.json",
        "empty": "native-empty.json",
        "unexpected": "native-unexpected.json",
        "library_failure": "native-library-failure.json",
    }
    assert all(
        (FIXTURE_ROOT / "duckduckgo" / fixture).is_file()
        for fixture in entry["native_boundary"].values()
    )


def test_failure_fixtures_do_not_invent_status_derived_native_codes():
    manifest = _load(FIXTURE_ROOT / "manifest.json")
    for provider_name, entry in manifest["providers"].items():
        for failure_class, declaration in entry["failures"].items():
            if "fixture" not in declaration:
                continue
            fixture = _load(FIXTURE_ROOT / provider_name / declaration["fixture"])
            body = fixture["body"]
            error = body.get("error") if isinstance(body, dict) else None
            code = error.get("code") if isinstance(error, dict) else error
            assert code not in {
                f"{provider_name}_{failure_class}",
                f"{provider_name}_{failure_class.replace('_504', '')}",
            }


def test_provider_codes_only_come_from_explicit_native_code_fields():
    prose_only = [
        (
            ProviderName.SERPER,
            {
                "transport": {"status_code": 200, "headers": {}},
                "body": {"message": "Not enough credits"},
            },
        ),
        (
            ProviderName.VALYU,
            {
                "transport": {"status_code": 200, "headers": {}},
                "body": {"success": False, "error": "insufficient credits"},
            },
        ),
    ]
    for provider, capture in prose_only:
        failure = classify_provider_failure_response(provider, capture)
        assert failure.category is FailureCategory.BALANCE_EXHAUSTED
        assert failure.provider_code is None

    explicit = classify_provider_failure_response(
        ProviderName.EXA,
        {
            "transport": {"status_code": 402, "headers": {}},
            "body": {"error": {"code": "NO_MORE_CREDITS"}},
        },
    )
    assert explicit.provider_code == "NO_MORE_CREDITS"


def test_github_fixtures_use_documented_native_error_messages():
    documented_messages = {
        "error-401.json": "Bad credentials",
        "error-403.json": "Resource not accessible by integration",
        "error-429.json": "API rate limit exceeded",
    }
    for fixture_name, message in documented_messages.items():
        fixture = _load(FIXTURE_ROOT / "github" / fixture_name)
        assert fixture["body"] == {
            "message": message,
            "documentation_url": (
                "https://docs.github.com/rest/using-the-rest-api/"
                "troubleshooting-the-rest-api"
            ),
            "status": fixture_name.removeprefix("error-").removesuffix(".json"),
        }


def test_serper_matrix_required_failure_classes_are_fixture_backed():
    failures = _load(FIXTURE_ROOT / "manifest.json")["providers"]["serper"][
        "failures"
    ]
    assert all("fixture" in failures[key] for key in ("401", "429", "5xx"))


@pytest.mark.parametrize("provider_name", sorted(REGISTERED))
def test_manifest_usage_and_rate_signal_declarations_are_executable(provider_name):
    entry = _load(FIXTURE_ROOT / "manifest.json")["providers"][provider_name]
    signals = entry["signals"]
    if "reason" in signals:
        return
    fixture = _load(FIXTURE_ROOT / provider_name / entry["fixtures"]["usage_rate"])
    assert isinstance(fixture, dict)
    batch = normalize_provider_response(
        ProviderName(provider_name),
        fixture.get("body", fixture),
        max_results=10,
        response_headers=fixture.get("headers", {}),
    )

    def resolve(path: str):
        value = batch.response_evidence
        for part in path.split("."):
            value = getattr(value, part)
        return value

    for signal in signals["typed"]:
        assert resolve(signal) is not None


@pytest.mark.parametrize("provider_name", sorted(REGISTERED))
def test_manifest_native_failure_fixtures_cross_provider_classifiers(provider_name):
    expected = {
        "401": FailureCategory.AUTHENTICATION_REJECTED,
        "402": FailureCategory.BALANCE_EXHAUSTED,
        "403": FailureCategory.POLICY_REJECTED,
        "408_504": FailureCategory.TIMEOUT,
        "422": FailureCategory.INVALID_REQUEST,
        "429": FailureCategory.RATE_LIMITED,
        "5xx": FailureCategory.PROVIDER_UNAVAILABLE,
    }
    entry = _load(FIXTURE_ROOT / "manifest.json")["providers"][provider_name]
    provider = ProviderName(provider_name)
    for failure_class, declaration in entry["failures"].items():
        if "reason" in declaration:
            continue
        fixture = _load(FIXTURE_ROOT / provider_name / declaration["fixture"])
        assert set(fixture) == {"transport", "body"}
        assert not {"status", "provider_code", "message"} & set(fixture)
        failure = classify_provider_failure_response(provider, fixture)
        assert failure.category is expected[failure_class]
        assert "fixture rejection" not in failure.summary


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", sorted(REGISTERED - {"duckduckgo"}))
async def test_every_applicable_failure_fixture_executes_real_adapter_search(
    provider_name,
):
    """The fixture matrix must test the adapter, not a parallel test classifier."""
    from argus.config import SearXNGConfig

    manifest = _load(FIXTURE_ROOT / "manifest.json")
    provider = ProviderName(provider_name)
    classes = {
        "searxng": ("argus.providers.searxng", "SearXNGProvider"),
        "yahoo": ("argus.providers.yahoo", "YahooProvider"),
        "github": ("argus.providers.github", "GitHubProvider"),
        "wolfram": ("argus.providers.wolfram", "WolframProvider"),
        "brave": ("argus.providers.brave", "BraveProvider"),
        "tavily": ("argus.providers.tavily", "TavilyProvider"),
        "exa": ("argus.providers.exa", "ExaProvider"),
        "linkup": ("argus.providers.linkup", "LinkupProvider"),
        "parallel": ("argus.providers.parallel", "ParallelProvider"),
        "serper": ("argus.providers.serper", "SerperProvider"),
        "you": ("argus.providers.you", "YouProvider"),
        "valyu": ("argus.providers.valyu", "ValyuProvider"),
        "searchapi": ("argus.providers.searchapi", "SearchApiProvider"),
    }
    config = (
        SearXNGConfig(enabled=True, base_url="https://fixture.test")
        if provider is ProviderName.SEARXNG
        else ProviderConfig(enabled=True, api_key="fixture")
    )
    module_name, class_name = classes[provider_name]
    adapter = getattr(importlib.import_module(module_name), class_name)(config)
    module = type(adapter).__module__
    expected = {
        "401": FailureCategory.AUTHENTICATION_REJECTED,
        "402": FailureCategory.BALANCE_EXHAUSTED,
        "403": FailureCategory.POLICY_REJECTED,
        "408_504": FailureCategory.TIMEOUT,
        "422": FailureCategory.INVALID_REQUEST,
        "429": FailureCategory.RATE_LIMITED,
        "5xx": FailureCategory.PROVIDER_UNAVAILABLE,
    }
    for failure_class, declaration in manifest["providers"][provider_name][
        "failures"
    ].items():
        if "reason" in declaration:
            continue
        fixture = _load(FIXTURE_ROOT / provider_name / declaration["fixture"])
        transport = fixture["transport"]
        response = _http_response(
            status=transport["status_code"],
            body=fixture["body"],
            headers=transport["headers"],
        )
        response.request = MagicMock()
        with patch(f"{module}.httpx.AsyncClient") as client_type:
            client = client_type.return_value
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.get = AsyncMock(return_value=response)
            client.post = AsyncMock(return_value=response)
            batch = await adapter.search(SearchQuery(query="fixture"))
        assert batch.failure is not None, (provider_name, failure_class)
        assert batch.failure.category is expected[failure_class]
        native_error = fixture["body"].get("error")
        if isinstance(native_error, dict):
            native_code = native_error.get("code") or native_error.get("type")
            if isinstance(native_code, str):
                assert batch.failure.provider_code == native_code


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", sorted(REGISTERED))
async def test_common_rate_and_timeout_cases_execute_real_adapter_path(
    provider_name,
):
    import httpx

    from argus.config import SearXNGConfig

    entry = _load(FIXTURE_ROOT / "manifest.json")["providers"][provider_name]
    classes = {
        "searxng": ("argus.providers.searxng", "SearXNGProvider"),
        "duckduckgo": ("argus.providers.duckduckgo", "DuckDuckGoProvider"),
        "yahoo": ("argus.providers.yahoo", "YahooProvider"),
        "github": ("argus.providers.github", "GitHubProvider"),
        "wolfram": ("argus.providers.wolfram", "WolframProvider"),
        "brave": ("argus.providers.brave", "BraveProvider"),
        "tavily": ("argus.providers.tavily", "TavilyProvider"),
        "exa": ("argus.providers.exa", "ExaProvider"),
        "linkup": ("argus.providers.linkup", "LinkupProvider"),
        "parallel": ("argus.providers.parallel", "ParallelProvider"),
        "serper": ("argus.providers.serper", "SerperProvider"),
        "you": ("argus.providers.you", "YouProvider"),
        "valyu": ("argus.providers.valyu", "ValyuProvider"),
        "searchapi": ("argus.providers.searchapi", "SearchApiProvider"),
    }
    config = (
        SearXNGConfig(enabled=True, base_url="https://fixture.test")
        if provider_name == "searxng"
        else ProviderConfig(enabled=True, api_key="fixture", timeout_seconds=1)
    )
    module_name, class_name = classes[provider_name]
    adapter = getattr(importlib.import_module(module_name), class_name)(config)
    adapter._available = True

    for case_name, declaration in entry["common_cases"].items():
        if provider_name == "duckduckgo":
            process = MagicMock()
            process.returncode = declaration.get("returncode", 1)
            process.communicate = AsyncMock(
                return_value=(
                    json.dumps(declaration.get("payload", {})).encode("utf-8"),
                    b"",
                )
            )
            if case_name == "transport_timeout":
                process.returncode = None
                process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
                process.wait = AsyncMock(return_value=-15)
                process.terminate = MagicMock()
                process.kill = MagicMock()
            with patch(
                f"{module_name}.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=process),
            ):
                batch = await adapter.search(SearchQuery(query="fixture"))
        else:
            with patch(f"{module_name}.httpx.AsyncClient") as client_type:
                client = client_type.return_value
                client.__aenter__ = AsyncMock(return_value=client)
                client.__aexit__ = AsyncMock(return_value=False)
                if case_name == "transport_timeout":
                    outcome = AsyncMock(
                        side_effect=httpx.ReadTimeout("fixture transport timeout")
                    )
                else:
                    outcome = AsyncMock(
                        return_value=_http_response(
                            status=declaration["status_code"],
                            body=declaration["body"],
                            headers=declaration["headers"],
                        )
                    )
                client.get = outcome
                client.post = outcome
                batch = await adapter.search(SearchQuery(query="fixture"))

        assert batch.failure is not None, (provider_name, case_name)
        if case_name.startswith("rate_limit"):
            assert batch.failure.category is FailureCategory.RATE_LIMITED
            if case_name.endswith("with_metadata"):
                assert (
                    batch.failure.retry_after_seconds is not None
                    or batch.failure.rate_limit_reset is not None
                )
            else:
                assert batch.failure.retry_after_seconds is None
                assert batch.failure.rate_limit_reset is None
        else:
            assert batch.failure.category is FailureCategory.TIMEOUT


@pytest.mark.asyncio
async def test_github_403_distinguishes_rate_limit_from_policy_rejection():
    from argus.providers.github import GitHubProvider

    adapter = GitHubProvider(ProviderConfig(enabled=True))
    categories = []
    for headers in ({}, {"X-RateLimit-Remaining": "0"}):
        response = _http_response(
            status=403, body={"message": "forbidden"}, headers=headers
        )
        response.request = MagicMock()
        with patch("argus.providers.github.httpx.AsyncClient") as client_type:
            client = client_type.return_value
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.get = AsyncMock(return_value=response)
            batch = await adapter.search(SearchQuery(query="fixture"))
        categories.append(batch.failure.category)
    assert categories == [
        FailureCategory.POLICY_REJECTED,
        FailureCategory.RATE_LIMITED,
    ]


@pytest.mark.asyncio
async def test_github_rate_limited_403_preserves_transport_evidence():
    from argus.providers.github import GitHubProvider

    response = _http_response(
        status=403,
        body={"message": "API rate limit exceeded"},
        headers={
            "X-GitHub-Request-Id": "github-request-403",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1785225600",
        },
    )
    response.request = MagicMock()
    with patch("argus.providers.github.httpx.AsyncClient") as client_type:
        client = client_type.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=response)
        batch = await GitHubProvider(ProviderConfig(enabled=True)).search(
            SearchQuery(query="fixture")
        )

    assert batch.failure is not None
    assert batch.failure.category is FailureCategory.RATE_LIMITED
    assert batch.failure.http_status == 403
    assert batch.failure.request_id == "github-request-403"
    assert batch.failure.rate_limit_reset == datetime(
        2026, 7, 28, 8, 0, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (UsageEvidence, {"count": 1e308}),
        (UsageEvidence, {"cost_usd": 1e308}),
        (RateLimitEvidence, {"remaining": 1e308}),
        (ProviderResponseEvidence, {"usage_count": 1e308}),
        (ProviderResponseEvidence, {"cost_usd": 1e308}),
        (ProviderResponseEvidence, {"rate_limit_remaining": 1e308}),
        (
            ProviderFailure,
            {
                "category": FailureCategory.RATE_LIMITED,
                "provider": ProviderName.GITHUB,
                "retry_after_seconds": 1e308,
            },
        ),
    ],
)
def test_numeric_usage_cost_and_rate_evidence_has_semantic_maxima(factory, kwargs):
    with pytest.raises(ValueError):
        factory(**kwargs)


@pytest.mark.parametrize(
    ("factory", "field", "maximum"),
    [
        (UsageEvidence, "count", MAX_USAGE_COUNT),
        (UsageEvidence, "cost_usd", MAX_COST_USD),
        (RateLimitEvidence, "remaining", MAX_RATE_LIMIT_REMAINING),
        (ProviderResponseEvidence, "usage_count", MAX_USAGE_COUNT),
        (ProviderResponseEvidence, "cost_usd", MAX_COST_USD),
        (
            ProviderResponseEvidence,
            "rate_limit_remaining",
            MAX_RATE_LIMIT_REMAINING,
        ),
    ],
)
def test_numeric_evidence_accepts_maximum_and_rejects_next_value(
    factory, field, maximum
):
    assert getattr(factory(**{field: maximum}), field) == maximum
    with pytest.raises(ValueError):
        factory(**{field: maximum + 1})


def test_retry_after_accepts_maximum_and_rejects_next_value():
    kwargs = {
        "category": FailureCategory.RATE_LIMITED,
        "provider": ProviderName.GITHUB,
    }
    assert (
        ProviderFailure(
            **kwargs, retry_after_seconds=MAX_RETRY_AFTER_SECONDS
        ).retry_after_seconds
        == MAX_RETRY_AFTER_SECONDS
    )
    with pytest.raises(ValueError):
        ProviderFailure(
            **kwargs, retry_after_seconds=MAX_RETRY_AFTER_SECONDS + 1
        )


@pytest.mark.parametrize("factory", [ProviderResponseEvidence, ProviderFailure])
def test_rate_reset_accepts_exact_temporal_boundaries(factory):
    observed = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    common = (
        {"observed_at": observed}
        if factory is ProviderResponseEvidence
        else {
            "category": FailureCategory.RATE_LIMITED,
            "provider": ProviderName.GITHUB,
            "observed_at": observed,
        }
    )
    for reset in (
        observed,
        observed + timedelta(seconds=EXPECTED_MAX_RATE_RESET_AHEAD_SECONDS),
    ):
        assert factory(**common, rate_limit_reset=reset).rate_limit_reset == reset


@pytest.mark.parametrize("factory", [ProviderResponseEvidence, ProviderFailure])
@pytest.mark.parametrize(
    "reset_factory",
    [
        lambda observed: observed - timedelta(microseconds=1),
        lambda observed: observed
        + timedelta(
            seconds=EXPECTED_MAX_RATE_RESET_AHEAD_SECONDS, microseconds=1
        ),
        lambda _observed: datetime.max.replace(tzinfo=timezone.utc),
    ],
)
def test_rate_reset_rejects_backward_or_implausible_deadlines(
    factory, reset_factory
):
    observed = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    common = (
        {"observed_at": observed}
        if factory is ProviderResponseEvidence
        else {
            "category": FailureCategory.RATE_LIMITED,
            "provider": ProviderName.GITHUB,
            "observed_at": observed,
        }
    )
    with pytest.raises(ValueError):
        factory(**common, rate_limit_reset=reset_factory(observed))


def test_failure_parser_discards_temporally_invalid_rate_reset():
    observed = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    capture = {
        "transport": {
            "status_code": 429,
            "headers": {"x-ratelimit-reset": str(datetime.max.timestamp())},
        },
        "body": {},
    }
    failure = classify_provider_failure_response(
        ProviderName.GITHUB, capture, observed_at=observed
    )
    assert failure.rate_limit_reset is None


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
    empty_declaration = fixtures["empty"]
    empty = (
        normalize_provider_response(
            provider,
            _load(FIXTURE_ROOT / provider_name / empty_declaration),
            max_results=10,
        )
        if isinstance(empty_declaration, str)
        else None
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
    if empty is None:
        assert provider is ProviderName.YAHOO
        assert "parse failure" in empty_declaration["reason"]
    else:
        assert empty.observations == ()
        assert empty.failure is not None
        assert empty.failure.category is FailureCategory.EMPTY
    assert malformed.failure is not None
    assert malformed.failure.category is FailureCategory.PARSE_ERROR


@pytest.mark.parametrize(
    ("provider", "field", "expected"),
    [
        (ProviderName.BRAVE, "rate_remaining", 9.0),
        (ProviderName.GITHUB, "request_id", "github-usage"),
        (ProviderName.GITHUB, "rate_remaining", 9.0),
        (ProviderName.TAVILY, "request_id", "tavily-usage"),
        (ProviderName.TAVILY, "usage_count", 1.0),
        (ProviderName.EXA, "request_id", "exa-usage"),
        (ProviderName.EXA, "cost_usd", 0.01),
        (ProviderName.PARALLEL, "request_id", "parallel-usage"),
        (ProviderName.PARALLEL, "usage_count", 9.0),
        (ProviderName.YOU, "request_id", "you-usage"),
        (ProviderName.VALYU, "transaction_id", "valyu-usage"),
        (ProviderName.VALYU, "cost_usd", 0.0),
        (ProviderName.SEARCHAPI, "request_id", "searchapi-usage"),
    ],
)
def test_usage_rate_fixtures_retain_only_typed_response_evidence(
    provider, field, expected
):
    fixture = _load(FIXTURE_ROOT / provider.value / "usage_rate.json")
    assert isinstance(fixture, dict)
    payload = fixture.get("body", fixture)
    headers = fixture.get("headers", {})
    batch = normalize_provider_response(
        provider, payload, max_results=10, response_headers=headers
    )
    response = batch.response_evidence
    if field == "rate_remaining":
        assert response.rate_limit is not None
        actual = response.rate_limit.remaining
    else:
        actual = getattr(response, field)
    assert actual == expected


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


def test_ambiguous_publication_fields_are_explicitly_unverified():
    for provider in (ProviderName.SEARXNG, ProviderName.TAVILY):
        fixture = _load(FIXTURE_ROOT / provider.value / "freshness.json")
        batch = normalize_provider_response(provider, fixture, max_results=10)
        publication = batch.observations[0].publication
        assert publication is not None
        assert publication.contract_confidence is ContractConfidence.UNVERIFIED
        assert publication.semantic_contract_ref is None


def test_searxng_native_score_is_unverified_without_engine_specific_contract():
    batch = normalize_provider_response(
        ProviderName.SEARXNG,
        {
            "results": [
                {
                    "url": "https://example.test",
                    "title": "Result",
                    "content": "Fixture",
                    "engine": "unknown-engine",
                    "score": 1.5,
                }
            ]
        },
        max_results=1,
    )
    assert batch.observations[0].native_score is not None
    assert (
        batch.observations[0].native_score.contract_confidence
        is ContractConfidence.UNVERIFIED
    )


@pytest.mark.asyncio
async def test_searxng_residential_route_sets_real_provenance_and_ignores_caller_claims():
    from argus.config import SearXNGConfig
    from argus.providers.searxng import SearXNGProvider

    response = _http_response(
        body={
            "results": [
                {
                    "url": "https://example.test/result",
                    "title": "Result",
                    "content": "Fixture",
                }
            ]
        }
    )
    with patch("argus.providers.searxng.httpx.AsyncClient") as client_type:
        client = client_type.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=response)
        batch = await SearXNGProvider(
            SearXNGConfig(
                enabled=True,
                base_url="https://local.test",
                residential_base_url="https://residential.test",
            )
        ).search(
            SearchQuery(
                query="provenance",
                metadata={
                    "prefer_residential": True,
                    "egress": "datacenter",
                    "machine": "/Volumes/private/caller-machine",
                },
            )
        )

    assert batch.response_evidence.egress.value == "residential"
    assert batch.response_evidence.machine is None
    assert batch.observations[0].egress.value == "residential"
    assert batch.observations[0].machine is None
    assert batch.observations[0].observed_at == batch.response_evidence.observed_at


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


def test_nested_private_values_are_never_stringified_at_normalization_seams():
    batches = [
        normalize_provider_response(
            ProviderName.BRAVE,
            {
                "web": {
                    "results": [
                        {
                            "url": "https://example.test",
                            "title": {"raw_body": "nested title sentinel"},
                            "description": ["nested snippet sentinel"],
                        }
                    ]
                },
                "warnings": [{"raw_body": "nested warning sentinel"}],
            },
            max_results=1,
        ),
        normalize_provider_response(
            ProviderName.PARALLEL,
            {
                "results": [
                    {
                        "url": "https://example.test/parallel",
                        "title": "Result",
                        "excerpts": [{"raw_body": "parallel nested sentinel"}],
                    }
                ]
            },
            max_results=1,
        ),
        normalize_provider_response(
            ProviderName.EXA,
            {
                "results": [
                    {
                        "url": "https://example.test/exa",
                        "title": "Result",
                        "highlights": [["exa nested sentinel"]],
                    }
                ]
            },
            max_results=1,
        ),
    ]
    rendered = json.dumps([batch.safe_log_record() for batch in batches])
    assert "nested title sentinel" not in rendered
    assert "nested snippet sentinel" not in rendered
    assert "nested warning sentinel" not in rendered
    assert "parallel nested sentinel" not in rendered
    assert "exa nested sentinel" not in rendered


def test_nonempty_native_list_with_only_malformed_rows_is_parse_error():
    batch = normalize_provider_response(
        ProviderName.TAVILY,
        {"results": ["raw body sentinel", 7, ["nested"]]},
        max_results=10,
    )
    assert batch.failure is not None
    assert batch.failure.category is FailureCategory.PARSE_ERROR


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ProviderResponseEvidence(latency_ms="1"),
        lambda: ProviderResponseEvidence(result_count=1.5),
        lambda: ProviderFailure(
            FailureCategory.RATE_LIMITED,
            ProviderName.BRAVE,
            http_status=99,
        ),
        lambda: ProviderFailure(
            FailureCategory.RATE_LIMITED,
            ProviderName.BRAVE,
            rate_limit_reset=datetime(2026, 7, 27),
        ),
        lambda: ProviderSearchBatch(
            provider=ProviderName.BRAVE,
            provider_contract_version="v1",
            request_evidence="not evidence",
        ),
        lambda: ProviderSearchBatch(
            provider=ProviderName.BRAVE,
            provider_contract_version="v1",
            response_evidence="not evidence",
        ),
        lambda: ProviderSearchBatch(
            provider=ProviderName.BRAVE,
            provider_contract_version="v1",
            observations=("not an observation",),
        ),
        lambda: ProviderSearchBatch(
            provider=ProviderName.BRAVE,
            provider_contract_version="v1",
            failure=ProviderFailure(FailureCategory.POLICY_REJECTED, ProviderName.EXA),
        ),
        lambda: ProviderResponseEvidence(usage="not usage"),
        lambda: ProviderResponseEvidence(warnings="not a warning tuple"),
        lambda: ProviderResponseEvidence(skipped=1),
        lambda: ProviderResponseEvidence(observed_at="not a datetime"),
        lambda: ProviderResponseEvidence(egress="local"),
        lambda: ProviderRequestEvidence(redirect_children=[]),
        lambda: ResultObservation(
            provider=ProviderName.BRAVE,
            provider_rank=0,
            url="https://example.test",
            title="Result",
            snippet=SnippetEvidence("text", SnippetKind.PROVIDER_SNIPPET),
            publication="not publication",
        ),
        lambda: SnippetEvidence(
            "text", SnippetKind.PROVIDER_SNIPPET, highlights=["not tuple"]
        ),
        lambda: PublicationEvidence(contract_confidence="unverified"),
        lambda: NativeScoreEvidence(math.nan, NativeScoreSemantics.RELEVANCE),
        lambda: ControlTranslation("none", "exact", FilterStrength.STRICT_CONTRACT),
        lambda: RedirectChildEvidence(
            "attempt",
            1,
            "https://source.test",
            "https://destination.test",
            cross_origin=1,
            credentials_stripped=True,
            timeout_seconds=1.0,
        ),
        lambda: ResultObservation(
            provider=ProviderName.BRAVE,
            provider_rank=0,
            url="https://example.test",
            title="Result",
            snippet=SnippetEvidence("text", SnippetKind.PROVIDER_SNIPPET),
            observed_at="not datetime",
        ),
        lambda: ProviderFailure(
            FailureCategory.RATE_LIMITED,
            ProviderName.BRAVE,
            retry_after_seconds="60",
        ),
    ],
)
def test_evidence_model_rejects_invalid_nested_types_and_ranges(factory):
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_provider_modules_import_cleanly_in_fresh_processes():
    root = Path(__file__).parents[1]
    for module in (
        "argus.providers.base",
        "argus.providers.brave",
        "argus.providers.github",
        "argus.providers.normalization",
    ):
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


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
    assert (
        attempt_timeout_seconds(
            configured_timeout=15.0,
            provider_phase_deadline=110.0,
            monotonic=lambda: 100.0,
        )
        == 10.0
    )
    assert (
        attempt_timeout_seconds(
            configured_timeout=5.0,
            provider_phase_deadline=110.0,
            monotonic=lambda: 100.0,
        )
        == 5.0
    )
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
    with pytest.raises(RequiredControlUnsupported):
        translate_freshness(
            ProviderName.PARALLEL,
            FreshnessWindow(end_date=date(2026, 7, 27)),
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
        batch = await ParallelProvider(
            ProviderConfig(enabled=True, api_key="fixture")
        ).search(SearchQuery(query="current contract", max_results=7))

    assert PARALLEL_API_BASE.endswith("/v1/search")
    _, kwargs = client.post.call_args
    assert kwargs["json"]["advanced_settings"]["max_results"] == 7
    assert "parallel-beta" not in kwargs["headers"]
    assert batch.request_evidence.provider_query_hash == query_hash(
        json.dumps(kwargs["json"], sort_keys=True, separators=(",", ":"), default=str)
    )
    assert batch.request_evidence.provider_query_hash != query_hash("current contract")


@pytest.mark.asyncio
async def test_github_hashes_the_actual_freshness_rewritten_request():
    from argus.providers.github import GitHubProvider

    response = _http_response(body={"items": []})
    with patch("argus.providers.github.httpx.AsyncClient") as client_type:
        client = client_type.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=response)
        batch = await GitHubProvider(ProviderConfig(enabled=True)).search(
            SearchQuery(
                query="argus",
                metadata={
                    "_freshness_window": FreshnessWindow(start_date=date(2026, 7, 1))
                },
            )
        )

    params = client.get.call_args.kwargs["params"]
    assert "pushed:" in params["q"]
    assert batch.request_evidence.query_relation is QueryRelation.PROVIDER_REWRITE
    assert batch.request_evidence.provider_query_hash == query_hash(
        json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    )
