#!/usr/bin/env python3
"""Terminal shell and batch runner for the throwaway issue #65 prototype."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from model import validate_envelope


ROOT = Path(__file__).resolve().parent
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def load_inputs() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    schema = json.loads((ROOT / "envelope.schema.json").read_text())
    vectors = json.loads((ROOT / "vectors.json").read_text())
    return schema, vectors


def summary(vector: dict[str, Any]) -> str:
    attempts = vector["provider_attempts"]
    origin_attempts = vector["origin_provider_attempts"]
    extraction_steps = sum(
        len(item["steps"]) for item in vector["extractions"]
    )
    final = vector["final"]
    lines = [
        f"{BOLD}scenario{RESET}: {vector['scenario']}",
        f"{BOLD}outcome{RESET}: {final['outcome']}",
        f"{BOLD}run{RESET}: {vector['request']['run_id']}",
        f"{BOLD}plan/cache{RESET}: {vector['plan']['plan_id']} / "
        f"{vector['cache']['decision']}:{vector['cache']['reason']}",
        f"{BOLD}providers{RESET}: planned={len(vector['readiness'])} "
        f"current_attempts={len(attempts)} origin_attempts={len(origin_attempts)} "
        f"new_calls={vector['accounting']['provider_call_count']}",
        f"{BOLD}results{RESET}: ranked={len(vector['fusion']['clusters'])} "
        f"visible={len(final['result_refs'])} "
        f"diagnostic={len(final['diagnostic_result_refs'])}",
        f"{BOLD}extraction{RESET}: runs={len(vector['extractions'])} "
        f"steps={extraction_steps} citations={len(final['citations'])}",
        f"{BOLD}spend{RESET}: actual_usd={vector['accounting']['actual_usd']} "
        f"origin_actual_usd="
        f"{(vector['cache']['origin_spend'] or {}).get('actual_usd', 'none')} "
        f"state={vector['accounting']['reconciliation']}",
        f"{BOLD}latency{RESET}: wall={vector['accounting']['operation_latency_ms']}ms "
        f"provider_sum={vector['accounting']['provider_attempt_latency_ms']}ms "
        f"extractor_sum={vector['accounting']['extractor_attempt_latency_ms']}ms",
        f"{BOLD}acceptance{RESET}: {vector['acceptance']['status']} "
        f"receipt={vector['acceptance']['receipt_ref']}",
        f"{BOLD}delivery{RESET}: synthesis={final['synthesis_allowed']} "
        f"delivery={final['delivery_allowed']}",
        f"{BOLD}visible labels{RESET}: {', '.join(final['visible_labels'])}",
    ]
    return "\n".join(lines)


def corrupted_cases(
    vectors: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], str]]:
    by_name = {vector["scenario"]: vector for vector in vectors}
    cases: list[tuple[str, dict[str, Any], str]] = []

    dangling = copy.deepcopy(by_name["success"])
    dangling["final"]["result_refs"][0] = "cluster-missing"
    cases.append(("dangling final result ref", dangling, "dangling result ref"))

    stale = copy.deepcopy(by_name["stale_cache"])
    stale["cache"]["served"] = True
    cases.append(("stale cache served", stale, "only a cache hit may be served"))

    no_provider = copy.deepcopy(by_name["no_provider"])
    no_provider["final"]["outcome"] = "empty"
    no_provider["composition"]["composite_outcome"] = "empty"
    cases.append(
        ("no-provider mislabeled empty", no_provider, "empty requires")
    )

    citation = copy.deepcopy(by_name["success"])
    cited_id = citation["final"]["citations"][0]["artifact_ref"]
    for extraction in citation["extractions"]:
        artifact = extraction["artifact"]
        if artifact is not None and artifact["artifact_id"] == cited_id:
            artifact["citation_eligible"] = False
    cases.append(("ineligible artifact cited", citation, "cites ineligible artifact"))

    cost = copy.deepcopy(by_name["success"])
    cost["accounting"]["actual_usd"] = "1.000000000"
    cases.append(("spend mismatch", cost, "spend does not reconcile"))

    persistence = copy.deepcopy(by_name["persistence_failure"])
    persistence["acceptance"]["receipt_ref"] = "receipt-fabricated"
    cases.append(
        (
            "fabricated persistence receipt",
            persistence,
            "failed acceptance cannot fabricate",
        )
    )

    rrf = copy.deepcopy(by_name["success"])
    rrf["fusion"]["clusters"][0]["contributions"][0]["denominator"] += 1
    cases.append(("incorrect exact RRF input", rrf, "incorrect exact RRF"))

    fallback = copy.deepcopy(by_name["fallback_success"])
    fallback["extractions"][0]["rejection"] = {
        "rejection_id": "reject-fabricated",
        "code": "parse_error",
        "recommended_action": "terminal",
        "provider": "trafilatura",
        "attempt_count": 2,
        "last_status": "content",
        "total_attempt_latency_ms": 82
    }
    cases.append(
        (
            "fallback success keeps final rejection",
            fallback,
            "success retains final rejection",
        )
    )

    disposition = copy.deepcopy(by_name["partial_failure"])
    disposition["composition"]["links"][0]["artifact_disposition"] = "usable"
    cases.append(
        (
            "link lies about artifact disposition",
            disposition,
            "artifact disposition differs",
        )
    )

    cross_cluster = copy.deepcopy(by_name["success"])
    cross_cluster["final"]["citations"][0]["cluster_ref"] = "cluster-success-b"
    cases.append(
        (
            "citation crosses cluster boundary",
            cross_cluster,
            "artifact does not belong to cluster",
        )
    )

    selected = copy.deepcopy(by_name["success"])
    selected["extractions"][0]["selected_extractor"] = "playwright"
    cases.append(
        (
            "selected extractor did not produce artifact",
            selected,
            "selected extractor does not produce",
        )
    )

    ranking_policy = copy.deepcopy(by_name["success"])
    ranking_policy["fusion"]["ranking_policy"] = "ranking-v999"
    cases.append(
        (
            "fusion uses wrong ranking policy",
            ranking_policy,
            "ranking policy differs",
        )
    )

    reordered = copy.deepcopy(by_name["success"])
    reordered["fusion"]["clusters"][0]["base_rank"] = 1
    reordered["fusion"]["clusters"][1]["base_rank"] = 0
    cases.append(
        (
            "exact RRF order is reversed",
            reordered,
            "base ranking does not follow",
        )
    )

    caller_projection = copy.deepcopy(by_name["success"])
    caller_projection["final"]["caller_result"]["results"][0]["title"] = "Wrong"
    cases.append(
        (
            "caller output diverges from accepted cluster",
            caller_projection,
            "caller result projection differs",
        )
    )

    hit = copy.deepcopy(by_name["eligible_cache_hit"])
    hit["origin_provider_attempts"] = []
    cases.append(
        (
            "cache hit drops origin attempts",
            hit,
            "cache hit must be served from origin attempts",
        )
    )

    origin_spend = copy.deepcopy(by_name["eligible_cache_hit"])
    origin_spend["cache"]["origin_spend"]["actual_usd"] = "0.002000000"
    cases.append(
        (
            "cache hit misstates origin spend",
            origin_spend,
            "cache origin spend does not reconcile",
        )
    )
    return cases


def run_all(schema: dict[str, Any], vectors: list[dict[str, Any]]) -> int:
    failed = False
    seen_scenarios: set[str] = set()
    for vector in vectors:
        name = vector["scenario"]
        violations = validate_envelope(vector, schema)
        if name in seen_scenarios:
            violations.append("duplicate scenario name")
        seen_scenarios.add(name)
        if violations:
            failed = True
            print(f"FAIL vector {name}")
            for violation in violations:
                print(f"  - {violation}")
        else:
            print(f"PASS vector {name}: {vector['final']['outcome']}")

    for name, corrupted, expected in corrupted_cases(vectors):
        violations = validate_envelope(corrupted, schema)
        if any(expected in violation for violation in violations):
            print(f"PASS fail-closed {name}: rejected ({len(violations)} invariant(s))")
        else:
            failed = True
            print(
                f"FAIL fail-closed {name}: expected invariant not observed: "
                f"{expected!r}"
            )
            for violation in violations:
                print(f"  - {violation}")

    required = {
        "success",
        "partial_failure",
        "fallback_success",
        "stale_cache",
        "no_provider",
        "rejected_extraction",
        "persistence_failure",
        "eligible_cache_hit",
    }
    missing = required - seen_scenarios
    if missing:
        failed = True
        print(f"FAIL missing required scenarios: {sorted(missing)}")

    if failed:
        return 1
    print(
        f"\nPASS prototype: {len(vectors)} vectors and "
        f"{len(corrupted_cases(vectors))} fail-closed mutations"
    )
    return 0


def interactive(schema: dict[str, Any], vectors: list[dict[str, Any]]) -> int:
    index = 0
    show_json = False
    while True:
        vector = vectors[index]
        violations = validate_envelope(vector, schema)
        print("\033[2J\033[H", end="")
        print(f"{BOLD}Argus evidence-envelope prototype{RESET}")
        print(f"{DIM}{index + 1}/{len(vectors)} — fixture only, no I/O{RESET}\n")
        print(summary(vector))
        print(f"\n{BOLD}validation{RESET}: {'PASS' if not violations else 'FAIL'}")
        for violation in violations:
            print(f"  - {violation}")
        if show_json:
            print(f"\n{BOLD}complete envelope{RESET}")
            print(json.dumps(vector, indent=2, sort_keys=True))
        print(
            f"\n{BOLD}[n]{RESET} {DIM}next{RESET}  "
            f"{BOLD}[p]{RESET} {DIM}previous{RESET}  "
            f"{BOLD}[j]{RESET} {DIM}toggle complete JSON{RESET}  "
            f"{BOLD}[q]{RESET} {DIM}quit{RESET}"
        )
        choice = input("> ").strip().lower()
        if choice == "q":
            return 0
        if choice == "n":
            index = (index + 1) % len(vectors)
        elif choice == "p":
            index = (index - 1) % len(vectors)
        elif choice == "j":
            show_json = not show_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="validate all positive vectors and fail-closed corruptions",
    )
    parser.add_argument("--scenario", help="print and validate one scenario")
    args = parser.parse_args()
    schema, vectors = load_inputs()

    if args.all or (not sys.stdin.isatty() and args.scenario is None):
        return run_all(schema, vectors)
    if args.scenario is not None:
        matches = [item for item in vectors if item["scenario"] == args.scenario]
        if not matches:
            print(f"unknown scenario: {args.scenario}", file=sys.stderr)
            return 2
        vector = matches[0]
        print(json.dumps(vector, indent=2, sort_keys=True))
        violations = validate_envelope(vector, schema)
        for violation in violations:
            print(f"ERROR: {violation}", file=sys.stderr)
        return 1 if violations else 0
    return interactive(schema, vectors)


if __name__ == "__main__":
    raise SystemExit(main())
