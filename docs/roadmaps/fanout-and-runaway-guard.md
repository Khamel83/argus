# Fan-out routing and runaway guard — implementation brief

Status: proposal, not implemented. Hand this to the implementing agent as-is.

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

Counters, per `query.caller` (fallback: API key id), kept in the authority
database so they survive restarts and are shared across workers:

| Rule | Default | Action |
|------|---------|--------|
| Same normalized query repeated | 3 in 10 min | Serve cache only; no provider call |
| Calls per caller | 60 in 10 min | Degrade to tier 0 only |
| Calls per caller | 300 per day | Deny with 429 and a reason |
| Paid calls (tier 1 + 3), all callers | per-provider monthly budget (existing) | Existing skip |

Normalization: lowercase, collapse whitespace, strip punctuation.

Model check, only on a trip, never per query:

- Trigger when a caller reaches the "60 in 10 min" rule or 25 near-identical
  queries in a row.
- Send the last N queries (query text, timestamps, result counts) to the
  configured local model with one question: "loop or legitimate?" Expect a JSON
  verdict `{"runaway": bool, "reason": str}`.
- Timeout 10 s. On timeout, error or unparseable output, fail closed: keep the
  caller degraded to free-only for 15 minutes.
- On a "legitimate" verdict, lift the degrade for that caller for 1 hour.
- Log every trip and verdict. Surface them in `argus budgets`.

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
- 100 identical queries in a minute produce at most 3 provider calls.
- A guarded caller still gets free-tier results, not an error.
- Full test suite passes with `uv run pytest`.
