"""Hermetic execution harness for the real canonical provider adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from unittest.mock import patch

from argus.config import ProviderConfig, SearXNGConfig
from argus.models import ProviderName, SearchQuery
from argus.providers.fixture_registry import canonical_adapter


_SECRET = "fixture-private-query-value"
_SENTINEL = "fixture-credential-sentinel"
_SCENARIOS = ("success", "empty", "error", "malformed")


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def _row(provider: ProviderName) -> dict[str, object]:
    url = "https://example.com/fixture"
    return {
        ProviderName.DUCKDUCKGO: {
            "href": url, "title": "Fixture", "body": "fixture snippet"
        },
        ProviderName.GITHUB: {
            "html_url": url,
            "full_name": "fixture/repository",
            "description": "fixture snippet",
        },
        ProviderName.LINKUP: {
            "url": url, "name": "Fixture", "content": "fixture snippet"
        },
        ProviderName.SERPER: {
            "link": url, "title": "Fixture", "snippet": "fixture snippet"
        },
        ProviderName.SEARXNG: {
            "url": url, "title": "Fixture", "content": "fixture snippet"
        },
        ProviderName.TAVILY: {
            "url": url, "title": "Fixture", "content": "fixture snippet"
        },
        ProviderName.VALYU: {
            "url": url, "title": "Fixture", "description": "fixture snippet"
        },
        ProviderName.BRAVE: {
            "url": url, "title": "Fixture", "description": "fixture snippet"
        },
        ProviderName.PARALLEL: {
            "url": url, "title": "Fixture", "excerpt": "fixture snippet"
        },
        ProviderName.EXA: {
            "url": url, "title": "Fixture", "text": "fixture snippet"
        },
        ProviderName.SEARCHAPI: {
            "link": url, "title": "Fixture", "snippet": "fixture snippet"
        },
        ProviderName.YOU: {
            "url": url, "title": "Fixture", "description": "fixture snippet"
        },
        ProviderName.YAHOO: {
            "url": url, "title": "Fixture", "snippet": "fixture snippet"
        },
    }.get(provider, {"url": url, "title": "Fixture"})


def _payload(provider: ProviderName, rows: list[object]) -> dict[str, object]:
    if provider is ProviderName.BRAVE:
        return {"web": {"results": rows}}
    if provider is ProviderName.GITHUB:
        return {"items": rows}
    if provider is ProviderName.SERPER:
        return {"organic": rows}
    if provider is ProviderName.YOU:
        return {"results": {"web": rows}}
    if provider is ProviderName.SEARCHAPI:
        return {"organic_results": rows}
    if provider is ProviderName.WOLFRAM:
        return {"answer": "fixture answer"} if rows else {"empty": True}
    return {"results": rows}


def _yahoo_html(success: bool) -> str:
    if not success:
        return "<html><body>No results for fixture query</body></html>"
    return (
        '<div class="dd algo-sr"><div class="compTitle">'
        '<a href="https://example.com/fixture"><h3>Fixture</h3></a>'
        '</div><div class="compText">fixture snippet</div></div>'
    )


@dataclass
class _FakeResponse:
    provider: ProviderName
    scenario: str
    status_code: int
    headers: dict[str, str]

    def json(self):
        if self.scenario == "malformed":
            raise ValueError("malformed fixture response")
        rows = [] if self.scenario == "empty" else [_row(self.provider)]
        return _payload(self.provider, rows)

    @property
    def text(self) -> str:
        if self.scenario == "malformed":
            raise ValueError("malformed fixture response")
        if self.provider is ProviderName.YAHOO:
            return _yahoo_html(self.scenario == "success")
        if self.provider is ProviderName.WOLFRAM:
            return "fixture answer" if self.scenario == "success" else ""
        return json.dumps(self.json())


class _FakeClient:
    def __init__(self, provider: ProviderName, scenario: str, **_kwargs):
        self.response = _FakeResponse(
            provider=provider,
            scenario=scenario,
            status_code=429 if scenario == "error" else (
                501
                if provider is ProviderName.WOLFRAM and scenario == "empty"
                else 200
            ),
            headers={},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, *_args, **_kwargs):
        return self.response

    async def post(self, *_args, **_kwargs):
        return self.response


class _FakeProcess:
    def __init__(self, provider: ProviderName, scenario: str):
        self.provider = provider
        self.scenario = scenario
        self.returncode = 0

    async def communicate(self, _input):
        if self.scenario == "malformed":
            return b"not-json", b""
        if self.scenario == "error":
            payload = {"error": {"kind": "rate_limit"}}
        else:
            rows = [] if self.scenario == "empty" else [_row(self.provider)]
            payload = _payload(self.provider, rows)
        return json.dumps(payload).encode(), b""

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


def _provider_instance(provider: ProviderName):
    _, _, _, provider_class = canonical_adapter(provider)
    if provider is ProviderName.SEARXNG:
        return provider_class(SearXNGConfig(enabled=True))
    instance = provider_class(ProviderConfig(
        enabled=True,
        api_key=_SENTINEL,
        monthly_budget_usd=1.0,
        timeout_seconds=2,
    ))
    if provider is ProviderName.DUCKDUCKGO:
        instance._available = True
    return instance


async def _execute_case(provider: ProviderName, scenario: str):
    adapter = _provider_instance(provider)
    query = SearchQuery(query=_SECRET, max_results=1)

    def fake_client(**kwargs):
        return _FakeClient(provider, scenario, **kwargs)

    async def fake_subprocess(*_args, **_kwargs):
        return _FakeProcess(provider, scenario)

    capture = _CaptureHandler()
    argus_logger = logging.getLogger("argus")
    provider_logger = logging.getLogger(f"argus.providers.{provider.value}")
    previous_level = argus_logger.level
    previous_propagate = argus_logger.propagate
    previous_provider_disabled = provider_logger.disabled
    previous_provider_level = provider_logger.level
    previous_provider_propagate = provider_logger.propagate
    argus_logger.addHandler(capture)
    argus_logger.setLevel(logging.DEBUG)
    argus_logger.propagate = False
    provider_logger.disabled = False
    provider_logger.setLevel(logging.DEBUG)
    provider_logger.propagate = True
    try:
        with (
            patch("httpx.AsyncClient", fake_client),
            patch(
                "argus.providers.duckduckgo.asyncio.create_subprocess_exec",
                fake_subprocess,
            ),
        ):
            return await adapter.search(query), tuple(capture.messages)
    finally:
        argus_logger.removeHandler(capture)
        argus_logger.setLevel(previous_level)
        argus_logger.propagate = previous_propagate
        provider_logger.disabled = previous_provider_disabled
        provider_logger.setLevel(previous_provider_level)
        provider_logger.propagate = previous_provider_propagate


def _fixture_contract(provider: ProviderName) -> dict[str, object]:
    from importlib.resources import files

    document = json.loads(
        files("argus.providers")
        .joinpath("fixture_contracts.json")
        .read_text(encoding="utf-8")
    )
    return document["providers"][provider.value]


def run_fixture_case_summaries(
    provider: ProviderName,
) -> dict[str, dict[str, object]]:
    """Execute and enforce the declared outcome of every hermetic case."""
    contract = _fixture_contract(provider)
    summaries: dict[str, dict[str, object]] = {}
    for scenario in _SCENARIOS:
        batch, captured_logs = asyncio.run(_execute_case(provider, scenario))
        trace = batch.trace
        summary = {
            "failure": (
                batch.failure.category.value if batch.failure is not None else None
            ),
            "failure_http_status": (
                batch.failure.http_status if batch.failure is not None else None
            ),
            "observations": len(batch.observations),
            "provider": batch.provider.value,
            "provider_contract_version": batch.provider_contract_version,
            "query_relation": batch.request_evidence.query_relation.value,
            "safe_log": batch.safe_log_record(),
            "trace": {
                "status": trace.status,
                "error": trace.error,
                "credit_info": trace.credit_info,
            },
        }
        if scenario == "success":
            valid = batch.failure is None and len(batch.observations) == 1
        elif scenario == "empty":
            valid = (
                batch.failure is not None
                and batch.failure.category.value == "empty"
                and not batch.observations
            )
        elif scenario == "error":
            valid = (
                batch.failure is not None
                and batch.failure.category.value == contract["error_category"]
                and batch.failure.http_status == contract["error_http_status"]
                and not batch.observations
            )
        else:
            valid = (
                batch.failure is not None
                and batch.failure.category.value == "parse_error"
                and not batch.observations
            )
        if not valid:
            raise ValueError(f"{provider.value} {scenario} fixture failed")
        privacy_surface = json.dumps(
            {
                "captured_logs": captured_logs,
                "safe_record": batch.safe_log_record(),
                "trace": summary["trace"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if _SECRET in privacy_surface or _SENTINEL in privacy_surface:
            raise ValueError(f"{provider.value} privacy fixture failed")
        summaries[scenario] = summary
    summaries["privacy"] = {"private_query_absent": True}
    return summaries


def run_fixture_cases(provider: ProviderName) -> str:
    """Run real canonical adapter methods with hermetic transport fixtures."""
    summaries = run_fixture_case_summaries(provider)
    encoded = json.dumps(summaries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
