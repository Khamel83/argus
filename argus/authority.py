"""Execution-authority boundaries shared by API and protocol adapters."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping
from urllib.parse import urlsplit

import httpx

from argus.provider_controls import HERMETIC_PROVIDER_ENV_PREFIXES

if TYPE_CHECKING:
    from argus.mcp.capabilities import ContractSelection


class AuthorityConfigurationError(RuntimeError):
    """Raised when a process is configured across an authority boundary."""


@dataclass(frozen=True)
class AuthorityClientConfig:
    base_url: str
    token: str


class AuthorityRequestError(RuntimeError):
    """Bounded adapter-facing failure from the execution authority."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class HttpAuthorityClient:
    """Small authenticated client for the sole Argus execution authority."""

    _MAX_RESPONSE_BYTES = 11 * 1024 * 1024

    def __init__(
        self,
        config: AuthorityClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 120.0,
    ):
        self._config = config
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        from argus.mcp.capabilities import HttpContractResolver

        self._http_contract_resolver = HttpContractResolver(self._discover_capabilities)

    @property
    def authority_origin(self) -> str:
        """Canonical origin used to scope in-process discovery entries."""
        parts = urlsplit(self._config.base_url)
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}"

    async def _discover_capabilities(
        self, authority_origin: str
    ) -> Mapping[str, object]:
        if authority_origin != self.authority_origin:
            raise AuthorityRequestError("Argus execution authority unavailable")
        return await self.request("GET", "/api/capabilities")

    async def resolve_http_contract(
        self,
        deployment_id: str | None,
        clock: Callable[[], float],
        *,
        refresh: bool = False,
    ) -> ContractSelection:
        """Discover a route family with GET only, before operation execution."""
        return await self._http_contract_resolver.resolve_http_contract(
            self.authority_origin,
            deployment_id,
            clock,
            refresh=refresh,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        status_code, content = await self._bounded_response(
            method,
            path,
            payload=payload,
            token=token,
        )
        if status_code >= 400:
            messages = {
                401: "Argus execution authority authentication failed",
                403: "Argus execution authority denied the request",
                404: "Argus execution authority route unavailable",
                422: "Argus execution authority rejected the request",
                429: "Argus execution authority rate limited the request",
            }
            message = messages.get(
                status_code,
                (
                    "Argus execution authority unavailable"
                    if status_code >= 500
                    else "Argus execution authority request failed"
                ),
            )
            raise AuthorityRequestError(
                message,
                status_code=status_code,
            )
        return self._decode_response(content, status_code=status_code)

    async def request_v2(
        self,
        path: str,
        *,
        payload: Mapping[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        """Return an exact v2 envelope even when its HTTP status is non-2xx."""
        status_code, content = await self._bounded_response(
            "POST",
            path,
            payload=payload,
            token=token,
        )
        body = self._decode_response(content, status_code=status_code)
        from argus.contracts import validate_v2_envelope

        try:
            validate_v2_envelope(body, http_status=status_code)
        except ValueError as exc:
            raise AuthorityRequestError(
                "Argus execution authority returned an invalid response",
                status_code=status_code,
            ) from exc
        return body

    async def _bounded_response(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None,
        token: str | None,
    ) -> tuple[int, bytes]:
        headers = {
            "Authorization": f"Bearer {token or self._config.token}",
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._config.base_url,
                transport=self._transport,
                timeout=self._timeout_seconds,
            ) as client:
                async with client.stream(
                    method,
                    path,
                    json=dict(payload) if payload is not None else None,
                    headers=headers,
                ) as response:
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self._MAX_RESPONSE_BYTES:
                            raise AuthorityRequestError(
                                "Argus execution authority response exceeded "
                                "the size limit",
                                status_code=response.status_code,
                            )
                        chunks.append(chunk)
                    return response.status_code, b"".join(chunks)
        except httpx.HTTPError as exc:
            raise AuthorityRequestError(
                "Argus execution authority unavailable"
            ) from exc

    @staticmethod
    def _decode_response(content: bytes, *, status_code: int) -> dict[str, Any]:
        try:
            body = json.loads(content)
        except ValueError as exc:
            raise AuthorityRequestError(
                "Argus execution authority returned an invalid response",
                status_code=status_code,
            ) from exc
        if not isinstance(body, dict):
            raise AuthorityRequestError(
                "Argus execution authority returned an invalid response",
                status_code=status_code,
            )
        return body

    async def search(
        self,
        payload: Mapping[str, Any],
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/api/search",
            payload=payload,
            token=token,
        )


_FORBIDDEN_ADAPTER_EXACT = {
    "ARGUS_ADMIN_API_KEY",
    "ARGUS_DB_URL",
    "ARGUS_BUDGET_DB_PATH",
    "ARGUS_DATA_ROOT",
    "ARGUS_COOKIE_DIR",
    "ARGUS_OBSCURA_CDP_URL",
    "ARGUS_MAYA_CAPTURE_URL",
    "ARGUS_MAYA_CAPTURE_TOKEN",
    "ARGUS_RESIDENTIAL_SHARED_SECRET",
    "ARGUS_EGRESS_SHARED_SECRET",
    "PLAYWRIGHT_BROWSERS_PATH",
    "WOLFRAM_APP_ID",
    "JINA_API_KEY",
    "FIRECRAWL_API_KEY",
}
_FORBIDDEN_ADAPTER_PREFIXES = (
    "ARGUS_MAYA_",
    "ARGUS_RESIDENTIAL_",
    "ARGUS_EGRESS_",
)


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def adapter_execution_mode(environ: Mapping[str, str] | None = None) -> str:
    """Choose HTTP authority, explicit development standalone, or no mode."""
    values = environ if environ is not None else os.environ
    if values.get("ARGUS_AUTHORITY_URL", "").strip():
        return "http"
    if values.get(
        "ARGUS_ENV", "development"
    ).strip().lower() != "production" and _is_true(values.get("ARGUS_MCP_STANDALONE")):
        return "standalone"
    return "unconfigured"


def _forbidden_adapter_inputs(values: Mapping[str, str]) -> list[str]:
    provider_prefixes = tuple(
        f"ARGUS_{provider}_" for provider in HERMETIC_PROVIDER_ENV_PREFIXES
    )
    bare_provider_keys = {
        f"{provider}_API_KEY" for provider in HERMETIC_PROVIDER_ENV_PREFIXES
    }
    forbidden = []
    for name, value in values.items():
        if not str(value or "").strip():
            continue
        if (
            name in _FORBIDDEN_ADAPTER_EXACT
            or name in bare_provider_keys
            or name.startswith(_FORBIDDEN_ADAPTER_PREFIXES)
            or (
                name.startswith(provider_prefixes)
                and name not in {"ARGUS_PROVIDER_RECONCILIATION_KEYS_JSON"}
            )
        ):
            forbidden.append(name)
    return sorted(forbidden)


def authority_client_config(
    environ: Mapping[str, str] | None = None,
    *,
    adapter: str,
) -> AuthorityClientConfig:
    """Validate a credential-minimal stateless HTTP adapter configuration."""
    values = environ if environ is not None else os.environ
    base_url = values.get("ARGUS_AUTHORITY_URL", "").strip().rstrip("/")
    token = values.get("ARGUS_AUTHORITY_TOKEN", "").strip()
    if not base_url:
        raise AuthorityConfigurationError(f"{adapter} requires ARGUS_AUTHORITY_URL")
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise AuthorityConfigurationError(
            f"{adapter} requires a valid HTTP authority URL"
        )
    if not token:
        raise AuthorityConfigurationError(
            f"{adapter} requires authority authentication"
        )
    forbidden = (
        _forbidden_adapter_inputs(values)
        if values.get("ARGUS_ENV", "development").strip().lower() == "production"
        else []
    )
    if forbidden:
        raise AuthorityConfigurationError(
            f"{adapter} received forbidden execution inputs: " + ", ".join(forbidden)
        )
    return AuthorityClientConfig(base_url=base_url, token=token)


def broker_construction_allowed(*, authority_capability: object | None) -> None:
    """Reject a second production broker outside the HTTP API process."""
    if os.environ.get("ARGUS_ENV", "development").strip().lower() != "production":
        return
    from argus.api.main import _HTTP_API_AUTHORITY_CAPABILITY

    if authority_capability is not _HTTP_API_AUTHORITY_CAPABILITY:
        raise RuntimeError(
            "Production broker construction is reserved for the "
            "HTTP API execution authority"
        )
    if os.environ.get("ARGUS_NODE_ROLE", "primary").strip().lower() != "primary":
        raise RuntimeError(
            "Production HTTP API execution authority requires ARGUS_NODE_ROLE=primary"
        )


def extraction_execution_allowed(*, authority_capability: object | None) -> None:
    """Reject production browser/extractor execution outside the HTTP API."""
    from argus.api.main import _HTTP_API_AUTHORITY_CAPABILITY

    if (
        os.environ.get("ARGUS_ENV", "development").strip().lower() == "production"
        and authority_capability is not _HTTP_API_AUTHORITY_CAPABILITY
    ):
        raise RuntimeError(
            "Production extraction is reserved for the HTTP API execution authority"
        )


def worker_execution_allowed() -> None:
    """Legacy provider workers are development-only."""
    if os.environ.get("ARGUS_ENV", "development").strip().lower() == "production":
        raise RuntimeError(
            "Production provider execution is reserved for the "
            "HTTP API execution authority"
        )
