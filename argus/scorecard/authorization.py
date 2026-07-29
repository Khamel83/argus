"""Fail-closed authorization receipt validation for budgeted scorecard runs."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


class AuthorizationError(ValueError):
    """A budgeted scorecard authorization is absent, mismatched, or reused."""


_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ONE_TIME_PROVIDERS = frozenset({"serper", "you", "searchapi", "valyu"})
_FIELDS = {
    "schema",
    "receipt_id",
    "run_id",
    "generation",
    "permitted_providers",
    "maximum_tier",
    "call_count_cap",
    "cost_or_credit_cap",
    "one_time_credit_providers",
    "issued_at",
}


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AuthorizationError(f"{label} must be a positive integer")
    return value


def _provider_list(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) != len(set(value))
        or any(
            not isinstance(provider, str) or not _IDENTIFIER.fullmatch(provider)
            for provider in value
        )
    ):
        raise AuthorizationError(f"{label} must name unique providers")
    return value


def validate_and_consume_authorization(
    receipt_path: Path,
    *,
    expected_sha256: str,
    run_id: str,
    generation: str,
    consumption_dir: Path,
) -> Mapping[str, Any]:
    """Legacy local validation wrapper; production consumption is SQL-backed."""
    try:
        encoded = receipt_path.read_bytes()
    except OSError as exc:
        raise AuthorizationError("authorization receipt is required") from exc
    receipt = validate_authorization_bytes(
        encoded,
        expected_sha256=expected_sha256,
        run_id=run_id,
        generation=generation,
    )
    receipt_id = receipt["receipt_id"]
    assert isinstance(receipt_id, str)
    consumption_dir.mkdir(parents=True, exist_ok=True)
    marker = consumption_dir / f"{receipt_id}-{expected_sha256}.consumed"
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise AuthorizationError("authorization receipt was already consumed") from exc
    try:
        os.write(descriptor, f"{run_id}\n{generation}\n".encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt


def validate_authorization_bytes(
    encoded: bytes,
    *,
    expected_sha256: str,
    run_id: str,
    generation: str,
) -> Mapping[str, Any]:
    """Validate a receipt without granting or consuming execution authority."""
    if not _SHA256.fullmatch(expected_sha256):
        raise AuthorizationError("expected receipt SHA-256 is invalid")
    if not _IDENTIFIER.fullmatch(run_id):
        raise AuthorizationError("expected run id is invalid")
    if not _SHA256.fullmatch(generation):
        raise AuthorizationError("expected generation is invalid")
    if sha256(encoded).hexdigest() != expected_sha256:
        raise AuthorizationError("authorization receipt digest mismatch")
    try:
        receipt = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise AuthorizationError("authorization receipt is invalid JSON") from exc
    if not isinstance(receipt, Mapping) or set(receipt) != _FIELDS:
        raise AuthorizationError("authorization receipt must contain exact fields")
    if receipt["schema"] != "scorecard-budget-authorization-v1":
        raise AuthorizationError("unsupported authorization receipt schema")
    receipt_id = receipt["receipt_id"]
    if not isinstance(receipt_id, str) or not _IDENTIFIER.fullmatch(receipt_id):
        raise AuthorizationError("authorization receipt id is invalid")
    if receipt["run_id"] != run_id:
        raise AuthorizationError("authorization receipt run id mismatch")
    if receipt["generation"] != generation:
        raise AuthorizationError("authorization receipt generation mismatch")
    providers = _provider_list(receipt["permitted_providers"], "permitted providers")
    maximum_tier = _positive_int(receipt["maximum_tier"], "maximum tier")
    if maximum_tier not in {1, 3}:
        raise AuthorizationError("maximum tier must be 1 or 3")
    _positive_int(receipt["call_count_cap"], "call count cap")
    _positive_int(receipt["cost_or_credit_cap"], "cost or credit cap")
    one_time = receipt["one_time_credit_providers"]
    if (
        not isinstance(one_time, list)
        or any(
            not isinstance(provider, str) or not _IDENTIFIER.fullmatch(provider)
            for provider in one_time
        )
        or len(one_time) != len(set(one_time))
    ):
        raise AuthorizationError("one-time-credit providers must be unique")
    if any(provider not in _ONE_TIME_PROVIDERS for provider in one_time):
        raise AuthorizationError("unknown one-time-credit provider")
    required_one_time_names = set(providers) & _ONE_TIME_PROVIDERS
    if set(one_time) != required_one_time_names:
        raise AuthorizationError(
            "one-time-credit providers must be individually named in the receipt"
        )
    if required_one_time_names and maximum_tier != 3:
        raise AuthorizationError("one-time-credit providers require maximum tier 3")
    issued_at = receipt["issued_at"]
    if not isinstance(issued_at, str):
        raise AuthorizationError("authorization issued_at is invalid")
    try:
        parsed = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorizationError("authorization issued_at is invalid") from exc
    if parsed.tzinfo is None:
        raise AuthorizationError("authorization issued_at requires a timezone")

    return receipt
