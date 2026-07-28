#!/usr/bin/env python3
"""Generate or verify hermetic canonical-adapter fixture attestations."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from argus.models import ProviderName
from argus.providers.fixture_attestation import (
    _adapter_search_hash,
    _content_ref,
    _manifest_path,
    _sha256_file,
    _shared_dependency_hash,
    attestation_artifact_path,
)
from argus.providers.fixture_harness import run_fixture_cases
from argus.providers.fixture_registry import canonical_adapter


def generate_attestation_document() -> dict[str, object]:
    manifest_path = _manifest_path()
    manifest = json.loads(manifest_path.read_bytes())
    providers = {}
    for provider in ProviderName:
        if provider is ProviderName.CACHE:
            continue
        module_name, class_name, module, provider_class = canonical_adapter(provider)
        contract = manifest["providers"][provider.value]
        attestation = {
            "provider": provider.value,
            "release": "canonical-adapter-fixtures-v1",
            "adapter_module": module_name,
            "adapter_class": class_name,
            "adapter_code_sha256": _sha256_file(Path(module.__file__ or "")),
            "adapter_identity_sha256": _adapter_search_hash(provider_class),
            "shared_adapter_sha256": _shared_dependency_hash(),
            "fixture_manifest_sha256": _sha256_file(manifest_path),
            "fixture_case_digest": run_fixture_cases(provider),
            "request_contract": str(contract["request_contract"]),
            "response_contract": str(contract["response_contract"]),
            "provider_contract": str(contract["contract_version"]),
        }
        providers[provider.value] = {
            "attestation": attestation,
            "evidence_ref": _content_ref(attestation),
        }
    return {
        "generator": "scripts/generate_provider_fixture_attestations.py",
        "harness": "canonical-adapter-v1",
        "providers": providers,
        "schema_version": 1,
    }


def load_attestation_document() -> dict[str, object]:
    return json.loads(attestation_artifact_path().read_bytes())


def _encoded(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = generate_attestation_document()
    if args.check:
        existing = load_attestation_document()
        if generated != existing:
            expected = hashlib.sha256(_encoded(generated).encode()).hexdigest()
            actual = hashlib.sha256(_encoded(existing).encode()).hexdigest()
            raise SystemExit(
                "provider fixture attestations are stale "
                f"(expected {expected}, found {actual})"
            )
        return
    attestation_artifact_path().write_text(_encoded(generated))


if __name__ == "__main__":
    main()
