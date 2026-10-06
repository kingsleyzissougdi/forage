# Architecture decision records

ADRs record **durable** choices only: runtime shape, authoritative ledger,
tool-permission boundary, upstream extension approach, and similar.

Do **not** write an ADR for routine features or fixes.

Never mark an ADR **Accepted** without human approval or authorization
already present in the governing product prompt.

## Template

Copy `TEMPLATE.md` to `ADR-NNNN-short-title.md`.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-0001](ADR-0001-foundation-boundaries.md) | Foundation boundaries (dev/runtime, permissions, upstream) | Accepted (foundation prompt) |
| [ADR-0002](ADR-0002-runtime-and-ledger.md) | Runtime stack and authoritative ledger | Accepted (product prompt) |
| [ADR-0003](ADR-0003-economic-loop-unit.md) | Gen-0 economic unit is the loop (merchant/refinery) | Accepted (Gen-0 direction) |
| [ADR-0004](ADR-0004-cinder-droplet-ch-host.md) | Host CH SKU on the former Cinder droplet | Accepted (owner deploy) |
