"""Shared auth helpers for HTTP API and MCP transports."""

from __future__ import annotations

import ipaddress
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

    @classmethod
    def from_env(cls) -> "AuthConfig":
        caller_api_key = os.environ.get("ARGUS_API_KEY", "").strip()
        admin_api_key = os.environ.get("ARGUS_ADMIN_API_KEY", "").strip() or caller_api_key
        cors_origins = tuple(parse_cors_origins(os.environ.get("ARGUS_CORS_ORIGINS")))
        return cls(
            caller_api_key=caller_api_key,
            admin_api_key=admin_api_key,
            cors_origins=cors_origins,
        )

    def has_caller_key(self) -> bool:
        return bool(self.caller_api_key)

    def has_admin_key(self) -> bool:
        return bool(self.admin_api_key)

    def matches_caller_token(self, token: str | None) -> bool:
        return bool(token) and token == self.caller_api_key

    def matches_admin_token(self, token: str | None) -> bool:
        return bool(token) and token == self.admin_api_key


def is_admin_path(path: str) -> bool:
    return path.startswith("/api/admin/")


def is_public_path(path: str) -> bool:
    if path == "/api/health":
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
    }


def remote_mcp_requires_auth(transport: str, host: str) -> bool:
    """Remote HTTP MCP transports require auth unless bound to loopback."""
    return transport in {"sse", "streamable-http"} and not is_local_client(host)
