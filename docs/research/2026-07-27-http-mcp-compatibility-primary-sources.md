# HTTP and MCP compatibility: primary-source research

- Date: 2026-07-27
- Issue: [#66](https://github.com/Khamel83/argus/issues/66)
- Scope: research for a decision record; no implementation or deployment
- Baseline: the issue #66 worktree, including the accepted
  [stability scorecard](../scorecards/stability-competitive.md) and
  [ADR 0005](../adr/0005-structured-extraction-outcome-composition.md)

## Executive conclusion

Argus can add evidence-rich HTTP and MCP results without breaking existing
successful callers, but the compatibility promise needs two layers:

1. preserve the current unversioned `/api` success fields and current MCP tool
   names/arguments as a legacy presentation; and
2. add one typed semantic projection whose outcome, evidence, rejection, and
   acceptance facts are identical across HTTP and MCP.

For HTTP, additive optional response members are compatible only when Argus
also promises that existing member names, types, nullability, and meanings do
not change. Error responses need one machine-readable shape; RFC 9457 gives a
sound extension model because clients must ignore unknown extension members.
For MCP, the current protocol already provides the correct migration mechanism:
return `structuredContent` conforming to an advertised `outputSchema` and also
return the serialized/readable representation in a text content block for old
clients.

The deployed transport posture is not yet sufficient for that promise.
Argus requires bearer authentication when the MCP listener is non-loopback,
but it does not configure the MCP SDK's remote Host/Origin allowlists. In the
locked SDK, automatic DNS-rebinding protection is enabled only for a loopback
bind; a non-loopback bind with no explicit transport-security settings disables
that protection. The HTTP authority likewise has optional CORS but no trusted
Host middleware. CORS is not a substitute: Starlette passes ordinary requests
through and merely controls which response headers browsers expose.

The compatibility target should be the current MCP revision,
`2025-11-25`, not the draft. The draft proposes removing transport-level
sessions, while the published version still defines `MCP-Session-Id`.
Therefore Argus should not make application semantics depend on an MCP
transport session. Its search refinement `session_id` remains an explicit
Argus request field.

## Method and source boundary

This note uses only:

- the current published MCP specification and official MCP Python SDK;
- IETF/RFC Editor standards;
- official FastAPI and Starlette documentation/source; and
- the issue #66 worktree's source, tests, and dependency lock.

No Argus HTTP/MCP service, provider, extractor, production host, credential, or
secret was accessed. Repository behavior below is a static or hermetic source
finding, not a live-deployment claim.

The worktree locks:

- MCP Python SDK `1.27.0`;
- FastAPI `0.136.1`;
- Starlette `1.0.0`; and
- Pydantic `2.13.3`

in [`uv.lock`](../../uv.lock). The project requirement is only `mcp>=1.0.0`
in [`pyproject.toml`](../../pyproject.toml), so a future unconstrained sync can
cross an MCP SDK major version even though the audited lock cannot. The
official SDK repository currently describes v1 as stable/maintenance and v2
as a separate major line
([official SDK README](https://github.com/modelcontextprotocol/python-sdk)).

## MCP normative transport contract

The current MCP protocol version is `2025-11-25`; MCP versions identify the
date of the last backwards-incompatible change. Backwards-compatible edits do
not change that identifier. Draft revisions are not ready for consumption
([MCP versioning](https://modelcontextprotocol.io/docs/learn/versioning)).

### Initialization and protocol version

- `initialize` must be the first client/server interaction. It negotiates one
  protocol version and capabilities; after the response, the client sends
  `notifications/initialized`
  ([lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle#initialization)).
- If the server supports the client's requested version, it returns the same
  version. Otherwise it returns another version it supports; a client that
  cannot support that response should disconnect
  ([version negotiation](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle#version-negotiation)).
- Every HTTP request after initialization must carry
  `MCP-Protocol-Version` with the negotiated version. If the header is absent
  and the version cannot otherwise be known, the server should assume
  `2025-03-26`. Invalid or unsupported values require HTTP 400
  ([protocol-version header](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#protocol-version-header)).

**Design implication.** Argus should advertise and test an explicit supported
MCP-version set. HTTP 400 for an invalid transport version is not the same as
an Argus tool's `invalid_request` outcome. Transport negotiation completes
before tool execution and must never create a retrieval/extraction run.

### POST, GET, DELETE, content negotiation, and errors

Streamable HTTP uses one MCP endpoint supporting POST and GET:

- Every client JSON-RPC message is a new POST. `Accept` must list both
  `application/json` and `text/event-stream`; the body is one JSON-RPC
  request, notification, or response.
- An accepted notification or response receives HTTP 202 with no body. A
  JSON-RPC request receives either an `application/json` object or an SSE
  stream. GET either opens an SSE stream or returns 405.
- A disconnected SSE response does not mean cancellation. Cancellation is an
  explicit MCP notification. Event IDs and `Last-Event-ID` may support
  resumption when the server has an event store.

These rules are normative in the
[Streamable HTTP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#streamable-http).
RFC 9110 separately defines 202 as accepted but not completed
([section 15.3.3](https://www.rfc-editor.org/rfc/rfc9110.html#name-202-accepted)).
That explains why MCP uses 202 only to acknowledge a notification/response;
an ordinary completed Argus tool result should not use 202.

The published transport supports optional protocol sessions:

- a server may return a cryptographically secure, visible-ASCII
  `MCP-Session-Id` on initialization;
- if returned, the client must send it on subsequent requests;
- an unknown/terminated session receives 404, causing the client to
  initialize a new session; and
- a client should DELETE the MCP endpoint to terminate a session, while a
  server that does not support client termination may return 405.

See [session management](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#session-management).

**Design implication.** The present SDK default is stateful HTTP. Argus should
either explicitly promise the published session behavior and bound session
lifetime, or deliberately select stateless HTTP and test compatible clients.
It should not leave the choice as an SDK default. An MCP session is transport
state; it must not be persisted or exposed as the Argus search-refinement
`session_id`.

### Published-versus-draft uncertainty

The current draft changelog proposes removing protocol-level sessions and
`MCP-Session-Id`
([draft changelog](https://modelcontextprotocol.io/specification/draft/changelog)).
The official versioning page says drafts are not ready for consumption and
still names `2025-11-25` as current. The draft is therefore relevant migration
warning, not a normative target. Issue #66 should freeze its tests to the
published revision and treat a later revision/SDK-major adoption as a separate
compatibility change.

## MCP authorization contract

Authorization is optional in MCP generally, but HTTP implementations that
support it should follow the MCP authorization specification. The current
published rules include:

- use `Authorization: Bearer` on every HTTP request, including requests in
  the same logical session;
- never put an access token in the URI query;
- validate that the token is intended for this resource;
- return 401 for missing/invalid/expired authentication and 403 for
  insufficient permission/scope; and
- expose the applicable bearer challenge and protected-resource metadata for
  interoperable discovery.

See the MCP
[authorization introduction](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#introduction),
[token requirements](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#token-requirements),
and
[authorization error handling](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization#error-handling).
RFC 9110 requires a `WWW-Authenticate` challenge on 401
([section 15.5.2](https://www.rfc-editor.org/rfc/rfc9110.html#name-401-unauthorized));
RFC 6750 defines Bearer challenges, `invalid_token`, and
`insufficient_scope`
([sections 3 and 3.1](https://www.rfc-editor.org/rfc/rfc6750.html#section-3)).

**Current Argus behavior.**

- [`argus/mcp/server.py`](../../argus/mcp/server.py) requires a configured
  caller credential for non-loopback SSE/Streamable HTTP, installs the SDK
  bearer middleware, and accepts only tokens known to `AuthConfig`.
- The verified token identity is preserved as the MCP caller and the same
  bearer token is passed to the canonical HTTP authority.
- The verifier is a static shared-token verifier, not an OAuth authorization
  server. Its `issuer_url` is hard-coded to loopback and its
  `resource_server_url` is constructed from the bind address. Values such as
  `0.0.0.0` are listener addresses, not canonical client-visible resource
  URIs.
- The main HTTP API accepts Bearer, `X-API-Key`, and `X-Admin-API-Key`, while
  the MCP authorization contract is Bearer-only.

**Design implication.** Static scoped service tokens can remain an explicit
private-deployment authentication profile, but Argus must not advertise the
current loopback/bind-address metadata as interoperable OAuth discovery.
Configuration needs one canonical external MCP resource URI and one
authorization mode. Every 401 needs a usable Bearer challenge; 403 is reserved
for an authenticated principal lacking permission (and for invalid Origin as
required by the transport). Authentication and authorization rejection occur
before provider execution or persistence.

## MCP Origin and DNS-rebinding requirements

For every Streamable HTTP connection, the MCP specification requires:

1. validate `Origin`;
2. return HTTP 403 when a present Origin is invalid;
3. bind local-only servers to loopback rather than `0.0.0.0`; and
4. use proper authentication.

See the transport
[security warning](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#security-warning).

The locked MCP SDK exposes
`TransportSecuritySettings(allowed_hosts=..., allowed_origins=...)`. Its
v1.27.0 implementation:

- auto-enables loopback Host/Origin allowlists only when `FastMCP.host` is
  `127.0.0.1`, `localhost`, or `::1`;
- otherwise leaves transport security unset unless the application supplies
  it;
- treats an absent Origin as valid;
- returns 403 for a disallowed Origin and 421 for a disallowed Host; and
- validates JSON content type for POST independently of DNS-rebinding mode.

See the official SDK
[`FastMCP` source](https://github.com/modelcontextprotocol/python-sdk/blob/v1.27.0/src/mcp/server/fastmcp/server.py#L146-L185)
and
[`TransportSecurityMiddleware` source](https://github.com/modelcontextprotocol/python-sdk/blob/v1.27.0/src/mcp/server/transport_security.py).

**Current Argus gap.** Argus passes `host` and `port` but no
`transport_security` to `FastMCP`. Therefore a remote bind does not receive
the SDK's automatic loopback rules and Host/Origin validation is disabled.
Bearer auth reduces exposure but does not satisfy the MCP Origin MUST.

The SDK places bearer enforcement outside the Streamable HTTP transport
handler. Consequently an unauthenticated request with an invalid Origin can be
rejected as 401 before the transport reaches its required Origin 403. Strict
MCP behavior requires an outer Origin gate that runs before tool execution and
returns 403 for a present disallowed Origin.

**Design implication.** Remote MCP startup should fail closed unless explicit
canonical allowed hosts and allowed origins are configured. Do not use `*`.
Allow absent Origin for non-browser MCP clients, but reject every present
Origin not exactly allowlisted. The bind address, trusted Host values, allowed
browser origins, and public resource URI are separate settings.

## FastAPI/Starlette Host and CORS behavior

Starlette's `TrustedHostMiddleware` enforces an allowed Host list, supports
explicit wildcard subdomains, and returns 400 for an invalid Host
([official Starlette middleware documentation](https://www.starlette.io/middleware/#trustedhostmiddleware)).

Starlette/FastAPI CORS behavior is different:

- an origin is the scheme, host, and port tuple;
- preflight requests are intercepted and receive 200 or 400;
- an ordinary request with `Origin` is passed through and CORS response
  headers are added only when appropriate; and
- wildcard origins cannot authorize credentialed browser requests, including
  Bearer authorization.

See the official
[FastAPI CORS guide](https://fastapi.tiangolo.com/tutorial/cors/) and
[Starlette CORSMiddleware documentation](https://www.starlette.io/middleware/#corsmiddleware).

**Current Argus behavior.** [`argus/api/main.py`](../../argus/api/main.py)
installs `CORSMiddleware` only when `ARGUS_CORS_ORIGINS` is non-empty. It uses
an explicit origin list and limited methods/headers, with credentials disabled.
It does not install `TrustedHostMiddleware` and has no application-level
Origin rejection. Therefore:

- CORS is a browser-read policy, not the HTTP authority's authentication or
  DNS-rebinding boundary;
- a non-browser client is unaffected by the CORS allowlist;
- an ordinary disallowed-Origin request can still execute if otherwise
  authenticated; and
- Host validation must be added independently.

**Design implication.** The HTTP authority needs explicit trusted hosts in
production. Its CORS list should remain disabled by default and explicitly
allow only real browser origins when a browser client is supported. MCP's
Origin 403 rule belongs on the MCP endpoint and cannot be delegated to the
HTTP API's CORS middleware.

When a trusted reverse proxy supplies forwarded headers, only that proxy's
addresses should be trusted. FastAPI documents that forwarded headers are not
interpreted until explicitly trusted
([behind a proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/#enable-proxy-forwarded-headers)).

## Structured MCP results and truthful errors

The MCP tool contract distinguishes:

- **protocol errors** for malformed requests, unknown tools/invalid protocol
  arguments, and protocol/server failures; and
- **tool execution errors** as `CallToolResult` with `isError: true` for API,
  validated domain-input, and business/execution failures that a model can
  potentially act on.

MCP structured data belongs in `structuredContent`. A tool may advertise an
`outputSchema`; if it does, the server must conform and clients should
validate. For backwards compatibility, a tool returning structured content
should also return serialized JSON in a text content block
([MCP tools: structured content and errors](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#structured-content)).

The locked Python SDK automatically derives an `outputSchema` from a return
annotation. A `-> str` tool is represented as structured `{"result": "..."}`,
alongside text. It also converts uncaught tool exceptions into
`isError: true` results. See the official
[SDK structured-output documentation](https://github.com/modelcontextprotocol/python-sdk#structured-output-support)
and
[`FastMCP` tool registration source](https://github.com/modelcontextprotocol/python-sdk/blob/v1.27.0/src/mcp/server/fastmcp/server.py#L302-L346).

**Current Argus behavior.**

- Every core MCP tool in [`argus/mcp/server.py`](../../argus/mcp/server.py)
  returns `str`. Its structured result is therefore only a wrapper around
  rendered Markdown, not the canonical Argus outcome/evidence object.
- [`argus/mcp/http_adapter.py`](../../argus/mcp/http_adapter.py) discards
  structured search/extraction fields while composing Markdown.
- An HTTP authority status of 4xx/5xx becomes `AuthorityRequestError`; the MCP
  SDK turns the exception into a tool error, but the canonical Argus outcome,
  status, run identity, and retry evidence are lost.
- A legacy HTTP-200 extraction carrying a truthy `error` becomes ordinary
  failure prose returned successfully by the tool. MCP sees `isError: false`.

**Design implication.** Core tools should return one typed MCP result with:

- `structuredContent` equal to the accepted semantic projection;
- a stable `outputSchema`;
- readable text/serialized JSON preserving the legacy human-facing path; and
- `isError: true` exactly for canonical terminal outcomes
  (`invalid_request`, `policy_rejected`, `timeout`, `persistence_failed`,
  `providers_failed`, `extraction_failed`, and `unready`).

`success`, `degraded`, and search `empty` remain normal tool results.
Authentication may fail at HTTP transport level before a tool exists; it
should not be fabricated as a tool result. An application input that is
well-formed MCP but violates an Argus operation rule is a tool execution
error, while malformed JSON-RPC, unknown tool, or invalid MCP parameters is a
protocol error.

Old clients must keep receiving a text content block. New clients must never
have to parse Markdown to recover `outcome`, rejection, run ID, provenance, or
retry guidance.

## HTTP status and error semantics

The accepted Argus scorecard already fixes the public outcome-to-status
mapping. Primary HTTP semantics support most of it:

| Argus condition | HTTP | Primary-source meaning and implication |
|---|---:|---|
| `success`, `degraded`, search `empty` | 200 | The operation completed; the body carries the semantic distinction. |
| syntactically valid JSON with invalid operation fields | 422 | Content type and syntax are understood, but contained instructions cannot be processed ([RFC 9110 §15.5.21](https://www.rfc-editor.org/rfc/rfc9110.html#name-422-unprocessable-content)). |
| malformed JSON/framing or invalid MCP transport header | 400 | Client request syntax/framing/routing error ([RFC 9110 §15.5.1](https://www.rfc-editor.org/rfc/rfc9110.html#name-400-bad-request)). |
| unsupported request media type | 415 | The method does not support the request format ([RFC 9110 §15.5.16](https://www.rfc-editor.org/rfc/rfc9110.html#name-415-unsupported-media-type)). |
| `authentication_rejected` | 401 | Missing or invalid target credentials; include `WWW-Authenticate`. |
| `policy_rejected`; invalid MCP Origin | 403 | Request understood but refused ([RFC 9110 §15.5.4](https://www.rfc-editor.org/rfc/rfc9110.html#name-403-forbidden)). |
| idempotency key conflicts with a different accepted request | 409 | Conflict with current target-resource state; provide enough information to resolve it ([RFC 9110 §15.5.10](https://www.rfc-editor.org/rfc/rfc9110.html#name-409-conflict)). |
| Argus admission rate limit before execution | 429 | Too many requests; details should explain and may include `Retry-After` ([RFC 6585 §4](https://www.rfc-editor.org/rfc/rfc6585.html#section-4)). |
| `providers_failed`, `extraction_failed` | 502 | The scorecard uses gateway semantics for unusable upstream acquisition. RFC 9110 defines 502 narrowly as an invalid inbound response, so the body outcome remains essential ([§15.6.3](https://www.rfc-editor.org/rfc/rfc9110.html#name-502-bad-gateway)). |
| `persistence_failed`, `unready` | 503 | Service cannot currently handle the operation; include `Retry-After` only when a bounded retry time is actually known ([RFC 9110 §15.6.4](https://www.rfc-editor.org/rfc/rfc9110.html#name-503-service-unavailable)). |
| `timeout` | 504 | The scorecard treats Argus as the acquisition gateway. RFC 9110 defines 504 as an untimely required upstream response ([§15.6.5](https://www.rfc-editor.org/rfc/rfc9110.html#name-504-gateway-timeout)). |

HTTP 408 is not the operation deadline: it means the server did not receive a
complete request message in time
([RFC 9110 §15.5.9](https://www.rfc-editor.org/rfc/rfc9110.html#name-408-request-timeout)).

The 502/504 mappings are intentionally application-specific at the edge of the
RFC wording. Argus is brokering upstream providers and extractors, but a
quality-floor failure is not always a malformed upstream HTTP response.
Clients must key on the stable `outcome`; the status communicates broad
failure class to generic HTTP software.

### One compatible error object

RFC 9457 defines `application/problem+json` with stable `type`, `status`,
`title`, `detail`, and `instance` members. Problem types may add extension
members, and clients must ignore extensions they do not recognize
([RFC 9457 §§3.1–3.2](https://www.rfc-editor.org/rfc/rfc9457.html#section-3)).

**Current Argus behavior.** Error shapes vary:

- validation: `{"detail": [...]}`;
- `HTTPException`: usually `{"detail": "..."}`;
- auth and rate limit: `{"error": "...", ...}`;
- ordinary extraction rejection: HTTP 200 with a top-level `error`; and
- successful response models have no common schema-version field.

**Design implication.** A new typed problem object should be the canonical
error lane and carry `outcome`, safe correlation ID, retry evidence, and
authorized rejection/evidence references as bounded extensions. During a
compatibility window, the legacy `detail` or `error` member can remain as an
additive alias where an old route already returned it. Do not put raw exception
text, provider response bodies, URLs containing credentials, tokens, cookies,
or secret-bearing headers in problem details.

## HTTP additive compatibility and versioning

No RFC makes arbitrary JSON response additions safe. RFC 9110 says recipients
should ignore unrecognized HTTP fields so HTTP headers are extensible
([§5.1](https://www.rfc-editor.org/rfc/rfc9110.html#name-field-names)), and RFC
9457 says problem-detail clients must ignore unknown extension members, but
ordinary Argus success JSON needs its own explicit rule.

The least disruptive rule for the existing `/api` lane is:

1. Existing successful field names, types, nullability, enum meanings, list
   ordering, and status semantics remain stable.
2. New fields are optional/additive. Omission and explicit `null` keep
   documented, distinct meanings.
3. Existing fields are not renamed, moved, repurposed, narrowed, or changed
   from scalar to object/list on that lane.
4. Unknown response members must be ignored by supported clients. Argus's own
   HTTP/MCP/CLI adapters must demonstrate this with fixtures.
5. New request fields default to old behavior when absent. Unknown request
   fields should not be silently accepted unless that is an intentional,
   tested compatibility rule.
6. New enum values can break exhaustive old clients; introduce them only in a
   versioned semantic field or after a compatibility review.
7. Removal, type/meaning changes, or incompatible status changes require a
   new major media/path contract, not an application release-number bump.
8. The OpenAPI document and response models are part of the compatibility
   evidence and must describe both success and problem responses.

An additive top-level `schema_version` does not by itself make a breaking
shape compatible. Internal policy versions such as ranking, rejection, or
extraction-outcome policy identify semantics/cache identity; they are not a
substitute for an HTTP representation version.

## Current-to-target audit

| Concern | Current worktree behavior | Normative/target implication |
|---|---|---|
| HTTP API path/version | `/api`, FastAPI metadata version hard-coded `1.6.2`; no representation negotiation | Preserve as compatibility lane; document additive rules and introduce a new major lane only for actual breaking change. |
| HTTP successes | Stable typed route-specific models; search/extraction lack canonical outcome | Add the one accepted semantic projection without changing old success fields. |
| HTTP errors | Mixed `detail`, `error`, and HTTP-200 failure shapes | One RFC 9457-style typed problem lane plus temporary legacy aliases. |
| HTTP Host | No `TrustedHostMiddleware` | Explicit production host allowlist, independently of CORS/auth. |
| HTTP CORS | Optional explicit origins, limited methods/headers, no credentials | Browser policy only; keep explicit and disabled unless needed. It is not request authentication or MCP Origin enforcement. |
| MCP revision | SDK negotiates protocol; Argus does not state a tested version set | Freeze/test current `2025-11-25` and supported fallback versions. |
| MCP sessions | SDK default stateful, no Argus-selected stateless mode, event store, or bounded lifetime | Make the choice explicit. Do not couple it to Argus search sessions. |
| MCP bearer auth | Required for non-loopback bind; scoped/legacy static token verifier | Preserve private service-token profile, correct canonical metadata/challenges, and keep auth before execution. |
| MCP DNS rebinding | Remote listener receives no explicit SDK transport security | Fail startup without exact allowed Host/Origin configuration; invalid present Origin is 403. |
| MCP structured result | SDK wraps returned Markdown string as `{"result": ...}` | Return the semantic object with output schema plus readable text. |
| MCP tool failure | HTTP 4xx/5xx becomes generic tool error; HTTP-200 extraction error becomes successful prose | Preserve canonical outcome; terminal operation failures use `isError: true`; transport auth stays an HTTP error. |
| SDK dependency | lock `1.27.0`, requirement `mcp>=1.0.0` | Bound the audited major line before a sync can select v2; evaluate v2 separately. |

## Minimum compatibility evidence

The decision and later implementation should have hermetic tests for:

- initialization and capability/version negotiation;
- `MCP-Protocol-Version` missing, valid, and unsupported cases;
- stateful session creation, subsequent header, unknown/expired ID, DELETE, and
  bounded expiry, or the explicitly selected stateless equivalents;
- POST `Accept`/content type, notification 202, JSON response, SSE response,
  GET/405, and disconnect-not-cancel semantics;
- missing/invalid bearer 401 with a usable challenge; insufficient permission
  403; no provider/persistence call on either;
- absent Origin, exact allowed Origin, invalid Origin 403, invalid Host, and
  non-loopback startup without allowlists;
- direct and trusted-proxy Host behavior;
- success, degraded, empty, invalid request, policy rejection, timeout,
  persistence failure, providers failed, extraction failed, and unready across
  HTTP and MCP;
- MCP `outputSchema`, conforming `structuredContent`, old-client text content,
  and `isError` classification;
- legacy HTTP clients ignoring new success members and legacy MCP clients
  consuming the text block;
- stable RFC 9457 problem types and ignored unknown extensions;
- idempotency conflict 409 and retry metadata without automatic duplicate
  execution; and
- secret-injection fixtures for error, Origin, Host, trace, provider, and
  correlation fields.

## Source confidence and remaining uncertainties

- **High confidence:** published MCP `2025-11-25` lifecycle, transport,
  session, Origin, authorization, structured-content, and error rules.
- **High confidence:** locked MCP SDK `1.27.0` behavior, because both the lock
  and the official tagged source were inspected.
- **High confidence:** current repository behavior described above, based on
  the issue #66 worktree.
- **High confidence:** RFC status, bearer-challenge, and problem-detail rules.
- **Unsettled:** the next MCP revision removes transport sessions in the
  current draft. It is deliberately not treated as normative.
- **Unsettled:** which browser Origin, public MCP resource URI, reverse-proxy
  boundary, and transport session mode deployment will use. These are
  configuration/decision inputs, not facts inferable from the bind address.
- **Unsettled:** whether Argus will eventually provide full OAuth discovery or
  retain only a documented private static-service-token profile. The current
  metadata is not adequate evidence of full OAuth interoperability.
