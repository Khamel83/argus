# TASK03: Result Pipeline And Persistence Boundaries

## Objective
Isolate ranking, dedupe, cache writes, and search persistence behind explicit pipeline boundaries.

## Why This Task Comes Now
Once provider execution is separated, result processing becomes the next obvious seam. Isolating it reduces hidden coupling and improves test focus.

## Scope
- Extract post-provider result processing from `SearchBroker`.
- Clarify where cache policy ends and persistence begins.
- Keep public response ordering and metadata stable.

## What To Build
- A result pipeline component that merges provider outputs, ranks, dedupes, trims, and prepares the final response.
- A persistence seam for recording completed search runs.
- Tests that characterize final-result ordering, caching policy, and persistence failure handling.

## Files To Create Or Modify
- `argus/broker/router.py`
- `argus/broker/ranking.py`
- `argus/broker/dedupe.py`
- `argus/persistence/db.py`
- `tests/test_broker.py`

## Dependencies
- Completion of `TASK02.md`

## Implementation Notes
- Preserve reciprocal-rank-fusion behavior and current dedupe normalization rules.
- Persistence failures should remain non-fatal, but their boundary should be explicit.
- Cache writes should stay policy-driven and easy to test.

## Verification Steps
- Run `pytest tests/test_broker.py`
- Run the full `pytest` suite
- Add focused tests for persistence failure and cache-hit behavior

## Definition Of Done
- Result processing no longer lives as opaque inline logic in `SearchBroker`.
- Cache and persistence decisions are explicit and covered by tests.
- Final response behavior remains stable.

## Follow-On Notes
This task leaves infrastructure concerns concentrated enough to address cleanly in `TASK04.md`.
