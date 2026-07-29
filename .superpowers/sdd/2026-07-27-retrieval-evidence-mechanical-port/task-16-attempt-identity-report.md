# Task 16 attempt-identity propagation report

## Scope

The isolated failure was limited to native `ProviderSearchBatch` adapters.
`ProviderExecutor` received the readiness authority's `attempt_id`, but did
not pass it to the adapter query metadata. `BaseProvider._request_evidence()`
therefore emitted its fallback `<provider>-attempt` identity, which the
authority correctly rejected while binding the returned batch.

Files changed:

- `argus/broker/execution.py`
- `tests/test_broker.py`
- This report

No live systems, workflows, scorecard scope, documentation outside this
evidence report, or Homelab files were changed.

## RED

Regression added: `test_executor_passes_readiness_attempt_id_to_native_provider_batch`.
It uses a real native `ProviderSearchBatch` produced from
`BaseProvider._request_evidence()`, rather than a legacy tuple or a fabricated
post-binding batch.

Command:

```sh
uv run pytest -q tests/test_broker.py::test_executor_passes_readiness_attempt_id_to_native_provider_batch
```

Result before the implementation: `1 failed in 0.41s`.

Exact failure:

```text
ValueError: provider batch attempt identity does not match authority
```

The failure was raised by `_bind_attempt_identity()` after the adapter emitted
the fallback `yahoo-attempt` identity and readiness had authorized a distinct
generated attempt ID.

## GREEN

The executor now supplies `authorization.attempt_id` to `_execute_provider()`,
which writes it as `_provider_attempt_id` in the adapter-only query metadata.
The existing `BaseProvider._request_evidence()` contract then emits the exact
authority ID; no evidence binding behavior was relaxed.

Focused regression:

```sh
uv run pytest -q tests/test_broker.py::test_executor_passes_readiness_attempt_id_to_native_provider_batch
```

Result: `1 passed in 0.31s`.

Affected broker/provider-evidence verification:

```sh
uv run pytest -q tests/test_broker.py tests/test_provider_evidence.py
```

Result: `397 passed in 6.95s`.

Broader accepted-operation/retrieval verification:

```sh
uv run pytest -q tests/test_accepted_operations.py tests/test_accepted_retrieval.py
```

Result: `35 passed, 1 skipped in 1.44s`.

Structural check:

```sh
git diff --check
```

Result: passed with no output.

## Self-review

- The propagation overwrites caller-provided `_provider_attempt_id` only when
  an authority authorization is present, keeping the authority as the identity
  source of truth.
- Direct `_execute_provider()` test callers retain the optional default and
  are unchanged.
- The regression exercises the real native evidence path that previously
  failed; it would fail again if the authorization ID were not passed to the
  adapter.
- Scope is limited to four executable lines and the regression/report; no
  fallback binding rule, provider adapter, budget, readiness, or live path was
  broadened.

## Commit

Committed with subject: `fix: propagate readiness attempt identity to providers`.
