"""Evidence bundles are deterministic, complete, and secret-free."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json

import pytest

from argus.scorecard.bundle import (
    BundleError,
    _validate_competitive_documents,
    _validate_stability_document,
    derive_generation,
    verify_bundle,
    write_bundle,
)
from argus.scorecard.corpus import COMPETITIVE_CASE_MODES
from argus.scorecard.stability import HARD_GATES, evaluate_stability


def _stability():
    profile = {
        gate: {
            "status": "pass",
            "evidence": {
                "schema": "normalized-gate-evidence-v2",
                "fixture_id": gate,
                "check": {"kind": gate, "passed": True, "observation_count": 1},
            },
        }
        for gate in HARD_GATES
    }
    return evaluate_stability(
        {"free": profile, "budgeted": profile}, architecture_exceptions=()
    )


def _payload():
    provider_snapshot = {
        "schema": "normalized-provider-snapshot-v1",
        "profile": "hermetic",
        "providers": [],
    }
    provider_hash = sha256(
        (
            json.dumps(provider_snapshot, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    ).hexdigest()
    dimensions = {
        "corpus_hashes": {
            "corpus.json": "a" * 64,
            "stability-evidence.json": "b" * 64,
        },
        "evaluator": {
            "model": "frozen-fixture-evaluator-v1",
            "prompt_sha256": "c" * 64,
            "settings_sha256": "d" * 64,
        },
        "topology": {"egress": "hermetic", "machine": "ci-fixture"},
        "profile": "hermetic",
        "provider_snapshot_sha256": provider_hash,
        "sanitized_config_sha256": "f" * 64,
    }
    generation = derive_generation(dimensions)
    return {
        "run_id": "hermetic-001",
        "generation": generation,
        "candidate_identity": {
            "generation": generation,
            "commit": "9c209b2",
            "image_digest": None,
            "sanitized_config_sha256": "f" * 64,
            "dimensions": dimensions,
            "started_at": "2026-07-29T00:00:00Z",
            "finished_at": "2026-07-29T00:00:01Z",
        },
        "corpus": {
            "version": "v1",
            "hashes": dimensions["corpus_hashes"],
            "hermetic_search_case_ids": ["discovery-01"],
            "hermetic_extraction_case_ids": ["static"],
            "competitive_case_ids": ["discovery-01", "primary-docs"],
            "dimensions": dimensions,
            "generation": generation,
        },
        "provider_snapshot": provider_snapshot,
        "surface_equivalence": {
            "schema": "surface-equivalence-v1",
            "status": "pass",
            "cases": [
                {
                    "case_id": "success",
                    "outcome": "success",
                    "http_status": 200,
                    "mcp_is_error": False,
                    "cli_exit": 0,
                    "python_error": False,
                },
                {
                    "case_id": "timeout",
                    "outcome": "timeout",
                    "http_status": 504,
                    "mcp_is_error": True,
                    "cli_exit": 1,
                    "python_error": True,
                },
            ],
        },
        "timing_receipts": {
            "schema": "normalized-timing-receipts-v1",
            "operations": [
                {
                    "operation_id": "fixture-evaluation",
                    "wall_ms": 1,
                    "component_ms": 1,
                    "timeout_source": "none",
                    "cache_ms": 0,
                }
            ],
        },
        "persistence_receipts": {
            "schema": "normalized-persistence-receipts-v1",
            "status": "not_applicable",
            "reason": "hermetic fixture evaluation performs no persistence",
            "receipts": [],
        },
        "artifacts": {
            "hermetic-summary.json": {
                "schema": "hermetic-summary-v1",
                "search_cases": 1,
                "extraction_cases": 1,
                "provider_execution": "none",
                "stability_verdict": "stable",
            },
            "searches/discovery-01.json": {
                "schema": "normalized-search-fixture-v1",
                "case_id": "discovery-01",
                "mode": "discovery",
                "profile": "hermetic",
                "input": {"query": "fixture", "raw_fixture_id": "discovery-01"},
                "actual": {
                    "outcome": "success",
                    "result_count": 1,
                    "domain_count": 1,
                    "provenance_complete": True,
                },
                "expected": {
                    "outcome": "success",
                    "result_count": 1,
                    "domain_count": 1,
                    "provenance_complete": True,
                },
                "matched": True,
            },
            "extractions/static.json": {
                "schema": "normalized-extraction-fixture-v1",
                "case_id": "static",
                "profile": "hermetic",
                "input": {
                    "raw_fixture_id": "static",
                    "content_type": "text/html",
                },
                "actual": {
                    "outcome": "success",
                    "quality": "passing",
                    "complete": True,
                    "provenance_complete": True,
                },
                "expected": {
                    "outcome": "success",
                    "quality": "passing",
                    "complete": True,
                    "provenance_complete": True,
                },
                "matched": True,
            },
        },
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


def test_competitive_bundle_accepts_all_normalized_live_case_artifacts(tmp_path):
    payload = _payload()
    generation = payload["generation"]
    payload["baseline_identity"] = {
        **payload["candidate_identity"],
        "commit": "8" * 40,
        "generation": generation,
    }
    payload["candidate_identity"]["commit"] = "9" * 40
    payload["corpus"]["hermetic_search_case_ids"] = [
        case_id
        for case_id, mode in COMPETITIVE_CASE_MODES.items()
        if mode != "extraction"
    ]
    payload["corpus"]["competitive_case_ids"] = list(COMPETITIVE_CASE_MODES)
    payload["persistence_receipts"] = {
        "schema": "normalized-persistence-receipts-v1",
        "status": "accepted",
        "reason": "canonical HTTP evidence persisted",
        "receipts": [
            {
                "operation_id": "competitive-run",
                "repository": "postgresql",
                "durable_id": "receipt-001",
                "status": "accepted",
            }
        ],
    }
    pairs = [
        {
            "pair_id": case_id,
            "mode": mode,
            "forward": "tie",
            "reverse": "tie",
            "classification": "tie",
        }
        for case_id, mode in COMPETITIVE_CASE_MODES.items()
    ]
    payload["competitive"] = {
        "deterministic_metrics": {
            "schema": "competitive-deterministic-metrics-v1",
            "consistent_pairs": 28,
            "decisive_pairs": 0,
            "candidate_wins": 0,
            "baseline_wins": 0,
            "p_value": None,
        },
        "blinded_comparisons": {
            "schema": "blinded-comparisons-v1",
            "pairs": pairs,
        },
        "verdict": {
            "schema": "competitive-verdict-v1",
            "verdict": "inconclusive",
            "reason": "fewer than 8 decisive pairs",
        },
    }
    search_result = {
        "url": "https://example.com/result",
        "title": "Result",
        "snippet": "Normalized evidence",
        "domain": "example.com",
        "provider": "duckduckgo",
        "score": 1.0,
        "egress": "residential",
        "machine": "homelab",
    }
    extraction = {
        "url": "https://example.com/article",
        "title": "Article",
        "text": "Normalized extracted evidence.",
        "author": None,
        "date": None,
        "word_count": 3,
        "egress": "residential",
        "machine": "homelab",
        "source_type": "trafilatura",
    }
    payload["artifacts"] = {
        **{
            f"searches/{case_id}.json": {
                "schema": "normalized-competitive-search-v1",
                "case_id": case_id,
                "mode": mode,
                "baseline": {"outcome": "success", "results": [search_result]},
                "candidate": {"outcome": "success", "results": [search_result]},
            }
            for case_id, mode in COMPETITIVE_CASE_MODES.items()
            if mode != "extraction"
        },
        **{
            f"extractions/{case_id}.json": {
                "schema": "normalized-competitive-extraction-v1",
                "case_id": case_id,
                "mode": "extraction",
                "baseline": {"outcome": "success", "content": extraction},
                "candidate": {"outcome": "success", "content": extraction},
            }
            for case_id, mode in COMPETITIVE_CASE_MODES.items()
            if mode == "extraction"
        },
    }

    output = tmp_path / "competitive"
    write_bundle(output, lane="competitive", stability=_stability(), payload=payload)
    manifest = verify_bundle(output)

    assert manifest["lane"] == "competitive"
    assert len(list((output / "artifacts" / "searches").glob("*.json"))) == 24
    assert len(list((output / "artifacts" / "extractions").glob("*.json"))) == 4

    failure_payload = json.loads(json.dumps(payload))
    failure_payload["artifacts"]["extractions/javascript-live.json"]["candidate"] = {
        "outcome": "extraction_failed",
        "content": None,
    }
    failure_output = tmp_path / "competitive-failure"
    write_bundle(
        failure_output,
        lane="competitive",
        stability=_stability(),
        payload=failure_payload,
    )
    assert verify_bundle(failure_output)["lane"] == "competitive"

    for outcome in (
        "invalid_request",
        "authentication_rejected",
        "policy_rejected",
        "persistence_failed",
    ):
        canonical_failure = json.loads(json.dumps(payload))
        canonical_failure["artifacts"]["searches/discovery-01.json"]["candidate"] = {
            "outcome": outcome,
            "results": [],
        }
        canonical_failure["artifacts"]["extractions/javascript-live.json"][
            "candidate"
        ] = {
            "outcome": outcome,
            "content": None,
        }
        canonical_output = tmp_path / f"competitive-{outcome}"
        write_bundle(
            canonical_output,
            lane="competitive",
            stability=_stability(),
            payload=canonical_failure,
        )
        assert verify_bundle(canonical_output)["lane"] == "competitive"

    contradictions = [
        (
            "searches/discovery-01.json",
            {"outcome": "success", "results": []},
        ),
        (
            "extractions/javascript-live.json",
            {
                "outcome": "extraction_failed",
                "content": {
                    "url": "https://example.com/article",
                    "title": "Article",
                    "text": "Contradictory content.",
                    "author": None,
                    "date": None,
                    "word_count": 2,
                    "egress": "residential",
                    "machine": "homelab",
                    "source_type": "trafilatura",
                },
            },
        ),
    ]
    for index, (artifact, evidence) in enumerate(contradictions):
        contradictory = json.loads(json.dumps(payload))
        contradictory["artifacts"][artifact]["candidate"] = evidence
        with pytest.raises(BundleError, match="outcome"):
            write_bundle(
                tmp_path / f"contradictory-{index}",
                lane="competitive",
                stability=_stability(),
                payload=contradictory,
            )


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
    with pytest.raises(BundleError, match="undeclared|closed|required"):
        verify_bundle(output)


def test_bundle_rejects_cross_generation_and_secret_bearing_payloads(tmp_path):
    cross_generation = _payload()
    cross_generation["baseline_identity"] = {
        **cross_generation["candidate_identity"],
        "generation": "0" * 64,
    }
    cross_generation["competitive"] = {
        "deterministic_metrics": {},
        "blinded_comparisons": {},
        "verdict": {"verdict": "inconclusive"},
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


def test_bundle_supports_corpus_linked_nested_search_and_extraction_artifacts(tmp_path):
    payload = _payload()

    write_bundle(
        tmp_path / "nested", lane="hermetic", stability=_stability(), payload=payload
    )

    assert (
        tmp_path / "nested" / "artifacts" / "searches" / "discovery-01.json"
    ).is_file()
    assert (tmp_path / "nested" / "artifacts" / "extractions" / "static.json").is_file()


@pytest.mark.parametrize(
    "artifact_path",
    (
        "/absolute.json",
        "../escape.json",
        "searches/../../escape.json",
        r"searches\escape.json",
        "./dot.json",
    ),
)
def test_bundle_rejects_noncanonical_artifact_paths_before_writing(
    tmp_path, artifact_path
):
    payload = _payload()
    payload["artifacts"][artifact_path] = {"outcome": "success"}
    output = tmp_path / "unsafe"

    with pytest.raises(BundleError, match="path"):
        write_bundle(output, lane="hermetic", stability=_stability(), payload=payload)

    assert not output.exists()


def test_bundle_verifier_rejects_symlinks_duplicate_checksums_and_extra_files(tmp_path):
    output = tmp_path / "bundle"
    write_bundle(output, lane="hermetic", stability=_stability(), payload=_payload())
    target = output / "artifacts" / "hermetic-summary.json"
    saved = tmp_path / "saved.json"
    saved.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(saved)

    with pytest.raises(BundleError, match="symlink"):
        verify_bundle(output)

    output = tmp_path / "duplicates"
    write_bundle(output, lane="hermetic", stability=_stability(), payload=_payload())
    checksums = output / "checksums.sha256"
    checksums.write_text(
        checksums.read_text() + checksums.read_text().splitlines()[0] + "\n"
    )
    with pytest.raises(BundleError, match="duplicate|closed"):
        verify_bundle(output)

    output = tmp_path / "extra"
    write_bundle(output, lane="hermetic", stability=_stability(), payload=_payload())
    (output / "undeclared.json").write_text("{}\n")
    with pytest.raises(BundleError, match="undeclared|closed"):
        verify_bundle(output)


@pytest.mark.parametrize(
    "native",
    (
        {"provider_response": {"ok": True}},
        {"raw_response": {"items": []}},
        {"cookies": []},
        {"auth_header": "Basic dXNlcjpwYXNz"},
        {"provider_key": "value"},
        {"credentials": {"user": "name"}},
        {"value": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"},
    ),
)
def test_bundle_rejects_native_and_sensitive_variants_before_any_io(tmp_path, native):
    payload = _payload()
    payload["surface_equivalence"] = {"status": "pass", "evidence": native}
    output = tmp_path / "unsafe"

    with pytest.raises(BundleError, match="forbidden|schema"):
        write_bundle(output, lane="hermetic", stability=_stability(), payload=payload)

    assert not output.exists()


def test_bundle_requires_closed_manifest_checksum_set_including_manifest(tmp_path):
    output = tmp_path / "bundle"
    write_bundle(output, lane="hermetic", stability=_stability(), payload=_payload())
    lines = (output / "checksums.sha256").read_text().splitlines()

    assert any(line.endswith("  manifest.json") for line in lines)
    manifest = verify_bundle(output)
    assert set(manifest["file_set"]) == {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() or path.is_symlink()
    }


def test_bundle_failure_leaves_no_final_or_private_staging_directory(tmp_path):
    payload = _payload()
    payload["candidate_identity"]["generation"] = "wrong"
    output = tmp_path / "bundle"

    with pytest.raises(BundleError):
        write_bundle(output, lane="hermetic", stability=_stability(), payload=payload)

    assert not output.exists()
    assert not list(tmp_path.glob(".bundle.staging-*"))


def test_bundle_recomputes_stability_and_rejects_fabricated_stable_claim():
    document = json.loads(json.dumps(asdict(_stability())))
    document["profiles"]["free"]["gates"]["authentication"]["status"] = "fail"
    document["profiles"]["free"]["gates"]["authentication"]["evidence"]["check"][
        "passed"
    ] = False

    with pytest.raises(BundleError, match="derivable"):
        _validate_stability_document(document, "stable")


@pytest.mark.parametrize("fabrication", ("classification", "count", "verdict"))
def test_bundle_recomputes_competitive_claims_from_raw_blinded_pairs(fabrication):
    pairs = [
        {
            "pair_id": pair_id,
            "mode": mode,
            "forward": "tie",
            "reverse": "tie",
            "classification": "tie",
        }
        for pair_id, mode in COMPETITIVE_CASE_MODES.items()
    ]
    metrics = {
        "schema": "competitive-deterministic-metrics-v1",
        "consistent_pairs": 28,
        "decisive_pairs": 0,
        "candidate_wins": 0,
        "baseline_wins": 0,
        "p_value": None,
    }
    verdict = {
        "schema": "competitive-verdict-v1",
        "verdict": "inconclusive",
        "reason": "fewer than 8 decisive pairs",
    }
    if fabrication == "classification":
        pairs[0]["classification"] = "candidate_win"
    elif fabrication == "count":
        metrics["candidate_wins"] = 1
    else:
        verdict["verdict"] = "competitive"

    with pytest.raises(BundleError, match="derivable|invalid"):
        _validate_competitive_documents(
            metrics,
            {"schema": "blinded-comparisons-v1", "pairs": pairs},
            verdict,
            list(COMPETITIVE_CASE_MODES),
            verdict["verdict"],
            _stability(),
        )


@pytest.mark.parametrize("hostile_key", ("apiKey", "api key"))
def test_bundle_secret_scanner_normalizes_camel_and_spaced_keys(tmp_path, hostile_key):
    payload = _payload()
    payload["provider_snapshot"][hostile_key] = "forbidden"

    with pytest.raises(BundleError, match="forbidden"):
        write_bundle(
            tmp_path / "secret-variant",
            lane="hermetic",
            stability=_stability(),
            payload=payload,
        )
