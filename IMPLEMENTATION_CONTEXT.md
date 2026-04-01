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
- User decisions on 2026-03-31 clarified product priorities: reliability and cheapness over strict compatibility or architecture purity.

## Project Goal
Produce a staged architectural refactor plan that improves correctness, lifecycle control, maintainability, extension safety, and scale-readiness without breaking Argus's public HTTP, CLI, MCP, or Python interfaces.

## Success Criteria
- The plan is explicit, sequential, and executable in small slices.
- Each stage is independently shippable and testable.
- Public contracts change only when the change clearly improves reliability, cheapness, or maintainability.
- High-risk coupling points are called out before code changes begin.
- The earliest execution slice produces an observable improvement without destabilizing current behavior.

## Confirmed Inputs
- Argus is already shipping search, extraction, and multi-turn session features.
- Core modules exist for API, broker, providers, extraction, sessions, persistence, and MCP.
- The repo includes automated tests that currently pass.
- Git is available.
- The user wants the architectural refactor to exist as a durable plan.
- The user is comfortable with additive or breaking changes if they are the right design choice.
- The product priority is reliability and cheapness for a single-user hobby workflow.
- Expected usage is single operator usage across one or more intermittent Claude sessions, not team-scale multi-tenant deployment.
- Untracked paths currently present: `1shot/`, `docs/sessions/`, `scripts/`.

## Assumptions
- No rewrite is desired or justified while the test baseline is green.
- SQLite-backed session persistence remains supported.
- External services can be mocked or adapter-wrapped when direct access is unavailable.
- This run's implementation scope is the planning system itself: operator doc, context, and task files.
- Deployment remains simple and process-local unless a later real need appears.

## Constraints
- Avoid destructive repo cleanup or unrelated file churn.
- Preserve behavior where it is cheap to do so, but do not protect accidental complexity just for compatibility's sake.
- Keep the repo legible for future agents and humans.
- Use the existing code shape as the source of truth for refactor seams.
- Ask-user-questions tooling is unavailable in this runtime, so assumptions must be written down explicitly.

## Dependency Status
- Runtime dependencies: present for local testing.
- Test framework: present and passing.
- External search providers: configurable, but not required for planning.
- Secrets loading: currently mixed into config loading via environment and subprocess fallback.
- Persistence: available through current session and search persistence modules.
- Deployment path: local and Docker both appear supported; no multi-worker bootstrap work is required for this plan.

## Execution Strategy
1. Preserve current behavior with characterization coverage around the public surfaces most affected by refactor.
2. Introduce explicit app composition and dependency seams so lifecycle and overrides are no longer hidden behind module globals.
3. Decompose `SearchBroker` into smaller units with single-purpose responsibilities.
4. Rework provider execution around cheap-first reliability: primary-first execution, explicit stop conditions, and optional bounded hedging only when it measurably improves outcomes.
5. Isolate result processing, persistence, config, and session infrastructure behind clearer boundaries.
6. Add operational hardening for contracts and compatibility.
7. Finish with release-gate verification and migration notes.

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
- Conversion of the initial open questions into explicit product decisions

## Key Decisions
- The first execution slice should target app composition because it is the safest high-leverage seam and produces observable API behavior with low product risk.
- `SearchBroker` is the primary refactor hotspot because it currently owns too many responsibilities.
- Reliability and cheapness are the primary design criteria.
- Backward compatibility is useful but not sacred; breaking or additive changes are acceptable when they clearly improve the design.
- Provider execution should not be "parallel by default." The target model is cheap-first routing: start with a primary provider, stop early when results are sufficient, and only hedge to a second provider when failure, timeout, or low-confidence output justifies the extra cost.
- A formal compatibility matrix is not required before code movement starts; a lightweight compatibility checklist and contract tests are sufficient for this single-user project.
- Configuration can remain synchronous and process-local; no special multi-worker bootstrap architecture is needed for the current use case.
- Planning files live at repo root for now because that is the least surprising placement in this repository.

## Implementation Notes
- `argus/api/routes_search.py` currently uses a module-global broker singleton.
- `argus/api/main.py` constructs middleware and routers around that implicit global lifecycle.
- `argus/broker/router.py` currently mixes cache lookup, routing, provider execution, health tracking, budget tracking, ranking, dedupe, persistence, and session-aware behavior.
- `argus/config.py` currently mixes environment parsing, secrets subprocess fallback, dataclass construction, and a global singleton.
- `argus/sessions/store.py` currently combines in-memory state and persistence lookup paths in one store implementation.

## Risks / Blockers
- No true blockers for planning.
- Exact thresholds for "sufficient results" and hedge triggers still need to be defined during implementation.
- Hidden external consumers may rely on current module-level construction patterns.
- Refactor safety depends on expanding contract coverage before moving internals.

## Open Questions
- What should count as "enough" search results for an early stop: fixed count, score threshold, domain diversity, or per-mode policy?
- Should a hedge be triggered only on hard failure and timeout, or also on low-result and low-quality outcomes?
- Which public interfaces are acceptable to break first if the simplification materially improves the codebase?

## Next Recommended Step
Execute `TASK01.md`: introduce an explicit app factory and dependency seams, then add characterization tests that prove current `/api` behavior is unchanged.

## Execution Log
- 2026-03-31: Reviewed `README.md`, `pyproject.toml`, broker, API, config, session, and test modules.
- 2026-03-31: Ran `pytest`; all 126 tests passed.
- 2026-03-31: Decided against rewrite and selected staged architectural refactor planning.
- 2026-03-31: Added `Full_Operator.md`, `IMPLEMENTATION_CONTEXT.md`, and staged task files at repo root.
- 2026-03-31: Recorded user decisions: optimize for reliability and cheapness, allow interface changes when justified, keep compatibility checks lightweight, and keep configuration bootstrap simple for single-user usage.
