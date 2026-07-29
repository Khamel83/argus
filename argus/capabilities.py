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
    mcp_transport: Mapping[str, object]
    mcp_tools: Mapping[str, object]

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

MCP_TRANSPORT_DESCRIPTOR = _freeze(
    {
        "endpoint": "/mcp",
        "protocol_versions": (
            "2024-11-05",
            "2025-03-26",
            "2025-06-18",
            "2025-11-25",
        ),
        "methods": ("POST", "GET", "DELETE", "OPTIONS"),
        "post_content_type": "application/json",
        "post_accept": ("application/json", "text/event-stream"),
        "get_accept": "text/event-stream",
        "max_request_body_bytes": 4 * 1024 * 1024,
        "notification_status": 202,
        "session_idle_timeout_seconds": 30 * 60,
        "max_active_sessions": 256,
        "session_id_max_characters": 128,
        "legacy_sse_paths": ("/sse", "/messages/"),
    }
)

_MCP_V2_TOOL_NAMES = (
    "search_web_v2",
    "recover_url_v2",
    "expand_links_v2",
    "extract_content_v2",
)
_MCP_V2_OUTPUT_SHA256 = (
    "7d6282dadca6c6ac229b37f235ff92b02f96d96529e1bd52e2ebe06072ed3d9d"
)
MCP_V2_TOOL_DESCRIPTOR = _freeze(
    {
        "transport_version": "2025-11-25",
        "tool_contract_version": "2.0",
        "tools": _MCP_V2_TOOL_NAMES,
        "schemas": {
            "search_web_v2": {
                "input_sha256": (
                    "377f1d1772fb412f6fae31ac6784cd93a8f76c212e764b92969d80d55198e767"
                ),
                "output_sha256": _MCP_V2_OUTPUT_SHA256,
            },
            "recover_url_v2": {
                "input_sha256": (
                    "a0bf26f296772a0f1ddbd471e071a3d6179a3515fa12560cbdc6ffcfcd22a908"
                ),
                "output_sha256": _MCP_V2_OUTPUT_SHA256,
            },
            "expand_links_v2": {
                "input_sha256": (
                    "a575e66279bb3d09f447b1034d6132cf3ad60955626192931845ae14cb63b2fa"
                ),
                "output_sha256": _MCP_V2_OUTPUT_SHA256,
            },
            "extract_content_v2": {
                "input_sha256": (
                    "50bb241cb4156390d2d522ebafecbc8b42905214f4c4a8f1db776e7bfc29e442"
                ),
                "output_sha256": _MCP_V2_OUTPUT_SHA256,
            },
        },
    }
)


def http_capability_manifest(
    *,
    evidence_enabled: bool,
    registrations: set[str] | frozenset[str] | None = None,
    mcp_transport_registration: Mapping[str, object] | None = None,
    mcp_tool_registration: Mapping[str, object] | None = None,
) -> ReleaseCapabilityManifest:
    active = frozenset() if registrations is None else frozenset(registrations)
    try:
        validate_complete_mcp_registration(
            (
                MCP_TRANSPORT_DESCRIPTOR
                if mcp_transport_registration is None
                else mcp_transport_registration
            ),
            (
                MCP_V2_TOOL_DESCRIPTOR
                if mcp_tool_registration is None
                else mcp_tool_registration
            ),
        )
    except CapabilityManifestError:
        mcp_v2_supported = False
    else:
        mcp_v2_supported = True
    if evidence_enabled:
        missing = sorted(_HTTP_V2_REGISTRATIONS - active)
        if missing:
            raise CapabilityManifestError(
                "HTTP contract registration is missing: " + ", ".join(missing)
            )
        mcp_contract = {
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "argus_tool_contract_versions": (
                ["1", "2.0"] if mcp_v2_supported else ["1"]
            ),
        }
        if mcp_v2_supported:
            mcp_contract["version_2_tool_suffix"] = "_v2"
        snapshot = {
            "http_contracts": [
                {"version": "1", "base_path": "/api", "legacy": True},
                {"version": "2.0", "base_path": "/api/v2", "legacy": False},
            ],
            "mcp_contract": mcp_contract,
        }
    else:
        snapshot = {}
    return ReleaseCapabilityManifest(
        snapshot=_freeze(snapshot),
        mcp_transport=MCP_TRANSPORT_DESCRIPTOR,
        mcp_tools=MCP_V2_TOOL_DESCRIPTOR,
    )


def validate_mcp_transport_registration(
    registration: Mapping[str, object],
) -> None:
    """Fail closed when the listener does not match the release descriptor."""
    expected = MCP_TRANSPORT_DESCRIPTOR
    if _freeze(registration) != expected:
        raise CapabilityManifestError(
            "MCP transport registration does not match the release manifest"
        )


def validate_complete_mcp_registration(
    transport_registration: Mapping[str, object],
    tool_registration: Mapping[str, object],
) -> None:
    """Validate one process against the immutable complete MCP release lane."""
    validate_mcp_transport_registration(transport_registration)
    validate_mcp_tool_registration(tool_registration)


def validate_mcp_tool_registration(
    tool_registration: Mapping[str, object],
) -> None:
    """Fail closed when tool names or schemas drift from the release lane."""
    if _freeze(tool_registration) != MCP_V2_TOOL_DESCRIPTOR:
        raise CapabilityManifestError(
            "MCP tool registration does not match the release manifest"
        )
