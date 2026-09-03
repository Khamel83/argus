import pytest

from argus.contracts import (
    AcceptedOperation,
    CanonicalOutcome,
    EvidenceIdentity,
    FailureCode,
    FailureRecord,
    ReleaseIdentity,
    SchemaIdentity,
    failure_codes,
    failure_spec,
    operation_error_for,
)


def _identities():
    schema = SchemaIdentity(
        "0010_domain_policies",
        "a" * 64,
        "b" * 64,
        "argus-schema-contract-v1",
    )
    release = ReleaseIdentity(
        "c" * 40,
        "sha256:" + "d" * 64,
        "e" * 64,
        "f" * 64,
    )
    return schema, release


def test_failure_taxonomy_is_closed_and_each_code_has_one_mapping():
    codes = failure_codes()
    assert codes == tuple(sorted(codes, key=lambda code: code.value))
    assert len(codes) == len(set(codes))
    for code in codes:
        spec = failure_spec(code)
        assert spec.code is code
        assert isinstance(spec.outcome, CanonicalOutcome)
        assert (400 <= spec.status <= 599) or spec.status == 202


@pytest.mark.parametrize("code", tuple(FailureCode))
def test_failure_record_and_operation_error_are_bounded(code):
    record = FailureRecord(
        code=code,
        safe_reason="The operation was stopped by an approved boundary",
        request_id="request-1",
        operation_id="operation-1",
        release_identity="release-1",
        evidence_references=("evidence-1",),
    )
    error = operation_error_for(record)
    spec = failure_spec(code)
    assert error.code == code.value
    assert error.status == spec.status
    assert error.instance == "urn:argus:request:request-1"
    operation = AcceptedOperation(
        outcome=spec.outcome,
        request_id="request-1",
        result=None,
        error=error,
        operation_began=spec.operation_began,
    )
    assert operation.outcome is spec.outcome


def test_identity_chain_is_immutable_and_canonical():
    schema, release = _identities()
    evidence = EvidenceIdentity(
        operation_id="operation-1",
        request_id="request-1",
        release_identity=release,
        schema_identity=schema,
        receipt_identity="receipt-1",
    )
    assert schema.as_tuple()[0] == "0010_domain_policies"
    assert len(schema.identity_id) == 64
    assert len(release.release_id) == 64
    assert len(evidence.evidence_id) == 64
    with pytest.raises((AttributeError, TypeError)):
        evidence.request_id = "other"  # type: ignore[misc]


def test_sensitive_failure_detail_is_rejected():
    with pytest.raises(ValueError, match="unsafe"):
        FailureRecord(
            code=FailureCode.POLICY_REJECTED,
            safe_reason="token=do-not-store",
            request_id="request-1",
        )


def test_schema_and_release_digest_validation_is_strict():
    schema, release = _identities()
    with pytest.raises(ValueError):
        SchemaIdentity(
            schema.schema_head,
            "not-a-digest",
            schema.canonical_postgresql_schema_sha256,
            schema.schema_contract_format,
        )
    with pytest.raises(ValueError):
        ReleaseIdentity(
            release.source_revision,
            "sha256:" + "D" * 64,
            release.release_descriptor_digest,
            release.runtime_manifest_digest,
        )
