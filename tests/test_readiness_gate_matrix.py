"""Pure matrix tests for the conditional acceptance-v3 readiness gates."""

from __future__ import annotations

from argus.acceptance_v3.readiness import evaluate_readiness_gates


def _accepted(*, capture_id: str | None = None) -> dict[str, str]:
    value = {
        "status": "accepted",
        "operation_id": "operation-1",
        "request_id": "request-1",
        "release_identity": "release-1",
        "schema_identity": "schema-1",
        "receipt_identity": "receipt-1",
        "result_hash": "sha256:result-1",
    }
    if capture_id is not None:
        value["maya_capture_id"] = capture_id
    return value


def _outbox(*, status: str = "acknowledged", capture_id: str | None = None):
    value = {
        "status": status,
        "operation_id": "operation-1",
        "request_id": "request-1",
        "release_identity": "release-1",
        "schema_identity": "schema-1",
        "receipt_identity": "receipt-1",
        "result_hash": "sha256:result-1",
    }
    if capture_id is not None:
        value["maya_capture_id"] = capture_id
    return value


def _maya_receipt(*, status: str = "acknowledged", capture_id: str = "capture-1"):
    return {
        "status": status,
        "operation_id": "operation-1",
        "request_id": "request-1",
        "release_identity": "release-1",
        "schema_identity": "schema-1",
        "receipt_identity": "receipt-1",
        "result_hash": "sha256:result-1",
        "maya_capture_id": capture_id,
    }


def test_core_readiness_does_not_require_delivery():
    verdict = evaluate_readiness_gates(_accepted(), level="core")

    assert verdict["status"] == "PASS"
    assert verdict["outcome"] == "success"
    assert verdict["gates"]["delivery_intent"]["status"] == "PASS"
    assert verdict["gates"]["delivery_receipt"]["status"] == "PASS"


def test_requested_delivery_without_outbox_fails_closed():
    verdict = evaluate_readiness_gates(
        _accepted(),
        level="core_integration",
        delivery_requested=True,
    )

    assert verdict["status"] == "FAIL"
    assert verdict["outcome"] == "delivery_failed"
    assert verdict["gates"]["delivery_intent"]["status"] == "FAIL"


def test_pending_outbox_is_not_reported_as_success():
    verdict = evaluate_readiness_gates(
        _accepted(),
        level="core_integration",
        delivery_requested=True,
        outbox=_outbox(status="delivering"),
    )

    assert verdict["status"] == "PENDING"
    assert verdict["outcome"] == "delivery_pending"
    assert verdict["gates"]["delivery_intent"]["status"] == "PENDING"


def test_outbox_identity_mismatch_fails_closed():
    outbox = _outbox()
    outbox["release_identity"] = "release-other"

    verdict = evaluate_readiness_gates(
        _accepted(),
        level="core_integration",
        delivery_requested=True,
        outbox=outbox,
    )

    assert verdict["status"] == "FAIL"
    assert verdict["outcome"] == "delivery_failed"
    assert (
        "release_identity_mismatch" in verdict["gates"]["delivery_intent"]["evidence"]
    )


def test_full_fleet_requires_a_correlated_maya_receipt():
    accepted = _accepted(capture_id="capture-1")
    outbox = _outbox(capture_id="capture-1")

    missing = evaluate_readiness_gates(
        accepted,
        level="full_fleet",
        delivery_requested=True,
        outbox=outbox,
    )
    assert missing["status"] == "FAIL"
    assert missing["gates"]["delivery_receipt"]["status"] == "FAIL"

    related = evaluate_readiness_gates(
        accepted,
        level="full_fleet",
        delivery_requested=True,
        outbox=outbox,
        maya_receipt=_maya_receipt(),
    )
    assert related["status"] == "PASS"
    assert related["outcome"] == "success"

    unrelated = evaluate_readiness_gates(
        accepted,
        level="full_fleet",
        delivery_requested=True,
        outbox=outbox,
        maya_receipt=_maya_receipt(capture_id="capture-other"),
    )
    assert unrelated["status"] == "FAIL"
    assert unrelated["gates"]["delivery_receipt"]["status"] == "FAIL"


def test_hard_failure_cannot_be_offset_by_score_or_health():
    verdict = evaluate_readiness_gates(
        _accepted(),
        level="core",
        hard_gates={"security": False},
        score=100,
        health={"status": "healthy"},
    )

    assert verdict["status"] == "FAIL"
    assert verdict["gates"]["security"]["status"] == "FAIL"
    assert verdict["context"] == {"score": 100, "health_status": "healthy"}


def test_evaluator_does_not_call_downstream_objects_for_core_claim():
    class MayaReceipt:
        def as_dict(self):
            raise AssertionError("core readiness must not call Maya")

    verdict = evaluate_readiness_gates(
        _accepted(),
        level="core",
        maya_receipt=MayaReceipt(),
    )

    assert verdict["status"] == "PASS"
