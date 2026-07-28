"""Content-addressed, no-network provider fixture attestations."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from pathlib import Path
from typing import Mapping

from argus.broker.provider_evidence import (
    FailureCategory,
    ProviderRequestEvidence,
    query_hash,
)
from argus.models import ProviderName
from argus.providers.normalization import (
    classify_provider_failure_response,
    normalize_provider_response,
)


_CANONICAL_ADAPTERS = {
    ProviderName.BRAVE: ("argus.providers.brave", "BraveProvider"),
    ProviderName.DUCKDUCKGO: (
        "argus.providers.duckduckgo", "DuckDuckGoProvider"
    ),
    ProviderName.EXA: ("argus.providers.exa", "ExaProvider"),
    ProviderName.GITHUB: ("argus.providers.github", "GitHubProvider"),
    ProviderName.LINKUP: ("argus.providers.linkup", "LinkupProvider"),
    ProviderName.PARALLEL: ("argus.providers.parallel", "ParallelProvider"),
    ProviderName.SEARCHAPI: ("argus.providers.searchapi", "SearchApiProvider"),
    ProviderName.SEARXNG: ("argus.providers.searxng", "SearXNGProvider"),
    ProviderName.SERPER: ("argus.providers.serper", "SerperProvider"),
    ProviderName.TAVILY: ("argus.providers.tavily", "TavilyProvider"),
    ProviderName.VALYU: ("argus.providers.valyu", "ValyuProvider"),
    ProviderName.WOLFRAM: ("argus.providers.wolfram", "WolframProvider"),
    ProviderName.YAHOO: ("argus.providers.yahoo", "YahooProvider"),
    ProviderName.YOU: ("argus.providers.you", "YouProvider"),
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_path() -> Path:
    return Path(__file__).with_name("fixture_contracts.json")


def _shared_dependency_hash() -> str:
    root = Path(__file__).parents[1]
    paths = (
        root / "providers" / "base.py",
        root / "providers" / "normalization.py",
        root / "broker" / "provider_evidence.py",
        Path(__file__),
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _payload(provider: ProviderName, rows: list[object]) -> dict[str, object]:
    if provider is ProviderName.BRAVE:
        return {"web": {"results": rows}}
    if provider is ProviderName.GITHUB:
        return {"items": rows}
    if provider is ProviderName.SERPER:
        return {"organic": rows}
    if provider is ProviderName.YOU:
        return {"results": {"web": rows}}
    if provider is ProviderName.SEARCHAPI:
        return {"organic_results": rows}
    if provider is ProviderName.WOLFRAM:
        return (
            {
                "answer": "fixture answer",
                "query_url": "https://www.wolframalpha.com/",
            }
            if rows else {"empty": True}
        )
    return {"results": rows}


def _row(provider: ProviderName) -> dict[str, object]:
    url = "https://example.com/fixture"
    fields = {
        ProviderName.DUCKDUCKGO: {
            "href": url, "title": "Fixture", "body": "fixture snippet"
        },
        ProviderName.GITHUB: {
            "html_url": url,
            "full_name": "fixture/repository",
            "description": "fixture snippet",
        },
        ProviderName.LINKUP: {
            "url": url, "name": "Fixture", "content": "fixture snippet"
        },
        ProviderName.SERPER: {
            "link": url, "title": "Fixture", "snippet": "fixture snippet"
        },
        ProviderName.SEARXNG: {
            "url": url, "title": "Fixture", "content": "fixture snippet"
        },
        ProviderName.TAVILY: {
            "url": url, "title": "Fixture", "content": "fixture snippet"
        },
        ProviderName.VALYU: {
            "url": url, "title": "Fixture", "description": "fixture snippet"
        },
        ProviderName.BRAVE: {
            "url": url, "title": "Fixture", "description": "fixture snippet"
        },
        ProviderName.PARALLEL: {
            "url": url, "title": "Fixture", "excerpt": "fixture snippet"
        },
        ProviderName.EXA: {
            "url": url, "title": "Fixture", "text": "fixture snippet"
        },
        ProviderName.SEARCHAPI: {
            "link": url, "title": "Fixture", "snippet": "fixture snippet"
        },
        ProviderName.YOU: {
            "url": url, "title": "Fixture", "description": "fixture snippet"
        },
        ProviderName.YAHOO: {
            "url": url, "title": "Fixture", "snippet": "fixture snippet"
        },
    }
    return fields.get(provider, {"url": url, "title": "Fixture"})


def run_fixture_cases(provider: ProviderName) -> str:
    """Execute success, empty, error, malformed, and privacy cases."""
    secret = "fixture-private-query-value"
    request = ProviderRequestEvidence(
        effective_query_hash=query_hash(secret),
        provider_query_hash=query_hash(secret),
    )
    success = normalize_provider_response(
        provider,
        _payload(provider, [_row(provider)]),
        max_results=1,
        request_evidence=request,
    )
    empty = normalize_provider_response(
        provider,
        _payload(provider, []),
        max_results=1,
        request_evidence=request,
    )
    malformed = normalize_provider_response(
        provider,
        {"unexpected": [secret]},
        max_results=1,
        request_evidence=request,
    )
    error = classify_provider_failure_response(
        provider,
        {"transport": {"status_code": 429}, "body": {}},
    )
    if len(success.observations) != 1 or success.failure is not None:
        raise ValueError("fixture success case failed")
    if empty.failure is None or empty.failure.category is not FailureCategory.EMPTY:
        raise ValueError("fixture empty case failed")
    if (
        malformed.failure is None
        or malformed.failure.category is not FailureCategory.PARSE_ERROR
    ):
        raise ValueError("fixture malformed case failed")
    if error.category is not FailureCategory.RATE_LIMITED:
        raise ValueError("fixture error case failed")
    safe = json.dumps(
        {
            "success": success.safe_log_record(),
            "empty": empty.safe_log_record(),
            "malformed": malformed.safe_log_record(),
            "error": error.safe_log_record(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    if secret in safe:
        raise ValueError("fixture privacy case failed")
    return hashlib.sha256(safe.encode()).hexdigest()


def build_fixture_attestation(
    provider: ProviderName,
    *,
    release: str,
    provider_contract: str,
    adapter_module: str | None = None,
    adapter_class: str | None = None,
) -> tuple[str, Mapping[str, str]]:
    """Build an attestation from the exact checked-in executable inputs."""
    manifest_path = _manifest_path()
    manifest = json.loads(manifest_path.read_bytes())
    contract = manifest["providers"][provider.value]
    module_name, class_name = _CANONICAL_ADAPTERS[provider]
    if adapter_module is not None and adapter_module != module_name:
        raise ValueError("runtime adapter module is not the canonical adapter")
    if adapter_class is not None and adapter_class != class_name:
        raise ValueError("runtime adapter class is not the canonical adapter")
    module = importlib.import_module(module_name)
    module_path = Path(module.__file__ or "")
    provider_class = getattr(module, class_name)
    payload = {
        "provider": provider.value,
        "release": release,
        "adapter_module": module_name,
        "adapter_class": class_name,
        "adapter_code_sha256": _sha256_file(module_path),
        "adapter_identity_sha256": hashlib.sha256(
            inspect.getsource(provider_class).encode()
        ).hexdigest(),
        "shared_adapter_sha256": _shared_dependency_hash(),
        "fixture_manifest_sha256": _sha256_file(manifest_path),
        "fixture_case_digest": run_fixture_cases(provider),
        "request_contract": str(contract["request_contract"]),
        "response_contract": str(contract["response_contract"]),
        "provider_contract": provider_contract,
    }
    ref = "attestation:" + hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()[:48]
    return ref, payload


def verify_fixture_attestation(
    attestation: Mapping[str, str],
    *,
    evidence_ref: str | None = None,
) -> bool:
    """Recompute every content address and executable case digest."""
    try:
        provider = ProviderName(attestation["provider"])
        expected_ref, expected = build_fixture_attestation(
            provider,
            release=attestation["release"],
            provider_contract=attestation["provider_contract"],
            adapter_module=attestation["adapter_module"],
            adapter_class=attestation["adapter_class"],
        )
    except (
        AttributeError,
        KeyError,
        ValueError,
        ImportError,
        OSError,
        TypeError,
        json.JSONDecodeError,
    ):
        return False
    supplied_ref = "attestation:" + hashlib.sha256(json.dumps(
        dict(attestation), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()[:48]
    return (
        dict(attestation) == dict(expected)
        and supplied_ref == expected_ref
        and (evidence_ref is None or evidence_ref == expected_ref)
    )
