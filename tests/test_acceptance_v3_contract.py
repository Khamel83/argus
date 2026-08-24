"""Unit contracts for the isolated v3 acceptance evidence harness."""

from __future__ import annotations

from hashlib import sha256
import json
import os
import pytest

import argus.acceptance_v3.contract as contract_module

from argus.acceptance_v3.contract import (
    CYCLE_ID,
    FROZEN_ENDPOINT_PATHS,
    FROZEN_REQUEST_KEYS,
    GLOBAL_GUARD_PATH,
    PROFILE,
    SCHEMA,
    ContractError,
    build_execution_contract,
    canonical_bytes,
    canonical_hash,
    create_evidence_root,
    create_global_guard,
    validate_execution_contract,
    bind_returned_run,
    write_immutable_json,
)


@pytest.fixture(autouse=True)
def _isolated_trusted_state(tmp_path, monkeypatch):
    """Keep filesystem contract tests portable without changing production paths."""

    trusted = tmp_path / "state"
    trusted.mkdir(mode=0o700)
    monkeypatch.setattr(contract_module, "TRUSTED_EVIDENCE_PARENT", trusted)


def _contract() -> dict[str, object]:
    root = (
        contract_module.TRUSTED_EVIDENCE_PARENT
        / "argus-acceptance-v3-contract-root"
    )
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return build_execution_contract(
        request={
            "topic": "topic",
            "official_url": None,
            "max_research_pages": 17,
            "research_targets": [],
            "free_only": True,
            "caller": "tonight-acceptance-v3",
        },
        start_body={
            "topic": "topic",
            "official_url": None,
            "max_research_pages": 17,
            "research_targets": [],
            "free_only": True,
            "caller": "tonight-acceptance-v3",
        },
        endpoints={
            "start": "/api/workflows/build-research-pack/start",
            "status": "/api/workflows/{run_id}/status",
            "report": "/api/workflows/{run_id}/artifacts/report",
            "manifest": "/api/workflows/{run_id}/artifacts/manifest",
        },
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
            "version": "acceptance-v3-evaluator-1",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "sampling": "no_override",
            "web_enabled": False,
            "tools_enabled": False,
            "memory_enabled": False,
            "provider_enabled": False,
            "database_enabled": False,
            "spend_authority": "none",
            "prompt_sha256": "4" * 64,
            "prompt_bytes_sha256": "4" * 64,
            "settings_sha256": "5" * 64,
            "run_receipt_sha256": "6" * 64,
        },
        snapshots={
            "pre_canary_sha256": "7" * 64,
            "topology_sha256": "8" * 64,
            "provider_sha256": "9" * 64,
            "extractor_sha256": "a" * 64,
            "corpus_sha256": "b" * 64,
            "authority_sha256": "c" * 64,
            "observed_at": "2026-08-10T20:00:00Z",
        },
        canary={
            "query_sha256": "8" * 64,
            "search_body_sha256": "9" * 64,
            "maya_body_sha256": "b" * 64,
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
        topology={
            "egress": "unknown",
            "machine": "fixture",
            "node_role": "primary",
            "residential_policy": "fallback",
        },
        policy={
            "profile": "free",
            "free_only": True,
            "caller_identity": "mac-agents",
            "tier_cap": 1,
            "you_contents_enabled": False,
            "eligible_providers": ["github"],
            "eligible_extractors": ["trafilatura"],
            "policy_skipped": ["jina", "valyu", "firecrawl", "you"],
            "diagnostics_complete": True,
        },
        corpus={
            "version": "v3",
            "request_sha256": "5" * 64,
            "corpus_sha256": "6" * 64,
            "target_count": 5,
            "requirement_count": 15,
            "external_page_budget": 17,
        },
        authority={
            "evidence_authority": True,
            "database_authority": "postgresql",
            "sqlite_fallback": False,
            "observed_at": "2026-08-10T20:00:00Z",
            "snapshot_sha256": "d" * 64,
        },
        evidence_root=str(root),
    )


def test_compact_canonical_json_preserves_null_and_absent_and_hashes_exact_bytes():
    value = {"z": "é", "null": None, "nested": {"b": 2, "a": 1}}
    expected = '{"nested":{"a":1,"b":2},"null":null,"z":"é"}'.encode()
    assert canonical_bytes(value) == expected
    assert canonical_hash(value) == sha256(expected).hexdigest()
    assert canonical_hash({"x": None}) != canonical_hash({})


def test_evidence_root_is_private_non_symlink_and_rejects_existing_symlink(tmp_path):
    root = create_evidence_root(
        contract_module.TRUSTED_EVIDENCE_PARENT,
        name=f"argus-acceptance-v3-test-{tmp_path.parent.name}-{tmp_path.name}",
    )
    assert root.is_dir()
    assert not root.is_symlink()
    assert os.stat(root).st_mode & 0o777 == 0o700
    link = (
        contract_module.TRUSTED_EVIDENCE_PARENT
        / f"argus-acceptance-v3-link-{tmp_path.parent.name}-{tmp_path.name}"
    )
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ContractError, match="symlink"):
        create_evidence_root(contract_module.TRUSTED_EVIDENCE_PARENT, name=link.name)


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


def test_execution_contract_rejects_empty_endpoints_extra_snapshot_fields_and_unsafe_roots():
    contract = _contract()
    with pytest.raises(ContractError):
        validate_execution_contract(
            {**contract, "endpoints": {}, "endpoint_hashes": {}}
        )
    with pytest.raises(ContractError):
        validate_execution_contract(
            {
                **contract,
                "snapshots": {"pre_canary_sha256": "7" * 64, "unexpected": "x"},
            }
        )
    with pytest.raises(ContractError):
        validate_execution_contract({**contract, "evidence_root": "relative/root"})
    with pytest.raises(ContractError, match="trusted"):
        validate_execution_contract(
            {**contract, "evidence_root": "/private/tmp/argus-untrusted"}
        )
    with pytest.raises(ContractError):
        validate_execution_contract({**contract, "guard_path": "relative/guard.json"})
    with pytest.raises(ContractError, match="frozen"):
        validate_execution_contract(
            {
                **contract,
                "guard_path": "/Users/macmini/.local/state/./argus-tonight-final-score-v3-started.json",
            }
        )


def test_execution_contract_binds_the_frozen_endpoint_set_and_request_keys():
    contract = _contract()
    assert set(contract["request"]) == FROZEN_REQUEST_KEYS
    assert set(contract["start_body"]) == FROZEN_REQUEST_KEYS
    assert {
        endpoint["path"] for endpoint in contract["endpoints"].values()
    } == FROZEN_ENDPOINT_PATHS
    contract["endpoints"]["legacy"] = {
        "path": "/api/workflows/build-research-pack",
        "request_sha256": "a" * 64,
        "pagination_sha256": "b" * 64,
        "envelope_normalization_sha256": "c" * 64,
    }
    contract["endpoint_hashes"]["legacy"] = canonical_hash(
        contract["endpoints"]["legacy"]
    )
    with pytest.raises(ContractError, match="frozen"):
        validate_execution_contract(contract)


def test_evidence_root_rejects_lexical_parent_traversal():
    parent = contract_module.TRUSTED_EVIDENCE_PARENT / "nested" / ".."
    with pytest.raises(ContractError, match="trusted"):
        create_evidence_root(parent, name="argus-acceptance-v3-traversal")
