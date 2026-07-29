"""Pure hermetic execution of frozen raw scorecard inputs.

The fixtures contain transport-shaped inputs, never a second copy of their
expected normalized observations.  These functions deliberately exercise the
same provider-independent models and extraction completeness contract used by
the application.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from urllib.parse import urlparse

from argus.contracts.outcomes import CanonicalOutcome, http_status_for, mcp_is_error_for
from argus.extraction.completeness import assess_completeness
from argus.models import SearchResult


def load_expected_observations(
    path: Path,
) -> dict[str, dict[str, Mapping[str, object]]]:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid independent expected observations: {exc}") from exc
    if not isinstance(document, Mapping) or set(document) != {
        "schema",
        "searches",
        "extractions",
    }:
        raise ValueError("expected observations have invalid schema")
    if document["schema"] != "scorecard-hermetic-expected-v1":
        raise ValueError("unsupported expected observation schema")
    if not isinstance(document["searches"], Mapping) or not isinstance(
        document["extractions"], Mapping
    ):
        raise ValueError("expected observation sets must be objects")
    return {
        "searches": dict(document["searches"]),
        "extractions": dict(document["extractions"]),
    }


def execute_search_fixture(raw: Mapping[str, object]) -> dict[str, object]:
    outcome = CanonicalOutcome(raw["transport_outcome"])
    rows = raw["results"]
    if not isinstance(rows, list):
        raise ValueError("raw search results must be a list")
    results: list[SearchResult] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != {
            "url",
            "title",
            "snippet",
            "egress",
            "machine",
        }:
            raise ValueError("raw search result has invalid shape")
        if not all(isinstance(row[key], str) and row[key] for key in row):
            raise ValueError("raw search result values must be strings")
        domain = urlparse(row["url"]).hostname or ""
        results.append(
            SearchResult(
                url=row["url"],
                title=row["title"],
                snippet=row["snippet"],
                domain=domain,
                raw_rank=index,
                metadata={"egress": row["egress"], "machine": row["machine"]},
            )
        )
    if outcome is CanonicalOutcome.EMPTY and results:
        raise ValueError("empty raw outcome cannot contain results")
    if outcome is CanonicalOutcome.SUCCESS and not results:
        raise ValueError("successful raw outcome requires results")
    return {
        "outcome": outcome.value,
        "result_count": len(results),
        "domain_count": len({result.domain for result in results}),
        "provenance_complete": all(
            bool(result.metadata.get("egress")) and bool(result.metadata.get("machine"))
            for result in results
        ),
    }


def execute_extraction_fixture(raw: Mapping[str, object]) -> dict[str, object]:
    outcome = CanonicalOutcome(raw["transport_outcome"])
    text = raw["text"]
    if not isinstance(text, str):
        raise ValueError("raw extraction text must be a string")
    provenance = raw["provenance"]
    if not isinstance(provenance, Mapping) or set(provenance) != {
        "egress",
        "machine",
        "source_type",
    }:
        raise ValueError("raw extraction provenance has invalid shape")
    provenance_complete = all(
        isinstance(value, str) and bool(value) for value in provenance.values()
    )
    if outcome in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}:
        complete = assess_completeness(text).is_complete
        quality = "passing" if complete else "degraded"
        normalized_outcome = "success" if complete else "degraded"
    else:
        complete = False
        quality = "failed"
        normalized_outcome = outcome.value
    return {
        "outcome": normalized_outcome,
        "quality": quality,
        "complete": complete,
        "provenance_complete": provenance_complete,
    }


def execute_surface_fixture(raw: Mapping[str, object]) -> dict[str, object]:
    outcome = CanonicalOutcome(raw["outcome"])
    code = raw.get("code")
    if code is not None and not isinstance(code, str):
        raise ValueError("surface code must be a string or null")
    return {
        "outcome": outcome.value,
        "http_status": http_status_for(outcome, code),
        "mcp_is_error": mcp_is_error_for(outcome),
        "cli_exit": 0
        if outcome
        in {
            CanonicalOutcome.SUCCESS,
            CanonicalOutcome.DEGRADED,
            CanonicalOutcome.EMPTY,
        }
        else 1,
        "python_error": outcome
        not in {
            CanonicalOutcome.SUCCESS,
            CanonicalOutcome.DEGRADED,
            CanonicalOutcome.EMPTY,
        },
    }
