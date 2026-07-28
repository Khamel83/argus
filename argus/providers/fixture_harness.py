"""Hermetic execution harness for the real canonical provider adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from unittest.mock import patch

from argus.config import ProviderConfig, SearXNGConfig
from argus.models import ProviderName, SearchQuery
from argus.providers.fixture_golden_contracts import (
    CREDENTIAL,
    GOLDEN_PROVIDER_CONTRACTS,
    QUERY,
)
from argus.providers.fixture_registry import canonical_adapter


_SECRET = QUERY
_SENTINEL = "fixture-credential-sentinel"
_SCENARIOS = ("success", "empty", "error", "malformed")


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _redact_fixture_credential(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _redact_fixture_credential(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_fixture_credential(item) for item in value]
    if isinstance(value, str):
        return value.replace(_SENTINEL, CREDENTIAL)
    return value


@dataclass
class _FakeResponse:
    scenario: str
    status_code: int
    headers: dict[str, str]
    response: object

    def json(self):
        if self.scenario == "malformed":
            raise ValueError("malformed fixture response")
        if not isinstance(self.response, Mapping):
            raise ValueError("fixture response is not JSON")
        return _plain(self.response)

    @property
    def text(self) -> str:
        if self.scenario == "malformed":
            raise ValueError("malformed fixture response")
        if isinstance(self.response, str):
            return self.response
        return json.dumps(_plain(self.response))


class _FakeClient:
    def __init__(
        self,
        provider: ProviderName,
        scenario: str,
        contract: Mapping[str, object],
        **kwargs,
    ):
        self.provider = provider
        self.contract = contract
        self.client_kwargs = kwargs
        self.validation_error: str | None = None
        responses = contract["responses"]
        assert isinstance(responses, Mapping)
        self.response = _FakeResponse(
            scenario=scenario,
            status_code=429 if scenario == "error" else (
                501
                if provider is ProviderName.WOLFRAM and scenario == "empty"
                else 200
            ),
            headers={},
            response=responses[scenario],
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def _validate(self, method: str, url: object, kwargs: Mapping) -> None:
        expected = self.contract["request"]
        assert isinstance(expected, Mapping)
        actual = {
            "method": method,
            "url": url,
            "params": kwargs.get("params"),
            "headers": kwargs.get("headers"),
            "json": kwargs.get("json"),
            "client_headers": self.client_kwargs.get("headers"),
        }
        normalized = _redact_fixture_credential(actual)
        if normalized != _plain(expected):
            self.validation_error = (
                f"{self.provider.value} golden request contract mismatch"
            )

    async def get(self, url, **kwargs):
        self._validate("GET", url, kwargs)
        return self.response

    async def post(self, url, **kwargs):
        self._validate("POST", url, kwargs)
        return self.response


class _FakeProcess:
    def __init__(
        self,
        provider: ProviderName,
        scenario: str,
        contract: Mapping[str, object],
        argv: tuple[object, ...],
    ):
        self.provider = provider
        self.scenario = scenario
        self.contract = contract
        self.argv = argv
        self.returncode = 0
        self.validation_error: str | None = None

    async def communicate(self, input_bytes):
        expected = self.contract["request"]
        assert isinstance(expected, Mapping)
        actual = {
            "method": "SUBPROCESS",
            "argv": ["<python>", *self.argv[1:]],
            "stdin": json.loads(input_bytes),
        }
        if _redact_fixture_credential(actual) != _plain(expected):
            self.validation_error = (
                f"{self.provider.value} golden request contract mismatch"
            )
        if self.scenario == "malformed":
            return b"not-json", b""
        responses = self.contract["responses"]
        assert isinstance(responses, Mapping)
        payload = _plain(responses[self.scenario])
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
    contract = GOLDEN_PROVIDER_CONTRACTS[provider]
    adapter = _provider_instance(provider)
    query = SearchQuery(query=_SECRET, max_results=1)
    transports: list[_FakeClient | _FakeProcess] = []

    def fake_client(**kwargs):
        transport = _FakeClient(provider, scenario, contract, **kwargs)
        transports.append(transport)
        return transport

    async def fake_subprocess(*args, **_kwargs):
        transport = _FakeProcess(provider, scenario, contract, args)
        transports.append(transport)
        return transport

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
            batch = await adapter.search(query)
            request = contract["request"]
            assert isinstance(request, Mapping)
            expected_transport_count = 1
            if (
                len(transports) != expected_transport_count
                or any(item.validation_error for item in transports)
            ):
                detail = next(
                    (
                        item.validation_error
                        for item in transports
                        if item.validation_error
                    ),
                    f"{provider.value} golden request was not executed",
                )
                raise ValueError(detail)
            return batch, tuple(capture.messages)
    finally:
        argus_logger.removeHandler(capture)
        argus_logger.setLevel(previous_level)
        argus_logger.propagate = previous_propagate
        provider_logger.disabled = previous_provider_disabled
        provider_logger.setLevel(previous_provider_level)
        provider_logger.propagate = previous_provider_propagate


def run_fixture_case_summaries(
    provider: ProviderName,
) -> dict[str, dict[str, object]]:
    """Execute and enforce the declared outcome of every hermetic case."""
    contract = GOLDEN_PROVIDER_CONTRACTS[provider]
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
            "golden_request_validated": True,
        }
        actual_output = {
            "failure": summary["failure"],
            "failure_http_status": summary["failure_http_status"],
            "provider_contract_version": summary["provider_contract_version"],
            "query_relation": summary["query_relation"],
            "results": summary["safe_log"]["results"],
        }
        expected = contract["expected"]
        assert isinstance(expected, Mapping)
        if actual_output != _plain(expected[scenario]):
            raise ValueError(
                f"{provider.value} golden output contract mismatch for {scenario}"
            )
        summary["golden_output_validated"] = True
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
    expected = contract["expected"]
    assert isinstance(expected, Mapping)
    privacy = {
        "private_query_absent": True,
        "credential_sentinel_absent": True,
    }
    if privacy != _plain(expected["privacy"]):
        raise ValueError(f"{provider.value} golden privacy contract mismatch")
    summaries["privacy"] = privacy
    return summaries


def run_fixture_cases(provider: ProviderName) -> str:
    """Run real canonical adapter methods with hermetic transport fixtures."""
    summaries = run_fixture_case_summaries(provider)
    encoded = json.dumps(summaries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
