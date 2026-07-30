# Raw Browser Fetch Compatibility Design

## Goal

Let authenticated HTTP callers such as Tix request a bounded browser-rendered
fetch through Argus and receive either captured inventory JSON or rendered HTML
without exposing provider credentials or bypassing Argus SSRF controls.

## HTTP contract

`POST /api/fetch-raw` accepts the Tix compatibility request:

```json
{
  "url": "https://seatgeek.com/<real-event-path>",
  "render": "browser",
  "cache": false,
  "extractors": ["raw_html"],
  "impersonate": "chrome",
  "egress": "residential",
  "timeout_seconds": 25,
  "headers": {}
}
```

The route uses the existing caller-token middleware, accepting `X-API-Key` or
Bearer authentication. Only public HTTP(S) URLs are admitted. Caller headers
that could alter routing or forward credentials are rejected.

A successful response has `status: "ok"`, the upstream document status,
non-empty `body`, final URL, SHA-256, actual render/extractor/egress metadata,
elapsed milliseconds, and `from_cache: false`. Argus prefers a same-site JSON
response containing listing or inventory data; otherwise it returns
`page.content()`.

Failures use `status: "error"` and a useful `http_status` when one was observed.
Timeouts, browser unavailability, unsafe redirects, upstream failures, and
empty bodies must never be presented as success.

## Implementation boundary

The API route is a thin presenter. Browser execution belongs in a focused raw
fetch module that reuses the managed Playwright browser and existing SSRF
validation. The compatibility endpoint does not enter the general extraction
fallback chain and therefore cannot invoke paid extractors.

## Tix integration

Tix sends the stored `SourceEvent.event_url`, not a fabricated SeatGeek path.
Its parser accepts direct listing JSON and searches rendered HTML scripts for
embedded JSON containing listing arrays. Fixture behavior remains unchanged.

## Verification

Unit tests cover request validation, authentication, JSON preference, HTML
fallback, redirects, explicit errors, and Tix parsing. After merge and immutable
Argus promotion, a remote request from the Tix environment targets a real
SeatGeek event. Completion requires a non-empty parseable body and a Tix poll
that persists at least one live listing.
