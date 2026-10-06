# Forage — development contract

Canonical instructions for **development agents** working in this repo.
Runtime business agents must never edit host code, credentials, budgets,
evaluator config, or these development rules.

## Ownership

| Concern | Owner |
|---------|--------|
| Application architecture reference | CrewAI upstream scaffold (extend; do not fork core lightly) |
| Product / forage-owned code | This repo (default write location) |
| Budgets, authorization, idempotency, accounting | Deterministic code + tests (not docs) |
| Development process | `AGENTS.md`, `.cursor/rules/foundation.mdc`, `/ship-change` |

Durable choices live in `docs/architecture.md` and `docs/decisions/`.
Do not duplicate policy across AGENTS, rules, ADRs, and a constitution.

## Upstream seams

Prefer scaffold extension points and forage-owned modules over patching
CrewAI core. A material upstream-core patch needs a short plan, an ADR when
durable, and human approval.

## Change workflow

For each meaningful change:

1. Read relevant code and docs
2. Short plan (goal, files/seam, how success is checked)
3. Acceptance tests for correctness-sensitive behavior
4. Smallest patch
5. `./scripts/verify.sh`
6. Diff review (prefer fresh review context; `/ship-change` or diff-reviewer)
7. Update affected docs only

Routine work within an already-authorized design does not need another
approval round. Request approval only for a material deviation: new service
or framework, upstream-core patch, expanded permissions/spending, destructive
data change, or changed accounting/evaluation meaning. Batch questions.

## Verification

```bash
./scripts/verify.sh
```

Hooks, if used, must call this same command. No hosted CI or paid verification
framework. Do not add GitHub Actions workflows.

## Limits

- Surgical diffs; no opportunistic refactors or drive-by lint outside the plan
- Do not weaken assertions, remove tests, or hide pre-existing failures for green
- Parallel agents use isolated branches/worktrees; no concurrent edits to shared files without ownership
- Documentation is guidance, not a security boundary
- Development workspace must not be exposed to runtime business agents

## Atlas practice (selective)

Adopted / omitted notes: `docs/architecture.md` § Atlas orientation.
Source repo studied read-only: `/home/jetjm/projects/Atlas`.
