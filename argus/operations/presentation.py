"""Shared presentation rules for HTTP-authority diagnostics."""

from __future__ import annotations

from typing import Any, Mapping


_PROVIDER_STATE_ALIASES = {
    "enabled": "healthy",
    "healthy": "healthy",
    "degraded": "degraded",
    "temporarily_disabled_after_failures": "unready",
    "budget_exhausted": "unready",
    "disabled_by_config": "disabled",
    "unavailable_missing_key": "disabled",
}


def budget_remaining(value: object) -> object:
    """Render unlimited distinctly from a numeric zero balance."""
    return "unlimited" if value is None else value


def provider_display_state(status: Mapping[str, Any]) -> str:
    """Prefer cached operational truth over a legacy configured status."""
    current = status.get("state")
    if current:
        return str(current)
    legacy = str(status.get("effective_status") or "unknown")
    return _PROVIDER_STATE_ALIASES.get(legacy, "unknown")


def nested_status_failures(status: Mapping[str, Any]) -> list[str]:
    """Render nested non-healthy observations without dropping their reason."""
    failures = []
    for name, observation in sorted((status.get("observations") or {}).items()):
        state = observation.get("state", "unknown")
        if state in {"healthy", "disabled"}:
            continue
        reason = observation.get("reason")
        failures.append(f"{name}={state}" + (f" ({reason})" if reason else ""))
    return failures
