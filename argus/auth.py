"""Shared auth helpers for HTTP API and MCP transports."""

from __future__ import annotations

import ipaddress
import hmac
import json
import os
from dataclasses import dataclass


def is_local_client(client_host: str | None) -> bool:
    """Return True when the caller is clearly loopback/local."""
    if not client_host:
        return False

    if client_host in {"localhost", "testclient"}:
        return True

    try:
        ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False

    return ip.is_loopback


def extract_api_token(headers, *header_names: str) -> str | None:
    """Read an API token from Bearer auth or a list of custom headers."""
    auth_header = headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            return token

    for header_name in header_names:
        token = headers.get(header_name, "").strip()
        if token:
            return token

    return None


def parse_cors_origins(raw_value: str | None) -> list[str]:
    """Parse a comma-separated origin list for CORS."""
    if not raw_value:
        return []
    return [origin.strip() for origin in raw_value.split(",") if origin.strip()]


@dataclass(frozen=True)
class AuthConfig:
    caller_api_key: str
    admin_api_key: str
    cors_origins: tuple[str, ...]
    scoped_caller_credentials: tuple[tuple[str, str], ...] = ()
    provider_reconciliation_credentials: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_env(cls) -> "AuthConfig":
        caller_api_key = os.environ.get("ARGUS_API_KEY", "").strip()
        admin_api_key = os.environ.get("ARGUS_ADMIN_API_KEY", "").strip() or caller_api_key
        cors_origins = tuple(parse_cors_origins(os.environ.get("ARGUS_CORS_ORIGINS")))
        scoped_caller_credentials = cls._parse_scoped_credentials(
            os.environ.get("ARGUS_CALLER_CREDENTIALS_JSON")
        )
        provider_reconciliation_credentials = cls._parse_provider_credentials(
            os.environ.get("ARGUS_PROVIDER_RECONCILIATION_KEYS_JSON")
        )
        return cls(
            caller_api_key=caller_api_key,
            admin_api_key=admin_api_key,
            cors_origins=cors_origins,
            scoped_caller_credentials=scoped_caller_credentials,
            provider_reconciliation_credentials=provider_reconciliation_credentials,
        )

    def has_caller_key(self) -> bool:
        return bool(self.caller_api_key or self.scoped_caller_credentials)

    def has_admin_key(self) -> bool:
        return bool(self.admin_api_key)

    def matches_caller_token(self, token: str | None) -> bool:
        return self.identity_for_token(token) is not None

    def matches_admin_token(self, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(token, self.admin_api_key)

    def identity_for_token(self, token: str | None) -> str | None:
        if not token:
            return None
        for identity, candidate in self.scoped_caller_credentials:
            if hmac.compare_digest(token, candidate):
                return identity
        if self.caller_api_key and hmac.compare_digest(token, self.caller_api_key):
            return "legacy-http"
        return None

    def matches_provider_reconciliation_token(
        self,
        provider: str,
        token: str | None,
    ) -> bool:
        if not token:
            return False
        for configured_provider, candidate in self.provider_reconciliation_credentials:
            if configured_provider == provider and hmac.compare_digest(token, candidate):
                return True
        return False

    @staticmethod
    def _parse_scoped_credentials(
        raw_value: str | None,
    ) -> tuple[tuple[str, str], ...]:
        if not raw_value:
            return ()
        try:
            payload = json.loads(raw_value)
        except (TypeError, ValueError):
            return ()
        if not isinstance(payload, dict):
            return ()
        credentials = []
        for identity, config in payload.items():
            if not isinstance(identity, str) or not identity.strip():
                continue
            token = config.get("token") if isinstance(config, dict) else None
            if isinstance(token, str) and token:
                credentials.append((identity.strip(), token))
        return tuple(credentials)

    @staticmethod
    def _parse_provider_credentials(
        raw_value: str | None,
    ) -> tuple[tuple[str, str], ...]:
        if not raw_value:
            return ()
        try:
            payload = json.loads(raw_value)
        except (TypeError, ValueError):
            return ()
        if not isinstance(payload, dict):
            return ()
        credentials = []
        for provider, config in payload.items():
            if not isinstance(provider, str) or not provider.strip():
                continue
            token = config.get("token") if isinstance(config, dict) else config
            if isinstance(token, str) and token:
                credentials.append((provider.strip().lower(), token))
        return tuple(credentials)


def is_admin_path(path: str) -> bool:
    return path.startswith("/api/admin/")


def is_public_path(path: str) -> bool:
    if path in {
        "/api/live",
        "/api/startup",
        "/api/ready",
        "/api/health",
    }:
        return True
    if path.startswith("/dashboard"):
        return True
    return False


def is_caller_path(path: str) -> bool:
    if path.startswith("/api/workflows/"):
        return True
    return path in {
        "/api/search",
        "/api/recover-url",
        "/api/expand",
        "/api/extract",
        "/api/assess-content",
        "/api/capabilities",
        "/api/provider-health",
        "/api/budgets",
    }


def remote_mcp_requires_auth(transport: str, host: str) -> bool:
    """Remote HTTP MCP transports require auth unless bound to loopback."""
    return transport in {"sse", "streamable-http"} and not is_local_client(host)
