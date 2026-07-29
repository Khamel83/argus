"""HTTP application operations for provider diagnostics and guarded probes.

Routes depend on this narrow operation surface rather than owning broker,
provider, extraction, or persistence semantics directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from argus.broker.budgets import PROVIDER_TIERS
from argus.broker.execution import conservative_charge_estimate
from argus.broker.readiness import ProbeAuthorization
from argus.broker.router import SearchBroker
from argus.extraction.playwright_extractor import browser_capability_status
from argus.models import ProviderName, SearchMode, SearchQuery, is_adapter_provider
from argus.persistence.provider_spend import SpendConflictError
from argus.recovery.evidence import recovery_status_from_environment


class ProbeRejected(RuntimeError):
    """A diagnostic probe was denied before provider execution."""


class UnknownProviderError(ValueError):
    """Only provider-name parsing failed."""


@dataclass(frozen=True)
class FixtureProviderFacts:
    provider: str
    available: bool
    status: str
    readiness: Mapping[str, Any]


@dataclass(frozen=True)
class LiveProviderFacts:
    provider: str
    available: bool
    status: str
    trace: Mapping[str, Any]
    sample_results: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ProviderSpendFacts:
    providers: tuple[Mapping[str, Any], ...]
    operational: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class BudgetStateFacts:
    rows: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ProviderHealthFacts:
    status: str
    providers: Mapping[str, Any]


@dataclass(frozen=True)
class CallerBudgetsFacts:
    providers: Mapping[str, Any]


@dataclass(frozen=True)
class HealthDetailFacts:
    providers: Mapping[str, Any]
    health_tracking: Mapping[str, Any]
    browser: Mapping[str, Any]
    recovery: Mapping[str, Any]


@dataclass(frozen=True)
class AdminBudgetsFacts:
    budgets: Mapping[str, Any]
    token_balances: Mapping[str, Any]


class ProviderApplicationService:
    """Coordinate authority dependencies and return typed provider facts."""

    def __init__(
        self,
        broker_provider: Callable[[], SearchBroker],
        spend_repository_provider: Callable[[], Any],
    ) -> None:
        self._broker_provider = broker_provider
        self._spend_repository_provider = spend_repository_provider

    @staticmethod
    def is_spend_conflict(exc: Exception) -> bool:
        return isinstance(exc, SpendConflictError)

    @staticmethod
    def _provider_name(provider: str) -> ProviderName:
        try:
            return ProviderName(provider)
        except ValueError as exc:
            raise UnknownProviderError(provider) from exc

    def fixture_provider(self, provider: str) -> FixtureProviderFacts:
        broker = self._broker_provider()
        pname = self._provider_name(provider)
        decision = broker.readiness_service.authorize_probe(pname, "fixture")
        readiness = broker.provider_readiness_projection(pname)
        return FixtureProviderFacts(
            provider=provider,
            available=decision.allowed,
            status="fixture_verified" if decision.allowed else "denied",
            readiness=readiness,
        )

    async def live_provider(
        self,
        *,
        provider: str,
        query_text: str,
        caller: str,
        idempotency_key: str | None,
        durable_receipt: str | None,
    ) -> LiveProviderFacts:
        broker = self._broker_provider()
        pname = self._provider_name(provider)
        probe_query = SearchQuery(
            query=query_text,
            mode=SearchMode.DISCOVERY,
            max_results=3,
            providers=[pname],
            caller=caller,
            user_visible=False,
        )
        tier = PROVIDER_TIERS[pname]
        authorization = ProbeAuthorization(
            workflow="explicit_validation",
            provider=pname,
            named_quota="free_provider_request" if tier == 0 else None,
            idempotency_key=idempotency_key,
            durable_receipt=durable_receipt,
            conservative_charge=(
                conservative_charge_estimate(pname, probe_query) if tier > 0 else None
            ),
        )
        kind = "no_money_quota" if tier == 0 else "billable_search"
        decision = broker.readiness_service.authorize_probe(pname, kind, authorization)
        if not decision.allowed:
            raise ProbeRejected(decision.reason)
        query = SearchQuery(
            query=probe_query.query,
            mode=SearchMode.DISCOVERY,
            max_results=3,
            providers=[pname],
            caller=caller,
            user_visible=False,
            metadata={
                "caller_label": "http-admin-smoke",
                "probe_receipt": durable_receipt,
                "probe_idempotency_key": idempotency_key,
                "probe_provider": pname.value,
                "probe_no_fallback": True,
                "probe_attempt_id": decision.attempt_id,
            },
        )
        response = await broker.search(query)
        trace = response.traces[0] if response.traces else None
        return LiveProviderFacts(
            provider=provider,
            available=trace is not None,
            status=trace.status if trace else "no_trace",
            trace={
                "status": trace.status if trace else "no_trace",
                "results_count": trace.results_count if trace else 0,
                "latency_ms": trace.latency_ms if trace else 0,
                "error": trace.error if trace else None,
            },
            sample_results=tuple(
                {"url": r.url, "title": r.title, "snippet": r.snippet[:100]}
                for r in response.results[:3]
            ),
        )

    def provider_spend(self) -> ProviderSpendFacts:
        broker = self._broker_provider()
        repository = self._spend_repository_provider()
        providers = []
        operational = []
        for provider in ProviderName:
            if not is_adapter_provider(provider):
                continue
            projection = broker.provider_budget_projection(provider)
            providers.append(projection)
            operational.append(
                repository.non_authoritative_operational_projection(
                    provider,
                    budget_limit=float(projection.get("budget_limit") or 0),
                )
            )
        return ProviderSpendFacts(tuple(providers), tuple(operational))

    def budget_state(self) -> BudgetStateFacts:
        broker = self._broker_provider()
        rows = []
        for pname in ProviderName:
            projection = broker.provider_budget_projection(pname)
            budget = projection.get("budget_limit")
            if not isinstance(budget, (int, float)) or budget <= 0:
                continue
            remaining = projection.get("remaining")
            used = (
                max(0.0, budget - remaining)
                if isinstance(remaining, (int, float))
                else 0.0
            )
            status = str(projection.get("state", "unknown"))
            rows.append(
                {
                    "provider": pname.value,
                    "tier": PROVIDER_TIERS.get(pname, 99),
                    "budget": int(budget),
                    "used": int(used),
                    "remaining": (
                        int(remaining) if isinstance(remaining, (int, float)) else None
                    ),
                    "used_today": 0,
                    "pct_used": round(min(100.0, (used / budget) * 100.0), 1),
                    "status": status,
                }
            )
        rows.sort(
            key=lambda row: (
                row["status"] != "exhausted",
                row["status"] != "over_pace",
                row["tier"],
            )
        )
        return BudgetStateFacts(tuple(rows))

    def provider_health(self, operational_status: Any) -> ProviderHealthFacts:
        from argus.operations.presentation import provider_display_state

        broker = self._broker_provider()
        providers = {
            pname.value: broker.provider_readiness_projection(pname)
            for pname in ProviderName
            if pname != ProviderName.CACHE
        }
        cached = operational_status.full_status().get("providers") or {}
        for provider, evidence in cached.items():
            if provider in providers:
                providers[provider] = {
                    **providers[provider],
                    "operational": {
                        "non_authoritative": True,
                        "state": evidence.get("state", "unknown"),
                        "observations": evidence.get("observations") or {},
                    },
                }
        active_states = [
            provider_display_state(status)
            for status in providers.values()
            if provider_display_state(status) != "disabled"
        ]
        healthy = any(state in {"healthy", "degraded"} for state in active_states)
        fully_healthy = healthy and all(state == "healthy" for state in active_states)
        return ProviderHealthFacts(
            status="ok" if fully_healthy else "degraded", providers=providers
        )

    def caller_budgets(self) -> CallerBudgetsFacts:
        broker = self._broker_provider()
        return CallerBudgetsFacts(
            providers={
                pname.value: broker.provider_budget_projection(pname)
                for pname in ProviderName
                if pname != ProviderName.CACHE
            },
        )

    def health_detail(self) -> HealthDetailFacts:
        broker = self._broker_provider()
        provider_evidence = broker.operational_provider_evidence()
        providers = {
            name: dict(entry.get("status") or {})
            for name, entry in provider_evidence.items()
        }
        for pname_str, entry in providers.items():
            reachability = (provider_evidence.get(pname_str) or {}).get("reachability")
            if reachability:
                entry["best_egress"] = reachability["best"]
                entry["egress_probes"] = reachability["probes"]
            else:
                entry["best_egress"] = "local"
                entry["egress_probes"] = {}
        return HealthDetailFacts(
            providers=providers,
            health_tracking={
                name: (provider_evidence.get(name) or {}).get("readiness", {})
                for name in providers
            },
            browser=browser_capability_status(),
            recovery=recovery_status_from_environment(),
        )

    def admin_budgets(self) -> AdminBudgetsFacts:
        broker = self._broker_provider()
        budget_info = {
            pname.value: broker.provider_budget_projection(pname)
            for pname in ProviderName
        }
        token_balances = {}
        store = broker.budget_tracker._store
        if store:
            token_balances = store.get_all_token_balances()
        return AdminBudgetsFacts(budgets=budget_info, token_balances=token_balances)
