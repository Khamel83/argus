"""The sole provider-readiness semantic and execution decision authority."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Mapping

from argus.broker.budgets import PROVIDER_TIERS
from argus.models import ProviderName


UTC = timezone.utc
MAX_EVIDENCE_RECEIPTS = 32
MAX_EXECUTABLE_SCOPES = 32
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UNSAFE_REASON = re.compile(
    r"(?i)(?:https?://|authorization|cookie|password|secret|token|[?&][^ ]+=)"
)
_DIMENSION_STATES = {
    "registration": {"registered", "not_registered", "unknown"},
    "configuration": {
        "configured",
        "disabled_by_config",
        "missing_credential",
        "missing_account_binding",
        "missing_budget",
        "missing_spend_repository",
        "unknown",
    },
    "reachability": {"unknown", "reachable", "unreachable"},
    "compatibility": {"unknown", "compatible", "incompatible"},
    "usability": {"unknown", "usable", "empty", "unusable"},
    "cooldown": {"clear", "active", "half_open_claimed", "unknown"},
    "spend": {
        "not_applicable",
        "unknown",
        "available",
        "low",
        "exhausted",
        "uncertain",
        "policy_denied",
    },
}
_CONFIGURATION_ISSUES = (
    "disabled_by_config",
    "missing_credential",
    "missing_account_binding",
    "missing_budget",
    "missing_spend_repository",
    "incompatible_fixture_release",
)
_NO_SPEND_ACCOUNT_ALLOWLIST = {
    ("brave", "2026-07-27", "brave-account-v1"),
}


def _safe_optional(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SAFE_VALUE.fullmatch(value):
        raise ValueError(f"{name} must be an opaque bounded identifier")
    return value


def _safe_reason(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 160
        or _UNSAFE_REASON.search(value)
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("safe_reason must be bounded non-sensitive text")
    return " ".join(value.split())


def provider_catalog() -> tuple[ProviderName, ...]:
    """Static complete provider catalog, excluding the synthetic cache."""
    return tuple(provider for provider in ProviderName if provider is not ProviderName.CACHE)


@dataclass(frozen=True, slots=True)
class ReadinessScope:
    egress: str | None = None
    machine: str | None = None
    request_class: str | None = None
    release_revision: str | None = None
    contract_version: str | None = None
    configuration_fingerprint: str | None = None
    credential_version_fingerprint: str | None = None
    account_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, _safe_optional(getattr(self, name), name))

    def as_dict(self) -> dict[str, str | None]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


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
        allowed = _DIMENSION_STATES.get(self.dimension)
        if allowed is None or self.state not in allowed:
            raise ValueError("observation dimension/state is outside the closed taxonomy")
        if (
            self.ttl_seconds is not None
            and (
                type(self.ttl_seconds) is not int
                or not 1 <= self.ttl_seconds <= 31_536_000
            )
        ):
            raise ValueError("observation TTL must be a bounded positive integer")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone aware")
        _safe_optional(self.source, "source")
        _safe_optional(self.evidence_ref, "evidence_ref")
        object.__setattr__(self, "safe_reason", _safe_reason(self.safe_reason))


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
            "eligible",
            "policy_skipped",
            "unavailable",
            "cooldown",
            "spend_blocked",
            "compatibility_unproven",
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
            "healthy"
            if self.healthy
            else "disabled"
            if self.registration != "registered"
            or not self.configuration.configured
            else "unready"
            if self.execution_decision.code
            in {"unavailable", "cooldown", "spend_blocked"}
            else "degraded"
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
            "valid_until": (
                self.valid_until.isoformat() if self.valid_until else None
            ),
            "evidence_receipts": list(self.evidence_receipts),
            "generation": self.generation,
            "protected_evidence_count": self.protected_evidence_count,
            "scope_fingerprint": self.scope_fingerprint,
            "authority": "provider_readiness",
        }

    def as_legacy_status(self) -> dict[str, object]:
        if self.healthy:
            effective = "healthy"
        elif self.registration != "registered" or not self.configuration.configured:
            effective = "disabled_by_config"
        elif self.spend in {"exhausted", "uncertain", "policy_denied"}:
            effective = "budget_exhausted"
        elif self.cooldown != "clear":
            effective = "temporarily_disabled_after_failures"
        else:
            effective = "degraded"
        return {
            "provider": self.provider.value,
            "config_status": (
                "enabled" if self.configuration.configured else "disabled_by_config"
            ),
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
    egress: str
    request_class: str
    release_revision: str | None = None
    contract_version: str | None = None


@dataclass(frozen=True, slots=True)
class ProbeAuthorization:
    workflow: str
    provider: ProviderName
    allowlist_version: str | None = None
    endpoint_contract: str | None = None
    named_quota: str | None = None
    idempotency_key: str | None = None
    durable_receipt: str | None = None
    spend_reserved: bool = False


@dataclass(frozen=True, slots=True)
class ProbeDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RegistrationInputs:
    enabled: bool
    credential_present: bool
    account_fingerprint: str | None
    budget_limit: float | None
    durable_spend_repository: bool
    compatible_fixture_release: bool


@dataclass(frozen=True, slots=True)
class RegistrationDecision:
    registered: bool
    issues: tuple[str, ...]


@dataclass(slots=True)
class _RenderCacheEntry:
    snapshot: ProviderReadinessSnapshot
    local_deadline: float


class ProviderReadinessService:
    """Folds durable normalized observations into one immutable truth."""

    def __init__(
        self,
        *,
        repository,
        monotonic=time.monotonic,
        legacy_health=None,
        legacy_budgets=None,
        legacy_reachability=None,
        legacy_providers=None,
    ):
        self.repository = repository
        self._monotonic = monotonic
        self._render_cache: dict[tuple[str, str], _RenderCacheEntry] = {}
        self._legacy_health = legacy_health
        self._legacy_budgets = legacy_budgets
        self._legacy_reachability = legacy_reachability
        self._legacy_providers = legacy_providers or {}

    @classmethod
    def from_legacy_observation_sources(
        cls,
        *,
        providers,
        health_tracker,
        budget_tracker,
        reachability,
        spend_repository=None,
        monotonic=time.monotonic,
    ) -> "ProviderReadinessService":
        """Normalize frozen legacy mechanics behind the readiness seam."""
        factory = getattr(spend_repository, "session_factory", None)
        bind = getattr(factory, "kw", {}).get("bind") if factory is not None else None
        if bind is not None and bind.__class__.__module__.startswith("sqlalchemy."):
            from argus.persistence.readiness import (
                readiness_repository_from_session_factory,
            )

            repository = readiness_repository_from_session_factory(
                factory
            )
        else:
            from argus.persistence.readiness import create_readiness_repository

            repository = create_readiness_repository("sqlite:///:memory:")
        return cls(
            repository=repository,
            monotonic=monotonic,
            legacy_health=health_tracker,
            legacy_budgets=budget_tracker,
            legacy_reachability=reachability,
            legacy_providers=providers,
        )

    def record_observation(
        self, observation: ProviderObservation
    ) -> ProviderReadinessSnapshot:
        self.repository.record_observation(observation)
        self._drop_provider_cache(observation.provider)
        return self.snapshot(
            observation.provider,
            egress=observation.scope.egress or "local",
            request_class=observation.scope.request_class or "discovery",
            release_revision=observation.scope.release_revision,
            contract_version=observation.scope.contract_version,
        )

    def snapshot(
        self,
        provider: ProviderName,
        *,
        egress: str = "local",
        request_class: str = "discovery",
        release_revision: str | None = None,
        contract_version: str | None = None,
        execution_context: ExecutionContext | None = None,
    ) -> ProviderReadinessSnapshot:
        query_scope = ReadinessScope(
            egress=egress,
            request_class=request_class,
            release_revision=release_revision,
            contract_version=contract_version,
        )
        now = self.repository.authority_now()
        all_rows = self.repository.observations(provider)
        matching = [row for row in all_rows if self._scope_matches(row.scope, query_scope)]
        current = [
            row
            for row in matching
            if row.expires_at is None or now < row.expires_at
        ]
        latest: dict[str, object] = {}
        for row in current:
            latest[row.dimension] = row

        registration = self._state(latest, "registration", "not_registered")
        config_state = self._state(latest, "configuration", "unknown")
        configuration = ConfigurationReadiness(
            () if config_state == "configured" else (config_state,)
        )
        reachability = self._state(latest, "reachability", "unknown")
        compatibility = self._state(latest, "compatibility", "unknown")
        usability = self._state(latest, "usability", "unknown")
        cooldown = self._state(latest, "cooldown", "unknown")
        spend_default = (
            "not_applicable" if PROVIDER_TIERS.get(provider, 0) == 0 else "unknown"
        )
        spend = self._state(latest, "spend", spend_default)

        protected_rows = [
            row for row in all_rows if row.protected and row.evidence_ref
        ]
        protected_count = len({row.evidence_ref for row in protected_rows})
        receipts = self._compact_receipts(all_rows)
        overflow = protected_count > MAX_EVIDENCE_RECEIPTS
        decision = self._decision(
            provider=provider,
            registration=registration,
            configuration=configuration,
            reachability=reachability,
            compatibility=compatibility,
            cooldown=cooldown,
            spend=spend,
            context=execution_context,
            evidence_overflow=overflow,
        )
        healthy = (
            not overflow
            and registration == "registered"
            and configuration.configured
            and compatibility == "compatible"
            and reachability == "reachable"
            and usability in {"usable", "empty"}
            and cooldown == "clear"
            and spend in {"not_applicable", "available", "low"}
        )
        expiries = [row.expires_at for row in current if row.expires_at is not None]
        valid_until = min(expiries) if expiries else None
        provisional = ProviderReadinessSnapshot(
            provider=provider,
            catalog_status="supported",
            registration=registration,
            configuration=configuration,
            reachability=reachability,
            compatibility=compatibility,
            usability=usability,
            cooldown=cooldown,
            spend=spend,
            healthy=healthy,
            execution_decision=decision,
            observed_at=now,
            valid_until=valid_until,
            evidence_receipts=receipts,
            generation=0,
            protected_evidence_count=protected_count,
            scope_fingerprint=query_scope.fingerprint(),
        )
        generation = self.repository.materialize_snapshot(
            provider=provider,
            scope_key=query_scope.fingerprint(),
            snapshot=provisional.as_dict(),
            valid_until=valid_until,
        )
        return replace(provisional, generation=generation)

    def render_snapshot(self, provider: ProviderName, **scope) -> ProviderReadinessSnapshot:
        query_scope = ReadinessScope(
            egress=scope.get("egress", "local"),
            request_class=scope.get("request_class", "discovery"),
            release_revision=scope.get("release_revision"),
            contract_version=scope.get("contract_version"),
        )
        key = (provider.value, query_scope.fingerprint())
        local_now = self._monotonic()
        authority_now = self.repository.authority_now()
        cached = self._render_cache.get(key)
        if (
            cached is not None
            and local_now < cached.local_deadline
            and (
                cached.snapshot.valid_until is None
                or authority_now < cached.snapshot.valid_until
            )
        ):
            return cached.snapshot
        snapshot = self.snapshot(provider, **scope)
        if snapshot.valid_until is None:
            lifetime = 5.0
        else:
            lifetime = min(
                5.0,
                (snapshot.valid_until - self.repository.authority_now()).total_seconds()
                - 1.0,
            )
        if lifetime > 0:
            self._render_cache[key] = _RenderCacheEntry(
                snapshot=snapshot, local_deadline=local_now + lifetime
            )
        else:
            self._render_cache.pop(key, None)
        return snapshot

    def execution_decision(self, context: ExecutionContext) -> ExecutionDecision:
        self._refresh_legacy_observations(context.provider, context.egress)
        return self.snapshot(
            context.provider,
            egress=context.egress,
            request_class=context.request_class,
            release_revision=context.release_revision,
            contract_version=context.contract_version,
            execution_context=context,
        ).execution_decision

    def best_egress(self, provider: ProviderName) -> str | None:
        if self._legacy_reachability is None:
            return "local"
        return self._legacy_reachability.peek_best_egress(provider)

    def claim_invocation(self, provider: ProviderName, egress: str):
        if self._legacy_health is None or self._legacy_reachability is None:
            return (None, None)
        health_claim = self._legacy_health.claim_execution(provider)
        if health_claim is None:
            return None
        reachability_claim = self._legacy_reachability.claim_egress(provider, egress)
        if reachability_claim is None:
            self._legacy_health.release_execution_claim(health_claim)
            return None
        return (health_claim, reachability_claim)

    def release_invocation(self, claims) -> None:
        if claims is None or self._legacy_health is None or self._legacy_reachability is None:
            return
        health_claim, reachability_claim = claims
        if reachability_claim is not None:
            self._legacy_reachability.release_claim(reachability_claim)
        if health_claim is not None:
            self._legacy_health.release_execution_claim(health_claim)

    def record_legacy_outcome(
        self,
        provider: ProviderName,
        *,
        egress: str,
        success: bool,
        latency_ms: int,
    ) -> None:
        if self._legacy_health is not None:
            (
                self._legacy_health.record_success
                if success
                else self._legacy_health.record_failure
            )(provider)
        if self._legacy_reachability is not None and success:
            self._legacy_reachability.update_probe(
                egress,
                provider,
                reachable=success,
                latency_ms=latency_ms,
                source="provider_execution",
            )
        self._refresh_legacy_observations(provider, egress)

    def paid_pacing(self, provider: ProviderName) -> tuple[bool, str, float, float, float]:
        if self._legacy_budgets is None:
            return True, "under pace", 0.0, 0.0, 0.0
        remaining = self._legacy_budgets.get_remaining_budget(provider) or 0.0
        used_today = self._legacy_budgets.used_today(provider)
        pace = self._legacy_budgets.daily_pace(provider)
        if self._legacy_budgets.is_budget_exhausted(provider):
            return False, "budget exhausted", remaining, used_today, pace
        if self._legacy_budgets.is_over_pace(provider):
            return (
                False,
                "over pace, conserving monthly credits",
                remaining,
                used_today,
                pace,
            )
        return True, "under pace", remaining, used_today, pace

    def budget_limit(self, provider: ProviderName) -> float:
        if self._legacy_budgets is None:
            return 0.0
        return self._legacy_budgets.get_budget_limit(provider)

    def legacy_health_projection(self, provider: ProviderName) -> dict | None:
        """Explicit detached projection for the pre-readiness response shape."""
        if self._legacy_health is None:
            return None
        snapshot = self._legacy_health.snapshot(provider)
        return snapshot.as_dict() if snapshot is not None else None

    def record_budget_usage(self, provider: ProviderName, cost: float) -> float | None:
        if self._legacy_budgets is None:
            return None
        self._legacy_budgets.record_usage(provider, cost)
        return self._legacy_budgets.get_remaining_budget(provider)

    def _refresh_legacy_observations(
        self, provider: ProviderName, egress: str
    ) -> None:
        if not self._legacy_providers:
            return
        now = datetime.now(UTC)
        provider_object = self._legacy_providers.get(provider)
        available = bool(provider_object and provider_object.is_available())
        observations = [
            ProviderObservation(
                provider=provider,
                dimension="registration",
                state="registered" if available else "not_registered",
                source="legacy_registration_projection",
                scope=ReadinessScope(),
                observed_at=now,
                ttl_seconds=300,
                safe_reason="explicit_compatibility_projection",
            ),
            ProviderObservation(
                provider=provider,
                dimension="configuration",
                state="configured" if available else "disabled_by_config",
                source="legacy_configuration_projection",
                scope=ReadinessScope(),
                observed_at=now,
                ttl_seconds=300,
            ),
            ProviderObservation(
                provider=provider,
                dimension="compatibility",
                state="compatible" if available else "unknown",
                source="versioned_fixture_projection",
                scope=ReadinessScope(),
                observed_at=now,
                ttl_seconds=300,
                safe_reason="explicit_compatibility_projection",
            ),
        ]
        cooldown = "clear"
        if self._legacy_health is not None:
            cooldown, _ = self._legacy_health.normalized_observation(provider)
        observations.append(
            ProviderObservation(
                provider=provider,
                dimension="cooldown",
                state=cooldown,
                source="legacy_health_observation",
                scope=ReadinessScope(),
                observed_at=now,
                ttl_seconds=60,
            )
        )
        reachability = "unknown"
        if self._legacy_reachability is not None:
            reachability, _ = self._legacy_reachability.normalized_observation(
                provider, egress=egress
            )
        observations.append(
            ProviderObservation(
                provider=provider,
                dimension="reachability",
                state=reachability,
                source="legacy_reachability_observation",
                scope=ReadinessScope(egress=egress),
                observed_at=now,
                ttl_seconds=60,
            )
        )
        tier = PROVIDER_TIERS.get(provider, 0)
        spend = "not_applicable"
        if tier > 0:
            spend = (
                "exhausted"
                if self._legacy_budgets is not None
                and self._legacy_budgets.is_budget_exhausted(provider)
                else "available"
            )
        observations.append(
            ProviderObservation(
                provider=provider,
                dimension="spend",
                state=spend,
                source="legacy_spend_observation",
                scope=ReadinessScope(),
                observed_at=now,
                ttl_seconds=60,
            )
        )
        for observation in observations:
            self.repository.record_observation(observation)
        self._drop_provider_cache(provider)

    def authorize_probe(
        self,
        provider: ProviderName,
        probe_kind: str,
        authorization: ProbeAuthorization | None = None,
    ) -> ProbeDecision:
        if probe_kind in {"fixture", "local_component"}:
            return ProbeDecision(True, "routine_no_spend")
        if probe_kind == "no_spend_account":
            if authorization is None or authorization.provider is not provider:
                return ProbeDecision(False, "versioned_allowlist_required")
            key = (
                provider.value,
                authorization.allowlist_version,
                authorization.endpoint_contract,
            )
            return (
                ProbeDecision(True, "versioned_no_spend_contract")
                if key in _NO_SPEND_ACCOUNT_ALLOWLIST
                else ProbeDecision(False, "endpoint_not_allowlisted")
            )
        if probe_kind == "no_money_quota":
            allowed = bool(
                authorization
                and authorization.workflow == "explicit_validation"
                and authorization.named_quota
                and authorization.idempotency_key
                and authorization.durable_receipt
            )
            return ProbeDecision(
                allowed, "explicit_quota_authorized" if allowed else "quota_spend_denied"
            )
        if probe_kind == "billable_search":
            allowed = bool(
                authorization
                and authorization.workflow == "explicit_validation"
                and authorization.provider is provider
                and authorization.idempotency_key
                and authorization.durable_receipt
                and authorization.spend_reserved
            )
            return ProbeDecision(
                allowed, "explicit_spend_authorized" if allowed else "billable_probe_denied"
            )
        return ProbeDecision(False, "unknown_probe_kind")

    def evaluate_registration(
        self, provider: ProviderName, inputs: RegistrationInputs
    ) -> RegistrationDecision:
        tier = PROVIDER_TIERS[provider]
        issues: list[str] = []
        if not inputs.enabled:
            issues.append("disabled_by_config")
        if not inputs.credential_present:
            issues.append("missing_credential")
        if tier > 0:
            if not inputs.account_fingerprint:
                issues.append("missing_account_binding")
            if inputs.budget_limit is None or inputs.budget_limit <= 0:
                issues.append("missing_budget")
            if not inputs.durable_spend_repository:
                issues.append("missing_spend_repository")
        if not inputs.compatible_fixture_release:
            issues.append("incompatible_fixture_release")
        ordered = tuple(issue for issue in _CONFIGURATION_ISSUES if issue in issues)
        return RegistrationDecision(not ordered, ordered)

    @staticmethod
    def validate_scope_manifest(
        *, egresses: tuple[str, ...], request_classes: tuple[str, ...]
    ) -> None:
        if (
            len(egresses) > 8
            or len(request_classes) > 4
            or len(egresses) * len(request_classes) > MAX_EXECUTABLE_SCOPES
        ):
            raise ValueError("scope manifest exceeds 32 executable scopes")
        if len(set(egresses)) != len(egresses) or len(set(request_classes)) != len(
            request_classes
        ):
            raise ValueError("scope manifest entries must be unique")

    @staticmethod
    def _scope_matches(stored: Mapping[str, str | None], query: ReadinessScope) -> bool:
        requested = query.as_dict()
        scoped_dimensions = {
            "egress",
            "request_class",
            "release_revision",
            "contract_version",
        }
        for name in scoped_dimensions:
            expected = stored.get(name)
            actual = requested.get(name)
            if expected is not None and actual is not None and expected != actual:
                return False
        return True

    @staticmethod
    def _state(latest: Mapping[str, object], dimension: str, default: str) -> str:
        row = latest.get(dimension)
        return getattr(row, "state", default)

    @staticmethod
    def _compact_receipts(rows) -> tuple[str, ...]:
        protected = []
        diagnostics = []
        for row in reversed(rows):
            receipt = row.evidence_ref
            if not receipt:
                continue
            target = protected if row.protected else diagnostics
            if receipt not in target:
                target.append(receipt)
        selected = protected[:MAX_EVIDENCE_RECEIPTS]
        for receipt in diagnostics:
            if len(selected) >= MAX_EVIDENCE_RECEIPTS:
                break
            if receipt not in selected:
                selected.append(receipt)
        return tuple(selected)

    @staticmethod
    def _decision(
        *,
        provider: ProviderName,
        registration: str,
        configuration: ConfigurationReadiness,
        reachability: str,
        compatibility: str,
        cooldown: str,
        spend: str,
        context: ExecutionContext | None,
        evidence_overflow: bool,
    ) -> ExecutionDecision:
        if evidence_overflow:
            return ExecutionDecision(
                "unavailable", "evidence_overflow", ("evidence_receipts",)
            )
        if context is not None:
            if provider not in context.plan_providers:
                return ExecutionDecision(
                    "policy_skipped", "provider_not_in_plan", ("plan",)
                )
            if context.free_only and context.tier > 0:
                return ExecutionDecision(
                    "policy_skipped", "free_only", ("caller_policy",)
                )
            if (
                context.caller_tier_cap is not None
                and context.tier > context.caller_tier_cap
            ):
                return ExecutionDecision(
                    "policy_skipped", "caller_tier_cap", ("caller_policy",)
                )
        if registration != "registered":
            return ExecutionDecision(
                "unavailable", "not_registered", ("registration",)
            )
        if not configuration.configured:
            return ExecutionDecision(
                "unavailable",
                configuration.issues[0],
                ("configuration",),
            )
        if compatibility != "compatible":
            return ExecutionDecision(
                "compatibility_unproven",
                (
                    "compatibility_incompatible"
                    if compatibility == "incompatible"
                    else "compatibility_unknown"
                ),
                ("compatibility",),
            )
        if reachability == "unreachable":
            return ExecutionDecision(
                "unavailable", "egress_unreachable", ("reachability",)
            )
        if cooldown in {"active", "half_open_claimed"}:
            return ExecutionDecision("cooldown", cooldown, ("cooldown",))
        if spend in {"exhausted", "uncertain", "policy_denied", "unknown"}:
            return ExecutionDecision("spend_blocked", spend, ("spend",))
        return ExecutionDecision("eligible", "all_gates_satisfied")

    def _drop_provider_cache(self, provider: ProviderName) -> None:
        for key in list(self._render_cache):
            if key[0] == provider.value:
                self._render_cache.pop(key, None)
