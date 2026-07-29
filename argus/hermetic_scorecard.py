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


def execute_authority_gate_contracts() -> dict[str, dict[str, object]]:
    """Exercise production authority, durable acceptance, and cache contracts locally."""
    import asyncio
    from datetime import datetime, timedelta, timezone
    import os
    from types import SimpleNamespace
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from argus.api.main import create_app
    from argus.api.schemas import SearchRequest
    from argus.authority import (
        AuthorityConfigurationError,
        authority_client_config,
        broker_construction_allowed,
    )
    from argus.broker.accepted import (
        AcceptanceReceipt,
        AcceptedRetrieval,
        CacheEntry,
        CacheOutcome,
        RetrievalCache,
    )
    from argus.config import reset_config
    from argus.models import SearchMode, SearchResponse
    from argus.operations.accepted import AcceptedOperationService

    principal = "scorecard:hermetic"
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)

    class CapturingBroker:
        def __init__(self) -> None:
            self.search_calls = 0

        async def search(self, query, **kwargs):
            del kwargs
            self.search_calls += 1
            return SearchResponse(
                query=query.query,
                mode=SearchMode(query.mode),
                results=[],
                total_results=0,
                search_run_id="hermetic-run",
            )

    class CapturingRepository:
        def __init__(self) -> None:
            self.accept_calls = 0
            self.caller_identity = ""

        def accept(self, query, response):
            del response
            self.accept_calls += 1
            self.caller_identity = query.caller
            return SimpleNamespace(
                run_id="hermetic-run",
                delivery_intent_id=None,
            )

    accepted_broker = CapturingBroker()
    accepted_repository = CapturingRepository()
    service = AcceptedOperationService(
        broker_provider=lambda: accepted_broker,
        repository_provider=lambda: accepted_repository,
    )
    accepted_operation = asyncio.run(
        service.search(
            SearchRequest(query="hermetic authority contract"),
            principal=principal,
            request_id="hermetic-authority",
        )
    )

    receipt = AcceptanceReceipt(
        receipt_ref="receipt-origin",
        accepted_at=now,
        acceptance_fingerprint="a" * 64,
    )
    accepted = AcceptedRetrieval(
        operation_id="operation-origin",
        cache_fingerprint="cache-fingerprint",
        execution_cohort="free:en",
        outcome=CacheOutcome.SUCCESS,
        results=(
            {
                "url": "https://example.com/answer",
                "title": "answer",
                "providers": ("brave", "duckduckgo"),
            },
        ),
        contributor_attempt_refs=("attempt-brave", "attempt-duckduckgo"),
        origin_spend_usd="0.01",
        acceptance_receipt=receipt,
    )
    publication_events: list[str] = []
    cache = RetrievalCache(
        clock=lambda: now,
        on_publish=lambda: publication_events.append("cache"),
    )
    accepted_receipt = cache.accept_and_publish(
        accepted,
        persist=lambda value: (
            publication_events.append("durable") or value.acceptance_receipt
        ),
    )
    fresh = cache.decide(
        cache_fingerprint="cache-fingerprint",
        execution_cohort="free:en",
        max_age_seconds=60,
    )
    wrong_policy = cache.decide(
        cache_fingerprint="cache-fingerprint",
        execution_cohort="budgeted:en",
        max_age_seconds=60,
    )
    stale_cache = RetrievalCache(clock=lambda: now + timedelta(seconds=61))
    stale_cache.publish(CacheEntry.from_accepted(accepted))
    stale = stale_cache.decide(
        cache_fingerprint="cache-fingerprint",
        execution_cohort="free:en",
        max_age_seconds=60,
    )

    auth_broker = CapturingBroker()
    auth_repository = CapturingRepository()
    auth_environment = {
        "ARGUS_API_KEY": "hermetic-caller-key",
        "ARGUS_ENV": "production",
        "ARGUS_ACCEPTED_OPERATION_AUTHORITY": "legacy",
        "ARGUS_AUTOLOAD_DOTENV": "false",
        "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
        "ARGUS_DB_URL": "sqlite:///:memory:",
        "ARGUS_ALLOWED_HOSTS": "testserver",
        "ARGUS_ALLOWED_ORIGINS": "",
    }
    with patch.dict(os.environ, auth_environment):
        reset_config()
        client = TestClient(
            create_app(
                broker=auth_broker,
                search_repository=auth_repository,
            ),
            client=("203.0.113.10", 50000),
        )
        response = client.post(
            "/api/search",
            json={"query": "must be rejected before execution"},
        )
        client.close()
        reset_config()

    adapter_rejected_db_config = False
    try:
        authority_client_config(
            {
                "ARGUS_ENV": "production",
                "ARGUS_AUTHORITY_URL": "https://argus.invalid",
                "ARGUS_AUTHORITY_TOKEN": "hermetic-token",
                "ARGUS_DB_URL": "sqlite:///:memory:",
            },
            adapter="scorecard",
        )
    except AuthorityConfigurationError:
        adapter_rejected_db_config = True
    caller_rejected_broker = False
    with patch.dict(
        os.environ,
        {"ARGUS_ENV": "production", "ARGUS_NODE_ROLE": "caller"},
    ):
        try:
            broker_construction_allowed(authority_capability=None)
        except RuntimeError:
            caller_rejected_broker = True

    return {
        "authentication": {
            "http_status": response.status_code,
            "network_calls_before_rejection": (
                auth_broker.search_calls + auth_repository.accept_calls
            ),
        },
        "caller_attribution": {
            "request_caller_identity": principal,
            "durable_caller_identity": accepted_repository.caller_identity,
        },
        "durable_acceptance": {
            "operation_outcome": accepted_operation.outcome.value,
            "acceptance_receipt_present": bool(
                accepted_operation.result
                and accepted_operation.result.get("acceptance_receipt")
            ),
            "publication_events": publication_events,
            "receipt_matched": accepted_receipt == receipt,
        },
        "persistence_isolation": {
            "production_adapter_rejected_db_config": adapter_rejected_db_config,
            "production_caller_rejected_broker": caller_rejected_broker,
            "development_sqlite_scope": "explicit",
        },
        "cache_eligibility": {
            "fresh_decision": fresh.outcome.value,
            "stale_decision": stale.outcome.value,
        },
        "cache_isolation": {
            "wrong_policy_decision": wrong_policy.outcome.value,
            "origin_spend_usd": (
                fresh.accepted.origin_spend_usd if fresh.accepted is not None else None
            ),
            "current_spend_usd": (
                fresh.accepted.current_spend_usd if fresh.accepted is not None else None
            ),
            "current_provider_calls": (
                fresh.accepted.current_provider_calls
                if fresh.accepted is not None
                else None
            ),
        },
    }
