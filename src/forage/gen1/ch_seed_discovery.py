"""SELF_TEST discovery seed for UK_COMPANY_COUNTERPARTY_PREFLIGHT only.

Never counts as E5/E6/revenue. Does not touch Gen-1 seed artifacts except shared denylist.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forage.gen1.denylist import register_controlled
from forage.gen1.seed_discovery import (
    NETWORK as SEPOLIA_NETWORK,
)
from forage.gen1.seed_discovery import (
    ensure_seed_buyer,
    faucet_usdc,
)
from forage.gen1.wallet import ensure_receiver_wallet

SEED_REPORT = Path("data/gen1_ch/self_test_discovery_seed.json")


def _load_dotenv() -> None:
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def paid_get(url: str, private_key: str, network: str) -> dict[str, Any]:
    from eth_account import Account as EthAccount
    from x402 import x402Client
    from x402.http.clients import wrapHttpxWithPayment
    from x402.mechanisms.evm.exact import register_exact_evm_client

    acct = EthAccount.from_key(private_key)
    client = x402Client()
    register_exact_evm_client(client, acct, networks=network)

    async with wrapHttpxWithPayment(client, timeout=90.0) as http:
        r = await http.get(url)
        headers = {k.lower(): v for k, v in r.headers.items()}
        body_organism = None
        try:
            if r.headers.get("content-type", "").startswith("application/json"):
                body_organism = r.json().get("organism")
        except Exception:
            body_organism = None
        return {
            "url": url,
            "status": r.status_code,
            "ok": r.status_code == 200,
            "payment_response_present": "payment-response" in headers or "x-payment-response" in headers,
            "body_organism": body_organism,
            "evidence_class": "SELF_TEST",
        }


async def run_ch_seed(public_base_url: str, *, network: str | None = None) -> dict[str, Any]:
    """Minimum SELF_TEST paid call to seed Bazaar. Prefer Sepolia when CH app is on Sepolia."""
    _load_dotenv()
    if not os.environ.get("CDP_API_KEY_ID") or not os.environ.get("CDP_API_KEY_SECRET"):
        raise RuntimeError("CDP_API_KEY_ID/SECRET required")

    net = network or os.environ.get("FORAGE_GEN1_NETWORK", SEPOLIA_NETWORK)
    receiver = ensure_receiver_wallet()
    buyer = ensure_seed_buyer()
    register_controlled(receiver["address"])
    register_controlled(buyer["address"])
    # Also mirror into CH-local denylist file
    ch_deny = Path("data/gen1_ch/controlled_wallets.json")
    ch_deny.parent.mkdir(parents=True, exist_ok=True)
    wallets = {receiver["address"].lower(), buyer["address"].lower()}
    if ch_deny.exists():
        wallets |= {w.lower() for w in json.loads(ch_deny.read_text()).get("wallets", [])}
    ch_deny.write_text(json.dumps({"wallets": sorted(wallets)}, indent=2))

    faucet: dict[str, Any] = {"skipped": True}
    if net == SEPOLIA_NETWORK:
        faucet = await faucet_usdc(buyer["address"])
        await asyncio.sleep(8)

    seed_url = f"{public_base_url.rstrip('/')}/v1/uk_company_counterparty_preflight?company_number=00445790"
    payment = await paid_get(seed_url, buyer["private_key"], net)

    report: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": "SELF_TEST_DISCOVERY_SEED",
        "evidence_class": "SELF_TEST",
        "rule": "SELF_TEST never promotes to E5/E6 or revenue",
        "network": net,
        "receiver": receiver["address"],
        "buyer": buyer["address"],
        "buyer_denylisted": True,
        "faucet": faucet,
        "payment": payment,
        "public_base_url": public_base_url.rstrip("/"),
        "organism": "uk_company_counterparty_preflight",
        "price_usd": "0.01",
    }
    SEED_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SEED_REPORT.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    public = Path("data/gen1_ch/public_url.txt").read_text().strip()
    report = asyncio.run(run_ch_seed(public))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
