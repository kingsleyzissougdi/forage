# ADR-0003: Gen-0 economic unit is the loop (merchant/refinery)

## Status

Accepted (authorized Gen-0 direction, 2026-10-01)

## Context

Generation 0 falsified a narrow worker proposition:
`ZERO_SETUP_OBJECTIVE_TASK_MARKET = SPARSE` — publicly visible,
explicitly priced human tasks that an unauthenticated agent can
autonomously complete for money were not found in useful density.

That result does not falsify autonomous economic organisms. Continuing
to hunt human gig listings would optimize the wrong game. The intended
Forage shape is produce → package → distribute → receive payment, with
machine-verifiable demand, fulfillment, and payment.

## Decision

1. The Gen-0 comparison unit is an **economic loop** (merchant / refinery /
   broker / later operator), not a human gig listing.
2. Optimize for **zero ongoing human involvement**. One-time
   wallet/account setup is allowed; ongoing owner work is not.
3. Discovery surfaces (x402 Bazaar, agent marketplaces, affiliate programs,
   monitoring products, etc.) are **inputs to scouts**, not architecture.
   Do not hard-code any single marketplace as Forage’s spine.
4. Worker-style listing hunts remain closed unless a future ADR reopens them.

## Consequences

- Campaigns produce evidenced loops and <$20 afternoon experiment specs.
- Live treasury funding and paid endpoint deployment are separate steps.
- x402 may appear in scouts without becoming a required dependency.

## Alternatives considered

- Continue worker listing hunts after SPARSE — rejected; wrong economic game.
- Hard-wire x402 as the only merchant path — rejected; surface, not spine.
- Require zero third-party setup forever — rejected; conflates one-time
  setup with ongoing human burden.
