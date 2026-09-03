"""Authoritative, no-spend provider registration contract tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from argus.models import ProviderName, is_adapter_provider


def _registration_spec(provider: ProviderName):
    from argus.broker.readiness import ProviderRegistrationSpec
    from argus.providers.fixture_attestation import load_fixture_attestation

    contract = "2026-07-27-v1"
    fixture_ref, attestation = load_fixture_attestation(
        provider,
        release_revision="argus-1.6.4",
        provider_contract=contract,
    )
    paid = provider not in {
        ProviderName.SEARXNG,
        ProviderName.DUCKDUCKGO,
        ProviderName.YAHOO,
        ProviderName.GITHUB,
        ProviderName.WOLFRAM,
    }
    return ProviderRegistrationSpec(
        provider=provider,
        enabled=True,
        configuration_fingerprint=f"config-{provider.value}-v1",
        credential_version_fingerprint=(
            "credential-v1"
            if paid or provider is ProviderName.WOLFRAM
            else "not-applicable-credential"
        ),
        account_fingerprint=(
            f"account-{provider.value}-v1" if paid else "not-applicable-account"
        ),
        budget_limit=10.0 if paid else None,
        durable_spend_repository=True,
        release_revision="argus-1.6.4",
        contract_version=contract,
        fixture_contract=contract,
        fixture_evidence_ref=fixture_ref,
        fixture_attestation=attestation,
    )


def test_fixture_manifest_declares_registration_metadata_for_all_adapters():
    manifest = json.loads(
        Path("argus/providers/fixture_contracts.json").read_text(encoding="utf-8")
    )
    providers = {
        provider.value for provider in ProviderName if is_adapter_provider(provider)
    }

    assert set(manifest["providers"]) == providers
    for provider in providers:
        entry = manifest["providers"][provider]
        registration = entry["registration"]
        assert registration["fixture_contract"] == entry["contract_version"]
        assert registration["billing_class"] in {"free", "monthly", "one_time"}
        assert registration["extraction_capability"] in {
            "search_only",
            "search_and_extract",
            "computed_answer",
        }


@pytest.mark.parametrize(
    "provider",
    tuple(provider for provider in ProviderName if is_adapter_provider(provider)),
)
def test_every_adapter_registration_persists_explicit_profile(tmp_path, provider):
    from argus.broker.readiness import ProviderReadinessService
    from argus.persistence.readiness import create_readiness_repository

    repository = create_readiness_repository(
        f"sqlite:///{tmp_path / f'{provider.value}.db'}"
    )
    service = ProviderReadinessService(repository=repository)
    spec = _registration_spec(provider)

    class ExplodingAvailabilityAdapter:
        def is_available(self):
            raise AssertionError("registration must not call adapter availability")

    decision = service.register_provider(
        spec,
        adapter=ExplodingAvailabilityAdapter(),
    )
    payload = repository.get_registration(provider)

    assert decision.registered is True
    assert payload is not None
    assert payload["billing_class"] == spec.billing_class
    assert payload["extraction_capability"] == spec.extraction_capability
    assert payload["configuration_fingerprint"] == spec.configuration_fingerprint
    assert payload["fixture_contract"] == spec.fixture_contract
    assert payload["fixture_contract_version"] == spec.fixture_contract
    if spec.billing_class == "free":
        assert payload["account_fingerprint"] == "not-applicable-account"
    else:
        assert payload["account_fingerprint"] == spec.account_fingerprint


def test_registration_profile_aliases_are_normalized_and_bounded():
    from argus.broker.readiness import ProviderRegistrationSpec

    spec = _registration_spec(ProviderName.BRAVE)
    alias_spec = replace(
        spec,
        billing_class="monthly_recurring",
        extraction_capability="search",
    )
    assert alias_spec.billing_class == "monthly"
    assert alias_spec.fixture_contract_version == "2026-07-27-v1"

    with pytest.raises(ValueError, match="billing_class"):
        ProviderRegistrationSpec(
            provider=ProviderName.BRAVE,
            enabled=True,
            configuration_fingerprint="config-v1",
            credential_version_fingerprint="credential-v1",
            account_fingerprint="account-v1",
            budget_limit=10.0,
            durable_spend_repository=True,
            release_revision="argus-1.6.4",
            contract_version="2026-07-27-v1",
            fixture_evidence_ref="fixture-v1",
            billing_class="free",
        )


def test_runtime_registry_persists_all_canonical_profiles_without_availability(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from argus.broker.readiness import (
        ExecutableProviderRegistry,
        ProviderReadinessService,
    )
    from argus.config import ArgusConfig
    from argus.persistence.readiness import create_readiness_repository
    from argus.providers.fixture_registry import canonical_adapter
    from argus.providers.searxng import SearXNGProvider

    monkeypatch.setenv("ARGUS_RELEASE_REVISION", "argus-1.6.4")
    config = ArgusConfig()
    adapters = {}
    for provider in ProviderName:
        if not is_adapter_provider(provider):
            continue
        provider_config = getattr(config, provider.value)
        if provider is ProviderName.SEARXNG:
            provider_config = replace(provider_config, enabled=True)
            adapters[provider] = SearXNGProvider(provider_config)
        else:
            paid = provider not in {
                ProviderName.DUCKDUCKGO,
                ProviderName.YAHOO,
                ProviderName.GITHUB,
                ProviderName.WOLFRAM,
            }
            provider_config = replace(
                provider_config,
                enabled=True,
                api_key=(
                    "fixture-key" if paid or provider is ProviderName.WOLFRAM else ""
                ),
                monthly_budget_usd=(
                    10.0 if paid else provider_config.monthly_budget_usd
                ),
                credential_version_fingerprint=(
                    "credential-v1" if paid or provider is ProviderName.WOLFRAM else ""
                ),
                account_fingerprint=("account-v1" if paid else ""),
            )
            config = replace(config, **{provider.value: provider_config})
            _, _, _, provider_class = canonical_adapter(provider)
            adapters[provider] = provider_class(provider_config)

    # SearXNG is not a ProviderConfig, so update it after constructing the
    # full ArgusConfig replacement above.
    config = replace(
        config,
        searxng=replace(config.searxng, enabled=True),
    )

    registry = ExecutableProviderRegistry.from_runtime(
        config=config,
        providers=adapters,
        durable_spend_repository=True,
    )
    assert {spec.provider for spec in registry.specs} == {
        provider for provider in ProviderName if is_adapter_provider(provider)
    }
    assert all(
        spec.billing_class
        and spec.extraction_capability
        and spec.configuration_fingerprint
        and spec.fixture_contract
        for spec in registry.specs
    )

    service = ProviderReadinessService(
        repository=create_readiness_repository(
            f"sqlite:///{tmp_path / 'runtime-registry.db'}"
        )
    )
    registry.persist(service, adapters)
    assert all(service.registration(provider).registered for provider in adapters)
