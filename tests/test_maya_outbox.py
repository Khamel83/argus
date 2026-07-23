import hashlib
import json
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy import event, func, select

from argus.extraction.models import ExtractedContent, ExtractionAttempt, ExtractorName
from argus.models import SearchQuery
from argus.persistence.search_ledger import DeliveryIntentRow
from tests.test_search_ledger import _response


def _repository(tmp_path):
    from argus.persistence.search_ledger import create_search_ledger_repository

    return create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'maya-outbox.db'}",
        create_schema=True,
    )


def _outbox_row(repository):
    with repository.session_factory() as session:
        return session.scalar(select(DeliveryIntentRow))


def _maya_receipt(*, pages=0, duplicate=False):
    return {
        "capture_id": "a" * 32,
        "caller": "argus",
        "page_ids": [f"{index + 1:032x}" for index in range(pages)],
        "duplicate": duplicate,
        "children_added": 0 if duplicate else pages,
        "received_at": "2026-07-23T12:01:00Z",
    }


@pytest.mark.parametrize(
    ("status_code", "response_body"),
    [
        (204, None),
        (200, "not-json"),
        (201, {"capture_id": "partial"}),
        (200, {**_maya_receipt(), "caller": "not-argus"}),
        (201, {**_maya_receipt(), "page_ids": ["b" * 32]}),
        (201, {**_maya_receipt(), "received_at": 123}),
        (201, {**_maya_receipt(pages=1), "page_ids": [{}]}),
    ],
)
def test_malformed_success_response_never_acknowledges_delivery(
    tmp_path,
    status_code,
    response_body,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="receipt contract"), _response())
    now = datetime(2026, 7, 23, 12, 0)

    def malformed(request):
        if response_body is None:
            return httpx.Response(status_code)
        if isinstance(response_body, str):
            return httpx.Response(status_code, text=response_body)
        return httpx.Response(status_code, json=response_body)

    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(malformed),
        clock=lambda: now,
    )

    assert dispatcher.run_once() == {"retried": 1}
    row = _outbox_row(repository)
    assert row.status == "retry"
    assert row.delivered_at is None
    assert row.response_json is None
    assert row.last_error_code == "invalid_maya_receipt"


def test_search_acceptance_atomically_enqueues_a_bounded_sanitized_maya_parent(
    tmp_path,
):
    repository = _repository(tmp_path)
    response = _response()
    response.results[0].url = "https://example.com/article?token=do-not-store"
    response.results[
        0
    ].snippet = "Authorization: Bearer do-not-store ghp_abcdefghijklmnopqrstuvwxyz"

    receipt = repository.accept(
        SearchQuery(query="find api_key=do-not-store", caller="maya"),
        response,
    )

    row = _outbox_row(repository)
    payload = json.loads(row.payload_json)
    assert row.id == receipt.delivery_intent_id
    assert row.run_id
    assert row.extraction_run_id is None
    assert row.status == "pending"
    assert payload["idempotency_key"] == "run-1"
    assert payload["pages"] == []
    assert len(payload["result_summary"]) <= 16_384
    assert "do-not-store" not in row.payload_json
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in row.payload_json
    assert "[redacted]" in row.payload_json


@pytest.mark.parametrize(
    "credential",
    [
        "Authorization: Basic dXNlcjpwYXNzd29yZA==",
        "Cookie: session=super-secret-cookie",
        "Set-Cookie: auth=super-secret-cookie",
        "api_key=super-secret-api-key",
        "token: super-secret-token",
        "client_secret=super-secret-client-secret",
        "cookie=session=s3nt1n3lLeakValue987",
        "session_cookie=s3nt1n3lLeakValue987",
        "access_token=s3nt1n3lLeakValue987",
        "refresh_token=s3nt1n3lLeakValue987",
        "id_token=s3nt1n3lLeakValue987",
        "refresh=s3nt1n3lLeakValue987",
        "id=s3nt1n3lLeakValue987",
        '{"token":"s3nt1n3lLeakValue987"}',
        "{'api_key':'s3nt1n3lLeakValue987'}",
        '"authorization": "s3nt1n3lLeakValue987"',
    ],
)
def test_extraction_outbox_redacts_comprehensive_credential_material(
    tmp_path,
    credential,
):
    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/article",
        text=f"safe prefix {credential} safe suffix",
        word_count=5,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="credential-redaction",
    )

    payload = _outbox_row(repository).payload_json
    assert "super-secret" not in payload
    assert "dXNlcjpwYXNzd29yZA" not in payload
    assert "s3nt1n3lLeakValue987" not in payload
    assert "[redacted]" in payload


@pytest.mark.parametrize(
    "message",
    [
        "cookie=session=d34dL3tt3rLeakValue987",
        "session_cookie=d34dL3tt3rLeakValue987",
        "access_token=d34dL3tt3rLeakValue987",
        "refresh_token=d34dL3tt3rLeakValue987",
        "id_token=d34dL3tt3rLeakValue987",
        "refresh=d34dL3tt3rLeakValue987",
        "id=d34dL3tt3rLeakValue987",
        '{"token":"d34dL3tt3rLeakValue987"}',
        "{'api_key':'d34dL3tt3rLeakValue987'}",
        '"authorization": "d34dL3tt3rLeakValue987"',
    ],
)
def test_dead_letter_summary_redacts_structured_and_assignment_secrets(
    tmp_path,
    message,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="unsafe dead letter"), _response())
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "invalid_capture_request",
                        "message": message,
                    }
                },
            )
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"dead_lettered": 1}
    row = _outbox_row(repository)
    assert "d34dL3tt3rLeakValue987" not in row.last_error_summary
    assert "[redacted]" in row.last_error_summary


def test_outbox_url_query_redacts_all_sensitive_key_aliases(tmp_path):
    repository = _repository(tmp_path)
    response = _response()
    response.results[0].url = (
        "https://example.com/article?"
        "session_cookie=urlLeakValue987&refresh=urlLeakValue987&id=urlLeakValue987"
    )

    repository.accept(SearchQuery(query="sensitive url"), response)

    payload = _outbox_row(repository).payload_json
    assert "urlLeakValue987" not in payload
    assert "session_cookie" not in payload


@pytest.mark.parametrize(
    "credential",
    [
        "auth=aliasLeakValue987",
        "AUTH-TOKEN=aliasLeakValue987",
        '"authentication":"aliasLeakValue987"',
        "session=aliasLeakValue987",
        "Session-ID=aliasLeakValue987",
        "credential: aliasLeakValue987",
        "credentials[password]=aliasLeakValue987",
        "auth[token]=aliasLeakValue987",
        "user_credentials=aliasLeakValue987",
        "authToken=aliasLeakValue987",
    ],
)
def test_outbox_redacts_normalized_sensitive_alias_variants(
    tmp_path,
    credential,
):
    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/article",
        text=f"safe prefix {credential} safe suffix",
        word_count=5,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="credential-alias-redaction",
    )

    payload = _outbox_row(repository).payload_json
    assert "aliasLeakValue987" not in payload
    assert "[redacted]" in payload


@pytest.mark.parametrize(
    "message",
    [
        "auth=summaryAliasLeak987",
        "AUTH-TOKEN=summaryAliasLeak987",
        '"authentication":"summaryAliasLeak987"',
        "session=summaryAliasLeak987",
        "Session-ID=summaryAliasLeak987",
        "credential: summaryAliasLeak987",
        "credentials[password]=summaryAliasLeak987",
        "auth[token]=summaryAliasLeak987",
        "user_credentials=summaryAliasLeak987",
        "authToken=summaryAliasLeak987",
    ],
)
def test_dead_letter_redacts_normalized_sensitive_alias_variants(
    tmp_path,
    message,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="unsafe alias summary"), _response())
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "invalid_capture_request",
                        "message": message,
                    }
                },
            )
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"dead_lettered": 1}
    row = _outbox_row(repository)
    assert "summaryAliasLeak987" not in row.last_error_summary
    assert "[redacted]" in row.last_error_summary


def test_outbox_url_query_uses_normalized_conservative_sensitive_aliases(tmp_path):
    repository = _repository(tmp_path)
    response = _response()
    response.results[0].url = (
        "https://example.com/article?"
        "%61uth%5Ftoken=urlAliasLeak987&"
        "credentials%5Bpassword%5D=urlAliasLeak987&"
        "my-session-id=urlAliasLeak987"
    )

    repository.accept(SearchQuery(query="encoded sensitive url"), response)

    payload = _outbox_row(repository).payload_json
    assert "urlAliasLeak987" not in payload
    assert "%61uth" not in payload


def test_safe_extraction_content_preserves_900k_with_bounded_runtime(tmp_path):
    repository = _repository(tmp_path)
    content = ("safe visible extraction content " * 30_000)[:900_000]
    result = ExtractedContent(
        url="https://example.com/large-article",
        text=content,
        word_count=120_000,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    started = time.perf_counter()
    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="large-safe-content",
    )
    elapsed = time.perf_counter() - started

    payload = json.loads(_outbox_row(repository).payload_json)
    assert payload["pages"][0]["content"] == content
    assert elapsed < 3.0


@pytest.mark.parametrize(
    "credential",
    [
        "%2561uth=deepEncodedPayloadLeak987",
        'prefix {"\\u0061uth":"deepEncodedPayloadLeak987"} suffix',
        f"{'_' * 192}token=deepEncodedPayloadLeak987",
        '{"outer":{"auth":"deepEncodedPayloadLeak987"}}',
        '{"outer":[{"\\u0061uth":"deepEncodedPayloadLeak987"}]}',
        '{"outer":{"%2561uth":"deepEncodedPayloadLeak987"}}',
        '{"outer":"{\\"auth\\":\\"deepEncodedPayloadLeak987\\"}"}',
        '{"outer":"safe article auth=deepEncodedPayloadLeak987"}',
        "{'outer':[{'auth':'deepEncodedPayloadLeak987'}]}",
        '{"outer":{"api key":"deepEncodedPayloadLeak987"}}',
        '{"outer":[{"AUTH-token":"deepEncodedPayloadLeak987"}]}',
        '{"outer":"{\\"session id\\":\\"deepEncodedPayloadLeak987\\"}"}',
        "{'outer':[{'Credentials Password':'deepEncodedPayloadLeak987'}]}",
    ],
)
def test_outbox_redacts_nested_encoded_and_long_key_credentials(
    tmp_path,
    credential,
):
    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/article",
        text=credential,
        word_count=1,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="nested-credential-redaction",
    )

    row = _outbox_row(repository)
    assert "deepEncodedPayloadLeak987" not in row.payload_json
    assert "[redacted]" in row.payload_json


@pytest.mark.parametrize(
    "message",
    [
        "%2561uth=deepEncodedSummaryLeak987",
        'prefix {"\\u0061uth":"deepEncodedSummaryLeak987"} suffix',
        f"{'_' * 192}token=deepEncodedSummaryLeak987",
        '{"outer":{"auth":"deepEncodedSummaryLeak987"}}',
        '{"outer":[{"\\u0061uth":"deepEncodedSummaryLeak987"}]}',
        '{"outer":{"%2561uth":"deepEncodedSummaryLeak987"}}',
        '{"outer":"{\\"auth\\":\\"deepEncodedSummaryLeak987\\"}"}',
        '{"outer":"safe article auth=deepEncodedSummaryLeak987"}',
        "{'outer':[{'auth':'deepEncodedSummaryLeak987'}]}",
        '{"outer":{"api key":"deepEncodedSummaryLeak987"}}',
        '{"outer":[{"AUTH-token":"deepEncodedSummaryLeak987"}]}',
        '{"outer":"{\\"session id\\":\\"deepEncodedSummaryLeak987\\"}"}',
        "{'outer':[{'Credentials Password':'deepEncodedSummaryLeak987'}]}",
    ],
)
def test_dead_letter_redacts_nested_encoded_and_long_key_credentials(
    tmp_path,
    message,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="nested unsafe summary"), _response())
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "invalid_capture_request",
                        "message": message,
                    }
                },
            )
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"dead_lettered": 1}
    row = _outbox_row(repository)
    assert "deepEncodedSummaryLeak987" not in row.last_error_summary
    assert "[redacted]" in row.last_error_summary


@pytest.mark.parametrize(
    "credential",
    [
        '{"outer":{"AUTH-token":{"child":"structuredObjectLeak987"}}}',
        '{"outer":{"api key":["safe","structuredArrayLeak987"]}}',
        "{'outer': {'credentials password': {'child': 'pythonTreeLeak987'}}}",
        (
            '{"outer":"%257B%2522session%2520id%2522%253A'
            '%255B%2522encodedTreeLeak987%2522%255D%257D"}'
        ),
    ],
)
def test_outbox_redacts_entire_free_text_field_for_sensitive_containers(
    tmp_path,
    credential,
):
    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/article",
        text=credential,
        word_count=1,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="container-credential-redaction",
    )

    payload = json.loads(_outbox_row(repository).payload_json)
    assert payload["pages"][0]["content"] == "[redacted]"
    assert "Leak987" not in json.dumps(payload)


@pytest.mark.parametrize(
    "message",
    [
        '{"outer":{"AUTH-token":{"child":"summaryObjectLeak987"}}}',
        '{"outer":{"api key":["safe","summaryArrayLeak987"]}}',
        "{'outer': {'credentials password': {'child': 'summaryPythonLeak987'}}}",
        (
            '{"outer":"%257B%2522session%2520id%2522%253A'
            '%255B%2522summaryEncodedLeak987%2522%255D%257D"}'
        ),
    ],
)
def test_dead_letter_redacts_entire_summary_for_sensitive_containers(
    tmp_path,
    message,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="unsafe container summary"), _response())
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "invalid_capture_request",
                        "message": message,
                    }
                },
            )
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"dead_lettered": 1}
    row = _outbox_row(repository)
    assert row.last_error_summary == "[redacted]"
    assert "Leak987" not in row.last_error_summary


@pytest.mark.parametrize("layers", [5, 6, 20])
def test_outbox_url_query_drops_deeply_encoded_sensitive_keys(tmp_path, layers):
    repository = _repository(tmp_path)
    response = _response()
    key = "auth token"
    for _ in range(layers):
        key = quote(key, safe="")
    response.results[
        0
    ].url = f"https://example.com/article?{key}=deepUrlLeak987&safe=visible"

    repository.accept(SearchQuery(query="deep encoded URL"), response)

    payload = json.loads(_outbox_row(repository).payload_json)
    assert "deepUrlLeak987" not in payload["result_summary"]
    assert "visible" in payload["result_summary"]


def test_outbox_url_query_preserves_benign_deeply_encoded_values(tmp_path):
    repository = _repository(tmp_path)
    response = _response()
    value = "ordinary value"
    for _ in range(20):
        value = quote(value, safe="")
    response.results[0].url = f"https://example.com/article?safe={value}&other=visible"

    repository.accept(SearchQuery(query="benign encoded value"), response)

    payload = json.loads(_outbox_row(repository).payload_json)
    assert "safe=" in payload["result_summary"]
    assert "other=visible" in payload["result_summary"]
    assert "[redacted]" not in payload["result_summary"]


def test_outbox_redacts_twenty_layer_encoded_sensitive_container(tmp_path):
    repository = _repository(tmp_path)
    credential = '{"ａｕｔｈ token":{"child":"twentyLayerLeak987"}}'
    for _ in range(20):
        credential = quote(credential, safe="")
    result = ExtractedContent(
        url="https://example.com/article",
        text=credential,
        word_count=1,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="twenty-layer-container-redaction",
    )

    payload = json.loads(_outbox_row(repository).payload_json)
    assert payload["pages"][0]["content"] == "[redacted]"
    assert "twentyLayerLeak987" not in json.dumps(payload)


def test_outbox_redacts_triple_encoded_query_key(tmp_path):
    repository = _repository(tmp_path)
    response = _response()
    response.results[
        0
    ].url = (
        "https://example.com/article?%252561uth=tripleEncodedUrlLeak987&safe=visible"
    )

    repository.accept(SearchQuery(query="triple encoded URL"), response)

    row = _outbox_row(repository)
    assert "tripleEncodedUrlLeak987" not in row.payload_json
    assert "visible" in row.payload_json


def test_extraction_url_redacts_credential_bearing_path_everywhere(tmp_path):
    repository = _repository(tmp_path)
    sentinel = "credentialPathLeak987"
    result = ExtractedContent(
        url=f"https://example.com/auth/{sentinel}/reset?safe=visible",
        text="safe extracted content",
        word_count=3,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="credential-path-redaction",
    )

    payload = json.loads(_outbox_row(repository).payload_json)
    assert sentinel not in json.dumps(payload)
    assert "[redacted]" in payload["query"]
    assert "[redacted]" in payload["result_summary"]
    assert "[redacted]" in payload["pages"][0]["source_url"]
    assert "visible" in payload["query"]


def test_safe_json_extraction_preserves_899965_bytes_with_bounded_runtime(tmp_path):
    repository = _repository(tmp_path)
    target_size = 899_965
    prefix = '{"article":"'
    suffix = '"}'
    content = prefix + ("x" * (target_size - len(prefix) - len(suffix))) + suffix
    assert len(content.encode("utf-8")) == target_size
    result = ExtractedContent(
        url="https://example.com/large-json-article",
        text=content,
        word_count=1,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    started = time.perf_counter()
    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="large-safe-json-content",
    )
    elapsed = time.perf_counter() - started

    payload = json.loads(_outbox_row(repository).payload_json)
    assert payload["pages"][0]["content"] == content
    assert elapsed < 3.0


def test_unicode_heavy_extraction_is_fitted_to_maya_request_byte_budget(tmp_path):
    from argus.persistence.maya_outbox import MAYA_RETRIEVAL_PAYLOAD_MAX_BYTES

    repository = _repository(tmp_path)
    original = "😀" * 1_048_576
    result = ExtractedContent(
        url="https://example.com/emoji-article",
        text=original,
        word_count=1,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="unicode-byte-budget",
    )

    row = _outbox_row(repository)
    payload = json.loads(row.payload_json)
    content = payload["pages"][0]["content"]
    assert len(row.payload_json.encode("utf-8")) <= MAYA_RETRIEVAL_PAYLOAD_MAX_BYTES
    assert 0 < len(content) < len(original)
    assert content == "😀" * len(content)
    assert (
        payload["pages"][0]["content_sha256"]
        == hashlib.sha256(content.encode("utf-8")).hexdigest()
    )
    assert row.content_sha256 == payload["pages"][0]["content_sha256"]


def test_multi_page_capture_budget_includes_metadata_and_is_deterministic():
    from argus.persistence.maya_outbox import (
        MAYA_RETRIEVAL_PAYLOAD_MAX_BYTES,
        _fit_maya_payload_to_budget,
        _maya_payload_json,
    )

    content = "x" * 1_048_576
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    provenance = {
        "egress": "residential",
        "machine": "m" * 128,
        "provider": "p" * 64,
        "providers": ["p" * 64] * 16,
        "source_type": "s" * 64,
    }
    pages = [
        {
            "position": index,
            "source_url": f"https://example.com/{index}/" + ("u" * 1900),
            "title": "t" * 1024,
            "content": content,
            "content_sha256": content_hash,
            "provenance": provenance,
            "extracted_at": "2026-07-23T12:00:00Z",
        }
        for index in range(16)
    ]
    payload = {
        "idempotency_key": "multi-page-budget",
        "query": "q" * 4096,
        "mode": "research",
        "result_summary": "r" * 16_384,
        "provenance": provenance,
        "started_at": "2026-07-23T12:00:00Z",
        "completed_at": "2026-07-23T12:01:00Z",
        "pages": pages,
    }

    first = _fit_maya_payload_to_budget(payload)
    second = _fit_maya_payload_to_budget(payload)
    encoded = _maya_payload_json(first).encode("utf-8")

    assert len(encoded) <= MAYA_RETRIEVAL_PAYLOAD_MAX_BYTES
    assert first == second
    assert all(page["content"] for page in first["pages"])
    assert all(
        page["content_sha256"]
        == hashlib.sha256(page["content"].encode("utf-8")).hexdigest()
        for page in first["pages"]
    )
    assert all(len(page["content"]) < len(content) for page in first["pages"])


def test_extraction_content_preserves_newlines_and_hashes_verbatim(tmp_path):
    repository = _repository(tmp_path)
    content = "line one\nline two\r\nline three"
    result = ExtractedContent(
        url="https://example.com/multiline",
        text=content,
        word_count=6,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )

    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="verbatim-multiline-content",
    )

    payload = json.loads(_outbox_row(repository).payload_json)
    page = payload["pages"][0]
    assert page["content"] == content
    assert page["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert "\n" not in payload["result_summary"]
    assert "\r" not in payload["result_summary"]


def test_url_sanitization_bounds_input_and_query_work_deterministically():
    from argus.persistence.maya_outbox import _safe_url

    huge_query = "&".join(f"safe{index}=visible" for index in range(100_000))
    url = f"https://example.com/article?{huge_query}&auth=urlWorkLeak987"

    tracemalloc.start()
    started = time.perf_counter()
    sanitized = [_safe_url(url) for _ in range(50)]
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert all(len(item.encode("utf-8")) <= 2048 for item in sanitized)
    assert all("urlWorkLeak987" not in item for item in sanitized)
    assert elapsed < 3.0
    assert peak_bytes < 1_000_000


def test_nonduplicate_receipt_cannot_acknowledge_zero_inserted_children(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/receipt-child",
        text="child content",
        word_count=2,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )
    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="invalid-zero-child-receipt",
    )
    receipt = _maya_receipt(pages=1)
    receipt["children_added"] = 0
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(201, json=receipt)
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"retried": 1}
    row = _outbox_row(repository)
    assert row.status == "retry"
    assert row.delivered_at is None
    assert row.response_json is None
    assert row.last_error_code == "invalid_maya_receipt"


def test_partial_child_receipt_can_return_two_durable_ids_for_one_page_subset(
    tmp_path,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/second-page",
        text="second child content",
        word_count=3,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )
    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="one-page-additive-retry",
    )
    receipt = _maya_receipt(pages=2)
    receipt["children_added"] = 1
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(201, json=receipt)
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"acknowledged": 1}
    row = _outbox_row(repository)
    assert row.status == "acknowledged"
    assert json.loads(row.response_json)["page_ids"] == receipt["page_ids"]


def test_two_page_retry_can_acknowledge_one_new_child(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/two-page-retry",
        text="first child content",
        word_count=3,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )
    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="two-page-additive-retry",
    )
    with repository.session_factory.begin() as session:
        row = session.scalar(select(DeliveryIntentRow))
        payload = json.loads(row.payload_json)
        second = {
            **payload["pages"][0],
            "position": 1,
            "source_url": "https://example.com/two-page-retry/second",
            "title": "Second page",
            "content": "second child content",
        }
        second["content_sha256"] = hashlib.sha256(
            second["content"].encode()
        ).hexdigest()
        payload["pages"].append(second)
        row.payload_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        row.payload_sha256 = hashlib.sha256(row.payload_json.encode()).hexdigest()
    receipt = _maya_receipt(pages=2)
    receipt["children_added"] = 1
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(201, json=receipt)
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"acknowledged": 1}
    assert _outbox_row(repository).status == "acknowledged"


@pytest.mark.parametrize(
    ("pages", "status_code", "duplicate", "children_added", "page_id_count"),
    [
        (2, 201, False, 0, 2),
        (2, 201, False, 1, 1),
        (1, 201, False, 2, 2),
        (1, 200, True, 1, 2),
    ],
)
def test_additive_receipt_rejects_inconsistent_counts(
    tmp_path,
    pages,
    status_code,
    duplicate,
    children_added,
    page_id_count,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/malformed-additive",
        text="child content",
        word_count=2,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )
    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id=f"malformed-additive-{pages}-{children_added}",
    )
    if pages == 2:
        with repository.session_factory.begin() as session:
            row = session.scalar(select(DeliveryIntentRow))
            payload = json.loads(row.payload_json)
            payload["pages"].append(
                {
                    **payload["pages"][0],
                    "position": 1,
                    "source_url": "https://example.com/malformed-additive/second",
                }
            )
            row.payload_json = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            )
            row.payload_sha256 = hashlib.sha256(row.payload_json.encode()).hexdigest()
    receipt = _maya_receipt(pages=page_id_count, duplicate=duplicate)
    receipt["children_added"] = children_added
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, json=receipt)
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"retried": 1}
    row = _outbox_row(repository)
    assert row.status == "retry"
    assert row.last_error_code == "invalid_maya_receipt"


def test_remote_error_code_is_allowlisted_and_never_persists_assignment(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="unsafe remote code"), _response())
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "token=synthetic-secret-code",
                        "message": "request rejected",
                    }
                },
            )
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"dead_lettered": 1}
    row = _outbox_row(repository)
    assert row.last_error_code == "http_422"
    assert "synthetic-secret" not in row.last_error_code


def test_new_outbox_preserves_issue34_acceptance_fingerprint_shape():
    from argus.persistence.search_ledger import serialize_acceptance

    query = SearchQuery(query="atomic search")
    response = _response()
    serialized = serialize_acceptance(query, response)
    legacy_state = dict(serialized.state)
    legacy_state["delivery_intent"] = {
        "destination": "maya",
        "status": "pending",
        "payload": {
            "search_run_id": "run-1",
            "result_count": 1,
        },
    }
    expected = hashlib.sha256(
        json.dumps(
            legacy_state,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()

    assert serialized.fingerprint == expected


def test_extraction_acceptance_atomically_enqueues_parent_before_ordered_page(tmp_path):
    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/article",
        title="Article",
        text="the extracted page body",
        word_count=5,
        extracted_at=datetime(2026, 7, 23, 12, 0),
        extractor=ExtractorName.TRAFILATURA,
        source_type="live",
        egress="residential",
        machine="homelab",
        attempts=[
            ExtractionAttempt(
                extractor="trafilatura",
                status="success",
                latency_ms=4,
            )
        ],
    )

    receipt = repository.record_extraction(
        url=result.url,
        domain="example.com",
        mode="default",
        caller="maya",
        result=result,
        latency_ms=5,
        extraction_run_id="extract-1",
    )

    row = _outbox_row(repository)
    payload = json.loads(row.payload_json)
    assert receipt.delivery_intent_id == row.id
    assert row.run_id is None
    assert row.extraction_run_id
    assert payload["idempotency_key"] == "extract-1"
    assert [page["position"] for page in payload["pages"]] == [0]
    assert payload["pages"][0]["content"] == result.text
    assert payload["pages"][0]["content_sha256"] == row.content_sha256


def test_extraction_and_maya_outbox_roll_back_as_one_transaction(tmp_path):
    from argus.persistence.search_ledger import ExtractionRunRow

    repository = _repository(tmp_path)

    @event.listens_for(DeliveryIntentRow, "before_insert")
    def fail_outbox_insert(mapper, connection, target):
        raise RuntimeError("injected outbox failure")

    with pytest.raises(RuntimeError, match="injected outbox failure"):
        repository.record_extraction(
            url="https://example.com/rollback",
            domain=None,
            mode="default",
            caller="maya",
            result=ExtractedContent(
                url="https://example.com/rollback",
                text="must roll back",
                word_count=3,
                extractor=ExtractorName.TRAFILATURA,
                attempts=[ExtractionAttempt("trafilatura", "success", 1)],
            ),
            latency_ms=1,
            extraction_run_id="extract-outbox-rollback",
        )

    event.remove(DeliveryIntentRow, "before_insert", fail_outbox_insert)
    with repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ExtractionRunRow)) == 0
        assert session.scalar(select(func.count()).select_from(DeliveryIntentRow)) == 0


def test_transient_failure_is_restart_safe_and_replays_the_same_idempotency_key(
    tmp_path,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="restart safe"), _response())
    now = datetime(2026, 7, 23, 12, 0)
    requests = []

    def unavailable(request):
        requests.append(json.loads(request.content))
        return httpx.Response(503, json={"detail": "temporarily unavailable"})

    first = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/api/orchestration/captures/retrievals",
        token="test-token",
        transport=httpx.MockTransport(unavailable),
        clock=lambda: now,
    )
    assert first.run_once() == {"retried": 1}
    assert _outbox_row(repository).status == "retry"

    def accepted(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            201,
            json=_maya_receipt(),
        )

    restarted = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/api/orchestration/captures/retrievals",
        token="test-token",
        transport=httpx.MockTransport(accepted),
        clock=lambda: now + timedelta(minutes=1),
    )
    assert restarted.run_once() == {"acknowledged": 1}
    row = _outbox_row(repository)
    assert row.status == "acknowledged"
    assert row.attempt_count == 2
    assert requests[0]["idempotency_key"] == requests[1]["idempotency_key"]


def test_lost_ack_duplicate_and_partial_child_replay_are_acknowledged(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/article",
        text="child body",
        word_count=2,
        extractor=ExtractorName.TRAFILATURA,
        attempts=[ExtractionAttempt("trafilatura", "success", 1)],
    )
    repository.record_extraction(
        url=result.url,
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=1,
        extraction_run_id="partial-child",
    )
    now = datetime(2026, 7, 23, 12, 0)

    first = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("lost ack"))
        ),
        clock=lambda: now,
    )
    assert first.run_once() == {"retried": 1}

    def duplicate(request):
        assert len(json.loads(request.content)["pages"]) == 1
        return httpx.Response(
            200,
            json=_maya_receipt(pages=1, duplicate=True),
        )

    second = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(duplicate),
        clock=lambda: now + timedelta(minutes=1),
    )
    assert second.run_once() == {"acknowledged": 1}
    assert json.loads(_outbox_row(repository).response_json)["duplicate"] is True


def test_expired_delivery_lease_is_reclaimed_after_process_restart(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="crash recovery"), _response())
    now = datetime(2026, 7, 23, 12, 0)
    claimed = repository.claim_maya_outbox(now=now, limit=1, lease_seconds=10)
    assert len(claimed) == 1
    assert _outbox_row(repository).status == "delivering"

    restarted = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_maya_receipt(duplicate=True),
            )
        ),
        clock=lambda: now + timedelta(seconds=11),
    )

    assert restarted.run_once() == {"acknowledged": 1}
    assert _outbox_row(repository).attempt_count == 2


def test_sqlite_stale_worker_cannot_overwrite_reclaimed_lease(tmp_path):
    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="stale sqlite worker"), _response())
    started = datetime(2026, 7, 23, 12, 0)
    first = repository.claim_maya_outbox(
        now=started,
        limit=1,
        lease_seconds=10,
    )[0]
    second = repository.claim_maya_outbox(
        now=started + timedelta(seconds=11),
        limit=1,
        lease_seconds=10,
    )[0]

    assert (
        repository.acknowledge_maya_outbox(
            first["id"],
            lease_token=first["lease_token"],
            response=_maya_receipt(),
            now=started + timedelta(seconds=11),
        )
        is False
    )
    assert (
        repository.fail_maya_outbox(
            first["id"],
            lease_token=first["lease_token"],
            transient=False,
            error_code="stale_worker",
            error_summary="stale worker",
            now=started + timedelta(seconds=11),
        )
        is None
    )
    assert repository.acknowledge_maya_outbox(
        second["id"],
        lease_token=second["lease_token"],
        response=_maya_receipt(),
        now=started + timedelta(seconds=12),
    )
    assert _outbox_row(repository).status == "acknowledged"


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(204),
        lambda request: httpx.Response(200, json={"capture_id": "partial"}),
        lambda request: httpx.Response(503, json={"detail": "unavailable"}),
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("unavailable")),
    ],
    ids=["unsupported-204", "invalid-receipt", "http-503", "exception"],
)
def test_dispatcher_reports_lease_lost_for_every_stale_failure_branch(
    tmp_path,
    handler,
):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="stale dispatcher"), _response())
    started = datetime(2026, 7, 23, 12, 0)
    stale = repository.claim_maya_outbox(
        now=started,
        limit=1,
        lease_seconds=10,
    )[0]
    current = repository.claim_maya_outbox(
        now=started + timedelta(seconds=11),
        limit=1,
        lease_seconds=10,
    )[0]
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(handler),
        clock=lambda: started + timedelta(seconds=11),
    )

    with httpx.Client(
        transport=dispatcher.transport,
        timeout=dispatcher.timeout_seconds,
    ) as client:
        outcome = dispatcher._deliver_one(
            client,
            {
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
            stale,
        )

    row = _outbox_row(repository)
    assert outcome == "lease_lost"
    assert row.status == "delivering"
    assert row.lease_token == current["lease_token"]


def test_acknowledgement_rejects_oversized_audit_response(tmp_path):
    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="bounded acknowledgement"), _response())
    started = datetime(2026, 7, 23, 12, 0)
    claimed = repository.claim_maya_outbox(
        now=started,
        limit=1,
        lease_seconds=10,
    )[0]

    assert (
        repository.acknowledge_maya_outbox(
            claimed["id"],
            lease_token=claimed["lease_token"],
            response={"unexpected": "x" * 4096},
            now=started + timedelta(seconds=1),
        )
        is False
    )
    row = _outbox_row(repository)
    assert row.status == "delivering"
    assert row.response_json is None


def test_crash_during_final_attempt_expires_to_recoverable_dead_letter(tmp_path):
    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="final crash"), _response())
    row = _outbox_row(repository)
    with repository.session_factory.begin() as session:
        session.get(DeliveryIntentRow, row.id).max_attempts = 1
    now = datetime(2026, 7, 23, 12, 0)

    assert repository.claim_maya_outbox(
        now=now,
        limit=1,
        lease_seconds=10,
    )
    assert (
        repository.claim_maya_outbox(
            now=now + timedelta(seconds=11),
            limit=1,
            lease_seconds=10,
        )
        == []
    )

    row = _outbox_row(repository)
    assert row.status == "dead_letter"
    assert row.last_error_code == "retry_exhausted"
    assert repository.recover_maya_dead_letter(
        row.id,
        now=now + timedelta(seconds=12),
    )


def test_permanent_rejection_dead_letters_safely_and_can_be_recovered(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="dead letter"), _response())
    now = datetime(2026, 7, 23, 12, 0)
    rejected = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="secret-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                422,
                json={
                    "detail": {
                        "code": "invalid_capture_request",
                        "message": (
                            "Authorization: Bearer must-not-persist "
                            "ghp_abcdefghijklmnopqrstuvwxyz"
                        ),
                    }
                },
            )
        ),
        clock=lambda: now,
    )

    assert rejected.run_once() == {"dead_lettered": 1}
    row = _outbox_row(repository)
    assert row.status == "dead_letter"
    assert row.last_error_code == "invalid_capture_request"
    assert len(row.last_error_summary) <= 256
    assert "must-not-persist" not in row.last_error_summary
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in row.last_error_summary
    assert repository.list_maya_dead_letters() == [
        {
            "id": row.id,
            "attempt_count": 1,
            "last_error_code": "invalid_capture_request",
            "last_error_summary": row.last_error_summary,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    ]

    repository.recover_maya_dead_letter(row.id, now=now + timedelta(minutes=1))
    recovered = _outbox_row(repository)
    assert recovered.status == "pending"
    assert recovered.last_error_code is None


def test_transient_retries_are_bounded_and_exhaust_into_dead_letter(tmp_path):
    from argus.persistence.maya_outbox import MayaOutboxDispatcher

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="bounded retries"), _response())
    row = _outbox_row(repository)
    with repository.session_factory.begin() as session:
        session.get(DeliveryIntentRow, row.id).max_attempts = 1
    dispatcher = MayaOutboxDispatcher(
        repository,
        endpoint="http://maya/captures",
        token="test-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, json={"detail": "unavailable"})
        ),
        clock=lambda: datetime(2026, 7, 23, 12, 0),
    )

    assert dispatcher.run_once() == {"dead_lettered": 1}
    row = _outbox_row(repository)
    assert row.status == "dead_letter"
    assert row.last_error_code == "retry_exhausted"


def test_acknowledged_payload_compaction_is_bounded_and_keeps_audit(tmp_path):
    from argus.persistence.search_ledger import acceptance_fingerprint

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="compact"), _response())
    row = _outbox_row(repository)
    old = datetime(2026, 7, 1)
    with repository.session_factory.begin() as session:
        stored = session.get(DeliveryIntentRow, row.id)
        stored.status = "acknowledged"
        stored.delivered_at = old
        stored.response_json = json.dumps({"capture_id": "capture-1"})

    compacted = repository.compact_maya_outbox(
        acknowledged_before=datetime(2026, 7, 8),
        limit=1,
        now=datetime(2026, 7, 23),
    )

    row = _outbox_row(repository)
    assert compacted == 1
    assert row.payload_json is None
    assert row.payload_sha256
    assert row.response_json == json.dumps({"capture_id": "capture-1"})
    assert row.payload_compacted_at == datetime(2026, 7, 23)
    snapshot = repository.load_acceptance_snapshot("run-1")
    assert acceptance_fingerprint(snapshot.state) == snapshot.stored_fingerprint


def test_synthetic_probe_is_operationally_recorded_without_maya_artifact(tmp_path):
    repository = _repository(tmp_path)
    repository.accept(
        SearchQuery(query="deployment canary", caller="deployment-probe"),
        _response(run_id="probe-1"),
    )

    row = _outbox_row(repository)
    assert row.status == "suppressed"
    assert row.payload_json is None


def test_reachability_probe_is_recorded_without_maya_artifact(tmp_path):
    repository = _repository(tmp_path)

    repository.accept(
        SearchQuery(query="provider reachability", caller="argus-reachability"),
        _response(run_id="reachability-probe"),
    )

    row = _outbox_row(repository)
    assert row.status == "suppressed"
    assert row.payload_json is None


def test_explicit_operational_query_is_audited_without_maya_artifact(tmp_path):
    repository = _repository(tmp_path)

    repository.accept(
        SearchQuery(
            query="provider smoke",
            caller="authenticated-admin",
            user_visible=False,
        ),
        _response(run_id="operational-provider-smoke"),
    )

    row = _outbox_row(repository)
    assert row.status == "suppressed"
    assert row.payload_json is None


def test_outbox_observability_reports_bounded_counts_and_oldest_age(tmp_path):
    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="observable"), _response())
    row = _outbox_row(repository)
    with repository.session_factory.begin() as session:
        session.get(DeliveryIntentRow, row.id).created_at = datetime(2026, 7, 23, 11, 0)

    status = repository.maya_outbox_status(now=datetime(2026, 7, 23, 12, 0))

    assert status == {
        "counts": {"pending": 1},
        "oldest_pending_age_seconds": 3600,
        "dead_letter_oldest_age_seconds": None,
        "dead_letter_payload_bytes": 0,
    }


def test_maya_capture_config_uses_dedicated_secret_and_bounded_worker_settings():
    from argus.config import load_config

    config = load_config(
        environ={
            "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
            "ARGUS_MAYA_CAPTURE_URL": "http://maya/api/orchestration/captures/retrievals",
            "ARGUS_MAYA_CAPTURE_TOKEN": "dedicated-token",
            "ARGUS_MAYA_OUTBOX_BATCH_SIZE": "5000",
            "ARGUS_MAYA_OUTBOX_POLL_SECONDS": "0",
        }
    )

    assert config.maya_capture.endpoint.endswith("/captures/retrievals")
    assert config.maya_capture.token == "dedicated-token"
    assert config.maya_capture.batch_size == 100
    assert config.maya_capture.poll_seconds == 1


def test_migration_suppresses_and_compacts_historical_placeholder_intents(
    tmp_path,
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    database = tmp_path / "historical-outbox.db"
    url = f"sqlite:///{database}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0005_provider_spend")
    historical_payload = json.dumps(
        {"search_run_id": "historical-run", "result_count": 2},
        sort_keys=True,
        separators=(",", ":"),
    )
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO retrieval_requests "
                "(id, query_text, mode, max_results, caller, created_at, "
                "providers_json, free_only) "
                "VALUES "
                "('request-1', 'historical query', 'discovery', 10, 'maya', "
                "'2026-07-01 12:00:00', NULL, false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO retrieval_runs "
                "(id, request_id, search_run_id, status, total_results, cached, "
                "started_at, committed_at, acceptance_fingerprint) "
                "VALUES "
                "('run-1', 'request-1', 'historical-run', 'accepted', 2, false, "
                "'2026-07-01 12:00:00', '2026-07-01 12:00:01', :fingerprint)"
            ),
            {"fingerprint": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO delivery_intents "
                "(id, run_id, destination, status, payload_json, created_at) "
                "VALUES "
                "('intent-1', 'run-1', 'maya', 'pending', :payload, "
                "'2026-07-01 12:00:01')"
            ),
            {"payload": historical_payload},
        )

    command.upgrade(config, "head")

    columns = {
        column["name"] for column in inspect(engine).get_columns("delivery_intents")
    }
    assert {
        "extraction_run_id",
        "payload_sha256",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "updated_at",
        "payload_compacted_at",
    } <= columns
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT run_id, extraction_run_id, status, payload_json, "
                    "payload_sha256, attempt_count, max_attempts, next_attempt_at, "
                    "updated_at, payload_compacted_at "
                    "FROM delivery_intents WHERE id = 'intent-1'"
                )
            )
            .mappings()
            .one()
        )
    assert row["run_id"] == "run-1"
    assert row["extraction_run_id"] is None
    assert row["status"] == "suppressed"
    assert row["payload_json"] is None
    assert (
        row["payload_sha256"]
        == hashlib.sha256(historical_payload.encode("utf-8")).hexdigest()
    )
    assert row["attempt_count"] == 0
    assert row["max_attempts"] == 8
    assert row["next_attempt_at"] == row["updated_at"]
    assert row["payload_compacted_at"] == row["updated_at"]

    from argus.persistence.search_ledger import create_search_ledger_repository

    repository = create_search_ledger_repository(url, create_schema=False)
    assert (
        repository.claim_maya_outbox(
            now=datetime(2026, 7, 23, 12, 0),
            limit=10,
        )
        == []
    )

    command.downgrade(config, "0005_provider_spend")
    downgraded_columns = {
        column["name"] for column in inspect(engine).get_columns("delivery_intents")
    }
    assert "extraction_run_id" not in downgraded_columns
    with engine.connect() as connection:
        downgraded = (
            connection.execute(
                text(
                    "SELECT status, payload_json "
                    "FROM delivery_intents WHERE id = 'intent-1'"
                )
            )
            .mappings()
            .one()
        )
    assert downgraded == {
        "status": "pending",
        "payload_json": historical_payload,
    }


def test_postgresql_concurrent_workers_claim_each_intent_once(postgres_ledger_url):
    from alembic import command
    from alembic.config import Config

    from argus.persistence.search_ledger import create_search_ledger_repository

    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        postgres_ledger_url.replace("%", "%%"),
    )
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    repository = create_search_ledger_repository(postgres_ledger_url)
    repository.accept(
        SearchQuery(query="single claim", caller="maya"),
        _response(run_id="postgres-single-claim"),
    )
    barrier = Barrier(2)
    now = datetime(2026, 7, 23, 12, 0)

    def claim():
        barrier.wait()
        return repository.claim_maya_outbox(now=now, limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))

    claimed_ids = [row["id"] for batch in claims for row in batch]
    assert len(claimed_ids) == 1
    assert len(set(claimed_ids)) == 1


def test_postgresql_reclaim_race_rejects_expired_worker(postgres_ledger_url):
    from alembic import command
    from alembic.config import Config

    from argus.persistence.search_ledger import create_search_ledger_repository

    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        postgres_ledger_url.replace("%", "%%"),
    )
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    repository = create_search_ledger_repository(postgres_ledger_url)
    repository.accept(
        SearchQuery(query="postgres stale worker", caller="maya"),
        _response(run_id="postgres-stale-worker"),
    )
    started = datetime(2026, 7, 23, 12, 0)
    stale = repository.claim_maya_outbox(
        now=started,
        limit=1,
        lease_seconds=10,
    )[0]
    barrier = Barrier(2)

    def reclaim():
        barrier.wait()
        return repository.claim_maya_outbox(
            now=started + timedelta(seconds=11),
            limit=1,
            lease_seconds=10,
        )

    def stale_ack():
        barrier.wait()
        return repository.acknowledge_maya_outbox(
            stale["id"],
            lease_token=stale["lease_token"],
            response=_maya_receipt(),
            now=started + timedelta(seconds=11),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        reclaimed_future = pool.submit(reclaim)
        stale_future = pool.submit(stale_ack)
        reclaimed = reclaimed_future.result()
        stale_result = stale_future.result()

    assert stale_result is False
    assert len(reclaimed) == 1
    assert reclaimed[0]["lease_token"] != stale["lease_token"]


def test_postgresql_migration_suppresses_historical_placeholder_intents(
    postgres_ledger_url,
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        postgres_ledger_url.replace("%", "%%"),
    )
    command.downgrade(config, "base")
    command.upgrade(config, "0005_provider_spend")
    historical_payload = json.dumps(
        {"search_run_id": "postgres-historical-run", "result_count": 2},
        sort_keys=True,
        separators=(",", ":"),
    )
    engine = create_engine(postgres_ledger_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO retrieval_requests "
                "(id, query_text, mode, max_results, caller, created_at, "
                "providers_json, free_only) "
                "VALUES "
                "('request-1', 'historical query', 'discovery', 10, 'maya', "
                "'2026-07-01 12:00:00', NULL, false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO retrieval_runs "
                "(id, request_id, search_run_id, status, total_results, cached, "
                "started_at, committed_at, acceptance_fingerprint) "
                "VALUES "
                "('run-1', 'request-1', 'postgres-historical-run', 'accepted', "
                "2, false, '2026-07-01 12:00:00', '2026-07-01 12:00:01', "
                ":fingerprint)"
            ),
            {"fingerprint": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO delivery_intents "
                "(id, run_id, destination, status, payload_json, created_at) "
                "VALUES "
                "('intent-1', 'run-1', 'maya', 'pending', :payload, "
                "'2026-07-01 12:00:01')"
            ),
            {"payload": historical_payload},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT status, payload_json, payload_sha256, attempt_count, "
                    "max_attempts, next_attempt_at, updated_at, payload_compacted_at "
                    "FROM delivery_intents WHERE id = 'intent-1'"
                )
            )
            .mappings()
            .one()
        )
    assert row["status"] == "suppressed"
    assert row["payload_json"] is None
    assert (
        row["payload_sha256"]
        == hashlib.sha256(historical_payload.encode("utf-8")).hexdigest()
    )
    assert row["attempt_count"] == 0
    assert row["max_attempts"] == 8
    assert row["next_attempt_at"] == row["updated_at"]
    assert row["payload_compacted_at"] == row["updated_at"]
