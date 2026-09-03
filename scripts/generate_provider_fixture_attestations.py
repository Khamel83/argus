#!/usr/bin/env python3
"""Generate or verify hermetic canonical-adapter fixture attestations."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from argus.models import ProviderName, is_adapter_provider
from argus.providers.fixture_attestation import (
    _adapter_search_hash,
    _content_ref,
    _golden_contract_path,
    _manifest_path,
    _sha256_file,
    _shared_dependency_files,
    _shared_dependency_hash,
    attestation_artifact_path,
    default_release_revision,
)
from argus.providers.fixture_harness import run_fixture_cases
from argus.providers.fixture_golden_contracts import GOLDEN_PROVIDER_CONTRACTS
from argus.providers.fixture_registry import canonical_adapter


def generate_attestation_document(
    release_revision: str | None = None,
) -> dict[str, object]:
    release_revision = release_revision or default_release_revision()
    manifest_path = _manifest_path()
    manifest = json.loads(manifest_path.read_bytes())
    providers = {}
    for provider in ProviderName:
        if not is_adapter_provider(provider):
            continue
        module_name, class_name, module, provider_class = canonical_adapter(provider)
        manifest_entry = manifest["providers"][provider.value]
        # Registration metadata is part of the provider manifest, but it is
        # not part of the adapter request/response contract hashed into an
        # attestation.  Keep the legacy contract comparison exact for the
        # five fixture fields while allowing the startup registry to consume
        # the adjacent no-spend profile.
        contract = {
            key: manifest_entry[key]
            for key in (
                "contract_version",
                "request_contract",
                "response_contract",
                "error_category",
                "error_http_status",
            )
        }
        golden = GOLDEN_PROVIDER_CONTRACTS[provider]
        declared_contract = {
            "contract_version": golden["provider_contract_version"],
            "request_contract": golden["request_contract"],
            "response_contract": golden["response_contract"],
            "error_category": golden["expected"]["error"]["failure"],
            "error_http_status": golden["expected"]["error"][
                "failure_http_status"
            ],
        }
        if contract != declared_contract:
            raise ValueError(
                f"{provider.value} manifest disagrees with golden contract"
            )
        attestation = {
            "provider": provider.value,
            "release": release_revision,
            "adapter_module": module_name,
            "adapter_class": class_name,
            "adapter_code_sha256": _sha256_file(Path(module.__file__ or "")),
            "adapter_identity_sha256": _adapter_search_hash(provider_class),
            "shared_adapter_sha256": _shared_dependency_hash(),
            "shared_dependency_files": list(_shared_dependency_files()),
            "fixture_manifest_sha256": _sha256_file(manifest_path),
            "golden_contract_sha256": _sha256_file(_golden_contract_path()),
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
        "release_revision": release_revision,
        "schema_version": 1,
    }


def load_attestation_document() -> dict[str, object]:
    return json.loads(attestation_artifact_path().read_bytes())


def _encoded(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--release-revision",
        default=default_release_revision(),
    )
    args = parser.parse_args()
    generated = generate_attestation_document(args.release_revision)
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
