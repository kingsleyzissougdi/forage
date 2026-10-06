# ADR-0001: Foundation boundaries (dev/runtime, permissions, upstream)

## Status

Accepted (authorized by foundation prompt, 2026-10-01)

## Context

Forage needs a minimal engineering foundation before product code. Atlas is
a reference for AI-assisted practice; CrewAI's upstream scaffold is the
reference for application architecture. Runtime business agents must stay
isolated from the development workspace and from self-modification of
controls.

## Decision

1. **Development vs runtime** — Development agents may edit this repo under
   `AGENTS.md`. Runtime business agents must never gain permission to edit
   their own rules, budgets, evaluator, credentials, or host code. Do not
   expose this development workspace to runtime agents.
2. **Enforcement** — Budgets, authorization, idempotency, and accounting are
   enforced in deterministic code and tests. Documentation guides developers;
   it is not a security boundary.
3. **Upstream** — Extend CrewAI's scaffold via forage-owned modules and
   documented seams. Material patches to upstream core require human approval
   and an ADR when the choice is durable.
4. **Process** — Meaningful changes use short plan → acceptance tests →
   smallest patch → `./scripts/verify.sh` → fresh diff review → docs as
   needed (`/ship-change`). No hosted CI; no GitHub Actions workflows.

Runtime product stack and the authoritative ledger remain open until Section 7
or an authorized vertical-slice design specifies them.

## Consequences

- Foundation stays thin: one contract, one always-apply rule, one ship skill,
  one verify entrypoint.
- Product ADRs for ledger/runtime come later; do not invent them here.
- Security-sensitive behavior must land as tests, not as prose alone.

## Alternatives considered

- Copy Atlas roles, planning gate, and rule set wholesale — rejected as
  operational complexity not justified for this greenfield.
- Defer all docs until the app exists — rejected; a short contract prevents
  process drift during the first vertical slice.
