# ADR-0002: Runtime stack and authoritative ledger

## Status

Accepted (authorized by product vertical-slice prompt, 2026-10-01)

## Context

Forage needs a minimal autonomous-business experiment runner. The product
prompt selected the stack and forbade alternate platforms.

## Decision

1. **Runtime** — CrewAI Flows (`crewai[tools]==1.15.23`) for workflow/state/
   routing; at most three logical roles (Strategist, Operator, Critic) as thin
   wrappers. Deterministic Python owns permissions, budgets, deadlines,
   retries, transitions, and accounting.
2. **Ledger** — SQLite (`data/forage.sqlite`) is the authoritative store for
   experiments, actions, evidence, and economics. Agents cannot write verified
   revenue; only trusted ledger adapters with `trusted=True` may.
3. **Browser** — Browser Use is optional and policy-gated
   (`browser_use_enabled`, default false). Current `browser-use` releases
   conflict with CrewAI's `openai`/`mcp` pins; V1 public research uses HN
   Algolia + DuckDuckGo HTML via httpx instead.
4. **Default policy** — `dry_run=true`, `external_writes_enabled=false`.

## Consequences

- One flow, one SQLite file, no Redis/Postgres/vector DB/dashboard.
- Commercial posts wait on owner channel credentials (NEEDS_CHANNEL_SETUP).
- Flow `@persist()` may store orchestration snapshots; money/evidence truth
  remains the forage ledger.

## Alternatives considered

- Custom agent runtime / OpenHands / Optuna / DSPy — rejected by prompt.
- Postgres or hosted CI — rejected for V1 scope.
