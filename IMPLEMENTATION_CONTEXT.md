# Implementation Context

## Run Configuration
- Project name: Argus
- Project type: mixed
- Primary spec path: `README.md`
- Supporting doc paths: `1shot/PROJECT.md`, `1shot/ROADMAP.md`, `1shot/STATE.md`, `docs/`
- Stack notes: Python 3.11+, FastAPI, Click CLI, MCP server, SQLAlchemy, SQLite/Postgres persistence, provider adapter model
- Runtime target: mixed (`local`, `docker`)
- Verification mode: mixed (`tests`, `api response`, `cli output`)
- Ask user questions tool available: no
- Todo tool available: no
- Git available: yes
- Can run tests: yes
- Can run app locally: assumed yes

## Project Name
Argus

## Project Type
Mixed: API, CLI, MCP server, and Python library

## Spec Sources
- `README.md`
- `1shot/PROJECT.md`
- `1shot/ROADMAP.md`
- `1shot/STATE.md`
- Repo code in `argus/` and `tests/`
- User direction on 2026-03-31: create the full operator doc and a staged architectural refactor plan, not a rewrite

## Current Status
- Existing product is functional and already structured into domain modules.
- `pytest` passed on 2026-03-31: `126 passed in 24.51s`.
- This run is planning-first. No production code refactor has started yet.
- Durable planning artifacts are being added at repo root because there is no existing planning folder convention.

## Project Goal
Produce a staged architectural refactor plan that improves correctness, lifecycle control, maintainability, extension safety, and scale-readiness without breaking Argus's public HTTP, CLI, MCP, or Python interfaces.

## Success Criteria
- The plan is explicit, sequential, and executable in small slices.
- Each stage is independently shippable and testable.
- Public contracts stay stable unless a later stage explicitly versions them.
- High-risk coupling points are called out before code changes begin.
- The earliest execution slice produces an observable improvement without destabilizing current behavior.

## Confirmed Inputs
- Argus is already shipping search, extraction, and multi-turn session features.
- Core modules exist for API, broker, providers, extraction, sessions, persistence, and MCP.
- The repo includes automated tests that currently pass.
- Git is available.
- The user wants the architectural refactor to exist as a durable plan.
- Untracked paths currently present: `1shot/`, `docs/sessions/`, `scripts/`.

## Assumptions
- This refactor should preserve all current public interfaces in early phases.
- No rewrite is desired or justified while the test baseline is green.
- SQLite-backed session persistence remains supported.
- Provider routing behavior should remain stable unless explicitly changed in a later task.
- External services can be mocked or adapter-wrapped when direct access is unavailable.
- This run's implementation scope is the planning system itself: operator doc, context, and task files.

## Constraints
- Avoid destructive repo cleanup or unrelated file churn.
- Preserve behavior before moving internals.
- Keep the repo legible for future agents and humans.
- Use the existing code shape as the source of truth for refactor seams.
- Ask-user-questions tooling is unavailable in this runtime, so assumptions must be written down explicitly.

## Dependency Status
- Runtime dependencies: present for local testing.
- Test framework: present and passing.
- External search providers: configurable, but not required for planning.
- Secrets loading: currently mixed into config loading via environment and subprocess fallback.
- Persistence: available through current session and search persistence modules.
- Deployment path: local and Docker both appear supported; no deployment change is required for planning.

## Execution Strategy
1. Preserve current behavior with characterization coverage around the public surfaces most affected by refactor.
2. Introduce explicit app composition and dependency seams so lifecycle and overrides are no longer hidden behind module globals.
3. Decompose `SearchBroker` into smaller units with single-purpose responsibilities.
4. Isolate result processing, persistence, config, and session infrastructure behind clearer boundaries.
5. Add operational hardening for concurrency, contracts, and compatibility.
6. Finish with release-gate verification and migration notes.

## Task Inventory
- `TASK01.md`: App composition and characterization baseline
- `TASK02.md`: Broker orchestration extraction
- `TASK03.md`: Result pipeline and persistence boundaries
- `TASK04.md`: Config, secrets, and session infrastructure cleanup
- `TASK05.md`: Provider contracts, bounded concurrency, and operational hardening
- `TASK06.md`: Compatibility sweep, docs, and release gate

## Completed Tasks
- Intake across repo structure, docs, and representative modules
- Baseline verification with `pytest`
- Decision to use staged architectural refactor planning instead of rewrite
- Creation of operator and task-planning docs

## Key Decisions
- The first execution slice should target app composition because it is the safest high-leverage seam and produces observable API behavior with low product risk.
- `SearchBroker` is the primary refactor hotspot because it currently owns too many responsibilities.
- Public behavior remains the compatibility anchor for all planned changes.
- Planning files live at repo root for now because that is the least surprising placement in this repository.

## Implementation Notes
- `argus/api/routes_search.py` currently uses a module-global broker singleton.
- `argus/api/main.py` constructs middleware and routers around that implicit global lifecycle.
- `argus/broker/router.py` currently mixes cache lookup, routing, provider execution, health tracking, budget tracking, ranking, dedupe, persistence, and session-aware behavior.
- `argus/config.py` currently mixes environment parsing, secrets subprocess fallback, dataclass construction, and a global singleton.
- `argus/sessions/store.py` currently combines in-memory state and persistence lookup paths in one store implementation.

## Risks / Blockers
- No true blockers for planning.
- Unknown non-functional targets remain: expected QPS, concurrency, latency budgets, and deployment envelope.
- Hidden external consumers may rely on current module-level construction patterns.
- Refactor safety depends on expanding contract coverage before moving internals.

## Open Questions
- Should the refactor remain strictly no-break, or are additive API changes acceptable if they improve observability?
- Is sequential provider fallback the intended long-term behavior, or is bounded parallel execution desired?
- Does the project need a formal compatibility matrix across HTTP, CLI, MCP, and Python usage before code movement starts?
- Should configuration loading remain synchronous and process-local, or is a clearer settings bootstrap required for multi-worker deployment?

## Next Recommended Step
Execute `TASK01.md`: introduce an explicit app factory and dependency seams, then add characterization tests that prove current `/api` behavior is unchanged.

## Execution Log
- 2026-03-31: Reviewed `README.md`, `pyproject.toml`, broker, API, config, session, and test modules.
- 2026-03-31: Ran `pytest`; all 126 tests passed.
- 2026-03-31: Decided against rewrite and selected staged architectural refactor planning.
- 2026-03-31: Added `Full_Operator.md`, `IMPLEMENTATION_CONTEXT.md`, and staged task files at repo root.
