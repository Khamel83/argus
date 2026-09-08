# Task 2 report: bind future accepted extraction provenance

## Status

Implemented and committed the accepted extraction provenance changes.

## Changes

- `AcceptedOperationService.extract` now derives extraction `release_identity`
  from `create_operational_status().build["source_revision"]`.
- A valid lower-case 40-character revision becomes `argus-<full-sha>`.
- Missing or invalid admitted revisions become `unknown-release`.
- Durable extraction claim and projection state now use `_safe_persisted_url`,
  preserving benign paths, query values, and fragments while retaining the
  credential and sensitive-parameter redaction behavior.
- Added regression coverage for valid and invalid manifest revisions and for
  an extractor artifact URL that differs from the exact request URL. The
  latter verifies durable and reloaded evidence retains the request URL.

## TDD and verification

Red phase:

- `pytest -q tests/test_http_authority.py tests/test_extraction_outcomes.py -k 'release_identity or accepted_extraction'`
  initially failed on the durable URL assertion because the stored URL was
  reduced to `https://example.com/`.

Green phase:

- `pytest -q tests/test_accepted_operations.py tests/test_extraction_outcomes.py -k 'accepted_extraction or release_identity or safe_semantic'`
  passed: 5 passed.
- `pytest -q tests/test_http_authority.py tests/test_extraction_outcomes.py -k 'release_identity or accepted_extraction'`
  passed: 2 passed.
- `pytest -q tests/test_accepted_operations.py tests/test_extraction_outcomes.py`
  produced 126 passed, 1 skipped, and 3 failures in existing domain-policy
  recovery/frozen-fixture tests. Those failures raise
  `ValueError: no cache admission outcome for 'invalid_request'/'domain_policy_unrecognized'`
  and do not exercise the Task 2 changes.
- `uv run --no-sync ruff check argus/operations/accepted.py argus/persistence/search_ledger.py tests/test_accepted_operations.py tests/test_extraction_outcomes.py`
  passed.
- `uv run --no-sync python -m compileall -q argus/operations/accepted.py argus/persistence/search_ledger.py`
  passed.
- `git diff --check` passed.

No network or provider calls were made, and no secrets were inspected.

## Commit

The implementation commit is recorded in the handoff message.

## Follow-up: preserve historical extraction claim replay

### Status

Fixed the idempotency regression caused by path/query-preserving extraction
URLs. Historical root-normalized receipts now replay without changing their
durable rows.

### Root cause

The current `_extraction_claim_state` persists a credential-safe path/query URL
and therefore produces a different source fingerprint from claims written by
the earlier root-only URL projection. Existing extraction plans retain their
original immutable fingerprint, so a retry of the same path/query URL raised
`ExtractionAcceptanceConflict` before loading the committed receipt.

### Changes

- Kept the current path/query-preserving `_safe_persisted_url` projection for
  all new extraction claim and projection rows.
- Added a legacy root-only fingerprint candidate for matching existing
  immutable claim and projection rows. The raw URL identity hash remains part
  of the fingerprint, so distinct URLs do not become interchangeable.
- Left historical plan JSON, projection JSON, fingerprints, and mirror rows
  unchanged during replay.
- Added a hermetic regression that seeds a complete root-normalized historical
  receipt, retries with a path/query URL, verifies the original receipt is
  returned, and checks every captured durable field remains unchanged.

### TDD and verification

Red phase:

- The new historical replay regression failed with
  `ExtractionAcceptanceConflict` against the root-normalized source
  fingerprint.

Green phase:

- `uv run --no-sync pytest -q tests/test_extraction_outcomes.py -k historical_root_normalized_claim_replays_path_query_retry_unchanged`
  passed: 1 passed.
- `uv run --no-sync pytest -q tests/test_extraction_outcomes.py` passed:
  106 passed, 1 skipped.
- Concurrency and conflict coverage passed: 6 passed.
- `uv run --no-sync ruff check argus/persistence/search_ledger.py tests/test_extraction_outcomes.py`
  passed.
- `uv run --no-sync python -m compileall -q argus/persistence/search_ledger.py`
  passed.
- `git diff --check` passed.

No network, provider, or secret access was used.

### Commit

This follow-up is committed as `5b00dc8` (`fix: replay historical extraction
claims`).

### Concerns

The compatibility candidate is intentionally limited to the legacy root-only
URL shape. New rows continue to fingerprint the redacted path/query URL, and
the separate raw URL identity hash prevents a different path/query request
from replaying a historical receipt.
