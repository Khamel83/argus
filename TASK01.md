# TASK01: App Composition And Characterization Baseline

## Objective
Replace implicit API composition with an explicit app factory and dependency seams while preserving current observable behavior.

## Why This Task Comes Now
This is the earliest meaningful observable improvement for a mixed API project. It makes application startup, dependency overrides, and future refactor work safer without changing product features.

## Scope
- Add an explicit `create_app()` path for FastAPI assembly.
- Move broker/config lifecycle toward injectable dependencies instead of module globals.
- Preserve current routes, middleware behavior, and request/response contracts.
- Expand tests that characterize current HTTP behavior before deeper architectural changes.

## What To Build
- App factory function that returns a configured FastAPI instance.
- A dependency seam for broker access that can be overridden in tests.
- Backward-compatible module-level `app` export so existing launch paths keep working.
- Characterization tests for health, search, request ID, and rate-limit behavior under the new composition path.

## Files To Create Or Modify
- `argus/api/main.py`
- `argus/api/routes_search.py`
- `argus/api/routes_health.py`
- `argus/api/routes_admin.py`
- `argus/api/routes_extract.py`
- `tests/test_api.py`

## Dependencies
- Existing FastAPI routes and schemas
- Existing broker factory
- Existing API tests

## Implementation Notes
- Preserve import-time compatibility for `uvicorn argus.api.main:app`.
- Prefer app state or explicit dependency providers over hidden module-level singletons.
- Do not change route paths or response schemas in this task.
- Keep middleware order explicit and covered by tests.

## Verification Steps
- Run `pytest tests/test_api.py`
- Run the full `pytest` suite if targeted changes touch shared behavior
- Optionally smoke-test `GET /api/health` through the created app factory

## Definition Of Done
- FastAPI app composition is explicit and testable.
- Existing `/api` behavior remains unchanged.
- Tests covering the affected API surface pass.

## Follow-On Notes
This task creates the seam required for broker decomposition in `TASK02.md`.
