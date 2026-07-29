"""Fail-closed HTTP contract discovery for protocol adapters."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


_CACHE_TTL_SECONDS = 60.0
_MAX_CACHE_ENTRIES = 128


@dataclass(frozen=True, slots=True)
class ContractSelection:
    """The one HTTP contract an adapter may use for an operation."""

    contract_version: str | None
    base_path: str | None
    outcome: str

    @classmethod
    def unready(cls) -> ContractSelection:
        return cls(contract_version=None, base_path=None, outcome="unready")


@dataclass(frozen=True, slots=True)
class _CachedSelection:
    selection: ContractSelection
    expires_at: float


Discovery = Callable[[str], Awaitable[Mapping[str, object]]]


class HttpContractResolver:
    """Discover one explicit HTTP route family without executing an operation."""

    def __init__(self, discover: Discovery, *, max_entries: int = _MAX_CACHE_ENTRIES):
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._discover = discover
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple[str, str], _CachedSelection] = OrderedDict()

    async def resolve_http_contract(
        self,
        authority_origin: str,
        deployment_id: str | None,
        clock: Callable[[], float],
        *,
        refresh: bool = False,
    ) -> ContractSelection:
        """Resolve v2, proven legacy, or unready before any execution request.

        ``refresh`` exists for callers that already have authoritative deployment
        change information. Discovery failure never replaces a valid cached choice.
        """
        origin = _normalize_origin(authority_origin)
        if origin is None:
            return ContractSelection.unready()
        deployment = _normalize_deployment_id(deployment_id)
        now = clock()
        self._discard_expired(now)
        if deployment is not None:
            self._discard_other_deployments(origin, deployment)
            key = (origin, deployment)
            cached = self._cache.get(key)
            if cached is not None and not refresh:
                self._cache.move_to_end(key)
                return cached.selection

        try:
            document = await self._discover(origin)
        except Exception:
            return ContractSelection.unready()

        selection = _select_contract(document)
        if selection.outcome != "ready" or deployment is None:
            return selection
        self._cache[(origin, deployment)] = _CachedSelection(
            selection=selection,
            expires_at=now + _CACHE_TTL_SECONDS,
        )
        self._cache.move_to_end((origin, deployment))
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)
        return selection

    def _discard_expired(self, now: float) -> None:
        for key, cached in tuple(self._cache.items()):
            if cached.expires_at <= now:
                del self._cache[key]

    def _discard_other_deployments(self, origin: str, deployment: str) -> None:
        for key in tuple(self._cache):
            if key[0] == origin and key[1] != deployment:
                del self._cache[key]


def _normalize_origin(authority_origin: str) -> str | None:
    parsed = urlsplit(authority_origin)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _normalize_deployment_id(deployment_id: str | None) -> str | None:
    if not isinstance(deployment_id, str):
        return None
    value = deployment_id.strip()
    return value or None


def _select_contract(document: object) -> ContractSelection:
    if not _is_capability_document(document):
        return ContractSelection.unready()
    if "http_contracts" not in document:
        return ContractSelection("1", "/api", "ready")
    contracts = document["http_contracts"]
    if not isinstance(contracts, list) or not contracts:
        return ContractSelection.unready()
    if not all(isinstance(contract, Mapping) for contract in contracts):
        return ContractSelection.unready()
    for contract in contracts:
        if (
            contract.get("version") == "2.0"
            and contract.get("base_path") == "/api/v2"
            and contract.get("legacy") is False
        ):
            return ContractSelection("2.0", "/api/v2", "ready")
    return ContractSelection.unready()


def _is_capability_document(document: object) -> bool:
    if not isinstance(document, Mapping):
        return False
    capabilities = document.get("capabilities")
    if (
        document.get("schema_version") != "1.0"
        or document.get("execution_authority") != "http-api"
        or document.get("role") != "primary"
        or not isinstance(capabilities, Mapping)
    ):
        return False
    return all(
        capabilities.get(name) is True
        for name in ("search", "extraction", "recovery", "expansion")
    )
