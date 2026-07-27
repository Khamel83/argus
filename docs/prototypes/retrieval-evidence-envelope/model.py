"""Pure invariant checker for the throwaway issue #65 envelope prototype."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


SUCCESS_OUTCOMES = {"success", "degraded", "empty"}
FAILURE_OUTCOMES = {
    "invalid_request",
    "authentication_rejected",
    "policy_rejected",
    "timeout",
    "persistence_failed",
    "providers_failed",
    "extraction_failed",
    "unready",
}
FAILED_PROVIDER_OUTCOMES = {
    "invalid_request",
    "authentication_rejected",
    "policy_rejected",
    "rate_limited",
    "balance_exhausted",
    "timeout",
    "provider_unavailable",
    "parse_error",
}
FORBIDDEN_KEYS = {
    "authorization",
    "headers",
    "cookies",
    "credentials",
    "raw_payload",
    "raw_body",
    "raw_error",
    "provider_payload",
}
FORBIDDEN_TEXT = ("authorization: bearer", "bearer sk-", "api_key=", "token=")


def _duplicate(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _walk(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    walked = [(path, value)]
    if isinstance(value, dict):
        for key, child in value.items():
            walked.extend(_walk(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walked.extend(_walk(child, f"{path}[{index}]"))
    return walked


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_envelope(envelope: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Return every schema/reference/semantic violation without mutating input."""

    violations: list[str] = []
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for error in sorted(validator.iter_errors(envelope), key=lambda item: list(item.path)):
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in error.absolute_path
        )
        violations.append(f"{location}: {error.message}")
    if violations:
        return violations

    request = envelope["request"]
    plan = envelope["plan"]
    cache = envelope["cache"]
    readiness = envelope["readiness"]
    attempts = envelope["provider_attempts"]
    fusion = envelope["fusion"]
    extractions = envelope["extractions"]
    composition = envelope["composition"]
    accounting = envelope["accounting"]
    acceptance = envelope["acceptance"]
    final = envelope["final"]

    if request["mode"] != plan["intent"]:
        violations.append("request.mode must equal plan.intent")
    if plan["artifact_requirement_ref"] != composition["artifact_requirement_ref"]:
        violations.append("plan and composition artifact requirement refs differ")
    if cache["maximum_age_ms"] != plan["controls"]["freshness"]["maximum_cache_age_ms"]:
        violations.append("cache maximum age differs from the resolved plan")

    planned = plan["candidate_providers"]
    readiness_providers = [item["provider"] for item in readiness]
    if readiness_providers != planned:
        violations.append("readiness must cover candidate providers once, in plan order")

    attempt_ids = [item["attempt_id"] for item in attempts]
    if duplicates := _duplicate(attempt_ids):
        violations.append(f"duplicate provider attempt ids: {sorted(duplicates)}")
    if [item["ordinal"] for item in attempts] != list(range(len(attempts))):
        violations.append("provider attempt ordinals must be contiguous from zero")
    readiness_by_provider = {item["provider"]: item for item in readiness}
    request_time = _timestamp(request["received_at"])
    for item in readiness:
        if item["observed_at"] is None or item["valid_until"] is None:
            violations.append(f"{item['provider']} readiness lacks bounded time")
        elif not (
            _timestamp(item["observed_at"])
            <= request_time
            < _timestamp(item["valid_until"])
        ):
            violations.append(f"{item['provider']} readiness is not valid at request time")
    for attempt in attempts:
        provider = attempt["provider"]
        if provider not in readiness_by_provider:
            violations.append(f"{attempt['attempt_id']} provider is not in readiness")
        elif readiness_by_provider[provider]["decision"] != "eligible":
            violations.append(f"{attempt['attempt_id']} invoked an ineligible provider")
        if attempt["outcome"] == "success" and attempt["result_count"] == 0:
            violations.append(f"{attempt['attempt_id']} success has no results")
        if (
            attempt["outcome"] in FAILED_PROVIDER_OUTCOMES
            and attempt["result_count"] != 0
        ):
            violations.append(f"{attempt['attempt_id']} failed but reports results")
        if attempt["outcome"] == "empty" and attempt["result_count"] != 0:
            violations.append(f"{attempt['attempt_id']} empty reports results")
        freshness_requested = plan["controls"]["freshness"]["start"] is not None
        translation = attempt["applied_controls"]["freshness_translation"]
        if freshness_requested and translation == "not_requested":
            violations.append(f"{attempt['attempt_id']} omitted requested freshness")
        if not freshness_requested and translation != "not_requested":
            violations.append(f"{attempt['attempt_id']} invented freshness translation")

    if cache["new_provider_calls"] != len(attempts):
        violations.append("cache new_provider_calls must equal invoked provider attempts")
    if accounting["provider_call_count"] != len(attempts):
        violations.append("accounting provider_call_count must equal attempts")
    if cache["decision"] == "hit":
        if not cache["served"] or attempts:
            violations.append("cache hit must be served with zero provider attempts")
    elif cache["served"]:
        violations.append("only a cache hit may be served")
    if (
        cache["decision"] == "rejected"
        and cache["reason"] == "stale"
        and (
            cache["age_ms"] is None
            or cache["age_ms"] <= cache["maximum_age_ms"]
        )
    ):
        violations.append("stale cache rejection must exceed maximum age")
    if cache["origin_run_ref"] is None:
        if cache["origin_provider_refs"] or cache["origin_spend"] is not None:
            violations.append("cache without origin run has fabricated origin lineage")
    elif not cache["origin_provider_refs"] or cache["origin_spend"] is None:
        violations.append("cache origin run lacks provider/spend lineage")

    provider_latency = sum(item["latency_ms"] for item in attempts)
    if accounting["provider_attempt_latency_ms"] != provider_latency:
        violations.append("provider attempt latency total does not reconcile")
    actual_cost = sum(Decimal(item["spend"]["actual_usd"]) for item in attempts)
    reserved_cost = sum(Decimal(item["spend"]["reserved_usd"]) for item in attempts)

    attempt_by_id = {item["attempt_id"]: item for item in attempts}
    clusters = fusion["clusters"]
    cluster_ids = [item["cluster_id"] for item in clusters]
    if duplicates := _duplicate(cluster_ids):
        violations.append(f"duplicate cluster ids: {sorted(duplicates)}")
    if [item["base_rank"] for item in sorted(clusters, key=lambda row: row["base_rank"])] != list(
        range(len(clusters))
    ):
        violations.append("base ranks must be unique and contiguous")
    if [item["output_rank"] for item in sorted(clusters, key=lambda row: row["output_rank"])] != list(
        range(len(clusters))
    ):
        violations.append("output ranks must be unique and contiguous")
    for cluster in clusters:
        contributors: set[str] = set()
        for contribution in cluster["contributions"]:
            attempt = attempt_by_id.get(contribution["attempt_ref"])
            if attempt is None:
                violations.append(
                    f"{cluster['cluster_id']} has dangling attempt contribution"
                )
                continue
            if attempt["outcome"] != "success":
                violations.append(
                    f"{cluster['cluster_id']} cites a non-successful provider attempt"
                )
            if attempt["provider"] != contribution["provider"]:
                violations.append(
                    f"{cluster['cluster_id']} contribution provider does not match attempt"
                )
            if contribution["provider"] in contributors:
                violations.append(
                    f"{cluster['cluster_id']} has duplicate provider contributions"
                )
            contributors.add(contribution["provider"])
            expected_denominator = fusion["rrf_k"] + contribution["provider_rank"] + 1
            if contribution["denominator"] != expected_denominator:
                violations.append(
                    f"{cluster['cluster_id']} has incorrect exact RRF denominator"
                )

    distinct_sites = {cluster["site_key"] for cluster in clusters}
    floor_should_pass = (
        len(clusters) >= fusion["floor"]["required_clusters"]
        and len(distinct_sites) >= fusion["floor"]["required_sites"]
    )
    if fusion["floor"]["passed"] != floor_should_pass:
        violations.append("fusion structural floor does not match selected clusters/sites")

    extraction_ids = [item["extraction_run_id"] for item in extractions]
    if duplicates := _duplicate(extraction_ids):
        violations.append(f"duplicate extraction run ids: {sorted(duplicates)}")
    extraction_by_id = {item["extraction_run_id"]: item for item in extractions}
    artifact_by_id: dict[str, dict[str, Any]] = {}
    rejection_by_id: dict[str, dict[str, Any]] = {}
    invoked_extractor_latency = 0
    invoked_extractor_count = 0
    for extraction in extractions:
        if extraction["cluster_ref"] not in cluster_ids:
            violations.append(f"{extraction['extraction_run_id']} has dangling cluster")
        if extraction["plan_ref"] != plan["plan_id"]:
            violations.append(f"{extraction['extraction_run_id']} has wrong plan ref")
        steps = extraction["steps"]
        if [step["ordinal"] for step in steps] != list(range(len(steps))):
            violations.append(
                f"{extraction['extraction_run_id']} step ordinals are not contiguous"
            )
        invoked = [step for step in steps if step["decision"] == "invoked"]
        invoked_extractor_count += len(invoked)
        invoked_extractor_latency += sum(step["latency_ms"] or 0 for step in invoked)
        for step in steps:
            if step["kind"] == "cache":
                if (
                    step["extractor"] is not None
                    or step["attempt_outcome"] is not None
                    or step["spend"] is not None
                ):
                    violations.append(
                        f"{extraction['extraction_run_id']} cache step looks invoked"
                    )
            elif step["decision"] == "invoked":
                if (
                    step["extractor"] is None
                    or step["attempt_outcome"] is None
                    or step["latency_ms"] is None
                    or step["provenance"] is None
                    or step["spend"] is None
                ):
                    violations.append(
                        f"{extraction['extraction_run_id']} invoked step lacks evidence"
                    )
                else:
                    actual_cost += Decimal(step["spend"]["actual_usd"])
                    reserved_cost += Decimal(step["spend"]["reserved_usd"])
            elif (
                step["attempt_outcome"] is not None
                or step["latency_ms"] is not None
                or step["provenance"] is not None
                or step["spend"] is not None
            ):
                violations.append(
                    f"{extraction['extraction_run_id']} skipped step has attempt facts"
                )

        artifact = extraction["artifact"]
        rejection = extraction["rejection"]
        if artifact is not None:
            artifact_id = artifact["artifact_id"]
            if artifact_id in artifact_by_id:
                violations.append(f"duplicate artifact id: {artifact_id}")
            artifact_by_id[artifact_id] = artifact
            disposition = artifact["disposition"]
            if disposition == "usable":
                if (
                    artifact["quality_passed"] is not True
                    or artifact["is_complete"] is not True
                    or not artifact["citation_eligible"]
                    or extraction["outcome"] != "success"
                    or rejection is not None
                ):
                    violations.append(f"{artifact_id} violates usable artifact rules")
            elif disposition == "partial":
                if (
                    artifact["quality_passed"] is not True
                    or artifact["is_complete"] is not False
                    or not artifact["citation_eligible"]
                    or not extraction["partial_allowed"]
                    or extraction["outcome"] != "degraded"
                    or rejection is None
                    or rejection["code"] != "incomplete_content"
                ):
                    violations.append(f"{artifact_id} violates partial artifact rules")
            else:
                if artifact["citation_eligible"]:
                    violations.append(
                        f"{artifact_id} diagnostic/none artifact is citation eligible"
                    )
        if rejection is not None:
            rejection_id = rejection["rejection_id"]
            if rejection_id in rejection_by_id:
                violations.append(f"duplicate rejection id: {rejection_id}")
            rejection_by_id[rejection_id] = rejection
            if rejection["attempt_count"] != len(invoked):
                violations.append(f"{rejection_id} attempt count does not match steps")
            if rejection["total_attempt_latency_ms"] != sum(
                step["latency_ms"] or 0 for step in invoked
            ):
                violations.append(f"{rejection_id} latency does not match steps")
        if extraction["outcome"] == "success" and rejection is not None:
            violations.append(
                f"{extraction['extraction_run_id']} success retains final rejection"
            )

    if accounting["extractor_attempt_latency_ms"] != invoked_extractor_latency:
        violations.append("extractor attempt latency total does not reconcile")
    if accounting["extractor_call_count"] != invoked_extractor_count:
        violations.append("accounting extractor_call_count must equal invoked steps")
    if Decimal(accounting["actual_usd"]) != actual_cost:
        violations.append("actual provider/extractor spend does not reconcile")
    if Decimal(accounting["reserved_usd"]) != reserved_cost:
        violations.append("reserved provider/extractor spend does not reconcile")

    links = composition["links"]
    link_ids = [item["link_id"] for item in links]
    if duplicates := _duplicate(link_ids):
        violations.append(f"duplicate extraction link ids: {sorted(duplicates)}")
    link_by_id = {item["link_id"]: item for item in links}
    for cluster in clusters:
        link_ref = cluster["extraction_link"]
        if link_ref is not None:
            link = link_by_id.get(link_ref)
            if link is None or link["cluster_ref"] != cluster["cluster_id"]:
                violations.append(f"{cluster['cluster_id']} has invalid extraction link")
    for link in links:
        if link["cluster_ref"] not in cluster_ids:
            violations.append(f"{link['link_id']} has dangling cluster ref")
        extraction = extraction_by_id.get(link["extraction_run_ref"])
        if extraction is None:
            violations.append(f"{link['link_id']} has dangling extraction ref")
            continue
        if extraction["cluster_ref"] != link["cluster_ref"]:
            violations.append(f"{link['link_id']} cluster/extraction mismatch")
        if extraction["outcome"] != link["outcome"]:
            violations.append(f"{link['link_id']} outcome differs from extraction")
        artifact = extraction["artifact"]
        rejection = extraction["rejection"]
        if link["artifact_ref"] != (
            artifact["artifact_id"] if artifact is not None else None
        ):
            violations.append(f"{link['link_id']} artifact ref differs from extraction")
        if link["rejection_ref"] != (
            rejection["rejection_id"] if rejection is not None else None
        ):
            violations.append(f"{link['link_id']} rejection ref differs from extraction")

    accepted_refs = set(composition["accepted_artifact_refs"])
    degraded_refs = set(composition["degraded_artifact_refs"])
    rejected_refs = set(composition["rejected_extraction_refs"])
    expected_accepted = {
        artifact_id
        for artifact_id, artifact in artifact_by_id.items()
        if artifact["disposition"] in {"usable", "partial"}
    }
    expected_degraded = {
        artifact_id
        for artifact_id, artifact in artifact_by_id.items()
        if artifact["disposition"] == "partial"
    }
    expected_rejected = {
        extraction["extraction_run_id"]
        for extraction in extractions
        if extraction["outcome"] not in {"success", "degraded"}
    }
    if accepted_refs != expected_accepted:
        violations.append("composition accepted artifact refs do not reconcile")
    if degraded_refs != expected_degraded:
        violations.append("composition degraded artifact refs do not reconcile")
    if rejected_refs != expected_rejected:
        violations.append("composition rejected extraction refs do not reconcile")

    requirement = composition["artifact_requirement"]
    requirement_ref = composition["artifact_requirement_ref"]
    if requirement_ref is None:
        if requirement is not None or links:
            violations.append("composition without requirement has extraction policy")
    else:
        if requirement is None or requirement["requirement_ref"] != requirement_ref:
            violations.append("artifact requirement object/ref do not reconcile")
        else:
            if requirement["max_extractions"] < len(extractions):
                violations.append("extraction count exceeds artifact requirement")
            disposition_value = {
                "none": 0,
                "diagnostic_only": 1,
                "partial": 2,
                "usable": 3,
            }
            minimum = disposition_value[requirement["minimum_disposition"]]
            passing = sum(
                1
                for artifact in artifact_by_id.values()
                if disposition_value[artifact["disposition"]] >= minimum
            )
            if (
                composition["composite_outcome"] in {"success", "degraded"}
                and passing < requirement["aggregate_count"]
            ):
                violations.append("successful composition does not meet artifact floor")

    if final["outcome"] != composition["composite_outcome"]:
        violations.append("final outcome differs from composite outcome")
    known_result_refs = set(cluster_ids)
    for result_ref in final["result_refs"] + final["diagnostic_result_refs"]:
        if result_ref not in known_result_refs:
            violations.append(f"final has dangling result ref: {result_ref}")
    if set(final["result_refs"]) & set(final["diagnostic_result_refs"]):
        violations.append("result and diagnostic result refs overlap")
    if len(final["result_refs"]) > plan["result_limit"]:
        violations.append("final result count exceeds plan result limit")
    for citation in final["citations"]:
        artifact = artifact_by_id.get(citation["artifact_ref"])
        if citation["cluster_ref"] not in known_result_refs:
            violations.append(f"{citation['citation_id']} has dangling cluster")
        if artifact is None or not artifact["citation_eligible"]:
            violations.append(f"{citation['citation_id']} cites ineligible artifact")

    outcome = final["outcome"]
    if outcome in {"success", "degraded"}:
        if acceptance["status"] != "accepted" or not final["delivery_allowed"]:
            violations.append(f"{outcome} requires accepted, deliverable evidence")
    if outcome in FAILURE_OUTCOMES:
        if final["delivery_allowed"] or final["synthesis_allowed"]:
            violations.append(f"{outcome} cannot allow synthesis or delivery")
    if outcome == "empty":
        if not any(attempt["outcome"] == "empty" for attempt in attempts):
            violations.append("empty requires an eligible successful-empty attempt")
    if outcome == "providers_failed":
        eligible = [
            item["provider"] for item in readiness if item["decision"] == "eligible"
        ]
        if not eligible or set(eligible) != {item["provider"] for item in attempts}:
            violations.append("providers_failed must attempt every eligible provider")
    if outcome == "unready" and not attempts:
        if any(item["decision"] == "eligible" for item in readiness):
            violations.append("zero-attempt unready cannot have an eligible provider")

    if acceptance["status"] == "accepted":
        if acceptance["receipt_ref"] is None or acceptance["accepted_at"] is None:
            violations.append("accepted evidence requires receipt and timestamp")
    else:
        if outcome != "persistence_failed":
            violations.append("failed acceptance must surface persistence_failed")
        if acceptance["receipt_ref"] is not None or acceptance["accepted_at"] is not None:
            violations.append("failed acceptance cannot fabricate receipt/timestamp")
        if (
            acceptance["cache_published"]
            or final["result_refs"]
            or final["citations"]
            or final["delivery_allowed"]
            or final["synthesis_allowed"]
        ):
            violations.append("persistence failure leaked accepted output/cache")
    if acceptance["artifact_count"] != len(artifact_by_id):
        violations.append("acceptance artifact count does not reconcile")

    for path, value in _walk(envelope):
        key = path.rsplit(".", 1)[-1].lower()
        if key in FORBIDDEN_KEYS:
            violations.append(f"{path}: forbidden private/native field")
        if isinstance(value, str) and any(
            marker in value.lower() for marker in FORBIDDEN_TEXT
        ):
            violations.append(f"{path}: credential-like text is forbidden")

    return violations
