"""Immutable release contract capability manifest."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


class CapabilityManifestError(RuntimeError):
    """Release contract registration is incomplete or inconsistent."""


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class ReleaseCapabilityManifest:
    snapshot: Mapping[str, object]

    def as_dict(self) -> dict[str, object]:
        return _thaw(self.snapshot)


_HTTP_V2_REGISTRATIONS = frozenset(
    {
        "accepted_service",
        "legacy_presenter",
        "v2_presenter",
        "v2_routes",
        "transport_security",
    }
)


def http_capability_manifest(
    *,
    evidence_enabled: bool,
    registrations: set[str] | frozenset[str] | None = None,
) -> ReleaseCapabilityManifest:
    active = _HTTP_V2_REGISTRATIONS if registrations is None else frozenset(registrations)
    if evidence_enabled:
        missing = sorted(_HTTP_V2_REGISTRATIONS - active)
        if missing:
            raise CapabilityManifestError(
                "HTTP contract registration is missing: " + ", ".join(missing)
            )
        snapshot = {
            "http_contracts": [
                {"version": "1", "base_path": "/api", "legacy": True},
                {"version": "2.0", "base_path": "/api/v2", "legacy": False},
            ],
            "mcp_contract": {
                "transport": "streamable-http",
                "endpoint": "/mcp",
                "argus_tool_contract_versions": ["1"],
            },
        }
    else:
        snapshot = {}
    return ReleaseCapabilityManifest(snapshot=_freeze(snapshot))
