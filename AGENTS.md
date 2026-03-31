<!-- FOR CLAUDE - NOT FOR HUMANS -->

# ONE_SHOT v13 — Operator Framework

> **Context is the scarce resource.** Three operators, seven utilities. Discover skills on demand.

---

## OPERATORS

### `/short` — Quick Iteration

```
/short [scope]
```

**Behavior:**
1. Load context: git log -5, TaskList, DECISIONS.md, BLOCKERS.md
2. Ask: "What are you working on?"
3. Discover skills on demand (~/.claude/skills/ index)
4. Execute in burn-down mode
5. Show delegation summary on completion

**Decision defaults:** Simplest implementation, match existing patterns, skip refactors unless blocking.

### `/full` — Structured Work

```
/full [project-description]
```

**Behavior:**
1. Create/load IMPLEMENTATION_CONTEXT.md
2. Structured intake: goals, scope, architecture, constraints
3. Phase-based planning with milestones
4. Skill discovery via ~/.claude/skills/ index
5. Execute with context checkpoints (50% → suggest handoff, 70% → auto-handoff)
6. Verify and show completion summary

**For:** New projects, refactors, complex features.

### `/conduct` — Multi-Model PMO Orchestrator

```
/conduct [idea or goal]
```

**Behavior:**
1. Detect available providers (codex, gemini)
2. Ask clarifying questions — BLOCKING, nothing runs until answered
3. Create structured plan with task breakdown
4. Route work across Claude + Codex + Gemini based on task type
5. Loop until goal is fully met (not just started)

**For:** Non-trivial tasks where you want autonomous execution across models until done.

---

## UTILITY COMMANDS

| Command | Purpose |
|---------|---------|
| `/handoff` | Save context before /clear |
| `/restore` | Resume from handoff |
| `/research` | Background research |
| `/freesearch` | Zero-token web search (Exa) |
| `/doc` | Cache external docs |
| `/vision` | Image/website analysis |
| `/secrets` | SOPS/Age secrets |

---

## DEPLOYABLE TEMPLATES

| Use Case | Template | Stack |
|----------|----------|-------|
| Membership/community sites | `templates/community-starter/` | Vercel + Supabase + Python + Resend |

---

## DECISION DEFAULTS

When ambiguous, apply without asking:

| Ambiguity | Default |
|-----------|---------|
| Multiple implementations | **Simplest** |
| Naming | Follow existing pattern |
| Refactor opportunity | **Skip** unless blocking |
| Stack | Follow CLAUDE.md |
| Error handling | Match surrounding code |
| Test framework | Use existing tests |

**Key rule:** When truly ambiguous, pick option A, note in DECISIONS.md.

---

## AUTO-APPROVED ACTIONS

- Reading any file
- Writing to scope-matched files
- Running tests and linters
- Creating DECISIONS.md, BLOCKERS.md, CHANGES.md
- Git commit (not push)
- Creating native tasks

## REQUIRES CONFIRMATION

- Destructive operations (rm -rf, DROP TABLE)
- Git push to shared branches
- External API calls that cost money
- Deploying to production

---

## DELEGATION

Before delegating: assess (complexity, criticality, uncertainty)
After delegating: verify the result
On failure: escalate (original → inline → human)

**Delegation summary on completion:**
```
📊 Delegation Summary
├─ N delegations, avg reward: X.XX
├─ Best: [agent] (reward) - [description]
└─ Tip: [optimization]
```

---

## PHILOSOPHY

> "It's harder to read code than to write it." — Joel Spolsky

**NEVER rewrite from scratch.** Extend, refactor, use existing solutions.

**USER TIME IS PRECIOUS.** Agents should make reasonable decisions autonomously.

---

## VERSION

v13.2 | 10 skills + 1 external | Operators discover skills on demand
