# Argus Readiness Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Close remaining readiness evidence and correct future extraction acceptance provenance without provider requests.

**Architecture:** Production extraction stays behind the HTTP authority. The accepted-operation path derives release identity from the admitted runtime manifest. The original request URL remains the acceptance URL even when an extractor returns a canonical artifact URL.

**Tech Stack:** Python, FastAPI, SQLAlchemy, pytest, Docker Compose, PostgreSQL.

## Global Constraints

- Do not issue provider requests, retries, billing changes, credential rotation, or Valyu enablement.
- Do not expose secrets or raw content.
- Preserve user-owned dirty files and immutable existing receipts.
- Keep source, image, runtime, authenticated operation, receipt, and downstream effect as distinct evidence.
- Use hermetic tests for source changes and the private operator procedure for promotion.
- Keep external browser access fail-closed without a valid attestation.

### Task 1: Record exact Maya readback

**Files:** `docs/STATUS.md`, `TODO.md`, private dated operations evidence.

- [ ] Record the read-only native Maya result for capture `b7ce142e053345f28e11d6fc70f9632e`: parent matched, one child page, page `4650664d214646888cd108ee263ed323`, hash verification true, and no semantic-consumption claim.
- [ ] State that the historical page source URL was the PEP root and that immutable historical receipts are not rewritten.

### Task 2: Bind future accepted extraction provenance

**Files:** `argus/operations/accepted.py`, `argus/extraction/extractor.py`, `tests/test_http_authority.py`, `tests/test_extraction_outcomes.py`.

- [ ] Add failing tests using a 40-character manifest revision and an extractor artifact URL that differs from the exact request URL.
- [ ] Run `pytest -q tests/test_http_authority.py tests/test_extraction_outcomes.py -k 'release_identity or accepted_extraction'` and observe the missing admitted identity or URL preservation.
- [ ] Derive canonical extraction `release_identity` from `create_operational_status().build["source_revision"]`: use `argus-<full-sha>` for a valid admitted revision and `unknown-release` otherwise.
- [ ] Ensure final legacy projection uses the original `extract_url` argument after accepted finalization, retaining extractor output only in artifact provenance.
- [ ] Run `pytest -q tests/test_http_authority.py tests/test_extraction_outcomes.py tests/test_extraction.py`, then commit `fix: bind accepted extraction provenance`.

### Task 3: Close no-request operational evidence

**Files:** `docs/STATUS.md`, `TODO.md`, private dated operations evidence.

- [ ] Diagnose the saved Serper 403 only from its sanitized stored request contract and response classification; retain uncertainty unless evidence identifies a cause.
- [ ] Reconcile only durable Argus spend records. Keep failed-call reservations uncertain without an authoritative provider statement.
- [ ] Verify whether browser policy has a trusted external attestation. If absent, retain fail-closed behavior and document the exact missing authority/configuration.

### Task 4: Apply bounded hardening only where tests prove a defect

**Files:** affected lifecycle/workflow modules and matching focused tests; `TODO.md`.

- [ ] Trace resource shutdown, optional workflow LLM-gateway admission, and scorecard handoff.
- [ ] Implement only a local, independently testable lifecycle or fail-closed gate defect; leave deployment automation as a scoped operations item if it needs new infrastructure authority.
- [ ] Run affected tests and commit each bounded repair separately.

### Task 5: Verify, promote if source changed, and publish closure

**Files:** `docs/STATUS.md`, `TODO.md`, `CONTEXT.md`, `AUDIT_REPORT.md`, affected specs, private dated evidence.

- [ ] Run focused and required repository verification.
- [ ] When source changes, use the private procedure to build/admit a candidate and record source SHA, image digest, root Compose runtime, migration/no-drift result, authenticated health, and soak. Do not issue a provider request.
- [ ] Publish a provider matrix that labels each provider verified, blocked, unconfigured, or not revalidated, with its evidence layer and next action.
