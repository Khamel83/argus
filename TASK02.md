# TASK02: Broker Orchestration Extraction

## Objective
Split `SearchBroker` into a thin coordinator plus focused services for provider execution and session-aware search flow, while introducing cheap-first routing behavior.

## Why This Task Comes Now
`SearchBroker` is the highest-value refactor hotspot. Moving it earlier reduces risk for every later task because most core behavior flows through it.

## Scope
- Extract provider execution orchestration from `SearchBroker.search`.
- Separate session-aware search flow from raw search execution.
- Introduce explicit provider stop conditions and hedge triggers.
- Keep ranking, dedupe, cache, persistence, and response shape behavior stable unless a deliberate simplification is chosen.

## What To Build
- A provider execution service that owns provider iteration, health handling, budget handling, and trace collection.
- A routing policy seam that can decide when to stop after a successful provider and when a backup provider should be attempted.
- A search coordinator that assembles cache lookup, execution, and response construction.
- A session-aware wrapper or service for `search_with_session`.
- Focused tests around success, skip, error, cooldown, budget-exhausted, early-stop, and hedge-trigger paths.

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
- Optimize for cheapness and reliability, not maximum provider fan-out. The default path should avoid querying extra providers once a search is already "good enough."

## Verification Steps
- Run `pytest tests/test_broker.py`
- Run the full `pytest` suite
- Compare behavior around cache hits, provider skips, and exception handling

## Definition Of Done
- `SearchBroker` is materially smaller and easier to reason about.
- Provider execution logic is isolated behind its own seam.
- Provider routing behavior is explicit, measurable, and aligned with cheap-first reliability goals.

## Follow-On Notes
This task sets up `TASK03.md` by isolating the execution half of the broker from the result-processing half.
