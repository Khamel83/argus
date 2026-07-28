"""Load and verify checked canonical-adapter fixture attestations."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Mapping

from argus.models import ProviderName
from argus.providers.fixture_harness import (  # noqa: F401
    run_fixture_cases as run_fixture_cases,
)
from argus.providers.fixture_registry import CANONICAL_ADAPTERS, canonical_adapter


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_path() -> Path:
    return Path(__file__).with_name("fixture_contracts.json")


def attestation_artifact_path() -> Path:
    return Path(__file__).with_name("fixture_attestations.json")


def _shared_dependency_hash() -> str:
    root = Path(__file__).parents[1]
    paths = (
        root / "providers" / "base.py",
        root / "providers" / "normalization.py",
        root / "providers" / "fixture_harness.py",
        root / "providers" / "fixture_registry.py",
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


def _adapter_search_hash(provider_class) -> str:
    return hashlib.sha256(
        inspect.getsource(provider_class.search).encode()
    ).hexdigest()


def _load_document() -> Mapping[str, object]:
    payload = json.loads(attestation_artifact_path().read_bytes())
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported fixture attestation schema")
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        raise ValueError("fixture attestation providers are missing")
    return payload


def _content_ref(payload: Mapping[str, str]) -> str:
    return "attestation:" + hashlib.sha256(json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()[:48]


def load_fixture_attestation(
    provider: ProviderName,
    *,
    adapter_module: str | None = None,
    adapter_class: str | None = None,
) -> tuple[str, Mapping[str, str]]:
    """Load one checked artifact; runtime values cannot regenerate its claims."""
    module_name, class_name = CANONICAL_ADAPTERS[provider]
    if adapter_module is not None and adapter_module != module_name:
        raise ValueError("runtime adapter module is not the canonical adapter")
    if adapter_class is not None and adapter_class != class_name:
        raise ValueError("runtime adapter class is not the canonical adapter")
    entry = _load_document()["providers"].get(provider.value)
    if not isinstance(entry, dict):
        raise ValueError("provider fixture attestation is missing")
    evidence_ref = entry.get("evidence_ref")
    attestation = entry.get("attestation")
    if not isinstance(evidence_ref, str) or not isinstance(attestation, dict):
        raise ValueError("provider fixture attestation is malformed")
    return evidence_ref, dict(attestation)


def build_fixture_attestation(
    provider: ProviderName,
    *,
    release: str,
    provider_contract: str,
    adapter_module: str | None = None,
    adapter_class: str | None = None,
) -> tuple[str, Mapping[str, str]]:
    """Compatibility wrapper for callers migrating to the checked loader."""
    del release, provider_contract
    return load_fixture_attestation(
        provider,
        adapter_module=adapter_module,
        adapter_class=adapter_class,
    )


def verify_fixture_attestation(
    attestation: Mapping[str, str],
    *,
    evidence_ref: str | None = None,
) -> bool:
    """Verify checked content and current code identity without running fixtures."""
    try:
        provider = ProviderName(attestation["provider"])
        expected_ref, expected = load_fixture_attestation(
            provider,
            adapter_module=attestation["adapter_module"],
            adapter_class=attestation["adapter_class"],
        )
        module_name, class_name, module, provider_class = canonical_adapter(provider)
        module_path = Path(module.__file__ or "")
        live_checks = {
            "adapter_module": module_name,
            "adapter_class": class_name,
            "adapter_code_sha256": _sha256_file(module_path),
            "adapter_identity_sha256": _adapter_search_hash(provider_class),
            "shared_adapter_sha256": _shared_dependency_hash(),
            "fixture_manifest_sha256": _sha256_file(_manifest_path()),
        }
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
    return (
        dict(attestation) == dict(expected)
        and all(attestation.get(key) == value for key, value in live_checks.items())
        and _content_ref(attestation) == expected_ref
        and (evidence_ref is None or evidence_ref == expected_ref)
    )
