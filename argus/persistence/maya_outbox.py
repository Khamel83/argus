"""Bounded, restart-safe delivery of accepted retrieval captures to Maya."""

from __future__ import annotations

import ast
import json
import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qsl, quote, urlencode, unquote, urlsplit, urlunsplit

import httpx

from argus.models import SearchQuery, SearchResponse


_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*[^\r\n]+"
)
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/._~=-]{4,}")
_COOKIE_HEADER_RE = re.compile(r"(?i)\b(?:set-cookie|cookie)\s*:\s*[^\r\n]+")
_SENSITIVE_KEY_PATTERN = r"""
    (?:
        authorization|proxy[_ -]?authorization|
        cookie|set[_ -]?cookie|session[_ -]?cookie|
        access[_ -]?token|refresh[_ -]?token|id[_ -]?token|
        api[_ -]?key|access[_ -]?key|client[_ -]?secret|
        refresh|id|token|secret|password|passwd|signature
    )
"""
_SENSITIVE_KEY_RE = re.compile(rf"(?ix)^{_SENSITIVE_KEY_PATTERN}$")
_SENSITIVE_KEY_VALUE_RE = re.compile(
    rf"""(?ix)
    (?<![A-Za-z0-9_])
    ["']?
    {_SENSITIVE_KEY_PATTERN}
    ["']?
    \s*[:=]\s*
    (?:
        "(?:\\.|[^"\\\r\n])*"|
        '(?:\\.|[^'\\\r\n])*'|
        [^\s,;}}\]\r\n]+
    )
    """
)
_MAYA_SECRET_TOKEN_RE = re.compile(
    r"(?i)\b(?:token|secret|key)[_-][A-Za-z0-9][A-Za-z0-9._-]{7,}\b|"
    r"\b(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"AKIA[0-9A-Z]{12,})\b"
)
_PEM_RE = re.compile(
    r"-----BEGIN [^-]+-----.*?(?:-----END [^-]+-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_MAYA_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MAYA_ERROR_CODES = {
    "capture_idempotency_conflict",
    "capture_request_too_large",
    "content_hash_mismatch",
    "invalid_capture_request",
    "unsafe_capture_payload",
}
_MAYA_RECEIPT_KEYS = {
    "capture_id",
    "caller",
    "page_ids",
    "duplicate",
    "children_added",
    "received_at",
}


def _is_sensitive_key(value: object) -> bool:
    return _SENSITIVE_KEY_RE.fullmatch(str(value).strip()) is not None


def _sanitize_structure(value: object, *, depth: int = 0) -> object:
    if depth >= 8:
        return "[truncated]"
    if isinstance(value, dict):
        sanitized = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 128:
                sanitized["[truncated]"] = True
                break
            safe_key = str(key)[:256]
            sanitized[safe_key] = (
                "[redacted]"
                if _is_sensitive_key(safe_key)
                else _sanitize_structure(item, depth=depth + 1)
            )
        return sanitized
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_structure(item, depth=depth + 1) for item in value[:128]
        ]
    if isinstance(value, str):
        return value[:4096]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4096]


def _bounded_text(value: object, scan_limit: int) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            _sanitize_structure(value),
            ensure_ascii=False,
            separators=(",", ":"),
        )[:scan_limit]
    text = str(value or "")[:scan_limit]
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        parsed = None
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError, json.JSONDecodeError):
            try:
                parsed = ast.literal_eval(stripped)
            except (TypeError, ValueError, SyntaxError, MemoryError, RecursionError):
                pass
        if isinstance(parsed, (dict, list, tuple)):
            return json.dumps(
                _sanitize_structure(parsed),
                ensure_ascii=False,
                separators=(",", ":"),
            )[:scan_limit]
    return text


def _redact_text(value: object, limit: int) -> str:
    scan_limit = min(max(limit * 4, 4096), 65_536)
    text = _bounded_text(value, scan_limit).replace("\x00", "")
    text = _AUTH_HEADER_RE.sub("[redacted]", text)
    text = _AUTH_SCHEME_RE.sub("[redacted]", text)
    text = _COOKIE_HEADER_RE.sub("[redacted]", text)
    text = _SENSITIVE_KEY_VALUE_RE.sub("[redacted]", text)
    text = _MAYA_SECRET_TOKEN_RE.sub("[redacted]", text)
    return _PEM_RE.sub("[redacted]", text)[:limit]


def safe_failure_summary(value: str | None) -> str | None:
    if not value:
        return None
    redacted = _redact_text(value, 256)
    return redacted.replace("\n", " ").replace("\r", " ")


def _safe_text(value: object, limit: int) -> str:
    return _redact_text(value, limit).replace("\r", " ").replace("\n", " ")


def _safe_url(value: str) -> str:
    parts = urlsplit(value)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    query = [
        (_safe_text(key, 256), _safe_text(item, 2048))
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_sensitive_key(key)
    ]
    path = quote(_safe_text(unquote(parts.path), 2048), safe="/:@-._~")
    return urlunsplit((parts.scheme, host, path, urlencode(query), ""))[:2048]


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds") + (
        "Z" if value.tzinfo is None else ""
    )


def _mode(value: str) -> str:
    mode = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").lower()).strip("-_")[:64]
    return mode if mode and mode[0].isalpha() else "extraction"


def _provenance(
    *,
    providers: list[str] | None = None,
    provider: str | None = None,
    egress: str | None = None,
    machine: str | None = None,
    source_type: str | None = None,
) -> dict:
    safe_egress = (
        egress if egress in {"residential", "datacenter", "unknown"} else "unknown"
    )
    value = {
        "egress": safe_egress,
        "machine": _safe_text(machine or "unknown", 128) or "unknown",
        "source_type": _safe_text(source_type or "search", 64) or "search",
    }
    if provider:
        value["provider"] = _safe_text(provider, 64)
    if providers:
        value["providers"] = [_safe_text(item, 64) for item in providers[:16] if item]
    return value


def search_capture_payload(
    query: SearchQuery,
    response: SearchResponse,
    *,
    completed_at: datetime,
) -> dict:
    providers = sorted(
        {
            result.provider.value
            for result in response.results
            if result.provider is not None
        }
    )
    summary_parts = []
    for rank, result in enumerate(response.results[:50], start=1):
        summary_parts.append(
            f"{rank}. {_safe_text(result.title, 1024)} — "
            f"{_safe_url(result.url)} — {_safe_text(result.snippet, 2048)}"
        )
    summary = _safe_text("\n".join(summary_parts), 16_384)
    if not summary:
        summary = f"No results returned for {_safe_text(query.query, 4096)}"
    egresses = {
        result.metadata.get("egress") for result in response.results if result.metadata
    }
    machines = {
        result.metadata.get("machine")
        for result in response.results
        if result.metadata and result.metadata.get("machine")
    }
    return {
        "idempotency_key": response.search_run_id,
        "query": _safe_text(query.query, 4096) or "[redacted]",
        "mode": query.mode.value,
        "result_summary": summary,
        "provenance": _provenance(
            providers=providers,
            egress=next(iter(egresses)) if len(egresses) == 1 else "unknown",
            machine=next(iter(machines)) if len(machines) == 1 else "unknown",
            source_type="search",
        ),
        "started_at": _timestamp(response.created_at),
        "completed_at": _timestamp(completed_at),
        "pages": [],
    }


def extraction_capture_payload(
    *,
    public_id: str,
    mode: str,
    result,
    completed_at: datetime,
) -> tuple[dict, str | None]:
    content = _safe_text(result.text, 1_048_576)
    content_sha256 = (
        hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
    )
    provenance = _provenance(
        provider=result.extractor.value if result.extractor else None,
        egress=result.egress,
        machine=result.machine,
        source_type=result.source_type or "extraction",
    )
    pages = []
    if content:
        pages.append(
            {
                "position": 0,
                "source_url": _safe_url(result.url),
                "title": _safe_text(result.title or result.url, 1024)
                or "Extracted page",
                "content": content,
                "content_sha256": content_sha256,
                "provenance": provenance,
                "extracted_at": _timestamp(result.extracted_at),
            }
        )
    summary = _safe_text(
        (
            f"Extracted {result.word_count} words from {_safe_url(result.url)}"
            if content
            else f"Extraction failed for {_safe_url(result.url)}"
        ),
        16_384,
    )
    return (
        {
            "idempotency_key": public_id,
            "query": _safe_url(result.url) or "[redacted]",
            "mode": _mode(mode),
            "result_summary": summary,
            "provenance": provenance,
            "started_at": _timestamp(result.extracted_at),
            "completed_at": _timestamp(completed_at),
            "pages": pages,
        },
        content_sha256,
    )


def excludes_capture(caller: str, *, user_visible: bool = True) -> bool:
    return not user_visible or caller.strip().lower() in {
        "probe",
        "synthetic",
        "deployment-probe",
        "health-probe",
        "deployment-canary",
        "argus-reachability",
        "legacy-import",
    }


class MayaOutboxDispatcher:
    """Deliver a claimed outbox batch with at-least-once semantics."""

    def __init__(
        self,
        repository,
        *,
        endpoint: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 15.0,
        batch_size: int = 20,
        lease_seconds: int = 60,
    ):
        self.repository = repository
        self.endpoint = endpoint
        self.token = token
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(tz=None))
        self.timeout_seconds = timeout_seconds
        self.batch_size = max(1, min(int(batch_size), 100))
        self.lease_seconds = max(
            10,
            min(
                max(
                    int(lease_seconds),
                    int(timeout_seconds * self.batch_size) + 15,
                ),
                14_400,
            ),
        )

    def run_once(self) -> dict[str, int]:
        now = self.clock()
        claimed = self.repository.claim_maya_outbox(
            now=now,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        outcomes: Counter[str] = Counter()
        if not claimed:
            return {}
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        with httpx.Client(
            transport=self.transport,
            timeout=self.timeout_seconds,
        ) as client:
            for item in claimed:
                outcome = self._deliver_one(client, headers, item)
                outcomes[outcome] += 1
        return dict(outcomes)

    def _deliver_one(self, client, headers, item) -> str:
        now = self.clock()
        try:
            response = client.post(
                self.endpoint,
                headers=headers,
                content=item["payload_json"],
            )
        except httpx.HTTPError as exc:
            status = self.repository.fail_maya_outbox(
                item["id"],
                lease_token=item["lease_token"],
                transient=True,
                error_code="maya_unavailable",
                error_summary=type(exc).__name__,
                now=now,
            )
            return self._failure_outcome(status)

        if response.status_code in {200, 201}:
            try:
                body = response.json()
            except (TypeError, ValueError):
                body = None
            audit = self._validated_receipt(
                response.status_code,
                body,
                item["payload_json"],
            )
            if audit is None:
                status = self.repository.fail_maya_outbox(
                    item["id"],
                    lease_token=item["lease_token"],
                    transient=True,
                    error_code="invalid_maya_receipt",
                    error_summary="Maya returned an invalid success receipt",
                    now=now,
                )
                return self._failure_outcome(status)
            acknowledged = self.repository.acknowledge_maya_outbox(
                item["id"],
                lease_token=item["lease_token"],
                response=audit,
                now=now,
            )
            return "acknowledged" if acknowledged else "lease_lost"

        if 200 <= response.status_code < 300:
            status = self.repository.fail_maya_outbox(
                item["id"],
                lease_token=item["lease_token"],
                transient=True,
                error_code="invalid_maya_receipt",
                error_summary="Maya returned an unsupported success status",
                now=now,
            )
            return self._failure_outcome(status)

        transient = (
            response.status_code in {408, 425, 429} or response.status_code >= 500
        )
        code, summary = self._error_detail(response)
        status = self.repository.fail_maya_outbox(
            item["id"],
            lease_token=item["lease_token"],
            transient=transient,
            error_code=code,
            error_summary=summary,
            now=now,
        )
        return self._failure_outcome(status)

    @staticmethod
    def _failure_outcome(status: str | None) -> str:
        if status is None:
            return "lease_lost"
        if status == "retry":
            return "retried"
        return "dead_lettered"

    @staticmethod
    def _validated_receipt(
        status_code: int,
        body: object,
        payload_json: str,
    ) -> dict | None:
        if not isinstance(body, dict) or set(body) != _MAYA_RECEIPT_KEYS:
            return None
        try:
            payload = json.loads(payload_json)
            expected_pages = payload["pages"]
            received_at_text = body["received_at"]
            if not isinstance(received_at_text, str):
                return None
            received_at = datetime.fromisoformat(
                received_at_text.replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        capture_id = body["capture_id"]
        caller = body["caller"]
        page_ids = body["page_ids"]
        duplicate = body["duplicate"]
        children_added = body["children_added"]
        if (
            not isinstance(expected_pages, list)
            or len(expected_pages) > 16
            or not isinstance(capture_id, str)
            or _MAYA_ID_RE.fullmatch(capture_id) is None
            or caller != "argus"
            or not isinstance(page_ids, list)
            or len(page_ids) != len(expected_pages)
            or not all(
                isinstance(page_id, str) and _MAYA_ID_RE.fullmatch(page_id) is not None
                for page_id in page_ids
            )
            or len(set(page_ids)) != len(page_ids)
            or not isinstance(duplicate, bool)
            or isinstance(children_added, bool)
            or not isinstance(children_added, int)
            or not 0 <= children_added <= len(expected_pages)
            or received_at.tzinfo is None
            or len(received_at_text) > 64
            or (status_code == 200 and (not duplicate or children_added != 0))
            or (status_code == 201 and duplicate)
        ):
            return None
        return {
            "capture_id": capture_id,
            "caller": "argus",
            "page_ids": page_ids,
            "duplicate": duplicate,
            "children_added": children_added,
            "received_at": received_at.astimezone(timezone.utc).isoformat(),
        }

    @staticmethod
    def _error_detail(response: httpx.Response) -> tuple[str, str]:
        code = f"http_{response.status_code}"
        summary = f"Maya returned HTTP {response.status_code}"
        try:
            body = response.json()
        except (TypeError, ValueError, json.JSONDecodeError):
            return code, summary
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            remote_code = detail.get("code")
            if isinstance(remote_code, str) and remote_code in _MAYA_ERROR_CODES:
                code = remote_code
            summary = str(detail.get("message") or summary)
        elif isinstance(detail, str):
            summary = detail
        return code, safe_failure_summary(summary) or summary
