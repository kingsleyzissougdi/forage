# Forage architecture and ownership

Short, durable notes only. Feature details belong in code and tests.
Reconcile with product Section 7 when provided; do not add a competing
constitution.

## Locked from the foundation prompt

| Choice | Decision | Status |
|--------|----------|--------|
| Development vs runtime | Development agents own this workspace. Runtime business agents must never edit host code, credentials, budgets, evaluator config, or development rules. | Accepted (foundation prompt) |
| Tool-permission boundary | Budgets, authorization, idempotency, and accounting are enforced in deterministic code and tests. Documentation is not a security boundary. | Accepted (foundation prompt) |
| Upstream extension | CrewAI upstream scaffold is the application architecture reference. Prefer scaffold seams and forage-owned modules; material core patches need approval + ADR. | Accepted (foundation prompt) |
| Runtime product stack | CrewAI Flows 1.15.23 + typed Python; Browser Use policy-gated (optional) | Accepted (ADR-0002) |
| Authoritative ledger | SQLite (`data/forage.sqlite`) for experiments/actions/evidence/economics | Accepted (ADR-0002) |
| CH public host | Former Cinder droplet `cinder-host` (`138.197.9.7`); Cinder frozen failed | Accepted (ADR-0004) |

## Layout gravity (until Section 7 refines)

```text
AGENTS.md                 # development contract
.cursor/rules/            # thin always-on pointer (foundation.mdc)
.cursor/skills/           # ship-change workflow
.cursor/agents/           # optional read-only diff-reviewer
docs/architecture.md      # this file
docs/decisions/           # ADRs for durable choices only
scripts/verify.sh         # sole local verification entrypoint
```

Default product code lands in forage-owned packages once the CrewAI scaffold
is present. Do not invent a parallel framework.

## Atlas orientation (patterns only)

Studied read-only: `/home/jetjm/projects/Atlas` (2026-10-01).

### Adopted

| Practice | Atlas source | How used here |
|----------|--------------|---------------|
| Root `AGENTS.md` as contract | `Atlas/AGENTS.md`, `atlas/AGENTS.md` | Single concise contract; no org-chart roles |
| Thin always-apply rule that points to docs | `.cursor/rules/atlas-boundaries.mdc` | One `foundation.mdc`, not a rule pile |
| Short plan before meaningful edits | `.cursor/rules/atlas-planning-gate.mdc`, `atlas/routines/before-coding.md` | Embedded in `/ship-change`; approval only for material deviations |
| ADR for durable choices, template + index | `atlas/decisions/README.md`, `ADR-001-…` | `docs/decisions/`; not for routine features |
| Surgical diffs / anti-refactor | `.cursor/rules/atlas-anti-refactor.mdc` | Stated in AGENTS + foundation rule |
| Tests with the change | e.g. commit `30a9575ba` (ADR-073 + unit tests); `9b73d9287` (ADR-067 + unit tests) | Acceptance/regression tests before or with implementation |
| Local verification script | `scripts/atlas-check-boundaries.sh` pattern | `scripts/verify.sh` (lint/types/tests + no-GHA guard) |

### Deliberately omitted

| Atlas practice | Why omitted |
|----------------|-------------|
| Architect / Implementation / Review / Deployment role org (`atlas/ROLES.md`, `atlas-roles.mdc`) | Prompt asks for one workflow + at most one read-only reviewer, not a hierarchy |
| Always-on planning gate that blocks all edits until approval | Too bureaucratic for authorized routine work; approve only material deviations |
| Large rule set (ClickUp sync, version bump, migrations, UI, deploy safety, …) | Employer/product-specific; add scoped rules only when this build needs them |
| `touch-points.yaml` + boundary shell for FOSS fork seams | No Onyx fork; CrewAI scaffold seams are lighter |
| Prod deploy skill, hosted CI visibility | No GitHub Actions; CH SKU uses the existing Cinder droplet (ADR-0004), not a new platform |
| STE writing standard, full CONTRIBUTING mirror | Keep prose short; do not copy Atlas process docs |

Cursor conventions checked against current docs (`cursor.com/docs` rules +
skills): project rules are `.mdc` with YAML frontmatter; plain `.md` under
`.cursor/rules/` is ignored; skills live in `.cursor/skills/<name>/SKILL.md`;
optional project subagents in `.cursor/agents/*.md`.

## Gen-0 economic orientation

After the worker listing-hunt result (`ZERO_SETUP_OBJECTIVE_TASK_MARKET =
SPARSE`), Gen-0 compares **economic loops** (merchant/refinery/broker), not
human gig listings. Prefer zero ongoing human involvement over zero one-time
setup. Marketplace surfaces (including x402) are scout inputs, not the
runtime spine — see [ADR-0003](decisions/ADR-0003-economic-loop-unit.md).

CH (`uk_company_counterparty_preflight`) stays frozen on `:4022`. Sibling
Bazaar SKUs on `:4023` are paused (`PASSIVE_BAZAAR_DISCOVERY = NO_INTENT_SIGNAL`).
MCP_INSTALL_FUNNEL_V1 serves `check_x402_payment_readiness` on `:4024` `/mcp`
(same droplet/Caddy; wraps existing `x402_resource_preflight`; not a new SKU).
