# Cinder on this droplet — frozen (failed experiment)

Host: `cinder-host` / `138.197.9.7`. Code remains at `/opt/cinder`; PIT and
seals remain at `/var/lib/cinder/prospective`. Do not delete.

## What was useful

- **v1 sealed verdict:** `PROSPECTIVE_RISK_SIGNAL_CONFIRMED / ALPHA_NOT_YET_ESTABLISHED`
  (`prospective_alpha_v1`, checkpoint `CONFIRMATION_READY`). Risk scores are not
  a buy signal.
- **Layout that worked:** systemd user `cinder`, env `/etc/cinder/prospective.env`,
  data `/var/lib/cinder/prospective`, sqlite PIT + parquet batches.
- **Seals on disk (examples):** `birdeye_t60_v3/SEAL.json`, `ultra_early_v2/SEAL.json`,
  `wallet_copy_v1/SEAL.json`.
- **Ops lesson:** concurrent quote storms + SQLite WAL on the collector path
  caused load spikes; retries were the wrong fix.

## What failed

No established executable alpha. Bakeoff legs and copy probes stayed research.
Hummingbot was kept dormant for Cinder and is unused for Forage CH.

## Forage CH

This host serves frozen CH `uk_company_counterparty_preflight` on `:4022`
and parallel discovery SKUs on `:4023` (same Caddy/x402 wallet). Cinder units
are disabled. Re-enabling Cinder collectors would starve the 2 vCPU / 4 GiB box.
