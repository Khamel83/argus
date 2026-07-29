"""Frozen scorecard corpus validation, deliberately independent of retrieval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


SEARCH_MODES = frozenset({"discovery", "grounding", "recovery", "research"})
PROFILES = frozenset({"free", "budgeted"})
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
    }
)


def load_corpus(path: Path) -> dict[str, Any]:
    """Load and validate an immutable JSON corpus without interpreting URLs."""
    try:
        corpus = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid scorecard corpus: {exc}") from exc
    validate_corpus(corpus)
    return corpus


def _require_mapping(value: object, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{description} must be an object")
    return value


def _require_cases(
    corpus: Mapping[str, Any], name: str, count: int, *, synchronized: bool = False
) -> None:
    entries = corpus.get(name)
    if not isinstance(entries, list) or len(entries) != count:
        raise ValueError(f"{name} must contain exactly {count} cases")
    seen: set[str] = set()
    for entry in entries:
        entry = _require_mapping(entry, name)
        case_id = entry.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"{name} has an invalid or duplicate id")
        seen.add(case_id)
        if not isinstance(entry.get("kind"), str) or not entry["kind"]:
            raise ValueError(f"{name}.{case_id} must declare kind")
        if synchronized and entry.get("synchronized") is not True:
            raise ValueError(f"{name}.{case_id} must be synchronized")
        if not synchronized:
            profiles = entry.get("profiles")
            if (
                not isinstance(profiles, list)
                or not profiles
                or not set(profiles) <= PROFILES
            ):
                raise ValueError(f"{name}.{case_id} must declare supported profiles")


def validate_corpus(corpus: Mapping[str, Any]) -> None:
    """Fail closed unless the frozen generation covers the accepted contract."""
    corpus = _require_mapping(corpus, "corpus")
    if not isinstance(corpus.get("version"), str) or not corpus["version"]:
        raise ValueError("corpus must declare version")
    intents = corpus.get("search_intents")
    if not isinstance(intents, list) or len(intents) != 24:
        raise ValueError("search_intents must contain exactly 24 intents")
    seen: set[str] = set()
    mode_counts = {mode: 0 for mode in SEARCH_MODES}
    for intent in intents:
        intent = _require_mapping(intent, "search intent")
        missing = sorted(_INTENT_FIELDS - intent.keys())
        if missing:
            raise ValueError(
                f"search intent missing required fields: {', '.join(missing)}"
            )
        intent_id = intent["id"]
        if not isinstance(intent_id, str) or not intent_id or intent_id in seen:
            raise ValueError("search intents require unique non-empty ids")
        seen.add(intent_id)
        mode = intent["mode"]
        if mode not in SEARCH_MODES:
            raise ValueError(f"search intent {intent_id} has unknown mode")
        mode_counts[mode] += 1
        for field in ("intent", "forbidden_interpretation"):
            if not isinstance(intent[field], str) or not intent[field]:
                raise ValueError(f"search intent {intent_id} requires {field}")
        for field in ("required_source_characteristics", "forbidden_patterns"):
            if not isinstance(intent[field], list):
                raise ValueError(f"search intent {intent_id} requires {field}")
        if intent["freshness_window_days"] is not None and (
            not isinstance(intent["freshness_window_days"], int)
            or intent["freshness_window_days"] <= 0
        ):
            raise ValueError(f"search intent {intent_id} has invalid freshness window")
        evidence_shape = intent["minimum_evidence_shape"]
        if not isinstance(evidence_shape, Mapping) or not isinstance(
            evidence_shape.get("sources"), int
        ):
            raise ValueError(
                f"search intent {intent_id} requires minimum_evidence_shape"
            )
        profiles = intent["profiles"]
        if (
            not isinstance(profiles, list)
            or not profiles
            or not set(profiles) <= PROFILES
        ):
            raise ValueError(
                f"search intent {intent_id} has invalid profile applicability"
            )
    if set(mode_counts.values()) != {6}:
        raise ValueError("corpus must contain six intents for each search mode")
    _require_cases(corpus, "hermetic_extractions", 8)
    _require_cases(corpus, "live_extractions", 4, synchronized=True)
