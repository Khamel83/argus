# Fan-out routing and runaway guard — implementation brief

Status: changes A and B are implemented on this branch (`argus/broker/guard.py`,
`argus/broker/execution.py`, `argus/operations/accepted.py`). Change C needs live
provider keys and is not done. Known gaps against this brief: guard counters are
process-local rather than in the shared authority database; the guard covers
`POST /api/search` only (not recover, expand or workflows); a repeated query
beyond the limit runs free-only rather than cache-only.

## Problem

1. Free-tier results are thin. DuckDuckGo (policy block) and Yahoo (scraped) are
   the only keyless providers, and the router treats tier 0 as a cheap first
   pass, so ordinary queries often get one or two weak hits.
2. The budget rules exist to stop runaway callers (a loop issuing the same
   search thousands of times), but they do it by rationing every query, which
   degrades normal use. Spend protection and result quality are coupled when
   they should be separate.
3. `docs/STATUS.md` records Tavily/Exa/Linkup 400, Parallel 411, Serper 403 and
   DuckDuckGo policy block on the last probes. Fixes shipped afterwards were
   never re-probed. Some "bad results" are probably dead providers.

## Change A — fan out across every free and monthly provider

Goal: a normal query uses all tier 0 and tier 1 providers and fuses them.
Tier 3 (one-time credits) stays a last resort.

- `argus/broker/execution.py`, the skip at the "free results satisfied query"
  branch (~line 426): only apply it to `tier >= 3`. Tier 1 must always run
  (subject to its own monthly budget and health).
- Add a tier 3 gate: skip tier 3 unless the fused result count so far is below
  `ARGUS_MIN_GOOD_RESULTS` (default 3 distinct domains).
- Step 1 can stay sequential. Step 2 (optional): run tier 0 and tier 1 providers
  concurrently with `asyncio.gather` under the existing per-provider deadlines,
  then pass all `provider_results` to the existing RRF fusion.
- Update `tests/test_broker.py` (~line 2050, `free is enough`): it asserts the
  old behavior. Add tests: tier 1 runs even when tier 0 filled `max_results`;
  tier 3 is skipped when enough domains exist; tier 3 runs when tier 0+1 return
  fewer than the threshold.
- Document `ARGUS_MIN_GOOD_RESULTS` in README, `.env.example` and CHANGELOG
  under `[Unreleased]`. Do not bump versions.

## Change B — runaway guard (separate from result quality)

Deterministic first, model second.

Identity: key every counter on the authenticated principal derived server-side
from the API key or token that passed authentication (in the accepted-operation
service this is `principal`, which becomes `query.caller`). Never use the
caller-supplied label (`request.caller`, stored in `metadata["caller_label"]`),
because a caller can rotate it on every request.

Counters are kept in the authority database so they survive restarts and are
shared across workers. Admission must be atomic: check the limits and reserve
the request in one operation (a single transaction or an atomic
increment-and-compare) before any provider is dispatched, so concurrent workers
cannot all see a count below the limit. A retry counts as a new request. A
reservation for a request that never dispatched a provider is released.

| Rule | Default | Action |
|------|---------|--------|
| Same normalized query repeated | 3 in 10 min | Serve cache only; no provider call |
| Calls per caller | 60 in 10 min | Degrade to tier 0 only |
| Calls per caller | 300 per day | Deny with 429 and a reason |
| Paid calls (tier 1 + 3), all callers | per-provider monthly budget (existing) | Existing skip |

Normalization: Unicode NFKC, lowercase, collapse whitespace, trim. Do not strip
punctuation or symbols, since that merges distinct queries such as `C++` and
`C#`. Hash the normalized string for the counter key.

Model check, only on a trip, never per query:

- Trigger when a caller reaches the "60 in 10 min" rule or 25 near-identical
  queries in a row.
- Send the last N queries (query text, timestamps, result counts) to the
  configured local model with one question: "loop or legitimate?" Expect a JSON
  verdict `{"runaway": bool, "reason": str}`.
- Timeout 10 s. On timeout, error or unparseable output, fail closed: keep the
  caller degraded to free-only for 15 minutes.
- The model can never disable a deterministic rule. On a "legitimate" verdict
  the only effect is a bounded exception: the 10-minute call limit doubles
  (60 to 120) for that caller for 1 hour. The repeated-query rule, the daily cap
  and all provider budgets stay enforced. Query text is attacker-controlled, so
  treat the verdict as advice that can only loosen one limit by a fixed amount.
- Log every trip and verdict. Surface them in `argus budgets`.

Privacy boundary for the model call:

- Send at most the last 20 queries, each truncated to 200 characters, plus
  timestamps and result counts. No caller tokens, headers or results content.
- `ARGUS_GUARD_MODEL_URL` must resolve to a loopback or private-network address
  (reuse the SSRF address checks in `argus/extraction/ssrf.py` in reverse:
  accept only private ranges). Refuse to start the guard with a public URL.
- The model endpoint must not retain prompts. Argus stores only the verdict and
  reason in its own log, never the query text it sent.

Config: `ARGUS_GUARD_ENABLED`, `ARGUS_GUARD_MODEL_URL`, and the thresholds above
as env vars. If no model URL is set, skip the model step and keep the
deterministic rules.

## Change C — verify providers before trusting any of this

- Re-run one bounded probe per provider through `POST /api/admin/test-provider`
  (one result, no fallback, durable receipt) for Tavily, Exa, Linkup, Parallel,
  Serper and DuckDuckGo.
- Record the outcomes in `docs/STATUS.md`. Disable providers that still fail,
  rather than leaving them in the routing table returning errors.

## Acceptance

- A query with all free and monthly providers healthy returns fused results
  from at least three providers.
- 100 identical queries in a minute from one principal, sent concurrently,
  produce at most 3 admitted query executions. The other 97 are served from
  cache. (Each admitted execution fans out to every healthy tier 0 and tier 1
  provider, so upstream calls are 3 times that provider count, not 3.)
- A caller degraded by the 60-in-10-minutes rule still gets tier 0 results, not
  an error. A caller past the 300-per-day cap gets a 429 with a reason.
- Rotating `query.caller` between requests does not reset any counter.
- `C++` and `C#` do not trigger each other's repeat limit.
- Full test suite passes with `uv run pytest`.
