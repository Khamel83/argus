"""Value-free, deterministic observations consumed by the v3 bundle writer."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from .contract import canonical_hash


class ObservationError(ValueError):
    """A snapshot or transport observation cannot support acceptance."""


_SENSITIVE_KEY_WORDS = (
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
    "cookie",
    "payload",
    "connection",
    "sql",
    "private",
    "raw_exception",
    "stacktrace",
    "traceback",
    "exception",
)
_OPAQUE_KEYS = {
    "id",
    "run_id",
    "operation_id",
    "attempt_id",
    "reservation_id",
    "capture_id",
    "idempotency_key",
    "session_id",
    "request_id",
    "receipt_id",
}
_MUTABLE_KEYS = {
    "updated_at",
    "last_seen_at",
    "count",
    "result_count",
    "latency_ms",
    "status",
}
_SAFE_STATUSES = {"success", "empty", "failed", "policy_skipped", "cache", "accepted"}
_SENSITIVE_VALUE = re.compile(
    r"(?:\bbearer\s+|\bbasic\s+|\bcookie\s*[:=]|\b(?:api[_-]?key|password|secret|authorization|private[_-]?key|raw[_-]?exception)\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:traceback|exception|stack trace)\b|(?:postgres(?:ql)?|pg)://|\bssh://|\bfile://)",
    re.IGNORECASE,
)
_LOCAL_VALUE = re.compile(
    r"(?:^|[\s'\[(])(?:/(?:Users|Volumes|private|tmp|var|opt|srv|etc|root|usr|workspace|run|data)/|[A-Za-z]:[\\/]|\\\\|~/(?:\.\.?/)?|\.\.?/|%2f(?:Users|Volumes|private|tmp|var|opt|srv|etc|root|usr)(?:%2f|/))",
    re.IGNORECASE,
)


def _utc(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ObservationError(f"{label} must be ISO-8601 UTC") from exc
    else:
        raise ObservationError(f"{label} must be ISO-8601 UTC")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ObservationError(f"{label} must be aware UTC")
    if parsed.utcoffset().total_seconds() != 0:
        raise ObservationError(f"{label} must be UTC")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def hash_opaque(value: object) -> str:
    """Hash an opaque identifier without emitting the identifier itself."""

    if not isinstance(value, str) or not value:
        raise ObservationError("opaque identifier must be a non-empty string")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key_is_sensitive(key: str) -> bool:
    normalized = "".join(char for char in key.lower() if char.isalnum())
    if normalized in {"tokencount", "tokenbalance", "tokenlimit"}:
        return False
    if normalized in {
        "apikey",
        "privatekey",
        "rawexception",
        "stacktrace",
        "traceback",
        "exception",
    }:
        return True
    return any(word.replace("_", "") in normalized for word in _SENSITIVE_KEY_WORDS)


def _sanitize(value: Any, *, path: str = "row") -> Any:
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ObservationError(f"non-string field at {path}")
            normalized = key.lower().replace("-", "_")
            if _key_is_sensitive(key) and normalized not in {
                "status",
                "source_type",
                "spend_status",
                "cache_status",
            }:
                raise ObservationError(f"sensitive field at {path}.{key}")
            if normalized in _OPAQUE_KEYS:
                if not isinstance(nested, str) or not nested:
                    raise ObservationError(
                        f"opaque field {path}.{key} must be a string"
                    )
                output[f"{normalized}_sha256"] = hash_opaque(nested)
            else:
                output[key] = _sanitize(nested, path=f"{path}.{key}")
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, path=f"{path}[]") for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ObservationError(f"non-finite value at {path}")
    if isinstance(value, str):
        if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
            raise ObservationError(f"control character at {path}")
        if _SENSITIVE_VALUE.search(value) or _LOCAL_VALUE.search(value):
            raise ObservationError(f"sensitive/local value at {path}")
    return value


def _row_identity(row: Mapping[str, Any]) -> str:
    for key in _OPAQUE_KEYS:
        if isinstance(row.get(key), str) and row[key]:
            return row[key]
    return canonical_hash(row)


def build_snapshot(
    kind: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    observed_at: datetime | str | None = None,
    authority: str = "postgresql",
    statement_timeout_ms: int = 15_000,
    lock_timeout_ms: int = 2_000,
) -> dict[str, Any]:
    """Create a deterministic DB-UTC snapshot with opaque identifiers hashed."""

    if not isinstance(kind, str) or not kind or len(kind) > 80:
        raise ObservationError("snapshot kind is invalid")
    if authority.lower() == "sqlite" or authority != "postgresql":
        raise ObservationError("PostgreSQL authority is required; SQLite is forbidden")
    if statement_timeout_ms != 15_000 or lock_timeout_ms != 2_000:
        raise ObservationError("snapshot timeout bounds must be 15s statement/2s lock")
    timestamp = _utc(observed_at or datetime.now(timezone.utc), "observed_at")
    original: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ObservationError("snapshot rows must be objects")
        original.append(dict(row))
    ordered = sorted(original, key=lambda row: (_row_time(row), _row_identity(row)))
    sanitized = [_sanitize(row) for row in ordered]
    result: dict[str, Any] = {
        "schema": "argus-acceptance-v3/snapshot",
        "kind": kind,
        "authority": authority,
        "database_timezone": "UTC",
        "observed_at": _iso(timestamp),
        "statement_timeout_ms": statement_timeout_ms,
        "lock_timeout_ms": lock_timeout_ms,
        "rows": sanitized,
    }
    result["sha256"] = canonical_hash(result)
    return result


def _row_time(row: Mapping[str, Any]) -> str:
    value = row.get("observed_at", row.get("created_at", ""))
    if not value:
        return ""
    return _iso(_utc(value, "row timestamp"))


def _identity_set(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identity = _row_identity(row)
        result[identity] = row
    return result


def audit_spend_delta(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    *,
    window_start: datetime | str,
    window_end: datetime | str,
    caller: str,
    label: str,
    network_attempts: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, str]]:
    """Reject every forbidden new spend/attempt row in the observation window."""

    start = _utc(window_start, "window_start")
    end = _utc(window_end, "window_end")
    if end < start:
        raise ObservationError("observation window is reversed")
    old = _identity_set(before)
    violations: list[dict[str, str]] = []
    for row in after:
        identity = _row_identity(row)
        if identity in old:
            continue
        when = row.get("observed_at", row.get("created_at"))
        if when is None:
            violations.append(
                {"code": "missing_timestamp", "row": hash_opaque(identity)}
            )
            continue
        timestamp = _utc(when, "spend row timestamp")
        if not start <= timestamp <= end:
            violations.append({"code": "outside_window", "row": hash_opaque(identity)})
        tier = row.get("tier", row.get("provider_tier", 0))
        if isinstance(tier, bool) or not isinstance(tier, (int, float)):
            violations.append({"code": "invalid_tier", "row": hash_opaque(identity)})
        elif tier > 0:
            violations.extend(
                {"code": code, "row": hash_opaque(identity)}
                for code in ("paid", "tier_above_zero")
            )
        for field, code in (
            ("reserved", "reserved_nonzero"),
            ("reserved_usd", "reserved_nonzero"),
            ("actual", "actual_nonzero"),
            ("actual_usd", "actual_nonzero"),
            ("overrun", "overrun"),
            ("overrun_usd", "overrun"),
            ("amount", "actual_nonzero"),
            ("charge", "actual_nonzero"),
        ):
            amount = row.get(field, 0)
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                violations.append(
                    {"code": "invalid_amount", "row": hash_opaque(identity)}
                )
            elif not math.isfinite(float(amount)) or amount < 0:
                violations.append(
                    {"code": "invalid_amount", "row": hash_opaque(identity)}
                )
            elif amount != 0:
                violations.append({"code": code, "row": hash_opaque(identity)})
        status = str(row.get("status", row.get("spend_status", ""))).lower()
        if status in {"uncertain", "unsettled", "pending", "unknown"}:
            violations.append({"code": status, "row": hash_opaque(identity)})
        if row.get("estimator_violation") or row.get("estimator_valid") is False:
            violations.append(
                {"code": "estimator_violation", "row": hash_opaque(identity)}
            )
        if (
            row.get("unresolved")
            or row.get("spend_audit_delta")
            or row.get("balance_delta")
        ):
            violations.append(
                {"code": "unresolved_audit_delta", "row": hash_opaque(identity)}
            )
        row_label = row.get("label", row.get("phase_label"))
        if not row_label:
            violations.append({"code": "unlabelled", "row": hash_opaque(identity)})
        row_caller = row.get("caller", row.get("caller_identity"))
        if row_caller != caller:
            violations.append({"code": "caller_mismatch", "row": hash_opaque(identity)})
        if row_label and row_label != label:
            violations.append({"code": "label_mismatch", "row": hash_opaque(identity)})
    for attempt in network_attempts:
        attempted = bool(attempt.get("attempted", True))
        tier = attempt.get("tier", 0)
        if attempted and (not isinstance(tier, (int, float)) or tier > 0):
            raise ObservationError("unledgered billable network attempt")
    return violations


def assert_spend_delta(violations: Sequence[Mapping[str, Any]]) -> None:
    """Raise on any spend/audit violation instead of silently ignoring it."""

    if not isinstance(violations, Sequence):
        raise ObservationError("spend violations must be a sequence")
    if violations:
        raise ObservationError("spend delta contains forbidden rows")


def compare_predecessors(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
    *,
    immutable_fields: Iterable[str] | None = None,
) -> None:
    """Ensure predecessor rows retain all immutable identity fields."""

    old = _identity_set(before)
    new = _identity_set(after)
    fields = set(immutable_fields or ())
    for identity, row in old.items():
        if identity not in new:
            raise ObservationError("predecessor row was deleted")
        current = new[identity]
        keys = fields or (set(row) - _MUTABLE_KEYS)
        for key in keys:
            if row.get(key) != current.get(key):
                raise ObservationError("predecessor immutable field changed")


def validate_cache_trace(trace: Mapping[str, Any]) -> None:
    status = trace.get("status")
    if status == "hit" and trace.get("attempted") is not False:
        raise ObservationError("cache hit must record attempted=false")
    if status == "hit" and trace.get("eligible") is not True:
        raise ObservationError("cache hit must be positively eligible")
    required = {
        "status",
        "attempted",
        "eligible",
        "age_seconds",
        "origin",
        "spend_provenance",
    }
    if not required.issubset(trace):
        raise ObservationError("cache trace is incomplete")
    if status == "hit":
        age = trace["age_seconds"]
        if isinstance(age, bool) or not isinstance(age, (int, float)):
            raise ObservationError("cache hit age is invalid")
        if not math.isfinite(float(age)) or age < 0:
            raise ObservationError("cache hit age is invalid")
        if not isinstance(trace["origin"], str) or not trace["origin"]:
            raise ObservationError("cache hit origin is required")
        provenance = trace["spend_provenance"]
        if (
            not isinstance(provenance, Mapping)
            or provenance.get("ledgered") is not True
        ):
            raise ObservationError("cache hit spend provenance is incomplete")


def validate_transport_pages(
    pages: Sequence[Mapping[str, Any]],
    *,
    expected_sha256: str,
    max_pages: int = 64,
    max_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any]:
    """Validate a contiguous UTF-8 artifact page stream and its terminal hash."""

    if not pages or len(pages) > max_pages:
        raise ObservationError("transport page count is outside bound")
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise ObservationError("terminal artifact hash is invalid")
    offset = 0
    chunks: list[bytes] = []
    terminal_count = 0
    for index, page in enumerate(pages):
        if page.get("offset") != offset:
            raise ObservationError("transport page offsets are not contiguous")
        data = page.get("data")
        if not isinstance(data, str):
            raise ObservationError("transport page data must be UTF-8 text")
        encoded = data.encode("utf-8")
        chunks.append(encoded)
        offset += len(encoded)
        if offset > max_bytes:
            raise ObservationError("transport artifact exceeds byte bound")
        terminal = page.get("terminal") is True
        if terminal:
            terminal_count += 1
            if index != len(pages) - 1:
                raise ObservationError("terminal page is not last")
    if terminal_count != 1:
        raise ObservationError("transport stream needs one terminal page")
    payload = b"".join(chunks)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise ObservationError("terminal artifact hash mismatch")
    return {"pages": len(pages), "bytes": len(payload), "sha256": digest}


def replay_capture_evidence(
    *,
    body: bytes,
    first_response: Mapping[str, Any],
    replay_response: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(body, bytes) or not body:
        raise ObservationError("Maya body must be non-empty bytes")
    body_sha256 = hashlib.sha256(body).hexdigest()
    idempotency_key_sha256: str | None = None
    try:
        decoded_body = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        decoded_body = None
    if isinstance(decoded_body, Mapping) and "idempotency_key" in decoded_body:
        key = decoded_body["idempotency_key"]
        if not isinstance(key, str) or not key:
            raise ObservationError("Maya idempotency key is invalid")
        idempotency_key_sha256 = hash_opaque(key)
    for label, response in (
        ("first", first_response),
        ("replay", replay_response),
    ):
        if not isinstance(response, Mapping):
            raise ObservationError(f"{label} Maya response must be an object")
        for field in (
            "status",
            "duplicate",
            "capture_id",
            "caller",
            "pages",
            "body_sha256",
            "capture_id_sha256",
        ):
            if field not in response:
                raise ObservationError(f"Maya response identity is incomplete: {field}")
        if response.get("caller") != "argus":
            raise ObservationError("Maya capture caller is not argus")
        if response.get("pages") != []:
            raise ObservationError("canary Maya capture must contain zero pages")
        if response.get("body_sha256") != body_sha256:
            raise ObservationError("Maya response body hash mismatch")
        if idempotency_key_sha256 is not None:
            if response.get("idempotency_key_sha256") != idempotency_key_sha256:
                raise ObservationError("Maya response idempotency hash mismatch")
    if (
        first_response.get("status") != 201
        or first_response.get("duplicate") is not False
    ):
        raise ObservationError("first Maya capture must be 201/nonduplicate")
    if (
        replay_response.get("status") != 200
        or replay_response.get("duplicate") is not True
    ):
        raise ObservationError("Maya replay must be 200/duplicate")
    capture_id = first_response.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        raise ObservationError("first Maya capture ID is missing")
    if replay_response.get("capture_id") != capture_id:
        raise ObservationError("Maya replay changed capture ID")
    expected_capture_hash = hash_opaque(capture_id)
    if first_response.get("capture_id_sha256") != expected_capture_hash:
        raise ObservationError("first Maya capture hash mismatch")
    if replay_response.get("capture_id_sha256") != expected_capture_hash:
        raise ObservationError("Maya replay capture hash mismatch")
    return {
        "body_sha256": body_sha256,
        "capture_id_sha256": expected_capture_hash,
        "duplicate": True,
    }


def scan_log_window(
    logs: Sequence[Mapping[str, Any]],
    *,
    allowed_request_hashes: set[str],
    max_records: int = 10_000,
    window_start: datetime | str | None = None,
    window_end: datetime | str | None = None,
) -> dict[str, Any]:
    """Redact and hash a bounded log window, allowing only predeclared auth probes."""

    if len(logs) > max_records:
        raise ObservationError("log window exceeds record bound")
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in allowed_request_hashes
    ):
        raise ObservationError("allowed request hashes are invalid")
    start = _utc(window_start, "window_start") if window_start is not None else None
    end = _utc(window_end, "window_end") if window_end is not None else None
    if start is not None and end is not None and end < start:
        raise ObservationError("log window is reversed")
    sanitized: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    allowed_seen: set[str] = set()
    for record in logs:
        if not isinstance(record, Mapping):
            raise ObservationError("log record must be an object")
        status = record.get("status", record.get("status_code"))
        request_hash = record.get("request_hash")
        if status in {421, 500, 501, 502, 503, 504}:
            unexpected.append({"status": str(status)})
        elif status in {401, 403}:
            if request_hash not in allowed_request_hashes:
                unexpected.append({"status": str(status)})
            elif request_hash in allowed_seen:
                unexpected.append({"status": str(status), "reason": "auth loop"})
            else:
                allowed_seen.add(request_hash)
        if (start is not None or end is not None) and "at" not in record:
            unexpected.append({"status": str(status), "reason": "timestamp missing"})
        elif "at" in record:
            try:
                observed = _utc(record["at"], "log timestamp")
            except ObservationError:
                unexpected.append(
                    {"status": str(status), "reason": "timestamp invalid"}
                )
            else:
                if start is not None and observed < start:
                    unexpected.append(
                        {"status": str(status), "reason": "before window"}
                    )
                if end is not None and observed > end:
                    unexpected.append({"status": str(status), "reason": "after window"})
        sanitized.append(_sanitize(record, path="log"))
    if unexpected:
        raise ObservationError(f"unexpected log events: {unexpected}")
    return {"records": sanitized, "unexpected": [], "sha256": canonical_hash(sanitized)}


def validate_outbox_drain(
    before: Mapping[str, int], after: Mapping[str, int]
) -> dict[str, Any]:
    """Require no dead-letter increase and a drained/non-increasing queue."""

    for key in ("pending", "retry", "dead_letter"):
        if key not in before or key not in after:
            raise ObservationError("outbox snapshot is incomplete")
    if after["dead_letter"] > before["dead_letter"]:
        raise ObservationError("dead-letter count increased")
    if after["pending"] > before["pending"] or after["retry"] > before["retry"]:
        raise ObservationError("outbox pending/retry count increased")
    return {
        "pending": after["pending"],
        "retry": after["retry"],
        "dead_letter": after["dead_letter"],
    }


def validate_unauth_probe(
    *,
    status: int,
    redirected: bool,
    session_issued: bool,
    side_effect_delta: int,
    response_sha256: str,
) -> dict[str, Any]:
    """Validate the single unauthenticated MCP negative probe."""

    if status not in {401, 403}:
        raise ObservationError("unauthenticated probe must be 401 or 403")
    if redirected or session_issued or side_effect_delta != 0:
        raise ObservationError("unauthenticated probe had a side effect")
    if not isinstance(response_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", response_sha256
    ):
        raise ObservationError("unauthenticated probe response hash is invalid")
    return {
        "status": status,
        "redirected": False,
        "session_issued": False,
        "side_effect_delta": 0,
        "response_sha256": response_sha256,
    }
