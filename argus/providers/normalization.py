"""Provider-native response translation.

Native response keys belong to the provider layer.  This module is the sole
place that interprets those keys before producing the broker's closed evidence
model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from typing import Any, Mapping

from argus.broker.provider_evidence import (
    ContractConfidence,
    EvidenceKind,
    EgressType,
    FailureCategory,
    NativeScoreEvidence,
    NativeScoreSemantics,
    ProviderFailure,
    ProviderRequestEvidence,
    ProviderResponseEvidence,
    ProviderSearchBatch,
    PublicationEvidence,
    ResultObservation,
    SnippetEvidence,
    SnippetKind,
    classify_http_failure,
)
from argus.models import ProviderName

_CONTRACT_VERSION = {
    provider: "2026-07-27-v1"
    for provider in ProviderName
    if provider is not ProviderName.CACHE
}


def _mapping(data: object) -> Mapping[str, Any] | None:
    return data if isinstance(data, Mapping) else None


def _sequence(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return list(value)


def _rows(
    provider: ProviderName, data: Mapping[str, Any]
) -> tuple[list[object] | None, bool]:
    if provider is ProviderName.BRAVE:
        web = _mapping(data.get("web"))
        return (_sequence(web.get("results")) if web else None, web is not None)
    if provider is ProviderName.GITHUB:
        return _sequence(data.get("items")), "items" in data
    if provider is ProviderName.SERPER:
        return _sequence(data.get("organic")), "organic" in data
    if provider is ProviderName.YOU:
        results = _mapping(data.get("results"))
        return (
            _sequence(results.get("web")) if results else None,
            results is not None and "web" in results,
        )
    if provider is ProviderName.SEARCHAPI:
        key = "organic_results" if "organic_results" in data else "organic"
        return _sequence(data.get(key)), key in data
    if provider is ProviderName.WOLFRAM:
        answer = data.get("answer")
        if isinstance(answer, str):
            return (
                [
                    {
                        "url": data.get("query_url") or "https://www.wolframalpha.com/",
                        "title": data.get("title") or "WolframAlpha",
                        "text": answer,
                    }
                ],
                True,
            )
        return ([], data.get("empty") is True)
    return _sequence(data.get("results")), "results" in data


def _fields(
    provider: ProviderName, item: Mapping[str, Any]
) -> tuple[object, object, object, SnippetKind]:
    fields = {
        ProviderName.DUCKDUCKGO: (
            "href",
            "title",
            "body",
            SnippetKind.PROVIDER_SNIPPET,
        ),
        ProviderName.GITHUB: (
            "html_url",
            "full_name",
            "description",
            SnippetKind.PROVIDER_DESCRIPTION,
        ),
        ProviderName.LINKUP: (
            "url",
            "name",
            "content",
            SnippetKind.PROVIDER_TEXT_EXCERPT,
        ),
        ProviderName.SERPER: ("link", "title", "snippet", SnippetKind.PROVIDER_SNIPPET),
        ProviderName.SEARXNG: ("url", "title", "content", SnippetKind.PROVIDER_SNIPPET),
        ProviderName.TAVILY: (
            "url",
            "title",
            "content",
            SnippetKind.PROVIDER_TEXT_EXCERPT,
        ),
        ProviderName.VALYU: (
            "url",
            "title",
            "description",
            SnippetKind.PROVIDER_DESCRIPTION,
        ),
        ProviderName.BRAVE: (
            "url",
            "title",
            "description",
            SnippetKind.PROVIDER_DESCRIPTION,
        ),
        ProviderName.WOLFRAM: (
            "url",
            "title",
            "text",
            SnippetKind.PROVIDER_TEXT_EXCERPT,
        ),
    }
    if provider is ProviderName.PARALLEL:
        excerpts = item.get("excerpts")
        snippet = (
            " ".join(value for value in excerpts[:3] if isinstance(value, str))
            if isinstance(excerpts, list)
            else item.get("excerpt") or item.get("snippet")
        )
        return (
            item.get("url"),
            item.get("title"),
            snippet,
            SnippetKind.PROVIDER_TEXT_EXCERPT,
        )
    if provider is ProviderName.EXA:
        highlights = item.get("highlights")
        snippet = (
            " ".join(value for value in highlights[:3] if isinstance(value, str))
            if isinstance(highlights, list) and highlights
            else item.get("text")
        )
        return (
            item.get("url"),
            item.get("title"),
            snippet,
            SnippetKind.PROVIDER_HIGHLIGHT,
        )
    if provider is ProviderName.SEARCHAPI:
        return (
            item.get("link") or item.get("url"),
            item.get("title"),
            item.get("snippet") or item.get("description"),
            SnippetKind.PROVIDER_SNIPPET,
        )
    if provider is ProviderName.YOU:
        snippets = item.get("snippets")
        snippet = (
            snippets[0]
            if isinstance(snippets, list) and snippets
            else item.get("description")
        )
        return item.get("url"), item.get("title"), snippet, SnippetKind.PROVIDER_SNIPPET
    keys = fields.get(
        provider, ("url", "title", "snippet", SnippetKind.PROVIDER_SNIPPET)
    )
    url_key, title_key, snippet_key, kind = keys
    title = item.get(title_key)
    if provider is ProviderName.GITHUB:
        title = title or item.get("name")
    snippet = item.get(snippet_key)
    if provider is ProviderName.VALYU:
        snippet = snippet or item.get("content")
    return item.get(url_key), title, snippet, kind


def _publication(
    provider: ProviderName, item: Mapping[str, Any]
) -> PublicationEvidence | None:
    candidates: tuple[str, ...] = ()
    confidence = ContractConfidence.OFFICIAL_CONTRACT
    reference: str | None = f"{provider.value}-search-contract"
    if provider is ProviderName.EXA:
        candidates = ("publishedDate", "published_date")
    elif provider is ProviderName.PARALLEL:
        candidates = ("publish_date",)
    elif provider is ProviderName.VALYU:
        candidates = ("publication_date",)
    elif provider is ProviderName.TAVILY:
        candidates = ("published_date",)
        confidence, reference = ContractConfidence.UNVERIFIED, None
    elif provider is ProviderName.SEARXNG:
        candidates = ("publishedDate",)
        # SearXNG date semantics vary by upstream engine.
        confidence, reference = ContractConfidence.UNVERIFIED, None
    for field_name in candidates:
        if field_name in item:
            item_confidence = (
                ContractConfidence.FIXTURE_BACKED
                if provider is ProviderName.EXA and field_name == "published_date"
                else confidence
            )
            return PublicationEvidence.from_raw(
                item[field_name],
                raw_field_name=field_name,
                confidence=item_confidence,
                semantic_contract_ref=reference,
                parser_version="iso8601-v1",
            )
    return None


def _score(
    provider: ProviderName, item: Mapping[str, Any]
) -> NativeScoreEvidence | None:
    if provider is ProviderName.SEARXNG:
        value, semantics = item.get("score"), NativeScoreSemantics.PROVIDER_RANK_SCORE
    elif provider is ProviderName.TAVILY:
        value, semantics = item.get("score"), NativeScoreSemantics.RELEVANCE
    elif provider is ProviderName.VALYU:
        value, semantics = item.get("relevance_score"), NativeScoreSemantics.RELEVANCE
    else:
        return None
    return NativeScoreEvidence.from_value(
        value,
        semantics=semantics,
        confidence=(
            ContractConfidence.UNVERIFIED
            if provider is ProviderName.SEARXNG
            else ContractConfidence.OFFICIAL_CONTRACT
        ),
    )


def _kind(provider: ProviderName, item: Mapping[str, Any]) -> EvidenceKind:
    if provider is ProviderName.GITHUB:
        return EvidenceKind.REPOSITORY
    if provider is ProviderName.WOLFRAM:
        return EvidenceKind.COMPUTED_ANSWER
    source = item.get("source_type") or item.get("type")
    normalized_source = source.lower() if isinstance(source, str) else ""
    return {
        "web": EvidenceKind.WEB_PAGE,
        "news": EvidenceKind.NEWS,
        "paper": EvidenceKind.PAPER,
        "proprietary": EvidenceKind.PROPRIETARY,
    }.get(
        normalized_source,
        EvidenceKind.WEB_PAGE if source is None else EvidenceKind.UNKNOWN,
    )


def _header(headers: Mapping[str, object], name: str) -> object:
    lowered = name.lower()
    return next(
        (
            value
            for key, value in headers.items()
            if isinstance(key, str) and key.lower() == lowered
        ),
        None,
    )


def _numeric_header(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _response(
    provider: ProviderName,
    data: Mapping[str, Any],
    count: int,
    *,
    http_status: int | None,
    headers: Mapping[str, object],
    observed_at: datetime,
    egress: EgressType,
    machine: str | None,
) -> ProviderResponseEvidence:
    metadata = _mapping(data.get("metadata")) or {}
    usage = _mapping(data.get("usage")) or {}
    search_metadata = _mapping(data.get("search_metadata")) or {}
    warning_values = data.get("warnings")
    if not isinstance(warning_values, list):
        warning_values = []
    suggestions = data.get("suggestions")
    if not isinstance(suggestions, list):
        suggestions = []
    request_id = (
        data.get("requestId")
        or data.get("request_id")
        or data.get("search_id")
        or metadata.get("search_uuid")
        or search_metadata.get("id")
        or _header(headers, "x-request-id")
        or _header(headers, "x-github-request-id")
    )
    usage_count = usage.get("credits")
    if usage_count is None:
        usage_count = usage.get("total_tokens")
    cost = data.get("costDollars")
    if isinstance(cost, Mapping):
        cost = cost.get("total")
    if cost is None:
        cost = data.get("total_deduction_dollars")
    remaining = _numeric_header(_header(headers, "x-ratelimit-remaining"))
    reset = _header(headers, "x-ratelimit-reset")
    reset_at = None
    try:
        if reset is not None:
            reset_at = datetime.fromtimestamp(float(reset), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    return ProviderResponseEvidence(
        http_status=http_status,
        request_id=request_id,
        session_id=data.get("session_id"),
        transaction_id=data.get("tx_id"),
        warnings=tuple(warning_values),
        suggestions=tuple(suggestions),
        usage_count=usage_count,
        cost_usd=cost,
        rate_limit_remaining=remaining,
        rate_limit_reset=reset_at,
        result_count=count,
        observed_at=observed_at,
        egress=egress,
        machine=machine,
    )


def normalize_provider_response(
    provider: ProviderName,
    payload: object,
    *,
    max_results: int,
    request_evidence: ProviderRequestEvidence | None = None,
    http_status: int | None = None,
    response_headers: Mapping[str, object] | None = None,
    observed_at: datetime | None = None,
    egress: EgressType = EgressType.UNKNOWN,
    machine: str | None = None,
) -> ProviderSearchBatch:
    """Translate one native payload into closed, bounded broker evidence."""
    data = _mapping(payload)
    observed = observed_at or datetime.now(timezone.utc)
    if data is None or isinstance(max_results, bool) or max_results <= 0:
        return _parse_failure(
            provider,
            request_evidence,
            "invalid provider response shape",
            data=data or {},
            http_status=http_status,
            response_headers=response_headers or {},
            observed_at=observed,
            egress=egress,
            machine=machine,
        )
    rows, recognized = _rows(provider, data)
    if rows is None or not recognized:
        return _parse_failure(
            provider,
            request_evidence,
            "provider success response did not match contract",
            data=data,
            http_status=http_status,
            response_headers=response_headers or {},
            observed_at=observed,
            egress=egress,
            machine=machine,
        )
    observations: list[ResultObservation] = []
    for item in rows:
        if len(observations) >= max_results:
            break
        if not isinstance(item, Mapping):
            continue
        url, title, snippet, snippet_kind = _fields(provider, item)
        try:
            engines = item.get("engines")
            if not isinstance(engines, list):
                engines = [item.get("engine")] if item.get("engine") else []
            highlights = item.get("highlights")
            observations.append(
                ResultObservation(
                    provider=provider,
                    provider_rank=len(observations),
                    url=url,
                    title=title or "",
                    snippet=SnippetEvidence(
                        snippet or "",
                        snippet_kind if snippet else SnippetKind.EMPTY,
                        tuple(highlights) if isinstance(highlights, list) else (),
                    ),
                    source_kind=_kind(provider, item),
                    provider_source_type=item.get("source_type") or item.get("type"),
                    upstream_engines=tuple(engines),
                    publication=_publication(provider, item),
                    native_score=_score(provider, item),
                    provider_result_ref=item.get("id"),
                    provider_position=item.get("position"),
                    author=item.get("author"),
                    language=item.get("language"),
                    section=item.get("_section"),
                    star_count=item.get("stargazers_count"),
                    fork_count=item.get("forks_count"),
                    topics=tuple(item.get("topics"))
                    if isinstance(item.get("topics"), list)
                    else (),
                    observed_at=observed,
                    egress=egress,
                    machine=machine,
                )
            )
        except (TypeError, ValueError):
            continue
    response = _response(
        provider,
        data,
        len(observations),
        http_status=http_status,
        headers=response_headers or {},
        observed_at=observed,
        egress=egress,
        machine=machine,
    )
    if rows and not observations:
        failure = ProviderFailure(
            FailureCategory.PARSE_ERROR,
            provider,
            http_status=http_status,
            summary="all provider result rows were structurally invalid",
        )
    elif not rows:
        failure = ProviderFailure(
            FailureCategory.EMPTY,
            provider,
            http_status=http_status,
            summary="valid empty provider response",
        )
    else:
        failure = None
    normalized_request = request_evidence or ProviderRequestEvidence()
    if (
        failure is not None
        and failure.category is FailureCategory.EMPTY
        and normalized_request.freshness_translation is not None
    ):
        normalized_request = replace(
            normalized_request,
            freshness_translation=replace(
                normalized_request.freshness_translation,
                successful_empty_contract_ref="successful-empty-v1",
            ),
        )
    return ProviderSearchBatch(
        provider=provider,
        provider_contract_version=_CONTRACT_VERSION[provider],
        request_evidence=normalized_request,
        response_evidence=response,
        observations=tuple(observations),
        failure=failure,
    )


def _parse_failure(
    provider: ProviderName,
    request_evidence: ProviderRequestEvidence | None,
    summary: str,
    *,
    data: Mapping[str, Any],
    http_status: int | None,
    response_headers: Mapping[str, object],
    observed_at: datetime,
    egress: EgressType,
    machine: str | None,
) -> ProviderSearchBatch:
    return ProviderSearchBatch(
        provider=provider,
        provider_contract_version=_CONTRACT_VERSION[provider],
        request_evidence=request_evidence or ProviderRequestEvidence(),
        response_evidence=_response(
            provider,
            data,
            0,
            http_status=http_status,
            headers=response_headers,
            observed_at=observed_at,
            egress=egress,
            machine=machine,
        ),
        failure=ProviderFailure(
            FailureCategory.PARSE_ERROR,
            provider,
            http_status=http_status,
            summary=summary,
            observed_at=observed_at,
        ),
    )


def classify_provider_failure_response(
    provider: ProviderName,
    captured_response: object,
    *,
    observed_at: datetime | None = None,
) -> ProviderFailure:
    """Classify a captured native response using only allowlisted fields."""
    capture = _mapping(captured_response)
    transport = _mapping(capture.get("transport")) if capture else None
    if transport is None or type(transport.get("status_code")) is not int:
        return ProviderFailure(
            FailureCategory.PARSE_ERROR,
            provider,
            summary="invalid captured provider failure",
        )
    observed_status = int(transport["status_code"])
    observed = observed_at or datetime.now(timezone.utc)
    classification_status = observed_status
    headers = _mapping(transport.get("headers")) or {}
    body = _mapping(capture.get("body")) or {}
    error = body.get("error")
    error_map = _mapping(error) or {}
    query_result = _mapping(body.get("queryresult")) or {}
    nested_query_error = _mapping(query_result.get("error")) or {}
    code = (
        error_map.get("code")
        or error_map.get("type")
        or body.get("tag")
        or nested_query_error.get("code")
    )
    retry_after_raw = _header(headers, "retry-after")
    retry_after = _numeric_header(retry_after_raw)
    remaining = _header(headers, "x-ratelimit-remaining")
    request_id = (
        body.get("requestId")
        or body.get("request_id")
        or _header(headers, "x-request-id")
        or _header(headers, "x-github-request-id")
    )
    if not isinstance(request_id, str):
        request_id = None
    reset_at = None
    reset_raw = _header(headers, "x-ratelimit-reset")
    if reset_raw is not None:
        try:
            reset_at = datetime.fromtimestamp(float(reset_raw), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass
    if (
        provider is ProviderName.GITHUB
        and observed_status == 403
        and (remaining == "0" or retry_after_raw is not None)
    ):
        classification_status = 429
    message = body.get("message")
    if (
        provider is ProviderName.SERPER
        and observed_status < 400
        and isinstance(message, str)
    ):
        if "credit" in message.lower():
            classification_status = 402
    if (
        provider is ProviderName.VALYU
        and observed_status < 400
        and body.get("success") is False
        and isinstance(error, str)
        and "credit" in error.lower()
    ):
        classification_status = 402
    classification_kwargs = {
        "provider_code": code if isinstance(code, str) else None,
        "request_id": request_id,
        "retry_after_seconds": retry_after,
        "rate_limit_reset": reset_at,
        "observed_at": observed,
        "summary": f"{provider.value} native response rejected the request",
    }
    try:
        classified = classify_http_failure(
            provider, classification_status, **classification_kwargs
        )
    except ValueError:
        classification_kwargs["rate_limit_reset"] = None
        classified = classify_http_failure(
            provider, classification_status, **classification_kwargs
        )
    if classification_status == observed_status:
        return classified
    return ProviderFailure(
        category=classified.category,
        provider=provider,
        http_status=observed_status,
        provider_code=classified.provider_code,
        request_id=classified.request_id,
        retry_after_seconds=classified.retry_after_seconds,
        rate_limit_reset=classified.rate_limit_reset,
        summary=classified.summary,
        observed_at=observed,
    )


def provider_response_indicates_failure(
    provider: ProviderName, status: int, body: object
) -> bool:
    if status >= 400:
        return True
    data = _mapping(body)
    message = data.get("message") if data is not None else None
    return bool(
        (
            provider is ProviderName.SERPER
            and isinstance(message, str)
            and "credit" in message.lower()
        )
        or (
            provider is ProviderName.VALYU
            and data is not None
            and data.get("success") is False
            and isinstance(data.get("error"), str)
        )
    )
