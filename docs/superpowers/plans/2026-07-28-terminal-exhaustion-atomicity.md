# Terminal Exhaustion Atomicity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make recurring terminal-exhaustion fanout atomic and restrict exact observation expiry to bounded authoritative terminal spend.

**Architecture:** Split the existing observation writer into a public transaction wrapper and one session-bound primitive. `record_terminal_exhaustion()` will acquire one transaction and one database timestamp, validate the reset before writing, then use the primitive for every registered account scope. Exact expiry remains represented on `ProviderObservation`, but both model validation and a persistence capability gate restrict it to protected authoritative `spend=exhausted` observations within one year.

**Tech Stack:** Python 3.12, SQLAlchemy, SQLite, PostgreSQL 16, pytest, Ruff.

## Global Constraints

- Use strict RED to GREEN test-driven development.
- Preserve ordinary integer TTL behavior.
- One-time exhaustion must retain `expires_at=None`.
- Do not access live providers, production systems, or credentials; do not push.

---

### Task 1: Exact-expiry authorization and bounds

**Files:**
- Modify: `argus/broker/readiness.py`
- Modify: `argus/persistence/readiness.py`
- Test: `tests/test_provider_readiness.py`

**Interfaces:**
- Consumes: `ProviderObservation.expires_at`
- Produces: model and persistence validation for exact expiry, with an internal `allow_exact_expiry` capability

- [ ] **Step 1: Write failing model and defensive-persistence tests**

Cover non-spend dimensions, non-exhausted spend, non-authoritative sources, TTL-plus-expiry, naive timestamps, and a 100-year expiry. Mutate a valid frozen observation to prove persistence independently rejects the same invalid shapes and bounds.

- [ ] **Step 2: Run the exact tests and observe the authorization/bounds failures**

Run:

```bash
uv run pytest -q tests/test_provider_readiness.py -k "exact_expiry"
```

- [ ] **Step 3: Add closed semantic validation and persistence capability checks**

Accept exact expiry only when all conditions hold:

```python
dimension == "spend"
state == "exhausted"
source in {"provider_authoritative", "provider_reconciliation"}
protected is True
observed_at < expires_at <= observed_at + timedelta(seconds=31_536_000)
```

At persistence, independently require the same shape, an internal capability, and:

```python
database_now < expires_at <= database_now + timedelta(seconds=31_536_000)
```

- [ ] **Step 4: Run the exact-expiry tests and observe GREEN**

Run the command from Step 2 and require zero failures.

### Task 2: Atomic terminal fanout

**Files:**
- Modify: `argus/persistence/readiness.py`
- Test: `tests/test_provider_spend.py`

**Interfaces:**
- Consumes: the session-bound observation writer from Task 1
- Produces: `record_terminal_exhaustion(...)` using one transaction and one database timestamp

- [ ] **Step 1: Write failing atomicity and single-clock tests**

Exercise `record_terminal_exhaustion()` through its public API. Inject a failure after an intermediate scope and assert no terminal rows or snapshots commit. Inject a clock that would cross the reset on a second read and assert the method reads database time exactly once and commits all four rows with identical expiry.

- [ ] **Step 2: Run the atomicity tests and observe partial writes/current multiple transactions**

Run:

```bash
uv run pytest -q tests/test_provider_spend.py -k "recurring_terminal"
```

- [ ] **Step 3: Extract a session-bound writer and make fanout one transaction**

The public observation writer opens its normal transaction and delegates to the session-bound primitive. Terminal fanout opens one transaction, reads database time once, validates before the first write, and writes/materializes each account scope through that primitive. Tests inject failure at the snapshot-write seam inside the transaction.

- [ ] **Step 4: Run the atomicity tests and observe GREEN**

Run the command from Step 2 and require zero failures.

### Task 3: PostgreSQL proof and final verification

**Files:**
- Modify: `scripts/verify_provider_readiness_postgres.py`
- Modify: `.superpowers/sdd/task-6-report.md`

**Interfaces:**
- Consumes: public atomic terminal API
- Produces: sanitized PostgreSQL verifier evidence for rollback, exact fractional reset, and the reset boundary

- [ ] **Step 1: Extend the PostgreSQL verifier**

Inject a mid-fanout fault and verify zero terminal rows, then write the fractional reset successfully and verify all four identical rows. Retain denial at `reset_at - 1 microsecond`, allowance at equality, and permanent one-time precedence.

- [ ] **Step 2: Run a disposable PostgreSQL 16 migration and verifier**

Migrate a guarded `argus_restore_*` scratch database through Alembic head, run the verifier, hash the sanitized artifact, and confirm container, volume, and tunnel removal.

- [ ] **Step 3: Run focused, full, and static verification**

```bash
uv run pytest -q tests/test_provider_readiness.py tests/test_provider_spend.py tests/test_distribution_artifacts.py tests/test_spend_boundaries.py
uv run pytest -q
uv run ruff check argus/broker/readiness.py argus/persistence/readiness.py scripts/verify_provider_readiness_postgres.py tests/test_provider_readiness.py tests/test_provider_spend.py
git diff --check
```

- [ ] **Step 4: Update the report and commit explicit paths**

Record RED/GREEN, SQLite/PostgreSQL atomicity, artifact hash, cleanup, and final test counts. Stage only the plan and changed implementation/test/verifier paths, then create one separate commit.
