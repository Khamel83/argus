"""Bounded one-shot DuckDuckGo worker.

The parent process owns the hard deadline and terminates this process if the
blocking DDGS library does not return.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from ddgs.exceptions import RatelimitException, TimeoutException

MAX_IPC_BYTES = 1_048_576
MAX_QUERY_CHARS = 8_192
MAX_RESULTS = 20


def execute_request(
    request: object, *, ddgs_factory: Callable[[], object]
) -> tuple[dict[str, object], int]:
    try:
        if not isinstance(request, dict):
            return {"error": "invalid_request"}, 2
        query = request["query"]
        max_results = request["max_results"]
        timelimit = request.get("timelimit")
        if (
            not isinstance(query, str)
            or not query
            or len(query) > MAX_QUERY_CHARS
            or type(max_results) is not int
            or not 1 <= max_results <= MAX_RESULTS
            or (timelimit is not None and timelimit not in {"d", "w", "m", "y"})
        ):
            return {"error": "invalid_request"}, 2
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
    from ddgs import DDGS

    payload, returncode = execute_request(request, ddgs_factory=DDGS)
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_IPC_BYTES:
        return 3
    sys.stdout.buffer.write(encoded)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
