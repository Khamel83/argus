# Homelab Authority, Research Admission, and Repair Loop — Implementation Plan

> **For the implementing agent:** Use the repository's test-first workflow.
> This is a draft for parent-task approval.  It authorizes no runtime,
> deployment, secret, provider, or external-service mutation by itself.

**Goal:** Make Homelab the sole Argus execution authority, expose one
authenticated MagicDNS Streamable HTTP MCP endpoint with modern/legacy
compatibility, admit research only at the three-citation/two-domain floor, and
operate providers through no-spend observations and Maya-owned repair flow.

**Architecture:** Keep the HTTP authority as the only broker/provider/database
owner.  The MCP adapter remains a stateless caller.  Direct tailnet clients
reach its single Serve route; the OpenAI tunnel-client reaches it internally.
Add a research-specific admission contract and durable confidence records at
the authority, then publish redacted operational observations to Baywatch.

**Tech stack:** Python 3.11+, FastAPI, Pydantic, `mcp==2.0.0`, SQLAlchemy/Alembic
as already used by Argus, pytest/pytest-asyncio, Docker Compose, Tailscale
Serve, OpenAI Secure MCP Tunnel, Baywatch, Maya.

## Non-negotiable constraints

- Work in an isolated worktree; preserve unrelated changes and stage named
  files only.
- Do not deploy, alter secret stores, restart services, call paid providers, or
  change Tailscale/OpenAI/Baywatch/Maya state until a separately authorized
  implementation turn reaches its human gate.
- Do not put a raw tailnet IP, `localhost`, container URL, database URL, or
  provider credential in real Codex/Claude client configuration.
- Do not replace `mcp==2.0.0` with an older lock.  Reconcile stale v1 tests and
  documentation with the existing v2 source instead.
- Do not regenerate `README.md` visual material until Task 12 has passed all
  deployment and operational gates.  It is the last change before push.

## Phase 0 — Baseline and source reconciliation

### Task 1: Record the source baseline and make legacy-vs-v2 drift explicit

**Files:**

- Modify: `docs/adr/0006-http-mcp-compatibility-contract.md`
- Modify: `docs/mcp-clients.md`
- Modify: `docs/operations.md`
- Modify: `argus/cli/main.py`
- Test: `tests/test_mcp_stateless_v2.py`
- Test: affected v1-assumption tests found by `rg -n 'FastMCP|1\\.27\\.0|2025-11-05' tests docs`
- Test: `tests/test_cli.py`

**Step 1: establish the actual source baseline without asserting deployment health.**

Run:

```bash
git status --short --branch
rg -n 'mcp==|FastMCP|MCPServer|2026-07-28|2025-11-25|stateless_http' pyproject.toml uv.lock argus tests docs
uv sync --frozen --extra dev --extra mcp
uv run --no-sync pytest -q tests/test_mcp_stateless_v2.py
```

Record failures as source migration gaps.  Do not relabel an interrupted,
stalled, or pre-existing full-suite failure as a pass.  Do not use
`uv run --no-sync pytest` before the extras are installed: it can resolve a
host `pytest`/MCP package instead of the locked project environment.

**Step 2: write red tests for the target compatibility matrix.**

Extend `tests/test_mcp_stateless_v2.py` so its isolated authority double proves:

- 2026-07-28 `server/discover`, `tools/list`, and an allowed call use the
  matching protocol/meta/header contract and never receive/session-bind an MCP
  session identifier;
- a valid legacy initialization path on the same `/mcp` endpoint completes;
- a mismatched protocol/header/body request returns the specified 400/error
  before the authority double has a call recorded;
- an unauthorized or unacceptable-Origin request fails before the authority
  double; and
- JSON and `data:` SSE responses are parsed according to content type.

**Step 3: make the source pass the tests with the existing v2 server.**

Edit `argus/mcp/server.py` and only directly related adapter code if the red
tests expose a real gap.  Keep `MCPServer` and `streamable_http_app` as the
serving path.  Do not add a separate `/mcp-v2` route or a client-side fallback.

**Step 4: reconcile documentation.**

Amend ADR 0006 with a dated superseding decision: `/mcp` is one endpoint;
2026-07-28 uses discovery/stateless per-request metadata; supported legacy
revisions use the SDK path only; modern mismatch must not downgrade blindly.
Replace raw-address/localhost real-client examples in `docs/mcp-clients.md`
and `docs/operations.md` with the canonical hostname placeholder and a clear
internal-only loopback note.  In `argus/cli/main.py`, make real-client remote
mode require the canonical HTTPS URL and a token *environment-variable name*;
remove the `--key`/`ARGUS_API_KEY` path that writes a raw token to a shell RC
file.  Retain local stdio only behind an explicit development-only command and
never emit it from a production client template.  Keep historical material
labeled historical.

**Step 5: verify.**

```bash
uv run --no-sync pytest -q tests/test_mcp_stateless_v2.py
uv run --no-sync pytest -q tests/test_mcp_transport.py tests/test_mcp_v2.py
uv run --no-sync pytest -q tests/test_cli.py
git diff --check
```

**Acceptance:** all focused MCP tests pass on `mcp==2.0.0`; the docs have no
actionable real-client raw-IP/localhost fallback; the source makes no statement
that Homelab is already live.

### Task 2: Pin a single caller configuration contract

**Files:**

- Create: `docs/contracts/argus-remote-mcp-client-v1.md`
- Modify: `argus/cli/main.py`
- Test: `tests/test_cli.py`

**Step 1: write failing configuration-validation tests.**

Cover normalization of exactly one canonical `https://<magicdns>/mcp` value and
rejection of a raw IP, `http://`, a localhost hostname, a non-`/mcp` path, and
any fallback list.  Tests must not use a real token.

**Step 2: implement the smallest validation seam.**

Add a single `validate_remote_mcp_url()` function at the existing config
boundary.  It returns a safe reason code only, not the full URL or credential.
Change `mcp init` from raw `--key`/`ARGUS_API_KEY` handling to an explicit
`--token-env-var` reference and delete the shell-RC write path.  Do not add
validation to generic development-only direct broker commands.

**Step 3: document exact client shapes.**

`argus-remote-mcp-client-v1.md` defines the public configuration value,
authentication reference, expected auth failure, protocol checks, and
prohibited values.  It distinguishes direct Codex/Claude configuration from
the Homelab-internal tunnel-client target.

**Step 4: verify.**

```bash
uv run --no-sync pytest -q tests/test_cli.py
rg -n '100\\.|localhost|127\\.0\\.0\\.1|:8000|:8443' docs/operations.md docs/mcp-clients.md docs/contracts/argus-remote-mcp-client-v1.md
```

**Acceptance:** only explicitly marked internal-Homelab examples can contain a
loopback address; no real-client template does.

## Phase 1 — Research admission and provider confidence

### Task 3: Define a versioned research-admission model before wiring routes

**Files:**

- Create: `argus/contracts/research_admission.py`
- Create: `tests/test_research_admission.py`
- Modify: `argus/contracts/__init__.py`
- Create: `docs/contracts/argus-research-admission-v1.md`

**Step 1: write failing model tests.**

Define `ResearchAdmissionOutcome` with `admitted`, `insufficient_evidence`,
and `failed`; `ResearchCitation`; `ResearchAdmission`; and
`ResearchAdmissionEnvelope`.  Tests prove that 3 distinct canonical URLs over
2 registrable domains admit; 2/2 and 3/1 do not; duplicate canonical URLs,
malformed links, no-title entries, blocked/error candidates, and unknown
registrable domains do not count.

**Step 2: implement pure deterministic admission.**

Place URL canonicalization/domain counting in the contract/module layer, using
the repository's existing registrable-domain utility where available.  Return
`missing` requirements and partial safe citations deterministically.  Do not
call a provider, database, or extractor from this module.

**Step 3: publish the route-specific contract.**

Document `contract_version: research-admission-1`, the 3/2 constants, 422
body for `insufficient_evidence`, and the distinction between semantic
insufficiency and a transport/workflow `failed` state.  Do not mutate legacy
discovery/search response semantics.

**Step 4: verify.**

```bash
uv run --no-sync pytest -q tests/test_research_admission.py
uv run --no-sync ruff check argus/contracts tests/test_research_admission.py
```

**Acceptance:** the 3/2 decision is pure, deterministic, domain-aware, and
testable with no provider credentials.

### Task 4: Route Targeted Research through admission on HTTP and MCP

**Files:**

- Modify: `argus/workflows/targeted_research.py`
- Modify: `argus/workflows/service.py`
- Create: `argus/api/routes_research.py`
- Modify: `argus/api/main.py`
- Create: `argus/api/research_presenters.py`
- Modify: `argus/mcp/http_adapter.py`
- Modify: `argus/mcp/v2_tools.py`
- Modify: `argus/mcp/server.py`
- Create: `tests/test_research_admission_http.py`
- Create: `tests/test_research_admission_mcp.py`

**Step 1: write end-to-end authority-double tests.**

For an admitted fixture and an insufficient fixture, assert HTTP
`POST /api/research` and MCP `research_web` produce byte-equivalent
structured admission counts/citations.  Assert HTTP insufficiency is 422;
MCP insufficiency has an error result with usable structured content.  Assert
the old discovery search route/tool is unchanged.

**Step 2: implement one workflow boundary.**

Construct citations only from normalized, accepted `SearchResult`/
`ExtractedContent` data after existing dedupe/ranking.  Call the pure admission
function once at the workflow boundary.  The HTTP presenter maps it to the
new route; the MCP adapter serializes the same envelope.  Do not make MCP
perform local broker work.

**Step 3: prevent unsafe synthesis.**

Expose an explicit `admitted` predicate to the workflow consumer.  Any answer
builder must refuse to produce a substantive research conclusion when outcome
is `insufficient_evidence`; it may return the receipt/citations only.

**Step 4: verify.**

```bash
uv run --no-sync pytest -q tests/test_research_admission.py tests/test_research_admission_http.py tests/test_research_admission_mcp.py
uv run --no-sync pytest -q tests/test_http_authority.py tests/test_mcp_stateless_v2.py
```

**Acceptance:** all three client surfaces see one semantic decision; a partial
result is impossible to mistake for an admitted research answer.

### Task 5: Persist provider confidence without storing secrets or spending

**Files:**

- Create: `argus/persistence/provider_confidence.py`
- Create: `migrations/versions/0010_provider_confidence.py`
- Modify: `argus/broker/readiness.py`
- Modify: `argus/broker/planning.py`
- Modify: `argus/broker/execution.py`
- Modify: `argus/broker/policies.py`
- Create: `tests/test_provider_confidence_repository.py`
- Create: `tests/test_provider_confidence_planning.py`

**Step 1: write repository migration tests.**

Create/migrate a disposable SQLite database and, where the repository test
setup supports it, the production SQL dialect.  Assert unique key
`(provider, request_class, egress_scope)`, bounded rolling counters, expiry,
and safe reason-code validation.  Assert no model field accepts `key`, `token`,
`authorization`, raw balance response, or query text.

**Step 2: implement the forward-only persistence seam.**

Add `ProviderConfidenceV1` and repository methods to record an aggregate
real-query outcome and fetch an expired/nonexpired confidence snapshot.  Reuse
the existing readiness/spend repository transaction conventions.  Do not
create a caller-local database fallback.

**Step 3: write planner behavior tests before changing routing.**

Prove the stage order: renewable/free, recurring, then prepaid only after
lower stages are insufficient or a capability requirement explicitly matches.
Prove unknown balance is eligible; confirmed exhaustion/cooldown/disabled is
not; fresh relevant confidence ranks but never overrides readiness; stale
confidence may cause only the smallest demand-triggered comparison after lower
tiers fail; and the first admitted stage halts escalation.

**Step 4: wire the planner and outcome recorder.**

Use `ProviderConfidenceRepository` from the authority planner/execution path.
Record aggregate admission yield only after the request outcome is known.
Keep no-spend diagnostics off this code path.  Enforce caller tier caps and
request budget before scheduling each provider.

**Step 5: verify.**

```bash
uv run --no-sync pytest -q tests/test_provider_confidence_repository.py tests/test_provider_confidence_planning.py
uv run --no-sync pytest -q tests/test_provider_readiness*.py tests/test_provider_spend*.py
alembic upgrade head
alembic current
```

Run the migration commands only against a disposable test database in this
phase.  Production migration is a later human-authorized gate.

**Acceptance:** confidence ranks safely but cannot spend on a cron, cannot
override a hard readiness exclusion, and unknown balance remains eligible.

## Phase 2 — No-spend operations and repair contracts

### Task 6: Produce a redacted diagnostic observation at the authority

**Files:**

- Create: `argus/operations/provider_diagnostics.py`
- Create: `argus/contracts/provider_observation.py`
- Modify: `argus/api/provider_operations.py`
- Modify: `argus/persistence/readiness.py`
- Create: `tests/test_provider_diagnostics.py`
- Create: `tests/test_provider_observation_redaction.py`

**Step 1: write no-spend tests.**

Use provider doubles that raise if `search`, `extract`, browser, or billable
API methods are called.  Tests must prove a daily diagnostic reads only allowed
readiness/configuration facts and persists/posts an observation without
provider execution.

**Step 2: implement the bounded observation model.**

Implement `ArgusProviderObservationV1` exactly as in the design specification.
Validate length and reject sensitive key names/patterns from `safe_summary`.
Use enumerated dimension/state/reason/owner fields.  Never pass through an
exception message without redaction.

**Step 3: expose a local, authenticated operational trigger.**

Add an authority-only command or privileged route that runs one diagnostic
cycle.  It must be unusable by normal MCP callers and have no hidden search
fallback.  The Homelab scheduler invokes that bounded trigger in Phase 3.

**Step 4: verify.**

```bash
uv run --no-sync pytest -q tests/test_provider_diagnostics.py tests/test_provider_observation_redaction.py
uv run --no-sync ruff check argus/operations argus/contracts tests/test_provider_diagnostics.py tests/test_provider_observation_redaction.py
```

**Acceptance:** diagnostics cannot consume retrieval capacity and cannot emit
secrets even when a provider double supplies them in an exception.

### Task 7: Specify and test the Baywatch/Maya handoff

**Files:**

- Create: `docs/contracts/argus-baywatch-provider-observation-v1.md`
- Create: `docs/contracts/argus-maya-provider-repair-v1.md`
- Create: `tests/test_baywatch_observation_payload.py`
- Modify: `argus/operations/provider_diagnostics.py`

**Step 1: write payload and retry-boundary tests.**

Assert the JSON schema includes opaque IDs/timestamps/reason codes but rejects
tokens, Authorization headers, high-entropy credential strings, query text,
and raw balance amounts.  Assert posting failure creates an Argus self-health
observation; it must not fabricate a provider failure or trigger provider
work.

**Step 2: implement the Baywatch publisher seam.**

Use one small interface, `ProviderObservationPublisher`, with a production
Baywatch HTTP implementation and a test recorder.  Authenticate using a
Homelab secret reference at deployment time, not a source literal.  Retries are
bounded and idempotent by `observation_id`.

**Step 3: document cross-repository ownership.**

The Baywatch contract defines dedupe key and freshness semantics.  The Maya
contract defines the resulting `#inbox` fields: provider, reason code,
observed time, affected capability, safe remediation class, and no secret.
Neither document grants Argus authority to rotate a key nor asks Maya to run a
provider query.

**Step 4: verify.**

```bash
uv run --no-sync pytest -q tests/test_baywatch_observation_payload.py tests/test_provider_diagnostics.py
git diff --check
```

**Acceptance:** a repeated provider condition is representable as one safe,
idempotent observation stream; the repair boundary is unambiguous.

## Phase 3 — Candidate deployment and client proof (human-authorized)

### Task 8: Prepare the Homelab deployment change without client fallbacks

**Files/repositories:**

- Modify in Argus: `docker-compose.yml`, `docs/operations.md`, and any
  existing deployment manifest actually used by Homelab
- Modify in Homelab repository: the selected Compose/systemd/Tailscale Serve
  configuration and encrypted secret-reference manifest
- Test: a candidate-only deployment verification script under
  `scripts/verify_remote_mcp_candidate.py`

**Step 1: map source manifest to deployed manifest.**

Before changing anything, identify the live Homelab service unit/Compose
project, image tag, adapter port, authority port, database URL reference, and
Tailscale Serve owner.  Record source artifact SHA and target image digest.
Do not infer this from a `/healthz` response.

**Step 2: prepare the target topology.**

Use a loopback-bound adapter and authority.  Configure Tailscale Serve to map
only `/mcp` on the canonical MagicDNS HTTPS host to the adapter.  Keep
container/host loopback internal.  Disable/omit Funnel and alternate external
ports.  Configure the tunnel-client to reach the same internal adapter path.

**Step 3: create a nonsecret candidate verifier.**

`verify_remote_mcp_candidate.py` accepts endpoint and token *environment
variable names* rather than values.  It reports only status, protocol branch,
tool names, request IDs, and redacted reason codes.  It performs unauthenticated
rejection, modern discovery/list/call, and legacy initialize/list checks.

**Step 4: verify only after human authorization in a candidate environment.**

```bash
uv run --no-sync python scripts/verify_remote_mcp_candidate.py \
  --endpoint-env ARGUS_CANONICAL_MCP_URL \
  --token-env ARGUS_MCP_CANDIDATE_TOKEN
```

**Acceptance:** evidence identifies source SHA, candidate image digest,
canonical URL, modern protocol proof, legacy protocol proof, and no local
caller fallback.  It does not print the token.

### Task 9: Promote Homelab and verify the actual ingress

**Authority required:** Homelab operator approves service/Tailscale change,
deployment window, database backup/snapshot, and rollback owner.

**Step 1: deploy only the reviewed candidate artifact.**

Capture the pre-promotion image/config identifier and a verified database
backup/snapshot reference.  Apply the forward database migration once.  Start
the new authority/adapter, then Tailscale Serve path.  Do not start a local
Mac/OCI authority as a rescue path.

**Step 2: run the ingress matrix.**

From an authorized tailnet caller, prove unauthenticated 401/403 and
authenticated modern `server/discover`, `tools/list`, and harmless tool call at
the exact canonical URL.  Prove a documented legacy client on the same URL.
Validate response content type before parsing JSON/SSE.  Capture redacted
receipt only.

**Step 3: stop on any ambiguous result.**

An HTTP 200 `/healthz`, a container-loopback call, a raw-IP success, a
transport-only SSE frame, or an initialized connection without a tool call does
not pass this gate.  Restore the prior image/Serve mapping or use the database
forward-repair plan specified at promotion if the source of failure is not
immediately reversible.

**Acceptance:** the real edge is proven, not merely configured.

### Task 10: Attach direct Codex and Claude Code clients

**Authority required:** client owner approves configuration and scoped
caller-token reference.

**Step 1: configure only the canonical remote endpoint.**

Use Codex `config.toml` and the installed Claude Code remote HTTP command with
secret-reference variables.  Do not place a token in shell history, config,
logs, or a repository.  Do not configure stdio/local fallback.

**Step 2: independently prove each client.**

For each client separately: list/discover Argus, run one harmless allowed tool,
and record URL hostname/path, protocol revision, request ID, and outcome.  A
client's "configured" status alone is insufficient.

**Step 3: rollback client-only failures.**

Remove the new entry/reference; do not modify authority/provider configuration
to compensate.  Re-open source diagnosis against recorded redacted output.

**Acceptance:** both direct clients work against precisely one remote URL and
have no ability to launch local Argus execution.

### Task 11: Attach ChatGPT through Secure MCP Tunnel, not a public listener

**Authority required:** OpenAI organization/workspace administrator creates the
tunnel, associates it with the desired workspace, issues runtime credential,
and enables the appropriate developer-mode/app permissions.

**Step 1: deploy tunnel-client on Homelab only.**

It uses outbound HTTPS to OpenAI and an internal adapter URL.  Bind its local
admin/health interface to loopback.  Store tunnel runtime credential through
the approved Homelab secret mechanism; never in Argus code, Baywatch, or Slack.

**Step 2: prove tunnel transport and end-to-end tool semantics separately.**

Check tunnel-client readiness/metrics, then ChatGPT app tool discovery and one
harmless call.  Record that the request reached the same authority receipt as
the direct client.  Tunnel healthy is insufficient without the tool call.

**Step 3: rollback.**

Disable/revoke tunnel association/runtime credential and stop tunnel-client;
keep Tailscale-only Argus ingress intact and never create a public endpoint.

**Acceptance:** ChatGPT can use Argus privately through outbound tunnel traffic
only; no inbound public Argus access exists.

## Phase 4 — Research and operations production proof

### Task 12: Validate research economics, operations, and final documentation

**Authority required:** requester/operator authorizes the bounded real query,
daily scheduler enablement, and final documentation/push review.

**Step 1: perform the lowest-cost bounded real-query proof.**

Run one ordinary research request with normal per-caller budget and capture a
redacted receipt.  Prove an admitted result meets 3/2.  Separately use a
synthetic/controlled fixture or authorized real case to prove the
`insufficient_evidence` result.  Escalate to recurring/prepaid capacity only
when the recorded lower-tier evidence is insufficient or a required capability
is declared.

**Step 2: enable the daily no-spend schedule.**

After verifying the job source in a candidate, enable the Homelab timer.  Let
one scheduled run post a redacted observation.  Confirm Baywatch dedupes a
repeat and Maya produces one safe `#inbox` repair packet for an injected/test
condition.  Never inject a real credential failure merely to test alerts.

**Step 3: execute final verification.**

```bash
uv sync --frozen --extra dev --extra mcp
ARGUS_AUTOLOAD_DOTENV=false uv run --no-sync pytest -q
uv run --no-sync ruff check argus tests
git diff --check
git status --short
```

The full suite must complete with an exit code of zero.  If environmental tests
need a documented exclusion, record the reason and rerun the complete declared
environment before claiming success.

**Step 4: regenerate visual README last.**

Only after every source, remote-ingress, client, research, and operations gate
is attached to review, regenerate the visual README/overview.  Render/inspect
it if the repository toolchain supports that.  It must depict the deployed
canonical endpoint, one authority, direct clients, tunnel path, research 3/2
floor, and repair ownership—not a desired state that failed verification.

**Step 5: final commit and push.**

Stage exact reviewed paths, run final diff/status checks, commit, then push the
approved branch.  The README regeneration commit is the final artifact before
remote push.

**Acceptance:** source, deployment, direct clients, tunnel, research outcome,
and repair loop each have their own evidence; none is inferred from another.

## Review checklist before implementation authorization

- [ ] Parent task accepts the target architecture and 3/2 semantic contract.
- [ ] Homelab operator names the canonical MagicDNS endpoint and rollback owner.
- [ ] Maya owner accepts the bounded repair packet and secret-update boundary.
- [ ] Baywatch owner accepts observation schema, freshness, and dedupe key.
- [ ] OpenAI workspace administrator accepts tunnel/account actions and private
      app scope.
- [ ] Budget authority accepts the real-query/bakeoff limit.
- [ ] The implementation starts from the v2 source baseline, not the obsolete
      `mcp==1.27.0` premise.
