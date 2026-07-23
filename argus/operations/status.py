"""Cached, transport-neutral operational truth for callers and operators."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from argus.models import ProviderName


_STATES = {"healthy", "degraded", "unready", "unknown", "disabled"}
_SAFE_CORRELATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FULL_REVISION = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_REASON = re.compile(
    r"(?i)(?:https?://|authorization|cookie|password|passwd|secret|token|"
    r"\bquery\b|\burl\b|(?:^|[\s,;])[^=\s]+=[^\s]+)"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_PROVIDERS = frozenset(
    provider.value for provider in ProviderName if provider != ProviderName.CACHE
)
_PROVIDER_DIMENSIONS = frozenset(
    {"reachability", "health", "cooldown", "balance", "capability"}
)
_DEPENDENCIES = frozenset(
    {"postgresql", "schema", "outbox", "maya", "browser", "recovery"}
)
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"})
_STATUS_CLASSES = frozenset({"1xx", "2xx", "3xx", "4xx", "5xx"})
_OUTCOMES = frozenset({"success", "client_error", "server_error"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _aware(value).isoformat().replace("+00:00", "Z")


def _sanitize_reason(value: object) -> str | None:
    if value is None:
        return None
    text = _CONTROL.sub(" ", str(value)).strip()
    if not text:
        return None
    if _SENSITIVE_REASON.search(text):
        return "redacted"
    return " ".join(text.split())[:160]


def _safe_identity(value: object, *, default: str = "unknown") -> str:
    candidate = str(value or "").strip()
    return candidate if _SAFE_CORRELATION.fullmatch(candidate) else default


def safe_correlation_id(value: object | None) -> str:
    """Return safe caller correlation or a bounded generated identifier."""
    candidate = str(value or "").strip()
    if _SAFE_CORRELATION.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex[:16]


@dataclass(frozen=True)
class StatusObservation:
    """One bounded observation with explicit provenance and expiry."""

    state: str
    source: str
    observed_at: datetime
    expires_at: datetime
    reason: str | None
    last_transition: datetime
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _aware(now or _utc_now())
        stale = current >= self.expires_at
        result: dict[str, Any] = {
            "state": "unknown" if stale else self.state,
            "source": self.source,
            "observed_at": _iso(self.observed_at),
            "expires_at": _iso(self.expires_at),
            "reason": "observation_expired" if stale else self.reason,
            "last_transition": _iso(self.last_transition),
            "stale": stale,
        }
        if self.details:
            result["details"] = dict(self.details)
        return result


class ObservationStore:
    """Thread-safe latest-observation store preserving transition time."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now):
        self._clock = clock
        self._lock = threading.RLock()
        self._observations: dict[str, StatusObservation] = {}

    def observe(
        self,
        name: str,
        *,
        state: str,
        source: str,
        ttl: timedelta,
        observed_at: datetime | None = None,
        reason: object | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> StatusObservation:
        if state not in _STATES:
            raise ValueError(f"unsupported observation state: {state}")
        if ttl.total_seconds() <= 0:
            raise ValueError("observation ttl must be positive")
        when = _aware(observed_at or self._clock())
        safe_source = _safe_identity(source)
        safe_details = _bounded_details(details or {})
        with self._lock:
            previous = self._observations.get(name)
            last_transition = (
                previous.last_transition
                if previous is not None
                and previous.state == state
                and previous.expires_at > when
                else when
            )
            observation = StatusObservation(
                state=state,
                source=safe_source,
                observed_at=when,
                expires_at=when + ttl,
                reason=_sanitize_reason(reason),
                last_transition=last_transition,
                details=safe_details,
            )
            self._observations[name] = observation
            return observation

    def get(self, name: str) -> StatusObservation | None:
        with self._lock:
            return self._observations.get(name)

    def rendered(self, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
        current = _aware(now or self._clock())
        with self._lock:
            return {
                name: observation.as_dict(now=current)
                for name, observation in sorted(self._observations.items())
            }


def _bounded_details(details: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "remaining",
        "unlimited",
        "latency_ms",
        "consecutive_failures",
        "cooldown_until",
        "counts",
        "oldest_pending_age_seconds",
        "dead_letter_oldest_age_seconds",
        "schema_head",
        "promotion_allowed",
        "loaded",
        "processes",
        "memory_bytes",
        "process_restarts",
    }
    result: dict[str, Any] = {}
    for key, value in details.items():
        if key not in allowed:
            continue
        if isinstance(value, dict):
            result[key] = {
                str(nested_key)[:32]: int(nested_value)
                for nested_key, nested_value in list(value.items())[:16]
                if isinstance(nested_value, (int, float)) and not isinstance(nested_value, bool)
            }
        elif value is None or isinstance(value, (bool, int, float)):
            result[key] = value
        elif key in {"cooldown_until", "schema_head"}:
            result[key] = _safe_identity(value)
    return result


class BoundedMetrics:
    """Small concurrency-safe telemetry store with fixed label vocabularies."""

    max_series = 256
    _LABELS = {
        "requests": frozenset({"route", "method", "status_class", "outcome"}),
        "provider_outcomes": frozenset({"provider", "outcome"}),
        "state_transitions": frozenset({"component", "state"}),
        "accounting_reconciliation": frozenset({"provider", "state"}),
    }

    def __init__(self):
        self._lock = threading.RLock()
        self._series: dict[
            tuple[str, tuple[tuple[str, str], ...]], dict[str, float | int]
        ] = {}
        self._in_flight = 0
        self._gauges: dict[str, dict[str, Any]] = {}
        self._route_templates = {"unmatched"}

    def register_route_templates(self, routes: list[str]) -> None:
        """Admit bounded templates registered by the HTTP application."""
        safe = {
            route
            for route in routes[: self.max_series]
            if isinstance(route, str)
            and route.startswith("/")
            and len(route) <= 160
        }
        with self._lock:
            self._route_templates.update(safe)

    def increment(
        self,
        metric: str,
        labels: Mapping[str, str],
        *,
        value: int = 1,
        latency_seconds: float | None = None,
    ) -> None:
        allowed = self._LABELS.get(metric)
        if allowed is None or set(labels) != allowed:
            raise ValueError("metric labels are not in the bounded vocabulary")
        normalized = self._normalize_labels(metric, labels)
        key = (metric, tuple(sorted(normalized.items())))
        with self._lock:
            if key not in self._series and len(self._series) >= self.max_series:
                return
            series = self._series.setdefault(
                key,
                {"count": 0, "latency_seconds_sum": 0.0, "latency_seconds_max": 0.0},
            )
            series["count"] = int(series["count"]) + int(value)
            if latency_seconds is not None:
                bounded = max(0.0, min(float(latency_seconds), 300.0))
                series["latency_seconds_sum"] = (
                    float(series["latency_seconds_sum"]) + bounded
                )
                series["latency_seconds_max"] = max(
                    float(series["latency_seconds_max"]), bounded
                )

    def _normalize_labels(
        self, metric: str, labels: Mapping[str, str]
    ) -> dict[str, str]:
        result = {key: str(value) for key, value in labels.items()}
        if "provider" in result and result["provider"] not in _PROVIDERS:
            result["provider"] = "unknown"
        if metric == "requests":
            if result["route"] not in self._route_templates:
                result["route"] = "unmatched"
            if result["method"] not in _METHODS:
                result["method"] = "OTHER"
            if result["status_class"] not in _STATUS_CLASSES:
                result["status_class"] = "5xx"
            if result["outcome"] not in _OUTCOMES:
                result["outcome"] = "server_error"
        if "state" in result and result["state"] not in _STATES:
            result["state"] = "unknown"
        if "outcome" in result and metric != "requests":
            allowed = {"success", "failure", "timeout", "skipped", "uncertain"}
            if result["outcome"] not in allowed:
                result["outcome"] = "failure"
        if "component" in result:
            allowed_components = _DEPENDENCIES | {"startup", "readiness"}
            if result["component"] not in allowed_components:
                result["component"] = "readiness"
        return result

    def request_started(self) -> float:
        with self._lock:
            self._in_flight += 1
        return time.monotonic()

    def request_finished(
        self,
        *,
        started: float,
        route: str,
        method: str,
        status_code: int,
    ) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
        self.record_request(
            route=route,
            method=method,
            status_code=status_code,
            latency_seconds=time.monotonic() - started,
        )

    def record_request(
        self,
        *,
        route: str,
        method: str,
        status_code: int,
        latency_seconds: float,
    ) -> None:
        status_class = f"{max(1, min(int(status_code) // 100, 5))}xx"
        outcome = (
            "success"
            if status_code < 400
            else "client_error"
            if status_code < 500
            else "server_error"
        )
        self.increment(
            "requests",
            {
                "route": route,
                "method": method.upper(),
                "status_class": status_class,
                "outcome": outcome,
            },
            latency_seconds=latency_seconds,
        )

    def set_gauge(
        self,
        name: str,
        value: int | float | None,
        *,
        state: str,
        source: str,
    ) -> None:
        allowed = {
            "outbox_pending",
            "outbox_dead_letters",
            "browser_memory_bytes",
            "browser_processes",
            "process_restarts",
            "accounting_uncertain_charge",
        }
        if name not in allowed:
            raise ValueError("unsupported operational gauge")
        if state not in _STATES:
            state = "unknown"
        with self._lock:
            self._gauges[name] = {
                "value": value,
                "state": state,
                "source": _safe_identity(source),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests = []
            other: dict[str, list[dict[str, Any]]] = {}
            for (metric, label_items), values in self._series.items():
                entry = {"labels": dict(label_items), **values}
                if metric == "requests":
                    requests.append(entry)
                else:
                    other.setdefault(metric, []).append(entry)
            return {
                "requests": requests,
                "in_flight": self._in_flight,
                "series_count": len(self._series),
                "gauges": dict(self._gauges),
                **other,
            }


class OperationalStatusService:
    """Own current cached observations and derive readiness/degradation."""

    def __init__(
        self,
        *,
        production: bool,
        build: Mapping[str, Any],
        deployment: Mapping[str, Any],
        authority: Mapping[str, Any],
        capabilities: Mapping[str, bool],
        clock: Callable[[], datetime] = _utc_now,
    ):
        self.production = production
        self.build = dict(build)
        self.deployment = {
            key: _safe_identity(value)
            for key, value in deployment.items()
            if key in {"deployment_id", "environment", "release"}
        }
        self.authority = {
            key: _safe_identity(value)
            for key, value in authority.items()
            if key in {"role", "backend", "machine", "egress"}
        }
        self.capabilities = {
            str(key)[:48]: bool(value)
            for key, value in capabilities.items()
            if isinstance(value, bool)
        }
        self.service_instance_id = uuid.uuid4().hex
        self.started_at = _aware(clock())
        self._clock = clock
        self._startup = ObservationStore(clock=clock)
        self._dependencies = ObservationStore(clock=clock)
        self._providers: dict[str, ObservationStore] = {}
        self.metrics = BoundedMetrics()
        for dependency in _DEPENDENCIES:
            self._dependencies.observe(
                dependency,
                state="unknown",
                source="process_memory",
                ttl=timedelta(days=3650),
                reason="not_observed_since_restart",
            )
        for gauge in (
            "outbox_pending",
            "outbox_dead_letters",
            "browser_memory_bytes",
            "browser_processes",
            "process_restarts",
            "accounting_uncertain_charge",
        ):
            self.metrics.set_gauge(
                gauge,
                None,
                state="unknown",
                source="not_observed",
            )
        self._startup.observe(
            "initialization",
            state="unknown",
            source="process_memory",
            ttl=timedelta(days=3650),
            reason="initialization_pending",
        )

    def mark_initialized(
        self,
        *,
        source: str,
        reason: object | None = None,
    ) -> None:
        self._startup.observe(
            "initialization",
            state="healthy",
            source=source,
            ttl=timedelta(days=3650),
            reason=reason,
        )

    def mark_initialization_failed(self, *, source: str, reason: object) -> None:
        self._startup.observe(
            "initialization",
            state="unready",
            source=source,
            ttl=timedelta(minutes=5),
            reason=reason,
        )

    def observe_dependency(self, name: str, **observation: Any) -> StatusObservation:
        if name not in _DEPENDENCIES:
            raise ValueError(f"unknown dependency: {name}")
        return self._dependencies.observe(name, **observation)

    def observe_provider(
        self,
        provider: str,
        dimension: str,
        **observation: Any,
    ) -> StatusObservation:
        if provider not in _PROVIDERS:
            raise ValueError(f"unknown provider: {provider}")
        if dimension not in _PROVIDER_DIMENSIONS:
            raise ValueError(f"unknown provider observation: {dimension}")
        store = self._providers.setdefault(
            provider, ObservationStore(clock=self._clock)
        )
        return store.observe(dimension, **observation)

    def _render(self) -> tuple[
        dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]]
    ]:
        now = _aware(self._clock())
        dependencies = self._dependencies.rendered(now=now)
        providers = {
            provider: store.rendered(now=now)
            for provider, store in sorted(self._providers.items())
        }
        return dependencies, providers

    @staticmethod
    def _provider_state(observations: Mapping[str, Mapping[str, Any]]) -> str:
        capability = observations.get("capability", {}).get("state")
        if capability == "disabled":
            return "disabled"
        if capability != "healthy":
            return "unknown" if capability == "unknown" else "unready"
        states = [
            observation.get("state", "unknown")
            for dimension, observation in observations.items()
            if dimension != "capability"
        ]
        if any(state == "unready" for state in states):
            return "unready"
        if any(state in {"unknown", "degraded"} for state in states):
            return "degraded"
        return "healthy"

    def _classification(
        self,
        dependencies: Mapping[str, Mapping[str, Any]],
        providers: Mapping[str, Mapping[str, Mapping[str, Any]]],
    ) -> tuple[str, list[str]]:
        startup = self._startup.rendered(now=self._clock())["initialization"]
        if startup["state"] != "healthy":
            return "unready", ["startup"]

        reasons: list[str] = []
        if self.production:
            for name in ("postgresql", "schema", "outbox"):
                if dependencies.get(name, {}).get("state") != "healthy":
                    reasons.append(name)
            if reasons:
                return "unready", reasons

        active_provider_states = {
            provider: self._provider_state(observations)
            for provider, observations in providers.items()
            if observations.get("capability", {}).get("state") != "disabled"
        }
        usable = [
            provider
            for provider, state in active_provider_states.items()
            if state in {"healthy", "degraded"}
        ]
        if not usable:
            return "unready", ["retrieval_path"]

        degraded = [
            f"provider:{provider}"
            for provider, state in active_provider_states.items()
            if state in {"degraded", "unready", "unknown"}
        ]
        for name in ("maya", "browser", "recovery"):
            state = dependencies.get(name, {}).get("state", "unknown")
            allowed = {"healthy", "disabled"} if name == "maya" else {"healthy"}
            if state not in allowed:
                degraded.append(name)
        return ("degraded", degraded) if degraded else ("ready", [])

    def startup_status(self) -> dict[str, Any]:
        startup = self._startup.rendered(now=self._clock())["initialization"]
        startup_state = startup["state"]
        return {
            "status": (
                "initialized"
                if startup_state == "healthy"
                else "failed"
                if startup_state == "unready"
                else "initializing"
            ),
            "initialized": startup_state == "healthy",
            "version": self.build.get("version", "unknown"),
        }

    def readiness_status(self) -> dict[str, Any]:
        dependencies, providers = self._render()
        status, reasons = self._classification(dependencies, providers)
        return {
            "status": status,
            "ready": status != "unready",
            "reason_codes": reasons[:16],
        }

    def full_status(self) -> dict[str, Any]:
        dependencies, providers = self._render()
        status, reasons = self._classification(dependencies, providers)
        provider_payload = {
            provider: {
                "state": self._provider_state(observations),
                "observations": observations,
            }
            for provider, observations in providers.items()
        }
        recovery = dependencies.get("recovery", {})
        promotion_allowed = (
            recovery.get("state") == "healthy"
            and recovery.get("details", {}).get("promotion_allowed") is True
        )
        return {
            "status": status,
            "ready": status != "unready",
            "reason_codes": reasons[:16],
            "identity": {
                "service_instance_id": self.service_instance_id,
                "started_at": _iso(self.started_at),
            },
            "build": dict(self.build),
            "deployment": dict(self.deployment),
            "authority": dict(self.authority),
            "schema_head": dependencies.get("schema", {})
            .get("details", {})
            .get("schema_head"),
            "capabilities": dict(self.capabilities),
            "dependencies": dependencies,
            "providers": provider_payload,
            "promotion_allowed": promotion_allowed,
            "metrics": self.metrics.snapshot(),
            "observed_at": _iso(_aware(self._clock())),
        }


def create_operational_status(
    environ: Mapping[str, str] | None = None,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> OperationalStatusService:
    """Build process identity without network calls or runtime artifact rehashing."""
    values = environ if environ is not None else os.environ
    from argus import __version__
    from argus.runtime_manifest import EXPECTED_RUNTIME_CAPABILITIES

    manifest: dict[str, Any] = {}
    manifest_path = Path(
        values.get("ARGUS_RUNTIME_MANIFEST", "/app/runtime-manifest.json")
    )
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            manifest = loaded
    except (OSError, ValueError):
        pass

    source_revision = manifest.get("source_revision")
    lock_digest = manifest.get("lock_sha256")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = EXPECTED_RUNTIME_CAPABILITIES
    db_url = values.get("ARGUS_DB_URL", "")
    backend = (
        "postgresql"
        if db_url.startswith(("postgresql:", "postgres:"))
        else "sqlite"
        if not db_url or db_url.startswith("sqlite:")
        else "unknown"
    )
    environment = values.get("ARGUS_ENV", "development").strip().lower()
    return OperationalStatusService(
        production=environment == "production",
        build={
            "version": __version__,
            "source_revision": (
                source_revision
                if isinstance(source_revision, str)
                and _FULL_REVISION.fullmatch(source_revision)
                else "unknown"
            ),
            "lock_sha256": (
                lock_digest
                if isinstance(lock_digest, str) and _DIGEST.fullmatch(lock_digest)
                else "unknown"
            ),
            "manifest_source": (
                "runtime_manifest" if manifest else "package_metadata"
            ),
        },
        deployment={
            "deployment_id": values.get("ARGUS_DEPLOYMENT_ID", "unknown"),
            "environment": environment,
            "release": values.get("ARGUS_RELEASE", __version__),
        },
        authority={
            "role": values.get("ARGUS_NODE_ROLE", "primary"),
            "backend": backend,
            "machine": values.get("ARGUS_MACHINE_NAME", "unknown"),
            "egress": values.get("ARGUS_EGRESS_TYPE", "unknown"),
        },
        capabilities=capabilities,
        clock=clock,
    )


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "unknown")


def _provider_observed_at(status: Mapping[str, Any], now: datetime) -> datetime:
    health = status.get("health")
    if not isinstance(health, Mapping):
        return now
    timestamps = [
        value
        for value in (health.get("last_success"), health.get("last_failure"))
        if isinstance(value, (int, float))
    ]
    return datetime.fromtimestamp(max(timestamps), tz=timezone.utc) if timestamps else now


def _observe_provider_status(
    service: OperationalStatusService,
    *,
    broker: Any,
    now: datetime,
) -> None:
    try:
        reachability = broker._reachability.get_all()
    except Exception:
        reachability = {}
    uncertain_total = 0.0

    for provider in ProviderName:
        name = provider.value
        if name not in _PROVIDERS:
            continue
        try:
            status = broker.get_provider_status(provider)
        except Exception:
            service.observe_provider(
                name,
                "capability",
                state="unknown",
                source="process_memory",
                observed_at=now,
                ttl=timedelta(minutes=5),
                reason="provider_status_unavailable",
            )
            continue

        config_status = _enum_value(status.get("config_status"))
        effective_status = _enum_value(status.get("effective_status"))
        admitted = config_status in {"enabled", "healthy"}
        service.observe_provider(
            name,
            "capability",
            state="healthy" if admitted else "disabled",
            source="runtime_config",
            observed_at=now,
            ttl=timedelta(minutes=5),
            reason=None if admitted else config_status,
        )
        if not admitted:
            continue

        provider_reachability = reachability.get(provider) or reachability.get(name)
        if isinstance(provider_reachability, Mapping):
            probes = provider_reachability.get("probes") or {}
            probe_values = [
                probe for probe in probes.values() if isinstance(probe, Mapping)
            ]
            observed_timestamps = [
                probe.get("last_checked")
                for probe in probe_values
                if isinstance(probe.get("last_checked"), (int, float))
            ]
            observed = (
                datetime.fromtimestamp(max(observed_timestamps), tz=timezone.utc)
                if observed_timestamps
                else now
            )
            reachable = any(probe.get("reachable") is True for probe in probe_values)
            service.observe_provider(
                name,
                "reachability",
                state="healthy" if reachable else "unready",
                source="reachability_probe",
                observed_at=observed,
                ttl=timedelta(minutes=35),
                reason=None if reachable else "all_egress_probes_failed",
            )
        else:
            service.observe_provider(
                name,
                "reachability",
                state="unknown",
                source="process_memory",
                observed_at=now,
                ttl=timedelta(minutes=35),
                reason="not_observed_since_restart",
            )

        health = status.get("health")
        health_observed = _provider_observed_at(status, now)
        if isinstance(health, Mapping):
            failures = int(health.get("consecutive_failures") or 0)
            health_state = (
                "unready"
                if effective_status
                in {"temporarily_disabled_after_failures", "budget_exhausted"}
                else "degraded"
                if failures
                else "healthy"
            )
            service.observe_provider(
                name,
                "health",
                state=health_state,
                source="process_memory",
                observed_at=health_observed,
                ttl=timedelta(minutes=35),
                reason=(
                    "provider_failures"
                    if failures
                    else None
                ),
                details={"consecutive_failures": failures},
            )
            disabled_until = health.get("disabled_until")
            cooldown_active = (
                isinstance(disabled_until, (int, float))
                and disabled_until > now.timestamp()
            )
            service.observe_provider(
                name,
                "cooldown",
                state="unready" if cooldown_active else "healthy",
                source="process_memory",
                observed_at=health_observed,
                ttl=timedelta(minutes=35),
                reason="cooldown_active" if cooldown_active else None,
                details={
                    "cooldown_until": (
                        _iso(datetime.fromtimestamp(disabled_until, tz=timezone.utc))
                        if cooldown_active
                        else None
                    )
                },
            )
        else:
            for dimension in ("health", "cooldown"):
                service.observe_provider(
                    name,
                    dimension,
                    state="unknown",
                    source="process_memory",
                    observed_at=now,
                    ttl=timedelta(minutes=35),
                    reason="not_observed_since_restart",
                )

        try:
            budget_limit = broker.budget_tracker.get_budget_limit(provider)
            summary = broker.spend_repository.provider_summary(
                provider,
                budget_limit=budget_limit,
            )
            remaining = summary.get("remaining")
            unlimited = remaining is None
            balance_state = (
                "healthy"
                if unlimited or (isinstance(remaining, (int, float)) and remaining > 0)
                else "unready"
            )
            snapshot = summary.get("provider_snapshot")
            balance_observed = now
            source = "accounting_ledger"
            if isinstance(snapshot, Mapping):
                source = "provider_snapshot"
                try:
                    balance_observed = _aware(
                        datetime.fromisoformat(
                            str(snapshot["observed_at"]).replace("Z", "+00:00")
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    balance_observed = now
            service.observe_provider(
                name,
                "balance",
                state=balance_state,
                source=source,
                observed_at=balance_observed,
                ttl=timedelta(hours=24),
                reason="budget_exhausted" if balance_state == "unready" else None,
                details={"remaining": remaining, "unlimited": unlimited},
            )
            uncertain_total += float(summary.get("uncertain_charge") or 0)
            service.metrics.increment(
                "accounting_reconciliation",
                {
                    "provider": name,
                    "state": (
                        "degraded"
                        if float(summary.get("uncertain_charge") or 0) > 0
                        else "healthy"
                    ),
                },
            )
        except Exception:
            service.observe_provider(
                name,
                "balance",
                state="unknown",
                source="accounting_ledger",
                observed_at=now,
                ttl=timedelta(minutes=5),
                reason="balance_unavailable",
            )

    service.metrics.set_gauge(
        "accounting_uncertain_charge",
        uncertain_total,
        state="degraded" if uncertain_total else "healthy",
        source="accounting_ledger",
    )


def refresh_operational_status(
    service: OperationalStatusService,
    *,
    broker: Any,
    repository: Any,
    browser_status: Mapping[str, Any] | None = None,
    recovery_status: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Refresh cached evidence without making provider or external network probes."""
    from argus.recovery.database import EXPECTED_SCHEMA_HEAD

    observed = _aware(now or service._clock())
    try:
        authority = repository.operational_status(now=observed)
    except TypeError:
        # Simple adapters and test doubles may not accept a clock.
        try:
            authority = repository.operational_status()
        except Exception:
            authority = None
    except Exception:
        authority = None

    if not isinstance(authority, Mapping):
        for name in ("postgresql", "schema", "outbox"):
            service.observe_dependency(
                name,
                state="unready",
                source="authority_probe",
                observed_at=observed,
                ttl=timedelta(minutes=1),
                reason="authority_unavailable",
            )
        service.mark_initialization_failed(
            source="authority_probe",
            reason="authority_unavailable",
        )
    else:
        backend = str(authority.get("backend") or "unknown")
        service.authority["backend"] = _safe_identity(backend)
        connected = authority.get("connected") is True
        postgres_healthy = connected and (
            backend == "postgresql" or not service.production
        )
        service.observe_dependency(
            "postgresql",
            state="healthy" if postgres_healthy else "unready",
            source="authority_probe",
            observed_at=observed,
            ttl=timedelta(minutes=1),
            reason=None if postgres_healthy else "postgresql_authority_unavailable",
        )
        schema_head = authority.get("schema_head")
        schema_healthy = (
            schema_head == EXPECTED_SCHEMA_HEAD
            or (not service.production and schema_head == "sqlite-managed")
        )
        service.observe_dependency(
            "schema",
            state="healthy" if schema_healthy else "unready",
            source="authority_probe",
            observed_at=observed,
            ttl=timedelta(minutes=1),
            reason=None if schema_healthy else "schema_head_mismatch",
            details={"schema_head": schema_head},
        )
        outbox = authority.get("outbox")
        outbox_healthy = isinstance(outbox, Mapping)
        counts = dict(outbox.get("counts") or {}) if outbox_healthy else {}
        service.observe_dependency(
            "outbox",
            state="healthy" if outbox_healthy else "unready",
            source="authority_probe",
            observed_at=observed,
            ttl=timedelta(minutes=1),
            reason=None if outbox_healthy else "outbox_authority_unavailable",
            details={
                "counts": counts,
                "oldest_pending_age_seconds": (
                    outbox.get("oldest_pending_age_seconds") if outbox_healthy else None
                ),
                "dead_letter_oldest_age_seconds": (
                    outbox.get("dead_letter_oldest_age_seconds")
                    if outbox_healthy
                    else None
                ),
            },
        )
        service.metrics.set_gauge(
            "outbox_pending",
            int(counts.get("pending", 0))
            + int(counts.get("retry", 0))
            + int(counts.get("delivering", 0)),
            state="healthy" if outbox_healthy else "unready",
            source="authority_ledger",
        )
        service.metrics.set_gauge(
            "outbox_dead_letters",
            int(counts.get("dead_letter", 0)),
            state="degraded" if counts.get("dead_letter", 0) else "healthy",
            source="authority_ledger",
        )
        if postgres_healthy and schema_healthy and outbox_healthy:
            service.mark_initialized(source="authority_probe")
        else:
            service.mark_initialization_failed(
                source="authority_probe",
                reason="required_authority_unready",
            )

    _observe_provider_status(service, broker=broker, now=observed)

    if browser_status is not None:
        available = browser_status.get("available") is True
        loaded = browser_status.get("loaded") is True
        service.observe_dependency(
            "browser",
            state="healthy" if available else "degraded",
            source="runtime_manifest",
            observed_at=observed,
            ttl=timedelta(hours=24),
            reason=(
                browser_status.get("degraded_reason")
                if not available
                else None
            ),
            details={
                "loaded": loaded,
                "processes": browser_status.get("processes"),
                "memory_bytes": browser_status.get("memory_bytes"),
                "process_restarts": browser_status.get("process_restarts"),
            },
        )
        metrics_source = str(
            browser_status.get("metrics_source") or "process_memory"
        )
        for name, key in (
            ("browser_processes", "processes"),
            ("browser_memory_bytes", "memory_bytes"),
            ("process_restarts", "process_restarts"),
        ):
            value = browser_status.get(key)
            numeric = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
            )
            service.metrics.set_gauge(
                name,
                value if numeric else None,
                state="healthy" if numeric else "unknown",
                source=metrics_source,
            )

    if recovery_status is not None:
        recovery_state = str(recovery_status.get("state") or "unavailable")
        mapped = (
            "healthy"
            if recovery_state == "ready"
            else "degraded"
            if recovery_state == "degraded"
            else "unknown"
        )
        reasons = recovery_status.get("reasons")
        reason = (
            str(reasons[0])
            if isinstance(reasons, list) and reasons
            else "recovery_evidence_unavailable"
            if mapped == "unknown"
            else None
        )
        service.observe_dependency(
            "recovery",
            state=mapped,
            source="recovery_evidence",
            observed_at=observed,
            ttl=timedelta(minutes=5),
            reason=reason,
            details={
                "promotion_allowed": (
                    recovery_status.get("schema_promotion_allowed") is True
                )
            },
        )
