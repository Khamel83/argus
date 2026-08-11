"""Unit contracts for the isolated v3 acceptance evidence harness."""

from __future__ import annotations

from hashlib import sha256
import json
import os
import pytest

from argus.acceptance_v3.contract import (
    CYCLE_ID,
    GLOBAL_GUARD_PATH,
    PROFILE,
    SCHEMA,
    ContractError,
    build_execution_contract,
    canonical_bytes,
    canonical_hash,
    create_evidence_root,
    create_global_guard,
    bind_returned_run,
    write_immutable_json,
)


def _contract() -> dict[str, object]:
    return build_execution_contract(
        request={"topic": "topic", "official_url": None},
        start_body={"topic": "topic", "official_url": None},
        endpoints={"start": "/api/workflows/build-research-pack/start"},
        candidate={
            "version": "1.6.4",
            "source_revision": "a" * 40,
            "image_digest": "sha256:" + "b" * 64,
            "deployment_identity": "deploy-a",
            "release_receipt_sha256": "c" * 64,
            "hermetic_bundle_sha256": "d" * 64,
            "live_config_sha256": "e" * 64,
            "promotion_receipt_sha256": "f" * 64,
            "runtime_manifest_sha256": "0" * 64,
        },
        rollback={
            "source_revision": "1" * 40,
            "image_digest": "sha256:" + "2" * 64,
            "deployment_identity": "rollback",
            "release_receipt_sha256": "3" * 64,
        },
        evaluator={
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "prompt_sha256": "4" * 64,
            "settings_sha256": "5" * 64,
            "run_receipt_sha256": "6" * 64,
        },
        snapshots={"pre_canary_sha256": "7" * 64},
        canary={
            "query_sha256": "8" * 64,
            "body_sha256": "9" * 64,
            "idempotency_key_sha256": "a" * 64,
        },
        artifact_hashes={
            key: str(index) * 64
            for index, key in enumerate(
                (
                    "spec_sha256",
                    "scorecard_sha256",
                    "synthesis_prompt_sha256",
                    "evaluator_sha256",
                    "harness_sha256",
                    "client_probe_sha256",
                ),
                start=1,
            )
        },
        negative_probe={
            "request_sha256": "1" * 64,
            "response_sha256": "2" * 64,
            "before_snapshot_sha256": "3" * 64,
            "after_snapshot_sha256": "4" * 64,
        },
        evidence_root="/private/evidence",
    )


def test_compact_canonical_json_preserves_null_and_absent_and_hashes_exact_bytes():
    value = {"z": "é", "null": None, "nested": {"b": 2, "a": 1}}
    expected = '{"nested":{"a":1,"b":2},"null":null,"z":"é"}'.encode()
    assert canonical_bytes(value) == expected
    assert canonical_hash(value) == sha256(expected).hexdigest()
    assert canonical_hash({"x": None}) != canonical_hash({})


def test_evidence_root_is_private_non_symlink_and_rejects_existing_symlink(tmp_path):
    root = create_evidence_root(tmp_path)
    assert root.is_dir()
    assert not root.is_symlink()
    assert os.stat(root).st_mode & 0o777 == 0o700
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ContractError, match="symlink"):
        create_evidence_root(tmp_path, name="link")


def test_o_excl_json_write_fsyncs_and_second_bind_cannot_replace(tmp_path):
    path = tmp_path / "guard.json"
    write_immutable_json(path, {"a": 1})
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(ContractError, match="exists"):
        write_immutable_json(path, {"a": 2})
    bound = tmp_path / "run.json"
    first = bind_returned_run(
        bound,
        run_id="run-1",
        kind="build-research-pack",
        topic="topic",
        request_sha256="a" * 64,
        body_sha256="b" * 64,
        dispatched_at="2026-08-10T20:00:00Z",
    )
    assert first["run_id"] == "run-1"
    with pytest.raises(ContractError, match="exists"):
        bind_returned_run(
            bound,
            run_id="run-2",
            kind="build-research-pack",
            topic="other",
            request_sha256="c" * 64,
            body_sha256="d" * 64,
            dispatched_at="2026-08-10T20:00:01Z",
        )
    assert json.loads(bound.read_text()) == first


def test_execution_contract_has_frozen_identity_and_guard(tmp_path):
    contract = _contract()
    assert contract["cycle_id"] == CYCLE_ID
    assert contract["profile"] == PROFILE
    assert contract["schema"] == SCHEMA
    assert contract["request"]["official_url"] is None  # type: ignore[index]
    path = tmp_path / "execution-contract.json"
    write_immutable_json(path, contract)
    guard = tmp_path / "guard.json"
    result = create_global_guard(guard, contract)
    assert result["execution_contract_sha256"] == canonical_hash(contract)
    assert GLOBAL_GUARD_PATH.endswith("argus-tonight-final-score-v3-started.json")


def test_execution_contract_rejects_missing_or_extra_identity_before_guard():
    contract = _contract()
    contract["candidate"] = {"version": "1.6.4"}  # type: ignore[index]
    with pytest.raises(ContractError):
        build_execution_contract(
            request={"topic": "topic"},
            start_body={"topic": "topic"},
            endpoints={},
            candidate=contract["candidate"],  # type: ignore[arg-type]
            rollback={},
            evaluator={},
            snapshots={},
            canary={},
            artifact_hashes={},
            negative_probe={},
        )
