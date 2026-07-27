# HTTP and MCP compatibility matrix

- Date: 2026-07-27
- Issue: [#66](https://github.com/Khamel83/argus/issues/66)
- Decision:
  [ADR 0006](../adr/0006-http-mcp-compatibility-contract.md)
- Primary sources:
  [transport research](2026-07-27-http-mcp-compatibility-primary-sources.md)

## Scope and method

This matrix compares the checked-in #78 design base with the accepted
version-2 target. It is a static repository and primary-source audit. No live
Argus, provider, extractor, browser, production endpoint, credential, or
historical record was called.

## Current HTTP baseline

| Surface | Current behavior | Target |
|---|---|---|
| route version | unversioned `/api/*` | freeze as legacy v1; add `/api/v2/*` |
| success body | route-specific Pydantic object | preserve v1; wrap accepted v2 result in stable envelope |
| overall outcome | absent on search/extraction | required canonical v2 outcome |
| errors | FastAPI `detail`, custom `error`, or route-specific 503 | safe RFC 9457-derived problem member in the v2 envelope and exact status |
| validation | 422 `detail` from Pydantic errors | v1 preserved; v2 bounded field/reason without input echo |
| request identity | `X-Request-ID` response header | same header plus identical v2 body field |
| auth | Bearer or custom key; production loopback authenticated | v1 preserved; v2 advertises Bearer and rejects multiple carriers |
| caller label | request field exists but scoped credentials override it | authenticated identity remains authoritative |
| CORS | optional exact origins; no credentials; GET/POST/OPTIONS | exact browser policy plus independent pre-execution Origin guard |
| Host | no application allowlist | explicit production allowlist before work |
| response size to adapter | HTTP client caps at 11 MiB | preserve cap |
| capability discovery | schema `1.0`, execution/capability booleans | additive contract-discovery entries |
| capability cache | none for contract selection | in-process only, origin/deployment scoped, at most 60 seconds |

Current `/api/live`, `/api/startup`, `/api/ready`, `/api/health`, and
authenticated status surfaces already have separately documented operational
semantics. Version 2 does not turn liveness into dependency health or initiate
diagnostic probes.

## Current MCP baseline

| Surface | Current behavior | Target |
|---|---|---|
| production authority | HTTP adapter | unchanged |
| default transport | stdio | unchanged |
| remote transport | Streamable HTTP; legacy SSE also selectable | Streamable HTTP canonical; preserve secured `/sse` + `/messages/` compatibility |
| tool output | text plus SDK-derived `{"result": text}` | freeze existing names/schema; add explicit `*_v2` tools with bounded text plus v2 envelope |
| tool errors | exception or ordinary failure prose | preserve legacy; v2 canonical failure tool result with `isError=true` |
| protocol errors | SDK JSON-RPC behavior | preserve as a separate layer |
| remote auth | enabled only when bind address is non-loopback | required for every remotely exposed listener, including proxied loopback |
| auth metadata | static verifier plus placeholder localhost issuer | static bearer without fake OAuth discovery claims |
| transport security | SDK receives no explicit allowlist, so protection is not an Argus guarantee | explicit Host and Origin allowlists |
| HTTP session mode | SDK default stateful session | preserve, bind to principal, use cryptographically unpredictable IDs, bound idle/capacity |
| execution state | lives at HTTP authority | unchanged |
| resumability | no event store configured | explicitly not promised in v2 |
| request body | SDK target defaults to 4 MiB | contract and test 4 MiB |

The current phrase “stateless MCP adapter” describes execution ownership. It
does not mean the Streamable HTTP protocol lacks a process-local transport
session.

## Version-2 envelope matrix

| Outcome | `result` | `error` | HTTP | MCP `isError` | CLI |
|---|---|---|---:|---:|---:|
| `success` | object | null | 200 | false | 0 |
| `degraded` | object | null | 200 | false | 0 |
| `empty` | object | null | 200 | false | 0 |
| `invalid_request` | null | object | 422 after semantic validation | true when a tool was reached | 1 |
| `authentication_rejected` | null | object | 401 | transport rejection; tool not reached | 1 |
| `policy_rejected` | optional accepted evidence | object | 403 | true | 1 |
| `timeout` | optional accepted evidence | object | 504 | true | 1 |
| `persistence_failed` | optional unaccepted diagnostic facts only | object | 503 | true | 1 |
| `providers_failed` | accepted traces/results if any | object | 502 | true | 1 |
| `extraction_failed` | accepted retrieval/source refs if any | object | 502 | true | 1 |
| `unready` | optional readiness facts | object | 503 | true | 1 |

Transport admission specializes the code/status without inventing a successful
operation:

| Admission condition | Outcome/code | HTTP |
|---|---|---:|
| malformed JSON/framing | `invalid_request/malformed_request` | 400 |
| oversize body | `invalid_request/payload_too_large` | 413 |
| unsupported media type | `invalid_request/unsupported_media_type` | 415 |
| unknown route | `invalid_request/route_not_found` | 404 |
| unknown caller-scoped resource | `unready/session_not_found` | 404 |
| conflicting idempotency-key reuse | `invalid_request/idempotency_conflict` | 409 |
| request rate limit | `unready/rate_limited` | 429 |
| invalid Host | request rejected before operation | 421 |
| invalid Origin | `policy_rejected/origin_rejected` | 403 |
| safe unclassified failure | `unready/internal_failure` | 503 |

## Additive versus breaking

| Change | Version 1 | Version 2 |
|---|---|---|
| add optional nullable field | allowed | allowed when default semantics are unchanged |
| add new required field | breaking | breaking |
| remove/rename field | breaking | breaking |
| change number to string | breaking | breaking |
| change nullability/default/unit | breaking | breaking |
| add enum outcome | breaking | new contract/policy version |
| add open-ended provider/list entry | allowed | allowed |
| reorder ranked results | semantic change, not transport-additive | controlled by accepted ranking policy |
| change HTTP status | breaking | breaking |
| wrap body | breaking | already fixed envelope |
| tighten unsafe Host/Origin/input bound | security fix | security fix |
| redact leaked detail | privacy fix | privacy fix |

## Session identity matrix

| Identifier | Owner | Durable | Authentication | Reuse |
|---|---|---:|---:|---|
| `X-Request-ID` | one HTTP operation | no | no | correlation only |
| idempotency key | accepted operation/attempt | durable where declared | no | same caller and exact contract only |
| search `session_id` | authenticated Argus caller | yes | no | same caller only |
| `Mcp-Session-Id` | atomic MCP registry + authenticated principal | no | no | same principal until idle/delete/restart; expired entries swept before capacity |
| bearer token | credential authority | external | yes | never exposed as another identifier |
| run/trace/artifact ref | HTTP authority | yes | no | access-controlled evidence reference |

Unknown or other-caller search/MCP session identifiers return not-found
semantics without confirming ownership.

## Streamable HTTP method matrix

| Method | Required headers | Success | Fixed failures |
|---|---|---|---|
| POST initialize | Bearer when remote, JSON content type, JSON+SSE Accept | JSON or SSE response plus session ID | 400/401/403/413/415 |
| POST request | above plus session ID and negotiated protocol version | JSON or SSE/tool result | 400 missing session/version issue; 404 unknown session |
| POST notification/response | above plus session ID and negotiated protocol version | 202 with no body | 400 missing session/version issue; 404 unknown session |
| GET | Bearer, SSE Accept, session ID, protocol version | SSE stream | 400/404/405 |
| DELETE | Bearer, session ID, protocol version | 200 termination | 400/404/405 |
| OPTIONS | Origin plus CORS request headers | preflight only | CORS rejection |
| other | none | never | 405 with `Allow` |

The canonical published MCP target is `2025-11-25`. Missing
`MCP-Protocol-Version` remains the SDK compatibility default `2025-03-26`.
New clients send the negotiated version. Argus bounds the SDK dependency to
major version 1; adopting SDK v2 is a separate compatibility decision.

## Host and Origin cases

| Request fact | Decision |
|---|---|
| exact configured Host, no Origin | allow non-browser client |
| exact Host and exact configured Origin | allow |
| missing Host | 421 |
| arbitrary Host or Host suffix trick | 421 |
| trusted Host only in `X-Forwarded-Host` | reject actual Host |
| missing Origin | allow non-browser client |
| `Origin: null` | reject |
| wildcard/suffix/regex-like Origin | reject |
| correct hostname, wrong scheme or port | reject |
| arbitrary Origin with valid bearer token | reject before execution |
| CORS wildcard with bearer | configuration invalid |

## MCP result matrix

| HTTP accepted outcome | Text content | Structured content | JSON-RPC |
|---|---|---|---|
| legacy-name success/failure | current text | current `{"result": text}` | current SDK behavior |
| v2 success | legacy-readable result | exact v2 envelope | success |
| v2 degraded | visibly degraded result | exact v2 envelope | success |
| v2 empty | visibly empty result | exact v2 envelope | success |
| v2 canonical operation failure | safe failure summary | exact v2 envelope | tool result with `isError=true` |
| malformed `tools/call` protocol parameters | SDK-safe error | none | invalid-params error |
| JSON-RPC-valid call violating advertised `inputSchema` | bounded SDK error; no rejected-value echo | none | tool result with `isError=true`; tool/HTTP not reached |
| schema-valid call rejected by Argus input/domain rules | safe failure summary | exact v2 envelope | tool result with `isError=true` |
| malformed JSON-RPC | protocol body | none | protocol error |
| listener auth/Host/Origin/session failure | transport body | none | request never reaches tool |

MCP does not turn a 502/503/504 from the HTTP authority into ordinary Markdown
success, and HTTP/MCP do not independently reclassify ADR 0005 rejection
evidence.

A transport-capacity 503 occurs before JSON-RPC/tool execution. An
HTTP-authority 503 through `*_v2` is a JSON-RPC success carrying an
`isError=true` tool result and canonical envelope.

## Migration fixtures

The minimum hermetic fixture corpus includes:

1. every current version-1 success body;
2. every current version-1 auth, validation, rate, persistence, and route
   failure;
3. all canonical version-2 outcomes and status mappings;
4. safe partial evidence on failed composition;
5. malformed JSON, non-finite input, oversized body, and injected secret/raw
   error values;
6. capability discovery against a legacy and version-2 server;
7. origin/deployment-scoped 60-second capability expiry, deployment
   invalidation, and no fallback POST after ambiguous version-2 failure;
8. HTTP bearer/custom/multiple credential cases;
9. caller-owned and cross-caller retrieval sessions;
10. every Host/Origin/CORS case above;
11. MCP initialize for every promised protocol revision;
12. missing, invalid, expired, deleted, wrong-principal, at-capacity, and
    post-restart MCP sessions, plus unpredictable generation, atomic collision
    handling, atomic concurrent capacity admission, expired-session
    reclamation, and no valid-session LRU eviction;
13. MCP POST requests, 202/no-body notification/response acknowledgement, GET,
    DELETE, OPTIONS, content type, Accept, and body bounds;
14. every `*_v2` tool success/degraded/empty/failure as text plus structured
    content, and schema-invalid arguments as bounded SDK tool errors with no
    rejected-value echo or HTTP execution;
15. unchanged legacy-name `{"result": text}` schemas and text rendering;
16. secured legacy `GET /sse` and `POST /messages/` compatibility;
17. CLI human/JSON stdout, stderr, and exit codes;
18. presenter dependency boundaries; and
19. rejection of over-bound accepted results without partial streaming.

These fixtures use in-process fakes only. They must prove zero provider,
extractor, paid, production, or secret access.
