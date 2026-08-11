"""Acceptance v3 bundle, gates, score, and terminal-branch tests."""

from __future__ import annotations

import json
import pytest

from argus.acceptance_v3.bundle import (
    EIGHT_GATES,
    RUBRIC_CELLS,
    BundleError,
    calculate_score,
    evaluate_gates,
    minimum_evidence,
    verify_bundle,
    write_bundle,
)


def _gates(status: str = "PASS"):
    return {
        name: {"status": status, "reason": "fixture", "evidence": ["x.json"]}
        for name in EIGHT_GATES
    }


def _manifest(*, status="completed"):
    sections = {
        "artifact": "report.json",
        "claim_support": "claim-support.json",
        "synthesis": "report.json",
        "scoring": "score.json",
    }
    if status == "evaluator_not_run":
        sections = {
            "artifact": "report.json",
            "claim_support": "not_run",
            "synthesis": "not_run",
            "scoring": "not_run",
        }
    elif status != "completed":
        sections = {
            "artifact": "not_run",
            "claim_support": "not_run",
            "synthesis": "not_run",
            "scoring": "not_run",
        }
    return {
        "schema": "argus-acceptance-v3/free-targeted",
        "status": status,
        "sections": sections,
        "competitive_baseline": "not_applicable",
        "competitive_pair": "not_applicable",
    }


def _score():
    return {
        "status": "scored",
        "cells": {name: points for name, points in RUBRIC_CELLS},
        "total": 100,
        "evaluator": {"model": "gpt-5.6-sol", "prompt_sha256": "a" * 64},
    }


def _claim_support():
    return {
        "status": "scored",
        "requirements": [
            {
                "requirement_id": f"r{i}",
                "disposition": "supported",
                "reason": "fixture",
                "source_text_sha256": "a" * 64,
                "citation_id": f"S{i}",
                "citation_url": f"https://example{i}.com/source",
                "evaluator": {"model": "gpt-5.6-sol", "run_receipt_sha256": "b" * 64},
            }
            for i in range(15)
        ],
    }


def test_v3_has_exactly_eight_distinct_gates_and_six_frozen_cells():
    assert len(EIGHT_GATES) == 8
    assert len(set(EIGHT_GATES)) == 8
    assert sum(points for _, points in RUBRIC_CELLS) == 100
    assert {name for name, _ in RUBRIC_CELLS} == {
        "source_citation_integrity",
        "coverage_diversity",
        "factual_discipline",
        "decision_usefulness",
        "execution_delivery",
        "provenance_cost_truth",
    }


def test_gate_validator_passes_only_exact_complete_tuple():
    result = evaluate_gates(_gates())
    assert result["verdict"] == "PASS"
    assert result["passed"] == 8
    assert (
        evaluate_gates(
            {
                **_gates(),
                "delivery": {
                    "status": "PENDING",
                    "reason": "not dispatched",
                    "evidence": ["x.json"],
                },
            }
        )["verdict"]
        == "FAIL"
    )
    with pytest.raises(BundleError, match="exactly"):
        evaluate_gates({"only": {"status": "PASS"}})


def test_score_arithmetic_and_not_run_semantics():
    assert calculate_score(_score()) == 100
    partial = {"status": "scored", "cells": {name: 1 for name, _ in RUBRIC_CELLS}}
    assert calculate_score(partial) == 6
    with pytest.raises(BundleError, match="not_run"):
        calculate_score({"status": "not_run", "cells": None})


def test_minimum_evidence_recomputes_usable_source_floor_and_primary_floor():
    domains = [
        "one.example.com",
        "two.example.com",
        "three.example.net",
        "four.example.net",
        "five.example.org",
    ]
    sources = [
        {
            "url": f"https://{domains[i]}/a",
            "disposition": "usable",
            "primary": i < 2,
            "provider": "github",
            "extractor": "trafilatura",
            "egress": "unknown",
            "machine": "fixture",
            "source_type": "search",
        }
        for i in range(5)
    ]
    result = minimum_evidence(
        sources, required_requirements=15, covered_requirements=15
    )
    assert result["usable_sources"] == 5
    assert result["registrable_domains"] == 3
    assert result["primary_sources"] == 2
    with pytest.raises(BundleError, match="floor"):
        minimum_evidence(sources[:4], required_requirements=15, covered_requirements=15)


def test_bundle_checksums_all_other_files_and_verdict_is_derived(tmp_path):
    output = tmp_path / "bundle"
    payload = {
        "manifest": _manifest(),
        "gates": _gates(),
        "score": _score(),
        "claim_support": _claim_support(),
        "recovery": {"status": "not_applicable", "reason": "no changes"},
        "artifacts": {"report.json": b'{"ok":true}', "workflow.json": b"{}"},
    }
    write_bundle(output, payload)
    verified = verify_bundle(output)
    assert verified["verdict"] == "PASS"
    checksum = output / "checksums.sha256"
    assert checksum.exists()
    assert "manifest.json" in checksum.read_text()
    (output / "artifacts" / "report.json").write_bytes(b"tampered")
    with pytest.raises(BundleError, match="checksum"):
        verify_bundle(output)


def test_manifest_declares_exact_checksum_closed_file_set_and_raw_opaque_ids_are_rejected(
    tmp_path,
):
    output = tmp_path / "bundle"
    payload = {
        "manifest": _manifest(),
        "gates": _gates(),
        "score": _score(),
        "claim_support": _claim_support(),
        "recovery": {"status": "not_applicable", "reason": "no changes"},
        "artifacts": {"report.json": b'{"ok":true}'},
    }
    write_bundle(output, payload)
    manifest = json.loads((output / "manifest.json").read_text())
    checksums = {
        line.split("  ", 1)[1]
        for line in (output / "checksums.sha256").read_text().splitlines()
    }
    assert manifest["files"] == sorted(checksums) + ["checksums.sha256"]
    with pytest.raises(BundleError, match="opaque"):
        write_bundle(
            tmp_path / "opaque",
            {**payload, "artifacts": {"bad.json": b'{"run_id":"raw"}'}},
        )


@pytest.mark.parametrize(
    "status,expected",
    [
        ("pre_artifact_not_run", "not_run"),
        ("evaluator_not_run", "FAIL"),
        ("preflight_failed", "FAIL"),
        ("rollback_incomplete", "rollback_incomplete"),
    ],
)
def test_terminal_branches_emit_not_run_or_fail_without_fabricating_score(
    tmp_path, status, expected
):
    output = tmp_path / status
    payload = {
        "manifest": _manifest(status=status),
        "gates": _gates("FAIL"),
        "score": {"status": "not_run", "reason": status, "cells": None},
        "claim_support": {"status": "not_run", "reason": status, "requirements": None},
        "recovery": {"status": "not_applicable", "reason": status},
        "artifacts": {"status.json": json.dumps({"status": status}).encode()},
    }
    write_bundle(output, payload)
    assert verify_bundle(output)["verdict"] == expected
