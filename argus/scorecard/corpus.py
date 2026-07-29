"""Frozen scorecard corpus validation, deliberately independent of retrieval."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping


SEARCH_MODES = frozenset({"discovery", "grounding", "recovery", "research"})
PROFILES = frozenset({"free", "budgeted"})
OUTCOMES = frozenset(
    {
        "success",
        "degraded",
        "empty",
        "timeout",
        "providers_failed",
        "extraction_failed",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOP_LEVEL_FIELDS = frozenset(
    {"version", "search_intents", "hermetic_extractions", "live_extractions"}
)
_INTENT_FIELDS = frozenset(
    {
        "id",
        "mode",
        "intent",
        "forbidden_interpretation",
        "required_source_characteristics",
        "forbidden_patterns",
        "freshness_window_days",
        "minimum_evidence_shape",
        "profiles",
        "hermetic_input",
    }
)
_HERMETIC_EXTRACTION_FIELDS = frozenset(
    {
        "id",
        "kind",
        "profiles",
        "hermetic_input",
    }
)
_LIVE_EXTRACTION_FIELDS = frozenset(
    {
        "id",
        "kind",
        "profiles",
        "synchronized",
        "snapshot_id",
        "snapshot_sha256",
        "url",
    }
)
COMPETITIVE_CASE_MODES = {
    **{
        f"{mode}-{number:02d}": mode
        for mode in ("discovery", "grounding", "recovery", "research")
        for number in range(1, 7)
    },
    "primary-docs": "extraction",
    "long-form": "extraction",
    "javascript-live": "extraction",
    "moved-live": "extraction",
}
HERMETIC_SEARCH_CASE_IDS = tuple(
    case_id for case_id, mode in COMPETITIVE_CASE_MODES.items() if mode in SEARCH_MODES
)
HERMETIC_EXTRACTION_CASE_IDS = (
    "static",
    "javascript",
    "paywall",
    "malformed",
    "redirect",
    "mirror",
    "timeout",
    "unsupported",
)


def load_corpus(path: Path) -> dict[str, Any]:
    """Load and validate an immutable JSON corpus without interpreting URLs."""
    try:
        corpus = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid scorecard corpus: {exc}") from exc
    validate_corpus(corpus)
    return corpus


def _mapping(value: object, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{description} must be an object")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: frozenset[str] | set[str], description: str
) -> None:
    if set(value) != set(expected):
        raise ValueError(f"{description} must contain exact keys")


def _nonempty_string(value: object, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{description} must be a non-empty string")
    return value


def _string_list(value: object, description: str, *, allow_empty: bool = False) -> None:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"{description} must be a string list")


def _profiles(value: object, description: str) -> None:
    if (
        not isinstance(value, list)
        or not value
        or len(value) != len(set(value))
        or not set(value) <= PROFILES
    ):
        raise ValueError(f"{description} has invalid profiles")


def _count(value: object, description: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{description} has invalid evidence count")
    return value


def _search_evidence(value: object, description: str) -> None:
    evidence = _mapping(value, description)
    _exact_keys(
        evidence,
        {"outcome", "result_count", "domain_count", "provenance_complete"},
        description,
    )
    if evidence["outcome"] not in OUTCOMES:
        raise ValueError(f"{description} has invalid outcome")
    result_count = _count(evidence["result_count"], description)
    domain_count = _count(evidence["domain_count"], description)
    if domain_count > result_count:
        raise ValueError(f"{description} has impossible evidence cardinality")
    if not isinstance(evidence["provenance_complete"], bool):
        raise ValueError(f"{description} has invalid provenance flag")


def _extraction_evidence(value: object, description: str) -> None:
    evidence = _mapping(value, description)
    _exact_keys(
        evidence,
        {"outcome", "quality", "complete", "provenance_complete"},
        description,
    )
    if evidence["outcome"] not in OUTCOMES:
        raise ValueError(f"{description} has invalid outcome")
    if evidence["quality"] not in {"passing", "degraded", "failed"}:
        raise ValueError(f"{description} has invalid quality")
    for field in ("complete", "provenance_complete"):
        if not isinstance(evidence[field], bool):
            raise ValueError(f"{description} has invalid {field}")


def validate_corpus(corpus: Mapping[str, Any]) -> None:
    """Fail closed unless the frozen generation exactly covers the contract."""
    corpus = _mapping(corpus, "corpus")
    _exact_keys(corpus, _TOP_LEVEL_FIELDS, "corpus")
    _nonempty_string(corpus["version"], "corpus version")

    intents = corpus["search_intents"]
    if not isinstance(intents, list) or len(intents) != 24:
        raise ValueError("search_intents must contain exactly 24 intents")
    seen: set[str] = set()
    mode_counts = {mode: 0 for mode in SEARCH_MODES}
    for raw in intents:
        intent = _mapping(raw, "search intent")
        _exact_keys(intent, _INTENT_FIELDS, "search intent")
        intent_id = _nonempty_string(intent["id"], "search intent id")
        if intent_id in seen:
            raise ValueError("search intents require unique ids")
        seen.add(intent_id)
        mode = intent["mode"]
        if mode not in SEARCH_MODES or COMPETITIVE_CASE_MODES.get(intent_id) != mode:
            raise ValueError(f"search intent {intent_id} has unknown mode or id")
        mode_counts[mode] += 1
        for field in ("intent", "forbidden_interpretation"):
            _nonempty_string(intent[field], f"search intent {intent_id}.{field}")
        _string_list(
            intent["required_source_characteristics"],
            f"search intent {intent_id}.required_source_characteristics",
        )
        _string_list(
            intent["forbidden_patterns"],
            f"search intent {intent_id}.forbidden_patterns",
            allow_empty=True,
        )
        freshness = intent["freshness_window_days"]
        if freshness is not None and (
            isinstance(freshness, bool)
            or not isinstance(freshness, int)
            or freshness <= 0
        ):
            raise ValueError(f"search intent {intent_id} has invalid freshness window")
        minimum = _mapping(
            intent["minimum_evidence_shape"],
            f"search intent {intent_id} minimum evidence",
        )
        expected_minimum_keys = (
            {"sources", "domains"} if mode == "research" else {"sources"}
        )
        _exact_keys(minimum, expected_minimum_keys, "minimum evidence")
        for count in minimum.values():
            _count(count, f"search intent {intent_id} minimum evidence")
        _profiles(intent["profiles"], f"search intent {intent_id}")
        hermetic_input = _mapping(
            intent["hermetic_input"], f"search intent {intent_id} hermetic input"
        )
        _exact_keys(
            hermetic_input,
            {"query", "transport_outcome", "results"},
            "hermetic search input",
        )
        _nonempty_string(hermetic_input["query"], "hermetic search query")
        if hermetic_input["transport_outcome"] not in OUTCOMES:
            raise ValueError("hermetic search input has invalid transport outcome")
        results = hermetic_input["results"]
        if not isinstance(results, list):
            raise ValueError("hermetic search results must be a list")
        for result in results:
            result = _mapping(result, "hermetic raw search result")
            _exact_keys(
                result,
                {"url", "title", "snippet", "egress", "machine"},
                "hermetic raw search result",
            )
            for field in result:
                _nonempty_string(result[field], f"hermetic search result {field}")
    if set(mode_counts.values()) != {6}:
        raise ValueError("corpus must contain six intents for each search mode")

    extractions = corpus["hermetic_extractions"]
    if not isinstance(extractions, list) or len(extractions) != 8:
        raise ValueError("hermetic_extractions must contain exactly 8 cases")
    for raw in extractions:
        entry = _mapping(raw, "hermetic extraction")
        _exact_keys(entry, _HERMETIC_EXTRACTION_FIELDS, "hermetic extraction")
        case_id = _nonempty_string(entry["id"], "hermetic extraction id")
        if case_id in seen:
            raise ValueError("corpus case ids must be unique")
        seen.add(case_id)
        _nonempty_string(entry["kind"], f"hermetic extraction {case_id} kind")
        _profiles(entry["profiles"], f"hermetic extraction {case_id}")
        hermetic_input = _mapping(
            entry["hermetic_input"], f"hermetic extraction {case_id} input"
        )
        _exact_keys(
            hermetic_input,
            {"fixture", "content_type", "transport_outcome", "text", "provenance"},
            "extraction input",
        )
        _nonempty_string(hermetic_input["fixture"], "extraction fixture")
        _nonempty_string(hermetic_input["content_type"], "extraction content type")
        if hermetic_input["transport_outcome"] not in OUTCOMES:
            raise ValueError("hermetic extraction has invalid transport outcome")
        if not isinstance(hermetic_input["text"], str):
            raise ValueError("hermetic extraction text must be a string")
        provenance = _mapping(
            hermetic_input["provenance"], "hermetic extraction provenance"
        )
        _exact_keys(
            provenance,
            {"egress", "machine", "source_type"},
            "hermetic extraction provenance",
        )
        for field in provenance:
            _nonempty_string(provenance[field], f"extraction provenance {field}")

    live = corpus["live_extractions"]
    if not isinstance(live, list) or len(live) != 4:
        raise ValueError("live_extractions must contain exactly 4 cases")
    for raw in live:
        entry = _mapping(raw, "live extraction")
        _exact_keys(entry, _LIVE_EXTRACTION_FIELDS, "live extraction")
        case_id = _nonempty_string(entry["id"], "live extraction id")
        if case_id in seen or COMPETITIVE_CASE_MODES.get(case_id) != "extraction":
            raise ValueError("live extraction ids must be unique and frozen")
        seen.add(case_id)
        _nonempty_string(entry["kind"], f"live extraction {case_id} kind")
        _profiles(entry["profiles"], f"live extraction {case_id}")
        if entry["synchronized"] is not True:
            raise ValueError(f"live extraction {case_id} must be synchronized")
        _nonempty_string(entry["snapshot_id"], f"live extraction {case_id} snapshot")
        if not isinstance(entry["snapshot_sha256"], str) or not _SHA256.fullmatch(
            entry["snapshot_sha256"]
        ):
            raise ValueError(f"live extraction {case_id} has invalid snapshot hash")
        url = _nonempty_string(entry["url"], f"live extraction {case_id} URL")
        if not url.startswith("https://"):
            raise ValueError(f"live extraction {case_id} URL must use HTTPS")
