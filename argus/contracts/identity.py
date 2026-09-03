"""Immutable identities used to bind Argus evidence across boundaries."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


def _text(name: str, value: object, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be non-empty and bounded")
    if not value.isprintable() or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{name} must use a safe identifier")
    return value


def _digest(name: str, value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return value


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _identity_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SchemaIdentity:
    """The exact four-member schema identity tuple."""

    schema_head: str
    migration_chain_sha256: str
    canonical_postgresql_schema_sha256: str
    schema_contract_format: str

    def __post_init__(self) -> None:
        _text("schema_head", self.schema_head)
        _digest("migration_chain_sha256", self.migration_chain_sha256)
        _digest(
            "canonical_postgresql_schema_sha256",
            self.canonical_postgresql_schema_sha256,
        )
        _text("schema_contract_format", self.schema_contract_format, 64)

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (
            self.schema_head,
            self.migration_chain_sha256,
            self.canonical_postgresql_schema_sha256,
            self.schema_contract_format,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_head": self.schema_head,
            "migration_chain_sha256": self.migration_chain_sha256,
            "canonical_postgresql_schema_sha256": self.canonical_postgresql_schema_sha256,
            "schema_contract_format": self.schema_contract_format,
        }

    @property
    def identity_id(self) -> str:
        return _identity_digest(
            {"schema": "argus.schema-identity.v1", **self.as_dict()}
        )


@dataclass(frozen=True, slots=True)
class ReleaseIdentity:
    """Source and artifact identity for one immutable release."""

    source_revision: str
    image_digest: str
    release_descriptor_digest: str
    runtime_manifest_digest: str

    def __post_init__(self) -> None:
        _text("source_revision", self.source_revision)
        _digest("image_digest", self.image_digest)
        _digest("release_descriptor_digest", self.release_descriptor_digest)
        _digest("runtime_manifest_digest", self.runtime_manifest_digest)

    def as_dict(self) -> dict[str, str]:
        return {
            "source_revision": self.source_revision,
            "image_digest": self.image_digest,
            "release_descriptor_digest": self.release_descriptor_digest,
            "runtime_manifest_digest": self.runtime_manifest_digest,
        }

    @property
    def release_id(self) -> str:
        payload = {
            "schema": "argus.release-identity.v1",
            "source_revision": self.source_revision,
            "image_digest": self.image_digest,
            "release_descriptor_digest": self.release_descriptor_digest,
        }
        return _identity_digest(payload)


@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    """Identity chain for one accepted operation and optional delivery."""

    operation_id: str
    request_id: str
    release_identity: ReleaseIdentity
    schema_identity: SchemaIdentity
    receipt_identity: str
    maya_capture_id: str | None = None

    def __post_init__(self) -> None:
        _text("operation_id", self.operation_id)
        _text("request_id", self.request_id)
        _text("receipt_identity", self.receipt_identity)
        if self.maya_capture_id is not None:
            _text("maya_capture_id", self.maya_capture_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "release_identity": self.release_identity.as_dict(),
            "schema_identity": self.schema_identity.as_dict(),
            "receipt_identity": self.receipt_identity,
            "maya_capture_id": self.maya_capture_id,
        }

    @property
    def evidence_id(self) -> str:
        return _identity_digest(
            {"schema": "argus.evidence-identity.v1", **self.as_dict()}
        )


__all__ = ["EvidenceIdentity", "ReleaseIdentity", "SchemaIdentity"]
