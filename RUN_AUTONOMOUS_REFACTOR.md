# Run Autonomous Refactor

Use this file as the only handoff prompt for a fresh session.

## Mission
Execute the architectural refactor for Argus from start to finish autonomously.

Work from repo state only. Do not rely on prior chat context.

Do not stop after planning. Do not ask the user questions unless you hit a true blocker that cannot be resolved with reasonable assumptions. Keep moving task by task until the refactor is implemented, verified, committed, pushed, and the repo is cleaned up for the next user.

## Read First
Before making changes, read these files in this order:

1. `README.md`
2. `Full_Operator.md`
3. `IMPLEMENTATION_CONTEXT.md`
4. `TASK01.md`
5. `TASK02.md`
6. `TASK03.md`
7. `TASK04.md`
8. `TASK05.md`
9. `TASK06.md`

Then inspect the current code and tests before editing.

## Decisions Already Made
Treat the following as resolved:

- Breaking or additive changes are allowed if they improve the system.
- Optimize for reliability and cheapness.
- Prefer simpler design over backward compatibility when the compatibility cost is not worth it.
- Use cheap-first provider routing:
  - start with the primary provider for the mode
  - stop early when results are good enough
  - only hedge to another provider when failure, timeout, or weak results justify extra cost
- Do not build a formal compatibility matrix up front.
- Use lightweight compatibility checks plus contract tests.
- Keep configuration simple and process-local.
- This is a single-user hobby project, not a team or multi-tenant deployment target.
- The final state should look like a normal maintained repo, not an in-progress planning workspace.

## Execution Rules
- Execute `TASK01` through `TASK06` in order.
- Update `IMPLEMENTATION_CONTEXT.md` as you go so progress is durable.
- Run verification after each task.
- Fix obvious failures before moving on.
- Commit at logical checkpoints.
- Push your branch as you progress.
- If you must make an assumption, document it in `IMPLEMENTATION_CONTEXT.md` and continue.
- Only stop for a true blocker.

## True Blocker Standard
A blocker is real only if further implementation would be wasteful or impossible. Examples:

- required credentials with no mockable path
- contradictory requirements that force mutually exclusive implementations
- a missing core artifact with no inferable contract
- an unrecoverable runtime constraint

If blocked:
- push implementation as far as possible first
- document exactly what is missing
- recommend the smallest next step to unblock

## Required End State
By the end of the run:

- the refactor is implemented as far as possible
- tests and checks have been run as appropriate
- user-facing docs reflect the final architecture
- the branch is pushed
- the repo is cleaned up so it does not look like a temporary planning workspace

## Required Final Cleanup
After implementation and verification are complete, delete the planning artifacts that were only needed to drive the refactor:

- `Full_Operator.md`
- `IMPLEMENTATION_CONTEXT.md`
- `TASK01.md`
- `TASK02.md`
- `TASK03.md`
- `TASK04.md`
- `TASK05.md`
- `TASK06.md`
- `RUN_AUTONOMOUS_REFACTOR.md`

Do this only at the very end, after the refactor is complete, verified, documented, committed, and pushed or ready to push.

The final repo should read like a finished project, ready for the next user, with no temporary task-planning files left behind.

## Suggested Fresh-Session Prompt
If you want a one-message kickoff in the next session, use:

```text
Read /home/ubuntu/github/argus/RUN_AUTONOMOUS_REFACTOR.md and execute it exactly. Work autonomously from repo state only. Do not ask me questions unless you hit a true blocker that cannot be resolved with reasonable assumptions.
```
