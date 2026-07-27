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


def _error(outcome, *, code: str | None = None, status: int | None = None):
    contracts = _contracts()
    actual_code = code or outcome.value
    actual_status = (
        contracts.http_status_for(outcome, actual_code)
        if status is None
        else status
    )
    return contracts.OperationError(
        outcome=outcome,
        type=f"urn:argus:problem:{actual_code}",
        title=actual_code.replace("_", " ").title(),
        status=actual_status,
        detail=actual_code.replace("_", " "),
        instance="urn:argus:request:request-1",
        code=actual_code,
        retryable=False,
        retry_after_seconds=None,
    )


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
    for entry in manifest["sources"]:
        path = ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    for entry in manifest["fixtures"]:
        path = EVIDENCE_ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]


def test_valid_retrieval_evidence_is_an_exact_standalone_copy():
    source_vectors = {
        vector["scenario"]: vector
        for vector in _load_json(
            ROOT / "docs/prototypes/retrieval-evidence-envelope/vectors.json"
        )
    }
    manifest = _load_json(EVIDENCE_ROOT / "manifest.json")

    for entry in manifest["fixtures"]:
        if entry["kind"] != "valid":
            continue
        fixture = _load_json(EVIDENCE_ROOT / entry["path"])
        assert fixture["result"]["evidence"] == source_vectors[entry["scenario"]]


def test_every_retrieval_fixture_loads_immutably_and_obeys_outer_contract():
    contracts = _contracts()
    manifest = _load_json(EVIDENCE_ROOT / "manifest.json")

    for entry in manifest["fixtures"]:
        fixture = _freeze(_load_json(EVIDENCE_ROOT / entry["path"]))
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

    assert len(fixtures) == 12
    assert {(_load_json(path)["transport"]) for path in fixtures} == {
        "http",
        "mcp",
    }
    for path in fixtures:
        fixture = _load_json(path)
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
        outcome = contracts.CanonicalOutcome(response["outcome"])
        error_data = response["error"]
        error = (
            None
            if error_data is None
            else contracts.OperationError(outcome=outcome, **error_data)
        )
        operation = contracts.AcceptedOperation(
            contract_version=response["contract_version"],
            outcome=outcome,
            request_id=response["request_id"],
            result=response["result"],
            error=error,
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
