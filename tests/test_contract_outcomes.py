"""Frozen tests for the accepted-operation contract kernel."""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import pytest


EXPECTED = {
    "success": (200, False),
    "degraded": (200, False),
    "empty": (200, False),
    "invalid_request": (422, True),
    "authentication_rejected": (401, True),
    "policy_rejected": (403, True),
    "timeout": (504, True),
    "persistence_failed": (503, True),
    "providers_failed": (502, True),
    "extraction_failed": (502, True),
    "unready": (503, True),
}

NARROW = {
    "malformed_request": ("invalid_request", 400),
    "payload_too_large": ("invalid_request", 413),
    "unsupported_media_type": ("invalid_request", 415),
    "route_not_found": ("invalid_request", 404),
    "idempotency_conflict": ("invalid_request", 409),
    "session_not_found": ("unready", 404),
    "rate_limited": ("unready", 429),
    "internal_failure": ("unready", 503),
}

V1_SHA256 = {
    "http_authentication_rejected.json": (
        "fe6df51e5393e58b9f7013d9f118bf359af42acc33d53a4818e3740a9c91ee71"
    ),
    "http_persistence_failure.json": (
        "53041873f7f33ca31661a349dbc97d8bacdda2f2832ddde5266d895ff87be63c"
    ),
    "http_rate_limited.json": (
        "6e211ce8edeaf2dc489c73b6179da5f642b8f1a6c6c68d0572dad5b3e1276024"
    ),
    "http_recover_archive_success.json": (
        "db6b6064f775b375cc69e64aae68a65800ec196d69ff4d36a20a0d8d81516c74"
    ),
    "http_search_success.json": (
        "52255c0c0c256c33c9de08e4be52e7b911926eda81e1fd602d7f1787c0cc13df"
    ),
    "http_validation_error.json": (
        "17756a11c2f4fd42b53565ae9cf919b54b754c4c4043201c331ba1a773c2100e"
    ),
    "mcp_extract_success.json": (
        "4ef543803ed8fee2f86660588ecf55852564b745204822c77f48a31afc3617a6"
    ),
    "mcp_read_pack_file_rejected.json": (
        "4723e0d55e13fe19e559834625978ebd9962b2e7425c97fe4cd516587d740068"
    ),
    "mcp_read_pack_file_utf8.json": (
        "61e0169a74bb79fa94076f8680ff16474dc849543c115c4bf8c324c94c0595a6"
    ),
    "mcp_search_success.json": (
        "fc5e9899eac78cec97a1af75734e60fc732a648b8e4b8119284c39eb3819c4e9"
    ),
    "mcp_workflow_json_failed.json": (
        "00bfcc9fde50a6c09478597b5c43861e10a5c08d4c1b79bfbd440b64cbdce07f"
    ),
    "mcp_workflow_success.json": (
        "16bdebcc9a527f6b03b77ae322407ab935c6b3a4429db335de9c2b4f91a66461"
    ),
}

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "tests/fixtures/contracts/retrieval_evidence_v2"
TRANSPORT_ROOT = ROOT / "tests/fixtures/transports"
FORBIDDEN_KEYS = {
    "authorization",
    "headers",
    "cookies",
    "credentials",
    "raw_payload",
    "raw_body",
    "raw_error",
    "provider_payload",
}
FORBIDDEN_TEXT = ("authorization: bearer", "bearer sk-", "api_key=", "token=")


def _contracts():
    return importlib.import_module("argus.contracts")


def _error(
    outcome,
    *,
    code: str | None = None,
    status: int | None = None,
    title: str | None = None,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
    operation_began: bool | None = None,
):
    contracts = _contracts()
    actual_code = code or outcome.value
    actual_status = (
        contracts.http_status_for(outcome, actual_code)
        if status is None
        else status
    )
    arguments = {
        "outcome": outcome,
        "type": f"urn:argus:problem:{actual_code}",
        "title": title or actual_code.replace("_", " ").title(),
        "status": actual_status,
        "detail": actual_code.replace("_", " "),
        "instance": "urn:argus:request:request-1",
        "code": actual_code,
        "retryable": retryable,
        "retry_after_seconds": retry_after_seconds,
    }
    if operation_began is not None:
        arguments["operation_began"] = operation_began
    return contracts.OperationError(**arguments)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze(child) for key, child in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _walk(value: Any):
    yield value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield key
            yield from _walk(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk(child)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_canonical_outcomes_are_closed_and_in_scorecard_order():
    contracts = _contracts()

    assert [outcome.value for outcome in contracts.CanonicalOutcome] == list(
        EXPECTED
    )


@pytest.mark.parametrize(("name", "expected"), EXPECTED.items())
def test_default_status_and_mcp_mapping(name, expected):
    contracts = _contracts()
    outcome = contracts.CanonicalOutcome(name)
    status, is_error = expected

    assert contracts.http_status_for(outcome) == status
    assert contracts.http_status_for(outcome, name) == status
    assert contracts.mcp_is_error_for(outcome) is is_error
    assert contracts.is_success_like(outcome) is (not is_error)


@pytest.mark.parametrize(("code", "case"), NARROW.items())
def test_narrow_transport_codes_preserve_their_canonical_outcome(code, case):
    contracts = _contracts()
    outcome_name, status = case
    outcome = contracts.CanonicalOutcome(outcome_name)

    assert contracts.http_status_for(outcome, code) == status
    assert contracts.mcp_is_error_for(outcome) is True


def test_unknown_or_reclassifying_transport_codes_fail_closed():
    contracts = _contracts()

    with pytest.raises(ValueError, match="unknown error code"):
        contracts.http_status_for(
            contracts.CanonicalOutcome.UNREADY,
            "future_unreviewed_code",
        )
    with pytest.raises(ValueError, match="not valid for outcome"):
        contracts.http_status_for(
            contracts.CanonicalOutcome.PROVIDERS_FAILED,
            "malformed_request",
        )
    with pytest.raises(ValueError):
        contracts.CanonicalOutcome("provider_authentication_rejected")


def test_operation_error_contains_only_stable_public_fields():
    contracts = _contracts()

    assert [field.name for field in fields(contracts.OperationError)] == [
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "code",
        "retryable",
        "retry_after_seconds",
    ]
    assert [field.name for field in fields(contracts.AcceptedOperation)] == [
        "outcome",
        "request_id",
        "result",
        "error",
        "contract_version",
    ]


def test_operation_error_rejects_a_status_that_disagrees_with_the_outcome():
    contracts = _contracts()

    with pytest.raises(ValueError, match="status"):
        _error(
            contracts.CanonicalOutcome.INVALID_REQUEST,
            status=500,
        )


def test_operation_error_rejects_retry_after_without_retryable():
    contracts = _contracts()

    with pytest.raises(ValueError, match="retry_after_seconds"):
        contracts.OperationError(
            outcome=contracts.CanonicalOutcome.UNREADY,
            type="urn:argus:problem:unready",
            title="Unready",
            status=503,
            detail="Unready",
            instance="urn:argus:request:request-1",
            code="unready",
            retryable=False,
            retry_after_seconds=1,
        )


def test_operation_error_title_is_canonical_for_its_code():
    contracts = _contracts()

    with pytest.raises(ValueError, match="title"):
        _error(
            contracts.CanonicalOutcome.UNREADY,
            title="A caller-specific unready title",
        )


def test_operation_error_retry_delay_has_a_finite_inclusive_maximum():
    contracts = _contracts()

    accepted = _error(
        contracts.CanonicalOutcome.UNREADY,
        retryable=True,
        retry_after_seconds=86_400,
    )
    assert accepted.retry_after_seconds == 86_400
    with pytest.raises(ValueError, match="retry_after_seconds"):
        _error(
            contracts.CanonicalOutcome.UNREADY,
            retryable=True,
            retry_after_seconds=86_401,
        )


@pytest.mark.parametrize("name", ("success", "degraded", "empty"))
def test_success_like_operation_requires_an_object_result_and_no_error(name):
    contracts = _contracts()
    outcome = contracts.CanonicalOutcome(name)
    operation = contracts.AcceptedOperation(
        outcome=outcome,
        request_id="request-1",
        result={"accepted": True},
        error=None,
    )

    assert operation.contract_version == "2.0"
    assert operation.result == {"accepted": True}
    with pytest.raises(FrozenInstanceError):
        operation.request_id = "changed"
    with pytest.raises(ValueError, match="object result"):
        contracts.AcceptedOperation(
            outcome=outcome,
            request_id="request-1",
            result=None,
            error=None,
        )
    with pytest.raises(ValueError, match="must not have an error"):
        contracts.AcceptedOperation(
            outcome=outcome,
            request_id="request-1",
            result={"accepted": True},
            error=_error(contracts.CanonicalOutcome.INVALID_REQUEST),
        )


@pytest.mark.parametrize(
    "name",
    tuple(name for name, (_, is_error) in EXPECTED.items() if is_error),
)
def test_failure_operation_requires_an_error(name):
    contracts = _contracts()
    outcome = contracts.CanonicalOutcome(name)
    error = _error(outcome)
    operation = contracts.AcceptedOperation(
        outcome=outcome,
        request_id="request-1",
        result=None,
        error=error,
    )

    assert operation.error is error
    with pytest.raises(ValueError, match="requires an error"):
        contracts.AcceptedOperation(
            outcome=outcome,
            request_id="request-1",
            result=None,
            error=None,
        )


def test_failure_operation_may_retain_only_an_object_result():
    contracts = _contracts()
    outcome = contracts.CanonicalOutcome.EXTRACTION_FAILED

    operation = contracts.AcceptedOperation(
        outcome=outcome,
        request_id="request-1",
        result={"diagnostic_results": []},
        error=_error(outcome),
    )

    assert operation.result == {"diagnostic_results": ()}
    with pytest.raises(ValueError, match="object result"):
        contracts.AcceptedOperation(
            outcome=outcome,
            request_id="request-1",
            result=["not", "an", "object"],
            error=_error(outcome),
        )


def test_failure_operation_rejects_an_error_for_another_request():
    contracts = _contracts()
    outcome = contracts.CanonicalOutcome.UNREADY

    with pytest.raises(ValueError, match="request_id"):
        contracts.AcceptedOperation(
            outcome=outcome,
            request_id="request-2",
            result=None,
            error=_error(outcome),
        )


@pytest.mark.parametrize(("code", "case"), NARROW.items())
def test_admission_only_codes_are_rejected_after_operation_begins(code, case):
    contracts = _contracts()
    outcome_name, status = case
    outcome = contracts.CanonicalOutcome(outcome_name)

    with pytest.raises(ValueError, match="admission"):
        _error(outcome, code=code, status=status)

    error = _error(
        outcome,
        code=code,
        status=status,
        operation_began=False,
    )
    with pytest.raises(ValueError, match="admission"):
        contracts.AcceptedOperation(
            outcome=outcome,
            request_id="request-1",
            result=None,
            error=error,
        )
    operation = contracts.AcceptedOperation(
        outcome=outcome,
        request_id="request-1",
        result=None,
        error=error,
        operation_began=False,
    )
    assert operation.error is error


def test_result_is_recursively_immutable_and_rejects_non_json_leaves():
    contracts = _contracts()
    source = {"nested": {"items": [{"accepted": True}]}}
    operation = contracts.AcceptedOperation(
        outcome=contracts.CanonicalOutcome.SUCCESS,
        request_id="request-1",
        result=source,
        error=None,
    )

    source["nested"]["items"][0]["accepted"] = False
    assert operation.result["nested"]["items"][0]["accepted"] is True
    with pytest.raises(TypeError):
        operation.result["nested"]["items"][0]["accepted"] = False

    unsupported = (object(), b"bytes", {"set"}, float("nan"), float("inf"))
    for value in unsupported:
        with pytest.raises(ValueError, match="JSON"):
            contracts.AcceptedOperation(
                outcome=contracts.CanonicalOutcome.SUCCESS,
                request_id="request-1",
                result={"unsupported": value},
                error=None,
            )
    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError, match="acyclic JSON"):
        contracts.AcceptedOperation(
            outcome=contracts.CanonicalOutcome.SUCCESS,
            request_id="request-1",
            result=cycle,
            error=None,
        )


@pytest.mark.parametrize(
    "request_id",
    (
        "",
        "x" * 65,
        "has/slash",
        "has whitespace",
        "query=https://example.test/?token=secret",
    ),
)
def test_request_id_must_be_bounded_before_construction(request_id):
    contracts = _contracts()

    with pytest.raises(ValueError, match="request_id"):
        contracts.AcceptedOperation(
            outcome=contracts.CanonicalOutcome.SUCCESS,
            request_id=request_id,
            result={"accepted": True},
            error=None,
        )


def test_contract_version_is_exact():
    contracts = _contracts()

    with pytest.raises(ValueError, match="contract_version"):
        contracts.AcceptedOperation(
            outcome=contracts.CanonicalOutcome.SUCCESS,
            request_id="request-1",
            result={"accepted": True},
            error=None,
            contract_version="2.1",
        )


def test_retrieval_evidence_manifest_hashes_every_source_and_fixture():
    manifest = _load_json(EVIDENCE_ROOT / "manifest.json")

    assert manifest["valid_vector_count"] == 8
    assert manifest["invalid_mutation_count"] == 19
    assert len(manifest["fixtures"]) == 27
    assert {path.name for path in EVIDENCE_ROOT.glob("*.json")} == {
        "manifest.json",
        *(entry["path"] for entry in manifest["fixtures"]),
    }
    for entry in manifest["sources"]:
        path = ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    for entry in manifest["fixtures"]:
        path = EVIDENCE_ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]


def test_every_retrieval_fixture_loads_immutably_and_obeys_outer_contract():
    contracts = _contracts()
    manifest = _load_json(EVIDENCE_ROOT / "manifest.json")

    for entry in manifest["fixtures"]:
        fixture = _freeze(_load_json(EVIDENCE_ROOT / entry["path"]))
        assert set(fixture) == {
            "contract_version",
            "outcome",
            "request_id",
            "result",
            "error",
        }
        assert isinstance(fixture, MappingProxyType)
        with pytest.raises(TypeError):
            fixture["outcome"] = "changed"
        error_data = fixture["error"]
        outcome = contracts.CanonicalOutcome(fixture["outcome"])
        error = (
            None
            if error_data is None
            else contracts.OperationError(
                outcome=outcome,
                **dict(error_data),
            )
        )
        operation = contracts.AcceptedOperation(
            contract_version=fixture["contract_version"],
            outcome=outcome,
            request_id=fixture["request_id"],
            result=fixture["result"],
            error=error,
        )
        evidence = fixture["result"]["evidence"]
        assert operation.request_id == evidence["request"]["request_id"]
        assert operation.outcome.value == evidence["final"]["outcome"]


def test_invalid_retrieval_mutations_remain_frozen_for_later_replay():
    manifest = _load_json(EVIDENCE_ROOT / "manifest.json")
    invalid = [
        entry
        for entry in manifest["fixtures"]
        if entry["kind"] == "invalid_mutation"
    ]

    assert len(invalid) == 19
    assert all(entry["expected_invariant"] for entry in invalid)
    assert len({entry["mutation"] for entry in invalid}) == 19


def test_retrieval_fixtures_reject_private_fields_and_credential_like_text():
    manifest = _load_json(EVIDENCE_ROOT / "manifest.json")

    for entry in manifest["fixtures"]:
        fixture = _load_json(EVIDENCE_ROOT / entry["path"])
        for value in _walk(fixture):
            if isinstance(value, str):
                assert value.lower() not in FORBIDDEN_KEYS
                assert not any(
                    marker in value.lower() for marker in FORBIDDEN_TEXT
                )


def test_v1_transport_goldens_have_named_inputs_status_and_response_values():
    fixtures = sorted((TRANSPORT_ROOT / "v1").glob("*.json"))

    assert {path.name for path in fixtures} == set(V1_SHA256)
    assert {(_load_json(path)["transport"]) for path in fixtures} == {
        "http",
        "mcp",
    }
    for path in fixtures:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == V1_SHA256[
            path.name
        ]
        fixture = _load_json(path)
        assert set(fixture) == {
            "transport",
            "name",
            "input",
            "status",
            "response_value",
        }
        assert fixture["name"]
        assert isinstance(fixture["input"], dict)
        assert isinstance(fixture["status"], dict)
        assert "response_value" in fixture


def test_v2_transport_goldens_cover_default_and_narrow_mappings():
    contracts = _contracts()
    fixtures = sorted((TRANSPORT_ROOT / "v2").glob("*.json"))

    assert len(fixtures) == len(EXPECTED) + len(NARROW)
    observed = set()
    for path in fixtures:
        fixture = _load_json(path)
        response = fixture["response_value"]
        assert set(response) == {
            "contract_version",
            "outcome",
            "request_id",
            "result",
            "error",
        }
        outcome = contracts.CanonicalOutcome(response["outcome"])
        error_data = response["error"]
        error = (
            None
            if error_data is None
            else contracts.OperationError(
                outcome=outcome,
                operation_began=not path.name.startswith("narrow_"),
                **error_data,
            )
        )
        operation = contracts.AcceptedOperation(
            contract_version=response["contract_version"],
            outcome=outcome,
            request_id=response["request_id"],
            result=response["result"],
            error=error,
            operation_began=not path.name.startswith("narrow_"),
        )
        code = error.code if error is not None else outcome.value
        observed.add((outcome.value, code))
        assert fixture["status"] == {
            "http_status": contracts.http_status_for(outcome, code),
            "mcp_is_error": contracts.mcp_is_error_for(outcome),
        }
        assert operation.outcome is outcome

    assert observed == {
        *((name, name) for name in EXPECTED),
        *((outcome, code) for code, (outcome, _) in NARROW.items()),
    }
