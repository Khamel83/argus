"""Bounded, restart-safe delivery of accepted retrieval captures to Maya."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import threading
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import (
    parse_qsl,
    quote,
    urlencode,
    unquote,
    urlsplit,
    urlunsplit,
)

import httpx

from argus.api.lifecycle import LifecycleCapability
from argus.models import SearchQuery, SearchResponse


_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*[^\r\n]+"
)
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/._~=-]{4,}")
_COOKIE_HEADER_RE = re.compile(r"(?i)\b(?:set-cookie|cookie)\s*:\s*[^\r\n]+")
_KEY_VALUE_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
    (?<![A-Za-z0-9_.%+\\\-\[\]])
    (?:
        "(?P<double_key>(?:\\.|[^"\\\r\n])+)"|
        '(?P<single_key>(?:\\.|[^'\\\r\n])+)'|
        (?P<bare_key>[A-Za-z_%\\][A-Za-z0-9_.%+\\\-\[\]]*)
    )
    \s*[:=]\s*
    )
    (?P<value>
    (?:
        "(?:\\.|[^"\\\r\n])*"|
        '(?:\\.|[^'\\\r\n])*'|
        [^\s,;}\]"'\r\n]+
    )
    )
    """
)
_JSON_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_MAX_DECODE_ROUNDS = 8
_MAX_DECODE_CHARS = 1_048_576
_MAX_REDACTION_DEPTH = 8
_MAX_INSPECTION_WORK = 6_291_456
_MAX_URL_INPUT_BYTES = 8192
_MAX_URL_QUERY_FIELDS = 64
_MAX_URL_OUTPUT_BYTES = 2048
_MAYA_MAX_RETRIEVAL_BODY_BYTES = 10 * 1024 * 1024
_MAYA_TRANSPORT_HEADROOM_BYTES = 1024 * 1024
_MAYA_RECEIPT_MAX_BYTES = 64 * 1024
MAYA_RETRIEVAL_PAYLOAD_MAX_BYTES = (
    _MAYA_MAX_RETRIEVAL_BODY_BYTES - _MAYA_TRANSPORT_HEADROOM_BYTES
)
_SENSITIVE_KEY_SUBSTRINGS = (
    "auth",
    "session",
    "credential",
    "token",
    "secret",
    "password",
    "passwd",
    "cookie",
    "signature",
    "apikey",
    "accesskey",
    "refresh",
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


def _decode_identifier_state(value: object) -> tuple[str, bool]:
    raw = str(value).strip()
    decoded = raw[:_MAX_DECODE_CHARS]
    unresolved = len(raw) > _MAX_DECODE_CHARS
    for _ in range(_MAX_DECODE_ROUNDS):
        candidate = unquote(decoded)
        candidate = _JSON_UNICODE_ESCAPE_RE.sub(
            lambda match: chr(int(match.group(1), 16)),
            candidate,
        )[:_MAX_DECODE_CHARS]
        if candidate == decoded:
            return decoded, unresolved
        decoded = candidate
    return decoded, True


def _decode_identifier(value: object) -> str:
    return _decode_identifier_state(value)[0]


def _is_sensitive_key(value: object) -> bool:
    decoded, unresolved = _decode_identifier_state(value)
    if unresolved:
        return True
    decoded = unicodedata.normalize("NFKC", decoded)
    with_camel_boundaries = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", decoded)
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        with_camel_boundaries.lower(),
    ).strip("_")
    tokens = tuple(token for token in normalized.split("_") if token)
    compact = "".join(tokens)
    return "id" in tokens or any(
        marker in compact for marker in _SENSITIVE_KEY_SUBSTRINGS
    )


def _match_key(match: re.Match) -> str:
    return (
        match.group("double_key")
        or match.group("single_key")
        or match.group("bare_key")
    )


def _contains_sensitive_material(
    value: object,
    *,
    depth: int = 0,
    work: list[int] | None = None,
) -> bool:
    if work is None:
        work = [_MAX_INSPECTION_WORK]
    if work[0] <= 0:
        return True
    if isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            if index >= 128:
                return True
            if _is_sensitive_key(key) or _contains_sensitive_material(
                item,
                depth=depth + 1,
                work=work,
            ):
                return True
        return False
    if isinstance(value, (list, tuple)):
        if len(value) > 128:
            return True
        return any(
            _contains_sensitive_material(item, depth=depth + 1, work=work)
            for item in value
        )
    if not isinstance(value, str):
        return False

    text = value[: min(len(value), work[0], _MAX_DECODE_CHARS)]
    truncated = len(text) < len(value)
    work[0] -= len(text)
    if any(
        _is_sensitive_key(_match_key(match)) for match in _KEY_VALUE_RE.finditer(text)
    ):
        return True
    if truncated:
        return True
    if depth >= _MAX_REDACTION_DEPTH:
        return bool(re.search(r"[\[{]|%[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}", text))
    stripped = text.lstrip()
    structured_candidate = stripped.startswith(("{", "[", "(", "'", '"'))
    encoded_candidate = bool(re.search(r"%[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}", text))
    if not structured_candidate and not encoded_candidate:
        return False

    if structured_candidate:
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(text)
            except (TypeError, ValueError, SyntaxError, MemoryError, RecursionError):
                continue
            if parsed == text:
                break
            return _contains_sensitive_material(
                parsed,
                depth=depth + 1,
                work=work,
            )

    decoded, unresolved = _decode_identifier_state(text)
    if decoded != text:
        return _contains_sensitive_material(
            decoded,
            depth=depth + 1,
            work=work,
        )
    normalized_encoded = re.sub(r"[^a-z0-9]+", "", decoded.lower())
    return (
        unresolved
        and bool(re.search(r"%[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}", decoded))
        and any(marker in normalized_encoded for marker in _SENSITIVE_KEY_SUBSTRINGS)
    )


def _sanitize_structure(value: object, *, depth: int = 0) -> object:
    if depth >= 8:
        return "[truncated]"
    if isinstance(value, dict):
        sanitized = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 128:
                sanitized["[truncated]"] = True
                break
            raw_key = str(key)
            safe_key = raw_key[:256]
            sanitized[safe_key] = (
                "[redacted]"
                if _is_sensitive_key(raw_key)
                else _sanitize_structure(item, depth=depth + 1)
            )
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_structure(item, depth=depth + 1) for item in value[:128]]
    if isinstance(value, str):
        bounded = value[:4096]
        return "[redacted]" if _contains_sensitive_material(bounded) else bounded
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
    return str(value or "")[:scan_limit]


def _redact_text(value: object, limit: int, *, _depth: int = 0) -> str:
    scan_limit = limit if limit > 65_536 else max(limit * 4, 4096)
    if isinstance(value, str) and _contains_sensitive_material(
        value[:scan_limit],
        depth=_depth,
    ):
        return "[redacted]"
    text = _bounded_text(value, scan_limit).replace("\x00", "")
    text = _AUTH_HEADER_RE.sub("[redacted]", text)
    text = _AUTH_SCHEME_RE.sub("[redacted]", text)
    text = _COOKIE_HEADER_RE.sub("[redacted]", text)
    text = _MAYA_SECRET_TOKEN_RE.sub("[redacted]", text)
    return _PEM_RE.sub("[redacted]", text)[:limit]


def safe_failure_summary(value: str | None) -> str | None:
    if not value:
        return None
    redacted = _redact_text(value, 256)
    return redacted.replace("\n", " ").replace("\r", " ")


def _safe_text(value: object, limit: int) -> str:
    return _redact_text(value, limit).replace("\r", " ").replace("\n", " ")


def _safe_content(value: object, limit: int) -> str:
    return _redact_text(value, limit)


def _bounded_utf8(value: object, limit: int) -> str:
    text = str(value or "")[:limit]
    return text.encode("utf-8", errors="ignore")[:limit].decode(
        "utf-8",
        errors="ignore",
    )


def _maya_payload_json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fit_maya_payload_to_budget(payload: dict) -> dict:
    fitted = copy.deepcopy(payload)
    if len(_maya_payload_json(fitted).encode("utf-8")) <= (
        MAYA_RETRIEVAL_PAYLOAD_MAX_BYTES
    ):
        return fitted

    pages = fitted.get("pages")
    if not isinstance(pages, list):
        raise ValueError("Maya capture metadata exceeds request budget")
    content_pages = [
        page
        for page in pages
        if isinstance(page, dict)
        and isinstance(page.get("content"), str)
        and page["content"]
    ]
    if not content_pages:
        raise ValueError("Maya capture metadata exceeds request budget")
    originals = [page["content"] for page in content_pages]

    def apply_ratio(ratio: int) -> None:
        for page, original in zip(content_pages, originals, strict=True):
            length = max(1, len(original) * ratio // 1_000_000)
            page["content"] = original[:length]

    apply_ratio(0)
    if len(_maya_payload_json(fitted).encode("utf-8")) > (
        MAYA_RETRIEVAL_PAYLOAD_MAX_BYTES
    ):
        raise ValueError("Maya capture metadata exceeds request budget")

    low = 0
    high = 1_000_000
    while low < high:
        candidate = (low + high + 1) // 2
        apply_ratio(candidate)
        if len(_maya_payload_json(fitted).encode("utf-8")) <= (
            MAYA_RETRIEVAL_PAYLOAD_MAX_BYTES
        ):
            low = candidate
        else:
            high = candidate - 1
    apply_ratio(low)
    for page in content_pages:
        page["content_sha256"] = hashlib.sha256(
            page["content"].encode("utf-8")
        ).hexdigest()
    if len(_maya_payload_json(fitted).encode("utf-8")) > (
        MAYA_RETRIEVAL_PAYLOAD_MAX_BYTES
    ):
        raise ValueError("Maya capture payload exceeds request budget")
    return fitted


def maya_payload_json(payload: dict) -> str:
    return _maya_payload_json(_fit_maya_payload_to_budget(payload))


def _safe_url(value: str) -> str:
    try:
        parts = urlsplit(_bounded_utf8(value, _MAX_URL_INPUT_BYTES))
        port = parts.port
    except ValueError:
        return "[redacted]"
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port:
        host = f"{host}:{port}"
    try:
        query_fields = parse_qsl(
            parts.query,
            keep_blank_values=True,
            max_num_fields=_MAX_URL_QUERY_FIELDS,
        )
    except ValueError:
        query_fields = []
    query = []
    for key, item in query_fields:
        if not _is_sensitive_key(key):
            query.append((_safe_text(key, 256), _safe_text(item, 2048)))
    path_segments = []
    redact_next = False
    for segment in _decode_identifier(parts.path).split("/"):
        if redact_next:
            path_segments.append("[redacted]")
            redact_next = False
        elif _is_sensitive_key(segment):
            path_segments.append("[redacted]")
            redact_next = True
        else:
            path_segments.append(_safe_text(segment, 2048))
    path = quote("/".join(path_segments), safe="/:@-._~[]")
    return _bounded_utf8(
        urlunsplit((parts.scheme, host, path, urlencode(query), "")),
        _MAX_URL_OUTPUT_BYTES,
    )


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
    return _fit_maya_payload_to_budget(
        {
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
    )


def extraction_capture_payload(
    *,
    public_id: str,
    mode: str,
    result,
    completed_at: datetime,
) -> tuple[dict, str | None]:
    content = _safe_content(result.text, 1_048_576)
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
                "title": (
                    _safe_text(result.title, 1024)
                    if result.title
                    else _safe_url(result.url)
                )
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
    payload = _fit_maya_payload_to_budget(
        {
            "idempotency_key": public_id,
            "query": _safe_url(result.url) or "[redacted]",
            "mode": _mode(mode),
            "result_summary": summary,
            "provenance": provenance,
            "started_at": _timestamp(result.extracted_at),
            "completed_at": _timestamp(completed_at),
            "pages": pages,
        }
    )
    fitted_content_hash = (
        payload["pages"][0]["content_sha256"] if payload["pages"] else None
    )
    return payload, fitted_content_hash


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

    lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED
    dispatch_capability = LifecycleCapability.COOPERATIVE_BOUNDED

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

    def run_once(
        self,
        *,
        stop_event: threading.Event | None = None,
    ) -> dict[str, int]:
        if stop_event is not None and stop_event.is_set():
            return {}
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
            timeout=httpx.Timeout(
                self.timeout_seconds,
                read=min(1.0, self.timeout_seconds),
            ),
        ) as client:
            for index, item in enumerate(claimed):
                if stop_event is not None and stop_event.is_set():
                    self._release_untouched_claims(claimed[index:])
                    break
                attempt = self.repository.begin_maya_outbox_attempt(
                    item["id"],
                    lease_token=item["lease_token"],
                    now=self.clock(),
                )
                if attempt is None:
                    outcomes["lease_lost"] += 1
                    continue
                item.update(attempt)
                if stop_event is not None and stop_event.is_set():
                    self.repository.release_maya_outbox_claim(
                        item["id"],
                        lease_token=item["lease_token"],
                        now=self.clock(),
                        attempt_started=True,
                        previous_last_attempt_at=item["previous_last_attempt_at"],
                    )
                    self._release_untouched_claims(claimed[index + 1 :])
                    break
                outcome = self._deliver_one(client, headers, item)
                outcomes[outcome] += 1
        return dict(outcomes)

    def _release_untouched_claims(self, claimed: list[dict]) -> None:
        now = self.clock()
        for item in claimed:
            self.repository.release_maya_outbox_claim(
                item["id"],
                lease_token=item["lease_token"],
                now=now,
            )

    def _deliver_one(self, client, headers, item) -> str:
        now = self.clock()
        try:
            response = self._post_with_total_deadline(
                client,
                headers,
                item["payload_json"],
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

    def _post_with_total_deadline(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        payload_json: str,
    ) -> httpx.Response:
        """Read one bounded receipt within a wall-clock delivery deadline."""
        deadline = time.monotonic() + self.timeout_seconds
        with client.stream(
            "POST",
            self.endpoint,
            headers=headers,
            content=payload_json,
        ) as response:
            content = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() > deadline:
                    raise httpx.ReadTimeout(
                        "Maya delivery exceeded its total deadline",
                        request=response.request,
                    )
                content.extend(chunk)
                if len(content) > _MAYA_RECEIPT_MAX_BYTES:
                    raise httpx.DecodingError(
                        "Maya receipt exceeded its response budget",
                        request=response.request,
                    )
            if time.monotonic() > deadline:
                raise httpx.ReadTimeout(
                    "Maya delivery exceeded its total deadline",
                    request=response.request,
                )
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=bytes(content),
                request=response.request,
            )

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
            or not len(expected_pages) <= len(page_ids) <= 1000
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
            or (
                status_code == 201
                and (
                    duplicate
                    or (
                        expected_pages
                        and not 1 <= children_added <= len(expected_pages)
                    )
                    or (not expected_pages and (children_added != 0 or page_ids))
                )
            )
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
