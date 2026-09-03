"""Conformance tests for provider outbound transport boundaries."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from argus.acquisition.models import CredentialPolicy, OperationClass, OriginProfile
from argus.models import ProviderName, SearchQuery, is_adapter_provider


ROOT = Path(__file__).parents[1]
PROVIDER_MODULES = tuple(
    sorted(
        path
        for path in (ROOT / "argus/providers").glob("*.py")
        if path.name
        not in {
            "__init__.py",
            "base.py",
            "normalization.py",
            "fixture_attestation.py",
            "fixture_golden_contracts.py",
            "fixture_harness.py",
            "fixture_registry.py",
        }
    )
)


def _http_client_construction(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Attribute) and function.attr in {
            "AsyncClient",
            "Client",
        }:
            calls.append(node)
    return calls


def test_provider_adapters_do_not_construct_private_http_clients():
    """All adapter HTTP calls must use the shared BaseProvider seam."""

    offenders = {
        str(path.relative_to(ROOT)): len(_http_client_construction(path))
        for path in PROVIDER_MODULES
        if _http_client_construction(path)
    }

    assert offenders == {}


@dataclass
class _Response:
    status_code: int = 200
    headers: dict[str, str] | None = None
    payload: object = None

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}

    def json(self) -> object:
        return self.payload

    @property
    def text(self) -> str:
        return ""


@pytest.mark.asyncio
async def test_base_provider_transport_uses_authenticated_provider_scope(monkeypatch):
    from argus.config import ProviderConfig
    from argus.providers.base import BaseProvider

    class ProbeProvider(BaseProvider):
        def __init__(self) -> None:
            self._config = ProviderConfig(
                enabled=True,
                api_key="provider-secret",
                timeout_seconds=7,
            )

        @property
        def name(self) -> ProviderName:
            return ProviderName.BRAVE

        def is_available(self) -> bool:
            return True

        def status(self):
            return None

        async def search(self, query):
            raise NotImplementedError

    captured: dict[str, Any] = {}

    async def guarded_request(url: str, **kwargs: Any) -> _Response:
        captured["url"] = url
        captured.update(kwargs)
        return _Response(payload={"web": {"results": []}})

    monkeypatch.setattr("argus.providers.base.guarded_http_request", guarded_request)
    provider = ProbeProvider()
    query = SearchQuery(
        query="private user query",
        metadata={"_provider_attempt_id": "attempt-provider-1"},
    )

    response = await provider._provider_request(
        query,
        "https://api.search.brave.com/res/v1/web/search",
        method="GET",
        headers={"X-Subscription-Token": provider._config.api_key},
        params={"q": query.query, "count": 1},
    )

    assert response.status_code == 200
    assert captured["profile"] is OriginProfile.AUTHENTICATED_CONTENT
    assert captured["credential_policy"] == CredentialPolicy.ORIGIN_SCOPED
    assert captured["operation_class"] is OperationClass.DIRECT_HTTP
    assert captured["caller_principal"] == "provider:brave"
    assert captured["request_id"] == "attempt-provider-1"
    assert captured["headers"]["X-Subscription-Token"] == "provider-secret"
    assert captured.get("target_url") is None
    assert captured["timeout"] == 7.0


@pytest.mark.asyncio
async def test_base_provider_transport_uses_only_explicitly_patched_httpx_factory(
    monkeypatch,
):
    from argus.config import ProviderConfig
    from argus.providers.base import BaseProvider

    class ProbeProvider(BaseProvider):
        def __init__(self) -> None:
            self._config = ProviderConfig(enabled=True, timeout_seconds=3)

        @property
        def name(self) -> ProviderName:
            return ProviderName.GITHUB

        def is_available(self) -> bool:
            return True

        def status(self):
            return None

        async def search(self, query):
            raise NotImplementedError

    factory = object()
    monkeypatch.setattr(
        "argus.providers.base.patched_httpx_client",
        lambda _factory: factory,
    )
    calls: list[dict[str, Any]] = []

    async def guarded_request(url: str, **kwargs: Any) -> _Response:
        calls.append({"url": url, **kwargs})
        return _Response(payload={"items": []})

    monkeypatch.setattr("argus.providers.base.guarded_http_request", guarded_request)
    await ProbeProvider()._provider_request(
        SearchQuery(query="query"),
        "https://api.github.com/search/repositories",
        method="GET",
    )

    assert callable(calls[0]["compat_client_factory"])


@pytest.mark.parametrize(
    "provider",
    tuple(provider for provider in ProviderName if is_adapter_provider(provider)),
)
def test_provider_names_have_one_canonical_endpoint_module(provider):
    """The registry remains the source of supported provider adapters."""

    assert provider.value in {path.stem for path in PROVIDER_MODULES} or provider is ProviderName.SEARCHAPI
