"""The sole provider-readiness semantic and execution decision authority."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Mapping
from pathlib import Path

from argus.broker.budgets import PROVIDER_TIERS
from argus.models import ProviderName

UTC = timezone.utc
MAX_EVIDENCE_RECEIPTS = 32
MAX_EXECUTABLE_SCOPES = 32
EXECUTABLE_REQUEST_CLASSES = ("discovery", "research", "recovery", "grounding")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UNSAFE_REASON = re.compile(
    r"(?i)(?:https?://|authorization|cookie|password|secret|token|[?&][^ ]+=)"
)
_DIMENSIONS = {
    "registration": {"registered", "not_registered", "unknown"},
    "configuration": {
        "configured", "disabled_by_config", "missing_credential",
        "missing_account_binding", "missing_budget", "missing_spend_repository",
        "evidence_overflow", "unknown",
    },
    "reachability": {"unknown", "reachable", "unreachable"},
    "compatibility": {"unknown", "compatible", "incompatible"},
    "usability": {"unknown", "usable", "empty", "unusable"},
    "cooldown": {"clear", "active", "half_open_claimed", "unknown"},
    "spend": {
        "not_applicable", "unknown", "available", "low", "exhausted",
        "uncertain", "policy_denied",
    },
}
_ISSUE_ORDER = (
    "disabled_by_config", "missing_credential", "missing_account_binding",
    "missing_budget", "missing_spend_repository",
    "incompatible_fixture_release", "evidence_overflow",
)
_NO_SPEND_ACCOUNT_ALLOWLIST = {
    ("brave", "2026-07-27", "brave-account-v1"),
}


def _identifier(value: str | None, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required for exact evidence scope")
    if not isinstance(value, str) or not _SAFE.fullmatch(value):
        raise ValueError(f"{name} must be an opaque bounded identifier")
    return value


def _reason(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str) or not value.strip() or len(value) > 160
        or _UNSAFE_REASON.search(value)
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("safe_reason must be bounded non-sensitive text")
    return " ".join(value.split())


def provider_catalog() -> tuple[ProviderName, ...]:
    return tuple(provider for provider in ProviderName if provider is not ProviderName.CACHE)


@dataclass(frozen=True, slots=True)
class ReadinessScope:
    """Exact evidence identity. Sentinel values are identities, never wildcards."""

    egress: str = "local"
    machine: str = "unknown-machine"
    request_class: str = "discovery"
    release_revision: str = "unknown-release"
    contract_version: str = "unknown-contract"
    configuration_fingerprint: str = "unknown-config"
    credential_version_fingerprint: str = "not-applicable-credential"
    account_fingerprint: str = "not-applicable-account"

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, _identifier(getattr(self, name), name))

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    provider: ProviderName
    dimension: str
    state: str
    source: str
    scope: ReadinessScope
    observed_at: datetime
    ttl_seconds: int | None
    evidence_ref: str | None = None
    safe_reason: str | None = None
    protected: bool = False

    def __post_init__(self) -> None:
        if self.provider is ProviderName.CACHE:
            raise ValueError("cache is not a provider-readiness subject")
        if self.state not in _DIMENSIONS.get(self.dimension, set()):
            raise ValueError("observation dimension/state is outside the closed taxonomy")
        if self.ttl_seconds is not None and (
            type(self.ttl_seconds) is not int
            or not 1 <= self.ttl_seconds <= 31_536_000
        ):
            raise ValueError("observation TTL must be a bounded positive integer")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone aware")
        _identifier(self.source, "source")
        if self.evidence_ref is not None:
            _identifier(self.evidence_ref, "evidence_ref")
        object.__setattr__(self, "safe_reason", _reason(self.safe_reason))


@dataclass(frozen=True, slots=True)
class ConfigurationReadiness:
    issues: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return not self.issues


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    code: str
    reason: str
    contributing_dimensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.code not in {
            "eligible", "policy_skipped", "unavailable", "cooldown",
            "spend_blocked", "compatibility_unproven",
        }:
            raise ValueError("execution decision is outside the closed taxonomy")


@dataclass(frozen=True, slots=True)
class ProviderReadinessSnapshot:
    provider: ProviderName
    catalog_status: str
    registration: str
    configuration: ConfigurationReadiness
    reachability: str
    compatibility: str
    usability: str
    cooldown: str
    spend: str
    healthy: bool
    execution_decision: ExecutionDecision
    observed_at: datetime
    valid_until: datetime | None
    evidence_receipts: tuple[str, ...]
    generation: int
    protected_evidence_count: int = 0
    scope_fingerprint: str = ""

    def as_dict(self) -> dict[str, object]:
        state = (
            "healthy" if self.healthy else
            "disabled" if self.registration != "registered"
            or not self.configuration.configured else
            "unready" if self.execution_decision.code in {
                "unavailable", "cooldown", "spend_blocked"
            } else "degraded"
        )
        return {
            "provider": self.provider.value,
            "catalog_status": self.catalog_status,
            "registration": self.registration,
            "configuration": {
                "configured": self.configuration.configured,
                "issues": list(self.configuration.issues),
            },
            "reachability": self.reachability,
            "compatibility": self.compatibility,
            "usability": self.usability,
            "cooldown": self.cooldown,
            "spend": self.spend,
            "healthy": self.healthy,
            "state": state,
            "execution_decision": {
                "code": self.execution_decision.code,
                "reason": self.execution_decision.reason,
                "contributing_dimensions": list(
                    self.execution_decision.contributing_dimensions
                ),
            },
            "observed_at": self.observed_at.isoformat(),
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "evidence_receipts": list(self.evidence_receipts),
            "generation": self.generation,
            "protected_evidence_count": self.protected_evidence_count,
            "scope_fingerprint": self.scope_fingerprint,
            "authority": "provider_readiness",
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ProviderReadinessSnapshot":
        configuration = payload["configuration"]
        decision = payload["execution_decision"]
        assert isinstance(configuration, Mapping) and isinstance(decision, Mapping)
        return cls(
            provider=ProviderName(str(payload["provider"])),
            catalog_status=str(payload["catalog_status"]),
            registration=str(payload["registration"]),
            configuration=ConfigurationReadiness(tuple(configuration["issues"])),
            reachability=str(payload["reachability"]),
            compatibility=str(payload["compatibility"]),
            usability=str(payload["usability"]),
            cooldown=str(payload["cooldown"]),
            spend=str(payload["spend"]),
            healthy=bool(payload["healthy"]),
            execution_decision=ExecutionDecision(
                str(decision["code"]), str(decision["reason"]),
                tuple(decision["contributing_dimensions"]),
            ),
            observed_at=datetime.fromisoformat(str(payload["observed_at"])),
            valid_until=(
                datetime.fromisoformat(str(payload["valid_until"]))
                if payload.get("valid_until") else None
            ),
            evidence_receipts=tuple(payload["evidence_receipts"]),
            generation=int(payload["generation"]),
            protected_evidence_count=int(payload.get("protected_evidence_count", 0)),
            scope_fingerprint=str(payload["scope_fingerprint"]),
        )

    def as_legacy_status(self) -> dict[str, object]:
        effective = (
            "healthy" if self.healthy else
            "disabled_by_config" if self.registration != "registered"
            or not self.configuration.configured else
            "budget_exhausted" if self.spend in {
                "exhausted", "uncertain", "policy_denied"
            } else
            "temporarily_disabled_after_failures" if self.cooldown != "clear"
            else "degraded"
        )
        return {
            "provider": self.provider.value,
            "config_status": "enabled" if self.configuration.configured
            else "disabled_by_config",
            "health": self.as_dict(),
            "budget_remaining": None,
            "effective_status": effective,
            "authority": "provider_readiness",
        }


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    provider: ProviderName
    tier: int
    plan_providers: tuple[ProviderName, ...]
    free_only: bool
    caller_tier_cap: int | None
    scope: ReadinessScope | None = None
    plan_id: str = "legacy-plan"
    caller_identity: str = "legacy-caller"
    idempotency_key: str = "legacy-request"
    egress: str = "local"
    request_class: str = "discovery"
    release_revision: str = "unknown-release"
    contract_version: str = "unknown-contract"

    def __post_init__(self):
        if self.scope is None:
            object.__setattr__(self, "scope", ReadinessScope(
                egress=self.egress, request_class=self.request_class,
                release_revision=self.release_revision,
                contract_version=self.contract_version,
            ))

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider, "tier": self.tier,
            "plan_providers": self.plan_providers, "free_only": self.free_only,
            "caller_tier_cap": self.caller_tier_cap, "scope": self.scope,
            "plan_id": self.plan_id, "caller_identity": self.caller_identity,
            "idempotency_key": self.idempotency_key, "egress": self.egress,
            "request_class": self.request_class,
            "release_revision": self.release_revision,
            "contract_version": self.contract_version,
        }


@dataclass(frozen=True, slots=True)
class ExecutionAuthorization:
    allowed: bool
    decision: ExecutionDecision
    provider: ProviderName
    scope: ReadinessScope
    owner: str = ""
    scope_key: str = ""
    fencing_token: int = 0
    attempt_id: str = ""


@dataclass(frozen=True, slots=True)
class ProbeAuthorization:
    workflow: str
    provider: ProviderName
    allowlist_version: str | None = None
    endpoint_contract: str | None = None
    named_quota: str | None = None
    idempotency_key: str | None = None
    durable_receipt: str | None = None
    conservative_charge: float | None = None


@dataclass(frozen=True, slots=True)
class ProbeDecision:
    allowed: bool
    reason: str
    authorization_id: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class RegistrationInputs:
    enabled: bool
    credential_present: bool
    account_fingerprint: str | None
    budget_limit: float | None
    durable_spend_repository: bool
    compatible_fixture_release: bool


@dataclass(frozen=True, slots=True)
class ProviderRegistrationSpec:
    provider: ProviderName
    enabled: bool
    configuration_fingerprint: str
    credential_version_fingerprint: str | None
    account_fingerprint: str | None
    budget_limit: float | None
    durable_spend_repository: bool
    release_revision: str | None
    contract_version: str | None
    fixture_evidence_ref: str | None
    fixture_attestation: Mapping[str, object] | None = None
    budget_reset_at: datetime | None = None
    budget_period_started_at: datetime | None = None
    budget_next_reset_at: datetime | None = None
    machine: str = "test-machine"
    egress: str = "local"
    request_class: str = "discovery"


@dataclass(frozen=True, slots=True)
class RegistrationDecision:
    registered: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutableProviderRegistry:
    """Immutable startup registry containing every registration predicate."""

    specs: tuple[ProviderRegistrationSpec, ...]

    @classmethod
    def from_runtime(cls, *, config, providers, durable_spend_repository: bool):
        from argus import __version__
        from argus.providers.fixture_attestation import (
            load_fixture_attestation,
        )

        fixture_path = Path(__file__).parents[1] / "providers" / "fixture_contracts.json"
        fixture = json.loads(fixture_path.read_bytes())
        keyless = {
            ProviderName.SEARXNG, ProviderName.DUCKDUCKGO,
            ProviderName.YAHOO, ProviderName.GITHUB,
        }
        specs = []
        for provider in providers:
            provider_config = getattr(config, provider.value)
            tier = PROVIDER_TIERS[provider]
            prefix = provider.value.upper()
            config_payload = {
                "enabled": bool(provider_config.enabled),
                "timeout_seconds": int(provider_config.timeout_seconds),
            }
            if hasattr(provider_config, "base_url"):
                config_payload["base_url"] = str(provider_config.base_url)
            config_ref = "config:" + hashlib.sha256(json.dumps(
                config_payload, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()[:24]
            credential_ref = (
                "not-applicable-credential"
                if provider in keyless
                else os.environ.get(f"ARGUS_{prefix}_CREDENTIAL_VERSION_FINGERPRINT")
            )
            account_ref = (
                "not-applicable-account"
                if tier == 0
                else os.environ.get(f"ARGUS_{prefix}_ACCOUNT_FINGERPRINT")
            )
            contract = fixture["providers"].get(provider.value, {}).get(
                "contract_version"
            )
            adapter = providers[provider]
            release = os.environ.get(
                "ARGUS_RELEASE_REVISION", f"argus-{__version__}"
            )
            try:
                attestation_ref, attestation_payload = (
                    load_fixture_attestation(
                        provider,
                        release_revision=release,
                        provider_contract=str(contract),
                        adapter_module=type(adapter).__module__,
                        adapter_class=type(adapter).__name__,
                    )
                )
            except (ImportError, OSError, ValueError):
                attestation_ref, attestation_payload = None, None
            reset_value = os.environ.get(f"ARGUS_{prefix}_BUDGET_RESET_AT")
            next_reset_at = (
                datetime.fromisoformat(reset_value.replace("Z", "+00:00"))
                if reset_value else None
            )
            period_start_value = os.environ.get(
                f"ARGUS_{prefix}_BUDGET_PERIOD_STARTED_AT"
            )
            period_started_at = (
                datetime.fromisoformat(
                    period_start_value.replace("Z", "+00:00")
                )
                if period_start_value else None
            )
            if next_reset_at is not None and next_reset_at.tzinfo is None:
                raise ValueError(
                    f"ARGUS_{prefix}_BUDGET_RESET_AT must include a timezone"
                )
            if period_started_at is not None and period_started_at.tzinfo is None:
                raise ValueError(
                    f"ARGUS_{prefix}_BUDGET_PERIOD_STARTED_AT must include a timezone"
                )
            specs.append(ProviderRegistrationSpec(
                provider=provider,
                enabled=bool(provider_config.enabled),
                configuration_fingerprint=config_ref,
                credential_version_fingerprint=credential_ref,
                account_fingerprint=account_ref,
                budget_limit=(
                    float(provider_config.monthly_budget_usd) if tier > 0 else None
                ),
                durable_spend_repository=durable_spend_repository,
                release_revision=release,
                contract_version=contract,
                fixture_evidence_ref=attestation_ref,
                fixture_attestation=attestation_payload,
                budget_next_reset_at=next_reset_at,
                budget_period_started_at=period_started_at,
                machine=config.node.machine_name or "argus-primary",
            ))
        return cls(tuple(specs))

    def persist(self, service: "ProviderReadinessService", adapters) -> None:
        service.validate_scope_manifest(
            egresses=tuple(sorted({spec.egress for spec in self.specs})),
            request_classes=EXECUTABLE_REQUEST_CLASSES,
        )
        for spec in self.specs:
            service.register_provider(spec, adapter=adapters.get(spec.provider))


class ProviderReadinessService:
    """Deterministic fold over exact, durable evidence scopes."""

    def __init__(
        self, *, repository, monotonic=time.monotonic, legacy_health=None,
        legacy_budgets=None, legacy_reachability=None, legacy_providers=None,
    ):
        self.repository = repository
        self._monotonic = monotonic
        self._legacy_health = legacy_health
        self._legacy_budgets = legacy_budgets
        self._legacy_reachability = legacy_reachability
        self._legacy_providers = legacy_providers or {}

    @classmethod
    def from_legacy_observation_sources(
        cls, *, providers, health_tracker, budget_tracker, reachability,
        spend_repository=None, monotonic=time.monotonic,
    ):
        factory = getattr(spend_repository, "session_factory", None)
        if factory is not None and isinstance(getattr(factory, "kw", None), Mapping):
            from argus.persistence.readiness import readiness_repository_from_session_factory
            repository = readiness_repository_from_session_factory(factory)
        else:
            from argus.persistence.readiness import create_readiness_repository
            repository = create_readiness_repository("sqlite:///:memory:")
        service = cls(
            repository=repository, monotonic=monotonic,
            legacy_health=health_tracker, legacy_budgets=budget_tracker,
            legacy_reachability=reachability, legacy_providers=providers,
        )
        # Compatibility construction is explicit and fail-closed. It never
        # asks an adapter to describe its own availability.
        from argus.providers.fixture_attestation import (
            default_release_revision,
            load_fixture_attestation,
        )
        fixture_path = (
            Path(__file__).parents[1] / "providers" / "fixture_contracts.json"
        )
        fixture_contracts = json.loads(fixture_path.read_bytes())["providers"]
        fixture_release = default_release_revision()
        for provider in providers:
            tier = PROVIDER_TIERS[provider]
            reachability_state = reachability.get_all().get(provider, {})
            registered_egress = reachability_state.get("best", "local")
            budget_limit = (
                budget_tracker.get_budget_limit(provider) if tier > 0 else None
            )
            fixture_contract = str(
                fixture_contracts[provider.value]["contract_version"]
            )
            fixture_ref, fixture_attestation = load_fixture_attestation(
                provider,
                release_revision=fixture_release,
                provider_contract=fixture_contract,
            )
            service.register_provider(ProviderRegistrationSpec(
                provider=provider, enabled=True,
                configuration_fingerprint="legacy-config-v1",
                credential_version_fingerprint="legacy-credential-v1",
                account_fingerprint=(
                    "not-applicable-account" if tier == 0 else "legacy-account-v1"
                ),
                budget_limit=budget_limit,
                durable_spend_repository=True,
                release_revision=fixture_release,
                contract_version=fixture_contract,
                fixture_evidence_ref=fixture_ref,
                fixture_attestation=fixture_attestation,
                machine="legacy-machine",
                egress=registered_egress or "local",
            ))
            for request_class in EXECUTABLE_REQUEST_CLASSES:
                scope = service.execution_scope(
                    provider,
                    egress=registered_egress or "local",
                    request_class=request_class,
                )
                if registered_egress is None:
                    service.record_observation(ProviderObservation(
                        provider=provider,
                        dimension="reachability",
                        state="unreachable",
                        source="legacy_observed_failure",
                        scope=scope,
                        observed_at=repository.authority_now(),
                        ttl_seconds=60,
                    ))
                if health_tracker is not None:
                    cooldown, _ = health_tracker.normalized_observation(provider)
                    service.record_observation(ProviderObservation(
                        provider=provider, dimension="cooldown", state=cooldown,
                        source="health_observation", scope=scope,
                        observed_at=repository.authority_now(), ttl_seconds=60,
                    ))
        return service

    def evaluate_registration(
        self, provider: ProviderName, inputs: RegistrationInputs,
    ) -> RegistrationDecision:
        issues = []
        if not inputs.enabled:
            issues.append("disabled_by_config")
        if not inputs.credential_present:
            issues.append("missing_credential")
        if PROVIDER_TIERS[provider] > 0:
            if not inputs.account_fingerprint:
                issues.append("missing_account_binding")
            if (
                inputs.budget_limit is None
                or not math.isfinite(float(inputs.budget_limit))
                or inputs.budget_limit <= 0
            ):
                issues.append("missing_budget")
            if not inputs.durable_spend_repository:
                issues.append("missing_spend_repository")
        if not inputs.compatible_fixture_release:
            issues.append("incompatible_fixture_release")
        ordered = tuple(issue for issue in _ISSUE_ORDER if issue in issues)
        return RegistrationDecision(not ordered, ordered)

    def register_provider(
        self, spec: ProviderRegistrationSpec, *, adapter=None,
    ) -> RegistrationDecision:
        del adapter  # adapters are executable objects, never evidence authorities
        account_spend = self.repository.spend_state(
            spec.provider,
            account_fingerprint=(
                spec.account_fingerprint or "missing-account"
            ),
        )
        now = self.repository.authority_now()
        period_started_at = spec.budget_period_started_at
        next_reset_at = spec.budget_next_reset_at or spec.budget_reset_at
        for name, value in (
            ("budget_period_started_at", period_started_at),
            ("budget_next_reset_at", next_reset_at),
        ):
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone aware")
        if period_started_at is not None and period_started_at > now:
            raise ValueError("budget period start cannot be in the future")
        if (
            period_started_at is not None
            and now - period_started_at > timedelta(days=370)
        ):
            raise ValueError("budget period start is implausibly old")
        if next_reset_at is not None and next_reset_at <= now:
            raise ValueError("budget next reset must be in the future")
        if (
            next_reset_at is not None
            and next_reset_at - now > timedelta(days=370)
        ):
            raise ValueError("budget next reset is implausibly distant")
        if (
            period_started_at is not None
            and next_reset_at is not None
            and period_started_at >= next_reset_at
        ):
            raise ValueError("budget period boundaries are inverted")
        required_attestation_fields = {
            "provider",
            "release",
            "adapter_module",
            "adapter_class",
            "adapter_code_sha256",
            "adapter_identity_sha256",
            "shared_adapter_sha256",
            "shared_dependency_files",
            "fixture_manifest_sha256",
            "fixture_case_digest",
            "request_contract",
            "response_contract",
            "provider_contract",
        }
        from argus.providers.fixture_attestation import (
            verify_fixture_attestation,
        )
        compatibility = bool(
            spec.release_revision and spec.contract_version
            and spec.fixture_evidence_ref
            and spec.fixture_evidence_ref.startswith("attestation:")
            and spec.fixture_attestation
            and required_attestation_fields <= set(spec.fixture_attestation)
            and spec.fixture_attestation.get("release") == spec.release_revision
            and (
                spec.fixture_attestation.get("provider_contract")
                == spec.contract_version
            )
            and verify_fixture_attestation(
                spec.fixture_attestation,
                evidence_ref=spec.fixture_evidence_ref,
            )
        )
        decision = self.evaluate_registration(spec.provider, RegistrationInputs(
            enabled=spec.enabled,
            credential_present=bool(spec.credential_version_fingerprint),
            account_fingerprint=spec.account_fingerprint,
            budget_limit=spec.budget_limit,
            durable_spend_repository=spec.durable_spend_repository,
            compatible_fixture_release=compatibility,
        ))
        previous_registration = self.repository.get_registration(spec.provider) or {}
        authority_changed = (
            previous_registration.get("fixture_evidence_ref")
            != spec.fixture_evidence_ref
            or previous_registration.get("registered") != decision.registered
        )
        scopes = tuple(ReadinessScope(
            egress=spec.egress, machine=spec.machine,
            request_class=request_class,
            release_revision=spec.release_revision or "missing-release",
            contract_version=spec.contract_version or "missing-contract",
            configuration_fingerprint=spec.configuration_fingerprint,
            credential_version_fingerprint=(
                spec.credential_version_fingerprint or "missing-credential"
            ),
            account_fingerprint=(
                spec.account_fingerprint or "missing-account"
            ),
        ) for request_class in EXECUTABLE_REQUEST_CLASSES)
        scope = next(
            item for item in scopes if item.request_class == spec.request_class
        )
        self.repository.put_registration(spec.provider, {
            "registered": decision.registered,
            "issues": list(decision.issues),
            "budget_limit": spec.budget_limit,
            "scope": scope.as_dict(),
            "scopes": [item.as_dict() for item in scopes],
            "fixture_evidence_ref": spec.fixture_evidence_ref,
            "fixture_attestation": dict(spec.fixture_attestation or {}),
            "budget_period_started_at": (
                period_started_at.astimezone(UTC).isoformat()
                if period_started_at else None
            ),
            "budget_next_reset_at": (
                next_reset_at.astimezone(UTC).isoformat()
                if next_reset_at else None
            ),
        })
        configuration_issue = next(
            (
                issue for issue in decision.issues
                if issue in _DIMENSIONS["configuration"]
            ),
            None,
        )
        observations = (
            ("registration", "registered" if decision.registered else "not_registered",
             "registry", None),
            ("configuration", configuration_issue or "configured",
             "registry", None),
            ("compatibility", "compatible" if compatibility else "unknown",
             "fixture_contract", spec.fixture_evidence_ref),
            ("reachability", "unknown", "topology_registry", None),
            ("usability", "unknown", "fixture_contract", None),
            ("cooldown", "clear", "registry", None),
            (
                "spend",
                "not_applicable" if PROVIDER_TIERS[spec.provider] == 0
                else account_spend
                if account_spend in {"exhausted", "uncertain"}
                else "available" if decision.registered else "unknown",
                "account_spend_authority"
                if account_spend in {"exhausted", "uncertain"}
                else "registry",
                None,
            ),
        )
        for exact_scope in scopes:
            for dimension, state, source, receipt in observations:
                payload = self.repository.record_and_materialize(
                    ProviderObservation(
                        provider=spec.provider, dimension=dimension, state=state,
                        source=source, scope=exact_scope, observed_at=now,
                        ttl_seconds=None, evidence_ref=receipt,
                    ),
                    lambda rows, folded_at, generation, exact_scope=exact_scope: (
                        self._fold(
                            spec.provider, exact_scope, rows, folded_at, generation
                        ).as_dict()
                    ),
                    replace_existing=(
                        authority_changed
                        and dimension in {
                            "registration", "configuration", "compatibility"
                        }
                    ),
                )
                ProviderReadinessSnapshot.from_dict(payload)
        return decision

    def registration(self, provider: ProviderName) -> RegistrationDecision:
        payload = self.repository.get_registration(provider) or {}
        issues = tuple(payload.get("issues", ("not_registered",)))
        return RegistrationDecision(bool(payload.get("registered")), issues)

    def record_observation(self, observation: ProviderObservation):
        payload = self.repository.record_and_materialize(
            observation,
            lambda rows, now, generation: self._fold(
                observation.provider, observation.scope, rows, now, generation
            ).as_dict(),
        )
        return ProviderReadinessSnapshot.from_dict(payload)

    def snapshot_for_scope(
        self, provider: ProviderName, scope: ReadinessScope,
        execution_context: ExecutionContext | None = None,
    ) -> ProviderReadinessSnapshot:
        payload = self.repository.read_snapshot(provider, scope.fingerprint())
        if payload is None or payload.get("compatibility_write"):
            payload = self.repository.refresh_expired(
                provider, scope.fingerprint(),
                lambda rows, now, generation: self._fold(
                    provider, scope, rows, now, generation, execution_context
                ).as_dict(),
            )
        snapshot = ProviderReadinessSnapshot.from_dict(payload)
        if snapshot.valid_until and self.repository.authority_now() >= snapshot.valid_until:
            payload = self.repository.refresh_expired(
                provider, scope.fingerprint(),
                lambda rows, now, generation: self._fold(
                    provider, scope, rows, now, generation, execution_context
                ).as_dict(),
            )
            snapshot = ProviderReadinessSnapshot.from_dict(payload)
        if execution_context is not None:
            decision = self._decision_from_snapshot(snapshot, execution_context)
            return replace(snapshot, execution_decision=decision)
        return snapshot

    def _active_scope(self, provider: ProviderName, **overrides) -> ReadinessScope:
        registration = self.repository.get_registration(provider) or {}
        request_class = overrides.get("request_class")
        candidates = registration.get("scopes") or ()
        selected = next(
            (
                item for item in candidates
                if item.get("request_class") == request_class
            ),
            registration.get("scope") or {},
        )
        data = dict(selected)
        if not data:
            rows = self.repository.observations(provider)
            if rows:
                data = dict(rows[-1].scope)
        defaults = ReadinessScope().as_dict()
        defaults.update(data)
        for name, value in overrides.items():
            if value is not None:
                defaults[name] = value
        return ReadinessScope(**defaults)

    def snapshot(
        self, provider: ProviderName, *, egress="local",
        request_class="discovery", release_revision=None, contract_version=None,
        execution_context=None,
    ):
        scope = self._active_scope(
            provider, egress=egress, request_class=request_class,
            release_revision=release_revision, contract_version=contract_version,
        )
        return self.snapshot_for_scope(provider, scope, execution_context)

    render_snapshot = snapshot

    def execution_decision(self, context: ExecutionContext) -> ExecutionDecision:
        assert context.scope is not None
        if context.scope.configuration_fingerprint == "unknown-config":
            scope = self._active_scope(
                context.provider,
                egress=context.egress,
                request_class=context.request_class,
                release_revision=(
                    None if context.release_revision == "unknown-release"
                    else context.release_revision
                ),
                contract_version=(
                    None if context.contract_version == "unknown-contract"
                    else context.contract_version
                ),
            )
            context = replace(context, scope=scope)
        return self.snapshot_for_scope(
            context.provider, context.scope, context
        ).execution_decision

    def authorize_execution(
        self, context: ExecutionContext, *, owner: str,
        conservative_charge: float, execution_timeout_seconds: int,
        _probe_authorization: ProbeAuthorization | None = None,
    ) -> ExecutionAuthorization:
        assert context.scope is not None
        result = self.repository.authorize_execution(
            context=context, owner=owner,
            conservative_charge=conservative_charge,
            execution_timeout_seconds=execution_timeout_seconds,
            decide=self._decision_from_payload,
            probe_authorization=_probe_authorization,
        )
        return ExecutionAuthorization(
            allowed=result["allowed"], decision=result["decision"],
            provider=context.provider, scope=context.scope,
            owner=result.get("owner", owner),
            scope_key=result.get("scope_key", ""),
            fencing_token=result.get("fencing_token", 0),
            attempt_id=result.get("attempt_id", ""),
        )

    def complete_execution(
        self, authorization: ExecutionAuthorization, *, failure,
        actual_charge: float | None, charge_known: bool, evidence_ref: str,
        termination_known: bool = True,
        probe_idempotency_key: str | None = None,
    ) -> None:
        if not authorization.allowed or not authorization.fencing_token:
            raise ValueError("durable execution authorization is required")
        category = getattr(getattr(failure, "category", None), "value", None)
        from argus.persistence.readiness import StaleFencingToken
        try:
            self.repository.settle_execution(
                authorization=authorization, category=category or "success",
                actual_charge=actual_charge, charge_known=charge_known,
                termination_known=termination_known,
                evidence_ref=evidence_ref,
                probe_idempotency_key=probe_idempotency_key,
                fold=lambda rows, now, generation: self._fold(
                    authorization.provider, authorization.scope, rows, now, generation
                ).as_dict(),
            )
        except StaleFencingToken:
            self.repository.append_stale_charge_evidence(
                authorization=authorization, actual_charge=actual_charge,
                charge_known=charge_known, evidence_ref=evidence_ref,
            )
            raise

    def _fold(
        self, provider, scope, rows, now, generation, context=None,
    ) -> ProviderReadinessSnapshot:
        current = {
            row.dimension: row for row in rows
            if row.expires_at is None or now < row.expires_at
        }
        registration = current.get("registration")
        config = current.get("configuration")
        reach = current.get("reachability")
        compatibility = current.get("compatibility")
        usability = current.get("usability")
        cooldown = current.get("cooldown")
        spend = current.get("spend")
        registration_state = registration.state if registration else "not_registered"
        config_state = config.state if config else "unknown"
        configuration = ConfigurationReadiness(
            () if config_state == "configured" else (config_state,)
        )
        spend_state = (
            spend.state if spend else
            "not_applicable" if PROVIDER_TIERS.get(provider, 0) == 0 else "unknown"
        )
        receipts = []
        protected = set()
        for row in sorted(current.values(), key=lambda item: item.ingested_at, reverse=True):
            if row.evidence_ref and row.evidence_ref not in receipts:
                receipts.append(row.evidence_ref)
            if row.evidence_ref and row.protected:
                protected.add(row.evidence_ref)
        receipts = receipts[:MAX_EVIDENCE_RECEIPTS]
        provisional = {
            "registration": registration_state,
            "configuration": configuration,
            "reachability": reach.state if reach else "unknown",
            "compatibility": compatibility.state if compatibility else "unknown",
            "usability": usability.state if usability else "unknown",
            "cooldown": cooldown.state if cooldown else "unknown",
            "spend": spend_state,
        }
        decision = self._decision(**provisional, context=context)
        healthy = (
            registration_state == "registered" and configuration.configured
            and provisional["compatibility"] == "compatible"
            and provisional["reachability"] == "reachable"
            and provisional["usability"] in {"usable", "empty"}
            and provisional["cooldown"] == "clear"
            and spend_state in {"not_applicable", "available", "low"}
        )
        expiries = [
            row.expires_at for row in current.values() if row.expires_at is not None
        ]
        return ProviderReadinessSnapshot(
            provider=provider, catalog_status="supported",
            registration=registration_state, configuration=configuration,
            reachability=provisional["reachability"],
            compatibility=provisional["compatibility"],
            usability=provisional["usability"],
            cooldown=provisional["cooldown"], spend=spend_state,
            healthy=healthy, execution_decision=decision, observed_at=now,
            valid_until=min(expiries) if expiries else None,
            evidence_receipts=tuple(receipts), generation=generation,
            protected_evidence_count=len(protected),
            scope_fingerprint=scope.fingerprint(),
        )

    def _decision_from_payload(self, payload, context):
        if not payload:
            return ExecutionDecision("unavailable", "not_materialized", ("snapshot",))
        snapshot = ProviderReadinessSnapshot.from_dict(payload)
        return self._decision_from_snapshot(snapshot, context)

    def _decision_from_snapshot(self, snapshot, context):
        return self._decision(
            registration=snapshot.registration,
            configuration=snapshot.configuration,
            reachability=snapshot.reachability,
            compatibility=snapshot.compatibility,
            usability=snapshot.usability,
            cooldown=snapshot.cooldown,
            spend=snapshot.spend,
            context=context,
        )

    @staticmethod
    def _decision(
        *, registration, configuration, reachability, compatibility,
        usability, cooldown, spend, context=None,
    ):
        if context is not None:
            if context.caller_tier_cap is not None and context.tier > context.caller_tier_cap:
                return ExecutionDecision("policy_skipped", "caller_tier_cap", ("plan",))
            if context.free_only and context.tier > 0:
                return ExecutionDecision("policy_skipped", "free_only", ("plan",))
            if context.provider not in context.plan_providers:
                return ExecutionDecision("policy_skipped", "provider_not_in_plan", ("plan",))
        if registration != "registered":
            return ExecutionDecision("unavailable", registration, ("registration",))
        if not configuration.configured:
            return ExecutionDecision("unavailable", configuration.issues[0], ("configuration",))
        if compatibility != "compatible":
            return ExecutionDecision(
                "compatibility_unproven", compatibility, ("compatibility",)
            )
        if reachability == "unreachable":
            return ExecutionDecision("unavailable", "egress_unreachable", ("reachability",))
        if usability == "unusable":
            return ExecutionDecision("unavailable", "provider_unusable", ("usability",))
        if cooldown != "clear":
            return ExecutionDecision("cooldown", cooldown, ("cooldown",))
        if spend in {"exhausted", "uncertain", "policy_denied", "unknown"}:
            return ExecutionDecision("spend_blocked", spend, ("spend",))
        return ExecutionDecision("eligible", "ready", ())

    def best_egress(self, provider):
        registration = self.repository.get_registration(provider) or {}
        egress = (registration.get("scope") or {}).get("egress", "local")
        snapshot = self.snapshot(provider, egress=egress)
        return egress if snapshot.reachability != "unreachable" else None

    def execution_scope(
        self, provider: ProviderName, *, egress: str, request_class: str,
    ) -> ReadinessScope:
        return self._active_scope(
            provider, egress=egress, request_class=request_class
        )

    def claim_invocation(self, provider, egress):
        return (None, None)

    def release_invocation(self, claims):
        return None

    def record_legacy_outcome(
        self, provider, *, egress, success, latency_ms, scope,
    ):
        if self._legacy_health is not None:
            (
                self._legacy_health.record_success
                if success else self._legacy_health.record_failure
            )(provider)
        if self._legacy_reachability is not None:
            self._legacy_reachability.update_probe(
                egress, provider, reachable=success, latency_ms=latency_ms,
                source="provider_execution",
            )
        now = self.repository.authority_now()
        self.record_observation(ProviderObservation(
            provider=provider, dimension="reachability",
            state="reachable" if success else "unreachable",
            source="provider_execution", scope=scope, observed_at=now,
            ttl_seconds=60,
        ))
        self.record_observation(ProviderObservation(
            provider=provider, dimension="usability",
            state="usable" if success else "unusable",
            source="provider_execution", scope=scope, observed_at=now,
            ttl_seconds=60,
        ))

    def paid_pacing(self, provider):
        snapshot = self.snapshot(provider)
        if snapshot.spend not in {"exhausted", "uncertain"} and self._legacy_budgets:
            if self._legacy_budgets.is_budget_exhausted(provider):
                return False, "budget exhausted", 0.0, 0.0, 0.0
            if self._legacy_budgets.is_over_pace(provider):
                return False, "over pace, conserving monthly credits", 0.0, 0.0, 0.0
        allowed = snapshot.spend in {"available", "low"}
        return allowed, snapshot.spend, 0.0, 0.0, 0.0

    def budget_limit(self, provider):
        return float((self.repository.get_registration(provider) or {}).get(
            "budget_limit"
        ) or 0.0)

    def record_budget_usage(self, provider, cost):
        if self._legacy_budgets is None:
            return None
        self._legacy_budgets.record_usage(provider, cost)
        return None

    def legacy_health_projection(self, provider):
        return self.snapshot(provider).as_dict()

    def readiness_projection(self, provider: ProviderName) -> dict[str, object]:
        return self.snapshot(provider).as_dict()

    def budget_projection(self, provider: ProviderName) -> dict[str, object]:
        snapshot = self.snapshot(provider)
        limit = self.budget_limit(provider)
        spend = self.repository.provider_spend_projection(
            provider, budget_limit=limit
        )
        return {
            "provider": provider.value,
            "state": snapshot.spend,
            "budget_limit": limit if limit > 0 else None,
            "remaining": spend["remaining"],
            "argus_estimated_charge": spend["argus_estimated_charge"],
            "uncertain_charge": spend["uncertain_charge"],
            "exhausted": snapshot.spend == "exhausted",
            "authority": "provider_readiness",
            "readiness_generation": snapshot.generation,
        }

    def _refresh_legacy_observations(self, provider, egress):
        # Frozen mechanics may feed explicit observations through
        # record_legacy_outcome; they may never overwrite durable readiness.
        del provider, egress

    def authorize_probe(self, provider, probe_kind, authorization=None):
        if probe_kind in {"fixture", "local_component"}:
            return self.run_fixture_probe(provider)
        if probe_kind == "no_spend_account":
            if authorization is None or authorization.provider is not provider:
                return ProbeDecision(False, "versioned_allowlist_required")
            key = (
                provider.value, authorization.allowlist_version,
                authorization.endpoint_contract,
            )
            return ProbeDecision(
                key in _NO_SPEND_ACCOUNT_ALLOWLIST,
                "versioned_no_spend_contract" if key in _NO_SPEND_ACCOUNT_ALLOWLIST
                else "endpoint_not_allowlisted",
            )
        allowed = bool(
            authorization
            and authorization.workflow == "explicit_validation"
            and authorization.provider is provider
            and authorization.idempotency_key
            and authorization.durable_receipt
            and (
                probe_kind == "no_money_quota"
                or (
                    probe_kind == "billable_search"
                    and authorization.conservative_charge is not None
                    and math.isfinite(authorization.conservative_charge)
                    and authorization.conservative_charge > 0
                )
            )
        )
        if not allowed:
            return ProbeDecision(False, "probe_denied")
        if probe_kind == "billable_search":
            scope = self.execution_scope(
                provider, egress=self.best_egress(provider) or "local",
                request_class="discovery",
            )
            execution = self.authorize_execution(
                ExecutionContext(
                    provider=provider,
                    tier=PROVIDER_TIERS[provider],
                    plan_providers=(provider,),
                    free_only=False,
                    caller_tier_cap=PROVIDER_TIERS[provider],
                    scope=scope,
                    plan_id=f"probe:{authorization.idempotency_key}",
                    caller_identity="explicit_validation",
                    idempotency_key=str(authorization.idempotency_key),
                ),
                owner=f"probe:{authorization.idempotency_key}",
                conservative_charge=float(authorization.conservative_charge),
                execution_timeout_seconds=60,
                _probe_authorization=authorization,
            )
            if not execution.allowed:
                return ProbeDecision(False, execution.decision.reason)
            return ProbeDecision(
                True,
                "explicit_probe_authorized",
                self.repository.probe_authorization_id(
                    str(authorization.idempotency_key)
                ),
                execution.attempt_id,
            )
        authorization_id = self.repository.authorize_probe_once(
            provider=provider,
            probe_kind=probe_kind,
            authorization=authorization,
        )
        return ProbeDecision(
            True, "explicit_probe_authorized", authorization_id
        )

    def run_fixture_probe(self, provider: ProviderName) -> ProbeDecision:
        registration = self.repository.get_registration(provider) or {}
        evidence_ref = registration.get("fixture_evidence_ref")
        if not (
            isinstance(evidence_ref, str)
            and evidence_ref.startswith("attestation:")
        ):
            return ProbeDecision(False, "fixture_attestation_missing")
        attestation = registration.get("fixture_attestation") or {}
        if not attestation or not {
            "provider",
            "release",
            "adapter_module",
            "adapter_class",
            "adapter_code_sha256",
            "adapter_identity_sha256",
            "shared_adapter_sha256",
            "shared_dependency_files",
            "fixture_manifest_sha256",
            "fixture_case_digest",
            "request_contract",
            "response_contract",
            "provider_contract",
        } <= set(attestation):
            return ProbeDecision(False, "fixture_attestation_incomplete")
        from argus.providers.fixture_attestation import (
            verify_fixture_attestation,
        )
        if not verify_fixture_attestation(
            attestation,
            evidence_ref=evidence_ref,
        ):
            return ProbeDecision(False, "fixture_contract_failed")
        return ProbeDecision(True, "fixture_cases_verified", evidence_ref)

    @staticmethod
    def validate_scope_manifest(*, egresses, request_classes):
        if (
            len(egresses) > 8 or len(request_classes) > 4
            or len(egresses) * len(request_classes) > MAX_EXECUTABLE_SCOPES
        ):
            raise ValueError("scope manifest exceeds 32 executable scopes")
        if len(set(egresses)) != len(egresses) or len(set(request_classes)) != len(request_classes):
            raise ValueError("scope manifest entries must be unique")
