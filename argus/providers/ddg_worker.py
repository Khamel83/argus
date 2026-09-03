"""Bounded one-shot DuckDuckGo worker.

Production requests use the guarded HTML transport in this process.  The
parent still owns the hard deadline and terminates this process if a network
request does not return.  The native ``ddgs`` adapter remains only as a
hermetic fixture seam for compatibility tests.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from urllib.parse import urlencode

from ddgs.exceptions import RatelimitException, TimeoutException

from argus.acquisition.errors import AcquisitionFailureCode
from argus.acquisition.guarded import GuardedAcquisitionError, guarded_http_request
from argus.acquisition.models import CredentialPolicy, OperationClass, OriginProfile

MAX_IPC_BYTES = 1_048_576
MAX_QUERY_CHARS = 8_192
MAX_RESULTS = 20
DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"


def _validated_request(request: object) -> tuple[str, int, str | None] | None:
    """Validate the small request envelope shared by both worker paths."""

    if not isinstance(request, dict):
        return None
    query = request.get("query")
    max_results = request.get("max_results")
    timelimit = request.get("timelimit")
    if (
        not isinstance(query, str)
        or not query
        or len(query) > MAX_QUERY_CHARS
        or type(max_results) is not int
        or not 1 <= max_results <= MAX_RESULTS
        or (timelimit is not None and timelimit not in {"d", "w", "m", "y"})
    ):
        return None
    return query, max_results, timelimit


def _parse_search_html(html_text: str, max_results: int) -> list[dict[str, str]]:
    """Parse only the bounded DDG result projection.

    The ``ddgs`` package performs its own network calls through ``primp``.
    The production worker therefore uses the package only for its legacy
    hermetic fixture helper and parses the guarded HTML response locally.
    """

    from lxml import html as lxml_html

    tree = lxml_html.fromstring(html_text)
    nodes = tree.xpath(
        '//div[contains(concat(" ", normalize-space(@class), " "), " result ")]'
    )
    results: list[dict[str, str]] = []
    for node in nodes:
        anchors = node.xpath(
            './/a[contains(concat(" ", normalize-space(@class), " "), " result__a ")][@href]'
        )
        if not anchors:
            continue
        anchor = anchors[0]
        href = anchor.get("href", "")
        if not isinstance(href, str) or not href.startswith(("http://", "https://")):
            continue
        title = " ".join(anchor.text_content().split())
        snippets = node.xpath(
            './/*[contains(concat(" ", normalize-space(@class), " "), " result__snippet ")]'
        )
        body = " ".join(snippets[0].text_content().split()) if snippets else ""
        results.append({"href": href, "title": title, "body": body})
        if len(results) >= max_results:
            break
    return results


def _guarded_error_kind(error: GuardedAcquisitionError) -> str:
    """Map internal acquisition failures to the worker's stable IPC kinds."""

    code = error.failure.code
    if code is AcquisitionFailureCode.TIMEOUT:
        return "timeout"
    if code in {
        AcquisitionFailureCode.ACQUISITION_BLOCKED,
        AcquisitionFailureCode.POLICY_REJECTED,
        AcquisitionFailureCode.INVALID_REQUEST,
        AcquisitionFailureCode.AUTHENTICATION_REJECTED,
        AcquisitionFailureCode.BROWSER_POLICY_UNAVAILABLE,
    }:
        return "policy_rejected"
    return "provider_unavailable"


async def execute_guarded_request(
    request: object,
    *,
    request_fn: Callable[..., object] = guarded_http_request,
) -> tuple[dict[str, object], int]:
    """Run the production DuckDuckGo request through Guarded Acquisition.

    This function is intentionally separate from ``execute_request``.  The
    latter remains a fixture-only compatibility seam for native ``ddgs``
    result objects.  ``main`` never invokes that unguarded library path.
    """

    validated = _validated_request(request)
    if validated is None:
        return {"error": "invalid_request"}, 2
    query, max_results, timelimit = validated
    form: dict[str, object] = {"q": query, "b": "", "l": "us-en"}
    if timelimit:
        form["df"] = timelimit
    body = urlencode(form)
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(body.encode("utf-8"))),
        "User-Agent": "Argus/guarded-ddg",
    }
    try:
        response = request_fn(
            DDG_SEARCH_URL,
            method="POST",
            headers=headers,
            body=body,
            profile=OriginProfile.AUTHENTICATED_CONTENT,
            credential_policy=CredentialPolicy.ORIGIN_SCOPED,
            operation_class=OperationClass.DIRECT_HTTP,
            caller_principal="provider:duckduckgo",
            request_id="duckduckgo-worker",
            timeout=5.0,
        )
        if hasattr(response, "__await__"):
            response = await response
        status = getattr(response, "status_code", 0)
        if type(status) is not int:
            return {"error": {"kind": "provider_unavailable"}}, 1
        if status == 429:
            return {"error": {"kind": "rate_limit"}}, 1
        if status >= 400:
            return {"error": {"kind": "provider_unavailable"}}, 1
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            content = getattr(response, "content", b"")
            text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else ""
        try:
            results = _parse_search_html(text, max_results)
        except Exception:
            return {"error": {"kind": "parse_error"}}, 1
        return {"results": results}, 0
    except GuardedAcquisitionError as error:
        return {"error": {"kind": _guarded_error_kind(error)}}, 1
    except TimeoutError:
        return {"error": {"kind": "timeout"}}, 1
    except Exception:
        return {"error": {"kind": "provider_unavailable"}}, 1


def execute_request(
    request: object, *, ddgs_factory: Callable[[], object]
) -> tuple[dict[str, object], int]:
    try:
        validated = _validated_request(request)
        if validated is None:
            return {"error": "invalid_request"}, 2
        query, max_results, timelimit = validated
        rows = ddgs_factory().text(
            query,
            max_results=max_results,
            backend="duckduckgo",
            **({"timelimit": timelimit} if timelimit else {}),
        )
        results = []
        for row in rows:
            if not isinstance(row, dict):
                results.append({})
                continue
            href = row.get("href")
            title = row.get("title")
            body = row.get("body")
            if not isinstance(href, str):
                results.append({})
                continue
            results.append(
                {
                    "href": href,
                    "title": title if isinstance(title, str) else "",
                    "body": body if isinstance(body, str) else "",
                }
            )
            if len(results) == max_results:
                break
        payload: dict[str, object] = {"results": results}
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_IPC_BYTES:
            return {"error": "response_too_large"}, 3
        return payload, 0
    except RatelimitException:
        return {"error": {"kind": "rate_limit"}}, 1
    except TimeoutException:
        return {"error": {"kind": "timeout"}}, 1
    except Exception:
        return {"error": {"kind": "library_failure"}}, 1


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_IPC_BYTES + 1)
    if len(raw) > MAX_IPC_BYTES:
        return 2
    try:
        request = json.loads(raw)
    except Exception:
        return 2
    # ``ddgs`` uses an internal primp client and cannot prove Argus's DNS and
    # address-pinning contract.  Production execution is therefore always the
    # guarded HTML path above.  ``execute_request`` remains available only for
    # hermetic compatibility fixtures and does not run here.
    import asyncio

    payload, returncode = asyncio.run(execute_guarded_request(request))
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_IPC_BYTES:
        return 3
    sys.stdout.buffer.write(encoded)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
