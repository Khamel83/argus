# ADR 0006: HTTP and MCP compatibility contract

- Status: Accepted
- Date: 2026-07-27
- Issue: [#66](https://github.com/Khamel83/argus/issues/66)
- Parent: [#58](https://github.com/Khamel83/argus/issues/58)
- Depends on:
  [the stability scorecard](../scorecards/stability-competitive.md),
  [the provider/extraction drift inventory](../research/2026-07-26-provider-extraction-contract-drift.md),
  [the primary-source transport research](../research/2026-07-27-http-mcp-compatibility-primary-sources.md),
  [the compatibility matrix](../research/2026-07-27-http-mcp-compatibility-matrix.md),
  [ADR 0002](0002-bounded-retrieval-plan-cache-identity.md),
  [ADR 0003](0003-provider-aware-freshness-provenance-ranking.md),
  [ADR 0004](0004-no-spend-provider-readiness.md),
  and
  [ADR 0005](0005-structured-extraction-outcome-composition.md)

## Decision

Argus will keep the current unversioned `/api/*` shapes as the legacy
HTTP contract and introduce the evidence-rich contract at explicit
`/api/v2/*` routes. Both route families execute through the same HTTP
authority and accepted-operation modules. They differ only at a
`TransportPresenter` seam:

```text
authenticated request
  -> one execution and durable acceptance
  -> AcceptedOperation
       -> LegacyHttpPresenter  -> /api/*
       -> EvidenceHttpPresenter -> /api/v2/*
       -> LegacyMcpPresenter    -> current names + {"result": text}
       -> EvidenceMcpPresenter  -> *_v2 + v2 structuredContent
       -> CliPresenter          -> human text or exact JSON
```

No presenter may call a provider, extractor, cache fill, persistence mapper,
or rejection classifier. HTTP remains the only production execution
authority. MCP and CLI remain adapters over authenticated HTTP.

The version-2 HTTP outer envelope is stable and mandatory:

```json
{
  "contract_version": "2.0",
  "outcome": "success",
  "request_id": "bounded-correlation-id",
  "result": {},
  "error": null
}
```

Issue #65 chooses the exact route-specific evidence object placed in
`result`; it cannot change this outer envelope or the outcome/status rules
below. ADR 0005's accepted extraction outcome is carried without
reclassification.

Streamable HTTP at `/mcp` is the canonical remote MCP transport. It preserves
the current stateful protocol-session behavior for compliant old clients,
while remaining stateless with respect to Argus execution and durable data.
Each new version-2 tool returns the same version-2 HTTP envelope as MCP
`structuredContent` and retains a bounded text rendering in `content`.
Existing tool names retain their current `{"result": "<text>"}` structured
schema so old structured clients are not silently broken.

## Why explicit `/api/v2`

Three approaches were considered.

### Mutate `/api/*` additively

Optional fields could be appended to every current response. That preserves
many permissive JSON clients, but it cannot truthfully change current
200-with-error behavior, generic exception handling, or MCP prose without
breaking strict clients. It also leaves no unambiguous capability boundary.

Rejected as the sole target. Small nullable additions remain allowed under the
legacy rules, but the evidence-rich contract does not depend on them.

### Negotiate a vendor media type on the same path

`Accept: application/vnd.argus+json;version=2` would avoid new paths. It also
introduces cache `Vary` behavior, proxy-dependent negotiation, and a dangerous
default when clients send broad `*/*`. A missed header could silently select
the wrong semantics for a provider-spending POST.

Rejected. Contract selection must be visible in the URL before execution.

### Add `/api/v2/*` as a presentation adapter

The route family is explicit, debuggable, cache-safe, and cannot be selected
accidentally by an old client. The execution implementation is not duplicated:
both route families cross the same deep accepted-operation interface and only
their presenters differ.

Accepted.

## Contract discovery and negotiation

`GET /api/capabilities` remains an authenticated, unversioned discovery
document. Its existing fields remain readable. It may add:

```json
{
  "http_contracts": [
    {
      "version": "1",
      "base_path": "/api",
      "legacy": true
    },
    {
      "version": "2.0",
      "base_path": "/api/v2",
      "legacy": false
    }
  ],
  "mcp_contract": {
    "transport": "streamable-http",
    "endpoint": "/mcp",
    "argus_tool_contract_versions": ["1", "2.0"],
    "version_2_tool_suffix": "_v2"
  }
}
```

New Argus MCP and CLI adapters read capabilities before their first execution
request:

1. if version 2 is advertised, they use `/api/v2`;
2. if the current legacy capabilities response has no `http_contracts`, they
   use `/api`;
3. if discovery is unavailable or malformed, they return `unready` and do not
   speculate by issuing a provider-spending request; and
4. after selecting version 2, they never retry a failed version-2 POST against
   version 1.

A missing version-2 route is a safe fallback signal only when discovery
already proved that the server is a legacy server. Timeouts, connection
failure, 5xx, invalid bodies, or a server that advertised version 2 never
trigger a second execution request.

An adapter may cache a valid capability document only in process, for at most
60 seconds, keyed by authority origin and deployment ID when advertised. A
deployment-ID change invalidates it immediately. A cache miss or expired entry
is resolved before execution; an error never overwrites a valid entry or
authorizes speculative fallback. Using a still-valid legacy entry after a
server upgrade remains safe because version 1 stays supported, and it
converges to version 2 at expiry without duplicating an operation.

## Legacy HTTP contract

The unversioned `/api/*` contract is compatibility version 1.

The following are frozen:

- existing route and method;
- existing successful status;
- existing field name, type, nullability, default, and meaning;
- existing array ordering when order is semantically meaningful;
- current authentication carriers and legacy error body shape; and
- current human CLI/MCP text derived from those responses, except that
  security-sensitive values may be redacted more aggressively.

Version 1 may add only:

- optional fields with a backward-compatible default;
- new object members beneath a new optional field;
- new list entries where clients already treat the list as open-ended; and
- response headers that do not change representation selection.

The following require a new major contract and are never called additive:

- removing or renaming a field;
- changing a field type, nullability, default, or semantic unit;
- adding a required field;
- changing the meaning or ordering of an existing list;
- adding a value to an enum that clients may exhaustively switch over;
- changing a successful or failure status;
- wrapping the existing object in a new envelope; or
- turning a formerly non-executing request into an executing request.

JSON object member order is never contractual. Security and privacy fixes may
reject a request that was previously unsafe, tighten a bound, or redact a
leak; compatibility does not preserve insecure behavior.

Version 1 has no scheduled removal. Removing it requires a separate accepted
decision, a new Argus major release, explicit migration evidence, and a human
merge/release gate.

The existing MCP tool names are also compatibility version 1. Their names,
inputs, text content, and SDK-derived `{"result": "<text>"}` output schema
remain frozen under the same additive/security rules. A text-only
compatibility block cannot justify replacing that structured schema.

## Version-2 HTTP envelope

Every matched `/api/v2` route returns the outer envelope unless HTTP requires a
bodyless response, such as CORS preflight.

### Required fields and invariants

| Field | Rule |
|---|---|
| `contract_version` | exact string `2.0` |
| `outcome` | one canonical scorecard outcome |
| `request_id` | same bounded value as `X-Request-ID` |
| `result` | route-specific accepted object or `null` |
| `error` | bounded error object or `null` |

For `success`, `degraded`, and `empty`, `error` is null and `result` is an
object. For every failure outcome, `error` is non-null. `result` may still be
an object when accepted partial evidence must remain visible, such as search
results retained after an extraction-floor failure. A failure result cannot
be treated as an accepted synthesized answer or delivery.

The response also carries:

```text
Content-Type: application/json
Argus-Contract-Version: 2.0
X-Request-ID: <same value as body>
```

### Error object

```json
{
  "type": "urn:argus:problem:invalid_request",
  "title": "Invalid request",
  "status": 422,
  "detail": "Request is invalid",
  "instance": "urn:argus:request:bounded-correlation-id",
  "code": "invalid_request",
  "retryable": false,
  "retry_after_seconds": null
}
```

The member follows RFC 9457 problem-detail field semantics and adds bounded
Argus extensions. It is deliberately a member of the stable
`application/json` envelope, not a standalone `application/problem+json`
representation: a failed accepted operation may also need to retain
caller-authorized partial evidence in `result`, and MCP `structuredContent`
for a version-2 tool must be the exact same object.

`type` is a stable `urn:argus:problem:<code>` identifier. `title` is stable
display text for the type. `status` equals the actual HTTP status.
`detail` is bounded safe display text for this occurrence. `instance` is
derived only from the bounded `request_id`; it contains no route, query, URL,
session, or credential.

`code` is a stable, bounded machine category. It normally equals `outcome`;
transport admission may use a narrower code such as `rate_limited`,
`payload_too_large`, `unsupported_media_type`, `route_not_found`, or
`session_not_found` while preserving the canonical outcome. `detail` is not a
raw exception, request value, provider body, credential, URL, content excerpt,
or validation input echo. Clients ignore unknown problem extensions.

`retryable=true` is allowed only when an authoritative reset/readiness fact and
the caller-owned retry policy permit another bounded attempt.
`retry_after_seconds` is present only with such authority. Presenters never
initiate the retry.

Validation detail, when a route includes it inside `result`, contains only
bounded field locations and allowlisted reason codes. It never echoes rejected
input.

## Canonical HTTP status mapping

| Condition | Outcome | HTTP |
|---|---|---:|
| accepted success | `success` | 200 |
| accepted above-floor partial evidence | `degraded` | 200 |
| accepted eligible no-result search | `empty` | 200 |
| malformed JSON/framing before operation acceptance | `invalid_request` / `malformed_request` | 400 |
| semantic request validation | `invalid_request` | 422 |
| request body exceeds the fixed bound | `invalid_request` / `payload_too_large` | 413 |
| unsupported request media type | `invalid_request` / `unsupported_media_type` | 415 |
| missing or invalid caller authentication | `authentication_rejected` | 401 |
| caller/profile/provider/security policy denies execution | `policy_rejected` | 403 |
| unknown route | `invalid_request` / `route_not_found` | 404 |
| unknown caller-scoped application resource | `unready` / `session_not_found` | 404 |
| conflicting reuse of a declared idempotency key | `invalid_request` / `idempotency_conflict` | 409 |
| HTTP admission rate limit | `unready` / `rate_limited` | 429 |
| all eligible search providers failed | `providers_failed` | 502 |
| extraction produced no above-floor artifact | `extraction_failed` | 502 |
| durable acceptance failed | `persistence_failed` | 503 |
| no eligible/ready execution path | `unready` | 503 |
| bounded operation deadline expired | `timeout` | 504 |
| unclassified safe server failure | `unready` / `internal_failure` | 503 |

`401` includes `WWW-Authenticate: Bearer`. `405` includes `Allow`. `429`
includes `Retry-After` only when authoritative. An operation that already
began cannot be relabeled `invalid_request` or `authentication_rejected`
because an upstream provider rejected Argus's request or credential.

Framework-generated HTML error pages are forbidden on `/api/v2`. Unmatched
routes, method errors, body parsing, validation, and safe internal failures all
use the JSON envelope when a body is permitted.

## Request identity and application sessions

`X-Request-ID` is correlation, not idempotency or authentication. Argus accepts
only the existing bounded safe form; otherwise it replaces it. The response
header and version-2 body always agree.

The search payload's `session_id` is a durable Argus retrieval-session
resource. It is not an MCP transport session. Version 2 requires:

- a bounded opaque identifier;
- ownership by the authenticated caller identity;
- the same identity on every continuation;
- `404 session_not_found` for unknown, expired, or other-caller identifiers;
- no identity authority from the caller-supplied `caller` label; and
- no session ID in logs beyond a bounded opaque/hash reference.

The `Mcp-Session-Id` header is never copied into `SearchRequest.session_id`,
and a retrieval session never authenticates an MCP request.

## Authentication

### HTTP authority

Production caller and admin routes authenticate even on loopback. Bearer is
the canonical advertised carrier:

```text
Authorization: Bearer <scoped token>
```

Legacy `X-API-Key` and `X-Admin-API-Key` remain readable on version 1. Version
2 rejects ambiguous requests that present more than one credential carrier;
it never guesses precedence. Cookies, query parameters, URLs, and MCP session
IDs are not credential carriers.

The authenticated principal, not a request `caller` string, owns tier policy,
session access, spend attribution, and audit identity. The caller label
remains non-authoritative display/evidence metadata.

### MCP listener

Stdio has no network-listener credential, but its process must possess a
scoped HTTP-authority credential. A network MCP listener requires bearer
authentication on initialize, POST, GET, and DELETE whenever it is:

- bound to a non-loopback address;
- reachable through a reverse proxy or tunnel; or
- explicitly configured as remotely exposed.

Exposure is not inferred only from the bind address. A loopback-bound process
behind a proxy is remote and must fail startup without listener auth.

The listener accepts only `Authorization: Bearer`. It forwards the verified
token to the HTTP authority so the same identity and tier policy apply end to
end. `Mcp-Session-Id` is not authentication. Every stateful transport session
is bound to the principal that initialized it; a different principal receives
the same not-found behavior as an unknown session.

Argus uses the private static-bearer profile and does not advertise fake OAuth
issuer, authorization, registration, or discovery endpoints. A future OAuth
profile requires a separate decision and real metadata endpoints.

## Host, Origin, CORS, and DNS rebinding

Authentication is necessary but does not replace Host and Origin validation.
CORS is a browser response policy and does not prevent a forged cross-origin
request from executing, so the execution guard runs before auth, rate limits,
body parsing, or provider work.

A shared `TransportSecurityGuard` applies to production HTTP and network MCP:

1. validate the actual HTTP `Host` header against an explicit exact allowlist;
2. ignore `Forwarded` and `X-Forwarded-*` as security authority unless a
   separately configured trusted ingress has already rewritten the actual
   request;
3. allow a missing `Origin` for non-browser clients;
4. when `Origin` is present, require an exact scheme/host/port allowlist match;
5. reject `Origin: null`, wildcard origins, suffix-only matches, userinfo,
   malformed origins, and reflected arbitrary origins;
6. return 421 for invalid/missing Host and 403 for invalid Origin; and
7. perform no provider, extractor, persistence, or session work on rejection.

Loopback defaults admit only `localhost:*`, `127.0.0.1:*`, and `[::1]:*`.
Remote production requires explicit canonical host values and an explicit
Origin policy, and fails startup when either policy is absent. The explicit
Origin policy may be empty, meaning only requests with no Origin are admitted.
Allowed Origin values are separate from allowed Host values.

If browser access is configured, CORS:

- uses the same exact Origin allowlist;
- never uses `*` or `allow_credentials=true`;
- allows only required methods;
- allows only `Authorization`, `Content-Type`, `MCP-Protocol-Version`,
  `Mcp-Session-Id`, `Last-Event-ID`, and the applicable Argus request headers;
- exposes `Mcp-Session-Id`, `X-Request-ID`,
  `X-Argus-Deployment-ID`, `Argus-Contract-Version`, and authoritative
  `Retry-After`; and
- wraps the full application so safe error responses receive the same CORS
  policy.

No configured browser origins means no CORS headers. Non-browser bearer
clients remain supported.

## Streamable HTTP MCP

### Protocol and methods

Argus pins the MCP SDK to the audited major line (`mcp>=1,<2`) and locks an
exact resolved version in each release. MCP SDK v2 adoption requires a
separate compatibility decision. The canonical published target is
`2025-11-25`; Argus also contract-tests these older compatibility revisions
supported by the pinned SDK:

```text
2024-11-05
2025-03-26
2025-06-18
2025-11-25
```

Dropping one requires an Argus major release and explicit compatibility
decision. `MCP-Protocol-Version` selects MCP protocol behavior only; it never
selects the Argus HTTP or tool-result contract.

The single `/mcp` endpoint supports:

- `POST` for JSON-RPC messages;
- `GET` for a server-sent-event stream;
- `DELETE` for explicit protocol-session termination; and
- `OPTIONS` only as CORS preflight.

POST requires `Content-Type: application/json` and an `Accept` value covering
both `application/json` and `text/event-stream`. GET requires
`text/event-stream`. Unsupported methods return 405 with `Allow`.
An accepted JSON-RPC notification or response receives HTTP 202 with no body.
A JSON-RPC request receives either an `application/json` response or an SSE
stream as negotiated.

The fixed request-body maximum is 4 MiB. Oversize requests return 413 before
JSON parsing or tool execution.

### Stateful transport session, stateless execution

Argus preserves stateful MCP transport sessions for compatibility:

1. an authenticated initialize request has no session header;
2. the server negotiates a supported protocol version and returns an opaque
   `Mcp-Session-Id`;
3. subsequent POST, GET, and DELETE requests carry that session ID and the
   same principal;
4. a missing required ID is 400;
5. an unknown, expired, terminated, or wrong-principal ID is 404;
6. DELETE terminates the session and later use is 404; and
7. restart of the MCP adapter invalidates transport sessions, after which the
   client reinitializes.

For legacy clients, a missing post-initialize `MCP-Protocol-Version` is treated
as `2025-03-26`. New clients send the negotiated version. An unsupported
version is 400 and never reaches a tool.

Sessions are process-local routing state only. They do not own broker,
provider, browser, cache, budget, retrieval-session, or database state. The
target bounds are:

```text
idle timeout: 30 minutes
maximum active sessions: 256
session identifier: opaque, at most 128 visible ASCII characters
```

Session identifiers are generated with a cryptographically secure random
source, are unpredictable, and are regenerated on the vanishingly unlikely
event of an active-ID collision. Collision check and registration are one
atomic registry operation; concurrent initializes cannot both claim an ID.
Expired-session reclamation, capacity check/reservation, collision check, and
insertion are one atomic registry transaction (or equivalent semaphore-backed
reservation), so concurrent initializes cannot exceed the capacity bound. A
sequential ID, bearer-token derivative, request ID, retrieval session ID, or
caller-provided value is forbidden.

Expired sessions are reclaimed before every capacity decision and by a
periodic bounded sweep. Valid sessions are never silently evicted by LRU.
After reclamation, a registry still at capacity rejects a new initialize with
transport HTTP 503 and bounded retry guidance; existing sessions continue.

Version 2 does not promise SSE event replay or `Last-Event-ID` resumability.
Disconnect is visible and is not cancellation by itself, but Argus performs
no hidden transport retry. Resumability requires a future bounded event-store
decision that keeps the HTTP authority as the only durable execution owner.

### MCP error layers

Three error layers remain distinct:

| Layer | Representation |
|---|---|
| listener auth, Host, Origin, media type, method, session | HTTP status; bounded JSON/JSON-RPC body as required by MCP |
| malformed JSON-RPC, unknown tool, malformed `tools/call` protocol parameters, unsupported protocol | JSON-RPC error |
| JSON-RPC-valid call whose arguments fail the advertised tool `inputSchema` | SDK tool error with `isError=true`, bounded text, and no Argus envelope |
| schema-valid tool call rejected by Argus input/domain rules | MCP tool result with `isError=true` |
| accepted Argus operation outcome | MCP tool result |

Provider, extraction, policy, timeout, persistence, and readiness outcomes are
tool results, not JSON-RPC protocol failures. This lets MCP-aware clients and
models receive the same evidence as HTTP callers.

Transport capacity exhaustion is an HTTP 503 before a tool exists and has no
version-2 envelope. An HTTP-authority 503 reached through a version-2 tool is a
successful JSON-RPC exchange carrying `isError=true` and the exact canonical
envelope. Clients can therefore distinguish these layers without parsing
display text.

Input-schema validation happens in the pinned SDK before the version-2 tool
adapter can create an Argus accepted operation. It therefore cannot truthfully
claim a version-2 envelope or request ID. The SDK error must remain bounded,
must not echo rejected values, and performs no HTTP-authority or provider
work. Once arguments pass `inputSchema`, Argus domain rejection is normalized
to the version-2 `invalid_request` envelope.

## MCP structured content and old clients

MCP has no Argus tool-schema version negotiation. Replacing the SDK-derived
structured schema beneath an existing tool name would therefore be breaking.
Argus keeps every current version-1 tool name and input field unchanged,
including the core names `search_web`, `recover_url`, `expand_links`, and
`extract_content`. Those tools keep their current text result and
`{"result": "<text>"}` structured shape.

Evidence-rich tools use explicit new names:

```text
search_web_v2
recover_url_v2
expand_links_v2
extract_content_v2
```

Each version-2 tool uses the corresponding version-1 inputs unless its
advertised schema explicitly adds an optional field. Its result contains:

```text
content:
  - type: text
    text: <bounded legacy-compatible rendering>
structuredContent:
  <exact version-2 HTTP envelope>
isError:
  true only for canonical failure outcomes
```

The tool advertises an `outputSchema` matching `structuredContent`.
`success`, `degraded`, and `empty` use `isError=false`; all other canonical
outcomes use `isError=true`. Degraded and empty remain visibly labeled in
both structured and text forms.

Old clients keep calling the original names and receive the exact structured
shape they already understand. Clients selecting `*_v2` may ignore
`structuredContent` and still read text. Version-2 clients do not parse
Markdown to recover URLs, outcome, provenance, rejection, quality,
completeness, spend, cache, or trace references. The text presenter may become
more explicit but cannot claim success when the structured outcome failed.

Adding a new optional structured field is additive. Changing a required
field, outcome meaning, tool input, or output schema requires a new Argus tool
contract major version and a new explicit tool-name lane. Adding an
attempt/outcome enum member also requires the exhaustive mapping and
policy-version fixture required by ADR 0005.

### Legacy SSE transport

Streamable HTTP is canonical, but the currently selectable legacy SSE
transport remains compatibility version 1 at the SDK-default `GET /sse` and
`POST /messages/` paths. It receives the same bearer, Host, Origin, CORS,
body-bound, principal, and no-fake-OAuth protections as `/mcp`; it does not
gain version-2 transport-session or resumability promises.

No new client is directed to legacy SSE. Removing or changing those paths is a
major compatibility break requiring a separate accepted decision, migration
evidence, and human release gate.

## CLI compatibility

Old installed CLIs continue to use version 1. New CLIs negotiate through
capabilities and prefer version 2.

- human output preserves the existing core result text and adds visible
  outcome/request/evidence labels;
- `--json` emits the exact version-2 envelope and nothing else on stdout;
- diagnostics and logs go to stderr;
- `success`, `degraded`, and `empty` exit 0;
- every canonical operation failure exits 1; and
- local CLI syntax/usage errors remain Click exit 2.

The CLI never silently retries a version-2 execution against version 1.

## Privacy and bounds

Public transport evidence may contain stable codes, bounded provider names,
run/reference IDs, quality/completeness facts, provenance, spend summaries,
and accepted content already authorized for that route.

It never contains:

- bearer/API/admin credentials, cookies, authorization headers, or session
  secrets;
- raw provider, database, HTTP, SDK, validation, or exception text;
- rejected request values;
- provider-native bodies or headers;
- unbounded URLs/content inside an error object;
- internal file paths, environment values, or host inventory; or
- a full trace copied into logs.

The HTTP-authority response remains capped at 11 MiB for MCP/CLI adapters.
Version 2 does not promise arbitrary result streaming or chunk reconstruction:
the accepted object must fit the bound before presentation. HTTP transfer
framing and MCP SSE may stream bytes, but they do not relax that semantic
bound. Server write deadlines and transport backpressure bound slow consumers;
a future resumable/chunked artifact contract requires a separate decision.
Every identifier, label, signal, retry value, and trace reference obeys its
own accepted ADR bound. A presenter rejects an invalid accepted object rather
than truncating identity or converting a failure into success.

## Compatibility and migration

The mechanical implementation order is:

1. add hermetic fixtures for legacy shapes and current MCP initialize;
2. introduce the accepted-operation and transport-presenter interfaces;
3. add version-2 envelope/status/error rendering without changing version 1;
4. add capability negotiation to HTTP adapters;
5. add the shared Host/Origin guard and explicit remote-listener admission;
6. bind/bound MCP transport sessions;
7. freeze legacy MCP names/schemas, add version-2 tool names with structured
   content, and secure the legacy SSE paths;
8. migrate CLI rendering and exit behavior;
9. port every route/tool/workflow; and
10. atomically switch production adapters after the full compatibility suite
    passes.

Intermediate commits may exist only in isolation. No mixed presentation
authority may merge or deploy. There is no dual execution, dual persistence,
or fallback POST.

## Verification obligations

Hermetic tests must prove:

- every version-1 golden response remains byte-semantically compatible except
  permitted optional fields, safe redaction, and security rejection;
- an architecture/dependency test proves presenters cannot import or invoke
  providers, extractors, cache-fill, persistence, or rejection classifiers;
- version 2 always emits the required envelope/header identity;
- every canonical outcome maps to exactly one HTTP status and MCP `isError`;
- partial accepted evidence remains visible on failures without becoming an
  accepted synthesis/delivery;
- malformed/oversize/validation input never echoes rejected values;
- bearer identity overrides all caller labels;
- ambiguous version-2 credential carriers are rejected;
- retrieval sessions are caller-scoped and are never MCP transport sessions;
- Host and Origin rejection occurs before parsing, auth, rate-limit spend,
  provider, extraction, persistence, or session creation;
- loopback, canonical remote Host, missing non-browser Origin, allowed browser
  Origin, malicious Origin, `Origin: null`, suffix tricks, wildcard ports,
  and forwarded-header spoofing have fixed results;
- remote or proxy-exposed MCP fails startup without auth and explicit Host and
  Origin policies;
- initialize negotiates each promised MCP protocol version;
- Streamable HTTP acknowledges accepted JSON-RPC notifications/responses with
  202 and no body;
- missing, wrong, expired, wrong-principal, terminated, capacity-limited, and
  post-restart MCP sessions have fixed behavior;
- session IDs are cryptographically unpredictable, bounded, collision-checked,
  and never derived from another identifier; concurrent admission cannot
  exceed capacity;
- POST/GET/DELETE/OPTIONS, Accept, Content-Type, protocol version, and 4 MiB
  body bounds match the transport contract;
- JSON-RPC errors and Argus tool errors never collapse into one another;
- schema-invalid tool arguments return a bounded SDK `isError=true` result
  without `structuredContent`, rejected-value echo, or HTTP execution;
- every version-2 tool returns schema-valid `structuredContent` plus bounded
  text after its input schema accepts the call;
- legacy tool names retain their current text and `{"result": text}` schema;
- legacy SSE paths retain their current transport shape under the shared
  security boundary;
- capability negotiation selects v2, safely recognizes a legacy server, and
  never retries an ambiguous execution request; its cache is origin/deployment
  scoped, expires within 60 seconds, and invalidates on deployment change;
- CLI stdout/stderr and exit behavior are exact; and
- no public response/log contains injected credential, cookie, raw error,
  request-body, environment, path, or provider-body sentinel.

No live provider, paid credit, production deployment, browser origin, or
secret is required for this verification.

## Consequences

### Positive

- Existing clients keep their current paths and shapes.
- New callers receive truthful statuses and one evidence-rich envelope.
- MCP exposes typed results without abandoning text-only clients.
- Search sessions, MCP sessions, authentication, and request IDs cannot be
  confused.
- DNS-rebinding protection happens before any spend or side effect.
- Version selection cannot accidentally duplicate a provider call.

### Cost

- Version 1 remains supported as a presentation adapter.
- Version 2 requires explicit route registration and golden fixtures.
- Remote MCP deployments need explicit Host/Origin/auth configuration.
- Stateful protocol sessions require bounded process-local accounting and
  reinitialization after MCP restart.

These costs are smaller than breaking old clients or allowing each surface to
reconstruct outcomes independently.

## Amendment — 2026-08-24: verified 2026-07-28 conformance

This ADR predates protocol revision `2026-07-28`. That revision removes the GET
stream endpoint and protocol-level sessions, and mirrors selected body fields
into required request headers. Argus serves both eras from the single `/mcp`
endpoint via one `streamable_http_app(stateless_http=True)`; the SDK routes on
the `MCP-Protocol-Version` header, so there is no Argus-side era branch and no
second route.

The behavior below was measured against the pinned SDK, not inferred. It is
pinned by `tests/test_mcp_2026_07_28_contract.py` so an SDK regression fails
the suite rather than silently changing what real clients receive.

| Requirement | Observed | Owner |
|---|---|---|
| Single POST endpoint; GET/DELETE rejected | `405` | SDK |
| No session minted or echoed; a client-supplied `Mcp-Session-Id` is ignored | verified | SDK |
| `server/discover` implemented | `200` | SDK |
| Header/body protocol mismatch | `400` + `-32020` | SDK |
| Missing or mismatched `Mcp-Method` / `Mcp-Name` | `400` + `-32020` | SDK |
| Unsupported version lists supported revisions | `400` + `-32022` with `data.supported` | SDK |
| Unknown method | `404` + `-32601` | SDK |
| Modern body with no `MCP-Protocol-Version` header | `400` + `-32020` | **Argus** |

Only the last row is implemented by Argus. The specification permits treating a
header-less request as `2025-03-26` for pre-2025-06-18 clients, and the SDK does
exactly that. A body carrying `_meta.io.modelcontextprotocol/protocolVersion`
is unambiguously a modern request, so applying that fallback would silently
downgrade it — the one thing the compatibility contract must never do. Argus
rejects that combination and leaves genuine header-less legacy `initialize`
traffic untouched.

### Known constraint: `stateless_http=True` and the legacy back channel

`stateless_http=True` affects only the legacy leg. It removes sticky-routing
requirements for legacy clients but disables server-initiated requests on that
leg, raising `NoBackChannelError` if a tool needs one. No Argus tool currently
accepts a `Context` parameter, so nothing depends on the back channel today.
A future tool that emits progress notifications or uses sampling/elicitation
will fail for legacy clients under this setting, and that is a deliberate
trade-off to re-open rather than a bug to work around.
