# TASK02: Broker Orchestration Extraction

## Objective
Split `SearchBroker` into a thin coordinator plus focused services for provider execution and session-aware search flow.

## Why This Task Comes Now
`SearchBroker` is the highest-value refactor hotspot. Moving it earlier reduces risk for every later task because most core behavior flows through it.

## Scope
- Extract provider execution orchestration from `SearchBroker.search`.
- Separate session-aware search flow from raw search execution.
- Keep ranking, dedupe, cache, persistence, and response shape behavior stable.

## What To Build
- A provider execution service that owns provider iteration, health handling, budget handling, and trace collection.
- A search coordinator that assembles cache lookup, execution, and response construction.
- A session-aware wrapper or service for `search_with_session`.
- Focused tests around success, skip, error, cooldown, and budget-exhausted paths.

## Files To Create Or Modify
- `argus/broker/router.py`
- `argus/broker/`
- `tests/test_broker.py`
- Potential new internal modules under `argus/broker/`

## Dependencies
- Completion of `TASK01.md`
- Existing broker policies, ranking, health, budget, and cache modules

## Implementation Notes
- Reduce `SearchBroker.search` to orchestration only.
- Preserve existing response models and provider trace semantics.
- Prefer small internal services over a deep class hierarchy.
- Keep factory wiring simple so later dependency injection remains clear.

## Verification Steps
- Run `pytest tests/test_broker.py`
- Run the full `pytest` suite
- Compare behavior around cache hits, provider skips, and exception handling

## Definition Of Done
- `SearchBroker` is materially smaller and easier to reason about.
- Provider execution logic is isolated behind its own seam.
- Existing broker behavior is preserved by tests.

## Follow-On Notes
This task sets up `TASK03.md` by isolating the execution half of the broker from the result-processing half.
