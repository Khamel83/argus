"""Pre-execution Host, Origin, credential, media, and body guards."""

from __future__ import annotations

import os
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request

from argus.api.contracts_v2 import EvidenceHttpPresenter, admission_operation
from argus.contracts import CanonicalOutcome
from argus.operations.status import safe_correlation_id

_MAX_V2_BODY_BYTES = 1_048_576


class TransportSecurityConfigurationError(RuntimeError):
    """A remotely reachable production listener lacks an explicit policy."""


@dataclass(frozen=True, slots=True)
class RetrievalSessionAuthority:
    """Issue bounded opaque retrieval-session IDs bound to one principal."""

    secret: bytes

    @classmethod
    def from_environment(cls) -> "RetrievalSessionAuthority | None":
        value = os.environ.get("ARGUS_RETRIEVAL_SESSION_SECRET", "")
        return cls(value.encode()) if len(value) >= 32 else None

    def issue(self, principal: str) -> str:
        nonce = secrets.token_hex(16)
        principal_digest = hashlib.sha256(principal.encode()).hexdigest()[:16]
        payload = f"r2.{principal_digest}.{nonce}"
        signature = hmac.new(
            self.secret,
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
        return f"{payload}.{signature}"

    def owns(self, session_id: str, principal: str) -> bool:
        if len(session_id) > 96:
            return False
        parts = session_id.split(".")
        if len(parts) != 4 or parts[0] != "r2":
            return False
        version, principal_digest, nonce, signature = parts
        if (
            len(principal_digest) != 16
            or len(nonce) != 32
            or len(signature) != 32
        ):
            return False
        expected_principal = hashlib.sha256(principal.encode()).hexdigest()[:16]
        payload = f"{version}.{principal_digest}.{nonce}"
        expected_signature = hmac.new(
            self.secret,
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
        return hmac.compare_digest(principal_digest, expected_principal) and (
            hmac.compare_digest(signature, expected_signature)
        )


def _csv(name: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.environ.get(name, "").split(",")
        if item.strip()
    )


@dataclass(frozen=True, slots=True)
class TransportSecurityGuard:
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    host_policy_explicit: bool
    origin_policy_explicit: bool

    @classmethod
    def from_environment(cls) -> "TransportSecurityGuard":
        origin_name = (
            "ARGUS_ALLOWED_ORIGINS"
            if "ARGUS_ALLOWED_ORIGINS" in os.environ
            else "ARGUS_CORS_ORIGINS"
        )
        return cls(
            allowed_hosts=_csv("ARGUS_ALLOWED_HOSTS"),
            allowed_origins=_csv(origin_name),
            host_policy_explicit="ARGUS_ALLOWED_HOSTS" in os.environ,
            origin_policy_explicit=origin_name in os.environ,
        )

    def validate_startup(
        self,
        *,
        production: bool,
        bind_host: str,
        has_bearer_auth: bool,
    ) -> None:
        if not production or bind_host in {"localhost", "127.0.0.1", "::1"}:
            return
        if not self.host_policy_explicit or not self.allowed_hosts:
            raise TransportSecurityConfigurationError(
                "remote production requires explicit allowed hosts"
            )
        if not self.origin_policy_explicit:
            raise TransportSecurityConfigurationError(
                "remote production requires an explicit Origin policy"
            )
        if not has_bearer_auth:
            raise TransportSecurityConfigurationError(
                "remote production requires bearer authentication"
            )

    def _host_allowed(self, request: Request) -> bool:
        raw_host = request.headers.get("host", "")
        if raw_host in self.allowed_hosts:
            return True
        hostname = request.url.hostname or ""
        if request.client and request.client.host == "testclient":
            return hostname == "testserver"
        return hostname in {"localhost", "127.0.0.1", "::1"}

    def _origin_allowed(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        if origin is None:
            return True
        if origin == "null" or origin not in self.allowed_origins:
            return False
        parsed = urlsplit(origin)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )

    async def rejection(self, request: Request):
        request_id = safe_correlation_id(request.headers.get("x-request-id"))
        presenter = EvidenceHttpPresenter()
        if not self._host_allowed(request):
            return presenter.response(
                admission_operation(
                    outcome=CanonicalOutcome.POLICY_REJECTED,
                    request_id=request_id,
                    detail="Request Host is not allowed",
                    code="misdirected_request",
                )
            )
        if not self._origin_allowed(request):
            return presenter.response(
                admission_operation(
                    outcome=CanonicalOutcome.POLICY_REJECTED,
                    request_id=request_id,
                    detail="Request Origin is not allowed",
                )
            )
        if not request.url.path.startswith("/api/v2"):
            return None
        carriers = sum(
            bool(request.headers.get(name, "").strip())
            for name in ("authorization", "x-api-key", "x-admin-api-key")
        )
        if carriers > 1:
            return presenter.response(
                admission_operation(
                    outcome=CanonicalOutcome.AUTHENTICATION_REJECTED,
                    request_id=request_id,
                    detail="Exactly one bearer credential is allowed",
                )
            )
        if request.method in {"POST", "PUT", "PATCH"}:
            content_type = request.headers.get("content-type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                return presenter.response(
                    admission_operation(
                        outcome=CanonicalOutcome.INVALID_REQUEST,
                        request_id=request_id,
                        detail="Request media type must be application/json",
                        code="unsupported_media_type",
                    )
                )
            try:
                content_length = int(request.headers.get("content-length", "0"))
            except ValueError:
                content_length = _MAX_V2_BODY_BYTES + 1
            if content_length > _MAX_V2_BODY_BYTES:
                return presenter.response(
                    admission_operation(
                        outcome=CanonicalOutcome.INVALID_REQUEST,
                        request_id=request_id,
                        detail="Request body exceeds the route limit",
                        code="payload_too_large",
                    )
                )
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > _MAX_V2_BODY_BYTES:
                    return presenter.response(
                        admission_operation(
                            outcome=CanonicalOutcome.INVALID_REQUEST,
                            request_id=request_id,
                            detail="Request body exceeds the route limit",
                            code="payload_too_large",
                        )
                    )
                body.extend(chunk)
            # Starlette replays this bounded cache to downstream parsing.
            request._body = bytes(body)
        return None
