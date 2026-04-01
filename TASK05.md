# TASK05: Provider Contracts, Bounded Concurrency, And Operational Hardening

## Objective
Strengthen internal contracts and operational behavior so the refactored architecture is safe under real usage, not just cleaner on paper.

## Why This Task Comes Now
Hardening belongs after boundaries exist. Otherwise the project risks encoding operational assumptions into code that still needs to move.

## Scope
- Formalize provider contract coverage.
- Decide and implement bounded concurrency or explicitly preserve sequential execution with tests.
- Improve observability and failure characterization around provider routing.

## What To Build
- Contract tests for provider adapters against the `BaseProvider` interface.
- Execution tests around provider timeout, skip, error, and recovery paths.
- If adopted, bounded concurrency controls with deterministic tests.
- Additional logging or trace assertions that make production behavior easier to reason about.

## Files To Create Or Modify
- `argus/providers/`
- `argus/broker/router.py`
- `argus/broker/health.py`
- `tests/test_providers.py`
- `tests/test_broker.py`

## Dependencies
- Completion of `TASK04.md`

## Implementation Notes
- Concurrency is optional only if sequential execution is explicitly retained as a conscious design decision and tested as such.
- Avoid changing provider-specific request semantics in this task.
- Focus on operability, determinism, and contract clarity.

## Verification Steps
- Run `pytest tests/test_providers.py tests/test_broker.py`
- Run the full `pytest` suite
- If concurrency changes are introduced, add timing-independent behavioral tests

## Definition Of Done
- Provider behavior is protected by contract coverage.
- Execution behavior under failure and load assumptions is explicit.
- Operational choices are documented and verified.

## Follow-On Notes
This task feeds the final compatibility and release gate in `TASK06.md`.
