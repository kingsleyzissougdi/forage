# Forage

Bounded autonomous-business experiment runner (CrewAI Flows + SQLite ledger).

## Setup

```bash
uv sync --extra dev
cp .env.example .env   # optional LLM keys; public research works without them
./scripts/verify.sh
```

## Commands

```bash
uv run forage demo
uv run forage mechanisms run          # Gen-0 mechanism analyst + parallel cheap tests
uv run forage campaign run
uv run forage campaign status
uv run forage campaign pause
uv run forage campaign resume
```

Policy: `policy.yaml` (default DRY RUN; no commercial writes until enabled).
Ledger: `data/forage.sqlite` (or `--db` path). Gen-0 report: `data/mechanisms.gen0.json`.

See `AGENTS.md` and `docs/architecture.md`.

<!-- mcp-name: io.github.jiminfinite/check-x402-payment-readiness -->

## MCP: Check x402 Payment Readiness

Call immediately before paying an x402 resource. Checks payment requirements,
network, wallet balance/readiness, and budget; returns PAY/BLOCKED with reason
codes. **Does not move funds.**

This is a distribution experiment (`MCP_INSTALL_FUNNEL_V1`). It wraps the
existing `x402_resource_preflight` backend. It is **not** a new SKU and does
not restart paused Bazaar listings.

### Install (HTTP)

```bash
claude mcp add --transport http check-x402-payment-readiness \
  https://138-197-9-7.sslip.io/mcp
```

Cursor / other MCP clients — Streamable HTTP:

```json
{
  "mcpServers": {
    "check-x402-payment-readiness": {
      "url": "https://138-197-9-7.sslip.io/mcp"
    }
  }
}
```

### When an agent should call it

Use `check_x402_payment_readiness` **immediately before** paying any x402 URL.
Pass the resource URL, the paying wallet address, and optional `max_spend_usd`.

Do **not** paste private keys into the tool or into chat. This server never
accepts keys.

### Wallet / payment (minimum)

The tool only needs a public wallet **address** to check readiness. It does not
pay the target resource.

If you later pay that resource yourself, you need a client that speaks x402
(USDC on Base, `eip155:8453`) and a funded wallet. There is no faucet, custodial
wallet, or gas abstraction here. Missing that setup is recorded as
`PAYMENT_BOOTSTRAP_FRICTION`, not as a product gap to paper over.

### Local stdio (optional)

```bash
uvx --from git+https://github.com/jiminfinite/forage check-x402-payment-readiness
```

Set `FORAGE_MCP_TRANSPORT=stdio` for stdio. Default transport is Streamable HTTP.
