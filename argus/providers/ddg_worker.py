"""Bounded one-shot DuckDuckGo worker.

The parent process owns the hard deadline and terminates this process if the
blocking DDGS library does not return.
"""

from __future__ import annotations

import json
import sys

MAX_IPC_BYTES = 1_048_576
MAX_QUERY_CHARS = 8_192
MAX_RESULTS = 20


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_IPC_BYTES + 1)
    if len(raw) > MAX_IPC_BYTES:
        return 2
    try:
        request = json.loads(raw)
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
            return 2
        from ddgs import DDGS

        rows = DDGS().text(
            query,
            max_results=max_results,
            backend="duckduckgo",
            **({"timelimit": timelimit} if timelimit else {}),
        )
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            href = row.get("href")
            title = row.get("title")
            body = row.get("body")
            if not isinstance(href, str):
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
        encoded = json.dumps({"results": results}, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(encoded) > MAX_IPC_BYTES:
            return 3
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception:
        sys.stdout.write('{"error":"worker_failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
