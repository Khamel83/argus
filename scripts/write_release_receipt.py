#!/usr/bin/env python3
"""Validate and write the immutable container build receipt."""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path


class ReceiptError(ValueError):
    """Release receipt input is incomplete or inconsistent."""


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_IMAGE = re.compile(
    r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+\Z",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_FIELDS = (
    "schema_head",
    "migration_chain_sha256",
    "canonical_postgresql_schema_sha256",
    "schema_contract_format",
)
_SCHEMA_CONTRACT_FORMAT = "argus-schema-contract-v1"


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ReceiptError("value is not canonical JSON") from error


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as error:
        raise ReceiptError(f"release artifact is unreadable: {path}") from error


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReceiptError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise ReceiptError(f"{label} must be a JSON object")
    return value


def _schema_identity_from_contract(
    path: Path,
    *,
    expected_head: str,
) -> dict[str, str]:
    contract = _read_json(path, "schema contract")
    if contract.get("schema_head") != expected_head:
        raise ReceiptError("schema contract head does not match runtime manifest")
    if any(
        not isinstance(contract.get(field), str) for field in _SCHEMA_FIELDS
    ):
        raise ReceiptError("schema contract has an incomplete schema identity")
    identity = {field: contract[field] for field in _SCHEMA_FIELDS}
    if any(
        not _SHA256.fullmatch(identity[field])
        for field in _SCHEMA_FIELDS
        if field.endswith("_sha256")
    ):
        raise ReceiptError("schema contract identity hash is invalid")
    if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", expected_head):
        raise ReceiptError("schema contract head is invalid")
    if identity["schema_contract_format"] != _SCHEMA_CONTRACT_FORMAT:
        raise ReceiptError("schema contract format is unsupported")
    declared_id = contract.get("schema_id")
    calculated_id = _sha256_bytes(
        _canonical_json_bytes({"schema": "argus.schema-identity.v1", **identity})
    )
    if declared_id is not None and declared_id != calculated_id:
        raise ReceiptError("schema contract schema identity hash is invalid")
    return {**identity, "schema_id": calculated_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--runtime-manifest", required=True, type=Path)
    parser.add_argument("--release-descriptor", required=True, type=Path)
    parser.add_argument("--schema-contract", required=True, type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--request-id")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if not _IMAGE.fullmatch(args.image):
        parser.error("--image must be an untagged ghcr.io owner/repository name")
    if not _DIGEST.fullmatch(args.digest):
        parser.error(
            "--digest must be sha256 followed by 64 lowercase hex characters"
        )
    if not _REVISION.fullmatch(args.source_revision):
        parser.error("--source-revision must be a full lowercase Git commit")

    operation_id = args.operation_id or f"release-{args.source_revision}"
    request_id = args.request_id or f"workflow-{args.run_id}-{args.run_attempt}"
    for name, value in (("--operation-id", operation_id), ("--request-id", request_id)):
        if not _IDENTIFIER.fullmatch(value):
            parser.error(f"{name} must be a bounded safe identifier")

    try:
        descriptor_digest = _sha256_file(args.release_descriptor)
        _read_json(args.release_descriptor, "release descriptor")
        schema_contract_digest = _sha256_file(args.schema_contract)
        runtime_manifest_digest = _sha256_file(args.runtime_manifest)
        runtime_manifest = _read_json(args.runtime_manifest, "runtime manifest")
        if runtime_manifest.get("manifest_version", 1) < 2:
            raise ReceiptError(
                "runtime manifest does not carry release identity"
            )
        if runtime_manifest.get("source_revision") != args.source_revision:
            raise ReceiptError(
                "runtime manifest source revision does not match receipt"
            )
        schema_identity_value = runtime_manifest.get("schema_identity")
        if not isinstance(schema_identity_value, dict):
            raise ReceiptError("runtime manifest is missing schema identity")
        if set(schema_identity_value) != set(_SCHEMA_FIELDS):
            raise ReceiptError("runtime manifest schema identity fields are incomplete")
        if any(
            not isinstance(schema_identity_value.get(field), str)
            for field in _SCHEMA_FIELDS
        ):
            raise ReceiptError("runtime manifest schema identity is invalid")
        if not _IDENTIFIER.fullmatch(schema_identity_value["schema_head"]):
            raise ReceiptError("runtime manifest schema head is invalid")
        if not _IDENTIFIER.fullmatch(schema_identity_value["schema_contract_format"]):
            raise ReceiptError("runtime manifest schema contract format is invalid")
        if schema_identity_value["schema_contract_format"] != _SCHEMA_CONTRACT_FORMAT:
            raise ReceiptError("runtime manifest schema contract format is unsupported")
        if any(
            not _SHA256.fullmatch(schema_identity_value[field])
            for field in _SCHEMA_FIELDS
            if field.endswith("_sha256")
        ):
            raise ReceiptError("runtime manifest schema identity hash is invalid")
        calculated_schema_id = _sha256_bytes(
            _canonical_json_bytes(
                {"schema": "argus.schema-identity.v1", **schema_identity_value}
            )
        )
        if runtime_manifest.get("schema_id") not in (None, calculated_schema_id):
            raise ReceiptError("runtime manifest schema identity hash is invalid")
        if runtime_manifest.get("release_descriptor_digest") != descriptor_digest:
            raise ReceiptError(
                "release descriptor digest does not match runtime manifest"
            )
        if runtime_manifest.get("schema_contract_sha256") != schema_contract_digest:
            raise ReceiptError(
                "schema contract digest does not match runtime manifest"
            )
        contract_identity = _schema_identity_from_contract(
            args.schema_contract,
            expected_head=schema_identity_value["schema_head"],
        )
        for field in (
            "schema_head",
            "migration_chain_sha256",
            "canonical_postgresql_schema_sha256",
            "schema_contract_format",
        ):
            if contract_identity[field] != schema_identity_value[field]:
                raise ReceiptError(
                    "schema identity does not match schema contract"
                )
    except (ReceiptError, TypeError, ValueError) as error:
        parser.error(str(error))

    schema_identity = dict(runtime_manifest["schema_identity"])
    schema_identity_id = _sha256_bytes(
        _canonical_json_bytes({"schema": "argus.schema-identity.v1", **schema_identity})
    )
    release_identity = {
        "source_revision": args.source_revision,
        "image_digest": args.digest,
        "release_descriptor_digest": descriptor_digest,
        "runtime_manifest_digest": runtime_manifest_digest,
    }
    release_id = _sha256_bytes(
        _canonical_json_bytes(
            {
                "schema": "argus.release-identity.v1",
                "source_revision": args.source_revision,
                "image_digest": args.digest,
                "release_descriptor_digest": descriptor_digest,
            }
        )
    )
    payload = {
        "schema": "argus.release-receipt.v2",
        "schema_version": 2,
        "image": args.image,
        "image_ref": f"{args.image}@{args.digest}",
        "digest": args.digest,
        "source_revision": args.source_revision,
        "release_identity": release_identity,
        "release_id": release_id,
        "schema_identity": schema_identity,
        "schema_id": schema_identity_id,
        "release_descriptor_digest": descriptor_digest,
        "runtime_manifest_digest": runtime_manifest_digest,
        "schema_contract_sha256": schema_contract_digest,
        "operation_id": operation_id,
        "request_id": request_id,
        "build": {
            "repository": args.repository,
            "workflow": args.workflow,
            "run_id": args.run_id,
            "run_attempt": args.run_attempt,
        },
    }
    payload["receipt_identity"] = _sha256_bytes(_canonical_json_bytes(payload))
    try:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(args.output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        parser.error(f"receipt output already exists: {args.output}")
    except OSError:
        parser.error(f"cannot write receipt: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
