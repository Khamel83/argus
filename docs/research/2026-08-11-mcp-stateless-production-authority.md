# Stateless MCP and the Argus production authority

- Date: 2026-08-11
- Scope: implications of the MCP 2026-07-28 release for Argus's canonical
  production MCP/HTTP authority
- Boundary: source review and architecture guidance only; this note does not
  claim that the deployed Argus endpoint already speaks MCP `2026-07-28`

## Executive conclusion

The MCP 2026-07-28 release strengthens Argus's existing role as a narrow,
authenticated edge adapter over one durable HTTP authority. It does **not**
make the current deployment automatically compatible: Argus's accepted ADR
still targets published MCP `2025-11-25` behavior, including bounded
transport-session compatibility ([ADR 0006](../adr/0006-http-mcp-compatibility-contract.md)).
Adopting the new revision should therefore be an explicit compatibility
change with a client matrix and no-spend live proof, not an SDK upgrade hidden
inside routine deployment.

## What the sources establish

The official MCP release describes a stateless protocol core. It retires the
`initialize`/`initialized` exchange and `Mcp-Session-Id`; requests carry the
protocol version, client identity, and capabilities, and any request may land
on any instance behind a normal round-robin load balancer. Application state,
when needed, should be represented by an explicit tool-issued handle rather
than hidden transport state. The release also adds Multi Round-Trip Requests
(MRTR), header-based method/name routing, cache hints and deterministic list
ordering, authorization hardening, the Tasks extension, and a deprecation
window for legacy HTTP+SSE ([official MCP release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)).

Simon Willison's implementation report is consistent with those transport
claims: one-shot requests remove session storage and backend affinity, and a
small MCP tool surface is easier to audit and control than giving an agent
arbitrary shell plus network access. His `mcp-explorer` and `datasette-mcp`
examples are useful operational patterns, but are implementation experience,
not normative protocol requirements ([Willison](https://simonwillison.net/2026/Jul/31/stateless-mcp/)).

## Argus implications

1. **Keep the authority boundary.** The MCP process should remain a caller
   adapter: bearer identity, caller tier, provider budgets, extraction, SQL
   state, and durable evidence belong to the HTTP authority. A transport
   session must never become an Argus retrieval `session_id`, an auth token, or
   a spend identity. If a future tool needs continuity, return an explicit,
   caller-scoped run or artifact handle and require it on the next call.

2. **Treat stateless transport as a versioned target.** Record support for
   published `2025-11-25` and `2026-07-28` separately. Do not advertise the
   latter until the server SDK, reverse proxy, and Codex/Claude/OpenCode
   clients have passed direct one-shot probes. A compatibility fallback must
   not retry a provider-spending operation under another protocol version.

3. **Make the gateway observable before parsing.** The new
   `Mcp-Method`/`Mcp-Name` headers allow the protected gateway to route,
   authorize, rate-limit, and meter without inspecting JSON bodies. Proxies
   must preserve and constrain these headers; acceptance should prove that an
   invalid method/name is rejected before any provider or persistence work.

4. **Use deterministic catalogs.** `tools/list` (and other list/read
   responses) can carry `ttlMs` and `cacheScope`. Argus should keep tool order
   deterministic and make cache scope explicit, while treating cached catalogs
   as discovery only—not evidence that an execution was accepted.

5. **Keep authentication resource-bound.** The release's issuer validation,
   issuer-bound credentials, and move from Dynamic Client Registration toward
   Client ID Metadata Documents are relevant to a future OAuth profile. The
   current private deployment may continue using a scoped bearer credential,
   but it should use one canonical external resource URI, never query-string
   tokens, and must not imply OAuth discovery it does not implement.

6. **Use extensions for interactive or long-running work.** If research-report
   execution later needs confirmation or missing input, MRTR provides an
   `input_required` result and a retry carrying `inputResponses`; it avoids a
   held-open bidirectional stream. For a long-running report, the Tasks
   extension's poll/update model is a better fit than hidden transport state.
   The existing durable report/run handle remains an Argus authority concern.

7. **Plan the legacy off-ramp.** Legacy HTTP+SSE, Roots, Sampling, and
   Logging are deprecated in the new release. Keep the currently supported
   compatibility route only while its security and no-spend tests remain
   green, and document a removal date when all named clients have migrated.

## Required follow-up evidence before adoption

- A declared MCP-version/client matrix for the canonical HTTPS endpoint.
- A no-spend probe that sends a direct stateless `tools/call` with
  `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name`, without initialization
  or a session header, then verifies durable outcome/evidence accounting.
- A restart or multi-instance probe proving the next request can land on a
  different adapter without transport-session state.
- Header routing and invalid-origin/authorization rejection tests proving no
  provider, extractor, or persistence work occurs before admission.
- Deterministic `tools/list` output and explicit cache-hint behavior.
- An explicit migration decision updating ADR 0006 and the client setup docs;
  until then, keep the published `2025-11-25` compatibility contract.

## Operator-supplied caller correction

Clio is retired and is not an active Argus caller. Remove `clio*` from caller
tier policy, client configuration, acceptance fixtures, and status summaries;
this is an operator instruction, not a conclusion drawn from either source.
