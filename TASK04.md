# TASK04: Config, Secrets, And Session Infrastructure Cleanup

## Objective
Separate configuration loading, secrets lookup, and session persistence concerns into clearer, testable seams.

## Why This Task Comes Now
By this point, API and broker composition are cleaner. Infrastructure cleanup can then happen without re-opening the major behavioral seams already stabilized.

## Scope
- Isolate secrets lookup from config model creation.
- Reduce reliance on global config singleton behavior where practical.
- Clarify the session-store boundary between in-memory state and persistence.

## What To Build
- A configuration loader path that clearly separates env parsing, fallback secret resolution, and cached settings access.
- Session persistence seams that avoid expensive existence checks and clarify load/write behavior.
- Tests covering config fallbacks, singleton access expectations, and session persistence edge cases.

## Files To Create Or Modify
- `argus/config.py`
- `argus/sessions/store.py`
- `argus/sessions/persistence.py`
- `tests/test_config.py`
- `tests/test_sessions.py`

## Dependencies
- Completion of `TASK03.md`

## Implementation Notes
- Keep the public config API stable in early passes if possible.
- Preserve current environment variable names.
- Avoid introducing unnecessary abstraction layers; the goal is explicit boundaries, not framework-style indirection.

## Verification Steps
- Run `pytest tests/test_config.py tests/test_sessions.py`
- Run the full `pytest` suite
- Add tests for fallback resolution and persistence reload behavior

## Definition Of Done
- Config and secrets concerns are no longer tightly coupled in one opaque path.
- Session storage behavior is explicit and better isolated.
- All affected tests pass.

## Follow-On Notes
This task prepares the codebase for concurrency and contract hardening in `TASK05.md`.
