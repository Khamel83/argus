"""Immutable release contract capability manifest."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
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

MCP_RELEASE_DESCRIPTOR_PATH = (
    Path(__file__).resolve().parent / "mcp/release_descriptor.json"
)
_MCP_RELEASE_DESCRIPTOR_SHA256 = (
    "679728d897ac24bca9eb2547c6e9ef7d819ef76605863b401bd7f36475793e56"
)


def _read_release_descriptor(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise CapabilityManifestError(
            "MCP release manifest descriptor is unavailable or malformed"
        ) from exc
    if not isinstance(document, dict):
        raise CapabilityManifestError(
            "MCP release manifest descriptor must be an object"
        )
    return document


def _tool_descriptor(release: Mapping[str, object]) -> Mapping[str, object]:
    tools = release.get("tools")
    if not isinstance(tools, (list, tuple)):
        raise CapabilityManifestError(
            "MCP release manifest descriptor tools are malformed"
        )
    names = []
    schemas = {}
    for entry in tools:
        if not isinstance(entry, Mapping):
            raise CapabilityManifestError(
                "MCP release manifest descriptor tool entry is malformed"
            )
        name = entry.get("name")
        input_digest = entry.get("input_sha256")
        output_digest = entry.get("output_sha256")
        if (
            not isinstance(name, str)
            or not isinstance(input_digest, str)
            or not isinstance(output_digest, str)
        ):
            raise CapabilityManifestError(
                "MCP release manifest descriptor tool digest is malformed"
            )
        names.append(name)
        schemas[name] = {
            "input_sha256": input_digest,
            "output_sha256": output_digest,
        }
    return _freeze(
        {
            "transport_version": release.get("transport_version"),
            "tool_contract_version": release.get("tool_contract_version"),
            "tools": names,
            "schemas": schemas,
        }
    )


def load_mcp_release_descriptor(
    path: str | Path | None = None,
) -> Mapping[str, object]:
    """Load and authenticate the immutable packaged MCP release artifact."""
    descriptor_path = MCP_RELEASE_DESCRIPTOR_PATH if path is None else Path(path)
    try:
        encoded = descriptor_path.read_bytes()
    except OSError as exc:
        raise CapabilityManifestError(
            "MCP release manifest descriptor is unavailable"
        ) from exc
    if hashlib.sha256(encoded).hexdigest() != _MCP_RELEASE_DESCRIPTOR_SHA256:
        raise CapabilityManifestError(
            "MCP release manifest descriptor digest does not match the release"
        )
    document = _read_release_descriptor(descriptor_path)
    if document.get("descriptor_version") != 1:
        raise CapabilityManifestError(
            "MCP release manifest descriptor version does not match the release"
        )
    transport = document.get("transport")
    if not isinstance(transport, Mapping):
        raise CapabilityManifestError(
            "MCP release manifest descriptor transport is malformed"
        )
    _tool_descriptor(document)
    return _freeze(document)


_PACKAGED_RELEASE = _freeze(_read_release_descriptor(MCP_RELEASE_DESCRIPTOR_PATH))
MCP_TRANSPORT_DESCRIPTOR = _PACKAGED_RELEASE["transport"]
MCP_V2_TOOL_DESCRIPTOR = _tool_descriptor(_PACKAGED_RELEASE)


def http_capability_manifest(
    *,
    evidence_enabled: bool,
    registrations: set[str] | frozenset[str] | None = None,
    mcp_transport_registration: Mapping[str, object] | None = None,
    mcp_tool_registration: Mapping[str, object] | None = None,
    release_descriptor_path: str | Path | None = None,
) -> ReleaseCapabilityManifest:
    active = frozenset() if registrations is None else frozenset(registrations)
    try:
        release = load_mcp_release_descriptor(release_descriptor_path)
        expected_transport = release["transport"]
        expected_tools = _tool_descriptor(release)
        if (
            mcp_transport_registration is not None
            and _freeze(mcp_transport_registration) != expected_transport
        ):
            raise CapabilityManifestError(
                "MCP transport registration does not match the release manifest descriptor"
            )
        if (
            mcp_tool_registration is not None
            and _freeze(mcp_tool_registration) != expected_tools
        ):
            raise CapabilityManifestError(
                "MCP tool registration does not match the release manifest descriptor"
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
    *,
    release_descriptor_path: str | Path | None = None,
) -> None:
    """Fail closed when the listener does not match the release descriptor."""
    release = load_mcp_release_descriptor(release_descriptor_path)
    _validate_mcp_transport_registration(registration, release)


def _validate_mcp_transport_registration(
    registration: Mapping[str, object],
    release: Mapping[str, object],
) -> None:
    expected = release["transport"]
    if _freeze(registration) != expected:
        raise CapabilityManifestError(
            "MCP transport registration does not match the release manifest descriptor"
        )


def validate_complete_mcp_registration(
    transport_registration: Mapping[str, object],
    tool_registration: Mapping[str, object],
    *,
    release_descriptor_path: str | Path | None = None,
) -> None:
    """Validate one process against the immutable complete MCP release lane."""
    release = load_mcp_release_descriptor(release_descriptor_path)
    _validate_mcp_transport_registration(
        transport_registration,
        release,
    )
    _validate_mcp_tool_registration(
        tool_registration,
        release,
    )


def validate_mcp_tool_registration(
    tool_registration: Mapping[str, object],
    *,
    release_descriptor_path: str | Path | None = None,
) -> None:
    """Fail closed when tool names or schemas drift from the release lane."""
    release = load_mcp_release_descriptor(release_descriptor_path)
    _validate_mcp_tool_registration(tool_registration, release)


def _validate_mcp_tool_registration(
    tool_registration: Mapping[str, object],
    release: Mapping[str, object],
) -> None:
    if _freeze(tool_registration) != _tool_descriptor(release):
        raise CapabilityManifestError(
            "MCP tool registration does not match the release manifest descriptor"
        )
