"""Evidence bundles are deterministic, complete, and secret-free."""

from __future__ import annotations

import json

import pytest

from argus.scorecard.bundle import BundleError, verify_bundle, write_bundle
from argus.scorecard.stability import HARD_GATES, evaluate_stability


def _stability():
    profile = {
        gate: {"status": "pass", "evidence": {"fixture": gate}} for gate in HARD_GATES
    }
    return evaluate_stability(
        {"free": profile, "budgeted": profile}, architecture_exceptions=()
    )


def _payload():
    return {
        "run_id": "hermetic-001",
        "generation": "generation-001",
        "candidate_identity": {"commit": "9c209b2", "generation": "generation-001"},
        "corpus": {"version": "v1", "sha256": "a" * 64},
        "provider_snapshot": {"providers": [], "profile": "free"},
        "surface_equivalence": {"status": "pass", "cases": ["success", "timeout"]},
        "artifacts": {"hermetic-summary.json": {"outcome": "success"}},
    }


def test_bundle_writer_emits_declared_tree_and_verified_checksums(tmp_path):
    output = tmp_path / "bundle"

    write_bundle(output, lane="hermetic", stability=_stability(), payload=_payload())
    manifest = verify_bundle(output)

    assert manifest["lane"] == "hermetic"
    assert (output / "identities" / "candidate.json").is_file()
    assert (output / "corpus" / "manifest.json").is_file()
    assert (output / "stability" / "gates.json").is_file()
    assert (output / "artifacts" / "hermetic-summary.json").is_file()
    assert (output / "checksums.sha256").is_file()


def test_bundle_writer_never_overwrites_an_existing_evidence_directory(tmp_path):
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "kept.txt").write_text("immutable")

    with pytest.raises(BundleError, match="already exists"):
        write_bundle(
            output, lane="hermetic", stability=_stability(), payload=_payload()
        )

    assert (output / "kept.txt").read_text() == "immutable"


def test_bundle_verifier_rejects_tampering_missing_sections_and_identity_mismatch(
    tmp_path,
):
    output = tmp_path / "bundle"
    write_bundle(output, lane="hermetic", stability=_stability(), payload=_payload())
    (output / "stability" / "gates.json").write_text("{}\n")

    with pytest.raises(BundleError, match="checksum"):
        verify_bundle(output)

    output = tmp_path / "missing"
    write_bundle(output, lane="hermetic", stability=_stability(), payload=_payload())
    (output / "corpus" / "manifest.json").unlink()
    with pytest.raises(BundleError, match="required"):
        verify_bundle(output)


def test_bundle_rejects_cross_generation_and_secret_bearing_payloads(tmp_path):
    cross_generation = _payload()
    cross_generation["baseline_identity"] = {
        "commit": "baseline",
        "generation": "other-generation",
    }
    with pytest.raises(BundleError, match="generation"):
        write_bundle(
            tmp_path / "cross",
            lane="competitive",
            stability=_stability(),
            payload=cross_generation,
        )

    secret = _payload()
    secret["artifacts"] = {"raw.json": {"Authorization": "Bearer forbidden"}}
    with pytest.raises(BundleError, match="forbidden"):
        write_bundle(
            tmp_path / "secret", lane="hermetic", stability=_stability(), payload=secret
        )

    sentinel = _payload()
    sentinel["artifacts"] = {"sentinel.json": {"value": "__ARGUS_SECRET_SENTINEL__"}}
    with pytest.raises(BundleError, match="forbidden"):
        write_bundle(
            tmp_path / "sentinel",
            lane="hermetic",
            stability=_stability(),
            payload=sentinel,
        )


def test_bundle_verifier_rejects_mismatched_manifest_identity_hash(tmp_path):
    output = tmp_path / "bundle"
    write_bundle(output, lane="hermetic", stability=_stability(), payload=_payload())
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["identities"]["candidate"]["sha256"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")

    with pytest.raises(BundleError, match="checksum|identity"):
        verify_bundle(output)
