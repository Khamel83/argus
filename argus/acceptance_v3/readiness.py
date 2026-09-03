"""Conditional readiness gates for Argus core and downstream delivery.

The evaluator is deliberately pure.  It consumes bounded evidence that a
caller already collected.  It never calls Maya, a database, a provider, or a
health endpoint.  A score or a health response is context only; hard gates
remain authoritative.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import Enum
import json
import math
from typing import Any


class ReadinessLevel(str, Enum):
    """The three conditional readiness claims supported by the contract."""

    CORE = "core"
    CORE_INTEGRATION = "core_integration"
    FULL_FLEET = "full_fleet"


class GateStatus(str, Enum):
    """The closed result for one hard gate."""

    PASS = "PASS"
    PENDING = "PENDING"
    FAIL = "FAIL"


_DELIVERY_PENDING = frozenset({"pending", "queued", "delivering", "retry"})
_DELIVERY_PASSED = frozenset({"acknowledged", "delivered", "success", "succeeded"})
_DELIVERY_FAILED = frozenset({"failed", "dead_letter", "rejected", "superseded"})
_ACCEPTED_STATUSES = frozenset(
    {"accepted", "succeeded", "success", "degraded", "empty"}
)
_HARD_GATE_NAMES = frozenset(
    {"security", "spend", "ownership", "schema", "publication"}
)
_IDENTITY_FIELDS = (
    "operation_id",
    "request_id",
    "release_identity",
    "schema_identity",
    "receipt_identity",
)


@dataclass(frozen=True, slots=True)
class ReadinessGateVerdict:
    """Immutable result returned by :func:`evaluate_readiness_gates`."""

    level: ReadinessLevel
    status: GateStatus
    outcome: str
    reason: str
    gates: Mapping[str, Mapping[str, Any]]
    score: int | float | None = None
    health_status: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a transport-safe projection without raw identity values."""

        return {
            "schema": "argus-acceptance-v3/readiness-gates",
            "level": self.level.value,
            "status": self.status.value,
            "outcome": self.outcome,
            "reason": self.reason,
            "gates": {name: dict(value) for name, value in self.gates.items()},
            "context": {
                "score": self.score,
                "health_status": self.health_status,
            },
        }


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        try:
            projected = as_dict()
        except Exception:
            return {}
        return dict(projected) if isinstance(projected, Mapping) else {}
    if value is not None and hasattr(value, "__dataclass_fields__"):
        try:
            projected = asdict(value)
        except (TypeError, ValueError):
            return {}
        return dict(projected) if isinstance(projected, Mapping) else {}
    return {}


def _json_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return _mapping(parsed)
    return _mapping(value)


def _canonical(value: object) -> str:
    """Compare nested identity values without depending on object type."""

    projected = _mapping(value)
    if projected:
        value = projected
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        )
    except (TypeError, ValueError):
        return str(value)


def _first(mapping: Mapping[str, Any], *keys: str) -> object:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _identity_projection(value: object) -> dict[str, object]:
    """Extract the stable identity tuple from common evidence projections."""

    root = _json_mapping(value)
    containers: list[Mapping[str, Any]] = [root]
    for key in (
        "identity",
        "evidence_identity",
        "accepted_identity",
        "delivery_identity",
        "argus_identity",
    ):
        nested = _json_mapping(root.get(key))
        if nested:
            containers.append(nested)

    merged: dict[str, object] = {}
    for container in containers:
        for key in (
            "operation_id",
            "request_id",
            "release_identity",
            "schema_identity",
            "receipt_identity",
            "accepted_receipt_identity",
            "result_hash",
            "content_hash",
            "content_sha256",
            "artifact_hash",
            "maya_capture_id",
            "capture_id",
        ):
            if key in container and container[key] not in (None, ""):
                merged.setdefault(key, container[key])

    # Search and extraction use different public operation names.  They are
    # equivalent at the acceptance boundary.
    if "operation_id" not in merged:
        operation = _first(root, "search_run_id", "extraction_run_id", "run_id")
        if operation is not None:
            merged["operation_id"] = operation
    if "release_identity" not in merged:
        release = _first(root, "release_id", "release_identity_id")
        if release is not None:
            merged["release_identity"] = release
    if "schema_identity" not in merged:
        schema = _first(root, "schema_id", "schema_identity_id")
        if schema is not None:
            merged["schema_identity"] = schema
    if "receipt_identity" not in merged:
        receipt = _first(
            root,
            "accepted_receipt_identity",
            "acceptance_receipt_identity",
            "receipt_ref",
        )
        if receipt is None:
            receipt_mapping = _json_mapping(root.get("acceptance_receipt"))
            receipt = _first(receipt_mapping, "receipt_identity", "receipt_ref")
        if receipt is not None:
            merged["receipt_identity"] = receipt
    if "result_hash" not in merged:
        result_hash = _first(
            root,
            "content_hash",
            "content_sha256",
            "artifact_hash",
        )
        if result_hash is not None:
            merged["result_hash"] = result_hash
    if "maya_capture_id" not in merged:
        capture = _first(root, "capture_id")
        if capture is not None:
            merged["maya_capture_id"] = capture
    return merged


def _identity_mismatches(
    accepted: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    require_result_hash: bool = False,
) -> list[str]:
    missing: list[str] = []
    for field in _IDENTITY_FIELDS:
        expected = accepted.get(field)
        actual = candidate.get(field)
        if expected in (None, "") or actual in (None, ""):
            missing.append(field)
        elif _canonical(expected) != _canonical(actual):
            missing.append(f"{field}_mismatch")
    expected_hash = accepted.get("result_hash")
    actual_hash = candidate.get("result_hash")
    if require_result_hash and (
        expected_hash in (None, "") or actual_hash in (None, "")
    ):
        missing.append("result_hash")
    elif expected_hash not in (None, "") or actual_hash not in (None, ""):
        if expected_hash in (None, "") or actual_hash in (None, ""):
            missing.append("result_hash")
        elif _canonical(expected_hash) != _canonical(actual_hash):
            missing.append("result_hash_mismatch")
    return missing


def _status(value: object, *, default: str = "") -> str:
    mapping = _json_mapping(value)
    candidate = _first(mapping, "status", "state", "outcome")
    if candidate is None and isinstance(value, str):
        candidate = value
    return str(candidate or default).strip().lower()


def _gate(
    status: GateStatus, reason: str, *, evidence: tuple[str, ...] = ()
) -> dict[str, Any]:
    item: dict[str, Any] = {"status": status.value, "reason": reason}
    if evidence:
        item["evidence"] = list(evidence)
    return item


def _hard_gate_status(value: object) -> tuple[GateStatus, str]:
    if isinstance(value, bool):
        return (GateStatus.PASS, "passed") if value else (GateStatus.FAIL, "failed")
    mapping = _json_mapping(value)
    candidate = _status(mapping)
    if candidate in {"pass", "passed", "success", "healthy", "true"}:
        return GateStatus.PASS, "passed"
    if candidate in {"pending", "unknown", "unavailable", "not_run"}:
        return GateStatus.PENDING, candidate
    if candidate in {"fail", "failed", "false", "blocked", "denied"}:
        return GateStatus.FAIL, candidate
    if value is None:
        return GateStatus.PENDING, "not supplied"
    return GateStatus.FAIL, "unrecognized hard-gate state"


def _context_score(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def _context_health(value: object) -> str | None:
    if isinstance(value, str):
        return value[:64]
    mapping = _json_mapping(value)
    candidate = _first(mapping, "status", "state", "outcome", "health")
    return str(candidate)[:64] if candidate not in (None, "") else None


def _unpack_evidence(
    accepted: object,
    outbox: object | None,
    maya_receipt: object | None,
) -> tuple[object, object | None, object | None]:
    root = _json_mapping(accepted)
    if outbox is None and any(
        key in root for key in ("accepted", "accepted_result", "result")
    ):
        accepted = root.get("accepted", root.get("accepted_result", root.get("result")))
        outbox = root.get("outbox", root.get("delivery_intent"))
        maya_receipt = (
            maya_receipt
            if maya_receipt is not None
            else root.get("maya_receipt", root.get("acknowledgment"))
        )
    return accepted, outbox, maya_receipt


def evaluate_readiness_gates(
    accepted: object,
    *,
    level: str | ReadinessLevel | None = None,
    delivery_requested: bool = False,
    claims_maya_integration: bool = False,
    full_fleet: bool = False,
    outbox: object | None = None,
    maya_receipt: object | None = None,
    hard_gates: Mapping[str, object] | None = None,
    score: int | float | None = None,
    health: object | None = None,
) -> dict[str, Any]:
    """Evaluate one conditional readiness claim.

    ``accepted`` may be the accepted-result evidence itself or a mapping with
    ``accepted``, ``outbox``, and ``maya_receipt`` members.  The evaluator does
    not create an outbox row and does not treat an HTTP health result as proof
    of persistence or downstream delivery.
    """

    accepted, outbox, maya_receipt = _unpack_evidence(accepted, outbox, maya_receipt)
    if level is None:
        resolved_level = (
            ReadinessLevel.FULL_FLEET
            if full_fleet
            else ReadinessLevel.CORE_INTEGRATION
            if delivery_requested or claims_maya_integration
            else ReadinessLevel.CORE
        )
    else:
        resolved_level = ReadinessLevel(level)

    accepted_map = _json_mapping(accepted)
    accepted_status = _status(accepted_map)
    gates: dict[str, dict[str, Any]] = {}
    if accepted_map and (
        accepted_status in _ACCEPTED_STATUSES
        or accepted_map.get("accepted") is True
        or accepted_map.get("durable") is True
    ):
        gates["accepted_result"] = _gate(
            GateStatus.PASS,
            "accepted durable result is present",
        )
    else:
        gates["accepted_result"] = _gate(
            GateStatus.FAIL,
            "accepted durable result is missing or not accepted",
        )

    delivery_required = resolved_level is not ReadinessLevel.CORE
    accepted_identity = _identity_projection(accepted_map)
    outbox_identity = _identity_projection(outbox)
    outbox_status = _status(outbox)
    if not delivery_required:
        gates["delivery_intent"] = _gate(
            GateStatus.PASS,
            "delivery is not requested for core readiness",
        )
        gates["delivery_receipt"] = _gate(
            GateStatus.PASS,
            "downstream receipt is not required for core readiness",
        )
    elif not _json_mapping(outbox):
        gates["delivery_intent"] = _gate(
            GateStatus.FAIL,
            "requested delivery has no durable outbox intent",
        )
        gates["delivery_receipt"] = _gate(
            GateStatus.FAIL,
            "delivery cannot be evaluated without its outbox intent",
        )
    else:
        mismatches = _identity_mismatches(accepted_identity, outbox_identity)
        if mismatches:
            gates["delivery_intent"] = _gate(
                GateStatus.FAIL,
                "outbox identity does not match the accepted result",
                evidence=tuple(mismatches),
            )
        elif outbox_status in _DELIVERY_FAILED:
            gates["delivery_intent"] = _gate(
                GateStatus.FAIL,
                "outbox delivery failed",
            )
        elif outbox_status in _DELIVERY_PENDING:
            gates["delivery_intent"] = _gate(
                GateStatus.PENDING,
                "outbox intent is durable but delivery is pending",
            )
        elif outbox_status in _DELIVERY_PASSED:
            gates["delivery_intent"] = _gate(
                GateStatus.PASS,
                "outbox intent is acknowledged",
            )
        else:
            gates["delivery_intent"] = _gate(
                GateStatus.FAIL,
                "outbox intent has an unknown state",
            )

        if resolved_level is ReadinessLevel.FULL_FLEET:
            receipt_map = _json_mapping(maya_receipt)
            receipt_status = _status(receipt_map)
            receipt_identity = _identity_projection(receipt_map)
            if not receipt_map:
                receipt_status = "missing"
            receipt_mismatches = _identity_mismatches(
                accepted_identity,
                receipt_identity,
                require_result_hash=True,
            )
            # A Maya capture ID is created by the downstream service.  Older
            # accepted/outbox records therefore do not have it yet.  If one
            # side already records the ID, require the returned receipt to
            # match it; otherwise the receipt itself establishes the new
            # capture identity after the other identity fields correlate.
            accepted_capture = accepted_identity.get(
                "maya_capture_id"
            ) or outbox_identity.get("maya_capture_id")
            receipt_capture = receipt_identity.get("maya_capture_id")
            if receipt_capture in (None, ""):
                receipt_mismatches.append("maya_capture_id")
            elif accepted_capture not in (None, "") and _canonical(
                accepted_capture
            ) != _canonical(receipt_capture):
                receipt_mismatches.append("maya_capture_id_mismatch")
            if receipt_status in _DELIVERY_FAILED:
                gates["delivery_receipt"] = _gate(
                    GateStatus.FAIL,
                    "Maya rejected or failed the delivery",
                )
            elif receipt_status in _DELIVERY_PASSED and not receipt_mismatches:
                gates["delivery_receipt"] = _gate(
                    GateStatus.PASS,
                    "correlated Maya receipt is present",
                )
            elif receipt_status in _DELIVERY_PENDING or (
                receipt_status == "missing"
                and gates["delivery_intent"]["status"] == GateStatus.PENDING.value
            ):
                gates["delivery_receipt"] = _gate(
                    GateStatus.PENDING,
                    "Maya has not returned a correlated receipt",
                )
            else:
                gates["delivery_receipt"] = _gate(
                    GateStatus.FAIL,
                    "Maya receipt is missing or unrelated",
                    evidence=tuple(receipt_mismatches),
                )
        else:
            gates["delivery_receipt"] = _gate(
                GateStatus.PASS,
                "Maya receipt is not required for core integration readiness",
            )

    for name in sorted(_HARD_GATE_NAMES):
        if hard_gates is None or name not in hard_gates:
            continue
        status, reason = _hard_gate_status(hard_gates[name])
        gates[name] = _gate(status, reason)
    if "delivery" in (hard_gates or {}):
        status, reason = _hard_gate_status((hard_gates or {})["delivery"])
        gates["delivery"] = _gate(status, reason)

    statuses = [GateStatus(item["status"]) for item in gates.values()]
    if GateStatus.FAIL in statuses:
        overall = GateStatus.FAIL
    elif GateStatus.PENDING in statuses:
        overall = GateStatus.PENDING
    else:
        overall = GateStatus.PASS
    delivery_pending = (
        gates.get("delivery_intent", {}).get("status") == GateStatus.PENDING.value
        or gates.get("delivery_receipt", {}).get("status") == GateStatus.PENDING.value
    )
    delivery_failed = (
        gates.get("delivery_intent", {}).get("status") == GateStatus.FAIL.value
        or gates.get("delivery_receipt", {}).get("status") == GateStatus.FAIL.value
    )
    if overall is GateStatus.PASS:
        outcome = "success"
        reason = "all applicable hard gates passed"
    elif delivery_pending and not delivery_failed:
        outcome = "delivery_pending"
        reason = "accepted result is durable; downstream delivery is pending"
    elif delivery_failed:
        outcome = "delivery_failed"
        reason = "downstream delivery gate failed"
    else:
        outcome = "unready"
        reason = "one or more hard gates failed or are incomplete"
    verdict = ReadinessGateVerdict(
        level=resolved_level,
        status=overall,
        outcome=outcome,
        reason=reason,
        gates=gates,
        score=_context_score(score),
        health_status=_context_health(health),
    )
    return verdict.as_dict()


def project_readiness_evidence(
    verdict: Mapping[str, Any] | ReadinessGateVerdict,
) -> dict[str, Any]:
    """Return the bounded evidence projection used by acceptance bundles."""

    value = (
        verdict.as_dict()
        if isinstance(verdict, ReadinessGateVerdict)
        else dict(verdict)
    )
    gates = value.get("gates")
    projected_gates = {}
    if isinstance(gates, Mapping):
        for name, gate in gates.items():
            if isinstance(name, str) and isinstance(gate, Mapping):
                projected_gates[name] = {
                    "status": str(gate.get("status", "FAIL")),
                    "reason": str(gate.get("reason", ""))[:256],
                }
    return {
        "schema": "argus-acceptance-v3/readiness-evidence",
        "level": str(value.get("level", "core")),
        "status": str(value.get("status", "FAIL")),
        "outcome": str(value.get("outcome", "unready")),
        "reason": str(value.get("reason", ""))[:256],
        "gates": projected_gates,
    }


__all__ = [
    "GateStatus",
    "ReadinessGateVerdict",
    "ReadinessLevel",
    "evaluate_readiness_gates",
    "project_readiness_evidence",
]
