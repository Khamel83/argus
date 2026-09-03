"""Hand-curated golden contracts for canonical provider fixture execution.

This file is review-owned input.  The attestation generator reads it through
the harness but never writes or derives these expectations.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from argus.models import ProviderName


QUERY = "fixture-private-query-value"
CREDENTIAL = "<credential>"
CONTRACT_VERSION = "2026-07-27-v1"
RESULT_URL = "https://example.com/fixture"


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, object] | None = None,
    headers: dict[str, object] | None = None,
    json_body: dict[str, object] | None = None,
    client_headers: dict[str, object] | None = None,
    client_follow_redirects: bool | None = None,
    client_timeout: float = 2,
    call_timeout: float | None = None,
) -> dict[str, object]:
    def channels(**values: object) -> dict[str, object]:
        return {
            "params": values.get("params"),
            "headers": values.get("headers"),
            "json": values.get("json"),
            "data": values.get("data"),
            "content": values.get("content"),
            "files": values.get("files"),
            "cookies": values.get("cookies"),
            "auth": values.get("auth"),
            "extensions": values.get("extensions"),
            "timeout": values.get("timeout"),
            "follow_redirects": values.get("follow_redirects"),
            "extra_kwargs": {},
        }

    return {
        "kind": "http",
        "method": method,
        "url": url,
        "client_args": [],
        "client_kwargs": channels(
            headers=client_headers,
            timeout=client_timeout,
            follow_redirects=client_follow_redirects,
        ),
        "call_args": [],
        "call_kwargs": channels(
            params=params,
            headers={} if headers is None else headers,
            json=json_body,
            timeout=call_timeout,
        ),
    }


def _success_result(
    *,
    title: str = "Fixture",
    snippet: str = "fixture snippet",
    source_kind: str = "web_page",
    url: str = RESULT_URL,
) -> dict[str, object]:
    return {
        "rank": 0,
        "snippet": snippet,
        "source_kind": source_kind,
        "title": title,
        "url": url,
    }


def _expected(
    *,
    result: dict[str, object],
    empty_status: int | None = 200,
    error_status: int | None = 429,
    malformed_status: int | None = 200,
) -> dict[str, object]:
    common = {
        "provider_contract_version": CONTRACT_VERSION,
        "query_relation": "exact",
    }
    return {
        "success": {
            **common,
            "failure": None,
            "failure_http_status": None,
            "results": [result],
        },
        "empty": {
            **common,
            "failure": "empty",
            "failure_http_status": empty_status,
            "results": [],
        },
        "error": {
            **common,
            "failure": "rate_limited",
            "failure_http_status": error_status,
            "results": [],
        },
        "malformed": {
            **common,
            "failure": "parse_error",
            "failure_http_status": malformed_status,
            "results": [],
        },
        "privacy": {
            "private_query_absent": True,
            "credential_sentinel_absent": True,
        },
    }


def _contract(
    *,
    request: dict[str, object],
    success_response: object,
    empty_response: object,
    expected: dict[str, object],
) -> dict[str, object]:
    return {
        "provider_contract_version": CONTRACT_VERSION,
        "request_contract": "search-query-v1",
        "response_contract": "provider-batch-v1",
        "request": request,
        "responses": {
            "success": success_response,
            "empty": empty_response,
            "error": {"error": {"kind": "rate_limit"}},
            "malformed": "<malformed>",
        },
        "expected": expected,
    }


_WEB_RESULT = {
    "url": RESULT_URL,
    "title": "Fixture",
    "description": "fixture snippet",
}
_CONTENT_RESULT = {
    "url": RESULT_URL,
    "title": "Fixture",
    "content": "fixture snippet",
}

_CONTRACTS: dict[ProviderName, dict[str, object]] = {
    ProviderName.BRAVE: _contract(
        request=_request(
            "GET",
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": QUERY, "count": 1},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": CREDENTIAL,
            },
        ),
        success_response={"web": {"results": [_WEB_RESULT]}},
        empty_response={"web": {"results": []}},
        expected=_expected(result=_success_result()),
    ),
    ProviderName.DUCKDUCKGO: _contract(
        request={
            "kind": "subprocess",
            "method": "SUBPROCESS",
            "args": ["<python>", "-m", "argus.providers.ddg_worker"],
            "kwargs": {
                "stdin": "PIPE",
                "stdout": "PIPE",
                "stderr": "PIPE",
                "env": None,
                "cwd": None,
                "limit": 1_048_576,
                "start_new_session": None,
                "close_fds": None,
                "shell": None,
                "executable": None,
                "preexec_fn": None,
                "pass_fds": None,
                "restore_signals": None,
                "extra_kwargs": {},
            },
            "stdin_payload": {
                "query": QUERY,
                "max_results": 1,
                "timelimit": None,
            },
        },
        success_response={
            "results": [{
                "href": RESULT_URL,
                "title": "Fixture",
                "body": "fixture snippet",
            }]
        },
        empty_response={"results": []},
        expected=_expected(
            result=_success_result(),
            empty_status=None,
            error_status=None,
            malformed_status=None,
        ),
    ),
    ProviderName.YAHOO: _contract(
        request=_request(
            "GET",
            "https://search.yahoo.com/search",
            params={"p": QUERY, "n": 1, "ei": "UTF-8"},
            client_headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate",
            },
            client_follow_redirects=False,
            call_timeout=2,
        ),
        success_response=(
            '<div class="dd algo-sr"><div class="compTitle">'
            f'<a href="{RESULT_URL}"><h3>Fixture</h3></a>'
            '</div><div class="compText">fixture snippet</div></div>'
        ),
        empty_response="<html><body>No results for fixture query</body></html>",
        expected=_expected(result=_success_result()),
    ),
    ProviderName.SEARXNG: _contract(
        request=_request(
            "GET",
            "http://127.0.0.1:8080/search",
            params={"q": QUERY, "format": "json", "pageno": 1},
            headers={"Accept": "application/json"},
            client_timeout=12,
        ),
        success_response={"results": [_CONTENT_RESULT]},
        empty_response={"results": []},
        expected=_expected(result=_success_result()),
    ),
    ProviderName.GITHUB: _contract(
        request=_request(
            "GET",
            "https://api.github.com/search/repositories",
            params={"q": QUERY, "per_page": 1},
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Argus-Search-Broker",
                "Authorization": f"token {CREDENTIAL}",
            },
        ),
        success_response={
            "items": [{
                "html_url": RESULT_URL,
                "full_name": "fixture/repository",
                "description": "fixture snippet",
            }]
        },
        empty_response={"items": []},
        expected=_expected(result=_success_result(
            title="fixture/repository",
            source_kind="repository",
        )),
    ),
    ProviderName.WOLFRAM: _contract(
        request=_request(
            "GET",
            "https://www.wolframalpha.com/api/v1/llm-api",
            params={"appid": CREDENTIAL, "input": QUERY, "maxchars": 1000},
        ),
        success_response="fixture answer",
        empty_response="",
        expected=_expected(
            result=_success_result(
                title="Wolfram|Alpha computed answer",
                snippet="fixture answer",
                source_kind="computed_answer",
                url="https://www.wolframalpha.com/",
            ),
            empty_status=501,
        ),
    ),
    ProviderName.EXA: _contract(
        request=_request(
            "POST",
            "https://api.exa.ai/search",
            headers={
                "x-api-key": CREDENTIAL,
                "Content-Type": "application/json",
            },
            json_body={
                "query": QUERY,
                "numResults": 1,
                "type": "auto",
                "contents": {"highlights": {"maxCharacters": 500}},
            },
        ),
        success_response={"results": [{
            "url": RESULT_URL,
            "title": "Fixture",
            "text": "fixture snippet",
        }]},
        empty_response={"results": []},
        expected=_expected(result=_success_result()),
    ),
    ProviderName.LINKUP: _contract(
        request=_request(
            "POST",
            "https://api.linkup.so/v1/search",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {CREDENTIAL}",
            },
            json_body={
                "q": QUERY,
                "depth": "fast",
                "outputType": "searchResults",
                "maxResults": 1,
            },
        ),
        success_response={"results": [{
            "url": RESULT_URL,
            "name": "Fixture",
            "content": "fixture snippet",
        }]},
        empty_response={"results": []},
        expected=_expected(result=_success_result()),
    ),
    ProviderName.PARALLEL: _contract(
        request=_request(
            "POST",
            "https://api.parallel.ai/v1/search",
            headers={
                "Content-Type": "application/json",
                "x-api-key": CREDENTIAL,
            },
            json_body={
                "objective": QUERY,
                "search_queries": [QUERY],
                "advanced_settings": {"max_results": 1},
            },
        ),
        success_response={"results": [{
            "url": RESULT_URL,
            "title": "Fixture",
            "excerpt": "fixture snippet",
        }]},
        empty_response={"results": []},
        expected=_expected(result=_success_result()),
    ),
    ProviderName.SEARCHAPI: _contract(
        request=_request(
            "GET",
            "https://www.searchapi.io/api/v1/search",
            params={
                "engine": "google",
                "q": QUERY,
                "num": 1,
                "api_key": CREDENTIAL,
            },
        ),
        success_response={"organic_results": [{
            "link": RESULT_URL,
            "title": "Fixture",
            "snippet": "fixture snippet",
        }]},
        empty_response={"organic_results": []},
        expected=_expected(result=_success_result()),
    ),
    ProviderName.SERPER: _contract(
        request=_request(
            "POST",
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": CREDENTIAL,
                "Content-Type": "application/json",
            },
            json_body={"q": QUERY, "num": 1},
        ),
        success_response={"organic": [{
            "link": RESULT_URL,
            "title": "Fixture",
            "snippet": "fixture snippet",
        }]},
        empty_response={"organic": []},
        expected=_expected(result=_success_result()),
    ),
    ProviderName.TAVILY: _contract(
        request=_request(
            "POST",
            "https://api.tavily.com/search",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {CREDENTIAL}",
            },
            json_body={
                "query": QUERY,
                "max_results": 1,
                "search_depth": "basic",
                "auto_parameters": False,
            },
        ),
        success_response={"results": [_CONTENT_RESULT]},
        empty_response={"results": []},
        expected=_expected(result=_success_result()),
    ),
    ProviderName.VALYU: _contract(
        request=_request(
            "POST",
            "https://api.valyu.ai/v1/search",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": CREDENTIAL,
            },
            json_body={
                "query": QUERY,
                "max_num_results": 1,
                "search_type": "web",
                "fast_mode": True,
            },
        ),
        success_response={"results": [_WEB_RESULT]},
        empty_response={"results": []},
        expected=_expected(result=_success_result()),
    ),
    ProviderName.YOU: _contract(
        request=_request(
            "GET",
            "https://api.you.com/v1/search",
            params={"query": QUERY, "count": 1, "safesearch": "moderate"},
            headers={"X-API-Key": CREDENTIAL},
        ),
        success_response={"results": {"web": [_WEB_RESULT]}},
        empty_response={"results": {"web": []}},
        expected=_expected(result=_success_result()),
    ),
}


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({
            key: _freeze(item) for key, item in value.items()
        })
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


GOLDEN_PROVIDER_CONTRACTS: Mapping[
    ProviderName, Mapping[str, object]
] = MappingProxyType({
    provider: _freeze(contract) for provider, contract in _CONTRACTS.items()
})
