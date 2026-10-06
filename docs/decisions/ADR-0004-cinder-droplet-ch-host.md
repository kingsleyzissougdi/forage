# ADR-0004: Host CH SKU on the former Cinder droplet

## Status

Accepted (owner: deploy CH to the Cinder droplet; treat Cinder as failed)

## Context

Local WSL + Cloudflare Quick Tunnel cannot keep `uk_company_counterparty_preflight`
reachable. The DigitalOcean droplet `cinder-host` (`138.197.9.7`) already runs
as a long-lived Linux host. Cinder’s live collectors did not establish alpha.

## Decision

1. Freeze Cinder systemd units. Keep `/var/lib/cinder` and `/opt/cinder` on disk.
2. Run isolated Forage CH (`serve-ch` on `127.0.0.1:4022`) on that droplet.
3. Terminate TLS with Caddy on a stable hostname derived from the droplet IP
   (`*.sslip.io`). Do not add a new cloud platform or GitHub Actions.

## Consequences

- CH listing clock is independent of the laptop.
- Cinder is not continued as a live trading experiment on this host.
- Inbound 80/443 must be allowed; SSH remains the admin path.

## Alternatives considered

- Railway / new VPS — extra spend and account surface.
- Another Quick Tunnel — hostname still changes on process restart.
