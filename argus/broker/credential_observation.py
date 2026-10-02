"""Sanitized credential metadata observation receipts."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import json
import math
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

from argus.logging import get_logger

logger = get_logger("broker.credential_observation")

_FULL_SHA = "0123456789abcdef"
_USAGE_URL = "https://api.tavily.com/usage"
_CREDENTIAL_REFERENCE = "ARGUS_TAVILY_API_KEY"
_SCHEMA = "argus.credential-observation/v1"
_OWNER = "argus"
_PROVIDER = "tavily"
_CHECK_ID = "tavily.usage-metadata"
_RECEIPT_DIR = "credential-health"
_RECEIPT_LEAF = "tavily-usage.json"
_MAX_RESPONSE_BYTES = 65_536
_MAX_RECEIPT_BYTES = 4_096
_MAX_JSON_DEPTH = 8
_MAX_JSON_KEYS = 256
_RATE_LIMIT_SECONDS = 30 * 60
_MAX_AGE_SECONDS = 45 * 60
_MAX_USAGE_VALUE = 2**63 - 1

_FIELD_NAMES = (
    "presence",
    "decryptability",
    "authentication",
    "identity",
    "scope",
    "capability",
    "budget",
    "availability",
)
_STATUS_VALUES = {
    "verified",
    "failed",
    "unknown",
    "not_applicable",
    "not_configured",
}
_REASON_VALUES = {
    "injected_configuration",
    "optional_not_configured",
    "provider_disabled",
    "source_unavailable",
    "metadata_verified",
    "unauthorized",
    "forbidden",
    "rate_limited",
    "redirect_refused",
    "http_error",
    "timeout",
    "transport_error",
    "response_oversized",
    "response_invalid",
    "not_probed",
    "not_established",
    "partial_coverage",
}

_DEFINITION = {
    "schema": _SCHEMA,
    "owner": _OWNER,
    "provider": _PROVIDER,
    "check_id": _CHECK_ID,
    "method": "GET",
    "url": _USAGE_URL,
    "limits": {
        "response_bytes": _MAX_RESPONSE_BYTES,
        "json_depth": _MAX_JSON_DEPTH,
        "json_object_keys": _MAX_JSON_KEYS,
        "receipt_bytes": _MAX_RECEIPT_BYTES,
        "connect_timeout_seconds": 5,
        "total_deadline_seconds": 10,
    },
    "schedule_seconds": _RATE_LIMIT_SECONDS,
    "max_age_seconds": _MAX_AGE_SECONDS,
    "credential_reference": _CREDENTIAL_REFERENCE,
}


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


_DEFINITION_SHA256 = hashlib.sha256(_canonical_bytes(_DEFINITION)).hexdigest()


@dataclass(frozen=True)
class CredentialObservationResult:
    status: str
    reason: str
    attempts: int
    path: Path | None = None


@dataclass(frozen=True)
class _UsageMetadata:
    limit_known: bool


@dataclass(frozen=True)
class _ReceiptDirectory:
    path: Path
    root_fd: int
    child_fd: int
    root_identity: tuple[int, int]
    child_identity: tuple[int, int]


class _PublishError(Exception):
    code = "publish_failed"


class _UnsafePathError(_PublishError):
    code = "unsafe_filesystem"


class _DurabilityUnknown(_PublishError):
    code = "durability_unknown"


def _is_full_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in _FULL_SHA for character in value.lower())
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _field(status: str, reason: str) -> dict[str, str]:
    if status not in _STATUS_VALUES or reason not in _REASON_VALUES:
        raise ValueError("invalid credential observation field")
    return {"status": status, "reason": reason}


def _base_fields(*, has_key: bool) -> dict[str, dict[str, str]]:
    return {
        "presence": _field(
            "verified" if has_key else "not_configured",
            "injected_configuration" if has_key else "optional_not_configured",
        ),
        "decryptability": _field("unknown", "not_established"),
        "authentication": _field("unknown", "not_probed"),
        "identity": _field("unknown", "not_established"),
        "scope": _field("unknown", "not_established"),
        "capability": _field("unknown", "not_established"),
        "budget": _field("unknown", "not_established"),
        "availability": _field("unknown", "not_established"),
    }


def _receipt_payload(
    *,
    observed_at: datetime,
    finished_at: datetime,
    source_sha: str | None,
    attempts: int,
    reason: str,
    fields: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if set(fields) != set(_FIELD_NAMES):
        raise ValueError("credential observation fields are incomplete")
    receipt: dict[str, Any] = {
        "schema": _SCHEMA,
        "owner": _OWNER,
        "provider": _PROVIDER,
        "check_id": _CHECK_ID,
        "run_id": str(uuid.uuid4()),
        "observed_at": _utc_z(observed_at),
        "finished_at": _utc_z(finished_at),
        "source_sha": source_sha,
        "definition_sha256": _DEFINITION_SHA256,
        "credential_reference": _CREDENTIAL_REFERENCE,
        "credential_version": "unknown",
        "attempts": attempts,
        "status": "unknown",
        "reason": reason,
        "fields": fields,
    }
    receipt["payload_sha256"] = hashlib.sha256(_canonical_bytes(receipt)).hexdigest()
    return receipt


def _encode_receipt(receipt: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(
        receipt,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_RECEIPT_BYTES:
        raise ValueError("credential observation receipt is too large")
    return encoded


def _numeric_metadata(value: Any, *, allow_null: bool = False) -> int | float | None:
    if allow_null and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("usage metadata number is invalid")
    if isinstance(value, int):
        if value < 0 or value > _MAX_USAGE_VALUE:
            raise ValueError("usage metadata number is out of bounds")
        return value
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("usage metadata number is out of bounds") from exc
    if not finite or value < 0 or value > _MAX_USAGE_VALUE:
        raise ValueError("usage metadata number is out of bounds")
    return value


def _strict_json_loads(body: bytes) -> Any:
    key_count = 0

    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant {value}")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal key_count
        key_count += len(pairs)
        if key_count > _MAX_JSON_KEYS:
            raise ValueError("JSON object key limit exceeded")
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    try:
        return json.loads(
            body.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=object_pairs,
        )
    except (
        OverflowError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("usage metadata JSON is invalid") from exc


def _validate_json_depth(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > _MAX_JSON_DEPTH:
                raise ValueError("usage metadata JSON is too deep")
            values = item.values() if isinstance(item, dict) else item
            stack.extend((child, depth + 1) for child in values)


def parse_tavily_usage_metadata(body: bytes) -> _UsageMetadata:
    """Parse Tavily usage metadata without retaining provider account contents."""
    parsed = _strict_json_loads(body)

    _validate_json_depth(parsed)
    if not isinstance(parsed, dict):
        raise ValueError("usage metadata must be an object")
    key = parsed.get("key")
    account = parsed.get("account")
    if not isinstance(key, dict) or not isinstance(account, dict):
        raise ValueError("usage metadata is missing required objects")
    if "usage" not in key or "limit" not in key:
        raise ValueError("usage metadata is missing usage fields")
    _numeric_metadata(key["usage"])
    limit = _numeric_metadata(key["limit"], allow_null=True)
    return _UsageMetadata(limit_known=limit is not None)


async def _read_response_body(response: httpx.Response) -> bytes:
    content_encoding = response.headers.get("content-encoding")
    if content_encoding and content_encoding.strip().lower() != "identity":
        raise ValueError("response_invalid")
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            announced_length = int(content_length)
        except ValueError as exc:
            raise ValueError("response_invalid") from exc
        if announced_length < 0:
            raise ValueError("response_invalid")
        if announced_length > _MAX_RESPONSE_BYTES:
            raise ValueError("response_oversized")
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes(chunk_size=8192):
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            raise ValueError("response_oversized")
        chunks.append(chunk)
    return b"".join(chunks)


async def _fetch_usage_metadata(
    api_key: str,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, _UsageMetadata | None]:
    timeout = httpx.Timeout(timeout=10.0, connect=5.0)

    async def request_once() -> tuple[str, _UsageMetadata | None]:
        client_kwargs: dict[str, Any] = {
            "timeout": timeout,
            "trust_env": False,
            "follow_redirects": False,
            "verify": True,
        }
        if http_transport is not None:
            client_kwargs["transport"] = http_transport
        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream(
                "GET",
                _USAGE_URL,
                headers={
                    "Accept-Encoding": "identity",
                    "Authorization": f"Bearer {api_key}",
                },
            ) as response:
                status_code = response.status_code
                if 300 <= status_code < 400:
                    return "redirect_refused", None
                if status_code == 401:
                    return "unauthorized", None
                if status_code == 403:
                    return "forbidden", None
                if status_code == 429:
                    return "rate_limited", None
                if status_code != 200:
                    return "http_error", None
                try:
                    body = await _read_response_body(response)
                    metadata = parse_tavily_usage_metadata(body)
                except ValueError as exc:
                    if str(exc) == "response_oversized":
                        return "response_oversized", None
                    return "response_invalid", None
                return "partial_coverage", metadata

    try:
        return await asyncio.wait_for(request_once(), timeout=10.0)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return "timeout", None
    except httpx.DecodingError:
        return "response_invalid", None
    except httpx.HTTPError:
        return "transport_error", None


def _source_sha(build: Mapping[str, Any] | None) -> str | None:
    if not isinstance(build, Mapping):
        return None
    source_revision = build.get("source_revision")
    return source_revision if _is_full_sha(source_revision) else None


def _latest_is_current(receipt: Mapping[str, Any], *, source_sha: str | None) -> bool:
    now = _utc_now()
    if not _valid_latest_receipt(receipt, source_sha=source_sha, now=now):
        return False
    finished = _parse_receipt_utc_z(str(receipt["finished_at"]))
    age = (now - finished).total_seconds()
    return 0 <= age < _RATE_LIMIT_SECONDS


def _parse_receipt_utc_z(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("receipt timestamp must be UTC Z")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("receipt timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


_RECEIPT_KEYS = (
    "schema",
    "owner",
    "provider",
    "check_id",
    "run_id",
    "observed_at",
    "finished_at",
    "source_sha",
    "definition_sha256",
    "credential_reference",
    "credential_version",
    "attempts",
    "status",
    "reason",
    "fields",
    "payload_sha256",
)


def _valid_latest_receipt(
    receipt: Mapping[str, Any],
    *,
    source_sha: str | None,
    now: datetime,
) -> bool:
    try:
        if tuple(receipt.keys()) != _RECEIPT_KEYS:
            return False
        payload_hash = receipt["payload_sha256"]
        if not isinstance(payload_hash, str) or len(payload_hash) != 64:
            return False
        without_hash = dict(receipt)
        del without_hash["payload_sha256"]
        if hashlib.sha256(_canonical_bytes(without_hash)).hexdigest() != payload_hash:
            return False
        run_id = uuid.UUID(str(receipt["run_id"]))
        if run_id.version != 4 or str(run_id) != receipt["run_id"]:
            return False
        observed_at = _parse_receipt_utc_z(receipt["observed_at"])
        finished_at = _parse_receipt_utc_z(receipt["finished_at"])
        if observed_at > finished_at or observed_at > now or finished_at > now:
            return False
        attempts = receipt["attempts"]
        if (
            not isinstance(attempts, int)
            or isinstance(attempts, bool)
            or attempts not in (0, 1)
        ):
            return False
        if receipt["source_sha"] is not None and not _is_full_sha(receipt["source_sha"]):
            return False
        fields = receipt["fields"]
        if not isinstance(fields, Mapping) or tuple(fields.keys()) != _FIELD_NAMES:
            return False
        for value in fields.values():
            if not isinstance(value, Mapping) or tuple(value.keys()) != ("status", "reason"):
                return False
            if value["status"] not in _STATUS_VALUES or value["reason"] not in _REASON_VALUES:
                return False
        return (
            receipt["schema"] == _SCHEMA
            and receipt["owner"] == _OWNER
            and receipt["provider"] == _PROVIDER
            and receipt["check_id"] == _CHECK_ID
            and receipt["source_sha"] == source_sha
            and receipt["definition_sha256"] == _DEFINITION_SHA256
            and receipt["credential_reference"] == _CREDENTIAL_REFERENCE
            and receipt["credential_version"] == "unknown"
            and receipt["status"] == "unknown"
            and receipt["reason"] in _REASON_VALUES
        )
    except (KeyError, TypeError, ValueError):
        return False


def _safe_open_dir(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _dir_identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _leaf_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_gid,
        stat.S_IMODE(value.st_mode),
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _validate_root_stat(data_root: Path, root_fd: int) -> tuple[int, int]:
    current_uid = os.getuid()
    root_stat = os.fstat(root_fd)
    path_stat = os.stat(data_root, follow_symlinks=False)
    if _dir_identity(root_stat) != _dir_identity(path_stat):
        raise _UnsafePathError("credential observation data root changed")
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != current_uid
        or (root_stat.st_mode & 0o002)
    ):
        raise _UnsafePathError("credential observation data root is unsafe")
    return _dir_identity(root_stat)


def _validate_child_stat(root_fd: int, child_fd: int) -> tuple[int, int]:
    current_uid = os.getuid()
    child_stat = os.fstat(child_fd)
    path_stat = os.stat(_RECEIPT_DIR, dir_fd=root_fd, follow_symlinks=False)
    if _dir_identity(child_stat) != _dir_identity(path_stat):
        raise _UnsafePathError("credential observation directory changed")
    if (
        not stat.S_ISDIR(child_stat.st_mode)
        or child_stat.st_uid != current_uid
        or stat.S_IMODE(child_stat.st_mode) != 0o755
    ):
        raise _UnsafePathError("credential observation directory is unsafe")
    return _dir_identity(child_stat)


def _validate_dir_identities(receipt_dir: _ReceiptDirectory) -> None:
    if _validate_root_stat(receipt_dir.path, receipt_dir.root_fd) != receipt_dir.root_identity:
        raise _UnsafePathError("credential observation data root changed")
    if (
        _validate_child_stat(receipt_dir.root_fd, receipt_dir.child_fd)
        != receipt_dir.child_identity
    ):
        raise _UnsafePathError("credential observation directory changed")


def _open_receipt_dir(data_root: Path) -> _ReceiptDirectory:
    root_fd = _safe_open_dir(data_root)
    child_fd: int | None = None
    try:
        root_identity = _validate_root_stat(data_root, root_fd)
        created = False
        try:
            os.mkdir(_RECEIPT_DIR, 0o755, dir_fd=root_fd)
            os.fsync(root_fd)
            created = True
        except FileExistsError:
            pass
        child_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        child_fd = os.open(_RECEIPT_DIR, child_flags, dir_fd=root_fd)
        child_stat = os.fstat(child_fd)
        if created and stat.S_IMODE(child_stat.st_mode) != 0o755:
            try:
                os.fchmod(child_fd, 0o755)
                os.fsync(child_fd)
                os.fsync(root_fd)
                child_stat = os.fstat(child_fd)
            except OSError as exc:
                raise _UnsafePathError("credential observation directory mode") from exc
        child_identity = _validate_child_stat(root_fd, child_fd)
        return _ReceiptDirectory(
            path=data_root,
            root_fd=root_fd,
            child_fd=child_fd,
            root_identity=root_identity,
            child_identity=child_identity,
        )
    except Exception:
        if child_fd is not None:
            os.close(child_fd)
        os.close(root_fd)
        raise


def _close_receipt_dir(receipt_dir: _ReceiptDirectory) -> None:
    os.close(receipt_dir.child_fd)
    os.close(receipt_dir.root_fd)


def _leaf_stat(directory_fd: int) -> os.stat_result | None:
    try:
        return os.stat(
            _RECEIPT_LEAF,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None


def _validate_leaf_stat(leaf_stat: os.stat_result) -> None:
    if (
        not stat.S_ISREG(leaf_stat.st_mode)
        or leaf_stat.st_nlink != 1
        or leaf_stat.st_uid != os.getuid()
        or stat.S_IMODE(leaf_stat.st_mode) != 0o644
        or leaf_stat.st_size > _MAX_RECEIPT_BYTES
    ):
        raise _UnsafePathError("credential observation leaf is unsafe")


def _current_leaf_identity(directory_fd: int) -> tuple[int, int, int, int, int, int, int, int, int] | None:
    leaf_stat = _leaf_stat(directory_fd)
    if leaf_stat is None:
        return None
    _validate_leaf_stat(leaf_stat)
    return _leaf_identity(leaf_stat)


def _read_exact_fd(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(1024, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise _UnsafePathError("credential observation leaf is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _read_latest(receipt_dir: _ReceiptDirectory) -> dict[str, Any] | None:
    _validate_dir_identities(receipt_dir)
    directory_fd = receipt_dir.child_fd
    try:
        fd = os.open(
            _RECEIPT_LEAF,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _UnsafePathError("credential observation leaf is unsafe") from exc
    try:
        fd_stat = os.fstat(fd)
        _validate_leaf_stat(fd_stat)
        before = _current_leaf_identity(directory_fd)
        if before != _leaf_identity(fd_stat):
            raise _UnsafePathError("credential observation leaf changed")
        parsed = _strict_json_loads(_read_exact_fd(fd, _MAX_RECEIPT_BYTES))
        after = _current_leaf_identity(directory_fd)
        if after != before:
            raise _UnsafePathError("credential observation leaf changed")
        return parsed if isinstance(parsed, dict) else None
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    total = 0
    while total < len(view):
        try:
            written = os.write(fd, view[total:])
        except InterruptedError:
            continue
        if written == 0:
            raise _PublishError("credential observation write made no progress")
        total += written


def _publish_latest(receipt_dir: _ReceiptDirectory, payload: bytes) -> Path:
    _validate_dir_identities(receipt_dir)
    child_fd = receipt_dir.child_fd
    temp_name = f".{_RECEIPT_LEAF}.{uuid.uuid4().hex}.tmp"
    temp_created = False
    renamed = False
    try:
        before = _current_leaf_identity(child_fd)
        temp_fd = os.open(
            temp_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o644,
            dir_fd=child_fd,
        )
        temp_created = True
        try:
            os.fchmod(temp_fd, 0o644)
            _write_all(temp_fd, payload)
            temp_stat = os.fstat(temp_fd)
            if (
                not stat.S_ISREG(temp_stat.st_mode)
                or temp_stat.st_uid != os.getuid()
                or stat.S_IMODE(temp_stat.st_mode) != 0o644
                or temp_stat.st_nlink != 1
                or temp_stat.st_size != len(payload)
            ):
                raise _UnsafePathError("credential observation temp is unsafe")
            os.fsync(temp_fd)
        finally:
            os.close(temp_fd)
        temp_read_fd = os.open(
            temp_name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=child_fd,
        )
        try:
            if _read_exact_fd(temp_read_fd, len(payload)) != payload:
                raise _UnsafePathError("credential observation temp mismatch")
        finally:
            os.close(temp_read_fd)
        if _current_leaf_identity(child_fd) != before:
            raise _UnsafePathError("credential observation latest changed")
        _validate_dir_identities(receipt_dir)
        os.replace(temp_name, _RECEIPT_LEAF, src_dir_fd=child_fd, dst_dir_fd=child_fd)
        renamed = True
        temp_created = False
        os.fsync(child_fd)
        readback_fd = os.open(
            _RECEIPT_LEAF,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=child_fd,
        )
        try:
            _validate_leaf_stat(os.fstat(readback_fd))
            readback = _read_exact_fd(readback_fd, len(payload))
        finally:
            os.close(readback_fd)
        if readback != payload:
            raise _DurabilityUnknown("credential observation readback mismatch")
        _validate_dir_identities(receipt_dir)
        return receipt_dir.path / _RECEIPT_DIR / _RECEIPT_LEAF
    except _PublishError:
        raise
    except OSError as exc:
        if renamed:
            raise _DurabilityUnknown("credential observation durability unknown") from exc
        if exc.errno in {
            errno.ELOOP,
            errno.EPERM,
            errno.EACCES,
            errno.ENOTDIR,
            errno.EISDIR,
        }:
            raise _UnsafePathError("credential observation unsafe filesystem") from exc
        raise _PublishError("credential observation publication failed") from exc
    finally:
        if temp_created:
            try:
                os.unlink(temp_name, dir_fd=child_fd)
            except OSError:
                pass


async def observe_tavily_usage_credential(
    config: Any,
    *,
    build: Mapping[str, Any] | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> CredentialObservationResult:
    """Run one Tavily usage metadata observation when explicitly enabled."""
    if not getattr(config, "tavily_usage_observer_enabled", False):
        return CredentialObservationResult(
            status="disabled",
            reason="provider_disabled",
            attempts=0,
        )

    from argus.corpus import paths as corpus_paths

    data_root = corpus_paths.resolve_data_root()
    source_sha = _source_sha(build)
    receipt_dir = _open_receipt_dir(data_root)
    lock_acquired = False
    try:
        try:
            fcntl.flock(receipt_dir.child_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_acquired = True
        except BlockingIOError:
            return CredentialObservationResult(
                status="rate_limited",
                reason="rate_limited",
                attempts=0,
                path=data_root / _RECEIPT_DIR / _RECEIPT_LEAF,
            )
        try:
            latest = _read_latest(receipt_dir)
        except _PublishError as exc:
            logger.warning(
                "Credential observation publication failed code=%s class=%s",
                getattr(exc, "code", "publish_failed"),
                type(exc).__name__,
            )
            return CredentialObservationResult(
                status="publish_failed",
                reason=getattr(exc, "code", "publish_failed"),
                attempts=0,
            )
        if latest is not None and _latest_is_current(latest, source_sha=source_sha):
            return CredentialObservationResult(
                status="rate_limited",
                reason="rate_limited",
                attempts=0,
                path=data_root / _RECEIPT_DIR / _RECEIPT_LEAF,
            )

        observed_at = _utc_now()
        tavily = getattr(config, "tavily", None)
        provider_enabled = bool(getattr(tavily, "enabled", False))
        api_key = str(getattr(tavily, "api_key", "") or "")
        has_key = bool(api_key)
        fields = _base_fields(has_key=has_key)

        attempts = 0
        metadata: _UsageMetadata | None = None
        if not provider_enabled:
            reason = "provider_disabled"
        elif not has_key:
            reason = "optional_not_configured"
        elif source_sha is None:
            reason = "source_unavailable"
        else:
            attempts = 1
            reason, metadata = await _fetch_usage_metadata(
                api_key,
                http_transport=http_transport,
            )

        if reason == "partial_coverage" and metadata is not None:
            fields["authentication"] = _field("verified", "metadata_verified")
            if metadata.limit_known:
                fields["budget"] = _field("verified", "metadata_verified")
        elif attempts:
            if reason == "unauthorized":
                fields["authentication"] = _field("failed", "unauthorized")
            else:
                fields["authentication"] = _field("unknown", reason)

        receipt = _receipt_payload(
            observed_at=observed_at,
            finished_at=_utc_now(),
            source_sha=source_sha,
            attempts=attempts,
            reason=reason,
            fields=fields,
        )
        encoded = _encode_receipt(receipt)
        try:
            path = _publish_latest(receipt_dir, encoded)
        except _PublishError as exc:
            logger.warning(
                "Credential observation publication failed code=%s class=%s",
                getattr(exc, "code", "publish_failed"),
                type(exc).__name__,
            )
            return CredentialObservationResult(
                status="publish_failed",
                reason=getattr(exc, "code", "publish_failed"),
                attempts=attempts,
            )
        return CredentialObservationResult(
            status="published",
            reason=reason,
            attempts=attempts,
            path=path,
        )
    finally:
        if lock_acquired:
            fcntl.flock(receipt_dir.child_fd, fcntl.LOCK_UN)
        _close_receipt_dir(receipt_dir)
