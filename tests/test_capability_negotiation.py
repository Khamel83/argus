from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest


@dataclass
class Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def _legacy_document() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "execution_authority": "http-api",
        "role": "primary",
        "capabilities": {
            "search": True,
            "extraction": True,
            "recovery": True,
            "expansion": True,
        },
    }


def _v2_document() -> dict[str, object]:
    return {
        **_legacy_document(),
        "http_contracts": [
            {"version": "1", "base_path": "/api", "legacy": True},
            {"version": "2.0", "base_path": "/api/v2", "legacy": False},
        ],
    }


@pytest.mark.asyncio
async def test_resolver_selects_advertised_v2_and_only_discovers_with_get():
    from argus.authority import AuthorityClientConfig, HttpAuthorityClient

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json=_v2_document())

    client = HttpAuthorityClient(
        AuthorityClientConfig("https://authority.example", "service-token"),
        transport=httpx.MockTransport(handler),
    )

    selection = await client.resolve_http_contract("deploy-1", Clock())

    assert selection.contract_version == "2.0"
    assert selection.base_path == "/api/v2"
    assert selection.outcome == "ready"
    assert requests == [("GET", "/api/capabilities")]


@pytest.mark.asyncio
async def test_resolver_selects_legacy_only_for_a_proven_legacy_document():
    from argus.mcp.capabilities import HttpContractResolver

    async def discover(_origin: str):
        return _legacy_document()

    selection = await HttpContractResolver(discover).resolve_http_contract(
        "https://authority.example", "deploy-1", Clock()
    )

    assert selection.contract_version == "1"
    assert selection.base_path == "/api"
    assert selection.outcome == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        {"http_contracts": []},
        {**_legacy_document(), "http_contracts": "not-a-list"},
        {
            **_legacy_document(),
            "http_contracts": [
                {"version": "2.0", "base_path": "/api", "legacy": False}
            ],
        },
    ],
)
async def test_resolver_fails_closed_for_malformed_discovery(document):
    from argus.mcp.capabilities import HttpContractResolver

    async def discover(_origin: str):
        return document

    selection = await HttpContractResolver(discover).resolve_http_contract(
        "https://authority.example", "deploy-1", Clock()
    )

    assert selection.outcome == "unready"
    assert selection.contract_version is None
    assert selection.base_path is None


@pytest.mark.asyncio
async def test_resolver_never_falls_back_after_a_v2_server_discovery_failure():
    from argus.mcp.capabilities import HttpContractResolver

    calls = 0

    async def discover(_origin: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _v2_document()
        raise httpx.ConnectError("unavailable")

    clock = Clock()
    resolver = HttpContractResolver(discover)

    selected = await resolver.resolve_http_contract(
        "https://authority.example", "deploy-1", clock
    )
    clock.now = 60.0
    unavailable = await resolver.resolve_http_contract(
        "https://authority.example", "deploy-1", clock
    )

    assert selected.contract_version == "2.0"
    assert unavailable.outcome == "unready"
    assert unavailable.contract_version is None
    assert calls == 2


@pytest.mark.asyncio
async def test_resolver_expires_entries_after_sixty_seconds():
    from argus.mcp.capabilities import HttpContractResolver

    calls = 0

    async def discover(_origin: str):
        nonlocal calls
        calls += 1
        return _v2_document()

    clock = Clock()
    resolver = HttpContractResolver(discover)

    first = await resolver.resolve_http_contract(
        "https://authority.example", "deploy-1", clock
    )
    clock.now = 59.999
    cached = await resolver.resolve_http_contract(
        "https://authority.example", "deploy-1", clock
    )
    clock.now = 60.0
    refreshed = await resolver.resolve_http_contract(
        "https://authority.example", "deploy-1", clock
    )

    assert first == cached == refreshed
    assert calls == 2


@pytest.mark.asyncio
async def test_resolver_scopes_cache_by_origin_and_invalidates_on_deployment_change():
    from argus.mcp.capabilities import HttpContractResolver

    documents = {
        "https://one.example": _v2_document(),
        "https://two.example": _legacy_document(),
    }
    calls = []

    async def discover(origin: str):
        calls.append(origin)
        return documents[origin]

    resolver = HttpContractResolver(discover)
    clock = Clock()

    one = await resolver.resolve_http_contract("https://one.example", "a", clock)
    two = await resolver.resolve_http_contract("https://two.example", "a", clock)
    documents["https://one.example"] = _legacy_document()
    changed_deployment = await resolver.resolve_http_contract(
        "https://one.example", "b", clock
    )

    assert one.contract_version == "2.0"
    assert two.contract_version == "1"
    assert changed_deployment.contract_version == "1"
    assert calls == [
        "https://one.example",
        "https://two.example",
        "https://one.example",
    ]


@pytest.mark.asyncio
async def test_resolver_evicts_the_least_recently_used_entry_at_configured_bound():
    from argus.mcp.capabilities import HttpContractResolver

    calls = []

    async def discover(origin: str):
        calls.append(origin)
        return _v2_document()

    resolver = HttpContractResolver(discover, max_entries=2)
    clock = Clock()

    await resolver.resolve_http_contract("https://one.example", "deploy-1", clock)
    await resolver.resolve_http_contract("https://two.example", "deploy-1", clock)
    await resolver.resolve_http_contract("https://one.example", "deploy-1", clock)
    await resolver.resolve_http_contract("https://three.example", "deploy-1", clock)
    await resolver.resolve_http_contract("https://one.example", "deploy-1", clock)
    await resolver.resolve_http_contract("https://two.example", "deploy-1", clock)

    assert calls == [
        "https://one.example",
        "https://two.example",
        "https://three.example",
        "https://two.example",
    ]


@pytest.mark.asyncio
async def test_discovery_error_does_not_overwrite_a_still_valid_entry():
    from argus.mcp.capabilities import HttpContractResolver

    calls = 0

    async def discover(_origin: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _legacy_document()
        raise httpx.ReadTimeout("timed out")

    clock = Clock()
    resolver = HttpContractResolver(discover)
    valid = await resolver.resolve_http_contract(
        "https://authority.example", "deploy-1", clock
    )
    unavailable = await resolver.resolve_http_contract(
        "https://authority.example", "deploy-1", clock, refresh=True
    )
    retained = await resolver.resolve_http_contract(
        "https://authority.example", "deploy-1", clock
    )

    assert valid.contract_version == "1"
    assert unavailable.outcome == "unready"
    assert retained == valid
    assert calls == 2
